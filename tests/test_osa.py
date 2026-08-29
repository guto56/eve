from __future__ import annotations

import pytest

from eve.macos.osa import AppleScriptError, run_applescript, run_applescript_async

RETURN_FIRST = "on run argv\n  return item 1 of argv\nend run"


def test_returns_output() -> None:
    assert run_applescript('return "olá"') == "olá"


def test_arguments_are_data_not_code() -> None:
    """O texto do usuário nunca é interpretado como script."""
    perigoso = 'x" & (do shell script "echo INVADIDO") & "y'
    assert run_applescript(RETURN_FIRST, perigoso) == perigoso


def test_quotes_and_shell_metacharacters_survive_intact() -> None:
    texto = 'aspas " ; $(whoami) `id` \\ barra'
    assert run_applescript(RETURN_FIRST, texto) == texto


def test_error_is_raised_with_message() -> None:
    with pytest.raises(AppleScriptError) as exc:
        run_applescript("erro de sintaxe aqui (((")
    assert exc.value.code != 0


def test_timeout_becomes_an_error() -> None:
    with pytest.raises(AppleScriptError, match="passou de"):
        run_applescript("delay 5", timeout=0.3)


async def test_async_version_returns_the_same() -> None:
    assert await run_applescript_async(RETURN_FIRST, "assíncrono") == "assíncrono"
