# Host Stack - LM Studio + Opencode + OpenChamber + SSH via Cloudflare (non-Docker)

Single `cloudflared` tunnel with 4 subdomains, Python/non-Docker, host = your dual T4.

Fork of https://github.com/openchamber/openchamber extended for self-hosting.

## Subdomains (one tunnel)
- `ai.yourdomain.com` -> LM Studio `http://localhost:1234` (+ `/v1`)
- `oc.yourdomain.com` -> Opencode Web `http://localhost:4096`
- `chamber.yourdomain.com` -> OpenChamber `http://localhost:3000`
- `ssh.yourdomain.com` -> SSH `ssh://localhost:22`

All via `cloudflared` named tunnel `t4host`.

## Quick start (host = T4, non-Docker)

```bash
# 1. LM Studio + Opencode + OpenChamber (binaries)
curl -fsSL https://lmstudio.ai/install.sh | bash
# opencode
curl -fsSL https://opencode.ai/install | bash
# openchamber
curl -fsSL https://raw.githubusercontent.com/openchamber/openchamber/main/scripts/install.sh | bash

# 2. Cloudflare named tunnel (needs domain on Cloudflare)
cloudflared tunnel login
cloudflared tunnel create t4host
cloudflared tunnel route dns t4host ai.yourdomain.com
cloudflared tunnel route dns t4host oc.yourdomain.com
cloudflared tunnel route dns t4host chamber.yourdomain.com
cloudflared tunnel route dns t4host ssh.yourdomain.com
cp cloudflared/config.yml ~/.cloudflared/config.yml
# edit YOURDOMAIN and tunnel ID
cloudflared tunnel run t4host

# 3. Run host stack
./scripts/host.sh
```

## Files
- `cloudflared/config.yml` - template with 4 ingress rules
- `scripts/host.sh` - starts lms + opencode web + openchamber + cloudflared
- `opencode.json` - provider points to local LM Studio (ai subdomain for remote)
- `scripts/setup-tunnel.sh` - creates + routes DNS for 4 hostnames

See `scripts/host.sh` for systemd alternative.
