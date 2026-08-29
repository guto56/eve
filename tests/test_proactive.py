"""Proatividade: a política é o que separa assistente útil de incômodo."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from eve.events import Event
from eve.proactive.engine import ProactiveEngine, _mensagem
from eve.proactive.policy import Policy, Priority
from eve.tools.bus import ToolBus
from eve.tools.macos_tools import register_macos_tools
from eve.watch.manager import WatchManager


def evento(tipo: str, **payload) -> Event:
    return Event(type=tipo, payload=payload)


# ------------------------------------------------------------------ política


def test_padroes_sao_conservadores() -> None:
    """Quase tudo nasce silencioso; incomodar é a exceção."""
    politica = Policy()
    assert politica.priority_for("file.changed") is Priority.SILENT
    assert politica.priority_for("app.opened") is Priority.SILENT
    assert politica.priority_for("qualquer.coisa.nova") is Priority.SILENT
    assert politica.priority_for("build.failed") is Priority.HIGH


def test_regra_do_usuario_vence_o_padrao() -> None:
    politica = Policy(rules={"file.changed": "medium"})
    assert politica.priority_for("file.changed") is Priority.MEDIUM


def test_padrao_de_namespace() -> None:
    politica = Policy(rules={"build.*": "critical"})
    assert politica.priority_for("build.failed") is Priority.CRITICAL
    assert politica.priority_for("build.started") is Priority.CRITICAL


def test_regra_exata_vence_o_padrao_de_namespace() -> None:
    politica = Policy(rules={"build.*": "critical", "build.started": "silent"})
    assert politica.priority_for("build.started") is Priority.SILENT
    assert politica.priority_for("build.failed") is Priority.CRITICAL


def test_nivel_invalido_vira_silencio() -> None:
    """Configuração errada não pode virar barulho."""
    assert Policy(rules={"x.y": "urgentíssimo"}).priority_for("x.y") is Priority.SILENT


def test_silencioso_e_baixo_nao_notificam() -> None:
    politica = Policy(rules={"a.b": "silent", "c.d": "low"})
    assert politica.decide(evento("a.b")).notify is False
    assert politica.decide(evento("c.d")).notify is False


def test_medio_notifica_e_alto_toca_som() -> None:
    politica = Policy(rules={"a.b": "medium", "c.d": "high"})
    medio = politica.decide(evento("a.b"))
    alto = politica.decide(evento("c.d"))
    assert medio.notify is True
    assert medio.sound is False
    assert alto.sound is True


def test_repeticao_e_contida() -> None:
    """Um observador de arquivos dispara dezenas de eventos por minuto."""
    politica = Policy(rules={"file.changed": "medium"}, min_interval=60)
    agora = time.time()
    assert politica.decide(evento("file.changed"), agora).notify is True
    segunda = politica.decide(evento("file.changed"), agora + 5)
    assert segunda.notify is False
    assert "repetido" in segunda.reason
    assert politica.decide(evento("file.changed"), agora + 61).notify is True


def test_tipos_diferentes_nao_se_contem() -> None:
    politica = Policy(rules={"a.b": "medium", "c.d": "medium"}, min_interval=60)
    agora = time.time()
    assert politica.decide(evento("a.b"), agora).notify is True
    assert politica.decide(evento("c.d"), agora).notify is True


def test_horario_de_silencio() -> None:
    politica = Policy(rules={"a.b": "high"}, quiet_hours=(22, 8), min_interval=0)
    madrugada = time.mktime(time.struct_time((2026, 8, 29, 3, 0, 0, 5, 241, -1)))
    tarde = time.mktime(time.struct_time((2026, 8, 29, 15, 0, 0, 5, 241, -1)))
    assert politica.decide(evento("a.b"), madrugada).notify is False
    assert politica.decide(evento("a.b"), tarde).notify is True


def test_critico_atravessa_silencio_e_repeticao() -> None:
    """CRITICAL existe justamente para os casos em que interromper é o certo."""
    politica = Policy(rules={"a.b": "critical"}, quiet_hours=(0, 23), min_interval=9999)
    agora = time.time()
    assert politica.decide(evento("a.b"), agora).notify is True
    assert politica.decide(evento("a.b"), agora + 1).notify is True


def test_desligar_a_proatividade_cala_tudo() -> None:
    politica = Policy(rules={"a.b": "critical"}, enabled=False)
    assert politica.decide(evento("a.b")).notify is False


# -------------------------------------------------------------------- motor


async def test_notificacao_passa_pelo_tool_bus(tool_bus: ToolBus, event_bus) -> None:
    """A EVE não tem caminho privilegiado para falar: passa por permissão e auditoria."""
    register_macos_tools(tool_bus.registry)
    motor = ProactiveEngine(event_bus, tool_bus, Policy(rules={"a.b": "medium"}))
    assert await motor.handle(evento("a.b", message="olá")) is True
    assert motor.notified == 1
    entrada = tool_bus.audit.tail()[-1]
    assert entrada["tool"] == "system.notify"
    assert entrada["source"] == "proactive"


async def test_evento_silencioso_nao_chama_ferramenta(tool_bus: ToolBus, event_bus) -> None:
    register_macos_tools(tool_bus.registry)
    motor = ProactiveEngine(event_bus, tool_bus, Policy())
    assert await motor.handle(evento("file.changed")) is False
    assert tool_bus.audit.tail() == []


async def test_decisao_vira_evento_observavel(tool_bus: ToolBus, event_bus) -> None:
    motor = ProactiveEngine(event_bus, tool_bus, Policy())
    with event_bus.subscribe(["proactive.*"]) as sub:
        await motor.handle(evento("file.changed"))
        publicado = sub.queue.get_nowait()
    assert publicado.payload["priority"] == "silent"
    assert publicado.payload["notify"] is False


@pytest.mark.parametrize(
    ("tipo", "payload", "trecho"),
    [
        ("file.changed", {"path": "/a/projeto", "count": 3}, "3 arquivo(s)"),
        (
            "file.changed",
            {"path": "/a/projeto", "notable": [{"file": "uv.lock", "why": "d"}]},
            "uv.lock",
        ),
        ("git.changed", {"path": "/a/projeto"}, "Novo commit"),
        ("app.opened", {"app": "Safari"}, "Safari abriu"),
        ("app.closed", {"app": "Safari"}, "Safari fechou"),
    ],
)
def test_mensagem_e_uma_frase_nao_um_relatorio(tipo: str, payload: dict, trecho: str) -> None:
    assert trecho in _mensagem(evento(tipo, **payload))


# ------------------------------------------------------------- observadores


async def test_observador_de_caminho_inexistente_falha_sem_derrubar(event_bus) -> None:
    manager = WatchManager(event_bus)
    observador = await manager.add_path("fantasma", Path("/nao/existe/mesmo"))
    assert observador.running is False
    assert observador.error is not None
    await manager.aclose()


async def test_observador_de_arquivos_publica_mudancas(event_bus, tmp_path: Path) -> None:
    manager = WatchManager(event_bus)
    (tmp_path / "inicial.txt").write_text("x")
    await manager.add_path("teste", tmp_path)
    await asyncio.sleep(0.3)

    with event_bus.subscribe(["file.*"]) as sub:
        (tmp_path / "novo.txt").write_text("mudou")
        evento_recebido = await asyncio.wait_for(sub.queue.get(), 12)

    assert evento_recebido.type == "file.changed"
    assert evento_recebido.source == "watch:teste"
    assert evento_recebido.payload["count"] >= 1
    await manager.aclose()


def test_regra_do_usuario_vence_o_padrao_embutido() -> None:
    """Regressão: `build.* = critical` perdia para o padrão `build.failed = high`,
    porque a especificidade era comparada entre fontes diferentes."""
    politica = Policy(rules={"build.*": "critical"})
    assert politica.priority_for("build.failed") is Priority.CRITICAL

    # E o silêncio geral do usuário cala até o que o padrão levantaria.
    assert Policy(rules={"*": "silent"}).priority_for("build.failed") is Priority.SILENT
