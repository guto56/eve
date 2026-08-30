"""Guarda e recupera memórias.

A busca é híbrida: FTS5 acha o que o usuário escreveu com as mesmas palavras,
a busca vetorial acha o que ele escreveu com outras. As duas listas são
fundidas por RRF (Reciprocal Rank Fusion), que combina ordenações sem precisar
que os escores das duas sejam comparáveis entre si.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from eve.logging import get_logger
from eve.memory.db import connect
from eve.memory.models import Memory, MemoryKind

log = get_logger(__name__)

RRF_K = 60
"""Constante do RRF. 60 é o valor da literatura e funciona bem sem ajuste."""

MAX_DISTANCE = 0.72
"""Piso de relevância na busca vetorial (distância de cosseno).

Sem piso, a busca sempre devolve *alguma coisa* — o vizinho mais próximo de
"receita de bolo de cenoura" no meio das suas memórias ainda é uma memória
sua, e entrar no contexto do modelo como se fosse relevante é pior que não
ter achado nada. Medido nesta máquina: acerto legítimo ≈ 0,61, consulta sem
relação ≈ 0,75."""

COLUNAS = (
    "rowid, uid, title, content, kind, importance, confidence, source, session, context, "
    "created_at, updated_at, last_used_at, use_count, expires_at"
)

_FTS_SEGURO = re.compile(r"[^\w\sáàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ-]", re.UNICODE)


def escape_fts(query: str) -> str:
    """Transforma texto livre em consulta FTS5 válida.

    O usuário escreve "o que eu disse sobre o projeto?"; o FTS5 tem sintaxe
    própria e engasga com pontuação. Cada palavra vira um termo entre aspas.
    """
    limpo = _FTS_SEGURO.sub(" ", query)
    termos = [t for t in limpo.split() if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in termos)


class MemoryStore:
    def __init__(self, path: Path, dimensions: int = 768) -> None:
        self.path = path
        self._conn, self.vectors = connect(path, dimensions)
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await asyncio.to_thread(self._conn.close)

    # ------------------------------------------------------------ escrita

    async def add(self, memory: Memory, embedding: Sequence[float] | None = None) -> Memory:
        return await self._run(self._add_sync, memory, embedding)

    def _add_sync(self, memory: Memory, embedding: Sequence[float] | None) -> Memory:
        cursor = self._conn.execute(
            """INSERT INTO memories
               (uid, title, content, kind, importance, confidence, source, session, context,
                created_at, updated_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                memory.uid,
                memory.title,
                memory.content,
                memory.kind.value,
                memory.importance,
                memory.confidence,
                memory.source,
                memory.session,
                json.dumps(memory.context, ensure_ascii=False),
                memory.created_at,
                memory.updated_at,
                memory.expires_at,
            ),
        )
        memory.rowid = cursor.lastrowid
        self._store_vector(memory.rowid, embedding)
        self._conn.commit()
        return memory

    def _store_vector(self, rowid: int | None, embedding: Sequence[float] | None) -> None:
        if not self.vectors.available or embedding is None or rowid is None:
            return
        import sqlite_vec

        # Apagar e inserir, e não INSERT OR REPLACE: a tabela virtual do
        # sqlite-vec não aceita substituição pela chave, e reclama justamente
        # quando o conteúdo de uma memória muda — que é quando mais importa.
        self._conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (rowid,))
        self._conn.execute(
            "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(list(embedding))),
        )

    async def update_content(
        self,
        uid: str,
        content: str,
        embedding: Sequence[float] | None = None,
        title: str | None = None,
    ) -> Memory | None:
        return await self._run(self._update_sync, uid, content, embedding, title)

    def _update_sync(
        self,
        uid: str,
        content: str,
        embedding: Sequence[float] | None,
        title: str | None = None,
    ) -> Memory | None:
        linha = self._conn.execute("SELECT rowid FROM memories WHERE uid = ?", (uid,)).fetchone()
        if linha is None:
            return None
        self._conn.execute(
            "UPDATE memories SET content = ?, title = COALESCE(?, title), updated_at = ? "
            "WHERE uid = ?",
            (content, title, time.time(), uid),
        )
        self._store_vector(linha["rowid"], embedding)
        self._conn.commit()
        return self._get_sync(uid)

    async def reinforce(self, uid: str, importance_boost: float = 0.05) -> Memory | None:
        """Uma memória usada de novo vale mais e adia o próprio esquecimento."""
        return await self._run(self._reinforce_sync, uid, importance_boost)

    def _reinforce_sync(self, uid: str, boost: float) -> Memory | None:
        agora = time.time()
        self._conn.execute(
            """UPDATE memories
               SET use_count = use_count + 1,
                   last_used_at = ?,
                   importance = MIN(1.0, importance + ?),
                   expires_at = CASE WHEN expires_at IS NULL THEN NULL ELSE ? END
               WHERE uid = ?""",
            (agora, boost, agora + 6 * 3600, uid),
        )
        self._conn.commit()
        return self._get_sync(uid)

    async def delete(self, uid: str) -> bool:
        return await self._run(self._delete_sync, uid)

    def _delete_sync(self, uid: str) -> bool:
        linha = self._conn.execute("SELECT rowid FROM memories WHERE uid = ?", (uid,)).fetchone()
        if linha is None:
            return False
        if self.vectors.available:
            self._conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (linha["rowid"],))
        self._conn.execute("DELETE FROM memories WHERE uid = ?", (uid,))
        self._conn.execute("DELETE FROM memory_links WHERE from_uid = ? OR to_uid = ?", (uid, uid))
        self._conn.commit()
        return True

    # ---------------------------------------------------------- ligações

    async def set_links(self, uid: str, alvos: Sequence[tuple[str, str | None]]) -> None:
        """Substitui as ligações que saem desta nota.

        Substituir e não acrescentar: a nota no disco é a verdade, e um link
        apagado no Obsidian tem de sumir do índice também.
        """
        await self._run(self._set_links_sync, uid, list(alvos))

    def _set_links_sync(self, uid: str, alvos: list[tuple[str, str | None]]) -> None:
        self._conn.execute("DELETE FROM memory_links WHERE from_uid = ?", (uid,))
        self._conn.executemany(
            "INSERT OR REPLACE INTO memory_links(from_uid, to_title, to_uid) VALUES (?,?,?)",
            [(uid, titulo, destino) for titulo, destino in alvos],
        )
        self._conn.commit()

    async def links(self, uid: str) -> list[dict[str, Any]]:
        """Para onde esta nota aponta."""
        return await self._run(self._links_sync, uid)

    def _links_sync(self, uid: str) -> list[dict[str, Any]]:
        return [
            {"titulo": linha["to_title"], "uid": linha["to_uid"]}
            for linha in self._conn.execute(
                "SELECT to_title, to_uid FROM memory_links WHERE from_uid = ? ORDER BY to_title",
                (uid,),
            )
        ]

    async def backlinks(self, uid: str) -> list[Memory]:
        """Quem aponta para esta nota. É o que transforma a lista em rede."""
        return await self._run(self._backlinks_sync, uid)

    def _backlinks_sync(self, uid: str) -> list[Memory]:
        linhas = self._conn.execute(
            """SELECT m.* FROM memories m
               JOIN memory_links l ON l.from_uid = m.uid
               WHERE l.to_uid = ?
               ORDER BY m.updated_at DESC""",
            (uid,),
        ).fetchall()
        return [_to_memory(linha) for linha in linhas]

    async def vizinhas(self, uid: str, limit: int = 5) -> list[Memory]:
        """Notas a um passo de distância, nos dois sentidos."""
        return await self._run(self._vizinhas_sync, uid, limit)

    def _vizinhas_sync(self, uid: str, limit: int) -> list[Memory]:
        linhas = self._conn.execute(
            """SELECT DISTINCT m.* FROM memories m
               JOIN memory_links l
                 ON (l.to_uid = m.uid AND l.from_uid = ?)
                 OR (l.from_uid = m.uid AND l.to_uid = ?)
               WHERE m.uid != ?
               ORDER BY m.importance DESC, m.updated_at DESC
               LIMIT ?""",
            (uid, uid, uid, limit),
        ).fetchall()
        return [_to_memory(linha) for linha in linhas]

    async def todas(self) -> list[Memory]:
        """Tudo que está no índice. Usado só na migração para o cofre."""
        return await self._run(
            lambda: [
                _to_memory(linha) for linha in self._conn.execute(f"SELECT {COLUNAS} FROM memories")
            ]
        )

    async def meta(self, chave: str) -> str | None:
        return await self._run(
            lambda: (
                linha["valor"]
                if (
                    linha := self._conn.execute(
                        "SELECT valor FROM meta WHERE chave = ?", (chave,)
                    ).fetchone()
                )
                else None
            )
        )

    async def set_meta(self, chave: str, valor: str) -> None:
        def gravar() -> None:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(chave, valor) VALUES (?,?)", (chave, valor)
            )
            self._conn.commit()

        await self._run(gravar)

    async def uids(self) -> set[str]:
        """Tudo que o índice conhece — usado para achar o que sumiu do disco."""
        return await self._run(
            lambda: {linha["uid"] for linha in self._conn.execute("SELECT uid FROM memories")}
        )

    async def expired_uids(self) -> list[str]:
        """Quem já passou da validade — para apagar arquivo e índice juntos."""
        return await self._run(
            lambda: [
                linha["uid"]
                for linha in self._conn.execute(
                    "SELECT uid FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (time.time(),),
                )
            ]
        )

    async def forget_expired(self) -> int:
        return await self._run(self._forget_expired_sync)

    def _forget_expired_sync(self) -> int:
        agora = time.time()
        alvos = [
            linha["rowid"]
            for linha in self._conn.execute(
                "SELECT rowid FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                (agora,),
            )
        ]
        if not alvos:
            return 0
        marcas = ",".join("?" * len(alvos))
        if self.vectors.available:
            self._conn.execute(f"DELETE FROM memories_vec WHERE rowid IN ({marcas})", alvos)
        self._conn.execute(f"DELETE FROM memories WHERE rowid IN ({marcas})", alvos)
        self._conn.commit()
        return len(alvos)

    # ------------------------------------------------------------ leitura

    async def get(self, uid: str) -> Memory | None:
        return await self._run(self._get_sync, uid)

    def _get_sync(self, uid: str) -> Memory | None:
        linha = self._conn.execute(
            f"SELECT {COLUNAS} FROM memories WHERE uid = ?", (uid,)
        ).fetchone()
        return _to_memory(linha) if linha else None

    async def recent(
        self, limit: int = 20, kinds: Iterable[MemoryKind] | None = None
    ) -> list[Memory]:
        return await self._run(self._recent_sync, limit, kinds)

    def _recent_sync(self, limit: int, kinds: Iterable[MemoryKind] | None) -> list[Memory]:
        where, params = _kind_filter(kinds)
        linhas = self._conn.execute(
            f"SELECT {COLUNAS} FROM memories {where} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [_to_memory(linha) for linha in linhas]

    async def count(self) -> int:
        return await self._run(
            lambda: self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        )

    async def stats(self) -> dict[str, Any]:
        return await self._run(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        por_camada = {
            linha["kind"]: linha["n"]
            for linha in self._conn.execute("SELECT kind, COUNT(*) n FROM memories GROUP BY kind")
        }
        total = sum(por_camada.values())
        com_vetor = 0
        if self.vectors.available:
            com_vetor = self._conn.execute("SELECT COUNT(*) FROM memories_vec").fetchone()[0]
        return {
            "total": total,
            "por_camada": por_camada,
            "com_vetor": com_vetor,
            "busca_semantica": self.vectors.available,
            "detalhe_vetorial": self.vectors.reason,
            "arquivo": str(self.path),
            "bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    # -------------------------------------------------------------- busca

    async def search_text(
        self, query: str, limit: int = 10, kinds: Iterable[MemoryKind] | None = None
    ) -> list[Memory]:
        return await self._run(self._search_text_sync, query, limit, kinds)

    def _search_text_sync(
        self, query: str, limit: int, kinds: Iterable[MemoryKind] | None
    ) -> list[Memory]:
        termos = escape_fts(query)
        if not termos:
            return []
        where, params = _kind_filter(kinds, prefixo="AND")
        try:
            linhas = self._conn.execute(
                f"""SELECT {_prefixado(COLUNAS)}, bm25(memories_fts) AS rank
                    FROM memories_fts
                    JOIN memories m ON m.rowid = memories_fts.rowid
                    WHERE memories_fts MATCH ? {where}
                    ORDER BY rank LIMIT ?""",
                (termos, *params, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:  # pragma: no cover - consulta degenerada
            log.warning("memoria.fts_falhou", error=str(exc), query=termos[:80])
            return []
        return [_to_memory(linha, score=-linha["rank"]) for linha in linhas]

    async def search_vector(
        self,
        embedding: Sequence[float],
        limit: int = 10,
        kinds: Iterable[MemoryKind] | None = None,
        max_distance: float = MAX_DISTANCE,
    ) -> list[Memory]:
        if not self.vectors.available:
            return []
        return await self._run(self._search_vector_sync, embedding, limit, kinds, max_distance)

    def _search_vector_sync(
        self,
        embedding: Sequence[float],
        limit: int,
        kinds: Iterable[MemoryKind] | None,
        max_distance: float,
    ) -> list[Memory]:
        import sqlite_vec

        # O filtro por camada é aplicado depois, então buscamos com folga.
        k = limit * 4 if kinds else limit
        try:
            vizinhos = self._conn.execute(
                "SELECT rowid, distance FROM memories_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (sqlite_vec.serialize_float32(list(embedding)), k),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            self._disable_vectors(exc)
            return []
        if not vizinhos:
            return []

        distancias = {
            linha["rowid"]: linha["distance"]
            for linha in vizinhos
            if linha["distance"] <= max_distance
        }
        if not distancias:
            return []
        marcas = ",".join("?" * len(distancias))
        where, params = _kind_filter(kinds, prefixo="AND")
        linhas = self._conn.execute(
            f"SELECT {COLUNAS} FROM memories WHERE rowid IN ({marcas}) {where}",
            (*distancias.keys(), *params),
        ).fetchall()

        memorias = [_to_memory(linha, score=-distancias[linha["rowid"]]) for linha in linhas]
        memorias.sort(key=lambda m: m.score or 0, reverse=True)
        return memorias[:limit]

    async def search(
        self,
        query: str,
        embedding: Sequence[float] | None = None,
        limit: int = 10,
        kinds: Iterable[MemoryKind] | None = None,
    ) -> list[Memory]:
        """Busca híbrida. Sem embedding, cai para a textual."""
        textual = await self.search_text(query, limit * 2, kinds)
        semantica = (
            await self.search_vector(embedding, limit * 2, kinds) if embedding is not None else []
        )
        if not semantica:
            return textual[:limit]
        if not textual:
            return semantica[:limit]
        return _fuse(textual, semantica)[:limit]

    async def similar(
        self, embedding: Sequence[float], threshold: float = 0.12
    ) -> tuple[Memory, float] | None:
        """Memória mais parecida, se a distância for menor que ``threshold``.

        É o que evita gravar a mesma coisa cinco vezes com palavras diferentes.
        """
        candidatos = await self.search_vector(embedding, limit=1, max_distance=threshold)
        if not candidatos:
            return None
        melhor = candidatos[0]
        distancia = -(melhor.score or 0)
        return (melhor, distancia) if distancia <= threshold else None

    # ------------------------------------------------------------ interno

    def _disable_vectors(self, exc: sqlite3.OperationalError) -> None:
        """Desliga a busca semântica em vez de deixar o erro subir.

        Acontece, por exemplo, quando o modelo de embedding muda e a dimensão
        deixa de bater com a do banco. Perder a busca semântica é ruim; perder
        a memória inteira por causa disso seria pior.
        """
        if self.vectors.available:
            log.warning("memoria.vetores_desligados", error=str(exc))
        self.vectors.available = False
        self.vectors.reason = f"desligada: {exc}"

    async def _run(self, fn, *args):  # type: ignore[no-untyped-def]
        """SQLite é bloqueante: uma operação por vez, fora do event loop."""
        async with self._lock:
            return await asyncio.to_thread(fn, *args)


def _prefixado(colunas: str) -> str:
    return ", ".join(f"m.{c.strip()}" for c in colunas.split(","))


def _kind_filter(
    kinds: Iterable[MemoryKind] | None, prefixo: str = "WHERE"
) -> tuple[str, tuple[str, ...]]:
    if not kinds:
        return "", ()
    valores = tuple(k.value for k in kinds)
    coluna = "m.kind" if prefixo == "AND" else "kind"
    return f"{prefixo} {coluna} IN ({','.join('?' * len(valores))})", valores


def _to_memory(linha: sqlite3.Row, score: float | None = None) -> Memory:
    memoria = Memory(
        content=linha["content"],
        kind=MemoryKind(linha["kind"]),
        importance=linha["importance"],
        confidence=linha["confidence"],
        source=linha["source"],
        session=linha["session"],
        context=json.loads(linha["context"]),
        uid=linha["uid"],
        created_at=linha["created_at"],
        updated_at=linha["updated_at"],
        expires_at=linha["expires_at"],
    )
    memoria.title = linha["title"] if "title" in linha.keys() else None
    memoria.rowid = linha["rowid"]
    memoria.last_used_at = linha["last_used_at"]
    memoria.use_count = linha["use_count"]
    memoria.score = score
    return memoria


def _fuse(*listas: list[Memory]) -> list[Memory]:
    """Reciprocal Rank Fusion: combina ordenações sem comparar escores."""
    pontos: dict[str, float] = {}
    por_uid: dict[str, Memory] = {}
    for lista in listas:
        for posicao, memoria in enumerate(lista):
            pontos[memoria.uid] = pontos.get(memoria.uid, 0.0) + 1.0 / (RRF_K + posicao + 1)
            por_uid.setdefault(memoria.uid, memoria)
    ordenadas = sorted(pontos.items(), key=lambda item: item[1], reverse=True)
    resultado = []
    for uid, ponto in ordenadas:
        memoria = por_uid[uid]
        memoria.score = ponto
        resultado.append(memoria)
    return resultado
