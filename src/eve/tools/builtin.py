"""Ferramentas nativas mínimas.

As ferramentas de macOS de verdade (apps, arquivos, calendário, notificações)
chegam na Fase 3. Estas existem para que o Tool Bus tenha o que executar desde
já e para que ``eve doctor`` e a interface tenham algo real para mostrar.
"""

from __future__ import annotations

import platform
import subprocess
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from eve import __version__
from eve.tools.registry import ToolRegistry
from eve.tools.registry import tool as tool_decorator
from eve.tools.spec import NoParams, RiskLevel, ToolContext, ToolParams

#: O que a EVE responde quando perguntam o que ela é ou como usá-la.
#: Sem isto ela inventa: perguntada "como rodar o EVE?", respondeu
#: "uv run fastapi dev" — plausível, genérico e errado.
COMANDOS: dict[str, str] = {
    "eve start | stop | restart": "liga, desliga e reinicia a EVE",
    "eve status": "estado do sistema",
    "eve doctor": "diagnóstico completo da instalação",
    "eve chat [mensagem]": "conversa (sem mensagem, abre um REPL)",
    "eve web": "abre a interface no navegador",
    "eve memory list | search | add | forget": "memória",
    "eve tool list | call | audit": "ferramentas e auditoria",
    "eve permission list | set | grant": "permissões",
    "eve skill list | install": "Skills",
    "eve mcp list | add": "servidores MCP",
    "eve task list | show | cancel": "tarefas de agente",
    "eve watch add | list | status": "o que ela observa",
    "eve voice say | test": "voz",
    "eve key list | set | import": "credenciais no Keychain",
    "eve logs [-f]": "logs",
}


class AboutParams(ToolParams):
    topic: Literal["identidade", "capacidades", "comandos"] = Field(
        default="capacidades",
        description="identidade = o que ela é; capacidades = o que sabe fazer; "
        "comandos = como operá-la.",
    )


class EchoParams(ToolParams):
    message: str = Field(description="Texto a devolver.", max_length=4000)


def register_builtin_tools(registry: ToolRegistry) -> ToolRegistry:
    """Registra as ferramentas nativas em ``registry``."""

    @tool_decorator(
        "system.info",
        description="Informações do computador: sistema, chip, memória e versão da EVE.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def system_info(_: NoParams, __: ToolContext) -> dict[str, Any]:
        return {
            "system": platform.system(),
            "release": platform.mac_ver()[0] or platform.release(),
            "machine": platform.machine(),
            "chip": _sysctl("machdep.cpu.brand_string"),
            "memory_gb": _memory_gb(),
            "python": platform.python_version(),
            "eve": __version__,
        }

    @tool_decorator(
        "system.time",
        description="Data e hora atuais, local e em UTC.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def system_time(_: NoParams, __: ToolContext) -> dict[str, Any]:
        now = datetime.now().astimezone()
        return {
            "local": now.isoformat(),
            "utc": datetime.now(UTC).isoformat(),
            "timezone": str(now.tzinfo),
            "epoch": time.time(),
        }

    @tool_decorator(
        "eve.about",
        description="O que a EVE é, o que ela sabe fazer e como operá-la.",
        params=AboutParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        keywords=(
            "eve",
            "voce",
            "vc",
            "quem",
            "capacidade",
            "consegue",
            "sabe",
            "ajuda",
            "comando",
            "rodar",
            "usar",
            "funciona",
        ),
    )
    async def about(params: AboutParams, ctx: ToolContext) -> dict[str, Any]:
        por_grupo: dict[str, list[str]] = {}
        for spec in registry:
            por_grupo.setdefault(spec.namespace, []).append(spec.name.split(".", 1)[1])
        return {
            "topic": params.topic,
            "sou": (
                "a EVE, assistente pessoal que roda neste Mac. Local-first: "
                "modelo local para conversa e roteamento, modelo externo para "
                "tarefas complexas, memória e credenciais no próprio computador."
            ),
            "versao": __version__,
            "capacidades": {grupo: sorted(nomes) for grupo, nomes in sorted(por_grupo.items())},
            "comandos": COMANDOS,
            "onde_moro": {
                "configuracao": "~/.eve/config.toml",
                "memoria": "~/.eve/data/eve.db",
                "logs": "~/.eve/logs/",
                "skills": "~/.eve/skills/",
            },
            "interface": f"http://{ctx.settings.server.host}:{ctx.settings.server.port}",
        }

    @tool_decorator(
        "eve.echo",
        description="Devolve a mensagem recebida. Serve para testar o Tool Bus.",
        params=EchoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def echo(params: EchoParams, ctx: ToolContext) -> dict[str, Any]:
        return {"message": params.message, "source": ctx.source}

    return registry


def _sysctl(key: str) -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "desconhecido"
    return result.stdout.strip()


def _memory_gb() -> float | None:
    raw = _sysctl("hw.memsize")
    try:
        return round(int(raw) / 1024**3, 1)
    except ValueError:
        return None
