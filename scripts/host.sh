#!/usr/bin/env bash
set -e
# Host stack: LM Studio (1234) + Opencode Web (2456) + OpenChamber (3000) + cloudflared (4 subdomains) + SSH
# Non-Docker, Python/host-receiver
# Export PATH before any checks (fixes lms/opencode not found)
export PATH="$HOME/.lmstudio/bin:$HOME/.opencode/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
for _p in "$HOME/.local/share/fnm/aliases/default/bin" $HOME/.nvm/versions/node/v22*/bin; do [ -d "$_p" ] && export PATH="$_p:$PATH"; done

DOMAIN=${1:-yourdomain.com}
TUNNEL=${2:-t4host}
MODEL=${MODEL:-lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF}
# also allow positional 3rd arg as model
if [ -n "$3" ]; then MODEL="$3"; fi

# SSH password: SSH_PASSWORD -> SSH_PASS -> fallback to OpenChamber UI password
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
elif [ -n "${OPENCHAMBER_UI_PASSWORD:-}" ] && [ "${OPENCHAMBER_UI_PASSWORD}" != "changeme" ]; then
  echo "Using OPENCHAMBER_UI_PASSWORD for SSH..."
  for _u in "${USER:-root}" root; do if id -u "$_u" >/dev/null 2>&1; then echo "$_u:$OPENCHAMBER_UI_PASSWORD" | chpasswd 2>&1 || true; echo "SSH password set for $_u (from OPENCHAMBER_UI_PASSWORD)"; fi; done
fi
# Map old alias to valid HF repo
if [ "$MODEL" = "qwen/qwen3-coder-30b-a3b" ] || [ "$MODEL" = "qwen3-coder-30b-a3b" ]; then
  echo "Mapping alias $MODEL -> lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF"
  MODEL="lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF"
fi

# HuggingFace Space support: pull GGUF directly with --repo-type space
HF_REPO_TYPE=""
if echo "$MODEL" | grep -qi "/spaces/"; then
  MODEL="$(echo "$MODEL" | sed -E 's#.*/spaces/##')"
  HF_REPO_TYPE="--repo-type space"
elif echo "$MODEL" | grep -qi "^spaces/"; then
  MODEL="$(echo "$MODEL" | sed -E 's#^spaces/##')"
  HF_REPO_TYPE="--repo-type space"
fi

echo "Pulling model $MODEL (default: lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF) if missing..."
lms daemon up || true
base="${MODEL%%:*}"  # llmster rejects ':QUANT'
if ! lms get "$base" -y && ! lms get "$base"; then
  echo "lms get failed -> direct HF download into ~/.lmstudio/models ..."
  _dest="$HOME/.lmstudio/models/$base"; mkdir -p "$_dest"
  pip install -q -U "huggingface_hub[cli]" >/dev/null 2>&1 || true
  (hf download "$base" $HF_REPO_TYPE --include "*Q4_K_M*" --local-dir "$_dest" </dev/null || huggingface-cli download "$base" $HF_REPO_TYPE --include "*Q4_K_M*" --local-dir "$_dest" </dev/null) || echo "HF direct failed"
fi
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
