"""Rodar a EVE no terminal, com o que está acontecendo à vista.

`eve start` deixa a EVE em segundo plano, que é o certo para o dia a dia — mas
some. Sem nada na tela não dá para saber se ela está viva, o que ela decidiu
nem por que demorou. Este modo prende o terminal de propósito: você vê o fluxo
e encerra com Ctrl+C.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import uvicorn
from rich.console import Console

from eve import __version__
from eve.bus import EventBus
from eve.config import Settings
from eve.daemon.app import create_app
from eve.events import Event
from eve.logging import configure_logging
from eve.paths import paths

console = Console()

#: Eventos que interessam a quem está olhando. Deltas de token e
#: `client.connected` são ruído numa tela de acompanhamento.
TOPICOS = (
    "system.*",
    "message.received",
    "message.completed",
    "message.failed",
    "router.decided",
    "tool.*",
    "memory.written",
    "task.*",
    "proactive.evaluated",
    "voice.*",
    "file.changed",
    "git.changed",
    "mcp.*",
)


PRAZO_SAIDA = 12.0
"""Quanto o desligamento inteiro pode demorar antes de sairmos à força.

Ctrl+C só vale se encerrar. Uma etapa emperrada — um servidor MCP que não
responde, uma conexão que não fecha — não pode transformar "parar" em "torcer
para parar".
"""


class _Servidor(uvicorn.Server):
    """Uvicorn sem os próprios tratadores de sinal.

    Os dele apenas marcam ``should_exit`` e ficam calados; quem apertou Ctrl+C
    fica olhando uma tela parada sem saber se foi ouvido.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _atender_ctrl_c(servidor: uvicorn.Server) -> None:
    """Responde na hora, com prazo, e sai à força se insistirem."""
    laco = asyncio.get_running_loop()
    pedidos = 0

    def forcar(motivo: str) -> None:
        console.print(f"\n[yellow]{motivo}[/yellow]")
        sys.stdout.flush()
        os._exit(130)

    def parar() -> None:
        nonlocal pedidos
        pedidos += 1
        if pedidos > 1:
            forcar("Encerrando à força.")
        console.print("\n[yellow]encerrando[/yellow] [dim]— Ctrl+C de novo força a saída[/dim]")
        servidor.should_exit = True
        laco.call_later(PRAZO_SAIDA, forcar, f"O desligamento passou de {PRAZO_SAIDA:.0f}s.")

    for sinal in (signal.SIGINT, signal.SIGTERM):
        laco.add_signal_handler(sinal, parar)


async def serve(settings: Settings, verbose: bool = False) -> None:
    """Sobe a EVE e acompanha até o Ctrl+C."""
    p = paths().ensure()
    configure_logging(
        level=settings.log.level,
        json_format=settings.log.json_format,
        log_file=p.log_file,
        force=True,
        console=verbose,
    )

    app = create_app(settings)
    bus: EventBus = app.state.bus

    _cabecalho(settings, app)
    acompanhamento = asyncio.create_task(_acompanhar(bus))

    config = uvicorn.Config(
        app=app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
        access_log=False,
        # Uma conexão aberta não pode segurar o Ctrl+C.
        timeout_graceful_shutdown=int(PRAZO_SAIDA),
    )
    servidor = _Servidor(config)
    _atender_ctrl_c(servidor)

    try:
        await servidor.serve()
    finally:
        acompanhamento.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await acompanhamento
        console.print("\n[dim]EVE encerrada. Modelo local descarregado da memória.[/dim]")


def _cabecalho(settings: Settings, app: Any) -> None:
    endereco = f"http://{settings.server.host}:{settings.server.port}"
    ferramentas = len(app.state.tools.registry)
    console.print()
    console.print(f"  [bold]EVE[/bold] [dim]{__version__}[/dim]   [cyan]{endereco}[/cyan]")
    console.print(
        f"  [dim]{ferramentas} ferramentas · modelo local {settings.ai.local_model} · "
        f"externo {settings.ai.external_model}[/dim]"
    )
    console.print(f"  [dim]arquivos em {paths().work} · logs em {paths().log_file}[/dim]")
    console.print("\n  [dim]Ctrl+C para parar a EVE e liberar a memória do modelo.[/dim]\n")


