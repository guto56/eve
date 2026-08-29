from __future__ import annotations

import pytest

from eve.ai.base import ProviderError
from eve.ai.manager import ProviderManager
from eve.ai.ollama import OllamaProvider
from eve.ai.openrouter import OpenRouterProvider
from eve.config import Settings
from eve.secrets import SecretStore


@pytest.fixture
def manager(settings: Settings, secret_store: SecretStore) -> ProviderManager:
    return ProviderManager(settings, secret_store)


def test_local_is_always_available(manager: ProviderManager) -> None:
    assert isinstance(manager.local, OllamaProvider)
    assert manager.local is manager.local  # construído uma vez só


def test_external_is_none_without_a_credential(manager: ProviderManager) -> None:
    assert manager.external is None


def test_external_appears_when_the_credential_exists(
    manager: ProviderManager, secret_store: SecretStore
) -> None:
    secret_store.set("OPENROUTER_API_KEY", "sk-teste")
    assert isinstance(manager.external, OpenRouterProvider)


def test_external_disabled_by_configuration(
    manager: ProviderManager, secret_store: SecretStore
) -> None:
    secret_store.set("OPENROUTER_API_KEY", "sk-teste")
    manager.settings.ai.external_provider = "none"
    assert manager.external is None


def test_model_for_each_role(manager: ProviderManager) -> None:
    assert manager.model_for("local") == "qwen3.5:2b"
    assert manager.model_for("fast") == "qwen3.5:0.8b"
    assert manager.model_for("external") == "google/gemini-3.1-flash-lite"
    assert manager.model_for("heavy") == "anthropic/claude-sonnet-5"


def test_provider_for_roles(manager: ProviderManager, secret_store: SecretStore) -> None:
    assert manager.provider_for("local") is manager.local
    assert manager.provider_for("fast") is manager.local
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        manager.provider_for("external")

    secret_store.set("OPENROUTER_API_KEY", "sk-teste")
    manager.reset()
    assert manager.provider_for("heavy") is manager.external


async def test_reset_picks_up_a_new_credential(
    manager: ProviderManager, secret_store: SecretStore
) -> None:
    assert manager.external is None
    secret_store.set("OPENROUTER_API_KEY", "sk-nova")
    manager.reset()
    assert manager.external is not None
    await manager.aclose()


async def test_health_reports_the_missing_external(manager: ProviderManager) -> None:
    health = {h.name: h for h in await manager.health()}
    await manager.aclose()
    assert "ollama" in health
    assert health["openrouter"].ok is False
    assert "OPENROUTER_API_KEY" in health["openrouter"].detail
