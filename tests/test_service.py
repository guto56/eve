"""Serviço de background: plist, leitura de estado e o ciclo com a CLI."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from eve import service


@pytest.fixture(autouse=True)
def _permitir_servico(monkeypatch):
    """Estes testes exercitam o módulo, sempre com launchctl simulado."""
    monkeypatch.delenv("EVE_SERVICE_DISABLED", raising=False)


def test_plist_aponta_para_o_daemon_sem_enfeite(tmp_path: Path) -> None:
    dados = service.build_plist(Path("/usr/local/bin/eve"))
    assert dados["ProgramArguments"] == ["/usr/local/bin/eve", "daemon"]
    assert dados["RunAtLoad"] is True
    # Reergue se cair, mas respeita uma saída limpa.
    assert dados["KeepAlive"] == {"SuccessfulExit": False}
    assert "PATH" in dados["EnvironmentVariables"]


def test_plist_e_gravavel_e_lido_de_volta(tmp_path: Path) -> None:
    destino = tmp_path / "ai.eve.plist"
    with destino.open("wb") as fh:
        plistlib.dump(service.build_plist(Path("/bin/eve")), fh)
    with destino.open("rb") as fh:
        assert plistlib.load(fh)["Label"] == service.LABEL


def test_le_pid_e_saida_do_launchctl() -> None:
    saida = """
    ai.eve = {
        pid = 4242
        last exit code = 0
        program = /usr/local/bin/eve
    }
    """
    assert service._campo(saida, "pid") == 4242
    assert service._campo(saida, "last exit code") == 0
    assert service._campo(saida, "inexistente") is None


def test_sem_comando_no_path_nao_instala(tmp_path: Path) -> None:
    with patch.object(service, "executable", return_value=None):
        estado = service.install()
    assert estado.installed is False
    assert "PATH" in estado.detail


def test_status_sem_plist_diz_que_nao_esta_instalado(tmp_path: Path) -> None:
    ausente = subprocess.CompletedProcess([], 1, "", "não encontrado")
    with (
        patch.object(service, "plist_path", return_value=tmp_path / "nao-existe.plist"),
        patch.object(service, "_launchctl", return_value=ausente),
    ):
        estado = service.status()
    assert estado.installed is False
    assert estado.as_dict()["loaded"] is False


def test_parar_sem_servico_carregado_nao_finge() -> None:
    with patch.object(service, "status", return_value=service.ServiceState(False, False)):
        assert service.stop() is False


def test_start_sem_instalacao_diz_o_motivo(tmp_path: Path) -> None:
    with patch.object(service, "plist_path", return_value=tmp_path / "nao-existe.plist"):
        estado = service.start()
    assert estado.installed is False
    assert "não instalado" in estado.detail
