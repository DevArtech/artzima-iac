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

- Install-disk serials for host01, node01, node02, node03 and pbs are placeholders. Collect real serials read-only and confirm each target disk before preparing install answers.
- `media_existing_paths` and `nas_personal_shares` are empty. Inspect the NAS and record the media roots and personal shares to preserve/back up. Verify NFS, ownership, Docker and sudo prerequisites.
- Verify the scoped ESO role supports runtime reads and denies administrator-secret reads during cloud bootstrap.
- Review the bootstrap plans and generate the remaining credentials/passwords; schedule DNS cutover to avoid breaking the running lab.
- Emergency-kit locations and rotation dates remain unrecorded (`TODO`/empty). Finish the independent recovery credentials and printed kit before destructive work.
- The reviewed initial commit is published to `https://github.com/DevArtech/artzima-iac` on `main`. Public-tree and history scans passed. GitHub validation/security checks and the first image builds started; their results and private package visibility still need verification before deployment.
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
  *Project example-project → Access Control → Machine Identities → artzima-admin*: role **Admin**.

### A2. Accounts and the other four root credentials

Each value goes into Infisical (<https://app.infisical.com> → your actual project (read its name from the private settings) →
environment **prod** → the folder → *Add secret*). The folders appear with `make secrets-folders`.

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

### A3. Fill Infisical with everything else: `make bootstrap`

- [ ] **Existing DNS records use the same hostnames?** Review conflicts with the managed
  tunnel before applying the plan. Schedule any DNS changes for the deployment window.
- [ ] `make bootstrap`. In order, each with its plan and a `yes`:
  generated secrets (`make gen-secrets`: random passwords, API keys, hashes; only **missing** keys,
  existing ones are never touched) → Tailscale auth keys + the cluster's read-only Infisical
  identity (`tofu/bootstrap`) → Backblaze buckets + per-bucket keys (`tofu/backblaze`) → Cloudflare
  tunnel, DNS, Access (`tofu/cloudflare`). Re-running it is safe.
- [ ] **Copy the logins for people** into your password manager: Infisical folder `/operator`
  (`VAULTWARDEN_ADMIN_PASSWORD`, `BACKREST_PASSWORD`), plus `/k8s/paperless/ADMIN_PASSWORD`,
  `/k8s/monitoring/GRAFANA_ADMIN_PASSWORD`, `/k8s/alexandria/LOGIN_PASSWORD`,
  `/k8s/media/QBITTORRENT_PASSWORD`.
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

- [ ] Record the media sources and personal backup shares in private configuration. Verify permissions, mount boundaries and an independent backup before changing storage.
- [ ] NFS export `/volume1/Artzima` allowed for the lab subnet (no_root_squash where §3 says).
- [ ] The media data folder and its owner (`/volume1/Artzima/data`, §3).
- [ ] Where are the existing rips? → `media_existing_paths` in `ansible/group_vars/all/media.yml`
  (then `make media-adopt` later, dry-run first).
- [ ] Which personal shares to back up off-site? → `nas_personal_shares` in `backups.yml`.
- [ ] UGOS **Docker** app installed (Backrest runs there); your NAS user can `sudo` (asked once
  per `make up`/`make nas-backup`, the `-K` prompt).
