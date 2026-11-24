# Secrets are NOT variables (except the state passphrase): the B2 admin key is read
# from Infisical inside the configuration (infisical.tf).
variable "state_passphrase" {
  description = "Passphrase for OpenTofu state encryption (>= 16 chars), from Infisical /tofu/TOFU_STATE_PASSPHRASE"
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "nas_backup_mode" {
  description = "Override backups.yml backup_nas_media (restic-on-nas|existing|off); null = take it from backups.yml"
  type        = string
  default     = null

  validation {
    condition     = var.nas_backup_mode == null || contains(["restic-on-nas", "existing", "off"], coalesce(var.nas_backup_mode, "off"))
    error_message = "nas_backup_mode must be restic-on-nas, existing or off."
  }
}

variable "infisical_host" {
  description = "Infisical URL. null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}

variable "infisical_project_id" {
  description = "Infisical project ID. null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}

variable "infisical_k8s_project_id" {
  description = "The cluster's Infisical project ID (/k8s/** only). null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}

variable "infisical_env" {
  description = "Infisical environment slug. null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}
