# Host Stack - LM Studio + Opencode + OpenChamber + SSH via Cloudflare (non-Docker)

Single `cloudflared` named tunnel with 4 subdomains, Python host-receiver, your dual T4 2x16GB. Fork of https://github.com/openchamber/openchamber extended for self-hosting.

## ✅ Verified Wiring (cross-checked)

| Subdomain | Service | Port | Source | Status |
|---|---|---|---|---|
| `ai.yourdomain.com` | LM Studio (`/v1`) | 1234 | `cloudflared/config.yml` + `lms server start --port 1234` + `startup.py` + `opencode.json` | ✅ |
| `oc.yourdomain.com` | Opencode Web (full tools) | 2456 | `cloudflared/config.yml` + `opencode web --port 2456` + `host.sh`/`startup.py` | ✅ (was 4096, renamed) |
| `chamber.yourdomain.com` | OpenChamber WebUI | 3000 | `cloudflared/config.yml` + `openchamber` (Docker maps 3000:3000) | ✅ |
| `ssh.yourdomain.com` | SSH | 22 | `cloudflared/config.yml` `ssh://localhost:22` | ✅ |

* All 4 ingress in one `t4host` tunnel + catch-all `http_status:404`
* `opencode.json`: both `lmstudio-local` and `lmstudio-tunneled` have `qwen3-coder-30b-a3b` `tool_call:true` `reasoning:true` `limit:32768/8192`, plugin `opencode-lmstudio@1.0.0-rc.2`
* Default model `qwen/qwen3-coder-30b-a3b` auto-pulled via `lms get` if `MODEL` secret missing, else uses your `MODEL` (Kaggle + env, strip-empty, `MODEL_NAME` fallback, patches `opencode.json`)
* Dual T4 wiring: `gpu/dual-t4.json` `Q4_K_M ~18GB` `CUDA_VISIBLE_DEVICES=0,1` `MTP --spec-type draft-mtp` `32k ctx` (not 262k)
* Enhanced: `harnesses/smallcode` submodule `mebassett/smallcode` 18-tool forgiving parser, `scripts/run-smallcode.sh` fallback
* `python -m py_compile` + `bash -n` pass

## Subdomains (one tunnel `t4host`)
- `ai.yourdomain.com` -> `http://localhost:1234` (+ `/v1` for OpenAI)
- `oc.yourdomain.com` -> `http://localhost:2456` (Opencode Web)
- `chamber.yourdomain.com` -> `http://localhost:3000` (OpenChamber)
- `ssh.yourdomain.com` -> `ssh://localhost:22`

## Download Links

* **This repo (4-subdomain host, clone with tools):** `git clone --recurse-submodules https://github.com/tirforge/host-ai-oc-chamber-ssh` or ZIP https://github.com/tirforge/host-ai-oc-chamber-ssh/archive/refs/heads/main.zip
* LM Studio: https://lmstudio.ai/download or `curl -fsSL https://lmstudio.ai/install.sh | bash`
* Opencode: `curl -fsSL https://opencode.ai/install | bash` https://github.com/anomalyco/opencode
* OpenChamber: `curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash` https://github.com/openchamber/openchamber
* cloudflared: https://github.com/cloudflare/cloudflared/releases
* Model (default A3B): `lms get qwen/qwen3-coder-30b-a3b -y` or HF https://huggingface.co/lmstudio-community/Qwen3-Coder-30B-A3B-GGUF and https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
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
# 3. Run (auto-pulls qwen3-coder-30b-a3b if MODEL not set)
MODEL=qwen/qwen3-coder-30b-a3b ./scripts/host.sh yourdomain.com
# or: MODEL=mistralai/devstral-small-2507 ./scripts/host.sh yourdomain.com t4host
```

## Kaggle Start (with secrets) - fully supported

Kaggle notebook must have **Internet ON** (Settings -> Internet) and **GPU T4 x2** (Settings -> Accelerator -> GPU T4 x2). All 4 services auto-install if missing.

Add Secrets in Kaggle Notebook: `Add-ons -> Secrets` and toggle on for this notebook:
* `CF_TOKEN` (or `CLOUDFLARE_API_TOKEN`) - CF API token with Zone/DNS/Tunnel Edit **or** `TUNNEL_TOKEN` (Zero Trust -> Networks -> Tunnels -> Create tunnel -> Copy token) - if you use `TUNNEL_TOKEN` you don't need `CF_TOKEN` API calls
* `CF_DOMAIN` - `yourdomain.com` (your Cloudflare domain)
* `OPENCHAMBER_UI_PASSWORD` (or `PASSWORD`) - WebUI password for `oc` + `chamber`
* `SSH_PASSWORD` (or `SSH_PASS`) - **SSH password** for `ssh.yourdomain.com` (sets Linux `root` + current user via `chpasswd`, tune via env)
* `MODEL` (optional, default `qwen/qwen3-coder-30b-a3b` A3B - if secret missing -> default, if set like `mistralai/devstral-small-2507` -> pulls that)
* `TUNNEL_TOKEN` (optional alternative to `CF_TOKEN` for headless Kaggle where `cloudflared tunnel login` browser is impossible)

Cell (Kaggle, with Internet + GPU):
```python
!git clone --recurse-submodules https://github.com/tirforge/host-ai-oc-chamber-ssh
!pip install -q kaggle_secrets  # preinstalled on Kaggle, no-op if exists
!python host-ai-oc-chamber-ssh/scripts/startup.py  # reads Kaggle Secrets via UserSecretsClient + env, auto-installs lms/cloudflared/opencode/openchamber if missing, pulls $MODEL
# MODEL not set -> pulls qwen3-coder-30b-a3b Q4_K_M (55-70 tok/s on dual T4), if you set MODEL=... in Secrets it pulls that and patches opencode.json tool_call:true
```

`scripts/startup.py` handles both: `os.environ` and `kaggle_secrets.UserSecretsClient`, strip-empty, `MODEL`/`MODEL_NAME` fallback, `TUNNEL_TOKEN` vs `CF_TOKEN` branching, `~/.cloudflared` paths via `expanduser`, dual T4 auto-detect `CUDA_VISIBLE_DEVICES=0,1`.

## Python Command to Run

After `pip install -e .` (uses `pyproject.toml`):

```bash
pip install -e .
host-stack            # same as host-ai-oc, runs scripts.startup:main
host-ai-oc            # alias
python -m scripts.startup
python scripts/startup.py
# with custom model/domain
MODEL=qwen/qwen3.5-35b-a3b CF_DOMAIN=yourdomain.com host-stack
```

`scripts/startup.py` now has `def main():` + `if __name__ == "__main__": main()` so it works as module, script, and installed command. It also patches `opencode.json` to add your MODEL with `tool_call:true`.

## How to Connect SSH (3 ways)

Host already runs `cloudflared tunnel run t4host` exposing `ssh.yourdomain.com -> ssh://localhost:22` (sshd should listen on localhost, hardened).

