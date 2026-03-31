Run through the pre-deploy checklist before deploying CloudiQS Engine to AWS.

Read `docs/PRE-DEPLOY.md` first to get the full list of requirements.

Then work through each section:

## 1. AWS Secrets Manager — verify all secrets exist

For each secret path in PRE-DEPLOY.md, run via SSM (or ask the user to run in CloudShell):

```bash
aws secretsmanager get-secret-value \
  --secret-id "cloudiqs/cloudiqs-engine/SECRET_NAME" \
  --region eu-west-1 \
  --query SecretString \
  --output text 2>&1 | head -c 20
```

Report each secret as: ✅ exists | ❌ missing | ⚠️ returns DUMMY

## 2. AWS resources — verify they exist

Check:
- S3 bucket: `aws s3 ls s3://cloudiqs-engine-uploads-736956442878 --region eu-west-1`
- IAM role: `aws iam get-role --role-name cloudiqs-engine-role`
- IAM role: `aws iam get-role --role-name github-deploy`
- OIDC provider: `aws iam list-open-id-connect-providers | grep token.actions`
- EC2 instance: `aws ec2 describe-instances --instance-ids $EC2_INSTANCE_ID --region eu-west-1`
- SSM connectivity: send a test command `echo "ssm ok"` and verify it returns

## 3. External services — verify connectivity

Check that the bridge can reach these (run after bridge is deployed):
- HubSpot: `curl https://api.hubapi.com/crm/v3/objects/contacts?limit=1 -H "Authorization: Bearer {key}"`
- Instantly: `curl https://api.instantly.ai/api/v1/campaign/list -H "Authorization: Bearer {key}"`
- Teams: `curl -X POST {webhook_url} -H "Content-Type: application/json" -d '{"text":"pre-deploy test"}'`

## 4. GitHub Actions — verify secrets are set

Check that these GitHub repo secrets exist (Settings > Secrets):
- `AWS_DEPLOY_ROLE_ARN`
- `EC2_INSTANCE_ID`

## 5. Summary verdict

Report as a table:

| Category | Item | Status |
|----------|------|--------|
| Secrets | hubspot/api-key | ✅ / ❌ |
| ... | ... | ... |

Final verdict: **READY TO DEPLOY** (all green) or **BLOCKED** (list what is missing with fix steps).

Do not deploy. Just report the checklist results. The user will trigger the deploy manually via GitHub Actions workflow_dispatch.
