from __future__ import annotations

import json

import pytest

from eve.ai.base import (
    Message,
    ToolCall,
    Usage,
    assistant,
    classify_status,
    from_wire_name,
    parse_tool_arguments,
    system,
    to_wire_name,
    tool_result,
    user,
)


@pytest.mark.parametrize(
    ("nosso", "no_fio"),
    [("app.open", "app__open"), ("system.set_volume", "system__set_volume"), ("echo", "echo")],
)
def test_wire_name_roundtrip(nosso: str, no_fio: str) -> None:
    """Ponto não é aceito em nome de função pelos provedores; a volta é exata."""
    assert to_wire_name(nosso) == no_fio
    assert from_wire_name(no_fio) == nosso


def test_simple_messages() -> None:
    assert system("s").to_wire() == {"role": "system", "content": "s"}
    assert user("u").to_wire() == {"role": "user", "content": "u"}


def test_assistant_with_tool_calls_serializes_arguments_as_json() -> None:
    call = ToolCall("app.open", {"name": "Safari"}, id="abc")
    wire = assistant("", [call]).to_wire()
    função = wire["tool_calls"][0]["function"]
    assert função["name"] == "app__open"
    assert json.loads(função["arguments"]) == {"name": "Safari"}
    assert wire["tool_calls"][0]["id"] == "abc"


def test_tool_result_message() -> None:
    call = ToolCall("app.open", {}, id="abc")
    wire = tool_result(call, "aberto").to_wire()
    assert wire == {
        "role": "tool",
        "content": "aberto",
        "tool_call_id": "abc",
        "name": "app__open",
    }


def test_tool_call_ids_are_unique() -> None:
    assert ToolCall("a", {}).id != ToolCall("a", {}).id


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ("", {}),
        ("   ", {}),
        (None, {}),
        ("não é json", {"_raw": "não é json"}),
        ("[1,2]", {"_raw": "[1,2]"}),
    ],
)
def test_parse_tool_arguments(bruto: object, esperado: dict) -> None:
    assert parse_tool_arguments(bruto) == esperado


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (404, "unknown_model"),
        (429, "rate_limit"),
        (500, "unavailable"),
        (503, "unavailable"),
        (400, "bad_request"),
        (422, "bad_request"),
    ],
)
def test_classify_status(status: int, kind: str) -> None:
    assert classify_status(status) == kind


def test_usage_total() -> None:
    assert Usage(10, 5).total == 15


def test_messages_are_frozen() -> None:
    with pytest.raises(Exception):  # noqa: B017
        Message("user", "x").content = "y"
