"""Conversa ao vivo com o Gemini Live.

O caminho normal da voz na EVE são três peças: Deepgram transcreve, o modelo
pensa, Cartesia fala. Funciona e é bom, mas cada troca de mãos custa tempo.
Aqui é um modelo só, que ouve e fala: o áudio entra e sai pelo mesmo
WebSocket, sem transcrever no meio.

O que a EVE acrescenta é o que o Gemini não tem — as ferramentas dela. As
chamadas de função declaradas na abertura da sessão voltam por este mesmo
socket e são executadas pelo Tool Bus, com permissão e auditoria como
qualquer outra. É o que deixa a conversa por voz mexer na memória de verdade.

Referência: https://ai.google.dev/api/live
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from eve.logging import get_logger

log = get_logger(__name__)

URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)

TAXA_ENTRADA = 16000
TAXA_SAIDA = 24000
"""Fixas pelo Live API: 16 kHz PCM16 entra, 24 kHz PCM16 sai."""

ABERTURA_TIMEOUT = 20.0


class LiveError(RuntimeError):
    """A sessão não abriu ou caiu."""


class SessaoLive:
    """Uma conversa. Abre, troca áudio e ferramentas, fecha."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        voice: str,
        instrucoes: str,
        ferramentas: list[dict[str, Any]] | None = None,
        idioma: str = "pt-BR",
    ) -> None:
        if not api_key:
            raise LiveError("GOOGLE_API_KEY não configurada")
        self.api_key = api_key
        self.model = model if model.startswith("models/") else f"models/{model}"
        self.voice = voice
        self.instrucoes = instrucoes
        self.ferramentas = ferramentas or []
        self.idioma = idioma
        self._ws: Any = None

    # ------------------------------------------------------------- abertura

    async def __aenter__(self) -> SessaoLive:
        try:
            self._ws = await websockets.connect(f"{URL}?key={self.api_key}", max_size=None)
        except Exception as exc:
            raise LiveError(f"não consegui abrir a sessão: {_curto(exc)}") from exc
        await self._enviar(self._abertura())
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.fechar()

    def _abertura(self) -> dict[str, Any]:
        setup: dict[str, Any] = {
            "model": self.model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}},
                    "languageCode": self.idioma,
                },
            },
            "systemInstruction": {"parts": [{"text": self.instrucoes}]},
            # Sem transcrição não há o que mostrar na tela: o áudio passa e
            # some. Pedir as duas é o que deixa a conversa ficar escrita.
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
        if self.ferramentas:
            setup["tools"] = [{"functionDeclarations": self.ferramentas}]
        return {"setup": setup}

    # -------------------------------------------------------------- envio

    async def enviar_audio(self, pcm: bytes) -> None:
        """Um pedaço de PCM16 a 16 kHz, como sai do microfone."""
        await self._enviar(
            {
                "realtimeInput": {
                    "audio": {
                        "mimeType": f"audio/pcm;rate={TAXA_ENTRADA}",
                        "data": base64.b64encode(pcm).decode("ascii"),
                    }
                }
            }
        )

    async def enviar_texto(self, texto: str) -> None:
        """Fala escrita: entra na mesma conversa e sai em áudio."""
        await self._enviar(
            {
                "clientContent": {
                    "turns": [{"role": "user", "parts": [{"text": texto}]}],
                    "turnComplete": True,
                }
            }
        )

    async def responder_ferramentas(self, respostas: list[dict[str, Any]]) -> None:
        """Devolve o resultado das funções que o modelo pediu.

        O ``id`` é o que amarra a resposta ao pedido; sem ele o modelo não sabe
        de qual chamada estamos falando e a conversa trava esperando.
        """
        await self._enviar({"toolResponse": {"functionResponses": respostas}})

    async def _enviar(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise LiveError("sessão fechada")
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            raise LiveError(f"não consegui enviar: {_curto(exc)}") from exc

    # ------------------------------------------------------------ recepção

    async def eventos(self) -> AsyncIterator[dict[str, Any]]:
        """O que o Gemini manda, traduzido para o vocabulário da EVE."""
        if self._ws is None:
            raise LiveError("sessão fechada")
        try:
            async for bruto in self._ws:
                for evento in _traduzir(bruto):
                    yield evento
        except websockets.exceptions.ConnectionClosed as exc:
            yield {"tipo": "fechada", "motivo": _curto(exc)}
        except Exception as exc:
            yield {"tipo": "erro", "erro": _curto(exc)}

    async def fechar(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


def _traduzir(bruto: str | bytes) -> list[dict[str, Any]]:
    """Uma mensagem do servidor pode conter várias coisas de uma vez."""
    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("live.mensagem_invalida")
        return []

    if "setupComplete" in dados:
        return [{"tipo": "pronta"}]

    if chamada := dados.get("toolCall"):
        pedidos = [
            {"id": c.get("id"), "nome": c.get("name", ""), "args": c.get("args") or {}}
            for c in chamada.get("functionCalls") or []
        ]
        return [{"tipo": "ferramentas", "chamadas": pedidos}] if pedidos else []

    conteudo = dados.get("serverContent")
    if not conteudo:
        return []

    eventos: list[dict[str, Any]] = []
    if conteudo.get("interrupted"):
        # O usuário voltou a falar por cima: o que já foi enviado não vale mais.
        eventos.append({"tipo": "interrompido"})
    if texto := _transcricao(conteudo.get("inputTranscription")):
        eventos.append({"tipo": "ouvi", "texto": texto})
    if texto := _transcricao(conteudo.get("outputTranscription")):
        eventos.append({"tipo": "falei", "texto": texto})
    for pcm in _audio(conteudo.get("modelTurn")):
        eventos.append({"tipo": "audio", "pcm": pcm})
    if conteudo.get("turnComplete"):
        eventos.append({"tipo": "turno_completo"})
    return eventos


def _transcricao(bloco: Any) -> str:
    return (bloco or {}).get("text", "") if isinstance(bloco, dict) else ""


def _audio(turno: Any) -> list[bytes]:
    if not isinstance(turno, dict):
        return []
    pedacos = []
    for parte in turno.get("parts") or []:
        dados = (parte or {}).get("inlineData") or {}
        if str(dados.get("mimeType", "")).startswith("audio/") and dados.get("data"):
            try:
                pedacos.append(base64.b64decode(dados["data"]))
            except (ValueError, TypeError):
                log.warning("live.audio_invalido")
    return pedacos


def _curto(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__)[:200]
