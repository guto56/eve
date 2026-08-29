from __future__ import annotations

from eve.chat.phrasing import format_result


def test_time_is_written_out() -> None:
    frase = format_result(
        "system.time", {}, {"local": "2026-08-29T10:09:00-04:00", "utc": "", "timezone": ""}
    )
    assert frase == "São 10:09 de sábado, 29 de agosto de 2026."


def test_app_list_uses_natural_enumeration() -> None:
    valor = {"apps": [{"name": "Finder"}, {"name": "Chrome"}, {"name": "Terminal"}], "count": 3}
    assert format_result("app.list", {}, valor) == "Estão abertos: Finder, Chrome e Terminal."


def test_single_app_has_no_conjunction() -> None:
    assert format_result("app.list", {}, {"apps": [{"name": "Finder"}]}) == "Estão abertos: Finder."


def test_frontmost() -> None:
    assert format_result("app.frontmost", {}, {"name": "Safari"}) == "O app em foco é o Safari."
    assert "não consegui" in format_result("app.frontmost", {}, None).lower()


def test_volume() -> None:
    assert format_result("system.volume", {}, {"level": 81, "muted": False}) == (
        "O volume está em 81%."
    )
    assert "no mudo" in format_result("system.volume", {}, {"level": 0, "muted": True})


def test_clipboard() -> None:
    assert "«oi»" in format_result("clipboard.read", {}, {"text": "oi"})
    assert "vazia" in format_result("clipboard.read", {}, {"text": None})
    longo = format_result("clipboard.read", {}, {"text": "x" * 500})
    assert longo.endswith("…»")


def test_system_info() -> None:
    valor = {"chip": "Apple M1", "memory_gb": 8.0, "release": "27.0"}
    assert format_result("system.info", {}, valor) == (
        "Mac com chip Apple M1, 8 GB de RAM, macOS 27.0."
    )


def test_file_list_truncates() -> None:
    entradas = [{"name": f"a{i}.txt"} for i in range(20)]
    frase = format_result("file.list", {}, {"entries": entradas, "count": 20, "path": "/tmp"})
    assert "e mais 8" in frase


def test_empty_directory() -> None:
    frase = format_result("file.list", {}, {"entries": [], "count": 0, "path": "/tmp/vazio"})
    assert frase == "/tmp/vazio está vazia."


def test_action_templates() -> None:
    aberto = {"found": True, "opened": "Safari", "kind": "app"}
    assert format_result("app.open", {"name": "Safari"}, aberto) == "Abri Safari."
    uma_aba = {"opened": ["https://x.com"]}
    assert format_result("url.open", {}, uma_aba) == "Abri https://x.com."
    assert format_result("system.set_volume", {"level": 40}, {}) == "Volume em 40%."


def test_nao_achou_sugere_alternativa() -> None:
    nada = {"found": False, "query": "xyz", "similar": ["Xcode"], "suggestion": "..."}
    assert "Você quis dizer Xcode?" in format_result("app.open", {}, nada)

    sem_parecidos = {"found": False, "query": "xyz", "similar": [], "suggestion": "..."}
    assert "App Store" in format_result("app.open", {}, sem_parecidos)


def test_varias_abas() -> None:
    assert format_result("url.open", {}, {"opened": ["a", "b", "c"]}) == "Abri 3 abas."


def test_unknown_tool_returns_none() -> None:
    assert format_result("ferramenta.inventada", {}, {"x": 1}) is None


def test_unexpected_shape_returns_none_instead_of_raising() -> None:
    """Formato estranho não pode derrubar a resposta."""
    assert format_result("system.volume", {}, {"sem": "level"}) is None
    assert format_result("system.time", {}, {"local": "não é data"}) is None
    assert format_result("app.list", {}, "isso não é dict") is None
