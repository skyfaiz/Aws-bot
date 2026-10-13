#!/usr/bin/env bash
# Run on a fresh Ubuntu 22.04 EC2 instance, from inside the project directory
# (/home/ubuntu/tradingbot). Usage: bash deploy/setup_ec2.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

sudo apt update
sudo apt install -y python3 python3-venv python3-pip
sudo timedatectl set-timezone Asia/Kolkata || true

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

mkdir -p logs
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo ">> Edit .env and set GROWW_ACCESS_TOKEN"; }
[ -f instrument.csv ] || echo ">> WARNING: instrument.csv missing - upload it to $APP_DIR"

./venv/bin/python -m pytest -q tests

sudo cp deploy/tradingbot.service /etc/systemd/system/tradingbot.service
sudo cp deploy/tradingbot.logrotate /etc/logrotate.d/tradingbot
sudo systemctl daemon-reload
sudo systemctl enable tradingbot
echo "Setup done. After editing .env run: sudo systemctl start tradingbot"
