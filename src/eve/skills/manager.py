"""Instala, carrega e ativa Skills.

Uma Skill instalada não fica sempre ligada no prompt: as instruções e as
ferramentas dela entram quando a mensagem tem a ver com ela. É a mesma razão
de filtrar ferramentas por rota — contexto é caro.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from eve.logging import get_logger
from eve.mcp.client import MCPServerConfig
from eve.mcp.manager import MCPManager
from eve.secrets import SecretStore
from eve.skills.catalog import manifesto
from eve.skills.model import MANIFESTO, Skill, SkillError, load_skill

log = get_logger(__name__)

#: Prefixo que manda buscar o valor no Keychain em vez de usar o literal.
SEGREDO = "@"


class SkillManager:
    def __init__(self, raiz: Path, secrets: SecretStore, mcp: MCPManager) -> None:
        self.raiz = raiz
        self.secrets = secrets
        self.mcp = mcp
        self.skills: dict[str, Skill] = {}

    # ------------------------------------------------------------ carregar

    def load_all(self) -> list[Skill]:
        self.raiz.mkdir(parents=True, exist_ok=True)
        self.skills.clear()
        for diretorio in sorted(p for p in self.raiz.iterdir() if p.is_dir()):
            try:
                skill = load_skill(diretorio)
            except SkillError as exc:
                log.warning("skill.invalida", diretorio=diretorio.name, error=str(exc))
                continue
            self.skills[skill.name] = skill
        return list(self.skills.values())

    def get(self, nome: str) -> Skill | None:
        return self.skills.get(nome)

    # ------------------------------------------------------------ instalar

    def install(self, nome: str) -> Skill:
        """Instala uma Skill do catálogo embutido."""
        destino = self.raiz / nome
        destino.mkdir(parents=True, exist_ok=True)
        (destino / MANIFESTO).write_text(manifesto(nome), encoding="utf-8")
        skill = load_skill(destino)
        self.skills[skill.name] = skill
        log.info("skill.instalada", skill=skill.name)
        return skill

    def remove(self, nome: str) -> bool:
        skill = self.skills.pop(nome, None)
        if skill is None or skill.path is None:
            return False
        shutil.rmtree(skill.path, ignore_errors=True)
        log.info("skill.removida", skill=nome)
        return True

    def set_enabled(self, nome: str, ligada: bool) -> Skill | None:
        skill = self.skills.get(nome)
        if skill is None or skill.path is None:
            return None
        manifesto_path = skill.path / MANIFESTO
        texto = manifesto_path.read_text(encoding="utf-8")
        linhas = [linha for linha in texto.splitlines() if not linha.startswith("enabled")]
        # `enabled` fica logo depois do nome, para o arquivo continuar legível.
        for i, linha in enumerate(linhas):
            if linha.startswith("name"):
                linhas.insert(i + 1, f"enabled = {'true' if ligada else 'false'}")
                break
        else:  # pragma: no cover - manifesto sem nome não carrega
            linhas.insert(0, f"enabled = {'true' if ligada else 'false'}")
        manifesto_path.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        skill.enabled = ligada
        return skill

    # -------------------------------------------------------------- ativar

    def missing_secrets(self, skill: Skill) -> tuple[str, ...]:
        return tuple(nome for nome in skill.requires_secrets if not self.secrets.has(nome))

    async def connect_enabled(self, avulsos: list[MCPServerConfig] = ()) -> None:
        """Sobe os servidores MCP das Skills ligadas e os avulsos."""
        configs: list[MCPServerConfig] = [self._resolve(s) for s in avulsos if s.enabled]
        for skill in self.skills.values():
            if not skill.enabled:
                continue
            faltando = self.missing_secrets(skill)
            if faltando:
                log.info("skill.sem_credencial", skill=skill.name, falta=list(faltando))
                continue
            configs.extend(self._resolve(servidor) for servidor in skill.mcp)
        if configs:
            await self.mcp.connect_all(configs)

    def _resolve(self, servidor: MCPServerConfig) -> MCPServerConfig:
        """Troca `@NOME` pelo valor do Keychain, na hora de subir o servidor.

        O manifesto guarda o nome da credencial, nunca o valor — a Skill pode
        ser lida, versionada e compartilhada sem vazar nada.
        """
        env = {}
        for chave, valor in servidor.env.items():
            if valor.startswith(SEGREDO):
                resolvido = self.secrets.get(valor[1:].strip())
                if resolvido:
                    env[chave] = resolvido
            else:
                env[chave] = valor
        return MCPServerConfig(
            name=servidor.name,
            command=servidor.command,
            args=list(servidor.args),
            env=env,
            cwd=servidor.cwd,
            url=servidor.url,
            enabled=servidor.enabled,
        )

    def active_for(self, texto: str) -> list[Skill]:
        """Skills relevantes para esta mensagem."""
        return [
            skill
            for skill in self.skills.values()
            if skill.enabled and skill.matches(texto) and not self.missing_secrets(skill)
        ]

    def instructions_for(self, texto: str) -> str:
        ativas = self.active_for(texto)
        partes = [f"[{s.name}] {s.instructions.strip()}" for s in ativas if s.instructions.strip()]
        return "\n\n".join(partes)

    def namespaces_for(self, texto: str) -> tuple[str, ...]:
        namespaces: list[str] = []
        for skill in self.active_for(texto):
            namespaces.extend(skill.namespaces)
        return tuple(namespaces)

    def permission_overrides(self) -> dict[str, str]:
        """Permissões declaradas pelas Skills ligadas.

        A configuração do usuário é aplicada por cima: quem instala a Skill
        decide o padrão, quem usa decide o final.
        """
        regras: dict[str, str] = {}
        for skill in self.skills.values():
            if skill.enabled:
                regras.update(skill.permissions)
        return regras

    def describe(self) -> list[dict[str, Any]]:
        return [skill.describe(self.missing_secrets(skill)) for skill in self.skills.values()]
