"""Voz: o essencial — corte de frase, protocolo e degradação sem credencial."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve.daemon.app import create_app
from eve.voice.session import MIN_PARA_FALAR, _corta_frase
from eve.voice.stt import SpeechToText, Transcript
from eve.voice.tts import TextToSpeech


@pytest.mark.parametrize(
    ("acumulado", "fala", "resto"),
    [
        ("curto.", "", "curto."),  # curto demais soa picotado
        # O espaço depois do ponto fica no corte, não no resto.
        ("a" * 45 + ". resto que continua", "a" * 45 + ".", "resto que continua"),
        ("Uma frase sem fim ainda chegando aos poucos e sem pontuação", "", None),
        ("Primeira frase completa aqui, com pontuação! E o resto", "", None),
    ],
)
def test_corte_de_frase(acumulado: str, fala: str, resto: str | None) -> None:
    trecho, sobra = _corta_frase(acumulado)
    if resto is None:
        # Sem frase pronta o suficiente: nada é falado ainda.
        assert trecho == "" or trecho.endswith(("!", ".", "?"))
    else:
        assert trecho == fala
        assert sobra == resto


def test_frase_longa_e_completa_vira_fala() -> None:
    texto = "Abri o Safari e trouxe a janela para a frente, como você pediu."
    trecho, resto = _corta_frase(texto)
    assert trecho == texto.strip()
    assert resto == ""


def test_nunca_corta_antes_do_minimo() -> None:
    trecho, _ = _corta_frase("Oi." + " " * 5)
    assert trecho == ""
    assert MIN_PARA_FALAR > 10


def test_transcricao_vazia_e_inutil() -> None:
    assert Transcript("", True, True).usable is False
    assert Transcript("   ", True, True).usable is False
    assert Transcript("oi", False, False).usable is True


def test_clientes_exigem_credencial() -> None:
    with pytest.raises(ValueError, match="DEEPGRAM"):
        SpeechToText("")
    with pytest.raises(ValueError, match="CARTESIA_API_KEY"):
        TextToSpeech("", "voz")
    with pytest.raises(ValueError, match="CARTESIA_VOICE_ID"):
        TextToSpeech("chave", "")


def test_websocket_de_voz_recusa_sem_credencial() -> None:
    """Sem chave, a EVE diz o que falta em vez de travar o navegador."""
    with TestClient(create_app()) as client, client.websocket_connect("/ws/voice") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["fatal"] is True
    assert "DEEPGRAM_API_KEY" in frame["error"]


def test_status_reporta_voz_sem_credencial() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/status").json()
    assert data["components"]["voice"] == "sem credencial"


def test_cabecalho_wav() -> None:
    tts = TextToSpeech("chave", "voz", sample_rate=24000)
    header = tts.wav_header(1000)
    assert header[:4] == b"RIFF"
    assert header[8:12] == b"WAVE"
    assert len(header) == 44


def test_marcacao_nao_vai_para_o_sintetizador() -> None:
    """ "**Ilha de Páscoa**" vira "asterisco asterisco Ilha de Páscoa" na voz.

    O texto está certo para os olhos e errado para o ouvido, e o modelo escreve
    markdown sem pensar."""
    from eve.voice.session import so_fala

    assert so_fala("O oceano tem a **Ilha de Páscoa**.") == "O oceano tem a Ilha de Páscoa."
    assert so_fala("Use `eve start` para começar.") == "Use eve start para começar."
    assert so_fala("Isso é *importante* mesmo.") == "Isso é importante mesmo."
    assert so_fala("Veja [a documentação](https://x.com) depois.") == (
        "Veja a documentação depois."
    )
    assert so_fala("# Título") == "Título"
    # Texto sem marcação passa intacto — inclusive asterisco no meio da palavra.
    assert so_fala("nada muda aqui") == "nada muda aqui"
    assert so_fala("2 * 3 = 6") == "2 * 3 = 6"
