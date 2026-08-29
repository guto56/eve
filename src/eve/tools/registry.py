"""Registro de ferramentas.

Toda ferramenta da EVE — nativa, de Skill ou vinda de um MCP — entra aqui e
passa a existir para o Tool Bus, para a CLI, para a interface e para os
modelos, com a mesma descrição.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from eve.tools.spec import Handler, RiskLevel, ToolParams, ToolSpec


class DuplicateToolError(ValueError):
    pass


class UnknownToolError(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"ferramenta desconhecida: {self.name}"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
        # "__" é como o ponto do nome viaja até os provedores de LLM; um nome
        # que já contenha "__" quebraria a conversão de volta.
        if "__" in spec.name:
            raise ValueError(f"nome de ferramenta não pode conter '__': {spec.name}")
        if spec.name in self._tools and not replace:
            raise DuplicateToolError(f"ferramenta já registrada: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolSpec]:
        return [self._tools[n] for n in self.names()]

    def namespaces(self) -> list[str]:
        return sorted({spec.namespace for spec in self._tools.values()})

    def describe(self) -> list[dict[str, Any]]:
        return [spec.describe() for spec in self.all()]

    def wire_tools(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Definições no formato de function calling, para mandar a um modelo."""
        specs = self.all() if names is None else [self.get(n) for n in names]
        return [spec.as_wire_tool() for spec in specs]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self.all())


def tool(
    name: str,
    *,
    description: str,
    params: type[ToolParams],
    risk: RiskLevel,
    registry: ToolRegistry | None = None,
    reversible: bool = True,
    timeout: float = 30.0,
    requires: tuple[str, ...] = (),
    secret_fields: frozenset[str] = frozenset(),
    keywords: tuple[str, ...] = (),
) -> Callable[[Handler], Handler]:
    """Declara uma ferramenta e a registra.

    Sem ``registry`` a ferramenta vai para o registro padrão do processo,
    usado pelo daemon.
    """

    def decorator(handler: Handler) -> Handler:
        spec = ToolSpec(
            name=name,
            description=description,
            params=params,
            risk=risk,
            handler=handler,
            reversible=reversible,
            timeout=timeout,
            requires=requires,
            secret_fields=secret_fields,
            keywords=keywords,
        )
        # `registry or ...` seria falsy com registro vazio, pois ToolRegistry tem __len__.
        target = registry if registry is not None else default_registry()
        target.register(spec)
        handler.tool_spec = spec  # type: ignore[attr-defined]
        return handler

    return decorator


_DEFAULT: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ToolRegistry()
    return _DEFAULT


def reset_default_registry() -> ToolRegistry:
    """Usado pelos testes e pela recarga de Skills."""
    global _DEFAULT
    _DEFAULT = ToolRegistry()
    return _DEFAULT
