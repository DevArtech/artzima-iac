terraform {
  required_version = ">= 1.11.0" # ephemeral resources + write-only attributes
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "= 5.29.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "= 3.9.1"
    }
    infisical = {
      source  = "infisical/infisical"
      version = "= 0.20.18"
    }
  }
}
