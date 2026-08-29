"""A interface é servida pelo próprio daemon."""

from __future__ import annotations

from fastapi.testclient import TestClient

from eve.daemon.app import create_app
from eve.web import is_built


def test_root_serves_the_interface() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    if is_built():
        assert '<div id="root">' in response.text
    else:
        assert "npm run build" in response.text


def test_api_is_not_swallowed_by_the_spa_route() -> None:
    """A rota curinga da interface não pode capturar a API."""
    with TestClient(create_app()) as client:
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/inexistente").status_code == 404


def test_unknown_path_returns_the_app_not_a_404() -> None:
    if not is_built():
        return
    with TestClient(create_app()) as client:
        response = client.get("/qualquer/rota/do/navegador")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_static_files_are_served() -> None:
    if not is_built():
        return
    with TestClient(create_app()) as client:
        assert client.get("/favicon.svg").status_code == 200
