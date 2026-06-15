#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/courseflow"
git fetch origin main
git checkout main
git pull --ff-only origin main

docker compose --env-file .env.production -f docker-compose.production.yml build
docker compose --env-file .env.production -f docker-compose.production.yml run --rm migrate
docker compose --env-file .env.production -f docker-compose.production.yml up -d --remove-orphans
docker image prune -f
