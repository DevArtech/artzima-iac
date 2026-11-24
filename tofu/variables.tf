# ---- secrets: NONE are variables except the state passphrase ----------------
# The state passphrase must be known before any provider runs (it decrypts the
# state), so it comes from the environment: `make tofu-*` exports
# TF_VAR_state_passphrase from Infisical /tofu/TOFU_STATE_PASSPHRASE
# (scripts/infisical_exec.py). Everything else is read from Infisical inside
# the configuration (infisical.tf).
variable "state_passphrase" {
  description = "Passphrase for OpenTofu state encryption (>= 16 chars), from Infisical /tofu/TOFU_STATE_PASSPHRASE"
  type        = string
  sensitive   = true
  ephemeral   = true
}

# ---- Infisical (where the secrets are; not secrets) --------------------------
variable "infisical_host" {
  description = "Infisical URL. null = infisical_host in ansible/group_vars/all/infisical.yml (default https://app.infisical.com)"
  type        = string
  default     = null
}

variable "infisical_project_id" {
  description = "Infisical project ID. null = infisical_project_id in ansible/group_vars/all/infisical.yml"
  type        = string
  default     = null
}

variable "infisical_env" {
  description = "Infisical environment slug. null = infisical_env in ansible/group_vars/all/infisical.yml (prod)"
  type        = string
  default     = null
}

# ---- Proxmox -----------------------------------------------------------------
variable "proxmox_insecure" {
  description = "Skip TLS verification (PVE self-signed cert)"
  type        = bool
  default     = true
}

variable "vm_datastore" {
  description = "Datastore for VM/LXC disks on each node (installer default for ext4 is local-lvm)"
  type        = string
  default     = "local-lvm"
}

variable "image_datastore" {
  description = "Datastore for ISOs and LXC templates"
  type        = string
  default     = "local"
}

variable "bridge" {
  type    = string
  default = "vmbr0"
}

# ---- Talos / Kubernetes --------------------------------------------------------
variable "cluster_name" {
  type    = string
  default = "artzima"
}

variable "talos_version" {
  description = "Talos release (keep in step with the siderolabs/talos provider)"
  type        = string
  default     = "v1.14.6"
}

variable "kubernetes_version" {
  description = "null = the Talos release's default Kubernetes"
  type        = string
  default     = null
}

variable "talos_extensions" {
  type    = list(string)
  default = ["siderolabs/qemu-guest-agent", "siderolabs/nfs-utils"]
}

variable "talos_vm" {
  description = "Per-VM size (each mini PC hosts one Talos control plane + worker)"
  type = object({
    cores     = number
    memory_mb = number
    disk_gb   = number
  })
  default = {
    cores     = 4
    memory_mb = 12288
    disk_gb   = 100
  }
}

# ---- host01 appliances ---------------------------------------------------------
variable "debian_lxc_template_url" {
  type    = string
  default = "http://download.proxmox.com/images/system/debian-13-standard_13.6-1_amd64.tar.zst"
}

variable "jellyfin_lxc" {
  type = object({
    cores     = number
    memory_mb = number
    disk_gb   = number
  })
  default = {
    cores     = 4
    memory_mb = 4096
    disk_gb   = 32
  }
}

variable "ts_router_lxc" {
  type = object({
    cores     = number
    memory_mb = number
    disk_gb   = number
  })
  default = {
    cores     = 1
    memory_mb = 512
    disk_gb   = 4
  }
}
