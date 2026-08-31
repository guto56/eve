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
