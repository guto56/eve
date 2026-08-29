"""Quando falar, e com que insistência (spec §33).

A diferença entre um assistente útil e um incômodo não está no que ele percebe:
está no que ele decide que vale interromper. Por isso a política é explícita,
configurável e conservadora por padrão — quase tudo nasce em SILENCIOSO.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from eve.events import Event, EventType, matches


class Priority(StrEnum):
    SILENT = "silent"
    """Só entra no histórico. Ninguém é interrompido."""

    LOW = "low"
    """Aparece na interface, sem notificação do sistema."""

    MEDIUM = "medium"
    """Notificação do macOS."""

    HIGH = "high"
    """Notificação com som."""

    CRITICAL = "critical"
    """Interrompe de verdade. Futuramente, uma ligação (spec §32)."""


ORDEM = {
    Priority.SILENT: 0,
    Priority.LOW: 1,
    Priority.MEDIUM: 2,
    Priority.HIGH: 3,
    Priority.CRITICAL: 4,
}

#: Padrões conservadores. O usuário sobe o que quiser em `[proactive.rules]`.
PADRAO: dict[str, Priority] = {
    "build.failed": Priority.HIGH,
    "test.failed": Priority.MEDIUM,
    "git.changed": Priority.SILENT,
    "file.changed": Priority.SILENT,
    "app.opened": Priority.SILENT,
    "app.closed": Priority.SILENT,
    "notification": Priority.MEDIUM,
    "system.error": Priority.MEDIUM,
    "mcp.disconnected": Priority.LOW,
    "task.finished": Priority.LOW,
    "*": Priority.SILENT,
}


@dataclass
class Decision:
    priority: Priority
    notify: bool
    reason: str

    @property
    def sound(self) -> bool:
        return ORDEM[self.priority] >= ORDEM[Priority.HIGH]


@dataclass
class Policy:
    rules: dict[str, str] = field(default_factory=dict)
    quiet_hours: tuple[int, int] | None = None
    """Faixa (início, fim) em que nada interrompe. Ex.: (22, 8)."""
    min_interval: float = 60.0
    """Segundos entre notificações do mesmo tipo. Um observador de arquivos
    dispara dezenas de eventos por minuto; sem isto a EVE vira spam."""
    enabled: bool = True

    _ultima: dict[str, float] = field(default_factory=dict, repr=False)

    def priority_for(self, event_type: str) -> Priority:
        """Quem configurou vence o padrão; entre iguais, o mais específico.

        A ordem importa: uma regra do usuário `build.* = critical` tem de
        vencer o padrão embutido `build.failed = high`. Misturar as duas
        fontes num dicionário só fazia a especificidade do padrão derrubar a
        escolha explícita de quem usa.
        """
        for fonte in (self.rules, PADRAO):
            achado = _procurar(fonte, event_type)
            if achado is not None:
                return achado
        return Priority.SILENT

    def decide(self, event: Event, agora: float | None = None) -> Decision:
        prioridade = self.priority_for(event.type)

        if not self.enabled:
            return Decision(prioridade, False, "proatividade desligada")
        if prioridade is Priority.SILENT:
            return Decision(prioridade, False, "prioridade silenciosa")
        if ORDEM[prioridade] < ORDEM[Priority.MEDIUM]:
            return Decision(prioridade, False, "aparece na interface, sem notificar")

        agora = agora if agora is not None else time.time()
        if self._em_silencio(agora) and prioridade is not Priority.CRITICAL:
            return Decision(prioridade, False, "horário de silêncio")

        ultima = self._ultima.get(event.type, 0.0)
        if agora - ultima < self.min_interval and prioridade is not Priority.CRITICAL:
            espera = self.min_interval - (agora - ultima)
            return Decision(prioridade, False, f"repetido demais (faltam {espera:.0f}s)")

        self._ultima[event.type] = agora
        return Decision(prioridade, True, "notificar")

    def _em_silencio(self, agora: float) -> bool:
        if self.quiet_hours is None:
            return False
        inicio, fim = self.quiet_hours
        hora = time.localtime(agora).tm_hour
        if inicio <= fim:
            return inicio <= hora < fim
        return hora >= inicio or hora < fim  # faixa que cruza a meia-noite


def _procurar(regras: dict[str, Any], event_type: str) -> Priority | None:
    """Nome exato, depois padrão mais longo, depois `*`."""
    if event_type in regras:
        return _para_prioridade(regras[event_type])
    candidatos = [
        (padrao, valor)
        for padrao, valor in regras.items()
        if padrao != "*" and matches(padrao, event_type)
    ]
    if candidatos:
        candidatos.sort(key=lambda item: len(item[0]), reverse=True)
        return _para_prioridade(candidatos[0][1])
    if "*" in regras:
        return _para_prioridade(regras["*"])
    return None


def _para_prioridade(valor: str | Priority) -> Priority:
    if isinstance(valor, Priority):
        return valor
    try:
        return Priority(str(valor).lower())
    except ValueError:
        return Priority.SILENT


TITULOS: dict[str, str] = {
    EventType.SYSTEM_ERROR: "Algo deu errado",
    "build.failed": "Build falhou",
    "test.failed": "Testes falharam",
    "task.finished": "Tarefa concluída",
}


def titulo_para(event: Event) -> str:
    return TITULOS.get(event.type, "EVE")
