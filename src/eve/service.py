"""Serviço de background com launchd (spec §14, §26).

Depois do login, a EVE sobe sozinha. A interface web não precisa estar aberta
para o Core funcionar — é o que permite wake word, observadores e notificações
existirem sem ninguém olhando.

Usamos ``launchctl bootstrap`` em vez do antigo ``load``: o antigo sai com
código 0 mesmo quando não carregou nada, o que transforma um erro de instalação
em um mistério silencioso.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from eve.logging import get_logger
from eve.paths import paths

log = get_logger(__name__)

LABEL = "ai.eve"


def _isolado() -> bool:
    """Os testes não podem mexer no launchd do usuário.

    Sem isto, um `eve stop` de teste descarrega o serviço real da máquina e a
    suíte passa a brigar com a EVE de verdade pela porta.
    """
    return os.environ.get("EVE_SERVICE_DISABLED", "").lower() in ("1", "true", "sim")


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def executable() -> Path | None:
    """Onde está o comando `eve`. ``None`` se ele não estiver no PATH."""
    achado = shutil.which("eve")
    return Path(achado) if achado else None


@dataclass(frozen=True)
class ServiceState:
    installed: bool
    loaded: bool
    pid: int | None = None
    last_exit: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "loaded": self.loaded,
            "pid": self.pid,
            "last_exit": self.last_exit,
            "detail": self.detail,
            "plist": str(plist_path()),
        }


def build_plist(comando: Path) -> dict[str, object]:
    p = paths().ensure()
    return {
        "Label": LABEL,
        # `eve daemon` roda o Core sem enfeite de terminal — o modo com
        # acompanhamento é para quem está olhando.
        "ProgramArguments": [str(comando), "daemon"],
        "RunAtLoad": True,
        # Reinicia se cair, mas não fica em laço quando saiu de propósito.
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(p.logs / "service.out"),
        "StandardErrorPath": str(p.logs / "service.err"),
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(Path.home()),
        },
    }


def install(comando: Path | None = None) -> ServiceState:
    """Instala e sobe o agente. Idempotente."""
    if _isolado():
        return ServiceState(False, False, detail="serviço desativado neste ambiente")
    alvo = comando or executable()
    if alvo is None:
        return ServiceState(False, False, detail="comando `eve` não está no PATH")

    destino = plist_path()
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("wb") as fh:
        plistlib.dump(build_plist(alvo), fh)

    # Recarregar sem duplicar. O `bootout` volta antes de o launchd terminar
    # de desmontar; subir em cima disso falha com um erro pouco explicativo,
    # então esperamos o agente sumir de verdade.
    _launchctl("bootout", f"{_dominio()}/{LABEL}")
    _esperar_descarregar()

    resultado = _launchctl("bootstrap", _dominio(), str(destino))
    if resultado.returncode != 0:
        _esperar_descarregar()
        resultado = _launchctl("bootstrap", _dominio(), str(destino))
    if resultado.returncode != 0:
        return ServiceState(True, False, detail=_motivo(resultado))
    return status()


def _esperar_descarregar(limite: float = 6.0) -> bool:
    """Espera o launchd soltar o agente. ``False`` se ele insistir em ficar."""
    import time

    prazo = time.monotonic() + limite
    while time.monotonic() < prazo:
        if _launchctl("print", f"{_dominio()}/{LABEL}").returncode != 0:
            return True
        time.sleep(0.25)
    return False


def uninstall(remove_file: bool = True) -> ServiceState:
    """Para o agente e, por padrão, remove o arquivo."""
    if _isolado():
        return status()
    _launchctl("bootout", f"{_dominio()}/{LABEL}")
    _esperar_descarregar()
    if remove_file:
        plist_path().unlink(missing_ok=True)
    return status()


def status() -> ServiceState:
    if _isolado():
        return ServiceState(False, False, detail="serviço desativado neste ambiente")
    instalado = plist_path().exists()
    resultado = _launchctl("print", f"{_dominio()}/{LABEL}")
    if resultado.returncode != 0:
        return ServiceState(instalado, False, detail="não carregado")

    pid = _campo(resultado.stdout, "pid")
    saida = _campo(resultado.stdout, "last exit code")
    return ServiceState(
        installed=instalado,
        loaded=True,
        pid=pid,
        last_exit=saida,
        detail="rodando" if pid else "carregado, sem processo",
    )


def restart() -> ServiceState:
    """Reinicia o agente. Sem serviço carregado, não faz nada."""
    if not status().loaded:
        return status()
    _launchctl("kickstart", "-k", f"{_dominio()}/{LABEL}")
    return status()


def installed() -> bool:
    """O serviço está instalado, mesmo que parado no momento."""
    return not _isolado() and plist_path().exists()


def stop() -> bool:
    """Para a EVE sem desinstalar o serviço.

    Mandar sinal não resolve: o launchd trata "morto por sinal" como queda e
    reergue o agente na hora, independentemente de `KeepAlive`. Parar de
    verdade é descarregar — o arquivo continua instalado, então ela volta no
    próximo login ou com `eve start`.
    """
    if not status().loaded:
        return False
    _launchctl("bootout", f"{_dominio()}/{LABEL}")
    return _esperar_descarregar()


def start() -> ServiceState:
    """Sobe de novo um serviço instalado que está parado."""
    destino = plist_path()
    if not destino.exists():
        return ServiceState(False, False, detail="serviço não instalado")
    if status().loaded:
        _launchctl("kickstart", f"{_dominio()}/{LABEL}")
        return status()
    resultado = _launchctl("bootstrap", _dominio(), str(destino))
    if resultado.returncode != 0:
        return ServiceState(True, False, detail=_motivo(resultado))
    return status()


def _dominio() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/bin/launchctl", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def _campo(saida: str, chave: str) -> int | None:
    for linha in saida.splitlines():
        limpa = linha.strip()
        if limpa.startswith(f"{chave} = "):
            valor = limpa.split("=", 1)[1].strip()
            try:
                return int(valor)
            except ValueError:
                return None
    return None


def _motivo(resultado: subprocess.CompletedProcess[str]) -> str:
    texto = (resultado.stderr or resultado.stdout).strip()
    return texto.splitlines()[0][:180] if texto else f"launchctl saiu com {resultado.returncode}"
