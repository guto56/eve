"""Ciclo de vida real: sobe o daemon em um processo separado e conversa com ele."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
from websockets.sync.client import connect

from eve.cli import process
from eve.cli.main import app
from eve.paths import paths

pytestmark = pytest.mark.integration
runner = CliRunner()


@pytest.fixture
def running_daemon(free_port: int, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EVE_SERVER__PORT", str(free_port))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert "EVE ativa" in result.output
    try:
        yield free_port
    finally:
        runner.invoke(app, ["stop"])
        assert process.running_pid() is None


def test_start_writes_pidfile_and_serves_status(running_daemon: int) -> None:
    pid = process.read_pid()
    assert pid is not None
    assert process.pid_alive(pid)

    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["status"] == "ok"
    assert data["pid"] == pid
    assert data["server"]["port"] == running_daemon


def test_start_is_idempotent(running_daemon: int) -> None:
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0
    assert "já está rodando" in result.output


def test_doctor_sees_the_running_core(running_daemon: int) -> None:
    data = json.loads(runner.invoke(app, ["doctor", "--json"]).output)
    core = next(c for c in data["checks"] if c["name"] == "Core")
    assert core["status"] == "ok"
    assert str(running_daemon) in core["detail"]


def test_websocket_streams_events_from_the_live_daemon(running_daemon: int) -> None:
    with connect(f"ws://127.0.0.1:{running_daemon}/ws?topics=client.*&history=0") as ws:
        assert json.loads(ws.recv())["type"] == "hello"
        live = json.loads(ws.recv())
        assert live["event"]["type"] == "client.connected"
        ws.send(json.dumps({"op": "ping"}))
        assert json.loads(ws.recv()) == {"type": "pong"}


def test_daemon_writes_structured_logs(running_daemon: int, isolated_home: Path) -> None:
    log_file = paths().log_file
    assert log_file.exists()
    assert "core.started" in log_file.read_text()

    result = runner.invoke(app, ["logs", "-n", "5"])
    assert result.exit_code == 0


def test_stop_then_status_reports_stopped(running_daemon: int) -> None:
    assert runner.invoke(app, ["stop"]).exit_code == 0
    assert process.running_pid() is None
    assert runner.invoke(app, ["status"]).exit_code == 1


def test_restart_replaces_the_process(running_daemon: int) -> None:
    old_pid = process.read_pid()
    result = runner.invoke(app, ["restart"])
    assert result.exit_code == 0, result.output
    new_pid = process.read_pid()
    assert new_pid is not None
    assert new_pid != old_pid
    assert process.pid_alive(new_pid)


# ------------------------------------------------- Fase 2: Tool Bus ponta a ponta


def test_tool_list_and_call_through_the_cli(running_daemon: int) -> None:
    listed = runner.invoke(app, ["tool", "list", "--json"])
    assert listed.exit_code == 0
    assert json.loads(listed.output)["count"] == 42

    called = runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "olá"}'])
    assert called.exit_code == 0, called.output
    assert "olá" in called.output


def test_tool_call_rejects_bad_json_before_touching_the_daemon(running_daemon: int) -> None:
    result = runner.invoke(app, ["tool", "call", "eve.echo", "-a", "{nao json"])
    assert result.exit_code == 1
    assert "JSON válido" in result.output


def test_tool_call_surfaces_validation_errors(running_daemon: int) -> None:
    result = runner.invoke(app, ["tool", "call", "eve.echo", "-a", "{}"])
    assert result.exit_code == 1
    assert "invalid_args" in result.output


def test_unknown_tool_from_the_cli(running_daemon: int) -> None:
    result = runner.invoke(app, ["tool", "call", "nao.existe"])
    assert result.exit_code == 1
    assert "desconhecida" in result.output


def test_permission_set_blocks_a_tool_without_restarting(running_daemon: int) -> None:
    assert runner.invoke(app, ["permission", "set", "eve.echo", "blocked"]).exit_code == 0

    listed = json.loads(runner.invoke(app, ["permission", "list", "--json"]).output)
    assert listed["overrides"] == {"eve.echo": "blocked"}

    blocked = runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}'])
    assert blocked.exit_code == 1
    assert "denied" in blocked.output

    assert runner.invoke(app, ["permission", "unset", "eve.echo"]).exit_code == 0
    freed = runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}'])
    assert freed.exit_code == 0


def test_confirmation_flow_end_to_end(running_daemon: int) -> None:
    """Marca uma ferramenta como CONFIRM e responde à confirmação pelo terminal."""
    assert runner.invoke(app, ["permission", "set", "eve.echo", "confirm"]).exit_code == 0

    approved = runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}', "--yes"])
    assert approved.exit_code == 0, approved.output
    assert "oi" in approved.output

    denied = runner.invoke(
        app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}'], input="n\n"
    )
    assert denied.exit_code == 1
    assert "denied" in denied.output

    runner.invoke(app, ["permission", "unset", "eve.echo"])


def test_privileged_grant_flow(running_daemon: int) -> None:
    assert runner.invoke(app, ["permission", "set", "eve.echo", "privileged"]).exit_code == 0

    without_grant = runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}'])
    assert without_grant.exit_code == 1
    assert "concessão explícita" in without_grant.output

    assert runner.invoke(app, ["permission", "grant", "eve.echo"]).exit_code == 0
    with_grant = runner.invoke(
        app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}', "--yes"]
    )
    assert with_grant.exit_code == 0, with_grant.output

    assert runner.invoke(app, ["permission", "revoke", "eve.echo"]).exit_code == 0
    revoked = runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "oi"}', "--yes"])
    assert revoked.exit_code == 1

    runner.invoke(app, ["permission", "unset", "eve.echo"])


def test_audit_records_every_call(running_daemon: int) -> None:
    runner.invoke(app, ["tool", "call", "eve.echo", "-a", '{"message": "auditado"}'])
    runner.invoke(app, ["tool", "call", "eve.echo", "-a", "{}"])  # inválida

    data = json.loads(runner.invoke(app, ["tool", "audit", "--json"]).output)
    outcomes = [e["outcome"] for e in data["entries"]]
    assert "ok" in outcomes
    assert "failed" in outcomes


def test_commands_require_a_running_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVE_SERVER__PORT", "4999")
    result = runner.invoke(app, ["tool", "list"])
    assert result.exit_code == 1
    assert "não está rodando" in result.output


# ------------------------------------------ Fase 4: credenciais e provedores


def test_key_list_shows_state_without_values(running_daemon: int) -> None:
    result = runner.invoke(app, ["key", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["missing"] == ["OPENROUTER_API_KEY"]
    assert all(s["hint"] is None for s in data["secrets"])


def test_key_set_and_delete(running_daemon: int) -> None:
    definida = runner.invoke(app, ["key", "set", "TAVILY_API_KEY", "--value", "tvly-teste-12345"])
    assert definida.exit_code == 0
    assert "gravada no Keychain" in definida.output

    data = json.loads(runner.invoke(app, ["key", "list", "--json"]).output)
    entrada = next(s for s in data["secrets"] if s["name"] == "TAVILY_API_KEY")
    assert entrada["configured"] is True
    assert entrada["hint"] == "tvly…2345"

    removida = runner.invoke(app, ["key", "delete", "TAVILY_API_KEY"])
    assert removida.exit_code == 0
    assert "removida" in removida.output


def test_key_set_rejects_invalid_name(running_daemon: int) -> None:
    result = runner.invoke(app, ["key", "set", "minuscula", "--value", "x"])
    assert result.exit_code == 1
    assert "nome inválido" in result.output


def test_key_import(running_daemon: int, tmp_path: Path) -> None:
    arquivo = tmp_path / "env.txt"
    arquivo.write_text("TAVILY_API_KEY=tvly-do-arquivo\nGITHUB_TOKEN=ghp_teste\n")

    result = runner.invoke(app, ["key", "import", str(arquivo)])
    assert result.exit_code == 0
    assert result.output.count("importada") == 2
    assert "continua no disco" in result.output

    de_novo = runner.invoke(app, ["key", "import", str(arquivo)])
    assert "já existia" in de_novo.output

    for nome in ("TAVILY_API_KEY", "GITHUB_TOKEN"):
        runner.invoke(app, ["key", "delete", nome])


def test_key_import_missing_file(running_daemon: int, tmp_path: Path) -> None:
    result = runner.invoke(app, ["key", "import", str(tmp_path / "nao-existe.txt")])
    assert result.exit_code == 1
    assert "não encontrado" in result.output


def test_provider_list(running_daemon: int) -> None:
    data = json.loads(runner.invoke(app, ["provider", "list", "--json"]).output)
    assert data["models"]["local"] == "qwen3.5:2b"
    ollama = next(p for p in data["providers"] if p["name"] == "ollama")
    assert ollama["ok"] is True


def test_provider_models(running_daemon: int) -> None:
    result = runner.invoke(app, ["provider", "models", "ollama", "-f", "qwen"])
    assert result.exit_code == 0
    assert "qwen3.5" in result.output


def test_ask_the_local_model(running_daemon: int) -> None:
    result = runner.invoke(app, ["ask", "--no-stream", "-r", "fast", "Diga apenas: ok"])
    assert result.exit_code == 0, result.output
    assert "qwen3.5:0.8b" in result.output


def test_ask_streams_by_default(running_daemon: int) -> None:
    result = runner.invoke(app, ["ask", "-r", "fast", "Conte de 1 a 3"])
    assert result.exit_code == 0, result.output
    assert result.output.strip()


def test_ask_external_without_credential_fails_clearly(running_daemon: int) -> None:
    result = runner.invoke(app, ["ask", "--no-stream", "-r", "external", "oi"])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.output


# ------------------------------------------------- Fase 5: router e conversa


def test_chat_fast_path_from_the_cli(running_daemon: int) -> None:
    result = runner.invoke(app, ["chat", "-v", "que horas são"])
    assert result.exit_code == 0, result.output
    assert "via rule" in result.output
    assert "system.time" in result.output
    assert "São " in result.output


def test_chat_answers_a_system_question_without_any_model(running_daemon: int) -> None:
    result = runner.invoke(app, ["chat", "quanto de RAM tem esse Mac"])
    assert result.exit_code == 0, result.output
    assert "GB de RAM" in result.output


def test_chat_lists_the_running_apps(running_daemon: int) -> None:
    result = runner.invoke(app, ["chat", "quais aplicativos estão abertos"])
    assert result.exit_code == 0, result.output
    assert "Finder" in result.output


def test_route_endpoint_from_the_cli_side(running_daemon: int) -> None:
    import httpx2 as httpx

    base = f"http://127.0.0.1:{running_daemon}"
    data = httpx.post(f"{base}/api/route", json={"message": "abra o Safari"}, timeout=30).json()
    assert data["fast_path"] is True
    assert data["tool_call"]["arguments"] == {"name": "Safari"}
    assert data["latency_ms"] < 50


def test_chat_requires_a_running_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVE_SERVER__PORT", "4998")
    result = runner.invoke(app, ["chat", "oi"])
    assert result.exit_code == 1
    assert "não está rodando" in result.output


async def test_encerramento_desiste_de_etapa_travada() -> None:
    """Ctrl+C só vale se encerrar.

    Um servidor MCP que não responde — `npx` baixando o pacote na primeira vez —
    segurava o desligamento inteiro, e o Ctrl+C parecia não funcionar.
    """
    import asyncio

    from eve.daemon.app import _com_prazo

    async def nunca_termina() -> str:
        await asyncio.sleep(3600)
        return "nunca"

    inicio = asyncio.get_running_loop().time()
    assert await _com_prazo("teste", nunca_termina(), prazo=0.2) is None
    assert asyncio.get_running_loop().time() - inicio < 2

    async def termina() -> str:
        return "pronto"

    assert await _com_prazo("teste", termina(), prazo=5) == "pronto"


def test_stop_encontra_o_eve_run_e_nao_so_o_daemon() -> None:
    """Para quem digita `eve stop`, o daemon e o `eve run` são a mesma EVE."""
    from eve.cli.process import PADRAO_PROCESSO

    assert "eve[.]daemon" in PADRAO_PROCESSO
    assert "bin/eve run" in PADRAO_PROCESSO
