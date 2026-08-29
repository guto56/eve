"""Da percepção à ação (spec §29).

    evento
      ↓  política      quanto isso importa?
      ↓  interrupção   vale interromper agora?
    notificação

A notificação sai pelo Tool Bus, como qualquer outra ação: passa por permissão
e fica na auditoria. A EVE não tem um caminho privilegiado para falar com o
usuário só porque a ideia partiu dela.
"""

from __future__ import annotations

import asyncio
from typing import Any

from eve.bus import EventBus
from eve.events import Event
from eve.logging import get_logger
from eve.proactive.policy import Policy, titulo_para
from eve.tools.bus import ToolBus

log = get_logger(__name__)

#: Só estes tipos passam pela política; o resto do barramento é ruído interno
#: (deltas de mensagem, chamadas de ferramenta, eventos de voz).
OBSERVAVEIS = (
    "file.*",
    "git.*",
    "app.opened",
    "app.closed",
    "build.*",
    "test.*",
    "system.error",
    "notification",
    "task.finished",
    "mcp.*",
)


class ProactiveEngine:
    def __init__(self, bus: EventBus, tools: ToolBus, policy: Policy) -> None:
        self.bus = bus
        self.tools = tools
        self.policy = policy
        self.notified = 0
        self.considered = 0
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        with self.bus.subscribe(OBSERVAVEIS) as inscricao:
            while True:
                evento = await inscricao.queue.get()
                try:
                    await self.handle(evento)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("proativo.falhou", tipo=evento.type, error=str(exc)[:160])

    async def handle(self, evento: Event) -> bool:
        """Avalia um evento. ``True`` se notificou."""
        self.considered += 1
        decisao = self.policy.decide(evento)
        await self.bus.emit(
            "proactive.evaluated",
            source="proactive",
            event_type=evento.type,
            priority=decisao.priority.value,
            notify=decisao.notify,
            reason=decisao.reason,
        )
        if not decisao.notify:
            return False

        resultado = await self.tools.call(
            "system.notify",
            {
                "message": _mensagem(evento),
                "title": titulo_para(evento),
                "sound": decisao.sound,
            },
            source="proactive",
            caller="eve",
        )
        if resultado.ok:
            self.notified += 1
        else:
            log.info("proativo.notificacao_falhou", error=resultado.error)
        return resultado.ok

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.policy.enabled,
            "running": self._task is not None and not self._task.done(),
            "considered": self.considered,
            "notified": self.notified,
            "quiet_hours": list(self.policy.quiet_hours) if self.policy.quiet_hours else None,
            "min_interval": self.policy.min_interval,
            "rules": dict(self.policy.rules),
        }


def _mensagem(evento: Event) -> str:
    """Uma frase curta a partir do evento. Notificação não é relatório."""
    payload = evento.payload
    if texto := payload.get("message"):
        return str(texto)[:200]
    if evento.type == "file.changed":
        notaveis = payload.get("notable") or []
        if notaveis:
            arquivos = ", ".join(n["file"] for n in notaveis[:3])
            return f"Mudou {arquivos} em {_pasta(payload)}"
        return f"{payload.get('count', 0)} arquivo(s) mudaram em {_pasta(payload)}"
    if evento.type == "git.changed":
        return f"Novo commit em {_pasta(payload)}"
    if evento.type in ("app.opened", "app.closed"):
        verbo = "abriu" if evento.type.endswith("opened") else "fechou"
        return f"{payload.get('app', 'Um aplicativo')} {verbo}"
    if evento.type == "task.finished":
        tarefa = payload.get("task") or {}
        return f"Terminei: {str(tarefa.get('goal', ''))[:120]}"
    return evento.type


def _pasta(payload: dict[str, Any]) -> str:
    caminho = str(payload.get("path", ""))
    return caminho.rsplit("/", 1)[-1] or caminho
