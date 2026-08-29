"""O laço do agente (spec §34).

    pedido
      ↓  planejar        um plano curto, para o usuário ver o que vem
      ↓  executar        ferramentas de verdade, com replanejamento adaptativo
      ↓  sintetizar      uma resposta, não um relatório de execução

O plano é inspecionável de propósito: é o que transforma "a EVE está pensando"
em "a EVE vai fazer isto". E o replanejamento é adaptativo — o modelo recebe o
que deu errado e decide o que fazer, em vez de seguir um roteiro que já não
vale.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from eve.agent.task import Step, Task, TaskStatus
from eve.ai.base import (
    Message,
    ProviderError,
    assistant,
    system,
    tool_result,
    user,
)
from eve.ai.manager import ProviderManager
from eve.bus import EventBus
from eve.logging import get_logger
from eve.tools.bus import ToolBus
from eve.tools.spec import ToolResult

log = get_logger(__name__)

MAX_ROUNDS = 12
"""Orçamento de rodadas. Uma tarefa que não termina em doze não vai terminar."""

MAX_REPETICOES = 2
"""Quantas vezes a mesma chamada pode falhar antes de a EVE recusar repeti-la.

Avisar no texto não resolve: numa execução real o modelo repetiu a mesma
chamada inválida sete vezes, ignorando o aviso a cada volta. A recusa é
estrutural — a chamada nem chega à ferramenta."""

MAX_RODADAS_ESTEREIS = 3
"""Rodadas seguidas sem nenhum passo bem-sucedido antes de desistir."""

PLAN_SYSTEM = """Você planeja tarefas para um assistente de macOS.

Responda SOMENTE com um array JSON de 2 a 6 strings, cada uma um passo curto
em português, no infinitivo. Sem numeração, sem explicação, sem texto em volta.

O plano é para o usuário acompanhar, não para você seguir à risca.

Exemplo para "pesquise três fones, compare preços e recomende":
["Pesquisar fones bem avaliados", "Levantar o preço de cada um", "Comparar e recomendar um"]"""

WORK_SYSTEM = """Você é a EVE executando uma tarefa de várias etapas no Mac do usuário.

Trabalhe com as ferramentas disponíveis, um passo de cada vez. Depois de cada
resultado, decida o próximo passo com base no que realmente aconteceu — se algo
falhou, tente outro caminho em vez de repetir o mesmo.

Para buscar informação, prefira `web.search`, que já devolve o conteúdo e as
fontes. Use o navegador só quando precisar interagir com a página.

**Toda página que você abrir, leia.** Abrir sem ler não traz informação
nenhuma, e responder depois disso é inventar com aparência de pesquisa. Se não
conseguiu os dados, diga que não conseguiu em vez de estimar.

