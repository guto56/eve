"""Interface web servida pelo próprio daemon.

Os arquivos em ``static/`` são o build do ``ui/`` (Vite + React). Vão para o
repositório de propósito: quem instala a EVE não deveria precisar de Node.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parent / "static"


def is_built() -> bool:
    return (STATIC / "index.html").exists()
