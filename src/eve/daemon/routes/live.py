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

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

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
async def live_endpoint(
    websocket: WebSocket,
    motor: str = Query(default="auto", pattern="^(auto|nativo|openrouter|gemini)$"),
) -> None:
    await websocket.accept()
    app = websocket.app
    envio = asyncio.Lock()

    async def avisar(payload: dict[str, Any]) -> None:
        async with envio:
            await websocket.send_json(payload)

    async def falar(pcm: bytes) -> None:
        async with envio:
            await websocket.send_bytes(pcm)

    escolhido, falta = _motor(app, motor)
    if falta:
        await avisar({"kind": "error", "fatal": True, "error": falta[0], "hint": falta[1]})
        await websocket.close()
        return

    if escolhido == "nativo":
        await _conversa_ouvido_nativo(websocket, app, avisar, falar)
        return
    if escolhido == "openrouter":
        await _conversa_em_partes(websocket, app, avisar, falar)
        return

    settings = app.state.settings
    chave = app.state.secrets.get("GOOGLE_API_KEY") or ""
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


async def _conversa_ouvido_nativo(websocket: WebSocket, app: Any, avisar: Any, falar: Any) -> None:
    """O navegador ouve; a EVE pensa e o Cartesia fala.

    É o caminho normal sem o Deepgram: a transcrição vem pronta do próprio
    navegador, então pelo socket sobe texto em vez de áudio. A resposta
    continua vindo na voz do Cartesia — trocar a boca junto seria trocar o que
    ninguém pediu.
    """
    import json

    from eve.voice.session import VoiceSession
    from eve.voice.tts import TextToSpeech

    voz = app.state.settings.voice
    try:
        tts = TextToSpeech(
            app.state.secrets.get("CARTESIA_API_KEY") or "",
            app.state.secrets.get("CARTESIA_VOICE_ID") or "",
            model=voz.tts_model,
            language=voz.tts_language,
            sample_rate=voz.output_sample_rate,
        )
    except ValueError as exc:
        await avisar({"kind": "error", "fatal": True, "error": str(exc)})
        await websocket.close()
        return

    async def traduzir(payload: dict[str, Any]) -> None:
        tipo = DE_VOZ.get(str(payload.get("type")))
        if tipo is not None:
            await avisar({**{k: v for k, v in payload.items() if k != "type"}, "kind": tipo})

    await avisar(
        {
            "kind": "ready",
            "engine": "nativo",
            "inputRate": 0,
            "outputRate": voz.output_sample_rate,
            "model": app.state.providers.model_for("external"),
            "voice": "Cartesia",
            "incremental": True,
            "tools": ["a conversa inteira da EVE"],
        }
    )

    # Sessão de voz sem transcritor: `listen` e `feed` nunca são chamados,
    # porque quem ouve é o navegador. O resto — cortar em frases, falar, ser
    # interrompida — é o mesmo de sempre.
    sessao = VoiceSession(app.state.chat, _SemOuvido(), tts, voz, traduzir, falar)
    aquecimento = asyncio.create_task(tts.warm_up())
    try:
        while True:
            mensagem = await websocket.receive()
            if mensagem["type"] == "websocket.disconnect":
                break
            bruto = mensagem.get("text")
            if not bruto:
                continue
            try:
                dados = json.loads(bruto)
            except json.JSONDecodeError:
                continue
            if dados.get("op") == "calar":
                # O usuário voltou a falar por cima: cala na hora.
                await sessao.interromper()
            elif dados.get("op") == "texto" and (texto := str(dados.get("text", "")).strip()):
                await avisar({"kind": "final", "text": texto})
                await sessao.responder(texto)
    except (WebSocketDisconnect, RuntimeError) as exc:
        log.info("live.encerrada", motivo=str(exc)[:120])
    finally:
        aquecimento.cancel()
        await asyncio.gather(aquecimento, return_exceptions=True)
        await sessao.aclose()
        await tts.aclose()


class _SemOuvido:
    """Transcritor que não existe: quem ouve, aqui, é o navegador."""

    async def send_audio(self, frame: bytes) -> None: ...

    async def aclose(self) -> None: ...


