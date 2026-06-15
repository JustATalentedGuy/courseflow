#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <source-env> <domain> <s3-bucket>" >&2
  exit 2
fi

SOURCE_ENV="$1"
DOMAIN="$2"
S3_BUCKET="$3"
APP_DIR="${COURSEFLOW_APP_DIR:-$HOME/courseflow}"

if [[ ! -f "$SOURCE_ENV" ]]; then
  echo "Source environment file not found: $SOURCE_ENV" >&2
  exit 2
fi

read_env() {
  local key="$1"
  local line
  line="$(grep -m1 -E "^${key}=" "$SOURCE_ENV" || true)"
  printf '%s' "${line#*=}"
}

umask 077
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
SECRET_KEY="$(openssl rand -hex 32)"
GROQ_API_KEY="$(read_env GROQ_API_KEY)"
ANTHROPIC_API_KEY="$(read_env ANTHROPIC_API_KEY)"
CLOUDFLARE_ACCOUNT_ID="$(read_env CLOUDFLARE_ACCOUNT_ID)"
CLOUDFLARE_API_TOKEN="$(read_env CLOUDFLARE_API_TOKEN)"

if [[ -z "$GROQ_API_KEY" ]]; then
  echo "GROQ_API_KEY is missing from the source environment." >&2
  exit 2
fi

cat >"$APP_DIR/.env.production" <<EOF
POSTGRES_DB=courseflow
POSTGRES_USER=courseflow
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
SECRET_KEY=$SECRET_KEY
GROQ_API_KEY=$GROQ_API_KEY
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
CLOUDFLARE_ACCOUNT_ID=$CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN
CLOUDFLARE_IMAGE_MODEL=@cf/black-forest-labs/flux-2-klein-4b
CLOUDFLARE_DAILY_NEURON_BUDGET=8000
COURSEFLOW_DOMAIN=$DOMAIN
CORS_ORIGINS=https://$DOMAIN
VITE_API_URL=https://$DOMAIN
STORAGE_BACKEND=s3
AWS_S3_BUCKET=$S3_BUCKET
AWS_REGION=ap-south-1
GROQ_BATCH_ENABLED=false
GROQ_DAILY_RESERVE_PERCENT=0
ENVIRONMENT=production
EOF
chmod 600 "$APP_DIR/.env.production"

sed "s/courseflow\\.example\\.duckdns\\.org/$DOMAIN/g" \
  "$APP_DIR/deploy/Caddyfile.example" \
  | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy

sudo install -m 0644 \
  "$APP_DIR/deploy/cloudwatch-agent.json" \
  /opt/aws/amazon-cloudwatch-agent/etc/courseflow.json
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/courseflow.json

echo "Production environment, Caddy, and CloudWatch Agent configured for $DOMAIN."
