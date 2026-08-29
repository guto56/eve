from __future__ import annotations

from fastapi.testclient import TestClient

from eve.daemon.app import create_app


def test_secrets_endpoint_shows_state_without_values() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/secrets").json()
    assert data["missing_required"] == ["OPENROUTER_API_KEY"]
    nomes = [s["name"] for s in data["secrets"]]
    assert "DEEPGRAM_API_KEY" in nomes
    assert all(s["hint"] is None for s in data["secrets"])


def test_set_and_delete_a_secret() -> None:
    with TestClient(create_app()) as client:
        put = client.put("/api/secrets/OPENROUTER_API_KEY", json={"value": "sk-or-teste-12345"})
        assert put.status_code == 200

        data = client.get("/api/secrets").json()
        entrada = next(s for s in data["secrets"] if s["name"] == "OPENROUTER_API_KEY")
        assert entrada["configured"] is True
        assert entrada["hint"] == "sk-o…2345"
        assert data["missing_required"] == []

        assert client.delete("/api/secrets/OPENROUTER_API_KEY").json()["removed"] is True
        assert client.get("/api/secrets").json()["missing_required"] == ["OPENROUTER_API_KEY"]


def test_invalid_secret_name_is_rejected() -> None:
    with TestClient(create_app()) as client:
        response = client.put("/api/secrets/minuscula", json={"value": "x"})
    assert response.status_code == 400


def test_empty_secret_value_is_rejected() -> None:
    with TestClient(create_app()) as client:
        response = client.put("/api/secrets/MINHA_CHAVE", json={"value": ""})
    assert response.status_code == 422


def test_providers_endpoint_lists_roles() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/providers").json()
    assert data["models"]["local"] == "qwen3.5:2b"
    assert data["models"]["heavy"] == "anthropic/claude-sonnet-5"
    nomes = [p["name"] for p in data["providers"]]
    assert "ollama" in nomes


def test_external_role_without_credential_is_503() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/ai/ask", json={"prompt": "oi", "role": "external"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


def test_unknown_provider_models_is_404() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/providers/inventado/models").status_code == 404


def test_status_reports_ai_components() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/status").json()
    assert data["components"]["ai_local"] == "ativo"
    assert data["components"]["ai_external"] == "sem credencial"
    assert data["secrets"]["missing_required"] == ["OPENROUTER_API_KEY"]


def test_ask_validates_the_prompt() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/ai/ask", json={"prompt": ""}).status_code == 422
        assert client.post("/api/ai/ask", json={"prompt": "oi", "role": "x"}).status_code == 422


def test_providers_reset_rereads_configuration() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/providers/reset").json()["reset"] is True
