"""Observa aplicativos abrindo e fechando.

Sem notificação nativa disponível sem app empacotado, resta comparar listas.
O intervalo é generoso de propósito: saber que o Spotify abriu três segundos
depois não muda nada, e consultar de segundo em segundo é desperdício.
"""

from __future__ import annotations

import asyncio
from typing import Any

from eve.events import EventType
from eve.macos import native
from eve.watch.base import Observer

INTERVALO = 5.0


class AppObserver(Observer):
    kind = "apps"

    def __init__(self, bus: Any, name: str = "apps", intervalo: float = INTERVALO) -> None:
        super().__init__(bus, name)
        self.intervalo = intervalo
        self._conhecidos: dict[int, str] = {}

    async def run(self) -> None:
        self._conhecidos = await self._snapshot()
        while not self._stop.is_set():
            await asyncio.sleep(self.intervalo)
            atuais = await self._snapshot()

            for pid, nome in atuais.items():
                if pid not in self._conhecidos:
                    await self.emit(EventType.APP_OPENED, app=nome, pid=pid)
            for pid, nome in self._conhecidos.items():
                if pid not in atuais:
                    await self.emit(EventType.APP_CLOSED, app=nome, pid=pid)

            self._conhecidos = atuais

    async def _snapshot(self) -> dict[int, str]:
        apps = await asyncio.to_thread(native.running_apps)
        return {app["pid"]: app["name"] for app in apps}
