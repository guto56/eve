"""Modo de terminal: acompanhamento ao vivo e encerramento que devolve a RAM."""

from __future__ import annotations

import logging

import pytest

from eve.cli.run import _linha
from eve.events import Event
from eve.logging import configure_logging


def evento(tipo: str, **payload) -> Event:
    return Event(type=tipo, payload=payload)


@pytest.mark.parametrize(
    ("tipo", "payload", "trecho"),
    [
        ("system.started", {}, "no ar"),
        ("message.received", {"text": "que horas são"}, "que horas são"),
        ("router.decided", {"route": "command", "decided_by": "rule", "fast_path": True}, "rota"),
        ("tool.requested", {"tool": "system.time", "args": {}}, "system.time"),
        ("tool.failed", {"error_kind": "not_found", "error": "sumiu"}, "not_found"),
        ("tool.confirmation_required", {"tool": "file.write"}, "autorizar"),
        ("memory.written", {"content": "gosta de café"}, "lembrei"),
        ("voice.listening", {"on": True}, "ouvindo"),
    ],
)
def test_cada_evento_vira_uma_linha(tipo: str, payload: dict, trecho: str) -> None:
    assert trecho in _linha(evento(tipo, **payload))


def test_ruido_interno_nao_aparece() -> None:
    """Deltas de token e conexões de cliente encheriam a tela sem informar."""
    assert _linha(evento("message.delta", text="olá")) == ""
    assert _linha(evento("client.connected")) == ""
    assert _linha(evento("proactive.evaluated", notify=False)) == ""


def test_console_silencioso_no_primeiro_plano(tmp_path) -> None:
    """No terminal quem informa é o acompanhamento, não o log interno."""
    configure_logging(log_file=tmp_path / "eve.log", force=True, console=False)
    tipos = [type(h).__name__ for h in logging.getLogger().handlers]
    assert "StreamHandler" not in tipos
    assert "FileHandler" in tipos

    configure_logging(log_file=tmp_path / "eve.log", force=True, console=True)
    assert "StreamHandler" in [type(h).__name__ for h in logging.getLogger().handlers]


async def test_encerrar_devolve_a_memoria_do_modelo(settings, secret_store) -> None:
    """Numa máquina de 8 GB, deixar o modelo carregado depois de parar a EVE
    é cobrar por um serviço que o usuário desligou."""
    from unittest.mock import AsyncMock

    from eve.ai.manager import ProviderManager

    manager = ProviderManager(settings, secret_store)
    local = AsyncMock()
    manager._local = local

    await manager.aclose()
    assert local.unload.await_count >= 1
    local.aclose.assert_awaited()


async def test_dá_para_encerrar_sem_descarregar(settings, secret_store) -> None:
    from unittest.mock import AsyncMock

    from eve.ai.manager import ProviderManager

    manager = ProviderManager(settings, secret_store)
    local = AsyncMock()
    manager._local = local

    await manager.aclose(free_memory=False)
    local.unload.assert_not_awaited()
