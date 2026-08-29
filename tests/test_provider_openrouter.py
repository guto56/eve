"""Provedor externo.

A maior parte é testada com um transporte simulado — o que importa aqui é o
parsing (SSE em pedaços, tool calls fatiadas, mapeamento de erro). No fim há um
teste ao vivo, pulado quando não há credencial real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2 as httpx
import pytest

from eve.ai.base import ProviderError, user
from eve.ai.openrouter import OpenRouterProvider


def make(handler) -> OpenRouterProvider:
    return OpenRouterProvider(api_key="sk-teste", transport=httpx.MockTransport(handler))


def json_response(payload: dict, status: int = 200):
    return httpx.Response(status, json=payload)


def sse(*chunks: str) -> httpx.Response:
    body = "".join(f"data: {c}\n\n" for c in chunks)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


CHAT_OK = {
    "model": "google/gemini-3.1-flash-lite",
    "choices": [{"message": {"content": "olá"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3},
}


def test_missing_key_is_refused() -> None:
    with pytest.raises(ProviderError) as exc:
        OpenRouterProvider(api_key="")
    assert exc.value.kind == "auth"


async def test_chat_parses_the_response() -> None:
    provider = make(lambda request: json_response(CHAT_OK))
    result = await provider.chat([user("oi")])
    await provider.aclose()
    assert result.text == "olá"
    assert result.provider == "openrouter"
    assert result.model == "google/gemini-3.1-flash-lite"
    assert result.usage.total == 14
    assert result.finish_reason == "stop"


async def test_authorization_header_is_sent() -> None:
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["auth"] = request.headers.get("authorization")
        return json_response(CHAT_OK)

    provider = make(handler)
    await provider.chat([user("oi")])
    await provider.aclose()
    assert visto["auth"] == "Bearer sk-teste"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (429, "rate_limit"), (500, "unavailable"), (400, "bad_request")],
)
async def test_error_status_becomes_provider_error(status: int, kind: str) -> None:
    provider = make(lambda request: json_response({"error": {"message": "deu ruim"}}, status))
    with pytest.raises(ProviderError) as exc:
        await provider.chat([user("oi")])
    await provider.aclose()
    assert exc.value.kind == kind
    assert exc.value.status == status
    assert "deu ruim" in str(exc.value)


async def test_non_json_error_body_is_handled() -> None:
    provider = make(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(ProviderError, match="502"):
        await provider.chat([user("oi")])
    await provider.aclose()


async def test_tool_calls_in_a_plain_response() -> None:
    payload = {
        "model": "m",
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "app__open",
                                "arguments": '{"name": "Safari"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    provider = make(lambda request: json_response(payload))
    result = await provider.chat([user("abra o safari")])
    await provider.aclose()
    assert result.text == ""
    assert result.tool_calls[0].name == "app.open"
    assert result.tool_calls[0].arguments == {"name": "Safari"}
    assert result.tool_calls[0].id == "call_1"


async def test_stream_assembles_text() -> None:
    provider = make(
        lambda request: sse(
            json.dumps({"choices": [{"delta": {"content": "Olá"}}]}),
            json.dumps({"choices": [{"delta": {"content": ", "}}]}),
            json.dumps({"choices": [{"delta": {"content": "mundo"}}]}),
            json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            "[DONE]",
        )
    )
    texto = ""
    fim = None
    async for delta in provider.stream([user("oi")]):
        texto += delta.text
        if delta.done:
            fim = delta
    await provider.aclose()
    assert texto == "Olá, mundo"
    assert fim is not None
    assert fim.finish_reason == "stop"


async def test_stream_reassembles_tool_call_arguments_split_across_chunks() -> None:
    """Os argumentos chegam fatiados; a EVE só entrega a chamada completa."""
    provider = make(
        lambda request: sse(
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_9",
                                        "function": {"name": "file__write", "arguments": '{"pa'},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'th": "/tmp/a.txt"}'}}
                                ]
                            }
                        }
                    ]
                }
            ),
            json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "[DONE]",
        )
    )
    chamadas = []
    async for delta in provider.stream([user("escreva")]):
        chamadas.extend(delta.tool_calls)
    await provider.aclose()
    assert len(chamadas) == 1
    assert chamadas[0].name == "file.write"
    assert chamadas[0].arguments == {"path": "/tmp/a.txt"}
    assert chamadas[0].id == "call_9"


async def test_stream_error_becomes_provider_error() -> None:
    provider = make(lambda request: httpx.Response(429, json={"error": {"message": "devagar"}}))
    with pytest.raises(ProviderError) as exc:
        async for _ in provider.stream([user("oi")]):
            pass
    await provider.aclose()
    assert exc.value.kind == "rate_limit"


async def test_health_reports_balance() -> None:
    provider = make(lambda request: json_response({"data": {"usage": 2.5, "limit": 10.0}}))
    health = await provider.health()
    await provider.aclose()
    assert health.ok is True
    assert "7.50" in health.detail


async def test_health_with_bad_credential() -> None:
    provider = make(lambda request: httpx.Response(401, json={}))
    health = await provider.health()
    await provider.aclose()
    assert health.ok is False
    assert "recusada" in health.detail


async def test_models_are_sorted() -> None:
    provider = make(lambda request: json_response({"data": [{"id": "z/m"}, {"id": "a/m"}]}))
    assert await provider.models() == ["a/m", "z/m"]
    await provider.aclose()


# ------------------------------------------------------------------- ao vivo


def real_key() -> str | None:
    """Credencial real do Keychain, fora do backend de teste."""
    from eve.secrets import SecretStore

    try:
        store = SecretStore(Path.home() / ".eve" / "secrets.json", allow_env_fallback=False)
        return store.get("OPENROUTER_API_KEY")
    except Exception:
        return None


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("EVE_SKIP_NETWORK") == "1", reason="testes de rede desativados")
async def test_live_call() -> None:
    key = real_key()
    if not key:
        pytest.skip("OPENROUTER_API_KEY não está no Keychain")
    provider = OpenRouterProvider(api_key=key)
    try:
        health = await provider.health()
        assert health.ok is True
        result = await provider.chat(
            [user("Responda apenas com a palavra: ok")], temperature=0, max_tokens=8
        )
        assert result.text.strip()
        assert result.usage.total > 0
    finally:
        await provider.aclose()
