# Host Stack - LM Studio + Opencode + OpenChamber + SSH via Cloudflare (non-Docker)

Single `cloudflared` named tunnel with 4 subdomains, Python host-receiver, your dual T4 2x16GB. Fork of https://github.com/openchamber/openchamber extended for self-hosting.

## ✅ Verified Wiring (cross-checked)

| Subdomain | Service | Port | Source | Status |
|---|---|---|---|---|
| `ai.yourdomain.com` | LM Studio (`/v1`) | 1234 | `cloudflared/config.yml` + `lms server start --port 1234` + `startup.py` + `opencode.json` | ✅ |
| `oc.yourdomain.com` | Opencode Web (full tools) | 2456 | `cloudflared/config.yml` + `opencode web --port 2456` + `host.sh`/`startup.py` | ✅ |
| `chamber.yourdomain.com` | OpenChamber WebUI | 3000 | `cloudflared/config.yml` + `openchamber` | ✅ |
| `ssh.yourdomain.com` | SSH | 22 | `cloudflared/config.yml` `ssh://localhost:22` + `startup.py` installs/starts `sshd` | ✅ |

* All 4 ingress in one `t4host` tunnel + catch-all `http_status:404`.
* `opencode.json`: both `lmstudio-local` and `lmstudio-tunneled` expose `qwen3-coder-30b-a3b-instruct` with `tool_call:true`, `reasoning:true`, `limit:{context:49152, output:32768}`, plugin `opencode-lmstudio@1.0.0-rc.2`.
* The config is copied to `~/.config/opencode/opencode.jsonc` at startup, so **both** the web instance (`:2456`) and OpenChamber's managed OpenCode (`:34637`, `cwd=/root`) pick it up.
* **LSP is enabled** (see below).
* Default model `lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF` is auto-pulled if `MODEL` secret is missing; otherwise your `MODEL` (Kaggle + env, strip-empty, `MODEL_NAME` fallback, patches `opencode.json`). `MODEL` may also be an **HF Space URL** (see below).
* **Context:** 48K (`49152`) / out `32768`. 64K OOMs on dual T4 (`unable to allocate CUDA0 buffer`); 48K is the safe ceiling.
* Dual T4: `CUDA_VISIBLE_DEVICES=0,1`, `Q4_K_M ~18GB`, MTP draft, auto-split.
* Enhanced: `harnesses/smallcode` submodule, `scripts/run-smallcode.sh` fallback.
* `python -m py_compile` + `bash -n` + `pytest` pass.

## LSP (Language Server Protocol)

OpenCode LSP is **on** (`"lsp": { ... }`). Built-in servers auto-start per file extension. For Python we point at the installed `pylsp` and disable the `pyright` built-in (the `pyright` binary is **not** installed on the box — only `pylsp` + `ruff` are):

```json
"lsp": {
  "pyright": { "disabled": true },
  "python": { "command": ["pylsp"], "extensions": [".py"] }
}
```

Other built-ins (bash, go, yaml, typescript, …) remain available. To use `pyright` instead, `pip install pyright` and drop the `"pyright": { "disabled": true }` block. LSP is optional — set `"lsp": false` to disable all servers. Verified: a Space-pulled GGUF (`ML610/Mistral-7b-instruct-GGUF`) loaded and served with `pylsp` active for `.py` files.

## Subdomains (one tunnel `t4host`)

* `ai.yourdomain.com` -> `http://localhost:1234` (+ `/v1` for OpenAI-compatible)
* `oc.yourdomain.com` -> `http://localhost:2456` (Opencode Web)
* `chamber.yourdomain.com` -> `http://localhost:3000` (OpenChamber)
* `ssh.yourdomain.com` -> `ssh://localhost:22`

## Download Links

* **This repo (4-subdomain host, clone with tools):** `git clone --recurse-submodules https://github.com/tirforge/host-ai-oc-chamber-ssh` or ZIP https://github.com/tirforge/host-ai-oc-chamber-ssh/archive/refs/heads/main.zip
* LM Studio: https://lmstudio.ai/download or `curl -fsSL https://lmstudio.ai/install.sh | bash`
* Opencode: `curl -fsSL https://opencode.ai/install | bash` https://github.com/anomalyco/opencode
* OpenChamber: `curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash` https://github.com/openchamber/openchamber
* cloudflared: https://github.com/cloudflare/cloudflared/releases
* Model (default A3B): `lms get lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF` or HF https://huggingface.co/lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF and https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
* A working **HF Space** example (hosts its own GGUF): `https://huggingface.co/spaces/ML610/Mistral-7b-instruct-GGUF`
* Enhanced harness: vendored `harnesses/smallcode` https://github.com/mebassett/smallcode

