"""Tool Bus (spec §10).

Todo caminho até uma ação passa por aqui:

    modelo / CLI / interface
        ↓  chamada
    validação de argumentos
        ↓
    Permission Engine
        ↓  (confirmação, se exigida)
    execução com prazo
        ↓
    resultado + auditoria + eventos

Um handler nunca é chamado antes da validação e da decisão de permissão, e
nenhuma chamada sai daqui sem deixar rastro na auditoria.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError

from eve.bus import EventBus
from eve.config import Settings
from eve.events import EventType
from eve.logging import get_logger
from eve.permissions import Decision, PermissionEngine
from eve.tools.approvals import ApprovalBroker
from eve.tools.audit import AuditLog
from eve.tools.registry import ToolRegistry, UnknownToolError
from eve.tools.spec import (
    ToolContext,
    ToolResult,
    ToolSpec,
    new_request_id,
    now_ms,
)

log = get_logger(__name__)

# Falhas previsíveis viram categorias próprias, para que quem chamou — modelo,
# interface ou pessoa — saiba se deve corrigir o argumento, pedir permissão ou
# desistir. Só o que não está aqui é tratado como defeito.
ERROR_KINDS: tuple[tuple[type[Exception], str], ...] = (
    (PermissionError, "not_permitted"),
    (FileNotFoundError, "not_found"),
    (FileExistsError, "already_exists"),
    (NotADirectoryError, "wrong_kind"),
    (IsADirectoryError, "wrong_kind"),
    (TimeoutError, "timeout"),
)


def classify(exc: Exception) -> tuple[str, str]:
    """Devolve ``(categoria, mensagem)`` para uma exceção de handler."""
    for exc_type, kind in ERROR_KINDS:
        if isinstance(exc, exc_type):
            return kind, str(exc)
    return "handler_error", f"{type(exc).__name__}: {exc}"


class ToolBus:
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        events: EventBus,
        audit: AuditLog,
        settings: Settings,
        approvals: ApprovalBroker | None = None,
        services: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self.permissions = permissions
        self.events = events
        self.audit = audit
        self.settings = settings
        self.approvals = approvals or ApprovalBroker(settings.permissions.confirm_timeout)
        self.services: dict[str, Any] = services if services is not None else {}

    async def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        source: str = "api",
        caller: str = "user",
        auto_approve: bool = False,
    ) -> ToolResult:
        """Executa uma ferramenta do começo ao fim.

        ``auto_approve`` pula a confirmação humana e existe para chamadas em
        que o usuário já confirmou fora do barramento (``eve tool call --yes``).
        Um modelo nunca deve poder ligá-lo.
        """
        request_id = new_request_id()
        raw_args = dict(args or {})
        started = now_ms()

        try:
            spec = self.registry.get(name)
        except UnknownToolError as exc:
            return await self._fail(
                request_id, name, "unknown_tool", str(exc), raw_args, started, source, caller
            )

        await self.events.emit(
            EventType.TOOL_REQUESTED,
            source=source,
            request_id=request_id,
            tool=name,
            args=spec.redact(raw_args),
        )

        try:
            parsed = spec.params.model_validate(raw_args)
        except ValidationError as exc:
            return await self._fail(
                request_id,
                name,
                "invalid_args",
                _format_validation_error(exc),
                spec.redact(raw_args),
                started,
                source,
                caller,
                spec=spec,
            )

        decision = self.permissions.decide(spec)
        if not decision.allowed:
            return await self._deny(
                request_id, spec, raw_args, decision, decision.reason, started, source, caller
            )

        if decision.needs_confirmation and not auto_approve:
            approved, by = await self._ask(request_id, spec, raw_args, decision, source)
            if not approved:
                return await self._deny(
                    request_id, spec, raw_args, decision, by, started, source, caller
                )
            await self.events.emit(
                EventType.TOOL_APPROVED,
                source=source,
                request_id=request_id,
                tool=name,
                by=by,
            )
        elif decision.needs_confirmation:
            await self.events.emit(
                EventType.TOOL_APPROVED,
                source=source,
                request_id=request_id,
                tool=name,
                by="confirmação prévia",
            )

        context = ToolContext(
            request_id=request_id,
            source=source,
            settings=self.settings,
            bus=self.events,
            caller=caller,
            services=self.services,
        )

        try:
            async with asyncio.timeout(spec.timeout):
                value = await spec.handler(parsed, context)
        except TimeoutError:
            return await self._fail(
                request_id,
                name,
                "timeout",
                f"a ferramenta passou de {spec.timeout:g}s",
                spec.redact(raw_args),
                started,
                source,
                caller,
                spec=spec,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            kind, message = classify(exc)
            log.warning("tool.handler_error", tool=name, kind=kind, error=str(exc))
            return await self._fail(
                request_id,
                name,
                kind,
                message,
                spec.redact(raw_args),
                started,
                source,
                caller,
                spec=spec,
            )

        result = ToolResult(
            request_id=request_id,
            tool=name,
            ok=True,
            value=value,
            duration_ms=now_ms() - started,
        )
        await self.events.emit(
            EventType.TOOL_COMPLETED,
            source=source,
            request_id=request_id,
            tool=name,
            duration_ms=result.duration_ms,
        )
        self.audit.record(
            request_id=request_id,
            tool=name,
            args=spec.redact(raw_args),
            risk=decision.risk.value,
            outcome="ok",
            source=source,
            caller=caller,
            duration_ms=round(result.duration_ms, 2),
        )
        return result

    async def _ask(
        self,
        request_id: str,
        spec: ToolSpec,
        args: dict[str, Any],
        decision: Decision,
        source: str,
    ) -> tuple[bool, str]:
        redacted = spec.redact(args)
        await self.events.emit(
            EventType.TOOL_CONFIRMATION_REQUIRED,
            source=source,
            request_id=request_id,
            tool=spec.name,
            args=redacted,
            risk=decision.risk.value,
            reason=decision.reason,
            description=spec.description,
            reversible=spec.reversible,
        )
        return await self.approvals.request(
            request_id=request_id,
            tool=spec.name,
            args=redacted,
            risk=decision.risk.value,
            reason=decision.reason,
            source=source,
            timeout=self.settings.permissions.confirm_timeout,
        )

    async def _deny(
        self,
        request_id: str,
        spec: ToolSpec,
        args: dict[str, Any],
        decision: Decision,
        reason: str,
        started: float,
        source: str,
        caller: str,
    ) -> ToolResult:
        result = ToolResult(
            request_id=request_id,
            tool=spec.name,
            ok=False,
            error=reason,
            error_kind="denied",
            duration_ms=now_ms() - started,
        )
        await self.events.emit(
            EventType.TOOL_DENIED,
            source=source,
            request_id=request_id,
            tool=spec.name,
            reason=reason,
            risk=decision.risk.value,
        )
        self.audit.record(
            request_id=request_id,
            tool=spec.name,
            args=spec.redact(args),
            risk=decision.risk.value,
            outcome="denied",
            reason=reason,
            source=source,
            caller=caller,
        )
        return result

    async def _fail(
        self,
        request_id: str,
        name: str,
        kind: str,
        message: str,
        args: dict[str, Any],
        started: float,
        source: str,
        caller: str,
        spec: ToolSpec | None = None,
    ) -> ToolResult:
        result = ToolResult(
            request_id=request_id,
            tool=name,
            ok=False,
            error=message,
            error_kind=kind,
            duration_ms=now_ms() - started,
        )
        await self.events.emit(
            EventType.TOOL_FAILED,
            source=source,
            request_id=request_id,
            tool=name,
            error=message,
            error_kind=kind,
        )
        self.audit.record(
            request_id=request_id,
            tool=name,
            args=args,
            risk=spec.risk.value if spec else None,
            outcome="failed",
            error_kind=kind,
            error=message,
            source=source,
            caller=caller,
        )
        return result


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(p) for p in error["loc"]) or "(raiz)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)
