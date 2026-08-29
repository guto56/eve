"""Comandos de credenciais, provedores e conversa rápida com um modelo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import httpx2 as httpx
import typer
from rich.console import Console
from rich.table import Table

from eve.cli import process
from eve.config import load_settings
from eve.paths import paths
from eve.secrets import InvalidSecretName, build_store

console = Console()
err_console = Console(stderr=True)

key_app = typer.Typer(name="key", help="Credenciais no Keychain.", no_args_is_help=True)
provider_app = typer.Typer(name="provider", help="Provedores de IA.", no_args_is_help=True)


def _store():
    return build_store(paths().ensure().home / "secrets.json")


def _notify_daemon() -> None:
    """Faz o Core reler credenciais, se ele estiver de pé."""
    settings = load_settings()
    if process.probe_health(settings) is None:
        return
    try:
        httpx.post(f"{process.base_url(settings)}/api/providers/reset", timeout=10.0)
    except httpx.HTTPError:  # pragma: no cover
        err_console.print("[yellow]Credencial gravada, mas o Core não recarregou.[/yellow]")


def _require_daemon() -> str:
    settings = load_settings()
    if process.probe_health(settings) is None:
        err_console.print("[red]A EVE não está rodando.[/red] Use [bold]eve start[/bold].")
        raise typer.Exit(1)
    return process.base_url(settings)


# ---------------------------------------------------------------- credenciais


@key_app.command("list")
def key_list(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Mostra quais credenciais estão configuradas — nunca os valores."""
    store = _store()
    described = store.describe()
    if as_json:
        console.print_json(data={"secrets": described, "missing": store.missing_required()})
        return

    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("")
    table.add_column("Credencial", style="bold")
    table.add_column("Valor")
    table.add_column("Para quê")
    for item in described:
        if item["configured"]:
            marca, valor = "[green]✓[/green]", f"[dim]{item['hint']}[/dim]"
        elif item["required"]:
            marca, valor = "[red]✗[/red]", "[red]obrigatória[/red]"
        else:
            marca, valor = "[dim]·[/dim]", "[dim]opcional[/dim]"
        table.add_row(marca, item["name"], valor, f"[dim]{item['description']}[/dim]")
    console.print(table)


@key_app.command("set")
def key_set(
    name: str,
    value: Annotated[
        str | None,
        typer.Option("--value", help="Evite: fica no histórico do shell. Prefira o prompt."),
    ] = None,
) -> None:
    """Grava uma credencial no Keychain."""
    secret = value or typer.prompt(f"Valor de {name}", hide_input=True)
    try:
        _store().set(name, secret)
    except (InvalidSecretName, ValueError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    _notify_daemon()
    console.print(f"[green]{name} gravada no Keychain.[/green]")


@key_app.command("delete")
def key_delete(name: str) -> None:
    """Remove uma credencial do Keychain."""
    try:
        removed = _store().delete(name)
    except InvalidSecretName as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    _notify_daemon()
    if removed:
        console.print(f"[green]{name} removida.[/green]")
    else:
        console.print(f"[yellow]{name} não estava no Keychain.[/yellow]")


@key_app.command("import")
def key_import(
    path: Annotated[Path, typer.Argument(help="Arquivo CHAVE=valor.")] = Path("env.txt"),
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Substituir as existentes.")
    ] = False,
) -> None:
    """Importa credenciais de um arquivo para o Keychain.

    O arquivo não é apagado — depois de conferir, apague você mesmo.
    """
    if not path.exists():
        err_console.print(f"[red]Arquivo não encontrado:[/red] {path}")
        raise typer.Exit(1)
    result = _store().import_env_file(path, overwrite=overwrite)
    if not result:
        console.print("[yellow]Nenhuma credencial encontrada no arquivo.[/yellow]")
        return
    for name, outcome in sorted(result.items()):
        style = {"importada": "green", "já existia": "yellow"}.get(outcome, "dim")
        console.print(f"  [{style}]{outcome:24}[/] {name}")
    _notify_daemon()
    importadas = sum(1 for v in result.values() if v == "importada")
    console.print(f"\n[green]{importadas} credencial(is) no Keychain.[/green]")
    console.print(f"[dim]O arquivo {path} continua no disco — apague quando quiser.[/dim]")


