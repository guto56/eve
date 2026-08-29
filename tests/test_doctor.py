from __future__ import annotations

from pathlib import Path

from eve.config import Settings
from eve.doctor import (
    Check,
    Status,
    check_config,
    check_daemon,
    check_home,
    check_port,
    check_python,
    run_checks,
    worst,
)
from eve.paths import paths


def test_python_check_passes_on_supported_runtime() -> None:
    assert check_python(Settings()).status is Status.OK


def test_home_check_creates_tree(isolated_home: Path) -> None:
    result = check_home(Settings())
    assert result.status is Status.OK
    assert isolated_home.is_dir()


def test_config_check_accepts_missing_file() -> None:
    assert check_config(Settings()).status is Status.OK


def test_config_check_rejects_broken_toml(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text("isso [ nao é toml")
    result = check_config(Settings())
    assert result.status is Status.FAIL
    assert "TOML" in result.detail


def test_daemon_check_warns_when_stopped(settings: Settings) -> None:
    result = check_daemon(settings)
    assert result.status is Status.WARN
    assert "eve start" in result.detail


def test_port_check_reports_free_port(settings: Settings) -> None:
    assert check_port(settings).status is Status.OK


def test_run_checks_returns_every_check(settings: Settings) -> None:
    results = run_checks(settings)
    assert len(results) == 14
    assert {"Python", "Core", "Porta", "Configuração"} <= {c.name for c in results}


def test_a_broken_check_does_not_break_the_doctor(monkeypatch) -> None:
    import eve.doctor as doctor_module

    def explode(_: Settings) -> Check:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor_module, "CHECKS", [explode])
    results = run_checks(Settings())
    assert results[0].status is Status.FAIL
    assert "boom" in results[0].detail


def test_worst_picks_the_most_severe() -> None:
    ok = Check("a", Status.OK, "")
    warn = Check("b", Status.WARN, "")
    fail = Check("c", Status.FAIL, "")
    assert worst([ok, ok]) is Status.OK
    assert worst([ok, warn]) is Status.WARN
    assert worst([ok, warn, fail]) is Status.FAIL


def test_tools_check_counts_builtin_tools(settings: Settings) -> None:
    from eve.doctor import check_tools

    result = check_tools(settings)
    assert result.status is Status.OK
    assert "27 nativa" in result.detail


def test_tools_check_warns_about_blocked_tools(settings: Settings) -> None:
    from eve.doctor import check_tools

    settings.permissions.overrides = {"eve.*": "blocked"}
    result = check_tools(settings)
    assert result.status is Status.WARN
    assert "eve.echo" in result.detail


def test_secrets_check_flags_missing_required(settings: Settings) -> None:
    from eve.doctor import check_secrets

    result = check_secrets(settings)
    assert result.status is Status.WARN
    assert "OPENROUTER_API_KEY" in result.detail


def test_providers_check_sees_the_local_provider(settings: Settings) -> None:
    from eve.doctor import check_providers

    result = check_providers(settings)
    # Sem credencial externa o resultado é WARN, com o Ollama funcionando.
    assert result.status in {Status.OK, Status.WARN}
    assert "ollama" in result.detail
