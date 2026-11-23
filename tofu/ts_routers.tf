# Two Tailscale subnet routers (HA pair) advertising lab_subnet.
# Configured afterwards by Ansible: `make lxc-devices ts-routers`.
resource "proxmox_virtual_environment_container" "ts_router" {
  for_each = local.ts_routers

  node_name     = each.value.proxmox_node
  vm_id         = each.value.vmid
  description   = "Tailscale subnet router for ${local.lab_subnet} (managed by OpenTofu)"
  tags          = ["tailscale", "tofu"]
  unprivileged  = true
  start_on_boot = true

  features {
    nesting = true
  }

  cpu {
    cores = var.ts_router_lxc.cores
  }

  memory {
    dedicated = var.ts_router_lxc.memory_mb
    swap      = 256
  }

  disk {
    datastore_id = var.vm_datastore
    size         = var.ts_router_lxc.disk_gb
  }

  network_interface {
    name        = "eth0"
    bridge      = var.bridge
    mac_address = upper(each.value.mac)
    vlan_id     = local.lab_vlan_id
  }

  initialization {
    hostname = each.key
    dns {
      domain  = local.lab_domain
      servers = local.lab_dns
    }
    ip_config {
      ipv4 {
        address = "${each.value.ip}/${local.lab_prefix}"
        gateway = local.lab_gateway
      }
    }
    user_account {
      keys = local.admin_ssh_keys
    }
  }

  operating_system {
    template_file_id = proxmox_download_file.debian_lxc[each.value.proxmox_node].id
    type             = "debian"
  }

  lifecycle {
    ignore_changes = [device_passthrough] # /dev/net/tun added by Ansible
  }
}
