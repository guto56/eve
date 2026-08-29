"""API de credenciais e provedores de IA."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from eve.ai.base import Message, ProviderError, system, user
from eve.ai.manager import ProviderManager
from eve.secrets import InvalidSecretName, SecretStore

router = APIRouter(prefix="/api")


class SecretValue(BaseModel):
    value: str = Field(min_length=1, max_length=8000)


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    role: Literal["local", "fast", "external", "heavy"] = "local"
    system_prompt: str | None = Field(default=None, max_length=20_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32_000)
    with_tools: bool = False
    stream: bool = False


def _secrets(request: Request) -> SecretStore:
    return request.app.state.secrets


def _providers(request: Request) -> ProviderManager:
    return request.app.state.providers


# ------------------------------------------------------------------ segredos


@router.get("/secrets")
async def list_secrets(request: Request) -> dict[str, Any]:
    store = _secrets(request)
    return {"secrets": store.describe(), "missing_required": store.missing_required()}


@router.put("/secrets/{name}")
async def set_secret(request: Request, name: str, body: SecretValue) -> dict[str, Any]:
    try:
        _secrets(request).set(name, body.value)
    except InvalidSecretName as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _providers(request).reset()
    return {"name": name, "configured": True}


@router.delete("/secrets/{name}")
async def delete_secret(request: Request, name: str) -> dict[str, Any]:
    try:
        removed = _secrets(request).delete(name)
    except InvalidSecretName as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _providers(request).reset()
    return {"name": name, "removed": removed}


# ----------------------------------------------------------------- provedores


@router.get("/providers")
async def providers(request: Request) -> dict[str, Any]:
    manager = _providers(request)
    health = await manager.health()
    return {
        "providers": [h.as_dict() for h in health],
        "models": {
            "local": manager.model_for("local"),
            "fast": manager.model_for("fast"),
            "external": manager.model_for("external"),
            "heavy": manager.model_for("heavy"),
        },
    }


@router.post("/providers/reset")
async def reset_providers(request: Request) -> dict[str, Any]:
    """Relê configuração e credenciais sem reiniciar o Core."""
    from eve.config import load_settings

    settings = load_settings()
    manager = _providers(request)
    await manager.aclose()
    manager.settings = settings
    manager.reset()
    request.app.state.settings = settings
    return {"reset": True, "external_model": settings.ai.external_model}


@router.get("/providers/{name}/models")
async def provider_models(request: Request, name: str) -> dict[str, Any]:
    manager = _providers(request)
    provider = manager.local if name == "ollama" else manager.external
    if provider is None or provider.name != name:
        raise HTTPException(status_code=404, detail=f"provedor indisponível: {name}")
    try:
        models = await provider.models()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {"provider": name, "models": models, "count": len(models)}


# ---------------------------------------------------------------------- chat


@router.post("/ai/ask")
async def ask(request: Request, body: AskRequest) -> Any:
    manager = _providers(request)
    try:
        provider = manager.provider_for(body.role)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    messages: list[Message] = []
    if body.system_prompt:
        messages.append(system(body.system_prompt))
    messages.append(user(body.prompt))

    tools = request.app.state.tools.registry.wire_tools() if body.with_tools else ()
    default_temperature = request.app.state.settings.ai.temperature
    temperature = body.temperature if body.temperature is not None else default_temperature
    kwargs: dict[str, Any] = {
        "model": manager.model_for(body.role),
        "tools": tools,
        "temperature": temperature,
        "max_tokens": body.max_tokens,
    }

    if not body.stream:
        try:
            result = await provider.chat(messages, **kwargs)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        return result.as_dict()

    async def events() -> AsyncIterator[str]:
        try:
            async for delta in provider.stream(messages, **kwargs):
                payload = {
                    "text": delta.text,
                    "done": delta.done,
                    "finish_reason": delta.finish_reason,
                    "tool_calls": [c.as_dict() for c in delta.tool_calls],
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except ProviderError as exc:
            error = {"error": str(exc), "kind": exc.kind, "done": True}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
