"""Como o resultado de uma ferramenta vira frase.

O caminho rápido existe para responder em milissegundos. Se ele terminasse
chamando um modelo para dizer "abri o Safari", o ganho iria embora — então
cada ferramenta comum tem um formatador determinístico. O modelo só entra
quando a forma do resultado é desconhecida, e mesmo aí é o `fast`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

Formatter = Callable[[dict[str, Any], Any], str]

DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _lista(itens: list[str]) -> str:
    """Junta com vírgulas e um "e" antes do último, como se escreve."""
    if not itens:
        return "nenhum"
    if len(itens) == 1:
        return itens[0]
    return f"{', '.join(itens[:-1])} e {itens[-1]}"


def _tempo(_: dict[str, Any], value: Any) -> str:
    agora = datetime.fromisoformat(value["local"])
    return (
        f"São {agora:%H:%M} de {DIAS[agora.weekday()]}, "
        f"{agora.day} de {MESES[agora.month - 1]} de {agora.year}."
    )


def _apps(_: dict[str, Any], value: Any) -> str:
    nomes = [a["name"] for a in value["apps"]]
    return f"Estão abertos: {_lista(nomes)}."


def _frontmost(_: dict[str, Any], value: Any) -> str:
    if not value:
        return "Não consegui identificar o app em foco."
    return f"O app em foco é o {value['name']}."


def _volume(_: dict[str, Any], value: Any) -> str:
    mudo = " (no mudo)" if value.get("muted") else ""
    return f"O volume está em {value['level']}%{mudo}."


def _clipboard(_: dict[str, Any], value: Any) -> str:
    texto = value.get("text")
    if not texto:
        return "A área de transferência está vazia."
    recorte = texto if len(texto) <= 200 else texto[:200] + "…"
    return f"Na área de transferência: «{recorte}»"


def _info(_: dict[str, Any], value: Any) -> str:
    return (
        f"Mac com chip {value['chip']}, {value['memory_gb']:g} GB de RAM, macOS {value['release']}."
    )


def _listagem(args: dict[str, Any], value: Any) -> str:
    entradas = value["entries"]
    if not entradas:
        return f"{value['path']} está vazia."
    nomes = [e["name"] for e in entradas[:12]]
    resto = f" e mais {len(entradas) - 12}" if len(entradas) > 12 else ""
    return f"{value['count']} item(ns) em {value['path']}: {_lista(nomes)}{resto}."


def _memoria_gravada(_: dict[str, Any], value: Any) -> str:
    estado = value.get("estado")
    if estado == "reforçada":
        return "Isso eu já sabia — reforcei na memória."
    if estado == "descartada":
        return "Não guardei: pareceu pouco relevante."
    return f"Guardado: {value['content']}"


def _memoria_lembrada(_: dict[str, Any], value: Any) -> str:
    achadas = value.get("encontradas") or value.get("memorias") or []
    if not achadas:
        return "Não encontrei nada sobre isso na memória."
    if len(achadas) == 1:
        return achadas[0]["content"]
    linhas = "\n".join(f"- {m['content']}" for m in achadas)
    return f"O que eu lembro:\n{linhas}"


def _memoria_esquecida(_: dict[str, Any], value: Any) -> str:
    count = value.get("count", 0)
    if not count:
        return "Não achei nada para esquecer."
    return f"Esqueci {count} memória(s)."


def _template(texto: str) -> Formatter:
    def formatter(args: dict[str, Any], value: Any) -> str:
        dados = {**args, **(value if isinstance(value, dict) else {})}
        return texto.format(**dados)

    return formatter


#: Ações: template instantâneo. Consultas: formatador que lê o resultado.
FORMATTERS: dict[str, Formatter] = {
    "app.open": lambda args, value: (
        f"Abri {args['name']} no navegador."
        if isinstance(value, dict) and value.get("via") == "web"
        else f"Abri {args['name']}."
    ),
    "app.activate": _template("Trouxe {name} para a frente."),
    "app.quit": _template("Fechei {name}."),
    "url.open": _template("Abri {url}."),
    "clipboard.write": _template("Copiei para a área de transferência."),
    "system.set_volume": _template("Volume em {level}%."),
    "file.mkdir": _template("Pasta criada em {path}."),
    "file.write": _template("Escrevi {bytes} bytes em {path}."),
    "file.trash": _template("Mandei para a Lixeira."),
    "system.notify": _template("Notificação enviada."),
    "system.screenshot": _template("Tela capturada em {path}."),
    "system.time": _tempo,
    "app.list": _apps,
    "app.frontmost": _frontmost,
    "system.volume": _volume,
    "clipboard.read": _clipboard,
    "system.info": _info,
    "file.list": _listagem,
    "memory.remember": _memoria_gravada,
    "memory.recall": _memoria_lembrada,
    "memory.list": _memoria_lembrada,
    "memory.forget": _memoria_esquecida,
}

PHRASE_SYSTEM = """Você transforma o resultado de uma ferramenta em UMA frase curta
em português do Brasil, dirigida ao usuário. Não explique, não use JSON, não
invente informação que não está no resultado."""


def format_result(tool: str, arguments: dict[str, Any], value: Any) -> str | None:
    """Frase pronta, ou ``None`` quando não há formatador para esta ferramenta."""
    formatter = FORMATTERS.get(tool)
    if formatter is None:
        return None
    try:
        return formatter(arguments, value)
    except (KeyError, IndexError, TypeError, ValueError):
        # Formato inesperado não pode derrubar a resposta; quem chamou decide
        # o que fazer (normalmente, pedir a frase ao modelo).
        return None
