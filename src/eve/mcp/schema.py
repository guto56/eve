"""Validação de argumentos contra JSON Schema.

As ferramentas nativas descrevem os argumentos com pydantic. As de MCP chegam
com JSON Schema cru, que não dá para transformar em modelo pydantic sem
perder fidelidade. Este validador cobre o subconjunto que os servidores MCP
usam de verdade — e é uma primeira barreira, não a última: o servidor valida
de novo do outro lado.
"""

from __future__ import annotations

from typing import Any

TIPOS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


class SchemaError(ValueError):
    pass


def validate(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Confere ``args`` contra ``schema``. Devolve os argumentos ou levanta."""
    if not isinstance(schema, dict):
        return args

    problemas: list[str] = []
    propriedades: dict[str, Any] = schema.get("properties") or {}
    obrigatorios: list[str] = schema.get("required") or []

    for nome in obrigatorios:
        if nome not in args:
            problemas.append(f"{nome}: obrigatório")

    # Um servidor que não declara additionalProperties normalmente aceita
    # extras; só recusamos quando ele diz explicitamente que não aceita.
    if schema.get("additionalProperties") is False:
        for nome in args:
            if nome not in propriedades:
                problemas.append(f"{nome}: argumento inesperado")

    for nome, valor in args.items():
        definicao = propriedades.get(nome)
        if not isinstance(definicao, dict):
            continue
        problema = _check(nome, valor, definicao)
        if problema:
            problemas.append(problema)

    if problemas:
        raise SchemaError("; ".join(problemas))
    return args


def _check(nome: str, valor: Any, definicao: dict[str, Any]) -> str | None:
    esperado = definicao.get("type")
    if isinstance(esperado, list):
        # ["string", "null"] e afins.
        if valor is None and "null" in esperado:
            return None
        if not any(_is_type(valor, t) for t in esperado if t != "null"):
            return f"{nome}: esperado {' ou '.join(esperado)}"
        return None
    if isinstance(esperado, str) and not _is_type(valor, esperado):
        return f"{nome}: esperado {esperado}, veio {type(valor).__name__}"

    opcoes = definicao.get("enum")
    if isinstance(opcoes, list) and valor not in opcoes:
        return f"{nome}: valor fora das opções ({', '.join(map(str, opcoes[:6]))})"
    return None


def _is_type(valor: Any, tipo: str) -> bool:
    if tipo == "null":
        return valor is None
    aceitos = TIPOS.get(tipo)
    if aceitos is None:
        return True  # tipo desconhecido: não é nossa função reprovar
    # bool é subclasse de int em Python, mas não é um número para o schema.
    if tipo in ("number", "integer") and isinstance(valor, bool):
        return False
    return isinstance(valor, aceitos)
