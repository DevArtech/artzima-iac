# Disaster recovery runbook

**Public reference runbook:** addresses and the project slug below are fictional examples. Restore private configuration in B5 before server recovery. For the emergency kit, print `make dr-pdf`; it uses the private deployment copy with the actual network details.

**For the operator, alone.** It assumes the worst: a fire took the apartment, so every machine
is gone, including the lab, the NAS, the workstation, **Headquarters and G.A.R.T.H.** Nothing
here needs them, or any machine that was at home. You need:

- the **emergency kit** (printed: this runbook from `make dr-pdf`, plus the checklist at the end);
- **any laptop** with Linux or macOS and internet access (a borrowed one is fine; nothing
  secret stays on it if you follow the clean-up in F6);
- for the lab: replacement hardware (or the surviving machines, for a partial loss).

How to read it: every step is **numbered**, its commands can be copied as they are (replace only
`<angle brackets>`), **Expect** says what you should see, and **If this fails** says what to try.
Do the phases in order: **A** accounts → **B** laptop → **C** your files (rips, personal shares) →
**D** the lab → **E** app data → **F** verify. For anything smaller than a total loss, start at
[Partial loss](#partial-loss) instead.

What survives off-site, and what doesn't:

| Off-site | Holds |
|---|---|
| **GitHub** | this repository (public): <https://github.com/devartech/artzima-iac>, and the private app images (GHCR) |
| **Infisical** (cloud) | every secret and private setting ([secrets](secrets.md)) |
| **Backblaze B2** (`s3.us-east-005.backblazeb2.com`) | `artzima-nas` (rips + personal files, restic via Backrest), `artzima-restic` (app volumes), `artzima-pg` (Postgres base backups + WAL), `artzima-pbs` (Proxmox guests). Object Lock: nothing uploaded in the last 30 days can be deleted, not even by a stolen key |
| **Cloudflare** | the zone; the (now dead) tunnel and DNS records |
| **Tailscale** | the tailnet, its policy, the (now offline) devices |
| **The emergency kit** | what gets you back *into* all of the above |

**Not recoverable:** Jellyfin's users, watch history and metadata (the library is re-scanned); the
OpenTofu state files (they lived on the workstation); the on-NAS-only copies (Vaultwarden's
encrypted NAS dumps, CNPG volume snapshots, the Paperless export folder, the local PBS datastore).
Their data is covered by the off-site layers.

**The one rule throughout:** a rebuilt, still-empty system must never back up over the last good
snapshots. The pause switches (D2) stay on until F4.

---

## 0. Decide

### 0.1 What was lost?

| Lost | Go to |
|---|---|
| Everything (fire, theft, flood) | phases **A → F**, in order |
| One Proxmox machine (host01, node01-03 or pbs) | [P1](#p1-one-machine) |
| The NAS only (the lab still runs) | [P2](#p2-the-nas-only) |
| Infisical only (account locked, project deleted, vendor gone); the lab runs | [P3](#p3-infisical-only) |
| The workstation only | [P4](#p4-the-workstation-only) |

### 0.2 Same hardware or new?

- **Same models** (insurance replacement of identical machines): only MACs and disk serials change
  (D1). Everything else in the repo fits.
- **Different machines:** also check `install_disk`, the NIC name (`primary_nic_name` in
  `ansible/group_vars/all/lab.yml`), the GPU on host01 (`lxc_devices` in `ansible/hosts.yml`) and
  the Talos VM sizes (`tofu/variables.tf`). A different NAS model: anything that runs UGOS with
  Docker and NFS works unchanged; another OS needs the NFS export, Docker and an SSH user with sudo.
- **No hardware yet:** do A, B and C anyway (C can restore your files onto a USB disk). The lab
  can wait for weeks: the backups in B2 stay, and nothing deletes them while no job runs.

### 0.3 Time and cost (estimates; check prices on the day)

| Phase | Your time | Waiting time |
|---|---|---|
| A accounts | 1-3 h | up to days if an account needs support to recover |
| B laptop + tools | 30-60 min | |
| C files from B2 | 1-2 h | download: `hours ≈ TB × 8,000,000 / Mbit/s / 3600`, see C.6 |
| D lab rebuild | 4-6 h | hardware delivery; USB installs ~20 min per machine; `make up` 1.5-3 h |
| E app data | 1-2 h | Postgres recovery 10-30 min; volumes 10-60 min |
| F verify | 1 h, plus a check the next morning | the first nightly backups |

| Cost item | Estimate (2026-11) |
|---|---|
| B2 download (egress) | **free** up to 3× your average stored data per month; a full restore is 1× ([backups: costs](backups.md#cost-estimate-inputs-b2-checked-2026-11-23)) |
| B2 storage | continues as before: $6.95 per TB-month |
| Hardware, like for like | rough: 3 mini PCs $250-500 each, a GPU laptop $800-1500, ZimaBoard ~$200, UGREEN NAS $400-900 + disks ~$20-30 per TB, UPS ~$150, USB sticks, a switch |
| Laptop for the recovery | any you have; otherwise $300+ |

### 0.4 Point in time

Write down **when the disaster happened, in UTC** (e.g. `2026-12-01T03:00:00Z`): every restore
picks the newest snapshot **before** that moment.

```bash
export DR_TIME='<YYYY-MM-DDTHH:MM:SSZ>'      # used in C, E
```

---

## A. Accounts

Use the emergency kit's recovery codes. After each recovery, **write down the new recovery
codes** (they change when you use one) for the new kit (F5).

| Step | Account | Do | Expect / if this fails |
|---|---|---|---|
| A1 | **Mailbox** every account resets to | log in with the kit's password + recovery code | inbox visible. Fails: the provider's account recovery (can take days); everything else waits on it |
| A2 | **Infisical** <https://app.infisical.com> | log in (kit: login + 2FA recovery code) | **two projects**, environment **prod** in each: main **example-project** (`/settings`, `/ansible`, `/tofu`, `/operator`) and the cluster's **example-k8s-project** (`/k8s/...`) present. Fails: Infisical support; meanwhile [P3](#p3-infisical-only) and C (the kit has the restic passwords) |
| A3 | **Backblaze** <https://secure.backblaze.com> | log in | *Buckets*: `artzima-nas`, `artzima-restic`, `artzima-pg`, `artzima-pbs` with their usual sizes. **Change nothing.** Fails: the kit's read-all key still works for C |
| A4 | **GitHub** <https://github.com/login> | log in (2FA recovery code) | repo `devartech/artzima-iac`. Not needed to *read* (it's public); needed to push (D2) and for the Flux token |
| A5 | **Cloudflare** <https://dash.cloudflare.com> | log in | the zone; *Zero Trust → Networks → Tunnels*: `artzima` (down) |
| A6 | **Tailscale** <https://login.tailscale.com> | log in through its identity provider | *Machines*: devices from the failed deployment, offline. Remove them now (*… → Remove*) |
| A7 | **Vaultwarden** | not reachable until E. The kit has its master password; a phone/laptop **away from home** with the Bitwarden app has an encrypted offline copy | |

---

## B. Laptop

### B1. Prerequisites

- **macOS:** open *Terminal*, run `xcode-select --install` (git, make, ssh).
  **Expect:** a dialog, then "command line developer tools were installed".
- **Debian/Ubuntu:** `sudo apt update && sudo apt install -y git make curl bzip2 openssh-client`
- **Windows:** use WSL2 (`wsl --install -d Ubuntu`), then the Debian/Ubuntu line inside it.

### B2. The repository

```bash
git clone https://github.com/devartech/artzima-iac.git ~/artzima-iac && cd ~/artzima-iac
git log -1 --format='%h %cs %s'
```
**Expect:** a commit hash and date. Compare with the footer of your printout: if the repo is newer,
**read the repo's `docs/disaster-recovery.md`** instead of the paper.
**If this fails:** GitHub down → `git clone` again later; the repo is also on every developer
machine you may have pushed from. The runbook on paper is enough for A-C.

### B3. Tools

```bash
scripts/dr-tools.sh
```
**Expect** (5-10 min), ending with:
```
restic     restic 0.20.0 ...
tofu       OpenTofu v1.13.9
kubectl    Client Version: v1.37.2
flux       flux: v2.10.1
infisical  infisical version 0.45.14
uv         uv 0.13.9 ...
ansible    ansible [core 2.22.0]
talosctl   Tag: v1.14.6
```
Then, **in every new terminal**:
```bash
cd ~/artzima-iac && eval "$(scripts/dr-tools.sh --env)"
```
**If this fails:** `CHECKSUM MISMATCH` = the download is not what the repo pinned: retry on another
network; never bypass it. `missing 'bzip2'` etc.: B1. A project moved its downloads: the error
shows the URL; get the same version from the project's release page and check the SHA256 printed
in `scripts/dr-tools.sh`.

### B4. SSH key for this laptop

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C artzima-dr      # set a passphrase
cat ~/.ssh/id_ed25519.pub
```
- GitHub → *Settings → SSH and GPG keys → New SSH key*: paste it. Then
  `git remote set-url origin git@github.com:devartech/artzima-iac.git && ssh -T git@github.com`.
  **Expect:** "Hi devartech! You've successfully authenticated".
- Infisical → `/settings/ADMIN_SSH_KEYS`: **replace** the old workstation's line with this key
  (the new machines get it as root's key).

### B5. Infisical access for `make`

1. Project IDs: Infisical → project **example-project** → *Settings* → **Copy Project ID**; the same for
   the cluster's project **example-k8s-project**.
2. New client secret for the admin identity (the old one burned): *Organization → Access Control
   → Identities → artzima-admin → Universal Auth*: **revoke** every existing client secret, then
   **Create client secret** (copy it now: it is shown once). Note the **Client ID** shown there.
3. Write the two local files (values are typed, never echoed). Use your actual project slugs from Infisical, not the public examples:
   ```bash
   mkdir -p ~/.config/artzima && chmod 700 ~/.config/artzima
   ( umask 077; printf 'INFISICAL_HOST=https://app.infisical.com\nINFISICAL_PROJECT_ID=%s\nINFISICAL_PROJECT_SLUG=%s\nINFISICAL_K8S_PROJECT_ID=%s\nINFISICAL_K8S_PROJECT_SLUG=%s\nINFISICAL_ENV=prod\n' \
     "$(read -rp 'main project id: ' x; echo "$x")" "$(read -rp 'main project slug: ' x; echo "$x")" \
     "$(read -rp 'k8s project id: ' x; echo "$x")" "$(read -rp 'k8s project slug: ' x; echo "$x")" > ~/.config/artzima/infisical-project.env )
   ( umask 077; read -rp 'client id: ' id; read -rsp 'client secret: ' sec; echo
     printf 'INFISICAL_UNIVERSAL_AUTH_CLIENT_ID=%s\nINFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET=%s\n' "$id" "$sec" \
       > ~/.config/artzima/infisical.env )
   ```
4. The CLI, for reading single values in C and E: `infisical login` (choose *Infisical Cloud*,
   US region; a browser opens). On a machine without a browser: `infisical login --interactive`.
5. Restore the private inventory, then use the private deployment tree for the remaining commands:
   ```bash
   make private-restore
   make private-prepare
   cd .private/runtime
   make preflight PREFLIGHT_ARGS=--accounts-only
   ```
   **Expect:** private configuration restored, then `PASS  Infisical: operator keys present`.
   If the private backup is unavailable, recover the offline private configuration copy from the kit. Never deploy the public example inventory.
   **If this fails:** `401`/`invalid credentials` → step 2 again (whitespace in the pasted secret);
   `N missing, e.g. …` → a root credential was deleted: recreate it ([OPERATOR-SETUP A2](OPERATOR-SETUP.md#a2-accounts-and-the-other-four-root-credentials)).

---

## C. Your files first: rips and personal shares (no lab needed)

The NAS backup is a restic repository in `artzima-nas`, written by Backrest. Snapshot paths are
the container paths: `/userdata/media` (the media library) and `/userdata/<share>` (each personal
share in `nas_personal_shares`, `ansible/group_vars/all/backups.yml`).

### C1. Where to restore

- **(a) New NAS** (recommended; data is downloaded once, straight to its disks): set it up first:
  UGOS initial setup, then *Control Panel → Network*: **IPv4 manual** `192.0.2.156/24`, gateway and DNS
  `192.0.2.1` (it lives on the lab LAN behind the Firewalla, D6; a reservation is not needed), create the shared folder **`Artzima`** on
  `volume1` and **every personal share with its old name**, enable **SSH** (*Control Panel →
  Terminal*), install the **Docker** app. Your UGOS user must be an administrator.
  The laptop reaches it only from the lab side: a cable to the lab switch with a static
  `192.0.2.x/24` outside the DHCP pool (for example `192.0.2.72`, which is free once the workstation is
  gone), or a lease from the Firewalla pool. A laptop on the home Wi-Fi can't reach the lab
  until the Tailscale subnet routers exist again (D10).
- **(b) A USB disk on the laptop** (no NAS yet): format it (exFAT for both macOS and Linux, or
  ext4/APFS), mount it, and use the laptop's `restic` from B3. Later copy it onto the NAS.

### C2. Credentials (in this shell only)

From Infisical (A2 worked):
```bash
export AWS_ACCESS_KEY_ID="$(infisical secrets get NAS_B2_KEY_ID --env=prod --path=/ansible --projectId="$(grep ^INFISICAL_PROJECT_ID ~/.config/artzima/infisical-project.env | cut -d= -f2)" --plain --silent)"
export AWS_SECRET_ACCESS_KEY="$(infisical secrets get NAS_B2_APPLICATION_KEY --env=prod --path=/ansible --projectId="$(grep ^INFISICAL_PROJECT_ID ~/.config/artzima/infisical-project.env | cut -d= -f2)" --plain --silent)"
export RESTIC_PASSWORD="$(infisical secrets get NAS_RESTIC_PASSWORD --env=prod --path=/ansible --projectId="$(grep ^INFISICAL_PROJECT_ID ~/.config/artzima/infisical-project.env | cut -d= -f2)" --plain --silent)"
export RESTIC_REPOSITORY='s3:https://s3.us-east-005.backblazeb2.com/artzima-nas/restic'
```
Or from the **kit** (Infisical unreachable): type them without echo:
```bash
read -rp 'B2 read-all key id: ' AWS_ACCESS_KEY_ID; read -rsp 'B2 read-all key: ' AWS_SECRET_ACCESS_KEY; echo
read -rsp 'NAS restic password: ' RESTIC_PASSWORD; echo
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY RESTIC_PASSWORD
export RESTIC_REPOSITORY='s3:https://s3.us-east-005.backblazeb2.com/artzima-nas/restic'
```

### C3. Pick the snapshots

```bash
restic snapshots --compact --path /userdata/media
restic snapshots --compact              # all paths: one line per personal-share snapshot too
```
**Expect:** a table `ID  Time  Host  Tags  Paths`, host `backrest-artnas`, the newest a few hours
before `DR_TIME`. Note the **ID of the last snapshot before `DR_TIME`** for the media library and
for each share. Never use `latest` blindly: a snapshot taken during the disaster may be partial.
**If this fails:** `wrong password or no key found` → wrong `RESTIC_PASSWORD` (it is the NAS
repo's, not the cluster's); `Access Denied`/`403` → wrong key id/key, or a key limited to another
bucket; `repository does not exist` → typo in `RESTIC_REPOSITORY` (check the bucket name in A3).

### C4. Restore: personal files first (small, irreplaceable), then the media library

**(a) On the new NAS** (run in an SSH session on the NAS: `ssh <your user>@192.0.2.156`, then repeat
the `export` lines of C2 there; the B2 values can be pasted):
```bash
R="sudo --preserve-env=AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY,RESTIC_PASSWORD,RESTIC_REPOSITORY \
  docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e RESTIC_PASSWORD -e RESTIC_REPOSITORY \
  -v /volume1:/volume1 docker.io/restic/restic:0.20.0"
$R snapshots --compact
$R restore --verify <share snapshot id>:/userdata/<share> --target /volume1/<Share>   # one per personal share
$R restore --verify <media snapshot id>:/userdata/media --target /volume1/Artzima/data/media
```
**(b) On the laptop, to a USB disk:**
```bash
restic restore --verify <share snapshot id>:/userdata/<share> --target /Volumes/<disk>/<Share>     # macOS
restic restore --verify <media snapshot id>:/userdata/media   --target /media/$USER/<disk>/media   # Linux
```
**Expect:** a progress line, then `Summary: Restored N files/dirs (X GiB) in hh:mm:ss` and
`verified N files`, no `ignoring error`. An interrupted restore can simply be **run again**: restic
skips files that are already complete.
**If this fails:** `no space left` → the target is too small (`restic stats <id> --mode restore-size`
shows the size); `permission denied` on the NAS → you forgot `sudo` / the user is not admin;
`docker: command not found` → Docker app not installed (C1); very slow → see C6, or let it run overnight.

### C5. Owners and a check

```bash
sudo chown -R 1000:1000 /volume1/Artzima/data          # the media stack's uid/gid (media_puid/pgid)
$R check --read-data-subset=1%                          # optional: restic's own integrity check
```
Spot-check: open a few photos/documents and one film. Compare sizes with
`$R stats <id> --mode restore-size`.

### C6. How long, how much

| Downlink | Time per TB |
|---|---|
| 100 Mbit/s | ~22 h |
| 300 Mbit/s | ~7.5 h |
| 500 Mbit/s | ~4.5 h |
| 1 Gbit/s | ~2.2-3 h (limited by the NAS disks/CPU) |

The download is free (0.3). **Backrest must not run yet**: when it is installed in D10 it stays
paused (D2) until F4, or a half-restored NAS would become the newest snapshot.

---

## D. The lab

### D1. Inventory (the repo on the laptop)

For every machine in `ansible/hosts.yml` (host01, node01-03, pbs, artnas):
- `mac`: the primary NIC's MAC (sticker, BIOS, or later the installer's debug shell `ip -br link`);
- `install_disk` and **`install_disk_serial`** (in the installer's debug shell:
  `proxmox-auto-install-assistant device-info -t disk` and
  `udevadm info -q property -n /dev/<disk> | grep ^ID_SERIAL=`; a unique part is enough);
- for pbs: `pbs_datastore_disk_serial`, the external USB datastore disk (`lsblk -d -o NAME,SERIAL,TRAN`).
  If the old USB disk **survived**, plug it in and leave `pbs_datastore_format_disk: false`:
  `make up` reuses the datastore on it (its backups come back with it; a disk labelled `artzima-pbs`
  is never formatted again anyway). For a **new** disk, record its serial in `pbs_datastore_disk_serial`
  **and** `pbs_datastore_format_confirm_serial`, and set `pbs_datastore_format_disk: true` for that one
  build only, as in [OPERATOR-SETUP B1](OPERATOR-SETUP.md#b1-install-and-deploy). Then set it back to
  `false`. Unplug the disk during the PBS install (the installer targets the eMMC by serial).

For a first boot just to read these: boot the prepared USB stick (D7) and choose *Advanced Options →
Install (Automated) with debug shell*; the answer server refuses unknown machines, so nothing is
installed. New router or subnet: `ansible/group_vars/all/network.yml` too.

### D2. Rebuild switches + Postgres recovery (in git, before anything runs)

1. In `ansible/group_vars/all/backups.yml`:
   ```yaml
   backup_volsync_paused: true     # no backups of empty, rebuilt volumes; retention suspended too
   nas_backup_paused: true         # Backrest + the NAS maintenance container: no schedules
   ```
2. In **each** `k8s/apps/<app>/database.yaml` with a barman plugin (today `vaultwarden`, `paperless`):
   replace `bootstrap.initdb` with a recovery, and archive from now on under a **new**
   `serverName` (the old one holds the history; barman refuses to archive into it). Vaultwarden:
   ```yaml
   spec:
     bootstrap:
       recovery:
         source: origin
         database: vaultwarden
         owner: vaultwarden
         recoveryTarget:
           targetTime: "<DR_TIME as 2026-12-01 03:00:00+00>"   # or delete these two lines = all archived WAL
     externalClusters:
       - name: origin
         plugin:
           name: barman-cloud.cloudnative-pg.io
           parameters:
             barmanObjectName: b2
             serverName: vaultwarden-db          # where the OLD cluster archived
     plugins:
       - name: barman-cloud.cloudnative-pg.io
         isWALArchiver: true
         parameters:
           barmanObjectName: b2
           serverName: vaultwarden-db-dr1        # the NEW cluster archives here
   ```
   Paperless: `database: paperless`, `owner: paperless`, `paperless-db` → `paperless-db-dr1`.
3. Commit and push:
   ```bash
   make render && make lint
   git add -A && git commit -m "DR: pause backups, recover Postgres from B2" && git push
   ```
   **Expect:** `rendered files up to date` … `lint: all green`; `make render` also points the
   retention and restore-test jobs at the `-dr1` archives. (`make lint` needs the extra tools of
   `make deps`, Linux amd64 only: on macOS run `make render` and push; CI lints the push.)
   **If this fails:** a yamllint indentation error → compare with the block above.

### D3. Cloudflare: remove the dead tunnel

Its OpenTofu state burned; the objects still exist. Dashboard:
- *Zero Trust → Networks → Tunnels → artzima → Delete* (force-delete if it complains about connections);
- *your domain → DNS → Records*: delete the CNAMEs of every `/apps.yaml` name (`<name>.<domain>`,
  target `….cfargotunnel.com`);
- *Zero Trust → Access → Applications*: delete those named `artzima …` (if any).

`make bootstrap` (D7) creates them fresh and writes the new tunnel token into Infisical.

### D4. Backblaze: re-adopt the buckets (never delete them)

```bash
make b2-init
# Bucket IDs: B2 web UI -> Buckets -> each bucket -> "Bucket ID"
for b in pg restic pbs nas; do
  read -rp "bucket id of artzima-$b: " id
  ~/.local/share/artzima-tools/venv/bin/python scripts/infisical_exec.py \
    --set TF_VAR_state_passphrase=/tofu/TOFU_STATE_PASSPHRASE -- \
    tofu -chdir=tofu/backblaze import "b2_bucket.backup[\"$b\"]" "$id"
done
make b2-plan
```
**Expect:** four `Import successful!`, then a plan with **0 to destroy**, the 7 new keys (a
backup and a maintenance key per bucket, one for pbs) and their Infisical entries to add, and at
most in-place updates of the buckets. **STOP if the plan wants to destroy or replace a bucket**
(OpenTofu refuses anyway: `prevent_destroy`): compare `ansible/group_vars/all/backups.yml`
(names, `b2_object_lock_days`) with the real buckets.
After D7 has applied it: B2 web UI → *Application Keys*: delete the **old** `artzima-*` keys (the
new ones have new IDs). Keep the admin key and the kit's read-all key.

### D5. Identities

- Infisical → *Organization → Access Control → Identities*: **delete `artzima-eso`** (its state
  burned; D7 creates a new one).
- Tailscale: the dead machines are gone (A6). The OAuth client still works.

### D6. Network and BIOS

- **Firewalla** (kit: router login): if it survived, nothing changes. Hosts are static and keep their
  addresses after a hardware swap, because the pool `192.0.2.210`-`.249` never overlaps them. If it
  was lost, set up the replacement in **Router mode** exactly as in
  [manual steps §2.1](manual-steps.md#21-firewalla-router-mode): WAN to the Xfinity, LAN `192.0.2.1/24`
  to the lab switch, that DHCP pool, DNS, no port forwards, new-device quarantine off.
- **Xfinity gateway:** no lab reservations (the lab is behind the Firewalla). A replacement only
  needs to hand the Firewalla's WAN a DHCP lease.
- **The DR laptop on the lab:** cable it to the lab switch. Use a static `192.0.2.72/24` without a
  gateway (the old workstation's address) or a pool lease, and use its address as `<laptop lab IP>` for
  the answer server (D8). If it also runs Tailscale: `tailscale set --accept-routes=false`
  ([manual steps §2.3](manual-steps.md#23-admin-workstation-windows-wired-lab-nic)).
- The NAS keeps `192.0.2.156` (static in UGOS, gateway/DNS `192.0.2.1`).
- BIOS of each machine: [manual steps §1](manual-steps.md#1-bios--firmware-on-every-machine)
  (boot order USB first, power on after AC loss, virtualization on).
- NAS: the NFS rule for the lab subnet `192.0.2.0/24` (check with `showmount -e 192.0.2.156`;
  UGOS quirk: `sudo exportfs -ra`, [manual steps §3](manual-steps.md#3-ugreen-nas-ugos)), *Network
  UPS slave* = `192.0.2.100`, and the
  folders: `sudo mkdir -p /volume1/Artzima/{k8s,backups/vaultwarden,backups/paperless,paperless/data,paperless/media,paperless/consume}`.

### D7. Derived credentials

```bash
make bootstrap
```
**Expect,** each after a plan and your `yes`: `nothing to generate` (the generated secrets survived
in Infisical) → `tofu/bootstrap`: a new `artzima-eso` identity and new Tailscale keys → `tofu/backblaze`:
the plan of D4 → `tofu/cloudflare`: a new tunnel, DNS records, tunnel token. Ends with
`bootstrap done`.
**If this fails:** `identity name already exists` → D5; Cloudflare `record already exists` → D3;
Tailscale `requested tags are invalid` → the OAuth client lost a tag ([OPERATOR-SETUP A2](OPERATOR-SETUP.md#a2-accounts-and-the-other-four-root-credentials)).

### D8. USB installs

Follow [install/README.md](../install/README.md) steps 2-8. Summary:
```bash
make answers POSTINST_WEBHOOK_URL=http://<laptop lab IP>:8000/installed
install/prepare-iso.sh http://<laptop lab IP>:8000/answer proxmox-ve_*.iso proxmox-backup-server_*.iso
make answer-server POSTINST_WEBHOOK_URL=http://<laptop lab IP>:8000/installed
```
`prepare-iso.sh` needs Debian 13 with the Proxmox repo: on Linux a `debian:trixie` container
(podman/docker, install/README step 2); on macOS the same with Docker Desktop, OrbStack or podman
(`--platform linux/amd64`). **No container runtime?** Install host01 by hand from the stock ISO
(same hostname, IP and disk as its answer file), run the assistant there, then re-install host01
automated.
**Expect:** each machine fetches its answer (the server logs `served <host>`), installs, reboots;
`install/answers/installed/<host>.json` appears.
**If this fails:** `403 unknown MAC` → D1 `mac`; `412 install disk serial … not present` → D1
`install_disk_serial`; nothing reaches the server → the laptop's firewall (macOS: allow incoming
for python) or a different subnet.

### D9. Preflight

```bash
make preflight
```
**Expect:** every line `PASS`. **If this fails:** the `FAIL` line says which host and what
(no SSH → not installed or wrong IP; MAC/serial → D1; NAS export → D6).

### D10. Build

```bash
make up ANSIBLE_ARGS="-e pbs_offsite_reuse_datastore=true"
```
It asks `yes` per step, after showing the plan or task list, and once for the NAS sudo password.
`pbs_offsite_reuse_datastore` attaches the **existing** B2 datastore (same name). **Expect,** in
1.5-3 h: `site` (reboot host01 when it says so, then `make up` again), `tofu-apply` (Talos up,
`tofu/out/kubeconfig`), … `flux-bootstrap` (`✔ all components are healthy`), then
`up done. next: make verify`.
**If this fails:** fix and run `make up` again (everything is idempotent), or only the failed step:
`make <step>`. Talos not coming up → `talosctl --talosconfig tofu/out/talosconfig -n <ip> dmesg`.
Flux stuck at `secrets` → `kubectl get externalsecret -A` shows the missing key.

---

## E. App data

```bash
export KUBECONFIG=$PWD/tofu/out/kubeconfig
```

### E1. Postgres (automatic, from D2)

```bash
kubectl get clusters.postgresql.cnpg.io -A -w
```
**Expect** within 10-30 min: `STATUS  Cluster in healthy state` for every cluster. Then
```bash
kubectl -n vaultwarden exec vaultwarden-db-1 -c postgres -- psql -tAc 'select count(*) from users' vaultwarden
kubectl -n paperless exec paperless-db-1 -c postgres -- psql -tAc 'select count(*) from documents_document' paperless
```
shows your numbers.
**If this fails:** `kubectl -n <ns> describe cluster <name>` and
`kubectl -n <ns> logs -l cnpg.io/cluster=<name> --all-containers --tail=100`: `no backup found` →
wrong `serverName` in `externalClusters` or `targetTime` before the first backup; `access denied`
→ Secret `b2-pg` missing (`kubectl -n <ns> get externalsecret`).

**Afterwards** (same day): put `bootstrap.initdb` back in git (a leftover `recovery` would restore
again if the cluster were re-created) but **keep** `serverName: <cluster>-dr1` in `plugins`;
`make render`, commit, push. The old `<cluster>` folder in `artzima-pg` is no longer maintained:
delete it in the B2 web UI after 60 days (Object Lock keeps it 30 days anyway).

### E2. App volumes (VolSync)

1. Stop the apps that write to them:
   ```bash
   for ns in vaultwarden paperless alexandria media; do
     flux -n $ns suspend helmrelease --all
     kubectl -n $ns scale deploy --all --replicas 0      # CNPG pods are not Deployments: databases keep running
   done
   flux -n monitoring suspend helmrelease kube-prometheus-stack
   kubectl -n monitoring scale deploy kube-prometheus-stack-grafana --replicas 0
   ```
2. Restore every volume as of `DR_TIME`:
   ```bash
   make -s dr-volsync-restore AS_OF="$DR_TIME" > /tmp/dr-restore.yaml
   kubectl apply -f /tmp/dr-restore.yaml
   kubectl get replicationdestination -A -w
   ```
   **Expect:** every row's result `Successful` (minutes to an hour). Read-only volumes, if any are
   listed in `backup_volumes`, are skipped on purpose and printed as `SKIPPED`.
   **If this fails:** `kubectl -n <ns> describe replicationdestination <name>-dr` and the mover pod's
   log: `wrong password` → `/k8s/backup/RESTIC_PASSWORD` differs from the kit's (fix Infisical,
   wait for ESO or force: `kubectl -n <ns> annotate externalsecret <pvc>-restic force-sync=$(date +%s) --overwrite`).
3. Clean up and start the apps:
   ```bash
   kubectl delete -f /tmp/dr-restore.yaml && rm /tmp/dr-restore.yaml
   for ns in vaultwarden paperless alexandria media; do
     flux -n $ns resume helmrelease --all; kubectl -n $ns scale deploy --all --replicas 1
   done
   flux -n monitoring resume helmrelease kube-prometheus-stack
   ```

### E3. PBS (only if needed)

Talos and the LXCs are rebuilt by code. The old vzdump backups are in datastore `b2`
(attached in D10); restore a guest from it only if you need its old state
([backups: PBS restore](backups.md#restore-runbook-pbs-from-b2-the-pbs-box-is-lost)).

### E4. First logins

As in [OPERATOR-SETUP C](OPERATOR-SETUP.md#c-after): Vaultwarden (your account and its data are
back: log in with the master password from the kit), Paperless, Jellyfin (new setup, libraries,
API key → `/k8s/media/JELLYFIN_API_KEY`), Backrest (`http://192.0.2.156:9898`, still paused),
the media stack's VPN check.

---

## F. Verify, then re-arm

### F1. Automatic report

```bash
make verify
```
**Expect:** everything `PASS` except possibly the "< 26 h" backup lines (paused) and the self-checks
(not run yet).

### F2. Checklist

- [ ] **NAS:** personal shares and `data/media` complete (C4 summaries without errors; sizes match).
- [ ] **Hosts:** `ssh root@192.0.2.100 pvecm status` (host01) shows 5 votes; NUT works (`make nut-check`).
- [ ] **Cluster:** `make verify` Flux/HelmReleases/ExternalSecrets lines PASS; `kubectl get pods -A | grep -v Running | grep -v Completed` is empty.
- [ ] **Postgres:** both clusters healthy and archiving to `-dr1`
      (`kubectl -n vaultwarden get cluster vaultwarden-db -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}'` → `True`).
- [ ] **Apps:** a Vaultwarden item with an attachment opens and existing clients sync; Paperless
      search works; Alexandria answers; Grafana dashboards present; the arrs keep their settings
      and history; the wiring Job is green; Jellyfin libraries scanned.
- [ ] **Public routes:** `https://<app>.<domain>` loads for every `/apps.yaml` entry; `https://vault.<domain>/admin` → 403.
- [ ] **Tailnet:** `http://paperless`, `http://vault-admin/admin`, the Backrest UI reachable; subnet routes approved.

### F3. Self-checks once, by hand

```bash
kubectl -n backup create job --from=cronjob/restic-restore-test dr-rt && kubectl -n backup logs -f job/dr-rt
kubectl -n vaultwarden create job --from=cronjob/vaultwarden-db-restore-test dr-pt && kubectl -n vaultwarden logs -f job/dr-pt
```
**Expect:** `RESULT: PASS …` (the Postgres test needs 10-30 min).

### F4. Unpause the backups (only after F2 and F3)

In `ansible/group_vars/all/backups.yml`: `backup_volsync_paused: false`, `nas_backup_paused: false`.
```bash
make render && git commit -am "DR: backups on again" && git push && make nas-backup
```
The next morning: `make verify` → all PASS (backups < 26 h).

### F5. A new emergency kit

- New recovery codes of every account you used in A → the kit.
- Rotate what passed through a borrowed laptop: the admin identity's client secret (B5 again),
  your GitHub SSH key if the laptop isn't yours.
- `make emergency-kit` and update `kept_at`/`last_rotated` in `emergency-kit.yaml`; commit.
- `make dr-pdf`, print it, put it in the kit, destroy the old printout.

### F6. Clean up

- B2: old `artzima-*` application keys deleted (D4).
- Tailscale: old devices removed (A6).
- `pg` bucket: old `<cluster>` folders after 60 days (E1).
- A borrowed laptop: `rm -rf ~/.config/artzima ~/artzima-iac ~/.local/share/artzima-tools ~/.ssh/id_ed25519*`,
  `infisical logout`.

---

## Partial loss

### P1. One machine

The rest keeps running: the cluster has 3 Talos control planes (one may fail), Proxmox has 5 votes
with the QDevice (one may fail). Repair the machine or replace it, then:

1. **Remove the dead node from the clusters** (from a surviving node; only when the old one is gone for good):
   ```bash
   ssh root@<surviving PVE host> pvecm delnode <dead node>          # Proxmox
   export TALOSCONFIG=$PWD/tofu/out/talosconfig KUBECONFIG=$PWD/tofu/out/kubeconfig
   talosctl -n <healthy Talos IP> etcd members                       # if it hosted a Talos VM: note the dead one's ID
   talosctl -n <healthy Talos IP> etcd remove-member <member ID>
   kubectl delete node <dead talos-N>
   ```
2. New hardware: D1 for that one machine (`mac`, `install_disk_serial`), `make render`, commit.
3. USB install of that machine only (D8; the answer server answers only it).
4. `make preflight` (all PASS), then:
   ```bash
   make site LIMIT=<node>        # base config, joins the Proxmox cluster, storage, NUT, Tailscale
   make tofu-apply               # re-creates only the guests that lived there (plan shows exactly those)
   ```
   For **host01** also `make lxc-devices ts-routers jellyfin`; for
   **pbs**: [backups: PBS restore](backups.md#restore-runbook-pbs-from-b2-the-pbs-box-is-lost).
5. `make verify`.

### P2. The NAS only

The lab runs, but every Kubernetes volume **and both Postgres databases** lived on the NAS.

1. **Immediately:** D2 step 1 (pause switches), `make render`, commit, push
   (`flux reconcile kustomization apps --with-source`), so nothing backs up empty folders.
2. New NAS with the **same IP and share names**: C1, then **C** (your files).
3. NFS rule and folders: D6 (NAS part). Proxmox storage: `make storage`.
4. Postgres: D2 step 2 (recovery blocks), commit, push; then delete the broken clusters so they are
   re-created from B2: `kubectl -n vaultwarden delete cluster vaultwarden-db`,
   `kubectl -n paperless delete cluster paperless-db`. Continue with **E1**.
5. Volumes: delete the apps' broken PVCs/PVs (their folders are gone), let Flux re-create them,
   then **E2**: `kubectl get pv | grep Released` lists the leftovers to delete.
6. `make nas-backup` (Backrest, still paused), then **F**.

### P3. Infisical only

The cluster keeps working (ESO keeps the synced Secrets); only changes and re-runs are blocked.

1. **Two** new Infisical projects (same or new account): the main one and the cluster's one
   (only `/k8s/**`; [OPERATOR-SETUP A2b](OPERATOR-SETUP.md#a2b-the-clusters-infisical-project): delete
   protection on). Note host, both slugs and IDs; create the admin machine identity and make it
   **Admin on both** ([secrets](secrets.md#the-two-machine-identities)); write `~/.config/artzima` (B5).
   Change the private `infisical.yml` to the new slugs/host (`infisical_project_slug`,
   `infisical_k8s_project_slug`), `make private-prepare`, `make private-backup`.
2. `make secrets-folders` (both projects).
3. Copy everything the cluster still has:
   ```bash
   make dr-secrets-from-cluster                    # names: recoverable / missing / NOT in the cluster
   make dr-secrets-from-cluster DR_ARGS=--apply    # writes the missing ones (values never shown)
   ```
   Everything the cluster holds is a `/k8s` key, so it lands in the cluster's project; the cluster's
   `PUBLIC_DOMAIN` copy also restores `/settings/PUBLIC_DOMAIN` in the main project.
4. By hand, from the kit: `/ansible/NAS_RESTIC_PASSWORD`, `/tofu/TOFU_STATE_PASSPHRASE` (the
   surviving state files need the old one), and new root credentials (OPERATOR-SETUP A2; rotate the old ones).
5. `make gen-secrets` for the rest (`/ansible` NUT/PBS passwords, `/operator` logins and hashes;
   they are re-applied by `make site`, `make nas-backup`).
6. The old ESO identity and its state entries point at the dead projects:
   `tofu -chdir=tofu/bootstrap state rm infisical_identity.eso infisical_identity_universal_auth.eso infisical_identity_universal_auth_client_secret.eso infisical_project_identity.eso`
   (and `'infisical_project.k8s[0]'` if OpenTofu had created the cluster's project)
   (through `scripts/infisical_exec.py --set TF_VAR_state_passphrase=/tofu/TOFU_STATE_PASSPHRASE --`),
   then `make bootstrap` and `make eso-bootstrap`; commit and push the infisical.yml change.
7. `make site`, `make nas-backup`, `make verify`.

### P4. The workstation only

The lab keeps running and backing up. What burned with the workstation: the OpenTofu **state**
(`tofu/`, `tofu/cloudflare`, `tofu/backblaze`, `tofu/bootstrap`), and with it the Talos machine
secrets, `tofu/out/kubeconfig` and `talosconfig`. Without those there is **no admin access** to
Talos or Kubernetes; the apps don't notice.

1. B1-B5 on the new machine; the new SSH key into `/settings/ADMIN_SSH_KEYS`, then `make common`
   (puts it on the hosts; Ansible doesn't need any state).
2. Re-adopt the account-side objects with the lab running: D3-D5 (new tunnel, imported buckets,
   new ESO identity), `make bootstrap`, then `make eso-bootstrap` **cannot** run (no kubeconfig):
   the cluster keeps its old ESO credentials until step 3.
3. Kubernetes access comes back only with a **rebuild of the Talos VMs**, i.e. D2 (pause switches,
   Postgres recovery from B2), D10 (`make up`; `tofu-apply` first needs the old Talos, ts-router,
   and Jellyfin guests removed in the Proxmox UI, because the new state doesn't know them),
   then E and F. Check in the B2 web UI first that last night's uploads exist (`artzima-restic`,
   `artzima-pg` modification times), so nothing newer is lost.

Prevention (open item, README TODOs): keep the encrypted state files off the workstation too.

---

## Emergency kit

What must exist **outside the lab and outside Infisical**: printed, or on offline media kept
**away from home** (a fire safe at a relative's, a safe-deposit box). The list is
`emergency-kit.yaml` (names only, never values); `make dr-pdf` prints it after this runbook.

| Item | Why |
|---|---|
| Mailbox recovery codes + password | every other account resets through it (A1) |
| **Infisical** login + 2FA **recovery codes** | Infisical holds every other secret, in **two projects** (main + the cluster's) (A2, P3) |
| **Every restic repository password**: the NAS repo (`NAS_RESTIC_PASSWORD`) and the cluster repos (`RESTIC_PASSWORD`) | without them the restic backups are unreadable by anyone, including you (C, E2, P3) |
| A **Backblaze key that can read all buckets** | restore your files (C) before Infisical or the lab is back; create it once by hand, not with OpenTofu: `b2 key create artzima-emergency-read listBuckets,listAllBucketNames,readBuckets,listFiles,readFiles` |
| Backblaze, **Cloudflare**, **Tailscale** (its identity provider) and **GitHub** logins + 2FA **recovery codes** | A3-A6 |
| The **Vaultwarden master password** + its 2FA recovery code (sealed) | the restored vault (E4) |
| The **OpenTofu state passphrase** (`TOFU_STATE_PASSPHRASE`) | P3 only |
| The **router admin logins**: the Firewalla app account and the Xfinity gateway | lab LAN, DHCP pool; a replacement Firewalla (D6) |
| **This runbook, printed** (`make dr-pdf`) + the repo URL | everything |
| Optional: the PBS encryption key (if enabled); the private key of the Infisical export (if used) | E3; P3 |

**`make emergency-kit`** prints this as a checklist with where each item is kept and when it was
last rotated. It never prints a value.
- `EK_ARGS=--infisical` also shows when Infisical's copy last changed (names only). Newer than the
  kit's `last_rotated` = the kit copy is stale.
- After rotating any listed item: update the printed copy, then `last_rotated` in `emergency-kit.yaml`.
- **`make dr-pdf`** writes `dist/artzima-disaster-recovery.pdf`: this runbook plus the checklist,
  every page footed with the print date and the git commit (and `UNCOMMITTED CHANGES` if the
  runbook or kit differ from it). Print it after every change to either file; a printout whose
  commit is older than `git log -1 --format=%h -- docs/disaster-recovery.md emergency-kit.yaml`
  is stale.
- `make lint` checks that the kit's Infisical references exist.

### Optional: scheduled encrypted Infisical export (not set up; an option only)

Infisical Cloud is off-site already. This only guards against losing access to **Infisical
itself** at the worst moment (P3 without a running cluster). If you want it:
- **Where it runs:** a scheduled job on a machine **outside the home**, e.g. a small VPS.
- **What it does:** `infisical export --env=prod --projectId=<id> --path=<folder> --format=dotenv`
  for each folder of `secrets.manifest.yaml`, encrypted with [age](https://age-encryption.org) to a
  recipient whose private key exists **only in the emergency kit**.
- **Where it goes:** **not** the main B2 account (a second account, another provider, or a USB stick
  rotated off-site); never this repo, never the lab.
- **Rotation:** monthly; each generation holds every secret: treat it like the kit.

## Drills

The quarterly restore test is in [backups](backups.md#quarterly-restore-test-checklist), and the
monthly self-checks run by themselves. **Twice a year**, a **full drill from the kit alone**, on a
borrowed or spare laptop, without touching the lab:
- B1-B3 on that laptop (does `scripts/dr-tools.sh` still work?);
- regain Infisical access (B5) without using up recovery codes (a new browser profile);
- C3 against `artzima-nas` and restore one personal folder to a temp directory (C4 b);
- the restic CLI against one cluster repository, with the kit's read-all key (C2) and the cluster
  restic password: `restic -r 's3:https://s3.us-east-005.backblazeb2.com/artzima-restic/<namespace>/<pvc>' snapshots`,
  then restore one snapshot to a temp directory; list `artzima-pg` and `artzima-pbs` in the B2 web UI;
- walk D2-D10 against a throw-away single Talos VM far enough to reach "Flux Ready" (optional);
- note what was missing or wrong, fix this runbook, `make dr-pdf`, replace the printout.
