"""Memory Manager (spec §18).

A EVE não guarda tudo. Entre a conversa e o banco existe uma decisão: isto
merece sobreviver? É novo? É a mesma coisa que já sei, dita de outro jeito?
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Sequence
from typing import Any

from eve.ai.base import Message, ProviderError, system, user
from eve.ai.manager import ProviderManager
from eve.bus import EventBus
from eve.events import EventType
from eve.logging import get_logger
from eve.memory.embeddings import Embedder
from eve.memory.models import Memory, MemoryKind
from eve.memory.store import MemoryStore

log = get_logger(__name__)

DEDUPE_THRESHOLD = 0.15
"""Distância de cosseno abaixo da qual duas memórias são a mesma coisa.

Calibrado nesta máquina: idêntica 0,00 · paráfrase 0,08 · mesma ideia com
outras palavras 0,23 · relacionada mas diferente 0,24. As duas últimas são
indistinguíveis, então o limiar fica antes delas: funde só o que é quase
certamente repetido. Fundir "marque meus encontros cedo" com "prefiro almoçar
às 13h" perderia informação de verdade."""

MIN_IMPORTANCE = 0.25
"""Abaixo disto não vale ocupar espaço nem contexto."""

MAX_POR_EXTRACAO = 3
"""Teto por conversa. Oito memórias de uma troca não é aprendizado — é ruído."""

MIN_IMPORTANCE_EXTRACAO = 0.5
"""Exigência maior para o que a EVE decide guardar sozinha.

Quando o usuário manda guardar, a intenção basta. Quando a EVE decide, o
custo do erro é dela: memória inútil disputa espaço de contexto com a útil."""

EXTRACTION_SYSTEM = """Você extrai fatos duradouros de uma conversa, para a memória
de um assistente pessoal.

Responda SOMENTE com um array JSON. Cada item: {"content": "...", "kind": "...",
"importance": 0.0-1.0}.

kind:
  semantic    fato estável sobre o usuário, suas preferências ou seus projetos
  episodic    algo que aconteceu, com quando
  procedural  como fazer algo do jeito do usuário

Guarde só o que serviria daqui a semanas. NÃO guarde:
- perguntas, saudações, agradecimentos
- o que a EVE respondeu
- informação passageira ("estou com fome agora")
- qualquer coisa que já seja óbvia

Se nada merecer ser guardado, responda [].

Escreva cada fato como uma frase completa, em terceira pessoa, entendível fora
da conversa. Não use "eu" nem "você"."""


