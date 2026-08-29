from __future__ import annotations

from pathlib import Path

import pytest

from eve.config import load_settings
from eve.paths import paths


def test_defaults() -> None:
    s = load_settings()
    assert s.server.host == "127.0.0.1"
    assert s.server.port == 4242
    assert s.log.level == "info"
    assert s.ai.local_backend == "ollama"


def test_reads_toml_file(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text('[server]\nport = 5000\n\n[log]\nlevel = "debug"\n')
    s = load_settings()
    assert s.server.port == 5000
    assert s.log.level == "debug"


def test_env_beats_file(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text("[server]\nport = 5000\n")
    monkeypatch.setenv("EVE_SERVER__PORT", "6001")
    assert load_settings().server.port == 6001


def test_unknown_keys_are_ignored(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text("[futuro]\nx = 1\n\n[server]\nport = 4243\n")
    assert load_settings().server.port == 4243


def test_invalid_port_is_rejected(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text("[server]\nport = 99999\n")
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        load_settings()
