"""Skills e MCP: validação de esquema, carga de manifesto e proteção de namespace."""

from __future__ import annotations

from pathlib import Path

import pytest

from eve.mcp.client import MCPServerConfig
from eve.mcp.manager import RESERVADOS, MCPManager
from eve.mcp.schema import SchemaError, validate
from eve.skills.catalog import CATALOGO, manifesto
from eve.skills.manager import SkillManager
from eve.skills.model import SkillError, load_skill
from eve.tools.registry import ToolRegistry

ECHO = {
    "type": "object",
    "properties": {"message": {"type": "string"}, "vezes": {"type": "integer"}},
    "required": ["message"],
}


# ------------------------------------------------------- validação de esquema


def test_argumentos_validos_passam() -> None:
    assert validate({"message": "oi", "vezes": 2}, ECHO) == {"message": "oi", "vezes": 2}


def test_obrigatorio_ausente() -> None:
    with pytest.raises(SchemaError, match="obrigatório"):
        validate({}, ECHO)


def test_tipo_errado() -> None:
    with pytest.raises(SchemaError, match="esperado string"):
        validate({"message": 42}, ECHO)


def test_booleano_nao_e_numero() -> None:
    """bool é subclasse de int em Python, mas não é integer no JSON Schema."""
    with pytest.raises(SchemaError):
        validate({"message": "oi", "vezes": True}, ECHO)


def test_extras_permitidos_por_padrao() -> None:
    """Servidor que não declara additionalProperties costuma aceitar extras."""
    assert validate({"message": "oi", "extra": 1}, ECHO)


def test_extras_recusados_quando_proibidos() -> None:
    schema = {**ECHO, "additionalProperties": False}
    with pytest.raises(SchemaError, match="inesperado"):
        validate({"message": "oi", "extra": 1}, schema)


def test_enum() -> None:
    schema = {"type": "object", "properties": {"cor": {"enum": ["azul", "verde"]}}}
    validate({"cor": "azul"}, schema)
    with pytest.raises(SchemaError, match="fora das opções"):
        validate({"cor": "roxo"}, schema)


def test_tipo_desconhecido_nao_reprova() -> None:
    validate({"x": object()}, {"type": "object", "properties": {"x": {"type": "coisa"}}})


# ------------------------------------------------------------------- Skills


def escrever_skill(raiz: Path, nome: str, conteudo: str) -> Path:
    diretorio = raiz / nome
    diretorio.mkdir(parents=True)
    (diretorio / "skill.toml").write_text(conteudo, encoding="utf-8")
    return diretorio


def test_carrega_manifesto(tmp_path: Path) -> None:
    diretorio = escrever_skill(
        tmp_path,
        "teste",
        """
name = "teste"
description = "uma skill"
keywords = ["github", "issue"]
requires_secrets = ["ALGUMA_CHAVE"]

[[mcp]]
name = "srv"
command = "npx"
args = ["-y", "pacote"]
env = { TOKEN = "@ALGUMA_CHAVE" }

[permissions]
"srv.*" = "confirm"
""",
    )
    skill = load_skill(diretorio)
    assert skill.name == "teste"
    assert skill.keywords == ("github", "issue")
    assert skill.mcp[0].env == {"TOKEN": "@ALGUMA_CHAVE"}
    assert skill.permissions == {"srv.*": "confirm"}
    assert skill.namespaces == ("srv",)


def test_manifesto_invalido(tmp_path: Path) -> None:
    diretorio = escrever_skill(tmp_path, "ruim", "isso [ não é toml")
    with pytest.raises(SkillError, match="TOML"):
        load_skill(diretorio)


def test_manifesto_ausente(tmp_path: Path) -> None:
    (tmp_path / "vazia").mkdir()
    with pytest.raises(SkillError, match=r"skill\.toml"):
        load_skill(tmp_path / "vazia")


def test_ativacao_por_palavra_chave(tmp_path: Path) -> None:
    diretorio = escrever_skill(
        tmp_path, "gh", 'name = "gh"\nkeywords = ["github", "repositório"]\n'
    )
    skill = load_skill(diretorio)
    assert skill.matches("abra meu github") is True
    assert skill.matches("qual o repositório?") is True
    assert skill.matches("que horas são") is False


def test_skill_sem_palavras_nunca_ativa(tmp_path: Path) -> None:
    skill = load_skill(escrever_skill(tmp_path, "muda", 'name = "muda"\n'))
    assert skill.matches("qualquer coisa") is False


@pytest.mark.parametrize("nome", sorted(CATALOGO))
def test_catalogo_embutido_e_valido(nome: str, tmp_path: Path) -> None:
    diretorio = escrever_skill(tmp_path, nome, manifesto(nome))
    skill = load_skill(diretorio)
    assert skill.name
    assert skill.description


