"""Comandos de Skills e servidores MCP."""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx2 as httpx
import typer
from rich.console import Console
from rich.table import Table

from eve.cli import process
from eve.config import load_settings

console = Console()
err_console = Console(stderr=True)

skill_app = typer.Typer(name="skill", help="Skills da EVE.", no_args_is_help=True)
mcp_app = typer.Typer(name="mcp", help="Servidores MCP.", no_args_is_help=True)


def _api() -> str:
    settings = load_settings()
    if process.probe_health(settings) is None:
        err_console.print("[red]A EVE não está rodando.[/red] Use [bold]eve start[/bold].")
        raise typer.Exit(1)
    return process.base_url(settings)


def _pedido(metodo: str, caminho: str, **kwargs: Any) -> Any:
    resposta = httpx.request(metodo, f"{_api()}{caminho}", timeout=180.0, **kwargs)
    if resposta.status_code >= 400:
        detalhe = resposta.json().get("detail", resposta.text)
        err_console.print(f"[red]{detalhe}[/red]")
        raise typer.Exit(1)
    return resposta.json()


# ------------------------------------------------------------------- Skills


@skill_app.command("list")
def skill_list(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Lista as Skills instaladas."""
    data = _pedido("GET", "/api/skills")
    if as_json:
        console.print_json(data=data)
        return
    if data["skills"]:
        table = Table(box=None, padding=(0, 2, 0, 0))
        table.add_column("")
        table.add_column("Skill", style="bold")
        table.add_column("Descrição")
        table.add_column("Estado")
        for skill in data["skills"]:
            if skill["missing_secrets"]:
                marca, estado = "[red]✗[/red]", f"falta {', '.join(skill['missing_secrets'])}"
            elif skill["enabled"]:
                marca, estado = "[green]✓[/green]", "ligada"
            else:
                marca, estado = "[dim]·[/dim]", "desligada"
            table.add_row(marca, skill["name"], skill["description"][:52], f"[dim]{estado}[/dim]")
        console.print(table)
    else:
        console.print("[dim]Nenhuma Skill instalada.[/dim]")
    if data["catalog"]:
        console.print(f"\n[dim]disponíveis: {', '.join(data['catalog'])}[/dim]")
        console.print("[dim]instale com: eve skill install <nome>[/dim]")


@skill_app.command("info")
def skill_info(name: str) -> None:
    """Mostra tudo que uma Skill declara."""
    data = _pedido("GET", "/api/skills")
    skill = next((s for s in data["skills"] if s["name"] == name), None)
    if skill is None:
        err_console.print(f"[red]Skill não instalada:[/red] {name}")
        raise typer.Exit(1)
    console.print_json(data=skill)


@skill_app.command("install")
def skill_install(name: str) -> None:
    """Instala uma Skill do catálogo."""
    skill = _pedido("POST", f"/api/skills/{name}")
    console.print(f"[green]{skill['name']} instalada.[/green] {skill['description']}")
    if skill["mcp"]:
        servidores = ", ".join(s["name"] for s in skill["mcp"])
        console.print(f"[dim]servidores MCP: {servidores}[/dim]")
    if skill["missing_secrets"]:
        faltando = ", ".join(skill["missing_secrets"])
        console.print(f"[yellow]falta credencial:[/yellow] {faltando}")
        console.print(f"[dim]use: eve key set {skill['missing_secrets'][0]}[/dim]")


@skill_app.command("remove")
def skill_remove(name: str) -> None:
    """Remove uma Skill."""
    _pedido("DELETE", f"/api/skills/{name}")
    console.print(f"[green]{name} removida.[/green]")


@skill_app.command("enable")
def skill_enable(name: str) -> None:
    """Liga uma Skill."""
    _pedido("POST", f"/api/skills/{name}/enabled", params={"enabled": True})
    console.print(f"[green]{name} ligada.[/green]")


@skill_app.command("disable")
def skill_disable(name: str) -> None:
    """Desliga uma Skill."""
    _pedido("POST", f"/api/skills/{name}/enabled", params={"enabled": False})
    console.print(f"[yellow]{name} desligada.[/yellow]")


# ---------------------------------------------------------------------- MCP


@mcp_app.command("list")
def mcp_list(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Lista os servidores MCP."""
    data = _pedido("GET", "/api/mcp")
    if as_json:
        console.print_json(data=data)
        return
    if not data["servers"]:
        console.print("[dim]Nenhum servidor MCP.[/dim]")
        console.print("[dim]adicione com: eve mcp add <nome> --command npx --arg ...[/dim]")
        return
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("")
    table.add_column("Servidor", style="bold")
    table.add_column("Transporte")
    table.add_column("Ferramentas")
    table.add_column("")
    for servidor in data["servers"]:
        if servidor["connected"]:
            marca, detalhe = "[green]✓[/green]", servidor["server"]
        elif not servidor["enabled"]:
            marca, detalhe = "[dim]·[/dim]", "desligado"
        else:
            marca, detalhe = "[red]✗[/red]", (servidor["error"] or "não conectou")[:44]
        table.add_row(
            marca,
            servidor["name"],
            servidor["transport"],
            str(len(servidor["tools"])),
            f"[dim]{detalhe}[/dim]",
        )
    console.print(table)
    console.print(f"\n[dim]{data['tools']} ferramenta(s) de MCP no Tool Bus[/dim]")


@mcp_app.command("add")
def mcp_add(
    name: str,
    command: Annotated[str, typer.Option("--command", "-c", help="Executável.")] = "",
    arg: Annotated[list[str] | None, typer.Option("--arg", "-a", help="Argumento.")] = None,
    env: Annotated[
        list[str] | None,
        typer.Option("--env", "-e", help="VAR=valor, ou VAR=@CREDENCIAL do Keychain."),
    ] = None,
    url: Annotated[str, typer.Option("--url", help="Servidor HTTP.")] = "",
) -> None:
    """Adiciona um servidor MCP e conecta."""
    ambiente: dict[str, str] = {}
    for item in env or []:
        chave, _, valor = item.partition("=")
        if not valor:
            err_console.print(f"[red]--env precisa ser VAR=valor:[/red] {item}")
            raise typer.Exit(1)
        ambiente[chave] = valor

    servidor = _pedido(
        "POST",
        "/api/mcp",
        json={
            "name": name,
            "command": command,
            "args": arg or [],
            "env": ambiente,
            "url": url,
        },
    )
    if servidor["connected"]:
        console.print(
            f"[green]{name} conectado[/green] — {servidor['server']}, "
            f"{len(servidor['tools'])} ferramenta(s)"
        )
        console.print(f"[dim]{', '.join(servidor['tools'][:8])}[/dim]")
    else:
        err_console.print(f"[red]{name} não conectou:[/red] {servidor['error']}")
        raise typer.Exit(1)


@mcp_app.command("remove")
def mcp_remove(name: str) -> None:
    """Remove um servidor MCP."""
    _pedido("DELETE", f"/api/mcp/{name}")
    console.print(f"[green]{name} removido.[/green]")


@mcp_app.command("reconnect")
def mcp_reconnect(name: str) -> None:
    """Reconecta um servidor MCP."""
    servidor = _pedido("POST", f"/api/mcp/{name}/reconnect")
    if servidor["connected"]:
        console.print(f"[green]{name} reconectado[/green] — {len(servidor['tools'])} ferramenta(s)")
    else:
        err_console.print(f"[red]{name} não conectou:[/red] {servidor['error']}")
        raise typer.Exit(1)


@mcp_app.command("tools")
def mcp_tools(name: Annotated[str | None, typer.Argument()] = None) -> None:
    """Lista as ferramentas trazidas pelos servidores MCP."""
    data = _pedido("GET", "/api/mcp")
    for servidor in data["servers"]:
        if name and servidor["name"] != name:
            continue
        console.print(f"[bold]{servidor['name']}[/bold] [dim]{servidor['server']}[/dim]")
        for ferramenta in servidor["tools"]:
            console.print(f"  {servidor['name']}.{ferramenta}")
    if not data["servers"]:
        console.print("[dim]Nenhum servidor MCP conectado.[/dim]")


__all__ = ["json", "mcp_app", "skill_app"]
