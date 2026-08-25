#!/usr/bin/env bash
set -e
# Host stack: LM Studio (1234) + Opencode Web (2456) + OpenChamber (3000) + cloudflared (4 subdomains) + SSH
# Non-Docker, Python/host-receiver

DOMAIN=${1:-yourdomain.com}
TUNNEL=${2:-t4host}

echo "Pulling default model qwen3-coder-30b-a3b (Qwen3 Coder 30B A3B) if missing..."
lms get qwen/qwen3-coder-30b-a3b -y || echo "lms get failed, check LM Studio catalog"
echo "Starting LM Studio..."
lms server start --port 1234 --cors &
echo "Starting Opencode Web (full tool support, port 2456)..."
opencode web --port 2456 --hostname 0.0.0.0 &
echo "Starting OpenChamber..."
openchamber --ui-password "${OPENCHAMBER_UI_PASSWORD:-changeme}" &
sleep 2
echo "Starting Cloudflare tunnel $TUNNEL for ai.$DOMAIN oc.$DOMAIN chamber.$DOMAIN ssh.$DOMAIN"
cloudflared tunnel run $TUNNEL

# Receiver side (laptop) SSH:
# ssh -o ProxyCommand="cloudflared access ssh --hostname ssh.$DOMAIN" user@ssh.$DOMAIN
# Receiver opencode.json baseURL: https://ai.$DOMAIN/v1
