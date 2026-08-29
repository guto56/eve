# Fase 3 — Ferramentas do macOS

Concluída em 2026-08-29. 20 ferramentas nativas, 23 no total com as da Fase 2.

## O que a EVE já consegue fazer

| Namespace | Ferramentas |
|---|---|
| `app` | `open`, `activate`, `quit`, `list`, `frontmost` |
| `url` | `open` |
| `file` | `read`, `write`, `list`, `info`, `mkdir`, `move`, `copy`, `trash` |
| `clipboard` | `read`, `write` |
| `system` | `notify`, `volume`, `set_volume`, `screenshot`, `info`, `time` |

## Decisões

**Apple Events só quando não há alternativa.** `NSWorkspace`, `NSPasteboard` e
`NSFileManager` (PyObjC) resolvem apps, clipboard e Lixeira sem disparar prompt
de automação do macOS. `osascript` fica para volume, notificação e `app.quit`,
onde não existe caminho nativo equivalente.

**AppleScript recebe dados como `argv`, nunca por interpolação.** O script é
sempre uma constante nossa:

```applescript
on run argv
  display notification (item 1 of argv)
end run
```

Há teste passando `x" & (do shell script "echo INVADIDO") & "y` como texto e
verificando que ele volta literal.

**Apagar é mover para a Lixeira.** `file.trash` usa `trashItemAtURL` e devolve
onde o arquivo foi parar. Não existe ferramenta de exclusão definitiva.

**Cerca no sistema de arquivos.** Toda ferramenta de arquivo passa por
`resolve_safe_path`: resolve symlinks primeiro (um link não é atalho para
fora), exige que o destino esteja sob uma raiz permitida (padrão `~`) e recusa
áreas sensíveis mesmo dentro dela — `~/.ssh`, `~/.aws`, `~/.gnupg`,
`~/Library/Keychains`, entre outras. Tudo configurável em `[files]`.

**Nada bloqueante no event loop.** `osascript`, `open`, `screencapture`,
leituras, escritas, cópias e varreduras de pasta rodam em thread. O daemon
precisa continuar transmitindo eventos enquanto uma captura interativa espera
o usuário escolher a área.

**Falha previsível tem categoria própria.** `not_permitted`, `not_found`,
`already_exists`, `wrong_kind` e `timeout` são distintos de `handler_error`.
Quem chamou — modelo, interface ou pessoa — sabe se corrige o argumento, pede
permissão ou desiste.

## Permissões do macOS

`eve doctor` agora reporta o que já foi concedido. Nesta máquina:

- automação (System Events): **concedida**
- acessibilidade: **não concedida**
- gravação de tela: **não concedida** — `system.screenshot` devolve o erro
  dizendo exatamente onde autorizar

## Fora do escopo desta fase

Controle de mídia (play/pause/próxima) depende de teclas de sistema via
Acessibilidade. Ficou para depois de a permissão existir — preferi não
entregar ferramenta que não dá para testar de verdade.

Calendar e Reminders (EventKit) exigem um app empacotado com bundle id próprio
para o macOS conceder TCC. Entram junto com o empacotamento, na fase do
instalador.

## Testes

213 no total (62 novos), 32 s. Os testes de macOS rodam contra a máquina real:
clipboard de verdade (salvo e restaurado), lista de apps de verdade, Lixeira de
verdade (e o teste limpa o que colocou lá).
