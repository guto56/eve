"""Acompanha o cofre enquanto a EVE roda.

Sem isto, editar uma memória no Obsidian só valeria no próximo start — e uma
correção que não vale agora não é uma correção, é uma promessa. Aqui o disco
manda: o arquivo mudou, o índice muda atrás.
"""

from __future__ import annotations

from pathlib import Path

from eve.events import EventType
from eve.logging import get_logger
from eve.memory.sync import Resultado, Sincronizador
from eve.watch.base import Observer

log = get_logger(__name__)

DEBOUNCE_MS = 800
"""Salvar no Obsidian dispara vários eventos; esperar um pouco evita reindexar
o mesmo arquivo três vezes. Curto o bastante para parecer imediato."""


class CofreObserver(Observer):
    """Reindexa as notas que mudarem, uma a uma."""

    kind = "cofre"

    def __init__(self, bus: object, sync: Sincronizador) -> None:
        super().__init__(bus, "cofre")  # type: ignore[arg-type]
        self.sync = sync

    async def run(self) -> None:
        from watchfiles import Change, awatch

        raiz = self.sync.vault.preparar()
        async for mudancas in awatch(raiz, debounce=DEBOUNCE_MS, stop_event=self._stop):
            resultado = Resultado()
            for tipo, bruto in mudancas:
                caminho = Path(bruto)
                if caminho.suffix != ".md" or caminho.name.endswith(".md.tmp"):
                    continue
                if tipo is Change.deleted:
                    # Sumiu do disco: some do índice. É o que "o arquivo é a
                    # verdade" significa quando a verdade é uma exclusão.
                    if await self.sync.esquecer_arquivo(caminho):
                        resultado.removidas += 1
                else:
                    await self.sync.indexar(caminho, resultado)

            if resultado.mexeu():
                log.info("cofre.mudou", **resultado.as_dict())
                await self.emit(EventType.MEMORY_WRITTEN, **resultado.as_dict(), origem="cofre")
