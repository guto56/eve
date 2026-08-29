from __future__ import annotations

from fastapi.testclient import TestClient

from eve import __version__
from eve.daemon.app import create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_status_reports_components_and_bus() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/status").json()
    assert data["status"] == "ok"
    assert data["version"] == __version__
    assert data["uptime_seconds"] >= 0
    assert data["server"]["port"] == 4242
    # o lifespan publicou system.started
    assert data["bus"]["published"] >= 1
    assert "ai_local" in data["components"]


def test_status_reflects_configured_port(isolated_home, monkeypatch) -> None:
    monkeypatch.setenv("EVE_SERVER__PORT", "4999")
    with TestClient(create_app()) as client:
        assert client.get("/api/status").json()["server"]["port"] == 4999
