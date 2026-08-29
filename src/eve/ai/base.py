"""Tipos e contrato comuns a todo provedor de IA.

O resto da EVE fala com modelos só através destas estruturas. Trocar Ollama
por MLX, ou OpenRouter por outro provedor, não deve tocar em nada acima daqui.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]

# Nomes de ferramenta da EVE usam ponto (``app.open``), que a especificação de
# function calling da OpenAI não aceita. Na ida trocamos por ``__``; na volta
# desfazemos. Nenhum nome nosso contém ``__``, então a conversão é reversível.
WIRE_SEPARATOR = "__"


def to_wire_name(name: str) -> str:
    return name.replace(".", WIRE_SEPARATOR)


def from_wire_name(name: str) -> str:
    return name.replace(WIRE_SEPARATOR, ".")


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self, *, arguments_as_json: bool = True) -> dict[str, Any]:
        """Formato de fio para o provedor.

        Os dois dialetos divergem num ponto: a OpenAI (e o OpenRouter) querem
        ``arguments`` como **string JSON**; o Ollama quer **objeto**. Mandar o
        formato errado só falha na segunda rodada de uma conversa com
        ferramenta, quando a chamada anterior volta no histórico.
        """
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": to_wire_name(call.name),
                        "arguments": (
                            json.dumps(call.arguments, ensure_ascii=False)
                            if arguments_as_json
                            else call.arguments
                        ),
                    },
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = to_wire_name(self.name)
        return payload


def system(content: str) -> Message:
    return Message("system", content)


def user(content: str) -> Message:
    return Message("user", content)


def assistant(content: str = "", tool_calls: Sequence[ToolCall] = ()) -> Message:
    return Message("assistant", content, tuple(tool_calls))


def tool_result(call: ToolCall, content: str) -> Message:
    return Message("tool", content, tool_call_id=call.id, name=call.name)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ChatResult:
    text: str
    provider: str
    model: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None
    finish_reason: str | None = None
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "tool_calls": [c.as_dict() for c in self.tool_calls],
            "usage": (
                {
                    "prompt_tokens": self.usage.prompt_tokens,
                    "completion_tokens": self.usage.completion_tokens,
                    "total": self.usage.total,
                }
                if self.usage
                else None
            ),
            "finish_reason": self.finish_reason,
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass(frozen=True)
class Delta:
    """Um pedaço da resposta durante o streaming."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    done: bool = False
    finish_reason: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class ProviderHealth:
    name: str
    ok: bool
    detail: str
    models: tuple[str, ...] = ()
    latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "models": list(self.models),
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
        }


class ProviderError(RuntimeError):
    """Falha de provedor, classificada para quem chamou poder reagir."""

    def __init__(self, message: str, kind: str = "unavailable", status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


def classify_status(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "unknown_model"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "unavailable"
    return "bad_request"


class Provider(ABC):
    """Contrato mínimo de um provedor de IA."""

    name: str
    default_model: str

    @abstractmethod
    async def health(self) -> ProviderHealth: ...

    @abstractmethod
    async def models(self) -> list[str]: ...

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult: ...

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]: ...

    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Argumentos podem vir como objeto ou como string JSON, conforme o modelo."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {}
