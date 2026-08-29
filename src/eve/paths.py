"""Layout de diretórios da EVE.

Tudo vive sob ``EVE_HOME`` (padrão ``~/.eve``). A variável de ambiente
``EVE_HOME`` redireciona a árvore inteira, o que mantém os testes isolados
da instalação real do usuário.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def config_file(self) -> Path:
        return self.home / "config.toml"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs / "eve.log"

    @property
    def audit_file(self) -> Path:
        return self.logs / "audit.jsonl"

    @property
    def run(self) -> Path:
        return self.home / "run"

    @property
    def pid_file(self) -> Path:
        return self.run / "eve.pid"

    @property
    def data(self) -> Path:
        return self.home / "data"

    @property
    def db_file(self) -> Path:
        return self.data / "eve.db"

    @property
    def skills(self) -> Path:
        return self.home / "skills"

    @property
    def models(self) -> Path:
        return self.home / "models"

    def ensure(self) -> Paths:
        """Cria a árvore de diretórios. Idempotente."""
        for d in (self.home, self.logs, self.run, self.data, self.skills, self.models):
            d.mkdir(parents=True, exist_ok=True)
        return self


def eve_home() -> Path:
    raw = os.environ.get("EVE_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".eve"


def paths() -> Paths:
    """Resolve os caminhos a cada chamada — nunca cacheia, para respeitar EVE_HOME."""
    return Paths(eve_home())
