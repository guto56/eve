"""A chamada: áudio do Twilio de um lado, a EVE do outro.

    telefone → Twilio → μ-law 8 kHz → Deepgram
                                         ↓
                                  motor de conversa
                                         ↓
                        Cartesia → μ-law 8 kHz → Twilio → telefone

Sem conversão de áudio em lugar nenhum: a linha telefônica é μ-law a 8 kHz, e
tanto o Deepgram quanto o Cartesia falam esse formato direto. Converter seria
perder qualidade e ganhar latência de graça.

A conversa é a mesma do chat e da página ao vivo — mesmo motor, mesmas
ferramentas, mesma memória. Um telefonema não é um caminho paralelo.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from eve.logging import get_logger

log = get_logger(__name__)


class ChamadaTelefonica:
    """Traduz entre o vocabulário do Twilio e o da sessão de voz."""

    def __init__(self, websocket: Any, numero: str) -> None:
        self.ws = websocket
        self.numero = numero
        self.stream_sid = ""
        self.call_sid = ""
        self._envio = asyncio.Lock()

    # ------------------------------------------------------------- entrada

    async def ler(self) -> Any:
        """Cada mensagem do Twilio, já decodificada."""
        async for bruto in self.ws.iter_text():
            try:
                dados = json.loads(bruto)
            except json.JSONDecodeError:
                continue
            evento = dados.get("event")
            if evento == "start":
                inicio = dados.get("start") or {}
                self.stream_sid = inicio.get("streamSid", "")
                self.call_sid = inicio.get("callSid", "")
                log.info("telefone.stream_iniciado", call=self.call_sid[:12])
                yield ("inicio", None)
            elif evento == "media":
                carga = (dados.get("media") or {}).get("payload")
                if carga:
                    yield ("audio", base64.b64decode(carga))
            elif evento == "stop":
                yield ("fim", None)
                return
            elif evento == "dtmf":
                yield ("tecla", (dados.get("dtmf") or {}).get("digit", ""))

    # -------------------------------------------------------------- saída

    async def falar(self, pcm_mulaw: bytes) -> None:
        """Manda áudio para a linha. O Twilio enfileira e toca em ordem."""
        if not self.stream_sid:
            return
        await self._mandar(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": base64.b64encode(pcm_mulaw).decode("ascii")},
            }
        )

    async def calar(self) -> None:
        """Descarta o que já foi enfileirado.

        É o que faz a interrupção valer no telefone: sem isto, a EVE pararia de
        gerar fala mas o Twilio continuaria tocando os segundos já enviados, e
        quem ligou continuaria ouvindo ela falar por cima.
        """
        if self.stream_sid:
            await self._mandar({"event": "clear", "streamSid": self.stream_sid})

    async def _mandar(self, payload: dict[str, Any]) -> None:
        async with self._envio:
            await self.ws.send_text(json.dumps(payload))
