"""Sessões de conversa.

Por enquanto vivem em memória. A persistência chega junto com a memória
(Fase 6), que é quem decide o que de uma conversa merece sobreviver a ela.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from eve.ai.base import Message

MAX_SESSIONS = 50
MAX_HISTORY = 40


@dataclass
class ChatSession:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    messages: list[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    title: str = ""

    def add(self, message: Message) -> None:
        self.messages.append(message)
        self.updated_at = time.time()
        if not self.title and message.role == "user":
            self.title = message.content[:60]

    def history(self, limit: int = MAX_HISTORY) -> list[Message]:
        """Últimas mensagens, sem cortar no meio de um par ferramenta/resposta."""
        if len(self.messages) <= limit:
            return list(self.messages)
        recorte = self.messages[-limit:]
        # Uma mensagem "tool" órfã (sem a chamada que a originou) confunde o
        # modelo; sobe até encontrar um começo coerente.
        while recorte and recorte[0].role == "tool":
            recorte = recorte[1:]
        return recorte

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title or "(sem título)",
            "messages": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SessionStore:
    """Guarda as sessões recentes, descartando as mais antigas."""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()

    def get_or_create(self, session_id: str | None = None) -> ChatSession:
        if session_id and session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]
        session = ChatSession(id=session_id) if session_id else ChatSession()
        self._sessions[session.id] = session
        self._sessions.move_to_end(session.id)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
        return session

    def get(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    def all(self) -> list[ChatSession]:
        return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)
