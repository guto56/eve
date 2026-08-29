from __future__ import annotations

from fastapi.testclient import TestClient

from eve import __version__
from eve.daemon.app import create_app
from eve.events import EventType

SILENT = "?topics=nada.*"


def test_hello_and_topics() -> None:
    with TestClient(create_app()) as client, client.websocket_connect("/ws?topics=tool.*") as ws:
        hello = ws.receive_json()
    assert hello["type"] == "hello"
    assert hello["version"] == __version__
    assert hello["topics"] == ["tool.*"]


def test_default_topic_is_everything() -> None:
    with TestClient(create_app()) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["topics"] == ["*"]


def test_replays_history_then_streams_live() -> None:
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws?topics=system.*") as ws:
            assert ws.receive_json()["type"] == "hello"
            replay = ws.receive_json()
        assert replay["type"] == "event"
        assert replay["replay"] is True
        assert replay["event"]["type"] == EventType.SYSTEM_STARTED

        with client.websocket_connect("/ws?topics=client.*&history=0") as ws:
            assert ws.receive_json()["type"] == "hello"
            live = ws.receive_json()
    assert live["event"]["type"] == EventType.CLIENT_CONNECTED
    assert "replay" not in live


def test_history_can_be_disabled() -> None:
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws?topics=system.*&history=0") as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"op": "ping"})
            assert ws.receive_json()["type"] == "pong"


def test_ping_pong() -> None:
    with TestClient(create_app()) as client, client.websocket_connect(f"/ws{SILENT}") as ws:
        ws.receive_json()
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_subscribe_changes_topics() -> None:
    with TestClient(create_app()) as client, client.websocket_connect(f"/ws{SILENT}") as ws:
        ws.receive_json()
        ws.send_json({"op": "subscribe", "patterns": ["voice.*", "tool.*"]})
        assert ws.receive_json() == {"type": "subscribed", "topics": ["voice.*", "tool.*"]}


def test_invalid_messages_are_reported_without_closing() -> None:
    with TestClient(create_app()) as client, client.websocket_connect(f"/ws{SILENT}") as ws:
        ws.receive_json()

        ws.send_text("nao é json")
        assert ws.receive_json()["type"] == "error"

        ws.send_json(["lista"])
        assert ws.receive_json()["type"] == "error"

        ws.send_json({"op": "voar"})
        assert "voar" in ws.receive_json()["message"]

        ws.send_json({"op": "subscribe", "patterns": "nao-e-lista"})
        assert ws.receive_json()["type"] == "error"

        # a conexão continua utilizável depois de todos os erros
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_disconnect_is_counted_in_bus() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws{SILENT}") as ws:
            ws.receive_json()
        published = client.get("/api/status").json()["bus"]["published"]
    # system.started + client.connected + client.disconnected
    assert published >= 3


# ------------------------------------------------------- Fase 5: conversa


def test_chat_over_the_socket() -> None:
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws?topics=message.*,tool.*&history=0") as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"op": "chat", "message": "que horas são"})

            tipos, chat, texto = [], [], ""
            for _ in range(60):
                frame = ws.receive_json()
                if frame["type"] == "event":
                    tipos.append(frame["event"]["type"])
                elif frame["type"] == "chat":
                    chat.append(frame["kind"])
                    texto += frame.get("text", "")
                    if frame["kind"] == "done":
                        break

    # A sequência da conversa chega inteira e em ordem neste socket.
    assert chat[0] == "session"
    assert chat[-1] == "done"
    assert chat.index("tool") < chat.index("tool_result") < chat.index("done")
    assert texto.startswith("São ")
    # E os mesmos passos aparecem no barramento, para quem observa.
    assert EventType.MESSAGE_RECEIVED in tipos


def test_chat_op_validates_its_input() -> None:
    with TestClient(create_app()) as client, client.websocket_connect(f"/ws{SILENT}") as ws:
        ws.receive_json()

        ws.send_json({"op": "chat"})
        assert "message" in ws.receive_json()["message"]

        ws.send_json({"op": "chat", "message": "   "})
        assert ws.receive_json()["type"] == "error"

        ws.send_json({"op": "chat", "message": "oi", "session": 42})
        assert "session" in ws.receive_json()["message"]

        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}