def _motor(app: Any, pedido: str) -> tuple[str, tuple[str, str] | None]:
    """Qual motor usar, e o que falta quando nenhum dá.

    O pedido explícito manda; ``auto`` prefere o que a máquina consegue rodar
    agora. Dizer "escolha automática" e depois falhar por falta de chave seria
    escolher errado e culpar o usuário.
    """
    tem_gemini = bool(app.state.secrets.get("GOOGLE_API_KEY"))
    tem_fala = bool(app.state.secrets.get("CARTESIA_API_KEY"))
    tem_partes = bool(app.state.secrets.get("DEEPGRAM_API_KEY")) and tem_fala

    if pedido == "nativo":
        # O navegador ouve de graça, mas quem fala é o Cartesia.
        if tem_fala:
            return "nativo", None
        return "nativo", (
            "CARTESIA_API_KEY não configurada",
            "grave com: eve key set CARTESIA_API_KEY",
        )
    if pedido == "gemini":
        if tem_gemini:
            return "gemini", None
        return "gemini", (
            "GOOGLE_API_KEY não configurada",
            "pegue em aistudio.google.com/apikey e grave com: eve key set GOOGLE_API_KEY",
        )
    if pedido == "openrouter":
        if tem_partes:
            return "openrouter", None
        return "openrouter", (
            "falta DEEPGRAM_API_KEY ou CARTESIA_API_KEY",
            "grave com: eve key set DEEPGRAM_API_KEY",
        )

    preferido = app.state.settings.voice.live_engine
    disponibilidade = {"nativo": tem_fala, "openrouter": tem_partes, "gemini": tem_gemini}
    for candidato, disponivel in (
        (preferido, disponibilidade.get(preferido, False)),
        ("openrouter", tem_partes),
        # O nativo vem antes do gemini porque usa a mesma voz do caminho
        # principal, e depois do openrouter porque a transcrição do navegador
        # é mais pobre que a do Deepgram.
        ("nativo", tem_fala),
        ("gemini", tem_gemini),
    ):
        if disponivel:
            return candidato, None
    return "openrouter", (
        "nenhum motor de voz configurado",
        "grave CARTESIA_API_KEY (e DEEPGRAM_API_KEY), ou GOOGLE_API_KEY",
    )


#: O vocabulário da voz em partes, traduzido para o desta página.
DE_VOZ = {
    "listening": "listening",
    "partial": "partial",
    "final": "final",
    "reply": "reply",
    "tool": "tool",
    "speaking": "speaking",
    "interrupted": "interrupted",
    "reply_done": "turn",
    "error": "error",
}


async def _conversa_em_partes(websocket: WebSocket, app: Any, avisar: Any, falar: Any) -> None:
    """Deepgram ouve, o modelo do OpenRouter pensa, Cartesia fala.

    É a mesma sessão de voz do chat, com outro vocabulário na saída: reescrever
    o pipeline só para trocar o nome dos eventos seria manter duas versões da
    mesma coisa, e uma delas envelheceria.
    """
    from eve.daemon.routes.voice import build_stt, build_tts
    from eve.voice.session import VoiceSession

    voz = app.state.settings.voice
    try:
        stt, tts = build_stt(app), build_tts(app)
    except ValueError as exc:
        await avisar({"kind": "error", "fatal": True, "error": str(exc)})
        await websocket.close()
        return

    # Conectar ao Deepgram custa uns 700 ms. Eles eram gastos com o socket do
    # navegador parado — nada era lido enquanto isso, e quem clicasse e já
    # falasse perdia o começo da frase. Agora as três coisas andam juntas: a
    # conexão sobe, a página acende, e o que o microfone mandar espera na fila.
    conectando = asyncio.create_task(stt.__aenter__())
    aquecendo = asyncio.create_task(tts.warm_up())

    recebidos: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def ler_navegador() -> None:
        try:
            while True:
                mensagem = await websocket.receive()
                if mensagem["type"] == "websocket.disconnect":
                    break
                await recebidos.put(mensagem)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await recebidos.put(None)

    leitor = asyncio.create_task(ler_navegador())

    async def traduzir(payload: dict[str, Any]) -> None:
        tipo = DE_VOZ.get(str(payload.get("type")))
        if tipo is None:
            return
        await avisar({**{k: v for k, v in payload.items() if k != "type"}, "kind": tipo})

    await avisar(
        {
            "kind": "ready",
            "engine": "openrouter",
            "inputRate": voz.input_sample_rate,
            "outputRate": voz.output_sample_rate,
            "model": app.state.providers.model_for("external"),
            "voice": "Cartesia",
            # A transcrição do Deepgram vem inteira a cada parcial, não em
            # pedaços — a tela substitui a linha em vez de emendar.
            "incremental": False,
            "tools": ["a conversa inteira da EVE"],
        }
    )

    try:
        await conectando
    except Exception as exc:
        await avisar({"kind": "error", "fatal": True, "error": _resumo_stt(exc)})
        leitor.cancel()
        return

    sessao = VoiceSession(app.state.chat, stt, tts, voz, traduzir, falar)
    escuta = asyncio.create_task(sessao.listen())
    try:
        while (mensagem := await recebidos.get()) is not None:
            if (audio := mensagem.get("bytes")) is not None:
                await sessao.feed(audio)
            elif (texto := mensagem.get("text")) is not None:
                await _texto_para_a_conversa(sessao, texto)
    except (WebSocketDisconnect, RuntimeError) as exc:
        log.info("live.encerrada", motivo=str(exc)[:120])
    finally:
        for tarefa in (leitor, escuta, aquecendo):
            tarefa.cancel()
        await asyncio.gather(leitor, escuta, aquecendo, return_exceptions=True)
        await sessao.aclose()
        await tts.aclose()
        await stt.__aexit__(None, None, None)


def _resumo_stt(exc: BaseException) -> str:
    return f"não consegui abrir o Deepgram: {str(exc)[:120] or type(exc).__name__}"


async def _texto_para_a_conversa(sessao: Any, bruto: str) -> None:
    """Escrever na página vale como ter falado."""
    import json

    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        return
    if dados.get("op") == "texto" and (texto := str(dados.get("text", "")).strip()):
        await sessao.responder(texto)