# ----------------------------------------------------------------- provedores


@provider_app.command("list")
def provider_list(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Estado dos provedores de IA."""
    base = _require_daemon()
    data = httpx.get(f"{base}/api/providers", timeout=30.0).json()
    if as_json:
        console.print_json(data=data)
        return
    for item in data["providers"]:
        marca = "[green]✓[/green]" if item["ok"] else "[red]✗[/red]"
        latencia = f" [dim]({item['latency_ms']:.0f} ms)[/dim]" if item["latency_ms"] else ""
        console.print(f"{marca} [bold]{item['name']}[/bold]{latencia} — {item['detail']}")
    console.print("\n[bold]Modelos por papel[/bold]")
    for papel, modelo in data["models"].items():
        console.print(f"  {papel:9} [cyan]{modelo}[/cyan]")


@provider_app.command("models")
def provider_models(
    name: str,
    filtro: Annotated[str, typer.Option("--filter", "-f", help="Filtra por texto.")] = "",
) -> None:
    """Lista os modelos disponíveis em um provedor."""
    base = _require_daemon()
    response = httpx.get(f"{base}/api/providers/{name}/models", timeout=60.0)
    if response.status_code != 200:
        err_console.print(f"[red]{response.json().get('detail', response.text)}[/red]")
        raise typer.Exit(1)
    models = response.json()["models"]
    if filtro:
        models = [m for m in models if filtro.lower() in m.lower()]
    for model in models:
        console.print(f"  {model}")
    console.print(f"\n[dim]{len(models)} modelo(s)[/dim]")


# ---------------------------------------------------------------------- chat


def ask(
    prompt: str,
    role: Annotated[
        str, typer.Option("--role", "-r", help="local, fast, external ou heavy.")
    ] = "local",
    system_prompt: Annotated[
        str | None, typer.Option("--system", "-s", help="Instrução de sistema.")
    ] = None,
    tools: Annotated[bool, typer.Option("--tools", help="Oferecer as ferramentas da EVE.")] = False,
    no_stream: Annotated[
        bool, typer.Option("--no-stream", help="Esperar a resposta inteira.")
    ] = False,
) -> None:
    """Faz uma pergunta a um modelo. Serve para testar os provedores."""
    base = _require_daemon()
    payload: dict[str, Any] = {
        "prompt": prompt,
        "role": role,
        "system_prompt": system_prompt,
        "with_tools": tools,
        "stream": not no_stream,
    }

    if no_stream:
        response = httpx.post(f"{base}/api/ai/ask", json=payload, timeout=300.0)
        if response.status_code != 200:
            err_console.print(f"[red]{response.json().get('detail', response.text)}[/red]")
            raise typer.Exit(1)
        data = response.json()
        console.print(data["text"])
        _print_footer(data)
        return

    chamadas: list[dict[str, Any]] = []
    with httpx.stream("POST", f"{base}/api/ai/ask", json=payload, timeout=300.0) as response:
        if response.status_code != 200:
            body = response.read().decode("utf-8", "replace")
            err_console.print(f"[red]{body[:400]}[/red]")
            raise typer.Exit(1)
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            chunk = json.loads(line[5:].strip())
            if chunk.get("error"):
                console.print()
                err_console.print(f"[red]{chunk['kind']}:[/red] {chunk['error']}")
                raise typer.Exit(1)
            if chunk.get("text"):
                console.print(chunk["text"], end="", highlight=False, markup=False)
            chamadas.extend(chunk.get("tool_calls") or [])
            if chunk.get("done"):
                break
    console.print()
    for call in chamadas:
        args = json.dumps(call["arguments"], ensure_ascii=False)
        console.print(f"[magenta]→ ferramenta[/magenta] [bold]{call['name']}[/bold]{args}")


def _print_footer(data: dict[str, Any]) -> None:
    for call in data.get("tool_calls") or []:
        args = json.dumps(call["arguments"], ensure_ascii=False)
        console.print(f"[magenta]→ ferramenta[/magenta] [bold]{call['name']}[/bold]{args}")
    usage = data.get("usage") or {}
    console.print(
        f"[dim]{data['model']} · {data['duration_ms']:.0f} ms · "
        f"{usage.get('total', 0)} tokens[/dim]"
    )


__all__ = ["ask", "key_app", "provider_app"]
