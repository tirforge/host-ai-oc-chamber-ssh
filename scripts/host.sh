#!/usr/bin/env bash
set -e
# Host stack: LM Studio (1234) + Opencode Web (2456) + OpenChamber (3000) + cloudflared (4 subdomains) + SSH
# Non-Docker, Python/host-receiver

DOMAIN=${1:-yourdomain.com}
TUNNEL=${2:-t4host}
MODEL=${MODEL:-qwen/qwen3-coder-30b-a3b}
# also allow positional 3rd arg as model
if [ -n "$3" ]; then MODEL="$3"; fi

echo "Pulling model $MODEL (default: qwen/qwen3-coder-30b-a3b) if missing..."
lms get "$MODEL" -y || lms get "$MODEL" || echo "lms get $MODEL failed, check LM Studio catalog"
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
