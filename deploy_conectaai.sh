#!/usr/bin/env bash
# Sincroniza e reinicia SOMENTE o módulo ConectaAI (conectaai.service, porta
# 8081) na mesma instância EC2 do Leiria Eats — nunca toca no
# leiria-eats.service (Koma), que roda no mesmo processo/host mas é um
# serviço systemd inteiramente separado. Espelha deploy.sh, mudando apenas
# SERVICE_NAME e a porta de health-check.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$PROJECT_DIR/aws-key.pem}"
SERVER_HOST="${SERVER_HOST:-13.222.98.95}"
SERVER_USER="${SERVER_USER:-ec2-user}"
REMOTE_DIR="${REMOTE_DIR:-/home/ec2-user/leiria-eats}"
SERVICE_NAME="${SERVICE_NAME:-conectaai.service}"

if [[ ! -r "$SSH_KEY" ]]; then
  echo "Chave SSH não encontrada ou sem permissão de leitura: $SSH_KEY" >&2
  exit 1
fi

chmod 600 "$SSH_KEY"

RSYNC_SSH="ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no"
sync_output="$(rsync --archive --compress --itemize-changes \
  --exclude='.env' \
  --exclude='.venv/' \
  --exclude='.git/' \
  --exclude='.idea/' \
  --exclude='__pycache__/' \
  --exclude='*.py[cod]' \
  --exclude='athenna.pem' \
  --exclude='aws-key.pem' \
  --exclude='deploy.sh' \
  --exclude='deploy_conectaai.sh' \
  -e "$RSYNC_SSH" \
  "$PROJECT_DIR/" "$SERVER_USER@$SERVER_HOST:$REMOTE_DIR/")"

if [[ -n "$sync_output" ]]; then
  printf '%s\n' "$sync_output"
else
  echo "Nenhum arquivo alterado para sincronizar."
fi

requirements_changed=false
if [[ "$sync_output" == *"requirements.txt"* ]]; then
  requirements_changed=true
fi

ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  -o StrictHostKeyChecking=no \
  "$SERVER_USER@$SERVER_HOST" \
  "REMOTE_DIR='$REMOTE_DIR' SERVICE_NAME='$SERVICE_NAME' REQUIREMENTS_CHANGED='$requirements_changed' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

cd "$REMOTE_DIR"

if [[ "$REQUIREMENTS_CHANGED" == true ]]; then
  .venv/bin/pip install -r requirements.txt
fi

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active --quiet "$SERVICE_NAME"

for attempt in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8081/health >/dev/null; then
    echo "Deploy concluído: $SERVICE_NAME está ativo e respondendo em /health."
    exit 0
  fi
  sleep 2
done

echo "O serviço iniciou, mas não respondeu na porta 8081." >&2
sudo journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2
exit 1
REMOTE_SCRIPT
