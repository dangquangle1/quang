# AWS + CI/CD setup

End-to-end setup for deploying Terraform to a single AWS account from this repo:
you log in via **IAM Identity Center (SSO)**; GitHub Actions deploys via **OIDC
roles** (no static keys anywhere).

Two identities, on purpose:

| Identity | Who | Auth | Permissions |
|---|---|---|---|
| `quang` SSO user | you (local `terraform`, bootstrap) | IAM Identity Center + `awslogin` | permission sets: `ReadOnlyAccess` (default), `PowerUserAccess`, `AdministratorAccess` |
| `quang-github-plan` / `quang-github-apply` roles | GitHub Actions | OIDC (no keys) | least-privilege (backend-only for now) |

---

## 1. Local access — IAM Identity Center (SSO)

Three permission sets, so you default to read-only and step up only when needed.
(Already created: `AdministratorAccess`, `PowerUserAccess`, `ReadOnlyAccess`,
assigned to user `quang`.)

Which to use:
- **`quang-readonly`** — everyday default (inspecting, `aws` CLI reads).
- **`quang-poweruser`** — deploying most resources (no IAM/Organizations changes).
- **`quang-admin`** — bootstrap and anything touching IAM (roles, OIDC).

On your machine — one shared SSO session, three profiles, in `~/.aws/config`
(`sso_role_name` must match each permission set name exactly as shown in the portal):

```ini
[sso-session quang]
sso_start_url = https://<subdomain>.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[profile quang-readonly]
sso_session = quang
sso_account_id = <ACCOUNT_ID>
sso_role_name = ReadOnlyAccess
region = us-east-1

[profile quang-poweruser]
sso_session = quang
sso_account_id = <ACCOUNT_ID>
sso_role_name = PowerUserAccess
region = us-east-1

[profile quang-admin]
sso_session = quang
sso_account_id = <ACCOUNT_ID>
sso_role_name = AdministratorAccess
region = us-east-1
```

Login helper + read-only default:

- Git Bash / zsh: `alias awslogin='aws sso login --sso-session quang'`
- PowerShell (`$PROFILE`): `function awslogin { aws sso login --sso-session quang }`
- Default to read-only: `export AWS_PROFILE=quang-readonly` — name `quang-admin`
  (bootstrap/IAM) or `quang-poweruser` (other deploys) explicitly when needed.

Verify (one login covers both profiles):

```sh
awslogin
aws sts get-caller-identity --profile quang-readonly
aws sts get-caller-identity --profile quang-admin
```

---

## 2. Bootstrap (once, locally as admin)

Creates the state bucket, the GitHub OIDC provider, and the two CI roles.

```sh
awslogin                              # aws sso login --sso-session quang
export AWS_PROFILE=quang-admin        # PowerShell: $env:AWS_PROFILE="quang-admin"
cd bootstrap
terraform init
terraform apply
terraform output                      # note the values below
```

Outputs you'll need: `state_bucket`, `plan_role_arn`, `apply_role_arn`.

---

## 3. Wire up GitHub

In the repo → **Settings → Secrets and variables → Actions**:

**Secrets** (masked in logs — these contain your account ID):
- `AWS_STATE_BUCKET` → the `state_bucket` output
- `AWS_PLAN_ROLE_ARN` → the `plan_role_arn` output
- `AWS_APPLY_ROLE_ARN` → the `apply_role_arn` output

**Variables** (not sensitive):
- `AWS_REGION` → `us-east-1`

Then protect the apply step: **Settings → Environments → New environment
`production` → add yourself as a Required reviewer.** Now every merge to `main`
pauses for your approval before `terraform apply`. Also enable **branch
protection** on `main` (require PR + passing checks).

---

## 4. Point a project at the backend

```sh
cd ../projects/polymarket-movers      # a project stack (from bootstrap/)
cp backend.hcl.example backend.hcl    # fill bucket = state_bucket output (gitignored)
terraform init -backend-config=backend.hcl
terraform plan
```

Each project under `projects/` is its own stack with its own state key — see
[`../projects/README.md`](../projects/README.md) to add another.

---

## 5. Day-to-day

- **Change infra:** branch → edit a project under `projects/` → open a PR. That
  project's pipeline runs `fmt`/`validate`/`plan` and comments the plan.
- **Deploy:** merge to `main` → approve the `production` environment → CI applies.
- **Locally:** `awslogin && AWS_PROFILE=quang-admin terraform -chdir=projects/polymarket-movers plan`.

## Growing the least-privilege apply role

The CI apply role can currently only touch state. When you add a resource and the
apply fails with `AccessDenied`, add exactly that permission to
`aws_iam_role.apply` in `bootstrap/main.tf` (see the `TODO(least-privilege)`
marker), then re-run the bootstrap `terraform apply`.

## Deploying to other regions

The state bucket stays in `us-east-1`; resources can target any region. Set the
project's `providers.tf` `region`, or add an aliased provider and set
`provider = aws.<alias>` on specific resources. The CI role is global — no change
needed.
