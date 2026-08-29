"""API de Skills e servidores MCP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from eve.config import update_config_file
from eve.mcp.client import MCPServerConfig
from eve.skills.catalog import CATALOGO
from eve.skills.model import SkillError

router = APIRouter(prefix="/api")


class AddServer(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_-]*$")
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    enabled: bool = True


# ------------------------------------------------------------------- Skills


@router.get("/skills")
async def list_skills(request: Request) -> dict[str, Any]:
    manager = request.app.state.skills
    instaladas = manager.describe()
    nomes = {s["name"] for s in instaladas}
    return {
        "skills": instaladas,
        "count": len(instaladas),
        "catalog": sorted(nome for nome in CATALOGO if nome not in nomes),
    }


@router.post("/skills/{name}")
async def install_skill(request: Request, name: str) -> dict[str, Any]:
    manager = request.app.state.skills
    try:
        skill = manager.install(name)
    except (KeyError, SkillError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await _reload(request)
    return skill.describe(manager.missing_secrets(skill))


@router.delete("/skills/{name}")
async def remove_skill(request: Request, name: str) -> dict[str, Any]:
    if not request.app.state.skills.remove(name):
        raise HTTPException(status_code=404, detail="Skill desconhecida")
    await _reload(request)
    return {"removed": name}


@router.post("/skills/{name}/enabled")
async def set_skill_enabled(request: Request, name: str, enabled: bool = True) -> dict[str, Any]:
    manager = request.app.state.skills
    skill = manager.set_enabled(name, enabled)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill desconhecida")
    await _reload(request)
    return skill.describe(manager.missing_secrets(skill))


# ---------------------------------------------------------------------- MCP


@router.get("/mcp")
async def list_servers(request: Request) -> dict[str, Any]:
    mcp = request.app.state.mcp
    servidores = mcp.describe()
    return {"servers": servidores, "count": len(servidores), "tools": mcp.tool_count}


@router.post("/mcp")
async def add_server(request: Request, body: AddServer) -> dict[str, Any]:
    if not body.command and not body.url:
        raise HTTPException(status_code=400, detail="informe command ou url")

    def mutate(dados: dict[str, Any]) -> None:
        servidores = [s for s in dados.get("mcp", []) if s.get("name") != body.name]
        servidores.append(body.model_dump(exclude_defaults=False))
        dados["mcp"] = servidores

    update_config_file(mutate)

    conexao = await request.app.state.mcp.add(
        MCPServerConfig(
            name=body.name,
            command=body.command,
            args=list(body.args),
            env=dict(body.env),
            url=body.url,
            enabled=body.enabled,
        )
    )
    return conexao.describe()


@router.delete("/mcp/{name}")
async def remove_server(request: Request, name: str) -> dict[str, Any]:
    removido = await request.app.state.mcp.remove(name)

    def mutate(dados: dict[str, Any]) -> None:
        dados["mcp"] = [s for s in dados.get("mcp", []) if s.get("name") != name]

    update_config_file(mutate)
    if not removido:
        raise HTTPException(status_code=404, detail="servidor desconhecido")
    return {"removed": name}


@router.post("/mcp/{name}/reconnect")
async def reconnect_server(request: Request, name: str) -> dict[str, Any]:
    conexao = await request.app.state.mcp.reconnect(name)
    if conexao is None:
        raise HTTPException(status_code=404, detail="servidor desconhecido")
    return conexao.describe()


async def _reload(request: Request) -> None:
    """Recarrega Skills, permissões e conexões depois de uma mudança.

    A configuração é relida do disco: `eve mcp add` grava no config.toml, e
    usar a cópia carregada no start faria uma instalação de Skill derrubar
    silenciosamente os servidores avulsos.
    """
    from eve.config import load_settings
    from eve.daemon.app import _apply_skill_permissions, _standalone_servers

    app = request.app
    app.state.settings = load_settings()
    app.state.skills.load_all()
    _apply_skill_permissions(app)
    await app.state.mcp.aclose()
    await app.state.skills.connect_enabled(_standalone_servers(app.state.settings))