def test_manager_instala_e_remove(tmp_path: Path, secret_store) -> None:
    manager = SkillManager(tmp_path, secret_store, MCPManager(ToolRegistry()))
    skill = manager.install("github")
    assert skill.name == "github"
    assert (tmp_path / "github" / "skill.toml").exists()
    assert manager.remove("github") is True
    assert manager.remove("github") is False


def test_credencial_ausente_desativa_a_skill(tmp_path: Path, secret_store) -> None:
    manager = SkillManager(tmp_path, secret_store, MCPManager(ToolRegistry()))
    manager.install("github")
    assert manager.missing_secrets(manager.get("github")) == ("GITHUB_TOKEN",)
    # Sem credencial, a Skill não entra no prompt nem sobe servidor.
    assert manager.active_for("abra meu github") == []

    secret_store.set("GITHUB_TOKEN", "ghp_x")
    assert manager.active_for("abra meu github")[0].name == "github"


def test_segredo_do_manifesto_e_so_o_nome(tmp_path: Path, secret_store) -> None:
    """O manifesto guarda `@NOME`; o valor só aparece na hora de subir."""
    manager = SkillManager(tmp_path, secret_store, MCPManager(ToolRegistry()))
    skill = manager.install("github")
    secret_store.set("GITHUB_TOKEN", "ghp_super_secreto")

    conteudo = (skill.path / "skill.toml").read_text()
    assert "ghp_super_secreto" not in conteudo
    assert "@GITHUB_TOKEN" in conteudo

    resolvido = manager._resolve(skill.mcp[0])
    assert resolvido.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_super_secreto"


def test_ligar_e_desligar_persiste(tmp_path: Path, secret_store) -> None:
    manager = SkillManager(tmp_path, secret_store, MCPManager(ToolRegistry()))
    manager.install("github")
    manager.set_enabled("github", False)
    assert load_skill(tmp_path / "github").enabled is False
    manager.set_enabled("github", True)
    assert load_skill(tmp_path / "github").enabled is True


def test_ligar_a_skill_nao_mexe_no_servidor_mcp(tmp_path: Path, secret_store) -> None:
    """Regressão: a edição do manifesto apagava qualquer linha começada em
    "enabled" — inclusive a de dentro de `[[mcp]]`. Como o padrão do servidor
    é ligado, um MCP que o usuário tinha desativado voltava a ligar sozinho ao
    mexer na Skill."""
    manager = SkillManager(tmp_path, secret_store, MCPManager(ToolRegistry()))
    skill = manager.install("github")
    manifesto = skill.path / "skill.toml"

    texto = manifesto.read_text(encoding="utf-8")
    manifesto.write_text(
        texto.replace('command = "npx"', 'command = "npx"\nenabled = false'), encoding="utf-8"
    )

    manager.load_all()
    manager.set_enabled("github", False)
    manager.set_enabled("github", True)

    recarregada = load_skill(tmp_path / "github")
    assert recarregada.enabled is True
    assert recarregada.mcp[0].enabled is False, "o servidor MCP desativado voltou a ligar"


def test_comentario_e_texto_do_manifesto_sobrevivem(tmp_path: Path, secret_store) -> None:
    """Editar o manifesto não pode reescrever o que o usuário escreveu nele."""
    manager = SkillManager(tmp_path, secret_store, MCPManager(ToolRegistry()))
    skill = manager.install("github")
    manifesto = skill.path / "skill.toml"
    manifesto.write_text(
        "# nota do usuário\n" + manifesto.read_text(encoding="utf-8"), encoding="utf-8"
    )

    manager.load_all()
    manager.set_enabled("github", False)

    texto = manifesto.read_text(encoding="utf-8")
    assert "# nota do usuário" in texto
    assert load_skill(tmp_path / "github").instructions.strip()


# ---------------------------------------------------------------------- MCP


async def test_namespace_do_sistema_e_protegido() -> None:
    """Uma extensão não pode sobrescrever ferramenta nativa."""
    manager = MCPManager(ToolRegistry())
    for reservado in ("file", "system", "memory"):
        assert reservado in RESERVADOS
        with pytest.raises(ValueError, match="namespace do sistema"):
            await manager.add(MCPServerConfig(name=reservado, command="npx"))


async def test_servidor_inexistente_falha_sem_derrubar() -> None:
    manager = MCPManager(ToolRegistry())
    conexao = await manager.add(
        MCPServerConfig(name="fantasma", command="comando-que-nao-existe-7742")
    )
    assert conexao.connected is False
    assert conexao.error
    assert len(manager.registry) == 0
    await manager.aclose()


def test_instrucoes_nao_viram_regra_de_permissao(tmp_path: Path) -> None:
    """Regressão: no TOML tudo depois de `[permissions]` pertence à tabela.
    A chave `instructions` virava uma permissão chamada "instructions"."""
    diretorio = escrever_skill(tmp_path, "github", manifesto("github"))
    skill = load_skill(diretorio)
    assert set(skill.permissions) == {"github.*"}
    assert "instructions" not in skill.permissions
    assert skill.instructions
