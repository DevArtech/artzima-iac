# Secrets and private settings for the Cloudflare root, from Infisical
# (docs/secrets.md). Auth: machine identity "artzima-admin" via
# INFISICAL_UNIVERSAL_AUTH_CLIENT_ID / _SECRET (scripts/infisical_exec.py).
#
# What reaches the (encrypted, gitignored) state, honestly:
#   - Cloudflare API token: NO (ephemeral -> provider config only).
#   - /settings (domain, account/zone IDs, Access emails): YES, as a data source.
#     Private, but not secret.
#   - The tunnel secret and the tunnel token: YES. cloudflare/cloudflare 5.29
#     has no write-only `tunnel_secret` and no ephemeral token, so
#     random_id.tunnel_secret, the tunnel resource and the token data source
#     all keep it in state. infisical_secret.tunnel_token itself uses
#     value_wo (write-only), so it adds no extra copy. Encrypted state is the
#     mitigation (encryption.tf).
locals {
  inf                  = yamldecode(file("${path.module}/../../ansible/group_vars/all/infisical.yml"))
  infisical_host       = coalesce(var.infisical_host, local.inf.infisical_host)
  infisical_project_id = coalesce(var.infisical_project_id, local.inf.infisical_project_id)
  # The cluster's project (/k8s/** only): the tunnel token goes there.
  infisical_k8s_project_id = coalesce(var.infisical_k8s_project_id, local.inf.infisical_k8s_project_id)
  infisical_env            = coalesce(var.infisical_env, local.inf.infisical_env)
  settings                 = { for k, v in data.infisical_secrets.settings.secrets : k => v.value }
}

provider "infisical" {
  host = local.infisical_host
  auth = {
    universal = {} # client id/secret from INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/_SECRET
  }
}

# Cloudflare API token: Tunnel Edit, Access Apps and Policies Edit, DNS Edit (one zone).
ephemeral "infisical_secret" "cloudflare_api_token" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/tofu"
  name         = "CLOUDFLARE_API_TOKEN"
}

# PUBLIC_DOMAIN, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_ZONE_ID, CLOUDFLARE_ACCESS_APPS (optional).
data "infisical_secrets" "settings" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/settings"

  lifecycle {
    postcondition {
      condition = alltrue([for k in ["PUBLIC_DOMAIN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID"] :
      contains(keys(self.secrets), k)])
      error_message = "Infisical /settings needs PUBLIC_DOMAIN, CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_ZONE_ID (run `make secrets-check`)."
    }
  }
}

# The tunnel token goes straight into Infisical; ESO syncs it into
# cloudflared/tunnel-token (k8s/secrets/cloudflared/tunnel-token.yaml).
# Write-only: this resource stores no copy of the value. The version follows
# the tunnel id, so a re-created tunnel (= new token) re-writes it.
resource "infisical_secret" "tunnel_token" {
  workspace_id     = local.infisical_k8s_project_id # the cluster's project (ESO reads it)
  env_slug         = local.infisical_env
  folder_path      = "/k8s/cloudflared"
  name             = "TUNNEL_TOKEN"
  value_wo         = data.cloudflare_zero_trust_tunnel_cloudflared_token.artzima.token
  value_wo_version = parseint(substr(sha256(cloudflare_zero_trust_tunnel_cloudflared.artzima.id), 0, 7), 16)

  lifecycle {
    precondition {
      condition     = !strcontains(upper(local.infisical_k8s_project_id), "REPLACE")
      error_message = "Set INFISICAL_K8S_PROJECT_ID (the cluster's Infisical project) in ~/.config/artzima/infisical-project.env."
    }
  }
}
