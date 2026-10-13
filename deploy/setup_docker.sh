#!/usr/bin/env bash
# Fresh Ubuntu 22.04/24.04 EC2 instance. Run from the project directory:  bash deploy/setup_docker.sh
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v docker >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
fi
sudo usermod -aG docker "$USER" || true

# bind-mounted paths must exist (a missing .env would be created as a directory) and be owned by uid 1000
[ -f .env ] || { cp .env.example .env; echo ">> Edit .env: set TELEGRAM_BOT_TOKEN (and GROWW_ACCESS_TOKEN)"; }
chmod 600 .env
mkdir -p logs data
sudo chown -R 1000:1000 logs data config .env
[ -f instrument.csv ] || echo ">> WARNING: instrument.csv missing"

sudo docker compose build
sudo docker compose run --rm tests
echo "Done. After editing .env:  sudo docker compose up -d   (logs: sudo docker compose logs -f)"
