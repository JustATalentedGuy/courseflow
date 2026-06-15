#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git jq debian-keyring debian-archive-keyring apt-transport-https

curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh
sudo usermod -aG docker ubuntu

curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update
sudo apt-get install -y caddy

if ! sudo swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

ARCH="$(dpkg --print-architecture)"
CW_ARCH="amd64"
if [[ "$ARCH" == "arm64" ]]; then
  CW_ARCH="arm64"
fi
wget -q "https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/${CW_ARCH}/latest/amazon-cloudwatch-agent.deb" -O /tmp/cloudwatch-agent.deb
sudo dpkg -i /tmp/cloudwatch-agent.deb

sudo systemctl enable docker caddy
