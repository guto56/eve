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
from eve.memory.sync import Resultado, Sincronizador
from eve.memory.vault import Vault

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

Responda SOMENTE com um array JSON. Cada item:
{"titulo": "...", "content": "...", "kind": "...", "importance": 0.0-1.0,
 "pessoas": ["..."]}

titulo      duas a quatro palavras, como o nome de uma pasta. É o nome da nota
            e é por ele que outras notas vão apontar para esta, então use
            palavras que alguém digitaria de cabeça. Não repita a frase inteira.
pessoas     nomes próprios de gente citada no fato. [] se não houver.

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
da conversa. Não use "eu" nem "você".

Exemplo:
[{"titulo": "Sócia da empresa", "content": "A Marina é sócia do usuário e cuida
do jurídico.", "kind": "semantic", "importance": 0.7, "pessoas": ["Marina"]}]"""


class MemoryManager:
    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        providers: ProviderManager,
        bus: EventBus | None = None,
        vault: Vault | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.providers = providers
        self.bus = bus
        self.vault = vault
        self.sync = Sincronizador(vault, store, embedder) if vault is not None else None
        if vault is not None:
            # As pastas existem desde já: quem abre ~/EVE/Memória no primeiro
            # dia precisa ver a casa, não um diretório vazio.
            vault.preparar()

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
        title: str | None = None,
        pessoas: Sequence[str] = (),
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
        memoria.title = title
        # O arquivo primeiro: se o índice cair, ele é reconstruído a partir do
        # disco; se fosse o contrário, a memória existiria só no banco.
        if self.vault is not None:
            self.vault.escrever(memoria, corpo=self._corpo(memoria, pessoas))
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
        """Esquecer é apagar o arquivo. O índice só acompanha."""
        if self.vault is not None:
            self.vault.apagar(uid)
        return await self.store.delete(uid)

    async def forget_matching(self, query: str, limit: int = 5) -> list[Memory]:
        """Apaga o que casar com a consulta. Devolve o que foi apagado."""
        alvos = await self.recall(query, limit=limit, reinforce=False)
        return [m for m in alvos if await self.forget(m.uid)]

    async def housekeeping(self) -> int:
        """Remove o que expirou — do índice e do disco.

        Sem apagar o arquivo, um rascunho vencido voltaria à vida na próxima
        varredura do cofre, porque lá o arquivo é a verdade.
        """
        if self.vault is None:
            return await self.store.forget_expired()
        vencidas = await self.store.expired_uids()
        for uid in vencidas:
            self.vault.apagar(uid)
        return await self.store.forget_expired()

    def _corpo(self, memoria: Memory, pessoas: Sequence[str]) -> str:
        """O texto da nota, com as pessoas citadas viradas em ligação.

        É o que transforma uma lista de fatos em rede: a nota do fato aponta
        para a nota da pessoa, e a pessoa passa a ter um histórico sem que
        ninguém tenha escrito um histórico.
        """
        if self.vault is None or not pessoas:
            return memoria.content
        nomes = [n for n in dict.fromkeys(pessoas) if n.strip()]
        for nome in nomes:
            self.vault.garantir_pessoa(nome)
        ligacoes = " · ".join(f"[[{nome}]]" for nome in nomes)
        return f"{memoria.content}\n\nPessoas: {ligacoes}"

    # -------------------------------------------------------------- o cofre

    async def reconciliar(self) -> Resultado:
        """Faz o índice concordar com os arquivos. Sem cofre, não há o que fazer."""
        if self.sync is None:
            return Resultado()
        return await self.sync.reconciliar()

    async def vizinhas(self, uid: str, limit: int = 5) -> list[Memory]:
        """As memórias ligadas a esta — o passo seguinte no grafo."""
        return await self.store.vizinhas(uid, limit)

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
                max_tokens=1200,
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
                    title=str(item.get("titulo") or "").strip() or None,
                    pessoas=_nomes(item.get("pessoas")),
                )
            except (ValueError, KeyError) as exc:
                log.info("memoria.item_invalido", error=str(exc))
                continue
            if estado == "nova":
                gravadas.append(memoria)
        return gravadas


def _nomes(bruto: Any) -> tuple[str, ...]:
    """Nomes de gente, tolerando o modelo devolver string em vez de lista."""
    if isinstance(bruto, str):
        bruto = [bruto]
    if not isinstance(bruto, list):
        return ()
    return tuple(str(n).strip() for n in bruto if str(n).strip())


def _objetos_inteiros(bruto: str) -> list[dict[str, Any]]:
    """Salva os objetos de um JSON truncado, contando as chaves.

    Modelos pequenos entregam os objetos certos e esquecem de fechar o array —
    e às vezes o último objeto. Fechar chave e colchete que faltam é pontuação,
    não invenção: nenhum campo é criado, e o que não fizer sentido é descartado
    pelo próprio ``json.loads``.
    """
    itens: list[dict[str, Any]] = []
    profundidade, inicio, em_texto, escapado = 0, None, False, False
    for i, ch in enumerate(bruto):
        if em_texto:
            em_texto = not (ch == '"' and not escapado)
            escapado = ch == "\\" and not escapado
            continue
        if ch == '"':
            em_texto, escapado = True, False
        elif ch == "{":
            if profundidade == 0:
                inicio = i
            profundidade += 1
        elif ch == "}":
            profundidade -= 1
            if profundidade == 0 and inicio is not None:
                try:
                    itens.append(json.loads(bruto[inicio : i + 1]))
                except json.JSONDecodeError:
                    pass
                inicio = None

    if inicio is not None and not em_texto:
        # Sobrou um objeto aberto: tenta fechá-lo com as chaves que faltam.
        cauda = bruto[inicio:].rstrip().rstrip(",")
        for fecho in ("}", "]}", "}}"):
            try:
                itens.append(json.loads(cauda + fecho))
                break
            except json.JSONDecodeError:
                continue
    return itens


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
    # Do primeiro `[` até o fim, e não até o último `]`: o modelo costuma
    # esquecer de fechar o array, e o último `]` do texto é o de "pessoas".
    # Recortar ali cortaria o objeto no meio.
    inicio = raw.find("[")
    bruto = encontrado.group(0) if encontrado else (raw[inicio:] if inicio >= 0 else raw)
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        # Array cortado no meio — o modelo pequeno estourou o limite de tokens.
        # Descartar tudo por causa do último item perderia os que vieram
        # inteiros, que é justamente o trabalho já feito.
        dados = _objetos_inteiros(bruto)
        if not dados:
            log.info("memoria.json_invalido", trecho=raw[:120])
            return []
        log.info("memoria.json_truncado", aproveitados=len(dados))
    if not isinstance(dados, list):
        return []
    return [
        item
        for item in dados
        if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"]
    ]
