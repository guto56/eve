from __future__ import annotations

import pytest

from eve.ai.base import ToolCall
from eve.chat.engine import ChatEngine
from eve.events import EventType
from eve.router.router import Router
from eve.tools.bus import ToolBus
from eve.tools.macos_tools import register_macos_tools
from tests.fakes import boom, reply


@pytest.fixture
def engine(tool_bus: ToolBus, fake_providers, event_bus) -> ChatEngine:
    register_macos_tools(tool_bus.registry)
    from eve.tools.builtin import register_builtin_tools

    register_builtin_tools(tool_bus.registry)
    router = Router(tool_bus.registry, fake_providers)
    return ChatEngine(router, fake_providers, tool_bus, event_bus)


async def collect(engine: ChatEngine, texto: str, session: str | None = None) -> list:
    return [e async for e in engine.send(texto, session)]


def kinds(eventos: list) -> list[str]:
    return [e.kind for e in eventos]


def text_of(eventos: list) -> str:
    return "".join(e.data.get("text", "") for e in eventos if e.kind == "delta")


# ------------------------------------------------------------- caminho rápido


async def test_fast_path_executes_without_the_model(engine: ChatEngine, fake_providers) -> None:
    eventos = await collect(engine, "que horas são")
    assert kinds(eventos) == ["session", "routed", "tool", "tool_result", "delta", "done"]
    assert fake_providers.fake.calls == []  # nenhum modelo no caminho
    assert "São " in text_of(eventos)


async def test_fast_path_reports_the_routing(engine: ChatEngine) -> None:
    eventos = await collect(engine, "que horas são")
    routed = next(e for e in eventos if e.kind == "routed")
    assert routed.data["fast_path"] is True
    assert routed.data["decided_by"] == "rule"
    assert routed.data["tool_call"]["name"] == "system.time"


async def test_denied_tool_is_explained(engine: ChatEngine, tool_bus: ToolBus) -> None:
    """Ferramenta que exige confirmação e não recebe resposta vira recusa clara."""
    eventos = await collect(engine, "feche o Finder")  # app.quit é CONFIRM
    resultado = next(e for e in eventos if e.kind == "tool_result")
    assert resultado.data["ok"] is False
    assert resultado.data["error_kind"] == "denied"
    assert "Não fiz" in text_of(eventos)


# ------------------------------------------------------------ caminho modelo


async def test_plain_chat_streams_the_answer(engine: ChatEngine, fake_providers) -> None:
    fake_providers.fake.queue.append(reply("Oi! Tudo certo por aqui."))
    eventos = await collect(engine, "oi, tudo bem?")
    assert text_of(eventos) == "Oi! Tudo certo por aqui."
    assert len([e for e in eventos if e.kind == "delta"]) > 1  # veio em pedaços


async def test_chat_route_offers_no_tools(engine: ChatEngine, fake_providers) -> None:
    fake_providers.fake.queue.append(reply("olá"))
    await collect(engine, "oi")
    assert fake_providers.fake.calls[-1]["tools"] == []


async def test_model_calls_a_tool_and_then_answers(
    engine: ChatEngine, fake_providers, sandbox
) -> None:
    alvo = sandbox / "nova"
    fake_providers.fake.queue.extend(
        [
            reply("COMMAND"),  # classificação
            reply("", [ToolCall("file.mkdir", {"path": str(alvo)})]),
            reply("Pronto, criei a pasta."),
        ]
    )
    eventos = await collect(engine, "faça uma pasta ali no canto")
    sequencia = kinds(eventos)
    assert sequencia[:2] == ["session", "routed"]
    assert sequencia[2:4] == ["tool", "tool_result"]
    assert sequencia[-1] == "done"
    assert alvo.is_dir()
    assert "criei a pasta" in text_of(eventos)


async def test_only_the_selected_tools_reach_the_model(engine: ChatEngine, fake_providers) -> None:
    fake_providers.fake.queue.extend([reply("COMMAND"), reply("ok")])
    await collect(engine, "faça uma pasta chamada projetos ali")
    oferecidas = fake_providers.fake.calls[-1]["tools"]
    assert len(oferecidas) == 8  # e não as 23 registradas
    assert "file__mkdir" in oferecidas


