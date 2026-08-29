"""WebSocket de tempo real entre o Core e seus clientes (spec §27).

Protocolo, do servidor para o cliente:

* ``{"type": "hello", ...}`` logo após a conexão;
* ``{"type": "event", "event": {...}}`` para cada evento do barramento;
* ``{"type": "pong"}`` em resposta a ``{"op": "ping"}``;
* ``{"type": "error", "message": ...}`` quando a mensagem do cliente é inválida.

Do cliente para o servidor:

* ``{"op": "ping"}``
* ``{"op": "subscribe", "patterns": ["tool.*"]}`` — substitui os filtros.
* ``{"op": "chat", "message": "...", "session": "..."}`` — inicia uma conversa;
  cada passo volta como ``{"type": "chat", "kind": ...}`` neste socket, em
  ordem, e também vira evento no barramento para quem estiver observando.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from eve import __version__
from eve.bus import EventBus, Subscription
from eve.events import Event, EventType
from eve.logging import get_logger

log = get_logger(__name__)
router = APIRouter()

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


def _parse_topics(raw: str) -> tuple[str, ...]:
    topics = tuple(t.strip() for t in raw.split(",") if t.strip())
    return topics or ("*",)


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    topics: str = Query(default="*", description="Padrões separados por vírgula"),
    history: int = Query(default=20, ge=0, le=200),
) -> None:
    bus: EventBus = websocket.app.state.bus
    patterns = _parse_topics(topics)
    await websocket.accept()
    # Duas tarefas escrevem neste socket (o bombeamento de eventos e as
    # respostas a comandos); um envio de cada vez.
    lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with lock:
            await websocket.send_json(payload)

    with bus.subscribe(patterns) as sub:
        await send(
            {
                "type": "hello",
                "version": __version__,
                "topics": list(patterns),
                "server_time": asyncio.get_running_loop().time(),
            }
        )
        for past in bus.history(patterns, limit=history):
            await send({"type": "event", "event": past.model_dump(), "replay": True})

        await bus.emit(EventType.CLIENT_CONNECTED, source="ws", topics=list(patterns))

        pump = asyncio.create_task(_pump_events(send, sub))
        conversas: set[asyncio.Task[None]] = set()
        try:
            while True:
                raw = await websocket.receive_text()
                await _handle_client_message(websocket, send, sub, raw, conversas)
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()
            for tarefa in conversas:
                tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await pump
            await asyncio.gather(*conversas, return_exceptions=True)
            await bus.emit(EventType.CLIENT_DISCONNECTED, source="ws")


async def _pump_events(send: SendFn, sub: Subscription) -> None:
    """Entrega ao cliente cada evento que casa com os filtros da assinatura."""
    while True:
        event: Event = await sub.queue.get()
        await send({"type": "event", "event": event.model_dump()})


async def _run_chat(websocket: WebSocket, send: SendFn, message: str, session: str | None) -> None:
    """Roda uma conversa, emitindo cada passo neste socket, em ordem.

    Os mesmos passos também vão para o barramento, para quem estiver
    observando. Mas o cliente que pediu a conversa recebe a sequência completa
    por aqui: o bombeamento de eventos é outra tarefa e poderia entregar o
    "done" antes das ferramentas que o antecederam.
    """
    engine = websocket.app.state.chat
    try:
        async for evento in engine.send(message, session, source="ws"):
            await send({"type": "chat", **evento.as_dict()})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("ws.chat_falhou")
        await send({"type": "chat", "kind": "error", "error": str(exc)})


async def _handle_client_message(
    websocket: WebSocket,
    send: SendFn,
    sub: Subscription,
    raw: str,
    conversas: set[asyncio.Task[None]],
) -> None:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        await send({"type": "error", "message": "JSON inválido"})
        return
    if not isinstance(message, dict):
        await send({"type": "error", "message": "esperado um objeto JSON"})
        return

    op = message.get("op")
    if op == "ping":
        await send({"type": "pong"})
    elif op == "subscribe":
        patterns = message.get("patterns")
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            await send({"type": "error", "message": "patterns deve ser lista de string"})
            return
        sub.patterns = tuple(patterns) or ("*",)
        await send({"type": "subscribed", "topics": list(sub.patterns)})
    elif op == "chat":
        texto = message.get("message")
        if not isinstance(texto, str) or not texto.strip():
            await send({"type": "error", "message": "message deve ser texto não vazio"})
            return
        sessao = message.get("session")
        if sessao is not None and not isinstance(sessao, str):
            await send({"type": "error", "message": "session deve ser texto"})
            return
        tarefa = asyncio.create_task(_run_chat(websocket, send, texto, sessao))
        conversas.add(tarefa)
        tarefa.add_done_callback(conversas.discard)
    else:
        await send({"type": "error", "message": f"operação desconhecida: {op!r}"})
