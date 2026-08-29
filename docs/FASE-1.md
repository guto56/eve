# Fase 1 — Fundação e Core daemon

Concluída em 2026-08-29.

## O que entrou

| Módulo | Responsabilidade |
|---|---|
| `eve.paths` | árvore de diretórios sob `EVE_HOME`, redirecionável |
| `eve.config` | TOML + ambiente, com o ambiente vencendo o arquivo |
| `eve.logging` | structlog para console e arquivo, console ou JSON |
| `eve.events` | modelo de evento imutável e casamento de tópicos (`tool.*`) |
| `eve.bus` | pub/sub assíncrono, histórico circular, publicação que nunca bloqueia |
| `eve.daemon` | FastAPI: `/health`, `/api/status`, `WS /ws` |
| `eve.cli` | `eve` — start, stop, restart, status, doctor, logs, web, config |
| `eve.doctor` | 8 verificações, extensíveis por fase |

## Decisões

**Publicar nunca bloqueia.** Cada assinante tem fila limitada; quando enche, o
evento mais antigo é descartado e contabilizado em `dropped`. Um cliente lento
degrada a si mesmo, nunca ao Core.

**O daemon é a fonte da verdade.** CLI e interface web são clientes do mesmo
processo, o que satisfaz a exigência de paridade da spec §25 sem duplicar lógica.

**Ambiente vence arquivo.** Permite `EVE_SERVER__PORT=9000 eve start` sem editar
nada, e é como o pai passa host e porta ao filho na hora de subir o daemon.

## Bugs encontrados pelos próprios testes

1. **`history(limit=0)` devolvia tudo** — `found[-0:]` é `found[0:]`. Um cliente
   que pedisse "sem histórico" recebia o histórico inteiro.
2. **Processo zumbi contado como vivo** — `os.kill(pid, 0)` tem sucesso em um
   filho encerrado e ainda não colhido. `eve stop` esperava 10 s, partia para
   SIGKILL e ainda assim reportava falha. Agora `pid_alive` colhe o filho antes
   de decidir.
3. **PID reciclado** — um pidfile obsoleto podia apontar para um processo alheio.
   `running_pid` agora confirma pela linha de comando que o PID é mesmo da EVE
   antes de qualquer sinal.

## Testes

72 testes, 5 s. Inclui um teste de integração que sobe o daemon de verdade em
outro processo e valida pidfile, status, doctor, WebSocket ao vivo, logs,
restart e stop.
