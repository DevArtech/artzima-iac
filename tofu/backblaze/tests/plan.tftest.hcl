# Offline test (mocked providers, no Backblaze or Infisical calls, no credentials):
#   tofu -chdir=tofu/backblaze test     (part of `make lint`)
mock_provider "b2" {
  mock_resource "b2_bucket" {
    defaults = { bucket_id = "4a5b6c7d8e9f00000000000a" }
  }
  mock_resource "b2_application_key" {
    defaults = { application_key_id = "0001234567890ab0000000001", application_key = "K001placeholderplaceholder00" }
  }
}

mock_provider "infisical" {}

variables {
  state_passphrase         = "placeholder-passphrase-for-tests"
  infisical_project_id     = "test-project"
  infisical_k8s_project_id = "test-k8s-project"
}

run "buckets_private_encrypted_with_lifecycle" {
  command = plan

  assert {
    condition     = length(b2_bucket.backup) == length(yamldecode(file("../../ansible/group_vars/all/backups.yml")).b2_buckets) + (local.nas_mode == "restic-on-nas" ? 1 : 0)
    error_message = "one bucket per entry of b2_buckets in backups.yml (+ nas in restic-on-nas mode)"
  }
  assert {
    condition     = alltrue([for b in b2_bucket.backup : b.bucket_type == "allPrivate"])
    error_message = "every backup bucket must be private"
  }
  assert {
    condition = alltrue([for b in b2_bucket.backup :
    b.default_server_side_encryption[0].mode == "SSE-B2" && b.default_server_side_encryption[0].algorithm == "AES256"])
    error_message = "every bucket needs SSE-B2 (AES256) by default"
  }
  assert {
    condition     = alltrue([for b in b2_bucket.backup : b.lifecycle_rules[0].days_from_hiding_to_deleting > 0])
    error_message = "hidden (deleted) files must be purged by a lifecycle rule, or storage grows forever"
  }
}

run "object_lock_governance_where_configured" {
  command = plan

  assert {
    condition = (b2_bucket.backup["pg"].file_lock_configuration[0].is_file_lock_enabled
      && b2_bucket.backup["pg"].file_lock_configuration[0].default_retention[0].mode == "governance"
    && b2_bucket.backup["restic"].file_lock_configuration[0].default_retention[0].period[0].duration > 0)
    error_message = "pg and restic buckets have Object Lock in governance mode"
  }
  assert {
    condition     = length(b2_bucket.backup["pbs"].file_lock_configuration) == 0
    error_message = "pbs bucket: no Object Lock until PBS compatibility is verified (b2_object_lock_days.pbs = 0)"
  }
}

run "split_keys_none_can_delete" {
  command = plan

  assert {
    condition     = length(b2_application_key.bucket) == 2 * length(b2_bucket.backup) - 1
    error_message = "a backup key and a maintenance key per bucket (pbs: one key)"
  }
  assert {
    condition     = alltrue([for k, key in b2_application_key.bucket : length(key.bucket_ids) == 1])
    error_message = "each key is restricted to exactly ONE bucket"
  }
  assert {
    condition = alltrue([for key in b2_application_key.bucket :
      !contains(key.capabilities, "deleteFiles") && !contains(key.capabilities, "writeKeys")
    && !contains(key.capabilities, "deleteBuckets") && !contains(key.capabilities, "writeBuckets")])
    error_message = "no automated key may permanently delete versions (deleteFiles) or manage keys/buckets"
  }
  assert {
    condition     = contains(keys(b2_application_key.bucket), "restic-maint") && !contains(keys(b2_application_key.bucket), "pbs-maint")
    error_message = "separate maintenance keys, except for PBS (GC runs inside PBS)"
  }
}

run "keys_written_to_their_consumers" {
  command = plan

  assert {
    condition = (infisical_secret.k8s_backup["PG_B2_KEY_ID"].folder_path == "/k8s/backup"
      && toset(keys(infisical_secret.k8s_backup)) == toset(["PG_B2_KEY_ID", "PG_B2_APPLICATION_KEY", "PG_B2_MAINT_KEY_ID",
        "PG_B2_MAINT_APPLICATION_KEY", "RESTIC_B2_KEY_ID", "RESTIC_B2_APPLICATION_KEY", "RESTIC_B2_MAINT_KEY_ID",
      "RESTIC_B2_MAINT_APPLICATION_KEY"])
    && infisical_secret.pbs_key_id.folder_path == "/ansible" && infisical_secret.pbs_application_key.name == "PBS_B2_APPLICATION_KEY")
    error_message = "each key goes to the Infisical path its consumer reads"
  }
  assert {
    condition = (alltrue([for s in infisical_secret.k8s_backup : s.workspace_id == "test-k8s-project"])
    && infisical_secret.pbs_key_id.workspace_id == "test-project" && infisical_secret.nas_maint_key_id[0].workspace_id == "test-project")
    error_message = "the cluster's keys go to the k8s project, the PBS/NAS keys stay in the main project"
  }
}

run "k8s_project_must_be_configured" {
  command = plan
  variables {
    infisical_k8s_project_id = "REPLACE_VIA_INFISICAL_PROJECT_ENV"
  }
  expect_failures = [infisical_secret.k8s_backup]
}

# NAS media/personal files: the DEFAULT is Backrest (restic) on the NAS -> a dedicated
# bucket + keys; the old UGOS job ("existing") and "off" create nothing here.
run "nas_restic_on_nas_creates_dedicated_bucket_and_keys" {
  command = plan
  variables {
    nas_backup_mode = "restic-on-nas"
  }
  assert {
    condition = (contains(keys(b2_bucket.backup), "nas") && b2_bucket.backup["nas"].bucket_type == "allPrivate"
    && length(b2_application_key.bucket["nas-backup"].bucket_ids) == 1 && contains(keys(b2_application_key.bucket), "nas-maint"))
    error_message = "restic-on-nas: a dedicated private bucket with its own backup + maintenance keys"
  }
  assert {
    condition = (length(infisical_secret.nas_key_id) == 1 && infisical_secret.nas_key_id[0].folder_path == "/ansible"
    && infisical_secret.nas_maint_application_key[0].name == "NAS_B2_MAINT_APPLICATION_KEY")
    error_message = "restic-on-nas: the keys go to Infisical /ansible/NAS_B2_* (read by the nas_backup role)"
  }
}

run "nas_default_is_restic_on_nas" {
  command = plan
  assert {
    condition     = local.nas_mode == "restic-on-nas" && contains(keys(b2_bucket.backup), "nas")
    error_message = "backups.yml default must be restic-on-nas (Backrest on the NAS)"
  }
}

run "nas_existing_creates_nothing" {
  command = plan
  variables {
    nas_backup_mode = "existing"
  }
  assert {
    condition     = !contains(keys(b2_bucket.backup), "nas") && length(infisical_secret.nas_key_id) == 0
    error_message = "mode existing (old UGOS job) must not create a NAS bucket or key"
  }
}

run "nas_off_creates_nothing" {
  command = plan
  variables {
    nas_backup_mode = "off"
  }
  assert {
    condition     = !contains(keys(b2_bucket.backup), "nas") && length(infisical_secret.nas_maint_key_id) == 0
    error_message = "mode off creates nothing for the NAS"
  }
}

run "nas_mode_validated" {
  command = plan
  variables {
    nas_backup_mode = "restic"
  }
  expect_failures = [var.nas_backup_mode]
}
