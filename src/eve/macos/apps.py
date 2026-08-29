"""Descobrir aplicativos instalados, de verdade.

O Spotlight já indexa todo bundle de aplicativo da máquina, em qualquer pasta —
`/Applications`, `/System/Applications`, `~/Applications`, dentro de um pendrive.
Perguntar a ele custa ~70 ms e encontra 318 apps nesta máquina, contra os 4 que
existem em `/Applications`.

Nada aqui é uma lista fixa de nomes conhecidos: a EVE olha o que está
instalado agora.
"""

from __future__ import annotations

import difflib
import plistlib
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from eve.logging import get_logger

log = get_logger(__name__)

CACHE_TTL = 120.0
"""Instalar um app é raro; reindexar a cada pedido seria desperdício."""

CONSULTA = "kMDItemContentType == 'com.apple.application-bundle'"

#: Abaixo disto o palpite é ruim demais para agir sem perguntar.
LIMIAR = 0.62


@dataclass(frozen=True)
class App:
    name: str
    path: Path
    score: float = 1.0
    aliases: tuple[str, ...] = ()
    """Nome localizado e apelidos, como o próprio macOS os conhece.

    É daqui que sai "Ajustes" para *System Settings*, "Calendário" para
    *Calendar* e "iCal" para o mesmo app — sem nenhuma tabela nossa."""

    @property
    def keys(self) -> set[str]:
        """Tudo por que este app pode ser chamado, em forma comparável."""
        termos = {self.name, *self.aliases}
        chaves = set()
        for termo in termos:
            limpo = termo.removesuffix(".app")
            chaves.add(normalizar(limpo))
            # Palavras soltas: "Google Chrome" também atende por "chrome".
            for palavra in re.split(r"[\s\-_]+", limpo):
                if len(palavra) >= 3:
                    chaves.add(normalizar(palavra))
        return {c for c in chaves if c}

    @property
    def bundle_id(self) -> str | None:
        plist = self.path / "Contents" / "Info.plist"
        try:
            with plist.open("rb") as fh:
                return plistlib.load(fh).get("CFBundleIdentifier")
        except (OSError, plistlib.InvalidFileException):
            return None


_cache: tuple[float, list[App]] = (0.0, [])


def normalizar(texto: str) -> str:
    """Forma comparável: sem acento, sem espaço, sem pontuação, minúscula.

    É o que faz "appstore", "App Store", "app_store" e "APP-STORE" caírem no
    mesmo lugar.
    """
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def installed_apps(force: bool = False) -> list[App]:
    """Todos os aplicativos instalados, pelo índice do Spotlight."""
    global _cache
    agora = time.monotonic()
    if not force and _cache[1] and agora - _cache[0] < CACHE_TTL:
        return _cache[1]

    caminhos = _spotlight() or _varredura()
    apelidos = _metadados(caminhos)

    apps: list[App] = []
    vistos: set[str] = set()
    for caminho in caminhos:
        if caminho.stem in vistos:
            continue
        vistos.add(caminho.stem)
        apps.append(App(name=caminho.stem, path=caminho, aliases=apelidos.get(str(caminho), ())))

    _cache = (agora, apps)
    return apps


