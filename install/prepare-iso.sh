#!/usr/bin/env bash
# Prepare the auto-install ISOs (one PVE ISO for host01/node01-03, one PBS ISO
# for the ZimaBoard). Both fetch their answer from the same answer server, which
# picks the file by MAC. Needs `proxmox-auto-install-assistant` and `xorriso`
# (Debian 13 + the Proxmox repo; see install/README.md for a container recipe).
#
# Usage: install/prepare-iso.sh <answer-url> <proxmox-ve.iso> [proxmox-backup-server.iso]
#   e.g. install/prepare-iso.sh http://192.168.x.y:8000/answer \
#          ~/Downloads/proxmox-ve_9.1-1.iso ~/Downloads/proxmox-backup-server_4.1-1.iso
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
url="${1:?answer URL, e.g. http://<workstation-ip>:8000/answer}"
shift
command -v proxmox-auto-install-assistant >/dev/null || {
  echo "proxmox-auto-install-assistant not found (see install/README.md)" >&2; exit 1; }
mkdir -p "$here/iso"
for src in "$@"; do
  out="$here/iso/$(basename "${src%.iso}")-artzima-auto.iso"
  proxmox-auto-install-assistant prepare-iso "$src" \
    --fetch-from http \
    --url "$url" \
    --on-first-boot "$here/first-boot.sh" \
    --output "$out"
  proxmox-auto-install-assistant inspect-iso "$out" || true
  echo "prepared: $out"
done
