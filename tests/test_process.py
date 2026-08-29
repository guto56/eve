from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from eve.cli import process
from eve.config import Settings
from eve.paths import paths

DEAD_PID = 999_999


def test_read_pid_missing_file() -> None:
    assert process.read_pid() is None


def test_read_pid_ignores_garbage(isolated_home: Path) -> None:
    paths().ensure()
    paths().pid_file.write_text("nao-e-numero")
    assert process.read_pid() is None


def test_write_and_clear_pid(isolated_home: Path) -> None:
    process.write_pid(4242)
    assert process.read_pid() == 4242
    process.clear_pid()
    assert process.read_pid() is None
    process.clear_pid()  # idempotente


def test_pid_alive_for_self_and_dead_pid() -> None:
    assert process.pid_alive(os.getpid()) is True
    assert process.pid_alive(DEAD_PID) is False


def test_pid_alive_detects_zombie_child() -> None:
    """Um filho encerrado mas ainda não colhido não pode contar como vivo."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    # child.wait() já colheu; um segundo processo garante o caminho do waitpid
    other = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = other.pid
    os.waitpid(pid, 0)
    assert process.pid_alive(pid) is False


def test_is_eve_daemon_rejects_foreign_process() -> None:
    assert process.is_eve_daemon(1) is False  # launchd
    assert process.is_eve_daemon(DEAD_PID) is False


def test_running_pid_clears_stale_pidfile(isolated_home: Path) -> None:
    process.write_pid(DEAD_PID)
    assert process.running_pid() is None
    assert process.read_pid() is None


def test_running_pid_refuses_recycled_pid(isolated_home: Path) -> None:
    """PID vivo mas de outro programa: não é a EVE, então o pidfile é descartado."""
    process.write_pid(1)  # launchd, vivo e definitivamente não é a EVE
    assert process.running_pid() is None
    assert process.read_pid() is None


def test_terminate_dead_pid_reports_success() -> None:
    assert process.terminate(DEAD_PID) is True


def test_base_url_uses_settings(settings: Settings) -> None:
    assert process.base_url(settings) == f"http://127.0.0.1:{settings.server.port}"


def test_probe_health_returns_none_when_nothing_listens(settings: Settings) -> None:
    assert process.probe_health(settings, timeout=0.3) is None
