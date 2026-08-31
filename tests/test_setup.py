"""O setup e o modo que ele grava.

O que precisa de teste aqui é o que quebra calado: um papel que deixa de
apontar para a nuvem no modo externo volta a procurar um Ollama que não
existe, e a EVE emudece sem dizer por quê.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
from typer.testing import CliRunner

from eve.ai.manager import ProviderManager
from eve.cli import ask
from eve.cli.main import app
from eve.config import Settings, load_settings
from eve.secrets import InMemoryBackend, SecretStore

runner = CliRunner()


def _manager(modo: str, com_chave: bool = True) -> ProviderManager:
    settings = Settings()
    settings.ai.mode = modo
    store = SecretStore(
        pathlib.Path(tempfile.mkdtemp()) / "s.json",
        backend=InMemoryBackend(),
        allow_env_fallback=False,
    )
    if com_chave:
        store.set("OPENROUTER_API_KEY", "sk-teste")
    return ProviderManager(settings, store)


def test_modo_externo_manda_todo_papel_para_a_nuvem() -> None:
    manager = _manager("external")
    for papel in ("local", "fast", "external", "heavy"):
        assert type(manager.provider_for(papel)).__name__ == "OpenRouterProvider"
    ai = manager.settings.ai
    # Classificar e frasear rodam a cada mensagem: sem modelo local, o papel
    # rápido vai para o modelo pequeno, não para o de responder nem para o caro.
    assert manager.model_for("fast") == ai.external_fast_model
    assert manager.model_for("local") == ai.external_fast_model
    assert manager.model_for("external") == ai.external_model
    assert manager.model_for("heavy") == ai.external_heavy_model
    assert ai.external_fast_model not in (ai.external_model, ai.external_heavy_model)


def test_modo_hibrido_continua_usando_o_local() -> None:
    manager = _manager("hybrid")
    assert type(manager.provider_for("fast")).__name__ == "OllamaProvider"
    assert type(manager.provider_for("heavy")).__name__ == "OpenRouterProvider"
    assert manager.model_for("fast") == manager.settings.ai.local_fast_model


def test_modo_externo_sem_chave_falha_dizendo_o_que_falta() -> None:
    from eve.ai.base import ProviderError

    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        _manager("external", com_chave=False).provider_for("local")


def test_setup_sem_terminal_nao_pendura_o_instalador() -> None:
    """O `curl | bash` não tem stdin de gente; travar ali seria o pior caso."""
    resultado = runner.invoke(app, ["setup"])
    assert resultado.exit_code == 0
    assert "precisa de um terminal" in resultado.output
    # E não pode ter inventado configuração nenhuma.
    assert load_settings().ai.mode == "hybrid"


def test_perguntas_devolvem_o_padrao_quando_nao_ha_terminal() -> None:
    assert ask.escolher("?", "", [ask.Opcao("a"), ask.Opcao("b")], padrao=1) == 1
    assert ask.sim_ou_nao("?", padrao=True) is True
    assert ask.segredo("CHAVE", "", None) is None
