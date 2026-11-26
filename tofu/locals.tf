# Everything about the lab comes from the Ansible inventory: the single source
# of truth. Nothing here hard-codes an address, MAC or name.
locals {
  inv = yamldecode(file("${path.module}/../ansible/hosts.yml")).all.children
  net = yamldecode(file("${path.module}/../ansible/group_vars/all/network.yml"))
  lab = yamldecode(file("${path.module}/../ansible/group_vars/all/lab.yml"))
  inf = yamldecode(file("${path.module}/../ansible/group_vars/all/infisical.yml"))

  infisical_host       = coalesce(var.infisical_host, local.inf.infisical_host)
  infisical_project_id = coalesce(var.infisical_project_id, local.inf.infisical_project_id)
  infisical_env        = coalesce(var.infisical_env, local.inf.infisical_env)

  lab_subnet  = local.net.lab_subnet
  lab_prefix  = tonumber(split("/", local.lab_subnet)[1])
  lab_gateway = local.net.lab_gateway
  lab_vlan_id = local.net.lab_vlan_id # null = untagged
  lab_dns     = local.net.lab_dns
  lab_domain  = local.net.lab_domain

  ip = { for name, h in merge(
    local.inv.proxmox.children.pve_nodes.hosts,
    local.inv.proxmox.children.pbs_servers.hosts,
    local.inv.unmanaged.hosts,
    local.inv.talos.hosts,
    local.inv.ts_routers.hosts,
    local.inv.appliances.hosts,
  ) : name => cidrhost(local.lab_subnet, h.host_index) }

  k8s_api_vip = cidrhost(local.lab_subnet, local.net.k8s_api_vip_index)
  pve_api_url = "https://${local.ip["host01"]}:8006/"

  talos_nodes = { for name, h in local.inv.talos.hosts : name => merge(h, {
    ip   = local.ip[name]
    mac  = lower(h.mac)
    vmid = h.host_index
  }) }

  ts_routers = { for name, h in local.inv.ts_routers.hosts : name => merge(h, {
    ip   = local.ip[name]
    vmid = h.host_index
  }) }

  jellyfin = merge(local.inv.appliances.hosts.jellyfin, {
    ip = local.ip["jellyfin"], vmid = local.inv.appliances.hosts.jellyfin.host_index
  })

  llm = merge(local.inv.appliances.hosts.llm, {
    ip = local.ip["llm"], vmid = local.inv.appliances.hosts.llm.host_index
  })

  # Nodes that need the Debian LXC template.
  lxc_nodes = toset(concat([local.jellyfin.proxmox_node, local.llm.proxmox_node], [for r in local.ts_routers : r.proxmox_node]))

  # Root SSH public keys: personal, so not in git. Infisical /settings/ADMIN_SSH_KEYS
  # (one key per line). Public keys: they may appear in state and plans.
  admin_ssh_keys = [for k in split("\n", nonsensitive(try(data.infisical_secrets.settings.secrets["ADMIN_SSH_KEYS"].value, ""))) :
  trimspace(k) if trimspace(k) != ""]
}
