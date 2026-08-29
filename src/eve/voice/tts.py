"""Fala com Cartesia.

O áudio sai em pedaços conforme é gerado, e não depois da frase inteira: a EVE
começa a falar em torno de 200 ms. Um ``context_id`` por resposta permite
cancelar no meio quando o usuário interrompe (spec §13).
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import websockets

from eve.logging import get_logger

log = get_logger(__name__)

ENDPOINT = "wss://api.cartesia.ai/tts/websocket"
API_VERSION = "2026-03-01"


class TextToSpeech:
    def __init__(
        self,
        api_key: str,
        voice_id: str,
        *,
        model: str = "sonic-3",
        language: str = "pt",
        sample_rate: int = 24000,
    ) -> None:
        if not api_key:
            raise ValueError("CARTESIA_API_KEY não configurada")
        if not voice_id:
            raise ValueError("CARTESIA_VOICE_ID não configurada")
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self._socket: Any = None
        self._lock = asyncio.Lock()

    def _url(self) -> str:
        return f"{ENDPOINT}?api_key={self.api_key}&cartesia_version={API_VERSION}"

    def _payload(self, texto: str, context_id: str) -> dict[str, Any]:
        return {
            "model_id": self.model,
            "transcript": texto,
            "voice": {"mode": "id", "id": self.voice_id},
            "language": self.language,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate,
            },
            "context_id": context_id,
            "continue": False,
        }

    async def _connection(self) -> Any:
        """Socket persistente.

        Abrir a conexão custa cerca de um segundo — quase toda a latência
        percebida na primeira frase. Mantendo-a quente entre respostas, a EVE
        começa a falar em torno de 200 ms.
        """
        async with self._lock:
            if self._socket is not None and self._socket.state.name == "OPEN":
                return self._socket
            self._socket = await websockets.connect(self._url(), open_timeout=15, ping_interval=20)
            return self._socket

    async def stream(self, texto: str) -> AsyncIterator[bytes]:
        """Pedaços de PCM 16 bits, na ordem, conforme o modelo gera."""
        texto = texto.strip()
        if not texto:
            return
        context_id = uuid.uuid4().hex[:12]
        for tentativa in (1, 2):
            try:
                socket = await self._connection()
                await socket.send(json.dumps(self._payload(texto, context_id)))
                while True:
                    mensagem = json.loads(await asyncio.wait_for(socket.recv(), timeout=30))
                    if mensagem.get("context_id") not in (None, context_id):
                        continue  # sobra de uma geração cancelada
                    tipo = mensagem.get("type")
                    if tipo == "chunk":
                        yield base64.b64decode(mensagem["data"])
                    elif tipo == "done":
                        return
                    elif tipo == "error":
                        log.warning("tts.erro", detalhe=str(mensagem)[:200])
                        return
            except (websockets.WebSocketException, TimeoutError, ConnectionError) as exc:
                self._socket = None
                if tentativa == 1:
                    # O socket quente pode ter expirado no servidor; uma
                    # reconexão silenciosa é melhor que um silêncio.
                    log.info("tts.reconectando", error=str(exc)[:120])
                    continue
                log.warning("tts.falhou", error=str(exc)[:160])
                return

    async def aclose(self) -> None:
        if self._socket is not None:
            try:
                await self._socket.close()
            except Exception:
                pass
            self._socket = None

    async def warm_up(self) -> bool:
        """Abre a conexão antes de precisar dela.

        Quase um segundo da latência da primeira resposta é só o aperto de mão
        com o Cartesia. Fazê-lo enquanto o usuário ainda está falando tira esse
        tempo do caminho crítico.
        """
        try:
            await self._connection()
        except (websockets.WebSocketException, TimeoutError, ConnectionError) as exc:
            log.info("tts.aquecimento_falhou", error=str(exc)[:120])
            return False
        return True

    async def synthesize(self, texto: str) -> bytes:
        """Áudio completo. Para quando não há a quem entregar em pedaços."""
        pedacos = [pedaco async for pedaco in self.stream(texto)]
        return b"".join(pedacos)

    def wav_header(self, tamanho_pcm: int) -> bytes:
        """Cabeçalho WAV para o PCM cru virar arquivo tocável."""
        import struct

        return b"RIFF" + struct.pack(
            "<I4s4sIHHIIHH4sI",
            36 + tamanho_pcm,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            self.sample_rate,
            self.sample_rate * 2,
            2,
            16,
            b"data",
            tamanho_pcm,
        )
