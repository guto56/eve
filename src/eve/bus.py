"""Event Bus assíncrono em memória.

Publicar nunca bloqueia e nunca falha por causa de um assinante lento: a fila
de cada assinante tem tamanho fixo e descarta o evento mais antigo quando
enche, contabilizando o descarte. O barramento também guarda um histórico
circular para que um cliente que conecte depois receba o contexto recente.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import contextmanager

from eve.events import Event, matches
from eve.logging import get_logger

log = get_logger(__name__)

DEFAULT_QUEUE_SIZE = 256
DEFAULT_HISTORY_SIZE = 200


class Subscription:
    """Fluxo de eventos para um assinante."""

    def __init__(self, patterns: tuple[str, ...], maxsize: int) -> None:
        self.patterns = patterns
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self._closed = False

    def wants(self, event: Event) -> bool:
        return any(matches(p, event.type) for p in self.patterns)

    def offer(self, event: Event) -> None:
        """Entrega sem bloquear; descarta o mais antigo se a fila estiver cheia."""
        if self._closed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover - corrida improvável
                pass
            self.dropped += 1
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    def close(self) -> None:
        self._closed = True

    async def __aiter__(self) -> AsyncIterator[Event]:
        while not self._closed:
            yield await self.queue.get()


class EventBus:
    def __init__(self, history_size: int = DEFAULT_HISTORY_SIZE) -> None:
        self._subs: set[Subscription] = set()
        self._history: deque[Event] = deque(maxlen=history_size)
        self.published = 0

    async def publish(self, event: Event) -> Event:
        self._history.append(event)
        self.published += 1
        for sub in tuple(self._subs):
            if sub.wants(event):
                sub.offer(event)
        return event

    async def emit(self, type: str, /, source: str = "core", **payload: object) -> Event:
        """Atalho para publicar sem construir o :class:`Event` na mão."""
        return await self.publish(Event(type=type, source=source, payload=dict(payload)))

    @contextmanager
    def subscribe(
        self,
        patterns: Iterable[str] = ("*",),
        maxsize: int = DEFAULT_QUEUE_SIZE,
    ) -> Iterator[Subscription]:
        sub = Subscription(tuple(patterns), maxsize)
        self._subs.add(sub)
        try:
            yield sub
        finally:
            sub.close()
            self._subs.discard(sub)

    def history(self, patterns: Iterable[str] = ("*",), limit: int = 50) -> list[Event]:
        pats = tuple(patterns)
        if limit <= 0:
            return []
        found = [e for e in self._history if any(matches(p, e.type) for p in pats)]
        return found[-limit:]

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
