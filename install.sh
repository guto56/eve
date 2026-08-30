#!/usr/bin/env bash
#
# Instalador da EVE.
#
#   curl -fsSL https://raw.githubusercontent.com/guto56/eve/main/install.sh | bash
#
# Ou, de uma cópia local do projeto:
#
#   ./install.sh
#
# É idempotente: rodar de novo conserta o que faltar e não refaz o que já está
# pronto. Nunca toca em memória, credenciais nem configuração existentes.
#
set -euo pipefail

REPO="${EVE_REPO:-https://github.com/guto56/eve.git}"
# O código é infraestrutura: vai para a pasta oculta. ~/EVE fica para
# o que é do usuário.
DESTINO="${EVE_SOURCE:-$HOME/.eve/src}"
MODELO="${EVE_MODEL:-qwen3.5:2b}"
EMBEDDINGS="${EVE_EMBEDDING_MODEL:-embeddinggemma}"

# ---------------------------------------------------------------- aparência

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; VERDE=$'\033[32m'; AMARELO=$'\033[33m'
  VERMELHO=$'\033[31m'; CIANO=$'\033[36m'; FIM=$'\033[0m'
else
  BOLD=""; DIM=""; VERDE=""; AMARELO=""; VERMELHO=""; CIANO=""; FIM=""
fi

passo()  { printf "\n  %s%s%s\n" "$BOLD" "$1" "$FIM"; }
ok()     { printf "    %s✓%s %s\n" "$VERDE" "$FIM" "$1"; }
pulou()  { printf "    %s·%s %s%s%s\n" "$DIM" "$FIM" "$DIM" "$1" "$FIM"; }
aviso()  { printf "    %s!%s %s\n" "$AMARELO" "$FIM" "$1"; }
erro()   { printf "\n  %s✗ %s%s\n\n" "$VERMELHO" "$1" "$FIM" >&2; exit 1; }
tem()    { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------- compatível?

passo "Verificando o computador"

[ "$(uname -s)" = "Darwin" ] || erro "A EVE é para macOS. Este sistema é $(uname -s)."

VERSAO_OS="$(sw_vers -productVersion)"
MAIOR="${VERSAO_OS%%.*}"
[ "$MAIOR" -ge 14 ] || erro "Precisa de macOS 14 ou mais novo. Este é o $VERSAO_OS."
ok "macOS $VERSAO_OS"

CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo desconhecido)"
case "$(uname -m)" in
  arm64) ok "$CHIP" ;;
  *) aviso "$CHIP — sem Apple Silicon o modelo local fica bem mais lento" ;;
esac

RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [ "$RAM_GB" -lt 8 ]; then
  erro "${RAM_GB} GB de RAM. A EVE precisa de pelo menos 8 GB."
elif [ "$RAM_GB" -le 8 ]; then
  aviso "${RAM_GB} GB de RAM — use modelos locais de até 3B"
else
  ok "${RAM_GB} GB de RAM"
fi

LIVRE_GB=$(( $(df -k "$HOME" | awk 'NR==2 {print $4}') / 1048576 ))
[ "$LIVRE_GB" -ge 8 ] || erro "${LIVRE_GB} GB livres. Precisa de pelo menos 8 GB."
ok "${LIVRE_GB} GB livres em disco"

# ----------------------------------------------------------- dependências

passo "Preparando as dependências"

if ! tem brew; then
  erro "Homebrew não está instalado. Instale em https://brew.sh e rode de novo."
fi
pulou "Homebrew $(brew --version | head -1 | cut -d' ' -f2)"

instalar_brew() {
  if tem "$1"; then
    pulou "$1 já instalado"
  else
    printf "    %s…%s instalando %s\n" "$DIM" "$FIM" "$1"
    brew install "$2" >/dev/null 2>&1 || erro "não consegui instalar $1"
    ok "$1 instalado"
  fi
}

instalar_brew uv uv
pulou "o Ollama vem depois — só se você escolher usar IA local"

# ---------------------------------------------------------------- código

passo "Instalando a EVE"

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || echo "")"
if [ -n "$AQUI" ] && [ -f "$AQUI/pyproject.toml" ]; then
  DESTINO="$AQUI"
  pulou "usando a cópia local em $DESTINO"
elif [ -d "$DESTINO/.git" ]; then
  git -C "$DESTINO" pull --ff-only >/dev/null 2>&1 || aviso "não consegui atualizar o código"
  ok "código atualizado em $DESTINO"
else
  mkdir -p "$(dirname "$DESTINO")"
  git clone --depth 1 "$REPO" "$DESTINO" >/dev/null 2>&1 \
    || erro "não consegui baixar de $REPO"
  ok "código baixado em $DESTINO"
fi

uv tool install --editable "$DESTINO" --reinstall -q 2>/dev/null \
  || erro "a instalação falhou — rode 'uv tool install --editable $DESTINO' para ver o erro"
ok "comando eve instalado"

if ! tem eve; then
  aviso "~/.local/bin não está no PATH"
  printf "      %sacrescente ao seu shell:%s\n" "$DIM" "$FIM"
  printf "      %sexport PATH=\"\$HOME/.local/bin:\$PATH\"%s\n" "$CIANO" "$FIM"
  export PATH="$HOME/.local/bin:$PATH"
fi

# --------------------------------------------------------------- escolhas

passo "Configurando"