async def _acompanhar(bus: EventBus) -> None:
    with bus.subscribe(TOPICOS) as inscricao:
        while True:
            evento = await inscricao.queue.get()
            linha = _linha(evento)
            if linha:
                hora = datetime.fromtimestamp(evento.ts).strftime("%H:%M:%S")
                console.print(f"[dim]{hora}[/dim]  {linha}", highlight=False)


def _linha(evento: Event) -> str:
    """Uma linha por evento, ou vazio para o que não vale mostrar."""
    dados = evento.payload
    match evento.type:
        case "system.started":
            return "[green]no ar[/green]"
        case "system.stopping":
            # Quem apertou Ctrl+C já foi avisado na hora, por _atender_ctrl_c.
            return ""
        case "message.received":
            return f"[bold]>[/bold] {_curto(dados.get('text'), 90)}"
        case "message.completed":
            return f"[dim]respondido em {dados.get('duration_ms', 0):.0f} ms[/dim]"
        case "message.failed":
            return f"[red]falhou:[/red] {_curto(dados.get('error'), 90)}"
        case "router.decided":
            marca = " [cyan]sem modelo[/cyan]" if dados.get("fast_path") else ""
            return (
                f"[dim]rota[/dim] {dados.get('route')} "
                f"[dim]via {dados.get('decided_by')}, {dados.get('latency_ms', 0):.0f} ms, "
                f"{len(dados.get('tools') or [])} ferramentas[/dim]{marca}"
            )
        case "tool.requested":
            return f"[magenta]→[/magenta] {dados.get('tool')} [dim]{_args(dados)}[/dim]"
        case "tool.confirmation_required":
            return f"[yellow]aguardando você autorizar[/yellow] {dados.get('tool')}"
        case "tool.completed":
            return f"[dim]  ok em {dados.get('duration_ms', 0):.0f} ms[/dim]"
        case "tool.failed":
            return f"[red]  {dados.get('error_kind')}:[/red] {_curto(dados.get('error'), 70)}"
        case "tool.denied":
            return f"[yellow]  negado:[/yellow] {_curto(dados.get('reason'), 70)}"
        case "memory.written":
            return f"[cyan]lembrei[/cyan] [dim]{_curto(dados.get('content'), 70)}[/dim]"
        case "task.started":
            return f"[blue]tarefa[/blue] {_curto((dados.get('task') or {}).get('goal'), 70)}"
        case "task.finished":
            tarefa = dados.get("task") or {}
            return f"[blue]tarefa concluída[/blue] [dim]{tarefa.get('steps_done', 0)} passos[/dim]"
        case "proactive.evaluated":
            if not dados.get("notify"):
                return ""
            return f"[yellow]notifiquei[/yellow] [dim]{dados.get('event_type')}[/dim]"
        case "voice.listening":
            return "[green]ouvindo[/green]" if dados.get("on") else "[dim]parou de ouvir[/dim]"
        case "voice.transcript":
            return f"[bold]voz[/bold] {_curto(dados.get('text'), 80)}"
        case "file.changed":
            return f"[dim]arquivos mudaram em {dados.get('path', '')}[/dim]"
        case "mcp.connected":
            return f"[green]MCP[/green] {dados.get('servidor')}"
        case _:
            return ""


def _args(dados: dict[str, Any]) -> str:
    import json

    argumentos = dados.get("args") or {}
    if not argumentos:
        return ""
    return _curto(json.dumps(argumentos, ensure_ascii=False), 70)


def _curto(valor: Any, limite: int) -> str:
    texto = str(valor or "").replace("\n", " ")
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"
