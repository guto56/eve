from __future__ import annotations

import json

from fastapi.testclient import TestClient

from eve.daemon.app import create_app


def sse_events(response) -> list[dict]:
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def test_route_endpoint_does_not_execute_anything() -> None:
    with TestClient(create_app()) as client:
        data = client.post("/api/route", json={"message": "abra o Safari"}).json()
        auditoria = client.get("/api/audit").json()
    assert data["route"] == "command"
    assert data["fast_path"] is True
    assert data["tool_call"]["name"] == "app.open"
    assert auditoria["count"] == 0  # nada foi executado


def test_chat_fast_path_over_sse() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/chat", json={"message": "que horas são"})
    eventos = sse_events(response)
    tipos = [e["kind"] for e in eventos]
    assert tipos[0] == "session"
    assert "tool" in tipos
    assert tipos[-1] == "done"
    texto = "".join(e.get("text", "") for e in eventos if e["kind"] == "delta")
    assert texto.startswith("São ")


def test_chat_without_streaming_returns_the_whole_thing() -> None:
    with TestClient(create_app()) as client:
        data = client.post("/api/chat", json={"message": "que horas são", "stream": False}).json()
    assert data["text"].startswith("São ")
    assert data["events"][0]["kind"] == "session"


def test_chat_keeps_the_session() -> None:
    with TestClient(create_app()) as client:
        primeira = client.post(
            "/api/chat", json={"message": "que horas são", "stream": False}
        ).json()
        sid = primeira["events"][0]["session"]

        client.post("/api/chat", json={"message": "que horas são", "session": sid, "stream": False})
        sessoes = client.get("/api/sessions").json()
        detalhe = client.get(f"/api/sessions/{sid}").json()

    assert sessoes["count"] == 1
    assert detalhe["messages"] == 4
    assert [m["role"] for m in detalhe["history"]] == ["user", "assistant", "user", "assistant"]


def test_unknown_session_is_404() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/sessions/nao-existe").status_code == 404
        assert client.delete("/api/sessions/nao-existe").status_code == 404


def test_delete_session() -> None:
    with TestClient(create_app()) as client:
        data = client.post("/api/chat", json={"message": "que horas são", "stream": False}).json()
        sid = data["events"][0]["session"]
        assert client.delete(f"/api/sessions/{sid}").json()["deleted"] == sid
        assert client.get("/api/sessions").json()["count"] == 0


def test_message_is_validated() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/chat", json={"message": ""}).status_code == 422
        assert client.post("/api/route", json={}).status_code == 422


def test_status_reports_chat() -> None:
    with TestClient(create_app()) as client:
        client.post("/api/chat", json={"message": "que horas são", "stream": False})
        data = client.get("/api/status").json()
    assert data["chat"]["sessions"] == 1
    assert data["chat"]["max_tool_rounds"] >= 1


def test_stream_always_announces_the_end_even_on_failure(monkeypatch) -> None:
    """Regressão: uma exceção no gerador truncava o SSE em silêncio."""
    app = create_app()

    async def explode(*args, **kwargs):
        raise RuntimeError("estourou no meio")
        yield  # pragma: no cover

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.chat, "send", explode)
        response = client.post("/api/chat", json={"message": "oi"})
    eventos = sse_events(response)
    assert eventos[-2]["kind"] == "error"
    assert "estourou no meio" in eventos[-2]["error"]
    assert eventos[-1] == {"kind": "done", "aborted": True}
