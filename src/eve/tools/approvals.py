"""Confirmações pendentes.

Separa *propor* de *executar*: uma ferramenta que exige confirmação fica
parada aqui, descrita por inteiro, até que alguém aprove pela interface, pela
CLI ou pela API. Sem resposta dentro do prazo, a chamada é negada — o padrão
seguro é não executar.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    args: dict[str, Any]
    risk: str
    reason: str
    source: str
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future[tuple[bool, str]] = field(repr=False, default=None)  # type: ignore[assignment]

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "risk": self.risk,
            "reason": self.reason,
            "source": self.source,
            "created_at": self.created_at,
            "waiting_seconds": round(time.time() - self.created_at, 2),
        }


class ApprovalBroker:
    def __init__(self, default_timeout: float = 120.0) -> None:
        self.default_timeout = default_timeout
        self._pending: dict[str, ApprovalRequest] = {}

    async def request(
        self,
        *,
        request_id: str,
        tool: str,
        args: dict[str, Any],
        risk: str,
        reason: str,
        source: str,
        # O prazo é política, não cancelamento: estourá-lo é uma NEGAÇÃO com
        # motivo, então ele pertence à assinatura e não ao chamador.
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> tuple[bool, str]:
        """Bloqueia até alguém decidir. Retorna ``(aprovado, por quem/por quê)``."""
        loop = asyncio.get_running_loop()
        pending = ApprovalRequest(
            id=request_id,
            tool=tool,
            args=args,
            risk=risk,
            reason=reason,
            source=source,
            future=loop.create_future(),
        )
        self._pending[request_id] = pending
        try:
            async with asyncio.timeout(timeout or self.default_timeout):
                return await pending.future
        except TimeoutError:
            return False, "tempo de confirmação esgotado"
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, approved: bool, by: str = "usuário") -> bool:
        """Decide uma pendência. ``False`` se ela não existe (ou já foi decidida)."""
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result((approved, by))
        return True

    def pending(self) -> list[dict[str, Any]]:
        return [p.describe() for p in sorted(self._pending.values(), key=lambda r: r.created_at)]

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._pending.get(request_id)

    def deny_all(self, reason: str = "cancelado") -> int:
        count = 0
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result((False, reason))
                count += 1
        return count

    def __len__(self) -> int:
        return len(self._pending)
