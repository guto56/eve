"""Definição de uma ferramenta e dos tipos que circulam pelo Tool Bus.

Nenhum modelo executa ação diretamente (spec §10). Uma ferramenta é um
contrato declarado: nome, descrição, esquema de argumentos, nível de risco e
um handler. O Tool Bus é quem valida, autoriza, executa e audita.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover
    from eve.bus import EventBus
    from eve.config import Settings


class RiskLevel(StrEnum):
    """Categorias de permissão da spec §11."""

    SAFE = "safe"
    """Executa automaticamente. Ex.: abrir um app, ler informação do sistema."""

    CONFIRM = "confirm"
    """Exige confirmação do usuário. Ex.: enviar mensagem, apagar arquivo."""

    PRIVILEGED = "privileged"
    """Exige confirmação e uma concessão explícita na configuração.
    Ex.: instalar software, comando de terminal potencialmente destrutivo."""

    BLOCKED = "blocked"
    """Nunca executa automaticamente."""


ORDER: dict[RiskLevel, int] = {
    RiskLevel.SAFE: 0,
    RiskLevel.CONFIRM: 1,
    RiskLevel.PRIVILEGED: 2,
    RiskLevel.BLOCKED: 3,
}


class ToolParams(BaseModel):
    """Base dos esquemas de argumento.

    ``extra="forbid"`` faz o JSON Schema sair com ``additionalProperties:
    false``, que é o que os provedores de LLM esperam em modo estrito, e ainda
    rejeita argumento inventado antes de chegar no handler.
    """

    model_config = ConfigDict(extra="forbid")


class NoParams(ToolParams):
    """Para ferramentas sem argumento."""


@dataclass(frozen=True)
class ToolContext:
    """Contexto de uma chamada, entregue ao handler."""

    request_id: str
    source: str
    settings: Settings
    bus: EventBus
    caller: str = "user"
    services: Mapping[str, Any] = field(default_factory=dict)
    """Serviços do Core que a ferramenta pode usar (``memory``, etc.).

    Passar por aqui em vez de importar direto mantém a ferramenta testável e
    evita que ela alcance qualquer parte do sistema."""

    def service(self, name: str) -> Any:
        servico = self.services.get(name)
        if servico is None:
            raise RuntimeError(f"serviço indisponível: {name}")
        return servico


@dataclass(frozen=True)
class ToolResult:
    request_id: str
    tool: str
    ok: bool
    value: Any = None
    error: str | None = None
    error_kind: str | None = None
    duration_ms: float = 0.0
    waited_ms: float = 0.0
    """Tempo parado esperando autorização humana.

    Sem separar isto, uma ferramenta que ficou 86 s aguardando o usuário
    aparece como se tivesse levado 86 s para executar — e a observabilidade
    (spec §38) passa a mentir sobre onde está a lentidão."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool": self.tool,
            "ok": self.ok,
            "value": self.value,
            "error": self.error,
            "error_kind": self.error_kind,
            "duration_ms": round(self.duration_ms, 2),
            "waited_ms": round(self.waited_ms, 2),
        }


Handler = Callable[[Any, ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: type[ToolParams]
    risk: RiskLevel
    handler: Handler
    raw_schema: dict[str, Any] | None = None
    """Esquema cru, quando a ferramenta vem de fora (MCP) e não tem modelo
    pydantic. Quando presente, substitui o esquema derivado de ``params``."""
    reversible: bool = True
    timeout: float = 30.0
    requires: tuple[str, ...] = ()
    """Permissões do macOS necessárias, para a interface poder explicá-las."""
    secret_fields: frozenset[str] = field(default_factory=frozenset)
    """Campos que nunca entram no log de auditoria."""
    keywords: tuple[str, ...] = ()
    """Palavras que apontam para esta ferramenta na seleção por relevância.

    A descrição está em português mas o nome está em inglês; sem isto,
    "pesquise" não alcança `web.search`. Serve também para desempatar entre
    ferramentas do mesmo namespace."""

    @property
    def namespace(self) -> str:
        return self.name.split(".")[0]

    def json_schema(self) -> dict[str, Any]:
        """Esquema dos argumentos, no formato que os provedores de LLM aceitam."""
        if self.raw_schema is not None:
            return dict(self.raw_schema)
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return schema

    def validate_args(self, args: dict[str, Any]) -> Any:
        """Valida e devolve os argumentos prontos para o handler.

        Ferramentas nativas recebem o modelo pydantic; as de MCP recebem o
        dicionário, já conferido contra o JSON Schema declarado pelo servidor.
        """
        if self.raw_schema is not None:
            from eve.mcp.schema import validate

            return validate(args, self.raw_schema)
        return self.params.model_validate(args)

    def as_wire_tool(self) -> dict[str, Any]:
        """Definição no formato de function calling aceito pelos provedores."""
        from eve.ai.base import to_wire_name

        return {
            "type": "function",
            "function": {
                "name": to_wire_name(self.name),
                "description": self.description,
                "parameters": self.json_schema(),
            },
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "reversible": self.reversible,
            "requires": list(self.requires),
            "parameters": self.json_schema(),
        }

    def redact(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.secret_fields:
            return args
        return {k: ("***" if k in self.secret_fields else v) for k, v in args.items()}


def new_request_id() -> str:
    return uuid.uuid4().hex


def now_ms() -> float:
    return time.perf_counter() * 1000
