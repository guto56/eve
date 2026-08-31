"""Uma conversa por voz.

    microfone → Deepgram → transcrição
                              ↓ frase terminada
                        motor de conversa
                              ↓ resposta em streaming
                        Cartesia → áudio → alto-falante

A experiência precisa parecer conversa, não gravação (spec §13). Duas coisas
fazem essa diferença: a EVE começa a falar na primeira frase pronta, sem
esperar a resposta inteira; e para de falar assim que o usuário volta a falar.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from eve.chat.engine import ChatEngine
from eve.config import VoiceSettings
from eve.events import EventType
from eve.logging import get_logger
from eve.voice.stt import SpeechToText, Transcript
from eve.voice.tts import TextToSpeech

log = get_logger(__name__)

#: Fim de frase: é aqui que dá para mandar um trecho falar sem soar cortado.
FIM_DE_FRASE = re.compile(r"[.!?…]+[\s\"')\]]*$|[:;]\s*$")

MIN_PARA_FALAR = 40
"""Trecho curto demais soa picotado; longo demais atrasa o começo da fala."""

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]


@dataclass
class VoiceState:
    listening: bool = False
    speaking: bool = False
    session: str | None = None
    partial: str = ""
    fala_atual: asyncio.Task[None] | None = field(default=None, repr=False)


class VoiceSession:
    def __init__(
        self,
        engine: ChatEngine,
        stt: SpeechToText,
        tts: TextToSpeech,
        settings: VoiceSettings,
        send_json: SendJson,
        send_audio: SendAudio,
    ) -> None:
        self.engine = engine
        self.stt = stt
        self.tts = tts
        self.settings = settings
        self.send_json = send_json
        self.send_audio = send_audio
        self.state = VoiceState()

    # ------------------------------------------------------------- entrada

    async def feed(self, frame: bytes) -> None:
        await self.stt.send_audio(frame)

    async def listen(self) -> None:
        """Consome as transcrições até o microfone fechar."""
        self.state.listening = True
        await self.send_json({"type": "listening", "on": True})
        await self.engine.bus.emit(EventType.VOICE_LISTENING, source="voice", on=True)

        async for transcript in self.stt.transcripts():
            await self._on_transcript(transcript)

        self.state.listening = False
        await self.send_json({"type": "listening", "on": False})

    async def _on_transcript(self, transcript: Transcript) -> None:
        if not transcript.usable:
            return

        # Voltou a falar enquanto a EVE falava: cala a boca na hora.
        if self.settings.barge_in and self.state.speaking:
            await self._interrupt()

        if not transcript.is_final:
            self.state.partial = transcript.text
            await self.send_json({"type": "partial", "text": transcript.text})
            return

        await self.send_json({"type": "final", "text": transcript.text})
        await self.engine.bus.emit(EventType.VOICE_TRANSCRIPT, source="voice", text=transcript.text)
        if transcript.speech_final:
            self.state.partial = ""
            await self._respond(transcript.text)

    # -------------------------------------------------------------- saída

    async def responder(self, texto: str) -> None:
        """Responde a um texto escrito, como se tivesse sido falado.

        A página ao vivo deixa escrever quando não dá para falar; para o resto
        da sessão não há diferença.
        """
        await self._respond(texto)

    async def _respond(self, texto: str) -> None:
        pendente = ""
        falou_algo = False

        async for evento in self.engine.send(texto, self.state.session, source="voice"):
            if evento.kind == "session":
                self.state.session = evento.data["session"]
            elif evento.kind == "tool":
                await self.send_json({"type": "tool", "name": evento.data["name"]})
            elif evento.kind == "error":
                await self.send_json({"type": "error", "error": evento.data["error"]})
                return
            elif evento.kind == "delta":
                pendente += evento.data["text"]
                await self.send_json({"type": "reply", "text": evento.data["text"]})
                trecho, pendente = _corta_frase(pendente)
                if trecho:
                    await self._speak(trecho)
                    falou_algo = True

        if pendente.strip():
            await self._speak(pendente)
            falou_algo = True
        if not falou_algo:
            await self.send_json({"type": "reply_done"})

    async def _speak(self, texto: str) -> None:
        self.state.speaking = True
        await self.send_json({"type": "speaking", "on": True})
        await self.engine.bus.emit(EventType.VOICE_SPEAKING, source="voice", on=True)

        tarefa = asyncio.create_task(self._stream_audio(texto))
        self.state.fala_atual = tarefa
        try:
            await tarefa
        except asyncio.CancelledError:
            pass
        finally:
            self.state.fala_atual = None
            self.state.speaking = False
            await self.send_json({"type": "speaking", "on": False})

    async def _stream_audio(self, texto: str) -> None:
        async for pedaco in self.tts.stream(texto):
            await self.send_audio(pedaco)

    async def _interrupt(self) -> None:
        tarefa = self.state.fala_atual
        if tarefa is not None and not tarefa.done():
            tarefa.cancel()
        self.state.speaking = False
        await self.send_json({"type": "interrupted"})
        log.info("voz.interrompida")

    async def aclose(self) -> None:
        # No encerramento o socket do cliente já pode ter caído; cancelar a
        # fala é o que importa, avisar é opcional.
        tarefa = self.state.fala_atual
        if tarefa is not None and not tarefa.done():
            tarefa.cancel()
        self.state.speaking = False
        await self.stt.aclose()


def _corta_frase(acumulado: str) -> tuple[str, str]:
    """Separa a primeira frase pronta do que ainda está chegando.

    Devolve ``(trecho_para_falar, resto)``. Sem frase completa, o trecho é
    vazio e nada é falado ainda.
    """
    if len(acumulado) < MIN_PARA_FALAR:
        return "", acumulado
    for fim in range(len(acumulado), MIN_PARA_FALAR - 1, -1):
        if FIM_DE_FRASE.search(acumulado[:fim]):
            return acumulado[:fim].strip(), acumulado[fim:]
    return "", acumulado
