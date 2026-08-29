"""Atalhos determinísticos.

"Abra o Safari" não precisa de modelo nenhum: é uma frase com forma conhecida
e destino óbvio. Uma regra resolve em menos de 1 ms o que uma chamada de LLM
resolveria em centenas — é o que a spec §8 pede ao dizer para não usar IA
pesada no que dá para decidir instantaneamente.

As regras são deliberadamente conservadoras. Na dúvida elas não disparam e a
entrada segue para o classificador; um falso positivo aqui vira uma ação
errada no computador do usuário.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from eve.router.routes import Route

ArgBuilder = Callable[[re.Match[str]], dict[str, Any] | None]


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    route: Route
    tool: str | None = None
    build_args: ArgBuilder | None = None
    veto: re.Pattern[str] | None = None
    """Quando casa, a regra desiste e deixa o classificador decidir."""

    def match(self, normalized: str) -> re.Match[str] | None:
        if self.veto is not None and self.veto.search(normalized):
            return None
        return self.pattern.search(normalized)


@dataclass(frozen=True)
class RuleHit:
    rule: str
    route: Route
    tool: str | None
    arguments: dict[str, Any]


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """Minúsculas, sem acento, sem pontuação final, espaços colapsados.

    As regras casam contra esta forma; os argumentos saem do texto original,
    para preservar acentuação e maiúsculas de nomes próprios.
    """
    cleaned = strip_accents(text.strip().lower())
    cleaned = re.sub(r"[!?.,;]+$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned)


def _original_span(original: str, normalized_match: re.Match[str], group: str) -> str:
    """Recorta do texto original o trecho que o grupo casou no normalizado.

    ``normalize`` preserva o comprimento (só troca caracteres acentuados por
    seus equivalentes sem acento e colapsa espaços no fim), então os índices
    servem para a maioria dos casos; quando não servem, devolvemos o próprio
    grupo normalizado, que ainda é utilizável.
    """
    start, end = normalized_match.span(group)
    candidate = original[start:end] if end <= len(original) else ""
    if strip_accents(candidate.lower()) == normalized_match.group(group):
        return candidate.strip()
    return normalized_match.group(group).strip()


LOOKS_LIKE_URL = re.compile(r"^(https?://|www\.)|\.[a-z]{2,}(/|$)")

#: Palavras que, no começo do alvo, indicam arquivo/pasta e não aplicativo.
FILE_WORDS = ("pasta ", "arquivo ", "diretorio ", "folder ", "file ")


def _open_target(match: re.Match[str]) -> dict[str, Any] | None:
    alvo = match.group("alvo").strip()
    if not alvo or len(alvo) > 120:
        return None
    if alvo.startswith(FILE_WORDS):
        return None  # é sobre arquivo: deixa o modelo decidir a ferramenta
    return {"name": alvo}


def _open_url(match: re.Match[str]) -> dict[str, Any] | None:
    alvo = match.group("alvo").strip()
    if not LOOKS_LIKE_URL.search(alvo):
        return None
    if not urlparse(alvo).scheme:
        alvo = f"https://{alvo}"
    return {"url": alvo}


def _volume_level(match: re.Match[str]) -> dict[str, Any] | None:
    try:
        level = int(match.group("nivel"))
    except (ValueError, IndexError):
        return None
    return {"level": level} if 0 <= level <= 100 else None


def _clipboard_text(match: re.Match[str]) -> dict[str, Any] | None:
    texto = match.group("texto").strip().strip("\"'")
    return {"text": texto} if texto else None


#: Marcas de pedido com várias etapas. Uma frase que começa com "pesquise" mas
#: continua "compare preços e me recomende" não é uma busca: é uma tarefa. Na
#: dúvida, a regra desiste e o classificador decide.
MULTI_STEP = re.compile(
    r"\b(compare|comparar|comparando|e depois|em seguida|por fim"
    r"|e me (recomende|indique|sugira|diga qual)"
    r"|analise|organize|resuma e|opcoes|alternativas)\b"
)


def rule(
    name: str,
    pattern: str,
    route: Route,
    tool: str | None = None,
    build_args: ArgBuilder | None = None,
    veto: re.Pattern[str] | None = None,
) -> Rule:
    return Rule(name, re.compile(pattern), route, tool, build_args, veto)


#: Ordem importa: a primeira regra que casa vence. As mais específicas vêm
#: antes das mais genéricas.
RULES: tuple[Rule, ...] = (
    # --- endereços da web (antes de "abra", que é mais genérico)
    rule(
        "abrir_url",
        r"^(abra|abrir|abre|va para|acesse|visite|open)\s+(o\s+|a\s+|no\s+)?(?P<alvo>\S+)$",
        Route.COMMAND,
        "url.open",
        _open_url,
    ),
    # --- aplicativos
    rule(
        "abrir_app",
        r"^(abra|abrir|abre|inicie|iniciar|execute|rode|open|launch)"
        r"\s+(o\s+|a\s+|os\s+|as\s+)?(app\s+|aplicativo\s+|programa\s+)?(?P<alvo>.+)$",
        Route.COMMAND,
        "app.open",
        _open_target,
    ),
    rule(
        "fechar_app",
        r"^(feche|fechar|fecha|encerre|encerrar|saia do|quit|close)"
        r"\s+(o\s+|a\s+)?(app\s+|aplicativo\s+|programa\s+)?(?P<alvo>.+)$",
        Route.COMMAND,
        "app.quit",
        _open_target,
    ),
    rule(
        "app_em_foco",
        r"^(qual|que)\s+(app|aplicativo|programa|janela)\s+(esta|ta)\s+"
        r"(em foco|na frente|ativo|aberto agora)",
        Route.COMMAND,
        "app.frontmost",
        lambda _: {},
    ),
    rule(
        "listar_apps",
        r"^(quais|que)\s+(apps|aplicativos|programas)\s+(estao|tao)\s+"
        r"(abertos|rodando|ativos)",
        Route.COMMAND,
        "app.list",
        lambda _: {},
    ),
    # --- volume
    rule(
        "volume_definir",
        r"(volume|som).*?\b(para|em|a|no)\s+(?P<nivel>\d{1,3})\s*(%|por cento)?$",
        Route.COMMAND,
        "system.set_volume",
        _volume_level,
    ),
    rule(
        "volume_consultar",
        r"^(qual|que|quanto)\s+(e\s+)?(o\s+)?(volume|nivel do som)",
        Route.COMMAND,
        "system.volume",
        lambda _: {},
    ),
    # --- sistema
    rule(
        "hora",
        r"^(que horas sao|qual (e )?a hora|what time is it|me diga as horas)",
        Route.COMMAND,
        "system.time",
        lambda _: {},
    ),
    rule(
        "info_sistema",
        r"(quanto de (ram|memoria)|quanta (ram|memoria)|especificacoes (desse|deste|do) mac"
        r"|qual (o )?(chip|processador)|que mac e (esse|este)|qual (a )?versao do (macos|sistema))",
        Route.COMMAND,
        "system.info",
        lambda _: {},
    ),
    # --- área de transferência
    rule(
        "copiar_texto",
        r"^(copie|copiar|coloque|ponha)\s+[\"']?(?P<texto>.+?)[\"']?\s+"
        r"(na|para a|pra)\s+(area de transferencia|clipboard)$",
        Route.COMMAND,
        "clipboard.write",
        _clipboard_text,
    ),
    rule(
        "ler_clipboard",
        r"^(o que (tem|esta|ta) (na|no)|leia (a|o)|mostre (a|o))\s+"
        r"(area de transferencia|clipboard)",
        Route.COMMAND,
        "clipboard.read",
        lambda _: {},
    ),
    # --- rotas sem ferramenta direta: só encurtam a decisão
    rule(
        "saudacao",
        r"^(oi|ola|opa|bom dia|boa tarde|boa noite|e ai|hey|hi|hello|tudo bem"
        r"|obrigado|obrigada|valeu|tchau|ate mais|thanks)\b",
        Route.CHAT,
    ),
    rule(
        "pesquisa_web",
        r"^(pesquise|pesquisar|procure na internet|busque na web|search|google)\b"
        r"|(noticias|cotacao|previsao do tempo|ultimas novidades)\b",
        Route.WEB,
        veto=MULTI_STEP,
    ),
    rule(
        "memoria_gravar",
        r"^(lembre|lembra|memorize|anote|guarde|nao esqueca)\s+(que|de|disso)\b",
        Route.MEMORY,
        veto=MULTI_STEP,
    ),
    rule(
        "memoria_consultar",
        r"^(o que (eu )?(te )?(disse|falei|contei)|voce lembra|do que voce lembra"
        r"|esqueca (o )?que)\b",
        Route.MEMORY,
    ),
)


def apply_rules(text: str) -> RuleHit | None:
    """Primeira regra que casar. ``None`` quando nenhuma se aplica."""
    normalized = normalize(text)
    if not normalized:
        return None
    for candidate in RULES:
        match = candidate.match(normalized)
        if match is None:
            continue
        arguments: dict[str, Any] = {}
        if candidate.tool is not None:
            if candidate.build_args is None:
                continue
            built = candidate.build_args(match)
            if built is None:
                continue  # a regra reconheceu a forma mas não os argumentos
            arguments = _restore_originals(text, match, built)
        return RuleHit(candidate.name, candidate.route, candidate.tool, arguments)
    return None


def _restore_originals(
    original: str, match: re.Match[str], arguments: dict[str, Any]
) -> dict[str, Any]:
    """Troca valores normalizados pelos trechos originais, com acento e caixa."""
    restored = dict(arguments)
    for key, group in (("name", "alvo"), ("text", "texto")):
        if key in restored and group in match.groupdict():
            recorte = _original_span(original, match, group)
            if recorte:
                restored[key] = recorte
    return restored
