# Manual steps (not automatable)

Addresses below are today's values from `ansible/group_vars/all/network.yml` +
`ansible/hosts.yml`. If those change, use the new ones (for example, `make render` and
then read `k8s/clusters/artzima/cluster-vars.yaml`).

## 1. BIOS / firmware, on every machine

| Setting | Value | Why |
|---|---|---|
| Boot order | USB first for the install, then internal disk first | hands-off install, then normal boots |
| Wake-on-LAN | **Enabled** (often "Power On by PCI-E/LAN") | remote power-on after a UPS shutdown |
| Restore on AC power loss | **Power On** ("Last state" is not enough after an FSD) | the lab comes back by itself when mains returns |
| Virtualization | VT-x/AMD-V and VT-d/IOMMU **on** | VMs; GPU on host01 |
| Secure Boot | Off (simplest with the NVIDIA DKMS module on host01) | |
| Example laptop host | Disable "battery only" / lid-close sleep; plug in permanently | it is a server now |
| Example eMMC host | Boot from eMMC after install; second NIC unused for now | |

## 2. Xfinity gateway: DHCP reservations

The Xfinity gateway has no VLANs and no custom DNS records. In **Connected
Devices → Reserved IP** (or "Add device with reserved IP"), reserve:

Reserve **every row** of the generated table in [`addresses.md`](addresses.md)
(bare metal, guests and the NAS). HAOS takes DHCP, so its reservation **is** its
IP. The k8s API VIP has no MAC. If the UI allows it, shrink the DHCP pool so it
never hands out the lab range (today .100-.199).

Guests use static IPs, but reserving their fixed MACs prevents the gateway from leasing
the same addresses to someone else.

## 3. UGREEN NAS (UGOS)

**NFS share:** *Control Panel → File Service → NFS* (enable NFSv4.1), then on the
shared folder **Artzima** → NFS permissions:

- Allow `192.0.2.0/24` (i.e. `lab_subnet`), read/write, **no_root_squash**
  (Proxmox vzdump and the k8s CSI driver create files as root), sync.
- Create the sub-folder `k8s` (used by the `nfs-artnas` StorageClass:
  `/volume1/Artzima/k8s`).
- Create the app-backup folder for Vaultwarden's nightly encrypted backup (static PV
  `vaultwarden-backups`; owner = the uid the backup job runs as):
  ```bash
  sudo mkdir -p /volume1/Artzima/backups/vaultwarden
  sudo chown 1000:1000 /volume1/Artzima/backups/vaultwarden && sudo chmod 0700 /volume1/Artzima/backups/vaultwarden
  ```
