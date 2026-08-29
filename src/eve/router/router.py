"""Router: decide o que fazer com uma entrada do usuário (spec §8).

Três camadas, da mais barata para a mais cara:

1. **Regra** — casamento de forma, menos de 1 ms. Quando a regra também
   identifica a ferramenta e os argumentos, a EVE executa direto, sem modelo
   nenhum no caminho.
2. **Modelo local** — classificação de intenção com prompt few-shot, ~500 ms.
   Só roda quando nenhuma regra se aplicou.
3. **Retorno seguro** — se o modelo falhar ou responder algo que não é rótulo,
   a entrada vira conversa. Nunca uma ação.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from eve.ai.base import ProviderError, ToolCall, system, user
from eve.ai.manager import ProviderManager
from eve.logging import get_logger
from eve.router.routes import IMPLEMENTED, PREFERRED_ROLE, Route
from eve.router.rules import apply_rules
from eve.router.toolsets import DEFAULT_LIMIT, select_tools
from eve.tools.registry import ToolRegistry

log = get_logger(__name__)

Source = Literal["rule", "model", "fallback"]

CLASSIFIER_SYSTEM = """Você classifica a intenção do usuário para um assistente de macOS.
Responda SOMENTE com um rótulo, em maiúsculas, sem pontuação e sem explicar.

CHAT     conversa, opinião, pergunta de conhecimento geral
COMMAND  ação direta no computador (abrir, fechar, criar, mover, ajustar)
WEB      precisa de informação atual da internet
MEMORY   lembrar, esquecer ou consultar algo que o USUÁRIO contou antes.
         Não confunda com a memória RAM do computador — isso é COMMAND.
TASK     pedido com várias etapas que exige planejamento

Exemplos:
"abra o Safari" -> COMMAND
"tudo bem?" -> CHAT
"quem ganhou o jogo ontem" -> WEB
"lembre que meu café é sem açúcar" -> MEMORY
"pesquise três notebooks, compare preços e recomende" -> TASK
"crie uma pasta chamada projetos" -> COMMAND
"quanto de RAM tem esse Mac" -> COMMAND
"o que eu te disse ontem sobre o projeto" -> MEMORY"""

LABELS: dict[str, Route] = {
    "CHAT": Route.CHAT,
    "COMMAND": Route.COMMAND,
    "WEB": Route.WEB,
    "MEMORY": Route.MEMORY,
    "TASK": Route.TASK,
}


@dataclass(frozen=True)
class RoutingDecision:
    """``decided_by`` diz *como* a rota foi escolhida — não confundir com o
    ``source`` de um evento, que diz de onde a mensagem veio."""

    route: Route
    decided_by: Source
    reason: str
    tools: tuple[str, ...] = ()
    tool_call: ToolCall | None = None
    rule: str | None = None
    latency_ms: float = 0.0

    @property
    def is_fast_path(self) -> bool:
        """A regra já sabe exatamente o que executar — nenhum modelo envolvido."""
        return self.tool_call is not None

    @property
    def role(self) -> str:
        return PREFERRED_ROLE[self.route]

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "decided_by": self.decided_by,
            "reason": self.reason,
            "rule": self.rule,
            "tools": list(self.tools),
            "tool_call": self.tool_call.as_dict() if self.tool_call else None,
            "fast_path": self.is_fast_path,
            "role": self.role,
            "latency_ms": round(self.latency_ms, 2),
        }


class Router:
    def __init__(
        self,
        registry: ToolRegistry,
        providers: ProviderManager,
        tool_limit: int = DEFAULT_LIMIT,
    ) -> None:
        self.registry = registry
        self.providers = providers
        self.tool_limit = tool_limit

    async def route(self, text: str) -> RoutingDecision:
        started = time.perf_counter()

        hit = apply_rules(text)
        if hit is not None:
            if hit.tool is not None and self.registry.has(hit.tool):
                return RoutingDecision(
                    route=hit.route,
                    decided_by="rule",
                    reason=f"regra {hit.rule} identificou a ação",
                    tool_call=ToolCall(hit.tool, hit.arguments),
                    rule=hit.rule,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            selection = select_tools(self.registry, hit.route, text, self.tool_limit)
            return RoutingDecision(
                route=hit.route,
                decided_by="rule",
                reason=f"regra {hit.rule}; {selection.reason}",
                tools=selection.names,
                rule=hit.rule,
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        route, decided_by, reason = await self._classify(text)
        selection = select_tools(self.registry, route, text, self.tool_limit)
        return RoutingDecision(
            route=route,
            decided_by=decided_by,
            reason=f"{reason}; {selection.reason}",
            tools=selection.names,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def _classify(self, text: str) -> tuple[Route, Source, str]:
        try:
            result = await self.providers.local.chat(
                [system(CLASSIFIER_SYSTEM), user(text)],
                model=self.providers.model_for("local"),
                temperature=0,
                max_tokens=6,
            )
        except ProviderError as exc:
            log.warning("router.modelo_indisponivel", error=str(exc))
            return Route.CHAT, "fallback", f"modelo indisponível ({exc.kind})"

        route = parse_label(result.text)
        if route is None:
            log.info("router.rotulo_invalido", saida=result.text[:60])
            return Route.CHAT, "fallback", f"rótulo não reconhecido: {result.text.strip()[:40]!r}"
        if route not in IMPLEMENTED:  # pragma: no cover - defensivo
            return Route.CHAT, "fallback", f"rota {route.value} ainda não implementada"
        return route, "model", f"modelo classificou como {route.value}"


def parse_label(raw: str) -> Route | None:
    """Extrai o rótulo da resposta do modelo, tolerando ruído em volta."""
    texto = raw.strip().upper()
    for label, route in LABELS.items():
        if label in texto:
            return route
    return None
