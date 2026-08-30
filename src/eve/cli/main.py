"""CLI de administração da EVE (spec §23).

Tudo que existe aqui deve existir também na interface web (spec §25) — ambas
falam com o mesmo daemon.
"""

from __future__ import annotations

import json
import subprocess
import sys
import webbrowser
from typing import Annotated

import httpx2 as httpx
import typer
from rich.console import Console
from rich.table import Table

from eve import __version__
from eve.cli import process
from eve.cli.ai_cmd import ask, key_app, provider_app
from eve.cli.chat_cmd import chat as chat_cmd
from eve.cli.ext_cmd import mcp_app, skill_app
from eve.cli.memory_cmd import memory_app
from eve.cli.task_cmd import task_app
from eve.cli.tools_cmd import permission_app, tool_app
from eve.cli.voice_cmd import voice_app
from eve.cli.watch_cmd import watch_app
from eve.config import load_settings
from eve.doctor import Status, run_checks, worst
from eve.paths import paths

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="eve",
    help="EVE — assistente pessoal de IA local-first para macOS.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(name="config", help="Configuração da EVE.", no_args_is_help=True)
app.add_typer(config_app)
app.add_typer(tool_app)
app.add_typer(permission_app)
app.add_typer(key_app)
app.add_typer(provider_app)
app.add_typer(memory_app)
app.add_typer(voice_app)
app.add_typer(skill_app)
app.add_typer(mcp_app)
app.add_typer(task_app)
app.add_typer(watch_app)
app.command(name="ask")(ask)
app.command(name="chat")(chat_cmd)

_STATUS_STYLE = {Status.OK: "green", Status.WARN: "yellow", Status.FAIL: "red"}
_STATUS_MARK = {Status.OK: "✓", Status.WARN: "!", Status.FAIL: "✗"}


@app.command()
def version() -> None:
    """Mostra a versão da EVE."""
    console.print(f"EVE {__version__}")


@app.command()
def start(
    foreground: Annotated[
        bool, typer.Option("--foreground", "-f", help="Roda no terminal em vez de background.")
    ] = False,
) -> None:
    """Inicia o Core."""
    settings = load_settings()

    if process.probe_health(settings) is not None:
        console.print("[yellow]A EVE já está rodando.[/yellow]")
        raise typer.Exit(0)

    if foreground:
        import asyncio

        from eve.cli.run import serve

        try:
            asyncio.run(serve(settings, verbose=settings.log.level == "debug"))
        except KeyboardInterrupt:  # pragma: no cover - Ctrl+C antes do uvicorn assumir
            console.print("\n[dim]EVE encerrada.[/dim]")
        return

    pid = process.spawn_daemon(settings)
    body = process.wait_for_health(settings)
    if body is None:
        process.terminate(pid)
        process.clear_pid()
        err_console.print(
            f"[red]O Core não subiu.[/red] Veja os logs: [bold]{paths().logs / 'daemon.out'}[/bold]"
        )
        raise typer.Exit(1)
    console.print(
        f"[green]EVE ativa[/green] — pid {pid}, "
        f"http://{settings.server.host}:{settings.server.port}"
    )


@app.command()
def run(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Inclui os logs internos.")
    ] = False,
) -> None:
    """Roda a EVE no terminal, mostrando o que ela faz. Ctrl+C encerra."""
    settings = load_settings()
    if verbose:
        settings.log.level = "debug"
    if process.probe_health(settings) is not None:
        err_console.print(
            "[yellow]Já existe uma EVE rodando em segundo plano.[/yellow] "
            "Use [bold]eve stop[/bold] antes."
        )
        raise typer.Exit(1)
    start(foreground=True)


@app.command()
def stop() -> None:
    """Para o Core."""
    pid = process.running_pid()
    if pid is None:
        console.print("[yellow]A EVE não está rodando.[/yellow]")
        raise typer.Exit(0)
    if process.terminate(pid):
        process.clear_pid()
        console.print(f"[green]EVE parada[/green] (pid {pid}).")
    else:
        err_console.print(f"[red]Não foi possível encerrar o pid {pid}.[/red]")
        raise typer.Exit(1)


