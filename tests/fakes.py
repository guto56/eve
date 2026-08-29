"""Provedor de mentira, para testar router e motor de conversa sem modelo."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from eve.ai.base import (
    ChatResult,
    Delta,
    Message,
    Provider,
    ProviderError,
    ProviderHealth,
    ToolCall,
    Usage,
)


class FakeProvider(Provider):
    """Devolve respostas roteirizadas, na ordem, e registra o que recebeu."""

    name = "fake"

    def __init__(self, *responses: ChatResult | Exception, default_model: str = "fake") -> None:
        self.default_model = default_model
        self.queue: list[ChatResult | Exception] = list(responses)
        self.calls: list[dict[str, Any]] = []

    def _next(self, messages: Sequence[Message], tools: Sequence[dict], model: str | None):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": [t["function"]["name"] for t in tools],
                "model": model,
            }
        )
        if not self.queue:
            return ChatResult(text="", provider=self.name, model=model or self.default_model)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "de mentira")

    async def models(self) -> list[str]:
        return [self.default_model]

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        return self._next(messages, tools, model)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        result = self._next(messages, tools, model)
        # Palavra por palavra, para exercitar o caminho de streaming.
        palavras = result.text.split(" ")
        for i, palavra in enumerate(palavras):
            if palavra:
                yield Delta(text=palavra + (" " if i < len(palavras) - 1 else ""))
        yield Delta(done=True, tool_calls=result.tool_calls, finish_reason="stop")


def reply(text: str = "", tool_calls: Sequence[ToolCall] = ()) -> ChatResult:
    return ChatResult(
        text=text,
        provider="fake",
        model="fake",
        tool_calls=tuple(tool_calls),
        usage=Usage(10, 5),
    )


def boom(message: str = "provedor caiu", kind: str = "unavailable") -> ProviderError:
    return ProviderError(message, kind)
