"""Modelo de eventos da EVE (spec §28).

Todo estado que a interface, a CLI ou as Skills precisam observar viaja
como evento pelo :mod:`eve.bus`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class EventType:
    """Tipos conhecidos. O barramento aceita qualquer string pontuada."""

    # ciclo de vida do sistema
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPING = "system.stopping"
    SYSTEM_ERROR = "system.error"

    # sessão / interface
    CLIENT_CONNECTED = "client.connected"
    CLIENT_DISCONNECTED = "client.disconnected"

    # roteamento
    ROUTER_DECIDED = "router.decided"

    # conversa
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    MESSAGE_FAILED = "message.failed"
    THINKING = "message.thinking"

    # ferramentas
    TOOL_REQUESTED = "tool.requested"
    TOOL_CONFIRMATION_REQUIRED = "tool.confirmation_required"
    TOOL_APPROVED = "tool.approved"
    TOOL_DENIED = "tool.denied"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    # tarefas de agente
    TASK_STARTED = "task.started"
    TASK_STEP = "task.step"
    TASK_FINISHED = "task.finished"
    TASK_CANCELLED = "task.cancelled"

    # memória, skills, mcp
    MEMORY_WRITTEN = "memory.written"
    SKILL_INSTALLED = "skill.installed"
    MCP_CONNECTED = "mcp.connected"

    # voz
    VOICE_LISTENING = "voice.listening"
    VOICE_TRANSCRIPT = "voice.transcript"
    VOICE_SPEAKING = "voice.speaking"

    # notificações
    NOTIFICATION = "notification"


class Event(BaseModel):
    """Evento imutável publicado no barramento."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str
    ts: float = Field(default_factory=time.time)
    source: str = "core"
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


def matches(pattern: str, event_type: str) -> bool:
    """Casamento de tópicos por segmento.

    ``*`` casa com tudo; ``tool.*`` casa com ``tool.requested`` e
    ``tool.completed``; ``tool.requested`` casa apenas consigo mesmo.
    """
    if pattern == "*":
        return True
    p_parts = pattern.split(".")
    e_parts = event_type.split(".")
    if p_parts[-1] == "*":
        prefix = p_parts[:-1]
        return e_parts[: len(prefix)] == prefix and len(e_parts) > len(prefix)
    return p_parts == e_parts
