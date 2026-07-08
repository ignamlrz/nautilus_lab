# Deploy to EC2 via GitHub Actions on CodeBuild

End-to-end flow: `git push` → GitHub Actions runner (hosted on CodeBuild) →
builds image → pushes to ECR → triggers SSM on EC2 → EC2 pulls image and
restarts `orderbook-live`.

```
┌────────────┐    ┌────────────────────┐    ┌─────────┐    ┌─────────────┐
│ git push   │───▶│ GitHub Actions on  │───▶│  ECR    │◀───│ EC2 (SSM)   │
│ to main    │    │ CodeBuild runner   │    │ (image) │    │ docker pull │
└────────────┘    └────────────────────┘    └─────────┘    └─────────────┘
                         │                                  ▲
                         └────── SSM send-command ──────────┘
```

The workflow ships the repo's `docker-compose.yml` to the EC2 instance on
each deploy (base64'd inside the SSM command), so changes to the compose
file at the repo root propagate automatically without S3, git credentials,
or extra IAM permissions.

## One-time AWS setup

### 1. ECR repository

```bash
aws ecr create-repository --repository-name nautilus-lab --region eu-west-1
# note the registry URI, e.g. 123456789012.dkr.ecr.eu-west-1.amazonaws.com
```

### 2. SSM Parameter Store (Telegram secrets, SecureString)

```bash
aws ssm put-parameter --name /nautilus-lab/telegram/bot-token \
  --value "<your-bot-token>" --type SecureString --region eu-west-1
aws ssm put-parameter --name /nautilus-lab/telegram/chat-ids \
  --value "<your-chat-id>" --type SecureString --region eu-west-1
```

### 3. GitHub OIDC role for the workflow

```bash
# OIDC provider (one per AWS account):
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34996809991c9597f
```

Trust policy for the role (`trust-policy.json`):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:<owner>/nautilus-lab:*" }
    }
  }]
}
```

Then create the role and attach an inline policy that grants ECR push
and SSM send-command on the EC2 instance:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart",
        "ecr:DescribeRepositories"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["ssm:SendCommand", "ssm:GetCommandInvocation"],
      "Resource": [
        "arn:aws:ssm:eu-west-1:<account-id>:document/AWS-RunShellScript",
        "arn:aws:ec2:eu-west-1:<account-id>:instance/<ec2-instance-id>"
      ]
    }
  ]
}
```

Save the role ARN as the GitHub secret `AWS_DEPLOY_ROLE_ARN`.

### 4. CodeBuild project (the "runner")

This is the AWS-side counterpart to `runs-on:` in the workflow.

```bash
# Service role for CodeBuild: needs ECR pull, logs, S3, GitHub OIDC.
# Easier to set this up in the Console:
#   CodeBuild → Create project
#     Project name: nautilus-deploy-runner
#     Source: GitHub → connect to ignamlrz/nautilus-lab
#     Environment image: aws/codebuild/standard:7.0
#     Service role: new role with the AWSCodeBuildDeveloperAccess
#       managed policy
#     (no buildspec needed — the placeholder buildspec.yml in the repo
#      satisfies the validation step; the actual logic is in the workflow)
```

Enable it as a GitHub Actions runner for your repo/org under
**CodeBuild → Settings → Action runner**. See the
[AWS docs](https://docs.aws.amazon.com/codebuild/latest/userguide/action-runner.html).

### 5. EC2 instance

- AMI: **Amazon Linux 2023** (or any AL2/AL2023 with SSM agent by default)
- Instance type: `t3.small` is fine for the live bot, `t3.medium` for backtests
- IAM instance profile with this inline policy:

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ],
        "Resource": "*"
      },
      {
        "Effect": "Allow",
        "Action": ["ssm:GetParameter"],
        "Resource": "arn:aws:ssm:eu-west-1:<account-id>:parameter/nautilus-lab/telegram/*"
      }
    ]
  }
  ```

  Plus `AmazonSSMManagedInstanceCore` so SSM can manage the instance.

- User data: paste the contents of [`ec2-userdata.sh`](ec2-userdata.sh).
  Either as launch-template user-data, or in the EC2 launch wizard under
  "Advanced details → User data".

- Tag the instance with `Name=nautilus-bot` (or whatever) so it's easy to find.

### 6. GitHub repo settings

**Variables** (repo → Settings → Secrets and variables → Actions → Variables):

| Name | Value |
|---|---|
| `AWS_REGION` | `eu-west-1` |
| `ECR_REPOSITORY` | `nautilus-lab` |

**Secrets**:

| Name | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | the role ARN from step 3 |
| `EC2_INSTANCE_ID` | `i-0abc123...` from step 5 |

## Daily workflow

```bash
git push origin main
# watch it: gh run watch
```

The workflow:

1. Builds the Docker image from `Dockerfile`
2. Pushes it to ECR (tagged with `latest` and the commit SHA)
3. SSMs the EC2 instance to:
   - Write the repo's `docker-compose.yml` to `/opt/nautilus-lab/`
   - Refresh `.env` from Parameter Store
   - Authenticate Docker against ECR
   - Pull the new image
   - Restart the configured service (default: `orderbook-live`)
   - Prune dangling images

## Manual deploy

You can also trigger a deploy from the Actions tab and optionally override
the service name (e.g. to deploy `data-tester-live` instead).

## Logs

- **App logs**: bind-mounted to `/opt/nautilus-lab/logs/orderbook-live/`
  on the EC2 instance. SSH in (`aws ssm start-session --target <id>`) or
  copy them out with SSM.
- **User-data log**: `/var/log/user-data.log` on the EC2 instance.
- **SSM command history**: Systems Manager → Run Command → Command history.
