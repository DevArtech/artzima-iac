#!/bin/bash
# First-boot hook, embedded into the prepared ISOs (prepare-iso --on-first-boot)
# and run once by proxmox-first-boot (ordering = "network-online").
# Keep it tiny (limit 1 MiB) and generic for PVE and PBS: Ansible does the rest.
set -euo pipefail
exec > >(tee -a /var/log/artzima-first-boot.log) 2>&1
echo "artzima first boot: $(date -Is) on $(hostname -f)"

# Root is SSH-key-only: no password logins over SSH.
install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-artzima.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
systemctl reload ssh || systemctl reload sshd || true

# Drop enterprise repos so the first `apt update` from Ansible does not fail
# (the common role then writes the no-subscription repos).
for f in /etc/apt/sources.list.d/pve-enterprise.sources \
         /etc/apt/sources.list.d/pbs-enterprise.sources \
         /etc/apt/sources.list.d/ceph.sources; do
  [ -f "$f" ] && mv "$f" "$f.disabled"
done

# Wake-on-LAN on the pinned primary NIC, if supported (BIOS must allow it too).
if command -v ethtool >/dev/null 2>&1; then
  ethtool -s lan0 wol g || true
fi

echo "artzima first boot done"
