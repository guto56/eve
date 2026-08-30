"""Logs do arquivo, para a interface.

Os eventos ao vivo já chegam pelo WebSocket. Isto aqui serve para o que não
passa pelo barramento — avisos internos, uvicorn, erros de biblioteca — e para
ver o que aconteceu antes de a aba ser aberta.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from eve.paths import paths

router = APIRouter(prefix="/api/logs")

#: structlog em modo console: "2026-08-30T07:21:53.636285Z [info     ] evento  k=v"
LINHA = re.compile(r"^(?P<ts>\S+)\s+\[(?P<level>\w+)\s*\]\s+(?P<event>\S+)\s*(?P<rest>.*)$")

ARQUIVOS = {
    "eve": "log_file",
    "daemon": "daemon.out",
    "service": "service.err",
    "mcp": "mcp.log",
}


@router.get("")
async def read_logs(
    request: Request,
    source: str = Query(default="eve", pattern="^(eve|daemon|service|mcp)$"),
    lines: int = Query(default=200, ge=1, le=2000),
    q: str = Query(default="", max_length=200),
) -> dict[str, Any]:
    caminho = _caminho(source)
    if not caminho.exists():
        return {"source": source, "path": str(caminho), "entries": [], "count": 0}

    conteudo = caminho.read_text(encoding="utf-8", errors="replace").splitlines()
    if q:
        alvo = q.lower()
        conteudo = [linha for linha in conteudo if alvo in linha.lower()]

    return {
        "source": source,
        "path": str(caminho),
        "entries": [_analisar(linha) for linha in conteudo[-lines:]],
        "count": len(conteudo),
    }


@router.get("/sources")
async def list_sources(request: Request) -> dict[str, Any]:
    fontes = []
    for nome in ARQUIVOS:
        caminho = _caminho(nome)
        fontes.append(
            {
                "name": nome,
                "path": str(caminho),
                "bytes": caminho.stat().st_size if caminho.exists() else 0,
                "exists": caminho.exists(),
            }
        )
    return {"sources": fontes}


def _caminho(source: str) -> Path:
    p = paths()
    if source == "eve":
        return p.log_file
    return p.logs / ARQUIVOS[source]


def _analisar(linha: str) -> dict[str, Any]:
    """Quebra a linha em partes quando dá; senão devolve o texto cru."""
    if linha.startswith("{"):
        try:
            dados = json.loads(linha)
        except json.JSONDecodeError:
            pass
        else:
            return {
                "ts": dados.get("timestamp", ""),
                "level": dados.get("level", "info"),
                "event": dados.get("event", ""),
                "detail": " ".join(
                    f"{k}={v}" for k, v in dados.items() if k not in ("timestamp", "level", "event")
                ),
                "raw": linha,
            }

    casou = LINHA.match(linha)
    if casou is None:
        return {"ts": "", "level": "raw", "event": "", "detail": linha, "raw": linha}
    return {
        "ts": casou.group("ts"),
        "level": casou.group("level"),
        "event": casou.group("event"),
        "detail": casou.group("rest").strip(),
        "raw": linha,
    }
