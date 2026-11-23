# Secrets: Infisical

This repository is **public**. It holds **no secret material**, not even encrypted
files. Every secret, and every private-but-not-secret value (domain, email addresses,
SSH public keys, Cloudflare IDs), lives in **Infisical**. The repo only says
*where* each one lives: an Infisical folder and key name. Why: [decisions](decisions.md#secrets-infisical--external-secrets-not-sops).
What you do, step by step: [OPERATOR-SETUP](OPERATOR-SETUP.md).

**Five root credentials; everything else is derived or generated.**

| # | Root credential | Where | Derives |
|---|---|---|---|
| 1 | Infisical bootstrap identity `artzima-admin` | `~/.config/artzima/infisical.env` (not in Infisical) | the cluster's read-only identity `artzima-eso` (`tofu/bootstrap`), every generated secret (`make gen-secrets`) |
| 2 | Cloudflare API token | `/tofu/CLOUDFLARE_API_TOKEN` | the tunnel + its token (`tofu/cloudflare`) |
| 3 | Tailscale OAuth client | `/k8s/tailscale/OPERATOR_CLIENT_*` | the hosts' and routers' auth keys (`tofu/bootstrap`); also runs the k8s operator |
| 4 | Backblaze admin key | `/tofu/B2_ADMIN_*` | per bucket a backup key + a maintenance key (`tofu/backblaze`) |
| 5 | GitHub classic PAT | `/k8s/ghcr/GHCR_PULL_TOKEN` | pulls the private images; bootstraps Flux (read-only deploy key) |

Besides these, you provide a few **settings** (`/settings`: domain, mailbox, SSH public keys,
Cloudflare IDs) and the **Surfshark** WireGuard data (`/k8s/vpn`). Every random value (passwords,
API keys, the restic passwords, the state passphrase, the Vaultwarden admin hash) is created by
`make gen-secrets` (part of `make bootstrap`) **directly in Infisical**: generated in memory, never
printed, never written to disk; only **missing** keys are created, existing ones are never read
for overwriting or changed. Machine-made credentials (B2 keys, Tailscale keys, the tunnel token,
Proxmox tokens, the PBS fingerprint, the ESO identity) are written by the `make` step that makes them.

| Consumer | Reads Infisical with | Identity |
|---|---|---|
| Ansible | `infisical.vault` lookups in [`ansible/group_vars/all/secrets.yml`](../ansible/group_vars/all/secrets.yml), and the `create_secret`/`update_secret` modules (`proxmox_api`, `pbs` roles) | `artzima-admin` |
| OpenTofu (`tofu/`, `tofu/cloudflare/`, `tofu/backblaze/`, `tofu/bootstrap/`) | `infisical/infisical` provider: **ephemeral** `infisical_secret`, the `infisical_secrets` data source (`/settings` only), `infisical_secret` with write-only `value_wo`, and identity resources (`tofu/bootstrap`) | `artzima-admin` |
| `make` (state passphrase, answer server, `gen-secrets`, `preflight`, `eso-bootstrap`, `flux-bootstrap`) | [`scripts/infisical_exec.py`](../scripts/infisical_exec.py) (official `infisicalsdk`) puts values into one process's environment | `artzima-admin` |
| Kubernetes | External Secrets Operator: the `ClusterSecretStore` **infisical** plus one `ExternalSecret` per Secret ([`k8s/secrets/`](../k8s/secrets/README.md)) | `artzima-eso` (read-only, created by `tofu/bootstrap`) |

## Local configuration

**Local configuration, never committed.** Every `make` target (through
`scripts/infisical_exec.py`) loads the following, in this order (variables already in your environment win):

| File (path override) | Holds |
|---|---|
| `~/.config/artzima/infisical.env` (`ARTZIMA_INFISICAL_ENV`) | admin identity: `INFISICAL_UNIVERSAL_AUTH_CLIENT_ID`, `INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET` (mode 600) |
| `~/.config/artzima/infisical-project.env` (`ARTZIMA_INFISICAL_PROJECT`) | `INFISICAL_HOST`, `INFISICAL_PROJECT_ID`, `INFISICAL_PROJECT_SLUG`, `INFISICAL_ENV` |
| `./local.env` (`ARTZIMA_LOCAL_ENV`, gitignored, optional) | private overrides; not needed for the normal flow |

The values are passed to Ansible, OpenTofu and the answer server without printing
secret values. Actual project metadata lives in `.private/site/ansible/group_vars/all/infisical.yml`
and the local project environment file. Live make targets use the private deployment
copy; the in-cluster ESO configuration receives its settings from a privately applied
ConfigMap. The tracked YAML contains fictional CI settings only. See
[private configuration](PRIVATE-CONFIG.md) for backup and recovery.

**Outside Infisical:** the emergency kit (`emergency-kit.yaml`, `make emergency-kit`, printed with
`make dr-pdf`) lists the few items that must also exist printed/offline: Infisical's own recovery
codes, the restic passwords, the state passphrase, a Backblaze read-all key, and other accounts'
recovery codes. If Infisical itself is lost while the cluster runs, `make dr-secrets-from-cluster`
copies the cluster's values into a new project
([disaster recovery](disaster-recovery.md#emergency-kit)).

## Checking what's missing

```bash
make secrets-check     # which keys are missing in Infisical (names only, never values)
make secrets-folders   # create the folder layout (folders only)
make gen-secrets       # create every missing GENERATED key (GEN_ARGS=--dry-run lists them)
make preflight PREFLIGHT_ARGS=--accounts-only   # PASS/FAIL: are all of YOUR keys there?
```

Both work from [`secrets.manifest.yaml`](../secrets.manifest.yaml), the machine-readable list of
every folder and key. `make lint` checks offline that the manifest matches exactly what the code references.
`secrets-check` lists names with values hidden (`view_secret_value=False`), in sections:
"MISSING: you provide these", "not generated yet: run `make gen-secrets`", "pending (automation
writes these)". It exits 1 while one of yours is missing or a generated one doesn't exist yet.
Each key's `by:` (operator / generated / automation) and `generate:` recipe is in the manifest.

## Project layout

- **Organization:** yours (free Infisical Cloud plan).
- **Project:** slug `example-project` (Secrets Management). The ID is in your local project file.
- **Environment:** `prod` (one environment; the default `dev`/`staging` stay empty).
- **One folder per area** (already created in `prod` by `make secrets-folders`):

```
/settings            private but NOT secret (may appear in OpenTofu state, cluster vars)
/bare-metal          installer-only
/ansible             host configuration (+ Tailscale keys, B2 keys for PBS/NAS, PBS fingerprint)
/tofu                OpenTofu inputs (+ Proxmox token, ESO identity credentials)
/operator            generated logins for PEOPLE (Vaultwarden admin, Backrest): copy to your
                     password manager; nothing in the lab reads them
/k8s/cloudflared     \
/k8s/monitoring       |
/k8s/alerts           |  THE alert webhook URL (Alertmanager + Backrest on the NAS)
/k8s/tailscale        |  one folder per Kubernetes area, read by ESO
/k8s/ghcr             |
/k8s/alexandria       |
/k8s/media            |  media stack: arr API keys, qBittorrent login, Jellyfin API key
/k8s/vpn              |  Surfshark WireGuard (gluetun)
/k8s/vaultwarden      |  Vaultwarden admin token hash, backup passphrase, optional SMTP
/k8s/paperless        |  Paperless-ngx secret key + initial admin
/k8s/backup          /   off-site backups: B2 backup + maintenance keys (tofu/backblaze), restic password
```

## Every secret

**you** = you create it (root credentials, settings, Surfshark). **gen** = `make gen-secrets`
creates it (recipe from the manifest). Otherwise the named `make` step writes it.

| Path / key | What | Written by | Consumer |
|---|---|---|---|
| `/settings/PUBLIC_DOMAIN` | your Cloudflare zone, e.g. `example.org` | you | `tofu/cloudflare`; cluster `${public_domain}` via ESO → `flux-system/cluster-secret-vars`; `make verify` |
| `/settings/ADMIN_EMAIL` | mailbox for Proxmox notifications | you | answer files (`mailto`) |
| `/settings/ADMIN_SSH_KEYS` | root SSH **public** keys, one per line | you | Ansible `common`, OpenTofu (LXC root keys), answer files |
| `/settings/CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID` | dashboard → domain → Overview | you | `tofu/cloudflare` |
| `/settings/CLOUDFLARE_ACCESS_APPS` | optional JSON, e.g. `[{"app":"grafana","emails":["you@…"]}]`; absent = no Access | you | `tofu/cloudflare` |
| `/bare-metal/ROOT_PASSWORD_HASHED` | optional `mkpasswd -m yescrypt` hash; absent = root is SSH-key-only | you | answer server (in memory) |
| `/tofu/CLOUDFLARE_API_TOKEN` | **root #2**: Tunnel Edit, Access Apps and Policies Edit, Zone DNS Edit | you | `tofu/cloudflare` (ephemeral) |
| `/tofu/B2_ADMIN_KEY_ID`, `B2_ADMIN_APPLICATION_KEY` | **root #4**: may manage buckets (incl. retention/Object Lock) and keys ([OPERATOR-SETUP A2](OPERATOR-SETUP.md#a2-accounts-and-the-other-four-root-credentials)) | you | `tofu/backblaze` (ephemeral) |
| `/k8s/tailscale/OPERATOR_CLIENT_ID`, `OPERATOR_CLIENT_SECRET` | **root #3**: OAuth client (Devices Core, Auth Keys, Services write; tags `tag:k8s-operator`, `tag:artzima`, `tag:subnet-router`) | you | ESO → `tailscale/operator-oauth`; `tofu/bootstrap` (mints the auth keys) |
| `/k8s/ghcr/GHCR_PULL_TOKEN` | **root #5**: classic PAT, `read:packages` + `public_repo` | you | ESO → `ghcr-pull` (every namespace labelled `artzima.io/ghcr-pull`); `make flux-bootstrap` (in memory) |
| `/k8s/vpn/WIREGUARD_PRIVATE_KEY`, `WIREGUARD_ADDRESSES` | Surfshark *Manual setup → WireGuard* ([manual steps §10.1](manual-steps.md#101-before-the-flux-sync-in-this-order)) | you | ESO → `media/surfshark` (gluetun) |
| `/ansible/TAILSCALE_AUTHKEY`, `TAILSCALE_ROUTER_AUTHKEY` | reusable, pre-authorized, tagged (`tag:artzima` / `tag:subnet-router`), 90 days, re-minted when invalid | `make bootstrap` (ids-apply) | `tailscale` role |
| `/tofu/ESO_CLIENT_ID`, `ESO_CLIENT_SECRET` | universal-auth credentials of `artzima-eso` (scoped runtime read role) | `make bootstrap` (ids-apply) | `make eso-bootstrap` → `external-secrets/infisical-universal-auth` |
| `/k8s/cloudflared/TUNNEL_TOKEN` | tunnel connector token | `make bootstrap` (cf-apply) | ESO → `cloudflared/tunnel-token` |
| `/k8s/backup/PG_B2_*`, `/k8s/backup/RESTIC_B2_*`, `/ansible/PBS_B2_*`, `/ansible/NAS_B2_*` | per-bucket **backup** keys: list/read/write, **no `deleteFiles`** | `make bootstrap` (b2-apply) | CNPG `ObjectStore b2` (Secret `b2-pg`), VolSync movers (`<pvc>-restic`), `pbs_offsite`, Backrest |
| `/k8s/backup/PG_B2_MAINT_*`, `RESTIC_B2_MAINT_*`, `/ansible/NAS_B2_MAINT_*` | per-bucket **maintenance** keys (same capabilities), used only for retention | `make bootstrap` (b2-apply) | `<cluster>-retention` CronJobs (`b2-pg-maint`), `backup/restic-maintenance` + restore test (`restic-maint`), the NAS `restic-maint` container |
| `/tofu/PROXMOX_API_TOKEN`, `/k8s/monitoring/PVE_EXPORTER_TOKEN` | PVE API tokens | `make up` (proxmox-api) | `tofu/` proxmox provider; ESO → `monitoring/pve-exporter` |
| `/ansible/PBS_FINGERPRINT` | PBS certificate fingerprint | `make up` (pbs role) | `storage` role |
| `/tofu/TOFU_STATE_PASSPHRASE` | base64:32. **Losing it = losing the state** (emergency kit) | gen | state + plan encryption of every root |
| `/ansible/NUT_UPSMON_PRIMARY_PASSWORD`, `NUT_UPSMON_SECONDARY_PASSWORD` | hex:24 | gen | `nut` role |
| `/ansible/PBS_PVE_USER_PASSWORD` | hex:32 | gen | `pbs` role (`pve@pbs`), `storage` role |
| `/ansible/NAS_RESTIC_PASSWORD` | base64:48: the NAS restic repo. **Keep a copy outside the lab** | gen | Backrest + `restic-maint` on the NAS |
| `/ansible/NAS_BACKREST_USER`, `NAS_BACKREST_PASSWORD_BCRYPT` | `admin`; base64(bcrypt) of `/operator/BACKREST_PASSWORD` | gen | Backrest `auth` |
| `/ansible/NAS_BACKUP_HEALTHCHECKS_URL` | optional dead-man switch `https://hc-ping.com/<ping-key>`; **empty** = off | gen (empty), you | Backrest plan hooks |
| `/k8s/alerts/WEBHOOK_URL` | THE alert endpoint (https; Headquarters later); **empty** = alerts are not sent yet | gen (empty), you | ESO → `monitoring/alert-webhook` (Alertmanager); Backrest hooks (`generic+…`) and the NAS `restic-maint` |
| `/operator/VAULTWARDEN_ADMIN_PASSWORD`, `/operator/BACKREST_PASSWORD` | logins for **you** (password manager) | gen | nothing (their hashes are) |
| `/k8s/vaultwarden/ADMIN_TOKEN` | Argon2id PHC hash (m=65540, t=3, p=4) of `/operator/VAULTWARDEN_ADMIN_PASSWORD` | gen | ESO → `vaultwarden/vaultwarden` |
| `/k8s/vaultwarden/BACKUP_PASSPHRASE` | base64:32. **Keep a copy outside Vaultwarden** | gen | nightly NAS backup (gpg) |
| `/k8s/vaultwarden/SMTP_HOST`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD` | empty = no mail; fill in to enable | gen (empty), you | ESO → `vaultwarden/vaultwarden` (only non-empty keys emitted) |
| `/k8s/monitoring/GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD` | `admin`, base64:24 | gen | ESO → `monitoring/grafana-admin` |
| `/k8s/ghcr/GHCR_USERNAME` | `devartech` | gen | ESO → `ghcr-pull` |
| `/k8s/alexandria/ALEXANDRIA_MCP_TOKEN`, `LOGIN_USERNAME`, `LOGIN_PASSWORD` | hex:32, `admin`, hex:16 | gen | ESO → `alexandria/alexandria` |
| `/k8s/alexandria/LEGACY_BEARER_TOKEN`, `OPENAI_API_KEY` | empty (= reuse the MCP token / no embedder key) | gen (empty) | same |
| `/k8s/media/{SONARR,RADARR,LIDARR,PROWLARR,BAZARR}_API_KEY`, `SEERR_API_KEY` | hex:16 / hex:32, **pre-seeded** into the apps | gen | ESO → `media/media-apikeys`: the apps, media-wiring Job, Recyclarr |
| `/k8s/media/QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` | `admin`, base64:24 | gen | ESO → `media/qbittorrent` |
| `/k8s/media/JELLYFIN_API_KEY` | empty until you create the key in Jellyfin ([OPERATOR-SETUP C](OPERATOR-SETUP.md#c-after)); the wiring Job skips the Jellyfin connection while empty | gen (empty), you | ESO → `media/jellyfin-api` |
| `/k8s/paperless/SECRET_KEY`, `ADMIN_USER`, `ADMIN_PASSWORD` | base64:48, `admin`, base64:24 (first start only) | gen | ESO → `paperless/paperless` |
| `/k8s/backup/RESTIC_PASSWORD` | base64:48: every VolSync repo. **Keep a copy outside the lab** | gen | `<pvc>-restic` Secrets, `restic-maint` |

Not in Infisical, on purpose:

- **Skyjo** has no secrets besides `ghcr-pull`.
- **Media stack:** `SERVER_COUNTRIES` (gluetun) is not secret: `vpn_server_countries` in
  `ansible/group_vars/all/media.yml`. The arr/Bazarr **UI logins** are created at first login and
  live in your password manager (the apps don't read them from env). The Surfshark **OpenVPN** service
  credentials are only needed for the fallback ([manual steps §10.5](manual-steps.md#105-openvpn-fallback-surfshark-service-credentials)),
  which adds `/k8s/vpn/OPENVPN_USER`/`_PASSWORD` to the manifest.
- **Paperless API token for Headquarters (G.A.R.T.H.):** created by the operator in Paperless
  (user `hq` → *My Profile → API Auth Token*), stored in **HQ's own credential store**. It is not in
  Infisical or this repo ([manual steps §13.3](manual-steps.md#133-api-access-for-headquarters-garth)).
- **CNPG:** database credentials are never in Infisical. The operator generates them itself
  (Secret `<cluster>-app`, e.g. `vaultwarden/vaultwarden-db-app`, whose `uri` is Vaultwarden's
  `DATABASE_URL`). Only external credentials are in Infisical: the barman-cloud B2 keys
  (`/k8s/backup/PG_B2_*`).
- The **tailnet name** isn't used anywhere in the repo. If you ever need it, put it in `/settings`.

## The two machine identities

The free plan allows a handful of machine identities (5 at the time of writing). This uses 2.

| Identity | Created by | Project role | Used from | Credentials |
|---|---|---|---|---|
| `artzima-admin` (exists) | you, once | **Admin** (writes secrets; adds `artzima-eso` to the project). Organization role **Admin** or a custom role that may create identities | your admin workstation | `~/.config/artzima/infisical.env` |
| `artzima-eso` | `tofu/bootstrap` (`make bootstrap`) | **Scoped read-only**: `/k8s/**` and `/settings/PUBLIC_DOMAIN` | the cluster (ESO) | Infisical `/tofu/ESO_CLIENT_*` → `make eso-bootstrap` |

Hardening (optional, recommended):

- Universal Auth settings: a short **Access Token TTL** (e.g. 1 h for admin), and **Trusted IPs**.
  Set the admin identity to your home/Tailscale exit IP, and ESO to the lab's public egress IP
  (`artzima-eso`'s settings are in `tofu/bootstrap/main.tf`).
- The free plan's project roles are project-wide, so `artzima-eso` can read every folder,
  including `/ansible` and `/tofu`. If your plan offers custom roles or additional privileges,
  limit it to `/k8s/**` plus `/settings/PUBLIC_DOMAIN`. Otherwise, move the `/k8s` folders into a
  second project and point the `ClusterSecretStore` at it.

`local.env` (optional; copy [`local.env.example`](../local.env.example), then `chmod 600 local.env`) is
gitignored, scanned for by `make check-public`, and refused by the pre-commit hook. The two
`~/.config/artzima` files are outside the repo.

## Bootstrap order

The checklist with every click is [OPERATOR-SETUP](OPERATOR-SETUP.md); in short:

1. `make deps`, `make hooks`; the bootstrap identity in `~/.config/artzima/`.
2. `make secrets-folders`; put the five root credentials, `/settings` and `/k8s/vpn` in.
   `make preflight PREFLIGHT_ARGS=--accounts-only` must PASS.
3. `make bootstrap`: `gen-secrets` (every generated key), `ids-apply` (Tailscale keys, `artzima-eso`),
   `b2-apply` (B2 keys), `cf-apply` (tunnel token). Afterwards `make secrets-check` lists only
   "pending" keys (`PBS_FINGERPRINT`, the Proxmox tokens).
4. USB installs, `make preflight`, `make up`: Ansible writes `PBS_FINGERPRINT` and the Proxmox
   tokens on the way; `eso-bootstrap` hands ESO its identity; Flux installs ESO, then the `secrets`
   Kustomization waits until **every** `ExternalSecret` is synced (one missing key holds up
   everything after it), then the controllers and apps start.
5. Check: `make verify` (ExternalSecrets synced, among others), or
   `kubectl get clustersecretstore infisical` and `kubectl get externalsecret,clusterexternalsecret -A`.

## Day 2

- **Change a value:** edit it in Infisical. ESO refreshes every hour; to force it, run
  `kubectl -n <ns> annotate externalsecret <name> force-sync=$(date +%s) --overwrite`.
  Pods that read it from env need `kubectl rollout restart`.
- **Rotate the ESO identity:** `tofu -chdir=tofu/bootstrap apply -replace=infisical_identity_universal_auth_client_secret.eso`
  through `make ids-apply` (new client secret into Infisical), then `make eso-bootstrap`.
  The admin identity: create a new client secret in the Infisical UI, update
  `~/.config/artzima/infisical.env`, revoke the old one.
- **Tailscale auth keys:** they expire after `tailnet_key_expiry_days` (90); `make ids-apply`
  re-mints invalid keys (only needed to add or re-install hosts: joined hosts stay joined).
- **A generated secret:** delete it in Infisical, `make gen-secrets` creates a new one; then
  re-run its consumer (`make <playbook>`, or ESO refresh + `kubectl rollout restart`). Not for the
  restic passwords or `TOFU_STATE_PASSPHRASE` (they encrypt existing data).
- **The PBS fingerprint** is re-written by `make pbs` when the certificate changes.
- **Rotate the Proxmox tokens:** `make proxmox-api ANSIBLE_ARGS="-e proxmox_api_rotate=true"`.
- **Rotate the tunnel token:** the tunnel, its secret and the app DNS records carry `prevent_destroy`
  (guardrail). Remove those lines in a reviewed change, re-create the tunnel
  (`tofu -chdir=tofu/cloudflare apply -replace=random_id.tunnel_secret` through `make cf-plan cf-apply`),
  then put them back. The new token is written to Infisical automatically. Removing an app from
  `/apps.yaml` needs the same deliberate step for its DNS record.

## Where secrets still exist at rest (honestly)

| Place | What | Protection |
|---|---|---|
| Infisical | everything | Infisical's encryption, identity scoping, audit log |
| `tofu/terraform.tfstate`, `tofu/cloudflare/terraform.tfstate` (gitignored) | Talos machine secrets + client config, tunnel secret + token, `/settings` values | OpenTofu state **encryption** (AES-GCM, passphrase from Infisical). The Proxmox and Cloudflare API tokens are **not** in state (ephemeral) |
| `tofu/out/kubeconfig`, `talosconfig` (gitignored) | cluster admin credentials | file mode 0600; keep the workstation disk encrypted |
| `~/.config/artzima/infisical.env` | the admin identity | 0600; the key to everything, so treat it like a password-manager entry |
| `tofu/bootstrap/terraform.tfstate` (gitignored) | the Tailscale auth keys, the ESO client secret | state **encryption** (the providers have no write-only outputs) |
| Kubernetes Secrets (etcd) | the synced values | Talos encrypts Secrets in etcd at rest by default |
| NAS `/volume1/Artzima/backups/vaultwarden` | Vaultwarden DB dumps + `/data` (RSA signing keys, attachments) | gpg AES256 with `BACKUP_PASSPHRASE`; vault items are also end-to-end encrypted by the clients |
| `tofu/backblaze/terraform.tfstate` (gitignored) | the per-bucket backup + maintenance keys | state **encryption**; the B2 admin key is **not** in state (ephemeral) |
| NAS `/volume1/Artzima/backrest/` (root, 0700) | Backrest config + `maint/maint.env`: the NAS repo password, its backup and maintenance B2 keys, the UI bcrypt hash, the alert URL | file permissions only; the share is inside the lab folder, never exported to clients other than the lab subnet |
| Backblaze B2 | Postgres base backups + WAL, restic repos, PBS chunks, the NAS restic repo | SSE-B2 (AES256) at rest; restic is also encrypted client-side (`RESTIC_PASSWORD`); barman over TLS |
| NAS `/volume1/Artzima/k8s/` | Postgres volumes and CNPG snapshot tarballs (Vaultwarden DB) | **not** encrypted at rest by this repo (NAS volume encryption is a UGOS option); vault items inside are end-to-end encrypted, account metadata (emails, org names) is not |

Guards against leaks in git: `make check-public` (gitleaks over everything git would publish,
plus SOPS-leftover and gitignore checks), the pre-commit hook, and CI
(`.github/workflows/security.yml`, full history on every push and PR; `ci.yml` runs `make lint`).

### Runtime identity boundary

Project-wide Viewer would let a compromised cluster read administrator credentials.
The custom role permits only secret reads in the configured environment under
`/k8s/**`, plus `/settings/PUBLIC_DOMAIN`. It has no write permissions and no
access to `/tofu`, `/ansible`, `/operator` or other settings. See the
[Infisical permission reference](https://infisical.com/docs/internals/permissions/project-permissions).

During bootstrap, verify custom-role support and effective access. If unsupported,
stop; never fall back to Viewer. Before installing the identity in Kubernetes, test
one permitted runtime read and denied reads of `/tofu/B2_ADMIN_APPLICATION_KEY` and
`/ansible/NAS_RESTIC_PASSWORD`. Offline plans cannot verify account permissions.
The identity still reads runtime Tailscale OAuth and backup maintenance credentials;
it does not isolate individual apps from each other.