@app.command()
def restart() -> None:
    """Reinicia o Core."""
    pid = process.running_pid()
    if pid is not None:
        process.terminate(pid)
        process.clear_pid()
    settings = load_settings()
    new_pid = process.spawn_daemon(settings)
    if process.wait_for_health(settings) is None:
        process.terminate(new_pid)
        process.clear_pid()
        err_console.print("[red]O Core não voltou.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]EVE reiniciada[/green] — pid {new_pid}.")


@app.command()
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Mostra o estado da EVE."""
    settings = load_settings()
    try:
        response = httpx.get(f"{process.base_url(settings)}/api/status", timeout=2.0)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        if as_json:
            console.print_json(data={"status": "parada"})
        else:
            console.print("[yellow]EVE parada.[/yellow] Use [bold]eve start[/bold].")
        raise typer.Exit(1) from None

    if as_json:
        console.print_json(data=data)
        return

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Estado", "[green]ativa[/green]")
    table.add_row("Versão", data["version"])
    table.add_row("PID", str(data["pid"]))
    table.add_row("Uptime", f"{data['uptime_seconds']:.0f}s")
    table.add_row("Endereço", f"http://{data['server']['host']}:{data['server']['port']}")
    table.add_row("Eventos", f"{data['bus']['published']} publicados")
    table.add_row("Clientes", str(data["bus"]["subscribers"]))
    console.print(table)

    components = Table(box=None, padding=(0, 2, 0, 0), show_header=False)
    for name, state in data["components"].items():
        components.add_row(name, f"[dim]{state}[/dim]")
    console.print("\n[bold]Componentes[/bold]")
    console.print(components)


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Diagnostica a instalação."""
    settings = load_settings()
    results = run_checks(settings)

    if as_json:
        console.print_json(
            data={
                "overall": worst(results).value,
                "checks": [
                    {"name": c.name, "status": c.status.value, "detail": c.detail} for c in results
                ],
            }
        )
    else:
        table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        for check in results:
            style = _STATUS_STYLE[check.status]
            table.add_row(
                f"[{style}]{_STATUS_MARK[check.status]}[/{style}]",
                check.name,
                f"[dim]{check.detail}[/dim]",
            )
        console.print(table)

    if worst(results) is Status.FAIL:
        raise typer.Exit(1)


@app.command()
def logs(
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Acompanha em tempo real.")
    ] = False,
    lines: Annotated[int, typer.Option("--lines", "-n", help="Últimas N linhas.")] = 50,
) -> None:
    """Mostra os logs da EVE."""
    log_file = paths().log_file
    if not log_file.exists():
        console.print(f"[yellow]Ainda não há logs em {log_file}.[/yellow]")
        raise typer.Exit(0)
    cmd = ["tail", "-n", str(lines)] + (["-f"] if follow else []) + [str(log_file)]
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:  # pragma: no cover - interativo
        pass


@app.command()
def web() -> None:
    """Abre a interface web da EVE."""
    settings = load_settings()
    url = process.base_url(settings)
    if process.probe_health(settings) is None:
        console.print("[yellow]A EVE não está rodando.[/yellow] Iniciando...")
        pid = process.spawn_daemon(settings)
        if process.wait_for_health(settings) is None:
            process.terminate(pid)
            err_console.print("[red]O Core não subiu.[/red]")
            raise typer.Exit(1)
    webbrowser.open(url)
    console.print(f"Abrindo [bold]{url}[/bold]")


@config_app.command("path")
def config_path() -> None:
    """Mostra onde vive a configuração."""
    p = paths()
    # soft_wrap evita que o Rich quebre caminhos longos no meio.
    console.print(f"EVE_HOME     {p.home}", soft_wrap=True)
    console.print(f"config.toml  {p.config_file}", soft_wrap=True)
    console.print(f"banco        {p.db_file}", soft_wrap=True)
    console.print(f"logs         {p.log_file}", soft_wrap=True)


@config_app.command("show")
def config_show(
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
) -> None:
    """Mostra a configuração efetiva (arquivo + ambiente)."""
    data = load_settings().model_dump()
    if as_json:
        console.print_json(data=data)
    else:
        console.print_json(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:  # pragma: no cover - wrapper do console_script
    sys.exit(app())
