# OpenTofu native state encryption: the state holds the per-bucket B2 application
# keys, so it is encrypted at rest even though it is gitignored. Same passphrase
# as the other roots (Infisical /tofu/TOFU_STATE_PASSPHRASE).
terraform {
  encryption {
    key_provider "pbkdf2" "main" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "main" {
      keys = key_provider.pbkdf2.main
    }
    state {
      method   = method.aes_gcm.main
      enforced = true
    }
    plan {
      method   = method.aes_gcm.main
      enforced = true
    }
  }
}
