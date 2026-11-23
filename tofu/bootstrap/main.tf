# Bootstrap identities, DERIVED from the root credentials (docs/OPERATOR-SETUP.md):
#   - Tailscale auth keys for the Proxmox hosts and the subnet routers, minted with
#     the Tailscale OAuth client (root credential #3) -> Infisical /ansible
#   - the read-only ESO machine identity (universal auth + client secret, scoped reads of
#     runtime secrets only), created with the Infisical bootstrap identity (root credential #1)
#     -> Infisical /tofu/ESO_CLIENT_* (read by `make eso-bootstrap`)
# The Cloudflare tunnel token (tofu/cloudflare) and the B2 keys (tofu/backblaze) are
# derived in their own roots. `make bootstrap` applies all of them in order.
#
# State (encrypted, gitignored) holds the minted keys and the ESO client secret: the
# providers have no write-only variants; the Infisical copies use value_wo.
locals {
  inf                  = yamldecode(file("${path.module}/../../ansible/group_vars/all/infisical.yml"))
  infisical_host       = coalesce(var.infisical_host, local.inf.infisical_host)
  infisical_project_id = coalesce(var.infisical_project_id, local.inf.infisical_project_id)
  infisical_env        = coalesce(var.infisical_env, local.inf.infisical_env)
}

provider "infisical" {
  host = local.infisical_host
  auth = {
    universal = {} # the bootstrap identity: INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/_SECRET
  }
}

# ---------------------------------------------------------------- Tailscale
# ROOT CREDENTIAL #3. Its OAuth client needs the Auth Keys (write) scope and must be
# allowed to assign tag:artzima and tag:subnet-router (docs/OPERATOR-SETUP.md).
ephemeral "infisical_secret" "ts_client_id" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/k8s/tailscale"
  name         = "OPERATOR_CLIENT_ID"
}

ephemeral "infisical_secret" "ts_client_secret" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/k8s/tailscale"
  name         = "OPERATOR_CLIENT_SECRET"
}

provider "tailscale" {
  oauth_client_id     = ephemeral.infisical_secret.ts_client_id.value
  oauth_client_secret = ephemeral.infisical_secret.ts_client_secret.value
  tailnet             = "-" # the client's own tailnet
}

locals {
  tailnet_keys = {
    hosts   = { tags = ["tag:artzima"], path = "/ansible", name = "TAILSCALE_AUTHKEY", what = "Proxmox hosts" }
    routers = { tags = ["tag:subnet-router"], path = "/ansible", name = "TAILSCALE_ROUTER_AUTHKEY", what = "subnet routers" }
  }
}

resource "tailscale_tailnet_key" "this" {
  for_each            = local.tailnet_keys
  description         = "artzima ${each.key}"
  reusable            = true # one key for every host of the group, re-runs of `make tailscale`
  ephemeral           = false
  preauthorized       = true
  tags                = each.value.tags
  expiry              = var.tailnet_key_expiry_days * 86400
  recreate_if_invalid = "always" # an expired/revoked key is minted again on the next apply
}

resource "infisical_secret" "tailnet_key" {
  for_each         = local.tailnet_keys
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = each.value.path
  name             = each.value.name
  value_wo         = tailscale_tailnet_key.this[each.key].key
  value_wo_version = parseint(substr(sha256(tailscale_tailnet_key.this[each.key].id), 0, 7), 16)
}

# ---------------------------------------------------------------- ESO machine identity
# Created by the bootstrap identity, which needs org permission to manage identities.
data "infisical_projects" "this" {
  slug = local.inf.infisical_project_slug
}

resource "infisical_identity" "eso" {
  name   = "artzima-eso"
  org_id = data.infisical_projects.this.org_id
  role   = "no-access" # nothing at org level; project access below
}

resource "infisical_identity_universal_auth" "eso" {
  identity_id = infisical_identity.eso.id
}

resource "infisical_identity_universal_auth_client_secret" "eso" {
  identity_id = infisical_identity.eso.id
  description = "External Secrets Operator in the artzima cluster (tofu/bootstrap)"
  depends_on  = [infisical_identity_universal_auth.eso]
}

# Never fall back to Viewer if the account cannot create this scoped role.
resource "infisical_project_role" "eso" {
  project_id = data.infisical_projects.this.id
  name       = "Artzima runtime secrets"
  slug       = "artzima-runtime-secrets"
  permissions_v2 = [
    {
      subject  = "secrets"
      action   = ["describeSecret", "readValue"]
      inverted = false
      conditions = jsonencode({
        environment = local.infisical_env
        secretPath  = { "$glob" = "/k8s/**" }
      })
    },
    {
      subject  = "secrets"
      action   = ["describeSecret", "readValue"]
      inverted = false
      conditions = jsonencode({
        environment = local.infisical_env
        secretPath  = "/settings"
        secretName  = "PUBLIC_DOMAIN"
      })
    }
  ]
}

resource "infisical_project_identity" "eso" {
  project_id  = data.infisical_projects.this.id
  identity_id = infisical_identity.eso.id
  roles = [{
    role_slug = infisical_project_role.eso.slug
  }]
}

resource "infisical_secret" "eso_client_id" {
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/tofu"
  name             = "ESO_CLIENT_ID"
  value_wo         = infisical_identity_universal_auth_client_secret.eso.client_id
  value_wo_version = parseint(substr(sha256(infisical_identity_universal_auth_client_secret.eso.id), 0, 7), 16)
}

resource "infisical_secret" "eso_client_secret" {
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/tofu"
  name             = "ESO_CLIENT_SECRET"
  value_wo         = infisical_identity_universal_auth_client_secret.eso.client_secret
  value_wo_version = parseint(substr(sha256(infisical_identity_universal_auth_client_secret.eso.id), 0, 7), 16)
}