- [ ] Folders: `sudo mkdir -p /volume1/Artzima/{backups/vaultwarden,backups/paperless,paperless/data,paperless/media,paperless/consume,homeassistant/backups}`.
- [ ] If a backup provider already exists, retain it until Backrest completes a verified backup and test restore ([backup-provider transition](backups.md#backup-provider-transition)).

### A6. Private deployment inventory

- [ ] [`ansible/hosts.yml`](../ansible/hosts.yml) is an example. Edit `.private/site/ansible/hosts.yml` instead: for every Proxmox/PBS machine `mac` (primary
  NIC), `install_disk` and **`install_disk_serial`**. The answer server only answers machines with
  a known MAC and installs only onto the disk with that serial. <a id="disk-serials"></a>**Disk
  serials:** on the target system `udevadm info -q property -n /dev/nvme0n1 | grep ^ID_SERIAL=`
  (or `lsblk -d -o NAME,SERIAL,MODEL`); in the installer's debug shell the same command. A
  unique part of `ID_SERIAL` is enough (the match is `*<serial>*`).
- [ ] `timezone` in `ansible/group_vars/all/lab.yml`.
- [ ] `make render`, `make lint`, `make check-public` → all green. Commit and push to GitHub
  (**public**); *Actions → images* builds the private images; then make sure each package is
  **Private** ([manual steps §7](manual-steps.md#7-private-images-ghcr-pat-and-pull-secret) steps 1-2).

### A7. Hardware and network prerequisites

- [ ] Verify BIOS settings and DHCP reservations: [manual steps §1-2](manual-steps.md#1-bios--firmware-on-every-machine).
- [ ] Confirm the intended storage paths and independent backup coverage before deployment.

---

## B. Build day

- [ ] **USB installs** (hands-off): `make answers` (validates), `install/prepare-iso.sh …`,
  `make answer-server`, then boot each machine from the stick
  ([install/README.md](../install/README.md)). Unknown MAC or wrong disk serial = the server
  refuses (HTTP 403/412) and nothing is installed.
- [ ] `make preflight` → every line `PASS`: Infisical keys present, every host on SSH, MAC and
  disk serial match `hosts.yml`, NAS export reachable. Fix any `FAIL` first.
- [ ] `make up`. It runs, each after showing its plan/task list and your `yes`:
  `site` (hosts: base, network, cluster + QDevice, PBS, storage, UPS, Tailscale, GPU, API tokens;
  writes `PBS_FINGERPRINT` and the Proxmox tokens into Infisical) → `tofu-apply` (VMs/LXCs, Talos)
  → `lxc-devices` → `ts-routers` → `jellyfin` → `pbs-offsite` → `nas-backup` (asks for the NAS
  sudo password) → `eso-bootstrap` → `flux-bootstrap`. If one step fails, fix it and re-run
  `make up` (everything is idempotent), or run the failed step alone (`make <step>`).
- [ ] **Host01 reboot** after the NVIDIA driver (the `site` output says so).
- [ ] **UPS:** on the NAS set *Network UPS slave* to host01 ([manual steps](manual-steps.md#3-ugreen-nas-ugos)),
  then `make nut-check`.
- [ ] Wait ~15 minutes, then `make verify`. Backups and restore tests show `FAIL` ("never") until
  their first run: check again the next morning.

---

## C. After

- [ ] **Jellyfin:** `https://jellyfin.<domain>` → setup wizard, libraries `/data/media/{movies,tv,music}`
  ([manual steps §10.3](manual-steps.md#103-first-logins)), *Dashboard → API Keys → +* (name
  `arr-stack`) → put the key into Infisical `/k8s/media/JELLYFIN_API_KEY` (replace the empty value).
  Until then the wiring Job skips only the Sonarr/Radarr/Lidarr → Jellyfin connection; to apply it
  now: `kubectl -n media delete job media-wiring --ignore-not-found && flux reconcile kustomization apps`.
  "Known proxies" = the Talos node IPs ([addresses](addresses.md)).
- [ ] **Media stack:** VPN check, Recyclarr, first logins, Seerr wizard
  ([manual steps §10.2-10.3](manual-steps.md#102-after-the-sync)). Existing rips: `make media-adopt`
  (dry-run), then `make media-adopt MEDIA_ADOPT_ARGS=--apply`.
- [ ] **Vaultwarden:** <http://vault-admin/admin> over Tailscale with `/operator/VAULTWARDEN_ADMIN_PASSWORD`,
  invite yourself, create the account (+2FA) at `https://vault.<domain>`
  ([manual steps §12.2](manual-steps.md#122-first-run)).
- [ ] **Paperless:** <http://paperless> over Tailscale, user `admin` / `/k8s/paperless/ADMIN_PASSWORD`;
  create the `hq` user and its **API token for Headquarters** (stored in HQ, not here)
  ([manual steps §13.2-13.3](manual-steps.md#132-first-login)).
- [ ] **Alexandria:** first logins ([manual steps §8](manual-steps.md#8-alexandria-secrets-library-first-login-embedder)).
- [ ] **Home Assistant:** configure a fresh installation, `trusted_proxies` = Talos node IPs, network storage
  for backups ([backups](backups.md#setup-order) step 6).
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
- [ ] **Next morning:** `make verify` → all `PASS`.

Self-checks run by themselves from now on: the 1st of each month a restic repository is restored
and verified, the 2nd each Postgres database is recovered from B2 into a scratch cluster and
queried. A `FAIL` alerts; the quarterly manual test is still worth doing ([backups](backups.md#quarterly-restore-test-checklist)).

---

## D. Later

- [ ] **Firewalla + VLAN switch-over:** [README](../README.md#moving-the-lab-to-a-vlan-later) and
  [manual steps §5](manual-steps.md#5-later-vlans).
- [ ] **Pi-hole:** on hold (not in this repo yet).
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
