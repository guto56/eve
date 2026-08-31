"""Aplicação FastAPI do Core da EVE.

A interface web e a CLI são apenas clientes desta aplicação; todo o estado
vive aqui, no daemon.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from eve import __version__
from eve.agent.manager import TaskManager
from eve.agent.runner import AgentRunner
from eve.ai.manager import ProviderManager
from eve.browser.session import BrowserSession
from eve.bus import EventBus
from eve.chat.engine import ChatEngine
from eve.config import Settings, load_settings
from eve.daemon.routes import (
    ai,
    chat,
    extensions,
    health,
    live,
    logs,
    memory,
    proactive,
    tasks,
    tools,
    voice,
    web,
    ws,
)
from eve.events import EventType
from eve.logging import get_logger
from eve.mcp.client import MCPServerConfig
from eve.mcp.manager import MCPManager
from eve.memory.embeddings import Embedder
from eve.memory.manager import MemoryManager
from eve.memory.store import MemoryStore
from eve.memory.vault import Vault
from eve.memory.watcher import CofreObserver
from eve.paths import paths
from eve.permissions import PermissionEngine
from eve.proactive.engine import ProactiveEngine
from eve.proactive.policy import Policy
from eve.router.router import Router
from eve.secrets import build_store
from eve.skills.manager import SkillManager
from eve.tools.approvals import ApprovalBroker
from eve.tools.audit import AuditLog
from eve.tools.builtin import register_builtin_tools
from eve.tools.bus import ToolBus
from eve.tools.calendar_tools import register_calendar_tools
from eve.tools.macos_tools import register_macos_tools
from eve.tools.memory_tools import register_memory_tools
from eve.tools.registry import ToolRegistry
from eve.tools.spec import RiskLevel
from eve.tools.web_tools import register_web_tools
from eve.watch.manager import WatchManager
from eve.websearch.tavily import TavilySearch

log = get_logger(__name__)


PRAZO_ENCERRAMENTO = 5.0
"""Quanto cada etapa do desligamento pode demorar antes de ser abandonada.

