# Server journal

This journal records the server's design decisions and project history. The main
[README](../README.md) describes the IaC system; the [setup checklist](OPERATOR-SETUP.md)
and [recovery runbook](disaster-recovery.md) describe its operation.

Dates below record decisions or verified work. A planned action is not a completed action.
Actual device identities, network details, folder lists and credentials remain private.

## 23 November 2026 — Infrastructure automation

The project brought host configuration, VMs, Kubernetes apps, storage and independent
backups into one IaC workflow. Guardrails include verified install-disk targeting,
reviewed plans, generated credentials in Infisical, public-repository scans and restore tests.
The initial app-state plan was to initialise Home Assistant, Jellyfin and Alexandria
without importing their previous state; Skyjo model weights come from the image build.
Backrest was chosen for NAS backups after reliability problems with the previous UGOS job.

## Credential progress — 23 November 2026

- Infisical setup: completed with the operator; existing bootstrap login works.
- Cloudflare: token active; account/zone/domain settings saved; DNS, tunnel and Access read checks passed.
- Tailscale: OAuth login, requested Devices Core/Auth Keys/Services scopes and all three credential tags passed; devices/services reads succeeded. The operator tag is exactly `tag:k8s-operator`.
- Backblaze: dedicated `artzima-tofu` key created, verified and stored at `/tofu/B2_ADMIN_KEY_ID` and `B2_ADMIN_APPLICATION_KEY`, replacing the temporary master credentials. The existing master key was not rotated or revoked.
- Backblaze's authorization response reports region `us-east-005` and endpoint `s3.us-east-005.backblazeb2.com`; configuration and recovery examples use these.
- GitHub: classic PAT saved under `/k8s/ghcr/GHCR_PULL_TOKEN`; authentication and `read:packages` / `public_repo` scopes passed. It does not have the broader `repo` scope. The API did not report an expiry; record the chosen expiry separately.
- All five root credential sets and all required operator settings are present. Admin email and both Surfshark WireGuard values passed format checks. The workstation's existing `~/.ssh/id_ed25519.pub` was saved in `/settings/ADMIN_SSH_KEYS`, preserving any existing keys; private key material was not read or copied.
- `make preflight PREFLIGHT_ARGS=--accounts-only` passed on 23 November 2026. This checks required operator-secret presence, not a live VPN connection or hardware readiness. Hardware inspection, off-NAS backups, recovery-kit completion and bootstrap/cutover planning remain pending.
- These checks did not deploy infrastructure or change existing backup jobs.

## Initial rebuild retention decision — 23 November 2026

The operator wants a **fresh lab**, with no migration of old lab state. Wipe the verified
install disks on host01, node01, node02, node03 and pbs. Discard old VMs, containers,
databases, app settings, Home Assistant configuration, Jellyfin history and old lab backups.

**Only the operator's rips and personal data on the NAS survive.** The operator will supply
the exact absolute folder paths later. Until that list is recorded and checked, **NAS cleanup
must not start**. Do not infer protected paths from folder names. All other old NAS data is
in scope for removal, including old lab data inside the future deployment paths.

NAS cleanup is selective: do not format a disk, pool or volume containing protected files.
Before cleanup, verify an independent copy of the protected files, inspect mounts, symlinks
and nested paths, and prepare an exact deletion inventory for review. Exclude every protected
path and every parent directory containing one. Complete old-data cleanup before deploying
new apps into the same paths. Never use file dates alone to decide what to delete.

This decision applies to the **initial rebuild only**. Backups and catastrophic recovery for
the new lab remain required. No data was deleted when this decision was recorded.

### Initial NAS preparation plan (recorded, not executed)

**Decision, 23 November 2026:** discard all old lab state. Only the operator's rips and
personal files on the NAS survive. The exact protected paths are **not yet supplied**.
No cleanup can start until they are recorded. Old app settings, VMs, containers and lab
backups need no migration. Home Assistant and Jellyfin start fresh.

1. Ask the operator for the exact absolute NAS folders to preserve. Record them in the
   setup checklist, then configure `media_existing_paths` and `nas_personal_shares` as
   appropriate. These config lists support adoption/backups; they are not a deletion tool.
2. Inspect the NAS read-only: actual mount points, nested folders, symlinks, shared files
   and hardlinks. Resolve the protected paths to their actual storage locations.
3. Verify an independent copy of the protected files, including a test restore. A snapshot
   on the same NAS is an extra safeguard, not an independent copy.
4. Prepare an explicit inventory of proposed deletions across the NAS. Exclude the protected
   paths, their contents and any parent that contains them. Review overlaps before execution.
   Do not infer exclusions from names or infer age from timestamps.
5. Review the exact inventory with the operator before execution. Do not format a NAS
   disk, pool or volume containing protected files. No broad deletion command is provided
   here because the protected-folder list is still unknown.
6. Complete selective removal of old data before new apps use the same folders. This includes
   old Kubernetes PVCs and old databases under future deployment paths; `Retain` alone does
   not prevent a fresh app from attaching old files.
7. Verify the preserved files remain intact. Create the new lab folder layout, deploy fresh
   apps, adopt the preserved rips with the documented dry-run, and enable new backups.

This is a one-off rebuild procedure. Normal playbooks do not delete other NAS shares.
After deployment, all newly created app data is live data and must not be included in an
old-data cleanup inventory. Future catastrophic recovery restores the new lab's backups.

## 23 November 2026 — Private configuration and publication

Actual IP addresses, hardware MACs, physical role assignments, project settings and kit
locations moved into private configuration. Public files became fictional CI examples.
Live make targets use a private runtime; Flux receives its substitutions from a privately
applied ConfigMap. The private bundle was backed up in Infisical and its restore was verified.

