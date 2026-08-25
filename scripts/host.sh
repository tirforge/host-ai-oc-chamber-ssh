#!/usr/bin/env bash
set -e
# Host stack: LM Studio (1234) + Opencode Web (2456) + OpenChamber (3000) + cloudflared (4 subdomains) + SSH
# Non-Docker, Python/host-receiver
# Export PATH before any checks (fixes lms/opencode not found)
export PATH="$HOME/.lmstudio/bin:$HOME/.opencode/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
for _p in "$HOME/.local/share/fnm/aliases/default/bin" $HOME/.nvm/versions/node/v22*/bin; do [ -d "$_p" ] && export PATH="$_p:$PATH"; done

DOMAIN=${1:-yourdomain.com}
TUNNEL=${2:-t4host}
MODEL=${MODEL:-lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M}
# also allow positional 3rd arg as model
if [ -n "$3" ]; then MODEL="$3"; fi

# SSH password from env/Kaggle (SSH_PASSWORD or SSH_PASS) - only chpasswd, only existing users
if [ -n "${SSH_PASSWORD:-}" ]; then
  echo "Configuring SSH password from SSH_PASSWORD env..."
  _ssh_user="${USER:-root}"
  for _u in "$_ssh_user" root; do
    if id -u "$_u" >/dev/null 2>&1; then
      echo "$_u:$SSH_PASSWORD" | chpasswd 2>&1 || true
      echo "SSH password set for $_u"
    fi
  done
elif [ -n "${SSH_PASS:-}" ]; then
  echo "Configuring SSH password from SSH_PASS..."
  for _u in "${USER:-root}" root; do if id -u "$_u" >/dev/null 2>&1; then echo "$_u:$SSH_PASS" | chpasswd 2>&1 || true; echo "SSH password set for $_u"; fi; done
fi
# Map old alias to valid HF repo
if [ "$MODEL" = "qwen/qwen3-coder-30b-a3b" ] || [ "$MODEL" = "qwen3-coder-30b-a3b" ]; then
  echo "Mapping alias $MODEL -> lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M"
  MODEL="lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M"
fi

echo "Pulling model $MODEL (default: lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M) if missing..."
lms daemon up || true
lms get "$MODEL" -y || lms get "$MODEL" || lms get lmstudio-community/Qwen3-Coder-30B-A3B-GGUF -y || echo "lms get $MODEL failed, check LM Studio catalog"
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
