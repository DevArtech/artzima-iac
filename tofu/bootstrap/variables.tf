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

variable "infisical_k8s_project_slug" {
  description = "Slug of the cluster's Infisical project (/k8s/** only). null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}

variable "k8s_project_create" {
  description = "true = OpenTofu creates the cluster's Infisical project (needs org permission for artzima-admin). false (default) = it exists already (created by the operator, docs/OPERATOR-SETUP.md A2b) and is looked up by slug."
  type        = bool
  default     = false
}

variable "k8s_project_name" {
  description = "Display name of the cluster's project when OpenTofu creates it."
  type        = string
  default     = "Artzima K8s"
}

variable "eso_project_role" {
  description = "Built-in Infisical project role of the ESO identity on the cluster's project. Read-only only."
  type        = string
  default     = "viewer"

  validation {
    condition     = var.eso_project_role == "viewer"
    error_message = "ESO gets the built-in read-only role (viewer), nothing else: custom roles need a paid plan, and admin/member could write."
  }
}
