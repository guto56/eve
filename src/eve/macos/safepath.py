"""Cerca em volta do sistema de arquivos.

As ferramentas de arquivo da EVE só enxergam o que a configuração permite. Todo
caminho é resolvido antes da checagem, então um symlink apontando para fora não
serve de atalho.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_ALLOWED_ROOTS: tuple[str, ...] = ("~",)

DEFAULT_DENIED: tuple[str, ...] = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.docker",
    "~/.config/gh",
    "~/Library/Keychains",
    "~/Library/Cookies",
    "~/Library/Application Support/com.apple.TCC",
)


class PathNotAllowed(PermissionError):
    """O caminho está fora do que a configuração autoriza."""


def _expand(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def resolve_safe_path(
    raw: str,
    allowed_roots: tuple[str, ...] = DEFAULT_ALLOWED_ROOTS,
    denied: tuple[str, ...] = DEFAULT_DENIED,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve ``raw`` e garante que ele está dentro da cerca."""
    if not raw or not raw.strip():
        raise PathNotAllowed("caminho vazio")

    target = _expand(raw)

    roots = [_expand(root) for root in allowed_roots]
    if not any(target == root or root in target.parents for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise PathNotAllowed(f"{target} está fora dos diretórios permitidos ({allowed})")

    for pattern in denied:
        blocked = _expand(pattern)
        if target == blocked or blocked in target.parents:
            raise PathNotAllowed(f"{target} está em uma área protegida ({pattern})")

    if must_exist and not target.exists():
        raise FileNotFoundError(f"não existe: {target}")

    return target
