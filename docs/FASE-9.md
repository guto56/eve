# Fase 9 — Skills e MCP

Concluída em 2026-08-29.

## MCP é extensão do Tool Bus, não um caminho paralelo

Uma ferramenta que chega de um servidor MCP entra no mesmo barramento das
nativas: validação de argumentos, decisão de permissão, confirmação e
auditoria. A spec §20 é explícita nisso, e §21 em não dar acesso irrestrito a
extensão desconhecida — por isso **toda ferramenta de MCP nasce CONFIRM**.

Verificado com o servidor oficial do GitHub:

```
$ eve skill install github
github instalada. Repositórios, issues, pull requests e código no GitHub
servidores MCP: github

$ eve mcp list
✓  github  stdio  26   github-mcp-server
26 ferramenta(s) de MCP no Tool Bus

$ eve tool call everything.echo -a '{"message":"olá do Tool Bus"}' --yes
"Echo: olá do Tool Bus"                                          167.8 ms

$ eve tool call everything.echo -a '{}' --yes
invalid_args: message: obrigatório
```

## Decisões

**Validação de esquema cru.** Ferramentas nativas descrevem argumentos com
pydantic; as de MCP chegam com JSON Schema. Em vez de converter (e perder
fidelidade), a EVE valida o subconjunto que os servidores usam de verdade —
como primeira barreira, não como última: o servidor valida de novo.

**Namespaces do sistema são protegidos.** Um servidor MCP não pode se chamar
`file`, `system`, `memory` — senão uma extensão sobrescreveria uma ferramenta
nativa. A conexão é recusada com o motivo.

**Um servidor por tarefa própria.** O contexto do servidor (processo, streams,
sessão) precisa ser aberto e fechado na mesma tarefa, exigência do anyio que o
SDK usa. Cada conexão vive numa tarefa que abre tudo, avisa que está pronta e
espera o sinal de parada; as chamadas vêm de outras tarefas pela sessão aberta.

**Skills declaram tudo que trazem.** Instruções, servidores MCP, permissões,
credenciais necessárias e quando são relevantes. Nada implícito.

**O manifesto guarda o nome da credencial, nunca o valor.** `@GITHUB_TOKEN` é
resolvido no Keychain só na hora de subir o servidor — a Skill pode ser lida,
versionada e compartilhada sem vazar nada. Tem teste conferindo que o valor
não aparece no arquivo.

**Skill só entra no prompt quando é relevante.** Casamento por palavra-chave,
burro de propósito: decidir isso com modelo custaria uma chamada por mensagem,
e errar para menos apenas deixa a Skill de fora — errar para mais enche o
prompt.

```
"quais aplicativos estão abertos"    →  0 ferramentas (caminho rápido)
"liste minhas issues no github"      →  8 ferramentas do GitHub
```

**Padrão da Skill por baixo, escolha do usuário por cima.** Quem instala
decide o padrão de permissão; quem usa decide o final.

## Dois bugs encontrados rodando

1. **Instalar uma Skill derrubava os servidores MCP avulsos.** O recarregamento
   relia as Skills do disco mas usava a configuração carregada no start, onde
   o servidor recém-adicionado não existia.
2. **"issues" não casava com "create_issue".** Sem normalização de plural, o
   escore lexical dava zero para as ferramentas certas e cinco irrelevantes
   entravam por desempate alfabético.

## Interfaces

```bash
eve skill list | info | install | remove | enable | disable
eve mcp list | add | remove | reconnect | tools
```

Catálogo embutido: `github`, `filesystem`, `fetch`. Um marketplace de verdade é
assunto da spec §41.

## Testes

504 no total (22 novos).
