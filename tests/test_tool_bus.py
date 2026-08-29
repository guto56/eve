from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import Field

from eve.bus import EventBus, Subscription
from eve.events import EventType
from eve.tools.bus import ToolBus
from eve.tools.registry import ToolRegistry, tool
from eve.tools.spec import NoParams, RiskLevel, ToolParams

calls: list[dict] = []


class EchoParams(ToolParams):
    texto: str = Field(min_length=1)
    vezes: int = Field(default=1, ge=1, le=5)


@pytest.fixture(autouse=True)
def _reset_calls():
    calls.clear()
    yield
    calls.clear()


def add_echo(registry: ToolRegistry, name: str = "t.echo", risk: RiskLevel = RiskLevel.SAFE):
    @tool(name, description="ecoa", params=EchoParams, risk=risk, registry=registry)
    async def handler(params: EchoParams, ctx) -> dict:
        calls.append({"texto": params.texto, "source": ctx.source, "request_id": ctx.request_id})
        return {"resultado": params.texto * params.vezes}

    return handler


def drain(sub: Subscription) -> list:
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


async def resolve_when_pending(bus: ToolBus, approved: bool, by: str = "teste") -> None:
    for _ in range(200):
        pending = bus.approvals.pending()
        if pending:
            bus.approvals.resolve(pending[0]["id"], approved, by)
            return
        await asyncio.sleep(0.005)
    raise AssertionError("nenhuma confirmação apareceu")


# ---------------------------------------------------------------- caminho feliz


async def test_safe_tool_runs_and_returns(tool_bus: ToolBus, event_bus: EventBus) -> None:
    add_echo(tool_bus.registry)
    with event_bus.subscribe(["tool.*"]) as sub:
        result = await tool_bus.call("t.echo", {"texto": "oi", "vezes": 2}, source="cli")
        events = drain(sub)

    assert result.ok is True
    assert result.value == {"resultado": "oioi"}
    assert result.error is None
    assert result.duration_ms >= 0
    assert calls == [{"texto": "oi", "source": "cli", "request_id": result.request_id}]
    assert [e.type for e in events] == [EventType.TOOL_REQUESTED, EventType.TOOL_COMPLETED]
    assert all(e.payload["request_id"] == result.request_id for e in events)


async def test_success_is_audited(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry)
    await tool_bus.call("t.echo", {"texto": "oi"}, source="cli", caller="guto")
    entry = tool_bus.audit.tail()[-1]
    assert entry["tool"] == "t.echo"
    assert entry["outcome"] == "ok"
    assert entry["risk"] == "safe"
    assert entry["source"] == "cli"
    assert entry["caller"] == "guto"
    assert entry["args"] == {"texto": "oi"}


# ------------------------------------------------------------------- validação


async def test_unknown_tool_never_reaches_a_handler(tool_bus: ToolBus) -> None:
    result = await tool_bus.call("nao.existe")
    assert result.ok is False
    assert result.error_kind == "unknown_tool"
    assert calls == []


async def test_invalid_args_are_rejected_before_the_handler(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry)
    result = await tool_bus.call("t.echo", {"texto": ""})
    assert result.ok is False
    assert result.error_kind == "invalid_args"
    assert "texto" in result.error
    assert calls == []


async def test_missing_required_arg(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry)
    result = await tool_bus.call("t.echo", {})
    assert result.error_kind == "invalid_args"
    assert calls == []


async def test_unexpected_arg_is_rejected(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry)
    result = await tool_bus.call("t.echo", {"texto": "oi", "surpresa": 1})
    assert result.error_kind == "invalid_args"
    assert "surpresa" in result.error
    assert calls == []


async def test_out_of_range_arg_is_rejected(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry)
    result = await tool_bus.call("t.echo", {"texto": "oi", "vezes": 99})
    assert result.error_kind == "invalid_args"
    assert calls == []


# ------------------------------------------------------------------ permissões


