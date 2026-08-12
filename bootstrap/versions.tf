terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

# Bootstrap uses LOCAL state (no backend block) — it's the thing that creates
# the remote-state bucket. The resulting terraform.tfstate is gitignored.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "quang"
      ManagedBy = "terraform"
      Component = "bootstrap"
    }
  }
}
