"""Ferramentas nativas do macOS, exercitadas de verdade nesta máquina.

Nada aqui é simulado: o clipboard é o clipboard (restaurado depois), a lista de
apps é a real e os arquivos são criados dentro de uma pasta isolada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eve.tools.bus import ToolBus
from eve.tools.registry import ToolRegistry


def test_every_macos_tool_is_registered(registry: ToolRegistry) -> None:
    from eve.tools.macos_tools import register_macos_tools

    register_macos_tools(registry)
    assert registry.namespaces() == ["app", "clipboard", "file", "system", "url"]
    assert len(registry) == 20


def test_destructive_tools_are_not_safe(registry: ToolRegistry) -> None:
    """Nada que possa destruir trabalho fica em SAFE."""
    from eve.tools.macos_tools import register_macos_tools

    register_macos_tools(registry)
    for name in ("app.quit", "file.write", "file.move", "file.trash", "system.screenshot"):
        assert registry.get(name).risk.value != "safe", name


# --------------------------------------------------------------------- arquivos


async def test_write_read_roundtrip(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "nota.txt"
    escrita = await macos_bus.call(
        "file.write", {"path": str(alvo), "content": "olá, EVE"}, auto_approve=True
    )
    assert escrita.ok is True

    leitura = await macos_bus.call("file.read", {"path": str(alvo)})
    assert leitura.value["content"] == "olá, EVE"
    assert leitura.value["truncated"] is False


async def test_write_refuses_to_clobber_by_default(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "nota.txt"
    alvo.write_text("original")
    result = await macos_bus.call(
        "file.write", {"path": str(alvo), "content": "novo"}, auto_approve=True
    )
    assert result.ok is False
    assert result.error_kind == "already_exists"
    assert alvo.read_text() == "original"


async def test_write_overwrites_when_asked(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "nota.txt"
    alvo.write_text("original")
    result = await macos_bus.call(
        "file.write",
        {"path": str(alvo), "content": "novo", "overwrite": True},
        auto_approve=True,
    )
    assert result.ok is True
    assert alvo.read_text() == "novo"


async def test_read_is_truncated_at_the_limit(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "grande.txt"
    alvo.write_text("x" * 5000)
    result = await macos_bus.call("file.read", {"path": str(alvo), "max_bytes": 100})
    assert result.value["truncated"] is True
    assert len(result.value["content"]) == 100
    assert result.value["bytes"] == 5000


async def test_read_outside_the_fence_is_refused(macos_bus: ToolBus, sandbox: Path) -> None:
    result = await macos_bus.call("file.read", {"path": "/etc/hosts"})
    assert result.ok is False
    assert result.error_kind == "not_permitted"
    assert "fora dos diretórios permitidos" in result.error


async def test_write_outside_the_fence_is_refused(macos_bus: ToolBus, sandbox: Path) -> None:
    result = await macos_bus.call(
        "file.write", {"path": "/tmp/eve-invasao.txt", "content": "x"}, auto_approve=True
    )
    assert result.ok is False
    assert not Path("/tmp/eve-invasao.txt").exists()


async def test_list_directory(macos_bus: ToolBus, sandbox: Path) -> None:
    (sandbox / "pasta").mkdir()
    (sandbox / "b.txt").write_text("b")
    (sandbox / ".oculto").write_text("x")

    result = await macos_bus.call("file.list", {"path": str(sandbox)})
    nomes = [e["name"] for e in result.value["entries"]]
    assert nomes == ["pasta", "b.txt"]  # pastas primeiro, ocultos fora

    com_ocultos = await macos_bus.call("file.list", {"path": str(sandbox), "include_hidden": True})
    assert ".oculto" in [e["name"] for e in com_ocultos.value["entries"]]


async def test_list_respects_the_limit(macos_bus: ToolBus, sandbox: Path) -> None:
    for i in range(10):
        (sandbox / f"{i}.txt").write_text("x")
    result = await macos_bus.call("file.list", {"path": str(sandbox), "limit": 3})
    assert result.value["count"] == 3


async def test_list_on_a_file_is_an_error(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "a.txt"
    alvo.write_text("a")
    result = await macos_bus.call("file.list", {"path": str(alvo)})
    assert result.ok is False
    assert result.error_kind == "wrong_kind"
    assert "não é uma pasta" in result.error


async def test_info_reports_metadata(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "a.txt"
    alvo.write_text("doze bytes!")
    result = await macos_bus.call("file.info", {"path": str(alvo)})
    assert result.value["kind"] == "arquivo"
    assert result.value["size"] == 11
    assert result.value["readable"] is True
    assert "modified" in result.value


async def test_mkdir_is_idempotent(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "nova" / "funda"
    primeira = await macos_bus.call("file.mkdir", {"path": str(alvo)})
    segunda = await macos_bus.call("file.mkdir", {"path": str(alvo)})
    assert primeira.value["created"] is True
    assert segunda.value["created"] is False
    assert alvo.is_dir()


async def test_move(macos_bus: ToolBus, sandbox: Path) -> None:
    origem = sandbox / "a.txt"
    origem.write_text("conteúdo")
    destino = sandbox / "sub" / "b.txt"
    result = await macos_bus.call(
        "file.move", {"source": str(origem), "destination": str(destino)}, auto_approve=True
    )
    assert result.ok is True
    assert not origem.exists()
    assert destino.read_text() == "conteúdo"


async def test_copy_never_overwrites(macos_bus: ToolBus, sandbox: Path) -> None:
    origem = sandbox / "a.txt"
    origem.write_text("a")
    destino = sandbox / "b.txt"
    destino.write_text("b")
    result = await macos_bus.call("file.copy", {"source": str(origem), "destination": str(destino)})
    assert result.ok is False
    assert destino.read_text() == "b"


async def test_copy_directory(macos_bus: ToolBus, sandbox: Path) -> None:
    origem = sandbox / "pasta"
    origem.mkdir()
    (origem / "dentro.txt").write_text("x")
    destino = sandbox / "copia"
    result = await macos_bus.call("file.copy", {"source": str(origem), "destination": str(destino)})
    assert result.ok is True
    assert (destino / "dentro.txt").read_text() == "x"


async def test_trash_is_reversible(macos_bus: ToolBus, sandbox: Path) -> None:
    """file.trash manda para a Lixeira; o arquivo continua existindo."""
    alvo = sandbox / "descartavel.txt"
    alvo.write_text("adeus")
    result = await macos_bus.call("file.trash", {"path": str(alvo)}, auto_approve=True)
    assert result.ok is True
    assert not alvo.exists()

    na_lixeira = Path(result.value["now_at"])
    assert na_lixeira.exists()
    assert na_lixeira.read_text() == "adeus"
    na_lixeira.unlink()  # limpa a Lixeira do usuário depois do teste


async def test_trash_requires_confirmation(macos_bus: ToolBus, sandbox: Path) -> None:
    alvo = sandbox / "protegido.txt"
    alvo.write_text("fico")
    result = await macos_bus.call("file.trash", {"path": str(alvo)})  # ninguém confirma
    assert result.ok is False
    assert result.error_kind == "denied"
    assert alvo.exists()


# ------------------------------------------------------------------ clipboard


async def test_clipboard_roundtrip(macos_bus: ToolBus, preserve_clipboard: None) -> None:
    await macos_bus.call("clipboard.write", {"text": "EVE esteve aqui"})
    result = await macos_bus.call("clipboard.read")
    assert result.value["text"] == "EVE esteve aqui"
    assert result.value["empty"] is False


# ----------------------------------------------------------------- apps e URLs


async def test_app_list_sees_the_real_machine(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("app.list")
    assert result.ok is True
    assert result.value["count"] >= 1
    nomes = [a["name"] for a in result.value["apps"]]
    assert "Finder" in nomes
    assert all(a["pid"] > 0 for a in result.value["apps"])


async def test_frontmost_app(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("app.frontmost")
    assert result.ok is True
    assert result.value["name"]
    assert result.value["pid"] > 0


async def test_app_open_in_background(macos_bus: ToolBus) -> None:
    """Abre o Finder sem roubar o foco — já está rodando, então nada muda na tela."""
    result = await macos_bus.call("app.open", {"name": "Finder", "background": True})
    assert result.ok is True
    assert result.value["background"] is True


async def test_open_unknown_app_fails_clearly(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("app.open", {"name": "AplicativoQueNaoExiste7742"})
    assert result.ok is False
    assert result.error_kind == "handler_error"


async def test_quit_needs_confirmation_and_never_runs_without_it(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("app.quit", {"name": "Finder"})
    assert result.ok is False
    assert result.error_kind == "denied"


async def test_quit_reports_applescript_errors(macos_bus: ToolBus) -> None:
    result = await macos_bus.call(
        "app.quit", {"name": "AplicativoQueNaoExiste7742"}, auto_approve=True
    )
    assert result.ok is False
    assert result.error_kind == "handler_error"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,<script>", "ftp://x", "só texto"],
)
async def test_url_open_rejects_dangerous_schemes(macos_bus: ToolBus, url: str) -> None:
    result = await macos_bus.call("url.open", {"url": url})
    assert result.ok is False
    assert result.error_kind == "invalid_args"


async def test_url_open_accepts_https(macos_bus: ToolBus) -> None:
    spec = macos_bus.registry.get("url.open")
    parsed = spec.params.model_validate({"url": "https://exemplo.com"})
    assert parsed.url == "https://exemplo.com"


# --------------------------------------------------------------------- sistema


async def test_volume_is_readable(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("system.volume")
    assert result.ok is True
    assert 0 <= result.value["level"] <= 100
    assert isinstance(result.value["muted"], bool)


async def test_set_volume_round_trips(macos_bus: ToolBus) -> None:
    """Lê, regrava o mesmo valor e confere — sem alterar nada de fato."""
    atual = (await macos_bus.call("system.volume")).value["level"]
    result = await macos_bus.call("system.set_volume", {"level": atual})
    assert result.ok is True
    assert (await macos_bus.call("system.volume")).value["level"] == atual


async def test_set_volume_rejects_out_of_range(macos_bus: ToolBus) -> None:
    for nivel in (-1, 101):
        result = await macos_bus.call("system.set_volume", {"level": nivel})
        assert result.error_kind == "invalid_args"


async def test_notify(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("system.notify", {"message": "Fase 3 em teste", "title": "EVE"})
    assert result.ok is True


async def test_screenshot_needs_confirmation(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("system.screenshot")
    assert result.ok is False
    assert result.error_kind == "denied"


async def test_screenshot_reports_missing_permission_clearly(
    macos_bus: ToolBus, sandbox: Path
) -> None:
    """Sem Gravação de Tela concedida, o erro precisa dizer exatamente isso."""
    alvo = sandbox / "tela.png"
    result = await macos_bus.call("system.screenshot", {"path": str(alvo)}, auto_approve=True)
    if result.ok:
        assert alvo.exists()
        assert result.value["bytes"] > 0
    else:
        assert "Gravação de Tela" in result.error


async def test_reading_a_protected_area_is_not_permitted(macos_bus: ToolBus) -> None:
    """A cerca vale mesmo com a raiz padrão: ~/.ssh continua fora de alcance."""
    macos_bus.settings.files.allowed_roots = ["~"]
    result = await macos_bus.call("file.list", {"path": "~/.ssh"})
    assert result.error_kind == "not_permitted"
    assert "área protegida" in result.error


async def test_missing_file_is_reported_as_not_found(macos_bus: ToolBus, sandbox: Path) -> None:
    result = await macos_bus.call("file.read", {"path": str(sandbox / "fantasma.txt")})
    assert result.error_kind == "not_found"


async def test_web_only_service_falls_back_to_the_browser(macos_bus: ToolBus) -> None:
    """ "Abra o YouTube" não é pedido para achar um app chamado YouTube."""
    result = await macos_bus.call("app.open", {"name": "youtube"})
    assert result.ok is True
    assert result.value["via"] == "web"
    assert result.value["url"] == "https://www.youtube.com"


async def test_installed_app_wins_over_the_web_fallback(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("app.open", {"name": "Finder", "background": True})
    assert result.value["via"] == "app"


async def test_unknown_app_still_fails_clearly(macos_bus: ToolBus) -> None:
    result = await macos_bus.call("app.open", {"name": "AppQueNaoExiste7742"})
    assert result.ok is False
