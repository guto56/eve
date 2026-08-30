"""O cofre: a memória da EVE como arquivos Markdown que você pode abrir.

Um banco SQLite é ótimo para buscar e péssimo para confiar: você não abre, não
lê, não corrige. Aqui cada memória é um `.md` com frontmatter, dentro de
`~/EVE/Memória`, que é um vault de Obsidian — e os `[[colchetes]]` no texto são
as ligações entre elas.

**O arquivo é a verdade.** O banco continua existindo, mas como índice: ele é
reconstruído a partir daqui, nunca o contrário. Isso é o que permite editar uma
memória no Obsidian, ou apagar um arquivo, e a EVE concordar.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from eve.logging import get_logger
from eve.memory.models import Memory, MemoryKind

log = get_logger(__name__)

#: Uma pasta por camada. Os nomes são para humanos: quem abre o vault precisa
#: entender a divisão sem ler documentação nenhuma.
PASTAS: dict[MemoryKind, str] = {
    MemoryKind.SEMANTIC: "Fatos",
    MemoryKind.EPISODIC: "Diário",
    MemoryKind.PROCEDURAL: "Preferências",
    MemoryKind.WORKING: "Rascunhos",
}

#: Como cada camada aparece no frontmatter. Em português, porque quem lê é você.
TIPOS: dict[MemoryKind, str] = {
    MemoryKind.SEMANTIC: "fato",
    MemoryKind.EPISODIC: "diário",
    MemoryKind.PROCEDURAL: "preferência",
    MemoryKind.WORKING: "rascunho",
}
DE_TIPO = {valor: chave for chave, valor in TIPOS.items()}

PASTA_PESSOAS = "Pessoas"
PASTA_CONVERSAS = "Conversas"

#: Pastas cujo conteúdo a EVE nunca remaneja. Uma nota que o usuário guardou em
#: "Pessoas" fica em "Pessoas": arrumar a casa dos outros é a maneira mais
#: rápida de fazer alguém desconfiar do programa.
LIVRES = (PASTA_PESSOAS, PASTA_CONVERSAS)

#: Proibidos no nome do arquivo. Os quatro últimos não são do sistema: são do
#: Obsidian, que usa `[]` `#` `^` `|` na própria sintaxe de link.
PROIBIDOS = re.compile(r'[/\\:*?"<>|\[\]#^\x00-\x1f]')

LINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]*?)?\]\]")
"""``[[Nome]]`` ou ``[[Nome|como aparece]]`` — captura sempre o alvo."""

LIMITE_TITULO = 48
"""O título é um nome, não um resumo.

