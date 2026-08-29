"""Provedor local, testado contra o Ollama de verdade nesta máquina."""

from __future__ import annotations

import pytest

from eve.ai.base import ProviderError, system, user
from eve.ai.ollama import OllamaProvider

pytestmark = pytest.mark.integration

MODEL = "qwen3.5:0.8b"  # o menor, para o teste ser rápido


@pytest.fixture
async def provider():
    p = OllamaProvider(default_model=MODEL)
    yield p
    await p.aclose()


async def test_health(provider: OllamaProvider) -> None:
    health = await provider.health()
    assert health.ok is True
    assert health.name == "ollama"
    assert MODEL in health.models
    assert health.latency_ms is not None


async def test_health_reports_missing_model() -> None:
    p = OllamaProvider(default_model="modelo-que-nao-existe:1b")
    try:
        health = await p.health()
    finally:
        await p.aclose()
    assert health.ok is False
    assert "ainda não foi baixado" in health.detail


async def test_health_when_ollama_is_down() -> None:
    p = OllamaProvider(host="http://127.0.0.1:9", connect_timeout=0.3)
    try:
        health = await p.health()
    finally:
        await p.aclose()
    assert health.ok is False
    assert "não respondeu" in health.detail


async def test_models(provider: OllamaProvider) -> None:
    models = await provider.models()
    assert MODEL in models
    assert models == sorted(models)


async def test_chat_returns_text_and_usage(provider: OllamaProvider) -> None:
    """Verifica o provedor, não a obediência do modelo: estrutura e contabilidade."""
    result = await provider.chat(
        [system("Responda com uma palavra só."), user("Diga: pronto")],
        temperature=0,
        max_tokens=10,
    )
    assert result.text.strip()
    assert result.provider == "ollama"
    assert result.model.startswith("qwen3.5")
    assert result.usage is not None
    assert result.usage.completion_tokens > 0
    assert result.duration_ms > 0


async def test_stream_yields_pieces_then_finishes(provider: OllamaProvider) -> None:
    pedaços = []
    final = None
    async for delta in provider.stream(
        [user("Conte de 1 a 5, só os números.")], temperature=0, max_tokens=40
    ):
        pedaços.append(delta.text)
        if delta.done:
            final = delta
    assert "".join(pedaços).strip()
    assert final is not None
    assert final.usage is not None


async def test_tool_calling_returns_our_dotted_name(provider: OllamaProvider) -> None:
    """O modelo vê ``app__open`` e a EVE recebe de volta ``app.open``."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "app__open",
                "description": "Abre um aplicativo pelo nome",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    result = await provider.chat([user("abra o Safari")], tools=tools, temperature=0)
    assert result.tool_calls
    call = result.tool_calls[0]
    assert call.name == "app.open"
    assert call.arguments["name"].lower() == "safari"


async def test_unknown_model_is_reported(provider: OllamaProvider) -> None:
    with pytest.raises(ProviderError) as exc:
        await provider.chat([user("oi")], model="nao-existe:99b")
    assert exc.value.kind in {"unknown_model", "bad_request", "unavailable"}
