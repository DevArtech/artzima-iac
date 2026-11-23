# Backblaze B2 for off-site backups, as code (docs/backups.md):
#   - one PRIVATE bucket per kind (names: ansible/group_vars/all/backups.yml),
#     server-side encrypted (SSE-B2, AES256), with lifecycle rules;
#   - one application key PER BUCKET, scoped to only that bucket, written into
#     Infisical (infisical.tf) for its single consumer.
# Client-side encryption is on top of this (restic password, PBS, barman via TLS +
# SSE): SSE-B2 protects against disk theft at Backblaze, not against Backblaze.
locals {
  cfg = yamldecode(file("${path.module}/../../ansible/group_vars/all/backups.yml"))
  # NAS media + personal files: by default ("restic-on-nas") Backrest on the NAS backs
  # them up with restic into a DEDICATED bucket with its own key (Ansible role
  # nas_backup). Modes "existing" (old UGOS job) and "off" create nothing here.
  nas_mode = coalesce(var.nas_backup_mode, local.cfg.backup_nas_media)
  buckets = merge(local.cfg.b2_buckets, # { pg, restic, pbs }
  local.nas_mode == "restic-on-nas" ? { nas = local.cfg.b2_bucket_nas } : {})

  # Capabilities, through the S3 API, on ONE bucket each. NO key has deleteFiles: on B2
  # an S3 "delete by name" only HIDES a file (needs writeFiles); only the lifecycle rule
  # removes versions, days later, and never before an Object Lock retention ends. So
  # a leaked key can't destroy history. (docs/backups.md "Immutability")
  key_capabilities = [
    "listBuckets",
    "listAllBucketNames",
    "readBuckets",
    "listFiles",
    "readFiles",
    "writeFiles",
  ]

  # Two keys per bucket: "backup" (used by the backup jobs) and "maint" (forget/prune/
  # retention, on its own schedule). PBS runs GC in-process, so it gets one key.
  keys = merge(
    { for b in keys(local.buckets) : "${b}-backup" => b },
    { for b in keys(local.buckets) : "${b}-maint" => b if b != "pbs" },
  )

  # Re-write the Infisical copies whenever a key is re-created (new id).
  key_version = { for k, v in b2_application_key.bucket :
  k => parseint(substr(sha256(v.application_key_id), 0, 7), 16) }

  lock_days = { for b in keys(local.buckets) : b => try(local.cfg.b2_object_lock_days[b], 0) }
}

provider "b2" {
  # Ephemeral values from Infisical: never written to state or plan.
  application_key_id = ephemeral.infisical_secret.b2_admin_key_id.value
  application_key    = ephemeral.infisical_secret.b2_admin_application_key.value
}

resource "b2_bucket" "backup" {
  for_each    = local.buckets
  bucket_name = each.value
  bucket_type = "allPrivate"

  default_server_side_encryption {
    mode      = "SSE-B2"
    algorithm = "AES256"
  }

  # B2 keeps every version; "deleting" only hides a file. The backup tools decide
  # what to delete (restic forget/prune, barman retention, PBS GC); hidden files
  # are then purged after N days, and abandoned multipart uploads after 7.
  lifecycle_rules {
    file_name_prefix                                       = ""
    days_from_hiding_to_deleting                           = local.cfg.b2_days_hidden_to_deleted
    days_from_starting_to_canceling_unfinished_large_files = 7
  }

  # Object Lock, governance mode (docs/backups.md "Immutability"). Can only be enabled
  # when the bucket is created; 0 days = off.
  dynamic "file_lock_configuration" {
    for_each = local.lock_days[each.key] > 0 ? [1] : []
    content {
      is_file_lock_enabled = true
      default_retention {
        mode = "governance"
        period {
          duration = local.lock_days[each.key]
          unit     = "days"
        }
      }
    }
  }

  lifecycle {
    prevent_destroy = true # a destroyed bucket is a destroyed off-site copy
  }
}

resource "b2_application_key" "bucket" {
  for_each     = local.keys # "<bucket>-backup", "<bucket>-maint"
  key_name     = "artzima-${each.key}"
  bucket_ids   = [b2_bucket.backup[each.value].bucket_id]
  capabilities = local.key_capabilities
}
