terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
