"""Gerência do processo do daemon: pidfile, start, stop e espera por saúde."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx

from eve.config import Settings
from eve.paths import paths


def read_pid(pid_file: Path | None = None) -> int | None:
    target = pid_file or paths().pid_file
    try:
        return int(target.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _reap_if_zombie(pid: int) -> bool:
    """Colhe o processo se ele for nosso filho e já tiver terminado.

    Sem isso um daemon encerrado dentro do mesmo processo que o criou vira
    zumbi, e ``os.kill(pid, 0)`` continua tendo sucesso indefinidamente.
    """
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        return False  # não é nosso filho — nada a colher
    return reaped == pid


def pid_alive(pid: int) -> bool:
    """Existe um processo vivo (não zumbi) com esse PID?"""
    if _reap_if_zombie(pid):
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # EPERM significa que o processo existe mas pertence a outro usuário.
        return exc.errno == errno.EPERM
    return True


def process_command(pid: int) -> str:
    """Linha de comando do processo, ou string vazia se ele não existir."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def is_eve_daemon(pid: int) -> bool:
    """O PID é mesmo um daemon da EVE?

    Protege contra pidfile obsoleto cujo PID foi reciclado pelo sistema —
    sem esta checagem um ``eve stop`` poderia matar um processo alheio.
    """
    return "eve.daemon" in process_command(pid)


def _home_of(pid: int) -> Path | None:
    """A ``EVE_HOME`` sob a qual o processo roda, lida do ambiente dele.

    ``None`` quando não dá para saber — e aí o processo não é tratado como
    nosso, porque errar para o lado de não matar é o único erro barato aqui.
    """
    try:
        result = subprocess.run(
            ["ps", "-E", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for pedaco in result.stdout.split():
        if pedaco.startswith("EVE_HOME="):
            return Path(pedaco.removeprefix("EVE_HOME=")).expanduser()
    # Sem a variável, o daemon usa o padrão — o mesmo que ``eve_home()`` usaria.
    return Path.home() / ".eve"


def daemon_pids() -> list[int]:
    """Daemons da EVE desta ``EVE_HOME``, estejam no pidfile ou não.

    O pidfile guarda um processo; a máquina pode ter mais — um daemon que
    sobreviveu a um encerramento mal-sucedido, outro subido de outro terminal.
    Um ``eve stop`` que olha só o pidfile responde "não está rodando" com a EVE
    no ar, que é exatamente a incerteza que o comando existe para eliminar.

    O filtro por ``EVE_HOME`` não é detalhe: sem ele um teste isolado varreria
    a EVE de verdade do usuário.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "eve[.]daemon"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    nossa = paths().home.resolve()
    meu = os.getpid()
    encontrados = []
    for linha in result.stdout.split():
        if not linha.isdigit() or int(linha) == meu:
            continue
        alheia = _home_of(int(linha))
        if alheia is not None and alheia.resolve() == nossa:
            encontrados.append(int(linha))
    return encontrados


def running_pid(pid_file: Path | None = None) -> int | None:
    pid = read_pid(pid_file)
    if pid is None:
        return None
    if not pid_alive(pid) or not is_eve_daemon(pid):
        clear_pid(pid_file)
        return None
    return pid


def write_pid(pid: int, pid_file: Path | None = None) -> None:
    target = pid_file or paths().pid_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(pid))


def clear_pid(pid_file: Path | None = None) -> None:
    target = pid_file or paths().pid_file
    target.unlink(missing_ok=True)


def base_url(settings: Settings) -> str:
    return f"http://{settings.server.host}:{settings.server.port}"


def probe_health(settings: Settings, timeout: float = 1.0) -> dict | None:
    """Retorna o corpo de ``/health`` se o daemon responder, senão ``None``."""
    try:
        response = httpx.get(f"{base_url(settings)}/health", timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def wait_for_health(settings: Settings, timeout: float = 20.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = probe_health(settings, timeout=0.5)
        if body is not None:
            return body
        time.sleep(0.15)
    return None


def spawn_daemon(settings: Settings) -> int:
    """Sobe o daemon desacoplado do terminal e devolve o PID."""
    p = paths().ensure()
    stdout = (p.logs / "daemon.out").open("ab")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "eve.daemon"],
            stdout=stdout,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            # O filho recarrega a configuração sozinho; passamos host e porta
            # explicitamente para garantir que pai e filho concordem.
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "EVE_SERVER__HOST": settings.server.host,
                "EVE_SERVER__PORT": str(settings.server.port),
            },
        )
    finally:
        stdout.close()
    write_pid(process.pid)
    return process.pid


def terminate(pid: int, timeout: float = 10.0) -> bool:
    """SIGTERM e, se preciso, SIGKILL. ``True`` se o processo morreu."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False
