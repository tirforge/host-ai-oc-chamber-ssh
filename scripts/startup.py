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
import json
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

def wait_http(url, tries=12, delay=5, name=""):
    for i in range(tries):
        r = subprocess.run(f"curl -s -m 4 -o /dev/null -w '%{{http_code}}' {url}", shell=True, capture_output=True, text=True)
        code = r.stdout.strip()
        if code.startswith("2") or code in ("401", "403"):  # 401 = up, auth required
            print(f"{name or url}: UP ({code}) after {(i+1)*delay}s", flush=True)
            return True
        time.sleep(delay)
    print(f"{name or url}: DOWN after {tries*delay}s", flush=True)
    return False

def run(cmd):
    # Output is shown live on the terminal so failures are easy to spot.
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
        import urllib.error
        import urllib.request
        H = {"Authorization": f"Bearer {cf_token}", "Content-Type": "application/json"}
        def api(method, path, body=None):
            req = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}",
                                         data=_json.dumps(body).encode() if body else None,
                                         headers=H, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa
                    return _json.load(r)
            except urllib.error.HTTPError as e:
                # 4xx still carries JSON (e.g. 409 = tunnel already exists -> reuse path)
                try:
                    return _json.load(e)
                except Exception:
                    return {"success": False, "errors": [{"message": str(e)}]}
        # 1. account id - prefer /accounts; fallback to zone lookup (zone-scoped tokens return empty accounts)
        aid = None
        acc = api("GET", "/accounts")
        if acc.get("success") and acc["result"]:
            aid = acc["result"][0]["id"]
        if not aid:
            z0 = api("GET", f"/zones?name={domain}")
            if z0.get("success") and z0["result"]:
                aid = z0["result"][0]["account"]["id"]
                print(f"Account id derived from zone {domain}: {aid}", flush=True)
        if not aid:
            print("CF API: no account access - check token perms", flush=True)
            return None
        # 2. create tunnel (remote-managed); reuse if name exists
        t = api("POST", f"/accounts/{aid}/cfd_tunnel", {"name": tunnel_name, "config_src": "cloudflare"})
        tid = None
        if t.get("success"):
            tid = t["result"]["id"]
        else:
            errs = str(t.get("errors"))
            print(f"create -> {errs[:120]} (will reuse existing)", flush=True)
            lst = api("GET", f"/accounts/{aid}/cfd_tunnel?is_deleted=false")
            for cand in (lst.get("result") or []):
                if cand.get("name") == tunnel_name:
                    tid = cand["id"]
                    break
            if not tid:
                print(f"No tunnel named {tunnel_name} found", flush=True)
                return None
        tok = None
        ttok = api("GET", f"/accounts/{aid}/cfd_tunnel/{tid}/token")
        if ttok.get("success"):
            tok = ttok["result"]
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
    # HuggingFace Spaces are also supported as a model source (pull GGUF from the space).
    # Detect "/spaces/" early so we can strip it and set repo_type for the HF download.
    MODEL_REPO_TYPE = "model"
    if "/spaces/" in MODEL.lower():
        MODEL_REPO_TYPE = "space"
        MODEL = MODEL.split("/spaces/", 1)[1]
    elif MODEL.lower().startswith("spaces/"):
        MODEL_REPO_TYPE = "space"
        MODEL = MODEL[len("spaces/"):]
    # Accept either "user/repo" or a full HuggingFace URL (strip scheme/host/query)
    for _p in ("https://", "http://"):
        if MODEL.startswith(_p):
            MODEL = MODEL[len(_p):]
    if MODEL.startswith("huggingface.co/"):
        MODEL = MODEL[len("huggingface.co/"):]
    MODEL = MODEL.strip("/").split("?")[0].split("#")[0]
    # map old aliases to valid HF repo (fixes Kaggle artifact not found)
    if MODEL in ["qwen/qwen3-coder-30b-a3b", "qwen3-coder-30b-a3b", "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF", "lmstudio-community/Qwen3-Coder-30B-A3B-GGUF:Q4_K_M"]:
        print(f"Mapping alias {MODEL} -> {MODEL_DEFAULT}", flush=True)
        MODEL = MODEL_DEFAULT
    MODEL_QUANT = os.environ.get("MODEL_QUANT") or get_secret("MODEL_QUANT") or "Q4_K_M"
    served_model = None  # actual identifier LM Studio serves (populated after load)
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

    # Install & start sshd so the cloudflared ssh ingress (ssh://localhost:22) has a server.
    # The password above is useless without an actual SSH daemon running.
    try:
        if not shutil.which("sshd"):
            run("apt-get update -qq && apt-get install -y openssh-server || true")
        run("mkdir -p /run/sshd")
        run("ssh-keygen -A")  # generate host keys if missing
        # Allow root password login (we set a root password above specifically for SSH access)
        dropin = "/etc/ssh/sshd_config.d/99-cloudflare-ssh.conf"
        with open(dropin, "w") as f:
            f.write("PermitRootLogin yes\nPasswordAuthentication yes\n")
        run("/usr/sbin/sshd -t && echo sshd_config OK")  # validate before (re)start
        run("pkill -x sshd 2>/dev/null || true")
        time.sleep(1)
        run("/usr/sbin/sshd")
        print("sshd installed and listening on :22", flush=True)
    except Exception as e:
        print(f"sshd setup skipped: {e}", flush=True)

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
                        m[MODEL] = {"name": MODEL, "tool_call": True, "reasoning": True, "limit": {"context": 49152, "output": 32768}}
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
    ensure_tool("opencode", "npm install -g opencode-ai || bun install -g opencode-ai || curl -fsSL https://opencode.ai/install | bash || true; export PATH=\"$HOME/.opencode/bin:$HOME/.local/bin:$PATH\"")
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
        run("bash -c 'export PATH=\"$HOME/.local/share/fnm:$PATH\"; eval \"$(fnm env 2>/dev/null)\" || true; export NVM_DIR=\"$HOME/.nvm\"; [ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\"; nvm use 22 2>/dev/null || true; node --version; curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/v1.20.0/scripts/install.sh | bash' || curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/v1.20.0/scripts/install.sh | bash")

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
        # HF Spaces are pulled directly via huggingface_hub with --repo-type space
        rt_flag = "--repo-type space " if MODEL_REPO_TYPE == "space" else ""
        # try base repo id first, then full, then direct HuggingFace download into LM Studio models dir (auto-discovered)
        if not run(f'lms get "{base}" -y'):
            if not run(f'lms get "{base}"'):
                print("lms get failed -> direct HF download into ~/.lmstudio/models ...", flush=True)
                # Quantized GGUF is the norm; allow overriding via MODEL_QUANT (default Q4_K_M).
                quant = MODEL_QUANT
                dest = os.path.expanduser(f"~/.lmstudio/models/{base}")
                os.makedirs(dest, exist_ok=True)
                run(f'pip install -q -U "huggingface_hub[cli]" >/dev/null 2>&1 || true; (hf download "{base}" {rt_flag}--include "*{quant}*" --local-dir "{dest}" </dev/null || huggingface-cli download "{base}" {rt_flag}--include "*{quant}*" --local-dir "{dest}" </dev/null) || true')
                import glob as _g
                ggufs = _g.glob(os.path.join(dest, "**", "*.gguf"), recursive=True) or _g.glob(os.path.join(dest, "*.gguf"))
                if not ggufs and quant:
                    # quant filter matched nothing (repo ships a different quant) -> grab all GGUFs
                    print(f"quant '*{quant}*' not found in {base}, downloading all GGUF files", flush=True)
                    run(f'pip install -q -U "huggingface_hub[cli]" >/dev/null 2>&1 || true; (hf download "{base}" {rt_flag}--include "*.gguf" --local-dir "{dest}" </dev/null || huggingface-cli download "{base}" {rt_flag}--include "*.gguf" --local-dir "{dest}" </dev/null) || true')
                    ggufs = _g.glob(os.path.join(dest, "**", "*.gguf"), recursive=True) or _g.glob(os.path.join(dest, "*.gguf"))
                if ggufs:
                    print(f"Downloaded {len(ggufs)} GGUF file(s) -> {dest}", flush=True)
                    os.environ["GGUF_PATH"] = max(ggufs, key=os.path.getsize)  # for llama.cpp fallback
                    run("lms daemon up || true")
                    # trigger scan then load by discovered model key
                    ls = subprocess.run("lms ls", shell=True, capture_output=True, text=True).stdout
                    key = None
                    g0 = max(ggufs, key=os.path.getsize)
                    g0base = os.path.basename(g0).replace(".gguf", "").lower()
                    for line in ls.splitlines():
                        if g0base in line.lower() or "qwen3-coder" in line.lower():
                            key = line.split()[0]
                            break
                    if key:
                        print(f"Loading discovered key: {key}", flush=True)
                        # CUDA OOM root cause: GGUF metadata default ctx can be 262k -> KV cache 25GB+
                        # Force explicit safe contexts first (dual T4 = ~30GB usable total)
                        loaded = False
                        # 64K OOMs on dual T4 (unable to allocate CUDA0 buffer); 48K is the safe ceiling.
                        for ctx in ["--context-length 49152", "--context-length 32768", "--context-length 16384", ""]:
                            suffix = f" (ctx{ctx.split()[-1]})" if ctx else " (model default - last resort)"
                            if run(f'lms load "{key}" -y --gpu max {ctx}'):
                                loaded = True
                                break
                            print(f"load failed {suffix} - trying smaller...", flush=True)
                        if not loaded:
                            # llmster headless often fails multi-GPU split (bug tracker #1360/#1365:
                            # "tensor split 0 -> disabling GPU", or all weights on CUDA0 -> OOM)
                            os.environ["LMS_LOAD_FAILED"] = "1"
                            print("All lms load attempts OOMed - will fall back to llama-runner engine (pwilkin/llama-runner, proper multi-GPU split)", flush=True)
                    else:
                        g0 = max(ggufs, key=os.path.getsize)
                        print(f"Key not found via lms ls; relying on JIT auto-load of {os.path.basename(g0)}", flush=True)
                else:
                    print("HF direct download failed too - server will start with no model; set MODEL secret to a valid HF GGUF repo", flush=True)
    else:
        print("lms not found, skipping model pull (install LM Studio first)", flush=True)

    # 2. Start services
    # lms/openchamber are daemon-managed: their CLI returns immediately (code 0) while daemon serves.
    # Only cloudflared tunnels are long-lived processes worth monitoring.
    tunnels = []

    def deploy_llama_runner():
        """pwilkin/llama-runner: wraps raw llama-server (dual-T4 split), emulates LM Studio /v1 on 1234."""
        print("=== Engine: pwilkin/llama-runner + llama-server (explicit dual-T4 split) ===", flush=True)
        gguf = os.environ.get("GGUF_PATH")
        if not gguf:
            print("No GGUF path known - cannot start engine", flush=True)
            return []
        bindir = os.path.expanduser("~/.local/bin")
        os.makedirs(bindir, exist_ok=True)
        srv = os.path.join(bindir, "llama-server")
        if not os.path.exists(srv):
            print("Fetching prebuilt llama-server (linux vulkan)...", flush=True)
            try:
                asset = None
                import json as _json
                import urllib.request as _u
                rel = _json.load(_u.urlopen("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", timeout=30))
                asset = next((a for a in rel.get("assets", []) if "ubuntu" in a["name"] and "vulkan" in a["name"] and a["name"].endswith(".zip")), None)
                if asset:
                    run("apt-get install -y unzip libvulkan1 >/dev/null 2>&1 || true")
                    url = asset["browser_download_url"]
                    # extract WHOLE dist (llama-server needs libllama.so / libggml-*.so next to it)
                    dist = os.path.join(bindir, "llama-dist")
                    run("rm -rf /tmp/lc " + dist + " && mkdir -p /tmp/lc " + dist +
                        " && curl -L '" + url + "' -o /tmp/lc.zip && cd /tmp/lc && unzip -o -q /tmp/lc.zip" +
                        " && cp -r /tmp/lc/* " + dist + "/ || true")
                    if os.path.exists(dist):
                        found = None
                        for root, _, files in os.walk(dist):
                            if "llama-server" in files:
                                found = os.path.join(root, "llama-server")
                                break
                        if found:
                            import shutil as _sh
                            _sh.copy2(found, srv)
                            os.chmod(srv, 0o755)
                            libdir = os.path.dirname(found)
                            os.environ["LD_LIBRARY_PATH"] = libdir + ":" + os.environ.get("LD_LIBRARY_PATH", "")
            except Exception as e:
                print(f"prebuilt fetch failed: {e}", flush=True)
        if not os.path.exists(srv):
            print("Prebuilt unavailable -> building llama.cpp with CUDA from source (~10 min)...", flush=True)
            run("rm -rf /tmp/llama.cpp && git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp || true")
            run("cd /tmp/llama.cpp && cmake -B build -DGGML_CUDA=ON > /tmp/cmake.log 2>&1 && cmake --build build --config Release -j$(nproc) --target llama-server >> /tmp/cmake.log 2>&1 && cp build/bin/llama-server " + srv + " || tail -20 /tmp/cmake.log")
        if not os.path.exists(srv):
            print("llama-server unavailable", flush=True)
            return []
        # llama-runner install
        run("rm -rf /tmp/llama-runner && git clone --depth 1 https://github.com/pwilkin/llama-runner /tmp/llama-runner || true")
        run("pip install -q -r /tmp/llama-runner/requirements.txt --break-system-packages 2>/dev/null || pip install -q -r /tmp/llama-runner/requirements.txt || true")
        # config tuned for dual T4 + Qwen3-Coder-30B-A3B MoE
        cfg_dir = os.path.expanduser("~/.llama-runner")
        os.makedirs(cfg_dir, exist_ok=True)
        alias = "qwen3-coder"
        cfg = {
            "llama-runtimes": {"default": {"runtime": srv}},
            "models": {alias: {
                "model_path": gguf,
                "llama_cpp_runtime": "default",
                "parameters": {
                    "ctx_size": 49152,
                    "gpu_layers": 99,
                    "flash-attn": True,
                    "cache-type-k": "q8_0",
                    "cache-type-v": "q8_0",
                    "jinja": True,
                    "threads": 4,
                },
            }},
        }
        open(os.path.join(cfg_dir, "config.json"), "w").write(json.dumps(cfg, indent=2))
        print(f"Wrote {cfg_dir}/config.json (alias={alias}, ctx=49152, fa=on, kv=q8_0)", flush=True)
        run("fuser -k 1234/tcp 2>/dev/null || true")
        time.sleep(1)
        import json as _json
        procs = [run_bg("cd /tmp/llama-runner && python main.py --headless", "llama-runner:1234")]
        if wait_http("http://localhost:1234/v1/models", tries=36, delay=5, name="llama-runner :1234"):
            print(f"LLAMA-RUNNER UP: {os.path.basename(gguf)} split across both T4s", flush=True)
            try:
                pj = os.path.join(os.path.dirname(__file__), "..", "opencode.json")
                j = _json.load(open(pj))
                for prov in ["lmstudio-local", "lmstudio-tunneled"]:
                    m = j["provider"][prov].setdefault("models", {})
                    m.clear()
                    m[alias] = {"name": alias, "tool_call": True, "limit": {"context": 49152, "output": 32768}}
                open(pj, "w").write(_json.dumps(j, indent=2))
            except Exception:
                pass
        else:
            print("--- last 20 lines of llama-runner log ---", flush=True)
            try:
                print("\n".join(open("/tmp/llama-runner:1234.log").readlines()[-20:]), flush=True)
            except Exception:
                pass
        return procs

    def _json_loads_url(url):
        import json as _json
        import urllib.request as _u
        return _json.load(_u.urlopen(url, timeout=30))

    if os.environ.get("LMS_LOAD_FAILED") == "1":
        tunnels.extend(deploy_llama_runner())
        DOMAIN_SERVED = True
    else:
        DOMAIN_SERVED = False

    if not DOMAIN_SERVED and shutil.which("lms"):
        run_bg("lms server start --port 1234 --cors", "lmstudio:1234")
        if wait_http("http://localhost:1234/v1/models", name="LM Studio :1234"):
            # Correct opencode.json model id to the identifier LM Studio actually serves.
            # lms loads the GGUF under a cleaned id (e.g. qwen3-coder-30b-a3b-instruct),
            # not the HF repo name; otherwise OpenCode requests a model that 404s.
            try:
                import json as _json
                _mdl = _json_loads_url("http://localhost:1234/v1/models").get("data", [])
                _served = _mdl[0]["id"] if _mdl else None
                if _served:
                    pj = os.path.join(os.path.dirname(__file__), "..", "opencode.json")
                    j = _json.load(open(pj))
                    for prov in ["lmstudio-local", "lmstudio-tunneled"]:
                        m = j["provider"][prov].setdefault("models", {})
                        m.clear()
                        m[_served] = {"name": _served, "tool_call": True, "reasoning": True, "limit": {"context": 49152, "output": 32768}}
                    open(pj, "w").write(_json.dumps(j, indent=2))
                    served_model = _served
                    print(f"Patched opencode.json model id -> {_served}", flush=True)
            except Exception as e:
                print(f"opencode.json model-id patch skipped: {e}", flush=True)
        else:
            print("--- last 15 lines of LM Studio log ---", flush=True)
            try:
                print("\n".join(open("/tmp/lmstudio:1234.log").readlines()[-15:]), flush=True)
            except Exception:
                pass
    else:
        print("lms missing", flush=True)

    time.sleep(1)
    # Make the OpenCode config discoverable by ALL instances. OpenChamber launches its
    # own managed OpenCode with cwd=/root (and the web instance with cwd=/kaggle/working),
    # so neither ever sees this repo's opencode.json (OpenCode only searches cwd + ancestors).
    # Copying into ~/.config/opencode makes providers available everywhere.
    try:
        import json as _json, shutil as _shutil
        src = os.path.join(os.path.dirname(__file__), "..", "opencode.json")
        if os.path.exists(src):
            cfg = _json.load(open(src))
            # point the tunneled provider at the real domain (config ships with a placeholder)
            if "lmstudio-tunneled" in cfg.get("provider", {}):
                cfg["provider"]["lmstudio-tunneled"]["options"]["baseURL"] = f"https://ai.{DOMAIN}/v1"
            # Final safety net: set the model id to whatever LM Studio actually serves, so
            # OpenCode never requests a model id that 404s (covers lms + llama-runner paths).
            try:
                _models = _json_loads_url("http://localhost:1234/v1/models").get("data", [])
                _served = _models[0]["id"] if _models else None
                # only trust a real LLM (skip embedding-only servers, e.g. fresh installs serving just nomic-embed)
                if _served and "embed" not in _served.lower():
                    for prov in ["lmstudio-local", "lmstudio-tunneled"]:
                        m = cfg["provider"][prov].setdefault("models", {})
                        m.clear()
                        m[_served] = {"name": _served, "tool_call": True, "reasoning": True, "limit": {"context": 49152, "output": 32768}}
                    print(f"opencode model id corrected -> {_served}", flush=True)
                    served_model = _served
                    # local chat model available -> prefer it as default over free zen fallback
                    cfg["model"] = f"lmstudio-local/{_served}"
            except Exception as e:
                print(f"model-id correction skipped (LM Studio not reachable?): {e}", flush=True)
            # Default model: free opencode zen model so pickers always have a working default
            # even when LM Studio is still downloading / embedding-only.
            if not cfg.get("model"):
                cfg["model"] = "opencode/big-pickle"
            dst_dir = os.path.expanduser("~/.config/opencode")
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, "opencode.jsonc")
            _json.dump(cfg, open(dst, "w"), indent=2)
            print(f"Copied opencode config -> {dst} (tunneled baseURL https://ai.{DOMAIN}/v1)", flush=True)
    except Exception as e:
        print(f"opencode config copy skipped: {e}", flush=True)

    # Kill stale port holders only if port is held by non-opencode/openchamber process.
    # Previously blind fuser -k 2456/3000 killed the tunnel origin mid-flight -> oc 502.
    def _kill_stale(port, expected):
        try:
            out = subprocess.run(f"ss -tlnp 2>/dev/null | grep ':{port} ' || netstat -tlnp 2>/dev/null | grep ':{port} '", shell=True, capture_output=True, text=True).stdout
            if out and expected not in out:
                print(f"Port {port} held by unexpected process, killing stale holder: {out.strip()[:120]}", flush=True)
                run(f"fuser -k {port}/tcp 2>/dev/null || true")
                time.sleep(1)
            elif out:
                print(f"Port {port} already held by {expected}, keeping", flush=True)
        except Exception:
            pass

    _kill_stale(2456, "opencode")
    _kill_stale(3000, "node")

    if shutil.which("opencode"):
        os.environ["OPENCODE_SERVER_PASSWORD"] = PASSWORD  # secure opencode web (fixes unsecured warning)
        # If already listening, reuse ONLY if current password works (stale instance with
        # old OPENCODE_SERVER_PASSWORD would 401 the new password -> kill and restart).
        def _pw_ok(port):
            chk = subprocess.run(f"curl -s -m 4 -o /dev/null -w '%{{http_code}}' -u 'opencode:{PASSWORD}' http://localhost:{port}/", shell=True, capture_output=True, text=True)
            return chk.stdout.strip() == "200"
        reused = False
        if wait_http("http://localhost:2456", tries=2, name="Opencode :2456 (existing)"):
            if _pw_ok(2456):
                print("Existing Opencode :2456 accepts current password - reusing", flush=True)
                reused = True
            else:
                print("Existing Opencode :2456 has stale password -> killing for restart with current PASSWORD", flush=True)
                run("fuser -k 2456/tcp 2>/dev/null || true")
                time.sleep(2)
        if not reused:
            run_bg(f'opencode web --port 2456 --hostname 0.0.0.0', "opencode:2456")
            wait_http("http://localhost:2456", tries=6, name="Opencode :2456")
    else:
        print("opencode missing, install via: curl -fsSL https://opencode.ai/install | bash", flush=True)

    if shutil.which("openchamber"):
        if not wait_http("http://localhost:3000", tries=2, name="OpenChamber :3000 (existing)"):
            run_bg('openchamber --ui-password "$OPENCHAMBER_UI_PASSWORD"', "openchamber:3000")
            wait_http("http://localhost:3000", tries=6, name="OpenChamber :3000")
    else:
        print("openchamber missing", flush=True)

    time.sleep(1)

    # 3. Cloudflare tunnel - 4 ingress: ai, oc, chamber, ssh
    # For --token mode cloudflared ignores credentials-file, but for cert mode it must not point to missing file.
    config_path = os.path.expanduser("~/.cloudflared/config.yml")
    # Only use credentials-file if the file actually exists; otherwise omit it (token mode)
    from pathlib import Path as _Path
    token_mode = bool(TUNNEL_TOKEN and TUNNEL_TOKEN.startswith("eyJ") and TUNNEL_TOKEN.count(".") >= 2)
    # In token mode, credentials-file is ignored; in cert mode try to find real file
    cred_path = os.path.expanduser(f"~/.cloudflared/{TUNNEL}.json")
    real_cred = None
    for cand in [cred_path, os.path.expanduser(f"~/.cloudflared/{TUNNEL}.json"), f"/root/.cloudflared/{TUNNEL}.json"]:
        if _Path(cand).exists():
            real_cred = cand
            break
    # If no real cred file and not in token mode, try to find any json for this tunnel ID
    if not real_cred and not token_mode:
        import glob as _glob_tmp
        for jf in _glob_tmp.glob(os.path.expanduser("~/.cloudflared/*.json")):
            real_cred = jf
            break
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    # always ensure config reflects current DOMAIN/TUNNEL (fix stale ~/.cloudflared/config.yml)
    if real_cred or not token_mode:
        cfg = f"""tunnel: {TUNNEL}
credentials-file: {real_cred or cred_path}
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
    else:
        # Token mode: omit credentials-file so `cloudflared tunnel list` error is avoided and tunnel uses --token
        cfg = f"""tunnel: {TUNNEL}
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
    print(f"Wrote {config_path} ({'token mode, no credentials-file' if token_mode and not real_cred else f'credentials-file={real_cred or cred_path}'})", flush=True)

    if shutil.which("cloudflared"):
        # cfut_ is API token, eyJ... is JWT tunnel token
        is_jwt = TUNNEL_TOKEN and TUNNEL_TOKEN.startswith("eyJ") and TUNNEL_TOKEN.count(".") >= 2
        if TUNNEL_TOKEN and is_jwt:
            print(f"Using TUNNEL_TOKEN (Zero Trust JWT) for {TUNNEL} ...", flush=True)
            tunnels.append(run_bg(f"cloudflared tunnel run --token {TUNNEL_TOKEN}", "cloudflared"))
        elif TUNNEL_TOKEN and TUNNEL_TOKEN.startswith("cfut_"):
            # cfut_ = API token. Headless Kaggle has no cert.pem for `tunnel create`.
            # Use Cloudflare REST API to create named tunnel + ingress + DNS, then run with returned JWT.
            jwt = None
            tried = []
            for cand in [CF_TOKEN, TUNNEL_TOKEN]:
                # dedupe on the full token (truncated strings never matched -> double API call)
                if cand and cand not in tried:
                    tried.append(cand)
                    print(f"Trying CF API with token {cand[:12]}...", flush=True)
                    jwt = cf_api_named_tunnel(cand, DOMAIN, TUNNEL)
                    if jwt:
                        break
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

    # ---- Clean connection summary (printed in Python) ----
    ui_pw = PASSWORD if PASSWORD != "changeme" else "(unset - set OPENCHAMBER_UI_PASSWORD)"
    ssh_pw = SSH_PASSWORD if (SSH_PASSWORD and SSH_PASSWORD.strip()) else ui_pw
    model_line = served_model or MODEL
    bar = "=" * 64
    print("\n" + bar)
    print("  HOST STACK READY")
    print(bar)
    print(f"  Model (HF)     : {MODEL}")
    print(f"  Quant          : {MODEL_QUANT}  (override via MODEL_QUANT)")
    print(f"  Model (served) : {model_line}")
    print(f"  Context / Out  : 48K / 32K tokens  (64K OOMs on dual T4)")
    print("-" * 64)
    print("  CONNECT")
    print(f"   OpenCode Web  : https://oc.{DOMAIN}   (user: opencode  password: {ui_pw})")
    print(f"   OpenChamber   : https://chamber.{DOMAIN}  (password: {ui_pw})")
    print(f"   AI endpoint   : https://ai.{DOMAIN}/v1")
    print(f"   SSH           : ssh root@ssh.{DOMAIN}   (password: {ssh_pw})")
    print(f"   SSH (1-liner) : ssh-keygen -R ssh.{DOMAIN} && ssh -o StrictHostKeyChecking=no root@ssh.{DOMAIN}")
    print(f"   Local         : opencode :2456 (auth opencode:{ui_pw}) | openchamber :3000 | lmstudio :1234")
    print("-" * 64)
    print(f"  LOGS: service logs in /tmp/<name>.log (e.g. /tmp/lmstudio:1234.log, /tmp/opencode:2456.log)")
    print(bar + "\n")

    print("All services started. Keepalive monitor running (checks every 30s)... Ctrl+C to stop.", flush=True)
    SERVICES = [
        ("LM Studio :1234", "http://localhost:1234/v1/models", "lms server start --port 1234 --cors", 1234),
        ("Opencode :2456", "http://localhost:2456", "opencode web --port 2456 --hostname 0.0.0.0", 2456),
        ("OpenChamber :3000", "http://localhost:3000", 'openchamber --ui-password "$OPENCHAMBER_UI_PASSWORD"', 3000),
    ]
    last = {}
    down_count = {}
    tick = 0
    try:
        while True:
            time.sleep(30)
            tick += 1
            # port listeners
            r = subprocess.run(
                "ss -tlnp 2>/dev/null | grep -E ':(1234|2456|3000|22)\\b' || netstat -tlnp 2>/dev/null | grep -E ':(1234|2456|3000|22)\\b'",
                shell=True, capture_output=True, text=True)
            listeners = [ln for ln in r.stdout.strip().splitlines() if ln]
            status = []
            for nm, url, cmd, port in SERVICES:
                c = subprocess.run(f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' {url}", shell=True, capture_output=True, text=True).stdout.strip()
                ok = c.startswith("2") or c in ("401", "403")  # 401 = up + auth required (opencode)
                if last.get(nm) != ok:
                    tag = "UP (auth)" if c in ("401", "403") else ("UP" if ok else "DOWN")
                    print(f"[{time.strftime('%H:%M:%S')}] {nm}: {tag} (http {c})", flush=True)
                last[nm] = ok
                status.append(f"{nm.split(' :')[1].strip(':')}={'OK' if ok else 'DOWN'}")
                # auto-restart service after 2 consecutive DOWNs - verify port holder first to avoid killing tunnel origin unnecessarily
                if not ok:
                    down_count[nm] = down_count.get(nm, 0) + 1
                    if down_count[nm] >= 2:
                        # Check who holds the port before killing
                        holder = subprocess.run(f"ss -tlnp 2>/dev/null | grep ':{port} ' || netstat -tlnp 2>/dev/null | grep ':{port} '", shell=True, capture_output=True, text=True).stdout
                        # Only kill if not the expected service (prevents tunnel 502 blip when service is already restarting)
                        expected_map = {2456: ("opencode",), 3000: ("node", "openchamber"), 1234: ("lmstudio", "llmster")}
                        expected = expected_map.get(port, ())
                        if holder and expected and any(e in holder for e in expected):
                            print(f"{nm} DOWN x{down_count[nm]} but port {port} still held by {expected}, not killing", flush=True)
                        else:
                            print(f"{nm} DOWN x{down_count[nm]} -> restarting... (holder: {holder.strip()[:80] or 'none'})", flush=True)
                            run(f"fuser -k {port}/tcp 2>/dev/null || true")
                            time.sleep(1)
                            run_bg(cmd, f"{nm.split(' :')[1].strip(':')}-restart")
                        down_count[nm] = 0
                else:
                    down_count[nm] = 0
            # restart dead tunnels
            for i, p in enumerate(tunnels):
                if p.poll() is not None:
                    print(f"TUNNEL DIED: {p.args} code={p.returncode} -> restarting...", flush=True)
                    tunnels[i] = run_bg(p.args, f"cloudflared-restart-{i}")
            if tick % 6 == 0:
                print(f"[{time.strftime('%H:%M:%S')}] keepalive #{tick}: {' '.join(status)} | listening: {len(listeners)} ports", flush=True)
    except KeyboardInterrupt:
        print("Stopping...", flush=True)
        for p in tunnels:
            p.terminate()

if __name__ == "__main__":
    main()
