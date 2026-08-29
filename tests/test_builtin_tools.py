from __future__ import annotations

from eve.tools.builtin import register_builtin_tools
from eve.tools.bus import ToolBus
from eve.tools.registry import ToolRegistry


def test_builtin_tools_are_registered() -> None:
    registry = register_builtin_tools(ToolRegistry())
    assert registry.names() == ["eve.about", "eve.echo", "system.info", "system.time"]
    assert all(spec.risk.value == "safe" for spec in registry)


async def test_system_info_reports_the_machine(tool_bus: ToolBus) -> None:
    register_builtin_tools(tool_bus.registry)
    result = await tool_bus.call("system.info")
    assert result.ok is True
    assert result.value["system"] == "Darwin"
    assert result.value["memory_gb"] > 0
    assert result.value["eve"]


async def test_system_time_is_consistent(tool_bus: ToolBus) -> None:
    register_builtin_tools(tool_bus.registry)
    result = await tool_bus.call("system.time")
    assert result.ok is True
    assert result.value["epoch"] > 0
    assert "T" in result.value["local"]


async def test_echo_round_trips(tool_bus: ToolBus) -> None:
    register_builtin_tools(tool_bus.registry)
    result = await tool_bus.call("eve.echo", {"message": "olá"}, source="teste")
    assert result.value == {"message": "olá", "source": "teste"}


async def test_echo_rejects_oversized_message(tool_bus: ToolBus) -> None:
    register_builtin_tools(tool_bus.registry)
    result = await tool_bus.call("eve.echo", {"message": "x" * 5000})
    assert result.error_kind == "invalid_args"
