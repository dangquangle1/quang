terraform {
  backend "s3" {
    # Change PROJECT_NAME to this project's directory name (must be unique).
    key          = "projects/PROJECT_NAME/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
    # bucket + region supplied at init via -backend-config (backend.hcl / CI secrets)
  }
}
