"""Memória: o essencial — busca híbrida, deduplicação e o que chega ao modelo."""

from __future__ import annotations

import time
import zlib
from pathlib import Path

import pytest

from eve.memory.manager import DEDUPE_THRESHOLD, MemoryManager, _parse_items
from eve.memory.models import Memory, MemoryKind
from eve.memory.store import MemoryStore, escape_fts


class FakeEmbedder:
    """Vetores determinísticos: palavras iguais, vetores próximos.

    Usa CRC32 em vez de ``hash()``, que é aleatorizado por processo — com
    ``hash()`` a colisão entre duas frases sem relação mudava a cada execução
    e o teste passava ou falhava conforme a semente.
    """

    dimensions = 64

    def __init__(self) -> None:
        self.disponivel = True

    async def available(self) -> bool:
        return self.disponivel

    def _vec(self, texto: str) -> list[float]:
        vetor = [0.0] * self.dimensions
        for palavra in texto.lower().split():
            vetor[zlib.crc32(palavra.encode()) % self.dimensions] += 1.0
        norma = sum(v * v for v in vetor) ** 0.5 or 1.0
        return [v / norma for v in vetor]

    async def embed(self, texts):
        return [self._vec(t) for t in texts] if self.disponivel else None

    async def embed_documents(self, texts):
        return await self.embed(texts)

    async def embed_query(self, text):
        return self._vec(text) if self.disponivel else None

    async def embed_one(self, text):
        return self._vec(text) if self.disponivel else None

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "eve.db", dimensions=FakeEmbedder.dimensions)
    yield s
    await s.aclose()


# ---------------------------------------------------------------- persistência


async def test_add_get_delete(store: MemoryStore) -> None:
    m = await store.add(Memory(content="Meu café é sem açúcar"))
    assert (await store.get(m.uid)).content == "Meu café é sem açúcar"
    assert await store.delete(m.uid) is True
    assert await store.get(m.uid) is None
    assert await store.delete(m.uid) is False


async def test_layers_have_different_lifetimes() -> None:
    trabalho = Memory(content="agora", kind=MemoryKind.WORKING)
    fato = Memory(content="sempre", kind=MemoryKind.SEMANTIC)
    assert trabalho.expires_at is not None
    assert fato.expires_at is None


async def test_expired_memories_are_forgotten(store: MemoryStore) -> None:
    velha = Memory(content="passageira", kind=MemoryKind.WORKING)
    velha.expires_at = time.time() - 1
    await store.add(velha)
    await store.add(Memory(content="permanente"))
    assert await store.forget_expired() == 1
    assert await store.count() == 1


# --------------------------------------------------------------------- busca


async def test_text_search_ignores_accents_and_punctuation(store: MemoryStore) -> None:
    await store.add(Memory(content="Prefiro reuniões de manhã"))
    achadas = await store.search_text("reunioes?")
    assert len(achadas) == 1


def test_escape_fts_turns_free_text_into_a_valid_query() -> None:
    assert escape_fts("o que eu disse sobre o projeto?") == (
        '"que" OR "eu" OR "disse" OR "sobre" OR "projeto"'
    )
    assert escape_fts("???") == ""


async def test_text_search_survives_punctuation_only_query(store: MemoryStore) -> None:
    await store.add(Memory(content="algo"))
    assert await store.search_text("???") == []


async def test_kind_filter(store: MemoryStore) -> None:
    await store.add(Memory(content="fato do projeto", kind=MemoryKind.SEMANTIC))
    await store.add(Memory(content="fato aconteceu ontem", kind=MemoryKind.EPISODIC))
    achadas = await store.search_text("fato", kinds=[MemoryKind.EPISODIC])
    assert len(achadas) == 1
    assert achadas[0].kind is MemoryKind.EPISODIC


async def test_vector_search_has_a_relevance_floor(store: MemoryStore) -> None:
    """Sem piso, a busca sempre devolve algo — e algo irrelevante polui o contexto."""
    emb = FakeEmbedder()
    await store.add(Memory(content="café sem açúcar"), await emb.embed_one("café sem açúcar"))
    longe = await emb.embed_one("aviação supersônica intercontinental")
    assert await store.search_vector(longe, max_distance=0.1) == []
    assert await store.search_vector(longe, max_distance=2.0) != []


async def test_hybrid_search_falls_back_to_text_without_embeddings(store: MemoryStore) -> None:
    await store.add(Memory(content="o projeto usa Python"))
    achadas = await store.search("projeto", embedding=None)
    assert len(achadas) == 1


# ------------------------------------------------------------------ gerente


@pytest.fixture
def manager(store: MemoryStore, fake_providers) -> MemoryManager:
    return MemoryManager(store, FakeEmbedder(), fake_providers)


