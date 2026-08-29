"""Comandos de tarefas de agente."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import httpx2 as httpx
import typer
from rich.console import Console
from rich.table import Table

from eve.cli import process
from eve.config import load_settings

console = Console()
err_console = Console(stderr=True)

task_app = typer.Typer(name="task", help="Tarefas de agente.", no_args_is_help=True)

ESTADO = {
    "planning": ("cyan", "planejando"),
    "running": ("yellow", "rodando"),
    "done": ("green", "concluída"),
    "failed": ("red", "falhou"),
    "cancelled": ("dim", "cancelada"),
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


@task_app.command("list")
def task_list(
    limit: Annotated[int, typer.Option("--limit", "-n")] = 15,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Lista as tarefas recentes."""
    data = _pedido("GET", "/api/tasks", params={"limit": limit})
    if as_json:
        console.print_json(data=data)
        return
    if not data["tasks"]:
        console.print("[dim]Nenhuma tarefa ainda.[/dim]")
        return
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("ID", style="dim")
    table.add_column("Estado")
    table.add_column("Objetivo")
    table.add_column("Passos", justify="right")
    table.add_column("Tempo", justify="right")
    for tarefa in data["tasks"]:
        cor, rotulo = ESTADO.get(tarefa["status"], ("", tarefa["status"]))
        table.add_row(
            tarefa["id"][:8],
            f"[{cor}]{rotulo}[/]",
            tarefa["goal"][:52],
            str(tarefa["steps_done"]),
            f"{tarefa['duration_ms'] / 1000:.0f}s",
        )
    console.print(table)
    if data["active"]:
        console.print(f"\n[yellow]{data['active']} em andamento[/yellow]")


@task_app.command("show")
def task_show(task_id: str) -> None:
    """Mostra o plano e os passos de uma tarefa."""
    data = _pedido("GET", "/api/tasks", params={"limit": 100})
    completo = next((t["id"] for t in data["tasks"] if t["id"].startswith(task_id)), None)
    if completo is None:
        err_console.print(f"[red]Tarefa desconhecida:[/red] {task_id}")
        raise typer.Exit(1)

    tarefa = _pedido("GET", f"/api/tasks/{completo}")
    cor, rotulo = ESTADO.get(tarefa["status"], ("", tarefa["status"]))
    console.print(f"[bold]{tarefa['goal']}[/bold]")
    console.print(
        f"[{cor}]{rotulo}[/] · {tarefa['steps_done']} passo(s) · "
        f"{tarefa['duration_ms'] / 1000:.1f}s · "
        f"{datetime.fromtimestamp(tarefa['created_at']):%d/%m %H:%M}\n"
    )

    if tarefa["plan"]:
        console.print("[bold]Plano[/bold]")
        for i, passo in enumerate(tarefa["plan"], 1):
            console.print(f"  {i}. [dim]{passo}[/dim]")
        console.print()

    if tarefa["steps"]:
        console.print("[bold]O que foi feito[/bold]")
        for passo in tarefa["steps"]:
            marca = "[green]●[/green]" if passo["ok"] else "[red]✕[/red]"
            espera = ""
            console.print(
                f"  {marca} [magenta]{passo['tool']}[/magenta] "
                f"[dim]{passo['duration_ms']:.0f}ms{espera}[/dim]"
            )
            if passo["error"]:
                console.print(f"      [red]{passo['error'][:88]}[/red]")

    if tarefa["result"]:
        console.print(f"\n[bold]Resposta[/bold]\n{tarefa['result']}")
    if tarefa["error"]:
        console.print(f"\n[red]{tarefa['error']}[/red]")


@task_app.command("cancel")
def task_cancel(task_id: str) -> None:
    """Cancela uma tarefa em andamento."""
    data = _pedido("GET", "/api/tasks", params={"limit": 100})
    completo = next((t["id"] for t in data["tasks"] if t["id"].startswith(task_id)), None)
    if completo is None:
        err_console.print(f"[red]Tarefa desconhecida:[/red] {task_id}")
        raise typer.Exit(1)
    _pedido("POST", f"/api/tasks/{completo}/cancel")
    console.print(f"[yellow]{completo[:8]} cancelada.[/yellow]")
