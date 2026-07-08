#!/usr/bin/env bash
# EC2 user-data. Runs once at first boot of the trading bot instance.
#
# Required IAM permissions on the instance role:
#   - ecr:GetAuthorizationToken, ecr:BatchCheckLayerAvailability,
#     ecr:GetDownloadUrlForLayer, ecr:BatchGetImage, ecr:DescribeRepositories
#   - ssm:GetParameter on /nautilus-lab/telegram/*
#   - s3:GetObject on s3://<bucket>/nautilus-lab/*
#   - AmazonSSMManagedInstanceCore (for SSM agent)
#
# Required SSM Parameter Store entries (SecureString):
#   /nautilus-lab/telegram/bot-token
#   /nautilus-lab/telegram/chat-ids

set -euo pipefail
exec > >(tee /var/log/user-data.log) 2>&1

REGION="${AWS_REGION:-eu-west-1}"

# ---- Base packages --------------------------------------------------------
dnf update -y
dnf install -y docker git jq
systemctl enable --now docker
usermod -a -G docker ec2-user

# AWS CLI v2 (Amazon Linux 2023 ships with v1 by default in older AMIs).
if ! command -v aws >/dev/null 2>&1 || aws --version 2>&1 | grep -q 'aws-cli/1'; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
  unzip -q /tmp/awscliv2.zip -d /tmp/
  /tmp/aws/install
  rm -rf /tmp/awscliv2.zip /tmp/aws
fi

# ---- App directory ---------------------------------------------------------
mkdir -p /opt/nautilus-lab
cd /opt/nautilus-lab
mkdir -p logs artifacts

# .env gets refreshed by deploy.sh on every deploy, but write a placeholder
# here so docker compose has something to read on first boot.
cat > .env <<EOF
TELEGRAM_BOT_TOKEN=$(aws ssm get-parameter --name /nautilus-lab/telegram/bot-token --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
TELEGRAM_CHAT_IDS=$(aws ssm get-parameter --name /nautilus-lab/telegram/chat-ids --with-decryption --query 'Parameter.Value' --output text --region "$REGION")
EOF
chmod 600 .env

# Copy deploy.sh into place and mark executable.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/deploy.sh" ]; then
  cp "$SCRIPT_DIR/deploy.sh" /opt/nautilus-lab/deploy.sh
  chmod +x /opt/nautilus-lab/deploy.sh
fi

echo "user-data complete at $(date -u +%FT%TZ)"
