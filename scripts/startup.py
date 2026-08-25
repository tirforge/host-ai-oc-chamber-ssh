#!/usr/bin/env python3
"""
Host stack startup for Kaggle / any env - reads secrets from env or Kaggle UserSecretsClient
- CF token + domain -> cloudflared named tunnel with 4 subdomains
- UI password -> OpenChamber / Opencode
- Model -> default qwen3-coder-30b-a3b (auto-pulled via lms)

Env secrets (set in Kaggle Secrets -> Add-ons -> Secrets):
  CF_TOKEN / CLOUDFLARE_API_TOKEN / CLOUDFLARE_TOKEN (API token) OR TUNNEL_TOKEN (Zero Trust JWT, prefer for Kaggle)
  CF_DOMAIN / CLOUDFLARE_DOMAIN / DOMAIN  (e.g. aaruvi.space)
  OPENCHAMBER_UI_PASSWORD / UI_PASSWORD / PASSWORD
  SSH_PASSWORD / SSH_PASS (for ssh.yourdomain.com via cloudflared, sets Linux password for root/current user)
  MODEL  (default: lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M - use HF repo or LM Studio ID, not qwen/qwen3-coder-30b-a3b)
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


def ensure_tool(name, install_cmd):
    if shutil.which(name):
        return True
    print(f"{name} not found, installing...", flush=True)
    run(install_cmd)
    # also try common paths (must include opencode)
    for p in [os.path.expanduser("~/.lmstudio/bin"), os.path.expanduser("~/.opencode/bin"), os.path.expanduser("~/.local/bin"), "/usr/local/bin"]:
        if p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{p}:{os.environ.get('PATH','')}"
    return shutil.which(name) is not None

def main():
    # Export PATH before any which checks (fixes lms/opencode not found after install)
    for p in [os.path.expanduser("~/.lmstudio/bin"), os.path.expanduser("~/.opencode/bin"), os.path.expanduser("~/.local/bin"), "/usr/local/bin"]:
        if p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{p}:{os.environ.get('PATH','')}"
    # also add fnm/nvm node 22 bin if present
    import glob
    for pat in [os.path.expanduser("~/.local/share/fnm/aliases/default/bin"), os.path.expanduser("~/.nvm/versions/node/v22*/bin")]:
        for p in glob.glob(pat):
            if p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{p}:{os.environ.get('PATH','')}"
    # Kaggle explicit format you requested - you can tune these 5 via Kaggle Secrets UI
    # This populates env so get_secret() picks them up
    try:
        from kaggle_secrets import UserSecretsClient
        user_secrets = UserSecretsClient()
        secret_value_0 = user_secrets.get_secret("CF_DOMAIN")
        secret_value_1 = user_secrets.get_secret("CF_TOKEN")
        secret_value_2 = user_secrets.get_secret("MODEL")
        secret_value_3 = user_secrets.get_secret("OPENCHAMBER_UI_PASSWORD")
        secret_value_4 = user_secrets.get_secret("TUNNEL_TOKEN")
        secret_value_5 = user_secrets.get_secret("SSH_PASSWORD")
        # tune via env: if you set env, it overrides; otherwise use Kaggle secret
        if secret_value_0 and not os.getenv("CF_DOMAIN"): os.environ["CF_DOMAIN"] = secret_value_0.strip()
        if secret_value_1 and not os.getenv("CF_TOKEN"): os.environ["CF_TOKEN"] = secret_value_1.strip()
        if secret_value_2 and not os.getenv("MODEL"): os.environ["MODEL"] = secret_value_2.strip()
        if secret_value_3 and not os.getenv("OPENCHAMBER_UI_PASSWORD"): os.environ["OPENCHAMBER_UI_PASSWORD"] = secret_value_3.strip()
        if secret_value_4 and not os.getenv("TUNNEL_TOKEN"): os.environ["TUNNEL_TOKEN"] = secret_value_4.strip()
        if secret_value_5 and not os.getenv("SSH_PASSWORD"): os.environ["SSH_PASSWORD"] = secret_value_5.strip()
    except Exception:
        pass  # not in Kaggle or secrets not set - fallback to env/get_secret below

    CF_TOKEN = get_secret("CF_TOKEN") or get_secret("CLOUDFLARE_API_TOKEN") or get_secret("CLOUDFLARE_TOKEN")
    DOMAIN = get_secret("CF_DOMAIN") or get_secret("CLOUDFLARE_DOMAIN") or get_secret("DOMAIN")
    TUNNEL_TOKEN = get_secret("TUNNEL_TOKEN") or get_secret("CF_TUNNEL_TOKEN")
    PASSWORD = get_secret("OPENCHAMBER_UI_PASSWORD") or get_secret("UI_PASSWORD") or get_secret("PASSWORD") or "changeme"
    SSH_PASSWORD = get_secret("SSH_PASSWORD") or get_secret("SSH_PASS") or get_secret("SUDO_PASSWORD")
    # MODEL: if secret not present -> default, if passed -> use that (use HF repo for lms, e.g. lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M)
    MODEL_DEFAULT = "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M"
    MODEL = get_secret("MODEL") or get_secret("MODEL_NAME") or MODEL_DEFAULT
    if not MODEL or not MODEL.strip():
        MODEL = MODEL_DEFAULT
    MODEL = MODEL.strip()
    # map old alias to valid HF repo (fixes Kaggle qwen/qwen3-coder-30b-a3b not found)
    if MODEL in ["qwen/qwen3-coder-30b-a3b", "qwen3-coder-30b-a3b"]:
        print(f"Mapping alias {MODEL} -> lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M", flush=True)
        MODEL = "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M"
    TUNNEL = get_secret("TUNNEL_NAME") or "t4host"

    if not DOMAIN:
        print("ERROR: Need CF_DOMAIN (yourdomain.com) set as env/Kaggle secret", flush=True)
        print(f"Got DOMAIN={DOMAIN}", flush=True)
        sys.exit(1)
    if not CF_TOKEN and not TUNNEL_TOKEN:
        print("ERROR: Need CF_TOKEN (API token) or TUNNEL_TOKEN (Zero Trust tunnel token) set as env/Kaggle secret", flush=True)
        print(f"Got CF_TOKEN={'***' if CF_TOKEN else 'MISSING'} TUNNEL_TOKEN={'***' if TUNNEL_TOKEN else 'MISSING'}", flush=True)
        sys.exit(1)

    os.environ["OPENCHAMBER_UI_PASSWORD"] = PASSWORD
    os.environ["CLOUDFLARE_API_TOKEN"] = CF_TOKEN if CF_TOKEN else ""
    os.environ["CF_API_TOKEN"] = CF_TOKEN if CF_TOKEN else ""
    if SSH_PASSWORD and SSH_PASSWORD.strip():
        os.environ["SSH_PASSWORD"] = SSH_PASSWORD.strip()
        # configure SSH password - only chpasswd (passwd --stdin not on Debian), only for existing users
        ssh_user = os.getenv("USER") or "root"
        for u in list({ssh_user, "root"}):
            if run(f"id -u {u} >/dev/null 2>&1"):
                run(f'echo "{u}:{SSH_PASSWORD.strip()}" | chpasswd 2>&1 || true')
                print(f"SSH password set for {u}", flush=True)
            else:
                print(f"User {u} not found, skipping chpasswd", flush=True)
    else:
        print("SSH_PASSWORD not set - SSH will use existing host password/keys", flush=True)

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

    # Kaggle/CI auto-install missing tools - ensure PATH first (fix lms/opencode not found)
    for p in [os.path.expanduser("~/.lmstudio/bin"), os.path.expanduser("~/.opencode/bin"), os.path.expanduser("~/.local/bin"), "/usr/local/bin"]:
        if p not in os.environ.get("PATH",""):
            os.environ["PATH"] = f"{p}:{os.environ['PATH']}"
    for pat in [os.path.expanduser("~/.nvm/versions/node/v22*/bin"), os.path.expanduser("~/.local/share/fnm/aliases/default/bin")]:
        import glob as _glob
        for p in _glob.glob(pat):
            if p not in os.environ.get("PATH",""):
                os.environ["PATH"] = f"{p}:{os.environ['PATH']}"
    ensure_tool("lms", "curl -fsSL https://lmstudio.ai/install.sh | bash; export PATH=\"$HOME/.lmstudio/bin:$PATH\"; lms daemon up || true")
    ensure_tool("cloudflared", "curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared && chmod +x /tmp/cloudflared && mv /tmp/cloudflared /usr/local/bin/cloudflared || true")
    # opencode: npm -g is main, script as fallback (as you requested)
    ensure_tool("opencode", "npm install -g opencode-ai || npm i -g opencode || bun install -g opencode-ai || curl -fsSL https://opencode.ai/install | bash || true; export PATH=\"$HOME/.opencode/bin:$HOME/.local/bin:$PATH\"")
    # openchamber needs Node 22+ - ensure Node 22 is active before install (use nodesource for Kaggle)
    if not shutil.which("openchamber"):
        has_node22 = shutil.which("node") and run("node --version | grep -q 'v22\\|v23\\|v24'")
        if not has_node22:
            print("Installing Node 22 for openchamber (nodesource + fnm fallback)...", flush=True)
            run("curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs || true")
            # verify
            run("node --version; npm --version || true")
            if not (shutil.which("node") and run("node --version | grep -q 'v22'")):
                run("curl -fsSL https://fnm.vercel.app/install | bash || true")
                run("bash -c 'export PATH=\"$HOME/.local/share/fnm:$PATH\"; eval \"$(fnm env 2>/dev/null)\" || true; fnm install 22; fnm use 22; node --version' || true")
        # update PATH for this process after Node install
        for pat in [os.path.expanduser("~/.nvm/versions/node/v22*/bin"), os.path.expanduser("~/.local/share/fnm/aliases/default/bin")]:
            import glob as _glob2
            for p in _glob2.glob(pat):
                if p not in os.environ.get("PATH",""):
                    os.environ["PATH"] = f"{p}:{os.environ['PATH']}"
        run("node --version; npm --version || true")
        # now install openchamber with Node 22 in PATH
        run("bash -c 'export PATH=\"$HOME/.local/share/fnm:$PATH\"; eval \"$(fnm env 2>/dev/null)\" || true; export NVM_DIR=\"$HOME/.nvm\"; [ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\"; nvm use 22 2>/dev/null || true; node --version; curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash' || curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash")

    # 1. Pull model (LM Studio) - try HF repo, fallback to alias
    if shutil.which("lms"):
        try:
            n = subprocess.run("nvidia-smi --query-gpu=name --format=csv,noheader | grep -c T4 || echo 0", shell=True, capture_output=True, text=True).stdout.strip()
            if "2" in n:
                print("Dual T4 detected, setting CUDA_VISIBLE_DEVICES=0,1", flush=True)
                os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"
        except Exception:
            pass
        print(f"Pulling model {MODEL}...", flush=True)
        run("lms daemon up || true")
        # try requested model, fallback to known good
        if not run(f'lms get {MODEL} -y'):
            run(f'lms get {MODEL} || true')
        # fallback if still no model - try known tiny model that exists in older catalog
        for fb in ["lmstudio-community/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M", "qwen2.5-coder-7b-instruct", "tinyllama"]:
            run(f'lms get {fb} -y || true')
    else:
        print("lms not found, skipping model pull (install LM Studio first)", flush=True)

    # 2. Start services
    procs = []
    if shutil.which("lms"):
        # lms server start does not support --gpu/--jinja, use plain
        procs.append(run_bg("lms server start --port 1234 --cors", "lmstudio:1234"))
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
    cred_path = os.path.expanduser(f"~/.cloudflared/{TUNNEL}.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    # always ensure config reflects current DOMAIN/TUNNEL (fix stale ~/.cloudflared/config.yml)
    cfg = f"""tunnel: {TUNNEL}
