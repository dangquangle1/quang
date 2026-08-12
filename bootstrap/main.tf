data "aws_caller_identity" "current" {}

locals {
  state_bucket = "${var.state_bucket_prefix}-${data.aws_caller_identity.current.account_id}"
  github_sub   = "repo:${var.github_owner}/${var.github_repo}"
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
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_sub}:pull_request"]
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
# APPLY role — assumed only from refs/heads/main. Read/write state now;
# resource-creation permissions get added here as you build.
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
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_sub}:ref:refs/heads/main"]
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

# TODO(least-privilege): as you add resources to the root config, attach the
# matching permissions to aws_iam_role.apply here — e.g. a new
# aws_iam_role_policy for the specific s3:/ec2:/lambda: actions Terraform needs.
