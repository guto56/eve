"""Provedor local via Ollama.

No Apple Silicon o Ollama usa MLX por baixo, o que é o caminho mais rápido
nesta máquina. O modelo local existe para responder em milissegundos: conversa
curta, detecção de intenção e decisão sobre encaminhar ou não (spec §6).
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


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        default_model: str = "qwen3.5:2b",
        timeout: float = 120.0,
        connect_timeout: float = 3.0,
        transport: Any | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=self.host,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            transport=transport,
        )

    async def unload(self, model: str | None = None) -> bool:
        """Tira o modelo da memória.

        Numa máquina de 8 GB, o modelo local ocupa quase um terço da RAM.
        Deixá-lo carregado depois que a EVE parou é cobrar do usuário por um
        serviço que ele desligou. O Ollama recarrega sozinho na próxima vez.
        """
        try:
            await self._client.post(
                "/api/chat",
                json={"model": model or self.default_model, "messages": [], "keep_alive": 0},
                timeout=10.0,
            )
        except httpx.HTTPError:
            return False
        return True

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- estado

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = await self._client.get("/api/version", timeout=3.0)
            response.raise_for_status()
            version = response.json().get("version", "?")
            models = await self.models()
        except httpx.HTTPError as exc:
            return ProviderHealth(self.name, False, f"não respondeu: {_short(exc)}")
        latency = (time.perf_counter() - started) * 1000
        detail = f"v{version}, {len(models)} modelo(s)"
        if self.default_model not in models:
            detail += f" — {self.default_model} ainda não foi baixado"
            return ProviderHealth(self.name, False, detail, tuple(models), latency)
        return ProviderHealth(self.name, True, detail, tuple(models), latency)

    async def models(self) -> list[str]:
        try:
            response = await self._client.get("/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"não foi possível listar modelos: {_short(exc)}") from exc
        return sorted(m["name"] for m in response.json().get("models", []))

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
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            # O Ollama quer os argumentos como objeto, não como string JSON.
            "messages": [m.to_wire(arguments_as_json=False) for m in messages],
            "stream": stream,
            # Sem "pensamento" visível: o modelo local existe para responder
            # rápido, não para deliberar.
            "think": False,
            "options": options,
        }
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
        data = await self._post("/api/chat", payload)
        message = data.get("message", {})
        return ChatResult(
            text=message.get("content", ""),
            provider=self.name,
            model=data.get("model", payload["model"]),
            tool_calls=_tool_calls(message.get("tool_calls")),
            usage=Usage(data.get("prompt_eval_count", 0) or 0, data.get("eval_count", 0) or 0),
            finish_reason=data.get("done_reason"),
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
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderError(
                        _error_message(body, response.status_code),
                        classify_status(response.status_code),
                        response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:  # pragma: no cover - linha parcial
                        continue
                    message = chunk.get("message", {})
                    calls = _tool_calls(message.get("tool_calls"))
                    done = bool(chunk.get("done"))
                    yield Delta(
                        text=message.get("content", ""),
                        tool_calls=calls,
                        done=done,
                        finish_reason=chunk.get("done_reason") if done else None,
                        usage=(
                            Usage(
                                chunk.get("prompt_eval_count", 0) or 0,
                                chunk.get("eval_count", 0) or 0,
                            )
                            if done
                            else None
                        ),
                    )
                    if done:
                        return
        except httpx.HTTPError as exc:
            raise ProviderError(f"falha no streaming: {_short(exc)}", "unavailable") from exc

    # ------------------------------------------------------------ interno

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama não respondeu: {_short(exc)}", "unavailable") from exc
        if response.status_code >= 400:
            raise ProviderError(
                _error_message(response.text, response.status_code),
                classify_status(response.status_code),
                response.status_code,
            )
        return response.json()


def _tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    if not raw:
        return ()
    calls = []
    for item in raw:
        function = item.get("function", {})
        name = function.get("name")
        if not name:
            continue
        call = ToolCall(
            name=from_wire_name(name),
            arguments=parse_tool_arguments(function.get("arguments")),
        )
        if item.get("id"):
            call = ToolCall(call.name, call.arguments, item["id"])
        calls.append(call)
    return tuple(calls)


def _error_message(body: str, status: int) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return f"Ollama respondeu {status}: {body[:200]}"
    return f"Ollama respondeu {status}: {parsed.get('error', body[:200])}"


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text[:160]
