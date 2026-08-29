"""Aplicação FastAPI do Core da EVE.

A interface web e a CLI são apenas clientes desta aplicação; todo o estado
vive aqui, no daemon.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from eve import __version__
from eve.ai.manager import ProviderManager
from eve.browser.session import BrowserSession
from eve.bus import EventBus
from eve.chat.engine import ChatEngine
from eve.config import Settings, load_settings
from eve.daemon.routes import ai, chat, extensions, health, memory, tools, voice, web, ws
from eve.events import EventType
from eve.logging import get_logger
from eve.mcp.client import MCPServerConfig
from eve.mcp.manager import MCPManager
from eve.memory.embeddings import Embedder
from eve.memory.manager import MemoryManager
from eve.memory.store import MemoryStore
from eve.paths import paths
from eve.permissions import PermissionEngine
from eve.router.router import Router
from eve.secrets import build_store
from eve.skills.manager import SkillManager
from eve.tools.approvals import ApprovalBroker
from eve.tools.audit import AuditLog
from eve.tools.builtin import register_builtin_tools
from eve.tools.bus import ToolBus
from eve.tools.macos_tools import register_macos_tools
from eve.tools.memory_tools import register_memory_tools
from eve.tools.registry import ToolRegistry
from eve.tools.spec import RiskLevel
from eve.tools.web_tools import register_web_tools
from eve.websearch.tavily import TavilySearch

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bus: EventBus = app.state.bus
    app.state.started_at = time.time()
    # Servidores MCP podem demorar (npx baixa na primeira vez); subir em
    # segundo plano deixa o Core disponível imediatamente.
    conexoes = asyncio.create_task(
        app.state.skills.connect_enabled(_standalone_servers(app.state.settings))
    )

    await bus.emit(EventType.SYSTEM_STARTED, version=__version__, pid=os.getpid())
    log.info(
        "core.started", version=__version__, pid=os.getpid(), tools=len(app.state.tools.registry)
    )
    try:
        yield
    finally:
        conexoes.cancel()
        await asyncio.gather(conexoes, return_exceptions=True)
        await app.state.mcp.aclose()
        await app.state.browser.close()
        if app.state.search is not None:
            await app.state.search.aclose()
        removidas = await app.state.memory.housekeeping()
        if removidas:
            log.info("memoria.expiradas_removidas", count=removidas)
        cancelled = app.state.tools.approvals.deny_all("o Core está encerrando")
        if cancelled:
            log.info("approvals.cancelled", count=cancelled)
        await app.state.memory.aclose()
        await app.state.providers.aclose()
        await bus.emit(EventType.SYSTEM_STOPPING)
        log.info("core.stopping")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="EVE Core",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.bus = EventBus()
    app.state.started_at = time.time()
    app.state.secrets = build_store(paths().ensure().home / "secrets.json")
    app.state.providers = ProviderManager(settings, app.state.secrets)
    app.state.memory = build_memory(settings, app.state.bus, app.state.providers)
    app.state.browser = BrowserSession()
    app.state.search = _build_search(app.state.secrets)
    app.state.tools = build_tool_bus(
        settings,
        app.state.bus,
        {
            "memory": app.state.memory,
            "browser": app.state.browser,
            "search": app.state.search,
        },
    )
    app.state.mcp = MCPManager(app.state.tools.registry)
    app.state.skills = SkillManager(paths().ensure().skills, app.state.secrets, app.state.mcp)
    app.state.skills.load_all()
    _apply_skill_permissions(app)
    app.state.router = Router(
        app.state.tools.registry,
        app.state.providers,
        extra_namespaces=app.state.skills.namespaces_for,
    )
    app.state.chat = ChatEngine(
        router=app.state.router,
        providers=app.state.providers,
        tools=app.state.tools,
        bus=app.state.bus,
        memory=app.state.memory,
        skills=app.state.skills,
    )
    app.include_router(health.router)
    app.include_router(ws.router)
    app.include_router(tools.router)
    app.include_router(ai.router)
    app.include_router(chat.router)
    app.include_router(memory.router)
    app.include_router(voice.router)
    app.include_router(extensions.router)
    # A interface é montada por último: sua rota curinga não pode capturar
    # nada da API.
    web.mount(app)
    return app


def build_memory(settings: Settings, bus: EventBus, providers: ProviderManager) -> MemoryManager:
    p = paths().ensure()
    store = MemoryStore(p.db_file, settings.memory.embedding_dimensions)
    embedder = Embedder(
        host=settings.ai.ollama_host,
        model=settings.memory.embedding_model,
        dimensions=settings.memory.embedding_dimensions,
    )
    return MemoryManager(store, embedder, providers, bus)


def build_tool_bus(
    settings: Settings, bus: EventBus, services: dict[str, object] | None = None
) -> ToolBus:
    """Monta registro, permissões, auditoria e Tool Bus a partir da configuração."""
    registry = register_web_tools(
        register_memory_tools(register_macos_tools(register_builtin_tools(ToolRegistry())))
    )
    permissions = PermissionEngine(
        overrides=parse_overrides(settings.permissions.overrides),
        grants=dict(settings.permissions.grants),
    )
    return ToolBus(
        registry=registry,
        permissions=permissions,
        events=bus,
        audit=AuditLog(paths().ensure().audit_file),
        settings=settings,
        approvals=ApprovalBroker(settings.permissions.confirm_timeout),
        services=dict(services or {}),
    )


def _build_search(secrets: Any) -> TavilySearch | None:
    """``None`` quando não há credencial — a EVE segue sem pesquisar."""
    try:
        return TavilySearch(secrets.get("TAVILY_API_KEY") or "")
    except ValueError:
        return None


def _standalone_servers(settings: Settings) -> list[MCPServerConfig]:
    """Servidores MCP declarados direto no config.toml, fora de Skills."""
    return [
        MCPServerConfig(
            name=item.name,
            command=item.command,
            args=list(item.args),
            env=dict(item.env),
            cwd=item.cwd,
            url=item.url,
            enabled=item.enabled,
        )
        for item in settings.mcp
    ]


def _apply_skill_permissions(app: FastAPI) -> None:
    """Padrões das Skills por baixo, escolhas do usuário por cima.

    Quem instala a Skill decide o padrão; quem usa decide o final.
    """
    engine = app.state.tools.permissions
    das_skills = parse_overrides(app.state.skills.permission_overrides())
    engine.overrides = {**das_skills, **engine.overrides}


def parse_overrides(raw: dict[str, str]) -> dict[str, RiskLevel]:
    """Converte a política do TOML, ignorando nível desconhecido em vez de quebrar."""
    out: dict[str, RiskLevel] = {}
    for pattern, level in raw.items():
        try:
            out[pattern] = RiskLevel(level.lower())
        except ValueError:
            log.warning("permissions.unknown_level", pattern=pattern, level=level)
    return out
