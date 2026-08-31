"""O telefone é a única parte da EVE alcançável pela internet.

Por isso o que está testado aqui não é o áudio: é quem consegue entrar. Uma
falha nesses três controles não dá erro — dá acesso.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve.phone.security import (
    Bilhetes,
    assinatura_valida,
    numero_permitido,
    url_assinada_para_teste,
)

TOKEN = "token-de-teste"
URL = "https://tunel.exemplo/twilio/voice"


def test_assinatura_do_twilio() -> None:
    params = {"From": "+5531999998888", "CallSid": "CA123", "AccountSid": "AC1"}
    boa = url_assinada_para_teste(TOKEN, URL, params)

    assert assinatura_valida(TOKEN, URL, params, boa)
    # Qualquer coisa fora do lugar derruba: token, URL, parâmetro, assinatura.
    assert not assinatura_valida("outro-token", URL, params, boa)
    assert not assinatura_valida(TOKEN, URL + "x", params, boa)
    assert not assinatura_valida(TOKEN, URL, {**params, "From": "+5511000000000"}, boa)
    assert not assinatura_valida(TOKEN, URL, params, "")
    assert not assinatura_valida("", URL, params, boa)


def test_lista_vazia_recusa_todo_mundo() -> None:
    """Falha segura: um número de telefone é público por natureza."""
    assert not numero_permitido("+5531999998888", [])
    assert not numero_permitido("+5531999998888", [""])


def test_numero_reconhecido_com_qualquer_formatacao() -> None:
    permitidos = ["+55 (31) 99999-8888"]
    assert numero_permitido("+5531999998888", permitidos)
    assert numero_permitido("+55 31 99999 8888", permitidos)
    assert not numero_permitido("+5531999998887", permitidos)


def test_bilhete_vale_uma_vez_so() -> None:
    """Sem isso, a URL do áudio seria o caminho que pula a assinatura."""
    bilhetes = Bilhetes()
    bilhete = bilhetes.emitir("+5531999998888")

    assert bilhetes.resgatar(bilhete) == "+5531999998888"
    assert bilhetes.resgatar(bilhete) is None
    assert bilhetes.resgatar("inventado") is None


def test_bilhete_vencido_nao_vale() -> None:
    bilhetes = Bilhetes(validade=-1)
    assert bilhetes.resgatar(bilhetes.emitir("+5531999998888")) is None


@pytest.fixture
def telefone(isolated_home):
    from eve.daemon.app import create_app
    from eve.phone.app import criar_app_telefone

    nucleo = create_app()
    nucleo.state.secrets.set("TWILIO_AUTH_TOKEN", TOKEN)
    nucleo.state.settings.phone.allowed_callers = ["+5531999998888"]
    nucleo.state.settings.phone.public_url = "https://tunel.exemplo"
    return nucleo, criar_app_telefone(nucleo)


def _ligar(cliente: TestClient, de: str, token: str = TOKEN) -> object:
    form = {"From": de, "CallSid": "CA123"}
    return cliente.post(
        "/twilio/voice",
        data=form,
        headers={"X-Twilio-Signature": url_assinada_para_teste(token, URL, form)},
    )


def test_chamada_legitima_recebe_o_stream(telefone) -> None:
    _, app = telefone
    with TestClient(app) as cliente:
        resposta = _ligar(cliente, "+5531999998888")
    assert resposta.status_code == 200
    # `<Connect>` e não `<Start>`: só o primeiro deixa a EVE responder.
    assert "<Connect><Stream" in resposta.text
    assert "wss://tunel.exemplo/twilio/media?bilhete=" in resposta.text


def test_numero_de_fora_e_recusado(telefone) -> None:
    _, app = telefone
    with TestClient(app) as cliente:
        resposta = _ligar(cliente, "+5511000000000")
    assert "<Hangup/>" in resposta.text
    assert "<Stream" not in resposta.text


def test_assinatura_errada_nao_passa_nem_com_numero_certo(telefone) -> None:
    """A lista de números sozinha não vale nada: quem forja o formulário
    escreve o número que quiser."""
    _, app = telefone
    with TestClient(app) as cliente:
        resposta = _ligar(cliente, "+5531999998888", token="token-de-atacante")
    assert resposta.status_code == 403
    assert "<Stream" not in resposta.text


def test_audio_sem_bilhete_e_fechado(telefone) -> None:
    from starlette.websockets import WebSocketDisconnect

    _, app = telefone
    with TestClient(app) as cliente, pytest.raises(WebSocketDisconnect):
        with cliente.websocket_connect("/twilio/media?bilhete=inventado") as ws:
            ws.receive_text()


def test_o_app_do_telefone_nao_expoe_a_api(telefone) -> None:
    """O túnel expõe esta porta inteira: o que estiver aqui está na internet.

    Testado por alcance e não por introspecção — o que importa é o que
    responde a um pedido, não o que aparece na tabela de rotas."""
    _, app = telefone
    with TestClient(app) as cliente:
        for caminho in (
            "/api/tools",
            "/api/memory",
            "/api/secrets",
            "/api/status",
            "/health",
            "/ws",
            "/",
            "/docs",
            "/api/openapi.json",
        ):
            assert cliente.get(caminho).status_code == 404, caminho
        # E chamar ferramenta, que é o que faria estrago.
        assert cliente.post("/api/tools/file.trash/call", json={"args": {}}).status_code == 404


# ------------------------------------------------------- apontar sozinho


class _FakeTwilio:
    """A API do Twilio, o suficiente para os testes."""

    def __init__(self, porta: int) -> None:
        self.porta = porta
        self.recebido: dict = {}

    def servir(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs

        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: object) -> None: ...

            def do_GET(self) -> None:
                self._json(
                    {
                        "incoming_phone_numbers": [
                            {
                                "sid": "PN1",
                                "phone_number": "+5531988887777",
                                "friendly_name": "EVE",
                                "voice_url": "https://antigo/twilio/voice",
                            }
                        ]
                    }
                )

            def do_POST(self) -> None:
                tamanho = int(self.headers.get("Content-Length", 0))
                fake.recebido = {
                    k: v[0] for k, v in parse_qs(self.rfile.read(tamanho).decode()).items()
                }
                fake.recebido["caminho"] = self.path
                self._json({"sid": "PN1"})

            def _json(self, corpo: dict) -> None:
                bruto = json.dumps(corpo).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(bruto)))
                self.end_headers()
                self.wfile.write(bruto)

        servidor = HTTPServer(("127.0.0.1", self.porta), H)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        return servidor


def test_o_tunel_aponta_o_numero_sozinho(free_port: int, monkeypatch) -> None:
    """O endereço do túnel muda toda vez que ele sobe.

    Sem apontar por conta própria, seria preciso colar a URL nova no painel do
    Twilio a cada reinício — que é o contrário de plug and play.
    """
    import eve.phone.twilio as mod
    from eve.phone.twilio import Twilio

    fake = _FakeTwilio(free_port)
    servidor = fake.servir()
    monkeypatch.setattr(mod, "BASE", f"http://127.0.0.1:{free_port}/2010-04-01")
    try:
        cliente = Twilio("AC123", "token")
        numeros = cliente.numeros()
        assert [n.numero for n in numeros] == ["+5531988887777"]

        cliente.apontar(numeros[0].sid, "https://novo.trycloudflare.com/twilio/voice")
        assert fake.recebido["VoiceUrl"] == "https://novo.trycloudflare.com/twilio/voice"
        # POST, senão o Twilio manda GET e o formulário não chega.
        assert fake.recebido["VoiceMethod"] == "POST"
        assert fake.recebido["caminho"].endswith("/IncomingPhoneNumbers/PN1.json")
    finally:
        servidor.shutdown()


def test_erro_do_twilio_vira_frase_que_se_entende(free_port: int, monkeypatch) -> None:
    from eve.phone.twilio import Twilio, TwilioError

    with pytest.raises(TwilioError, match="não configurados"):
        Twilio("", "")

    import eve.phone.twilio as mod

    monkeypatch.setattr(mod, "BASE", "http://127.0.0.1:1/2010-04-01")
    with pytest.raises(TwilioError, match="não consegui falar com o Twilio"):
        Twilio("AC123", "token").numeros()


def test_allow_liga_a_telefonia_e_guarda_o_numero(isolated_home) -> None:
    """`eve phone allow` é o caminho curto para quem já sabe o que quer."""
    from typer.testing import CliRunner

    from eve.cli.main import app as cli
    from eve.config import load_settings

    runner_cli = CliRunner()
    assert runner_cli.invoke(cli, ["phone", "allow", "+5531999998888"]).exit_code == 0

    s = load_settings()
    assert s.phone.enabled is True
    assert s.phone.allowed_callers == ["+5531999998888"]

    # Sem o "+" não passa: o Twilio só fala E.164.
    assert runner_cli.invoke(cli, ["phone", "allow", "31999998888"]).exit_code == 1

    assert runner_cli.invoke(cli, ["phone", "deny", "+5531999998888"]).exit_code == 0
    assert load_settings().phone.allowed_callers == []
