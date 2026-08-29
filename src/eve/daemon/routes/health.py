"""Endpoints de saúde e estado."""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Request

from eve import __version__

router = APIRouter()

# Componentes previstos pela spec. Cada fase troca "planejado" por estado real.
_PLANNED_COMPONENTS = (
    "ai_local",
    "ai_external",
    "memory",
    "voice",
    "tools",
    "skills",
    "mcp",
    "browser",
)


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    app = request.app
    bus = app.state.bus
    settings = app.state.settings
    return {
        "status": "ok",
        "version": __version__,
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - app.state.started_at, 3),
        "server": {"host": settings.server.host, "port": settings.server.port},
        "bus": {
            "published": bus.published,
            "subscribers": bus.subscriber_count,
            "history": len(bus.history(limit=10_000)),
        },
        "components": {
            **{name: "planejado" for name in _PLANNED_COMPONENTS},
            "tools": "ativo",
            "ai_local": "ativo",
            "ai_external": (
                "ativo" if app.state.secrets.has("OPENROUTER_API_KEY") else "sem credencial"
            ),
            "browser": "aberto" if app.state.browser.open else "pronto",
            "web": "ativo" if app.state.search is not None else "sem credencial",
            "voice": (
                "ativo"
                if app.state.secrets.has("DEEPGRAM_API_KEY")
                and app.state.secrets.has("CARTESIA_API_KEY")
                else "sem credencial"
            ),
        },
        "skills": {
            "installed": len(app.state.skills.skills),
            "enabled": sum(1 for s in app.state.skills.skills.values() if s.enabled),
        },
        "mcp": {
            "servers": len(app.state.mcp.connections),
            "connected": sum(1 for c in app.state.mcp.connections.values() if c.connected),
            "tools": app.state.mcp.tool_count,
        },
        "memory": {
            "total": await app.state.memory.store.count(),
            "semantic_search": app.state.memory.store.vectors.available,
        },
        "tasks": {
            "total": len(app.state.tasks),
            "active": len(app.state.tasks.active),
        },
        "chat": {
            "sessions": len(app.state.chat.sessions),
            "max_tool_rounds": app.state.chat.max_rounds,
        },
        "secrets": {
            "configured": sum(1 for s in app.state.secrets.describe() if s["configured"]),
            "missing_required": app.state.secrets.missing_required(),
        },
        "tools": {
            "count": len(app.state.tools.registry),
            "namespaces": app.state.tools.registry.namespaces(),
            "pending_approvals": len(app.state.tools.approvals),
        },
    }