Uma nota chamada "Para rodar o projeto uv run eve start, depois eve web" é
impossível de linkar: ninguém escreve isso entre colchetes. Cortar na primeira
pausa da frase dá "Para rodar o projeto", que é o nome que a pessoa usaria.
"""

#: Onde a frase deixa de ser nome e começa a ser explicação.
PAUSA = re.compile(r"\s*[:;\u2014\u2013]\s*|,\s+(?=[a-z\u00e0-\u00fc])")


@dataclass(frozen=True)
class Nota:
    """Uma memória do jeito que está no disco."""

    memoria: Memory
    caminho: Path
    links: tuple[str, ...]
    """Alvos dos ``[[colchetes]]`` no corpo, na ordem em que aparecem."""


def titulo_de(conteudo: str, sugerido: str | None = None) -> str:
    """Um nome de arquivo curto e linkável.

    O título aparece no grafo do Obsidian e é por ele que os links resolvem —
    precisa ser curto o bastante para alguém digitar de cabeça.
    """
    bruto = (sugerido or "").strip() or _primeira_ideia(conteudo)
    limpo = PROIBIDOS.sub("", bruto).strip(" .-")
    limpo = re.sub(r"\s+", " ", limpo)
    if len(limpo) > LIMITE_TITULO:
        corte = limpo[:LIMITE_TITULO].rsplit(" ", 1)[0]
        limpo = (corte or limpo[:LIMITE_TITULO]).rstrip(" ,;:")
    return limpo or "sem título"


def _primeira_ideia(conteudo: str) -> str:
    primeira = conteudo.strip().split("\n")[0].strip()
    frase = re.split(r"(?<=[.!?])\s", primeira)[0].strip()
    return PAUSA.split(frase, maxsplit=1)[0].strip() or frase


def links_de(texto: str) -> tuple[str, ...]:
    vistos: dict[str, None] = {}
    for achado in LINK.finditer(texto):
        vistos.setdefault(achado.group(1).strip(), None)
    return tuple(vistos)


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


class Vault:
    """A pasta de memórias, e as operações de arquivo sobre ela."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz

    # ------------------------------------------------------------ estrutura

    def preparar(self) -> Path:
        for pasta in (*PASTAS.values(), PASTA_PESSOAS, PASTA_CONVERSAS):
            (self.raiz / pasta).mkdir(parents=True, exist_ok=True)
        leia = self.raiz / "LEIA-ME.md"
        if not leia.exists():
            leia.write_text(_LEIA_ME, encoding="utf-8")
        return self.raiz

    def pasta_de(self, kind: MemoryKind) -> Path:
        return self.raiz / PASTAS[kind]

    def varrer(self) -> Iterator[Path]:
        """Todo `.md` do cofre, menos o LEIA-ME."""
        for caminho in sorted(self.raiz.rglob("*.md")):
            if caminho.name != "LEIA-ME.md":
                yield caminho

    # -------------------------------------------------------------- escrita

    def caminho_para(self, memoria: Memory) -> Path:
        """Onde esta memória deve morar, sem colidir com outra.

        A colisão é resolvida pelo uid e não por um número: dois arquivos
        "Reunião" viram "Reunião" e "Reunião (a1b2)", e o segundo continua
        dizendo quem é mesmo depois de renomeado.
        """
        pasta = self.pasta_de(memoria.kind)
        base = titulo_de(memoria.content, memoria.title)
        candidato = pasta / f"{base}.md"
        if not candidato.exists() or self.uid_em(candidato) == memoria.uid:
            return candidato
        return pasta / f"{base} ({memoria.uid[:4]}).md"

    def escrever(self, memoria: Memory, corpo: str | None = None, em: Path | None = None) -> Path:
        """Grava a nota.

        ``em`` grava exatamente naquele arquivo. É o que permite dar cabeçalho
        a uma nota escrita à mão sem tirá-la do lugar onde o usuário a pôs.
        """
        destino = em or self._destino(memoria)
        destino.parent.mkdir(parents=True, exist_ok=True)
        anterior = self.caminho_por_uid(memoria.uid)
        texto = _montar(memoria, corpo if corpo is not None else memoria.content)

        tmp = destino.with_suffix(".md.tmp")
        tmp.write_text(texto, encoding="utf-8")
        tmp.replace(destino)
        if anterior is not None and anterior != destino and anterior.exists():
            # O conteúdo mudou o título: a nota se muda, não se duplica.
            anterior.unlink()
        return destino

    def _destino(self, memoria: Memory) -> Path:
        """Respeita onde a nota já está, quando ela está numa pasta livre."""
        atual = self.caminho_por_uid(memoria.uid)
        if atual is not None and any(pasta in atual.parts for pasta in LIVRES):
            return atual
        return self.caminho_para(memoria)

    def garantir_pessoa(self, nome: str) -> Path:
        """A nota de uma pessoa, criada vazia se ainda não existir.

        Um ``[[Marina]]`` sem nota do outro lado é um link pendente: aparece no
        grafo do Obsidian mas não leva a lugar nenhum. Criar a nota agora, mesmo
        sem nada dentro, dá à pessoa um lugar onde as menções se acumulam.
        """
        limpo = PROIBIDOS.sub("", nome).strip()
        existente = self.por_titulo(limpo)
        if existente is not None:
            return existente
        pasta = self.raiz / PASTA_PESSOAS
        pasta.mkdir(parents=True, exist_ok=True)
        caminho = pasta / f"{limpo}.md"
        memoria = Memory(content=f"{limpo}.", kind=MemoryKind.SEMANTIC, importance=0.4)
        memoria.title = limpo
        caminho.write_text(_montar(memoria, f"{limpo}."), encoding="utf-8")
        return caminho

    def registrar_conversa(self, sessao: str, falas: Sequence[str], lembrou: Sequence[str]) -> Path:
        """Anota uma conversa e a liga ao que ela produziu.

        Guarda o que o usuário disse, não o que a EVE respondeu: a resposta
        está no log, e um registro que repete a própria fala da EVE vira ela
        relendo a si mesma.
        """
        pasta = self.raiz / PASTA_CONVERSAS
        pasta.mkdir(parents=True, exist_ok=True)
        quando = datetime.now()
        caminho = pasta / f"{quando:%Y-%m-%d %Hh%M} {sessao[:8]}.md"

        if caminho.exists():
            nota = self.ler(caminho)
            memoria = nota.memoria if nota else _nova_conversa(quando, sessao)
        else:
            memoria = _nova_conversa(quando, sessao)

        anterior = self.ler(caminho).memoria.content if caminho.exists() else ""
        linhas = [anterior] if anterior else [f"Conversa de {quando:%d/%m/%Y}."]
        linhas += [f"- {fala}" for fala in falas]
        if lembrou:
            linhas.append("Virou memória: " + " · ".join(f"[[{t}]]" for t in lembrou))

        memoria.content = "\n".join(linhas).strip()
        memoria.title = caminho.stem
        return self.escrever(memoria, em=caminho)

    def apagar(self, uid: str) -> bool:
        caminho = self.caminho_por_uid(uid)
        if caminho is None:
            return False
        caminho.unlink(missing_ok=True)
        return True

    # -------------------------------------------------------------- leitura

    def ler(self, caminho: Path) -> Nota | None:
        """Lê uma nota. ``None`` se o arquivo não for legível como memória."""
        try:
            bruto = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("cofre.ilegivel", caminho=str(caminho), error=str(exc))
            return None

        dados, corpo = _separar(bruto)
        conteudo = corpo.strip()
        if not conteudo:
            return None

        memoria = _para_memoria(dados, conteudo, caminho)
        memoria.title = caminho.stem
        return Nota(memoria=memoria, caminho=caminho, links=links_de(corpo))

    def caminho_por_uid(self, uid: str) -> Path | None:
        for caminho in self.varrer():
            if self.uid_em(caminho) == uid:
                return caminho
        return None

    def por_titulo(self, titulo: str) -> Path | None:
        """Resolve um ``[[link]]`` como o Obsidian: pelo nome do arquivo.

        Sem acento e sem caixa, porque quem escreve o link à mão erra os dois e
        o link quebrado não avisa.
        """
        alvo = _sem_acento(titulo).casefold()
        for caminho in self.varrer():
            if _sem_acento(caminho.stem).casefold() == alvo:
                return caminho
        return None

    def uid_em(self, caminho: Path) -> str | None:
        """O uid da nota, sem ler o arquivo inteiro — o frontmatter fica no começo."""
        try:
            with caminho.open("r", encoding="utf-8") as fh:
                if fh.readline().rstrip("\n") != "---":
                    return None
                for _ in range(30):
                    linha = fh.readline()
                    if not linha or linha.rstrip("\n") == "---":
                        return None
                    if linha.startswith("uid:"):
                        return linha.split(":", 1)[1].strip() or None
        except (OSError, UnicodeDecodeError):
            return None
        return None


