"""O app que atende o telefone.

Separado do Core de propósito. Um túnel aponta para uma porta, e tudo que
estiver nela fica público: se fosse a porta 4242, `/api/tools/file.trash/call`
estaria na internet. Aqui só existem duas rotas, as duas do Twilio, e o
resto da EVE continua em 127.0.0.1 como sempre esteve.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, FastAPI, Form, Request, Response, WebSocket, WebSocketDisconnect

from eve.events import EventType
from eve.logging import get_logger
from eve.phone.security import Bilhetes, assinatura_valida, numero_permitido
from eve.phone.session import ChamadaTelefonica

log = get_logger(__name__)

RECUSA = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say language="pt-BR">Este número não está autorizado.</Say><Hangup/></Response>"""

XML = "application/xml"


def criar_app_telefone(nucleo: Any) -> FastAPI:
    """``nucleo`` é o app do Core: a conversa e a memória são as mesmas."""
    app = FastAPI(title="EVE — telefone", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.nucleo = nucleo
    app.state.bilhetes = Bilhetes()
    app.include_router(_rotas())
    return app


def _rotas() -> APIRouter:
    router = APIRouter()

    @router.post("/twilio/voice")
    async def atender(
        request: Request,
        From: str = Form(default=""),
        CallSid: str = Form(default=""),
    ) -> Response:
        """O Twilio pergunta o que fazer com a ligação; respondemos em TwiML."""
        nucleo = request.app.state.nucleo
        segredos = nucleo.state.secrets
        telefone = nucleo.state.settings.phone

        formulario = {k: v for k, v in (await request.form()).multi_items()}
        assinatura = request.headers.get("X-Twilio-Signature", "")
        url = _url_publica(request, telefone)

        if not assinatura_valida(
            segredos.get("TWILIO_AUTH_TOKEN") or "", url, formulario, assinatura
        ):
            log.warning("telefone.assinatura_invalida", de=From[-4:] if From else "?")
            return Response(RECUSA, media_type=XML, status_code=403)

        if not numero_permitido(From, telefone.allowed_callers):
            log.warning("telefone.numero_recusado", de=From[-4:] if From else "?")
            await nucleo.state.bus.emit(
                EventType.SYSTEM_ERROR, source="telefone", error=f"ligação recusada de {From}"
            )
            return Response(RECUSA, media_type=XML)

        bilhete = request.app.state.bilhetes.emitir(From)
        log.info("telefone.atendendo", de=From[-4:], call=CallSid[:12])
        return Response(_twiml(url, bilhete, telefone.greeting), media_type=XML)

    @router.websocket("/twilio/media")
    async def media(websocket: WebSocket, bilhete: str = "") -> None:
        """O áudio da chamada, nos dois sentidos."""
        numero = websocket.app.state.bilhetes.resgatar(bilhete)
        if numero is None:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await _conversar(websocket, websocket.app.state.nucleo, numero)

    return router


def _twiml(url_base: str, bilhete: str, saudacao: str) -> str:
    """Fala a saudação e abre o áudio nos dois sentidos.

    `<Connect><Stream>` em vez de `<Start><Stream>`: só o primeiro é
    bidirecional, e sem isso a EVE ouviria sem poder responder.
    """
    wss = url_base.replace("https://", "wss://").replace("http://", "ws://")
    wss = wss.rsplit("/twilio/voice", 1)[0] + "/twilio/media?" + urlencode({"bilhete": bilhete})
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>"
        f'<Say language="pt-BR">{_escapar(saudacao)}</Say>'
        f'<Connect><Stream url="{_escapar(wss)}"/></Connect>'
        "</Response>"
    )


def _escapar(texto: str) -> str:
    return (
        texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _url_publica(request: Request, telefone: Any) -> str:
    """A URL que o Twilio assinou.

    Ele assina o endereço público, não o que chega aqui: atrás de um túnel o
    host e o esquema mudam no caminho, e conferir a assinatura com a URL local
    reprovaria toda chamada legítima.
    """
    if getattr(telefone, "public_url", ""):
        return telefone.public_url.rstrip("/") + request.url.path
    encaminhado = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{encaminhado}://{host}{request.url.path}"


async def _conversar(websocket: WebSocket, nucleo: Any, numero: str) -> None:
    """Liga o áudio da chamada ao motor de conversa da EVE."""
    from eve.voice.session import VoiceSession
    from eve.voice.stt import SpeechToText
    from eve.voice.tts import TextToSpeech

    segredos = nucleo.state.secrets
    voz = nucleo.state.settings.voice
    telefone = nucleo.state.settings.phone

    stt = SpeechToText(
        segredos.get("DEEPGRAM_API_KEY") or "",
        model=voz.stt_model,
        language=voz.stt_language,
        sample_rate=telefone.sample_rate,
        endpointing_ms=voz.endpointing_ms,
        encoding="mulaw",
    )
    tts = TextToSpeech(
        segredos.get("CARTESIA_API_KEY") or "",
        segredos.get("CARTESIA_VOICE_ID") or "",
        model=voz.tts_model,
        language=voz.tts_language,
        sample_rate=telefone.sample_rate,
        encoding="pcm_mulaw",
    )

    chamada = ChamadaTelefonica(websocket, numero)
    await nucleo.state.bus.emit(
        EventType.VOICE_LISTENING, source="telefone", on=True, numero=numero[-4:]
    )

    async def avisar(payload: dict[str, Any]) -> None:
        # Quem está no telefone não vê tela; o que interessa aqui é cortar a
        # fala enfileirada quando ele volta a falar.
        if payload.get("type") == "interrupted":
            await chamada.calar()

    async with stt:
        sessao = VoiceSession(nucleo.state.chat, stt, tts, voz, avisar, chamada.falar)
        escuta = asyncio.create_task(sessao.listen())
        try:
            async for tipo, dado in chamada.ler():
                if tipo == "audio":
                    await sessao.feed(dado)
                elif tipo == "fim":
                    break
        except (WebSocketDisconnect, RuntimeError) as exc:
            log.info("telefone.desligou", motivo=str(exc)[:120])
        finally:
            escuta.cancel()
            await asyncio.gather(escuta, return_exceptions=True)
            await sessao.aclose()
            await tts.aclose()
            await nucleo.state.bus.emit(EventType.VOICE_LISTENING, source="telefone", on=False)
            log.info("telefone.encerrada", call=chamada.call_sid[:12])
