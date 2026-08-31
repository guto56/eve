"""Configuração da EVE: TOML em ``~/.eve/config.toml`` + variáveis de ambiente.

Precedência: ambiente > arquivo TOML > padrões.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from eve.logging import get_logger
from eve.macos.safepath import DEFAULT_ALLOWED_ROOTS, DEFAULT_DENIED
from eve.paths import paths

log = get_logger(__name__)


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

    mode: Literal["hybrid", "external"] = "hybrid"
    """Onde a EVE pensa.

    ``hybrid``: o modelo local resolve o barato e o repetitivo — classificar a
    intenção, extrair memória, conversa curta — e o externo entra no que é
    difícil. ``external``: nada roda nesta máquina; tudo vai para o OpenRouter.
    Não exige baixar modelo nenhum, mas cada mensagem sai do computador e a
    busca por significado na memória cai para busca textual, porque os
    embeddings são locais.
    """

    local_backend: Literal["ollama", "mlx"] = "ollama"
    ollama_host: str = "http://127.0.0.1:11434"
    local_model: str = "qwen3.5:2b"
    local_fast_model: str = "qwen3.5:0.8b"

    external_provider: Literal["openrouter", "none"] = "openrouter"
    external_base_url: str = "https://openrouter.ai/api/v1"
    external_model: str = "google/gemini-3.5-flash-lite"
    """Resposta do dia a dia. Medido nesta conta, mediana do primeiro token:
    3.5-flash-lite 1026 ms contra 1504 ms do 3.1 — 32% mais rápido, e é onde a
    inteligência ainda importa."""

    external_fast_model: str = "mistralai/ministral-8b-2512"
    """Classificar intenção e transformar resultado em frase.

    Roda a cada mensagem, então é aqui que a latência dói mais. Medido: 484 ms
    por classificação contra 1290 ms do gemini-3.1-flash-lite, com a mesma
    precisão (4/6) e a um décimo do preço de saída. Um modelo pequeno basta
    para escolher entre cinco rótulos."""

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


class VoiceSettings(BaseModel):
    """Voz (spec §13)."""

    stt_model: str = "nova-3"
    stt_language: str = "pt-BR"
    input_sample_rate: int = Field(default=16000, gt=0)
    endpointing_ms: int = Field(default=300, ge=10, le=3000)

    tts_model: str = "sonic-3"
    tts_language: str = "pt"
    output_sample_rate: int = Field(default=24000, gt=0)

    live_engine: Literal["nativo", "openrouter", "gemini"] = "openrouter"
    """Quem conduz a conversa ao vivo.

    ``openrouter``: Deepgram ouve, o modelo do OpenRouter pensa, Cartesia fala.
    São três peças, mas passam pelo motor de conversa da EVE — então rota,
    ferramentas e memória são os mesmos do chat.

    ``gemini``: um modelo só, que ouve e fala. Menos latência por não trocar de
    mãos, e exige uma GOOGLE_API_KEY à parte.

    ``nativo``: o mesmo que ``openrouter``, mas quem ouve é o navegador. A
    transcrição chega pronta, então pelo socket sobe texto em vez de áudio e o
    Deepgram sai da conta. A resposta continua na voz do Cartesia."""

    live_model: str = "gemini-3.1-flash-live-preview"
    """Modelo da conversa ao vivo: um só, que ouve e fala, sem STT nem TTS no meio.

    O par Deepgram+Cartesia continua sendo o caminho padrão da voz; este é o da
    página de conversa, onde o que importa é a resposta vir rápido."""

    live_voice: str = "Aoede"
    """Voz do Gemini Live. Outras: Puck, Charon, Kore, Fenrir."""

    live_input_rate: int = 16000
    live_output_rate: int = 24000
    """Taxas exigidas pelo Live API: 16 kHz na entrada, 24 kHz na saída."""

    settle_ms: int = 450
    """Quanto esperar, depois de a frase fechar, para ver se ainda vem mais.

    O Deepgram fecha a frase a cada silêncio curto, e um pedido composto tem
    silêncio no meio: "abre o YouTube" — respiro — "e em outra aba pesquisa o
    dólar" chegava como duas conversas, e a segunda, sozinha, é só uma
    pesquisa. Custa esses milissegundos no começo da resposta; custaria a
    intenção inteira não esperar."""

    barge_in: bool = True
    """Interromper a fala da EVE quando o usuário começa a falar."""


class PhoneSettings(BaseModel):
    """Falar com a EVE por telefone (spec §32).

    O ponto delicado não é o áudio: é que atender o telefone significa deixar
    algo da EVE alcançável pela internet. Por isso a telefonia sobe num app
    próprio, numa porta própria, com as rotas do Twilio e mais nada — a API
    continua só em 127.0.0.1, onde sempre esteve.
    """

    enabled: bool = False
    port: int = Field(default=4243, ge=1, le=65535)
    """Porta do app de telefonia. É esta que o túnel expõe, nunca a do Core."""

    allowed_callers: list[str] = Field(default_factory=list)
    """Quem pode falar com a EVE, em E.164 (``+5531999998888``).

    Lista vazia recusa todo mundo, de propósito. Um número de telefone é
    público por natureza: sem lista, quem descobrisse o número conversaria com
    o seu assistente e leria a sua memória."""

    public_url: str = ""
    """Endereço público do túnel, como o Twilio o vê (``https://algo.trycloudflare.com``).

    O Twilio assina a URL pública, não a que chega aqui: atrás de um túnel o
    host muda no caminho, e conferir a assinatura contra o endereço local
    reprovaria toda chamada legítima. Vazio deduz pelos cabeçalhos."""

    number_sid: str = ""
    """O número do Twilio que a EVE atende, para reapontar sozinha.

    Guardado porque o endereço do túnel muda a cada vez que ele sobe: sem o
    identificador do número, seria preciso colar a URL nova no painel do
    Twilio a cada reinício."""

    number: str = ""
    """O número em si, só para mostrar na tela."""

    greeting: str = "Oi, aqui é a EVE. Pode falar."
    sample_rate: int = 8000
    """A linha telefônica é 8 kHz μ-law. Não é escolha, é o que a rede entrega."""


class WatchSettings(BaseModel):
    """Um caminho observado (spec §30)."""

    name: str
    path: str
    enabled: bool = True


class ProactiveSettings(BaseModel):
    """Quando a EVE pode tomar a iniciativa (spec §29, §33)."""

    enabled: bool = True
    watch_apps: bool = False
    """Observar aplicativos abrindo e fechando. Desligado por padrão: é muito
    evento para pouca utilidade até existir uma regra que use isso."""
    rules: dict[str, str] = Field(default_factory=dict)
    quiet_hours: list[int] | None = None
    """Faixa ``[início, fim]`` em que nada interrompe. Ex.: ``[22, 8]``."""
    min_interval: float = Field(default=60.0, ge=0)

    @field_validator("quiet_hours")
    @classmethod
    def _duas_horas_validas(cls, valor: list[int] | None) -> list[int] | None:
        """Configuração torta desliga o silêncio; não derruba o programa.

        ``quiet_hours = [22]`` chegava inteiro até quem desempacota em dois
        nomes, e aí o erro aparecia num `eve watch status` — longe da causa e
        parecendo defeito do comando.
        """
        if valor is None:
            return None
        if len(valor) != 2 or not all(0 <= h <= 23 for h in valor):
            log.warning("config.quiet_hours_invalido", valor=valor)
            return None
        return valor


class MCPServerSettings(BaseModel):
    """Servidor MCP avulso, fora de qualquer Skill (spec §20)."""

    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    enabled: bool = True


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
    voice: VoiceSettings = VoiceSettings()
    phone: PhoneSettings = PhoneSettings()
    mcp: list[MCPServerSettings] = Field(default_factory=list)
    proactive: ProactiveSettings = ProactiveSettings()
    watch: list[WatchSettings] = Field(default_factory=list)

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
