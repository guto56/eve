"""Escolha de quais ferramentas o modelo vê.

Oferecer as 23 ferramentas de uma vez leva o prompt de ~30 para ~1.900 tokens e
a resposta de 0,5 s para 5 s. O corte é em duas etapas: a rota define os
namespaces plausíveis, e um escore lexical simples ordena o que sobrou.

O escore é deliberadamente burro e explicável. Um índice vetorial resolveria
melhor, mas custaria embeddings a cada mensagem — caro demais para uma decisão
que precisa caber em milissegundos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eve.router.routes import NAMESPACES, Route
from eve.router.rules import strip_accents
from eve.tools.registry import ToolRegistry

DEFAULT_LIMIT = 8

#: Palavras curtas e frequentes demais para distinguir ferramentas.
STOPWORDS = frozenset(
    """
    a o e de da do das dos em no na nos nas um uma uns umas para por com sem
    que se ao aos as os pelo pela como qual quais quando onde
    the of and to in for on with a an is are be
    """.split()
)

#: Sinônimos que o usuário usa mas a descrição da ferramenta não contém.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "file": ("arquivo", "arquivos", "pasta", "pastas", "diretorio", "documento"),
    "app": ("aplicativo", "programa", "janela", "aplicativos"),
    "clipboard": ("copiar", "colar", "transferencia", "copie", "cole"),
    "url": ("site", "link", "endereco", "navegador", "web"),
    "system": ("sistema", "computador", "mac", "maquina", "tela", "volume", "som"),
}

_WORD = re.compile(r"[a-z0-9]+")


def _raiz(palavra: str) -> str:
    """Normalização de plural, pobre e suficiente.

    Sem isto, "issues" não casa com "list_issues" nem com "create_issue", e a
    seleção enche as vagas por desempate alfabético. Um stemmer de verdade
    seria melhor e mais caro; aqui basta aproximar singular e plural.
    """
    if len(palavra) > 3 and palavra.endswith("s") and not palavra.endswith("ss"):
        return palavra[:-1]
    return palavra


def tokenize(text: str) -> set[str]:
    return {_raiz(w) for w in _WORD.findall(strip_accents(text.lower())) if w not in STOPWORDS}


@dataclass(frozen=True)
class ToolSelection:
    names: tuple[str, ...]
    reason: str

    def __len__(self) -> int:
        return len(self.names)


def keywords_for(name: str, description: str) -> set[str]:
    namespace = name.split(".")[0]
    words = tokenize(name.replace(".", " ").replace("_", " ")) | tokenize(description)
    return words | set(SYNONYMS.get(namespace, ()))


def select_tools(
    registry: ToolRegistry,
    route: Route,
    text: str,
    limit: int = DEFAULT_LIMIT,
    extra_namespaces: tuple[str, ...] = (),
) -> ToolSelection:
    """Ferramentas que valem a pena mandar ao modelo para esta mensagem.

    ``extra_namespaces`` vem das Skills ativas: uma Skill de GitHub só coloca
    as ferramentas dela no prompt quando a mensagem tem a ver com GitHub.
    """
    namespaces = tuple(dict.fromkeys(NAMESPACES.get(route, ()) + extra_namespaces))
    if not namespaces:
        return ToolSelection((), f"rota {route.value} não usa ferramentas")

    candidates = [spec for spec in registry if spec.namespace in namespaces]
    if not candidates:
        return ToolSelection((), f"nenhuma ferramenta nos namespaces {namespaces}")

    if len(candidates) <= limit:
        return ToolSelection(
            tuple(spec.name for spec in candidates),
            f"todas as {len(candidates)} da rota {route.value}",
        )

    palavras = tokenize(text)
    scored = []
    for spec in candidates:
        overlap = palavras & keywords_for(spec.name, spec.description)
        # Empate é resolvido pelo nome, para a seleção ser determinística.
        scored.append((len(overlap), spec.name))
    scored.sort(key=lambda item: (-item[0], item[1]))

    chosen = [name for _, name in scored[:limit]]
    melhores = scored[0][0]
    return ToolSelection(
        tuple(sorted(chosen)),
        f"{len(chosen)} de {len(candidates)} por relevância (melhor escore {melhores})",
    )
