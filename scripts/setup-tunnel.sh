#!/usr/bin/env bash
set -e
DOMAIN=${1:?Usage: $0 yourdomain.com [tunnel-name]}
TUNNEL=${2:-t4host}
echo "Creating tunnel $TUNNEL for $DOMAIN..."
cloudflared tunnel login
cloudflared tunnel create $TUNNEL
for h in ai oc chamber ssh; do
  cloudflared tunnel route dns $TUNNEL $h.$DOMAIN
done
echo "Done. Edit cloudflared/config.yml TUNNEL_ID and domain, then: cloudflared tunnel run $TUNNEL"