**1. Quick (no config):**
```bash
ssh -o ProxyCommand="cloudflared access ssh --hostname ssh.yourdomain.com" user@ssh.yourdomain.com
```

**2. Persistent `~/.ssh/config` (recommended):**
```
Host ssh.yourdomain.com
  ProxyCommand cloudflared access ssh --hostname %h
  User your-linux-user
# then:
ssh ssh.yourdomain.com
# scp, rsync, VS Code Remote SSH all work via same ProxyCommand
```

**3. WARP (mobile/Termius where ProxyCommand not possible):**
Install Cloudflare WARP app, enroll, then `ssh user@ssh.yourdomain.com` directly (WARP routes via tunnel).

Verify SSH: `ssh ssh.yourdomain.com "nvidia-smi"` should show `2x T4`.

## Workflow Verified

`Secrets (CF_TOKEN/CF_DOMAIN/PASSWORD/MODEL) -> startup.py/host.sh pulls $MODEL (or default A3B) -> nvidia-smi detects 2x T4 sets CUDA_VISIBLE_DEVICES=0,1 -> lms:1234 (Q4_K_M, MTP, jinja) -> oc:2456 (opencode web full tools) -> chamber:3000 (openchamber) -> cloudflared 4-ingress tunnel -> receiver: https://ai.yourdomain.com/v1 (opencode.json), https://oc.yourdomain.com (browser), https://chamber.yourdomain.com, ssh.yourdomain.com`

Check on host: `nvidia-smi` (2x T4) `lms ps` `curl http://localhost:1234/v1/models` `curl http://localhost:2456/doc` `curl http://localhost:3000` `cloudflared tunnel list`

## Files
- `cloudflared/config.yml` - 4 ingress (ai:1234 oc:2456 chamber:3000 ssh:22)
- `scripts/host.sh` - bash host, respects `MODEL` env + `$3`
- `scripts/startup.py` - Python host, Kaggle secrets, dual T4 detect, patches opencode.json, `def main()`
- `pyproject.toml` - `pip install -e .` gives `host-stack` command
- `opencode.json` - `tool_call:true` for A3B, `opencode-lmstudio` plugin
- `scripts/setup-tunnel.sh` - creates tunnel + 4 DNS routes
- `scripts/run-smallcode.sh` - fallback `harnesses/smallcode` (18 tools, forgiving parser)
- `enhanced/README.md` - tool enhancements
- `gpu/dual-t4.json/md` - dual T4 tuning
- `harnesses/smallcode` - submodule

## Fallback Harness
If opencode stalls (truncated JSON), run smallcode against same LM Studio:
```bash
./scripts/run-smallcode.sh ../your-project
```
