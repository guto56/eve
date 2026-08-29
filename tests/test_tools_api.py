from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from eve.daemon.app import create_app
from eve.paths import paths


def test_list_tools() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/tools").json()
    assert data["count"] == 37
    assert data["namespaces"] == [
        "app",
        "browser",
        "clipboard",
        "eve",
        "file",
        "memory",
        "system",
        "url",
        "web",
    ]
    names = [t["name"] for t in data["tools"]]
    assert names[:2] == ["app.activate", "app.frontmost"]
    assert "file.trash" in names
    assert data["tools"][0]["effective"]["allowed"] is True


def test_get_single_tool_and_404() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/tools/eve.echo").json()
        missing = client.get("/api/tools/nao.existe")
    assert data["parameters"]["additionalProperties"] is False
    assert missing.status_code == 404


def test_call_tool() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/tools/eve.echo/call", json={"args": {"message": "oi"}})
    body = response.json()
    assert body["ok"] is True
    assert body["value"]["message"] == "oi"
    assert body["value"]["source"] == "api"


def test_call_with_invalid_args_is_a_result_not_a_crash() -> None:
    with TestClient(create_app()) as client:
        body = client.post("/api/tools/eve.echo/call", json={"args": {}}).json()
    assert body["ok"] is False
    assert body["error_kind"] == "invalid_args"


def test_call_unknown_tool_is_404() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/tools/nao.existe/call", json={"args": {}})
    assert response.status_code == 404


def test_blocked_tool_is_reported_in_the_listing(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text('[permissions.overrides]\n"eve.*" = "blocked"\n')
    with TestClient(create_app()) as client:
        data = client.get("/api/tools/eve.echo").json()
        result = client.post("/api/tools/eve.echo/call", json={"args": {"message": "oi"}}).json()
    assert data["effective"]["risk"] == "blocked"
    assert data["effective"]["allowed"] is False
    assert result["error_kind"] == "denied"


def test_unknown_permission_level_is_ignored_not_fatal(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    paths().config_file.write_text('[permissions.overrides]\n"eve.echo" = "talvez"\n')
    with TestClient(create_app()) as client:
        data = client.get("/api/tools/eve.echo").json()
    assert data["effective"]["risk"] == "safe"


def test_audit_endpoint_reflects_calls() -> None:
    with TestClient(create_app()) as client:
        client.post("/api/tools/eve.echo/call", json={"args": {"message": "oi"}})
        data = client.get("/api/audit", params={"limit": 5}).json()
    assert data["count"] >= 1
    assert data["entries"][-1]["tool"] == "eve.echo"


def test_approvals_start_empty_and_unknown_decision_is_404() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/approvals").json() == {"pending": [], "count": 0}
        response = client.post("/api/approvals/inexistente", json={"approved": True})
    assert response.status_code == 404


def test_permissions_endpoint_and_reload(isolated_home: Path) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/permissions").json()["overrides"] == {}

        paths().ensure()
        paths().config_file.write_text('[permissions.overrides]\n"eve.echo" = "blocked"\n')
        reloaded = client.post("/api/permissions/reload").json()
        assert reloaded["overrides"] == {"eve.echo": "blocked"}

        result = client.post("/api/tools/eve.echo/call", json={"args": {"message": "oi"}}).json()
    assert result["error_kind"] == "denied"


def test_status_counts_tools() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/status").json()
    assert data["tools"]["count"] == 37
    assert data["components"]["tools"] == "ativo"
    assert data["tools"]["pending_approvals"] == 0
