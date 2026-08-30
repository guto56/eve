# Fase 13 — Instalador

Concluída em 2026-08-30.

## Um comando

```bash
curl -fsSL https://get.eve.ai | bash
```

Ou, de uma cópia local, `./install.sh`. O script:

1. confere macOS ≥ 14, chip, RAM e disco — e recusa com motivo quando não dá;
2. instala `uv` e `ollama` pelo Homebrew, se faltarem, e sobe o Ollama;
3. instala o comando `eve`, avisando se `~/.local/bin` não está no PATH;
4. baixa os modelos locais (`qwen3.5:2b` e `embeddinggemma`);
5. baixa o Chromium do Playwright;
6. cria `~/EVE`;
7. instala o serviço de background;
8. roda `eve doctor` e abre a interface.

**Idempotente de verdade.** Rodar de novo com tudo pronto pula tudo:

```
· Homebrew 6.0.20      · uv já instalado       · ollama já instalado
· qwen3.5:2b já baixado · embeddinggemma já baixado · Chromium já instalado
✓ a EVE vai subir sozinha depois do login
```

## Serviço de background

`launchctl bootstrap gui/$UID`, não o antigo `load` — que sai com código 0
mesmo quando não carrega nada, transformando erro de instalação em mistério.

```bash
eve service install | uninstall | status
```

Verificado: depois de `kill -9`, o launchd reergueu a EVE sozinho (pid novo,
`/health` de volta em segundos).

## Três coisas que só apareceram usando

**Corrida entre descarregar e carregar.** `bootout` retorna antes de o launchd
terminar de desmontar; subir em cima disso falha com um erro pouco
explicativo. Agora esperamos o agente sumir de fato antes de subir o novo — é o
que faz `eve service install` duas vezes seguidas funcionar.

**Sinal não para um agente com KeepAlive.** `launchctl kill SIGTERM` fazia o
launchd tratar como queda e reerguer na hora. Parar de verdade é descarregar;
o arquivo continua instalado, então ela volta no próximo login ou com
`eve start`.

**Quem manda no processo é o launchd.** Com o serviço instalado o daemon não
tem pidfile, então `eve stop` não o alcançava e `eve update` chegava a subir um
segundo daemon condenado a não conseguir a porta. Agora `start`, `stop`,
`restart` e `update` verificam o serviço antes.

## `eve` sozinho abre a interface

O texto de ajuda prometia isso e o comando mostrava a ajuda. Agora cumpre.

## Atualizar e remover

```bash
eve update       # preserva memória, credenciais e configuração
eve uninstall    # preserva seus dados por padrão
```

`update` puxa do git quando há remoto, reinstala e reinicia pelo caminho certo
(serviço ou processo solto). `uninstall` mostra exatamente o que vai fazer
antes de perguntar, e por padrão não apaga nada seu — nem `~/.eve`, nem
`~/EVE`, nem as credenciais no Keychain.

## Os testes não mexem na sua máquina

`EVE_SERVICE_DISABLED=1` no ambiente de teste. Sem isso, um `eve stop` de teste
descarregava o serviço real e a suíte passava a brigar com a EVE de verdade
pela porta — foi exatamente o que aconteceu, e travou a execução.

## Testes

604 no total (7 novos).
