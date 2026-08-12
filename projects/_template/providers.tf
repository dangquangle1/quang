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
  region = "us-east-1" # set this project's region

  default_tags {
    tags = {
      Project   = "PROJECT_NAME" # set to this project's name
      ManagedBy = "terraform"
    }
  }
}
