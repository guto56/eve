"""A memória como arquivos Markdown.

O que precisa de teste aqui é a regra que sustenta tudo: **o arquivo é a
verdade**. Se ela falhar em silêncio, o usuário corrige uma memória no Obsidian
e a EVE continua repetindo a versão errada — pior do que não ter editado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eve.ai.manager import ProviderManager
from eve.config import Settings
from eve.memory.embeddings import Embedder
from eve.memory.manager import MemoryManager, _parse_items
from eve.memory.models import Memory, MemoryKind
from eve.memory.store import MemoryStore
from eve.memory.vault import Vault, links_de, titulo_de
from eve.secrets import InMemoryBackend, SecretStore


@pytest.fixture
def cofre(tmp_path: Path) -> Vault:
    vault = Vault(tmp_path / "Memória")
    vault.preparar()
    return vault


@pytest.fixture
def memoria(tmp_path: Path, cofre: Vault) -> MemoryManager:
    settings = Settings()
    store = MemoryStore(tmp_path / "eve.db", settings.memory.embedding_dimensions)
    embedder = Embedder(host="http://127.0.0.1:1", model="nenhum")  # offline de propósito
    segredos = SecretStore(tmp_path / "s.json", backend=InMemoryBackend(), allow_env_fallback=False)
    return MemoryManager(store, embedder, ProviderManager(settings, segredos), None, vault=cofre)


def test_titulo_e_um_nome_nao_a_frase_inteira() -> None:
    """Ninguém escreve uma frase inteira entre colchetes."""
    assert titulo_de("Para rodar o projeto: uv run eve start, depois eve web.") == (
        "Para rodar o projeto"
    )
    assert titulo_de("qualquer coisa", "Rodar o projeto") == "Rodar o projeto"
    # Caracteres que quebrariam o link ou o sistema de arquivos não passam.
    assert "/" not in titulo_de("a/b: c")
    assert "[" not in titulo_de("[x] fazer isso")


def test_ida_e_volta_preserva_a_memoria(cofre: Vault) -> None:
    original = Memory(content="O gato chama Nina.", kind=MemoryKind.SEMANTIC, importance=0.7)
    caminho = cofre.escrever(original, corpo="O gato chama Nina.\n\nVer [[Augusto]].")

    nota = cofre.ler(caminho)
    assert nota is not None
    assert nota.memoria.uid == original.uid
    assert nota.memoria.kind is MemoryKind.SEMANTIC
    assert nota.memoria.importance == pytest.approx(0.7)
    assert nota.links == ("Augusto",)


def test_nota_escrita_a_mao_e_adotada_sem_sair_do_lugar(cofre: Vault) -> None:
    """Arrumar a pasta dos outros é a forma mais rápida de perder a confiança."""
    solta = cofre.raiz / "Pessoas" / "Nina.md"
    solta.write_text("A Nina é a gata do usuário.", encoding="utf-8")

    nota = cofre.ler(solta)
    assert nota is not None
    cofre.escrever(nota.memoria, em=solta)

    assert solta.exists()
    assert solta.read_text(encoding="utf-8").startswith("---")
    assert cofre.uid_em(solta) is not None


def test_link_resolve_sem_acento_e_sem_caixa(cofre: Vault) -> None:
    """Quem digita o link à mão erra os dois, e link quebrado não avisa."""
    (cofre.raiz / "Pessoas" / "João Antônio.md").write_text("x", encoding="utf-8")
    assert cofre.por_titulo("joao antonio") is not None
    assert cofre.por_titulo("ninguém") is None


async def test_o_arquivo_manda(memoria: MemoryManager, cofre: Vault) -> None:
    """Editar no Obsidian vale; apagar no Obsidian vale."""
    gravada, _ = await memoria.remember("O usuário mora em Belo Horizonte.", importance=0.8)
    caminho = cofre.caminho_por_uid(gravada.uid)
    assert caminho is not None

    caminho.write_text(
        caminho.read_text(encoding="utf-8").replace("Belo Horizonte", "Ouro Preto"),
        encoding="utf-8",
    )
    await memoria.reconciliar()
    assert "Ouro Preto" in (await memoria.store.get(gravada.uid)).content

    caminho.unlink()
    resultado = await memoria.reconciliar()
    assert resultado.removidas == 1
    assert await memoria.store.get(gravada.uid) is None


async def test_migracao_nao_perde_o_que_ja_estava_no_banco(memoria: MemoryManager) -> None:
    """Quem já usava a EVE não pode perder memória ao atualizar."""
    antiga = Memory(content="Memória de antes do cofre.", kind=MemoryKind.SEMANTIC)
    await memoria.store.add(antiga, None)

    await memoria.reconciliar()
    assert await memoria.store.get(antiga.uid) is not None
    assert memoria.vault.caminho_por_uid(antiga.uid) is not None

    # E a segunda passada não duplica nada.
    antes = await memoria.store.count()
    await memoria.reconciliar()
    assert await memoria.store.count() == antes


async def test_pessoas_citadas_viram_notas_ligadas(memoria: MemoryManager, cofre: Vault) -> None:
    """É o que transforma uma lista de fatos em rede."""
    fato, _ = await memoria.remember(
        "A Marina cuida do jurídico.", importance=0.7, pessoas=["Marina"]
    )
    await memoria.reconciliar()

    assert (cofre.raiz / "Pessoas" / "Marina.md").exists()
    ligacoes = await memoria.store.links(fato.uid)
    assert [x["titulo"] for x in ligacoes] == ["Marina"]
    assert ligacoes[0]["uid"] is not None

    nina = cofre.uid_em(cofre.raiz / "Pessoas" / "Marina.md")
    assert [b.uid for b in await memoria.store.backlinks(nina)] == [fato.uid]


def test_json_truncado_do_modelo_pequeno_nao_perde_tudo() -> None:
    """O modelo local entrega os objetos certos e esquece de fechar o array."""
    bruto = (
        '[{"titulo": "Sócia", "content": "A Marina é sócia.", "pessoas": ["Marina"]}, '
        '{"titulo": "Contador", "content": "O Rafael é contador.", "pessoas": ["Rafael"]'
    )
    assert [item["titulo"] for item in _parse_items(bruto)] == ["Sócia", "Contador"]


def test_frontmatter_quebrado_nao_leva_o_texto_junto(cofre: Vault) -> None:
    """Perder os metadados por causa de dois-pontos é ruim; perder o texto, pior."""
    ruim = cofre.raiz / "Fatos" / "torto.md"
    ruim.write_text("---\ntipo: fato: errado: demais\n---\n\nO conteúdo sobrevive.\n")
    nota = cofre.ler(ruim)
    assert nota is not None
    assert "O conteúdo sobrevive." in nota.memoria.content


def test_links_captura_alias_e_ignora_repetido() -> None:
    assert links_de("[[Nina]] e [[Nina|a gata]] e [[Rafael]]") == ("Nina", "Rafael")
