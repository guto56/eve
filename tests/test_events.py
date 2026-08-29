from __future__ import annotations

import pytest

from eve.events import Event, EventType, matches


@pytest.mark.parametrize(
    ("pattern", "event_type", "expected"),
    [
        ("*", "tool.requested", True),
        ("*", "notification", True),
        ("tool.*", "tool.requested", True),
        ("tool.*", "tool.completed", True),
        ("tool.*", "toolbar.x", False),
        ("tool.*", "tool", False),
        ("tool.requested", "tool.requested", True),
        ("tool.requested", "tool.completed", False),
        ("voice.*", "tool.requested", False),
    ],
)
def test_matches(pattern: str, event_type: str, expected: bool) -> None:
    assert matches(pattern, event_type) is expected


def test_event_defaults() -> None:
    event = Event(type=EventType.SYSTEM_STARTED)
    assert len(event.id) == 32
    assert event.source == "core"
    assert event.payload == {}
    assert event.ts > 0


def test_event_is_frozen() -> None:
    event = Event(type="x")
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        event.type = "y"
