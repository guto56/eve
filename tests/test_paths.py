from __future__ import annotations

from pathlib import Path

import pytest

from eve.paths import eve_home, paths


def test_home_follows_env(isolated_home: Path) -> None:
    assert eve_home() == isolated_home


def test_home_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVE_HOME", raising=False)
    assert eve_home() == Path.home() / ".eve"


def test_ensure_is_idempotent(isolated_home: Path) -> None:
    p = paths().ensure()
    p.ensure()
    for d in (p.home, p.logs, p.run, p.data, p.skills, p.models):
        assert d.is_dir()
    assert p.config_file.parent == p.home
    assert p.db_file.parent == p.data
