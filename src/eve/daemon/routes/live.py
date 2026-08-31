"""A conversa ao vivo: navegador ↔ Gemini Live ↔ Tool Bus.

O navegador manda PCM16 a 16 kHz e recebe PCM16 a 24 kHz — os mesmos formatos
que a página de voz já usava, então do lado dele muda pouco. O que muda é o
meio: em vez de transcrever, pensar e sintetizar, há um modelo só.

As ferramentas não vão para o Gemini executar: ele pede, e quem executa é o
Tool Bus daqui, com permissão e auditoria. É por isso que uma conversa por voz
pode mexer na memória sem virar um caminho paralelo sem regra.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from eve.ai.base import from_wire_name, to_wire_name
from eve.events import EventType
from eve.logging import get_logger
from eve.voice.live import TAXA_ENTRADA, TAXA_SAIDA, LiveError, SessaoLive

log = get_logger(__name__)
router = APIRouter()

#: O que a conversa ao vivo pode fazer. Curto de propósito: numa conversa
#: falada não dá para revisar argumentos antes, então o alcance é o da memória
#: e do que é seguro por natureza.
FERRAMENTAS = (
    "memory.recall",
    "memory.list",
    "memory.remember",
    "memory.edit",
    "memory.forget",
    "eve.about",
    "system.time",
)

#: Chaves que o schema do Gemini não aceita e que o pydantic gera.
FORA_DO_SCHEMA = ("additionalProperties", "$defs", "$schema", "definitions", "title")

INSTRUCOES = """Você é a EVE, assistente pessoal do usuário, falando por voz.

Fale português do Brasil, em tom natural e direto. Frases curtas: quem ouve não
volta atrás para reler. Nada de listar opções nem enumerar — isto é conversa.

Você tem acesso à memória do usuário e pode consultá-la, gravar, corrigir e
apagar. Quando ele contar um fato sobre a vida dele, grave. Quando disser que
algo mudou, corrija a memória existente em vez de gravar outra ao lado — senão
as duas versões passam a valer.

