"""`eve setup` — a conversa que decide como a EVE vai funcionar.

Vem logo depois da instalação e antes de qualquer download pesado, porque a
primeira pergunta — IA local ou só nuvem — é justamente a que diz se vale
baixar dois gigabytes de modelo.
"""

from __future__ import annotations

import typer
from rich.console import Console

from eve.cli import ask
from eve.cli.ask import Opcao
from eve.config import load_settings, update_config_file
from eve.paths import paths
from eve.secrets import KNOWN_SECRETS, build_store

console = Console()

#: As credenciais que o setup pergunta, na ordem, com o que cada uma libera.
#: Twilio fica de fora: é telefonia, ainda não existe, e perguntar por uma
#: chave que não faz nada só cansa quem está instalando.
PERGUNTADAS: tuple[tuple[str, str], ...] = (
    ("OPENROUTER_API_KEY", "modelos grandes — Gemini, Claude, GPT · openrouter.ai/keys"),
    ("TAVILY_API_KEY", "pesquisa na web · app.tavily.com"),
    ("GOOGLE_API_KEY", "conversa por voz ao vivo · aistudio.google.com/apikey"),
    ("DEEPGRAM_API_KEY", "ouvir você falar · console.deepgram.com"),
    ("CARTESIA_API_KEY", "falar com você · play.cartesia.ai"),
    ("CARTESIA_VOICE_ID", "qual voz usar (o id que aparece no Cartesia)"),
    ("GITHUB_TOKEN", "issues e repositórios · github.com/settings/tokens"),
)

MODOS = (
    Opcao(
        "IA local + OpenRouter",
        "o modelo pequeno roda aqui e resolve o rápido de graça; o grande "
        "entra quando precisa. Baixa ~2 GB e leva alguns minutos.",
    ),
    Opcao(
        "Só OpenRouter",
        "nada roda nesta máquina e nada é baixado. Toda mensagem sai do "
        "computador e é cobrada, e a memória busca por texto, não por sentido.",
    ),
)


def setup(
    somente_chaves: bool = typer.Option(
        False, "--chaves", help="Pergunta só as credenciais, sem as outras escolhas."
    ),
) -> None:
    """Configura a EVE: como ela pensa, com quais chaves e se sobe sozinha."""
    if not ask.interativo():
        console.print("[yellow]O setup precisa de um terminal.[/yellow]")
        console.print("[dim]Rode[/dim] [cyan]eve setup[/cyan] [dim]quando puder digitar.[/dim]")
        raise typer.Exit(0)

    console.print("\n  [bold]Vamos configurar a EVE.[/bold]")
    console.print("  [dim]Nada aqui é definitivo — dá para mudar tudo depois.[/dim]")

    store = build_store(paths().ensure().home / "secrets.json")
    atual = load_settings()

    try:
        modo = atual.ai.mode
        if not somente_chaves:
            escolha = ask.escolher(
                "Onde a EVE deve pensar?",
                "A escolha muda o que é baixado e o que sai do seu computador.",
                list(MODOS),
                padrao=0 if atual.ai.mode == "hybrid" else 1,
            )
            modo = "hybrid" if escolha == 0 else "external"

        console.print("\n  [bold green]Agora as chaves.[/bold green]")
        console.print(
            "  [dim]Só a do OpenRouter é necessária; nas outras, Enter pula "
            "e o recurso fica desligado.[/dim]"
        )

        gravadas, mantidas = [], []
        for nome, para_que in PERGUNTADAS:
            ja = store.get(nome)
            valor = ask.segredo(nome, para_que, _mascara(ja))
            if valor is None:
                if ja:
                    mantidas.append(nome)
                continue
            store.set(nome, valor)
            gravadas.append(nome)

        autostart = False
        if not somente_chaves:
            _permissoes()
            autostart = ask.sim_ou_nao(
                "A EVE deve subir sozinha depois do login?",
                "Se não, você a começa quando quiser com `eve run` ou `eve start`.",
                padrao=False,
            )
    except ask.Cancelado:
        console.print("\n  [yellow]Setup cancelado.[/yellow] Nada foi alterado.")
        console.print("  [dim]Rode[/dim] [cyan]eve setup[/cyan] [dim]quando quiser.[/dim]")
        raise typer.Exit(0) from None

    if not somente_chaves:
        update_config_file(lambda dados: dados.setdefault("ai", {}).update({"mode": modo}))

    _resumo(modo, gravadas, mantidas, store.missing_required(), somente_chaves)

    if autostart:
        from eve import service

        estado = service.install()
        if estado.loaded:
            console.print("  [green]✓[/green] vai subir sozinha depois do login")
        else:
            console.print("  [yellow]![/yellow] não consegui instalar o serviço")


