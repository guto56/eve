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
        ("abra github.com", "url.open", {"urls": ["https://github.com"]}),
        ("acesse https://exemplo.com/x", "url.open", {"urls": ["https://exemplo.com/x"]}),
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


@pytest.mark.parametrize(
    ("texto", "tool", "args"),
    [
        (
            "lembre que eu prefiro reuniões de manhã",
            "memory.remember",
            {"content": "eu prefiro reuniões de manhã"},
        ),
        (
            "memorize que o projeto usa Python 3.13",
            "memory.remember",
            {"content": "o projeto usa Python 3.13"},
        ),
        ("o que eu te disse sobre o Módulo de Voz", "memory.recall", {"query": "o Módulo de Voz"}),
    ],
)
def test_memory_rules_preserve_the_original_text(texto: str, tool: str, args: dict) -> None:
    """A regra casa contra o texto sem acento, mas guarda o que foi escrito."""
    hit = apply_rules(texto)
    assert hit is not None
    assert hit.tool == tool
    assert hit.arguments == args


def test_memory_rule_declines_when_there_is_no_fact() -> None:
    assert apply_rules("lembre disso") is None


@pytest.mark.parametrize(
    "texto",
    ["abre no navegador", "abre essa pasta", "abra este arquivo", "abra o meu projeto"],
)
def test_preposicao_e_demonstrativo_nao_sao_nome_de_app(texto: str) -> None:
    """Regressão: virava `app.open{"name": "no navegador"}` e falhava."""
    hit = apply_rules(texto)
    assert hit is None or hit.tool != "app.open"


@pytest.mark.parametrize(
    ("texto", "caminho"),
    [
        ("o que tem no Downloads", "~/Downloads"),
        ("quais arquivos tem na pasta Documentos", "~/Documents"),
        ("o que tem na área de trabalho", "~/Desktop"),
        ("o que tem em ~/Documents/EVE", "~/Documents/EVE"),
    ],
)
def test_listar_pasta_resolve_o_que_e_inequivoco(texto: str, caminho: str) -> None:
    hit = apply_rules(texto)
    assert hit is not None, texto
    assert hit.tool == "file.list"
    assert hit.arguments == {"path": caminho}


@pytest.mark.parametrize("texto", ["o que tem na pasta EVE", "o que temos na pasta do projeto"])
def test_pasta_ambigua_vai_para_o_modelo(texto: str) -> None:
    """Um nome solto não é caminho: pode estar em qualquer lugar."""
    assert apply_rules(texto) is None


@pytest.mark.parametrize(
    ("texto", "topico"),
    [
        ("quem é você?", "identidade"),
        ("o que você faz?", "capacidades"),
        ("o que você pode fazer", "capacidades"),
        ("como rodar o EVE?", "comandos"),
        ("como eu te uso", "comandos"),
    ],
)
def test_perguntas_sobre_a_propria_eve(texto: str, topico: str) -> None:
    """Regressão: perguntada como rodá-la, a EVE inventava `uv run fastapi dev`."""
    hit = apply_rules(texto)
    assert hit is not None, texto
    assert hit.tool == "eve.about"
    assert hit.arguments == {"topic": topico}


@pytest.mark.parametrize("texto", ["abra a pasta Documentos", "abra o arquivo relatório.pdf"])
def test_pasta_e_arquivo_vao_para_a_ferramenta_que_resolve(texto: str) -> None:
    """Antes a regra recusava; hoje `app.open` olha o disco e decide o tipo."""
    hit = apply_rules(texto)
    assert hit is not None
    assert hit.tool == "app.open"


@pytest.mark.parametrize(
    "texto",
    [
        "abre o youtube e a cotação do dólar",
        "abra o Safari, o Chrome e o Finder",
        "abre o youtube em outra aba",
    ],
)
def test_alvo_composto_vai_para_o_modelo(texto: str) -> None:
    """Regressão: a frase inteira virava "nome de app"."""
    assert apply_rules(texto) is None
