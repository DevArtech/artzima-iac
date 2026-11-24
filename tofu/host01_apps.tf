# ---------------------------------------------------------------------------
# host01 (laptop, NVIDIA GPU): the Jellyfin LXC.
# Kept outside Kubernetes on purpose (docs/decisions.md).
# ---------------------------------------------------------------------------

resource "proxmox_virtual_environment_container" "jellyfin" {
  node_name     = local.jellyfin.proxmox_node
  vm_id         = local.jellyfin.vmid
  description   = "Jellyfin with NVIDIA NVENC (managed by OpenTofu; devices by Ansible)"
  tags          = ["media", "tofu"]
  unprivileged  = true
  start_on_boot = true

  features {
    nesting = true
  }

  cpu {
    cores = var.jellyfin_lxc.cores
  }

  memory {
    dedicated = var.jellyfin_lxc.memory_mb
    swap      = 512
  }

  disk {
    datastore_id = var.vm_datastore
    size         = var.jellyfin_lxc.disk_gb
  }

  network_interface {
    name        = "eth0"
    bridge      = var.bridge
    mac_address = upper(local.jellyfin.mac)
    vlan_id     = local.lab_vlan_id
  }

  initialization {
    hostname = "jellyfin"
    dns {
      domain  = local.lab_domain
      servers = local.lab_dns
    }
    ip_config {
      ipv4 {
        address = "${local.jellyfin.ip}/${local.lab_prefix}"
        gateway = local.lab_gateway
      }
    }
    user_account {
      keys = local.admin_ssh_keys
    }
  }

  operating_system {
    template_file_id = proxmox_download_file.debian_lxc[local.jellyfin.proxmox_node].id
    type             = "debian"
  }

  lifecycle {
    # GPU device lines (dev0..devN from hosts.yml lxc_devices) and the read-only
    # media bind mount (mp0: /data/media, hosts.yml lxc_mounts) are added by
    # `make lxc-devices`, because only root@pam may set them.
    ignore_changes = [device_passthrough, mount_point]
  }
}
