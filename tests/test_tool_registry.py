from __future__ import annotations

import pytest
from pydantic import Field

from eve.tools.registry import (
    DuplicateToolError,
    ToolRegistry,
    UnknownToolError,
    default_registry,
    reset_default_registry,
    tool,
)
from eve.tools.spec import NoParams, RiskLevel, ToolParams


class Params(ToolParams):
    texto: str = Field(description="Um texto")
    vezes: int = 1


def test_register_and_get(registry: ToolRegistry) -> None:
    @tool("t.um", description="d", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params, ctx):
        return 1

    assert registry.has("t.um")
    assert registry.get("t.um").description == "d"
    assert handler.tool_spec.name == "t.um"


def test_duplicate_is_rejected_unless_replacing(registry: ToolRegistry) -> None:
    @tool("t.um", description="a", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
    async def first(params, ctx):
        return 1

    with pytest.raises(DuplicateToolError):

        @tool("t.um", description="b", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
        async def second(params, ctx):
            return 2

    registry.register(registry.get("t.um"), replace=True)


def test_unknown_tool_message(registry: ToolRegistry) -> None:
    with pytest.raises(UnknownToolError) as exc:
        registry.get("nao.existe")
    assert "nao.existe" in str(exc.value)


def test_empty_registry_does_not_fall_back_to_the_global_one(registry: ToolRegistry) -> None:
    """Registro vazio é falsy; o decorador não pode confundir isso com ausência."""
    reset_default_registry()
    assert len(registry) == 0

    @tool("t.local", description="d", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params, ctx):
        return 1

    assert registry.has("t.local")
    assert not default_registry().has("t.local")


def test_names_namespaces_and_iteration(registry: ToolRegistry) -> None:
    for name in ("b.dois", "a.um", "a.dois"):

        @tool(name, description="d", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
        async def handler(params, ctx):
            return 1

    assert registry.names() == ["a.dois", "a.um", "b.dois"]
    assert registry.namespaces() == ["a", "b"]
    assert len(registry) == 3
    assert [s.name for s in registry] == ["a.dois", "a.um", "b.dois"]


def test_unregister(registry: ToolRegistry) -> None:
    @tool("t.um", description="d", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params, ctx):
        return 1

    registry.unregister("t.um")
    assert not registry.has("t.um")
    registry.unregister("t.um")  # idempotente


def test_json_schema_is_strict_for_llms(registry: ToolRegistry) -> None:
    @tool("t.p", description="d", params=Params, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params, ctx):
        return 1

    schema = registry.get("t.p").json_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["texto"]
    assert schema["properties"]["texto"]["description"] == "Um texto"
    assert "title" not in schema


def test_describe_carries_risk_and_requirements(registry: ToolRegistry) -> None:
    @tool(
        "mac.notify",
        description="Mostra uma notificação",
        params=NoParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        requires=("notifications",),
    )
    async def handler(params, ctx):
        return 1

    described = registry.get("mac.notify").describe()
    assert described["risk"] == "confirm"
    assert described["requires"] == ["notifications"]
    assert described["reversible"] is True


def test_redaction_hides_secret_fields(registry: ToolRegistry) -> None:
    @tool(
        "api.call",
        description="d",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        secret_fields=frozenset({"token"}),
    )
    async def handler(params, ctx):
        return 1

    spec = registry.get("api.call")
    assert spec.redact({"url": "x", "token": "segredo"}) == {"url": "x", "token": "***"}
