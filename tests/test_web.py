"""Pesquisa e navegador: o essencial."""

from __future__ import annotations

import httpx2 as httpx
import pytest

from eve.browser.session import BrowserError, BrowserSession
from eve.tools.bus import ToolBus
from eve.tools.registry import ToolRegistry
from eve.tools.web_tools import OpenParams, register_web_tools
from eve.websearch.tavily import SearchError, TavilySearch

RESPOSTA = {
    "answer": "O céu é azul.",
    "results": [
        {"title": "Um", "url": "https://a.com", "content": "conteúdo", "score": 0.9},
        {"title": "Dois", "url": "https://b.com", "content": "outro", "score": 0.4},
    ],
}


def cliente(handler) -> TavilySearch:
    busca = TavilySearch("chave-de-teste")
    busca._client = httpx.AsyncClient(
        base_url="https://api.tavily.com",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer chave-de-teste"},
    )
    return busca


# ------------------------------------------------------------------ pesquisa


def test_credencial_obrigatoria() -> None:
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        TavilySearch("")


async def test_resposta_traz_fontes() -> None:
    busca = cliente(lambda request: httpx.Response(200, json=RESPOSTA))
    resposta = await busca.search("por que o céu é azul")
    await busca.aclose()
    dados = resposta.as_dict()
    assert dados["answer"] == "O céu é azul."
    assert dados["sources"] == ["https://a.com", "https://b.com"]
    assert dados["count"] == 2
    assert dados["results"][0]["score"] == 0.9


async def test_topico_de_noticias_manda_dias() -> None:
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        visto.update(json.loads(request.content))
        return httpx.Response(200, json=RESPOSTA)

    busca = cliente(handler)
    await busca.search("eleições", topic="news", days=3)
    await busca.aclose()
    assert visto["topic"] == "news"
    assert visto["days"] == 3


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (429, "rate_limit"), (500, "bad_request")],
)
async def test_erros_viram_categorias(status: int, kind: str) -> None:
    busca = cliente(lambda request: httpx.Response(status, json={}))
    with pytest.raises(SearchError) as exc:
        await busca.search("algo")
    await busca.aclose()
    assert exc.value.kind == kind


# ----------------------------------------------------------------- navegador


def test_url_sem_protocolo_ganha_https() -> None:
    assert OpenParams(url="exemplo.com").url == "https://exemplo.com"
    assert OpenParams(url="http://exemplo.com").url == "http://exemplo.com"


async def test_ler_sem_abrir_e_erro_claro() -> None:
    sessao = BrowserSession()
    with pytest.raises(BrowserError, match=r"browser\.open"):
        await sessao.read()
    assert await sessao.state() == {"open": False}


async def test_ferramentas_de_navegador_que_agem_pedem_confirmacao(
    registry: ToolRegistry,
) -> None:
    """Clicar numa página pode enviar formulário ou comprar algo."""
    register_web_tools(registry)
    for nome in ("browser.click", "browser.fill"):
        assert registry.get(nome).risk.value == "confirm"
    for nome in ("browser.open", "browser.read", "web.search"):
        assert registry.get(nome).risk.value == "safe"


async def test_busca_sem_credencial_falha_com_motivo(tool_bus: ToolBus) -> None:
    register_web_tools(tool_bus.registry)
    tool_bus.services.pop("search", None)
    resultado = await tool_bus.call("web.search", {"query": "algo"})
    assert resultado.ok is False
    assert "search" in resultado.error
