"""Ferramentas nativas do macOS (spec §9, §12).

Cada uma declara o risco que corresponde ao estrago que pode causar: abrir um
app é SAFE, mandar um app fechar (que pode descartar trabalho não salvo) é
CONFIRM, apagar é CONFIRM e vai para a Lixeira, não para o vazio.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import Field, field_validator

from eve.config import Settings
from eve.macos import native
from eve.macos.osa import run_applescript_async
from eve.macos.safepath import resolve_safe_path
from eve.tools.registry import ToolRegistry
from eve.tools.registry import tool as tool_decorator
from eve.tools.spec import NoParams, RiskLevel, ToolContext, ToolParams

ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel", "facetime", "sms"})

#: Serviços que a maioria das pessoas usa pelo navegador. "Abra o YouTube" não
#: é um pedido para achar um aplicativo chamado YouTube — é para abrir o site.
#: O app instalado sempre vence: só caímos aqui quando ele não existe.
WEB_FALLBACKS: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://pt.wikipedia.org",
    "twitch": "https://www.twitch.tv",
}


def _safe(raw: str, settings: Settings, *, must_exist: bool = False) -> Path:
    return resolve_safe_path(
        raw,
        tuple(settings.files.allowed_roots),
        tuple(settings.files.denied),
        must_exist=must_exist,
    )


def _open_blocking(*args: str) -> None:
    """Chama ``/usr/bin/open`` com argumentos, sem shell."""
    result = subprocess.run(
        ["/usr/bin/open", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"open falhou ({result.returncode})")


async def _open(*args: str) -> None:
    await asyncio.to_thread(_open_blocking, *args)


# --------------------------------------------------------------------- esquemas


class AppParams(ToolParams):
    name: str = Field(description="Nome do aplicativo, ex.: Safari.", min_length=1, max_length=200)


class OpenAppParams(AppParams):
    background: bool = Field(default=False, description="Abrir sem trazer para a frente.")


class UrlParams(ToolParams):
    url: str = Field(description="Endereço a abrir.", max_length=4000)

    @field_validator("url")
    @classmethod
    def _check_scheme(cls, value: str) -> str:
        scheme = urlparse(value).scheme.lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            allowed = ", ".join(sorted(ALLOWED_URL_SCHEMES))
            raise ValueError(f"esquema não permitido: {scheme or '(nenhum)'}. Use um de: {allowed}")
        return value


class PathParams(ToolParams):
    path: str = Field(description="Caminho do arquivo ou pasta.", min_length=1, max_length=4096)


class ListParams(PathParams):
    include_hidden: bool = False
    limit: int = Field(default=200, ge=1, le=2000)


class ReadParams(PathParams):
    max_bytes: int | None = Field(default=None, ge=1, description="Limite desta leitura.")


class WriteParams(PathParams):
    content: str = Field(max_length=5_000_000)
    overwrite: bool = Field(default=False, description="Sobrescrever se já existir.")
    create_parents: bool = True


class MoveParams(ToolParams):
    source: str = Field(min_length=1, max_length=4096)
    destination: str = Field(min_length=1, max_length=4096)
    overwrite: bool = False


class ClipboardParams(ToolParams):
    text: str = Field(max_length=1_000_000)


class NotifyParams(ToolParams):
    message: str = Field(min_length=1, max_length=500)
    title: str = Field(default="EVE", max_length=100)
    subtitle: str = Field(default="", max_length=200)
    sound: bool = False


class VolumeParams(ToolParams):
    level: int = Field(ge=0, le=100, description="Volume de saída, de 0 a 100.")


class ScreenshotParams(ToolParams):
    path: str | None = Field(
        default=None, description="Onde salvar. Padrão: ~/.eve/data/screenshots."
    )
    interactive: bool = Field(default=False, description="Deixar o usuário escolher a área.")


# ------------------------------------------------------------------- registro


def register_macos_tools(registry: ToolRegistry) -> ToolRegistry:
    _register_apps(registry)
    _register_files(registry)
    _register_clipboard(registry)
    _register_system(registry)
    return registry


def _register_apps(registry: ToolRegistry) -> None:
    @tool_decorator(
        "app.open",
        description="Abre um aplicativo pelo nome.",
        params=OpenAppParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def app_open(params: OpenAppParams, _: ToolContext) -> dict[str, Any]:
        args = ["-a", params.name]
        if params.background:
            args.insert(0, "-g")
        try:
            await _open(*args)
        except RuntimeError:
            url = WEB_FALLBACKS.get(params.name.strip().lower())
            if url is None:
                raise
            await _open(url)
            return {"opened": params.name, "via": "web", "url": url}
        return {"opened": params.name, "via": "app", "background": params.background}

    @tool_decorator(
        "app.activate",
        description="Traz um aplicativo já aberto para a frente.",
        params=AppParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def app_activate(params: AppParams, _: ToolContext) -> dict[str, Any]:
        await _open("-a", params.name)
        return {"activated": params.name}

    @tool_decorator(
        "app.quit",
        description="Pede a um aplicativo que encerre. Trabalho não salvo pode ser perdido.",
        params=AppParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
        requires=("automation",),
    )
    async def app_quit(params: AppParams, _: ToolContext) -> dict[str, Any]:
        await run_applescript_async(
            "on run argv\n  tell application (item 1 of argv) to quit\nend run",
            params.name,
        )
        return {"quit": params.name}

    @tool_decorator(
        "app.list",
        description="Lista os aplicativos abertos com interface.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def app_list(_: NoParams, __: ToolContext) -> dict[str, Any]:
        apps = native.running_apps()
        return {"apps": apps, "count": len(apps)}

    @tool_decorator(
        "app.frontmost",
        description="Diz qual aplicativo está em foco agora.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def app_frontmost(_: NoParams, __: ToolContext) -> dict[str, Any] | None:
        return native.frontmost_app()

    @tool_decorator(
        "url.open",
        description="Abre um endereço no navegador padrão.",
        params=UrlParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def url_open(params: UrlParams, _: ToolContext) -> dict[str, Any]:
        await _open(params.url)
        return {"opened": params.url}


def _register_files(registry: ToolRegistry) -> None:
    @tool_decorator(
        "file.read",
        description="Lê um arquivo de texto.",
        params=ReadParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def file_read(params: ReadParams, ctx: ToolContext) -> dict[str, Any]:
        path = _safe(params.path, ctx.settings, must_exist=True)
        if path.is_dir():
            raise IsADirectoryError(f"{path} é uma pasta — use file.list")
        limit = min(
            params.max_bytes or ctx.settings.files.max_read_bytes, ctx.settings.files.max_read_bytes
        )
        raw = (await asyncio.to_thread(path.read_bytes))[: limit + 1]
        truncated = len(raw) > limit
        text = raw[:limit].decode("utf-8", errors="replace")
        return {
            "path": str(path),
            "content": text,
            "bytes": path.stat().st_size,
            "truncated": truncated,
        }

    @tool_decorator(
        "file.write",
        description="Escreve texto em um arquivo.",
        params=WriteParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
    )
    async def file_write(params: WriteParams, ctx: ToolContext) -> dict[str, Any]:
        path = _safe(params.path, ctx.settings)
        if path.exists() and not params.overwrite:
            raise FileExistsError(f"{path} já existe — passe overwrite=true para substituir")
        if params.create_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, params.content, encoding="utf-8")
        return {"path": str(path), "bytes": len(params.content.encode("utf-8"))}

    @tool_decorator(
        "file.list",
        description="Lista o conteúdo de uma pasta.",
        params=ListParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def file_list(params: ListParams, ctx: ToolContext) -> dict[str, Any]:
        path = _safe(params.path, ctx.settings, must_exist=True)
        if not path.is_dir():
            raise NotADirectoryError(f"{path} não é uma pasta")
        # Uma pasta enorme (ou num volume de rede) não pode travar o event loop.
        entries = await asyncio.to_thread(
            _scan_directory, path, params.include_hidden, params.limit
        )
        return {"path": str(path), "entries": entries, "count": len(entries)}

    @tool_decorator(
        "file.info",
        description="Tamanho, datas e tipo de um arquivo ou pasta.",
        params=PathParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def file_info(params: PathParams, ctx: ToolContext) -> dict[str, Any]:
        path = _safe(params.path, ctx.settings, must_exist=True)
        return await asyncio.to_thread(_entry, path, full=True)

    @tool_decorator(
        "file.mkdir",
        description="Cria uma pasta.",
        params=PathParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def file_mkdir(params: PathParams, ctx: ToolContext) -> dict[str, Any]:
        path = _safe(params.path, ctx.settings)
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path), "created": not existed}

    @tool_decorator(
        "file.move",
        description="Move ou renomeia um arquivo ou pasta.",
        params=MoveParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
    )
    async def file_move(params: MoveParams, ctx: ToolContext) -> dict[str, Any]:
        source = _safe(params.source, ctx.settings, must_exist=True)
        destination = _safe(params.destination, ctx.settings)
        if destination.exists() and not params.overwrite:
            raise FileExistsError(f"{destination} já existe")
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(source), str(destination))
        return {"from": str(source), "to": str(destination)}

    @tool_decorator(
        "file.copy",
        description="Copia um arquivo ou pasta sem sobrescrever nada.",
        params=MoveParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def file_copy(params: MoveParams, ctx: ToolContext) -> dict[str, Any]:
        source = _safe(params.source, ctx.settings, must_exist=True)
        destination = _safe(params.destination, ctx.settings)
        if destination.exists():
            raise FileExistsError(f"{destination} já existe — file.copy nunca sobrescreve")
        destination.parent.mkdir(parents=True, exist_ok=True)
        copier = shutil.copytree if source.is_dir() else shutil.copy2
        await asyncio.to_thread(copier, source, destination)
        return {"from": str(source), "to": str(destination)}

    @tool_decorator(
        "file.trash",
        description="Move para a Lixeira. Reversível — nada é apagado de verdade.",
        params=PathParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
    )
    async def file_trash(params: PathParams, ctx: ToolContext) -> dict[str, Any]:
        path = _safe(params.path, ctx.settings, must_exist=True)
        landed = await native.move_to_trash_async(path)
        return {"trashed": str(path), "now_at": str(landed)}


def _register_clipboard(registry: ToolRegistry) -> None:
    @tool_decorator(
        "clipboard.read",
        description="Lê o texto da área de transferência.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def clipboard_read(_: NoParams, __: ToolContext) -> dict[str, Any]:
        text = native.clipboard_read()
        return {"text": text, "empty": text is None}

    @tool_decorator(
        "clipboard.write",
        description="Coloca um texto na área de transferência.",
        params=ClipboardParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def clipboard_write(params: ClipboardParams, _: ToolContext) -> dict[str, Any]:
        native.clipboard_write(params.text)
        return {"written": len(params.text)}


def _register_system(registry: ToolRegistry) -> None:
    @tool_decorator(
        "system.notify",
        description="Mostra uma notificação do macOS.",
        params=NotifyParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        requires=("notifications",),
    )
    async def system_notify(params: NotifyParams, _: ToolContext) -> dict[str, Any]:
        script = (
            "on run argv\n"
            "  set msg to item 1 of argv\n"
            "  set ttl to item 2 of argv\n"
            "  set sub to item 3 of argv\n"
            "  set snd to item 4 of argv\n"
            '  if snd is "1" then\n'
            '    display notification msg with title ttl subtitle sub sound name "Ping"\n'
            "  else\n"
            "    display notification msg with title ttl subtitle sub\n"
            "  end if\n"
            "end run"
        )
        await run_applescript_async(
            script, params.message, params.title, params.subtitle, "1" if params.sound else "0"
        )
        return {"notified": params.message}

    @tool_decorator(
        "system.volume",
        description="Diz o volume de saída atual, de 0 a 100.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def system_volume(_: NoParams, __: ToolContext) -> dict[str, Any]:
        raw = await run_applescript_async("output volume of (get volume settings)")
        muted = await run_applescript_async("output muted of (get volume settings)")
        return {"level": int(raw), "muted": muted.lower() == "true"}

    @tool_decorator(
        "system.set_volume",
        description="Ajusta o volume de saída.",
        params=VolumeParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def system_set_volume(params: VolumeParams, _: ToolContext) -> dict[str, Any]:
        await run_applescript_async(
            "on run argv\n  set volume output volume (item 1 of argv as integer)\nend run",
            str(params.level),
        )
        return {"level": params.level}

    @tool_decorator(
        "system.screenshot",
        description="Captura a tela e salva em um arquivo.",
        params=ScreenshotParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        requires=("screen_recording",),
        timeout=60.0,
    )
    async def system_screenshot(params: ScreenshotParams, ctx: ToolContext) -> dict[str, Any]:
        from eve.paths import paths

        if params.path:
            target = _safe(params.path, ctx.settings)
        else:
            folder = paths().data / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            target = folder / f"tela-{stamp}.png"
        target.parent.mkdir(parents=True, exist_ok=True)

        args = ["/usr/sbin/screencapture", "-x"]
        if params.interactive:
            args.append("-i")
        args.append(str(target))
        result = await asyncio.to_thread(_run_blocking, args, 55)
        if result.returncode != 0 or not target.exists():
            message = result.stderr.strip() or "a captura não produziu arquivo"
            if "could not create image" in message.lower():
                message += (
                    " — falta permissão de Gravação de Tela em "
                    "Ajustes ▸ Privacidade ▸ Gravação de Tela"
                )
            raise RuntimeError(message)
        return {"path": str(target), "bytes": target.stat().st_size}


def _scan_directory(path: Path, include_hidden: bool, limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not include_hidden and child.name.startswith("."):
            continue
        if len(entries) >= limit:
            break
        entries.append(_entry(child))
    return entries


def _run_blocking(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _entry(path: Path, *, full: bool = False) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:  # pragma: no cover - arquivo sumiu no meio da listagem
        return {"name": path.name, "path": str(path), "error": "inacessível"}
    info: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "kind": "pasta" if path.is_dir() else "arquivo",
        "size": stat.st_size,
    }
    if full:
        info["modified"] = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        info["created"] = datetime.fromtimestamp(stat.st_birthtime, UTC).isoformat()
        info["mode"] = oct(stat.st_mode & 0o777)
        info["readable"] = os.access(path, os.R_OK)
        info["writable"] = os.access(path, os.W_OK)
    return info
