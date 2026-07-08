#!/usr/bin/env bash
# Runs on the EC2 instance, invoked by SSM from the GitHub Actions workflow.
#
# Refreshes .env from SSM Parameter Store, authenticates Docker against ECR,
# pulls the latest image, and (re)starts the requested service.
#
# The docker-compose.yml is shipped by the workflow (base64'd via SSM) and
# written to /opt/nautilus-lab/ before this script runs.

set -euo pipefail

SERVICE="${1:-orderbook-live}"
REGION="${AWS_REGION:-eu-west-1}"
ECR_REGISTRY="${ECR_REGISTRY:?ECR_REGISTRY must be exported by the caller}"
ECR_REPOSITORY="${ECR_REPOSITORY:?ECR_REPOSITORY must be exported by the caller}"

cd /opt/nautilus-lab

# ---- Refresh .env from Parameter Store ------------------------------------
# Allows rotating Telegram tokens without re-running user-data.
cat > .env <<EOF
TELEGRAM_BOT_TOKEN=$(aws ssm get-parameter --name /nautilus-lab/telegram/bot-token --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
TELEGRAM_CHAT_IDS=$(aws ssm get-parameter --name /nautilus-lab/telegram/chat-ids --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
ECR_REGISTRY=$ECR_REGISTRY
ECR_REPOSITORY=$ECR_REPOSITORY
EOF
chmod 600 .env

# ---- Authenticate Docker against ECR --------------------------------------
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ECR_REGISTRY"

# ---- Pull image and (re)start the service --------------------------------
docker compose pull "$SERVICE"
docker compose up -d "$SERVICE"

# ---- Cleanup dangling images ---------------------------------------------
docker system prune -f

echo "deploy ok: service=$SERVICE image=$ECR_REGISTRY/$ECR_REPOSITORY:latest"
