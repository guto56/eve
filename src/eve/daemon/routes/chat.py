"""API de conversa."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from eve.chat.engine import ChatEngine
from eve.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api")


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    session: str | None = Field(default=None, max_length=64)
    stream: bool = True


class RouteRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)


def _engine(request: Request) -> ChatEngine:
    return request.app.state.chat


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> Any:
    engine = _engine(request)

    if not body.stream:
        eventos = [event.as_dict() async for event in engine.send(body.message, body.session)]
        return {"events": eventos, "text": _collect_text(eventos)}

    async def sse() -> AsyncIterator[str]:
        # Uma exceção aqui dentro encerraria o stream sem dizer nada ao
        # cliente, que ficaria esperando para sempre. Todo fim é anunciado.
        try:
            async for event in engine.send(body.message, body.session):
                yield _frame(event.as_dict())
        except Exception as exc:
            log.exception("chat.stream_falhou")
            yield _frame({"kind": "error", "error": str(exc), "kind_detail": type(exc).__name__})
            yield _frame({"kind": "done", "aborted": True})

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.post("/route")
async def route_only(request: Request, body: RouteRequest) -> dict[str, Any]:
    """Só a decisão de roteamento, sem executar nada. Útil para inspecionar."""
    decision = await request.app.state.router.route(body.message)
    return decision.as_dict()


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    sessions = _engine(request).sessions.all()
    return {"sessions": [s.describe() for s in sessions], "count": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    session = _engine(request).sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="sessão desconhecida")
    return {
        **session.describe(),
        "history": [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls": [c.as_dict() for c in m.tool_calls],
            }
            for m in session.messages
        ],
    }


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict[str, Any]:
    removed = _engine(request).sessions.delete(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="sessão desconhecida")
    return {"deleted": session_id}


def _collect_text(eventos: list[dict[str, Any]]) -> str:
    return "".join(e.get("text", "") for e in eventos if e["kind"] == "delta")