credentials-file: {cred_path}
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
        # cfut_ is API token, eyJ... is JWT tunnel token
        is_jwt = TUNNEL_TOKEN and TUNNEL_TOKEN.startswith("eyJ") and TUNNEL_TOKEN.count(".") >= 2
        if TUNNEL_TOKEN and is_jwt:
            print(f"Using TUNNEL_TOKEN (Zero Trust JWT) for {TUNNEL} ...", flush=True)
            procs.append(run_bg(f"cloudflared tunnel run --token {TUNNEL_TOKEN}", "cloudflared"))
        elif TUNNEL_TOKEN and TUNNEL_TOKEN.startswith("cfut_"):
            # cfut_ API token in headless Kaggle has no cert.pem -> use quick tunnels (trycloudflare.com) as fallback
            cert = os.path.expanduser("~/.cloudflared/cert.pem")
            if os.path.exists(cert):
                print(f"TUNNEL_TOKEN is cfut_ API token, using named tunnel flow (create + route dns)", flush=True)
                if not CF_TOKEN:
                    CF_TOKEN = TUNNEL_TOKEN
                    os.environ["CLOUDFLARE_API_TOKEN"] = CF_TOKEN
                run(f"cloudflared tunnel create {TUNNEL} || true")
                for sub in ["ai", "oc", "chamber", "ssh"]:
                    run(f"cloudflared tunnel route dns {TUNNEL} {sub}.{DOMAIN} || true")
                print(f"Starting cloudflared tunnel {TUNNEL} ...", flush=True)
                procs.append(run_bg(f"cloudflared tunnel run {TUNNEL}", "cloudflared"))
            else:
                print(f"TUNNEL_TOKEN is cfut_ but no cert.pem (headless Kaggle) -> using quick tunnels (trycloudflare.com) for each port", flush=True)
                # quick tunnels need no cert/domain, one per service
                for sub, port in [("ai", 1234), ("oc", 2456), ("chamber", 3000)]:
                    procs.append(run_bg(f"cloudflared tunnel --url http://localhost:{port} 2>&1 | sed -u 's/^/[{sub}]/'", f"cloudflared-{sub}"))
                print(f"Quick tunnels started for ai:1234 oc:2456 chamber:3000 (check logs for https://*.trycloudflare.com URLs)", flush=True)
        else:
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
