"""API de logs, que alimenta a aba de acompanhamento na interface."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from starlette.requests import Request

from eve.daemon.app import create_app
from eve.daemon.routes.logs import _analisar, stream_logs
from eve.paths import paths


def test_le_o_arquivo_de_log() -> None:
    p = paths().ensure()
    p.log_file.write_text(
        "2026-08-30T07:21:53.6Z [info     ] core.started  pid=1 version=0.1.0\n"
        "Uvicorn running on http://127.0.0.1:4242\n",
        encoding="utf-8",
    )
    with TestClient(create_app()) as client:
        data = client.get("/api/logs", params={"source": "eve"}).json()
    assert data["count"] == 2
    assert data["entries"][0]["event"] == "core.started"
    # Linha que não é do structlog vem inteira, sem se perder.
    assert data["entries"][1]["level"] == "raw"
    assert "Uvicorn" in data["entries"][1]["detail"]


def test_filtra_por_texto() -> None:
    p = paths().ensure()
    p.log_file.write_text("achar isto\nignorar aquilo\n", encoding="utf-8")
    with TestClient(create_app()) as client:
        data = client.get("/api/logs", params={"q": "achar"}).json()
    assert data["count"] == 1


def test_arquivo_inexistente_nao_e_erro() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/logs", params={"source": "mcp"}).json()
    assert data["entries"] == []


def test_fonte_desconhecida_e_recusada() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/logs", params={"source": "/etc/passwd"}).status_code == 422


def test_lista_as_fontes() -> None:
    with TestClient(create_app()) as client:
        fontes = client.get("/api/logs/sources").json()["sources"]
    assert {f["name"] for f in fontes} == {"eve", "daemon", "service", "mcp"}


def test_entende_log_em_json() -> None:
    linha = '{"timestamp": "2026-08-30T07:00:00Z", "level": "warning", "event": "x.y", "n": 3}'
    entrada = _analisar(linha)
    assert entrada["level"] == "warning"
    assert entrada["event"] == "x.y"
    assert "n=3" in entrada["detail"]


def test_websocket_entrega_historico_ao_conectar() -> None:
    """Abrir a aba e ver tela vazia não informa nada."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws?topics=*&history=50") as ws:
            assert ws.receive_json()["type"] == "hello"
            primeiro = ws.receive_json()
    assert primeiro["type"] == "event"
    assert primeiro["replay"] is True


async def test_stream_acompanha_o_arquivo_ao_vivo() -> None:
    """O barramento tem o que a EVE faz; o arquivo tem o resto — uvicorn,
    avisos de biblioteca, tudo que nunca vira evento.

    Consome o gerador direto: o TestClient roda a app até o fim antes de
    devolver a resposta, e uma resposta que por natureza não termina o
    penduraria para sempre. O ``wait_for`` é o que garante que uma regressão
    aqui vire falha em segundos em vez de uma suíte travada.
    """
    p = paths().ensure()
    p.log_file.write_text("linha antiga\n", encoding="utf-8")

    resposta = await stream_logs(_pedido_vivo(), source="eve")
    gerador = resposta.body_iterator

    async def escrever_depois() -> None:
        # Depois: o gerador só marca onde o arquivo estava quando começa a
        # ser consumido, e o que interessa é o que chega dali para frente.
        await asyncio.sleep(0.3)
        with p.log_file.open("a", encoding="utf-8") as fh:
            fh.write("2026-08-30T10:00:00Z [warning  ] algo.novo  x=1\n")

    escritor = asyncio.create_task(escrever_depois())
    try:
        pedaco = await asyncio.wait_for(anext(gerador), timeout=10)
    finally:
        await escritor
        await gerador.aclose()

    # Só o que chegou depois de conectar; a linha antiga não é reenviada.
    entrada = json.loads(pedaco.removeprefix("data: "))
    assert entrada["event"] == "algo.novo"
    assert entrada["level"] == "warning"


def _pedido_vivo() -> Request:
    """Requisição que nunca desconecta, para o gerador seguir acompanhando."""

    async def receive() -> dict[str, str]:  # pragma: no cover - cancelado antes de retornar
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    return Request(
        {"type": "http", "method": "GET", "path": "/api/logs/stream", "headers": []}, receive
    )


def test_stream_recusa_fonte_desconhecida() -> None:
    with TestClient(create_app()) as client:
        resposta = client.get("/api/logs/stream", params={"source": "../etc/passwd"})
    assert resposta.status_code == 422
