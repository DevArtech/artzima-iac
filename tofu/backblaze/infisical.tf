# Infisical: the one manual B2 secret in, the per-bucket keys out (docs/secrets.md).
# Auth: machine identity "artzima-admin" (INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/_SECRET).
#
# What reaches the (encrypted, gitignored) state, honestly:
#   - the B2 admin key: NO (ephemeral -> provider configuration only);
#   - the per-bucket application keys: YES, b2_application_key keeps the secret
#     in state (the provider has no write-only variant). The infisical_secret
#     resources use value_wo, so they add no extra copy. State encryption
#     (encryption.tf) is the mitigation.
locals {
  inf                  = yamldecode(file("${path.module}/../../ansible/group_vars/all/infisical.yml"))
  infisical_host       = coalesce(var.infisical_host, local.inf.infisical_host)
  infisical_project_id = coalesce(var.infisical_project_id, local.inf.infisical_project_id)
  # The cluster's project (/k8s/** only; secrets.manifest.yaml `projects:`).
  infisical_k8s_project_id = coalesce(var.infisical_k8s_project_id, local.inf.infisical_k8s_project_id)
  infisical_env            = coalesce(var.infisical_env, local.inf.infisical_env)
}

provider "infisical" {
  host = local.infisical_host
  auth = {
    universal = {} # client id/secret from INFISICAL_UNIVERSAL_AUTH_CLIENT_ID/_SECRET
  }
}

# The ONLY manual B2 secret: a key that may create buckets and keys
# (docs/backups.md "Backblaze account setup").
ephemeral "infisical_secret" "b2_admin_key_id" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/tofu"
  name         = "B2_ADMIN_KEY_ID"
}

ephemeral "infisical_secret" "b2_admin_application_key" {
  workspace_id = local.infisical_project_id
  env_slug     = local.infisical_env
  folder_path  = "/tofu"
  name         = "B2_ADMIN_APPLICATION_KEY"
}

# ---- scoped keys -> Infisical (write-only; re-written when the key is re-created)
# PBS S3 datastore (Ansible: make pbs-offsite)
resource "infisical_secret" "pbs_key_id" {
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/ansible"
  name             = "PBS_B2_KEY_ID"
  value_wo         = b2_application_key.bucket["pbs-backup"].application_key_id
  value_wo_version = local.key_version["pbs-backup"]
}

resource "infisical_secret" "pbs_application_key" {
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/ansible"
  name             = "PBS_B2_APPLICATION_KEY"
  value_wo         = b2_application_key.bucket["pbs-backup"].application_key
  value_wo_version = local.key_version["pbs-backup"]
}

# NAS media + personal shares: Backrest (restic) ON THE NAS, mode restic-on-nas only
# (Ansible role nas_backup, `make nas-backup`).
resource "infisical_secret" "nas_key_id" {
  count            = local.nas_mode == "restic-on-nas" ? 1 : 0
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/ansible"
  name             = "NAS_B2_KEY_ID"
  value_wo         = b2_application_key.bucket["nas-backup"].application_key_id
  value_wo_version = local.key_version["nas-backup"]
}

resource "infisical_secret" "nas_application_key" {
  count            = local.nas_mode == "restic-on-nas" ? 1 : 0
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/ansible"
  name             = "NAS_B2_APPLICATION_KEY"
  value_wo         = b2_application_key.bucket["nas-backup"].application_key
  value_wo_version = local.key_version["nas-backup"]
}

# ---- MAINTENANCE keys of the NAS bucket (forget/prune on their own schedule; docs/backups.md)
resource "infisical_secret" "nas_maint_key_id" {
  count            = local.nas_mode == "restic-on-nas" ? 1 : 0
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/ansible"
  name             = "NAS_B2_MAINT_KEY_ID"
  value_wo         = b2_application_key.bucket["nas-maint"].application_key_id
  value_wo_version = local.key_version["nas-maint"]
}

resource "infisical_secret" "nas_maint_application_key" {
  count            = local.nas_mode == "restic-on-nas" ? 1 : 0
  workspace_id     = local.infisical_project_id
  env_slug         = local.infisical_env
  folder_path      = "/ansible"
  name             = "NAS_B2_MAINT_APPLICATION_KEY"
  value_wo         = b2_application_key.bucket["nas-maint"].application_key
  value_wo_version = local.key_version["nas-maint"]
}

# ---- the CLUSTER's keys -> the k8s project (/k8s/backup; read by ESO):
# CNPG barman-cloud (k8s/secrets/backups/b2-pg.yaml) and the VolSync restic movers
# (k8s/secrets/backups/restic.yaml), each with its backup and its MAINTENANCE key.
# 2026-11-23: these moved from the main project. Their old resource addresses
# (infisical_secret.pg_key_id, ...) are gone, so the next apply creates them here and
# destroys the main-project copies (no copy by `make secrets-migrate-k8s`).
locals {
  k8s_backup_keys = {
    PG_B2_KEY_ID                    = { key = "pg-backup", part = "id" }
    PG_B2_APPLICATION_KEY           = { key = "pg-backup", part = "secret" }
    PG_B2_MAINT_KEY_ID              = { key = "pg-maint", part = "id" }
    PG_B2_MAINT_APPLICATION_KEY     = { key = "pg-maint", part = "secret" }
    RESTIC_B2_KEY_ID                = { key = "restic-backup", part = "id" }
    RESTIC_B2_APPLICATION_KEY       = { key = "restic-backup", part = "secret" }
    RESTIC_B2_MAINT_KEY_ID          = { key = "restic-maint", part = "id" }
    RESTIC_B2_MAINT_APPLICATION_KEY = { key = "restic-maint", part = "secret" }
  }
}

resource "infisical_secret" "k8s_backup" {
  for_each     = local.k8s_backup_keys
  workspace_id = local.infisical_k8s_project_id
  env_slug     = local.infisical_env
  folder_path  = "/k8s/backup"
  name         = each.key
  value_wo = (each.value.part == "id"
    ? b2_application_key.bucket[each.value.key].application_key_id
  : b2_application_key.bucket[each.value.key].application_key)
  value_wo_version = local.key_version[each.value.key]

  lifecycle {
    precondition {
      condition     = !strcontains(upper(local.infisical_k8s_project_id), "REPLACE")
      error_message = "Set INFISICAL_K8S_PROJECT_ID (the cluster's Infisical project) in ~/.config/artzima/infisical-project.env."
    }
  }
}
