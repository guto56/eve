# EVE — Plano de execução

Documento vivo. Cada fase só é considerada concluída quando todos os testes dela passam.

## Stack decidida (pesquisa de 2026-08-29)

| Camada | Escolha | Por quê |
|---|---|---|
| Linguagem do Core | Python 3.13 + `uv` | SDK MCP Tier 1, `mlx-lm`, PyObjC, Playwright, sqlite-vec e SDKs de voz — tudo numa linguagem só |
| API + tempo real | FastAPI + uvicorn (WebSocket) | streaming de tokens e eventos nativo |
| CLI | Typer + Rich | paridade com a interface web |
| Logs | structlog (JSON) | observabilidade (§38) |
| Config | pydantic-settings + TOML em `~/.eve/` | validado e tipado |
| Banco | SQLite WAL + FTS5 + sqlite-vec 0.1.9 | local-first, sem servidor (§18, §37) |
| IA local | Ollama (usa MLX no Apple Silicon) — qwen3.5:2b | escolhido por benchmark: 13/13 de acurácia de intenção em 510 ms, 2,7 GB |
| IA externa | OpenRouter — gemini-3.1-flash-lite (padrão) e claude-sonnet-5 (pesado) | um endpoint para 396 modelos |
| STT | Deepgram Nova-3 streaming | ~60–80 ms de endpointing |
| TTS | Cartesia Sonic | TTFA sub-100 ms, protocolo WebSocket nativo |
| Busca web | Tavily | API de busca pensada para LLM, com fontes |
| Navegador | Playwright | automação real (§15) |
| macOS nativo | PyObjC + AppleScript/JXA como fallback | Calendar, Reminders, Notifications, Accessibility |
| Extensibilidade | SDK oficial `mcp` 2.1.1 | cliente MCP (§20) |
| Interface web | React + Vite + TypeScript | UI conversacional em tempo real |
| Serviço | launchd LaunchAgent | roda após o login (§14) |

Restrição dominante do alvo: **Apple M1, 8 GB de RAM**. Nada de modelo local acima de ~3B.

## Fases

| # | Fase | Entrega | Estado |
|---|---|---|---|
| 1 | Fundação + Core daemon | repo, config, logging, event bus, daemon FastAPI, CLI `eve` (start/stop/status/doctor/logs) | **concluída** |
| 2 | Tool Bus + Permissões | registro de tools, validação, Permission Engine (SAFE/CONFIRM/PRIVILEGED/BLOCKED), auditoria | **concluída** |
| 3 | Tools do macOS | apps, URLs, arquivos, clipboard, notificações, volume, screenshot, sistema | **concluída** |
| 4 | Secrets + Providers | Keychain, migração do `env.txt`, cliente Ollama, cliente OpenRouter | **concluída** |
| 5 | Router + chat | classificação de intenção, streaming ponta a ponta local↔externo | **concluída** |
| 6 | Memória | SQLite + FTS5 + sqlite-vec, 4 camadas, Memory Manager | **concluída** |
| 7 | Interface web | React/Vite, chat, estados, tarefas, tools ao vivo | **concluída** |
| 8 | Voz | Deepgram STT streaming, Cartesia TTS, VAD, barge-in | **concluída** (wake word fica para depois) |
| 9 | Skills + MCP | instalar/remover/ativar Skills; cliente MCP no Tool Bus | |
| 10 | Web + navegador | Tavily, Playwright como Tool | |
| 11 | Agentes | tarefas multi-etapa com progresso | |
| 12 | Eventos + proatividade | Event System, observadores, notificações por prioridade | |
| 13 | Instalador | comando único, idempotente, atualizável, reversível | |
| 14 | Telefonia (futuro) | Twilio/SIP, chamadas por evento | |

## Regras de execução

1. Pesquisar antes de cada fase.
2. Nenhuma fase avança com teste vermelho.
3. Nada só na CLI: o que faz sentido existe também na interface (§25).
4. Modelo nunca executa ação direta — sempre via Tool Bus (§7 dos princípios).
5. Secrets no Keychain, nunca em código, log ou banco (§22).
