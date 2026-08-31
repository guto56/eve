"""A conversa ao vivo com o Gemini Live.

Não dá para testar contra o Google sem chave, mas a parte que quebra em
silêncio é nossa: traduzir o protocolo, podar o schema e responder toda
chamada de ferramenta. É isso que está aqui.
"""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from eve.daemon.app import create_app
from eve.daemon.routes.live import _declaracoes, _motor, _podar
from eve.voice.live import TAXA_ENTRADA, TAXA_SAIDA, SessaoLive, _traduzir, explicar


def test_sem_chave_nenhuma_a_pagina_diz_o_que_falta() -> None:
    """Erro sem instrução é erro que o usuário não resolve."""
    with TestClient(create_app()) as client, client.websocket_connect("/ws/live") as ws:
        aviso = ws.receive_json()
    assert aviso["fatal"] is True
    assert "nenhum motor" in aviso["error"]
    assert "CARTESIA_API_KEY" in aviso["hint"]


def test_ouvido_nativo_dispensa_o_deepgram_mas_nao_a_voz() -> None:
    """O navegador ouve de graça; quem fala continua sendo o Cartesia."""
    from eve.daemon.routes.live import _motor

    app = create_app()
    escolhido, falta = _motor(app, "nativo")
    assert escolhido == "nativo"
    assert falta is not None and "CARTESIA_API_KEY" in falta[0]

    app.state.secrets.set("CARTESIA_API_KEY", "c")
    assert _motor(app, "nativo") == ("nativo", None)
    # Só com a voz, `auto` escolhe o nativo: falta o ouvido pago, não a fala.
    assert _motor(app, "auto")[0] == "nativo"


def test_pedir_motor_sem_chave_diz_o_que_falta() -> None:
    """Pedido explícito devolve o que falta, em vez de trocar de motor por
    conta própria: erro sem instrução é erro que o usuário não resolve."""
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws/live?motor=gemini") as ws:
            aviso = ws.receive_json()
        assert aviso["fatal"] is True
        assert "GOOGLE_API_KEY" in aviso["error"]
        assert "eve key set" in aviso["hint"]

        with client.websocket_connect("/ws/live?motor=openrouter") as ws:
            aviso = ws.receive_json()
        assert "DEEPGRAM_API_KEY" in aviso["error"]


def test_motor_escolhido_segue_o_que_a_maquina_tem() -> None:
    """Dizer "automático" e falhar por falta de chave é escolher errado e
    culpar o usuário."""
    app = create_app()

    app.state.secrets.set("DEEPGRAM_API_KEY", "d")
    app.state.secrets.set("CARTESIA_API_KEY", "c")
    assert _motor(app, "auto") == ("openrouter", None)
    assert _motor(app, "openrouter") == ("openrouter", None)

    # Pedido explícito sem a chave devolve o que falta, não outro motor.
    escolhido, falta = _motor(app, "gemini")
    assert escolhido == "gemini"
    assert falta is not None and "GOOGLE_API_KEY" in falta[0]

    app.state.secrets.set("GOOGLE_API_KEY", "g")
    assert _motor(app, "gemini") == ("gemini", None)
    # Com os dois, vale o preferido da configuração.
    assert _motor(app, "auto")[0] == app.state.settings.voice.live_engine


def test_erro_do_google_vira_frase_que_se_entende() -> None:
    """ "received 1011 (internal error)" não diz a ninguém o que fazer."""
    assert explicar("Your prepayment credits are depleted. Please go to") == (
        "a conta do Google AI Studio está sem créditos"
    )
    assert explicar("API key not valid") == "a GOOGLE_API_KEY não é válida"
    assert explicar("algo que não conhecemos") == "algo que não conhecemos"


def test_abertura_pede_o_que_a_conversa_precisa() -> None:
    sessao = SessaoLive(
        "chave",
        model="gemini-3.1-flash-live-preview",
        voice="Aoede",
        instrucoes="Você é a EVE.",
        ferramentas=[{"name": "memory__recall", "description": "x", "parameters": {}}],
    )
    setup = sessao._abertura()["setup"]

    assert setup["model"].startswith("models/")
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    voz = setup["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert voz["voiceName"] == "Aoede"
    # Sem transcrição o áudio passa e some: não sobra nada na tela.
    assert "inputAudioTranscription" in setup
    assert "outputAudioTranscription" in setup
    assert setup["tools"][0]["functionDeclarations"][0]["name"] == "memory__recall"


def test_traduz_o_que_o_gemini_manda() -> None:
    assert _traduzir(json.dumps({"setupComplete": {}})) == [{"tipo": "pronta"}]

    pcm = base64.b64encode(b"\x01\x02").decode()
    eventos = _traduzir(
        json.dumps(
            {
                "serverContent": {
                    "inputTranscription": {"text": "oi"},
                    "outputTranscription": {"text": "olá"},
                    "modelTurn": {
                        "parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm}}]
                    },
                    "turnComplete": True,
                }
            }
        )
    )
    assert [e["tipo"] for e in eventos] == ["ouvi", "falei", "audio", "turno_completo"]
    assert eventos[2]["pcm"] == b"\x01\x02"

    chamada = _traduzir(
        json.dumps({"toolCall": {"functionCalls": [{"id": "a", "name": "memory__recall"}]}})
    )
    assert chamada[0]["chamadas"][0] == {"id": "a", "nome": "memory__recall", "args": {}}

    # Lixo não pode derrubar a conversa.
    assert _traduzir("não é json") == []
    assert _traduzir(json.dumps({"desconhecido": 1})) == []


def test_schema_podado_para_o_gemini() -> None:
    """O Gemini recusa a sessão inteira ao ver chave que não conhece, e o erro
    volta como uma desconexão sem explicação."""
    sujo = {
        "type": "object",
        "title": "RecallParams",
        "additionalProperties": False,
        "properties": {"query": {"type": "string", "title": "Query"}},
        "$defs": {"X": {}},
    }
    limpo = _podar(sujo)
    assert limpo == {"type": "object", "properties": {"query": {"type": "string"}}}


def test_a_conversa_alcanca_a_memoria_inteira() -> None:
    """Ver, criar, corrigir e apagar — foi o que se pediu da página."""
    app = create_app()
    nomes = {d["name"] for d in _declaracoes(app.state.tools.registry)}
    assert {"memory__recall", "memory__list", "memory__remember", "memory__edit"} <= nomes
    for declaracao in _declaracoes(app.state.tools.registry):
        assert "additionalProperties" not in json.dumps(declaracao["parameters"])


def test_taxas_sao_as_que_o_live_exige() -> None:
    assert (TAXA_ENTRADA, TAXA_SAIDA) == (16000, 24000)