async def test_blocked_tool_never_runs(tool_bus: ToolBus, event_bus: EventBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.BLOCKED)
    with event_bus.subscribe(["tool.*"]) as sub:
        result = await tool_bus.call("t.echo", {"texto": "oi"})
        types = [e.type for e in drain(sub)]

    assert result.ok is False
    assert result.error_kind == "denied"
    assert calls == []
    assert EventType.TOOL_DENIED in types
    assert tool_bus.audit.tail()[-1]["outcome"] == "denied"


async def test_override_can_block_a_safe_tool(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.SAFE)
    tool_bus.permissions.overrides["t.*"] = RiskLevel.BLOCKED
    result = await tool_bus.call("t.echo", {"texto": "oi"})
    assert result.error_kind == "denied"
    assert calls == []


async def test_privileged_without_grant_is_denied(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.PRIVILEGED)
    result = await tool_bus.call("t.echo", {"texto": "oi"})
    assert result.error_kind == "denied"
    assert "concessão explícita" in result.error
    assert calls == []


async def test_privileged_with_grant_still_asks(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.PRIVILEGED)
    tool_bus.permissions.grant("t.echo")
    task = asyncio.create_task(tool_bus.call("t.echo", {"texto": "oi"}))
    await resolve_when_pending(tool_bus, approved=True)
    result = await task
    assert result.ok is True
    assert len(calls) == 1


# ---------------------------------------------------------------- confirmação


async def test_confirm_flow_when_approved(tool_bus: ToolBus, event_bus: EventBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.CONFIRM)
    with event_bus.subscribe(["tool.*"]) as sub:
        task = asyncio.create_task(tool_bus.call("t.echo", {"texto": "oi"}))
        await resolve_when_pending(tool_bus, approved=True, by="guto")
        result = await task
        types = [e.type for e in drain(sub)]

    assert result.ok is True
    assert types == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_CONFIRMATION_REQUIRED,
        EventType.TOOL_APPROVED,
        EventType.TOOL_COMPLETED,
    ]


async def test_confirm_flow_when_denied(tool_bus: ToolBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.CONFIRM)
    task = asyncio.create_task(tool_bus.call("t.echo", {"texto": "oi"}))
    await resolve_when_pending(tool_bus, approved=False, by="guto")
    result = await task
    assert result.ok is False
    assert result.error_kind == "denied"
    assert result.error == "guto"
    assert calls == []


async def test_no_answer_means_no(tool_bus: ToolBus) -> None:
    """O padrão seguro é não executar."""
    add_echo(tool_bus.registry, risk=RiskLevel.CONFIRM)
    result = await tool_bus.call("t.echo", {"texto": "oi"})
    assert result.ok is False
    assert "esgotado" in result.error
    assert calls == []


async def test_auto_approve_skips_the_prompt(tool_bus: ToolBus, event_bus: EventBus) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.CONFIRM)
    with event_bus.subscribe(["tool.*"]) as sub:
        result = await tool_bus.call("t.echo", {"texto": "oi"}, auto_approve=True)
        types = [e.type for e in drain(sub)]
    assert result.ok is True
    assert EventType.TOOL_CONFIRMATION_REQUIRED not in types
    assert EventType.TOOL_APPROVED in types


async def test_confirmation_event_describes_the_action(
    tool_bus: ToolBus, event_bus: EventBus
) -> None:
    add_echo(tool_bus.registry, risk=RiskLevel.CONFIRM)
    with event_bus.subscribe(["tool.confirmation_required"]) as sub:
        task = asyncio.create_task(tool_bus.call("t.echo", {"texto": "oi"}))
        await resolve_when_pending(tool_bus, approved=False)
        await task
        event = drain(sub)[0]
    assert event.payload["tool"] == "t.echo"
    assert event.payload["args"] == {"texto": "oi"}
    assert event.payload["risk"] == "confirm"
    assert event.payload["description"] == "ecoa"
    assert event.payload["reversible"] is True


# ------------------------------------------------------------------ execução


async def test_handler_error_does_not_escape(tool_bus: ToolBus) -> None:
    @tool(
        "t.explode",
        description="d",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=tool_bus.registry,
    )
    async def handler(params, ctx):
        raise RuntimeError("estourou")

    result = await tool_bus.call("t.explode")
    assert result.ok is False
    assert result.error_kind == "handler_error"
    assert "estourou" in result.error
    assert tool_bus.audit.tail()[-1]["outcome"] == "failed"


