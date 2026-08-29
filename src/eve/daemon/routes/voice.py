"""WebSocket de voz.

O áudio do microfone sobe como quadros binários; o áudio da resposta desce do
mesmo jeito. As credenciais ficam no daemon — o navegador nunca vê a chave do
Deepgram nem a do Cartesia.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from eve.logging import get_logger
from eve.voice.session import VoiceSession
from eve.voice.stt import SpeechToText
from eve.voice.tts import TextToSpeech

log = get_logger(__name__)
router = APIRouter()


def build_stt(app: Any) -> SpeechToText:
    settings = app.state.settings.voice
    return SpeechToText(
        app.state.secrets.get("DEEPGRAM_API_KEY") or "",
        model=settings.stt_model,
        language=settings.stt_language,
        sample_rate=settings.input_sample_rate,
        endpointing_ms=settings.endpointing_ms,
    )


def build_tts(app: Any) -> TextToSpeech:
    settings = app.state.settings.voice
    return TextToSpeech(
        app.state.secrets.get("CARTESIA_API_KEY") or "",
        app.state.secrets.get("CARTESIA_VOICE_ID") or "",
        model=settings.tts_model,
        language=settings.tts_language,
        sample_rate=settings.output_sample_rate,
    )


@router.websocket("/ws/voice")
async def voice_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    lock = asyncio.Lock()

    async def send_json(payload: dict[str, Any]) -> None:
        async with lock:
            await websocket.send_json(payload)

    async def send_audio(frame: bytes) -> None:
        async with lock:
            await websocket.send_bytes(frame)

    app = websocket.app
    try:
        stt = build_stt(app)
        tts = build_tts(app)
    except ValueError as exc:
        await send_json({"type": "error", "error": str(exc), "fatal": True})
        await websocket.close()
        return

    settings = app.state.settings.voice
    await send_json(
        {
            "type": "ready",
            "input_sample_rate": settings.input_sample_rate,
            "output_sample_rate": settings.output_sample_rate,
            "barge_in": settings.barge_in,
        }
    )

    async with stt:
        session = VoiceSession(app.state.chat, stt, tts, settings, send_json, send_audio)
        # Aquece a fala em paralelo: o usuário ainda vai levar segundos falando.
        aquecimento = asyncio.create_task(tts.warm_up())
        escuta = asyncio.create_task(session.listen())
        quadros = 0
        try:
            while True:
                mensagem = await websocket.receive()
                if mensagem["type"] == "websocket.disconnect":
                    break
                if (audio := mensagem.get("bytes")) is not None:
                    quadros += 1
                    if quadros == 1:
                        log.info("voz.microfone_aberto")
                    await session.feed(audio)
                elif (texto := mensagem.get("text")) is not None:
                    if not await _handle_control(session, texto, send_json):
                        break
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.exception("voz.falhou")
            await send_json({"type": "error", "error": str(exc)})
        finally:
            escuta.cancel()
            aquecimento.cancel()
            await asyncio.gather(escuta, aquecimento, return_exceptions=True)
            await session.aclose()
            await tts.aclose()
            log.info("voz.sessao_encerrada", quadros=quadros)


async def _handle_control(session: VoiceSession, texto: str, send_json: Any) -> bool:
    """Trata um comando do cliente. ``False`` encerra a sessão."""
    try:
        comando = json.loads(texto)
    except json.JSONDecodeError:
        await send_json({"type": "error", "error": "JSON inválido"})
        return True

    op = comando.get("op")
    if op == "stop":
        await session.stt.finish()
        return True
    if op == "interrupt":
        await session._interrupt()
        return True
    if op == "bye":
        return False
    await send_json({"type": "error", "error": f"operação desconhecida: {op!r}"})
    return True
