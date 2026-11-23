provider "proxmox" {
  endpoint = local.pve_api_url
  # Ephemeral (infisical.tf): provider configuration is never written to state.
  api_token = ephemeral.infisical_secret.proxmox_api_token.value
  insecure  = var.proxmox_insecure
  # No SSH block: everything here goes through the API. Device passthrough
  # (devN:) needs root@pam, so Ansible's lxc-devices playbook adds it.
}

provider "talos" {}
