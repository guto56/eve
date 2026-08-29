"""Transcrição em streaming com Deepgram.

A conversa por voz não é uma sequência de gravações: o áudio sobe enquanto a
pessoa fala, os resultados parciais chegam em tempo real e o próprio Deepgram
avisa quando a frase terminou (``speech_final``). É isso que permite responder
sem esperar a pessoa apertar um botão.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import websockets

from eve.logging import get_logger

log = get_logger(__name__)

ENDPOINT = "wss://api.deepgram.com/v1/listen"

KEEPALIVE_SECONDS = 5.0
"""A Deepgram fecha depois de ~10 s sem dados; avisamos antes disso."""


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool
    speech_final: bool
    confidence: float = 0.0

    @property
    def usable(self) -> bool:
        return bool(self.text.strip())


class SpeechToText:
    """Uma sessão de transcrição. Vive enquanto o microfone estiver aberto."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "pt-BR",
        sample_rate: int = 16000,
        endpointing_ms: int = 300,
    ) -> None:
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY não configurada")
        self.api_key = api_key
        self.params = {
            "model": model,
            "language": language,
            "encoding": "linear16",
            "sample_rate": str(sample_rate),
            "channels": "1",
            "interim_results": "true",
            "endpointing": str(endpointing_ms),
            "smart_format": "true",
        }
        self._socket: Any = None
        self._closed = False
        self._last_audio = 0.0
        self._keepalive_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SpeechToText:
        url = f"{ENDPOINT}?{urlencode(self.params)}"
        self._socket = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {self.api_key}"},
            open_timeout=15,
        )
        self._last_audio = asyncio.get_running_loop().time()
        self._keepalive_task = asyncio.create_task(self._keepalive())
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def send_audio(self, frame: bytes) -> None:
        if self._socket is None or self._closed:
            return
        self._last_audio = asyncio.get_running_loop().time()
        try:
            await self._socket.send(frame)
        except websockets.WebSocketException as exc:  # pragma: no cover - rede
            log.warning("stt.envio_falhou", error=str(exc)[:120])
            self._closed = True

    async def _keepalive(self) -> None:
        """Sem áudio por alguns segundos, a Deepgram encerra a conexão.

        Acontece de verdade: microfone mudo, pausa longa, aba em segundo
        plano. Um ping periódico mantém a sessão de pé sem custo de áudio.
        """
        while not self._closed and self._socket is not None:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            ocioso = asyncio.get_running_loop().time() - self._last_audio
            if ocioso < KEEPALIVE_SECONDS or self._closed:
                continue
            with _quiet():
                await self._socket.send(json.dumps({"type": "KeepAlive"}))

    async def finish(self) -> None:
        """Avisa que o áudio acabou, para o Deepgram fechar a última frase."""
        if self._socket is None or self._closed:
            return
        with _quiet():
            await self._socket.send(json.dumps({"type": "CloseStream"}))

    async def transcripts(self) -> AsyncIterator[Transcript]:
        if self._socket is None:
            raise RuntimeError("sessão de transcrição não foi aberta")
        try:
            async for raw in self._socket:
                if isinstance(raw, bytes):
                    continue
                mensagem = json.loads(raw)
                if mensagem.get("type") != "Results":
                    if mensagem.get("type") in ("Close", "Error"):
                        return
                    continue
                alternativa = mensagem["channel"]["alternatives"][0]
                yield Transcript(
                    text=alternativa.get("transcript", ""),
                    is_final=bool(mensagem.get("is_final")),
                    speech_final=bool(mensagem.get("speech_final")),
                    confidence=float(alternativa.get("confidence", 0.0)),
                )
        except websockets.WebSocketException as exc:  # pragma: no cover - rede
            log.info("stt.conexao_encerrada", error=str(exc)[:120])

    async def aclose(self) -> None:
        self._closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._socket is not None:
            with _quiet():
                await self._socket.close()
            self._socket = None


class _quiet:
    """Fechar conexão que já caiu não deve virar exceção."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(
            exc_type, (websockets.WebSocketException, ConnectionError, asyncio.TimeoutError)
        )


AudioSink = Callable[[bytes], Any]
