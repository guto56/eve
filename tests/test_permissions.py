from __future__ import annotations

import pytest

from eve.permissions import PermissionEngine
from eve.tools.spec import NoParams, RiskLevel, ToolSpec


def make_spec(name: str, risk: RiskLevel) -> ToolSpec:
    async def handler(params, ctx):  # pragma: no cover - nunca executado aqui
        return None

    return ToolSpec(name=name, description="", params=NoParams, risk=risk, handler=handler)


def test_declared_risk_is_the_default() -> None:
    engine = PermissionEngine()
    assert engine.effective_risk(make_spec("app.open", RiskLevel.SAFE)) is RiskLevel.SAFE


def test_exact_override_wins() -> None:
    engine = PermissionEngine({"app.open": RiskLevel.CONFIRM})
    assert engine.effective_risk(make_spec("app.open", RiskLevel.SAFE)) is RiskLevel.CONFIRM


def test_namespace_pattern_applies() -> None:
    engine = PermissionEngine({"file.*": RiskLevel.CONFIRM})
    assert engine.effective_risk(make_spec("file.delete", RiskLevel.SAFE)) is RiskLevel.CONFIRM
    assert engine.effective_risk(make_spec("app.open", RiskLevel.SAFE)) is RiskLevel.SAFE


def test_exact_beats_pattern() -> None:
    engine = PermissionEngine({"file.*": RiskLevel.BLOCKED, "file.read": RiskLevel.SAFE})
    assert engine.effective_risk(make_spec("file.read", RiskLevel.CONFIRM)) is RiskLevel.SAFE
    assert engine.effective_risk(make_spec("file.delete", RiskLevel.SAFE)) is RiskLevel.BLOCKED


def test_longer_pattern_beats_global() -> None:
    engine = PermissionEngine({"*": RiskLevel.BLOCKED, "file.*": RiskLevel.CONFIRM})
    assert engine.effective_risk(make_spec("file.read", RiskLevel.SAFE)) is RiskLevel.CONFIRM
    assert engine.effective_risk(make_spec("app.open", RiskLevel.SAFE)) is RiskLevel.BLOCKED


@pytest.mark.parametrize(
    ("risk", "allowed", "needs_confirmation"),
    [
        (RiskLevel.SAFE, True, False),
        (RiskLevel.CONFIRM, True, True),
        (RiskLevel.BLOCKED, False, False),
    ],
)
def test_decision_per_level(risk: RiskLevel, allowed: bool, needs_confirmation: bool) -> None:
    decision = PermissionEngine().decide(make_spec("x.y", risk))
    assert decision.allowed is allowed
    assert decision.needs_confirmation is needs_confirmation
    assert decision.risk is risk


def test_privileged_needs_an_explicit_grant() -> None:
    spec = make_spec("software.install", RiskLevel.PRIVILEGED)
    engine = PermissionEngine()
    denied = engine.decide(spec)
    assert denied.allowed is False
    assert "concessão explícita" in denied.reason

    engine.grant("software.install")
    granted = engine.decide(spec)
    assert granted.allowed is True
    assert granted.needs_confirmation is True


def test_grant_can_be_revoked() -> None:
    spec = make_spec("software.install", RiskLevel.PRIVILEGED)
    engine = PermissionEngine(grants={"software.install": True})
    assert engine.decide(spec).allowed is True
    engine.grant("software.install", False)
    assert engine.decide(spec).allowed is False


def test_blocked_cannot_be_unlocked_by_a_grant() -> None:
    """Uma concessão não contorna BLOCKED — bloqueado é bloqueado."""
    spec = make_spec("shell.destroy", RiskLevel.PRIVILEGED)
    engine = PermissionEngine({"shell.*": RiskLevel.BLOCKED}, {"shell.destroy": True})
    assert engine.decide(spec).allowed is False
