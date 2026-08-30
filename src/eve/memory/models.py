"""O que é uma memória.

As quatro camadas da spec §17. A diferença entre elas não é técnica — é o que
justifica manter cada coisa e por quanto tempo.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryKind(StrEnum):
    WORKING = "working"
    """O que está acontecendo agora. Expira sozinha."""

    EPISODIC = "episodic"
    """Algo que aconteceu. "Hoje trabalhamos no módulo de voz." """

    SEMANTIC = "semantic"
    """Fato persistente. "O projeto X usa determinada tecnologia." """

    PROCEDURAL = "procedural"
    """Como fazer algo. "Para subir meu ambiente, siga este fluxo." """


#: Quanto tempo cada camada sobrevive sem ser usada, em segundos.
#: ``None`` = não expira.
TTL: dict[MemoryKind, float | None] = {
    MemoryKind.WORKING: 6 * 3600,
    MemoryKind.EPISODIC: None,
    MemoryKind.SEMANTIC: None,
    MemoryKind.PROCEDURAL: None,
}


@dataclass
class Memory:
    content: str
    kind: MemoryKind = MemoryKind.SEMANTIC
    importance: float = 0.5
    confidence: float = 0.8
    source: str = "user"
    session: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    title: str | None = None
    """Nome da nota no cofre. É por ele que os ``[[colchetes]]`` resolvem.

    Não vai para o banco: no cofre o nome do arquivo é o título, e ter as duas
    coisas seria ter duas verdades."""

    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    rowid: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_used_at: float | None = None
    use_count: int = 0
    expires_at: float | None = None
    score: float | None = None
    """Relevância na busca que devolveu esta memória. ``None`` fora de busca."""

    def __post_init__(self) -> None:
        if self.expires_at is None:
            ttl = TTL[self.kind]
            if ttl is not None:
                self.expires_at = self.created_at + ttl

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "content": self.content,
            "kind": self.kind.value,
            "importance": round(self.importance, 3),
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "session": self.session,
            "context": self.context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "expires_at": self.expires_at,
            "score": round(self.score, 4) if self.score is not None else None,
        }

    def summary(self, width: int = 70) -> str:
        texto = self.content.replace("\n", " ")
        return texto if len(texto) <= width else texto[: width - 1] + "…"
