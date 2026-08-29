"""Gerência dos servidores MCP e das ferramentas que eles trazem.

Uma ferramenta de MCP entra no mesmo Tool Bus das nativas: passa por
validação, por permissão e por auditoria (spec §20, §21). A EVE trata MCP como
uma extensão do Tool Bus, não como um mecanismo paralelo.
"""

from __future__ import annotations

import asyncio
from typing import Any

from eve.logging import get_logger
from eve.mcp.client import MCPConnection, MCPServerConfig
from eve.tools.registry import ToolRegistry
from eve.tools.spec import NoParams, RiskLevel, ToolContext, ToolSpec

log = get_logger(__name__)

#: Namespaces das ferramentas nativas. Um servidor MCP não pode se chamar
#: assim, sob pena de uma extensão sobrescrever uma ferramenta do sistema.
RESERVADOS = frozenset({"app", "url", "file", "clipboard", "system", "eve", "memory", "mcp"})

DEFAULT_RISK = RiskLevel.CONFIRM
"""Ferramenta de terceiro pede confirmação até o usuário decidir o contrário.

A EVE não sabe o que um servidor desconhecido faz; a spec §21 é explícita em
não dar acesso irrestrito automaticamente."""


class MCPManager:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.connections: dict[str, MCPConnection] = {}

    # ------------------------------------------------------------ conexão

    async def add(self, config: MCPServerConfig) -> MCPConnection:
        """Conecta um servidor e registra as ferramentas dele."""
        if config.name in RESERVADOS:
            raise ValueError(
                f"{config.name} é um namespace do sistema — escolha outro nome para o servidor"
            )
        if config.name in self.connections:
            await self.remove(config.name)

        conexao = MCPConnection(config)
        self.connections[config.name] = conexao
        if not config.enabled:
            return conexao

        if await conexao.connect():
            self._register_tools(conexao)
        return conexao

    async def remove(self, name: str) -> bool:
        conexao = self.connections.pop(name, None)
        if conexao is None:
            return False
        self._unregister_tools(conexao)
        await conexao.close()
        return True

    async def reconnect(self, name: str) -> MCPConnection | None:
        conexao = self.connections.get(name)
        if conexao is None:
            return None
        self._unregister_tools(conexao)
        await conexao.close()
        if await conexao.connect():
            self._register_tools(conexao)
        return conexao

    async def connect_all(self, configs: list[MCPServerConfig]) -> None:
        """Conecta os servidores configurados, sem deixar um travar os outros."""
        await asyncio.gather(*(self.add(c) for c in configs if c.enabled), return_exceptions=True)

    async def aclose(self) -> None:
        await asyncio.gather(
            *(self.remove(nome) for nome in list(self.connections)), return_exceptions=True
        )

    # --------------------------------------------------------- ferramentas

    def _tool_name(self, servidor: str, ferramenta: str) -> str:
        return f"{servidor}.{ferramenta}"

    def _register_tools(self, conexao: MCPConnection) -> None:
        for ferramenta in conexao.tools:
            nome = self._tool_name(conexao.config.name, ferramenta.name)
            if "__" in nome:
                log.warning("mcp.nome_incompativel", ferramenta=nome)
                continue
            self.registry.register(
                ToolSpec(
                    name=nome,
                    description=(
                        ferramenta.description or f"Ferramenta MCP de {conexao.server_name}"
                    ),
                    params=NoParams,
                    raw_schema=ferramenta.schema or {"type": "object", "properties": {}},
                    risk=DEFAULT_RISK,
                    handler=_make_handler(conexao, ferramenta.name),
                    reversible=False,
                    timeout=130.0,
                    requires=(f"mcp:{conexao.config.name}",),
                ),
                replace=True,
            )

    def _unregister_tools(self, conexao: MCPConnection) -> None:
        for ferramenta in conexao.tools:
            self.registry.unregister(self._tool_name(conexao.config.name, ferramenta.name))

    # ------------------------------------------------------------- estado

    def describe(self) -> list[dict[str, Any]]:
        return [c.describe() for c in self.connections.values()]

    @property
    def tool_count(self) -> int:
        return sum(len(c.tools) for c in self.connections.values() if c.connected)


def _make_handler(conexao: MCPConnection, ferramenta: str) -> Any:
    async def handler(params: Any, ctx: ToolContext) -> Any:
        argumentos = params if isinstance(params, dict) else {}
        return await conexao.call(ferramenta, argumentos)

    return handler
