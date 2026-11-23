terraform {
  required_version = ">= 1.11.0" # ephemeral resources + write-only attributes
  required_providers {
    b2 = {
      source  = "Backblaze/b2"
      version = "= 0.14.1" # latest published (2026-10-23); embeds the B2 SDK, no Python needed
    }
    infisical = {
      source  = "infisical/infisical"
      version = "= 0.20.18"
    }
  }
}
