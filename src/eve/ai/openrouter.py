"""Provedor externo via OpenRouter.

Um endpoint compatível com a API da OpenAI dá acesso a Gemini, Claude, GPT e
centenas de outros modelos com uma credencial só (spec §7). A EVE recorre a
ele quando a tarefa é complexa, atual ou longa demais para o modelo local.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx2 as httpx

from eve.ai.base import (
    ChatResult,
    Delta,
    Message,
    Provider,
    ProviderError,
    ProviderHealth,
    ToolCall,
    Usage,
    classify_status,
    from_wire_name,
    parse_tool_arguments,
)
from eve.logging import get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(Provider):
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        default_model: str = "google/gemini-3.1-flash-lite",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 180.0,
        connect_timeout: float = 5.0,
        transport: Any | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY não configurada", "auth")
        self.default_model = default_model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/eve-assistant",
                "X-Title": "EVE",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- estado

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = await self._client.get("/key", timeout=10.0)
        except httpx.HTTPError as exc:
            return ProviderHealth(self.name, False, f"não respondeu: {_short(exc)}")
        latency = (time.perf_counter() - started) * 1000
        if response.status_code in (401, 403):
            return ProviderHealth(self.name, False, "credencial recusada")
        if response.status_code >= 400:
            return ProviderHealth(self.name, False, f"respondeu {response.status_code}")

        data = response.json().get("data", {})
        usage = data.get("usage")
        limit = data.get("limit")
        if limit is None:
            saldo = "sem limite definido"
        else:
            saldo = f"US$ {max(0.0, float(limit) - float(usage or 0)):.2f} restantes"
        return ProviderHealth(
            self.name,
            True,
            f"credencial válida, {saldo}; modelo padrão {self.default_model}",
            (),
            latency,
        )

    async def models(self) -> list[str]:
        try:
            response = await self._client.get("/models", timeout=20.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"não foi possível listar modelos: {_short(exc)}") from exc
        return sorted(m["id"] for m in response.json().get("data", []))

    # -------------------------------------------------------------- chat

    def _payload(
        self,
        messages: Sequence[Message],
        model: str | None,
        tools: Sequence[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_wire() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = list(tools)
        return payload

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        payload = self._payload(messages, model, tools, temperature, max_tokens, stream=False)
        started = time.perf_counter()
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter não respondeu: {_short(exc)}", "unavailable") from exc
        if response.status_code >= 400:
            raise ProviderError(
                _error_message(response.text, response.status_code),
                classify_status(response.status_code),
                response.status_code,
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage") or {}
        return ChatResult(
            text=message.get("content") or "",
            provider=self.name,
            model=data.get("model", payload["model"]),
            tool_calls=_tool_calls(message.get("tool_calls")),
            usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
            finish_reason=choice.get("finish_reason"),
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        payload = self._payload(messages, model, tools, temperature, max_tokens, stream=True)
        partial = _ToolCallAccumulator()
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderError(
                        _error_message(body, response.status_code),
                        classify_status(response.status_code),
                        response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield Delta(tool_calls=partial.finish(), done=True)
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:  # pragma: no cover
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    partial.feed(delta.get("tool_calls"))
                    finish = choice.get("finish_reason")
                    text = delta.get("content") or ""
                    if text or finish:
                        yield Delta(
                            text=text,
                            done=bool(finish),
                            finish_reason=finish,
                            tool_calls=partial.finish() if finish else (),
                        )
                        if finish:
                            return
        except httpx.HTTPError as exc:
            raise ProviderError(f"falha no streaming: {_short(exc)}", "unavailable") from exc


class _ToolCallAccumulator:
    """No streaming, os argumentos de uma tool call chegam em pedaços."""

    def __init__(self) -> None:
        self._by_index: dict[int, dict[str, str]] = {}

    def feed(self, raw: Any) -> None:
        if not raw:
            return
        for item in raw:
            slot = self._by_index.setdefault(
                item.get("index", 0), {"id": "", "name": "", "arguments": ""}
            )
            if item.get("id"):
                slot["id"] = item["id"]
            function = item.get("function") or {}
            if function.get("name"):
                slot["name"] = function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]

    def finish(self) -> tuple[ToolCall, ...]:
        calls = []
        for index in sorted(self._by_index):
            slot = self._by_index[index]
            if not slot["name"]:
                continue
            call = ToolCall(
                name=from_wire_name(slot["name"]),
                arguments=parse_tool_arguments(slot["arguments"]),
                id=slot["id"] or ToolCall(name="", arguments={}).id,
            )
            calls.append(call)
        self._by_index.clear()
        return tuple(calls)


def _tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    if not raw:
        return ()
    calls = []
    for item in raw:
        function = item.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        calls.append(
            ToolCall(
                name=from_wire_name(name),
                arguments=parse_tool_arguments(function.get("arguments")),
                id=item.get("id") or ToolCall(name="", arguments={}).id,
            )
        )
    return tuple(calls)


def _error_message(body: str, status: int) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return f"OpenRouter respondeu {status}: {body[:200]}"
    error = parsed.get("error")
    if isinstance(error, dict):
        return f"OpenRouter respondeu {status}: {error.get('message', body[:200])}"
    return f"OpenRouter respondeu {status}: {str(error)[:200]}"


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text[:160]
