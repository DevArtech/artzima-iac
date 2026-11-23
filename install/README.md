# install/: hands-off Proxmox VE and PBS installs

This uses the official automated installer
([wiki](https://pve.proxmox.com/wiki/Automated_Installation)). We prepare one
**PVE ISO**, used for host01 and node01-03, and one **PBS ISO**, used for the ZimaBoard. Both are
prepared with `--fetch-from http`. At boot, each installer POSTs its system info
(including every NIC MAC) to `answer_server.py`. The server replies with the
`answer.toml` rendered for that MAC. It refuses a PBS host that boots the PVE ISO, and the
reverse.

| File | Purpose |
|---|---|
| `render_answers.py` | Inventory + admin keys/mailbox (from Infisical) to one answer per MAC, **in memory**. Run directly (`make answers`): validate in a temp dir, then delete |
| `answer_server.py` | Stdlib-only HTTP server: renders at start, then `POST /answer`, `POST /installed` (webhook) |
| `first-boot.sh` | Embedded first-boot hook: SSH key-only, disable enterprise repos, WoL |
| `prepare-iso.sh` | Wraps `proxmox-auto-install-assistant prepare-iso` |

## What the answer files set

- The FQDN is `<host>.artzima.local`. The static IP is `cidrhost(lab_subnet, host_index)`, the gateway is
  `lab_gateway`, and DNS is the first entry of `lab_dns`. The NIC is chosen by `ID_NET_NAME_MAC` (by MAC) and
  pinned to the name `lan0`. That pinning needs PVE 9.1+ / PBS 4.1+ ISOs.
- The disk is `ext4` on `install_disk` from `ansible/hosts.yml`. **Verify the disk names**
  (see below) before the real run.
- `root-ssh-keys` come from Infisical `/settings/ADMIN_SSH_KEYS` and `mailto` from
  `/settings/ADMIN_EMAIL`. `make answers` and `make answer-server` fetch them into the process
  environment (`scripts/infisical_exec.py`). They are never written to disk or git.
- No password hash is ever in git or on disk: `root-password-hashed` holds a random,
  discarded password (root is SSH-key-only), unless the optional Infisical secret
  `/bare-metal/ROOT_PASSWORD_HASHED` exists.
- `first-boot` runs `first-boot.sh` once the network is up.

## Steps

1. **Fill in the inventory and Infisical.** Check `mac`, `install_disk` and `install_disk_serial`
   per host in `ansible/hosts.yml`: the server answers ONLY known MACs, and each answer file
   selects the target disk by its udev `ID_SERIAL` (`filter-match = "all"`, `ID_SERIAL =
   "*<serial>*"`), so a wrong disk is never wiped; if the installer reports the disks it sees and
   the serial is not among them, the server refuses (HTTP 412). In Infisical, set
   `/settings/ADMIN_SSH_KEYS` and `/settings/ADMIN_EMAIL`; the admin identity is in
   `~/.config/artzima/` ([OPERATOR-SETUP](../docs/OPERATOR-SETUP.md)).
2. **Install the assistant** (it needs Debian 13). Without a Proxmox host, use a throwaway
   container:
   ```bash
   podman run --rm -it -v "$PWD:/w" -w /w debian:trixie bash
   apt update && apt install -y wget xorriso
   wget -O /usr/share/keyrings/proxmox-archive-keyring.gpg \
     https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg
   echo "deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pve trixie pve-no-subscription" \
     > /etc/apt/sources.list.d/pve.list
   apt update && apt install -y proxmox-auto-install-assistant
   ```
3. **Download the ISOs** (PVE 9.1 or newer, PBS 4.1 or newer) from <https://www.proxmox.com/downloads>.
4. **Render the answers and prepare the ISOs.** `<ws-ip>` is the workstation's LAN IP. On WSL2, use
   mirrored networking or a port proxy so the LAN can reach port 8000.
   ```bash
   make answers POSTINST_WEBHOOK_URL=http://<ws-ip>:8000/installed   # validates, writes nothing
   install/prepare-iso.sh http://<ws-ip>:8000/answer proxmox-ve_*.iso proxmox-backup-server_*.iso
   ```
5. **Write the USB sticks.** This destroys the stick's contents; check the device with `lsblk` first:
   ```bash
   sudo dd if=install/iso/proxmox-ve_*-artzima-auto.iso of=/dev/sdX bs=4M conv=fsync status=progress
   ```
   On Windows, use Rufus in **DD mode**, or balenaEtcher.
6. **Serve the answers:** `make answer-server POSTINST_WEBHOOK_URL=http://<ws-ip>:8000/installed`.
   It renders every answer in memory at start. Keep it running until all 5 hosts finish.
7. **Boot each machine from USB.** You may need the one-time boot menu: F7/F11/F12/Esc, depending on the model.
   The ISO auto-selects "Automated Installation" after 10 s, fetches its
   answer, installs and reboots. The server log shows which file it served. The
   webhook saves `answers/installed/<host>.json` with the SSH host keys.
8. **Clean up.** Stop the server. Nothing secret was written. `make clean-answers` deletes the
   webhook reports, which hold the host SSH public keys.

**Debugging:** choose "Advanced Options > Install Proxmox VE (Automated)" with the debug
shell. Then run `proxmox-auto-install-assistant device-info -t disk` (or `-t network`)
to check disk and NIC names, and `proxmox-fetch-answer` to test the fetch.

**VLAN later:** the installer only does untagged networking. Install on an
untagged or native port, and Ansible's `network` role then moves management to
the tagged `vmbr0.<lab_vlan_id>`.