## Quick Start (Host = dual T4, non-Docker)

```bash
# 1. Binaries
curl -fsSL https://lmstudio.ai/install.sh | bash
curl -fsSL https://opencode.ai/install | bash
curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash
# 2. Cloudflare named tunnel (needs domain on Cloudflare)
cloudflared tunnel login
cloudflared tunnel create t4host
cloudflared tunnel route dns t4host ai.yourdomain.com
cloudflared tunnel route dns t4host oc.yourdomain.com
cloudflared tunnel route dns t4host chamber.yourdomain.com
cloudflared tunnel route dns t4host ssh.yourdomain.com
cp cloudflared/config.yml ~/.cloudflared/config.yml  # edit <TUNNEL_ID> + yourdomain.com
cloudflared tunnel run t4host
# 3. Run (auto-pulls lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF if MODEL not set)
MODEL=lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF ./scripts/host.sh yourdomain.com
# or with an HF Space that hosts a GGUF:
# MODEL=https://huggingface.co/spaces/ML610/Mistral-7b-instruct-GGUF ./scripts/host.sh yourdomain.com t4host
```

## Kaggle Start (with secrets) - fully supported

Kaggle notebook must have **Internet ON** (Settings -> Internet) and **GPU T4 x2** (Settings -> Accelerator -> GPU T4 x2). All 4 services auto-install if missing.

Add Secrets in Kaggle Notebook: `Add-ons -> Secrets` and toggle on for this notebook:
* `CF_TOKEN` (or `CLOUDFLARE_API_TOKEN`) - CF API token with Zone/DNS/Tunnel Edit **or** `TUNNEL_TOKEN` (Zero Trust -> Networks -> Tunnels -> Create tunnel -> Copy token)
* `CF_DOMAIN` - `yourdomain.com` (your Cloudflare domain)
* `OPENCHAMBER_UI_PASSWORD` (or `PASSWORD`) - WebUI password for `oc` + `chamber`
* `SSH_PASSWORD` (or `SSH_PASS`) - **SSH password** for `ssh.yourdomain.com` (sets Linux `root` + current user; `startup.py` then installs & starts `sshd` with `PermitRootLogin yes` / `PasswordAuthentication yes`)
* `MODEL` (optional). Default `lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF`. May be:
  * a HF model repo id (`mistralai/devstral-small-2507`),
  * a full HF URL (`https://huggingface.co/owner/repo`),
  * **or an HF Space URL** (`https://huggingface.co/spaces/owner/name`) — pulled via `hf download --repo-type space`. The Space must host the `.gguf` in its repo.
* `MODEL_QUANT` (optional, default `Q4_K_M`) - quant to pull; if that quant isn't found, all GGUFs in the repo/Space are downloaded.
* `TUNNEL_TOKEN` (optional alternative to `CF_TOKEN` for headless Kaggle)

Cell (Kaggle, with Internet + GPU):
```python
!rm -rf host-ai-oc-chamber-ssh; git clone --recurse-submodules https://github.com/tirforge/host-ai-oc-chamber-ssh
!python host-ai-oc-chamber-ssh/scripts/startup.py
# MODEL not set -> pulls lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF (55-70 tok/s on dual T4)
# MODEL=https://huggingface.co/spaces/ML610/Mistral-7b-instruct-GGUF -> pulls from the Space
```

`scripts/startup.py` handles both `os.environ` and `kaggle_secrets.UserSecretsClient`, strip-empty, `MODEL`/`MODEL_NAME` fallback, `TUNNEL_TOKEN` vs `CF_TOKEN` branching, `~/.cloudflared` via `expanduser`, auto-installs `lms`/`cloudflared`/`opencode`/`openchamber` (Node 22 via fnm/nvm if needed), dual T4 auto-detect `CUDA_VISIBLE_DEVICES=0,1`, **installs & starts `sshd`**, copies `opencode.json` to `~/.config/opencode`, and patches the served model id to whatever LM Studio actually serves.

## Python Command to Run

After `pip install -e .` (uses `pyproject.toml`):
```bash
pip install -e .
host-stack            # same as host-ai-oc, runs scripts.startup:main
host-ai-oc            # alias
python -m scripts.startup
python scripts/startup.py
# with custom model/domain
MODEL=qwen/Qwen3-Coder-30B-A3B-Instruct CF_DOMAIN=yourdomain.com host-stack
```

`scripts/startup.py` has `def main():` + `if __name__ == "__main__": main()` so it works as module, script, and installed command.

## How to Connect SSH (3 ways)

