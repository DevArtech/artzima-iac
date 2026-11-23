resource "proxmox_download_file" "debian_lxc" {
  for_each     = local.lxc_nodes
  node_name    = each.key
  content_type = "vztmpl"
  datastore_id = var.image_datastore
  url          = var.debian_lxc_template_url
  overwrite    = false
}
