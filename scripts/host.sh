#!/usr/bin/env bash
set -e
# Host stack: LM Studio (1234) + Opencode Web (4096) + OpenChamber (3000) + cloudflared (4 subdomains) + SSH
# Non-Docker, Python/host-receiver

DOMAIN=${1:-yourdomain.com}
TUNNEL=${2:-t4host}

echo "Starting LM Studio..."
lms server start --port 1234 --cors &
echo "Starting Opencode Web..."
opencode web --port 4096 --hostname 0.0.0.0 &
echo "Starting OpenChamber..."
openchamber --ui-password "${OPENCHAMBER_UI_PASSWORD:-changeme}" &
sleep 2
echo "Starting Cloudflare tunnel $TUNNEL for ai.$DOMAIN oc.$DOMAIN chamber.$DOMAIN ssh.$DOMAIN"
cloudflared tunnel run $TUNNEL

# Receiver side (laptop) SSH:
# ssh -o ProxyCommand="cloudflared access ssh --hostname ssh.$DOMAIN" user@ssh.$DOMAIN
# Receiver opencode.json baseURL: https://ai.$DOMAIN/v1
