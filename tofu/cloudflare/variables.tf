# ---- secrets: only the state passphrase is a variable -------------------------
# Everything else comes from Infisical (infisical.tf). The passphrase must be
# known before the state can be read, so `make cf-*` exports
# TF_VAR_state_passphrase from Infisical /tofu/TOFU_STATE_PASSPHRASE.
variable "state_passphrase" {
  description = "OpenTofu state encryption passphrase (>= 16 chars), from Infisical /tofu/TOFU_STATE_PASSPHRASE"
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "infisical_host" {
  description = "Infisical URL. null = ansible/group_vars/all/infisical.yml (default https://app.infisical.com)"
  type        = string
  default     = null
}

variable "infisical_project_id" {
  description = "Infisical project ID. null = ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}

variable "infisical_env" {
  description = "Infisical environment slug. null = infisical_env in ansible/group_vars/all/infisical.yml (prod)"
  type        = string
  default     = null
}

# ---- behaviour ----------------------------------------------------------------
variable "tunnel_name" {
  type    = string
  default = "artzima"
}

variable "access_apps" {
  description = <<-EOT
    Optional Cloudflare Access, per app. `app` must be a name in /apps.yaml.
    null (default) = take it from Infisical /settings/CLOUDFLARE_ACCESS_APPS (JSON,
    keeps your email addresses out of the public repo; "[]" or absent = none).
    Allow rules: emails and/or email_domains (any match is allowed).
    Example: [{ app = "grafana", emails = ["you@example.com"] },
              { app = "wizarr",  email_domains = ["example.com"], session_duration = "8h" }]
  EOT
  type = list(object({
    app              = string
    emails           = optional(list(string), [])
    email_domains    = optional(list(string), [])
    session_duration = optional(string, "24h")
  }))
  default = null

  validation {
    condition     = var.access_apps == null || alltrue([for a in coalesce(var.access_apps, []) : length(a.emails) + length(a.email_domains) > 0])
    error_message = "Each access_apps entry needs at least one email or email_domain."
  }
}
