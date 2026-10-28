#!/usr/bin/env bash
# Pull the latest code from GitHub and restart (data in the database and uploads is kept).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