The public repository was created at <https://github.com/DevArtech/artzima-iac>.
The first commit contained only the checked source and example configuration, with no
previous Git history. Public-tree and history scans passed. Upload used the operator's
existing SSH authentication because the PAT did not grant workflow creation.

## 23 November 2026 — Documentation structure

The primary documentation now describes the IaC system and its operating procedures.
The one-time rebuild decisions and preparation plan moved into this journal.
Storage safeguards and future backup/recovery procedures remain in the operating guides.

## 23 November 2026 — Registry publication containment

The first four application image builds succeeded, but their GHCR packages were public.
The workflow had relied on post-publication visibility checks. That did not enforce the
intended private state. All four packages were removed with a narrowly scoped workflow
that checked their exact identities and creation window. Authenticated package reads
returned not found and fresh anonymous pull requests were denied afterwards.

An empty-image registration test in the public repository also created a public package.
It contained no application code or model weights and was immediately removed. Registry
publication workflows were then removed from the public repository. A separate private
builder was prepared with repository and package privacy checks before application builds.
Its GitHub repository is not yet created. No server deployment occurred. Public removal
cannot establish whether anyone downloaded a copy during the exposure window.

## 23 November 2026 — Lab network behind a Firewalla (decided, not executed)

- Decision by the operator: a Firewalla Purple SE moves from Bridge to **Router mode** on build day,
  between the Xfinity gateway (home network) and the lab switch. The whole lab and the NAS move to
  their own untagged lab LAN; every machine keeps its host offset. Rationale:
  [decisions](decisions.md#the-lab-network-behind-a-firewalla-no-vlans-2026-11-23).
- Repository changes: only the private `network.yml` addressing changed (subnet, gateway, DNS = the
  Firewalla plus public fallbacks). The documentation-only pool, unmanaged addresses and home network
  were added, and the build-day steps written ([OPERATOR-SETUP B0](OPERATOR-SETUP.md#b0-network-firewalla-nas-workstation)).
- Pending on build day: Firewalla switch-over, NAS address, NFS rule and UPS host, the workstation's
  lab NIC, and deleting the obsolete Xfinity lab reservations from 22 November.

## 23 November 2026 — PBS datastore disk for the first build (decided, not executed)

- Decision by the operator: for the first build, the ZimaBoard's external USB backup disk may be
  wiped and reformatted as a fresh PBS datastore. The old local PBS backups on it are given up; the
  installer still never touches that disk (the OS goes on the eMMC).
- Implemented as a one-time opt-in in private configuration (`pbs_datastore_format_disk`, `false` by
  default, plus a confirmation pinned to the disk's serial). It is set `true` for build day only and
  back to `false` afterwards ([OPERATOR-SETUP B1](OPERATOR-SETUP.md#b1-install-and-deploy)).

## 23 November 2026 — PBS OS moves onto the USB datastore disk (decided)

- The PBS install onto the ZimaBoard's eMMC failed twice: the Proxmox auto-installer cannot install
  onto eMMC. Decision by the operator: install the PBS OS onto the external USB disk instead and keep
  the datastore on the same disk. The eMMC stays unused. This replaces "the OS goes on the eMMC" above.
- Implemented: the answer file selects the USB disk by serial and caps the OS partition
  (`install_lvm.hdsize`; on PBS the installer ignores `maxroot`, so the partition size is the only way
  to leave room). `make answers` allows the install serial to equal the datastore serial only with
  `pbs_os_on_datastore_disk: true`. The role `pbs` adds the free rest of the disk to the OS volume group
  as the logical volume `datastore`, formatted once and reused afterwards.
- Consequence: reinstalling PBS now wipes its local datastore; the offsite copy in B2 remains.

## 23 November 2026 — Home Assistant removed; Jellyfin starts fresh (decided)

- Decision by the operator: **Home Assistant leaves the lab entirely.** Removed from the code and
  docs: the HAOS VM (OpenTofu) and its image staging, the public route and `/apps.yaml` entry, the
  Traefik external Service, its NAS backup folder and VolSync/restic backup, and its Firewalla
  reservation. Rationale: [decisions](decisions.md#home-assistant-leaves-the-lab-2026-11-23).
- **No pre-wipe saving:** neither a Home Assistant backup nor Jellyfin's users and watch history are
  kept. Jellyfin starts fresh: its first-run wizard creates the admin user, and its libraries are
  `/data/media/{movies,tv,music}`.
- If the Infisical setting `/settings/CLOUDFLARE_ACCESS_APPS` lists `homeassistant`, remove that
  entry before the next `make cf-plan` (it refuses unknown apps).

## 23 November 2026 — Infisical split into two projects (implemented in code; not applied)

- Found by G.A.R.T.H. through the API: Infisical's free plan refuses custom roles ("Failed to create
  custom role due to plan RBAC restriction"). `make ids-apply` failed at the ESO custom role; the
  resources created before it remain in the `tofu/bootstrap` state and are adopted unchanged.
- Already done before the split: `make gen-secrets` (38 secrets, main project), `make b2-apply`
  (4 buckets, 7 keys). `make cf-apply` not yet run.
- Decision by the operator: a second project only for the cluster (`/k8s/**`), ESO = built-in
  Viewer on it alone ([decisions](decisions.md#infisical-two-projects-2026-11-23)). The project is
  created once by the operator; existing `/k8s` secrets move with `make secrets-migrate-k8s`
  ([OPERATOR-SETUP A2b](OPERATOR-SETUP.md#a2b-the-clusters-infisical-project)).
