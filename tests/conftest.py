from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from eve.config import Settings


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isola EVE_HOME e limpa qualquer EVE_* herdado do ambiente real."""
    for key in [k for k in os.environ if k.startswith("EVE_")]:
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "eve-home"
    monkeypatch.setenv("EVE_HOME", str(home))
    # Nenhum teste toca no Keychain do usuário nem enxerga credencial real.
    monkeypatch.setenv("EVE_SECRETS_BACKEND", "memory")
    yield home


@pytest.fixture
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def settings(free_port: int) -> Settings:
    s = Settings()
    s.server.port = free_port
    return s


@pytest.fixture
def registry():
    from eve.tools.registry import ToolRegistry

    return ToolRegistry()


@pytest.fixture
def event_bus():
    from eve.bus import EventBus

    return EventBus()


@pytest.fixture
def audit_log(tmp_path: Path):
    from eve.tools.audit import AuditLog

    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def tool_bus(registry, event_bus, audit_log, settings):
    """Tool Bus com registro vazio e prazo de confirmação curto."""
    from eve.permissions import PermissionEngine
    from eve.tools.approvals import ApprovalBroker
    from eve.tools.bus import ToolBus

    settings.permissions.confirm_timeout = 0.4
    return ToolBus(
        registry=registry,
        permissions=PermissionEngine(),
        events=event_bus,
        audit=audit_log,
        settings=settings,
        approvals=ApprovalBroker(0.4),
    )


@pytest.fixture
def sandbox(tmp_path: Path, settings) -> Path:
    """Pasta única que as ferramentas de arquivo podem enxergar durante o teste."""
    root = tmp_path / "sandbox"
    root.mkdir()
    settings.files.allowed_roots = [str(root)]
    return root


@pytest.fixture
def macos_bus(tool_bus):
    """Tool Bus com as ferramentas nativas do macOS registradas."""
    from eve.tools.macos_tools import register_macos_tools

    register_macos_tools(tool_bus.registry)
    return tool_bus


@pytest.fixture
def preserve_clipboard():
    """Devolve a área de transferência do usuário ao estado original."""
    from eve.macos import native

    original = native.clipboard_read()
    yield
    if original is not None:
        native.clipboard_write(original)


@pytest.fixture
def secret_store(tmp_path: Path):
    from eve.secrets import InMemoryBackend, SecretStore

    return SecretStore(
        tmp_path / "secrets.json", backend=InMemoryBackend(), allow_env_fallback=False
    )


@pytest.fixture
def fake_providers(settings, secret_store):
    """ProviderManager cujo provedor local é falso e roteirizável."""
    from eve.ai.manager import ProviderManager
    from tests.fakes import FakeProvider

    manager = ProviderManager(settings, secret_store)
    fake = FakeProvider()
    manager._local = fake
    manager.fake = fake  # atalho para os testes
    return manager


@pytest.fixture
def full_registry():
    from eve.tools.builtin import register_builtin_tools
    from eve.tools.macos_tools import register_macos_tools
    from eve.tools.memory_tools import register_memory_tools
    from eve.tools.registry import ToolRegistry
    from eve.tools.web_tools import register_web_tools

    return register_web_tools(
        register_memory_tools(register_macos_tools(register_builtin_tools(ToolRegistry())))
    )
