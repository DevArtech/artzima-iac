output "k8s_api_endpoint" {
  value = local.cluster_endpoint
}

output "talos_nodes" {
  value = { for k, n in local.talos_nodes : k => { ip = n.ip, proxmox_node = n.proxmox_node } }
}

output "talos_installer_image" {
  value = data.talos_image_factory_urls.this.urls.installer
}

output "files" {
  value = {
    kubeconfig  = local_sensitive_file.kubeconfig.filename
    talosconfig = local_sensitive_file.talosconfig.filename
  }
}

output "talos_health" {
  value = data.talos_cluster_health.this.id
}