async def test_tool_failure_goes_back_to_the_model_as_text(
    engine: ChatEngine, fake_providers, sandbox
) -> None:
    fake_providers.fake.queue.extend(
        [
            reply("COMMAND"),
            reply("", [ToolCall("file.read", {"path": "/etc/passwd"})]),
            reply("Não consegui ler esse arquivo."),
        ]
    )
    eventos = await collect(engine, "leia um arquivo qualquer aí")
    resultado = next(e for e in eventos if e.kind == "tool_result")
    assert resultado.data["error_kind"] == "not_permitted"
    # A conversa continua: o modelo recebeu o erro e respondeu.
    assert "Não consegui" in text_of(eventos)


async def test_empty_final_answer_falls_back_to_describing_the_action(
    engine: ChatEngine, fake_providers, sandbox
) -> None:
    """O modelo agiu e ficou mudo; a EVE conta o que foi feito."""
    alvo = sandbox / "silenciosa"
    fake_providers.fake.queue.extend(
        [
            reply("COMMAND"),
            reply("", [ToolCall("file.mkdir", {"path": str(alvo)})]),
            reply(""),  # sem nada a dizer
        ]
    )
    eventos = await collect(engine, "faça uma pasta ali no canto")
    assert "Pasta criada" in text_of(eventos)


async def test_tool_loop_is_bounded(engine: ChatEngine, fake_providers, sandbox) -> None:
    chamada = ToolCall("file.mkdir", {"path": str(sandbox / "x")})
    fake_providers.fake.queue.append(reply("COMMAND"))
    for _ in range(10):
        fake_providers.fake.queue.append(reply("", [chamada]))
    eventos = await collect(engine, "faça uma pasta ali no canto")
    assert "Parei por aqui" in text_of(eventos)
    assert len([e for e in eventos if e.kind == "tool"]) == engine.max_rounds - 1


async def test_provider_error_becomes_an_error_event(engine: ChatEngine, fake_providers) -> None:
    fake_providers.fake.queue.extend([reply("CHAT"), boom("ollama caiu", "unavailable")])
    eventos = await collect(engine, "me conte uma coisa qualquer sobre isso")
    erro = next(e for e in eventos if e.kind == "error")
    assert erro.data["kind"] == "unavailable"
    assert "done" not in kinds(eventos)  # a mensagem não completou


# ------------------------------------------------------------------ sessões


async def test_session_is_reused_and_accumulates(engine: ChatEngine, fake_providers) -> None:
    fake_providers.fake.queue.extend([reply("primeira"), reply("segunda")])
    primeira = await collect(engine, "oi")
    sid = primeira[0].data["session"]
    await collect(engine, "bom dia", sid)

    session = engine.sessions.get(sid)
    assert session is not None
    assert [m.role for m in session.messages] == ["user", "assistant", "user", "assistant"]


async def test_history_is_sent_to_the_model(engine: ChatEngine, fake_providers) -> None:
    fake_providers.fake.queue.extend([reply("olá"), reply("de novo")])
    primeira = await collect(engine, "oi")
    sid = primeira[0].data["session"]
    await collect(engine, "e aí", sid)

    enviadas = fake_providers.fake.calls[-1]["messages"]
    assert [m.content for m in enviadas if m.role == "user"] == ["oi", "e aí"]


# ------------------------------------------------------------------ eventos


async def test_everything_shows_up_on_the_bus(engine: ChatEngine, event_bus) -> None:
    with event_bus.subscribe() as sub:
        await collect(engine, "que horas são")
        tipos = []
        while not sub.queue.empty():
            tipos.append(sub.queue.get_nowait().type)
    assert EventType.MESSAGE_RECEIVED in tipos
    assert EventType.ROUTER_DECIDED in tipos
    assert EventType.TOOL_COMPLETED in tipos
    assert EventType.MESSAGE_COMPLETED in tipos


async def test_router_event_does_not_collide_with_the_event_source(
    engine: ChatEngine, event_bus
) -> None:
    """Regressão: `decision.as_dict()` já teve uma chave `source` que colidia."""
    with event_bus.subscribe(["router.*"]) as sub:
        await collect(engine, "que horas são")
        evento = sub.queue.get_nowait()
    assert evento.source == "api"
    assert evento.payload["decided_by"] == "rule"


