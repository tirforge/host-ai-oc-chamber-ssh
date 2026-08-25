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
    # 1. os.environ (strip empty)
    for k in [name, name.upper(), name.lower()]:
        v = os.getenv(k)
        if v and v.strip():
            return v.strip()
    # 2. Kaggle UserSecretsClient
    try:
        from kaggle_secrets import UserSecretsClient
        c = UserSecretsClient()
        for k in [name, name.upper(), name.lower(), f"_{name.upper()}"]:
            try:
                v = c.get_secret(k)
                if v and str(v).strip():
                    return str(v).strip()
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

def main():
    CF_TOKEN = get_secret("CF_TOKEN") or get_secret("CLOUDFLARE_API_TOKEN") or get_secret("CLOUDFLARE_TOKEN")
    DOMAIN = get_secret("CF_DOMAIN") or get_secret("CLOUDFLARE_DOMAIN") or get_secret("DOMAIN")
    PASSWORD = get_secret("OPENCHAMBER_UI_PASSWORD") or get_secret("UI_PASSWORD") or get_secret("PASSWORD") or "changeme"
    # MODEL: if secret not present -> default qwen3-coder-30b-a3b, if passed -> use that
    MODEL_DEFAULT = "qwen/qwen3-coder-30b-a3b"
    MODEL = get_secret("MODEL") or get_secret("MODEL_NAME") or MODEL_DEFAULT
    if not MODEL or not MODEL.strip():
        MODEL = MODEL_DEFAULT
    MODEL = MODEL.strip()
    TUNNEL = get_secret("TUNNEL_NAME") or "t4host"

    if not CF_TOKEN or not DOMAIN:
        print("ERROR: Need CF_TOKEN and CF_DOMAIN set as env/Kaggle secrets", flush=True)
        print(f"Got CF_TOKEN={'***' if CF_TOKEN else 'MISSING'} DOMAIN={DOMAIN}", flush=True)
        sys.exit(1)

    os.environ["OPENCHAMBER_UI_PASSWORD"] = PASSWORD
    os.environ["CLOUDFLARE_API_TOKEN"] = CF_TOKEN
    os.environ["CF_API_TOKEN"] = CF_TOKEN

    print(f"Domain: {DOMAIN} Tunnel: {TUNNEL} Model: {MODEL} (default {MODEL_DEFAULT} if no secret)", flush=True)
    # Patch opencode.json to ensure requested MODEL is listed with tool_call
    try:
        import json
        p = os.path.join(os.path.dirname(__file__), "..", "opencode.json")
        if os.path.exists(p):
            j = json.load(open(p))
            for prov in ["lmstudio-local", "lmstudio-tunneled"]:
                if prov in j.get("provider", {}):
                    m = j["provider"][prov].setdefault("models", {})
                    if MODEL not in m:
                        m[MODEL] = {"name": MODEL, "tool_call": True, "reasoning": True, "limit": {"context": 32768, "output": 8192}}
                        print(f"Added {MODEL} to opencode.json provider {prov}", flush=True)
            open(p, "w").write(json.dumps(j, indent=2))
    except Exception as e:
        print(f"opencode.json patch skipped: {e}", flush=True)

    # 1. Pull model (LM Studio)
    if shutil.which("lms"):
        try:
            n = subprocess.run("nvidia-smi --query-gpu=name --format=csv,noheader | grep -c T4 || echo 0", shell=True, capture_output=True, text=True).stdout.strip()
            if "2" in n:
                print("Dual T4 detected, setting CUDA_VISIBLE_DEVICES=0,1 and MTP", flush=True)
                os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"
        except Exception:
            pass
        print("Pulling model...", flush=True)
        run(f'lms get {MODEL} -y || lms get {MODEL} || echo "lms get failed - check LM Studio catalog"')
    else:
        print("lms not found, skipping model pull (install LM Studio first)", flush=True)

    # 2. Start services
    procs = []
    if shutil.which("lms"):
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

    if shutil.which("cloudflared"):
        run(f"cloudflared tunnel create {TUNNEL} || true")
        for sub in ["ai", "oc", "chamber", "ssh"]:
            run(f"cloudflared tunnel route dns {TUNNEL} {sub}.{DOMAIN} || true")
        print(f"Starting cloudflared tunnel {TUNNEL} ...", flush=True)
        procs.append(run_bg(f"cloudflared tunnel run {TUNNEL}", "cloudflared"))
        print(f"AI: https://ai.{DOMAIN}/v1  OC: https://oc.{DOMAIN}  Chamber: https://chamber.{DOMAIN}  SSH: ssh.{DOMAIN}", flush=True)
    else:
        print("cloudflared missing, install via: curl -fsSL https://pkg.cloudflare.com/cloudflared | sh", flush=True)

    print("All services started. Tailing... Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(10)
            for p in procs:
                if p.poll() is not None:
                    print(f"Process exited: {p.args} code={p.returncode}", flush=True)
    except KeyboardInterrupt:
        print("Stopping...", flush=True)
        for p in procs:
            p.terminate()

if __name__ == "__main__":
    main()
