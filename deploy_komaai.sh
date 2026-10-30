#!/usr/bin/env bash
# Atualiza na nuvem APENAS o Koma AI (backend Leiria Eats + static/, incluindo a
# Política de Privacidade em /privacy/).
#
# Não envia nem altera os módulos ConectaAI (conectaai/) e Maria Portuguesa
# (modules/maria_portuguesa/ e os scripts/testes/migração dele): o que estiver
# no servidor para esses módulos fica exatamente como está. Também nunca
# reinicia o conectaai.service.
#
# Atenção: o Maria Portuguesa corre dentro do mesmo processo do Koma
# (leiria-eats.service), por isso o reinício no fim deixa ambos alguns
# segundos indisponíveis. Antes de reiniciar, o código do Koma é validado no
# servidor; se não compilar, o serviço NÃO é reiniciado e continua a correr a
# versão anterior que tem em memória.
#
# Uso:
#   ./deploy_komaai.sh             envia o Koma AI e reinicia o leiria-eats.service
#   ./deploy_komaai.sh --dry-run   só mostra o que seria enviado
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$PROJECT_DIR/aws-key.pem}"
# IP público atual da instância (api.leiriaeats.com resolve para ele).
SERVER_HOST="${SERVER_HOST:-18.206.45.50}"
SERVER_USER="${SERVER_USER:-ec2-user}"
REMOTE_DIR="${REMOTE_DIR:-/home/ec2-user/leiria-eats}"
SERVICE_NAME="${SERVICE_NAME:-leiria-eats.service}"
SERVER_PORT="${SERVER_PORT:-8080}"

# Pastas com código Python do Koma, validadas antes do reinício.
CODIGO_KOMA=(main.py api core repositories schemas services)

DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $arg (use --help)" >&2; exit 2 ;;
  esac
done

if [[ ! -r "$SSH_KEY" ]]; then
  echo "Chave SSH não encontrada ou sem permissão de leitura: $SSH_KEY" >&2
  exit 1
fi
chmod 600 "$SSH_KEY"

SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no)
RSYNC_SSH="ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no"
DESTINO="$SERVER_USER@$SERVER_HOST"

sincronizar() {
  # Os padrões começados por "/" são relativos à raiz do projeto. Sem --delete:
  # ficheiros excluídos (ou só existentes no servidor) nunca são apagados.
  rsync --archive --compress --itemize-changes "$@" \
    --exclude='.env' \
    --exclude='.venv/' \
    --exclude='.git/' \
    --exclude='.idea/' \
    --exclude='.artifacts/' \
    --exclude='__pycache__/' \
    --exclude='*.py[cod]' \
    --exclude='*.pem' \
    --exclude='.DS_Store' \
    --exclude='/deploy*.sh' \
    --exclude='/conectaai/' \
    --exclude='/tests/test_conectaai_*' \
    --exclude='/modules/maria_portuguesa/' \
    --exclude='/scripts/maria_portuguesa_*' \
    --exclude='/tests/test_maria_portuguesa.py' \
    --exclude='/migration_maria_portuguesa.sql' \
    -e "$RSYNC_SSH" \
    "$PROJECT_DIR/" "$DESTINO:$REMOTE_DIR/"
}

if [[ "$DRY_RUN" == true ]]; then
  echo "Simulação — nada é alterado no servidor. Seria enviado:"
  saida="$(sincronizar --dry-run)"
  if [[ -n "$saida" ]]; then printf '%s\n' "$saida"; else echo "(nada: o servidor já está igual)"; fi
  exit 0
fi

sync_output="$(sincronizar)"
if [[ -n "$sync_output" ]]; then
  printf '%s\n' "$sync_output"
else
  echo "Nenhum ficheiro do Koma AI alterado — nada a atualizar."
  exit 0
fi

requirements_changed=false
if [[ "$sync_output" == *"requirements.txt"* ]]; then
  requirements_changed=true
fi

ssh "${SSH_OPTS[@]}" "$DESTINO" \
  "REMOTE_DIR='$REMOTE_DIR' SERVICE_NAME='$SERVICE_NAME' SERVER_PORT='$SERVER_PORT' REQUIREMENTS_CHANGED='$requirements_changed' CODIGO_KOMA='${CODIGO_KOMA[*]}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$REMOTE_DIR"

if [[ "$REQUIREMENTS_CHANGED" == true ]]; then
  .venv/bin/pip install -r requirements.txt
fi

# Erros de sintaxe apanham-se aqui, antes de tocar no serviço em produção.
# shellcheck disable=SC2086
if ! .venv/bin/python -m compileall -q $CODIGO_KOMA >/dev/null; then
  echo "O código do Koma AI não compila. O serviço NÃO foi reiniciado." >&2
  .venv/bin/python -m compileall -q $CODIGO_KOMA >&2 || true
  exit 4
fi

responde() {
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$SERVER_PORT$1" >/dev/null 2>&1
}

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active --quiet "$SERVICE_NAME"

for _ in {1..30}; do
  if responde /openapi.json; then
    if responde /privacy/; then
      echo "Deploy concluído: $SERVICE_NAME responde em /openapi.json e a Política de Privacidade em /privacy/."
    else
      echo "Deploy concluído: $SERVICE_NAME responde em /openapi.json, mas /privacy/ não respondeu." >&2
    fi
    exit 0
  fi
  sleep 2
done

echo "O serviço iniciou, mas não respondeu na porta $SERVER_PORT." >&2
sudo journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2
exit 1
REMOTE_SCRIPT
