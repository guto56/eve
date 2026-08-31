"""`eve phone` — atender o telefone.

O trabalho difícil não é o áudio: é dar ao Twilio um endereço público que
chegue até esta máquina, sem abrir a máquina. O comando cuida das duas pontas
— sobe o túnel para a porta da telefonia (nunca a do Core) e mostra o que
colar no painel do Twilio.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from typing import Annotated

import typer
from rich.console import Console

from eve.config import load_settings, update_config_file

console = Console()
err_console = Console(stderr=True)

phone_app = typer.Typer(name="phone", help="Falar com a EVE por telefone.", no_args_is_help=True)

URL_DO_TUNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


@phone_app.command("allow")
def permitir(numero: str) -> None:
    """Autoriza um número a ligar para a EVE.

    Sem isto ninguém entra: a lista vazia recusa todo mundo, porque um número
    de telefone é público por natureza.
    """
    limpo = numero.strip()
    if not limpo.startswith("+"):
        err_console.print("[red]Use o formato internacional:[/red] +5531999998888")
        raise typer.Exit(1)

    def mudar(dados: dict) -> None:
        telefone = dados.setdefault("phone", {})
        atuais = list(telefone.get("allowed_callers") or [])
        if limpo not in atuais:
            atuais.append(limpo)
        telefone["allowed_callers"] = atuais
        telefone["enabled"] = True

    update_config_file(mudar)
    console.print(f"[green]{limpo} pode ligar para a EVE.[/green]")
    console.print("[dim]reinicie para valer:[/dim] [cyan]eve restart[/cyan]")


@phone_app.command("deny")
def negar(numero: str) -> None:
    """Tira um número da lista."""

    def mudar(dados: dict) -> None:
        telefone = dados.setdefault("phone", {})
        telefone["allowed_callers"] = [
            n for n in (telefone.get("allowed_callers") or []) if n.strip() != numero.strip()
        ]

    update_config_file(mudar)
    console.print(f"[yellow]{numero} não pode mais ligar.[/yellow]")


@phone_app.command("status")
def estado() -> None:
    """O que falta para o telefone tocar."""
    from eve.paths import paths
    from eve.secrets import build_store

    s = load_settings()
    segredos = build_store(paths().ensure().home / "secrets.json")

    def linha(ok: bool, texto: str, detalhe: str = "") -> None:
        marca = "[green]✓[/green]" if ok else "[yellow]![/yellow]"
        console.print(f"  {marca} {texto}" + (f" [dim]{detalhe}[/dim]" if detalhe else ""))

    console.print()
    falta_ligar = "" if s.phone.enabled else "eve phone allow <numero>"
    linha(s.phone.enabled, "telefonia ligada", falta_ligar)
    linha(
        bool(segredos.get("TWILIO_AUTH_TOKEN")),
        "TWILIO_AUTH_TOKEN",
        "" if segredos.get("TWILIO_AUTH_TOKEN") else "eve key set TWILIO_AUTH_TOKEN",
    )
    linha(
        bool(s.phone.allowed_callers),
        f"{len(s.phone.allowed_callers)} número(s) autorizado(s)",
        ", ".join(s.phone.allowed_callers),
    )
    linha(bool(s.phone.public_url), "endereço público", s.phone.public_url or "eve phone tunnel")
    linha(bool(shutil.which("cloudflared")), "cloudflared", "brew install cloudflared")
    console.print()


@phone_app.command("tunnel")
def tunel(
    guardar: Annotated[
        bool, typer.Option("--save/--no-save", help="Grava o endereço na configuração.")
    ] = True,
) -> None:
    """Abre um endereço público para a porta da telefonia.

    Só a porta da telefonia: a API do Core continua em 127.0.0.1, onde
    ninguém de fora alcança.
    """
    if not shutil.which("cloudflared"):
        err_console.print("[red]cloudflared não está instalado.[/red]")
        err_console.print("[cyan]brew install cloudflared[/cyan]")
        raise typer.Exit(1)

    porta = load_settings().phone.port
    console.print(f"[dim]abrindo um endereço público para 127.0.0.1:{porta}…[/dim]")
    try:
        asyncio.run(_rodar_tunel(porta, guardar))
    except KeyboardInterrupt:
        console.print("\n[dim]Túnel fechado. O telefone não toca mais.[/dim]")


async def _rodar_tunel(porta: int, guardar: bool) -> None:
    processo = await asyncio.create_subprocess_exec(
        "cloudflared",
        "tunnel",
        "--url",
        f"http://127.0.0.1:{porta}",
        "--no-autoupdate",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    endereco = ""
    assert processo.stdout is not None
    try:
        while linha := await processo.stdout.readline():
            texto = linha.decode("utf-8", "replace")
            if not endereco and (achado := URL_DO_TUNEL.search(texto)):
                endereco = achado.group(0)
                _mostrar(endereco, guardar)
    finally:
        processo.terminate()
        await processo.wait()


def _mostrar(endereco: str, guardar: bool) -> None:
    if guardar:
        update_config_file(lambda d: d.setdefault("phone", {}).update({"public_url": endereco}))

    console.print()
    console.print("  [bold green]O telefone pode tocar.[/bold green]")
    console.print("\n  [bold]Cole no painel do Twilio[/bold]")
    console.print("  [dim]número ▸ Voice ▸ A call comes in[/dim]")
    console.print(f"    [cyan]{endereco}/twilio/voice[/cyan]  [dim]HTTP POST[/dim]")
    console.print("\n  [dim]Enquanto este comando estiver aberto, o endereço vale.[/dim]")
    console.print("  [dim]Ctrl+C fecha o túnel e o telefone para de tocar.[/dim]\n")
    if guardar:
        console.print(
            "  [dim]Endereço gravado. Rode[/dim] [cyan]eve restart[/cyan] "
            "[dim]numa outra janela.[/dim]\n"
        )
