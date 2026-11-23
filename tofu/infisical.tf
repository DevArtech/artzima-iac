# Secrets for this root, from Infisical (docs/secrets.md). Auth: universal-auth
# machine identity "artzima-admin" from the environment
# (INFISICAL_UNIVERSAL_AUTH_CLIENT_ID / _SECRET; `make tofu-*` loads them from
# ~/.config/artzima/infisical.env via scripts/infisical_exec.py).
#
# What reaches the (encrypted, gitignored) state, honestly:
#   - Proxmox API token: NO. Ephemeral resource -> provider config only.
#   - /settings values (ADMIN_SSH_KEYS, ...): YES, a data source is stored in
#     state. That folder holds only private-but-not-secret values for this reason.
#   - Talos machine secrets (CA keys, bootstrap token) and the kubeconfig/talosconfig:
#     YES. talos_machine_secrets is a real resource whose values must persist
#     between runs (the cluster's CA), and local_sensitive_file writes
#     tofu/out/ (gitignored). That is why the state is encrypted (encryption.tf).
provider "infisical" {
  host = local.infisical_host
  auth = {
    universal = {} # client id/secret from INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/_SECRET
  }
}

# terraform@pve!tofu=<uuid>, pushed to Infisical by the Ansible proxmox_api role.
ephemeral "infisical_secret" "proxmox_api_token" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/tofu"
  name         = "PROXMOX_API_TOKEN"
}

# Private, non-secret settings (root SSH public keys, ...). Stored in state.
data "infisical_secrets" "settings" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/settings"

  lifecycle {
    postcondition {
      condition     = contains(keys(self.secrets), "ADMIN_SSH_KEYS")
      error_message = "Infisical /settings needs ADMIN_SSH_KEYS (root SSH public keys, one per line; run `make secrets-check`)."
    }
  }
}
