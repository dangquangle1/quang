data "aws_caller_identity" "current" {}

locals {
  state_bucket = "${var.state_bucket_prefix}-${data.aws_caller_identity.current.account_id}"
  repo_full    = "${var.github_owner}/${var.github_repo}"
}

# ---------------------------------------------------------------------------
# Remote state bucket (S3, versioned + encrypted + private, native locking)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # This bucket holds every project's state — guard against accidental destroy.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# GitHub Actions OIDC provider (lets Actions assume roles with no static keys)
# ---------------------------------------------------------------------------
data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

# ---------------------------------------------------------------------------
# State-access policies (backend-only for now — grow the apply role as you
# add real resources)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "state_read" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }
}

data "aws_iam_policy_document" "state_write" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid       = "ReadWriteState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"] # covers the state object + its .tflock lock
  }
}

# ---------------------------------------------------------------------------
# PLAN role — assumed by pull requests. Read-only: can read state to produce a
# plan, nothing else. (PR plans run with -lock=false, so no state writes.)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "plan_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Pin the repo via the clean `repository` claim (no numeric IDs); scope to
    # pull-request context. AWS requires a `sub` condition and the sub embeds
    # immutable owner/repo IDs, so match it with a wildcard.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository"
      values   = [local.repo_full]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:*:pull_request"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name               = "quang-github-plan"
  description        = "Read-only role assumed by GitHub Actions on pull requests to run terraform plan."
  assume_role_policy = data.aws_iam_policy_document.plan_assume.json
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "state-read"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.state_read.json
}

# ---------------------------------------------------------------------------
# APPLY role — assumed only by jobs running in the `production` environment
# (the gated apply on main). Because the apply job uses environment:production,
# GitHub sets the token sub to ...:environment:production (not ...:ref:...).
# Read/write state now; resource-creation permissions get added here as you build.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "apply_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Pin the repo via the clean `repository` claim (no numeric IDs); scope to
    # the gated `production` environment. AWS requires a `sub` condition and the
    # sub embeds immutable owner/repo IDs, so match it with a wildcard.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository"
      values   = [local.repo_full]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:*:environment:production"]
    }
  }
}

resource "aws_iam_role" "apply" {
  name               = "quang-github-apply"
  description        = "Read/write role assumed by GitHub Actions on merges to main to run terraform apply."
  assume_role_policy = data.aws_iam_policy_document.apply_assume.json
}

resource "aws_iam_role_policy" "apply_state" {
  name   = "state-read-write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.state_write.json
}

# ---------------------------------------------------------------------------
# Read access for both roles
#
# `terraform plan` refreshes every managed resource, so even the plan-only role
# needs Get*/Describe*/List* across whatever the projects manage — and several
# of those (logs:DescribeLogGroups, ssm:DescribeParameters) can't be scoped to a
# resource ARN at all. Enumerating them per service is brittle: one missing verb
# is a failed pipeline run plus another local apply here to fix it.
#
# ReadOnlyAccess grants no mutations, so the plan role stays read-only in the
# sense that matters. Trade-off: it can read anything in the account, not just
# this project. Acceptable while this is a single personal account whose roles
# are only assumable by this repo's own CI.
# ---------------------------------------------------------------------------
resource "aws_iam_role_policy_attachment" "plan_read" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "apply_read" {
  role       = aws_iam_role.apply.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# ReadOnlyAccess deliberately omits kms:Decrypt, which refreshing a SecureString
# parameter needs. ViaService means this unlocks nothing outside SSM.
data "aws_iam_policy_document" "ssm_decrypt" {
  statement {
    sid       = "DecryptViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.pmm_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "plan_ssm_decrypt" {
  name   = "ssm-decrypt"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.ssm_decrypt.json
}

# ---------------------------------------------------------------------------
# Write access — per project, scoped by resource name
#
# Actions are wildcarded per service and bounded by ARN instead: the blast
# radius is set by *which resources* the role can touch, not which verbs. Adding
# a resource type to a project usually needs nothing here; adding a new service
# or renaming a resource does.
#
# Region is eu-west-2 — deliberately not var.region, which is where the state
# bucket lives (us-east-1). The project's providers.tf pins eu-west-2.
# ---------------------------------------------------------------------------
locals {
  pmm_name    = "polymarket-movers"
  pmm_region  = "eu-west-2"
  pmm_account = data.aws_caller_identity.current.account_id
  pmm_log     = "arn:aws:logs:${local.pmm_region}:${local.pmm_account}:log-group:/aws/lambda/${local.pmm_name}"

  pmm_role_arn = "arn:aws:iam::${local.pmm_account}:role/${local.pmm_name}-lambda"

  pmm_resources = [
    "arn:aws:lambda:${local.pmm_region}:${local.pmm_account}:function:${local.pmm_name}",
    # Prefixed rather than exact so adding or renaming a table in the project
    # doesn't require another local bootstrap apply.
    "arn:aws:dynamodb:${local.pmm_region}:${local.pmm_account}:table/${local.pmm_name}-*",
    "arn:aws:events:${local.pmm_region}:${local.pmm_account}:rule/${local.pmm_name}-schedule",
    "arn:aws:ssm:${local.pmm_region}:${local.pmm_account}:parameter/${local.pmm_name}/*",
    local.pmm_log,
    "${local.pmm_log}:*",
  ]
}

data "aws_iam_policy_document" "apply_polymarket_movers" {
  statement {
    sid       = "PmmProjectResources"
    actions   = ["lambda:*", "dynamodb:*", "events:*", "logs:*", "ssm:*"]
    resources = local.pmm_resources
  }

  # The Lambda execution role. Unconditioned PassRole is fine on this single
  # ARN: the role's own trust policy admits only lambda.amazonaws.com, and its
  # permissions are one DynamoDB table plus its own two SSM parameters — so
  # there is nothing to escalate to.
  statement {
    sid       = "PmmExecutionRole"
    actions   = ["iam:*Role*"]
    resources = [local.pmm_role_arn]
  }

  statement {
    sid       = "PmmSsmEncrypt"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.pmm_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "apply_polymarket_movers" {
  name   = "polymarket-movers"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_polymarket_movers.json
}
