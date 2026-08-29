"""Execução de AppleScript sem injeção.

O script é sempre uma constante do nosso código; os dados do usuário viajam
como ``argv``, nunca interpolados no texto do script. É a diferença entre

    display notification "<texto do usuário>"     ← injetável

e

    on run argv
        display notification (item 1 of argv)
    end run                                        ← seguro
"""

from __future__ import annotations

import asyncio
import subprocess


class AppleScriptError(RuntimeError):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def run_applescript(script: str, *args: str, timeout: float = 15.0) -> str:
    """Roda ``script``, passando ``args`` como ``argv``. Devolve a saída."""
    command = ["osascript", "-e", script]
    if args:
        command.append("--")
        command.extend(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - só fora do macOS
        raise AppleScriptError("osascript não encontrado", 127) from exc
    except subprocess.TimeoutExpired as exc:
        raise AppleScriptError(f"AppleScript passou de {timeout:g}s", 124) from exc

    if result.returncode != 0:
        message = result.stderr.strip() or f"osascript falhou ({result.returncode})"
        raise AppleScriptError(_humanize(message), result.returncode)
    return result.stdout.strip()


_HINTS = {
    "-1743": "o macOS negou a automação — autorize em Ajustes ▸ Privacidade ▸ Automação",
    "-1728": "o aplicativo não entendeu o comando (objeto inexistente)",
    "-600": "o aplicativo não está rodando",
    "-25211": (
        "acesso de acessibilidade negado — autorize em Ajustes ▸ Privacidade ▸ Acessibilidade"
    ),
}


def _humanize(message: str) -> str:
    for code, hint in _HINTS.items():
        if code in message:
            return f"{message} — {hint}"
    return message


async def run_applescript_async(
    script: str,
    *args: str,
    # O prazo é do osascript, não deste await: ele vira AppleScriptError, não
    # cancelamento. Por isso fica na assinatura.
    timeout: float = 15.0,  # noqa: ASYNC109
) -> str:
    """Versão assíncrona: o osascript roda em thread, o event loop segue livre."""
    return await asyncio.to_thread(run_applescript, script, *args, timeout=timeout)
