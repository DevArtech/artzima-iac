terraform {
  required_version = ">= 1.11.0" # ephemeral resources + write-only attributes
  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "= 0.30.0"
    }
    infisical = {
      source  = "infisical/infisical"
      version = "= 0.20.18"
    }
  }
}
