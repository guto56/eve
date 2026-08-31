"""Escolha e ciclo de vida dos provedores de IA.

Quem chama pede pelo papel ("local", "fast", "external", "heavy"), não pela
implementação — e é isso que deixa o modo ``external`` funcionar sem mudar uma
linha de quem usa: os papéis rápidos passam a apontar para o provedor externo.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from eve.ai.base import Provider, ProviderError, ProviderHealth
from eve.ai.ollama import OllamaProvider
from eve.ai.openrouter import OpenRouterProvider
from eve.config import Settings
from eve.logging import get_logger
from eve.secrets import SecretStore

log = get_logger(__name__)

Role = Literal["local", "fast", "external", "heavy"]


class ProviderManager:
    def __init__(self, settings: Settings, secrets: SecretStore) -> None:
        self.settings = settings
        self.secrets = secrets
        self._local: Provider | None = None
        self._external: Provider | None = None
        self._external_failed: str | None = None

    # ------------------------------------------------------------ acesso

    @property
    def local(self) -> Provider:
        if self._local is None:
            ai = self.settings.ai
            self._local = OllamaProvider(
                host=ai.ollama_host,
                default_model=ai.local_model,
                timeout=ai.request_timeout,
            )
        return self._local

    @property
    def external(self) -> Provider | None:
        """``None`` quando não há credencial — a EVE segue funcionando local."""
        if self._external is not None:
            return self._external
        if self.settings.ai.external_provider == "none":
            return None
        key = self.secrets.get("OPENROUTER_API_KEY")
        if not key:
            self._external_failed = "OPENROUTER_API_KEY não configurada"
            return None
        try:
            self._external = OpenRouterProvider(
                api_key=key,
                default_model=self.settings.ai.external_model,
                base_url=self.settings.ai.external_base_url,
                timeout=self.settings.ai.request_timeout,
            )
        except ProviderError as exc:  # pragma: no cover - só sem chave
            self._external_failed = str(exc)
            return None
        return self._external

    @property
    def external_only(self) -> bool:
        """Não há modelo nesta máquina: todo papel vai para fora."""
        return self.settings.ai.mode == "external"

    def model_for(self, role: Role) -> str:
        ai = self.settings.ai
        if self.external_only:
            # Sem modelo local, cada papel vira um modelo externo diferente: o
            # pequeno para classificar e frasear, que roda a cada mensagem; o
            # médio para responder; o grande só para tarefa difícil.
            return {
                "local": ai.external_fast_model,
                "fast": ai.external_fast_model,
                "external": ai.external_model,
                "heavy": ai.external_heavy_model,
            }[role]
        return {
            "local": ai.local_model,
            "fast": ai.local_fast_model,
            "external": ai.external_model,
            "heavy": ai.external_heavy_model,
        }[role]

    def provider_for(self, role: Role) -> Provider:
        if role in ("local", "fast") and not self.external_only:
            return self.local
        provider = self.external
        if provider is None:
            raise ProviderError(
                self._external_failed or "nenhum provedor externo disponível", "auth"
            )
        return provider

    # ------------------------------------------------------------- saúde

    async def health(self) -> list[ProviderHealth]:
        # No modo externo o Ollama pode nem estar instalado; cobrá-lo seria
        # inventar um problema que o usuário escolheu não ter.
        checks = [] if self.external_only else [self.local.health()]
        external = self.external
        if external is not None:
            checks.append(external.health())
        results = await asyncio.gather(*checks, return_exceptions=True)

        out: list[ProviderHealth] = []
        for result in results:
            if isinstance(result, ProviderHealth):
                out.append(result)
            elif isinstance(result, BaseException):  # pragma: no cover - defensivo
                out.append(ProviderHealth("desconhecido", False, str(result)))
        if external is None and self.settings.ai.external_provider != "none":
            out.append(
                ProviderHealth(
                    "openrouter",
                    False,
                    self._external_failed or "não configurado",
                )
            )
        return out

    def reset(self) -> None:
        """Esquece os provedores para que a próxima chamada os reconstrua.

        Usado quando uma credencial muda: a chave nova precisa valer sem
        reiniciar o Core.
        """
        self._external = None
        self._external_failed = None

    async def aclose(self, free_memory: bool = True) -> None:
        """Fecha os provedores. Por padrão, devolve a RAM do modelo local."""
        if free_memory and self._local is not None:
            descarregar = getattr(self._local, "unload", None)
            if descarregar is not None:
                for modelo in {self.settings.ai.local_model, self.settings.ai.local_fast_model}:
                    await descarregar(modelo)
        for provider in (self._local, self._external):
            if provider is not None:
                await provider.aclose()
        self._local = None
        self._external = None
