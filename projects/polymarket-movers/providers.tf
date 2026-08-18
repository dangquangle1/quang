terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # Builds the Lambda deployment zip from src/. The service has no
    # third-party dependencies, so no pip stage or layer is needed and the
    # CI pipeline stays purely Terraform.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = "eu-west-2" # London — colocated with Polymarket's CLOB origin

  default_tags {
    tags = {
      Project   = "polymarket-movers"
      ManagedBy = "terraform"
    }
  }
}
