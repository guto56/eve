"""Serviço de background, atualização e desinstalação."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from eve import __version__, service
from eve.cli import process
from eve.config import load_settings
from eve.paths import paths

console = Console()
err_console = Console(stderr=True)

service_app = typer.Typer(
    name="service", help="A EVE subindo sozinha depois do login.", no_args_is_help=True
)


@service_app.command("install")
def service_install() -> None:
    """Faz a EVE iniciar junto com o login."""
    estado = service.install()
    if not estado.loaded:
        err_console.print(f"[red]Não consegui instalar:[/red] {estado.detail}")
        raise typer.Exit(1)
    console.print("[green]A EVE agora inicia sozinha depois do login.[/green]")
    console.print(f"[dim]{service.plist_path()}[/dim]")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Para de iniciar sozinha. Não apaga nada seu."""
    service.uninstall()
    console.print("[yellow]A EVE não sobe mais no login.[/yellow]")
    console.print("[dim]Memória, configuração e credenciais continuam onde estavam.[/dim]")


@service_app.command("status")
def service_status() -> None:
    """Mostra se o serviço está instalado e rodando."""
    estado = service.status()
    if not estado.installed:
        console.print("[dim]Não instalado.[/dim] Use [bold]eve service install[/bold].")
        return
    if estado.loaded and estado.pid:
        console.print(f"[green]Rodando[/green] [dim]pid {estado.pid}[/dim]")
    elif estado.loaded:
        console.print(f"[yellow]Carregado, sem processo.[/yellow] [dim]{estado.detail}[/dim]")
    else:
        console.print(f"[yellow]Instalado mas não carregado.[/yellow] [dim]{estado.detail}[/dim]")
    if estado.last_exit:
        console.print(f"[dim]última saída: código {estado.last_exit}[/dim]")
    console.print(f"[dim]{service.plist_path()}[/dim]")


def update(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Sem perguntar.")] = False,
) -> None:
    """Atualiza a EVE, preservando memória, credenciais e configuração."""
    origem = _origem()
    if origem is None:
        err_console.print(
            "[red]Não achei de onde a EVE foi instalada.[/red] "
            "Rode `uv tool install --editable <pasta>` na cópia do projeto."
        )
        raise typer.Exit(1)

    console.print(f"[dim]origem: {origem}[/dim]")
    if (origem / ".git").is_dir():
        if _tem_remoto(origem):
            console.print("Buscando novidades…")
            if not _rodar(["git", "-C", str(origem), "pull", "--ff-only"], tolerante=True):
                err_console.print(
                    "[yellow]Não consegui atualizar o código.[/yellow] "
                    "Resolva o `git pull` na mão e rode de novo."
                )
                raise typer.Exit(1)
        else:
            console.print("[dim]sem repositório remoto — atualizando só a instalação[/dim]")

    console.print("Reinstalando…")
    _rodar(["uv", "tool", "install", "--editable", str(origem), "--reinstall", "-q"])

    settings = load_settings()
    if process.probe_health(settings) is not None:
        console.print("Reiniciando a EVE…")
        if service.status().loaded:
            service.restart()
        else:
            pid = process.running_pid()
            if pid:
                process.terminate(pid)
                process.clear_pid()
            process.spawn_daemon(settings)
        process.wait_for_health(settings)

    console.print(f"[green]Atualizada.[/green] [dim]versão {__version__}[/dim]")
    console.print("[dim]Memória, credenciais e configuração intactas.[/dim]")


def uninstall(
    keep_data: Annotated[
        bool, typer.Option("--keep-data/--remove-data", help="Preservar memória e configuração.")
    ] = True,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Sem perguntar.")] = False,
) -> None:
    """Remove a EVE. Por padrão, preserva seus dados."""
    p = paths()
    console.print("[bold]Isto vai:[/bold]")
    console.print("  · parar a EVE e tirá-la do login")
    console.print("  · remover o comando `eve`")
    if keep_data:
        console.print(f"  · [green]preservar[/green] {p.home} e {p.work}")
        console.print("  · [green]preservar[/green] as credenciais no Keychain")
    else:
        console.print(f"  · [red]APAGAR[/red] {p.home} (memória, configuração, Skills)")
        console.print("  · [dim]as credenciais no Keychain continuam — use `eve key delete`[/dim]")
        console.print(f"  · [dim]preservar {p.work}, que é seu[/dim]")

    if not yes and not typer.confirm("\nSeguir?", default=False):
        console.print("[dim]Cancelado.[/dim]")
        raise typer.Exit(0)

    settings = load_settings()
    service.uninstall()
    if process.probe_health(settings) is not None:
        pid = process.running_pid()
        if pid:
            process.terminate(pid)
            process.clear_pid()

    _rodar(["uv", "tool", "uninstall", "eve-core"], tolerante=True)

    if not keep_data and p.home.exists():
        shutil.rmtree(p.home, ignore_errors=True)
        console.print(f"[dim]{p.home} removido.[/dim]")

    console.print("[green]EVE removida.[/green]")
    if keep_data:
        console.print(f"[dim]Seus dados continuam em {p.home} e {p.work}.[/dim]")


def _origem() -> Path | None:
    """De onde a EVE foi instalada, para poder atualizar dali."""
    import eve

    modulo = Path(eve.__file__).resolve()
    # Instalação editável: .../<projeto>/src/eve/__init__.py
    if modulo.parent.parent.name == "src":
        return modulo.parent.parent.parent
    return None


def _tem_remoto(origem: Path) -> bool:
    executavel = shutil.which("git")
    if executavel is None:
        return False
    resultado = subprocess.run(
        [executavel, "-C", str(origem), "remote"], capture_output=True, text=True, check=False
    )
    return bool(resultado.stdout.strip())


def _rodar(comando: list[str], tolerante: bool = False) -> bool:
    """Executa e diz se deu certo. Intolerante encerra o comando."""
    executavel = shutil.which(comando[0])
    if executavel is None:
        if tolerante:
            return False
        err_console.print(f"[red]não encontrei {comando[0]}[/red]")
        raise typer.Exit(1)
    resultado = subprocess.run(
        [executavel, *comando[1:]], capture_output=True, text=True, check=False
    )
    if resultado.returncode != 0:
        if tolerante:
            return False
        err_console.print(f"[red]{comando[0]} falhou:[/red] {resultado.stderr.strip()[:300]}")
        raise typer.Exit(1)
    return True


__all__ = ["service_app", "sys", "uninstall", "update"]
