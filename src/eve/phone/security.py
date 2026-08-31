"""Quem pode falar com a EVE pelo telefone.

Atender o telefone é a única parte da EVE que fica alcançável pela internet, e
por isso é a única que precisa provar quem está do outro lado. São três provas
independentes, e a chamada precisa passar nas três:

1. a assinatura do Twilio, que prova que o pedido veio mesmo dele;
2. o número de quem liga, contra uma lista que você escreve;
3. um bilhete de uso único na URL do áudio, que impede alguém de abrir o
   WebSocket direto sem ter passado pelo telefone.

Nenhuma delas basta sozinha. A primeira não diz *quem* ligou; a segunda é
falsificável sem a primeira; a terceira só protege o segundo salto.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

from eve.logging import get_logger

log = get_logger(__name__)

VALIDADE_BILHETE = 120.0
"""Segundos entre o TwiML e a abertura do áudio. O Twilio leva menos de um."""


def assinatura_valida(auth_token: str, url: str, params: dict[str, str], enviada: str) -> bool:
    """Confere o ``X-Twilio-Signature``.

    O Twilio assina a URL completa concatenada com cada par de parâmetros em
    ordem alfabética, em HMAC-SHA1, e manda em base64. Comparação em tempo
    constante porque comparar assinatura com ``==`` vaza o prefixo correto.
    """
    if not auth_token or not enviada:
        return False
    base = url + "".join(f"{c}{params[c]}" for c in sorted(params))
    esperada = base64.b64encode(
        hmac.new(auth_token.encode(), base.encode("utf-8"), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(esperada, enviada)


def numero_permitido(numero: str, permitidos: list[str]) -> bool:
    """Lista vazia recusa todo mundo — é a única falha segura aqui."""
    if not permitidos:
        return False
    alvo = _so_digitos(numero)
    return any(_so_digitos(p) == alvo for p in permitidos if p.strip())


def _so_digitos(numero: str) -> str:
    """ "+55 (31) 99999-8888" e "+5531999998888" são o mesmo telefone."""
    return "".join(c for c in numero if c.isdigit())


class Bilhetes:
    """Bilhetes de uso único, um por chamada.

    Sem isto, a URL do WebSocket seria adivinhável e qualquer um poderia abrir
    o áudio direto — pulando a assinatura e a lista de números, que só valem
    no primeiro salto.
    """

    def __init__(self, validade: float = VALIDADE_BILHETE) -> None:
        self.validade = validade
        self._abertos: dict[str, tuple[float, str]] = {}

    def emitir(self, numero: str) -> str:
        self._limpar()
        bilhete = secrets.token_urlsafe(24)
        self._abertos[bilhete] = (time.monotonic(), numero)
        return bilhete

    def resgatar(self, bilhete: str) -> str | None:
        """Devolve o número de quem ligou, uma vez só."""
        self._limpar()
        achado = self._abertos.pop(bilhete, None)
        if achado is None:
            log.warning("telefone.bilhete_invalido")
            return None
        return achado[1]

    def _limpar(self) -> None:
        agora = time.monotonic()
        vencidos = [b for b, (t, _) in self._abertos.items() if agora - t > self.validade]
        for b in vencidos:
            del self._abertos[b]


def url_assinada_para_teste(auth_token: str, url: str, params: dict[str, str]) -> str:
    """A assinatura que o Twilio mandaria. Existe para os testes."""
    base = url + "".join(f"{c}{params[c]}" for c in sorted(params))
    return base64.b64encode(
        hmac.new(auth_token.encode(), base.encode("utf-8"), hashlib.sha1).digest()
    ).decode()


def com_query(url: str, **params: str) -> str:
    return f"{url}?{urlencode(params)}" if params else url
