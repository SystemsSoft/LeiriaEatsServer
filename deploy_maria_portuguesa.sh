#!/usr/bin/env bash
# Atualiza na nuvem APENAS o módulo Maria Portuguesa.
#
# Envia para a instância EC2 só os ficheiros do módulo (modules/maria_portuguesa e
# os scripts/testes/migração dele) — o resto do Leiria Eats fica como está no
# servidor, mesmo que haja alterações locais por enviar.
#
# O módulo corre dentro do mesmo processo do Leiria Eats, por isso o serviço é
# reiniciado no fim (alguns segundos de indisponibilidade para ambos). Antes de
# reiniciar o código é validado; se não compilar, ou se o serviço não voltar a
# responder, a versão anterior do módulo é reposta automaticamente.
#
# Uso:
#   ./deploy_maria_portuguesa.sh               envia o módulo e reinicia o serviço
#   ./deploy_maria_portuguesa.sh --dry-run     só mostra o que seria enviado
#   ./deploy_maria_portuguesa.sh --com-main    envia também o main.py (ver abaixo)
#
# Primeira vez: o main.py do servidor tem de registar o módulo (include_router).
# Se ainda não o fizer, o script pára e avisa. Nesse caso corra o ./deploy.sh
# completo uma vez, ou use --com-main se o main.py local só diferir nisso.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$PROJECT_DIR/aws-key.pem}"
SERVER_HOST="${SERVER_HOST:-13.222.98.95}"
SERVER_USER="${SERVER_USER:-ec2-user}"
REMOTE_DIR="${REMOTE_DIR:-/home/ec2-user/leiria-eats}"
SERVICE_NAME="${SERVICE_NAME:-leiria-eats.service}"
SERVER_PORT="${SERVER_PORT:-8080}"

# Tudo o que pertence ao módulo, relativo à raiz do projeto.
ARQUIVOS_DO_MODULO=(
  modules/__init__.py
  modules/maria_portuguesa/
  scripts/maria_portuguesa_dev.py
  scripts/maria_portuguesa_criar_banco.py
  tests/test_maria_portuguesa.py
  migration_maria_portuguesa.sql
)

DRY_RUN=false
COM_MAIN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=true ;;
    --com-main) COM_MAIN=true ;;
    -h|--help)  sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $arg (use --help)" >&2; exit 2 ;;
  esac
done

if [[ ! -r "$SSH_KEY" ]]; then
  echo "Chave SSH não encontrada ou sem permissão de leitura: $SSH_KEY" >&2
  exit 1
fi
chmod 600 "$SSH_KEY"

cd "$PROJECT_DIR"
for caminho in "${ARQUIVOS_DO_MODULO[@]}"; do
  if [[ ! -e "$caminho" ]]; then
    echo "Ficheiro do módulo em falta: $caminho" >&2
    exit 1
  fi
done
if [[ "$COM_MAIN" == true ]]; then
  ARQUIVOS_DO_MODULO+=(main.py)
fi

SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no)
RSYNC_SSH="ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no"
DESTINO="$SERVER_USER@$SERVER_HOST"
# Identifica a cópia de segurança desta execução no servidor.
CARIMBO="$(date +%Y%m%d-%H%M%S)"

sincronizar() {
  # --relative preserva os caminhos (modules/..., scripts/...) a partir da raiz;
  # --delete só atua dentro das pastas enviadas, ou seja, em modules/maria_portuguesa/.
  rsync --archive --compress --itemize-changes --relative --delete "$@" \
    --exclude='__pycache__/' \
    --exclude='*.py[cod]' \
    -e "$RSYNC_SSH" \
    "${ARQUIVOS_DO_MODULO[@]}" "$DESTINO:$REMOTE_DIR/"
}

if [[ "$DRY_RUN" == true ]]; then
  echo "Simulação — nada é alterado no servidor. Seria enviado:"
  saida="$(sincronizar --dry-run)"
  if [[ -n "$saida" ]]; then printf '%s\n' "$saida"; else echo "(nada: o servidor já está igual)"; fi
  exit 0
fi

