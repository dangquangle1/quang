variable "region" {
  description = "AWS region for the Terraform state bucket."
  type        = string
  default     = "us-east-1"
}

variable "github_owner" {
  description = "GitHub org or user that owns the repo."
  type        = string
  default     = "dangquangle1"
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
  default     = "quang"
}

variable "state_bucket_prefix" {
  description = "Prefix for the state bucket name; the account ID is appended to guarantee global uniqueness."
  type        = string
  default     = "quang-tfstate"
}
