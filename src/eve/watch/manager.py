"""Ciclo de vida dos observadores."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from eve.bus import EventBus
from eve.logging import get_logger
from eve.watch.apps import AppObserver
from eve.watch.base import Observer
from eve.watch.files import FileObserver

log = get_logger(__name__)


class WatchManager:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.observers: dict[str, Observer] = {}

    async def add_path(self, name: str, path: Path) -> Observer:
        alvo = path.expanduser()  # noqa: ASYNC240 - só expande ~, não toca no disco
        await self.remove(name)
        observador = FileObserver(self.bus, name, alvo)
        self.observers[name] = observador
        await observador.start()
        # Um instante para o erro de caminho inexistente aparecer no `describe`.
        await asyncio.sleep(0.05)
        return observador

    async def add_apps(self) -> Observer:
        await self.remove("apps")
        observador = AppObserver(self.bus)
        self.observers["apps"] = observador
        await observador.start()
        return observador

    async def remove(self, name: str) -> bool:
        observador = self.observers.pop(name, None)
        if observador is None:
            return False
        await observador.stop()
        return True

    async def aclose(self) -> None:
        for nome in list(self.observers):
            await self.remove(nome)

    def describe(self) -> list[dict[str, Any]]:
        return [o.describe() for o in self.observers.values()]

    @property
    def running(self) -> int:
        return sum(1 for o in self.observers.values() if o.running)
