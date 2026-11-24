# Operator setup: THE checklist

**Private configuration:** edit actual inventory and settings under `.private/site/`, never the public `ansible/` examples. Live make commands automatically use `.private/runtime/`. After each private change, run `make private-backup`. Read [PRIVATE-CONFIG.md](PRIVATE-CONFIG.md) first. All addresses below are examples; use the private generated runbook and address table for actual commands.

**Start here.** This is everything *you* do, in order, from "nothing set up" to "lab running and
watching itself". Everything else is automated. Each item says **where** (a website, a file, the
NAS) and the **exact command or link**. Tick the boxes as you go.

How the automation works, in one paragraph: you provide **five root credentials** and a few
settings in Infisical. `make bootstrap` derives every other credential from them (Tailscale auth
keys, the cluster's Infisical identity, Backblaze keys, the Cloudflare tunnel token) and generates
every random password, **straight into Infisical** (never printed). Then `make preflight` checks the
hardware against the repo, `make up` builds everything, and `make verify` tells you what is
healthy. Every step that changes something first shows what it will do (a plan, a task list) and
waits for you to type **`yes`** (`AUTO_APPROVE=1` skips the question, e.g. for G.A.R.T.H.).

| Phase | Command | Touches the lab? |
|---|---|---|
| Accounts + keys ready? | `make preflight PREFLIGHT_ARGS=--accounts-only` | no (Infisical only, names only) |
| Derive credentials, generate secrets | `make bootstrap` | no (Infisical, Tailscale, Backblaze, Cloudflare) |
| Hardware matches the repo? | `make preflight` | read-only SSH |
| Build | `make up` | yes, step by step, each confirmed |
| Health report | `make verify` | read-only |

Background, when you want it: [secrets](secrets.md) (every key and who writes it),
[manual steps](manual-steps.md) (the details behind several items below), [backups](backups.md),
[disaster recovery](disaster-recovery.md).

---

## Deployment readiness

See the [public-source security review](PUBLIC-SECURITY-REVIEW.md) for checks, fixes and remaining deployment security requirements. Before deployment, verify trusted Proxmox API TLS, runtime secret access restrictions, first-run app administration before public ingress, installer LAN isolation, tool artifact integrity and built-image vulnerabilities.

The accounts-only check verifies credential presence. Hardware readiness requires the full preflight and installer target checks.

- Install-disk serials for host01, node01, node02, node03 and pbs were collected read-only on 2026-11-23 and are in private configuration; all MACs match. PBS runs from the ZimaBoard's eMMC (B1); the external USB disk, recorded by serial, is the datastore disk only. Confirm the targets once more in the installer's debug shell if hardware changes.
- The NAS was inspected read-only on 2026-11-23: the media roots and both personal shares are recorded in private configuration ([NAS plan](nas-plan.md)), and Docker and the admin user are present. Still to verify: the NFS rule (B0) and the data folder's owner.
- Infisical is split into two projects (2026-11-23): the free plan refuses custom roles, so the cluster's secrets get their own project and ESO the built-in Viewer role there only. Before the cloud bootstrap continues: create that project ([A2b](#a2b-the-clusters-infisical-project)), run the one-time migration, and verify that ESO can read `/k8s` and cannot read the main project.
- Review the bootstrap plans and generate the remaining credentials/passwords; schedule DNS cutover to avoid breaking the running lab.
- Emergency-kit locations and rotation dates remain unrecorded (`TODO`/empty). Finish the independent recovery credentials and printed kit before destructive work.
- The reviewed initial commit is published to `https://github.com/DevArtech/artzima-iac` on `main`. Public-tree and history scans passed. GitHub CI/security passed for the initial publication. All four image builds completed, but their packages were public. The four public packages were removed and anonymous pulls fail. Public registry workflows are removed. A private builder is prepared locally; its private GitHub repository must be created before private image builds and deployment.
- Confirm BIOS/network/timezone facts and prepare installer media. The later full hardware preflight is after OS installation and before `make up`; verify installer disk targets separately before installation.

## A. Prerequisites

Prepare the workstation, accounts, private inventory and NAS before deployment.

### A1. Workstation

- [ ] **Clone the repo and install the tools** (no sudo; pinned versions):
  `make deps`, then `make hooks` (gitleaks pre-commit: the repo is public).
- [ ] **Infisical bootstrap identity** (ROOT CREDENTIAL #1, already exists): its credentials are in
  `~/.config/artzima/infisical.env` and the project in `~/.config/artzima/infisical-project.env`
  ([secrets](secrets.md#local-configuration)). It must be allowed to create the cluster's own
  identity: <https://app.infisical.com> → *Organization → Access Control → Identities →
  artzima-admin*: organization role **Admin** (or a custom role with "Identities: create, edit");
  *Project example-project → Access Control → Machine Identities → artzima-admin*: role **Admin**,
  and the same on the cluster's project ([A2b](#a2b-the-clusters-infisical-project)).

### A2. Accounts and the other four root credentials

Each value goes into Infisical (<https://app.infisical.com> → the project that holds the folder:
`/k8s/...` = the cluster's project, everything else = the main project (names in the private
settings) → environment **prod** → the folder → *Add secret*). The folders appear with `make secrets-folders`.

- [ ] `make secrets-folders` (creates the empty folder layout; no values).
- [ ] **Cloudflare** (ROOT CREDENTIAL #2). The domain must be a zone in your Cloudflare account.
  - <https://dash.cloudflare.com/profile/api-tokens> → *Create Token → Custom token*:
    **Account / Cloudflare Tunnel / Edit**, **Account / Access: Apps and Policies / Edit**,
    **Zone / DNS / Edit** (zone: your domain). → `/tofu/CLOUDFLARE_API_TOKEN`
  - Dashboard → your domain → *Overview* (right column): **Account ID** →
    `/settings/CLOUDFLARE_ACCOUNT_ID`, **Zone ID** → `/settings/CLOUDFLARE_ZONE_ID`
  - The domain itself (e.g. `example.org`) → `/settings/PUBLIC_DOMAIN`
  - Optional: Cloudflare Access in front of some apps → `/settings/CLOUDFLARE_ACCESS_APPS`
    ([manual steps §6](manual-steps.md#6-cloudflare-tunnel-public-apps)); absent = none.
- [ ] **Tailscale** (ROOT CREDENTIAL #3).
  - <https://login.tailscale.com/admin/acls/file>: add to the policy file
    ```json
    "tagOwners": {
      "tag:k8s-operator":  [],
      "tag:k8s":           ["tag:k8s-operator"],
      "tag:artzima":       ["autogroup:admin", "tag:k8s-operator"],
      "tag:subnet-router": ["autogroup:admin", "tag:k8s-operator"]
    },
    "autoApprovers": { "routes": { "192.0.2.0/24": ["tag:subnet-router"] } }
    ```
    plus a grant that lets your own devices reach `tag:k8s` ([manual steps §4](manual-steps.md#4-tailscale-admin-console)).
  - <https://login.tailscale.com/admin/settings/oauth> → *Generate OAuth client*: scopes
    **Devices Core: write**, **Auth Keys: write**, **Services: write**; tags `tag:k8s-operator`,
    `tag:artzima`, `tag:subnet-router`. → `/k8s/tailscale/OPERATOR_CLIENT_ID` and
    `/k8s/tailscale/OPERATOR_CLIENT_SECRET`. (The same client runs the Kubernetes operator and
    mints the hosts' auth keys in `make bootstrap`.)
- [ ] **Backblaze B2** (ROOT CREDENTIAL #4). Account at <https://www.backblaze.com/sign-up/cloud-storage>.
  - Your region (B2 web UI, e.g. `us-east-005`) → `b2_region` and `b2_s3_endpoint` in
    [`ansible/group_vars/all/backups.yml`](../ansible/group_vars/all/backups.yml); check the bucket
    names there are free (they are global).
  - One dedicated administration key for creating buckets and keys (with the `b2` CLI, or the web UI
    *Application Keys → Add*, all buckets):
    ```bash
    b2 key create artzima-tofu listBuckets,listAllBucketNames,readBuckets,writeBuckets,readBucketEncryption,writeBucketEncryption,readBucketRetentions,writeBucketRetentions,listKeys,writeKeys,deleteKeys
    ```
    → `/tofu/B2_ADMIN_KEY_ID` and `/tofu/B2_ADMIN_APPLICATION_KEY`.
- [ ] **GitHub** (ROOT CREDENTIAL #5). <https://github.com/settings/tokens> → *Generate new token
  (classic)*, note `artzima`, scopes **`read:packages`** and **`public_repo`**, expiry with a calendar
  reminder. → `/k8s/ghcr/GHCR_PULL_TOKEN`. It pulls the private app images and bootstraps Flux
  (`make flux-bootstrap` hands it to `flux` in memory). If `flux bootstrap` is refused (403 on deploy
  keys), give the token the `repo` scope for that one run, then remove it again.
- [ ] **Surfshark** (subscription data, not a root credential): *my.surfshark.com → VPN → Manual
  setup → WireGuard* → `/k8s/vpn/WIREGUARD_PRIVATE_KEY` and `/k8s/vpn/WIREGUARD_ADDRESSES`
  (exact clicks: [manual steps §10.1](manual-steps.md#101-before-the-flux-sync-in-this-order) step 2).
- [ ] **Private settings:** `/settings/ADMIN_EMAIL` (mailbox for Proxmox mail),
  `/settings/ADMIN_SSH_KEYS` (your SSH **public** key(s), one per line). Optional:
  `/bare-metal/ROOT_PASSWORD_HASHED` (`mkpasswd -m yescrypt`; absent = root is SSH-key only).
- [ ] **Check:** `make preflight PREFLIGHT_ARGS=--accounts-only` → `PASS`. While something is
  missing, `make secrets-check` lists it (names only) under "MISSING: you provide these".

### A2b. The cluster's Infisical project

Two projects, because Infisical's free plan refuses custom roles ([secrets](secrets.md#project-layout-two-projects)):
the main project keeps `/settings`, `/bare-metal`, `/ansible`, `/tofu`, `/operator`; the cluster's
project holds **only** `/k8s/**`, and the cluster's identity `artzima-eso` gets the built-in
**Viewer** role on that project alone.

- [ ] **Create the project, once (you, in the UI).** `artzima-admin`'s organization role may not
  create projects, so this is a manual step. <https://app.infisical.com> → *Organization → Projects
  → Add project*: type **Secrets Management**, name `Artzima K8s`, slug **as in the private settings**
  (`infisical_k8s_project_slug`, e.g. `artzima-k8s`). Then in that project:
  *Settings → Delete protection* **on**; *Access Control → Machine Identities → Add* `artzima-admin`,
  role **Admin**. Keep the default `prod` environment. Do **not** add `artzima-eso` (OpenTofu does,
  as Viewer) and never put anything but `/k8s/**` into this project.
  (Alternative only if `artzima-admin` may create projects: `TF_VAR_k8s_project_create=true make ids-plan`
  shows OpenTofu creating it, delete-protected; a permission error means: do it by hand as above.)
- [ ] **Record its ID** (not secret): project → *Settings* → **Copy Project ID**, then add to
  `~/.config/artzima/infisical-project.env` (mode 600):
  ```
  INFISICAL_K8S_PROJECT_ID=<the id>
  INFISICAL_K8S_PROJECT_SLUG=<the slug>
  ```
  and `make private-backup` (the slug is in the private settings).
- [ ] `make secrets-folders`: creates the folders in **both** projects (the `/k8s` tree in the new one).
- [ ] **Move the cluster's secrets, once:** `make secrets-migrate-k8s` (dry run, names only: what
  is copied, what OpenTofu moves, conflicts), then `make secrets-migrate-k8s MIGRATE_ARGS=--apply`
  (copies, then verifies hash equality without showing values). `make secrets-check` must show no
  missing keys and no mirror difference.
- [ ] **Later, after `make bootstrap` and a verified `make secrets-check`:**
  `make secrets-migrate-k8s MIGRATE_ARGS=--delete-old` removes the old `/k8s` copies from the main
  project (it re-verifies first and refuses otherwise). Delete the then-empty `/k8s` folders of
  the main project in the UI.

### A3. Fill Infisical with everything else: `make bootstrap`

- [ ] **Existing DNS records use the same hostnames?** Review conflicts with the managed
  tunnel before applying the plan. Schedule any DNS changes for the deployment window.
- [ ] `make bootstrap`. In order, each with its plan and a `yes`:
  generated secrets (`make gen-secrets`: random passwords, API keys, hashes; only **missing** keys,
  existing ones are never touched) → Tailscale auth keys + the cluster's read-only Infisical
  identity, Viewer on the cluster's project only (`tofu/bootstrap`) → Backblaze buckets + per-bucket
  keys; the cluster's keys go to the cluster's project (`tofu/backblaze`) → Cloudflare tunnel, DNS,
  Access; the tunnel token goes to the cluster's project (`tofu/cloudflare`). Re-running it is safe.
- [ ] **Check the ESO boundary once** (with ESO's credentials from `/tofu/ESO_CLIENT_*`): a read of
  a `/k8s` key in the cluster's project works, a read of `/tofu/B2_ADMIN_APPLICATION_KEY` in the main
  project is **denied** ([secrets](secrets.md#runtime-identity-boundary)).
- [ ] **Copy the logins for people** into your password manager: Infisical folder `/operator`
  (main project: `VAULTWARDEN_ADMIN_PASSWORD`, `BACKREST_PASSWORD`), plus, in the cluster's project,
  `/k8s/paperless/ADMIN_PASSWORD`, `/k8s/alexandria/LOGIN_PASSWORD`,
  `/k8s/media/QBITTORRENT_PASSWORD`.
- [ ] **Grafana admin login (yours):** set `/k8s/monitoring/GRAFANA_ADMIN_USER` and
  `GRAFANA_ADMIN_PASSWORD` in the cluster's project (`by: operator`; `make preflight` waits for
  them). A later change reaches Grafana by itself through the `grafana-admin-sync` Job
  ([manual steps §15.1](manual-steps.md#151-admin-login)).
- [ ] `make secrets-check`: only "pending (automation writes these)" may be non-empty
  (`PBS_FINGERPRINT`, the Proxmox tokens: written during `make up`).

### A4. Emergency kit (outside the lab)

The disaster-recovery runbook assumes the lab, the NAS, this workstation, Headquarters and
G.A.R.T.H. are all gone: only the kit and a borrowed laptop remain.

- [ ] `make emergency-kit EK_ARGS=--infisical` and work through the list: the mailbox, Infisical,
  Backblaze, Cloudflare, Tailscale and GitHub recovery codes; the restic passwords
  (`/k8s/backup/RESTIC_PASSWORD`, `/ansible/NAS_RESTIC_PASSWORD`); the state passphrase
  (`/tofu/TOFU_STATE_PASSPHRASE`); the Vaultwarden master password (sealed); the router login.
  Printed or offline, kept **away from home** ([disaster recovery](disaster-recovery.md#emergency-kit)).
- [ ] The Backblaze **read-all key** for the kit (by hand, never through OpenTofu):
  `b2 key create artzima-emergency-read listBuckets,listAllBucketNames,readBuckets,listFiles,readFiles`
  (or the web UI: *Application Keys → Add*, all buckets, **Read Only**).
- [ ] Fill in `kept_at` / `last_rotated` in `emergency-kit.yaml`, commit, then **`make dr-pdf`** and
  print `dist/artzima-disaster-recovery.pdf` (runbook + checklist; every page shows the print date
  and commit). Reprint after every change to the runbook or the kit.
- [ ] Optional but recommended: try `scripts/dr-tools.sh` once on another laptop (the drill in
  [disaster recovery: Drills](disaster-recovery.md#drills) does the full version twice a year).

### A5. NAS configuration

All on artnas (UGOS web UI, or `ssh devartech@192.0.2.156`). Details: [manual steps §3](manual-steps.md#3-ugreen-nas-ugos).
Until build day the NAS is still on the home network at its old address; it moves to the lab
LAN in [B0](#b0-network-firewalla-nas-workstation), and the NFS rule and UPS host change with it.

Inspected read-only on 2026-11-23 (G.A.R.T.H.): `volume1` 7.3T (6.9T free), Docker 26.1, user
`devartech` in `admin`. The private [NAS plan](nas-plan.md) lists the results.

- [x] Record the media sources and personal backup shares in private configuration (2026-11-23).
  Permissions, mount boundaries and an independent backup are still verified before changing storage.
- [ ] NFS export `/volume1/Artzima` allowed for the lab subnet (no_root_squash where §3 says).
- [ ] The media data folder and its owner (`/volume1/Artzima/data`, §3).
- [x] Where are the existing rips? → `media_existing_paths` (private `media.yml`): two roots on the
  same volume as the data folder, so hardlinks work. The movies are loose files; `make media-adopt`
  gives each one its own title folder. The originals belong to an old LXC uid: the dry-run reports
  any that aren't world-readable (fixing that is a `chmod o+r` on the originals, your call).
  Run `make media-adopt` later, dry-run first.
- [x] Which personal shares to back up off-site? → `nas_personal_shares` (private `backups.yml`):
  both shares from the start, including the one that is still empty. Their `#recycle` folders are
  excluded (`nas_backup_excludes`).
- [x] UGOS **Docker** app installed (Docker 26.1); your NAS user is in `admin` and can `sudo`. Its password
  goes in Infisical (main project, prod) as `/ansible/NAS_SUDO_PASSWORD`; `make nas-backup` reads it
  from there. Without that entry: `make nas-backup NAS_ANSIBLE_ARGS=-K` and type it.
- [x] (2026-11-23) Folders: `sudo mkdir -p /volume1/Artzima/{backups/vaultwarden,backups/paperless,paperless/data,paperless/media,paperless/consume}`.
- [x] Previous backup identified: a container on the NAS (recorded in `nas_existing_backup`). **Keep it
  running** until Backrest's first full backup and a test restore are verified; retiring it is
  manual ([backup-provider transition](backups.md#backup-provider-transition), C below).

### A6. Private deployment inventory

- [x] (2026-11-23) [`ansible/hosts.yml`](../ansible/hosts.yml) is an example. Edit `.private/site/ansible/hosts.yml` instead: for every Proxmox/PBS machine `mac` (primary
  NIC), `install_disk` and **`install_disk_serial`**; for pbs also `pbs_datastore_disk_serial` (the USB
  datastore disk). All filled in; `make answers` refuses an install serial that overlaps the datastore disk, unless `pbs_os_on_datastore_disk: true` (a shared-disk mode pbs no longer uses; B1). The answer server only answers machines with
  a known MAC and installs only onto the disk with that serial. <a id="disk-serials"></a>**Disk
  serials:** on the target system `udevadm info -q property -n /dev/nvme0n1 | grep ^ID_SERIAL=`
  (or `lsblk -d -o NAME,SERIAL,MODEL`); in the installer's debug shell the same command. A
  unique part of `ID_SERIAL` is enough (the match is `*<serial>*`).
- [x] (2026-11-23) `timezone` (America/Los_Angeles) in `.private/site/ansible/group_vars/all/lab.yml`.
- [ ] `make render`, `make lint`, `make check-public` → all green. Commit and push to GitHub
  (**public**). Create the separate private image builder; its workflow verifies privacy before building
  application images ([manual steps §7](manual-steps.md#7-private-images-ghcr-pat-and-pull-secret) steps 1-2).

### A7. Hardware and network prerequisites

- [ ] Verify BIOS settings: [manual steps §1](manual-steps.md#1-bios--firmware-on-every-machine).
- [ ] The laptop host has `hw: laptop` (and `battery_charge_limit`) in `.private/site/ansible/hosts.yml`.
  `make site` (role `common`) then keeps it awake with the lid closed, masks sleep/suspend/hibernate
  and caps the battery charge. The same role also fixes NICs by driver: e1000e offloads off (I217
  hangs), r8169 ASPM/EEE off. On BIOS-boot hosts it points debconf `grub-pc/install_devices` at the
  install disk, found by `install_disk_serial` (a stale value once broke apt on pbs).
- [ ] Read [manual steps §2](manual-steps.md#2-firewalla-lab-router-and-the-xfinity-gateway) once: the
  lab moves behind the Firewalla on build day ([B0](#b0-network-firewalla-nas-workstation)). There are no
  reservations to make on the Xfinity any more.
- [ ] Confirm the intended storage paths and independent backup coverage before deployment.

---

## B. Build day

### B0. Network: Firewalla, NAS, workstation

Before the USB installs. Lab `192.0.2.0/24`, gateway and DNS `192.0.2.1` (the Firewalla);
every machine keeps its last octet ([addresses](addresses.md)). Details and the reasoning:
[manual steps §2](manual-steps.md#2-firewalla-lab-router-and-the-xfinity-gateway), [§3](manual-steps.md#3-ugreen-nas-ugos), [§4a](manual-steps.md#4a-reaching-the-lab-from-the-home-network).

- [ ] **NAS address** (UGOS, still reachable on the home network): *Control Panel → Network →
  (wired LAN) → IPv4 Manual*: `192.0.2.156` / `255.255.255.0`, gateway `192.0.2.1`, DNS
  `192.0.2.1`. The UI drops; continue with the Firewalla.
- **Firewalla** (the same steps as
  [disaster recovery P5](disaster-recovery.md#p5-firewalla-replace-or-factory-reset); `make firewalla-plan`
  checks the settings, read-only):
  - [x] (2026-11-23) **Router mode:** WAN = the port to the Xfinity (DHCP), LAN = the port to the lab switch.
  - [x] (2026-11-23) **LAN** `192.0.2.1/24`, DHCP on with pool `192.0.2.210`-`192.0.2.249` (static
    hosts `.100`-`.160` stay static, `.72` and `.204` stay out of the pool), DHCP DNS = `192.0.2.1`.
    No DHCP reservations: every lab machine and guest is static.
  - [x] (2026-11-23, plan PASS) New-device quarantine **off**, Active Protect **on**, **no port
    forwards**, UPnP off, no country or category rules (Cloudflare Tunnel and Tailscale need no forwards).
  - [ ] **WAN DNS upstreams `9.9.9.9` and `1.1.1.1`** (*Network Manager → WAN → DNS*). The plan on
    2026-11-23: FAIL, the box still uses the Xfinity's DNS from DHCP.
  - [ ] **Ad Block off** (system-wide, so also the lab). The plan on 2026-11-23: FAIL, it is on.
  - [x] (2026-11-23) **WireGuard VPN server** on port 51820, server `203.0.113.1`, client profile
    `workstation` edited to `Endpoint = <Firewalla WAN address>:51820`,
    `AllowedIPs = 192.0.2.0/24, 203.0.113.1/32`, no `DNS =` line. The workstation reaches the lab
    only through this tunnel.
  - [x] (2026-11-23) **SSH key:** the workstation's `~/.ssh/id_ed25519.pub` in `pi`'s
    `authorized_keys`; host key in `~/.ssh/known_hosts_lab`.
  - [ ] **Rules from git:** `make firewalla-plan`, review, then `make firewalla-apply`: block the
    lab from opening connections to the home network, except the answer server (tcp 8000 on the
    workstation's home address `198.51.100.72`). Only rules marked `artzima-iac:<id>` are touched.
- [ ] **Workstation:** WireGuard tunnel `workstation` active (`ping 192.0.2.1`). Allow inbound
  TCP 8000 for the answer server on its home network profile. If Tailscale runs here too,
  `tailscale set --accept-routes=false`, so the Tailscale lab route doesn't shadow the tunnel.
- [ ] **NAS on the lab:** open UGOS at `http://192.0.2.156` from the workstation. *NFS
  permissions* of **Artzima**: rule `192.0.2.0/24` (rw, no_root_squash, sync), old home-network
  rule deleted. Then `showmount -e 192.0.2.156`; if the export is missing, `sudo exportfs -ra`, or
  `sudo exportfs -o rw,sync,no_root_squash,no_subtree_check 192.0.2.0/24:/volume1/Artzima`
  ([UGOS quirk](manual-steps.md#3-ugreen-nas-ugos)). *UPS → Network UPS slave*, server
  `192.0.2.100` (host01).
- [ ] **Xfinity admin site:** delete the lab reservations from 2026-11-22
  (`198.51.100.100`-`.103`, `.127`, `.156`); leave the home devices alone.
- [ ] **Home devices that use the NAS:** Tailscale with *accept routes* ([§4a](manual-steps.md#4a-reaching-the-lab-from-the-home-network)).
  This works once ts-router-1/2 are up (end of `make up`).

### B1. Install and deploy

- [ ] **PBS install (the OS is on the eMMC).** PBS runs from the ZimaBoard's internal eMMC (LVM
  volume group `pbs`, BIOS boot, grub on the eMMC). The Proxmox auto-installer could not finish on
  the eMMC (2026-11-23), so PBS was installed onto the USB disk and its root and swap volumes were
  copied to the eMMC by hand; a rebuild repeats that by hand (the `install_*` keys of host `pbs` in
  `.private/site/ansible/hosts.yml` describe that temporary USB install). Afterwards the USB disk is
  the datastore disk only (`pbs_os_on_datastore_disk` stays `false`). `make answers` refuses an
  install serial that could match a datastore disk unless that flag is `true` and both serials are
  identical, and it refuses the flag without a small `hdsize`.
- [ ] **PBS datastore disk (in `make up`, role `pbs`, `tasks/datastore_disk.yml`):** the USB disk,
  found by `pbs_datastore_disk_serial`, carries a GPT with two ext4 partitions, both mounted by UUID in `/etc/fstab` with `nofail`:
  p1 `artzima-pbs`, the rest of the disk, at `/mnt/datastore/<name>` (the local datastore is the
  filesystem root), and p2 `artzima-pbscache` (GPT name `artzima-pbs-cache`; ext4 labels stop at
  16 characters), `pbs_datastore_cache_gib` = 100 GiB at the end of the disk, at
  `/mnt/datastore-cache`: the cache of the B2 datastore (`pbs_b2_cache_path`
  `/mnt/datastore-cache/b2`).
  Formatting it is a **one-time opt-in**: `pbs_datastore_format_disk: true` plus
  `pbs_datastore_format_confirm_serial` = that disk's serial (refused on any mismatch). A disk with
  both labels is only ever mounted and reused, even if the flag was left `true`. A disk still in the
  old single-partition layout (`artzima-pbs` only, no cache) is re-partitioned **only** with that
  opt-in: the role checks that no task uses the datastore, puts it offline, removes it from PBS, wipes
  the disk, partitions and formats it and re-creates the datastore **empty**. Without the opt-in it
  refuses and changes nothing. (Done on 2026-11-24: the old layout held only one night of backups.)
- [ ] **USB installs** (hands-off): `make answers` (validates), `install/prepare-iso.sh …`,
  `make answer-server`, then boot each machine from the stick
  ([install/README.md](../install/README.md)). `<ws-ip>` is the workstation's **home address**,
  `198.51.100.72` (the Firewalla rule `lab-to-answer-server` lets the lab reach its tcp 8000);
  re-prepare an ISO made with an older address. Unknown MAC, wrong ISO or wrong disk serial =
  the server refuses (HTTP 404/409/412) and nothing is installed.
- [ ] `make preflight` → every line `PASS`: Infisical keys present, every host on SSH, MAC and
  disk serial match `hosts.yml`, NAS export reachable. Fix any `FAIL` first.
- [ ] `make up`. It runs, each after showing its plan/task list and your `yes`:
  `site` (hosts: base, network, cluster + QDevice, PBS, storage, UPS, Tailscale, GPU, API tokens;
  writes `PBS_FINGERPRINT` and the Proxmox tokens into Infisical) → `tofu-apply` (VMs/LXCs, Talos)
  → `lxc-devices` → `ts-routers` → `jellyfin` → `pbs-offsite` → `nas-backup` (asks for the NAS
  sudo password) → `eso-bootstrap` → `flux-bootstrap`. If one step fails, fix it and re-run
  `make up` (everything is idempotent), or run the failed step alone (`make <step>`).
- [ ] **Right after the first successful `make up`: set `pbs_datastore_format_disk: false` again**
  (`.private/site/ansible/hosts.yml`), then `make private-backup`. From then on the datastore volume is
  only ever mounted and reused. Check: `ssh root@192.0.2.127 proxmox-backup-manager datastore list` shows
  the datastore at `/mnt/datastore/<name>`, `lsblk -o NAME,LABEL,SIZE,MOUNTPOINT` shows `artzima-pbs`
  and `artzima-pbscache` on the USB disk, mounted at `/mnt/datastore/<name>` and
  `/mnt/datastore-cache`, and `df -h /mnt/datastore-cache` shows about 100 GiB.
- [ ] **Host01 reboot** after the NVIDIA driver (the `site` output says so).
- [ ] **UPS:** the NAS's *Network UPS slave* points at host01, `192.0.2.100` (B0;
  [manual steps](manual-steps.md#3-ugreen-nas-ugos)), then `make nut-check`.
- [ ] Wait ~15 minutes, then `make verify`. Backups and restore tests show `FAIL` ("never") until
  their first run: check again the next morning.

---

## C. After

- [ ] **Jellyfin (starts fresh, automated):** nothing is carried over from the old server: no users,
  watch history or settings. Nothing to click: `make jellyfin` (part of `make up`) runs the first-run
  wizard, creates the libraries **Movies** `/data/media/movies`, **Shows** `/data/media/tv` and
  **Music** `/data/media/music`, turns on NVENC, sets the known proxies (Talos node IPs) and the
  published URL, and writes the API key `artzima-iac` to `/k8s/media/JELLYFIN_API_KEY` and copies
  of the admin login to `/k8s/media/JELLYFIN_ADMIN_*` (for Seerr)
  ([manual steps §10.3](manual-steps.md#103-first-logins)). **Your login** is in Infisical
  `/operator/JELLYFIN_ADMIN_USER` / `JELLYFIN_ADMIN_PASSWORD`: copy it to your password manager and
  log in at `https://jellyfin.<domain>`. If the wiring Job ran before the key existed, it skipped only
  the Sonarr/Radarr/Lidarr → Jellyfin connection; to apply it now:
  `kubectl -n media delete job media-wiring --ignore-not-found && flux reconcile kustomization apps`.
- [ ] **Seerr (automated):** nothing to click. The wiring Job does Seerr's setup wizard over its API:
  first sign-in with the Jellyfin admin login (it becomes Seerr's admin), Jellyfin server, the
  Movies and Shows libraries, application URL `https://seerr.<domain>`, then Sonarr and Radarr
  (Recyclarr's profiles, `/data/media/{tv,movies}`). Sign in at `https://seerr.<domain>` with the
  Jellyfin login. Left to you: *Settings → Users → Import Jellyfin users* (or allow new sign-ins).
- [ ] **Wizarr (automated):** nothing to click. The Job `wizarr-setup` creates Wizarr's admin with the
  Jellyfin admin login, adds the Jellyfin server (in-cluster URL, `/k8s/media/JELLYFIN_API_KEY`,
  invite links to `https://jellyfin.<domain>`) and its libraries
  ([manual steps §10.3](manual-steps.md#103-first-logins)). On an already set-up Wizarr it only adds
  what is missing and changes nothing else. Sign in at `https://wizarr.<domain>` with the Jellyfin
  login and create invites.
- [ ] **Media stack:** VPN check, Recyclarr, first logins
  ([manual steps §10.2-10.3](manual-steps.md#102-after-the-sync)). Existing rips: `make media-adopt`
  (dry-run), then `make media-adopt MEDIA_ADOPT_ARGS=--apply`.
- [ ] **Usenet via SABnzbd (automated):** nothing to click. Keys in Infisical `/k8s/media`: yours
  `NZBGEEK_API_KEY` and `USENET_HOST`/`_PORT`/`_USER`/`_PASSWORD` (UsenetServer), generated
  `SABNZBD_API_KEY`. The wiring Job sets SABnzbd up (folders `/data/usenet/...`, categories
  `tv`/`movies`/`music`, the news server with SSL and 20 connections, web login = the Jellyfin admin
  login), adds SABnzbd as the Usenet download client of Sonarr/Radarr/Lidarr and NZBGeek to Prowlarr.
  No VPN (TLS to the provider). Check: `kubectl -n media logs job/media-wiring | grep -i -e sabnzbd -e nzbgeek`
  (*server test passed*), and <http://sabnzbd:8080> over Tailscale. **Renewal:** the UsenetServer and
  NZBGeek subscriptions renew outside the lab; a lapsed one shows as a failed server test / failing
  searches, and a changed password or key goes into Infisical, then re-run the wiring
  ([manual steps §10.7](manual-steps.md#107-day-2)).
- [ ] **Vaultwarden:** <http://vault-admin/admin> over Tailscale with `/operator/VAULTWARDEN_ADMIN_PASSWORD`,
  invite yourself, create the account (+2FA) at `https://vault.<domain>`
  ([manual steps §12.2](manual-steps.md#122-first-run)).
- [ ] **Paperless:** <http://paperless> over Tailscale, user `admin` / `/k8s/paperless/ADMIN_PASSWORD`;
  create the `hq` user and its **API token for Headquarters** (stored in HQ, not here)
  ([manual steps §13.2-13.3](manual-steps.md#132-first-login)).
- [ ] **Grafana:** `https://grafana.<domain>` with your login (`/k8s/monitoring/GRAFANA_ADMIN_*`);
  the home dashboard is "artzima: overview", the rest in folders Hosts / Platform / Databases
  ([manual steps §15](manual-steps.md#15-grafana)).
- [ ] **Alexandria:** first logins ([manual steps §8](manual-steps.md#8-alexandria-secrets-library-first-login-embedder)).
- [ ] **Backrest on the NAS:** <http://192.0.2.156:9898>, user `admin` / `/operator/BACKREST_PASSWORD`.
  When the first **full** upload has finished and a test restore worked: **review any existing backup-provider transition** ([backups](backups.md#backup-provider-transition)).
- [ ] **Alerts:** when Headquarters has its endpoint, put the https URL into
  `/k8s/alerts/WEBHOOK_URL`, then `make nas-backup` (Backrest picks it up; Alertmanager within the
  hour). Until then alerts only show in Alertmanager/Grafana and `make verify`.
  Optional dead-man switch for the NAS: a healthchecks.io ping URL in `/ansible/NAS_BACKUP_HEALTHCHECKS_URL`.
- [ ] **Renovate:** install the Renovate GitHub App on the repo (<https://github.com/apps/renovate>);
  in *Settings → Branches* protect `main`: require the `ci / lint` check, allow auto-merge.
  Patch/digest updates then merge themselves after CI; majors wait for your approval on the
  Dependency Dashboard issue.
- [ ] **Retire the previous NAS backup** only after Backrest's first full backup **and** a test restore
  are verified: stop and remove its container, then delete its B2 data. All manual
  ([backup-provider transition](backups.md#backup-provider-transition)).
- [ ] **Old lab folders on the NAS (cleanup review, never automated):** the list with sizes is in the
  private [NAS plan](nas-plan.md). Delete by hand only what you no longer need, after the build is
  verified. `dump/` and `template/` are also used by the new Proxmox storage: remove old files there,
  never the folders. The rip sources (`media_existing_paths`) stay as long as you want the originals.
- [ ] **Next morning:** `make verify` → all `PASS`.

Self-checks run by themselves from now on: the 1st of each month a restic repository is restored
and verified, the 2nd each Postgres database is recovered from B2 into a scratch cluster and
queried. A `FAIL` alerts; the quarterly manual test is still worth doing ([backups](backups.md#quarterly-restore-test-checklist)).

---

## D. Later

- [ ] **VLANs:** not needed; the Firewalla already separates the lab. Only an option if a managed
  switch is added ([manual steps §5](manual-steps.md#5-later-vlans-only-with-a-managed-switch)).
- [ ] **Pi-hole:** on hold (not in this repo yet). The Firewalla can hand it out as the lab's DNS
  when it exists; the home side stays on the Xfinity's DNS ([manual steps §5](manual-steps.md#5-later-vlans-only-with-a-managed-switch)).
- [ ] **Major updates** from Renovate: read the changelog, approve on the Dependency Dashboard,
  merge after CI.
- [ ] **Rotations:** Tailscale auth keys live 90 days and are re-minted by `make ids-apply` (only
  needed to add hosts); the GitHub PAT before its expiry; everything else: [secrets, Day 2](secrets.md#day-2).

---

## Switches (all in git, defaults in brackets)

| Switch | Where | Default | What it does |
|---|---|---|---|
| `backup_nas_media` | `backups.yml` | `restic-on-nas` | `existing` = keep the old UGOS job, `off` = no NAS off-site backup |
| `backup_volsync_paused`, `nas_backup_paused` | `backups.yml` | `false` | disaster recovery: stop backups **and** retention of a rebuilt, empty system |
| `b2_object_lock_days` | `backups.yml` | pg/restic/nas 30, **pbs 0** | Object Lock (governance) per bucket; PBS off until verified |
| `nas_backup_window_stop_hour` | `backups.yml` | off | hard stop for long NAS uploads |
| `self_check_restic_max_gib` | `backups.yml` | 20 | bigger snapshots get `check` only, no full restore test |
| `/settings/CLOUDFLARE_ACCESS_APPS` | Infisical | absent | Cloudflare Access in front of listed apps |
| `/ansible/NAS_BACKUP_HEALTHCHECKS_URL`, `/k8s/alerts/WEBHOOK_URL` | Infisical | empty | dead-man switch, alert delivery |
| OpenVPN fallback, Traefik LAN shortcut | [manual steps §10.5](manual-steps.md#105-openvpn-fallback-surfshark-service-credentials), `traefik.yaml` | off | documented, commented out |
| `AUTO_APPROVE=1` | `make` | unset | skip the `yes` questions (automation only) |
