"""Ferramentas de memória (spec §17).

É por aqui que a rota MEMORY do router deixa de ser um beco sem saída.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from eve.memory.models import MemoryKind
from eve.tools.registry import ToolRegistry
from eve.tools.registry import tool as tool_decorator
from eve.tools.spec import RiskLevel, ToolContext, ToolParams


class RememberParams(ToolParams):
    content: str = Field(
        description="O fato a guardar, em uma frase completa e entendível sozinha.",
        min_length=3,
        max_length=2000,
    )
    kind: str = Field(
        default="semantic",
        description="semantic (fato estável), episodic (algo que aconteceu), "
        "procedural (como fazer algo).",
    )
    importance: float = Field(default=0.6, ge=0.0, le=1.0)


class RecallParams(ToolParams):
    query: str = Field(description="O que procurar na memória.", min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class ForgetParams(ToolParams):
    query: str = Field(description="O que esquecer.", min_length=2, max_length=500)


class EditParams(ToolParams):
    uid: str = Field(description="Identificador da memória, como aparece em memory.recall.")
    content: str = Field(min_length=1, description="O texto corrigido, inteiro.")


class ListParams(ToolParams):
    limit: int = Field(default=10, ge=1, le=50)
    kind: str | None = Field(default=None, description="Filtrar por camada.")


def register_memory_tools(registry: ToolRegistry) -> ToolRegistry:
    @tool_decorator(
        "memory.remember",
        description="Guarda um fato sobre o usuário para lembrar depois.",
        params=RememberParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def remember(params: RememberParams, ctx: ToolContext) -> dict[str, Any]:
        manager = ctx.service("memory")
        try:
            camada = MemoryKind(params.kind)
        except ValueError:
            camada = MemoryKind.SEMANTIC
        memoria, estado = await manager.remember(
            params.content,
            camada,
            importance=params.importance,
            source=ctx.source,
        )
        return {"uid": memoria.uid, "estado": estado, "content": memoria.content}

    @tool_decorator(
        "memory.recall",
        description="Procura na memória o que o usuário já contou.",
        params=RecallParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def recall(params: RecallParams, ctx: ToolContext) -> dict[str, Any]:
        memorias = await ctx.service("memory").recall(params.query, limit=params.limit)
        return {
            "encontradas": [
                {"uid": m.uid, "content": m.content, "kind": m.kind.value} for m in memorias
            ],
            "count": len(memorias),
        }

    @tool_decorator(
        "memory.edit",
        description=(
            "Corrige uma memória existente pelo uid. Use quando o fato mudou — "
            "guardar a versão nova ao lado da velha deixaria as duas valendo."
        ),
        params=EditParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        keywords=("corrigir", "corrija", "editar", "edite", "atualizar", "mudou", "errado"),
    )
    async def editar_memoria(params: EditParams, ctx: ToolContext) -> dict[str, Any]:
        memoria = await ctx.service("memory").editar(params.uid, params.content)
        if memoria is None:
            raise FileNotFoundError(f"não existe memória com uid {params.uid}")
        return {"uid": memoria.uid, "content": memoria.content}

    @tool_decorator(
        "memory.forget",
        description="Apaga da memória o que casar com a busca. Não tem volta.",
        params=ForgetParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
    )
    async def forget(params: ForgetParams, ctx: ToolContext) -> dict[str, Any]:
        apagadas = await ctx.service("memory").forget_matching(params.query)
        return {"apagadas": [m.content for m in apagadas], "count": len(apagadas)}

    @tool_decorator(
        "memory.list",
        description="Lista as memórias mais recentes.",
        params=ListParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def listar(params: ListParams, ctx: ToolContext) -> dict[str, Any]:
        camadas = None
        if params.kind:
            try:
                camadas = [MemoryKind(params.kind)]
            except ValueError:
                camadas = None
        memorias = await ctx.service("memory").store.recent(params.limit, camadas)
        return {
            "memorias": [
                {"uid": m.uid, "content": m.content, "kind": m.kind.value} for m in memorias
            ],
            "count": len(memorias),
        }

    return registry
