from __future__ import annotations

import asyncio

from eve.bus import EventBus
from eve.events import EventType


async def test_publish_reaches_subscriber() -> None:
    bus = EventBus()
    with bus.subscribe() as sub:
        await bus.emit(EventType.SYSTEM_STARTED, version="0.1.0")
        event = await asyncio.wait_for(sub.queue.get(), 1)
    assert event.type == EventType.SYSTEM_STARTED
    assert event.payload == {"version": "0.1.0"}


async def test_pattern_filters_events() -> None:
    bus = EventBus()
    with bus.subscribe(["tool.*"]) as sub:
        await bus.emit(EventType.SYSTEM_STARTED)
        await bus.emit(EventType.TOOL_REQUESTED, name="open_app")
        event = await asyncio.wait_for(sub.queue.get(), 1)
    assert event.type == EventType.TOOL_REQUESTED
    assert sub.queue.empty()


async def test_slow_subscriber_drops_oldest_and_never_blocks() -> None:
    bus = EventBus()
    with bus.subscribe(maxsize=3) as sub:
        for i in range(10):
            await bus.emit("test.tick", i=i)
        assert sub.queue.qsize() == 3
        assert sub.dropped == 7
        newest = [(await sub.queue.get()).payload["i"] for _ in range(3)]
    assert newest == [7, 8, 9]


async def test_unsubscribe_on_context_exit() -> None:
    bus = EventBus()
    with bus.subscribe() as sub:
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0
    await bus.emit("test.after")
    assert sub.queue.empty()


async def test_history_replays_filtered_and_limited() -> None:
    bus = EventBus(history_size=5)
    for i in range(4):
        await bus.emit("tool.completed", i=i)
    await bus.emit(EventType.SYSTEM_STARTED)
    assert len(bus.history()) == 5
    tools = bus.history(["tool.*"])
    assert [e.payload["i"] for e in tools] == [0, 1, 2, 3]
    assert len(bus.history(limit=2)) == 2
    assert bus.history(limit=0) == []


async def test_history_is_bounded() -> None:
    bus = EventBus(history_size=3)
    for i in range(10):
        await bus.emit("test.tick", i=i)
    assert [e.payload["i"] for e in bus.history()] == [7, 8, 9]
    assert bus.published == 10
