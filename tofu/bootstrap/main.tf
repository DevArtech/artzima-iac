# Bootstrap identities, DERIVED from the root credentials (docs/OPERATOR-SETUP.md):
#   - Tailscale auth keys for the Proxmox hosts and the subnet routers, minted with
#     the Tailscale OAuth client (root credential #3) -> Infisical /ansible (main project)
#   - the read-only ESO machine identity (universal auth + client secret) with the
#     BUILT-IN Viewer role on the cluster's Infisical project ONLY -> its credentials in
#     /tofu/ESO_CLIENT_* (main project; read by `make eso-bootstrap`)
# Two Infisical projects (docs/decisions.md "Infisical: two projects"): Infisical's free
# plan refuses custom roles, so the cluster's project holds ONLY /k8s/** and ESO may read
# all of it; it has no access to the main project at all.
# The Cloudflare tunnel token (tofu/cloudflare) and the B2 keys (tofu/backblaze) are
# derived in their own roots. `make bootstrap` applies all of them in order.
#
# State (encrypted, gitignored) holds the minted keys and the ESO client secret: the
# providers have no write-only variants; the Infisical copies use value_wo.
#
# Resource addresses are stable on purpose: a partial apply (2026-11-23, failed at the
# removed custom role) left the identity, its universal auth, the client secret and the
# Tailscale keys in state; this configuration adopts them unchanged.
locals {
  inf                  = yamldecode(file("${path.module}/../../ansible/group_vars/all/infisical.yml"))
  infisical_host       = coalesce(var.infisical_host, local.inf.infisical_host)
  infisical_project_id = coalesce(var.infisical_project_id, local.inf.infisical_project_id)
  infisical_env        = coalesce(var.infisical_env, local.inf.infisical_env)
  k8s_project_slug     = coalesce(var.infisical_k8s_project_slug, local.inf.infisical_k8s_project_slug)
  # The cluster's project: created here (k8s_project_create = true) or looked up by slug.
  infisical_k8s_project_id = var.k8s_project_create ? infisical_project.k8s[0].id : data.infisical_projects.k8s[0].id
}

provider "infisical" {
  host = local.infisical_host
  auth = {
    universal = {} # the bootstrap identity: INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/_SECRET
  }
}

# ---------------------------------------------------------------- the cluster's project
# Preferred: the operator creates it once in the UI and adds artzima-admin as Admin
# (docs/OPERATOR-SETUP.md A2b). Only if the bootstrap identity may create projects
# (org permission), `k8s_project_create = true` lets OpenTofu create it instead. It is
# protected twice: Infisical delete protection, and prevent_destroy here.
resource "infisical_project" "k8s" {
  count                 = var.k8s_project_create ? 1 : 0
  name                  = var.k8s_project_name
  slug                  = local.k8s_project_slug
  description           = "artzima: ONLY the secrets the Kubernetes cluster reads (/k8s/**). ESO = Viewer. Managed by tofu/bootstrap."
  has_delete_protection = true

  lifecycle {
    prevent_destroy = true
  }
}

data "infisical_projects" "k8s" {
  count = var.k8s_project_create ? 0 : 1
  slug  = local.k8s_project_slug
}

# ---------------------------------------------------------------- Tailscale
# ROOT CREDENTIAL #3. Its OAuth client needs the Auth Keys (write) scope and must be
# allowed to assign tag:artzima and tag:subnet-router (docs/OPERATOR-SETUP.md).
# It lives in the cluster's project (the Kubernetes operator uses it too).
ephemeral "infisical_secret" "ts_client_id" {
  workspace_id = local.infisical_k8s_project_id
  env_slug     = local.infisical_env
  folder_path  = "/k8s/tailscale"
  name         = "OPERATOR_CLIENT_ID"
}

ephemeral "infisical_secret" "ts_client_secret" {
  workspace_id = local.infisical_k8s_project_id
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

# The BUILT-IN read-only role, on the cluster's project ONLY. Never on the main project:
# that project holds the operator's root credentials and the OpenTofu/Ansible secrets.
resource "infisical_project_identity" "eso" {
  project_id  = local.infisical_k8s_project_id
  identity_id = infisical_identity.eso.id
  roles = [{
    role_slug = var.eso_project_role
  }]

  lifecycle {
    precondition {
      condition     = local.infisical_k8s_project_id != local.infisical_project_id
      error_message = "The ESO identity must only join the cluster's project, never the main project."
    }
  }
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

output "k8s_project_id" {
  description = "The cluster's Infisical project ID: put it into ~/.config/artzima/infisical-project.env as INFISICAL_K8S_PROJECT_ID."
  value       = local.infisical_k8s_project_id
}
