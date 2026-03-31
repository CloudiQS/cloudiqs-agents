# GitHub Setup Guide

## 1. Create the repo

```bash
# On github.com create: cloudiqs/engine (private)
# Then locally:
git clone git@github.com:cloudiqs/engine.git
cd engine
# Unzip v7 contents here
git add .
git commit -m "v7.0 foundation"
git push origin main
```

## 2. GitHub Secrets (Settings > Secrets and variables > Actions)

| Secret | Value | Where to get it |
|--------|-------|-----------------|
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::736956442878:role/github-deploy` | Create this role (see below) |
| `EC2_INSTANCE_ID` | `i-YOUR_INSTANCE_ID` | EC2 console → Instances → Instance ID |

The instance ID is a secret, not hardcoded, so you can change instances
without editing the workflow file.

## 3. Create the GitHub OIDC deploy role in AWS

This lets GitHub Actions authenticate to AWS without storing access keys.
Run in CloudShell on account 736956442878:

```bash
# Step 1: Create the OIDC provider (one-time)
aws iam create-open-id-connect-provider \
  --url "https://token.actions.githubusercontent.com" \
  --client-id-list "sts.amazonaws.com" \
  --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1"

# Step 2: Create the deploy role
cat > /tmp/trust-policy.json << 'TRUST'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::736956442878:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:cloudiqs/engine:*"
      }
    }
  }]
}
TRUST

aws iam create-role \
  --role-name github-deploy \
  --assume-role-policy-document file:///tmp/trust-policy.json

# Step 3: Attach permissions (SSM send-command + read instance status)
cat > /tmp/deploy-policy.json << 'POLICY'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    }
  ]
}
POLICY

aws iam put-role-policy \
  --role-name github-deploy \
  --policy-name deploy-via-ssm \
  --policy-document file:///tmp/deploy-policy.json

echo "Done. Role ARN: arn:aws:iam::736956442878:role/github-deploy"
```

## 4. Clone repo on the instance

First time only. Run via SSM:

```bash
cd /home/ubuntu
git clone https://github.com/cloudiqs/engine.git cloudiqs-engine
```

For private repos, use a deploy key:
1. Generate on instance: `ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""`
2. Add the public key to GitHub: repo Settings > Deploy keys
3. Configure git to use it:
```bash
cat > ~/.ssh/config << 'SSH'
Host github.com
  IdentityFile ~/.ssh/deploy_key
  IdentitiesOnly yes
SSH
```

## 5. Create the S3 uploads bucket

The bucket name is derived from stack name + account ID.
Run in CloudShell:

```bash
STACK_NAME="cloudiqs-engine"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3 mb "s3://${STACK_NAME}-uploads-${ACCOUNT}" --region eu-west-1
```

Then add S3 permissions to the instance role:

```bash
INSTANCE_ROLE="cloudiqs-engine-role"  # or whatever your CF created
aws iam put-role-policy --role-name "$INSTANCE_ROLE" --policy-name s3-uploads --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Effect\": \"Allow\",
    \"Action\": [\"s3:GetObject\",\"s3:PutObject\",\"s3:ListBucket\",\"s3:DeleteObject\"],
    \"Resource\": [
      \"arn:aws:s3:::${STACK_NAME}-uploads-${ACCOUNT}\",
      \"arn:aws:s3:::${STACK_NAME}-uploads-${ACCOUNT}/*\"
    ]
  }]
}"
```

## 6. Deploy

After setup, every push to main auto-deploys:

```bash
git add .
git commit -m "fix: ACE enum values"
git push origin main
# GitHub Actions runs -> SSM sends command -> instance pulls and deploys
```

Manual deploy (on instance via SSM):
```bash
cd /home/ubuntu/cloudiqs-engine
git pull origin main
STACK_NAME=cloudiqs-engine bash deploy.sh
```

## 7. Multiple environments

Change STACK_NAME for different environments:

```bash
# Production
STACK_NAME=cloudiqs-engine bash deploy.sh

# Staging
STACK_NAME=cloudiqs-staging bash deploy.sh

# Customer demo
STACK_NAME=cloudiqs-demo bash deploy.sh
```

Each gets its own S3 bucket, secrets prefix, and bridge container.
No name collisions.