`startup.py` installs and starts `sshd` (with `PermitRootLogin yes` / `PasswordAuthentication yes`) and runs `cloudflared tunnel run t4host` exposing `ssh.yourdomain.com -> ssh://localhost:22`. Use the password set via `SSH_PASSWORD`.

**1. Quick (no config):**
```bash
ssh -o ProxyCommand="cloudflared access ssh --hostname ssh.yourdomain.com" root@ssh.yourdomain.com
```

**2. Persistent `~/.ssh/config` (recommended):**
```
Host ssh.yourdomain.com
  ProxyCommand cloudflared access ssh --hostname %h
  User root
# then:
ssh ssh.yourdomain.com
# scp, rsync, VS Code Remote SSH all work via same ProxyCommand
```

**3. WARP (mobile/Termius where ProxyCommand not possible):**
Install Cloudflare WARP app, enroll, then `ssh root@ssh.yourdomain.com` directly (WARP routes via tunnel).

Verify SSH: `ssh ssh.yourdomain.com "nvidia-smi"` should show `2x T4`.

## Connection Summary

At the end of a successful run, `startup.py` prints a summary, e.g.:
```
  HOST STACK READY
  Model (HF)     : lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF
  Quant          : Q4_K_M  (override via MODEL_QUANT)
  Model (served) : qwen3-coder-30b-a3b-instruct
  Context / Out  : 48K / 32K tokens  (64K OOMs on dual T4)
  CONNECT
   OpenCode Web  : https://oc.yourdomain.com   (password: <ui_pw>)
   OpenChamber   : https://chamber.yourdomain.com  (password: <ui_pw>)
   AI endpoint   : https://ai.yourdomain.com/v1
   SSH           : ssh root@ssh.yourdomain.com   (password: <ssh_pw>)
   SSH (1-liner) : ssh-keygen -R ssh.yourdomain.com && ssh -o StrictHostKeyChecking=no root@ssh.yourdomain.com
   Local         : opencode :2456 | openchamber :3000 | lmstudio :1234
```

## API token note (tunneled provider)

`opencode.json`'s `lmstudio-tunneled` provider has `"apiKey": "{env:LM_API_TOKEN}"`. This is **optional** — LM Studio does not enforce an API key, so the token is cosmetic (any value, or none, works). If you want real authentication on the public `ai.yourdomain.com/v1` endpoint, put **Cloudflare Access** in front of that ingress hostname (the tunnel already routes through Cloudflare). The OpenCode web (`oc.*`) and OpenChamber (`chamber.*`) endpoints are genuinely password-protected via `OPENCODE_SERVER_PASSWORD` / `OPENCHAMBER_UI_PASSWORD`.

## Workflow Verified

`Secrets (CF_TOKEN/CF_DOMAIN/PASSWORD/MODEL) -> startup.py/host.sh pulls $MODEL (or default A3B, or an HF Space) -> nvidia-smi detects 2x T4 sets CUDA_VISIBLE_DEVICES=0,1 -> lms:1234 (Q4_K_M, MTP, jinja) -> oc:2456 (opencode web full tools, LSP on) -> chamber:3000 (openchamber, managed opencode :34637) -> cloudflared 4-ingress tunnel -> receiver: https://ai.yourdomain.com/v1 (opencode.json), https://oc.yourdomain.com (browser), https://chamber.yourdomain.com, ssh.yourdomain.com`

Check on host: `nvidia-smi` (2x T4) `lms ps` `curl http://localhost:1234/v1/models` `curl http://localhost:2456/doc` `curl http://localhost:3000` `cloudflared tunnel list`

## Files
- `cloudflared/config.yml` - 4 ingress (ai:1234 oc:2456 chamber:3000 ssh:22)
- `scripts/host.sh` - bash host, respects `MODEL` env + `$3`, HF Space `--repo-type space` support
- `scripts/startup.py` - Python host: Kaggle secrets, dual T4 detect, installs/starts `sshd`, copies `opencode.json`, Space pull, 48K context ladder, `def main()`
- `pyproject.toml` - `pip install -e .` gives `host-stack` command
- `opencode.json` - `tool_call:true` for A3B (`qwen3-coder-30b-a3b-instruct`), `lsp` block, `opencode-lmstudio` plugin
- `scripts/setup-tunnel.sh` - creates tunnel + 4 DNS routes
- `scripts/run-smallcode.sh` - fallback `harnesses/smallcode` (18 tools, forgiving parser)
- `enhanced/README.md` - tool enhancements
- `gpu/dual-t4.json/md` - dual T4 tuning (48K context, not 262K)
- `harnesses/smallcode` - submodule

## Fallback Harness

If opencode stalls (truncated JSON), run smallcode against same LM Studio:
```bash
./scripts/run-smallcode.sh ../your-project
```
