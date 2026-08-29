"""Diagnóstico da instalação (``eve doctor``, spec §24).

Cada fase registra as próprias verificações em :data:`CHECKS`, de modo que o
diagnóstico cresce junto com o sistema.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from eve.config import Settings
from eve.paths import paths


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


CheckFn = Callable[[Settings], Check]


def check_python(_: Settings) -> Check:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 13):
        return Check("Python", Status.OK, version)
    return Check("Python", Status.FAIL, f"{version} — a EVE exige 3.13 ou superior")


def check_home(_: Settings) -> Check:
    p = paths()
    try:
        p.ensure()
        probe = p.run / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check("Diretório da EVE", Status.FAIL, f"{p.home}: {exc}")
    return Check("Diretório da EVE", Status.OK, str(p.home))


def check_config(_: Settings) -> Check:
    target = paths().config_file
    if not target.exists():
        return Check("Configuração", Status.OK, "usando padrões (config.toml ainda não existe)")
    try:
        with target.open("rb") as fh:
            tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return Check("Configuração", Status.FAIL, f"TOML inválido: {exc}")
    return Check("Configuração", Status.OK, str(target))


def check_daemon(settings: Settings) -> Check:
    from eve.cli.process import probe_health, running_pid

    body = probe_health(settings)
    if body is not None:
        return Check(
            "Core",
            Status.OK,
            f"respondendo em {settings.server.host}:{settings.server.port} "
            f"(v{body.get('version', '?')})",
        )
    pid = running_pid()
    if pid is not None:
        return Check("Core", Status.WARN, f"processo {pid} vivo mas sem responder HTTP")
    return Check("Core", Status.WARN, "parado — use `eve start`")


def check_port(settings: Settings) -> Check:
    from eve.cli.process import probe_health

    if probe_health(settings) is not None:
        return Check("Porta", Status.OK, f"{settings.server.port} em uso pela EVE")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        if sock.connect_ex((settings.server.host, settings.server.port)) == 0:
            return Check(
                "Porta",
                Status.FAIL,
                f"{settings.server.port} ocupada por outro processo",
            )
    return Check("Porta", Status.OK, f"{settings.server.port} livre")


def check_ollama(settings: Settings) -> Check:
    if shutil.which("ollama") is None:
        return Check("Ollama", Status.WARN, "não instalado — necessário para a IA local")
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("Ollama", Status.WARN, f"instalado mas não respondeu: {exc}")
    if result.returncode != 0:
        return Check("Ollama", Status.WARN, "instalado mas o serviço não está rodando")
    installed = [line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()]
    if any(m.startswith(settings.ai.local_model.split(":")[0]) for m in installed):
        return Check("Ollama", Status.OK, f"modelo {settings.ai.local_model} disponível")
    return Check(
        "Ollama",
        Status.WARN,
        f"rodando, mas sem {settings.ai.local_model} ({len(installed)} outro(s) modelo(s))",
    )


def check_tools(settings: Settings) -> Check:
    from eve.daemon.app import parse_overrides
    from eve.permissions import PermissionEngine
    from eve.tools.builtin import register_builtin_tools
    from eve.tools.macos_tools import register_macos_tools
    from eve.tools.memory_tools import register_memory_tools
    from eve.tools.registry import ToolRegistry

    registry = register_memory_tools(register_macos_tools(register_builtin_tools(ToolRegistry())))
    engine = PermissionEngine(
        overrides=parse_overrides(settings.permissions.overrides),
        grants=dict(settings.permissions.grants),
    )
    blocked = [spec.name for spec in registry if not engine.decide(spec).allowed]
    detail = f"{len(registry)} nativa(s)"
    if blocked:
        return Check("Ferramentas", Status.WARN, f"{detail}; bloqueada(s): {', '.join(blocked)}")
    return Check("Ferramentas", Status.OK, detail)


def check_macos_permissions(_: Settings) -> Check:
    """As permissões do macOS que a EVE já tem — e as que faltam."""
    from eve.macos import native
    from eve.macos.osa import AppleScriptError, run_applescript

    granted: list[str] = []
    missing: list[str] = []

    try:
        run_applescript('tell application "System Events" to get name of first process', timeout=8)
        granted.append("automação")
    except AppleScriptError:
        missing.append("automação (Ajustes ▸ Privacidade ▸ Automação)")

    trusted = native.accessibility_trusted()
    if trusted is True:
        granted.append("acessibilidade")
    elif trusted is False:
        missing.append("acessibilidade (Ajustes ▸ Privacidade ▸ Acessibilidade)")

    if missing:
        return Check(
            "Permissões do macOS",
            Status.WARN,
            f"faltam: {'; '.join(missing)}",
        )
    return Check("Permissões do macOS", Status.OK, ", ".join(granted) or "nada exigido ainda")


def check_secrets(_: Settings) -> Check:
    from eve.secrets import build_store

    store = build_store(paths().ensure().home / "secrets.json")
    described = store.describe()
    configured = sum(1 for item in described if item["configured"])
    missing = store.missing_required()
    if missing:
        return Check(
            "Credenciais",
            Status.WARN,
            f"{configured} configurada(s); falta: {', '.join(missing)}",
        )
    return Check("Credenciais", Status.OK, f"{configured} configurada(s) no Keychain")


def check_providers(settings: Settings) -> Check:
    import asyncio

    from eve.ai.manager import ProviderManager
    from eve.secrets import build_store

    async def run() -> list:
        manager = ProviderManager(settings, build_store(paths().ensure().home / "secrets.json"))
        try:
            return await manager.health()
        finally:
            await manager.aclose()

    try:
        results = asyncio.run(run())
    except Exception as exc:
        return Check("Provedores de IA", Status.WARN, f"não deu para checar: {exc}")

    ok = [h.name for h in results if h.ok]
    bad = [f"{h.name} ({h.detail})" for h in results if not h.ok]
    if not ok:
        return Check("Provedores de IA", Status.FAIL, "; ".join(bad) or "nenhum disponível")
    if bad:
        return Check("Provedores de IA", Status.WARN, f"{', '.join(ok)} ok; problema em {bad[0]}")
    return Check("Provedores de IA", Status.OK, ", ".join(ok))


def check_memory_store(settings: Settings) -> Check:
    import asyncio

    from eve.memory.embeddings import Embedder
    from eve.memory.store import MemoryStore

    async def run() -> tuple[dict, bool]:
        store = MemoryStore(paths().ensure().db_file, settings.memory.embedding_dimensions)
        embedder = Embedder(host=settings.ai.ollama_host, model=settings.memory.embedding_model)
        try:
            return await store.stats(), await embedder.available()
        finally:
            await embedder.aclose()
            await store.aclose()

    try:
        stats, tem_embedder = asyncio.run(run())
    except Exception as exc:
        return Check("Memória da EVE", Status.FAIL, f"não abriu: {exc}")

    detalhe = f"{stats['total']} memória(s), {stats['bytes'] / 1024:.0f} KB"
    if not stats["busca_semantica"]:
        return Check("Memória da EVE", Status.WARN, f"{detalhe}; sem sqlite-vec — só busca textual")
    if not tem_embedder:
        return Check(
            "Memória da EVE",
            Status.WARN,
            f"{detalhe}; falta o modelo {settings.memory.embedding_model} "
            f"(ollama pull {settings.memory.embedding_model})",
        )
    return Check("Memória da EVE", Status.OK, f"{detalhe}, busca híbrida ativa")


def check_extensions(settings: Settings) -> Check:
    """Skills instaladas e servidores MCP declarados."""
    from eve.secrets import build_store
    from eve.skills.model import SkillError, load_skill

    raiz = paths().ensure().skills
    skills, quebradas, sem_credencial = [], [], []
    store = build_store(paths().home / "secrets.json")
    for diretorio in sorted(p for p in raiz.iterdir() if p.is_dir()):
        try:
            skill = load_skill(diretorio)
        except SkillError:
            quebradas.append(diretorio.name)
            continue
        skills.append(skill)
        if skill.enabled and any(not store.has(n) for n in skill.requires_secrets):
            sem_credencial.append(skill.name)

    servidores = len(settings.mcp) + sum(len(s.mcp) for s in skills if s.enabled)
    detalhe = f"{len(skills)} Skill(s), {servidores} servidor(es) MCP"
    if quebradas:
        return Check("Skills e MCP", Status.WARN, f"{detalhe}; inválida(s): {', '.join(quebradas)}")
    if sem_credencial:
        return Check(
            "Skills e MCP", Status.WARN, f"{detalhe}; sem credencial: {', '.join(sem_credencial)}"
        )
    return Check("Skills e MCP", Status.OK, detalhe)


def check_voice(_: Settings) -> Check:
    from eve.secrets import build_store

    store = build_store(paths().ensure().home / "secrets.json")
    faltando = [
        nome
        for nome in ("DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "CARTESIA_VOICE_ID")
        if not store.has(nome)
    ]
    if faltando:
        return Check("Voz", Status.WARN, f"falta {', '.join(faltando)}")
    return Check("Voz", Status.OK, "transcrição e fala configuradas")


def check_web(_: Settings) -> Check:
    from eve.web import STATIC, is_built

    if not is_built():
        return Check(
            "Interface web",
            Status.WARN,
            "não construída — rode `cd ui && npm install && npm run build`",
        )
    bytes_total = sum(f.stat().st_size for f in STATIC.rglob("*") if f.is_file())
    return Check("Interface web", Status.OK, f"pronta ({bytes_total / 1024:.0f} KB)")


def check_disk(_: Settings) -> Check:
    usage = shutil.disk_usage(paths().home.parent)
    free_gb = usage.free / 1024**3
    if free_gb < 5:
        return Check("Disco", Status.FAIL, f"{free_gb:.1f} GB livres — insuficiente")
    if free_gb < 15:
        return Check("Disco", Status.WARN, f"{free_gb:.1f} GB livres — apertado para modelos")
    return Check("Disco", Status.OK, f"{free_gb:.1f} GB livres")


def check_ram(_: Settings) -> Check:
    try:
        total = int(
            subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return Check("RAM", Status.WARN, "não foi possível medir")
    gb = total / 1024**3
    if gb < 8:
        return Check("RAM", Status.FAIL, f"{gb:.0f} GB — abaixo do mínimo")
    if gb <= 8:
        return Check("RAM", Status.WARN, f"{gb:.0f} GB — use apenas modelos locais até 3B")
    return Check("RAM", Status.OK, f"{gb:.0f} GB")


CHECKS: list[CheckFn] = [
    check_python,
    check_ram,
    check_disk,
    check_home,
    check_config,
    check_port,
    check_daemon,
    check_tools,
    check_macos_permissions,
    check_secrets,
    check_providers,
    check_memory_store,
    check_voice,
    check_extensions,
    check_web,
    check_ollama,
]


def run_checks(settings: Settings) -> list[Check]:
    results: list[Check] = []
    for fn in CHECKS:
        try:
            results.append(fn(settings))
        except Exception as exc:
            results.append(Check(fn.__name__, Status.FAIL, f"erro inesperado: {exc}"))
    return results


def worst(results: list[Check]) -> Status:
    if any(c.status is Status.FAIL for c in results):
        return Status.FAIL
    if any(c.status is Status.WARN for c in results):
        return Status.WARN
    return Status.OK
