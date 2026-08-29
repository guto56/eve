"""Permission Engine (spec §11).

Decide, antes de qualquer execução, se uma ferramenta pode rodar sozinha,
precisa de confirmação, precisa de uma concessão explícita, ou não roda.

O risco declarado pela ferramenta é o padrão; o usuário pode sobrescrevê-lo por
nome exato (``file.delete``), por namespace (``file.*``) ou globalmente (``*``).
A regra mais específica vence.
"""

from __future__ import annotations

from dataclasses import dataclass

from eve.events import matches
from eve.tools.spec import RiskLevel, ToolSpec


@dataclass(frozen=True)
class Decision:
    allowed: bool
    needs_confirmation: bool
    risk: RiskLevel
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "needs_confirmation": self.needs_confirmation,
            "risk": self.risk.value,
            "reason": self.reason,
        }


class PermissionEngine:
    def __init__(
        self,
        overrides: dict[str, RiskLevel] | None = None,
        grants: dict[str, bool] | None = None,
    ) -> None:
        self.overrides = dict(overrides or {})
        self.grants = dict(grants or {})

    def _override_for(self, name: str) -> RiskLevel | None:
        """Regra mais específica primeiro: nome exato, depois padrões."""
        if name in self.overrides:
            return self.overrides[name]
        candidates = [
            (pattern, risk)
            for pattern, risk in self.overrides.items()
            if pattern != name and matches(pattern, name)
        ]
        if not candidates:
            return None
        # Entre padrões, o mais longo é o mais específico ("file.*" > "*").
        candidates.sort(key=lambda item: len(item[0]), reverse=True)
        return candidates[0][1]

    def effective_risk(self, spec: ToolSpec) -> RiskLevel:
        return self._override_for(spec.name) or spec.risk

    def is_granted(self, name: str) -> bool:
        return bool(self.grants.get(name))

    def grant(self, name: str, value: bool = True) -> None:
        self.grants[name] = value

    def decide(self, spec: ToolSpec) -> Decision:
        risk = self.effective_risk(spec)

        if risk is RiskLevel.BLOCKED:
            return Decision(False, False, risk, "ferramenta bloqueada pela configuração")

        if risk is RiskLevel.PRIVILEGED:
            if not self.is_granted(spec.name):
                return Decision(
                    False,
                    False,
                    risk,
                    f"exige concessão explícita — use `eve permission grant {spec.name}`",
                )
            return Decision(True, True, risk, "ferramenta privilegiada com concessão")

        if risk is RiskLevel.CONFIRM:
            return Decision(True, True, risk, "exige confirmação do usuário")

        return Decision(True, False, risk, "ferramenta segura")