Nunca diga que não tem acesso à memória ou ao computador dele. Se uma ação
precisar de confirmação, ela aparece na tela; diga que está aguardando."""


@router.websocket("/ws/live")
async def live_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    app = websocket.app
    envio = asyncio.Lock()

    async def avisar(payload: dict[str, Any]) -> None:
        async with envio:
            await websocket.send_json(payload)

    async def falar(pcm: bytes) -> None:
        async with envio:
            await websocket.send_bytes(pcm)

    chave = app.state.secrets.get("GOOGLE_API_KEY")
    if not chave:
        await avisar(
            {
                "kind": "error",
                "fatal": True,
                "error": "GOOGLE_API_KEY não configurada",
                "hint": "pegue em aistudio.google.com/apikey e grave com: eve key set "
                "GOOGLE_API_KEY",
            }
        )
        await websocket.close()
        return

    settings = app.state.settings
    try:
        sessao = SessaoLive(
            chave,
            model=settings.voice.live_model,
            voice=settings.voice.live_voice,
            instrucoes=await _instrucoes(app),
            ferramentas=_declaracoes(app.state.tools.registry),
            idioma=settings.voice.tts_language or "pt-BR",
        )
        await sessao.__aenter__()
    except LiveError as exc:
        await avisar({"kind": "error", "fatal": True, "error": str(exc)})
        await websocket.close()
        return

    await avisar(
        {
            "kind": "ready",
            "inputRate": TAXA_ENTRADA,
            "outputRate": TAXA_SAIDA,
            "model": settings.voice.live_model,
            "voice": settings.voice.live_voice,
            "tools": list(FERRAMENTAS),
        }
    )
    await app.state.bus.emit(EventType.VOICE_LISTENING, source="live", on=True)

    # As confirmações continuam sendo do Tool Bus; a página só precisa vê-las
    # para mostrar o cartão. Quem decide responde pela API de sempre.
    with app.state.bus.subscribe(("tool.confirmation_required", "tool.approved")) as pendencias:

        async def repassar_pendencias() -> None:
            while True:
                evento = await pendencias.queue.get()
                await avisar({"kind": "approval", "event": evento.model_dump()})

        tarefas = [
            asyncio.create_task(_do_gemini(sessao, app, avisar, falar)),
            asyncio.create_task(repassar_pendencias()),
        ]
        try:
            while True:
                mensagem = await websocket.receive()
                if mensagem["type"] == "websocket.disconnect":
                    break
                if (pcm := mensagem.get("bytes")) is not None:
                    await sessao.enviar_audio(pcm)
                elif (texto := mensagem.get("text")) is not None:
                    await _do_navegador(sessao, texto)
        except (WebSocketDisconnect, LiveError, RuntimeError) as exc:
            log.info("live.encerrada", motivo=str(exc)[:120])
        finally:
            for tarefa in tarefas:
                tarefa.cancel()
            await asyncio.gather(*tarefas, return_exceptions=True)
            await sessao.fechar()
            await app.state.bus.emit(EventType.VOICE_LISTENING, source="live", on=False)


async def _do_navegador(sessao: SessaoLive, bruto: str) -> None:
    """Comandos em JSON vindos da página. Áudio vem em binário, não por aqui."""
    import json

    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        return
    if dados.get("op") == "texto" and (texto := str(dados.get("text", "")).strip()):
        await sessao.enviar_texto(texto)


async def _do_gemini(sessao: SessaoLive, app: Any, avisar: Any, falar: Any) -> None:
    """Traduz o que vem do modelo e executa o que ele pedir."""
    async for evento in sessao.eventos():
        tipo = evento["tipo"]
        if tipo == "audio":
            await falar(evento["pcm"])
        elif tipo == "ouvi":
            await avisar({"kind": "partial", "text": evento["texto"]})
        elif tipo == "falei":
            await avisar({"kind": "reply", "text": evento["texto"]})
        elif tipo == "interrompido":
            await avisar({"kind": "interrupted"})
        elif tipo == "turno_completo":
            await avisar({"kind": "turn"})
        elif tipo == "ferramentas":
            await _executar(sessao, app, avisar, evento["chamadas"])
        elif tipo in ("erro", "fechada"):
            await avisar({"kind": "error", "error": evento.get("erro") or evento.get("motivo", "")})
            return


async def _executar(sessao: SessaoLive, app: Any, avisar: Any, chamadas: list[dict]) -> None:
    """Roda no Tool Bus e devolve ao modelo, uma resposta por chamada.

    Toda chamada é respondida, inclusive a que falhou: sem a resposta o modelo
    fica esperando por ela e a conversa emudece.
    """
    respostas = []
    for chamada in chamadas:
        nome = from_wire_name(chamada["nome"])
        await avisar({"kind": "tool", "name": nome, "arguments": chamada["args"]})
        resultado = await app.state.tools.call(nome, chamada["args"], source="live", caller="voz")
        await avisar(
            {
                "kind": "tool_result",
                "name": nome,
                "ok": resultado.ok,
                "error": resultado.error,
            }
        )
        respostas.append(
            {
                "id": chamada["id"],
                "name": chamada["nome"],
                "response": (
                    {"resultado": resultado.value}
                    if resultado.ok
                    else {"erro": resultado.error, "tipo": resultado.error_kind}
                ),
            }
        )
    await sessao.responder_ferramentas(respostas)


def _declaracoes(registry: Any) -> list[dict[str, Any]]:
    """Ferramentas da EVE no formato do Gemini, com o schema podado."""
    declaracoes = []
    for nome in FERRAMENTAS:
        try:
            spec = registry.get(nome)
        except Exception:
            continue
        declaracoes.append(
            {
                "name": to_wire_name(spec.name),
                "description": spec.description,
                "parameters": _podar(spec.json_schema()),
            }
        )
    return declaracoes


def _podar(schema: Any) -> Any:
    """Tira o que o schema do Gemini não aceita.

    O pydantic gera `additionalProperties`, `title` e `$defs`; o Gemini recusa
    a sessão inteira quando encontra chave que não conhece — e o erro vem como
    uma desconexão sem explicação.
    """
    if isinstance(schema, list):
        return [_podar(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {k: _podar(v) for k, v in schema.items() if k not in FORA_DO_SCHEMA}


async def _instrucoes(app: Any) -> str:
    """O prompt do sistema, com o que a EVE já sabe do usuário.

    O modelo tem a ferramenta de busca, mas perguntar custa um turno inteiro de
    conversa falada. O que é mais provável já vai junto.
    """
    try:
        contexto = await app.state.memory.context_for("conversa por voz", limit=6)
    except Exception as exc:
        log.info("live.sem_memoria", error=str(exc)[:120])
        contexto = ""
    return f"{INSTRUCOES}\n\n{contexto}" if contexto else INSTRUCOES
