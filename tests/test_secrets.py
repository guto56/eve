from __future__ import annotations

from pathlib import Path

import pytest

from eve.secrets import (
    InMemoryBackend,
    InvalidSecretName,
    SecretStore,
    build_store,
    mask,
)


def test_valid_and_invalid_names(secret_store: SecretStore) -> None:
    secret_store.set("MINHA_CHAVE", "v")
    for ruim in ("minuscula", "COM-TRACO", "AB", "COM ESPACO", "1COMECA_COM_DIGITO", ""):
        with pytest.raises(InvalidSecretName):
            secret_store.set(ruim, "v")


def test_empty_value_is_rejected(secret_store: SecretStore) -> None:
    with pytest.raises(ValueError, match="vazio"):
        secret_store.set("MINHA_CHAVE", "")


def test_set_get_delete(secret_store: SecretStore) -> None:
    secret_store.set("MINHA_CHAVE", "segredo")
    assert secret_store.get("MINHA_CHAVE") == "segredo"
    assert secret_store.has("MINHA_CHAVE") is True
    assert secret_store.delete("MINHA_CHAVE") is True
    assert secret_store.get("MINHA_CHAVE") is None
    assert secret_store.has("MINHA_CHAVE") is False


def test_index_only_holds_names_never_values(secret_store: SecretStore) -> None:
    secret_store.set("MINHA_CHAVE", "valor-ultrassecreto")
    conteudo = secret_store.index_path.read_text()
    assert "MINHA_CHAVE" in conteudo
    assert "valor-ultrassecreto" not in conteudo


def test_index_survives_a_new_store(tmp_path: Path) -> None:
    backend = InMemoryBackend()
    index = tmp_path / "secrets.json"
    SecretStore(index, backend=backend, allow_env_fallback=False).set("MINHA_CHAVE", "v")
    outra = SecretStore(index, backend=backend, allow_env_fallback=False)
    assert outra.names() == ["MINHA_CHAVE"]


def test_deleting_unknown_key_cleans_the_index(secret_store: SecretStore) -> None:
    assert secret_store.delete("NUNCA_EXISTIU") is False
    assert secret_store.names() == []


def test_corrupted_index_does_not_break_listing(secret_store: SecretStore) -> None:
    secret_store.index_path.parent.mkdir(parents=True, exist_ok=True)
    secret_store.index_path.write_text("{ isso não é json")
    assert secret_store.names() == []


def test_environment_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SecretStore(tmp_path / "s.json", backend=InMemoryBackend(), allow_env_fallback=True)
    monkeypatch.setenv("MINHA_CHAVE", "do-ambiente")
    assert store.get("MINHA_CHAVE") == "do-ambiente"

    sem_fallback = SecretStore(
        tmp_path / "s2.json", backend=InMemoryBackend(), allow_env_fallback=False
    )
    assert sem_fallback.get("MINHA_CHAVE") is None


def test_keychain_beats_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SecretStore(tmp_path / "s.json", backend=InMemoryBackend(), allow_env_fallback=True)
    monkeypatch.setenv("MINHA_CHAVE", "do-ambiente")
    store.set("MINHA_CHAVE", "do-cofre")
    assert store.get("MINHA_CHAVE") == "do-cofre"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, None), ("", None), ("curto", "•••••"), ("sk-abcdefghijkl", "sk-a…ijkl")],
)
def test_mask(valor: str | None, esperado: str | None) -> None:
    assert mask(valor) == esperado


def test_describe_never_leaks_the_value(secret_store: SecretStore) -> None:
    secret_store.set("OPENROUTER_API_KEY", "sk-or-v1-valor-secreto-longo")
    described = {item["name"]: item for item in secret_store.describe()}
    entrada = described["OPENROUTER_API_KEY"]
    assert entrada["configured"] is True
    assert entrada["required"] is True
    assert entrada["hint"] == "sk-o…ongo"
    assert "valor-secreto-longo" not in str(described)


def test_describe_lists_known_secrets_even_when_absent(secret_store: SecretStore) -> None:
    nomes = [item["name"] for item in secret_store.describe()]
    assert "DEEPGRAM_API_KEY" in nomes
    assert all(item["configured"] is False for item in secret_store.describe())


def test_missing_required(secret_store: SecretStore) -> None:
    assert secret_store.missing_required() == ["OPENROUTER_API_KEY"]
    secret_store.set("OPENROUTER_API_KEY", "sk-x")
    assert secret_store.missing_required() == []


# ------------------------------------------------------------------ importação


def test_import_env_file(secret_store: SecretStore, tmp_path: Path) -> None:
    arquivo = tmp_path / "env.txt"
    arquivo.write_text(
        "# comentário\n"
        "\n"
        "OPENROUTER_API_KEY=sk-or-1\n"
        'TAVILY_API_KEY="tvly-2"\n'
        "minuscula=x\n"
        "VAZIA=\n"
        "linha sem igual\n"
    )
    resultado = secret_store.import_env_file(arquivo)
    assert resultado["OPENROUTER_API_KEY"] == "importada"
    assert resultado["TAVILY_API_KEY"] == "importada"
    assert "nome inválido" in resultado["minuscula"]
    assert "vazia" in resultado["VAZIA"]
    assert secret_store.get("TAVILY_API_KEY") == "tvly-2"  # aspas removidas
    assert arquivo.exists()  # o arquivo nunca é apagado


def test_import_does_not_overwrite_by_default(secret_store: SecretStore, tmp_path: Path) -> None:
    secret_store.set("TAVILY_API_KEY", "antiga")
    arquivo = tmp_path / "env.txt"
    arquivo.write_text("TAVILY_API_KEY=nova\n")

    assert secret_store.import_env_file(arquivo)["TAVILY_API_KEY"] == "já existia"
    assert secret_store.get("TAVILY_API_KEY") == "antiga"

    assert secret_store.import_env_file(arquivo, overwrite=True)["TAVILY_API_KEY"] == "importada"
    assert secret_store.get("TAVILY_API_KEY") == "nova"


def test_build_store_uses_memory_backend_in_tests(tmp_path: Path) -> None:
    """O ambiente de teste nunca pode cair no Keychain do usuário."""
    store = build_store(tmp_path / "s.json")
    assert isinstance(store._backend, InMemoryBackend)
    assert store.allow_env_fallback is False
