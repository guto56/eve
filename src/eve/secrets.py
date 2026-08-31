"""Cofre de credenciais (spec §22).

Chaves de API vivem no Keychain do macOS, nunca em código, banco, log ou
histórico de conversa. O que a EVE guarda em disco é apenas a *lista de nomes*
das credenciais que ela conhece — o Keychain não permite enumerar itens, e
guardar só os nomes deixa a interface mostrar o que falta sem tocar em valor
nenhum.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from eve.logging import get_logger

log = get_logger(__name__)

SERVICE = "ai.eve"

VALID_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

#: Credenciais que a EVE conhece, com o que cada uma habilita.
KNOWN_SECRETS: dict[str, str] = {
    "OPENROUTER_API_KEY": "IA externa (Gemini, Claude, GPT) via OpenRouter",
    "DEEPGRAM_API_KEY": "transcrição de voz em tempo real",
    "CARTESIA_API_KEY": "síntese de voz",
    "CARTESIA_VOICE_ID": "voz escolhida no Cartesia",
    "TAVILY_API_KEY": "pesquisa na web",
    "GOOGLE_API_KEY": "conversa por voz em tempo real (Gemini Live)",
    "GITHUB_TOKEN": "integração com o GitHub",
    "TWILIO_ACCOUNT_SID": "telefonia (futuro)",
    "TWILIO_AUTH_TOKEN": "telefonia (futuro)",
}

REQUIRED_SECRETS: frozenset[str] = frozenset({"OPENROUTER_API_KEY"})


class SecretBackend(Protocol):
    def get_password(self, service: str, name: str) -> str | None: ...
    def set_password(self, service: str, name: str, value: str) -> None: ...
    def delete_password(self, service: str, name: str) -> None: ...


class InvalidSecretName(ValueError):
    pass


class SecretBackendUnavailable(RuntimeError):
    pass


class InMemoryBackend:
    """Backend sem persistência, para testes — nunca toca no Keychain real."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self._data.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self._data[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        if (service, name) not in self._data:
            raise KeyError(name)
        del self._data[(service, name)]


def keychain_backend() -> SecretBackend:
    """Backend do sistema. Levanta se o Keychain não estiver acessível."""
    try:
        import keyring
        from keyring.backends import macOS
    except ImportError as exc:  # pragma: no cover - dependência declarada
        raise SecretBackendUnavailable(f"keyring indisponível: {exc}") from exc

    backend = keyring.get_keyring()
    if not isinstance(backend, macOS.Keyring):  # pragma: no cover - fora do macOS
        log.warning("secrets.backend_inesperado", backend=type(backend).__name__)
    return keyring


class SecretStore:
    def __init__(
        self,
        index_path: Path,
        backend: SecretBackend | None = None,
        service: str = SERVICE,
        allow_env_fallback: bool = True,
    ) -> None:
        self.index_path = index_path
        self.service = service
        self.allow_env_fallback = allow_env_fallback
        self._backend = backend

    @property
    def backend(self) -> SecretBackend:
        if self._backend is None:
            self._backend = keychain_backend()
        return self._backend

    # ------------------------------------------------------------- índice

    def names(self) -> list[str]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return sorted(n for n in data if isinstance(n, str))

    def _remember(self, name: str) -> None:
        names = set(self.names()) | {name}
        self._write_index(names)

    def _forget(self, name: str) -> None:
        self._write_index(set(self.names()) - {name})

    def _write_index(self, names: set[str]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(sorted(names), indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    # ------------------------------------------------------------ valores

    @staticmethod
    def validate_name(name: str) -> str:
        if not VALID_NAME.match(name):
            raise InvalidSecretName(
                f"nome inválido: {name!r} — use MAIÚSCULAS, dígitos e _, de 3 a 64 caracteres"
            )
        return name

    def set(self, name: str, value: str) -> None:
        self.validate_name(name)
        if not value:
            raise ValueError("valor vazio")
        self.backend.set_password(self.service, name, value)
        self._remember(name)
        log.info("secrets.gravada", name=name)

    def get(self, name: str) -> str | None:
        self.validate_name(name)
        value = self.backend.get_password(self.service, name)
        if value:
            return value
        if self.allow_env_fallback:
            return os.environ.get(name) or None
        return None

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def delete(self, name: str) -> bool:
        self.validate_name(name)
        try:
            self.backend.delete_password(self.service, name)
        except Exception:
            self._forget(name)
            return False
        self._forget(name)
        log.info("secrets.removida", name=name)
        return True

    # -------------------------------------------------------------- visão

    def describe(self) -> list[dict[str, object]]:
        """Estado de cada credencial conhecida, sem revelar valor algum."""
        known = set(KNOWN_SECRETS) | set(self.names())
        out = []
        for name in sorted(known):
            value = self.get(name)
            out.append(
                {
                    "name": name,
                    "description": KNOWN_SECRETS.get(name, "credencial personalizada"),
                    "configured": value is not None,
                    "required": name in REQUIRED_SECRETS,
                    "hint": mask(value),
                }
            )
        return out

    def missing_required(self) -> list[str]:
        return sorted(name for name in REQUIRED_SECRETS if not self.has(name))

    # ---------------------------------------------------------- importação

    def import_env_file(self, path: Path, *, overwrite: bool = False) -> dict[str, str]:
        """Traz credenciais de um arquivo ``CHAVE=valor`` para o Keychain.

        Devolve o que aconteceu com cada nome: ``importada``, ``já existia`` ou
        o motivo de ter sido ignorada. O arquivo não é apagado — quem decide
        isso é o usuário.
        """
        result: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip().strip("'\"")
            try:
                self.validate_name(name)
            except InvalidSecretName:
                result[name] = "ignorada (nome inválido)"
                continue
            if not value:
                result[name] = "ignorada (vazia)"
                continue
            if not overwrite and self.has(name):
                result[name] = "já existia"
                continue
            self.set(name, value)
            result[name] = "importada"
        return result


#: Backends em memória por ``EVE_HOME``. Um cofre precisa lembrar entre
#: chamadas para ser um substituto fiel do Keychain; a chave é o caminho do
#: índice, que é único por instalação (e por teste).
_MEMORY_BACKENDS: dict[str, InMemoryBackend] = {}


def build_store(index_path: Path) -> SecretStore:
    """Cofre para o processo em execução.

    ``EVE_SECRETS_BACKEND=memory`` troca o Keychain por armazenamento em
    memória e desliga o fallback de ambiente — é o que os testes usam, para
    nunca escreverem no Keychain do usuário nem enxergarem chaves reais.
    """
    if os.environ.get("EVE_SECRETS_BACKEND", "").lower() == "memory":
        backend = _MEMORY_BACKENDS.setdefault(str(index_path), InMemoryBackend())
        return SecretStore(index_path, backend=backend, allow_env_fallback=False)
    return SecretStore(index_path)


def mask(value: str | None) -> str | None:
    """Pista visual de uma credencial, sem revelá-la."""
    if not value:
        return None
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"
