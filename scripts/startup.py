#!/usr/bin/env python3
"""
Host stack startup for Kaggle / any env - reads secrets from env or Kaggle UserSecretsClient
- CF token + domain -> cloudflared named tunnel with 4 subdomains
- UI password -> OpenChamber / Opencode
- Model -> default qwen3-coder-30b-a3b (auto-pulled via lms)

Env secrets (set in Kaggle Secrets -> Add-ons -> Secrets):
  CF_TOKEN / CLOUDFLARE_API_TOKEN / CLOUDFLARE_TOKEN
  CF_DOMAIN / CLOUDFLARE_DOMAIN / DOMAIN  (e.g. yourdomain.com)
  OPENCHAMBER_UI_PASSWORD / UI_PASSWORD / PASSWORD
  MODEL  (default: qwen/qwen3-coder-30b-a3b)
  TUNNEL_NAME (default: t4host)
  LM_API_TOKEN (optional, for ai subdomain auth)

Run: python scripts/startup.py
"""
import os
import sys
import subprocess
import time
import shutil

def get_secret(name, default=None):
    # 1. os.environ
    for k in [name, name.upper(), name.lower()]:
        v = os.getenv(k)
        if v:
            return v
    # 2. Kaggle UserSecretsClient
    try:
        from kaggle_secrets import UserSecretsClient
        c = UserSecretsClient()
        for k in [name, name.upper()]:
            try:
                v = c.get_secret(k)
                if v:
                    return v
            except Exception:
                pass
    except ImportError:
        pass
    return default

def run_bg(cmd, name):
    print(f"[{name}] {cmd}", flush=True)
    return subprocess.Popen(cmd, shell=True)

def run(cmd):
    print(f"$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True)
    return r.returncode == 0

CF_TOKEN = get_secret("CF_TOKEN") or get_secret("CLOUDFLARE_API_TOKEN") or get_secret("CLOUDFLARE_TOKEN")
DOMAIN = get_secret("CF_DOMAIN") or get_secret("CLOUDFLARE_DOMAIN") or get_secret("DOMAIN")
PASSWORD = get_secret("OPENCHAMBER_UI_PASSWORD") or get_secret("UI_PASSWORD") or get_secret("PASSWORD") or "changeme"
MODEL = get_secret("MODEL") or "qwen/qwen3-coder-30b-a3b"
TUNNEL = get_secret("TUNNEL_NAME") or "t4host"

if not CF_TOKEN or not DOMAIN:
    print("ERROR: Need CF_TOKEN and CF_DOMAIN set as env/Kaggle secrets", flush=True)
    print(f"Got CF_TOKEN={'***' if CF_TOKEN else 'MISSING'} DOMAIN={DOMAIN}", flush=True)
    sys.exit(1)

os.environ["OPENCHAMBER_UI_PASSWORD"] = PASSWORD
# cloudflared expects token via env or config; we use token-based tunnel (quick) if needed
# For named tunnel with domain we use CF API token via CLOUDFLARE_API_TOKEN
os.environ["CLOUDFLARE_API_TOKEN"] = CF_TOKEN
os.environ["CF_API_TOKEN"] = CF_TOKEN

print(f"Domain: {DOMAIN} Tunnel: {TUNNEL} Model: {MODEL}", flush=True)

# 1. Pull model (LM Studio)
if shutil.which("lms"):
    # detect dual T4
    try:
        n = subprocess.run("nvidia-smi --query-gpu=name --format=csv,noheader | grep -c T4 || echo 0", shell=True, capture_output=True, text=True).stdout.strip()
        if "2" in n:
            print("Dual T4 detected, setting CUDA_VISIBLE_DEVICES=0,1 and MTP", flush=True)
            os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"
    except Exception:
        pass
    print("Pulling model...", flush=True)
    # enhanced: try Q4_K_M quant for dual T4 fit
    run(f'lms get {MODEL} -y || lms get {MODEL} || echo "lms get failed - check LM Studio catalog"')
else:
    print("lms not found, skipping model pull (install LM Studio first)", flush=True)

# 2. Start services
procs = []
if shutil.which("lms"):
    # full tool-calling + MTP for dual T4 50-70 tok/s
    procs.append(run_bg("lms server start --port 1234 --cors --gpu max --jinja", "lmstudio:1234"))
else:
    print("lms missing", flush=True)

time.sleep(2)
if shutil.which("opencode"):
    procs.append(run_bg("opencode web --port 2456 --hostname 0.0.0.0", "opencode:2456"))
else:
    print("opencode missing, install via: curl -fsSL https://opencode.ai/install | bash", flush=True)

if shutil.which("openchamber"):
    procs.append(run_bg("openchamber --ui-password \"$OPENCHAMBER_UI_PASSWORD\"", "openchamber:3000"))
else:
    print("openchamber missing, install via: curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash", flush=True)

time.sleep(3)

# 3. Cloudflare tunnel - 4 ingress: ai, oc, chamber, ssh
# Create config for named tunnel if not exists
config_path = os.path.expanduser("~/.cloudflared/config.yml")
os.makedirs(os.path.dirname(config_path), exist_ok=True)
if not os.path.exists(config_path):
    cfg = f"""tunnel: {TUNNEL}
credentials-file: /root/.cloudflared/{TUNNEL}.json
ingress:
  - hostname: ai.{DOMAIN}
    service: http://localhost:1234
  - hostname: oc.{DOMAIN}
    service: http://localhost:2456
  - hostname: chamber.{DOMAIN}
    service: http://localhost:3000
  - hostname: ssh.{DOMAIN}
    service: ssh://localhost:22
  - service: http_status:404
"""
    open(config_path, "w").write(cfg)
    print(f"Wrote {config_path}", flush=True)

# Try named tunnel, fallback to quick tunnel
if shutil.which("cloudflared"):
    # ensure tunnel exists + DNS (idempotent)
    run(f"cloudflared tunnel create {TUNNEL} || true")
    for sub in ["ai", "oc", "chamber", "ssh"]:
        run(f"cloudflared tunnel route dns {TUNNEL} {sub}.{DOMAIN} || true")
    print(f"Starting cloudflared tunnel {TUNNEL} ...", flush=True)
    procs.append(run_bg(f"cloudflared tunnel run {TUNNEL}", "cloudflared"))
    # also print quick tunnel for debugging
    print(f"AI: https://ai.{DOMAIN}/v1  OC: https://oc.{DOMAIN}  Chamber: https://chamber.{DOMAIN}  SSH: ssh.{DOMAIN}", flush=True)
else:
    print("cloudflared missing, install via: curl -fsSL https://pkg.cloudflare.com/cloudflared | sh", flush=True)

print("All services started. Tailing... Ctrl+C to stop.", flush=True)
try:
    while True:
        time.sleep(10)
        # keep alive check
        for p in procs:
            if p.poll() is not None:
                print(f"Process exited: {p.args} code={p.returncode}", flush=True)
except KeyboardInterrupt:
    print("Stopping...", flush=True)
    for p in procs:
        p.terminate()