# ── 1. No servidor: confirmar que o main.py conhece o módulo e guardar a versão atual ──
ssh "${SSH_OPTS[@]}" "$DESTINO" \
  "REMOTE_DIR='$REMOTE_DIR' CARIMBO='$CARIMBO' COM_MAIN='$COM_MAIN' bash -s" <<'REMOTE_PRE'
set -euo pipefail
cd "$REMOTE_DIR"

if [[ "$COM_MAIN" != true ]] && ! grep -q "maria_portuguesa" main.py; then
  echo "O main.py do servidor ainda não regista o módulo Maria Portuguesa." >&2
  echo "Corra o ./deploy.sh completo uma vez, ou repita com --com-main." >&2
  exit 3
fi

COPIA="$HOME/backups/maria_portuguesa/$CARIMBO"
mkdir -p "$COPIA"
if [[ -d modules/maria_portuguesa ]]; then
  cp -a modules/maria_portuguesa "$COPIA/maria_portuguesa"
fi
if [[ "$COM_MAIN" == true ]]; then
  cp -a main.py "$COPIA/main.py"
fi
# Ficam só as 5 cópias mais recentes.
ls -1dt "$HOME"/backups/maria_portuguesa/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
echo "Cópia de segurança da versão atual: $COPIA"
REMOTE_PRE

# ── 2. Enviar só os ficheiros do módulo ──
sync_output="$(sincronizar)"
if [[ -n "$sync_output" ]]; then
  printf '%s\n' "$sync_output"
else
  echo "Nenhum ficheiro do módulo alterado — nada a atualizar."
  exit 0
fi

# ── 3. No servidor: validar, reiniciar, verificar; repor a versão anterior se falhar ──
ssh "${SSH_OPTS[@]}" "$DESTINO" \
  "REMOTE_DIR='$REMOTE_DIR' SERVICE_NAME='$SERVICE_NAME' SERVER_PORT='$SERVER_PORT' CARIMBO='$CARIMBO' COM_MAIN='$COM_MAIN' bash -s" <<'REMOTE_POS'
set -euo pipefail
cd "$REMOTE_DIR"
COPIA="$HOME/backups/maria_portuguesa/$CARIMBO"

repor_versao_anterior() {
  echo "A repor a versão anterior do módulo..." >&2
  rm -rf modules/maria_portuguesa
  if [[ -d "$COPIA/maria_portuguesa" ]]; then
    cp -a "$COPIA/maria_portuguesa" modules/maria_portuguesa
  fi
  if [[ -f "$COPIA/main.py" ]]; then
    cp -a "$COPIA/main.py" main.py
  fi
}

responde() {
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$SERVER_PORT$1" >/dev/null 2>&1
}

aguardar() {  # aguardar <rota> <tentativas>
  for _ in $(seq 1 "$2"); do
    if responde "$1"; then return 0; fi
    sleep 2
  done
  return 1
}

# Erros de sintaxe apanham-se aqui, antes de tocar no serviço em produção.
if ! .venv/bin/python -m compileall -q modules/maria_portuguesa main.py >/dev/null; then
  echo "O código enviado não compila. O serviço NÃO foi reiniciado." >&2
  .venv/bin/python -m compileall -q modules/maria_portuguesa main.py >&2 || true
  repor_versao_anterior
  exit 4
fi

# Um reinício que falhe conta como falha do deploy (e não aborta o script a meio).
if sudo systemctl restart "$SERVICE_NAME" && aguardar /maria-portuguesa/catalogo 60; then
  echo "Deploy concluído: o módulo Maria Portuguesa responde em /maria-portuguesa/catalogo."
  sudo journalctl -u "$SERVICE_NAME" --since "3 minutes ago" --no-pager 2>/dev/null | grep -a "MariaPortuguesa" | tail -n 5 || true
  exit 0
fi

echo "O módulo não respondeu depois de reiniciar. Últimas linhas do serviço:" >&2
sudo journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
repor_versao_anterior
if sudo systemctl restart "$SERVICE_NAME" && aguardar /openapi.json 60; then
  echo "Versão anterior reposta e o serviço voltou a responder." >&2
else
  echo "ATENÇÃO: o serviço continua sem responder depois de repor a versão anterior." >&2
fi
exit 5
REMOTE_POS
