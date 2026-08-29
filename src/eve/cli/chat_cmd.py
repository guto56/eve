"""Conversa pelo terminal.

Consome o mesmo endpoint SSE que a interface web vai consumir, e responde às
confirmações de ferramenta pelo mesmo fluxo de aprovação da Fase 2.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Annotated, Any

import httpx2 as httpx
import typer
from rich.console import Console

from eve.cli import process
from eve.config import load_settings

console = Console()
err_console = Console(stderr=True)

RISK_STYLE = {"safe": "green", "confirm": "yellow", "privileged": "magenta", "blocked": "red"}


def _require_daemon() -> str:
    settings = load_settings()
    if process.probe_health(settings) is None:
        err_console.print("[red]A EVE não está rodando.[/red] Use [bold]eve start[/bold].")
        raise typer.Exit(1)
    return process.base_url(settings)


class _ApprovalWatcher:
    """Enquanto uma mensagem é processada, atende confirmações no terminal."""

    def __init__(self, base: str, auto_yes: bool) -> None:
        self.base = base
        self.auto_yes = auto_yes
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handled: set[str] = set()

    def __enter__(self) -> _ApprovalWatcher:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                pendentes = httpx.get(f"{self.base}/api/approvals", timeout=5.0).json()["pending"]
            except httpx.HTTPError:  # pragma: no cover
                pendentes = []
            for item in pendentes:
                if item["id"] in self._handled:
                    continue
                self._handled.add(item["id"])
                aprovado = self.auto_yes or _prompt(item)
                try:
                    httpx.post(
                        f"{self.base}/api/approvals/{item['id']}",
                        json={"approved": aprovado, "by": "cli"},
                        timeout=5.0,
                    )
                except httpx.HTTPError:  # pragma: no cover
                    pass
            self._stop.wait(0.15)


def _prompt(item: dict[str, Any]) -> bool:
    risco = item["risk"]
    console.print()
    console.print(
        f"[bold]A EVE quer executar[/bold] [cyan]{item['tool']}[/cyan] "
        f"([{RISK_STYLE.get(risco, '')}]{risco}[/])"
    )
    if item["args"]:
        console.print(f"  [dim]{json.dumps(item['args'], ensure_ascii=False)}[/dim]")
    return typer.confirm("Autorizar?", default=False)


def _send(
    base: str, message: str, session: str | None, verbose: bool, auto_yes: bool
) -> str | None:
    payload = {"message": message, "session": session, "stream": True}
    nova_sessao = session
    inicio = time.perf_counter()
    primeiro: float | None = None
    fontes: list[str] = []

    with _ApprovalWatcher(base, auto_yes):
        with httpx.stream("POST", f"{base}/api/chat", json=payload, timeout=600.0) as response:
            if response.status_code != 200:
                err_console.print(f"[red]{response.read().decode('utf-8', 'replace')[:400]}[/red]")
                raise typer.Exit(1)
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                evento = json.loads(line[5:].strip())
                kind = evento["kind"]
                if kind == "session":
                    nova_sessao = evento["session"]
                elif kind == "routed" and verbose:
                    marca = " [rápido]" if evento["fast_path"] else ""
                    console.print(
                        f"[dim]rota {evento['route']} via {evento['decided_by']} "
                        f"({evento['latency_ms']:.0f} ms, {len(evento['tools'])} ferramentas)"
                        f"{marca}[/dim]"
                    )
                elif kind == "tool":
                    args = json.dumps(evento["arguments"], ensure_ascii=False)
                    console.print(f"[magenta]→ {evento['name']}[/magenta][dim]{args}[/dim]")
                elif kind == "tool_result" and not evento["ok"]:
                    console.print(f"[red]  {evento['error_kind']}: {evento['error']}[/red]")
                elif kind == "tool_result" and evento["name"] == "web.search":
                    fontes.extend((evento.get("value") or {}).get("sources") or [])
                elif kind == "delta":
                    if primeiro is None:
                        primeiro = (time.perf_counter() - inicio) * 1000
                    console.print(evento["text"], end="", highlight=False, markup=False)
                elif kind == "error":
                    console.print()
                    tipo = evento.get("kind_detail") or evento.get("kind", "erro")
                    err_console.print(f"[red]{tipo}:[/red] {evento['error']}")
                elif kind == "done":
                    console.print()
                    for fonte in dict.fromkeys(fontes):
                        console.print(f"[dim]  {fonte}[/dim]")
                    if verbose:
                        console.print(
                            f"[dim]{evento.get('duration_ms', 0):.0f} ms"
                            f"{f', primeiro token {primeiro:.0f} ms' if primeiro else ''}[/dim]"
                        )
    return nova_sessao


def chat(
    message: Annotated[
        str | None, typer.Argument(help="Mensagem. Sem ela, abre uma conversa contínua.")
    ] = None,
    session: Annotated[str | None, typer.Option("--session", help="Continuar uma sessão.")] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Mostra rota, latência e ferramentas.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Autoriza confirmações automaticamente.")
    ] = False,
) -> None:
    """Conversa com a EVE."""
    base = _require_daemon()

    if message:
        _send(base, message, session, verbose, yes)
        return

    console.print("[bold]EVE[/bold] [dim]— Ctrl+D ou 'sair' para encerrar[/dim]\n")
    while True:
        try:
            entrada = console.input("[bold cyan]> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]até mais.[/dim]")
            return
        if not entrada:
            continue
        if entrada.lower() in {"sair", "exit", "quit", ":q"}:
            console.print("[dim]até mais.[/dim]")
            return
        session = _send(base, entrada, session, verbose, yes)
        console.print()
