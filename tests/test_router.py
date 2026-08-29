from __future__ import annotations

import pytest

from eve.router.router import Router, parse_label
from eve.router.routes import Route
from eve.tools.registry import ToolRegistry
from tests.fakes import boom, reply


@pytest.fixture
def router(full_registry: ToolRegistry, fake_providers) -> Router:
    return Router(full_registry, fake_providers)


async def test_rule_resolves_without_touching_the_model(router: Router, fake_providers) -> None:
    decision = await router.route("abra o Safari")
    assert decision.route is Route.COMMAND
    assert decision.decided_by == "rule"
    assert decision.is_fast_path is True
    assert decision.tool_call.name == "app.open"
    assert decision.tool_call.arguments == {"name": "Safari"}
    assert decision.tools == ()
    assert fake_providers.fake.calls == []  # nenhum modelo foi chamado


async def test_rule_latency_is_negligible(router: Router) -> None:
    decision = await router.route("que horas são")
    assert decision.latency_ms < 20


async def test_rule_without_tool_still_selects_tools(router: Router, fake_providers) -> None:
    decision = await router.route("oi, tudo bem?")
    assert decision.route is Route.CHAT
    assert decision.tools == ()
    assert fake_providers.fake.calls == []


async def test_model_decides_when_no_rule_applies(router: Router, fake_providers) -> None:
    fake_providers.fake.queue.append(reply("COMMAND"))
    decision = await router.route("crie uma pasta chamada projetos")
    assert decision.route is Route.COMMAND
    assert decision.decided_by == "model"
    assert decision.is_fast_path is False
    assert "file.mkdir" in decision.tools
    assert len(fake_providers.fake.calls) == 1


async def test_model_label_tolerates_noise(router: Router, fake_providers) -> None:
    fake_providers.fake.queue.append(reply("Resposta: TASK."))
    decision = await router.route("faça algo elaborado com meu projeto inteiro")
    assert decision.route is Route.TASK


async def test_unrecognized_label_falls_back_to_chat(router: Router, fake_providers) -> None:
    """O padrão seguro é conversar, nunca agir."""
    fake_providers.fake.queue.append(reply("LEMBRE"))
    decision = await router.route("faça algo indecifrável com o projeto")
    assert decision.route is Route.CHAT
    assert decision.decided_by == "fallback"
    assert "não reconhecido" in decision.reason


async def test_provider_failure_falls_back_to_chat(router: Router, fake_providers) -> None:
    fake_providers.fake.queue.append(boom("ollama sumiu"))
    decision = await router.route("faça algo indecifrável com o projeto")
    assert decision.route is Route.CHAT
    assert decision.decided_by == "fallback"
    assert "indisponível" in decision.reason


async def test_rule_for_an_unregistered_tool_degrades_gracefully(fake_providers) -> None:
    """Se a ferramenta da regra não existe no registro, não há caminho rápido."""
    router = Router(ToolRegistry(), fake_providers)
    fake_providers.fake.queue.append(reply("COMMAND"))
    decision = await router.route("abra o Safari")
    assert decision.is_fast_path is False


async def test_decision_serializes_for_the_interface(router: Router) -> None:
    data = (await router.route("abra o Safari")).as_dict()
    assert data["route"] == "command"
    assert data["decided_by"] == "rule"
    assert data["fast_path"] is True
    assert data["tool_call"]["name"] == "app.open"
    assert "source" not in data  # evitando a colisão com o campo de evento


async def test_role_per_route(router: Router, fake_providers) -> None:
    assert (await router.route("oi")).role == "local"
    fake_providers.fake.queue.append(reply("WEB"))
    assert (await router.route("descubra algo obscuro agora")).role == "external"


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("COMMAND", Route.COMMAND),
        ("  chat  ", Route.CHAT),  # tolera caixa e espaço
        ("A resposta é WEB", Route.WEB),
        ("MEMORY.", Route.MEMORY),
        ("blá", None),
        ("", None),
    ],
)
def test_parse_label(bruto: str, esperado: Route | None) -> None:
    assert parse_label(bruto) is esperado
