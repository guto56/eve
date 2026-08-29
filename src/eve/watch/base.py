"""Contrato de um observador.

Um observador não decide nada: ele percebe e publica. Quem decide o que fazer
é o motor proativo, com a política do usuário — separar as duas coisas é o que
permite observar bastante sem incomodar muito.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from eve.bus import EventBus
from eve.logging import get_logger

log = get_logger(__name__)


class Observer(ABC):
    """Roda em segundo plano publicando eventos no barramento."""

    kind: str

    def __init__(self, bus: EventBus, name: str) -> None:
        self.bus = bus
        self.name = name
        self.events_seen = 0
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.error = None
        self._task = asyncio.create_task(self._guarded())

    async def _guarded(self) -> None:
        """Um observador que quebra não pode derrubar o Core."""
        try:
            await self.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)[:200]
            log.warning("observador.falhou", observador=self.name, error=self.error)

    @abstractmethod
    async def run(self) -> None: ...

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.events_seen += 1
        await self.bus.emit(event_type, source=f"watch:{self.name}", **payload)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "running": self.running,
            "events": self.events_seen,
            "error": self.error,
        }