async def test_slow_handler_times_out(tool_bus: ToolBus, registry: ToolRegistry) -> None:
    from eve.tools.spec import ToolSpec

    async def slow(params, ctx):  # pragma: no cover - cancelado
        await asyncio.sleep(5)

    registry.register(
        ToolSpec(
            name="t.lento",
            description="d",
            params=NoParams,
            risk=RiskLevel.SAFE,
            handler=slow,
            timeout=0.05,
        )
    )
    result = await tool_bus.call("t.lento")
    assert result.ok is False
    assert result.error_kind == "timeout"
    assert "0.05s" in result.error


async def test_cancellation_is_not_swallowed(tool_bus: ToolBus, registry: ToolRegistry) -> None:
    from eve.tools.spec import ToolSpec

    async def slow(params, ctx):  # pragma: no cover - cancelado
        await asyncio.sleep(5)

    registry.register(
        ToolSpec(name="t.slow", description="d", params=NoParams, risk=RiskLevel.SAFE, handler=slow)
    )
    task = asyncio.create_task(tool_bus.call("t.slow"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ------------------------------------------------------------------- segredos


async def test_secrets_never_reach_events_or_audit(
    tool_bus: ToolBus, registry: ToolRegistry, event_bus: EventBus
) -> None:
    class SecretParams(ToolParams):
        url: str
        token: str

    @tool(
        "api.call",
        description="d",
        params=SecretParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        secret_fields=frozenset({"token"}),
    )
    async def handler(params: SecretParams, ctx) -> dict:
        calls.append({"token_visto_pelo_handler": params.token})
        return {"ok": True}

    with event_bus.subscribe(["tool.*"]) as sub:
        await tool_bus.call("api.call", {"url": "https://x", "token": "sk-super-secreto"})
        events = drain(sub)

    # O handler recebe o valor real...
    assert calls[0]["token_visto_pelo_handler"] == "sk-super-secreto"
    # ...mas ele não aparece em lugar nenhum observável.
    assert events[0].payload["args"]["token"] == "***"
    assert tool_bus.audit.tail()[-1]["args"]["token"] == "***"
    assert "sk-super-secreto" not in json.dumps(
        [e.payload for e in events] + tool_bus.audit.tail(), ensure_ascii=False
    )


async def test_secrets_are_redacted_even_when_args_are_invalid(
    tool_bus: ToolBus, registry: ToolRegistry
) -> None:
    class SecretParams(ToolParams):
        url: str
        token: str

    @tool(
        "api.call",
        description="d",
        params=SecretParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        secret_fields=frozenset({"token"}),
    )
    async def handler(params, ctx):  # pragma: no cover - nunca chamado
        return None

    await tool_bus.call("api.call", {"token": "sk-secreto"})  # falta url
    assert tool_bus.audit.tail()[-1]["args"]["token"] == "***"


# --------------------------------------------------- categorias de erro


@pytest.mark.parametrize(
    ("exception", "kind"),
    [
        (PermissionError("sem acesso"), "not_permitted"),
        (FileNotFoundError("sumiu"), "not_found"),
        (FileExistsError("já existe"), "already_exists"),
        (NotADirectoryError("não é pasta"), "wrong_kind"),
        (IsADirectoryError("é pasta"), "wrong_kind"),
        (RuntimeError("qualquer outra"), "handler_error"),
    ],
)
async def test_predictable_failures_get_their_own_kind(
    tool_bus: ToolBus, registry: ToolRegistry, exception: Exception, kind: str
) -> None:
    @tool("t.falha", description="d", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params, ctx):
        raise exception

    result = await tool_bus.call("t.falha")
    assert result.error_kind == kind


async def test_known_failures_do_not_leak_the_exception_class(
    tool_bus: ToolBus, registry: ToolRegistry
) -> None:
    @tool("t.negado", description="d", params=NoParams, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params, ctx):
        raise PermissionError("fora dos diretórios permitidos")

    result = await tool_bus.call("t.negado")
    assert result.error == "fora dos diretórios permitidos"
