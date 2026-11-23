terraform {
  required_version = ">= 1.11.0" # ephemeral resources + write-only attributes

  required_providers {
    # bpg/proxmox is 0.x and ships breaking changes in minor releases: pin exactly.
    proxmox = {
      source  = "bpg/proxmox"
      version = "= 0.123.1"
    }
    talos = {
      source  = "siderolabs/talos"
      version = "= 0.12.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "= 2.9.1"
    }
    # Secrets come from Infisical (docs/secrets.md), read as EPHEMERAL values
    # where the consumer allows it, so they stay out of state.
    infisical = {
      source  = "infisical/infisical"
      version = "= 0.20.18"
    }
  }

  # State is local, gitignored and ENCRYPTED (encryption.tf). For a remote
  # backend, add one here; the encryption applies to it unchanged.
}
