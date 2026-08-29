"""Tarefas em andamento.

Uma tarefa longa não pode prender a conversa. Ela roda em segundo plano, o
usuário acompanha o progresso e pode cancelar.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from eve.agent.task import Task, TaskStatus
from eve.logging import get_logger

log = get_logger(__name__)

MAX_TASKS = 50


class TaskManager:
    def __init__(self, max_tasks: int = MAX_TASKS) -> None:
        self.max_tasks = max_tasks
        self._tasks: OrderedDict[str, Task] = OrderedDict()
        self._running: dict[str, asyncio.Task[Any]] = {}

    def create(self, goal: str, session: str | None = None) -> Task:
        task = Task(goal=goal, session=session)
        self._tasks[task.id] = task
        while len(self._tasks) > self.max_tasks:
            velho, _ = self._tasks.popitem(last=False)
            self._running.pop(velho, None)
        return task

    def track(self, task: Task, corrotina: asyncio.Task[Any]) -> None:
        self._running[task.id] = corrotina
        corrotina.add_done_callback(lambda _: self._running.pop(task.id, None))

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    @property
    def active(self) -> list[Task]:
        return [t for t in self._tasks.values() if not t.status.finished]

    def cancel(self, task_id: str) -> bool:
        corrotina = self._running.get(task_id)
        task = self._tasks.get(task_id)
        if task is None or task.status.finished:
            return False
        if corrotina is not None:
            corrotina.cancel()
        else:
            # Sem corrotina viva (a conversa que a criava já saiu), marcar
            # como cancelada ainda é a resposta honesta.
            task.finish(TaskStatus.CANCELLED)
        log.info("tarefa.cancelada", task=task_id)
        return True

    async def aclose(self) -> None:
        for corrotina in list(self._running.values()):
            corrotina.cancel()
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)
        self._running.clear()

    def describe(self) -> dict[str, Any]:
        return {
            "total": len(self._tasks),
            "active": len(self.active),
            "tasks": [t.as_dict(False) for t in self.all()[:20]],
        }

    def __len__(self) -> int:
        return len(self._tasks)
