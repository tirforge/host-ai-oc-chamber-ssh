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
  MODEL  (default: lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF)
  TUNNEL_NAME (default: t4host)
  LM_API_TOKEN (optional, for ai subdomain auth)

Run: python scripts/startup.py
"""
import os
import sys
import subprocess
import time
import re
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
    # setsid + detached + logfile: survives parent/cell end, no pipe blocking
    log = f"/tmp/{name.replace('/', '_')}.log"
    lf = open(log, "ab", buffering=0)
    print(f"[{name}] (detached, log={log}) {cmd}", flush=True)
    return subprocess.Popen(cmd, shell=True, start_new_session=True, stdout=lf, stderr=subprocess.STDOUT)

def grep_url(name, pattern=r"https://[a-z0-9-]+\.trycloudflare\.com"):
    p = f"/tmp/{name}.log"
    try:
        m = re.search(pattern, open(p).read())
        return m.group(0) if m else None
    except Exception:
        return None

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

def cf_api_named_tunnel(cf_token, domain, tunnel_name):
    """Create named tunnel + DNS + ingress entirely via Cloudflare API (no cert.pem/browser).
    Returns run-JWT for `cloudflared tunnel run --token`, or None."""
    import json as _json
    try:
        import urllib.request
        H = {"Authorization": f"Bearer {cf_token}", "Content-Type": "application/json"}
        def api(method, path, body=None):
            req = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}",
                                         data=_json.dumps(body).encode() if body else None,
                                         headers=H, method=method)
            with urllib.request.urlopen(req, timeout=30) as r:
                return _json.load(r)
        # 1. account id
        acc = api("GET", "/accounts")
        if not acc.get("success"): print(f"CF API accounts failed: {acc.get('errors')}", flush=True); return None
        aid = acc["result"][0]["id"]
        # 2. create tunnel (remote-managed)
        t = api("POST", f"/accounts/{aid}/cfd_tunnel", {"name": tunnel_name, "config_src": "cloudflare"})
        if not t.get("success"):
            errs = str(t.get("errors"))
            if "already exists" in errs or "10603" in errs:
                lst = api("GET", f"/accounts/{aid}/cfd_tunnel?name={tunnel_name}&is_deleted=false")
                tid = lst["result"][0]["id"]; tok = None
                ttok = api("GET", f"/accounts/{aid}/cfd_tunnel/{tid}/token")
                tok = ttok["result"] if ttok.get("success") else None
            else:
                print(f"CF tunnel create failed: {errs}", flush=True); return None
        else:
            tid = t["result"]["id"]; tok = t["result"].get("token")
        # 3. ingress config (remote-managed): ai/oc/chamber/ssh -> local ports
        ing = {
            "config": {"ingress": [
                {"hostname": f"ai.{domain}", "service": "http://localhost:1234"},
                {"hostname": f"oc.{domain}", "service": "http://localhost:2456"},
                {"hostname": f"chamber.{domain}", "service": "http://localhost:3000"},
                {"hostname": f"ssh.{domain}", "service": "ssh://localhost:22"},
                {"service": "http_status:404"},
            ]}}
        cfg = api("PUT", f"/accounts/{aid}/cfd_tunnel/{tid}/configurations", ing)
        if not cfg.get("success"): print(f"CF ingress config failed: {cfg.get('errors')}", flush=True)
        # 4. DNS CNAMEs
        z = api("GET", f"/zones?name={domain}")
        if z.get("success") and z["result"]:
            zid = z["result"][0]["id"]
            for sub in ["ai", "oc", "chamber", "ssh"]:
                fqdn = f"{sub}.{domain}"
                existing = api("GET", f"/zones/{zid}/dns_records?type=CNAME&name={fqdn}")
                if existing.get("success") and existing["result"]:
                    print(f"DNS {fqdn}: already exists (reused)", flush=True)
                    continue
                body = {"type": "CNAME", "name": fqdn, "content": f"{tid}.cfargotunnel.com", "proxied": True}
                r = api("POST", f"/zones/{zid}/dns_records", body)
                print(f"DNS {fqdn}: {'created' if r.get('success') else r.get('errors')}", flush=True)
        else:
            print(f"Zone {domain} not found on this token's account - create CNAMEs manually -> {tid}.cfargotunnel.com", flush=True)
        print(f"Tunnel {tunnel_name} ({tid}) ready - run token obtained", flush=True)
        return tok
    except Exception as e:
        print(f"CF API setup failed: {e}", flush=True)
        return None


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
    # SSH password: SSH_PASSWORD -> SSH_PASS -> fallback to OpenChamber UI password
    SSH_PASSWORD = get_secret("SSH_PASSWORD") or get_secret("SSH_PASS") or get_secret("SUDO_PASSWORD") or PASSWORD
    # MODEL: if secret not present -> default, if passed -> use that (no :QUANT - llmster regex rejects colon; use HF repo id)
    MODEL_DEFAULT = "lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF"
    MODEL = get_secret("MODEL") or get_secret("MODEL_NAME") or MODEL_DEFAULT
    if not MODEL or not MODEL.strip():
        MODEL = MODEL_DEFAULT
    MODEL = MODEL.strip()
    # map old aliases to valid HF repo (fixes Kaggle artifact not found)
    if MODEL in ["qwen/qwen3-coder-30b-a3b", "qwen3-coder-30b-a3b", "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF", "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M"]:
        print(f"Mapping alias {MODEL} -> {MODEL_DEFAULT}", flush=True)
        MODEL = MODEL_DEFAULT
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
        # fallback: use OpenChamber UI password for SSH too
        if PASSWORD and PASSWORD != "changeme":
            os.environ["SSH_PASSWORD"] = PASSWORD
            ssh_user = os.getenv("USER") or "root"
            for u in list({ssh_user, "root"}):
                if run(f"id -u {u} >/dev/null 2>&1"):
                    run(f'echo "{u}:{PASSWORD}" | chpasswd 2>&1 || true')
                    print(f"SSH password set for {u} (from OPENCHAMBER_UI_PASSWORD)", flush=True)
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
        base = MODEL.split(":")[0]  # llmster regex rejects ':QUANT' suffix
        # try base repo id first, then full, then direct HuggingFace download into LM Studio models dir (auto-discovered)
        if not run(f'lms get "{base}" -y'):
            if not run(f'lms get "{base}"'):
                print("lms get failed -> direct HF download into ~/.lmstudio/models ...", flush=True)
                dest = os.path.expanduser(f"~/.lmstudio/models/{base}")
                os.makedirs(dest, exist_ok=True)
                run(f'pip install -q -U "huggingface_hub[cli]" >/dev/null 2>&1 || true; (hf download "{base}" --include "*Q4_K_M*" --local-dir "{dest}" </dev/null || huggingface-cli download "{base}" --include "*Q4_K_M*" --local-dir "{dest}" </dev/null) || true')
                import glob as _g
                ggufs = _g.glob(os.path.join(dest, "**", "*.gguf"), recursive=True) or _g.glob(os.path.join(dest, "*.gguf"))
                if ggufs:
                    print(f"Downloaded {len(ggufs)} GGUF file(s) -> {dest}", flush=True)
                    run("lms daemon up || true")
                    # load model so /v1/models lists it
                    g0 = sorted(ggufs, key=len)[0]  # smallest = often not main; prefer largest
                    g0 = max(ggufs, key=os.path.getsize)
                    print(f"Loading {os.path.basename(g0)}...", flush=True)
                    run(f'lms load "{g0}" -y --gpu max || lms load "{g0}" -y || lms load "{os.path.basename(g0)}" -y || true')
                else:
                    print("HF direct download failed too - server will start with no model; set MODEL secret to a valid HF GGUF repo", flush=True)
    else:
        print("lms not found, skipping model pull (install LM Studio first)", flush=True)

    # 2. Start services
    # lms/openchamber are daemon-managed: their CLI returns immediately (code 0) while daemon serves.
    # Only cloudflared tunnels are long-lived processes worth monitoring.
    tunnels = []
    if shutil.which("lms"):
        run_bg("lms server start --port 1234 --cors", "lmstudio:1234")
        time.sleep(2)
        if run("curl -s -m 5 http://localhost:1234/v1/models >/dev/null"):
            print("LM Studio API up on :1234", flush=True)
        else:
            print("LM Studio API not responding yet (model may still be downloading)", flush=True)
    else:
        print("lms missing", flush=True)

    time.sleep(1)
    if shutil.which("opencode"):
        os.environ["OPENCODE_SERVER_PASSWORD"] = PASSWORD  # secure opencode web (fixes unsecured warning)
        run_bg(f'opencode web --port 2456 --hostname 0.0.0.0', "opencode:2456")
    else:
        print("opencode missing, install via: curl -fsSL https://opencode.ai/install | bash", flush=True)

    if shutil.which("openchamber"):
        run_bg('openchamber --ui-password "$OPENCHAMBER_UI_PASSWORD"', "openchamber:3000")

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
            tunnels.append(run_bg(f"cloudflared tunnel run --token {TUNNEL_TOKEN}", "cloudflared"))
        elif TUNNEL_TOKEN and TUNNEL_TOKEN.startswith("cfut_"):
            # cfut_ = API token. Headless Kaggle has no cert.pem for `tunnel create`.
            # Use Cloudflare REST API to create named tunnel + ingress + DNS, then run with returned JWT.
            api_tok = CF_TOKEN or TUNNEL_TOKEN
            jwt = cf_api_named_tunnel(api_tok, DOMAIN, TUNNEL)
            if jwt:
                print(f"Using API-created tunnel run token for {TUNNEL} ...", flush=True)
                tunnels.append(run_bg(f"cloudflared tunnel run --token {jwt}", "cloudflared"))
            else:
                # last resort: quick tunnels
                print("API tunnel setup failed -> falling back to quick tunnels (trycloudflare.com)", flush=True)
                for sub, port in [("ai", 1234), ("oc", 2456), ("chamber", 3000)]:
                    tunnels.append(run_bg(f"cloudflared tunnel --url http://localhost:{port}", f"cloudflared-{sub}"))
                print("Waiting for quick tunnel URLs...", flush=True)
                time.sleep(12)
                for sub in ["ai", "oc", "chamber"]:
                    u = grep_url(f"cloudflared-{sub}")
                    print(f"  {sub}: {u or 'pending - check /tmp/cloudflared-' + sub + '.log'}", flush=True)
        else:
            run(f"cloudflared tunnel create {TUNNEL} || true")
            for sub in ["ai", "oc", "chamber", "ssh"]:
                run(f"cloudflared tunnel route dns {TUNNEL} {sub}.{DOMAIN} || true")
            print(f"Starting cloudflared tunnel {TUNNEL} ...", flush=True)
            tunnels.append(run_bg(f"cloudflared tunnel run {TUNNEL}", "cloudflared"))
        print(f"AI: https://ai.{DOMAIN}/v1  OC: https://oc.{DOMAIN}  Chamber: https://chamber.{DOMAIN}  SSH: ssh.{DOMAIN}", flush=True)
    else:
        print("cloudflared missing, install via: curl -fsSL https://pkg.cloudflare.com/cloudflared | sh", flush=True)

    print("All services started. Tailing... Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(10)
            for p in tunnels:
                if p.poll() is not None:
                    print(f"Process exited: {p.args} code={p.returncode}", flush=True)
    except KeyboardInterrupt:
        print("Stopping...", flush=True)
        for p in tunnels:
            p.terminate()

if __name__ == "__main__":
    main()