# --------------------------------------------------------------- frontmatter


def _separar(bruto: str) -> tuple[dict[str, Any], str]:
    """Divide frontmatter e corpo. Sem frontmatter, tudo é corpo."""
    if not bruto.startswith("---"):
        return {}, bruto
    partes = bruto.split("\n---", 2)
    if len(partes) < 2:
        return {}, bruto
    cabecalho = partes[0][3:]
    corpo = partes[1].lstrip("-").lstrip("\n") if len(partes) == 2 else partes[2]
    try:
        dados = yaml.safe_load(cabecalho) or {}
    except yaml.YAMLError as exc:
        # YAML quebrado é comum quando se edita à mão. Perder o texto por causa
        # de dois-pontos sem aspas seria bem pior que perder os metadados.
        log.warning("cofre.frontmatter_invalido", error=str(exc)[:120])
        return {}, corpo
    return (dados if isinstance(dados, dict) else {}), corpo


def _montar(memoria: Memory, corpo: str) -> str:
    linhas = [
        "---",
        f"uid: {memoria.uid}",
        f"tipo: {TIPOS[memoria.kind]}",
        f"importancia: {memoria.importance:.2f}",
        f"confianca: {memoria.confidence:.2f}",
        f"origem: {_valor(memoria.source)}",
        f"criada: {_quando(memoria.created_at)}",
        f"atualizada: {_quando(memoria.updated_at)}",
        f"usos: {memoria.use_count}",
    ]
    if memoria.session:
        linhas.append(f"sessao: {_valor(memoria.session)}")
    if memoria.expires_at:
        linhas.append(f"expira: {_quando(memoria.expires_at)}")
    linhas += ["tags:", f"  - eve/{TIPOS[memoria.kind]}", "---", "", corpo.strip(), ""]
    return "\n".join(linhas)


