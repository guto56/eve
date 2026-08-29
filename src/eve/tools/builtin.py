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
from typing import Any

from pydantic import Field

from eve import __version__
from eve.tools.registry import ToolRegistry
from eve.tools.registry import tool as tool_decorator
from eve.tools.spec import NoParams, RiskLevel, ToolContext, ToolParams


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
