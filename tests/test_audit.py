from __future__ import annotations

from pathlib import Path

from eve.tools.audit import AuditLog


def test_record_and_tail(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(tool="a.b", outcome="ok")
    log.record(tool="c.d", outcome="denied")
    entries = log.tail()
    assert [e["tool"] for e in entries] == ["a.b", "c.d"]
    assert entries[0]["ts"] > 0


def test_tail_limits_and_handles_absence(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.tail() == []
    for i in range(5):
        log.record(tool=f"t.{i}")
    assert [e["tool"] for e in log.tail(2)] == ["t.3", "t.4"]
    assert log.tail(0) == []


def test_creates_parent_directory(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "fundo" / "audit.jsonl")
    log.record(tool="a.b")
    assert log.path.exists()


def test_rotates_when_too_big(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=200)
    for i in range(20):
        log.record(tool=f"t.{i}", texto="x" * 20)
    rotated = tmp_path / "audit.1.jsonl"
    assert rotated.exists()
    assert log.path.stat().st_size < 1000
    assert len(log.tail(100)) < 20


def test_non_serializable_values_do_not_break_the_log(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(tool="a.b", args={"caminho": Path("/tmp/x")})
    assert log.tail()[0]["args"]["caminho"] == "/tmp/x"
