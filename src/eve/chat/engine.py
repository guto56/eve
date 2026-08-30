"""O laço que junta router, modelos e ferramentas.

    mensagem
      ↓  Router
    caminho rápido?  → executa a ferramenta e responde, sem modelo
      ↓  não
    modelo (streaming, com as ferramentas selecionadas)
      ↓  pediu ferramenta?
    Tool Bus (validação + permissão + confirmação)
      ↓  resultado de volta ao modelo
    resposta final

Tudo que acontece vira evento no barramento, então a interface web, a CLI e as
Skills veem o mesmo fluxo sem que o motor saiba quem está olhando.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from eve.ai.base import (
    Message,
    ProviderError,
    ToolCall,
    assistant,
    system,
    tool_result,
    user,
)
from eve.ai.manager import ProviderManager
from eve.bus import EventBus
from eve.chat.phrasing import PHRASE_SYSTEM, format_result
from eve.chat.prompts import system_prompt
from eve.chat.session import ChatSession, SessionStore
from eve.events import EventType
from eve.logging import get_logger
from eve.router.router import Router, RoutingDecision
from eve.router.routes import Route
from eve.tools.bus import ToolBus
from eve.tools.retry import MAX_REPETICOES, assinatura, payload, recusa
from eve.tools.spec import ToolResult

log = get_logger(__name__)

MAX_TOOL_ROUNDS = 5
"""Uma a mais do que o necessário, para caber uma recuperação de erro."""

#: Só se extrai memória de CONVERSA.
#:
#: Pedir uma ação, uma pesquisa ou uma tarefa não é contar algo sobre si. Sem
#: este corte, "compare o preço destes fones" virava oito memórias inventadas
#: sobre as preferências do usuário — e uma delas depois contaminou a resposta
#: de uma pergunta sem relação nenhuma.
COM_EXTRACAO = frozenset({Route.CHAT})


@dataclass
class ChatEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.data}


class ChatEngine:
    def __init__(
        self,
        router: Router,
        providers: ProviderManager,
        tools: ToolBus,
        bus: EventBus,
        sessions: SessionStore | None = None,
        max_rounds: int = MAX_TOOL_ROUNDS,
        memory: Any = None,
        skills: Any = None,
        agent: Any = None,
        tasks: Any = None,
    ) -> None:
        self.router = router
        self.providers = providers
        self.tools = tools
        self.bus = bus
        self.sessions = sessions or SessionStore()
        self.max_rounds = max_rounds
        self.memory = memory
        self.skills = skills
        self.agent = agent
        self.tasks = tasks
        self._extracoes: set[asyncio.Task[Any]] = set()

    async def send(
        self,
        text: str,
        session_id: str | None = None,
        *,
        source: str = "api",
    ) -> AsyncIterator[ChatEvent]:
        """Processa uma mensagem, emitindo eventos conforme avança."""
        session = self.sessions.get_or_create(session_id)
        started = time.perf_counter()

        session.add(user(text))
        await self.bus.emit(
            EventType.MESSAGE_RECEIVED, source=source, session=session.id, text=text
        )
        yield ChatEvent("session", {"session": session.id})

        decision = await self.router.route(text)
        log.info(
            "router.decidiu",
            rota=decision.route.value,
            por=decision.decided_by,
            regra=decision.rule,
            ms=round(decision.latency_ms, 1),
            ferramentas=len(decision.tools),
            rapido=decision.is_fast_path,
            texto=text[:80],
        )
        await self.bus.emit(
            EventType.ROUTER_DECIDED, source=source, session=session.id, **decision.as_dict()
        )
        yield ChatEvent("routed", decision.as_dict())

        try:
            if decision.is_fast_path:
                async for event in self._fast_path(session, decision, source):
                    yield event
            elif decision.route is Route.TASK and self.agent is not None:
                async for event in self._agent_path(session, decision, text, source):
                    yield event
            else:
                async for event in self._model_path(session, decision, text, source):
                    yield event
        except ProviderError as exc:
            yield await self._fail(session, source, str(exc), exc.kind)
            return
        except Exception as exc:
            log.exception("chat.falhou", session=session.id)
            yield await self._fail(session, source, str(exc), type(exc).__name__)
            return

        self._schedule_extraction(session, decision)

        elapsed = (time.perf_counter() - started) * 1000
        await self.bus.emit(
            EventType.MESSAGE_COMPLETED,
            source=source,
            session=session.id,
            duration_ms=round(elapsed, 2),
        )
        yield ChatEvent("done", {"session": session.id, "duration_ms": round(elapsed, 2)})

    async def _memory_context(self, text: str) -> str:
        if self.memory is None:
            return ""
        limite = self.tools.settings.memory.context_limit
        if limite <= 0:
            return ""
        try:
            return await self.memory.context_for(text, limit=limite)
        except Exception as exc:
            log.warning("chat.memoria_indisponivel", error=str(exc))
            return ""

    def _schedule_extraction(self, session: ChatSession, decision: RoutingDecision) -> None:
        """Extrai memórias em segundo plano: não pode atrasar a resposta."""
        if self.memory is None or not self.tools.settings.memory.auto_extract:
            return
        if len(session.messages) < 2:
            return
        if decision.route not in COM_EXTRACAO:
            return
        tarefa = asyncio.create_task(self._extract(session))
        self._extracoes.add(tarefa)
        tarefa.add_done_callback(self._extracoes.discard)

    async def _extract(self, session: ChatSession) -> None:
        try:
            gravadas = await self.memory.extract(session.history(limit=6), session.id)
        except Exception as exc:
            log.warning("chat.extracao_falhou", error=str(exc))
            return
        if gravadas:
            log.info("memoria.extraidas", count=len(gravadas), session=session.id)

    async def drain(self) -> None:
        """Espera as extrações pendentes. Usado no encerramento e nos testes."""
        if self._extracoes:
            await asyncio.gather(*list(self._extracoes), return_exceptions=True)

    async def _fail(self, session: ChatSession, source: str, error: str, kind: str) -> ChatEvent:
        await self.bus.emit(
            EventType.MESSAGE_FAILED,
            source=source,
            session=session.id,
            error=error,
            kind=kind,
        )
        return ChatEvent("error", {"error": error, "kind": kind})

    # ------------------------------------------------------- caminho rápido

    async def _fast_path(
        self, session: ChatSession, decision: RoutingDecision, source: str
    ) -> AsyncIterator[ChatEvent]:
        call = decision.tool_call
        assert call is not None  # garantido por is_fast_path

        yield ChatEvent("tool", {"name": call.name, "arguments": call.arguments})
        result = await self.tools.call(call.name, call.arguments, source=source, caller="router")
        yield ChatEvent("tool_result", _result_event(call, result))

        texto = await self._phrase(call, result)
        session.add(assistant(texto))
        yield ChatEvent("delta", {"text": texto})

    async def _phrase(self, call: ToolCall, result: ToolResult) -> str:
        """Frase para o usuário: formatador determinístico primeiro."""
        if not result.ok:
            if result.error_kind == "denied":
                return f"Não fiz: {result.error}."
            return f"Não consegui — {result.error}"

        pronta = format_result(call.name, call.arguments, result.value)
        if pronta is not None:
            return pronta
        return await self._phrase_with_model(call, result)

    async def _phrase_with_model(self, call: ToolCall, result: ToolResult) -> str:
        """Último recurso: o modelo `fast` transforma o resultado em frase."""
        payload = json.dumps(result.value, ensure_ascii=False, default=str)[:2000]
        try:
            resposta = await self.providers.provider_for("fast").chat(
                [
                    system(PHRASE_SYSTEM),
                    user(f"Ferramenta: {call.name}\nResultado: {payload}"),
                ],
                model=self.providers.model_for("fast"),
                temperature=0.2,
                max_tokens=120,
            )
        except ProviderError:
            return payload
        return resposta.text.strip() or payload

    # ------------------------------------------------------ caminho agente

    async def _agent_path(
        self,
        session: ChatSession,
        decision: RoutingDecision,
        text: str,
        source: str,
    ) -> AsyncIterator[ChatEvent]:
        """Tarefa de várias etapas: plano visível, passos reais, síntese."""
        task = self.tasks.create(text, session.id)
        yield ChatEvent("task", {"id": task.id, "goal": task.goal})

        # A tarefa roda numa corrotina própria, não no gerador desta conversa:
        # fechar o terminal ou a aba não pode matar um trabalho em andamento.
        # Quem sai perde o acompanhamento; a tarefa segue e fica em `eve task`.
        fila: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        corrotina = asyncio.create_task(
            self._pump_task(task, decision.tools, session.history(), fila)
        )
        self.tasks.track(task, corrotina)

        resposta = ""
        async for evento in _drain(fila):
            kind = evento.pop("kind")
            if kind == "delta":
                resposta += evento["text"]
                await self.bus.emit(
                    EventType.MESSAGE_DELTA, source=source, session=session.id, **evento
                )
                yield ChatEvent("delta", evento)
            elif kind == "step_started":
                yield ChatEvent("tool", {"name": evento["tool"], "arguments": evento["arguments"]})
            elif kind == "step_done":
                passo = evento["step"]
                yield ChatEvent(
                    "tool_result",
                    {
                        "name": passo["tool"],
                        "ok": passo["ok"],
                        "value": None,
                        "error": passo["error"],
                        "error_kind": None if passo["ok"] else "step_failed",
                        "duration_ms": passo["duration_ms"],
                    },
                )
            else:
                yield ChatEvent(kind, evento)

        session.add(assistant(resposta))

    async def _pump_task(
        self,
        task: Any,
        tools: Sequence[str],
        history: Sequence[Message],
        fila: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        try:
            async for evento in self.agent.run(task, tools, history):
                await fila.put(evento)
        finally:
            await fila.put(None)

    # ------------------------------------------------------ caminho modelo

    async def _model_path(
        self,
        session: ChatSession,
        decision: RoutingDecision,
        text: str,
        source: str,
    ) -> AsyncIterator[ChatEvent]:
        provider = self.providers.provider_for(decision.role)  # type: ignore[arg-type]
        model = self.providers.model_for(decision.role)  # type: ignore[arg-type]
        wire_tools = self.tools.registry.wire_tools(decision.tools) if decision.tools else []

        # Contexto de memória entra aqui, não no histórico: é conhecimento
        # sobre o usuário, não algo que alguém disse nesta conversa.
        lembrado = await self._memory_context(text)
        instrucoes = self.skills.instructions_for(text) if self.skills else ""
        extra = "\n\n".join(p for p in (instrucoes, lembrado) if p)
        messages: list[Message] = [
            system(system_prompt(with_tools=bool(wire_tools), extra=extra)),
            *session.history(),
        ]
        ultimo: tuple[ToolCall, ToolResult] | None = None
        falhas: dict[str, int] = {}

        for round_index in range(self.max_rounds):
            texto, chamadas = "", []
            async for delta in provider.stream(
                messages, model=model, tools=wire_tools, temperature=0.4
            ):
                if delta.text:
                    texto += delta.text
                    await self.bus.emit(
                        EventType.MESSAGE_DELTA,
                        source=source,
                        session=session.id,
                        text=delta.text,
                    )
                    yield ChatEvent("delta", {"text": delta.text})
                chamadas.extend(delta.tool_calls)

            resposta = assistant(texto, chamadas)
            session.add(resposta)
            messages.append(resposta)

            if not chamadas:
                if not texto.strip() and ultimo is not None:
                    # O modelo executou a ferramenta e não teve o que dizer.
                    # Melhor descrever o que foi feito do que ficar mudo.
                    fecho = await self._phrase(*ultimo)
                    session.add(assistant(fecho))
                    yield ChatEvent("delta", {"text": fecho})
                return

            if round_index == self.max_rounds - 1:
                aviso = "Parei por aqui: a tarefa exigiu ferramentas demais em sequência."
                session.add(assistant(aviso))
                yield ChatEvent("delta", {"text": aviso})
                return

            for call in chamadas:
                marca = assinatura(call.name, call.arguments)
                if falhas.get(marca, 0) >= MAX_REPETICOES:
                    # Insistir na mesma chamada inválida só queima rodadas.
                    messages.append(tool_result(call, recusa(call.name)))
                    continue

                yield ChatEvent("tool", {"name": call.name, "arguments": call.arguments})
                result = await self.tools.call(
                    call.name, call.arguments, source=source, caller="modelo"
                )
                yield ChatEvent("tool_result", _result_event(call, result))
                if not result.ok:
                    log.info(
                        "ferramenta.falhou",
                        tool=call.name,
                        kind=result.error_kind,
                        erro=str(result.error)[:120],
                    )
                ultimo = (call, result)
                if not result.ok:
                    falhas[marca] = falhas.get(marca, 0) + 1
                mensagem = tool_result(call, payload(result, falhas.get(marca, 0), limite=4000))
                session.add(mensagem)
                messages.append(mensagem)


async def _drain(fila: asyncio.Queue[dict[str, Any] | None]) -> AsyncIterator[dict[str, Any]]:
    """Repassa o que a tarefa produz até ela terminar."""
    while True:
        evento = await fila.get()
        if evento is None:
            return
        yield evento


def _result_event(call: ToolCall, result: ToolResult) -> dict[str, Any]:
    return {
        "name": call.name,
        "ok": result.ok,
        "value": result.value,
        "error": result.error,
        "error_kind": result.error_kind,
        "duration_ms": round(result.duration_ms, 2),
    }


def summarize_tools(names: Sequence[str]) -> str:  # pragma: no cover - utilitário
    return ", ".join(names) if names else "nenhuma"
