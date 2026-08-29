"""Conexão com um servidor MCP.

O contexto do servidor (processo, streams, sessão) precisa ser aberto e
fechado na mesma tarefa — é exigência do anyio, que o SDK usa por baixo. Por
isso cada conexão vive numa tarefa própria: ela abre tudo, avisa que está
pronta e fica esperando o sinal de parada. As chamadas de ferramenta vêm de
outras tarefas e passam pela sessão já aberta.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from typing import Any

from eve.logging import get_logger

log = get_logger(__name__)

CONNECT_TIMEOUT = 60.0
"""``npx -y`` pode baixar o servidor na primeira vez."""

CALL_TIMEOUT = 120.0


@dataclass
class MCPTool:
    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    enabled: bool = True

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "enabled": self.enabled,
            # Nomes das variáveis, nunca os valores.
            "env": sorted(self.env),
        }


class MCPConnection:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.tools: list[MCPTool] = []
        self.server_name = ""
        self.protocol = ""
        self.error: str | None = None
        self._session: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._session is not None and self.error is None

    async def connect(self) -> bool:
        """Sobe o servidor e descobre as ferramentas. ``False`` se falhar."""
        if self._task is not None:
            return self.connected
        self._stop.clear()
        self._ready.clear()
        self.error = None
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), CONNECT_TIMEOUT)
        except TimeoutError:
            self.error = f"não respondeu em {CONNECT_TIMEOUT:.0f}s"
            await self.close()
            return False
        return self.connected

    async def _run(self) -> None:
        from mcp.client.session import ClientSession

        try:
            async with self._streams() as (read, write), ClientSession(read, write) as session:
                init = await session.initialize()
                self.server_name = init.server_info.name
                self.protocol = init.protocol_version
                listadas = await session.list_tools()
                self.tools = [
                    MCPTool(
                        name=t.name,
                        description=(t.description or "").strip(),
                        schema=_schema_of(t),
                    )
                    for t in listadas.tools
                ]
                self._session = session
                log.info(
                    "mcp.conectado",
                    servidor=self.config.name,
                    nome=self.server_name,
                    ferramentas=len(self.tools),
                )
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = _resumo(exc)
            log.warning("mcp.falhou", servidor=self.config.name, error=self.error)
        finally:
            self._session = None
            self._ready.set()

    def _streams(self) -> Any:
        config = self.config
        if config.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            return _drop_third(streamablehttp_client(config.url))

        from mcp.client.stdio import (
            StdioServerParameters,
            stdio_client,
        )

        if not shutil.which(config.command):
            raise FileNotFoundError(f"comando não encontrado: {config.command}")
        return stdio_client(
            StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=dict(config.env) or None,
                cwd=config.cwd,
            )
        )

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError(f"servidor MCP {self.config.name} não está conectado")
        resultado = await asyncio.wait_for(self._session.call_tool(tool, arguments), CALL_TIMEOUT)
        if getattr(resultado, "is_error", False):
            raise RuntimeError(_texto(resultado) or "o servidor MCP recusou a chamada")
        return _conteudo(resultado)

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self._session = None

    def describe(self) -> dict[str, Any]:
        return {
            **self.config.describe(),
            "connected": self.connected,
            "server": self.server_name,
            "protocol": self.protocol,
            "tools": [t.name for t in self.tools],
            "error": self.error,
        }


class _drop_third:
    """O transporte HTTP devolve três valores; a sessão quer dois."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __aenter__(self) -> tuple[Any, Any]:
        read, write, *_ = await self._inner.__aenter__()
        return read, write

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._inner.__aexit__(*exc)


def _schema_of(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return dict(schema) if isinstance(schema, dict) else {}


def _texto(resultado: Any) -> str:
    partes = [c.text for c in getattr(resultado, "content", []) if hasattr(c, "text")]
    return "\n".join(partes).strip()


def _conteudo(resultado: Any) -> Any:
    """Estruturado quando o servidor oferece; senão, o texto."""
    estruturado = getattr(resultado, "structured_content", None)
    if estruturado:
        return estruturado
    texto = _texto(resultado)
    return texto or None


def _resumo(exc: BaseException) -> str:
    # ExceptionGroup do anyio esconde a causa real atrás de um agregado.
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _resumo(exc.exceptions[0])
    mensagem = str(exc) or type(exc).__name__
    return mensagem[:200]
