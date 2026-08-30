"""Banco da memória: SQLite com FTS5 e sqlite-vec (spec §18).

Local por padrão, num arquivo só, sem servidor — é o que permite prometer que
nada sai da máquina sem o usuário mandar.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from eve.logging import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    NOT NULL UNIQUE,
    title       TEXT,
    content     TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    importance  REAL    NOT NULL DEFAULT 0.5,
    confidence  REAL    NOT NULL DEFAULT 0.8,
    source      TEXT    NOT NULL DEFAULT 'user',
    session     TEXT,
    context     TEXT    NOT NULL DEFAULT '{}',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    last_used_at REAL,
    use_count   INTEGER NOT NULL DEFAULT 0,
    expires_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);

-- Busca textual. `content=''` mantém o índice externo à tabela, e a
-- sincronia fica a cargo dos gatilhos abaixo.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

-- As ligações do cofre: cada [[colchete]] escrito numa nota vira uma linha.
-- `to_uid` é nulo enquanto a nota do outro lado não existe — o Obsidian
-- desenha esses links pendentes no grafo, e perdê-los seria perder a intenção
-- de quem escreveu.
CREATE TABLE IF NOT EXISTS memory_links (
    from_uid  TEXT NOT NULL,
    to_title  TEXT NOT NULL,
    to_uid    TEXT,
    relation  TEXT NOT NULL DEFAULT 'menciona',
    PRIMARY KEY (from_uid, to_title, relation)
);

CREATE INDEX IF NOT EXISTS idx_links_to ON memory_links(to_uid);
CREATE INDEX IF NOT EXISTS idx_links_titulo ON memory_links(to_title);

CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""


class VectorSupport:
    """Estado do sqlite-vec nesta instalação."""

    def __init__(self, available: bool, reason: str = "", dimensions: int = 0) -> None:
        self.available = available
        self.reason = reason
        self.dimensions = dimensions


def connect(path: Path, dimensions: int = 768) -> tuple[sqlite3.Connection, VectorSupport]:
    """Abre (ou cria) o banco, aplica o esquema e tenta carregar o sqlite-vec.

    Sem o sqlite-vec a memória continua funcionando: perde a busca semântica,
    mantém a textual. Degradar é melhor que não abrir.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrar(conn)
    conn.executescript(SCHEMA)

    vectors = _load_vectors(conn, dimensions)
    conn.execute(
        "INSERT OR REPLACE INTO meta(chave, valor) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn, vectors


def _migrar(conn: sqlite3.Connection) -> None:
    """Ajustes de esquema entre versões.

    A v2 refez ``memory_links`` para guardar o título do alvo, e não só o uid.
    A tabela antiga nunca chegou a ser escrita, então recriá-la não perde nada
    — e vale mais que carregar uma migração de dados que não existem.
    """
    try:
        versao = conn.execute("SELECT valor FROM meta WHERE chave = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return  # banco novo: o esquema já nasce na versão atual
    atual = int(versao["valor"]) if versao is not None else 0
    if atual < 2:
        conn.execute("DROP TABLE IF EXISTS memory_links")
    if atual < 3:
        # O título vem do nome do arquivo no cofre; guardá-lo aqui é cache, não
        # segunda verdade — a reconciliação o reescreve a cada varredura.
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE memories ADD COLUMN title TEXT")
    conn.commit()


def _load_vectors(conn: sqlite3.Connection, dimensions: int) -> VectorSupport:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception as exc:
        log.warning("memoria.sem_busca_vetorial", error=str(exc))
        return VectorSupport(False, str(exc))

    # Cosseno em vez de L2: a distância fica entre 0 e 2 e comparável entre
    # consultas, o que permite um limiar de relevância que significa algo.
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0("
        f"rowid INTEGER PRIMARY KEY, "
        f"embedding float[{dimensions}] distance_metric=cosine)"
    )
    versao = conn.execute("SELECT vec_version()").fetchone()[0]
    return VectorSupport(True, f"sqlite-vec {versao}", dimensions)
