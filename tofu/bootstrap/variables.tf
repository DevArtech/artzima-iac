# Secrets are NOT variables (except the state passphrase): the Tailscale OAuth client
# is read from Infisical inside the configuration (main.tf).
variable "state_passphrase" {
  description = "Passphrase for OpenTofu state encryption (>= 16 chars), from Infisical /tofu/TOFU_STATE_PASSPHRASE"
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "tailnet_key_expiry_days" {
  description = "Lifetime of the minted Tailscale auth keys (max 90). Expired keys are re-created on the next apply."
  type        = number
  default     = 90

  validation {
    condition     = var.tailnet_key_expiry_days >= 1 && var.tailnet_key_expiry_days <= 90
    error_message = "Tailscale auth keys live 1-90 days."
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

variable "infisical_env" {
  description = "Infisical environment slug. null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}
