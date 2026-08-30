"""API de logs, que alimenta a aba de acompanhamento na interface."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from eve.daemon.app import create_app
from eve.daemon.routes.logs import _analisar
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


def test_stream_acompanha_o_arquivo_ao_vivo() -> None:
    """O barramento tem o que a EVE faz; o arquivo tem o resto — uvicorn,
    avisos de biblioteca, tudo que nunca vira evento."""
    p = paths().ensure()
    p.log_file.write_text("linha antiga\n", encoding="utf-8")

    with TestClient(create_app()) as client:
        with client.stream("GET", "/api/logs/stream", params={"source": "eve"}) as resposta:
            assert resposta.status_code == 200
            assert "text/event-stream" in resposta.headers["content-type"]
            with p.log_file.open("a", encoding="utf-8") as fh:
                fh.write("2026-08-30T10:00:00Z [warning  ] algo.novo  x=1\n")
            for linha in resposta.iter_lines():
                if linha.startswith("data:"):
                    entrada = json.loads(linha[5:])
                    break
    # Só o que chegou depois de conectar; a linha antiga não é reenviada.
    assert entrada["event"] == "algo.novo"
    assert entrada["level"] == "warning"


def test_stream_recusa_fonte_desconhecida() -> None:
    with TestClient(create_app()) as client:
        resposta = client.get("/api/logs/stream", params={"source": "../etc/passwd"})
    assert resposta.status_code == 422