- **Initial rebuild:** preserve only the exact rip/personal folders the operator will identify.
  All other old NAS data is in scope for selective removal before deployment. Cleanup is blocked
  until the protected list and independent backup are verified; do not format volumes containing
  protected files. Follow [§11](#11-optional-clean-up-the-old-labs-files-on-the-nas-after-verification)
  before creating new app folders. Normal deployment writes only inside `/volume1/Artzima`.
- **Media data folder** (the media stack, [§10](#10-media-stack-arr--qbittorrent--seerr)):
  one folder for downloads **and** the library, so imports are hardlinks, not copies.
  Its name is `media_data_subdir` and its owner is `media_puid`:`media_pgid` in
  `ansible/group_vars/all/media.yml` (today `data`, 1000:1000). It sits inside the `Artzima`
  export, so the NFS rule above already covers it. Over SSH on the NAS:
  ```bash
  sudo mkdir -p /volume1/Artzima/data/torrents/tv /volume1/Artzima/data/torrents/movies /volume1/Artzima/data/torrents/music \
                /volume1/Artzima/data/media/tv /volume1/Artzima/data/media/movies /volume1/Artzima/data/media/music
  sudo chown -R 1000:1000 /volume1/Artzima/data
  sudo find /volume1/Artzima/data -type d -exec chmod 2775 {} +   # setgid: files keep group 1000
  ```
  Run these **once, on the empty folder**. Existing rips are brought in by `make media-adopt`
  (hardlinks: [README](../README.md#bringing-in-your-existing-rips)). After that, **never**
  `chown -R`/`chmod -R` the data folder: a hardlink shares its inode, so that would change the
  originals' owner and mode too. That doesn't lose data, but it is not "leaving them untouched".
  Why exactly this: every media pod runs as 1000:1000 with umask 002. Kubelet applies the
  pods' `fsGroup` (1000) to the volume only if the folder's **root** doesn't already match
  (`fsGroupChangePolicy: OnRootMismatch`); with the root at `1000:1000` and `2775` it never
  walks the library. Files must stay **world-readable** (`o+r`, the umask gives 664/775): the
  Jellyfin LXC reads them as "other" (§10). Don't create this folder through UGOS' share
  wizard as a separate share with its own ACLs; a plain sub-folder keeps POSIX modes.
- **UGOS quirk:** a saved NFS rule is sometimes not actually exported. Check
  from any host with `showmount -e 192.0.2.156`. If `/volume1/Artzima` is missing,
  SSH to the NAS (enable SSH in the UGOS control panel) and run
  `sudo exportfs -ra`. If that still does not export it, run `sudo exportfs -o rw,sync,no_root_squash,no_subtree_check 192.0.2.0/24:/volume1/Artzima`.
  The NAS may need this again after a UGOS update or a reboot.

**UPS:** *Control Panel → Hardware → UPS* → enable, mode **Network UPS slave**,
UPS server IP **192.0.2.100** (host01). UGOS logs in as `monuser`/`secret` to
the UPS name `ups0`. Both are fixed by UGOS, which is why host01 serves a
`dummy-ups` alias called `ups0` that mirrors `goldenmate`. Choose "shut down when the UPS
reports low battery / FSD".

## 4. Tailscale admin console

- Tags: `tag:artzima` (hosts), `tag:subnet-router` (ts-router-1/2),
  `tag:k8s-operator` (operator). The auth keys are **not** made by hand: `make bootstrap`
  (`tofu/bootstrap`) mints them with the OAuth client below and writes
  `/ansible/TAILSCALE_AUTHKEY` and `/ansible/TAILSCALE_ROUTER_AUTHKEY` ([secrets](secrets.md)).
  The policy snippet (tagOwners, autoApprovers) is in [OPERATOR-SETUP A2](OPERATOR-SETUP.md#a2-accounts-and-the-other-four-root-credentials).
- Approve the subnet route `192.0.2.0/24` on **both** routers. Or add
  `"autoApprovers": {"routes": {"192.0.2.0/24": ["tag:subnet-router"]}}` to the
  policy file. With two routers advertising the same route, Tailscale fails over
  automatically.
- Enable MagicDNS. Optional: split DNS for `artzima.local`.
- ONE OAuth client (root credential #3) for the Kubernetes operator **and** the host keys:
  scopes Devices Core, Auth Keys, Services write; tags `tag:k8s-operator`, `tag:artzima`,
  `tag:subnet-router`. Store it in Infisical as `/k8s/tailscale/OPERATOR_CLIENT_ID` and
  `OPERATOR_CLIENT_SECRET`.
- The operator's proxies (the media apps' tailnet devices, [§10](#10-media-stack-arr--qbittorrent--seerr))
  are tagged `tag:k8s`. The policy file needs the `tagOwners` from OPERATOR-SETUP A2, and an ACL/grant
  that lets your own devices reach `tag:k8s` (and nothing else reach it).

## 5. Later: VLANs

The current **UGREEN 2.5G switch is unmanaged**. Its "VLAN mode" is only *port
isolation* (each port talks only to the uplink), not 802.1Q, so it can't carry a
tagged lab VLAN.

Plan:

1. Add a **TP-Link TL-SG108E** (Easy Smart, 802.1Q) and a **lab router** that can
   route between VLANs (OPNsense, pfSense, OpenWrt, MikroTik…). The Xfinity gateway can't do
   VLANs or inter-VLAN routing.
2. On the TL-SG108E (*VLAN → 802.1Q VLAN*, enable it): create VLAN `lab_vlan_id`
   (e.g. 20). Hypervisor ports are **tagged** for 20 (and any other guest VLANs).
   The router uplink is tagged. Leave the native VLAN 1 untagged on a spare port for
   console/rescue. In *802.1Q PVID Setting*, set PVID 20 for access ports of
   untagged lab devices (for example the NAS, if it stays untagged).
3. Lab router: interface for VLAN 20 = `lab_gateway`, DHCP reservations
   (copy the table above), DNS (add `*.artzima.local` here), NAT to the Xfinity
   uplink.
4. Then follow "Moving the lab to a VLAN later" in the README. In short: edit
   `network.yml`, `make render network common tofu-apply`.
5. The NAS: UGOS supports VLAN tagging on its NIC (network settings). Otherwise
   put it on an untagged access port with PVID 20. Update the NFS rule and the UPS
   server IP if the subnet changes.

## 6. Cloudflare Tunnel (public apps)

The tunnel, its ingress rules, the DNS records and the optional Access apps are
**code** (`tofu/cloudflare`, `make cf-*`). All of them come from **`/apps.yaml`**, the same
file that generates the Traefik routes. The only manual parts are the account
prerequisites below. Do not create the tunnel or hostnames in the dashboard: OpenTofu owns them.

1. **Zone:** the domain must use Cloudflare DNS (any plan). Store it in Infisical as
   `/settings/PUBLIC_DOMAIN`. It is not in the public repo.
2. **IDs:** copy the *Account ID* and *Zone ID* from the domain's Overview page (right
   column) into `/settings/CLOUDFLARE_ACCOUNT_ID` and `/settings/CLOUDFLARE_ZONE_ID`.
3. **API token:** *My Profile → API Tokens → Create Token → Custom*, with these permissions:
   - Account → **Cloudflare Tunnel: Edit**
   - Account → **Access: Apps and Policies: Edit** (only needed if you use `access_apps`)
   - Zone → **DNS: Edit**, limited to that one zone

   Set a TTL or an IP filter if you like. Store it as `/tofu/CLOUDFLARE_API_TOKEN`.
4. **State passphrase:** `/tofu/TOFU_STATE_PASSPHRASE` (shared with `tofu/`) is generated by
   `make gen-secrets`. Optional: `/settings/CLOUDFLARE_ACCESS_APPS` (step 8).
5. **Apply:** part of `make bootstrap` (alone: `make cf-apply`, which plans and asks `yes`). The
   tunnel, its secret and the DNS records carry `prevent_destroy`. This creates the tunnel `artzima`
   (remotely managed), one ingress rule per app to
   `http://traefik.traefik.svc.cluster.local:80` plus the `http_status:404` catch-all,
   and a proxied `CNAME <app>.<domain> → <tunnel-id>.cfargotunnel.com` per app.
6. **Token into the cluster:** nothing to do. `cf-apply` also writes the tunnel token
   into Infisical as `/k8s/cloudflared/TUNNEL_TOKEN` (write-only, never printed). ESO syncs it into
   `cloudflared/tunnel-token`, Flux starts the 2 cloudflared replicas, and the tunnel shows 2
   healthy connectors.
7. **SSL/TLS:** enable *Always Use HTTPS*. Universal SSL covers `<app>.<domain>`.
   The origin is the tunnel, so no origin certificates are needed.
8. **Cloudflare Access (optional, per app):** set the Infisical secret
   `/settings/CLOUDFLARE_ACCESS_APPS` (JSON, so your addresses stay out of git), e.g.
   `[{"app":"grafana","emails":["you@…"]},{"app":"wizarr","emails":["you@…"]}]`,
   then `make cf-apply`. Recommended: `grafana`. The one-time PIN login
   works without an IdP: enable it under *Zero Trust → Settings → Authentication*.
   Don't put `jellyfin` or `homeassistant` behind browser Access if you use their
   mobile apps, because the apps can't complete the Access login.
   **Never put `vault` (Vaultwarden) behind Access.** The Bitwarden mobile, desktop and
   browser-extension clients talk to the server directly and can't pass an Access login.
   `/apps.yaml` marks it `access: never`, so `make cf-plan` **fails** if it is listed. Its
   protection is Vaultwarden's own login, end-to-end vault encryption, closed signups, and the
   `/admin` page answering 403 on the public host ([§12](#12-vaultwarden)).
9. **Check:** `kubectl -n cloudflared logs deploy/cloudflared` shows "Registered tunnel
   connection" ×4 per pod, and `https://<app>.<domain>` loads.

**Adding an app later:** add an entry to `/apps.yaml`, then `make render`, then
`make cf-apply`, then commit and push.

### Apps outside Kubernetes behind Traefik

Traefik reaches the Jellyfin LXC and the HAOS VM via `k8s/apps/external/`
(Service + EndpointSlice, with IPs and ports from the inventory). Their requests come
from the Talos node IPs (pod traffic is SNATed when it leaves the cluster), so:

- **Home Assistant** rejects proxied requests (400 Bad Request) until you add the
  following to `configuration.yaml` (File editor / Studio Code Server add-on) and restart:
  ```yaml
  http:
    use_x_forwarded_for: true
    trusted_proxies:   # the Talos node IPs: copy from docs/addresses.md
      - 192.0.2.111
      - 192.0.2.112
      - 192.0.2.113
  ```
  Then set *Settings → System → Network → Home Assistant URL → Internet* to
  `https://homeassistant.<domain>`.
- **Jellyfin:** *Dashboard → Networking → Known proxies* = the same Talos node IPs,
  and *Published server URIs* = `all=https://jellyfin.<domain>`.

## 7. Private images: GHCR PAT and pull secret

The app images (`ghcr.io/devartech/alexandria-mcp`, `alexandria-oauth-proxy`, `skyjo`)
are **private** GHCR packages, even though this repo is public. The workflow `images.yml`
pushes them with its own `GITHUB_TOKEN`, so the build needs no secret. The cluster needs a
read-only token for pulling.

1. **Push the repo** (public) and let *Actions → images* finish once on `main`.
2. **Make sure every package is private:** *github.com → your profile → Packages →* each of
   the 3 packages. If one shows **Public**, go to *Package settings → Danger Zone → Change
   visibility → Private*. Never make them public. If a package with the same name already exists
   from somewhere else, the push fails with 403. In that case, open
   *Package settings → Manage Actions access*, add `artzima-iac` with **Write**, then re-run.
3. **The token** is root credential #5 ([OPERATOR-SETUP A2](OPERATOR-SETUP.md#a2-accounts-and-the-other-four-root-credentials)):
   *Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)*.
   - Note: `artzima`. Expiration: your choice; put a reminder before it expires.
   - Scopes: **`read:packages`** (image pulls) and **`public_repo`** (`make flux-bootstrap` pushes
     the Flux manifests and adds a read-only deploy key). No `write:packages`.
   - It must be a **classic** PAT (`ghp_…`): GHCR pulls do not take fine-grained tokens.
   - Trade-off: one token instead of two; `public_repo` can write to your public repos. If you
     prefer, keep it `read:packages` only and give `flux-bootstrap` a separate short-lived token:
     `make flux-bootstrap` then needs a small edit of its `--set GITHUB_TOKEN=` path.
4. **Store it in Infisical:** `/k8s/ghcr/GHCR_PULL_TOKEN`; `/k8s/ghcr/GHCR_USERNAME` (`devartech`)
   is generated. The ESO `ClusterExternalSecret`
   (`k8s/secrets/ghcr-pull.yaml`) builds the `kubernetes.io/dockerconfigjson` Secret
   `ghcr-pull` in every namespace labelled `artzima.io/ghcr-pull: "true"` (alexandria and
   skyjo). Nothing goes to git.
5. **Check:** `kubectl -n skyjo get secret ghcr-pull`. A wrong token shows up as
   `ErrImagePull … 401/403 Unauthorized` in `kubectl -n skyjo describe pod`.

**Rotating or expiry:** create a new token, replace `GHCR_PULL_TOKEN` in Infisical (ESO picks it up
within an hour, or force it: `kubectl -n skyjo annotate externalsecret ghcr-pull force-sync=$(date +%s) --overwrite`),
then revoke the old token. Running pods keep working on an expired token (the image is cached on
the node), but rescheduled pods on another node can't pull until the Secret is renewed.

## 8. Alexandria: secrets, library, first login, embedder

**Library: starts fresh, initialized automatically.** The old lab's memory library is not kept.
On the first start, the pod's `init-library` initContainer runs upstream's
`alexandria init /srv/alexandria` on the empty library PVC (NAS
`/volume1/Artzima/k8s/alexandria/alexandria-library`). The server needs an initialized library and
refuses to start without one. The initContainer skips `init` whenever `.alexandria/` already exists, so
later restarts never touch the library. Check it:
```bash
kubectl -n alexandria logs deploy/alexandria-mcp -c init-library   # "Initialized Alexandria library at /srv/alexandria" (first start)
```
**One-off by hand**, e.g. to start over: scale `alexandria-mcp` to 0 and empty the folder on the NAS
(the StorageClass is `Retain`, nothing deletes it for you). Then either scale back up (the
initContainer re-runs `init`), or run it yourself:
```bash
kubectl -n alexandria run alexandria-init --rm -i --restart=Never \
  --image=ghcr.io/devartech/alexandria-cli:d4834470902654e0fffaf4e4af3f5bcf2a62d780 \
  --overrides='{"spec":{"imagePullSecrets":[{"name":"ghcr-pull"}],"securityContext":{"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001},
    "containers":[{"name":"alexandria-init","image":"ghcr.io/devartech/alexandria-cli:d4834470902654e0fffaf4e4af3f5bcf2a62d780",
    "args":["init","/srv/alexandria"],"volumeMounts":[{"name":"lib","mountPath":"/srv/alexandria"}]}],
    "volumes":[{"name":"lib","persistentVolumeClaim":{"claimName":"alexandria-library"}}]}}'
```

**Secrets.** `make gen-secrets` creates `/k8s/alexandria`: `ALEXANDRIA_MCP_TOKEN` (hex:32),
`LOGIN_USERNAME` (`admin`), `LOGIN_PASSWORD` (hex:16), and `LEGACY_BEARER_TOKEN` and
`OPENAI_API_KEY` (empty). ESO syncs them into the Secret `alexandria/alexandria`.
`ALEXANDRIA_MCP_TOKEN` is the bearer that `alexandria-mcp` enforces. The proxy injects it, and
by default it is also the legacy bearer for Cursor/Codex. To keep the old lab's token instead
(saves re-configuring the clients), put it into Infisical **before** `make gen-secrets`: existing
keys are never changed.

**Don't put `alexandria` in `CLOUDFLARE_ACCESS_APPS`** (Cloudflare Access). Claude's OAuth flow and
bearer clients can't get past a browser Access login, and the proxy is the auth layer.
Only the proxy is public (`alexandria.<domain>` → Service `alexandria:8081`).
`alexandria-mcp:8080` has no route, and the proxy reaches it in-cluster.

**Check** (from anywhere):

```bash
curl https://alexandria.<domain>/health
curl https://alexandria.<domain>/.well-known/oauth-protected-resource/mcp   # "resource": "https://alexandria.<domain>/mcp"
curl -i -X POST https://alexandria.<domain>/mcp                             # 401 + WWW-Authenticate: Bearer resource_metadata=…
```

**Claude (web/Desktop custom connector), OAuth:**

1. *Settings → Connectors → Add custom connector*.
2. URL: `https://alexandria.<domain>/mcp`. Leave **Client ID / secret empty**, because Claude
   registers itself (DCR).
3. *Connect*. The proxy's login page opens. Sign in with `LOGIN_USERNAME` / `LOGIN_PASSWORD`.
4. If it hangs on "Checking connection…", delete the connector and add it again with only the URL. The OAuth
   signing keys and registered clients live on the `alexandria-oauth-data` PVC. Losing that PVC
   only means reconnecting. The old lab's registrations are not migrated, so connect again once.

**Cursor / Codex / Claude Desktop with a static bearer** (`ALLOW_LEGACY_STATIC_TOKEN=true` is
set in the HelmRelease):

- Cursor, `~/.cursor/mcp.json`:
  ```json
  { "mcpServers": { "alexandria": {
      "url": "https://alexandria.<domain>/mcp",
      "headers": { "Authorization": "Bearer <ALEXANDRIA_MCP_TOKEN or LEGACY_BEARER_TOKEN>" } } } }
  ```
- Codex, `~/.codex/config.toml` (token in your shell env `ALEXANDRIA_MCP_TOKEN`):
  ```toml
  [mcp_servers.alexandria]
  url = "https://alexandria.<domain>/mcp"
  bearer_token_env_var = "ALEXANDRIA_MCP_TOKEN"
  enabled = true
  ```

**Embedder.** The default is the in-process **fastembed** (`BGESmallENV15`). On first start
it downloads the model from Hugging Face to the `alexandria-cache` PVC (this needs outbound
internet), and the startup probe allows 10 minutes. To use an **OpenAI-compatible endpoint**
instead (Ollama, LocalAI, TEI, or OpenAI itself):

1. Edit the library's config. It's on the NAS at
   `/volume1/Artzima/k8s/alexandria/alexandria-library/.alexandria/config.toml` (the container's
   root filesystem is read-only, so edit it NAS-side as root, and keep the owner at 10001):
   ```toml
   [providers]
   embedder = "openai"

   [providers.openai]
   base_url = "http://ollama.ollama.svc.cluster.local:11434/v1"  # or a LAN host, or https://api.openai.com/v1
   embed_model = "nomic-embed-text"
   api_key_env = "OPENAI_API_KEY"   # read only if set and non-empty
   ```
2. If the endpoint needs a key, put it in Infisical `/k8s/alexandria/OPENAI_API_KEY`. An empty
   value means none, and keyless endpoints get no `Authorization` header.
3. `kubectl -n alexandria rollout restart deploy/alexandria-mcp`. When the embedder changes,
   the server **re-embeds every engram at startup**. For a big library, raise
   `probes.startup.spec.failureThreshold` for that first start. Every client shares this single
   embedder, so nothing changes on the client side.
4. Optional: with a remote embedder, the `alexandria-cache` PVC and most of the 1.5 GiB memory
   limit are unnecessary. Shrink them.

**SQLite on NFS.** `.alexandria/index.db` is SQLite on the NFS PVC. That is safe for **one**
process using NFSv4.1 locks (hard mount), which is why the deployment is 1 replica with `Recreate`.
Never run a second pod or a CLI against the same files while the server runs. The engram
files are the source of truth, and the index is a cache. Back up the library dir yourself
(for example NAS snapshots of `/volume1/Artzima/k8s/alexandria`). The PVC is not in PBS.

## 9. Skyjo: checkpoints

Nothing to do: the weights are **in the image**. DevArtech/skynet keeps them in Git LFS
(`runs/{muzero_baseline,muzero_belief}/checkpoints/checkpoint.pt`), and the image build pulls
them, fails if one is only an LFS pointer or doesn't match its oid, and adds
`checkpoint_iter_1.pt → checkpoint.pt` so the backend's default lookup (`checkpoint_iter_*.pt`)
finds them. Skyjo has no volume.

- **New weights:** commit them to skynet (LFS, same paths), bump `UPSTREAM_REF` in
  `images/skyjo/upstream.env`, push, wait for the `images` workflow, then set the new tag in
  `k8s/apps/skyjo/helmrelease.yaml`.
- **Check:** in the CI log, the build prints `checkpoint ok: … (sha256 …)` and
  `ok runs/…: checkpoint_iter_1.pt -> checkpoint.pt`. In the app, start a game against a MuZero
  agent with no explicit checkpoint path.

## 10. Media stack (arr + qBittorrent + Seerr)

What runs where ([README](../README.md#media-stack), design in
[decisions](decisions.md#media-stack-arr--qbittorrent-via-surfshark--seerr)): Prowlarr, Sonarr,
Radarr, Lidarr (music), Bazarr, qBittorrent (+ gluetun → Surfshark) and Seerr in namespace `media`
(`k8s/apps/media`), Recyclarr as a CronJob, and one idempotent Job (`media-wiring`) that wires
them together through their APIs. Only **Seerr** is public (`https://seerr.<domain>`). The
rest are on the **tailnet only** (Tailscale operator, MagicDNS names `prowlarr`, `sonarr`,
`radarr`, `lidarr`, `bazarr`, `qbittorrent`). Seerr has no music support: request music in
Lidarr directly.

### 10.1 Before the Flux sync (in this order)

> The Flux `secrets` Kustomization waits until **every** ExternalSecret is synced. A missing
> media key therefore holds up all controllers and apps, not only the media stack. Run
> `make secrets-check` before pushing.

1. **NAS media data folder** and its owner: [§3](#3-ugreen-nas-ugos).
2. **Surfshark WireGuard key** (needs an active Surfshark subscription):
   1. Log in at <https://my.surfshark.com> → **VPN** → **Manual setup** → **Desktop or mobile** →
      **WireGuard**.
   2. **I don't have a key pair** → enter a name (e.g. `artzima-gluetun`) → **Generate a new key
      pair**. Copy the **Private key** straight into Infisical
      `/k8s/vpn/WIREGUARD_PRIVATE_KEY`. (The public key stays registered at Surfshark.)
   3. Under **Locations**, pick any server and **download** its config file. Copy the IPv4
      value of its `Address =` line (e.g. `203.0.113.2/32`; the same for all Surfshark servers)
      into `/k8s/vpn/WIREGUARD_ADDRESSES`. Then **delete the downloaded file**, because it contains
      the private key.
   4. Server country (not secret): `vpn_server_countries` in `ansible/group_vars/all/media.yml`
      (gluetun's names, e.g. `Netherlands`, or `Switzerland,Sweden`), then `make render`.
3. **API keys and the qBittorrent login** in `/k8s/media`: created by `make gen-secrets` (part of
   `make bootstrap`; recipes in `secrets.manifest.yaml`). The apps **start** with these keys (`SONARR__AUTH__APIKEY`, …; seeded config files for Bazarr,
   Seerr and qBittorrent), so nothing has to be copied out of an app's UI.
4. **Jellyfin API key:** `/k8s/media/JELLYFIN_API_KEY` exists **empty** (generated), so the sync is
   not blocked. After Jellyfin's first start: *Dashboard → API Keys → +*, app name `arr-stack` →
   replace the empty value ([OPERATOR-SETUP C](OPERATOR-SETUP.md#c-after)). Sonarr, Radarr and
   Lidarr use it for their "Connect → Emby / Jellyfin" notification (library refresh on every
   import); while it is empty the wiring Job skips only that connection.
5. **Jellyfin's read-only library mount**: `make lxc-devices`. It mounts the NAS data folder
   read-only on host01 (`/mnt/artnas-data`, fstab) and adds `mp0: /mnt/artnas-data/media,mp=/data/media,ro=1`
   to the Jellyfin LXC (then reboots it). Check with `pct exec 130 -- ls /data/media` on host01.
6. **Tailscale policy**: `tag:k8s` owned by `tag:k8s-operator` ([§4](#4-tailscale-admin-console)).
7. `make secrets-check` (nothing missing).
8. Seerr's public hostname: `make cf-apply` (the `seerr` entry in `/apps.yaml`; part of
   `make bootstrap`), then commit and push (Flux syncs).

### 10.2 After the sync

```bash
kubectl -n media get pods,pvc,svc
kubectl -n media logs job/media-wiring          # the wiring log (re-created every Flux sync)
kubectl -n media create job --from=cronjob/recyclarr recyclarr-first   # TRaSH profiles now, not at 04:17
kubectl -n media logs job/recyclarr-first
```

To re-run the wiring right away: `kubectl -n media delete job media-wiring && flux reconcile kustomization apps --with-source`.
The Job waits up to 15 min for each app. Steps that can't run yet (Seerr before its wizard,
Seerr before Recyclarr has created the profiles) are **skipped** and picked up by a later run.

**Verify the VPN (do this before adding any indexer).** qBittorrent and gluetun share one
network namespace, so what gluetun sees is what qBittorrent sees:

```bash
# 1. gluetun's own report: "Public IP address is … (<country>, …)"
kubectl -n media logs deploy/qbittorrent -c gluetun | grep -i "public ip"
# 2. Ask from inside the pod: IP, country and the ASN of a Surfshark server, NOT your ISP
kubectl -n media exec deploy/qbittorrent -c gluetun -- wget -qO- https://ipinfo.io/json
# 3. Compare with the lab's real egress IP (must differ)
kubectl run egress-check --rm -i --restart=Never --image=docker.io/library/busybox:1.37 -- wget -qO- https://ipinfo.io/ip
# 4. Kill-switch: default-DROP policy and no route around the tunnel
kubectl -n media exec deploy/qbittorrent -c gluetun -- iptables -S | grep -E '^-P (INPUT|OUTPUT)'   # both DROP
kubectl -n media exec deploy/qbittorrent -c gluetun -- wget -qO- -T 5 http://192.0.2.1 || echo "LAN blocked: OK"
```

Optional leak test: in qBittorrent, add the magnet from <https://ipleak.net> ("Torrent Address
detection") and check that only the Surfshark IP shows up. To prove the kill-switch, stop the
tunnel (`kubectl -n media exec deploy/qbittorrent -c gluetun -- kill 1`). The pod goes NotReady,
qBittorrent's traffic stops, and it resumes once gluetun has reconnected.

**Port forwarding:** Surfshark has none, so qBittorrent can't accept incoming peers. Downloads
work (outgoing connections), but seeding ratios and some poorly seeded swarms suffer. That is
expected and cannot be fixed with this provider.

### 10.3 First logins

| App | URL (tailnet) | What to do |
|---|---|---|
| Sonarr, Radarr, Lidarr, Prowlarr | `http://sonarr:8989`, `http://radarr:7878`, `http://lidarr:8686`, `http://prowlarr:9696` | They ask for a UI user on first visit (Forms auth, required even on the LAN). Keep it in your password manager. The API keys are already the Infisical ones |
| Prowlarr | | *Indexers → Add*. Prowlarr pushes them to Sonarr, Radarr and Lidarr itself (apps added by the Job). If an indexer is behind Cloudflare, see FlareSolverr in `k8s/apps/media/flaresolverr.yaml` |
| qBittorrent | `http://qbittorrent:8080` | Log in with `/k8s/media/QBITTORRENT_USERNAME`/`_PASSWORD`. Save path and the categories `tv`/`movies`/`music` are set by the Job. Don't change the WebUI password in the UI: the init container resets it from Infisical on every start |
| Lidarr | `http://lidarr:8686` | Root folder `/data/media/music`, qBittorrent (category `music`), Jellyfin Connect and the recycle bin are set by the Job, which also turns **off** writing tags into audio files. **Add artists by MusicBrainz id** (search `lidarr:<artist MBID>`, the id from musicbrainz.org): text search on Lidarr's metadata service has been unreliable ([decisions](decisions.md#lidarr-metadata)). No Recyclarr/TRaSH profiles exist for Lidarr; use its built-in profiles |
| Bazarr | `http://bazarr:6767` | *Settings → General → Security*: set Forms auth. Set *Languages* (a language profile, applied to new series/movies) and *Providers*. Sonarr/Radarr are connected by the Job |
| Jellyfin | `https://jellyfin.<domain>` | *Dashboard → Libraries → Add*: **Shows** → folder `/data/media/tv`, **Movies** → `/data/media/movies`, **Music** → `/data/media/music` (the LXC's `/data/media` bind mount already includes it). "Real-time monitoring" doesn't see NFS changes; the Sonarr/Radarr Connect notifications refresh the library instead. **Re-pointing** an existing library: *Libraries → ⋯ → Manage library → Folders*, add the `/data/media/…` folder, scan, check the titles, **then** remove the old folder from the library (this only edits Jellyfin's list, no files are deleted). Existing rips: [README](../README.md#bringing-in-your-existing-rips) |
| Seerr | `https://seerr.<domain>` | Setup wizard: choose **Jellyfin**. Hostname `jellyfin.external.svc.cluster.local`, port `80`, no SSL, URL base empty, external URL `https://jellyfin.<domain>`, then sign in with the Jellyfin **admin** account (it becomes Seerr's admin). Pick the libraries. **Skip** the Sonarr/Radarr step: the Job adds both (Recyclarr's profiles, root folders `/data/media/{tv,movies}`) within one Flux interval. Then *Settings → Users → Import Jellyfin users* or allow new Jellyfin sign-ins |

### 10.4 Jellyfin LXC: uid/gid mapping (unprivileged)

The Jellyfin LXC is **unprivileged**: container uid/gid `N` is host uid/gid `100000+N`. host01
mounts the NAS folder (NFS, read-only), and Proxmox bind-mounts its `media` sub-folder into the
LXC at `/data/media` with `ro=1`. The files are owned by `1000:1000` on the NAS. Host uid 1000
is **not** mapped into the container, so inside the LXC they show as `nobody:nogroup`, and the
`jellyfin` user reads them through the **"other"** permission bits. That is why the library must
stay world-readable (664/775, which the pods' umask 002 produces). Jellyfin never needs to write
there (`ro=1`, and the NFS mount itself is read-only). Images and metadata go to its own
`/var/lib/jellyfin`.

Stricter alternative (not automated): map just the media group into the container and drop
the `o+r` requirement. On host01, add `root:1000:1` to `/etc/subgid`. Then add to
`/etc/pve/lxc/130.conf`:
```
lxc.idmap: u 0 100000 65536
lxc.idmap: g 0 100000 1000
lxc.idmap: g 1000 1000 1
lxc.idmap: g 1001 101001 64535
```
Inside the LXC, run `groupadd -g 1000 media && usermod -aG media jellyfin`, restart the CT, then
`chmod -R o-rwx` the library. Raw `lxc.*` lines are root-only and outside OpenTofu's view, the
same as the device lines.

### 10.5 OpenVPN fallback (Surfshark service credentials)

WireGuard is preferred: it is faster, uses less CPU on the mini PCs, and runs in the Talos kernel. If it
doesn't work for your account:

1. *my.surfshark.com → VPN → Manual setup → Desktop or mobile → OpenVPN → **Credentials***:
   the **service credentials** (a generated username/password, **not** your account login).
2. Put them in Infisical `/k8s/vpn/OPENVPN_USER` and `/k8s/vpn/OPENVPN_PASSWORD`, add both keys to
   `secrets.manifest.yaml` and to the `surfshark` ExternalSecret (`k8s/secrets/media/media.yaml`),
   and remove the `WIREGUARD_*` keys from both.
3. In `k8s/apps/media/qbittorrent.yaml` (gluetun env): `VPN_TYPE: openvpn`, replace the
   `WIREGUARD_*` entries with `OPENVPN_USER`/`OPENVPN_PASSWORD` secret refs, and optionally
   `OPENVPN_PROTOCOL: udp`. Everything else (firewall, health, DNS) stays the same.
4. `make lint`, commit, push.

### 10.6 Cloudflare Access for Seerr (optional)

Seerr has its own login (Jellyfin accounts), so Access isn't required. To restrict who can even
reach the login page, add `{"app":"seerr","emails":["…"]}` to `/settings/CLOUDFLARE_ACCESS_APPS`,
then `make cf-plan cf-apply`. Users then log in twice (Access, then Jellyfin), and third-party
Seerr/Overseerr mobile apps can't pass a browser Access login.

### 10.7 Day 2

- **Rotate an arr API key:** change it in Infisical, force-sync the ExternalSecret (see
  [secrets](secrets.md#day-2)), then `kubectl -n media rollout restart deploy/<app>`. The next
  wiring run updates Prowlarr, Bazarr and Seerr, and Recyclarr reads the new key on its next run.
  Exceptions: **Bazarr** reads its own key from its config file, which is only seeded on first
  start, so also set the new value in *Bazarr → Settings → General → Security → API key*.
  **Seerr** applies `API_KEY` on every start.
- **qBittorrent password:** change it in Infisical, then restart the pod. The init container re-hashes it,
  and the next wiring run updates Sonarr/Radarr.
- **Surfshark key:** generate a new pair (10.1 step 2), update Infisical, restart the qbittorrent pod,
  then delete the old key pair at Surfshark.
- **Sonarr v5:** the home-operations Sonarr image moved to v5 pre-releases. This repo pins its last v4
  build (`4.0.20.3012`). When v5 is stable, bump the tag (one-way database migration; back up
  `/config` first).
- **Backups:** the app configs are PVCs under `/volume1/Artzima/k8s/media/` (Sonarr/Radarr/Prowlarr
  also keep their own scheduled backups in `/config/Backups`). The library itself is the NAS data
  folder. Neither is in PBS, so snapshot them on the NAS.

### 10.8 Existing music (Lidarr)

`make media-adopt` handles music like movies and TV: add `{ path: /volume1/…/Music, kind: music }`
(children = artist folders) to `media_existing_paths`, dry-run, then `--apply`. It hardlinks the files into
`/data/media/music` and verifies every inode ([README](../README.md#bringing-in-your-existing-rips)).

The **import into Lidarr is manual**. Lidarr's API has no "unmapped folders" list like Sonarr/Radarr,
and it identifies music by its metadata service, whose text search is unreliable, so the Job doesn't
guess. Per artist:

1. *Library → Add New*, search **`lidarr:<MusicBrainz artist id>`** (more reliable than the name).
2. Set root folder `/data/media/music`, quality profile **Owned rip**, monitor **None**, tag
   **owned-rip**, and leave "Start search for missing albums" **off**. Lidarr then scans the artist folder
   and maps the existing files. Files it can't match stay under *Library → Unmapped Files*; match those
   with *Manual Import*, and never choose "Move".
3. The next wiring run protects every `owned-rip` artist: profile "Owned rip" (no upgrades), and
   every album that has files gets **unmonitored**. Missing albums stay monitorable. Renaming and
   writing audio tags are off globally, so the files (which share inodes with the originals) are
   never modified.

Jellyfin reads the same files at `/data/media/music` (Music library, §10.3).

<a id="11-optional-clean-up-the-old-labs-files-on-the-nas-after-verification"></a>

## 11. Initial NAS cleanup: preserve only the operator's selected folders

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

## 12. Vaultwarden

What runs (`k8s/apps/vaultwarden`, design in [decisions](decisions.md#vaultwarden)):
- **Vaultwarden 1.37.5** (official `vaultwarden/server` image), public at `https://vault.<domain>`.
- **Its own Postgres**: a CloudNativePG `Cluster vaultwarden-db`, credentials generated by CNPG.
- **Two backup layers:**
  - a nightly CNPG **volume snapshot**;
  - a nightly **encrypted** `pg_dump` plus `/data` tarball in `/volume1/Artzima/backups/vaultwarden`,
    keeping `vaultwarden_backup_keep` copies (`ansible/group_vars/all/lab.yml`, today 14).
- **The admin page** (`/admin`) answers **403 on the public host** and is only reachable over
  the tailnet at `http://vault-admin/admin`.

### 12.1 Before the Flux sync

1. **NAS folder** `/volume1/Artzima/backups/vaultwarden`, owner `1000:1000`, mode `0700`
   ([§3](#3-ugreen-nas-ugos)).
2. **Admin token, backup passphrase, SMTP** in `/k8s/vaultwarden`: created by `make gen-secrets`:
   - `ADMIN_TOKEN` = the **Argon2id PHC hash** (Vaultwarden's own preset: m=65540, t=3, p=4) of the
     generated admin password `/operator/VAULTWARDEN_ADMIN_PASSWORD`. Only the hash reaches the
     cluster; copy the plaintext into your password manager. `make check-public` refuses any
     Argon2 hash in git. To use a password of your own instead: put it into
     `/operator/VAULTWARDEN_ADMIN_PASSWORD` and delete `ADMIN_TOKEN` **before** `make gen-secrets`
     (or later: delete `ADMIN_TOKEN`, re-run `make gen-secrets`, wait for ESO, restart the pod).
   - `BACKUP_PASSPHRASE` (base64:32). **Also keep a copy outside Vaultwarden** (emergency kit).
     Without it the NAS backups can't be decrypted, and a backup you need is exactly when the vault
     itself is gone.
   - `SMTP_HOST`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD`: **empty** (optional; only non-empty
     ones reach the pod). **With SMTP** (fill them in): invites, new-device and 2FA mails work. Port
     `587` + STARTTLS is set in the HelmRelease; for port 465 change `SMTP_SECURITY` to
     `force_tls` there. **Without SMTP:** an invited user registers at the web vault with the
     invited address (see 12.2).
3. `make secrets-check` (nothing missing). `vault.<domain>` comes with `make bootstrap` (`cf-apply`).

**Do NOT put `vault` behind Cloudflare Access.** The Bitwarden apps can't pass it, and `make cf-plan`
refuses it (`access: never` in `/apps.yaml`).

### 12.2 First run

1. **Checks:**
   ```bash
   kubectl -n vaultwarden get cluster,pods,externalsecret        # Cluster "Cluster in healthy state", pods Ready
   curl -s -o /dev/null -w '%{http_code}\n' https://vault.<domain>/alive        # 200
   curl -s -o /dev/null -w '%{http_code}\n' https://vault.<domain>/admin        # 403 (blocked on the public host)
   curl -s -o /dev/null -w '%{http_code}\n' https://vault.<domain>/admin/users  # 403
   ```
2. **Admin page, over Tailscale only:** `http://vault-admin/admin` (MagicDNS name of the
   operator's proxy; your device needs ACL access to `tag:k8s`, [§4](#4-tailscale-admin-console)).
   Log in with the **plaintext** admin password.
3. **Invite yourself:** *Users → Invite User* → your email.
   - **With SMTP:** follow the link in the mail.
   - **Without SMTP:** open `https://vault.<domain>` → *Create account* with **exactly that
     address**. Invited addresses may register even though `SIGNUPS_ALLOWED=false`.
4. **Create the account:**
   - Choose the **master password**. It is the encryption key of the vault: nobody, including the
     admin page, can recover it.
   - Then *Settings → Security → Two-step login*, and enable TOTP (or a passkey/FIDO2 key).
   - Save the recovery code.
5. **Bitwarden apps** (mobile, desktop, browser extension, CLI):
   - On the login screen, pick **Self-hosted** (the region/server selector), server URL
     **`https://vault.<domain>`**, then log in.
   - CLI: `bw config server https://vault.<domain>`.
6. **Live sync:**
   - Desktop, extension and web vault get changes instantly over the websocket
     (`/notifications/hub`, through Traefik and the tunnel; nothing extra to configure). To check:
     edit an item in the web vault and watch the desktop app update.
   - Mobile apps sync on open/refresh. Mobile push notifications would need Bitwarden's push
     relay (`PUSH_ENABLED` + an installation id) and are not configured.
7. **Admin-page settings vs. this repo:** *Settings* saved in the admin page are written to
   `/data/config.json` and **override the environment** (e.g. `SIGNUPS_ALLOWED`, even the
   `ADMIN_TOKEN`). Change behaviour in `k8s/apps/vaultwarden/helmrelease.yaml` instead. If something
   was saved there by accident: *Settings → Reset defaults*, or delete `config.json` on the NAS
   (`/volume1/Artzima/k8s/vaultwarden/vaultwarden-data/config.json`) and restart the pod.

### 12.3 Backups

**1. CNPG volume snapshot**
- `ScheduledBackup vaultwarden-db-nightly` runs at 03:15, plus once right after creation.
- Each run is a CSI snapshot via `nfs-artnas-snapshot`: a tarball of the database volume on
  the NAS, under `/volume1/Artzima/k8s/`.
- It covers the **database only**.
- Check: `kubectl -n vaultwarden get backups.postgresql.cnpg.io,volumesnapshots`.
- Snapshots are `Retain` and are **not pruned automatically**. Every few months, delete old
  `Backup` objects (`kubectl -n vaultwarden delete backup <name>`), and their tarballs on the NAS
  when you no longer want them.

**2. Encrypted NAS copy**
- `CronJob vaultwarden-backup` runs at 03:45 and writes
  `vaultwarden-<UTC timestamp>-db.dump.gpg` and `…-data.tar.gpg` to
  `/volume1/Artzima/backups/vaultwarden`.
- **Encryption:** gpg AES256 with `BACKUP_PASSPHRASE`.
- **Verification:** each file is decrypted and checked before it counts.
- **Retention:** it keeps the newest `vaultwarden_backup_keep` of each kind, and deletes only its
  own older files.
- **This one is independent of CNPG.** It also covers `/data`: RSA signing keys, attachments,
  sends and `config.json`. Without the RSA keys, every client must log in again.
- Run it now:
  `kubectl -n vaultwarden create job --from=cronjob/vaultwarden-backup vw-backup-now && kubectl -n vaultwarden logs -f job/vw-backup-now`.
- Check on the NAS: `ls -l /volume1/Artzima/backups/vaultwarden`.

### 12.4 Restore

Stop Vaultwarden first, so nothing writes while you restore:
```bash
flux -n vaultwarden suspend helmrelease vaultwarden && kubectl -n vaultwarden scale deploy/vaultwarden --replicas 0
```

**A. Database from a CNPG snapshot** (newest consistent state of that night):

1. Pick the snapshot: `kubectl -n vaultwarden get volumesnapshots`.
2. In `k8s/apps/vaultwarden/database.yaml`, replace `bootstrap.initdb` with a recovery
   bootstrap. CNPG generates a new password into `vaultwarden-db-app`, and the pod reads it on start:
   ```yaml
   bootstrap:
     recovery:
       database: vaultwarden
       owner: vaultwarden
       volumeSnapshots:
         storage:
           name: <volumesnapshot name>
           kind: VolumeSnapshot
           apiGroup: snapshot.storage.k8s.io
   ```
3. Delete the old cluster: `kubectl -n vaultwarden delete cluster vaultwarden-db`. Its PVC folder
   stays on the NAS (`Retain`), as a fallback.
4. Commit and push. Flux creates the cluster from the snapshot.
5. Once it is healthy, **put `initdb` back** in git. Bootstrap only matters at creation, but a
   leftover `recovery` would restore that old snapshot again if the cluster were ever re-created.

**B. Database and/or `/data` from the encrypted NAS backup** (works even without CNPG
snapshots; the dump is a plain `pg_dump -Fc`):

1. Start a restore pod with the backups, the data volume, the passphrase and `DATABASE_URL`:
```bash
kubectl -n vaultwarden apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata: { name: vw-restore, namespace: vaultwarden }
spec:
  restartPolicy: Never
  securityContext: { runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true }
  containers:
    - name: restore
      image: docker.io/library/postgres:18.7-trixie
      command: ["sleep", "infinity"]
      env:
        - { name: HOME, value: /tmp }
        - name: DATABASE_URL
          valueFrom: { secretKeyRef: { name: vaultwarden-db-app, key: uri } }
      volumeMounts:
        - { name: backups, mountPath: /backups, readOnly: true }
        - { name: data, mountPath: /data }
        - { name: pass, mountPath: /secrets, readOnly: true }
  volumes:
    - { name: backups, persistentVolumeClaim: { claimName: vaultwarden-backups, readOnly: true } }
    - { name: data, persistentVolumeClaim: { claimName: vaultwarden-data } }
    - { name: pass, secret: { secretName: vaultwarden-backup } }
EOF
kubectl -n vaultwarden exec -it vw-restore -- bash
```
2. Inside the pod, pick the timestamp and restore the database:
```bash
ls -1 /backups                       # pick the timestamp
TS=20261201T034500Z
gpg_d() { gpg --batch --quiet --pinentry-mode loopback --passphrase-file /secrets/BACKUP_PASSPHRASE --decrypt "$1"; }
# database: replaces the current contents
gpg_d /backups/vaultwarden-$TS-db.dump.gpg | pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL"
```
3. Restore `/data` (this **empties** the current `/data` first):
```bash
find /data -mindepth 1 -delete
gpg_d /backups/vaultwarden-$TS-data.tar.gpg | tar -C /data -xf -
exit
```
4. Remove the restore pod: `kubectl -n vaultwarden delete pod vw-restore`.

**Then start Vaultwarden again:**
```bash
kubectl -n vaultwarden scale deploy/vaultwarden --replicas 1 && flux -n vaultwarden resume helmrelease vaultwarden
```
Log in from a client, and check an item with an attachment.

**Outside the cluster** (worst case): `gpg --decrypt` the two files anywhere, restore the dump
into any Postgres 18 (`pg_restore -d …`), untar `/data`, and run `vaultwarden/server:1.37.5` with
`DATABASE_URL` and `DATA_FOLDER` pointing at them.

## 13. Paperless-ngx

What runs (`k8s/apps/paperless`, design in [decisions](decisions.md#paperless-ngx)):
- **Apps:** Paperless-ngx 3.5.1 with Valkey (broker), Tika and Gotenberg (Office and e-mail
  documents), plus CNPG Postgres `paperless-db`.
- **Files:** in fixed NAS folders under `/volume1/Artzima/paperless/` (static PVs, `Retain`).
- **Backups:** a nightly `document_exporter` to `/volume1/Artzima/backups/paperless`, plus the
  off-site layers in [backups](backups.md).

**Tailnet only.** Paperless holds sensitive documents. It has **no** `/apps.yaml` entry, no
Traefik route and no tunnel hostname; the only way in is `http://paperless` on the tailnet (Tailscale
operator Service). Don't add it to `/apps.yaml`.

### 13.1 Before the Flux sync

1. NAS folders (inside the Artzima share):
   ```bash
   sudo mkdir -p /volume1/Artzima/paperless/data /volume1/Artzima/paperless/media \
                 /volume1/Artzima/paperless/consume /volume1/Artzima/backups/paperless
   sudo chown -R 1000:1000 /volume1/Artzima/paperless /volume1/Artzima/backups/paperless
   ```
2. Infisical `/k8s/paperless` (`make gen-secrets`): `SECRET_KEY` (base64:48), `ADMIN_USER`
   (`admin`), `ADMIN_PASSWORD` (base64:24; copy it into your password manager). `ADMIN_*` only
   creates the superuser on the first start; changing them later does nothing (change the
   password in Paperless instead).
3. `make secrets-check`, then commit and push.

### 13.2 First login

- `http://paperless` from a tailnet device (your ACLs must allow `tag:k8s`, [§4](#4-tailscale-admin-console)).
  Log in with `ADMIN_USER`/`ADMIN_PASSWORD`.
- **Day-to-day user:** create one for yourself (*Settings → Users & Groups*); keep `admin` for
  administration.
- **OCR language:** English (`PAPERLESS_OCR_LANGUAGE=eng`). For more languages, add
  `PAPERLESS_OCR_LANGUAGES` (installs the Tesseract packages) and set `PAPERLESS_OCR_LANGUAGE`,
  e.g. `eng+deu`.
- **Consume folder:** `/volume1/Artzima/paperless/consume` on the NAS. Files dropped there are picked up
  within 30 s (polling, because NFS has no inotify), and sub-folders are included. Paperless deletes
  each file from there after consuming it. Scanners that can write to a network share can scan straight into it.
- **Full MagicDNS name:** `paperless.<tailnet>.ts.net` is rejected (Django's host check) unless
  you add it in `k8s/apps/paperless/helmrelease.yaml`: `PAPERLESS_ALLOWED_HOSTS: paperless.<tailnet>.ts.net`
  and `PAPERLESS_CSRF_TRUSTED_ORIGINS: http://paperless.<tailnet>.ts.net`. The tailnet name is
  private; this repo doesn't use it otherwise.

### 13.3 API access for Headquarters (G.A.R.T.H.)

G.A.R.T.H. uses Paperless' REST API (`http://paperless/api/`) to upload (`POST
/api/documents/post_document/`), search (`/api/documents/?query=…`) and fetch
(`/api/documents/<id>/download/`) documents.

1. In Paperless, create a dedicated user `hq`, with only the permissions it needs (documents,
   tags, correspondents; no admin).
2. Log in as `hq` → *My Profile* → **API Auth Token** → generate.
3. Store the token in **HQ's own credential store**. It is **not** in this repo, not in Infisical and
   not in the cluster. HQ sends it as `Authorization: Token <token>`.
4. HQ must be on the tailnet (it reaches `http://paperless` like any tailnet client).

**Rotate:** regenerate the token in the same place (the old one stops working) and update HQ.
**Revoke:** delete the token or disable the `hq` user.

### 13.4 Restore

- **Database:** pick one source.
  - The **CNPG volume snapshot** (NAS), the same procedure as Vaultwarden in §12.4 A, with
    `paperless-db` / namespace `paperless`.
  - **Point-in-time recovery from B2** ([backups](backups.md#restore-runbook-postgres-point-in-time-recovery-pitr)).
- **Files:** the NAS folders are the live data. Their off-site copies are restic
  ([backups](backups.md#restore-runbook-restic-restore-of-a-pvc)).
- **Everything from the export** (the portable path, e.g. into a fresh install):
  1. Start Paperless with an **empty** database and media.
  2. Copy the export folder into `/usr/src/paperless/export` (the `paperless-export` PVC already
     is that folder).
  3. Run:
     ```bash
     kubectl -n paperless exec -it deploy/paperless -c app -- document_importer /usr/src/paperless/export
     ```
  The export holds all documents, metadata, tags, users and settings.
- **Keep the export current:**
  - `kubectl -n paperless get cronjob paperless-export` (02:30 local).
  - Run it now: `kubectl -n paperless create job --from=cronjob/paperless-export pl-export-now`.

## 14. NAS media and personal files → Backblaze (Backrest on the NAS)

The media library (`/volume1/Artzima/data/media`) and the operator's **personal shares** are backed
up by **restic, run on the NAS by Backrest in Docker** (`backup_nas_media: restic-on-nas`, the
default; role `nas_backup`). It is independent of the lab and keeps running during a wipe. The
personal shares are outside the lab: the repo never writes to them, and Backrest mounts them
**read-only**. Backrest **replaces** the UGOS backup app job, which was unreliable.

1. **Prerequisites on the NAS:**
   - the UGOS Docker app is installed;
   - SSH is enabled;
   - `devartech` is an administrator (has `sudo`);
   - `docker compose version` and `python3 --version` work over SSH.
2. **Infisical:** `make bootstrap` generates `/ansible/NAS_RESTIC_PASSWORD` (keep an offline
   copy!), `NAS_BACKREST_USER`, `NAS_BACKREST_PASSWORD_BCRYPT` (from `/operator/BACKREST_PASSWORD`,
   your UI login), `NAS_BACKUP_HEALTHCHECKS_URL` (empty), and its `b2-apply` step writes
   `NAS_B2_*` (backup key) and `NAS_B2_MAINT_*` (maintenance key). Alerts use `/k8s/alerts/WEBHOOK_URL`.
3. **List the personal shares** in `nas_personal_shares` (`backups.yml`), after `ls -la /volume1` on
   the NAS.
4. **Deploy:** `make nas-backup` (part of `make up`; asks `yes`, then the sudo password). If Ansible
   can't manage UGOS, render with `NAS_ANSIBLE_ARGS= ANSIBLE_ARGS="-e nas_backup_deploy=false"` and
   follow the one-time manual install
   ([backups: Backrest on the NAS](backups.md#backrest-on-the-nas)).
5. **Open the UI** at `http://192.0.2.156:9898` (LAN or Tailscale) and start the first runs.
   Set up healthchecks.io ([alerts](backups.md#alerts-failures-and-missed-runs)).
6. **Migrate:** let the first full upload finish, verify it (`restic check` plus test restores), and
   **only then** disable the UGOS job, and later delete its B2 data. Both are manual steps;
   the repo never does them ([backups: Migration](backups.md#migration-from-the-ugos-job)).
7. **Monthly:** [check the last successful run](backups.md#check-the-last-successful-run-monthly-and-in-the-quarterly-test).
