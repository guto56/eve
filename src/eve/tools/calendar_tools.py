"""Calendário e lembretes (spec §9, §12).

A EVE afirmou ter marcado um dentista sem ter marcado nada — não existia
ferramenta. Duas coisas resolvem isso: proibir a afirmação (prompt) e dar a
ferramenta (aqui).

As datas viajam decompostas em ano, mês, dia, hora e minuto. Montar a data no
AppleScript com `date "29/08/2026"` depende do formato regional da máquina;
atribuir componente por componente não depende de nada.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import Field, field_validator

from eve.macos.osa import run_applescript_async
from eve.tools.registry import ToolRegistry
from eve.tools.registry import tool as tool_decorator
from eve.tools.spec import RiskLevel, ToolContext, ToolParams

CRIAR = """on run argv
  set titulo to item 1 of argv
  set nomeAgenda to item 2 of argv
  set anotacao to item 3 of argv
  set duracao to (item 9 of argv) as integer

  set inicio to current date
  set year of inicio to (item 4 of argv) as integer
  set month of inicio to (item 5 of argv) as integer
  set day of inicio to (item 6 of argv) as integer
  set hours of inicio to (item 7 of argv) as integer
  set minutes of inicio to (item 8 of argv) as integer
  set seconds of inicio to 0
  set fim to inicio + duracao * minutes

  tell application "Calendar"
    if nomeAgenda is "" then
      set agenda to first calendar whose writable is true
    else
      set agenda to first calendar whose name is nomeAgenda
    end if
    tell agenda
      set novo to make new event with properties {summary:titulo, start date:inicio, end date:fim}
      if anotacao is not "" then set description of novo to anotacao
    end tell
    return (name of agenda)
  end tell
end run"""

LISTAR = """on run argv
  set diasFrente to (item 1 of argv) as integer
  set inicio to current date
  set hours of inicio to 0
  set minutes of inicio to 0
  set seconds of inicio to 0
  set fim to inicio + diasFrente * days
  set saida to ""
  tell application "Calendar"
    repeat with agenda in calendars
      repeat with evt in (every event of agenda whose start date is greater than inicio ¬
                          and start date is less than fim)
        set saida to saida & (summary of evt) & "\\t" & ((start date of evt) as string) & ¬
                     "\\t" & (name of agenda) & linefeed
      end repeat
    end repeat
  end tell
  return saida
end run"""

AGENDAS = """tell application "Calendar" to get name of every calendar"""


class CriarEventoParams(ToolParams):
    title: str = Field(description="Título do evento.", min_length=1, max_length=300)
    start: str = Field(
        description="Início, em ISO 8601 local. Ex.: 2026-08-30T14:00. "
        "Resolva 'amanhã às 14' para a data concreta antes de chamar."
    )
    duration_minutes: int = Field(default=60, ge=5, le=1440)
    calendar: str = Field(default="", description="Agenda. Vazio usa a primeira gravável.")
    notes: str = Field(default="", max_length=2000)

    @field_validator("start")
    @classmethod
    def _iso(cls, valor: str) -> str:
        try:
            datetime.fromisoformat(valor)
        except ValueError:
            raise ValueError(
                f"data inválida: {valor!r}. Use ISO 8601, como 2026-08-30T14:00"
            ) from None
        return valor


class ListarEventosParams(ToolParams):
    days: int = Field(default=7, ge=1, le=90, description="Quantos dias à frente.")


def register_calendar_tools(registry: ToolRegistry) -> ToolRegistry:
    @tool_decorator(
        "calendar.create_event",
        description="Cria um evento no Calendário do macOS.",
        params=CriarEventoParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
        requires=("automation",),
        timeout=30.0,
        keywords=(
            "marcar",
            "marca",
            "agendar",
            "agende",
            "compromisso",
            "reuniao",
            "evento",
            "consulta",
            "dentista",
            "medico",
            "calendario",
            "agenda",
        ),
    )
    async def criar(params: CriarEventoParams, _: ToolContext) -> dict[str, Any]:
        inicio = datetime.fromisoformat(params.start)
        agenda = await run_applescript_async(
            CRIAR,
            params.title,
            params.calendar,
            params.notes,
            str(inicio.year),
            str(inicio.month),
            str(inicio.day),
            str(inicio.hour),
            str(inicio.minute),
            str(params.duration_minutes),
        )
        fim = inicio + timedelta(minutes=params.duration_minutes)
        return {
            "created": params.title,
            "calendar": agenda,
            "start": inicio.isoformat(timespec="minutes"),
            "end": fim.isoformat(timespec="minutes"),
        }

    @tool_decorator(
        "calendar.list_events",
        description="Lista os próximos compromissos do Calendário.",
        params=ListarEventosParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        requires=("automation",),
        timeout=45.0,
        keywords=("agenda", "compromisso", "calendario", "proximos", "hoje", "semana"),
    )
    async def listar(params: ListarEventosParams, _: ToolContext) -> dict[str, Any]:
        bruto = await run_applescript_async(LISTAR, str(params.days), timeout=40)
        eventos = []
        for linha in bruto.splitlines():
            partes = linha.split("\t")
            if len(partes) >= 3 and partes[0].strip():
                eventos.append({"title": partes[0], "when": partes[1], "calendar": partes[2]})
        return {"events": eventos, "count": len(eventos), "days": params.days}

    @tool_decorator(
        "calendar.list_calendars",
        description="Lista as agendas disponíveis.",
        params=ToolParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        requires=("automation",),
    )
    async def agendas(_: ToolParams, __: ToolContext) -> dict[str, Any]:
        bruto = await run_applescript_async(AGENDAS)
        nomes = [n.strip() for n in bruto.split(",") if n.strip()]
        return {"calendars": nomes, "count": len(nomes)}

    return registry