Quando tiver o suficiente, pare de usar ferramentas e escreva a resposta final:
o que o usuário queria saber, não um relato do que você fez. Deixe claro o que
veio de fonte e o que é estimativa sua."""


class AgentRunner:
    def __init__(
        self,
        providers: ProviderManager,
        tools: ToolBus,
        bus: EventBus,
        max_rounds: int = MAX_ROUNDS,
    ) -> None:
        self.providers = providers
        self.tools = tools
        self.bus = bus
        self.max_rounds = max_rounds

    async def run(
        self,
        task: Task,
        tool_names: Sequence[str],
        history: Sequence[Message] = (),
    ) -> AsyncIterator[dict[str, Any]]:
        """Executa a tarefa, emitindo o que vai acontecendo."""
        try:
            provider = self.providers.provider_for("external")
            model = self.providers.model_for("external")
        except ProviderError as exc:
            task.finish(TaskStatus.FAILED, error=str(exc))
            yield {"kind": "task_failed", "task": task.as_dict(False), "error": str(exc)}
            return

        wire_tools = self.tools.registry.wire_tools(tool_names) if tool_names else []

        task.plan = await self._plan(task.goal, provider, model)
        task.status = TaskStatus.RUNNING
        await self.bus.emit("task.started", source="agent", task=task.as_dict(False))
        yield {"kind": "task_plan", "task": task.as_dict(False)}

        messages: list[Message] = [
            system(WORK_SYSTEM),
            *history,
            user(task.goal),
        ]

        try:
            async for evento in self._work(task, messages, provider, model, wire_tools):
                yield evento
        except asyncio.CancelledError:
            task.finish(TaskStatus.CANCELLED)
            await self.bus.emit("task.cancelled", source="agent", task=task.as_dict(False))
            raise
        except ProviderError as exc:
            task.finish(TaskStatus.FAILED, error=str(exc))
            yield {"kind": "task_failed", "task": task.as_dict(False), "error": str(exc)}
            return

        await self.bus.emit("task.finished", source="agent", task=task.as_dict(False))
        yield {"kind": "task_done", "task": task.as_dict(False)}

    # ------------------------------------------------------------- plano

    async def _plan(self, goal: str, provider: Any, model: str) -> list[str]:
        try:
            resposta = await provider.chat(
                [system(PLAN_SYSTEM), user(goal)],
                model=model,
                temperature=0,
                max_tokens=400,
            )
        except ProviderError as exc:
            log.info("agente.plano_falhou", error=str(exc))
            return []
        return _parse_plan(resposta.text)

    # ---------------------------------------------------------- execução

    async def _work(
        self,
        task: Task,
        messages: list[Message],
        provider: Any,
        model: str,
        wire_tools: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        falhas: dict[str, int] = {}
        esteries = 0

        for rodada in range(self.max_rounds):
            texto, chamadas = "", []
            async for delta in provider.stream(
                messages, model=model, tools=wire_tools, temperature=0.3
            ):
                if delta.text:
                    texto += delta.text
                    yield {"kind": "delta", "text": delta.text}
                chamadas.extend(delta.tool_calls)

            messages.append(assistant(texto, chamadas))

            if not chamadas:
                task.finish(TaskStatus.DONE, result=texto.strip())
                return

            if rodada == self.max_rounds - 1:
                aviso = (
                    f"Parei em {len(task.steps)} passos sem concluir. "
                    "O que consegui até aqui está acima."
                )
                task.finish(TaskStatus.FAILED, result=texto.strip(), error=aviso)
                yield {"kind": "delta", "text": aviso}
                return

            acertou = False
            for chamada in chamadas:
                assinatura = _assinatura(chamada.name, chamada.arguments)
                if falhas.get(assinatura, 0) >= MAX_REPETICOES:
                    # Nem chama: devolve a recusa e segue. Repetir custa tempo
                    # e orçamento de rodadas sem chance de dar certo.
                    messages.append(tool_result(chamada, _recusa(chamada.name)))
                    continue

                inicio = time.perf_counter()
                yield {
                    "kind": "step_started",
                    "tool": chamada.name,
                    "arguments": chamada.arguments,
                    "index": len(task.steps),
                }
                resultado = await self.tools.call(
                    chamada.name, chamada.arguments, source="agent", caller="agente"
                )
                passo = Step(
                    tool=chamada.name,
                    arguments=chamada.arguments,
                    ok=resultado.ok,
                    error=resultado.error,
                    duration_ms=(time.perf_counter() - inicio) * 1000,
                )
                task.steps.append(passo)
                yield {"kind": "step_done", "step": passo.as_dict(), "index": len(task.steps) - 1}

                if resultado.ok:
                    acertou = True
                else:
                    falhas[assinatura] = falhas.get(assinatura, 0) + 1
                messages.append(
                    tool_result(chamada, _payload(resultado, falhas.get(assinatura, 0)))
                )

            esteries = 0 if acertou else esteries + 1
            if esteries >= MAX_RODADAS_ESTEREIS:
                aviso = (
                    "Não consegui avançar: as últimas tentativas falharam todas. "
                    "Prefiro parar a inventar um resultado."
                )
                task.finish(TaskStatus.FAILED, error=aviso)
                yield {"kind": "delta", "text": aviso}
                return


def _recusa(tool: str) -> str:
    return json.dumps(
        {
            "erro": f"{tool} já falhou com esses mesmos argumentos e não foi chamada de novo.",
            "tipo": "repeticao_recusada",
            "atencao": "Mude os argumentos ou use outra ferramenta. Insistir não vai funcionar.",
        },
        ensure_ascii=False,
    )


def _assinatura(tool: str, argumentos: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(argumentos, sort_keys=True, default=str)}"


def _payload(resultado: ToolResult, repeticoes: int = 0) -> str:
    """O que o modelo vê de volta.

    Quando a mesma chamada falha de novo, o resultado vem com um aviso
    explícito. Sem isso o modelo insiste: numa execução real ele repetiu
    `browser.open` sem argumentos sete vezes seguidas, queimando o orçamento
    de rodadas em erros idênticos.
    """
    if resultado.ok:
        return json.dumps(resultado.value, ensure_ascii=False, default=str)[:6000]
    corpo: dict[str, Any] = {"erro": resultado.error, "tipo": resultado.error_kind}
    if repeticoes >= 2:
        corpo["atencao"] = (
            "Esta mesma chamada já falhou antes. NÃO repita: mude os argumentos "
            "ou use outra ferramenta."
        )
    if resultado.error_kind == "denied":
        corpo["atencao"] = (
            "A ação precisa de autorização do usuário e não foi autorizada. "
            "Siga por um caminho que não exija confirmação."
        )
    return json.dumps(corpo, ensure_ascii=False)


_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _parse_plan(bruto: str) -> list[str]:
    """Extrai o plano, tolerando o modelo embrulhar em texto ou cerca de código.

    Sem plano a tarefa roda igual — ele serve ao usuário, não ao laço.
    """
    encontrado = _ARRAY.search(bruto)
    if encontrado is None:
        return []
    try:
        dados = json.loads(encontrado.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(dados, list):
        return []
    return [item.strip() for item in dados if isinstance(item, str) and item.strip()][:8]