# Com `curl | bash`, a entrada padrão é o próprio script: sem /dev/tty não há
# como perguntar nada. Melhor dizer isso do que travar ou fingir que perguntou.
if [ -e /dev/tty ] && [ -r /dev/tty ]; then
  eve setup </dev/tty || aviso "o setup não terminou — rode 'eve setup' quando quiser"
else
  aviso "sem terminal para perguntar — rode 'eve setup' depois"
fi

MODO="$(eve config show --json 2>/dev/null \
  | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin)["ai"]["mode"])' \
  2>/dev/null || echo "hybrid")"

# ---------------------------------------------------------------- modelos

baixar_modelo() {
  # O Ollama lista "modelo:latest" quando a tag foi omitida.
  local etiqueta="$1"
  case "$etiqueta" in *:*) ;; *) etiqueta="$1:latest" ;; esac
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$etiqueta"; then
    pulou "$1 já baixado"
  else
    printf "    %s…%s baixando %s (pode demorar)\n" "$DIM" "$FIM" "$1"
    ollama pull "$1" >/dev/null 2>&1 && ok "$1" || aviso "não consegui baixar $1"
  fi
}

preparar_ia_local() {
  instalar_brew ollama ollama

  if ! pgrep -x ollama >/dev/null 2>&1; then
    printf "    %s…%s subindo o Ollama\n" "$DIM" "$FIM"
    brew services start ollama >/dev/null 2>&1 || ollama serve >/dev/null 2>&1 &
    sleep 3
  fi
  curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 \
    && ok "Ollama respondendo" \
    || aviso "Ollama não respondeu — a IA local pode não funcionar"

  baixar_modelo "$MODELO"
  baixar_modelo "$EMBEDDINGS"
}

if [ "$MODO" = "external" ]; then
  passo "IA local"
  pulou "você escolheu só OpenRouter — nada para instalar nem baixar"
else
  passo "Preparando a IA local"
  preparar_ia_local
fi

# ------------------------------------------------------------- navegador

passo "Preparando o navegador"

if [ -d "$HOME/Library/Caches/ms-playwright" ] \
   && ls "$HOME/Library/Caches/ms-playwright" 2>/dev/null | grep -q chromium; then
  pulou "Chromium já instalado"
else
  printf "    %s…%s baixando o Chromium\n" "$DIM" "$FIM"
  "$(dirname "$(command -v eve)")/../share/uv/tools/eve-core/bin/playwright" install chromium \
    >/dev/null 2>&1 \
    || uvx --from playwright playwright install chromium >/dev/null 2>&1 \
    || aviso "não consegui baixar o Chromium — 'eve doctor' explica como"
  ok "Chromium pronto"
fi

# --------------------------------------------------------------- serviço

passo "Deixando a EVE pronta"

mkdir -p "$HOME/EVE"
ok "pasta $HOME/EVE"

# Subir um processo em segundo plano que o usuário não vê nem sabe parar é
# uma decisão dele, não nossa — e ela já foi feita no setup. Aqui só resta o
# atalho para quem instala sem terminal.
if [ "${EVE_AUTOSTART:-}" = "1" ]; then
  eve service install >/dev/null 2>&1 \
    && ok "a EVE vai subir sozinha depois do login" \
    || aviso "não consegui instalar o serviço"
  sleep 3
elif eve service installed >/dev/null 2>&1; then
  pulou "serviço já configurado no setup"
else
  pulou "não vou deixar nada rodando — você escolhe como começar"
fi

# ------------------------------------------------------------ conferindo

passo "Conferindo"
eve doctor || true

printf "\n  %sEVE instalada.%s\n" "$BOLD$VERDE" "$FIM"

# Credenciais: só avisa o que realmente falta, e explica como se faz.
FALTANDO="$(eve key list --json 2>/dev/null \
  | /usr/bin/python3 -c 'import sys,json; print(" ".join(json.load(sys.stdin)["missing"]))' \
  2>/dev/null || echo "")"

if [ -n "$FALTANDO" ]; then
  printf "\n  %sFalta uma credencial:%s %s\n" "$AMARELO" "$FIM" "$FALTANDO"
  for chave in $FALTANDO; do
    printf "    %seve key set %s%s\n" "$CIANO" "$chave" "$FIM"
  done
  printf "    %so nome fica como está — a chave você digita depois, oculta%s\n" "$DIM" "$FIM"
  printf "    %seve key list%s     mostra o que já está no Keychain\n" "$CIANO" "$FIM"
fi

printf "\n  %sPara começar:%s\n" "$BOLD" "$FIM"
printf "    %seve run%s          roda no terminal — você vê tudo, Ctrl+C encerra\n" "$CIANO" "$FIM"
printf "    %seve start%s        roda em segundo plano (%seve stop%s encerra)\n" \
  "$CIANO" "$FIM" "$CIANO" "$FIM"
printf "\n  %sDepois:%s\n" "$BOLD" "$FIM"
printf "    %seve%s              abre a interface\n" "$CIANO" "$FIM"
printf "    %seve chat \"oi\"%s    conversa pelo terminal\n" "$CIANO" "$FIM"
printf "    %seve doctor%s       diagnóstico\n" "$CIANO" "$FIM"
if [ "${EVE_AUTOSTART:-}" != "1" ]; then
  printf "    %seve service install%s  faz subir sozinha depois do login\n" "$CIANO" "$FIM"
fi
printf "    %seve uninstall%s    remove tudo (preserva seus dados)\n\n" "$CIANO" "$FIM"
