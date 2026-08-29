"""Aplicação FastAPI do Core da EVE.

A interface web e a CLI são apenas clientes desta aplicação; todo o estado
vive aqui, no daemon.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from eve import __version__
from eve.ai.manager import ProviderManager
from eve.bus import EventBus
from eve.chat.engine import ChatEngine
from eve.config import Settings, load_settings
from eve.daemon.routes import ai, chat, health, memory, tools, web, ws
from eve.events import EventType
from eve.logging import get_logger
from eve.memory.embeddings import Embedder
from eve.memory.manager import MemoryManager
from eve.memory.store import MemoryStore
from eve.paths import paths
from eve.permissions import PermissionEngine
from eve.router.router import Router
from eve.secrets import build_store
from eve.tools.approvals import ApprovalBroker
from eve.tools.audit import AuditLog
from eve.tools.builtin import register_builtin_tools
from eve.tools.bus import ToolBus
from eve.tools.macos_tools import register_macos_tools
from eve.tools.memory_tools import register_memory_tools
from eve.tools.registry import ToolRegistry
from eve.tools.spec import RiskLevel

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bus: EventBus = app.state.bus
    app.state.started_at = time.time()
    await bus.emit(EventType.SYSTEM_STARTED, version=__version__, pid=os.getpid())
    log.info(
        "core.started", version=__version__, pid=os.getpid(), tools=len(app.state.tools.registry)
    )
    try:
        yield
    finally:
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
    app.state.tools = build_tool_bus(settings, app.state.bus, {"memory": app.state.memory})
    app.state.router = Router(app.state.tools.registry, app.state.providers)
    app.state.chat = ChatEngine(
        router=app.state.router,
        providers=app.state.providers,
        tools=app.state.tools,
        bus=app.state.bus,
        memory=app.state.memory,
    )
    app.include_router(health.router)
    app.include_router(ws.router)
    app.include_router(tools.router)
    app.include_router(ai.router)
    app.include_router(chat.router)
    app.include_router(memory.router)
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
    registry = register_memory_tools(register_macos_tools(register_builtin_tools(ToolRegistry())))
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


def parse_overrides(raw: dict[str, str]) -> dict[str, RiskLevel]:
    """Converte a política do TOML, ignorando nível desconhecido em vez de quebrar."""
    out: dict[str, RiskLevel] = {}
    for pattern, level in raw.items():
        try:
            out[pattern] = RiskLevel(level.lower())
        except ValueError:
            log.warning("permissions.unknown_level", pattern=pattern, level=level)
    return out
