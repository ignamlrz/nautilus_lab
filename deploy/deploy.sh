#!/usr/bin/env bash
# Runs on the EC2 instance (via SSM) or locally for testing.
#
# Local testing (default):
#   - Reads .env from the current directory (copy .env.example to .env first)
#   - Does ECR login + docker compose pull if ECR_REGISTRY is set in .env
#
# EC2 mode (set by the workflow):
#   REFRESH_ENV=true ./deploy.sh orderbook-live
#   - Refreshes .env from SSM Parameter Store

set -euo pipefail

SERVICE="${1:-orderbook-live}"
REGION="${AWS_REGION:-eu-south-2}"
WORKDIR="${DEPLOY_DIR:-$(pwd)}"

cd "$WORKDIR"

# ---- .env handling ---------------------------------------------------------
if [ "${REFRESH_ENV:-false}" = "true" ]; then
    # EC2 mode: refresh from Parameter Store.
    cat > .env <<EOF
TELEGRAM_BOT_TOKEN=$(aws ssm get-parameter --name /nautilus-lab/telegram/bot-token --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
TELEGRAM_CHAT_IDS=$(aws ssm get-parameter --name /nautilus-lab/telegram/chat-ids --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
ECR_REGISTRY=${ECR_REGISTRY:-}
ECR_REPOSITORY=${ECR_REPOSITORY:-nautilus-lab}
EOF
    chmod 600 .env
elif [ ! -f .env ]; then
    echo "no .env found. copy .env.example to .env and fill in your values," >&2
    echo "or set REFRESH_ENV=true to fetch from Parameter Store (EC2 mode)." >&2
    exit 1
fi

# docker-compose reads .env automatically for variable substitution
# (e.g. ${ECR_REGISTRY}/${ECR_REPOSITORY}:latest). We just need it on disk.

# ---- ECR login -------------------------------------------------------------
# Skipped automatically if ECR_REGISTRY is not set, or set SKIP_ECR_LOGIN=true
# to bypass (useful when testing with a local image).
if [ -n "${ECR_REGISTRY:-}" ] && [ "${SKIP_ECR_LOGIN:-false}" != "true" ]; then
    if ! aws ecr get-login-password --region "$REGION" 2>/dev/null | \
         docker login --username AWS --password-stdin "$ECR_REGISTRY" 2>/dev/null; then
        echo "::warning::ECR login failed; continuing with whatever's already cached locally"
    fi
fi

# ---- Pull + (re)start ------------------------------------------------------
docker-compose pull "$SERVICE"
docker-compose up -d "$SERVICE"

# ---- Cleanup dangling images ---------------------------------------------
docker system prune -f

echo "deploy ok: service=$SERVICE"
