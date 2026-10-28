#!/usr/bin/env bash
# Start Janamdatri-AI with a free public HTTPS link (Cloudflare quick tunnel) and print the link.
set -uo pipefail
cd "$(dirname "$0")"
F=(-f docker-compose.yml -f docker-compose.tunnel.yml)
docker compose "${F[@]}" up -d || { echo "Could not start: is Docker running?"; exit 1; }
echo "Waiting for the link (up to 90 seconds)..."
url=""
for _ in $(seq 45); do
  sleep 2
  url=$(docker compose "${F[@]}" logs tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [ -n "$url" ] && break
done
[ -z "$url" ] && { echo "No link yet: docker compose ${F[*]} logs tunnel"; exit 1; }
echo
echo "  Public link:  $url"
echo "  SMS inbox (OTPs, only on this computer):  http://localhost:8025"
echo "To stop sharing: docker compose ${F[*]} stop tunnel"
