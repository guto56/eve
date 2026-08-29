"""O que é uma tarefa de agente.

O plano é para o usuário ver o que a EVE pretende fazer; os passos são o que
ela fez de verdade. Manter os dois separados evita a tentação de fingir
progresso — marcar "passo 2 concluído" porque o modelo disse que sim, e não
porque alguma coisa aconteceu.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass
class Step:
    """Uma ação que aconteceu de verdade."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    ok: bool | None = None
    error: str | None = None
    duration_ms: float = 0.0
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "ok": self.ok,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
            "at": self.at,
        }


@dataclass
class Task:
    goal: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: TaskStatus = TaskStatus.PLANNING
    plan: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    result: str = ""
    error: str = ""
    session: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def duration_ms(self) -> float:
        fim = self.finished_at or time.time()
        return (fim - self.created_at) * 1000

    def finish(self, status: TaskStatus, result: str = "", error: str = "") -> None:
        self.status = status
        self.result = result or self.result
        self.error = error
        self.finished_at = time.time()

    def as_dict(self, com_passos: bool = True) -> dict[str, Any]:
        dados: dict[str, Any] = {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "plan": list(self.plan),
            "steps_done": len(self.steps),
            "result": self.result,
            "error": self.error,
            "session": self.session,
            "created_at": self.created_at,
            "duration_ms": round(self.duration_ms, 1),
        }
        if com_passos:
            dados["steps"] = [s.as_dict() for s in self.steps]
        return dados

    def summary(self) -> str:
        if self.status is TaskStatus.DONE:
            return f"{len(self.steps)} passo(s) em {self.duration_ms / 1000:.1f}s"
        if self.status is TaskStatus.FAILED:
            return self.error or "falhou"
        return self.status.value
