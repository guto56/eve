"""Perguntas de terminal para o setup.

Um seletor de setas em vez de "digite 1 ou 2" porque a escolha entre IA local
e nuvem é a decisão mais importante que o usuário toma na instalação, e ler
duas frases inteiras antes de escolher é mais fácil do que decorar números.

Quando não há terminal de verdade — ``curl | bash`` sem ``/dev/tty``, um
processo em pipe — nada aqui bloqueia: as funções devolvem o padrão e dizem
que não perguntaram, e quem chama decide o que fazer com isso.
"""

from __future__ import annotations

import sys
import termios
import tty
from dataclasses import dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

console = Console()

CIMA, BAIXO, ENTER, ESC, CTRL_C = "cima", "baixo", "enter", "esc", "ctrl_c"

SETA = "\u276f"
"""O mesmo cursor do Claude Code, escapado para o linter não o confundir com >."""


class Cancelado(Exception):
    """O usuário apertou Esc ou Ctrl+C."""


def interativo() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@dataclass(frozen=True)
class Opcao:
    titulo: str
    detalhe: str = ""


def _ler_tecla() -> str:
    """Uma tecla, sem esperar Enter. Sequências de escape viram nomes."""
    fd = sys.stdin.fileno()
    antes = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # Pode ser Esc sozinho ou o começo de uma seta: só as setas têm '['.
            seguinte = sys.stdin.read(1)
            if seguinte != "[":
                return ESC
            return {"A": CIMA, "B": BAIXO}.get(sys.stdin.read(1), "")
        if ch in ("\r", "\n"):
            return ENTER
        if ch == "\x03":
            return CTRL_C
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, antes)


def escolher(pergunta: str, descricao: str, opcoes: list[Opcao], padrao: int = 0) -> int:
    """Índice escolhido. ``Cancelado`` no Esc; o padrão quando não há terminal."""
    if not interativo():
        return padrao

    console.print(f"\n  [bold green]{pergunta}[/bold green]\n")
    if descricao:
        console.print(f"  {descricao}\n")

    atual = padrao
    # O ``Live`` cuida de apagar e redesenhar. Fazer essa conta à mão erra
    # sempre que um detalhe quebra em duas linhas — e quebra, em terminal
    # estreito.
    with Live(_bloco(opcoes, atual), console=console, auto_refresh=False) as live:
        while True:
            live.refresh()
            tecla = _ler_tecla()
            if tecla == CIMA:
                atual = (atual - 1) % len(opcoes)
            elif tecla == BAIXO:
                atual = (atual + 1) % len(opcoes)
            elif tecla == ENTER:
                live.update(_bloco(opcoes, atual, final=True))
                return atual
            elif tecla in (ESC, CTRL_C):
                raise Cancelado
            elif tecla.isdigit() and 1 <= int(tecla) <= len(opcoes):
                atual = int(tecla) - 1
                live.update(_bloco(opcoes, atual, final=True))
                return atual
            live.update(_bloco(opcoes, atual))


def _bloco(opcoes: list[Opcao], atual: int, final: bool = False) -> Group:
    linhas: list[Text] = []
    for i, opcao in enumerate(opcoes):
        escolhida = i == atual
        linhas.append(
            Text.assemble(
                (f" {SETA} " if escolhida else "   ", "bold cyan"),
                (f"{i + 1}. {opcao.titulo}", "bold" if escolhida else "dim"),
            )
        )
        if opcao.detalhe:
            linhas.append(Text(f"    {opcao.detalhe}", style="dim"))
    if not final:
        ajuda = "\n  \u2191\u2193 escolhe \u00b7 Enter confirma \u00b7 Esc cancela"
        linhas.append(Text(ajuda, style="dim"))
    return Group(*linhas)


def sim_ou_nao(pergunta: str, detalhe: str = "", padrao: bool = False) -> bool:
    escolha = escolher(
        pergunta,
        detalhe,
        [Opcao("Sim"), Opcao("Não")],
        padrao=0 if padrao else 1,
    )
    return escolha == 0


def segredo(rotulo: str, detalhe: str, atual: str | None) -> str | None:
    """A chave, digitada oculta. ``None`` quando o usuário só deu Enter.

    Colar uma chave e vê-la na tela é constrangedor e fica no scrollback; o
    terminal não devolve o eco, então lemos sem ele.
    """
    if not interativo():
        return None

    console.print(f"\n  [bold]{rotulo}[/bold]  [dim]{detalhe}[/dim]")
    if atual:
        console.print(f"  [dim]já configurada ({atual}) — Enter mantém[/dim]")
    else:
        console.print("  [dim]Enter deixa em branco[/dim]")

    fd = sys.stdin.fileno()
    antes = termios.tcgetattr(fd)
    try:
        novo = termios.tcgetattr(fd)
        novo[3] &= ~termios.ECHO  # lflag
        termios.tcsetattr(fd, termios.TCSADRAIN, novo)
        console.print("  [cyan]cole aqui:[/cyan] ", end="")
        digitado = sys.stdin.readline()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, antes)
    print()

    limpo = digitado.strip()
    return limpo or None