async def test_explicit_memory_turn_does_not_trigger_extraction(
    engine: ChatEngine, tool_bus: ToolBus
) -> None:
    """Regressão: o caminho rápido gravava, e a extração gravava de novo,
    reescrito — perto demais para ser útil, longe demais para o deduplicador."""
    from unittest.mock import AsyncMock

    from eve.tools.memory_tools import register_memory_tools

    register_memory_tools(tool_bus.registry)
    memoria = AsyncMock()
    memoria.context_for = AsyncMock(return_value="")
    memoria.remember = AsyncMock(return_value=(None, "nova"))
    # O cofre é síncrono de propósito (roda em thread). Um AsyncMock aqui
    # devolveria corrotina onde o código espera um caminho.
    memoria.vault = None
    tool_bus.services["memory"] = memoria
    engine.memory = memoria

    await collect(engine, "lembre que eu prefiro reuniões de manhã")
    await engine.drain()

    memoria.remember.assert_awaited()
    memoria.extract.assert_not_awaited()


async def test_so_conversa_gera_memoria(engine: ChatEngine, fake_providers) -> None:
    """Pedir pesquisa ou tarefa não é contar algo sobre si."""
    from unittest.mock import AsyncMock

    memoria = AsyncMock()
    memoria.context_for = AsyncMock(return_value="")
    # Sem cofre: o registro da conversa é síncrono e não é o que este teste vê.
    memoria.vault = None
    engine.memory = memoria

    fake_providers.fake.queue.extend([reply("WEB"), reply("achei isso")])
    await collect(engine, "descubra o preço do dólar agora mesmo")
    await engine.drain()
    memoria.extract.assert_not_awaited()

    fake_providers.fake.queue.extend([reply("olá!")])
    await collect(engine, "oi, tudo bem?")
    await engine.drain()
    memoria.extract.assert_awaited()


async def test_chamada_invalida_repetida_e_recusada(
    engine: ChatEngine, fake_providers, sandbox
) -> None:
    """Regressão: `memory.recall` com limite inválido virou laço até estourar
    as rodadas, sem resposta nenhuma para o usuário."""
    ruim = ToolCall("eve.echo", {})  # falta `message`, já registrada pela fixture
    fake_providers.fake.queue.append(reply("COMMAND"))
    for _ in range(8):
        fake_providers.fake.queue.append(reply("", [ruim]))

    eventos = await collect(engine, "faça algo que vai dar errado sempre")
    tentativas = [e for e in eventos if e.kind == "tool"]
    assert len(tentativas) == 2  # tentou duas vezes; depois nem chamou


async def test_contar_um_fato_nao_vira_busca(engine: ChatEngine) -> None:
    """Regressão: "meu irmão é dentista" ia para MEMORY, o modelo escolhia
    `memory.recall`, não achava nada e respondia "não tenho essa informação" —
    para uma frase que o usuário acabara de dizer."""
    from eve.router.routes import Route
    from eve.router.rules import apply_rules

    achado = apply_rules("meu irmão se chama Bruno e ele é dentista em Contagem")
    assert achado is not None
    assert achado.route is Route.CHAT

    # Pergunta continua sendo pergunta, mesmo com "meu" na frente.
    assert apply_rules("meu irmão trabalha onde") is None
    assert apply_rules("onde meu irmão trabalha") is None


async def test_extracao_segue_o_que_aconteceu_e_nao_a_rota(engine: ChatEngine) -> None:
    """Se nada gravou, ainda pode haver fato a guardar; se algo gravou, não."""
    from unittest.mock import AsyncMock

    from eve.ai.base import assistant, user
    from eve.router.router import RoutingDecision
    from eve.router.routes import Route

    memoria = AsyncMock()
    memoria.vault = None
    engine.memory = memoria
    decisao = RoutingDecision(route=Route.MEMORY, decided_by="rule", reason="teste")
    engine.sessions.get_or_create("s").add(user("meu irmão é dentista"))
    sessao = engine.sessions.get_or_create("s")
    sessao.add(assistant("Entendi."))

    engine._schedule_extraction(sessao, decisao, set())
    await engine.drain()
    memoria.extract.assert_awaited()

    memoria.extract.reset_mock()
    engine._schedule_extraction(sessao, decisao, {"memory.remember"})
    await engine.drain()
    memoria.extract.assert_not_awaited()
