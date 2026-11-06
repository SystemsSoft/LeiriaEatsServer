#!/usr/bin/env bash
# Atualiza o serviço de embeddings E5 (e5_service/) na instância dedicada.
#
# Sem Docker (poupa RAM numa instância de 2 GB): envia e5_service/, instala as
# dependências num ambiente virtual (só quando requirements.txt muda) e reinicia o
# serviço systemd e5.service. O serviço só escuta no IP privado da Lightsail: a API
# chega-lhe pela rede interna e a internet não. O token fica em ~/e5.env na instância
# (nunca no repositório) e tem de ser igual ao E5_SERVICE_TOKEN do .env da API.
#
# O modelo quantizado (~1,3 GB) fica em ~/e5_service/modelo/e5_int8.pt; se faltar, é
# gerado com quantizar.py (descarrega o E5 e demora vários minutos).
#
# Uso:
#   ./deploy_e5.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$PROJECT_DIR/instancia-e5.pem}"
SERVER_HOST="${SERVER_HOST:-15.217.243.159}"
SERVER_USER="${SERVER_USER:-ec2-user}"
PRIVATE_IP="${PRIVATE_IP:-172.26.38.59}"
PORT="${PORT:-8090}"

for arg in "$@"; do
  case "$arg" in
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# --delete não toca no que está excluído: o modelo e o ambiente virtual ficam no servidor.
sync_output="$(rsync --archive --compress --delete --itemize-changes \
  --exclude='/modelo/' --exclude='/.venv/' --exclude='__pycache__/' \
  -e "ssh ${SSH_OPTS[*]}" "$PROJECT_DIR/e5_service/" "$DESTINO:/home/ec2-user/e5_service/")"
[[ -n "$sync_output" ]] && printf '%s\n' "$sync_output"
requirements_changed=false
[[ "$sync_output" == *"requirements.txt"* ]] && requirements_changed=true

ssh "${SSH_OPTS[@]}" "$DESTINO" \
  "PRIVATE_IP='$PRIVATE_IP' PORT='$PORT' REQUIREMENTS_CHANGED='$requirements_changed' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd /home/ec2-user/e5_service

if [[ ! -f ~/e5.env ]]; then
  echo "Falta ~/e5.env com E5_SERVICE_TOKEN=... na instância." >&2
  exit 3
fi

if [[ ! -x .venv/bin/python ]]; then
  command -v python3.11 >/dev/null || sudo dnf install -y -q python3.11 python3.11-pip >/dev/null
  python3.11 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  REQUIREMENTS_CHANGED=true
fi
if [[ "$REQUIREMENTS_CHANGED" == true ]]; then
  .venv/bin/pip install -q torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/pip install -q -r requirements.txt
fi

if [[ ! -f modelo/e5_int8.pt ]]; then
  mkdir -p modelo
  .venv/bin/python quantizar.py modelo/e5_int8.pt
fi

sudo tee /etc/systemd/system/e5.service >/dev/null <<UNIT
[Unit]
Description=Koma - serviço de embeddings E5 (int8)
After=network-online.target
Wants=network-online.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/e5_service
EnvironmentFile=/home/ec2-user/e5.env
Environment=E5_MODELO=/home/ec2-user/e5_service/modelo/e5_int8.pt
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ec2-user/e5_service/.venv/bin/uvicorn app:app --host $PRIVATE_IP --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable e5.service >/dev/null 2>&1
sudo systemctl restart e5.service

for _ in {1..90}; do
  if curl --fail --silent --max-time 5 "http://$PRIVATE_IP:$PORT/health" >/dev/null; then
    echo "Serviço E5 ativo em http://$PRIVATE_IP:$PORT."
    exit 0
  fi
  sleep 2
done

echo "O serviço arrancou mas não respondeu em /health." >&2
sudo journalctl -u e5.service -n 60 --no-pager >&2
exit 1
REMOTE_SCRIPT
