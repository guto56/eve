"""Serve a interface web (spec §4).

A interface é uma aplicação de página única: qualquer caminho que não seja da
API devolve o ``index.html``, e o roteamento acontece no navegador.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from eve.logging import get_logger
from eve.web import STATIC, is_built

log = get_logger(__name__)
router = APIRouter()

SEM_BUILD = """<!doctype html>
<meta charset="utf-8"><title>EVE</title>
<style>
 body{background:#0b0d10;color:#e6e9ef;font:15px/1.6 -apple-system,system-ui,sans-serif;
      display:grid;place-items:center;height:100vh;margin:0;text-align:center}
 code{background:#151920;padding:2px 7px;border-radius:6px;color:#5ce1c8}
</style>
<div>
  <h1 style="font-weight:300;letter-spacing:.3em">EVE</h1>
  <p>A interface ainda não foi construída.</p>
  <p><code>cd ui &amp;&amp; npm install &amp;&amp; npm run build</code></p>
  <p style="color:#5f6878">O Core está no ar — a API e a CLI funcionam normalmente.</p>
</div>
"""


def mount(app: FastAPI) -> None:
    """Monta a interface, se houver build."""
    if not is_built():
        log.info("web.sem_build", esperado=str(STATIC))

        @app.get("/", include_in_schema=False)
        async def sem_build() -> FileResponse | object:
            from fastapi.responses import HTMLResponse

            return HTMLResponse(SEM_BUILD)

        return

    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/{caminho:path}", include_in_schema=False)
    async def spa(caminho: str) -> FileResponse:
        # Um arquivo real (favicon, ícone) é servido; o resto devolve a página
        # e deixa o roteamento para o navegador.
        alvo = (STATIC / caminho).resolve()
        if alvo.is_file() and alvo.is_relative_to(STATIC.resolve()):
            return FileResponse(alvo)
        if caminho.startswith(("api/", "ws")):
            raise HTTPException(status_code=404, detail="não encontrado")
        return FileResponse(STATIC / "index.html")
