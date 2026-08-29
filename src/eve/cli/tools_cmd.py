"""Comandos de ferramentas, permissões, confirmações e auditoria.

Falam com o daemon pela mesma API que a interface web usa (spec §25) — a CLI
não tem caminho privilegiado próprio. Quando uma ferramenta pede confirmação,
o terminal responde pelo fluxo normal de aprovação, não por um atalho.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Annotated, Any

import httpx2 as httpx
import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from eve.cli import process
from eve.config import load_settings, update_config_file
from eve.tools.spec import RiskLevel

console = Console()
err_console = Console(stderr=True)

tool_app = typer.Typer(name="tool", help="Ferramentas da EVE.", no_args_is_help=True)
permission_app = typer.Typer(
    name="permission", help="Política de permissões.", no_args_is_help=True
)

RISK_STYLE = {
    "safe": "green",
    "confirm": "yellow",
    "privileged": "magenta",
    "blocked": "red",
}


def _api() -> str:
    settings = load_settings()
    if process.probe_health(settings) is None:
        err_console.print("[red]A EVE não está rodando.[/red] Use [bold]eve start[/bold].")
        raise typer.Exit(1)
    return process.base_url(settings)


def _get(path: str, **params: Any) -> Any:
    response = httpx.get(f"{_api()}{path}", params=params, timeout=10.0)
    response.raise_for_status()
    return response.json()


@tool_app.command("list")
def tool_list(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Lista as ferramentas disponíveis."""
    data = _get("/api/tools")
    if as_json:
        console.print_json(data=data)
        return
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("Ferramenta", style="bold")
    table.add_column("Risco")
    table.add_column("Descrição")
    for item in data["tools"]:
        risk = item["effective"]["risk"]
        mark = "" if item["effective"]["allowed"] else " [red](bloqueada)[/red]"
        table.add_row(
            item["name"],
            f"[{RISK_STYLE.get(risk, '')}]{risk}[/]{mark}",
            item["description"],
        )
    console.print(table)
    console.print(
        f"\n[dim]{data['count']} ferramenta(s) em {len(data['namespaces'])} namespace(s)[/dim]"
    )


@tool_app.command("show")
def tool_show(name: str) -> None:
    """Mostra a definição completa de uma ferramenta."""
    try:
        data = _get(f"/api/tools/{name}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            err_console.print(f"[red]Ferramenta desconhecida:[/red] {name}")
            raise typer.Exit(1) from None
        raise
    console.print_json(data=data)


@tool_app.command("call")
def tool_call(
    name: str,
    args: Annotated[str, typer.Option("--args", "-a", help="Argumentos em JSON.")] = "{}",
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Responde sim automaticamente à confirmação.")
    ] = False,
) -> None:
    """Executa uma ferramenta pelo Tool Bus."""
    try:
        parsed_args = json.loads(args)
    except json.JSONDecodeError as exc:
        err_console.print(f"[red]--args não é JSON válido:[/red] {exc}")
        raise typer.Exit(1) from None
    if not isinstance(parsed_args, dict):
        err_console.print("[red]--args precisa ser um objeto JSON.[/red]")
        raise typer.Exit(1)

    base = _api()
    settings = load_settings()
    holder: dict[str, Any] = {}

    def run_call() -> None:
        try:
            response = httpx.post(
                f"{base}/api/tools/{name}/call",
                json={"args": parsed_args},
                timeout=settings.permissions.confirm_timeout + 30,
            )
            holder["result"] = response.json()
            holder["status"] = response.status_code
        except httpx.HTTPError as exc:  # pragma: no cover - rede local
            holder["error"] = str(exc)

    worker = threading.Thread(target=run_call, daemon=True)
    worker.start()

    handled: set[str] = set()
    while worker.is_alive():
        try:
            pending = httpx.get(f"{base}/api/approvals", timeout=5.0).json()["pending"]
        except httpx.HTTPError:  # pragma: no cover
            pending = []
        for item in pending:
            if item["id"] in handled or item["tool"] != name:
                continue
            handled.add(item["id"])
            approved = yes or _prompt(item)
            httpx.post(
                f"{base}/api/approvals/{item['id']}",
                json={"approved": approved, "by": "cli"},
                timeout=5.0,
            )
        time.sleep(0.15)
    worker.join()

    if "error" in holder:
        err_console.print(f"[red]{holder['error']}[/red]")
        raise typer.Exit(1)
    if holder.get("status") == 404:
        err_console.print(f"[red]Ferramenta desconhecida:[/red] {name}")
        raise typer.Exit(1)

    result = holder["result"]
    if result["ok"]:
        console.print(JSON(json.dumps(result["value"], ensure_ascii=False, default=str)))
        console.print(f"[dim]{result['duration_ms']} ms[/dim]")
    else:
        err_console.print(f"[red]{result['error_kind']}:[/red] {result['error']}")
        raise typer.Exit(1)


def _prompt(item: dict[str, Any]) -> bool:
    risk = item["risk"]
    console.print(
        f"\n[bold]A EVE quer executar[/bold] [cyan]{item['tool']}[/cyan] "
        f"([{RISK_STYLE.get(risk, '')}]{risk}[/])"
    )
    if item["args"]:
        console.print(JSON(json.dumps(item["args"], ensure_ascii=False, default=str)))
    console.print(f"[dim]{item['reason']}[/dim]")
    return typer.confirm("Autorizar?", default=False)