Um servidor MCP que não responde — ``npx`` baixando o pacote na primeira vez,
por exemplo — segurava o processo inteiro, e o Ctrl+C parecia não funcionar.
Nenhuma faxina vale prender o computador do usuário.
"""


async def _com_prazo(etapa: str, tarefa: Any, prazo: float = PRAZO_ENCERRAMENTO) -> Any:
    """Espera ``tarefa`` até ``prazo``; desiste dela sem derrubar o resto."""
    try:
        return await asyncio.wait_for(tarefa, timeout=prazo)
    except TimeoutError:
        log.warning("core.encerramento_demorou", etapa=etapa, prazo=prazo)
    except Exception as exc:  # pragma: no cover - defensivo no caminho de saída
        log.warning("core.encerramento_falhou", etapa=etapa, error=str(exc))
    return None


async def _atender_telefone(app: FastAPI) -> Any:
    """Sobe o app de telefonia numa porta própria, se estiver ligado.

    Porta própria porque é ela que o túnel expõe: pôr as rotas do Twilio no
    Core deixaria `/api` inteira na internet junto.
    """
    telefone = app.state.settings.phone
    app.state.telefone_tarefa = None
    if not telefone.enabled:
        return None

    import uvicorn

    from eve.phone.app import criar_app_telefone

    config = uvicorn.Config(
        app=criar_app_telefone(app),
        host="127.0.0.1",
        port=telefone.port,
        log_config=None,
        access_log=False,
    )
    servidor = uvicorn.Server(config)

    async def servir() -> None:
        """A telefonia é opcional; não pode derrubar o assistente.

        A porta pode estar ocupada — por outro EVE, por outra coisa qualquer.
        Sem isto, um `address already in use` no telefone matava o Core, e
        quem não usa telefone nem entenderia por quê.
        """
        try:
            await servidor.serve()
        except asyncio.CancelledError:
            raise
        except (Exception, SystemExit) as exc:
            # SystemExit, e não só Exception: o uvicorn sai por `sys.exit`
            # quando não consegue a porta, e SystemExit não é Exception.
            log.warning("telefone.nao_subiu", porta=telefone.port, error=str(exc)[:160])

    app.state.telefone_tarefa = asyncio.create_task(servir())
    log.info("telefone.no_ar", porta=telefone.port, permitidos=len(telefone.allowed_callers))
    return servidor


async def _conferir_cofre(app: FastAPI) -> None:
    """Acerta o índice com os arquivos e passa a acompanhá-los ao vivo."""
    await app.state.memory.reconciliar()
    if app.state.memory.sync is None:
        return
    observador = CofreObserver(app.state.bus, app.state.memory.sync)
    app.state.cofre = observador
    await observador.start()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bus: EventBus = app.state.bus
    app.state.started_at = time.time()
    # Servidores MCP podem demorar (npx baixa na primeira vez); subir em
    # segundo plano deixa o Core disponível imediatamente.
    conexoes = asyncio.create_task(
        app.state.skills.connect_enabled(_standalone_servers(app.state.settings))
    )

    await app.state.proactive.start()
    await _start_watchers(app)

    # O cofre pode ter mudado com a EVE desligada — alguém editou no Obsidian,
    # apagou uma nota, escreveu outra à mão. O índice acerta o passo agora, e
    # o observador mantém o passo enquanto ela roda.
    conferencia = asyncio.create_task(_conferir_cofre(app))
    telefone = await _atender_telefone(app)

    await bus.emit(EventType.SYSTEM_STARTED, version=__version__, pid=os.getpid())
    log.info(
        "core.started", version=__version__, pid=os.getpid(), tools=len(app.state.tools.registry)
    )
    try:
        yield
    finally:
        # Primeiro o aviso, depois a faxina: quem apertou Ctrl+C precisa ver
        # que foi ouvido agora, não depois que tudo fechou.
        await bus.emit(EventType.SYSTEM_STOPPING)
        log.info("core.stopping")

        conexoes.cancel()
        conferencia.cancel()
        if app.state.cofre is not None:
            await _com_prazo("cofre.observador", app.state.cofre.stop())
        if telefone is not None:
            telefone.should_exit = True
            await _com_prazo("telefone", app.state.telefone_tarefa)
        await _com_prazo("mcp.conexoes", asyncio.gather(conexoes, return_exceptions=True))
        await _com_prazo("cofre", asyncio.gather(conferencia, return_exceptions=True))
        await _com_prazo("watchers", app.state.watch.aclose())
        await _com_prazo("proatividade", app.state.proactive.stop())
        await _com_prazo("tarefas", app.state.tasks.aclose())
        await _com_prazo("mcp", app.state.mcp.aclose())
        await _com_prazo("navegador", app.state.browser.close())
        if app.state.search is not None:
            await _com_prazo("pesquisa", app.state.search.aclose())
        removidas = await _com_prazo("memoria.faxina", app.state.memory.housekeeping())
        if removidas:
            log.info("memoria.expiradas_removidas", count=removidas)
        cancelled = app.state.tools.approvals.deny_all("o Core está encerrando")
        if cancelled:
            log.info("approvals.cancelled", count=cancelled)
        await _com_prazo("memoria", app.state.memory.aclose())
        # Por último e com prazo maior: é o que devolve a RAM do modelo local.
        await _com_prazo("provedores", app.state.providers.aclose(), prazo=8.0)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="EVE Core",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.bus = EventBus()
    app.state.started_at = time.time()
    app.state.secrets = build_store(paths().ensure().home / "secrets.json")
    app.state.providers = ProviderManager(settings, app.state.secrets)
    app.state.memory = build_memory(settings, app.state.bus, app.state.providers)
    app.state.cofre = None
    app.state.browser = BrowserSession()
    app.state.search = _build_search(app.state.secrets)
    app.state.tools = build_tool_bus(
        settings,
        app.state.bus,
        {
            "memory": app.state.memory,
            "browser": app.state.browser,
            "search": app.state.search,
        },
    )
    app.state.mcp = MCPManager(app.state.tools.registry)
    app.state.skills = SkillManager(paths().ensure().skills, app.state.secrets, app.state.mcp)
    app.state.skills.load_all()
    _apply_skill_permissions(app)
    app.state.router = Router(
        app.state.tools.registry,
        app.state.providers,
        extra_namespaces=app.state.skills.namespaces_for,
    )
    app.state.watch = WatchManager(app.state.bus)
    app.state.proactive = ProactiveEngine(app.state.bus, app.state.tools, _build_policy(settings))
    app.state.tasks = TaskManager()
    app.state.agent = AgentRunner(app.state.providers, app.state.tools, app.state.bus)
    app.state.chat = ChatEngine(
        router=app.state.router,
        providers=app.state.providers,
        tools=app.state.tools,
        bus=app.state.bus,
        memory=app.state.memory,
        skills=app.state.skills,
        agent=app.state.agent,
        tasks=app.state.tasks,
    )
    app.include_router(health.router)
    app.include_router(ws.router)
    app.include_router(tools.router)
    app.include_router(ai.router)
    app.include_router(chat.router)
    app.include_router(memory.router)
    app.include_router(voice.router)
    app.include_router(live.router)
    app.include_router(extensions.router)
    app.include_router(tasks.router)
    app.include_router(proactive.router)
    app.include_router(logs.router)
    # A interface é montada por último: sua rota curinga não pode capturar
    # nada da API.
    web.mount(app)
    return app


def build_memory(settings: Settings, bus: EventBus, providers: ProviderManager) -> MemoryManager:
    p = paths().ensure()
    store = MemoryStore(p.db_file, settings.memory.embedding_dimensions)
    embedder = Embedder(
        host=settings.ai.ollama_host,
        model=settings.memory.embedding_model,
        dimensions=settings.memory.embedding_dimensions,
    )
    # O cofre fica na pasta visível: a memória é do usuário, não do programa.
    return MemoryManager(store, embedder, providers, bus, vault=Vault(p.memoria))


def build_tool_bus(
    settings: Settings, bus: EventBus, services: dict[str, object] | None = None
) -> ToolBus:
    """Monta registro, permissões, auditoria e Tool Bus a partir da configuração."""
    registry = register_calendar_tools(
        register_web_tools(
            register_memory_tools(register_macos_tools(register_builtin_tools(ToolRegistry())))
        )
    )
    permissions = PermissionEngine(
        overrides=parse_overrides(settings.permissions.overrides),
        grants=dict(settings.permissions.grants),
    )
    return ToolBus(
        registry=registry,
        permissions=permissions,
        events=bus,
        audit=AuditLog(paths().ensure().audit_file),
        settings=settings,
        approvals=ApprovalBroker(settings.permissions.confirm_timeout),
        services=dict(services or {}),
    )


def _build_policy(settings: Settings) -> Policy:
    proativo = settings.proactive
    horas = proativo.quiet_hours
    return Policy(
        rules=dict(proativo.rules),
        quiet_hours=(horas[0], horas[1]) if horas and len(horas) == 2 else None,
        min_interval=proativo.min_interval,
        enabled=proativo.enabled,
    )


async def _start_watchers(app: FastAPI) -> None:
    """Sobe os observadores configurados, sem deixar um travar os outros."""
    settings: Settings = app.state.settings
    for item in settings.watch:
        if not item.enabled:
            continue
        try:
            await app.state.watch.add_path(item.name, Path(item.path))
        except Exception as exc:
            log.warning("observador.nao_subiu", nome=item.name, error=str(exc)[:160])
    if settings.proactive.watch_apps:
        await app.state.watch.add_apps()


def _build_search(secrets: Any) -> TavilySearch | None:
    """``None`` quando não há credencial — a EVE segue sem pesquisar."""
    try:
        return TavilySearch(secrets.get("TAVILY_API_KEY") or "")
    except ValueError:
        return None


def _standalone_servers(settings: Settings) -> list[MCPServerConfig]:
    """Servidores MCP declarados direto no config.toml, fora de Skills."""
    return [
        MCPServerConfig(
            name=item.name,
            command=item.command,
            args=list(item.args),
            env=dict(item.env),
            cwd=item.cwd,
            url=item.url,
            enabled=item.enabled,
        )
        for item in settings.mcp
    ]


def _apply_skill_permissions(app: FastAPI) -> None:
    """Padrões das Skills por baixo, escolhas do usuário por cima.

    Quem instala a Skill decide o padrão; quem usa decide o final.
    """
    engine = app.state.tools.permissions
    das_skills = parse_overrides(app.state.skills.permission_overrides())
    engine.overrides = {**das_skills, **engine.overrides}


def parse_overrides(raw: dict[str, str]) -> dict[str, RiskLevel]:
    """Converte a política do TOML, ignorando nível desconhecido em vez de quebrar."""
    out: dict[str, RiskLevel] = {}
    for pattern, level in raw.items():
        try:
            out[pattern] = RiskLevel(level.lower())
        except ValueError:
            log.warning("permissions.unknown_level", pattern=pattern, level=level)
    return out
