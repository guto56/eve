from __future__ import annotations

from pathlib import Path

import pytest

from eve.macos.safepath import PathNotAllowed, resolve_safe_path


def test_allows_inside_the_root(tmp_path: Path) -> None:
    roots = (str(tmp_path),)
    assert resolve_safe_path(str(tmp_path / "a" / "b.txt"), roots, ()) == tmp_path / "a" / "b.txt"


def test_root_itself_is_allowed(tmp_path: Path) -> None:
    assert resolve_safe_path(str(tmp_path), (str(tmp_path),), ()) == tmp_path.resolve()


def test_rejects_outside_the_root(tmp_path: Path) -> None:
    with pytest.raises(PathNotAllowed, match="fora dos diretórios"):
        resolve_safe_path("/etc/passwd", (str(tmp_path),), ())


def test_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathNotAllowed):
        resolve_safe_path(str(tmp_path / ".." / ".." / "etc"), (str(tmp_path),), ())


def test_symlink_cannot_escape(tmp_path: Path) -> None:
    """Um link apontando para fora não vira um atalho para fora."""
    inside = tmp_path / "dentro"
    inside.mkdir()
    outside = tmp_path.parent / "fora-do-limite"
    outside.mkdir(exist_ok=True)
    link = inside / "atalho"
    link.symlink_to(outside)

    with pytest.raises(PathNotAllowed):
        resolve_safe_path(str(link / "x.txt"), (str(inside),), ())


def test_denied_area_is_blocked_even_inside_the_root(tmp_path: Path) -> None:
    secret = tmp_path / ".ssh"
    secret.mkdir()
    with pytest.raises(PathNotAllowed, match="área protegida"):
        resolve_safe_path(str(secret / "id_rsa"), (str(tmp_path),), (str(secret),))


def test_denied_uses_tilde_expansion() -> None:
    with pytest.raises(PathNotAllowed, match="área protegida"):
        resolve_safe_path("~/.ssh/id_rsa", ("~",), ("~/.ssh",))


def test_empty_path_is_rejected(tmp_path: Path) -> None:
    for bad in ("", "   "):
        with pytest.raises(PathNotAllowed, match="vazio"):
            resolve_safe_path(bad, (str(tmp_path),), ())


def test_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_safe_path(str(tmp_path / "sumido.txt"), (str(tmp_path),), (), must_exist=True)


def test_tilde_is_expanded(tmp_path: Path) -> None:
    resolved = resolve_safe_path("~/Documents", ("~",), ())
    assert resolved == (Path.home() / "Documents").resolve()
