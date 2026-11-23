# ---------------------------------------------------------------------------
# Talos: 3 VMs (talos-1/2/3, one per mini PC), each control plane + worker,
# with a shared Layer-2 VIP for the Kubernetes API.
# ---------------------------------------------------------------------------

# Image Factory schematic with the extensions we need.
resource "talos_image_factory_schematic" "this" {
  schematic = yamlencode({
    customization = {
      systemExtensions = {
        officialExtensions = var.talos_extensions
      }
    }
  })
}

# nocloud platform: Talos reads its static IP/DNS/hostname from the Proxmox
# cloud-init drive, so the node is reachable on its final IP in maintenance mode.
data "talos_image_factory_urls" "this" {
  talos_version = var.talos_version
  schematic_id  = talos_image_factory_schematic.this.id
  platform      = "nocloud"
  architecture  = "amd64"
}

resource "proxmox_download_file" "talos_iso" {
  for_each     = toset([for n in local.talos_nodes : n.proxmox_node])
  node_name    = each.key
  content_type = "iso"
  datastore_id = var.image_datastore
  file_name    = "talos-${var.talos_version}-${substr(talos_image_factory_schematic.this.id, 0, 12)}-nocloud-amd64.iso"
  url          = data.talos_image_factory_urls.this.urls.iso
  overwrite    = false
}

resource "proxmox_virtual_environment_vm" "talos" {
  for_each = local.talos_nodes

  name        = each.key
  node_name   = each.value.proxmox_node
  vm_id       = each.value.vmid
  description = "Talos ${var.talos_version} control plane + worker (managed by OpenTofu)"
  tags        = ["talos", "k8s", "tofu"]
  on_boot     = true
  machine     = "q35"
  bios        = "seabios"

  scsi_hardware = "virtio-scsi-single"
  boot_order    = ["scsi0", "ide2"] # empty disk falls through to the ISO; after install, disk

  agent {
    enabled = true # qemu-guest-agent system extension
  }

  cpu {
    cores = var.talos_vm.cores
    type  = "host"
  }

  memory {
    dedicated = var.talos_vm.memory_mb
  }

  disk {
    datastore_id = var.vm_datastore
    interface    = "scsi0"
    size         = var.talos_vm.disk_gb
    iothread     = true
    discard      = "on"
    ssd          = true
    file_format  = "raw"
  }

  cdrom {
    interface = "ide2"
    file_id   = proxmox_download_file.talos_iso[each.value.proxmox_node].id
  }

  network_device {
    bridge      = var.bridge
    model       = "virtio"
    mac_address = upper(each.value.mac)
    vlan_id     = local.lab_vlan_id
  }

  initialization {
    datastore_id = var.vm_datastore
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
  }

  operating_system {
    type = "l26"
  }

  lifecycle {
    # Talos upgrades happen in place (talosctl upgrade); the boot ISO can age.
    ignore_changes = [cdrom]
  }
}

# ---------------------------------------------------------------- configs
resource "talos_machine_secrets" "this" {
  talos_version = var.talos_version
}

locals {
  cluster_endpoint = "https://${local.k8s_api_vip}:6443"

  talos_common_patch = yamlencode({
    machine = {
      install = {
        disk  = "/dev/sda"
        image = data.talos_image_factory_urls.this.urls.installer
      }
      kubelet = {
        nodeIP = { validSubnets = [local.lab_subnet] }
      }
      certSANs = [local.k8s_api_vip, "k8s.${local.lab_domain}"]
      features = {
        kubernetesTalosAPIAccess = {
          enabled                     = true
          allowedRoles                = ["os:reader"]
          allowedKubernetesNamespaces = ["kube-system"]
        }
      }
    }
    cluster = {
      allowSchedulingOnControlPlanes = true # every node is also a worker
      apiServer = {
        certSANs = [local.k8s_api_vip, "k8s.${local.lab_domain}"]
      }
      etcd = {
        advertisedSubnets = [local.lab_subnet]
      }
      # Talos' defaults, made explicit from network.yml: the same values are
      # rendered into cluster-vars (Traefik trustedIPs, gluetun kill-switch).
      network = {
        podSubnets     = [local.net.k8s_pod_subnet]
        serviceSubnets = [local.net.k8s_service_subnet]
      }
    }
  })

  # Per-node multi-doc patches (Talos >= 1.12 network config documents).
  talos_node_patches = { for name, n in local.talos_nodes : name => [
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "HostnameConfig"
      hostname   = name
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "LinkAliasConfig"
      name       = "lan0"
      selector   = { match = "mac(link.permanent_addr) == \"${n.mac}\"" }
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "Layer2VIPConfig"
      name       = local.k8s_api_vip
      link       = "lan0"
    }),
  ] }
}

data "talos_machine_configuration" "cp" {
  for_each           = local.talos_nodes
  cluster_name       = var.cluster_name
  cluster_endpoint   = local.cluster_endpoint
  machine_type       = "controlplane"
  machine_secrets    = talos_machine_secrets.this.machine_secrets
  talos_version      = var.talos_version
  kubernetes_version = var.kubernetes_version
  docs               = false
  examples           = false
  config_patches     = concat([local.talos_common_patch], local.talos_node_patches[each.key])
}

data "talos_client_configuration" "this" {
  cluster_name         = var.cluster_name
  client_configuration = talos_machine_secrets.this.client_configuration
  endpoints            = [for n in local.talos_nodes : n.ip]
  nodes                = [for n in local.talos_nodes : n.ip]
}

resource "talos_machine_configuration_apply" "cp" {
  for_each                    = local.talos_nodes
  depends_on                  = [proxmox_virtual_environment_vm.talos]
  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.cp[each.key].machine_configuration
  node                        = each.value.ip
  endpoint                    = each.value.ip
}

locals {
  bootstrap_node = sort(keys(local.talos_nodes))[0]
}

resource "talos_machine_bootstrap" "this" {
  depends_on           = [talos_machine_configuration_apply.cp]
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = local.talos_nodes[local.bootstrap_node].ip
  endpoint             = local.talos_nodes[local.bootstrap_node].ip
}

resource "talos_cluster_kubeconfig" "this" {
  depends_on           = [talos_machine_bootstrap.this]
  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = local.talos_nodes[local.bootstrap_node].ip
  endpoint             = local.talos_nodes[local.bootstrap_node].ip
}

data "talos_cluster_health" "this" {
  depends_on             = [talos_cluster_kubeconfig.this]
  client_configuration   = talos_machine_secrets.this.client_configuration
  control_plane_nodes    = [for n in local.talos_nodes : n.ip]
  endpoints              = [for n in local.talos_nodes : n.ip]
  skip_kubernetes_checks = true # no CNI checks before Flux; Talos ships flannel by default
}

# ---------------------------------------------------------------- outputs to files (gitignored)
resource "local_sensitive_file" "talosconfig" {
  content         = data.talos_client_configuration.this.talos_config
  filename        = "${path.module}/out/talosconfig"
  file_permission = "0600"
}

resource "local_sensitive_file" "kubeconfig" {
  content         = talos_cluster_kubeconfig.this.kubeconfig_raw
  filename        = "${path.module}/out/kubeconfig"
  file_permission = "0600"
}
