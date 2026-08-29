"""Um navegador de verdade, controlado pela EVE.

O navegador fica aberto entre chamadas — abrir o Chromium custa segundos, e
uma tarefa de várias etapas ("abra o site, clique aqui, leia aquilo") ficaria
inviável se cada passo subisse um processo novo. Ele fecha sozinho depois de
um tempo parado.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from eve.logging import get_logger

log = get_logger(__name__)

IDLE_TIMEOUT = 300.0
"""Fecha o navegador depois de 5 minutos sem uso."""

NAV_TIMEOUT = 30_000
MAX_TEXT = 40_000


class BrowserError(RuntimeError):
    pass


@dataclass(frozen=True)
class PageState:
    url: str
    title: str

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title}


class BrowserSession:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._last_use = 0.0
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    @property
    def open(self) -> bool:
        return self._page is not None

    async def _ensure(self) -> Any:
        """Garante navegador e página prontos."""
        if self._page is not None:
            self._last_use = time.monotonic()
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - dependência declarada
            raise BrowserError(f"Playwright não instalado: {exc}") from exc

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            contexto = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="pt-BR",
            )
            self._page = await contexto.new_page()
            self._page.set_default_timeout(NAV_TIMEOUT)
        except Exception as exc:
            await self.close()
            raise BrowserError(_motivo(exc)) from exc

        self._last_use = time.monotonic()
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())
        log.info("navegador.aberto", headless=self.headless)
        return self._page

    async def _reap(self) -> None:
        """Fecha o navegador esquecido aberto."""
        while self._page is not None:
            await asyncio.sleep(30)
            if self._page is not None and time.monotonic() - self._last_use > IDLE_TIMEOUT:
                log.info("navegador.ocioso_fechado")
                await self.close()
                return

    # ------------------------------------------------------------- ações

    async def goto(self, url: str) -> PageState:
        async with self._lock:
            page = await self._ensure()
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                raise BrowserError(_motivo(exc)) from exc
            return await self._state(page)

    async def read(self, selector: str = "") -> dict[str, Any]:
        async with self._lock:
            page = self._require()
            alvo = page.locator(selector) if selector else page
            try:
                texto = await (alvo.inner_text() if selector else page.inner_text("body"))
            except Exception as exc:
                raise BrowserError(_motivo(exc)) from exc
            cortado = len(texto) > MAX_TEXT
            estado = await self._state(page)
            return {
                **estado.as_dict(),
                "text": texto[:MAX_TEXT],
                "truncated": cortado,
                "chars": len(texto),
            }

    async def links(self, limit: int = 60) -> dict[str, Any]:
        async with self._lock:
            page = self._require()
            achados = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => ({texto: e.innerText.trim().slice(0,90), href: e.href}))",
            )
            vistos, saida = set(), []
            for item in achados:
                if item["href"] in vistos or not item["texto"]:
                    continue
                vistos.add(item["href"])
                saida.append(item)
                if len(saida) >= limit:
                    break
            return {**(await self._state(page)).as_dict(), "links": saida, "count": len(saida)}

    async def click(self, selector: str) -> PageState:
        async with self._lock:
            page = self._require()
            try:
                await page.click(selector)
                await page.wait_for_load_state("domcontentloaded")
            except Exception as exc:
                raise BrowserError(_motivo(exc)) from exc
            return await self._state(page)

    async def fill(self, selector: str, value: str, submit: bool = False) -> PageState:
        async with self._lock:
            page = self._require()
            try:
                await page.fill(selector, value)
                if submit:
                    await page.press(selector, "Enter")
                    await page.wait_for_load_state("domcontentloaded")
            except Exception as exc:
                raise BrowserError(_motivo(exc)) from exc
            return await self._state(page)

    async def screenshot(self, path: str, full_page: bool = False) -> dict[str, Any]:
        async with self._lock:
            page = self._require()
            await page.screenshot(path=path, full_page=full_page)
            return {**(await self._state(page)).as_dict(), "path": path}

    async def state(self) -> dict[str, Any]:
        if self._page is None:
            return {"open": False}
        return {"open": True, **(await self._state(self._page)).as_dict()}

    # ----------------------------------------------------------- interno

    def _require(self) -> Any:
        if self._page is None:
            raise BrowserError("nenhuma página aberta — use browser.open primeiro")
        self._last_use = time.monotonic()
        return self._page

    async def _state(self, page: Any) -> PageState:
        return PageState(url=page.url, title=await page.title())

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for recurso, fechar in (
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if recurso is not None:
                try:
                    await getattr(recurso, fechar)()
                except Exception:
                    pass
        self._page = None
        self._browser = None
        self._playwright = None


def _motivo(exc: BaseException) -> str:
    """Mensagens do Playwright vêm com pilha; a primeira linha basta."""
    return str(exc).split("\n")[0][:220] or type(exc).__name__
