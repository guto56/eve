"""Conversar com a API do Twilio.

Existe por um motivo prático: o endereço do túnel muda a cada vez que ele
sobe. Se fosse preciso colar a URL nova no painel a cada reinício, a
telefonia seria qualquer coisa menos plug and play. Com o SID e o token, a
EVE aponta o próprio número para si mesma.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx2 as httpx

from eve.logging import get_logger

log = get_logger(__name__)

BASE = "https://api.twilio.com/2010-04-01"
TIMEOUT = 20.0


class TwilioError(RuntimeError):
    """A API recusou. A mensagem já vem pronta para ler."""


@dataclass(frozen=True)
class Numero:
    sid: str
    numero: str
    apelido: str
    voice_url: str

    def __str__(self) -> str:
        return f"{self.numero} ({self.apelido})" if self.apelido else self.numero


class Twilio:
    def __init__(self, account_sid: str, auth_token: str) -> None:
        if not account_sid or not auth_token:
            raise TwilioError("TWILIO_ACCOUNT_SID ou TWILIO_AUTH_TOKEN não configurados")
        self.account_sid = account_sid
        self.auth = (account_sid, auth_token)

    def numeros(self) -> list[Numero]:
        """Os números da conta."""
        dados = self._pedir("GET", f"/Accounts/{self.account_sid}/IncomingPhoneNumbers.json")
        return [
            Numero(
                sid=item.get("sid", ""),
                numero=item.get("phone_number", ""),
                apelido=item.get("friendly_name", ""),
                voice_url=item.get("voice_url") or "",
            )
            for item in dados.get("incoming_phone_numbers") or []
        ]

    def apontar(self, sid: str, url: str) -> None:
        """Faz o número chamar esta URL quando alguém ligar."""
        self._pedir(
            "POST",
            f"/Accounts/{self.account_sid}/IncomingPhoneNumbers/{sid}.json",
            dados={"VoiceUrl": url, "VoiceMethod": "POST"},
        )
        log.info("telefone.webhook_apontado", url=url)

    def _pedir(self, metodo: str, caminho: str, dados: dict[str, str] | None = None) -> dict:
        try:
            resposta = httpx.request(
                metodo, f"{BASE}{caminho}", auth=self.auth, data=dados, timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise TwilioError(f"não consegui falar com o Twilio: {exc}") from exc

        if resposta.status_code == 401:
            raise TwilioError("o Twilio recusou as credenciais — confira SID e token")
        if resposta.status_code >= 400:
            raise TwilioError(f"o Twilio respondeu {resposta.status_code}: {_motivo(resposta)}")
        try:
            return resposta.json()
        except ValueError as exc:
            raise TwilioError("o Twilio respondeu algo que não é JSON") from exc


def _motivo(resposta: httpx.Response) -> str:
    """A mensagem do Twilio, quando ele manda uma."""
    try:
        corpo = resposta.json()
    except ValueError:
        return resposta.text[:160]
    return str(corpo.get("message") or corpo)[:160]
