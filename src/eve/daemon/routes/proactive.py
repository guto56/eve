"""API de observadores e proatividade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from eve.config import update_config_file

router = APIRouter(prefix="/api")


class AddWatch(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_-]*$")
    path: str = Field(min_length=1, max_length=4096)


@router.get("/watch")
async def list_watchers(request: Request) -> dict[str, Any]:
    observadores = request.app.state.watch.describe()
    return {"observers": observadores, "count": len(observadores)}


@router.post("/watch")
async def add_watch(request: Request, body: AddWatch) -> dict[str, Any]:
    import asyncio

    alvo = await asyncio.to_thread(lambda: Path(body.path).expanduser().resolve())
    if not await asyncio.to_thread(alvo.exists):
        raise HTTPException(status_code=400, detail=f"não existe: {alvo}")

    def mutate(dados: dict[str, Any]) -> None:
        itens = [w for w in dados.get("watch", []) if w.get("name") != body.name]
        itens.append({"name": body.name, "path": str(alvo), "enabled": True})
        dados["watch"] = itens

    update_config_file(mutate)
    observador = await request.app.state.watch.add_path(body.name, alvo)
    return observador.describe()


@router.delete("/watch/{name}")
async def remove_watch(request: Request, name: str) -> dict[str, Any]:
    removido = await request.app.state.watch.remove(name)

    def mutate(dados: dict[str, Any]) -> None:
        dados["watch"] = [w for w in dados.get("watch", []) if w.get("name") != name]

    update_config_file(mutate)
    if not removido:
        raise HTTPException(status_code=404, detail="observador desconhecido")
    return {"removed": name}


@router.get("/proactive")
async def proactive_status(request: Request) -> dict[str, Any]:
    return {
        **request.app.state.proactive.describe(),
        "observers": request.app.state.watch.describe(),
    }
