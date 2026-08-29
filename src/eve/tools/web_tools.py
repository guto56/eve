"""Ferramentas de web: pesquisa e navegador (spec §15, §16)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from eve.tools.registry import ToolRegistry
from eve.tools.registry import tool as tool_decorator
from eve.tools.spec import NoParams, RiskLevel, ToolContext, ToolParams


class SearchParams(ToolParams):
    query: str = Field(description="O que pesquisar.", min_length=2, max_length=400)
    max_results: int = Field(default=5, ge=1, le=15)
    topic: Literal["general", "news"] = Field(
        default="general", description="Use news para acontecimentos recentes."
    )
    depth: Literal["basic", "advanced"] = Field(
        default="basic", description="advanced é mais lento e mais completo."
    )


class ExtractParams(ToolParams):
    urls: list[str] = Field(description="Endereços a ler.", min_length=1, max_length=5)


class OpenParams(ToolParams):
    url: str = Field(description="Endereço a abrir.", min_length=4, max_length=2000)

    @field_validator("url")
    @classmethod
    def _http(cls, valor: str) -> str:
        if not valor.startswith(("http://", "https://")):
            valor = f"https://{valor}"
        return valor


class ReadParams(ToolParams):
    selector: str = Field(
        default="", description="Seletor CSS. Vazio lê a página inteira.", max_length=300
    )


class ClickParams(ToolParams):
    selector: str = Field(description="Seletor CSS do elemento.", min_length=1, max_length=300)


class FillParams(ToolParams):
    selector: str = Field(description="Seletor CSS do campo.", min_length=1, max_length=300)
    value: str = Field(description="Texto a digitar.", max_length=5000)
    submit: bool = Field(default=False, description="Pressionar Enter depois.")


class LinksParams(ToolParams):
    limit: int = Field(default=40, ge=1, le=200)


def register_web_tools(registry: ToolRegistry) -> ToolRegistry:
    # ----------------------------------------------------------- pesquisa

    @tool_decorator(
        "web.search",
        description="Pesquisa na internet e devolve resposta com as fontes.",
        params=SearchParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        timeout=60.0,
        keywords=(
            "pesquisar",
            "pesquise",
            "pesquisa",
            "buscar",
            "busque",
            "procurar",
            "procure",
            "noticia",
            "preco",
            "cotacao",
            "quanto",
            "custa",
            "descubra",
        ),
    )
    async def search(params: SearchParams, ctx: ToolContext) -> dict[str, Any]:
        resposta = await ctx.service("search").search(
            params.query,
            max_results=params.max_results,
            depth=params.depth,
            topic=params.topic,
        )
        return resposta.as_dict()

    @tool_decorator(
        "web.extract",
        description="Lê o conteúdo de páginas específicas, sem abrir navegador.",
        params=ExtractParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        timeout=90.0,
        keywords=("ler", "leia", "extrair", "conteudo", "artigo"),
    )
    async def extract(params: ExtractParams, ctx: ToolContext) -> dict[str, Any]:
        paginas = await ctx.service("search").extract(params.urls)
        return {"pages": paginas, "count": len(paginas)}

    # ---------------------------------------------------------- navegador

    @tool_decorator(
        "browser.open",
        description="Abre um endereço no navegador controlado pela EVE.",
        params=OpenParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        timeout=60.0,
    )
    async def browser_open(params: OpenParams, ctx: ToolContext) -> dict[str, Any]:
        estado = await ctx.service("browser").goto(params.url)
        return estado.as_dict()

    @tool_decorator(
        "browser.read",
        description="Lê o texto da página aberta no navegador.",
        params=ReadParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        timeout=45.0,
    )
    async def browser_read(params: ReadParams, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.service("browser").read(params.selector)

    @tool_decorator(
        "browser.links",
        description="Lista os links da página aberta.",
        params=LinksParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        timeout=45.0,
    )
    async def browser_links(params: LinksParams, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.service("browser").links(params.limit)

    @tool_decorator(
        "browser.click",
        description="Clica em um elemento da página. Pode enviar formulário ou comprar algo.",
        params=ClickParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
        timeout=60.0,
    )
    async def browser_click(params: ClickParams, ctx: ToolContext) -> dict[str, Any]:
        estado = await ctx.service("browser").click(params.selector)
        return estado.as_dict()

    @tool_decorator(
        "browser.fill",
        description="Preenche um campo da página. Com submit, envia o formulário.",
        params=FillParams,
        risk=RiskLevel.CONFIRM,
        registry=registry,
        reversible=False,
        timeout=60.0,
    )
    async def browser_fill(params: FillParams, ctx: ToolContext) -> dict[str, Any]:
        estado = await ctx.service("browser").fill(params.selector, params.value, params.submit)
        return estado.as_dict()

    @tool_decorator(
        "browser.screenshot",
        description="Captura a página aberta no navegador (não a tela do usuário).",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
        timeout=60.0,
    )
    async def browser_screenshot(_: NoParams, ctx: ToolContext) -> dict[str, Any]:
        from eve.paths import paths

        pasta = paths().data / "paginas"
        pasta.mkdir(parents=True, exist_ok=True)
        alvo = pasta / f"pagina-{datetime.now(UTC):%Y%m%d-%H%M%S}.png"
        return await ctx.service("browser").screenshot(str(alvo))

    @tool_decorator(
        "browser.state",
        description="Diz qual página está aberta no navegador da EVE.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def browser_state(_: NoParams, ctx: ToolContext) -> dict[str, Any]:
        return await ctx.service("browser").state()

    @tool_decorator(
        "browser.close",
        description="Fecha o navegador da EVE.",
        params=NoParams,
        risk=RiskLevel.SAFE,
        registry=registry,
    )
    async def browser_close(_: NoParams, ctx: ToolContext) -> dict[str, Any]:
        await ctx.service("browser").close()
        return {"closed": True}

    return registry