class MemoryManager:
    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        providers: ProviderManager,
        bus: EventBus | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.providers = providers
        self.bus = bus

    async def aclose(self) -> None:
        await self.embedder.aclose()
        await self.store.aclose()

    # ------------------------------------------------------------- gravar

    async def remember(
        self,
        content: str,
        kind: MemoryKind = MemoryKind.SEMANTIC,
        *,
        importance: float = 0.5,
        confidence: float = 0.8,
        source: str = "user",
        session: str | None = None,
        context: dict[str, Any] | None = None,
        dedupe: bool = True,
    ) -> tuple[Memory, str]:
        """Grava. Devolve ``(memória, "nova" | "reforçada" | "descartada")``."""
        content = content.strip()
        if not content:
            raise ValueError("memória vazia")
        if importance < MIN_IMPORTANCE:
            memoria = Memory(content=content, kind=kind, importance=importance)
            return memoria, "descartada"

        embedding = await self.embedder.embed_one(content)

        if dedupe and embedding is not None:
            achado = await self.store.similar(embedding, DEDUPE_THRESHOLD)
            if achado is not None:
                existente, distancia = achado
                reforçada = await self.store.reinforce(existente.uid)
                log.info(
                    "memoria.duplicada",
                    uid=existente.uid,
                    distancia=round(distancia, 4),
                )
                return reforçada or existente, "reforçada"

        memoria = Memory(
            content=content,
            kind=kind,
            importance=importance,
            confidence=confidence,
            source=source,
            session=session,
            context=context or {},
        )
        await self.store.add(memoria, embedding)
        if self.bus is not None:
            await self.bus.emit(
                EventType.MEMORY_WRITTEN,
                source="memory",
                uid=memoria.uid,
                kind=kind.value,
                content=memoria.summary(),
            )
        return memoria, "nova"

    # ----------------------------------------------------------- recuperar

    async def recall(
        self,
        query: str,
        limit: int = 5,
        kinds: Iterable[MemoryKind] | None = None,
        reinforce: bool = True,
    ) -> list[Memory]:
        embedding = await self.embedder.embed_query(query)
        achadas = await self.store.search(query, embedding, limit, kinds)
        if reinforce:
            for memoria in achadas:
                await self.store.reinforce(memoria.uid, importance_boost=0.01)
        return achadas

    async def context_for(self, text: str, limit: int = 4) -> str:
        """Trecho para o prompt do sistema. Vazio quando não há nada relevante.

        Injetar memória irrelevante é pior que não injetar nada: gasta contexto
        e empurra o modelo para fora do assunto.
        """
        memorias = await self.recall(text, limit=limit, reinforce=False)
        if not memorias:
            return ""
        # Parte do que a EVE guarda vem do jeito que o usuário falou, em
        # primeira pessoa. Sem deixar explícito de quem é cada preferência, o
        # modelo responde "prefiro reuniões de manhã" como se fosse dele.
        # Apresentar como fala relatada e entre aspas resolve a atribuição sem
        # depender de o modelo interpretar uma instrução: um "eu" dentro de
        # aspas é claramente do usuário, não da EVE.
        linhas = "\n".join(f'- O usuário disse: "{m.content}"' for m in memorias)
        return f"Do que você lembra de conversas anteriores:\n{linhas}"

    async def forget(self, uid: str) -> bool:
        return await self.store.delete(uid)

    async def forget_matching(self, query: str, limit: int = 5) -> list[Memory]:
        """Apaga o que casar com a consulta. Devolve o que foi apagado."""
        alvos = await self.recall(query, limit=limit, reinforce=False)
        apagadas = [m for m in alvos if await self.store.delete(m.uid)]
        return apagadas

    async def housekeeping(self) -> int:
        return await self.store.forget_expired()

    # ------------------------------------------------------------ extrair

    async def extract(
        self, messages: Sequence[Message], session: str | None = None
    ) -> list[Memory]:
        """Lê uma conversa e grava o que merece ser lembrado."""
        transcricao = _transcribe(messages)
        if not transcricao.strip():
            return []

        try:
            resposta = await self.providers.provider_for("local").chat(
                [system(EXTRACTION_SYSTEM), user(transcricao)],
                model=self.providers.model_for("local"),
                temperature=0,
                max_tokens=600,
            )
        except ProviderError as exc:
            log.warning("memoria.extracao_falhou", error=str(exc))
            return []

        gravadas: list[Memory] = []
        for item in _parse_items(resposta.text)[:MAX_POR_EXTRACAO]:
            if float(item.get("importance", 0.5)) < MIN_IMPORTANCE_EXTRACAO:
                continue
            try:
                memoria, estado = await self.remember(
                    item["content"],
                    MemoryKind(item.get("kind", "semantic")),
                    importance=float(item.get("importance", 0.5)),
                    source="chat",
                    session=session,
                    context={"extraida_em": time.time()},
                )
            except (ValueError, KeyError) as exc:
                log.info("memoria.item_invalido", error=str(exc))
                continue
            if estado == "nova":
                gravadas.append(memoria)
        return gravadas


def _transcribe(messages: Sequence[Message]) -> str:
    """Só o que o usuário disse.

    Incluir as respostas da EVE fazia o extrator tratar as próprias conclusões
    dela como fatos sobre o usuário: uma recomendação de fone virou "o usuário
    valoriza a integração com o ecossistema Apple", que ninguém disse. A
    instrução no prompt não bastava; a informação não pode nem chegar lá.
    """
    return "\n".join(m.content for m in messages if m.role == "user" and m.content.strip())


_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _parse_items(raw: str) -> list[dict[str, Any]]:
    """Extrai o array JSON da resposta, tolerando texto em volta.

    Modelos pequenos costumam embrulhar o JSON em explicação ou em cerca de
    código; falhar aqui não pode custar a memória inteira.
    """
    encontrado = _ARRAY.search(raw)
    if encontrado is None:
        return []
    try:
        dados = json.loads(encontrado.group(0))
    except json.JSONDecodeError:
        log.info("memoria.json_invalido", trecho=raw[:120])
        return []
    if not isinstance(dados, list):
        return []
    return [
        item
        for item in dados
        if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"]
    ]
