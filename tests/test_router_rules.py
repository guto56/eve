from __future__ import annotations

import pytest

from eve.router.routes import Route
from eve.router.rules import apply_rules, normalize, strip_accents


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Abra o Safari!", "abra o safari"),
        ("  QUE   HORAS   SÃO?  ", "que horas sao"),
        ("Coração", "coracao"),
        ("", ""),
    ],
)
def test_normalize(bruto: str, esperado: str) -> None:
    assert normalize(bruto) == esperado


def test_strip_accents() -> None:
    assert strip_accents("ação, coração, ímã") == "acao, coracao, ima"


# ------------------------------------------------------------- caminho rápido


@pytest.mark.parametrize(
    ("texto", "tool", "args"),
    [
        ("abra o Safari", "app.open", {"name": "Safari"}),
        ("Abre o Spotify", "app.open", {"name": "Spotify"}),
        ("inicie o Terminal", "app.open", {"name": "Terminal"}),
        ("open Finder", "app.open", {"name": "Finder"}),
        ("feche o Chrome", "app.quit", {"name": "Chrome"}),
        ("encerre o Editor de Texto", "app.quit", {"name": "Editor de Texto"}),
        ("abra github.com", "url.open", {"url": "https://github.com"}),
        ("acesse https://exemplo.com/x", "url.open", {"url": "https://exemplo.com/x"}),
        ("que horas são", "system.time", {}),
        ("coloque o volume em 40", "system.set_volume", {"level": 40}),
        ("ajuste o volume para 0", "system.set_volume", {"level": 0}),
        ("qual o volume", "system.volume", {}),
        ("quanto de RAM tem esse Mac", "system.info", {}),
        ("qual o chip desse mac", "system.info", {}),
        ("quais aplicativos estão abertos", "app.list", {}),
        ("qual app está em foco", "app.frontmost", {}),
        ("o que tem na área de transferência", "clipboard.read", {}),
    ],
)
def test_rules_that_resolve_the_action(texto: str, tool: str, args: dict) -> None:
    hit = apply_rules(texto)
    assert hit is not None, texto
    assert hit.tool == tool
    assert hit.arguments == args
    assert hit.route is Route.COMMAND


def test_accents_and_case_survive_in_arguments() -> None:
    hit = apply_rules("copie 'olá, coração' para a área de transferência")
    assert hit is not None
    assert hit.tool == "clipboard.write"
    assert hit.arguments == {"text": "olá, coração"}


def test_url_wins_over_app_when_the_target_looks_like_an_address() -> None:
    assert apply_rules("abra github.com").tool == "url.open"
    assert apply_rules("abra o Safari").tool == "app.open"


# ----------------------------------------------------------- rotas sem ação


@pytest.mark.parametrize(
    ("texto", "rota"),
    [
        ("oi", Route.CHAT),
        ("bom dia", Route.CHAT),
        ("obrigado!", Route.CHAT),
        ("pesquise sobre o novo macOS", Route.WEB),
        ("quais as notícias de hoje", Route.WEB),
        ("lembre que eu prefiro café sem açúcar", Route.MEMORY),
        ("o que eu te disse ontem", Route.MEMORY),
    ],
)
def test_rules_that_only_pick_a_route(texto: str, rota: Route) -> None:
    hit = apply_rules(texto)
    assert hit is not None, texto
    assert hit.route is rota
    assert hit.tool is None


# ------------------------------------------------------- quando NÃO disparar


@pytest.mark.parametrize(
    "texto",
    [
        "analise meu projeto e descubra por que o build falha",
        "me explique o que é recursão",
        "crie uma pasta chamada projetos",
        "abra a pasta Documentos",
        "abra o arquivo relatório.pdf",
        "",
        "   ",
    ],
)
def test_conservative_rules_decline(texto: str) -> None:
    """Na dúvida a regra não dispara — um falso positivo vira ação errada."""
    hit = apply_rules(texto)
    assert hit is None or hit.tool is None


def test_multi_step_request_is_not_a_web_search() -> None:
    """'pesquise ... compare ... recomende' é tarefa, não busca."""
    assert apply_rules("pesquise três fones, compare preços e me recomende um") is None


def test_out_of_range_volume_declines() -> None:
    assert apply_rules("coloque o volume em 500") is None


def test_absurdly_long_target_declines() -> None:
    assert apply_rules("abra o " + "x" * 200) is None
