# EVE

Assistente pessoal de IA local-first para macOS.

Estado: **Fase 7 concluída** — a EVE conversa, age no Mac, lembra e tem interface web. Veja [docs/PLANO.md](docs/PLANO.md).

```bash
eve start && eve web
```

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
| `eve start [-f]` | sobe o Core em background (ou no terminal com `-f`) |
| `eve stop` | encerra o Core |
| `eve restart` | reinicia o Core |
| `eve status [--json]` | estado do sistema e dos componentes |
| `eve doctor [--json]` | diagnóstico da instalação |
| `eve logs [-f] [-n N]` | logs do Core |
| `eve web` | abre a interface (sobe o Core se preciso) |
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
| `GET/PUT/DELETE /api/secrets` | credenciais (nunca devolve valores) |
| `GET /api/providers` · `POST /api/providers/reset` | provedores de IA |
| `POST /api/ai/ask` | conversa com um modelo (SSE quando `stream`) |
| `POST /api/chat` | conversa com a EVE (SSE) |
| `POST /api/route` | só a decisão de roteamento, sem executar |
| `GET/DELETE /api/sessions` | sessões de conversa |
| `GET/POST /api/memory` · `/search` · `/recent` | memória |

No WebSocket o servidor envia `hello`, depois o histórico (`replay: true`) e então os eventos ao vivo. O cliente pode enviar `{"op":"ping"}` e `{"op":"subscribe","patterns":["voice.*"]}`.
