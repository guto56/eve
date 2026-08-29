"""Comandos de observadores e proatividade."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import httpx2 as httpx
import typer
from rich.console import Console
from rich.table import Table

from eve.cli import process
from eve.config import load_settings
from eve.proactive.policy import PADRAO, Policy

console = Console()
err_console = Console(stderr=True)

watch_app = typer.Typer(name="watch", help="O que a EVE observa.", no_args_is_help=True)

CORES = {
    "silent": "dim",
    "low": "cyan",
    "medium": "yellow",
    "high": "magenta",
    "critical": "red",
}


def _pedido(metodo: str, caminho: str, **kwargs: Any) -> Any:
    settings = load_settings()
    if process.probe_health(settings) is None:
        err_console.print("[red]A EVE não está rodando.[/red] Use [bold]eve start[/bold].")
        raise typer.Exit(1)
    resposta = httpx.request(
        metodo, f"{process.base_url(settings)}{caminho}", timeout=30.0, **kwargs
    )
    if resposta.status_code >= 400:
        err_console.print(f"[red]{resposta.json().get('detail', resposta.text)}[/red]")
        raise typer.Exit(1)
    return resposta.json()


@watch_app.command("list")
def watch_list() -> None:
    """Lista o que está sendo observado."""
    data = _pedido("GET", "/api/watch")
    if not data["observers"]:
        console.print("[dim]Nada sendo observado.[/dim]")
        console.print("[dim]adicione com: eve watch add projeto ~/Documents/EVE[/dim]")
        return
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("")
    table.add_column("Nome", style="bold")
    table.add_column("Tipo")
    table.add_column("Eventos", justify="right")
    table.add_column("")
    for obs in data["observers"]:
        marca = "[green]✓[/green]" if obs["running"] else "[red]✗[/red]"
        table.add_row(
            marca,
            obs["name"],
            obs["kind"],
            str(obs["events"]),
            f"[dim]{obs['error'] or ''}[/dim]",
        )
    console.print(table)


@watch_app.command("add")
def watch_add(
    name: str,
    path: Annotated[Path, typer.Argument(help="Pasta a observar.")],
) -> None:
    """Passa a observar uma pasta."""
    obs = _pedido("POST", "/api/watch", json={"name": name, "path": str(path)})
    if obs["running"]:
        console.print(f"[green]{name}[/green] observando [dim]{path}[/dim]")
    else:
        err_console.print(f"[red]{name} não subiu:[/red] {obs['error']}")
        raise typer.Exit(1)


@watch_app.command("remove")
def watch_remove(name: str) -> None:
    """Para de observar."""
    _pedido("DELETE", f"/api/watch/{name}")
    console.print(f"[green]{name} removido.[/green]")


@watch_app.command("status")
def watch_status() -> None:
    """Estado da proatividade e das regras em vigor."""
    data = _pedido("GET", "/api/proactive")
    estado = "[green]ligada[/green]" if data["enabled"] else "[yellow]desligada[/yellow]"
    console.print(f"Proatividade {estado}")
    console.print(
        f"[dim]{data['considered']} evento(s) avaliado(s), "
        f"{data['notified']} notificação(ões)[/dim]"
    )
    if data["quiet_hours"]:
        inicio, fim = data["quiet_hours"]
        console.print(f"[dim]silêncio das {inicio}h às {fim}h[/dim]")
    console.print(f"[dim]intervalo mínimo entre avisos iguais: {data['min_interval']:.0f}s[/dim]")

    console.print("\n[bold]Prioridades[/bold]")
    regras = {**PADRAO, **data["rules"]}
    for tipo, prioridade in sorted(regras.items()):
        valor = prioridade.value if hasattr(prioridade, "value") else str(prioridade)
        marca = " [dim](sua)[/dim]" if tipo in data["rules"] else ""
        console.print(f"  {tipo:22} [{CORES.get(valor, '')}]{valor}[/]{marca}")


@watch_app.command("simulate")
def watch_simulate(
    event_type: Annotated[str, typer.Argument(help="Tipo do evento, ex.: build.failed.")],
    message: Annotated[str, typer.Option("--message", "-m")] = "teste de proatividade",
) -> None:
    """Simula um evento para ver o que a EVE faria com ele.

    Não dispara notificação: só mostra a decisão da política.
    """
    data = _pedido("GET", "/api/proactive")
    politica = Policy(
        rules=data["rules"],
        quiet_hours=tuple(data["quiet_hours"]) if data["quiet_hours"] else None,
        min_interval=data["min_interval"],
        enabled=data["enabled"],
    )
    from eve.events import Event

    decisao = politica.decide(Event(type=event_type, payload={"message": message}))
    cor = CORES.get(decisao.priority.value, "")
    console.print(f"{event_type} → [{cor}]{decisao.priority.value}[/]")
    verbo = "notificaria" if decisao.notify else "não notificaria"
    console.print(f"[dim]{verbo}: {decisao.reason}[/dim]")
