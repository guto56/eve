# EVE

Assistente pessoal de IA local-first para macOS.

Estado: **Fase 13 concluída** — a EVE conversa por texto e por voz, age no Mac, lembra, pesquisa na web, controla um navegador, executa tarefas de várias etapas, percebe o que acontece e avisa quando importa, tem interface web e se estende por Skills e MCP. Veja [docs/PLANO.md](docs/PLANO.md).

## Instalação

```bash
curl -fsSL https://raw.githubusercontent.com/guto56/eve/main/install.sh | bash
```

De uma cópia local do projeto, `./install.sh`. O instalador confere o
computador, instala o que falta e cria `~/EVE`. Ele não deixa nada rodando:
quem escolhe como a EVE começa é você. Rodar de novo é seguro — ele pula o que
já está pronto e nunca toca em memória, credenciais ou configuração.

No fim ele abre o `eve setup`, que pergunta o essencial: onde a EVE pensa,
quais chaves você tem e se ela sobe sozinha. A primeira pergunta vem antes de
qualquer download, porque é ela que decide se vale baixar os modelos locais.

```bash
eve run                 # no terminal: você vê tudo, Ctrl+C encerra
eve start && eve web    # em segundo plano (`eve stop` encerra)
eve setup               # refazer as escolhas quando quiser
```

## Conversa ao vivo

Uma página própria, no menu à direita. Dois motores, escolhidos por
`ai.live_engine` no `config.toml` ou pelo parâmetro `?motor=`:

| Motor | Como funciona | Precisa de |
| --- | --- | --- |
| `openrouter` (padrão) | Deepgram ouve, o modelo pensa, Cartesia fala — passando pelo motor de conversa da EVE, com rota, ferramentas e memória iguais aos do chat | `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY` |
| `gemini` | um modelo só, que ouve e fala; menos latência por não trocar de mãos | `GOOGLE_API_KEY` |

No caminho do Gemini as ferramentas não vão para o Google executar: ele pede, e
quem executa é o Tool Bus daqui, com permissão e auditoria. É por isso que uma
conversa falada pode **ver, criar, corrigir e apagar** memória sem virar um
caminho paralelo sem regra.

`auto` prefere o motor que a máquina consegue rodar agora; faltando tudo, a
página diz o que falta e não pede o microfone.

## Telefone

Dá para ligar para a EVE e conversar. O áudio da linha é μ-law a 8 kHz, e tanto
o Deepgram quanto o Cartesia falam esse formato direto — o som da chamada não
passa por conversão nenhuma. A conversa é a mesma do chat: mesmo motor, mesmas
ferramentas, mesma memória.

O `eve setup` pergunta se você quer telefone e cuida do resto: pede as chaves
do Twilio, descobre os números da conta e guarda o que a EVE vai atender. O
instalador só traz o `cloudflared` se você disser que sim.

Depois, um comando põe o telefone no ar:

```bash
eve phone tunnel     # abre o endereço e aponta seu número para cá
```

Ele aponta sozinho porque precisa: o endereço do túnel muda toda vez que ele
sobe, e colar a URL nova no painel do Twilio a cada reinício seria o contrário
de plug and play. Enquanto o comando estiver aberto, o telefone toca.

```bash
eve phone status               # o que ainda falta
eve phone allow +5531999998888 # quem mais pode ligar
eve phone number               # trocar o número que ela atende
```

**A telefonia sobe num app próprio, numa porta própria.** Um túnel expõe uma
porta inteira: se fosse a do Core, `/api/tools/file.trash/call` estaria na
internet. Nessa porta existem duas rotas, as duas do Twilio, e mais nada.

Três provas independentes, e a chamada precisa passar nas três:

1. **a assinatura do Twilio**, que prova que o pedido veio dele;
2. **o número de quem liga**, contra a lista que você escreve — vazia recusa
   todo mundo, porque um número de telefone é público por natureza;
3. **um bilhete de uso único** na URL do áudio, senão o WebSocket seria um
   caminho que pula as duas primeiras.

Nenhuma basta sozinha: a primeira não diz *quem* ligou, a segunda é
falsificável sem a primeira, e a terceira só protege o segundo salto.

## Onde a EVE pensa

| Modo | O que roda aqui | O que isso custa |
| --- | --- | --- |
| `hybrid` (padrão) | o modelo pequeno classifica, extrai memória e responde o simples | ~2 GB de download; o difícil ainda vai para a nuvem |
| `external` | nada | zero download, mas toda mensagem sai do computador e é cobrada, e a memória busca por texto em vez de por sentido |

O modo fica em `ai.mode` no `config.toml`; `eve setup` grava e o Core aplica
sem precisar de mais nada. No modo `external` os papéis rápidos (`local`,
`fast`) passam a apontar para o modelo barato do OpenRouter, então nada no
código precisa saber em que modo está.

Para instalar já com o serviço, `EVE_AUTOSTART=1 ./install.sh`.

Sem instalar, tudo funciona com `uv run eve ...` de dentro do projeto.

## Interface

```bash
cd ui && npm install && npm run build   # sai em src/eve/web/static
cd ui && npm run dev                     # desenvolvimento, com proxy para o Core
```

## Desenvolvimento

```bash
uv sync
uv run eve doctor
uv run eve start
uv run eve status
uv run eve stop
```

Testes e lint:

```bash
uv run pytest -q
uv run ruff check src tests
```

## Comandos

