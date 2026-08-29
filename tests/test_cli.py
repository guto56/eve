from __future__ import annotations

import json

from typer.testing import CliRunner

from eve import __version__
from eve.cli.main import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_core_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("start", "stop", "restart", "status", "doctor", "logs", "web", "config"):
        assert command in result.output


def test_status_when_stopped_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setenv("EVE_SERVER__PORT", "4321")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "parada" in result.output


def test_status_json_when_stopped(monkeypatch) -> None:
    monkeypatch.setenv("EVE_SERVER__PORT", "4322")
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "parada"


def test_doctor_json_is_machine_readable(monkeypatch) -> None:
    monkeypatch.setenv("EVE_SERVER__PORT", "4323")
    result = runner.invoke(app, ["doctor", "--json"])
    data = json.loads(result.output)
    assert data["overall"] in {"ok", "warn", "fail"}
    assert len(data["checks"]) == 14


def test_stop_when_not_running() -> None:
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert "não está rodando" in result.output


def test_logs_without_log_file() -> None:
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 0
    assert "Ainda não há logs" in result.output


def test_config_path_points_into_eve_home(isolated_home) -> None:
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(isolated_home) in result.output


def test_config_show_json() -> None:
    result = runner.invoke(app, ["config", "show", "--json"])
    data = json.loads(result.output)
    assert data["server"]["port"] == 4242
    assert data["ai"]["local_backend"] == "ollama"
