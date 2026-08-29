"""Comandos de memória."""

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

memory_app = typer.Typer(name="memory", help="Memória da EVE.", no_args_is_help=True)

CAMADA_ESTILO = {
    "semantic": "cyan",
    "episodic": "magenta",
    "procedural": "yellow",
    "working": "dim",
}


def _api() -> str:
    settings = load_settings()
    if process.probe_health(settings) is None:
        err_console.print("[red]A EVE não está rodando.[/red] Use [bold]eve start[/bold].")
        raise typer.Exit(1)
    return process.base_url(settings)


def _tabela(memorias: list[dict[str, Any]], mostrar_escore: bool = False) -> Table:
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("ID", style="dim")
    table.add_column("Camada")
    table.add_column("Memória")
    table.add_column("Quando", style="dim")
    for m in memorias:
        quando = datetime.fromtimestamp(m["updated_at"]).strftime("%d/%m %H:%M")
        camada = m["kind"]
        table.add_row(
            m["uid"][:8],
            f"[{CAMADA_ESTILO.get(camada, '')}]{camada}[/]",
            m["content"][:78],
            quando,
        )
    return table


@memory_app.command("list")
def listar(
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    kind: Annotated[str | None, typer.Option("--kind", "-k", help="Filtrar por camada.")] = None,
) -> None:
    """Lista as memórias mais recentes."""
    params: dict[str, Any] = {"limit": limit}
    if kind:
        params["kind"] = kind
    data = httpx.get(f"{_api()}/api/memory/recent", params=params, timeout=30).json()
    if not data["memories"]:
        console.print("[dim]Nenhuma memória ainda.[/dim]")
        return
    console.print(_tabela(data["memories"]))


@memory_app.command("search")
def buscar(
    query: str,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 10,
) -> None:
    """Busca na memória (textual + semântica)."""
    data = httpx.get(
        f"{_api()}/api/memory/search", params={"q": query, "limit": limit}, timeout=60
    ).json()
    if not data["memories"]:
        console.print("[dim]Nada encontrado.[/dim]")
        return
    console.print(_tabela(data["memories"], mostrar_escore=True))


@memory_app.command("add")
def adicionar(
    content: str,
    kind: Annotated[str, typer.Option("--kind", "-k")] = "semantic",
    importance: Annotated[float, typer.Option("--importance", "-i")] = 0.6,
) -> None:
    """Guarda um fato na memória."""
    response = httpx.post(
        f"{_api()}/api/memory",
        json={"content": content, "kind": kind, "importance": importance},
        timeout=60,
    )
    if response.status_code != 200:
        err_console.print(f"[red]{response.json().get('detail', response.text)}[/red]")
        raise typer.Exit(1)
    data = response.json()
    estilo = {"nova": "green", "reforçada": "yellow", "descartada": "dim"}[data["estado"]]
    console.print(f"[{estilo}]{data['estado']}[/] [dim]{data['uid'][:8]}[/dim] {data['content']}")


@memory_app.command("forget")
def esquecer(uid: str) -> None:
    """Apaga uma memória pelo id."""
    base = _api()
    if len(uid) < 16:
        recentes = httpx.get(f"{base}/api/memory/recent", params={"limit": 200}, timeout=30).json()
        completos = [m["uid"] for m in recentes["memories"] if m["uid"].startswith(uid)]
        if len(completos) != 1:
            err_console.print(
                f"[red]{'nenhuma' if not completos else 'mais de uma'} memória com id {uid}[/red]"
            )
            raise typer.Exit(1)
        uid = completos[0]
    response = httpx.delete(f"{base}/api/memory/{uid}", timeout=30)
    if response.status_code != 200:
        err_console.print("[red]Memória desconhecida.[/red]")
        raise typer.Exit(1)
    console.print("[green]Esquecida.[/green]")


@memory_app.command("stats")
def estatisticas() -> None:
    """Estado da memória."""
    data = httpx.get(f"{_api()}/api/memory", timeout=30).json()
    console.print(f"[bold]{data['total']}[/bold] memória(s) · {data['bytes'] / 1024:.0f} KB")
    for camada, n in sorted(data["por_camada"].items()):
        console.print(f"  [{CAMADA_ESTILO.get(camada, '')}]{camada:11}[/] {n}")
    if data["busca_semantica"]:
        console.print(
            f"\n[green]busca híbrida ativa[/green] [dim]({data['detalhe_vetorial']}, "
            f"{data['com_vetor']} vetorizada(s))[/dim]"
        )
    else:
        console.print(f"\n[yellow]só busca textual[/yellow] [dim]{data['detalhe_vetorial']}[/dim]")
    console.print(f"[dim]{data['arquivo']}[/dim]")