@tool_app.command("approvals")
def tool_approvals(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Lista confirmações pendentes."""
    data = _get("/api/approvals")
    if as_json:
        console.print_json(data=data)
        return
    if not data["pending"]:
        console.print("[dim]Nenhuma confirmação pendente.[/dim]")
        return
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("ID")
    table.add_column("Ferramenta")
    table.add_column("Risco")
    table.add_column("Esperando")
    for item in data["pending"]:
        table.add_row(item["id"][:8], item["tool"], item["risk"], f"{item['waiting_seconds']:.0f}s")
    console.print(table)


@tool_app.command("approve")
def tool_approve(request_id: str) -> None:
    """Autoriza uma confirmação pendente."""
    _decide(request_id, True)


@tool_app.command("deny")
def tool_deny(request_id: str) -> None:
    """Nega uma confirmação pendente."""
    _decide(request_id, False)


def _decide(request_id: str, approved: bool) -> None:
    base = _api()
    pending = httpx.get(f"{base}/api/approvals", timeout=5.0).json()["pending"]
    full = next((p["id"] for p in pending if p["id"].startswith(request_id)), None)
    if full is None:
        err_console.print("[red]Confirmação inexistente ou já decidida.[/red]")
        raise typer.Exit(1)
    httpx.post(
        f"{base}/api/approvals/{full}",
        json={"approved": approved, "by": "cli"},
        timeout=5.0,
    )
    console.print("[green]Autorizada.[/green]" if approved else "[yellow]Negada.[/yellow]")


@tool_app.command("audit")
def tool_audit(
    lines: Annotated[int, typer.Option("--lines", "-n", help="Últimas N chamadas.")] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Mostra as últimas chamadas de ferramenta."""
    data = _get("/api/audit", limit=lines)
    if as_json:
        console.print_json(data=data)
        return
    if not data["entries"]:
        console.print("[dim]Nada auditado ainda.[/dim]")
        return
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("Quando")
    table.add_column("Ferramenta")
    table.add_column("Resultado")
    table.add_column("Origem")
    outcome_style = {"ok": "green", "denied": "yellow", "failed": "red"}
    for entry in data["entries"]:
        when = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
        outcome = entry.get("outcome", "?")
        table.add_row(
            when,
            entry.get("tool", "?"),
            f"[{outcome_style.get(outcome, '')}]{outcome}[/]",
            entry.get("source", "?"),
        )
    console.print(table)


@permission_app.command("list")
def permission_list(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Mostra a política de permissões em vigor."""
    data = _get("/api/permissions")
    if as_json:
        console.print_json(data=data)
        return
    console.print("[bold]Regras[/bold]")
    if data["overrides"]:
        for pattern, level in sorted(data["overrides"].items()):
            console.print(f"  {pattern}  [{RISK_STYLE.get(level, '')}]{level}[/]")
    else:
        console.print("  [dim]nenhuma — cada ferramenta usa o risco que declara[/dim]")
    console.print("\n[bold]Concessões[/bold]")
    granted = [name for name, value in data["grants"].items() if value]
    if granted:
        for name in sorted(granted):
            console.print(f"  {name}")
    else:
        console.print("  [dim]nenhuma[/dim]")
    console.print(f"\n[dim]prazo de confirmação: {data['confirm_timeout']:.0f}s[/dim]")


@permission_app.command("set")
def permission_set(pattern: str, level: str) -> None:
    """Define o nível de uma ferramenta ou padrão (ex.: `file.*` confirm)."""
    try:
        risk = RiskLevel(level.lower())
    except ValueError:
        valid = ", ".join(r.value for r in RiskLevel)
        err_console.print(f"[red]Nível inválido:[/red] {level}. Use um de: {valid}")
        raise typer.Exit(1) from None

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("permissions", {}).setdefault("overrides", {})[pattern] = risk.value

    update_config_file(mutate)
    _reload()
    console.print(f"[green]{pattern}[/green] → [{RISK_STYLE[risk.value]}]{risk.value}[/]")


@permission_app.command("unset")
def permission_unset(pattern: str) -> None:
    """Remove uma regra, devolvendo a ferramenta ao risco que ela declara."""

    def mutate(data: dict[str, Any]) -> None:
        data.get("permissions", {}).get("overrides", {}).pop(pattern, None)

    update_config_file(mutate)
    _reload()
    console.print(f"Regra removida: [bold]{pattern}[/bold]")


@permission_app.command("grant")
def permission_grant(name: str) -> None:
    """Concede uma ferramenta PRIVILEGED. Continua exigindo confirmação a cada uso."""
    _set_grant(name, True)
    console.print(f"[green]Concedida:[/green] {name} [dim](ainda pede confirmação)[/dim]")


@permission_app.command("revoke")
def permission_revoke(name: str) -> None:
    """Revoga a concessão de uma ferramenta PRIVILEGED."""
    _set_grant(name, False)
    console.print(f"[yellow]Revogada:[/yellow] {name}")


def _set_grant(name: str, value: bool) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("permissions", {}).setdefault("grants", {})[name] = value

    update_config_file(mutate)
    _reload()


def _reload() -> None:
    """Avisa o daemon, se ele estiver de pé. Sem daemon, o arquivo já basta."""
    settings = load_settings()
    if process.probe_health(settings) is None:
        return
    try:
        httpx.post(f"{process.base_url(settings)}/api/permissions/reload", timeout=5.0)
    except httpx.HTTPError:  # pragma: no cover
        err_console.print("[yellow]Configuração gravada, mas o Core não recarregou.[/yellow]")