def _valor(texto: str) -> str:
    """Escapa o que quebraria o YAML — dois-pontos e colchetes de link."""
    if re.search(r"[:#\[\]{}]|^\s|\s$", texto):
        return '"' + texto.replace('"', '\\"') + '"'
    return texto


def _quando(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _instante(valor: Any, padrao: float) -> float:
    if isinstance(valor, int | float):
        return float(valor)
    if isinstance(valor, datetime):
        return valor.timestamp()
    if isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor).timestamp()
        except ValueError:
            return padrao
    return padrao


def _numero(valor: Any, padrao: float) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def _para_memoria(dados: dict[str, Any], conteudo: str, caminho: Path) -> Memory:
    """Monta a memória, aceitando arquivo escrito à mão sem metadado nenhum."""
    mtime = caminho.stat().st_mtime if caminho.exists() else None
    kind = DE_TIPO.get(str(dados.get("tipo", "")).strip(), _kind_pela_pasta(caminho))

    memoria = Memory(
        content=conteudo,
        kind=kind,
        importance=_numero(dados.get("importancia"), 0.5),
        confidence=_numero(dados.get("confianca"), 0.8),
        source=str(dados.get("origem") or "cofre"),
        session=str(dados["sessao"]) if dados.get("sessao") else None,
        created_at=_instante(dados.get("criada"), mtime or 0.0),
    )
    if dados.get("uid"):
        memoria.uid = str(dados["uid"]).strip()
    memoria.updated_at = _instante(dados.get("atualizada"), memoria.created_at)
    memoria.use_count = int(_numero(dados.get("usos"), 0))
    memoria.expires_at = _instante(dados.get("expira"), 0.0) or None
    return memoria


def _kind_pela_pasta(caminho: Path) -> MemoryKind:
    """Sem `tipo` no frontmatter, a pasta decide — foi você que a escolheu."""
    for kind, pasta in PASTAS.items():
        if pasta in caminho.parts:
            return kind
    if PASTA_CONVERSAS in caminho.parts:
        return MemoryKind.EPISODIC
    return MemoryKind.SEMANTIC


def _nova_conversa(quando: datetime, sessao: str) -> Memory:
    memoria = Memory(
        content="",
        kind=MemoryKind.EPISODIC,
        importance=0.35,
        source="conversa",
        session=sessao,
        created_at=quando.timestamp(),
    )
    return memoria


_LEIA_ME = """# A memória da EVE

Cada arquivo aqui é uma coisa que a EVE lembra. São Markdown comum: dá para
abrir em qualquer editor, e o Obsidian entende esta pasta como um vault.

- **Fatos** — o que é verdade sobre você e seus projetos
- **Diário** — o que aconteceu, com data
- **Preferências** — como você gosta que as coisas sejam feitas
- **Rascunhos** — o fio da conversa atual; some sozinho
- **Pessoas** e **Conversas** — quem foi citado e o que foi dito

Os `[[colchetes]]` ligam uma nota à outra. É isso que o grafo do Obsidian
desenha, e é assim que a EVE chega de uma memória às vizinhas.

Pode editar e apagar à vontade: o arquivo é a verdade, e a EVE se ajusta.
Só não mexa no `uid` do cabeçalho — é por ele que ela reconhece a nota.
"""
