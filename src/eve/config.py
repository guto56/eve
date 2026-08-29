"""Configuração da EVE: TOML em ``~/.eve/config.toml`` + variáveis de ambiente.

Precedência: ambiente > arquivo TOML > padrões.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from eve.macos.safepath import DEFAULT_ALLOWED_ROOTS, DEFAULT_DENIED
from eve.paths import paths


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4242, ge=1, le=65535)


class LogSettings(BaseModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
    json_format: bool = False


class AISettings(BaseModel):
    """Modelos e provedores.

    Os padrões locais saem de um benchmark nesta classe de máquina: o
    qwen3.5:2b acertou 13/13 na classificação de intenção em ~510 ms, contra
    11/13 do 0.8b e 1372 ms do 4b.
    """

    local_backend: Literal["ollama", "mlx"] = "ollama"
    ollama_host: str = "http://127.0.0.1:11434"
    local_model: str = "qwen3.5:2b"
    local_fast_model: str = "qwen3.5:0.8b"

    external_provider: Literal["openrouter", "none"] = "openrouter"
    external_base_url: str = "https://openrouter.ai/api/v1"
    external_model: str = "google/gemini-3.1-flash-lite"
    external_heavy_model: str = "anthropic/claude-sonnet-5"

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    request_timeout: float = Field(default=180.0, gt=0)


class FileSettings(BaseModel):
    """Cerca do sistema de arquivos para as ferramentas de arquivo."""

    allowed_roots: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_ROOTS))
    denied: list[str] = Field(default_factory=lambda: list(DEFAULT_DENIED))
    max_read_bytes: int = Field(default=1_048_576, gt=0)


class MemorySettings(BaseModel):
    """Memória persistente (spec §17, §18)."""

    embedding_model: str = "embeddinggemma"
    embedding_dimensions: int = Field(default=768, gt=0)
    context_limit: int = Field(default=4, ge=0, le=20)
    """Quantas memórias entram no prompt do sistema a cada mensagem."""
    auto_extract: bool = True
    """Extrair memórias das conversas automaticamente."""


class PermissionSettings(BaseModel):
    """Política de permissões (spec §11).

    ``overrides`` aceita nome exato, padrão de namespace (``file.*``) ou ``*``.
    ``grants`` libera ferramentas PRIVILEGED, uma a uma e sempre por escrito.
    """

    overrides: dict[str, str] = Field(default_factory=dict)
    grants: dict[str, bool] = Field(default_factory=dict)
    confirm_timeout: float = Field(default=120.0, gt=0)


class Settings(BaseSettings):
    """Configuração raiz.

    Sobrescrita por ambiente com prefixo ``EVE_`` e ``__`` para aninhar,
    por exemplo ``EVE_SERVER__PORT=9000``.
    """

    model_config = SettingsConfigDict(
        env_prefix="EVE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerSettings = ServerSettings()
    log: LogSettings = LogSettings()
    ai: AISettings = AISettings()
    permissions: PermissionSettings = PermissionSettings()
    files: FileSettings = FileSettings()
    memory: MemorySettings = MemorySettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # O ambiente vence o arquivo: o TOML entra como ``init_settings``.
        return (env_settings, init_settings)


def read_config_file(path: Path | None = None) -> dict[str, Any]:
    target = path or paths().config_file
    if not target.exists():
        return {}
    with target.open("rb") as fh:
        return tomllib.load(fh)


def load_settings(path: Path | None = None) -> Settings:
    return Settings(**read_config_file(path))


def write_config_file(data: dict[str, Any], path: Path | None = None) -> Path:
    """Grava a configuração de forma atômica (escreve ao lado e renomeia)."""
    import tomli_w

    target = path or paths().config_file
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".toml.tmp")
    tmp.write_bytes(tomli_w.dumps(data).encode("utf-8"))
    tmp.replace(target)
    return target


def update_config_file(
    mutate: Callable[[dict[str, Any]], None], path: Path | None = None
) -> dict[str, Any]:
    """Lê, aplica ``mutate`` e regrava o config.toml, preservando o resto."""
    data = read_config_file(path)
    mutate(data)
    write_config_file(data, path)
    return data
