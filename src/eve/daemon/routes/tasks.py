"""API de tarefas de agente."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/tasks")


@router.get("")
async def list_tasks(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    manager = request.app.state.tasks
    tarefas = manager.all()[:limit]
    return {
        "tasks": [t.as_dict(False) for t in tarefas],
        "count": len(tarefas),
        "active": len(manager.active),
    }


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str) -> dict[str, Any]:
    task = request.app.state.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="tarefa desconhecida")
    return task.as_dict()


@router.post("/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str) -> dict[str, Any]:
    manager = request.app.state.tasks
    if manager.get(task_id) is None:
        raise HTTPException(status_code=404, detail="tarefa desconhecida")
    if not manager.cancel(task_id):
        raise HTTPException(status_code=409, detail="a tarefa já terminou")
    return {"cancelled": task_id}
