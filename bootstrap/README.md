# bootstrap

One-time setup, run **locally by a human with admin** (`awslogin` →
`quang-admin` profile). Creates the things CI depends on:

- the **S3 state bucket** (`quang-tfstate-<account-id>`, versioned/encrypted/private, native locking)
- the **GitHub Actions OIDC provider**
- two IAM roles: **`quang-github-plan`** (read-only, PRs) and **`quang-github-apply`** (read/write, `main`)

This config's own state lives in S3 (key `bootstrap/terraform.tfstate`), migrated
there after the first apply created the bucket. On a brand-new account, run the
first `apply` with local state (comment out `backend.tf`), then add it back and
`terraform init -migrate-state`.

## Run

```sh
awslogin                        # aws sso login --sso-session quang
export AWS_PROFILE=quang-admin  # PowerShell: $env:AWS_PROFILE="quang-admin"
cd bootstrap
terraform init -backend-config="bucket=quang-tfstate-<account-id>" -backend-config="region=us-east-1"
terraform apply
```

Then note the outputs and wire up GitHub / the project backend — see
[`aws-setup.md`](aws-setup.md).

```sh
terraform output          # state_bucket, plan_role_arn, apply_role_arn, ...
```
