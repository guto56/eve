"""Decidir o que o usuário quis dizer com "abre X".

X pode ser um aplicativo, um site, uma pasta ou um arquivo. A EVE resolve na
ordem do mais específico para o mais genérico e, quando não acha, diz que não
achou — em vez de abrir a coisa errada ou inventar um app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from eve.macos.apps import App, normalizar, search_apps

Kind = Literal["app", "url", "path", "web", "unknown"]

#: Serviços que a maioria usa pelo navegador. Consultado só depois de não
#: achar aplicativo instalado — o app sempre vence.
SERVICOS_WEB: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "whats": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "googledrive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "googlemaps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "instagram": "https://www.instagram.com",
    "insta": "https://www.instagram.com",
    "twitter": "https://x.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://pt.wikipedia.org",
    "twitch": "https://www.twitch.tv",
    "spotify": "https://open.spotify.com",
    "disney": "https://www.disneyplus.com",
    "primevideo": "https://www.primevideo.com",
    "globoplay": "https://globoplay.globo.com",
}

#: Ruído que aparece grudado no alvo e não faz parte do nome.
ENFEITES = re.compile(
    r"^(o|a|os|as|meu|minha|meus|minhas|um|uma)\s+|"
    r"\s+(pra mim|por favor|agora|ai|aqui)$",
    re.IGNORECASE,
)

PARECE_URL = re.compile(r"^(https?://|www\.)|^[\w-]+\.[a-z]{2,}(/|$)", re.IGNORECASE)

#: Quando o usuário diz o tipo, é isso que vale. "pasta EVE" é uma pasta,
#: mesmo que exista um aplicativo com nome parecido.
PISTAS: tuple[tuple[re.Pattern[str], tuple[Kind, ...]], ...] = (
    (re.compile(r"^(pasta|diretorio|diretório|folder)\s+", re.I), ("path",)),
    (re.compile(r"^(arquivo|file|documento)\s+", re.I), ("path",)),
    (re.compile(r"^(site|página|pagina|url|endereço|endereco)\s+", re.I), ("url", "web")),
    (re.compile(r"^(app|aplicativo|programa)\s+", re.I), ("app",)),
)


def _pista(alvo: str) -> tuple[str, tuple[Kind, ...] | None]:
    """Separa a pista de tipo do nome. Sem pista, tudo é possível."""
    for padrao, tipos in PISTAS:
        if padrao.match(alvo):
            return padrao.sub("", alvo).strip(), tipos
    return alvo, None


@dataclass(frozen=True)
class Target:
    kind: Kind
    value: str
    """O que efetivamente abrir: nome do app, URL ou caminho."""
    label: str
    """Como chamar isso ao falar com o usuário."""
    score: float = 1.0
    note: str = ""
    alternatives: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "label": self.label,
            "score": round(self.score, 2),
            "note": self.note,
            "alternatives": list(self.alternatives),
        }


def limpar(texto: str) -> str:
    anterior = ""
    limpo = texto.strip()
    while limpo != anterior:
        anterior = limpo
        limpo = ENFEITES.sub("", limpo).strip()
    return limpo


def resolve(texto: str, raizes: tuple[str, ...] = ("~",)) -> Target:
    """Descobre o que abrir. Nunca levanta: no pior caso devolve ``unknown``."""
    alvo, tipos = _pista(limpar(texto))
    if not alvo:
        return Target("unknown", "", texto, 0.0, "não entendi o que abrir")

    permitido = tipos or ("url", "path", "app", "web")
    tentativas = {
        "url": lambda: _como_url(alvo),
        "path": lambda: _como_caminho(alvo, raizes),
        "app": lambda: _como_app(alvo),
        "web": lambda: _como_servico(alvo),
    }
    for tipo in ("url", "path", "app", "web"):
        if tipo in permitido and (achado := tentativas[tipo]()) is not None:
            return achado

    if tipos == ("path",):
        return Target("unknown", "", alvo, 0.0, f"não achei nenhuma pasta ou arquivo {alvo!r}")

    parecidos = tuple(a.name for a in search_apps(alvo, limit=3, limiar=0.45))
    return Target(
        "unknown",
        "",
        alvo,
        0.0,
        f"não achei nada chamado {alvo!r} — nem app instalado, nem pasta, nem site conhecido",
        parecidos,
    )


def _como_url(alvo: str) -> Target | None:
    if not PARECE_URL.search(alvo):
        return None
    url = alvo if urlparse(alvo).scheme else f"https://{alvo}"
    return Target("url", url, alvo, 1.0, "endereço")


def _como_caminho(alvo: str, raizes: tuple[str, ...]) -> Target | None:
    candidatos = [Path(alvo).expanduser()]
    if not alvo.startswith(("~", "/", "./")):
        for raiz in raizes:
            base = Path(raiz).expanduser()
            candidatos += [base / alvo, base / "Documents" / alvo, base / "Downloads" / alvo]

    for candidato in candidatos:
        try:
            if candidato.exists():
                tipo = "pasta" if candidato.is_dir() else "arquivo"
                return Target("path", str(candidato), candidato.name, 1.0, tipo)
        except OSError:  # pragma: no cover - caminho absurdo
            continue
    return None


def _como_app(alvo: str) -> Target | None:
    achados = search_apps(alvo, limit=3)
    if not achados:
        return None
    melhor: App = achados[0]
    outros = tuple(a.name for a in achados[1:])
    nota = "" if melhor.score >= 0.95 else f"achei pelo nome parecido ({melhor.score:.0%})"
    return Target("app", melhor.name, melhor.name, melhor.score, nota, outros)


def _como_servico(alvo: str) -> Target | None:
    url = SERVICOS_WEB.get(normalizar(alvo))
    if url is None:
        return None
    return Target("web", url, alvo, 0.9, "não está instalado; abre no navegador")