def _spotlight() -> list[Path]:
    try:
        resultado = subprocess.run(
            ["/usr/bin/mdfind", CONSULTA],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("apps.spotlight_falhou", error=str(exc)[:120])
        return []
    return [Path(linha) for linha in resultado.stdout.splitlines() if linha.endswith(".app")]


def _varredura() -> list[Path]:
    """Plano B quando o Spotlight está desligado ou reindexando."""
    pastas = (
        Path("/Applications"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path("/Applications/Utilities"),
        Path.home() / "Applications",
    )
    achados: list[Path] = []
    for pasta in pastas:
        if pasta.is_dir():
            achados.extend(sorted(pasta.glob("*.app")))
    return achados


_CHAVE = re.compile(r"^(kMDItem\w+)\s*=\s*(.*)$")


def _metadados(caminhos: list[Path]) -> dict[str, tuple[str, ...]]:
    """Nome de exibição e apelidos de cada app, numa chamada só.

    São ~0,3 s para 316 aplicativos — barato o bastante para valer, e é o que
    faz "Ajustes" encontrar *System Settings* sem tabela de tradução nossa.
    """
    if not caminhos:
        return {}
    try:
        resultado = subprocess.run(
            [
                "/usr/bin/mdls",
                "-name",
                "kMDItemDisplayName",
                "-name",
                "kMDItemAlternateNames",
                *(str(c) for c in caminhos),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("apps.metadados_falharam", error=str(exc)[:120])
        return {}
    return _parse_mdls(resultado.stdout, caminhos)


def _parse_mdls(saida: str, caminhos: list[Path]) -> dict[str, tuple[str, ...]]:
    """Um registro por arquivo, na mesma ordem da entrada.

    O `mdls` não repete o caminho; ele emite os atributos de cada arquivo em
    sequência. Um registro termina quando uma chave já vista reaparece.
    """
    registros: list[dict[str, list[str]]] = []
    atual: dict[str, list[str]] = {}
    chave_aberta: str | None = None

    for linha in saida.splitlines():
        casou = _CHAVE.match(linha)
        if casou:
            chave, valor = casou.group(1), casou.group(2).strip()
            if chave in atual:
                registros.append(atual)
                atual = {}
            if valor == "(":
                atual[chave] = []
                chave_aberta = chave
            else:
                atual[chave] = [] if valor == "(null)" else [_limpar(valor)]
                chave_aberta = None
        elif chave_aberta is not None:
            item = linha.strip().rstrip(",")
            if item == ")":
                chave_aberta = None
            elif item:
                atual[chave_aberta].append(_limpar(item))

    if atual:
        registros.append(atual)

    saida_final: dict[str, tuple[str, ...]] = {}
    for caminho, registro in zip(caminhos, registros, strict=False):
        nomes = [
            *registro.get("kMDItemDisplayName", []),
            *registro.get("kMDItemAlternateNames", []),
        ]
        saida_final[str(caminho)] = tuple(dict.fromkeys(n for n in nomes if n))
    return saida_final


def _limpar(valor: str) -> str:
    valor = valor.strip().strip(",").strip()
    if valor.startswith('"') and valor.endswith('"'):
        valor = valor[1:-1]
    # O mdls escapa não-ASCII como \Uxxxx; nomes assim não nos servem.
    return "" if "\\U" in valor else valor
    """Plano B quando o Spotlight está desligado ou reindexando."""
    pastas = (
        Path("/Applications"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path("/Applications/Utilities"),
        Path.home() / "Applications",
    )
    achados: list[Path] = []
    for pasta in pastas:
        if pasta.is_dir():
            achados.extend(sorted(pasta.glob("*.app")))
    return achados


def find_app(consulta: str, limiar: float = LIMIAR) -> App | None:
    """Melhor aplicativo para o que o usuário escreveu, ou ``None``."""
    achados = search_apps(consulta, limit=1, limiar=limiar)
    return achados[0] if achados else None


def search_apps(consulta: str, limit: int = 5, limiar: float = LIMIAR) -> list[App]:
    """Aplicativos parecidos, do melhor para o pior.

    A pontuação vai do mais confiável ao menos: nome idêntico, começa com,
    contém, e por fim semelhança de caracteres — que é o que salva erro de
    digitação ("chorme" → "Chrome").
    """
    alvo = normalizar(consulta)
    if not alvo:
        return []

    pontuados: list[App] = []
    for app in installed_apps():
        score = max((_pontuar(alvo, chave) for chave in app.keys), default=0.0)
        if score >= limiar:
            pontuados.append(App(app.name, app.path, score, app.aliases))

    pontuados.sort(key=lambda a: (-a.score, len(a.name)))
    return pontuados[:limit]


def _pontuar(alvo: str, chave: str) -> float:
    """Do mais confiável ao menos: idêntico, começa com, contém, parecido."""
    if not chave:
        return 0.0
    if chave == alvo:
        return 1.0
    if chave.startswith(alvo) or alvo.startswith(chave):
        cobertura = min(len(alvo), len(chave)) / max(len(alvo), len(chave))
        return 0.90 + 0.09 * cobertura
    if alvo in chave or chave in alvo:
        return 0.82
    # Erro de digitação. Exige semelhança alta: "chorme" tem de virar
    # "chrome", não "home".
    parecido = difflib.SequenceMatcher(None, alvo, chave).ratio()
    return parecido if parecido >= 0.78 else 0.0


def is_running(nome: str) -> bool:
    from eve.macos import native

    alvo = normalizar(nome)
    return any(normalizar(app["name"]) == alvo for app in native.running_apps())
