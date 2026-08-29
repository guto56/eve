from __future__ import annotations

import asyncio

from eve.tools.approvals import ApprovalBroker


def kwargs(request_id: str = "r1", tool: str = "file.delete") -> dict:
    return {
        "request_id": request_id,
        "tool": tool,
        "args": {"path": "/tmp/x"},
        "risk": "confirm",
        "reason": "exige confirmação",
        "source": "test",
    }


async def test_approval_resolves() -> None:
    broker = ApprovalBroker(default_timeout=2)
    task = asyncio.create_task(broker.request(**kwargs()))
    await asyncio.sleep(0)
    assert len(broker) == 1
    assert broker.resolve("r1", True, "guto") is True
    assert await task == (True, "guto")
    assert len(broker) == 0


async def test_denial_resolves() -> None:
    broker = ApprovalBroker(default_timeout=2)
    task = asyncio.create_task(broker.request(**kwargs()))
    await asyncio.sleep(0)
    broker.resolve("r1", False, "guto")
    approved, by = await task
    assert approved is False
    assert by == "guto"


async def test_timeout_denies() -> None:
    broker = ApprovalBroker(default_timeout=0.05)
    approved, reason = await broker.request(**kwargs())
    assert approved is False
    assert "esgotado" in reason
    assert len(broker) == 0


async def test_pending_describes_the_request() -> None:
    broker = ApprovalBroker(default_timeout=2)
    task = asyncio.create_task(broker.request(**kwargs()))
    await asyncio.sleep(0)
    pending = broker.pending()
    assert len(pending) == 1
    assert pending[0]["tool"] == "file.delete"
    assert pending[0]["args"] == {"path": "/tmp/x"}
    assert pending[0]["waiting_seconds"] >= 0
    broker.resolve("r1", False)
    await task


async def test_resolving_unknown_or_twice_returns_false() -> None:
    broker = ApprovalBroker(default_timeout=2)
    assert broker.resolve("inexistente", True) is False
    task = asyncio.create_task(broker.request(**kwargs()))
    await asyncio.sleep(0)
    assert broker.resolve("r1", True) is True
    assert broker.resolve("r1", True) is False
    await task


async def test_deny_all_releases_every_pending() -> None:
    broker = ApprovalBroker(default_timeout=5)
    tasks = [
        asyncio.create_task(broker.request(**kwargs(request_id=f"r{i}", tool=f"t.{i}")))
        for i in range(3)
    ]
    await asyncio.sleep(0)
    assert broker.deny_all("encerrando") == 3
    for task in tasks:
        approved, reason = await task
        assert approved is False
        assert reason == "encerrando"
