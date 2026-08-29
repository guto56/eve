"""API da memória."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from eve.memory.manager import MemoryManager
from eve.memory.models import MemoryKind

router = APIRouter(prefix="/api/memory")


class RememberRequest(BaseModel):
    content: str = Field(min_length=3, max_length=4000)
    kind: MemoryKind = MemoryKind.SEMANTIC
    importance: float = Field(default=0.6, ge=0.0, le=1.0)


def _memory(request: Request) -> MemoryManager:
    return request.app.state.memory


@router.get("")
async def stats(request: Request) -> dict[str, Any]:
    return await _memory(request).store.stats()


@router.get("/recent")
async def recent(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    kind: MemoryKind | None = None,
) -> dict[str, Any]:
    memorias = await _memory(request).store.recent(limit, [kind] if kind else None)
    return {"memories": [m.as_dict() for m in memorias], "count": len(memorias)}


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
    kind: MemoryKind | None = None,
) -> dict[str, Any]:
    memorias = await _memory(request).recall(q, limit, [kind] if kind else None)
    return {"memories": [m.as_dict() for m in memorias], "count": len(memorias)}


@router.post("")
async def remember(request: Request, body: RememberRequest) -> dict[str, Any]:
    memoria, estado = await _memory(request).remember(
        body.content, body.kind, importance=body.importance, source="api"
    )
    return {**memoria.as_dict(), "estado": estado}


@router.delete("/{uid}")
async def forget(request: Request, uid: str) -> dict[str, Any]:
    if not await _memory(request).forget(uid):
        raise HTTPException(status_code=404, detail="memória desconhecida")
    return {"deleted": uid}