async def test_remember_returns_state(manager: MemoryManager) -> None:
    _, estado = await manager.remember("O projeto EVE usa Python 3.13")
    assert estado == "nova"


async def test_duplicate_is_reinforced_not_duplicated(manager: MemoryManager) -> None:
    memoria, _ = await manager.remember("O projeto EVE usa Python 3.13")
    de_novo, estado = await manager.remember("O projeto EVE usa Python 3.13")
    assert estado == "reforçada"
    assert de_novo.uid == memoria.uid
    assert de_novo.use_count == 1
    assert await manager.store.count() == 1


async def test_unimportant_is_discarded(manager: MemoryManager) -> None:
    _, estado = await manager.remember("bobagem", importance=0.05)
    assert estado == "descartada"
    assert await manager.store.count() == 0


async def test_empty_content_is_refused(manager: MemoryManager) -> None:
    with pytest.raises(ValueError, match="vazia"):
        await manager.remember("   ")


async def test_recall_reinforces_what_was_used(manager: MemoryManager) -> None:
    memoria, _ = await manager.remember("Prefiro reuniões de manhã")
    await manager.recall("reuniões")
    assert (await manager.store.get(memoria.uid)).use_count == 1


async def test_context_is_empty_when_nothing_matches(manager: MemoryManager) -> None:
    await manager.remember("O projeto EVE usa Python")
    assert await manager.context_for("aviação supersônica") == ""


async def test_context_lists_what_it_knows(manager: MemoryManager) -> None:
    await manager.remember("Prefiro reuniões de manhã")
    contexto = await manager.context_for("reuniões")
    assert "Prefiro reuniões de manhã" in contexto
    assert contexto.startswith("Do que você lembra")
    # Apresentado como fala relatada, para o modelo não adotar a preferência.
    assert 'O usuário disse: "' in contexto


async def test_forget_matching(manager: MemoryManager) -> None:
    await manager.remember("Prefiro reuniões de manhã")
    apagadas = await manager.forget_matching("reuniões")
    assert len(apagadas) == 1
    assert await manager.store.count() == 0


async def test_works_without_embeddings(manager: MemoryManager) -> None:
    """Sem modelo de embedding a memória continua funcionando, só sem semântica."""
    manager.embedder.disponivel = False
    _, estado = await manager.remember("O projeto EVE usa Python")
    assert estado == "nova"
    assert len(await manager.recall("projeto")) == 1


# --------------------------------------------------------------- extração


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ('[{"content": "a", "kind": "semantic"}]', 1),
        ('Claro! ```json\n[{"content": "a"}]\n```', 1),
        ("[]", 0),
        ("não consegui", 0),
        ('[{"kind": "semantic"}]', 0),
        ('[{"content": ""}]', 0),
        ('{"content": "a"}', 0),
    ],
)
def test_extraction_parsing_tolerates_messy_models(bruto: str, esperado: int) -> None:
    assert len(_parse_items(bruto)) == esperado


async def test_extraction_stores_what_the_model_returned(
    manager: MemoryManager, fake_providers
) -> None:
    from eve.ai.base import user as user_msg
    from tests.fakes import reply

    fake_providers.fake.queue.append(
        reply('[{"content": "O usuário prefere café sem açúcar", "importance": 0.7}]')
    )
    gravadas = await manager.extract([user_msg("meu café é sem açúcar")], session="s1")
    assert len(gravadas) == 1
    assert gravadas[0].source == "chat"
    assert gravadas[0].session == "s1"


async def test_extraction_failure_is_silent(manager: MemoryManager, fake_providers) -> None:
    from eve.ai.base import user as user_msg
    from tests.fakes import boom

    fake_providers.fake.queue.append(boom("modelo caiu"))
    assert await manager.extract([user_msg("oi")]) == []


def test_dedupe_threshold_is_conservative() -> None:
    """Medido: paráfrase 0,08 · mesma ideia 0,23 · relacionada 0,24.

    O limiar precisa ficar antes das duas últimas, que são indistinguíveis.
    """
    assert 0.08 < DEDUPE_THRESHOLD < 0.22


async def test_dimension_mismatch_degrades_instead_of_crashing(tmp_path: Path) -> None:
    """Trocar o modelo de embedding não pode quebrar a memória inteira."""
    store = MemoryStore(tmp_path / "eve.db", dimensions=8)
    await store.add(Memory(content="o projeto usa Python"), [0.0] * 8)
    assert store.vectors.available is True

    # Um vetor com a dimensão errada, como se o modelo tivesse mudado.
    assert await store.search_vector([0.0] * 768) == []
    assert store.vectors.available is False
    # A busca textual continua de pé.
    assert len(await store.search("projeto")) == 1
    await store.aclose()
