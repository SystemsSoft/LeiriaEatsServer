#!/usr/bin/env bash
# Atualiza o serviço de embeddings E5 (e5_service/) na instância dedicada.
#
# Envia e5_service/, constrói a imagem Docker na própria instância (x86) e troca o
# container. O serviço só escuta no IP privado da Lightsail: a API chega-lhe pela
# rede interna e a internet não. O token fica em ~/e5.env na instância (nunca no
# repositório) e tem de ser igual ao E5_SERVICE_TOKEN do .env da API.
#
# A imagem anterior fica com a etiqueta koma-e5:anterior; para voltar atrás:
#   ./deploy_e5.sh --rollback
#
# Uso:
#   ./deploy_e5.sh               constrói e troca o container
#   ./deploy_e5.sh --rollback    volta a correr a imagem anterior
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$PROJECT_DIR/instancia-e5.pem}"
SERVER_HOST="${SERVER_HOST:-15.217.243.159}"
SERVER_USER="${SERVER_USER:-ec2-user}"
PRIVATE_IP="${PRIVATE_IP:-172.26.38.59}"
PORT="${PORT:-8090}"

ROLLBACK=false
for arg in "$@"; do
  case "$arg" in
    --rollback) ROLLBACK=true ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $arg (use --help)" >&2; exit 2 ;;
  esac
done

if [[ ! -r "$SSH_KEY" ]]; then
  echo "Chave SSH não encontrada ou sem permissão de leitura: $SSH_KEY" >&2
  exit 1
fi
chmod 600 "$SSH_KEY"
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no -o ServerAliveInterval=30)
DESTINO="$SERVER_USER@$SERVER_HOST"

if [[ "$ROLLBACK" != true ]]; then
  rsync --archive --compress --delete -e "ssh ${SSH_OPTS[*]}" \
    "$PROJECT_DIR/e5_service/" "$DESTINO:/home/ec2-user/e5_service/"
fi

ssh "${SSH_OPTS[@]}" "$DESTINO" \
  "ROLLBACK='$ROLLBACK' PRIVATE_IP='$PRIVATE_IP' PORT='$PORT' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd /home/ec2-user/e5_service

if [[ ! -f ~/e5.env ]]; then
  echo "Falta ~/e5.env com E5_SERVICE_TOKEN=... na instância." >&2
  exit 3
fi

if [[ "$ROLLBACK" == true ]]; then
  sudo docker image inspect koma-e5:anterior >/dev/null 2>&1 || { echo "Não há imagem anterior." >&2; exit 4; }
  sudo docker tag koma-e5:anterior koma-e5:atual
else
  if sudo docker image inspect koma-e5:atual >/dev/null 2>&1; then
    sudo docker tag koma-e5:atual koma-e5:anterior
  fi
  # A 1ª construção descarrega e quantiza o E5 (vários minutos); as seguintes reaproveitam as camadas.
  sudo docker build -q -t koma-e5:atual . >/dev/null
fi

sudo docker rm -f koma-e5 >/dev/null 2>&1 || true
sudo docker run -d --name koma-e5 --restart=always --memory=1700m \
  --env-file ~/e5.env -p "$PRIVATE_IP:$PORT:8090" koma-e5:atual >/dev/null

for _ in {1..60}; do
  if curl --fail --silent --max-time 5 "http://$PRIVATE_IP:$PORT/health" >/dev/null; then
    echo "Serviço E5 ativo em http://$PRIVATE_IP:$PORT ($(sudo docker image inspect koma-e5:atual --format '{{.Id}}' | cut -c8-19))."
    sudo docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

echo "O container arrancou mas não respondeu em /health." >&2
sudo docker logs --tail 60 koma-e5 >&2
exit 1
REMOTE_SCRIPT
