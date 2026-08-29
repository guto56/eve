"""Observa arquivos e projetos (spec §30).

Um diretório de projeto gera dezenas de alterações por segundo enquanto se
trabalha nele. O que interessa não é cada arquivo salvo: é o padrão — um teste
que quebrou, um build que falhou, um commit novo. Por isso o observador agrupa
antes de publicar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eve.events import EventType
from eve.watch.base import Observer

DEBOUNCE_MS = 2000
"""Salvar um arquivo dispara vários eventos; agrupá-los evita ruído."""

INTERESSANTES: dict[str, str] = {
    "package.json": "dependências",
    "pyproject.toml": "dependências",
    "uv.lock": "dependências",
    "Cargo.toml": "dependências",
    "Dockerfile": "infraestrutura",
    ".env": "configuração",
}


class FileObserver(Observer):
    kind = "files"

    def __init__(self, bus: Any, name: str, path: Path) -> None:
        super().__init__(bus, name)
        self.path = path

    async def run(self) -> None:
        from watchfiles import awatch

        if not self.path.exists():
            raise FileNotFoundError(f"não existe: {self.path}")

        async for mudancas in awatch(self.path, debounce=DEBOUNCE_MS, stop_event=self._stop):
            arquivos = sorted({Path(caminho) for _, caminho in mudancas})
            git = [a for a in arquivos if ".git" in a.parts]
            comuns = [a for a in arquivos if a not in git]

            if git:
                await self._git(git)
            if comuns:
                await self._arquivos(comuns)

    async def _arquivos(self, arquivos: list[Path]) -> None:
        notaveis = [a for a in arquivos if a.name in INTERESSANTES]
        await self.emit(
            EventType.FILE_CHANGED,
            path=str(self.path),
            count=len(arquivos),
            files=[str(a) for a in arquivos[:12]],
            notable=[{"file": a.name, "why": INTERESSANTES[a.name]} for a in notaveis],
        )

    async def _git(self, arquivos: list[Path]) -> None:
        """Um commit mexe em ``.git/HEAD`` e nos refs; o resto é ruído do git."""
        if not any(a.name in ("HEAD", "ORIG_HEAD") or "refs" in a.parts for a in arquivos):
            return
        await self.emit(EventType.GIT_CHANGED, path=str(self.path), files=len(arquivos))
