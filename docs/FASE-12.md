# Fase 12 — Eventos e proatividade

Concluída em 2026-08-29.

## A cadeia

```
observador  →  barramento  →  política  →  Tool Bus  →  notificação
  percebe        publica       decide      autoriza      interrompe
```

Um observador não decide nada: percebe e publica. Quem decide é a política, com
as regras do usuário. Separar as duas coisas é o que permite **observar muito e
incomodar pouco**.

## A notificação passa pelo Tool Bus

A EVE não tem caminho privilegiado para falar com o usuário só porque a ideia
partiu dela. Quando ela decide avisar, chama `system.notify` como qualquer
outra ação — passa por permissão e fica na auditoria:

```
$ eve tool audit -n 2
17:31:47  system.notify  ok  proactive
```

## Conservadora por padrão

Quase tudo nasce **silencioso**. `file.changed`, `app.opened`, `git.changed` e
qualquer tipo desconhecido entram só no histórico.

| evento | padrão |
|---|---|
| `build.failed` | high |
| `test.failed` · `system.error` · `notification` | medium |
| `task.finished` · `mcp.disconnected` | low |
| **todo o resto** | **silent** |

Cinco níveis: `silent` (só histórico), `low` (aparece na interface), `medium`
(notificação), `high` (com som), `critical` (interrompe de verdade — e no
futuro, liga).

## Três coisas que a política precisa fazer

**Conter repetição.** Um observador de arquivos dispara dezenas de eventos por
minuto. Sem intervalo mínimo entre avisos do mesmo tipo, a EVE vira spam.

**Respeitar o silêncio.** Faixa de horas configurável, inclusive cruzando a
meia-noite. `critical` atravessa — é para isso que ele existe.

**Falhar calada.** Nível inválido na configuração vira `silent`, não barulho.

## Bug encontrado pelos testes

A regra do usuário `build.* = critical` **perdia** para o padrão embutido
`build.failed = high`. As duas fontes eram misturadas num dicionário só, e a
especificidade do padrão derrubava a escolha explícita de quem usa. Agora a
precedência é: regra do usuário primeiro (exata, depois padrão, depois `*`),
padrão embutido só depois.

## Observadores

| tipo | o que percebe |
|---|---|
| `files` | mudanças numa pasta, agrupadas por 2 s, com destaque para `package.json`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `.env` |
| `files` (git) | commits, filtrando o ruído interno do `.git` |
| `apps` | aplicativos abrindo e fechando (desligado por padrão) |

Um observador que quebra não derruba o Core: o erro fica visível em
`eve watch list`.

## Interfaces

```bash
eve watch add projeto ~/Documents/EVE
eve watch list | remove | status
eve watch simulate build.failed        # o que a EVE faria, sem notificar
```

```toml
[proactive]
min_interval = 60
quiet_hours = [22, 8]

[proactive.rules]
"build.failed" = "critical"
"file.changed" = "medium"
```

## Testes

555 no total (23 novos). O observador de arquivos é testado de verdade: cria um
arquivo e espera o evento chegar no barramento.
