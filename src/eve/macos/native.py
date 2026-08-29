"""Acesso ao Cocoa via PyObjC, carregado sob demanda.

Importar AppKit custa algumas centenas de milissegundos; o daemon não paga esse
preço no start, só na primeira ferramenta que precisar.
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any


@functools.cache
def _appkit() -> Any:
    import AppKit

    return AppKit


@functools.cache
def _foundation() -> Any:
    import Foundation

    return Foundation


def running_apps() -> list[dict[str, Any]]:
    """Aplicativos com interface, na ordem em que o sistema os lista."""
    workspace = _appkit().NSWorkspace.sharedWorkspace()
    apps = []
    for app in workspace.runningApplications():
        if app.activationPolicy() != 0:  # 0 = NSApplicationActivationPolicyRegular
            continue
        apps.append(
            {
                "name": app.localizedName(),
                "bundle_id": app.bundleIdentifier(),
                "pid": app.processIdentifier(),
                "active": bool(app.isActive()),
                "hidden": bool(app.isHidden()),
            }
        )
    return apps


def frontmost_app() -> dict[str, Any] | None:
    app = _appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:  # pragma: no cover - sempre há um app em foco
        return None
    return {
        "name": app.localizedName(),
        "bundle_id": app.bundleIdentifier(),
        "pid": app.processIdentifier(),
    }


def clipboard_read() -> str | None:
    appkit = _appkit()
    return appkit.NSPasteboard.generalPasteboard().stringForType_(appkit.NSPasteboardTypeString)


def clipboard_write(text: str) -> None:
    appkit = _appkit()
    board = appkit.NSPasteboard.generalPasteboard()
    board.clearContents()
    board.setString_forType_(text, appkit.NSPasteboardTypeString)


def move_to_trash(path: Path) -> Path:
    """Move para a Lixeira e devolve onde o arquivo foi parar.

    Usa ``NSFileManager`` em vez de mandar o Finder fazer, o que evita depender
    de permissão de automação — e, diferente de ``rm``, é reversível.
    """
    foundation = _foundation()
    url = foundation.NSURL.fileURLWithPath_(str(path))
    manager = foundation.NSFileManager.defaultManager()
    ok, resulting, error = manager.trashItemAtURL_resultingItemURL_error_(url, None, None)
    if not ok:
        detail = error.localizedDescription() if error else "motivo desconhecido"
        raise OSError(f"não foi possível mover para a Lixeira: {detail}")
    return Path(resulting.path()) if resulting else path


def accessibility_trusted() -> bool | None:
    """A EVE tem permissão de Acessibilidade? ``None`` se não deu para saber."""
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:  # pragma: no cover - depende do pacote instalado
        return None
    return bool(AXIsProcessTrusted())


async def move_to_trash_async(path: Path) -> Path:
    return await asyncio.to_thread(move_to_trash, path)