def _permissoes() -> None:
    """As permissões do macOS, pedidas enquanto o usuário ainda está aqui.

    O primeiro AppleScript é o que faz o macOS abrir a caixa de diálogo. Se ela
    aparecer daqui a três dias, no meio de um pedido, o usuário não vai ligar
    uma coisa à outra — vai achar que a EVE não funciona.
    """
    import subprocess

    from eve.macos import native
    from eve.macos.osa import AppleScriptError, run_applescript

    console.print("\n  [bold green]Permissões do macOS.[/bold green]")
    console.print("  [dim]Sem elas a EVE não abre aplicativos nem mexe em janelas.[/dim]")
    console.print("  [dim]Pode aparecer uma caixa do sistema — é esperado.[/dim]\n")

    try:
        run_applescript('tell application "System Events" to get name of first process', timeout=30)
        console.print("  [green]✓[/green] automação")
    except AppleScriptError:
        console.print("  [yellow]![/yellow] automação negada")
        console.print("    [dim]Ajustes ▸ Privacidade e Segurança ▸ Automação[/dim]")

    if native.accessibility_trusted() is False:
        console.print("  [yellow]![/yellow] acessibilidade ainda não liberada")
        if ask.sim_ou_nao("Abrir os Ajustes na tela certa?", padrao=True):
            subprocess.run(
                [
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                ],
                check=False,
            )
            console.print("    [dim]marque o terminal na lista e volte para cá[/dim]")
    else:
        console.print("  [green]✓[/green] acessibilidade")


def _mascara(valor: str | None) -> str | None:
    if not valor:
        return None
    return f"{valor[:4]}…{valor[-4:]}" if len(valor) > 12 else "•" * len(valor)


def _resumo(
    modo: str,
    gravadas: list[str],
    mantidas: list[str],
    faltando: list[str],
    somente_chaves: bool,
) -> None:
    console.print("\n  [bold]Ficou assim:[/bold]\n")

    if not somente_chaves:
        if modo == "hybrid":
            console.print("  [green]✓[/green] IA local + OpenRouter")
            console.print("    [dim]o rápido roda aqui; o difícil vai para a nuvem[/dim]")
        else:
            console.print("  [green]✓[/green] só OpenRouter")
            console.print("    [dim]nada roda nesta máquina; a memória busca por texto[/dim]")

    for nome in gravadas:
        console.print(f"  [green]✓[/green] {nome} [dim]gravada[/dim]")
    for nome in mantidas:
        console.print(f"  [dim]·[/dim] {nome} [dim]mantida como estava[/dim]")

    desligados = [nome for nome, _ in PERGUNTADAS if nome not in gravadas and nome not in mantidas]
    for nome in desligados:
        console.print(f"  [dim]·[/dim] {nome} [dim]em branco — {KNOWN_SECRETS[nome]}[/dim]")

    if faltando:
        console.print(f"\n  [yellow]Sem {', '.join(faltando)} a EVE não fala com a nuvem.[/yellow]")
        console.print(f"  [dim]dá para gravar depois:[/dim] [cyan]eve key set {faltando[0]}[/cyan]")