| Comando | O que faz |
|---|---|
| `eve` | abre a interface |
| `eve run` | roda no terminal, mostrando o que ela faz; Ctrl+C encerra |
| `eve start` | sobe o Core em segundo plano |
| `eve stop` | encerra o Core |
| `eve restart` | reinicia o Core |
| `eve status [--json]` | estado do sistema e dos componentes |
| `eve doctor [--json]` | diagnóstico da instalação |
| `eve logs [-f] [-n N]` | logs do Core |
| `eve web` | abre a interface (sobe o Core se preciso) |
| `eve setup` | refaz as escolhas: modo, chaves e autostart |
| `eve config path` | onde vivem configuração, banco e logs |
| `eve config show [--json]` | configuração efetiva (arquivo + ambiente) |
| `eve tool list/show/call` | ferramentas: listar, inspecionar, executar |
| `eve tool approvals/approve/deny` | confirmações pendentes |
| `eve tool audit [-n N]` | últimas chamadas de ferramenta |
| `eve permission list/set/unset` | política de permissões |
| `eve permission grant/revoke` | concessões para ferramentas privilegiadas |
| `eve key list/set/delete/import` | credenciais no Keychain |
| `eve provider list/models` | provedores de IA e seus modelos |
| `eve ask "..." [-r papel]` | pergunta a um modelo, com streaming |
| `eve chat [mensagem] [-v]` | conversa com a EVE (sem mensagem, abre um REPL) |
| `eve memory list/search/add/forget/stats` | memória persistente |
| `eve voice say "..."` · `eve voice test` | fala e diagnóstico de voz |
| `eve skill list/install/remove/enable/disable` | Skills |
| `eve mcp list/add/remove/reconnect/tools` | servidores MCP |
| `eve task list/show/cancel` | tarefas de agente |
| `eve watch add/list/remove/status/simulate` | observadores e proatividade |
| `eve service install/uninstall/status` | subir sozinha depois do login |
| `eve update` · `eve uninstall` | atualizar e remover, preservando seus dados |

## Credenciais

Ficam no Keychain do macOS, nunca em arquivo. No comando vai só o **nome** da
credencial; a chave em si você cola no prompt seguinte, que não mostra o que
foi digitado:

```bash
eve key set OPENROUTER_API_KEY
```

```
Cole a chave de OPENROUTER_API_KEY (não aparece na tela):
```

`eve key list` mostra os nomes que a EVE conhece, quais já estão gravados e o
que ainda falta.

## A memória

Fica em `~/EVE/Memória`, em Markdown. É um vault de Obsidian: abra a pasta e
você vê tudo que a EVE lembra, com o grafo desenhado.

```
~/EVE/Memória/
├── Fatos/          o que é verdade sobre você e seus projetos
├── Diário/         o que aconteceu, com data
├── Preferências/   como você gosta que as coisas sejam feitas
├── Rascunhos/      o fio da conversa atual; some sozinho
├── Pessoas/        quem foi citado
└── Conversas/      o que foi dito, ligado ao que virou memória
```

Cada nota é um `.md` com frontmatter, e os `[[colchetes]]` ligam uma à outra.
Uma conversa aponta para o fato que produziu; o fato aponta para as pessoas
que cita; a pessoa acumula um histórico que ninguém escreveu.

**O arquivo é a verdade.** O SQLite continua existindo, mas como índice —
busca textual, vetorial e o grafo — e é reconstruído a partir dos arquivos,
nunca o contrário. Na prática:

- editar uma nota no Obsidian corrige a memória, com a EVE rodando;
- apagar o arquivo apaga a memória;
- escrever um `.md` à mão vira memória, e a EVE só acrescenta o cabeçalho —
  sem tirar o arquivo do lugar onde você o pôs.

Só não mexa no `uid` do cabeçalho: é por ele que ela reconhece a nota.

## Onde as coisas ficam

Tudo sob `~/.eve` (ou `$EVE_HOME`):

```
~/.eve/
├── config.toml   configuração (opcional; há padrões para tudo)
├── data/eve.db   memória e estado
├── logs/         eve.log (estruturado) e daemon.out (stdout bruto)
├── run/eve.pid   pid do daemon
├── skills/
└── models/
```

Configuração por ambiente: `EVE_SERVER__PORT=9000 eve start`.

## API do Core

| Rota | Descrição |
|---|---|
| `GET /health` | vivo? |
| `GET /api/status` | estado detalhado |
| `GET /api/docs` | OpenAPI |
| `WS /ws?topics=tool.*&history=20` | eventos em tempo real |
| `GET /api/tools` · `POST /api/tools/{nome}/call` | ferramentas |
| `GET /api/approvals` · `POST /api/approvals/{id}` | confirmações |
| `GET /api/permissions` · `POST /api/permissions/reload` | política |
| `GET /api/audit` | trilha de auditoria |
| `GET /api/logs` · `/api/logs/sources` | logs do daemon, para a aba de acompanhamento |
| `GET/PUT/DELETE /api/secrets` | credenciais (nunca devolve valores) |
| `GET /api/providers` · `POST /api/providers/reset` | provedores de IA |
| `POST /api/ai/ask` | conversa com um modelo (SSE quando `stream`) |
| `POST /api/chat` | conversa com a EVE (SSE) |
| `POST /api/route` | só a decisão de roteamento, sem executar |
| `GET/DELETE /api/sessions` | sessões de conversa |
| `GET/POST /api/memory` · `/search` · `/recent` | memória |
| `WS /ws/voice` | microfone e fala (áudio binário nos dois sentidos) |

No WebSocket o servidor envia `hello`, depois o histórico (`replay: true`) e então os eventos ao vivo. O cliente pode enviar `{"op":"ping"}` e `{"op":"subscribe","patterns":["voice.*"]}`.
