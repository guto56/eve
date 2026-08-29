"""Rotas possíveis para uma entrada do usuário (spec §8)."""

from __future__ import annotations

from enum import StrEnum


class Route(StrEnum):
    CHAT = "chat"
    """Conversa. Responde o modelo local, sem ferramentas."""

    COMMAND = "command"
    """Ação direta no computador. Ferramentas nativas."""

    TASK = "task"
    """Pedido de várias etapas. Vai para agente e modelo externo."""

    WEB = "web"
    """Precisa de informação atual da internet."""

    MEMORY = "memory"
    """Consultar, gravar ou esquecer algo do usuário."""

    SKILL = "skill"
    """Capacidade especializada instalada."""

    MCP = "mcp"
    """Servidor MCP conectado."""

    VOICE = "voice"
    """Fluxo de voz."""

    CALL = "call"
    """Fluxo telefônico."""


#: Rotas que a Fase 5 já resolve. As demais existem no vocabulário e caem em
#: CHAT até a fase que as implementa.
IMPLEMENTED: frozenset[Route] = frozenset(
    {Route.CHAT, Route.COMMAND, Route.TASK, Route.WEB, Route.MEMORY}
)

#: Namespaces de ferramenta relevantes por rota. É o primeiro corte no prompt:
#: oferecer 23 ferramentas custa ~1.900 tokens e segundos de latência.
NAMESPACES: dict[Route, tuple[str, ...]] = {
    Route.CHAT: (),
    Route.COMMAND: ("app", "url", "file", "clipboard", "system", "calendar"),
    Route.WEB: ("web", "url"),
    Route.MEMORY: ("memory",),
    Route.TASK: (
        "app",
        "url",
        "file",
        "clipboard",
        "system",
        "calendar",
        "web",
        "browser",
        "memory",
    ),
    Route.SKILL: (),
    Route.MCP: (),
    Route.VOICE: (),
    Route.CALL: (),
}

#: Papel de modelo que cada rota prefere.
PREFERRED_ROLE: dict[Route, str] = {
    Route.CHAT: "local",
    Route.COMMAND: "local",
    Route.MEMORY: "local",
    Route.WEB: "external",
    Route.TASK: "external",
    Route.SKILL: "local",
    Route.MCP: "local",
    Route.VOICE: "fast",
    Route.CALL: "local",
}
