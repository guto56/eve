"""API do Tool Bus: listar, executar, confirmar e auditar."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from eve.tools.bus import ToolBus
from eve.tools.registry import UnknownToolError

router = APIRouter(prefix="/api")


class CallRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    approved: bool
    by: str = "interface"


def _tools(request: Request) -> ToolBus:
    return request.app.state.tools


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    bus = _tools(request)
    items = []
    for spec in bus.registry:
        decision = bus.permissions.decide(spec)
        items.append({**spec.describe(), "effective": decision.as_dict()})
    return {"tools": items, "count": len(items), "namespaces": bus.registry.namespaces()}


@router.get("/tools/{name}")
async def get_tool(request: Request, name: str) -> dict[str, Any]:
    bus = _tools(request)
    try:
        spec = bus.registry.get(name)
    except UnknownToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {**spec.describe(), "effective": bus.permissions.decide(spec).as_dict()}


@router.post("/tools/{name}/call")
async def call_tool(request: Request, name: str, body: CallRequest) -> dict[str, Any]:
    result = await _tools(request).call(name, body.args, source="api")
    if result.error_kind == "unknown_tool":
        raise HTTPException(status_code=404, detail=result.error)
    return result.as_dict()


@router.get("/permissions")
async def get_permissions(request: Request) -> dict[str, Any]:
    bus = _tools(request)
    return {
        "overrides": {k: v.value for k, v in bus.permissions.overrides.items()},
        "grants": bus.permissions.grants,
        "confirm_timeout": bus.settings.permissions.confirm_timeout,
    }


@router.post("/permissions/reload")
async def reload_permissions(request: Request) -> dict[str, Any]:
    """Relê o config.toml do disco e reconstrói a política, sem reiniciar o Core."""
    from eve.config import load_settings
    from eve.daemon.app import parse_overrides

    settings = load_settings()
    bus = _tools(request)
    bus.settings = settings
    bus.permissions.overrides = parse_overrides(settings.permissions.overrides)
    bus.permissions.grants = dict(settings.permissions.grants)
    bus.approvals.default_timeout = settings.permissions.confirm_timeout
    request.app.state.settings = settings
    return {
        "reloaded": True,
        "overrides": {k: v.value for k, v in bus.permissions.overrides.items()},
        "grants": bus.permissions.grants,
    }


@router.get("/approvals")
async def list_approvals(request: Request) -> dict[str, Any]:
    pending = _tools(request).approvals.pending()
    return {"pending": pending, "count": len(pending)}


@router.post("/approvals/{request_id}")
async def decide_approval(
    request: Request, request_id: str, body: ApprovalDecision
) -> dict[str, Any]:
    resolved = _tools(request).approvals.resolve(request_id, body.approved, body.by)
    if not resolved:
        raise HTTPException(status_code=404, detail="confirmação inexistente ou já decidida")
    return {"request_id": request_id, "approved": body.approved, "by": body.by}


@router.get("/audit")
async def audit(request: Request, limit: int = Query(default=50, ge=1, le=1000)) -> dict[str, Any]:
    entries = _tools(request).audit.tail(limit)
    return {"entries": entries, "count": len(entries)}
