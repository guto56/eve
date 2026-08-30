"""Catálogo embutido de Skills.

Poucas, conhecidas e úteis desde o primeiro dia. Um marketplace de verdade é
assunto da spec §41; isto aqui é o suficiente para `eve skill install github`
funcionar numa instalação recém-feita.
"""

from __future__ import annotations

CATALOGO: dict[str, str] = {
    "github": """
name = "github"
version = "0.1.0"
description = "Repositórios, issues, pull requests e código no GitHub"
keywords = ["github", "repositório", "repositorio", "issue", "pull request", "commit", "branch"]
requires_secrets = ["GITHUB_TOKEN"]
instructions = '''
Ao trabalhar com GitHub, confirme o repositório antes de agir se o usuário não
disser qual. Nunca faça push nem abra pull request sem pedido explícito.
'''

[[mcp]]
name = "github"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "@GITHUB_TOKEN" }

[permissions]
"github.*" = "confirm"
""",
    "filesystem": """
name = "filesystem"
version = "0.1.0"
description = "Leitura e escrita de arquivos por um servidor MCP dedicado"
keywords = ["arquivo", "pasta", "diretório", "diretorio"]

[[mcp]]
name = "fs"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"]

[permissions]
"fs.*" = "confirm"
""",
    "fetch": """
name = "fetch"
version = "0.1.0"
description = "Buscar e ler páginas da web"
keywords = ["site", "página", "pagina", "url", "link", "buscar na web"]

[[mcp]]
name = "fetch"
command = "uvx"
args = ["mcp-server-fetch"]

[permissions]
"fetch.*" = "safe"
""",
}


def disponivel(nome: str) -> bool:
    return nome in CATALOGO


def manifesto(nome: str) -> str:
    if nome not in CATALOGO:
        disponiveis = ", ".join(sorted(CATALOGO))
        raise KeyError(f"Skill desconhecida: {nome}. Disponíveis: {disponiveis}")
    return CATALOGO[nome].strip() + "\n"
