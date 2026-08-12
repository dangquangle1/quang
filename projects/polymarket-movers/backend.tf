terraform {
  backend "s3" {
    key          = "projects/polymarket-movers/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
    # bucket + region supplied at init via -backend-config (backend.hcl / CI secrets)
  }
}
