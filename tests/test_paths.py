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


def test_pasta_de_trabalho_e_visivel_e_se_explica(isolated_home: Path) -> None:
    """Uma captura salva em ~/.eve é uma captura perdida para quem não sabe
    mostrar arquivos ocultos no Finder."""
    p = paths().ensure()
    assert not p.work.name.startswith(".")
    assert p.screenshots.parent == p.work
    assert (p.work / "LEIA-ME.txt").exists()
    assert "~/.eve" in (p.work / "LEIA-ME.txt").read_text()
