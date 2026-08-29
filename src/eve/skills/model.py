"""O que é uma Skill.

Uma Skill declara tudo que traz: instruções, servidores MCP, permissões e
quando ela é relevante. Nada fica implícito — a spec §21 exige que o usuário
possa ver exatamente o que está instalando.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eve.mcp.client import MCPServerConfig

MANIFESTO = "skill.toml"
INSTRUCOES = "instructions.md"


class SkillError(ValueError):
    pass


@dataclass
class Skill:
    name: str
    version: str = "0.1.0"
    description: str = ""
    enabled: bool = True
    instructions: str = ""
    keywords: tuple[str, ...] = ()
    mcp: list[MCPServerConfig] = field(default_factory=list)
    permissions: dict[str, str] = field(default_factory=dict)
    requires_secrets: tuple[str, ...] = ()
    path: Path | None = None

    @property
    def namespaces(self) -> tuple[str, ...]:
        """Namespaces de ferramenta que esta Skill traz."""
        return tuple(servidor.name for servidor in self.mcp)

    def matches(self, texto: str) -> bool:
        """A Skill é relevante para esta mensagem?

        Casamento por palavra-chave. É burro de propósito: decidir isso com
        modelo custaria uma chamada por mensagem, e errar para menos apenas
        deixa a Skill de fora — errar para mais enche o prompt.
        """
        if not self.keywords:
            return False
        alvo = texto.lower()
        return any(palavra.lower() in alvo for palavra in self.keywords)

    def describe(self, faltando: tuple[str, ...] = ()) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "enabled": self.enabled,
            "keywords": list(self.keywords),
            "mcp": [servidor.describe() for servidor in self.mcp],
            "permissions": dict(self.permissions),
            "requires_secrets": list(self.requires_secrets),
            "missing_secrets": list(faltando),
            "path": str(self.path) if self.path else None,
        }


def load_skill(diretorio: Path) -> Skill:
    """Lê uma Skill de um diretório."""
    manifesto = diretorio / MANIFESTO
    if not manifesto.exists():
        raise SkillError(f"{diretorio.name}: falta {MANIFESTO}")
    try:
        with manifesto.open("rb") as fh:
            dados = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SkillError(f"{diretorio.name}: TOML inválido — {exc}") from None

    nome = dados.get("name") or diretorio.name
    if not isinstance(nome, str) or not nome.strip():
        raise SkillError(f"{diretorio.name}: nome inválido")

    instrucoes = str(dados.get("instructions", "")).strip()
    arquivo_instrucoes = diretorio / INSTRUCOES
    if not instrucoes and arquivo_instrucoes.exists():
        instrucoes = arquivo_instrucoes.read_text(encoding="utf-8").strip()

    return Skill(
        name=nome.strip(),
        version=str(dados.get("version", "0.1.0")),
        description=str(dados.get("description", "")).strip(),
        enabled=bool(dados.get("enabled", True)),
        instructions=instrucoes,
        keywords=tuple(str(k) for k in dados.get("keywords", ()) if str(k).strip()),
        mcp=[_servidor(item) for item in dados.get("mcp", [])],
        permissions={str(k): str(v) for k, v in (dados.get("permissions") or {}).items()},
        requires_secrets=tuple(str(s) for s in dados.get("requires_secrets", ())),
        path=diretorio,
    )


def _servidor(item: dict[str, Any]) -> MCPServerConfig:
    nome = str(item.get("name", "")).strip()
    if not nome:
        raise SkillError("servidor MCP sem nome")
    return MCPServerConfig(
        name=nome,
        command=str(item.get("command", "")),
        args=[str(a) for a in item.get("args", [])],
        env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
        cwd=item.get("cwd"),
        url=str(item.get("url", "")),
        enabled=bool(item.get("enabled", True)),
    )
