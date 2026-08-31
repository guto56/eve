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
    """Cada papel puxa o modelo da configuração dele.

    Comparado com a configuração e não com nomes fixos: os modelos mudam
    quando aparece um mais rápido, e um teste que decora nomes só avisa que
    alguém trocou — não que trocou errado."""
    ai = manager.settings.ai
    assert manager.model_for("local") == ai.local_model
    assert manager.model_for("fast") == ai.local_fast_model
    assert manager.model_for("external") == ai.external_model
    assert manager.model_for("heavy") == ai.external_heavy_model
    # O rápido tem de ser mesmo outro, senão o papel não existe.
    assert ai.local_fast_model != ai.local_model


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
