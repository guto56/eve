from __future__ import annotations

from eve.router.routes import Route
from eve.router.toolsets import keywords_for, select_tools, tokenize
from eve.tools.registry import ToolRegistry


def test_tokenize_drops_stopwords_accents_and_plurals() -> None:
    """Plural vira singular para "issues" casar com "create_issue"."""
    assert tokenize("Abra a pasta de Documentos") == {"abra", "pasta", "documento"}
    assert tokenize("issues") == tokenize("issue")


def test_keywords_include_namespace_synonyms() -> None:
    palavras = keywords_for("file.trash", "Move para a Lixeira.")
    assert "arquivo" in palavras  # sinônimo do namespace
    assert "lixeira" in palavras  # da descrição
    assert "trash" in palavras  # do nome


def test_chat_route_gets_no_tools(full_registry: ToolRegistry) -> None:
    selection = select_tools(full_registry, Route.CHAT, "tudo bem?")
    assert selection.names == ()
    assert "não usa ferramentas" in selection.reason


def test_command_route_is_capped(full_registry: ToolRegistry) -> None:
    selection = select_tools(full_registry, Route.COMMAND, "qualquer coisa", limit=8)
    assert len(selection) == 8


def test_a_mais_relevante_vem_primeiro(full_registry: ToolRegistry) -> None:
    """O modelo alcança a primeira ferramenta plausível da lista."""
    nomes = select_tools(full_registry, Route.TASK, "pesquise o preço de um fone").names
    assert nomes[0] == "web.search"


def test_relevant_tools_are_chosen(full_registry: ToolRegistry) -> None:
    escolhidas = select_tools(full_registry, Route.COMMAND, "crie uma pasta chamada projetos").names
    assert "file.mkdir" in escolhidas
    assert all(n.startswith("file.") for n in escolhidas)


def test_volume_request_selects_volume_tools(full_registry: ToolRegistry) -> None:
    escolhidas = select_tools(full_registry, Route.COMMAND, "aumente o volume do som").names
    assert "system.set_volume" in escolhidas
    assert "system.volume" in escolhidas


def test_clipboard_request_selects_clipboard_tools(full_registry: ToolRegistry) -> None:
    escolhidas = select_tools(full_registry, Route.COMMAND, "copie esse texto").names
    assert "clipboard.write" in escolhidas


def test_selection_is_deterministic(full_registry: ToolRegistry) -> None:
    texto = "faça alguma coisa no computador"
    primeira = select_tools(full_registry, Route.COMMAND, texto).names
    assert primeira == select_tools(full_registry, Route.COMMAND, texto).names


def test_small_namespace_returns_everything(full_registry: ToolRegistry) -> None:
    selection = select_tools(full_registry, Route.WEB, "pesquise algo")
    assert selection.names == ("url.open", "web.extract", "web.search")
    assert "todas" in selection.reason


def test_web_route_now_has_search(full_registry: ToolRegistry) -> None:
    """A rota WEB deixou de ser um beco sem saída."""
    assert "web.search" in select_tools(full_registry, Route.WEB, "notícias de hoje").names


def test_empty_registry_is_handled() -> None:
    selection = select_tools(ToolRegistry(), Route.COMMAND, "abra algo")
    assert selection.names == ()
    assert "nenhuma ferramenta" in selection.reason


def test_filtering_actually_shrinks_the_prompt(full_registry: ToolRegistry) -> None:
    """A razão de existir do filtro: 23 ferramentas custam ~2.000 tokens."""
    todas = full_registry.wire_tools()
    filtradas = full_registry.wire_tools(
        select_tools(full_registry, Route.COMMAND, "crie uma pasta").names
    )
    assert len(todas) == 37
    assert len(filtradas) == 8
    assert len(str(filtradas)) < len(str(todas)) / 2
