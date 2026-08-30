"""Manter o índice de acordo com o cofre.

O arquivo é a verdade; o banco é conveniência. Então tudo aqui anda numa
direção só: lê o disco, ajusta o índice. Nunca o contrário.

É o que permite abrir o Obsidian, corrigir uma memória errada, apagar outra, e
a EVE simplesmente concordar da próxima vez que olhar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from eve.logging import get_logger
from eve.memory.embeddings import Embedder
from eve.memory.models import Memory
from eve.memory.store import MemoryStore
from eve.memory.vault import Nota, Vault

log = get_logger(__name__)


@dataclass
class Resultado:
    """O que a reconciliação fez, para caber numa linha de log."""

    novas: int = 0
    atualizadas: int = 0
    removidas: int = 0
    adotadas: int = 0
    ignoradas: int = 0
    uids: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, int]:
        return {
            "novas": self.novas,
            "atualizadas": self.atualizadas,
            "removidas": self.removidas,
            "adotadas": self.adotadas,
            "ignoradas": self.ignoradas,
        }

    def mexeu(self) -> bool:
        return bool(self.novas or self.atualizadas or self.removidas or self.adotadas)


class Sincronizador:
    def __init__(self, vault: Vault, store: MemoryStore, embedder: Embedder) -> None:
        self.vault = vault
        self.store = store
        self.embedder = embedder
        # Um arquivo apagado não pode mais ser lido para dizer quem era. A
        # varredura preenche este mapa; a reconciliação cobre o que escapar.
        self.uid_por_caminho: dict[Path, str] = {}

    # ------------------------------------------------------------ um arquivo

    async def indexar(self, caminho: Path, resultado: Resultado | None = None) -> Memory | None:
        """Põe (ou atualiza) uma nota no índice. Devolve a memória indexada."""
        r = resultado or Resultado()
        self.adotar(caminho, r)
        nota = self.vault.ler(caminho)
        if nota is None:
            r.ignoradas += 1
            return None

        existente = await self.store.get(nota.memoria.uid)
        if existente is None:
            embedding = await self.embedder.embed_one(nota.memoria.content)
            await self.store.add(nota.memoria, embedding)
            r.novas += 1
        elif (existente.content, existente.title) != (nota.memoria.content, nota.memoria.title):
            embedding = await self.embedder.embed_one(nota.memoria.content)
            await self.store.update_content(
                nota.memoria.uid, nota.memoria.content, embedding, nota.memoria.title
            )
            r.atualizadas += 1

        await self._ligar(nota)
        r.uids.add(nota.memoria.uid)
        self.uid_por_caminho[caminho] = nota.memoria.uid
        return nota.memoria

    def adotar(self, caminho: Path, resultado: Resultado | None = None) -> bool:
        """Dá cabeçalho a uma nota escrita à mão, sem tirá-la do lugar.

        Sem uid próprio, a mesma nota viraria uma memória nova a cada varredura.
        """
        if self.vault.uid_em(caminho) is not None:
            return False
        nota = self.vault.ler(caminho)
        if nota is None:
            return False
        self.vault.escrever(nota.memoria, em=caminho)
        if resultado is not None:
            resultado.adotadas += 1
        return True

    async def esquecer_arquivo(self, caminho: Path) -> bool:
        """O arquivo sumiu do disco: a memória sai do índice."""
        uid = self.uid_por_caminho.pop(caminho, None) or self.vault.uid_em(caminho)
        if uid is None:
            return False
        return await self.store.delete(uid)

    # -------------------------------------------------------------- o cofre

    MARCA_MIGRACAO = "cofre_migrado"

    async def migrar(self) -> int:
        """Leva para o disco o que só existe no índice. Uma vez, e só uma.

        É a única vez em que a informação anda do banco para o arquivo, e ela
        precisa vir antes da primeira reconciliação: sem isso a varredura veria
        memórias sem arquivo e concluiria, corretamente pela regra dela, que
        foram apagadas.
        """
        if await self.store.meta(self.MARCA_MIGRACAO):
            return 0
        self.vault.preparar()
        levadas = 0
        for memoria in await self.store.todas():
            if self.vault.caminho_por_uid(memoria.uid) is None:
                self.vault.escrever(memoria)
                levadas += 1
        await self.store.set_meta(self.MARCA_MIGRACAO, "1")
        if levadas:
            log.info("cofre.migrado", memorias=levadas, cofre=str(self.vault.raiz))
        return levadas

    async def reconciliar(self) -> Resultado:
        """Varre o cofre inteiro e deixa o índice igual a ele."""
        self.vault.preparar()
        await self.migrar()
        resultado = Resultado()

        # Duas passadas, e a ordem importa: um link só resolve para uma nota
        # que já tenha uid. Numa passada só, quem fosse indexado antes da nota
        # escrita à mão apontaria para o vazio.
        for caminho in self.vault.varrer():
            self.adotar(caminho, resultado)
        for caminho in self.vault.varrer():
            await self.indexar(caminho, resultado)

        # O que está no índice e não no disco foi apagado por fora.
        for orfa in await self.store.uids() - resultado.uids:
            await self.store.delete(orfa)
            resultado.removidas += 1
        self.uid_por_caminho = {
            caminho: uid for caminho, uid in self.uid_por_caminho.items() if caminho.exists()
        }

        if resultado.mexeu():
            log.info("cofre.reconciliado", **resultado.as_dict())
        return resultado

    # ------------------------------------------------------------- ligações

    async def _ligar(self, nota: Nota) -> None:
        """Resolve cada ``[[colchete]]`` a um uid, quando a nota do lado existe."""
        alvos: list[tuple[str, str | None]] = []
        for titulo in nota.links:
            destino = self.vault.por_titulo(titulo)
            alvos.append((titulo, self.vault.uid_em(destino) if destino else None))
        await self.store.set_links(nota.memoria.uid, alvos)
