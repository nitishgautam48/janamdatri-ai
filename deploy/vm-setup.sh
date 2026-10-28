#!/usr/bin/env bash
# Run once on a fresh Ubuntu 22.04/24.04 VM (e.g. Azure B2s): installs Docker, git and a small swap file.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
if ! swapon --show | grep -q .; then   # 2 GB swap keeps a 4 GB VM stable while the image builds
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
echo
echo "Docker is installed. Run:  newgrp docker   (or log out and back in), then continue with step 6 of docs/DEPLOY_AZURE.md"
