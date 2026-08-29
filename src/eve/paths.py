"""Layout de diretórios da EVE.

Tudo vive sob ``EVE_HOME`` (padrão ``~/.eve``). A variável de ambiente
``EVE_HOME`` redireciona a árvore inteira, o que mantém os testes isolados
da instalação real do usuário.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

LEIAME = """Esta é a pasta da EVE.

Tudo que ela criar para você — capturas de tela, arquivos, downloads — vem
parar aqui, a não ser que você peça outro lugar ("tira um print e salva nos
Downloads").

  Capturas/   capturas de tela
  Downloads/  o que a EVE baixar
  Notas/      textos e anotações

A configuração, a memória e os logs ficam em ~/.eve, que é uma pasta oculta
porque é infraestrutura, não coisa sua.
"""


@dataclass(frozen=True)
class Paths:
    home: Path
    work: Path
    """Pasta visível para o que a EVE cria (padrão ``~/EVE``)."""

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

    # --- pasta visível ------------------------------------------------

    @property
    def screenshots(self) -> Path:
        return self.work / "Capturas"

    @property
    def downloads(self) -> Path:
        return self.work / "Downloads"

    @property
    def notes(self) -> Path:
        return self.work / "Notas"

    def ensure(self) -> Paths:
        """Cria a árvore de diretórios. Idempotente."""
        for d in (self.home, self.logs, self.run, self.data, self.skills, self.models):
            d.mkdir(parents=True, exist_ok=True)
        # A pasta de trabalho nasce com o README explicando o que é ela.
        self.work.mkdir(parents=True, exist_ok=True)
        leiame = self.work / "LEIA-ME.txt"
        if not leiame.exists():
            leiame.write_text(LEIAME, encoding="utf-8")
        return self


def workspace() -> Path:
    """Pasta visível onde a EVE guarda o que cria.

    ``~/.eve`` é oculta de propósito — é infraestrutura. Mas uma captura de
    tela salva lá é uma captura que o usuário não acha: aconteceu de verdade,
    e quem não conhece ``Cmd+Shift+.`` fica sem o arquivo. O que a EVE produz
    vai para uma pasta comum, que aparece no Finder.
    """
    raw = os.environ.get("EVE_WORKSPACE")
    return Path(raw).expanduser() if raw else Path.home() / "EVE"


def eve_home() -> Path:
    raw = os.environ.get("EVE_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".eve"


def paths() -> Paths:
    """Resolve os caminhos a cada chamada — nunca cacheia, para respeitar EVE_HOME."""
    return Paths(eve_home(), workspace())
