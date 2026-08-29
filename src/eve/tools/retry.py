"""Contenção de repetição.

Um modelo que erra os argumentos tende a insistir com os mesmos argumentos.
Avisar no texto do resultado não resolve — numa execução real o modelo repetiu
a mesma chamada inválida sete vezes, ignorando o aviso a cada volta, até
esgotar o orçamento de rodadas. A contenção precisa ser estrutural: a partir
da terceira, a chamada nem chega à ferramenta.
"""

from __future__ import annotations

import json
from typing import Any

from eve.tools.spec import ToolResult

MAX_REPETICOES = 2
"""Falhas idênticas toleradas antes da recusa."""


def assinatura(tool: str, argumentos: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(argumentos, sort_keys=True, default=str)}"


def recusa(tool: str) -> str:
    return json.dumps(
        {
            "erro": f"{tool} já falhou com esses mesmos argumentos e não foi chamada de novo.",
            "tipo": "repeticao_recusada",
            "atencao": "Mude os argumentos ou use outra ferramenta. Insistir não vai funcionar.",
        },
        ensure_ascii=False,
    )


def payload(resultado: ToolResult, repeticoes: int = 0, limite: int = 6000) -> str:
    """O que o modelo vê de volta, com aviso quando o erro se repete."""
    if resultado.ok:
        return json.dumps(resultado.value, ensure_ascii=False, default=str)[:limite]
    corpo: dict[str, Any] = {"erro": resultado.error, "tipo": resultado.error_kind}
    if repeticoes >= MAX_REPETICOES:
        corpo["atencao"] = (
            "Esta mesma chamada já falhou antes. NÃO repita: mude os argumentos "
            "ou use outra ferramenta."
        )
    if resultado.error_kind == "denied":
        corpo["atencao"] = (
            "A ação precisa de autorização do usuário e não foi autorizada. "
            "Siga por um caminho que não exija confirmação."
        )
    return json.dumps(corpo, ensure_ascii=False)
