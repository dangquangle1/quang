output "state_bucket" {
  description = "Name of the S3 state bucket. Use this for -backend-config in the root config."
  value       = aws_s3_bucket.state.id
}

output "region" {
  description = "Region the state bucket lives in."
  value       = var.region
}

output "plan_role_arn" {
  description = "ARN of the read-only PR plan role. Set as GitHub secret AWS_PLAN_ROLE_ARN."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "ARN of the main-branch apply role. Set as GitHub secret AWS_APPLY_ROLE_ARN."
  value       = aws_iam_role.apply.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider."
  value       = aws_iam_openid_connect_provider.github.arn
}
