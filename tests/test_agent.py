"""Agentes: plano, limites e recusa de repetição."""

from __future__ import annotations

import asyncio

import pytest

from eve.agent.manager import TaskManager
from eve.agent.runner import MAX_RODADAS_ESTEREIS, AgentRunner, _parse_plan
from eve.agent.task import Task, TaskStatus
from eve.ai.base import ToolCall
from eve.tools.bus import ToolBus
from eve.tools.registry import ToolRegistry, tool
from eve.tools.spec import RiskLevel, ToolParams
from tests.fakes import boom, reply

chamadas: list[dict] = []


class EcoParams(ToolParams):
    texto: str


@pytest.fixture(autouse=True)
def _limpa():
    chamadas.clear()
    yield
    chamadas.clear()


@pytest.fixture
def runner(fake_providers, tool_bus: ToolBus, event_bus, secret_store) -> AgentRunner:
    secret_store.set("OPENROUTER_API_KEY", "sk-teste")
    fake_providers.reset()
    fake_providers._external = fake_providers.fake
    return AgentRunner(fake_providers, tool_bus, event_bus, max_rounds=5)


def registra_eco(registry: ToolRegistry, falha: bool = False) -> None:
    @tool("t.eco", description="ecoa", params=EcoParams, risk=RiskLevel.SAFE, registry=registry)
    async def handler(params: EcoParams, ctx) -> dict:
        chamadas.append({"texto": params.texto})
        if falha:
            raise RuntimeError("sempre falha")
        return {"eco": params.texto}


async def executa(runner: AgentRunner, task: Task, tools=("t.eco",)) -> list[dict]:
    return [e async for e in runner.run(task, tools)]


# -------------------------------------------------------------------- plano


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ('["um", "dois"]', ["um", "dois"]),
        ('Claro! ```json\n["um"]\n```', ["um"]),
        ("não sei", []),
        ('[1, 2, "três"]', ["três"]),
        ("[]", []),
    ],
)
def test_plano_tolera_modelo_bagunçado(bruto: str, esperado: list[str]) -> None:
    assert _parse_plan(bruto) == esperado


async def test_plano_aparece_antes_da_execucao(runner: AgentRunner) -> None:
    registra_eco(runner.tools.registry)
    runner.providers.fake.queue.extend([reply('["Primeiro", "Segundo"]'), reply("pronto")])
    task = Task(goal="faça algo")
    eventos = await executa(runner, task)
    assert eventos[0]["kind"] == "task_plan"
    assert task.plan == ["Primeiro", "Segundo"]
    assert eventos[-1]["kind"] == "task_done"


async def test_tarefa_roda_sem_plano(runner: AgentRunner) -> None:
    """O plano serve ao usuário, não ao laço."""
    registra_eco(runner.tools.registry)
    runner.providers.fake.queue.extend([boom("plano falhou"), reply("respondi")])
    task = Task(goal="faça algo")
    await executa(runner, task)
    assert task.plan == []
    assert task.status is TaskStatus.DONE
    assert task.result == "respondi"


# ----------------------------------------------------------------- execução


async def test_passos_sao_registrados(runner: AgentRunner) -> None:
    registra_eco(runner.tools.registry)
    runner.providers.fake.queue.extend(
        [
            reply("[]"),
            reply("", [ToolCall("t.eco", {"texto": "oi"})]),
            reply("terminei"),
        ]
    )
    task = Task(goal="ecoe algo")
    await executa(runner, task)
    assert len(task.steps) == 1
    assert task.steps[0].tool == "t.eco"
    assert task.steps[0].ok is True
    assert chamadas == [{"texto": "oi"}]
    assert task.status is TaskStatus.DONE


async def test_repeticao_da_mesma_falha_e_recusada(runner: AgentRunner) -> None:
    """Avisar no texto não resolve: o modelo insiste. A recusa é estrutural."""
    registra_eco(runner.tools.registry, falha=True)
    chamada = ToolCall("t.eco", {"texto": "sempre igual"})
    runner.providers.fake.queue.append(reply("[]"))
    for _ in range(6):
        runner.providers.fake.queue.append(reply("", [chamada]))
    task = Task(goal="insista")
    await executa(runner, task)
    # A ferramenta só foi chamada até o limite; depois nem chegou nela.
    assert len(chamadas) == 2
    assert task.status is TaskStatus.FAILED


async def test_desiste_depois_de_rodadas_esteries(runner: AgentRunner) -> None:
    registra_eco(runner.tools.registry, falha=True)
    runner.providers.fake.queue.append(reply("[]"))
    for i in range(6):
        runner.providers.fake.queue.append(reply("", [ToolCall("t.eco", {"texto": f"n{i}"})]))
    task = Task(goal="tente sem parar")
    eventos = await executa(runner, task)
    assert task.status is TaskStatus.FAILED
    assert "não consegui avançar" in task.error.lower()
    assert len(task.steps) <= MAX_RODADAS_ESTEREIS + 1
    assert eventos[-1]["kind"] == "task_done" or task.status is TaskStatus.FAILED


async def test_sem_provedor_externo_a_tarefa_falha_com_motivo(
    fake_providers, tool_bus: ToolBus, event_bus
) -> None:
    runner = AgentRunner(fake_providers, tool_bus, event_bus)
    task = Task(goal="qualquer coisa")
    eventos = [e async for e in runner.run(task, ())]
    assert eventos[0]["kind"] == "task_failed"
    assert task.status is TaskStatus.FAILED
    assert "OPENROUTER_API_KEY" in task.error


# ------------------------------------------------------------------ gerente


def test_manager_cria_e_lista() -> None:
    manager = TaskManager(max_tasks=3)
    tarefas = [manager.create(f"objetivo {i}") for i in range(5)]
    assert len(manager) == 3
    assert manager.get(tarefas[0].id) is None
    assert manager.all()[0].id == tarefas[-1].id


def test_cancelar_tarefa_sem_corrotina() -> None:
    manager = TaskManager()
    task = manager.create("algo")
    assert manager.cancel(task.id) is True
    assert task.status is TaskStatus.CANCELLED
    assert manager.cancel(task.id) is False


async def test_cancelar_tarefa_em_andamento() -> None:
    manager = TaskManager()
    task = manager.create("algo demorado")
    corrotina = asyncio.create_task(asyncio.sleep(30))
    manager.track(task, corrotina)
    assert manager.cancel(task.id) is True
    with pytest.raises(asyncio.CancelledError):
        await corrotina


def test_estado_terminal() -> None:
    assert TaskStatus.DONE.finished is True
    assert TaskStatus.RUNNING.finished is False
