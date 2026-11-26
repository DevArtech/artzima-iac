# Design decisions

## Talos Linux for Kubernetes

- **Immutable, API-only OS.** It has no SSH, no package drift, and nothing to patch by hand. The
  whole node is its machine config, generated and applied by OpenTofu
  (`siderolabs/talos` provider). Rebuilding a node is cheap: destroy the VM, apply, done.
- **Small footprint** on mini PCs, compared with a full distro plus k3s or kubeadm.
- **Built-in HA endpoint.** The `Layer2VIPConfig` VIP fronts the API without
  keepalived or HAProxy. All 3 nodes are control plane + worker, which is the right shape for
  3 boxes.
- **Image Factory** bakes in the extensions we need (qemu-guest-agent,
  nfs-utils for the NFS CSI driver). The `nocloud` image takes its static IP from the Proxmox
  cloud-init drive, so a fresh VM is reachable at its final address in maintenance mode, and
  OpenTofu can push the config without DHCP guessing.
- **Trade-off:** you debug with `talosctl`, not a shell.

## Flux for GitOps

- Everything in `k8s/` is declarative and reconciled from git. Drift is
  corrected, and a rebuilt cluster converges by itself after bootstrap.
- **Secrets from Infisical via External Secrets Operator**, not from git: the repo is
  public and holds no secret material (see [below](#secrets-infisical--external-secrets-not-sops)).
  Flux orders it: `namespaces → external-secrets → secrets → controllers`.
- `postBuild.substituteFrom` lets k8s manifests use inventory-derived values
  (`cluster-vars.yaml`) without hard-coding addresses.
- Ordering with `dependsOn` (namespaces → secrets → controllers → configs → apps)
  avoids CRD races.
- **Not Argo CD:** Flux is lighter, has no UI to secure or expose, and its HelmRelease +
  dependsOn model fits a single-operator lab.

## QDevice on the PBS ZimaBoard

- The cluster has **4 PVE nodes**, an even number. Losing 2 nodes, or a 2/2 split, loses quorum and
  freezes HA and config changes.
- `corosync-qnetd` on pbs adds a **5th vote** for an odd total. pbs is
  outside the cluster, always on, and cheap. QNetd is a tiny daemon and does not need
  low latency.
- **Why not a 5th PVE node:** the ZimaBoard is too small to be a useful hypervisor and
  already has a job.
- Caveat: with an even node count, Proxmox recommends the default `ffsplit`
  algorithm (the default). Re-run `pvecm qdevice setup` after adding or removing nodes.

## GPU workloads stay outside Kubernetes

- **Jellyfin in an LXC on host01:** a GPU-enabled host provides hardware acceleration in the reference design.
  Inside k8s it would mean the NVIDIA device plugin, Talos NVIDIA extensions and
  driver/kernel lockstep, all for a workload pinned to one machine anyway. An LXC
  shares the host driver directly (`devN:` lines). That is simple, and NVENC works.
- It is backed up by vzdump to PBS like any other guest.
- **Home Assistant is not part of the lab** (removed 2026-11-23, [below](#home-assistant-leaves-the-lab-2026-11-23)).

## One reverse proxy behind one Cloudflare Tunnel

- **No open ports.** `cloudflared` only makes *outbound* connections to
  Cloudflare. Neither the Firewalla nor the Xfinity gateway has port forwards, and there is no public IP or DynDNS to
  keep in sync. CGNAT or an IP change doesn't matter.
- **One way in, one place to look.** The tunnel has exactly one origin,
  `http://traefik.traefik.svc.cluster.local:80`. Every hostname, in-cluster or not, is a
  Traefik route. Jellyfin stays outside k8s (see above) but is published
  through the same Traefik via a selector-less Service + EndpointSlice. Logging,
  metrics and routing rules are therefore uniform, and there is no second proxy on host01.
- **TLS at the edge.** Cloudflare terminates HTTPS with its certificate for `<app>.<domain>`.
  The tunnel to cloudflared is encrypted, and cloudflared → Traefik is plain HTTP on the
  pod network. Traefik does **not** redirect to HTTPS, which would loop. It trusts
  `X-Forwarded-*` only from the pod CIDR. cert-manager's internal CA stays available for
  anything that needs in-cluster TLS.
- **Cloudflare Access** can be put in front of admin-ish apps (e.g. Grafana) with no app changes, managed per app in `tofu/cloudflare` (`access_apps`).
- **Tailscale for admin, the tunnel for apps.** Proxmox UIs, SSH, `talosctl`, `kubectl` and
  PBS are never published through the tunnel. They are reached over the tailnet: hosts run
  Tailscale with `--ssh`, and two subnet routers give HA access to `lab_subnet`.
  The internet-facing surface is limited to the app list in `/apps.yaml`.
- **The tunnel is code** (`tofu/cloudflare`, official `cloudflare/cloudflare` v5
  provider): the tunnel, its ingress rules, the DNS records and Access are reviewed, diffable
  and reproducible from its configuration. The tunnel is remotely managed
  (`config_src = "cloudflare"`), so the k8s side is only a token, which `make cf-apply`
  writes into Infisical and ESO syncs into the cluster, and 2 replicas for HA.
- **One list, `/apps.yaml`.** It generates the Traefik IngressRoutes (`make render`) and feeds the
  tunnel rules, DNS and Access. Adding an app is one edit. Charts' own ingresses are
  disabled, and `make lint` fails on any other Ingress/IngressRoute, on a route or app
  mismatch, or on a route whose backend Service or port doesn't exist in the rendered charts.
  `tofu test` (mocked, offline) asserts one rule and one record per app, plus the catch-all.
- **Traefik is ClusterIP-only** by default, so nothing on the LAN can bypass Cloudflare
  (and Access). Exposing it to the LAN (a LoadBalancer via MetalLB/Cilium L2, or
  hostPorts) is an optional shortcut for local speed and offline use. The trade-off is a second,
  unauthenticated path to every app from any LAN device. If you enable it, use a
  separate entrypoint with an IP allowlist middleware.
- **Trade-offs:** traffic hairpins through Cloudflare even from home. Large Jellyfin
  streams go through Cloudflare, so check their terms for video before relying on it.
  Tailscale or the LAN option is the fallback. Cloudflare sees the plaintext at the edge.

## Public first-run pages (2026-11-24)

Jellyfin, Wizarr and Seerr each have a first-run wizard that makes **whoever completes it first**
the admin. All three are public through the tunnel, so on a fresh build or rebuild that page is
open to the internet from the moment the route works until its setup automation has run
(Jellyfin: `make jellyfin`; Wizarr: the `wizarr-setup` Job; Seerr: the `media-wiring` Job). This
was not theoretical: on 2026-11-24 an unknown client reached Jellyfin's open wizard.

- **The automation never needs the public host.** `make jellyfin` talks to `localhost:8096` in the
  LXC; the Wizarr and Seerr Jobs use the in-cluster Services. So the first-run paths can be closed
  to the internet for good without hurting the setup.
- **Rule: `setup_paths` in `/apps.yaml`.** `make render` adds a priority route per app:
  `Host && PathRegexp(setup prefixes) && (HeaderRegexp(Cf-Connecting-Ip) || HeaderRegexp(Cf-Ray))`
  → a Traefik `ipAllowList` that matches nobody → **403**. Blocked from the internet:

  | App | Prefixes | Why |
  |---|---|---|
  | Jellyfin | `/Startup` | the whole wizard API (`StartupController`). The web wizard is a `#/wizard…` route, never sent to the server. |
  | Seerr | `/setup`, `/api/v1/settings/initialize`, `/api/v1/auth/plex` | the setup page, the "wizard done" call, and the Plex sign-in, which on a fresh Seerr makes **any** Plex account admin (we never use Plex). |
  | Wizarr | `/setup` | creates the admin; invites (`/j/<code>`) stay public. |

- **How "came through the tunnel" is decided.** Traefik is ClusterIP-only: its clients are
  cloudflared and other pods (LAN and tailnet reach the apps directly, not through Traefik), and
  cloudflared's source is an ordinary pod IP. What only tunnel traffic carries is Cloudflare's
  headers: the edge sets `Cf-Connecting-Ip` and `Cf-Ray` on every request it proxies and
  overwrites what a client sent, so an internet client can't remove them. The rule only ever
  **blocks** on a header; nothing is allowed because of one. A pod that sends the header (even
  empty) blocks itself, which is the safe direction.
- **Match details.** Prefixes are plain paths turned into `(?i)^/+seg/+seg…`: Jellyfin
  (ASP.NET) and Seerr (Express) route case-insensitively, `//Startup` reaches the same handler,
  and Traefik decodes unreserved `%XX` (`/%53tartup`) before matching. Prefix match, so it can
  over-block a path that merely starts the same way, never under-block.
- **Seerr's residual window.** The first admin can also come from `POST /api/v1/auth/jellyfin`,
  which is the normal login and can't be denied. On a Seerr that has no Jellyfin server yet it
  accepts a `hostname`, so until the `media-wiring` Job has set our Jellyfin server, a stranger
  could sign in against their own Jellyfin and become admin. Close it on a rebuild by applying the
  Cloudflare route (`make cf-apply`) after the first green `media-wiring`, or seed
  `jellyfin.ip` in `wiring/seed.py` before Seerr's first start (not done yet). After the wiring,
  Seerr refuses a `hostname` and Plex sign-in is disabled.

## The lab network: behind a Firewalla, no VLANs (2026-11-23)

- **Decision:** a Firewalla Purple SE in **Router mode** sits between the Xfinity gateway (WAN
  side, the home network) and the lab's own switch (LAN side). The whole lab **and the NAS** sit on
  their own subnet behind it (`lab_subnet`, gateway and first DNS = the Firewalla), untagged
  (`lab_vlan_id: null`). Every machine kept its host offset (`host_index`), so only
  `network.yml` changed.
- **Why no VLANs:** the lab already has its own physical switch (the unmanaged UGREEN 2.5G), and
  nothing else is plugged into it. Physical separation plus a router between the two networks gives
  the same result as a tagged VLAN: a separate broadcast domain, a separate subnet, and a firewall
  between the lab and the home devices. It needs no managed switch, no trunk ports and no tagged
  host bridges. The earlier VLAN plan needed both a managed switch and a lab router; the router
  alone is enough when the cabling already separates the two networks.
- **What the Firewalla gives:** the home network can't open connections into the lab (NAT; the
  Xfinity can't hold a static route to it anyway). The lab's outbound traffic can be watched and,
  later, restricted. Lab DHCP and DNS are under the operator's control, independent of the
  Xfinity, so a Pi-hole for the lab later is a single Firewalla DNS setting.
- **Cost:** home devices (phones, the TV) reach the NAS and lab services only over Tailscale, via
  the two subnet routers, which already exist for remote access ([manual steps §4a](manual-steps.md#4a-reaching-the-lab-from-the-home-network)).
  The NAS moved with the lab, so every home client that used it needs Tailscale. Public apps
  are unchanged: Cloudflare Tunnel and Tailscale only make outbound connections, so there are still no
  port forwards (now double NAT, which both handle).
- **When VLANs come back:** only if a managed switch is added and one cable has to carry several
  networks (for example IoT devices on the same switch). The Firewalla can then serve the VLANs;
  the repo already supports `lab_vlan_id` ([manual steps §5](manual-steps.md#5-later-vlans-only-with-a-managed-switch)).
- **Alternatives:** a lab VLAN on a TL-SG108E plus a separate router (more hardware, same
  result here); leaving the lab on the Xfinity LAN (no separation, no control over DHCP/DNS); a
  static route on the Xfinity (not possible).

## Home Assistant leaves the lab (2026-11-23)

- **Decision (the operator):** Home Assistant is no longer part of this lab. The HAOS VM, its image
  staging (the Ansible `guest_images` role and the `import` storage content), its public route and
  `/apps.yaml` entry, its Traefik external Service, its off-site backups (the NAS folder and its
  VolSync/restic repository) and its Firewalla reservation are removed. Nothing else depended on it.
- **Consequence:** Jellyfin is the only app outside Kubernetes; every lab guest has a static
  address, so the Firewalla needs no DHCP reservations.
- **Not migrated:** no Home Assistant backup is saved before the wipe (the fresh-lab decision,
  [journal](JOURNAL.md)). If Home Assistant returns, it is a separate project.

## Infisical: two projects (2026-11-23)

- **Decision (the operator):** the secrets are split into two Infisical projects. The **main**
  project keeps the operator, Ansible and OpenTofu secrets (`/settings`, `/bare-metal`, `/ansible`,
  `/tofu`, `/operator`). A new **k8s** project holds only what the cluster reads (`/k8s/**`, plus
  `/k8s/settings/PUBLIC_DOMAIN`, a copy of `/settings/PUBLIC_DOMAIN`). The ESO identity has the
  built-in **Viewer** role on the k8s project and no access to the main project.
- **Why:** the first design gave ESO a custom project role limited to `/k8s/**` and
  `/settings/PUBLIC_DOMAIN`. Infisical's free plan refuses it (confirmed through the API:
  "Failed to create custom role due to plan RBAC restriction. Upgrade to Infisical Enterprise
  plan"; `make ids-apply` failed at that resource). The only built-in read-only role, Viewer, reads
  a whole project. In a single project that would expose the root credentials, the NAS restic
  password and the state passphrase to a compromised cluster. A second project draws the same line
  with a free feature: project membership.
- **How:** one rule (`projects:` in `secrets.manifest.yaml`: `/k8s/**` = k8s project) used by every
  tool (`scripts/infisical_projects.py`), checked by `make lint` for OpenTofu and Ansible. Writers
  of `/k8s` keys write into the k8s project (`gen-secrets`, `tofu/backblaze`, `tofu/cloudflare`,
  the Proxmox exporter token); the ClusterSecretStore names only the k8s project. A one-time,
  verified `make secrets-migrate-k8s` moved the secrets that already existed.
- **Costs:** a second project to create and to back up in the emergency kit, and one mirrored
  value (`PUBLIC_DOMAIN`) that `make secrets-check` keeps equal. Inside the k8s project there is no
  per-app isolation (Viewer reads all of it), the same as the custom role would have allowed.
- **Alternatives:** a paid plan for custom roles (cost, for one boundary); Viewer on a single project
  (exposes everything); the Infisical Kubernetes operator with per-namespace service tokens (more
  moving parts, a second sync path).

## Other choices

- **`pvecm` over SSH, not `community.proxmox.proxmox_cluster`:** the module's join
  needs the peer's root password over the API. Root here is SSH-key-only (the
  installer gets a random, discarded password hash). `pvecm add --use_ssh` is
  idempotent via `creates: /etc/pve/corosync.conf`.
- **LXC `devN:` and `mpN:` lines via Ansible:** Proxmox only lets `root@pam` set raw device
  passthrough and bind mounts, and API tokens (OpenTofu) can't. OpenTofu creates the containers and
  ignores `device_passthrough` and `mount_point`. `make lxc-devices` adds the lines from the
  inventory (`lxc_devices`, `lxc_mounts`).
- **Static IPs, DHCP pool outside them:** the router (now the Firewalla) is configured by
  hand, not by code. Static config keeps the lab independent of it. The Firewalla's DHCP pool
  (`lab_dhcp_pool`) sits outside every static address, which `make render` checks, so no
  reservations are needed (every guest has a static address).
- **Postgres via CNPG on NFS** is a compromise. NFS is the only shared storage. Backups
  are CSI volume snapshots on the same NAS. Add barman-cloud plus S3 for real off-box backups.
- **OpenTofu state** is local, gitignored, and **encrypted** (native state
  encryption, passphrase from Infisical), because it holds Talos secrets and the kubeconfig.

## Private app images on GHCR

- **The operator wants the images private.** `alexandria` and `skyjo` are personal apps, and
  their images (code, dependencies, the built frontend) should not be pullable by anyone else.
- **Private GHCR packages built by this (public) repo's Actions** are the cheapest way to get
  that with no new infrastructure:
  - The build runs in GitHub Actions (`images.yml`) with the job's own `GITHUB_TOKEN`
    (`packages: write`). There is no long-lived push credential anywhere, and the lab doesn't build anything.
  - The workflow's `GITHUB_TOKEN` gets `packages: write` only in the build job, only on
    `main`, and the workflow has no `pull_request` trigger, so forks can't publish.
  - The cluster pulls with a **classic PAT scoped to `read:packages` only** (GHCR does not take
    fine-grained tokens for pulls). It is stored like every other secret, in Infisical
    (`/k8s/ghcr`), and an ESO `ClusterExternalSecret` templates it into a `dockerconfigjson`
    in every namespace labelled `artzima.io/ghcr-pull=true`.
  - The repo itself is **public** but the packages are not. Visibility is per package, and
    each one must show *Private* (check after the first run).
  - The app repos stay untouched. Alexandria is built from its **own** Dockerfiles via a pinned git
    context, and Skyjo is cloned at a pinned SHA by our Dockerfile. Tags are the upstream SHA, so
    what runs is traceable to an exact commit.
- **Trade-offs:** the build depends on GitHub (Actions, GHCR) and the pull on the internet
  plus one PAT that expires. A pod rescheduled onto a node without the cached image can't start
  while GitHub is down or the PAT has lapsed. The weekly rebuild overwrites the `<sha>` tag with a
  fresh digest. Pin digests if you need byte-identical deploys.
- **Skyjo's weights are part of the image, from Git LFS.** skynet keeps the two MuZero
  checkpoints in Git LFS. The build pulls exactly those two objects, verifies them against
  their pointers' sha256 oids (failing on a bare ~134-byte pointer), and links
  `checkpoint_iter_1.pt → checkpoint.pt` for the backend's default lookup, without changing
  upstream.
  - **Why it's in the image:** the app is stateless, a pod can move freely, and "which weights
    run" is the image tag. Before, they were an untracked, hand-seeded volume.
  - **Cost:** a ~0.4 GB bigger image, and **LFS bandwidth**. Each uncached build downloads
    ~0.37 GiB from LFS, billed to the **repository owner** (DevArtech), not to this repo.
  - **Monthly budget:** GitHub's current free allowance is 10 GiB bandwidth/month for personal
    accounts. A weekly rebuild (≈ 4-5 builds, ≈ 1.6-1.9 GiB) plus a few pin bumps fits easily.
    Uploads don't count.
  - **Older accounts:** some are still on the legacy 1 GiB/month LFS quota, where a weekly
    rebuild would exceed it (LFS downloads are then blocked until the next month with a $0
    budget). Check *Settings → Billing → Git LFS*. If so, drop the weekly schedule for `skyjo`,
    or cache the LFS objects.
  - **The image cache doesn't help:** the source stage is invalidated whenever the base image
    changes, which is the point of the weekly run, so in practice every build is uncached.
- **`alexandria-cli` is upstream's release binary, not a source build.** The server image has no
  CLI, but the library must be `alexandria init`-ed once.
  - **Why the release asset:** upstream publishes exactly that binary per release. The image
    downloads it, pinned by **sha256** (equal to the digest GitHub shows for the asset). CI
    refuses to build unless the release tag is the same commit as the server's pin, so CLI and
    server always match.
  - **Why not compile it:** that would double the Rust build time for a one-line init.
  - **Base image:** Debian trixie, because the binary needs glibc ≥ 2.39.
- **Fully internal alternative (possible later step, not built):** an in-cluster OCI
  registry such as **Zot** (single binary, OCI-native, NFS-backed storage, htpasswd/OIDC auth,
  optional pull-through cache for Docker Hub/GHCR), plus **in-cluster builds** (BuildKit
  rootless as a Job, or Kaniko, triggered by a Flux/Tekton/Argo Workflows pipeline, or by a
  self-hosted Actions runner in the cluster). That removes the GitHub dependency for pulls, the PAT
  and any egress for images. The costs are another stateful service to run, back up and
  secure, a registry TLS/trust setup for containerd on Talos (`machine.registries` mirrors/config),
  and build CPU on the mini PCs (the Rust build is heavy). Worth it if the lab should keep
  working with no internet, or once there are many private images. With a public repo it
  also keeps even the image *names* off GitHub. See also the self-hosted Infisical note in
  the next section.

## Secrets: Infisical + External Secrets, not SOPS

- **The repo is public, so it holds no secret material at all, not even encrypted.**
  SOPS files are safe only as long as the age key is, and as long as the ciphertext's
  algorithms hold. In a public repo they are published forever, together with
  metadata (which secrets exist, when they changed). With an external store, the repo
  holds only *names*: an Infisical folder and key per secret ([secrets](secrets.md)).
  Personal data (domain, email addresses, SSH public keys, Cloudflare IDs) moves out the
  same way, to `/settings`.
- **Infisical** (free Infisical Cloud now, self-hostable later by changing one URL in
  `ansible/group_vars/all/infisical.yml`) has first-party integrations for every
  consumer here: an OpenTofu provider with **ephemeral** resources and **write-only**
  attributes (secrets stay out of state where the consuming provider allows it), an
  Ansible collection (`infisical.vault`), a Python SDK, and an ESO provider.
- **External Secrets Operator** in the cluster, not the Infisical operator: ESO is the
  vendor-neutral standard (one `ClusterSecretStore` to swap if the backend ever changes),
  it templates (`dockerconfigjson`, `pve.yml`), and its CRDs are validated offline by
  `make lint` against the pinned chart.
- **One imperative bootstrap secret:** ESO's own Infisical credentials
  (`make eso-bootstrap`), once per cluster. Everything else converges from git + Infisical.
- **Two machine identities:** an admin identity (workstation: Ansible, OpenTofu) and a
  read-only one for ESO. A compromised cluster can't write secrets, and since 2026-11-23 it can't
  read the operator's secrets either ([two projects](#infisical-two-projects-2026-11-23)).
- **Trade-offs:**
  - Applying anything now needs Infisical reachable, which also means internet while on
    Infisical Cloud.
  - A secret change is no longer a reviewed git diff (Infisical keeps its own versions
    and audit log instead).
  - The admin identity (`~/.config/artzima/infisical.env`) is a single high-value credential.
  - OpenTofu state still holds what the providers force into it (Talos CA, Cloudflare
    tunnel secret), so state stays encrypted and gitignored.
- **Guards:** `make check-public`, a gitleaks pre-commit hook and a CI job
  (`security.yml`) fail on any token pattern, password hash, personal email or SOPS leftover.
- **Fully internal later:** together with the Zot registry above, a self-hosted Infisical in
  the cluster (or on the NAS) would remove the last internet dependency for secrets and
  images. The catch is bootstrapping: the cluster would depend on a secrets store that it
  hosts itself. So it is best on a separate small host (e.g. the PBS ZimaBoard or an LXC), with
  its own backup. That is a possible later step, not built.

## Media stack: arr + qBittorrent via Surfshark + Seerr

Prowlarr, Sonarr, Radarr, Bazarr, qBittorrent (+ gluetun) and Seerr run in Kubernetes
(`k8s/apps/media`, bjw-s app-template). Jellyfin stays in its GPU LXC (see above) and reads the
same files. Steps: [manual steps §10](manual-steps.md#10-media-stack-arr--qbittorrent--seerr).

- **One shared volume, TRaSH "data" layout.** Downloads (`/data/torrents/{tv,movies,music}`) and the
  library (`/data/media/{tv,movies,music}`) live in **one** NAS folder (`/volume1/Artzima/data`,
  `media_data_subdir`), mounted once at `/data` in qBittorrent, Sonarr, Radarr and Bazarr.
  Same filesystem, same paths everywhere, so imports are instant **hardlinks** (seeding and the
  library share the bytes) or atomic moves, and no app needs remote path mappings. It is a
  **static** NFS PV (`Retain`, pre-bound to `media/media-data`), not the `nfs-artnas`
  StorageClass: the library is a fixed NAS folder that must outlive the cluster and must not
  move under `k8s/<ns>/<pvc>`. Per-app config stays on separate dynamic RWO PVCs.
- **One uid/gid, umask 002.** Every media pod runs as `media_puid:media_pgid` (1000:1000).
  The NAS folder root is `1000:1000 2775`, so kubelet's `fsGroup` handling
  (`OnRootMismatch`) never walks the library. Files stay world-readable, which is all the
  unprivileged Jellyfin LXC needs: it reads as "other" through a read-only bind mount
  ([§10.4](manual-steps.md#104-jellyfin-lxc-uidgid-mapping-unprivileged); an idmap is the documented
  stricter option).
- **Same paths in Jellyfin.** The LXC sees the library at `/data/media` too (`mp0`, `ro=1`). That's why
  Sonarr/Radarr's Jellyfin "Connect" refresh needs no path mapping. The bind mount is set by
  Ansible (`make lxc-devices`, `lxc_mounts` in `hosts.yml`) because, like `devN:`, only `root@pam`
  may set bind mounts; OpenTofu ignores `mount_point`. host01 mounts the NAS folder read-only
  (`hard`), and `pve-guests` waits for remote filesystems. Bind-mounting a *sub*-folder means a
  missing NFS mount makes the CT fail to start instead of showing Jellyfin an empty library.
- **Transcodes on the NAS too** (2026-11-25, after leftover segments filled the LXC's 32 GB root
  disk). `data/transcodes` is a separate **read-write** NFS mount on host01 (the library mount stays
  read-only), bind-mounted at `/data/transcodes` (`rw: true` in `lxc_mounts`). Because a failed
  `nofail` mount would leave an empty local directory to bind, the folder carries a marker file and
  Jellyfin refuses to start without it, rather than fall back to a local disk. Cleared by Jellyfin's
  segment deletion + throttling and a 15-minute timer (files > 60 min). NFS write latency doesn't
  matter at these rates; a NAS outage stalls playback (hard mount) instead of breaking the database.
  [§10.4](manual-steps.md#104-jellyfin-lxc-uidgid-mapping-unprivileged).
- **Images: home-operations** (`ghcr.io/home-operations/*`) for the arr apps and qBittorrent.
  They are rootless and run as any uid with a read-only root filesystem, without s6 or
  `PUID`/`PGID` root init. LSIO images need root at start. Sonarr is pinned to that image's
  **last v4 build** because the image now follows v5 pre-releases.
- **Usenet (SABnzbd) without the VPN.** Only BitTorrent needs gluetun: a swarm sees every peer's IP
  and every client uploads. Usenet is a plain client → provider download over TLS (port 563, strict
  certificate check): no peers, no uploads, and the provider's TLS hides the content from the ISP.
  SABnzbd downloads into `/data/usenet/{incomplete,complete/<category>}` on the same `/data` volume, so
  the arrs' imports are atomic moves. It is wired over its API by the same Job (server, categories,
  login); NZBGeek is a Prowlarr Newznab indexer synced to the apps like the others.
- **Malware protection: block file types, then scan before import.** Fake releases carry
  executables, shortcuts or disc images, not infected video. Layer 1 (wire.py `DANGEROUS_EXT`):
  SABnzbd aborts a job with such a file (also inside its RARs) or with a password-protected RAR;
  qBittorrent's *excluded file names* never downloads them. Subtitles, nfo and media types are
  not on the list; `iso`/`img` are (the TRaSH profiles reject disc images anyway). Layer 2: a
  ClamAV clamd (official image, signatures in an emptyDir seeded from the image, freshclam every
  6 h) and a SABnzbd post-processing script that streams every file of a finished job to clamd
  and **fails closed** (`script_can_fail`: infected, unreadable or clamd unreachable = job
  Failed, so the arrs never import it). Infected jobs are renamed into `/data/usenet/quarantine`,
  never deleted. Video/audio files with a matching container header send only their first
  32 MiB (a real MKV cannot run; streaming whole remuxes over NFS would take minutes); anything
  else up to 512 MiB. **No ClamAV hook for qBittorrent:** its *run on torrent finished* program
  runs asynchronously, while Sonarr/Radarr import as soon as the torrent is complete, so it
  cannot hold an import (not fail closed); and the pod's DNS is gluetun's (no cluster names).
  Torrents rely on layer 1.
- **qBittorrent only through Surfshark: gluetun as a sidecar.** One pod, one network
  namespace. gluetun owns the namespace's routes and iptables, and qBittorrent has no other way out.
  - **Kill-switch:** gluetun's firewall (default DROP) allows only the tunnel, the Talos pod and
    service CIDRs (`FIREWALL_OUTBOUND_SUBNETS`, rendered from `network.yml`, which also feeds the
    Talos config, so it can't drift), and the WebUI/health input ports. The LAN is not allowed.
  - **Ordering:** gluetun is a native sidecar (`restartPolicy: Always` init container). Its
    startupProbe (healthy tunnel) gates qBittorrent's start, and qBittorrent's readiness *is*
    gluetun's health, so Sonarr/Radarr stop seeing it while the VPN is down.
  - **Rules survive restarts:** gluetun doesn't remove its rules on exit, and they live in the
    pod's netns, so they survive a sidecar restart.
  - **Second guard:** gluetun falls back to ACCEPT only if *enabling* the firewall fails. For that case,
    qBittorrent is also **bound to `tun0`** (seeded config + API), so no tunnel means no BitTorrent
    traffic.
  - **DNS:** goes to gluetun's resolver on 127.0.0.1, through the tunnel.
  - **Talos:** `tun` and WireGuard are built into the kernel; `/dev/net/tun` is a hostPath char
    device. `NET_ADMIN` + hostPath need PSA `privileged` on the `media` namespace. That is the
    one exception: every other media container is non-root with all capabilities dropped.
  - **Why not a pod-gateway/VPN router for the whole namespace:** more moving parts (a
    gateway pod, a mutating webhook, VXLAN) for one client.
  - **WireGuard over OpenVPN:** lower CPU on the mini PCs, in-kernel. OpenVPN with
    Surfshark service credentials is the documented fallback.
  - **Trade-off:** **Surfshark has no port forwarding**, so there are no incoming peer
    connections: seeding and thin swarms suffer. A provider with forwarding (gluetun supports
    PIA, ProtonVPN, …) would fix it, at the cost of changing providers.
- **Exposure.** Only **Seerr** is public (`/apps.yaml` → Traefik → tunnel). It has its own
  login (Jellyfin accounts), and Cloudflare Access is an optional extra. The arr apps, Bazarr and
  qBittorrent are admin tools that hold indexer credentials and the download client. They are
  **tailnet-only** via the Tailscale operator's Service annotation (`tailscale.com/expose`): one
  tailnet device per app, reachable by MagicDNS name, and governed by the tailnet ACLs. This was
  chosen over a second, "internal" Traefik entrypoint because Traefik is deliberately
  ClusterIP-only. An internal entrypoint would need a LAN/tailnet-reachable LoadBalancer
  plus allowlists, and `make lint`'s "every route is in /apps.yaml" rule would get an exception.
  The operator approach adds no route and no LAN listener.
- **Auth:** arr apps use Forms auth, **required** even for "local" addresses, because the
  Tailscale proxy reaches them from the pod network. qBittorrent's image default "bypass auth
  for private subnets" is turned off for the same reason.
- **Wiring as code: pre-seeded keys + one idempotent Job, not Buildarr or Configarr.**
  - **Pre-seeded keys:** every app's API key is chosen in Infisical and given to the app at start
    (`*__AUTH__APIKEY` env for Sonarr/Radarr/Prowlarr; `API_KEY` + a first-start
    `settings.json` for Seerr; a first-start `config.yaml` for Bazarr; a PBKDF2 hash written into
    `qBittorrent.conf` by an init container). No key ever has to be copied out of a UI.
  - **The Job** (`media-wiring`, Python stdlib in a pinned `python` image, script in git) re-runs
    on every Flux sync (TTL-deleted, re-created by the Kustomization). Each step reads the current
    state and only changes what differs, starting from each API's own `/schema` so it doesn't
    hard-code every field. Steps fail or skip independently.
  - **What it wires:** the TRaSH folders, qBittorrent (save path, categories `tv`/`movies`, `tun0`
    binding), Sonarr/Radarr (root folders, hardlinks, qBittorrent download client, Jellyfin
    Connect), Prowlarr (Sonarr/Radarr apps, optional FlareSolverr proxy), Bazarr (Sonarr/Radarr)
    and Seerr (its whole setup wizard: first sign-in with the Jellyfin admin login, Jellyfin
    server and libraries, application URL, initialized; then Sonarr/Radarr).
  - **Buildarr** (last stable release 2023-11, last commit 2024-05) and **Flemmarr** (last commit
    2024-01) are unmaintained.
  - **Configarr** (raydak-labs, actively released, v1.39 in 2026-11) is maintained. It covers
    quality profiles/custom formats, root folders, download clients and Prowlarr apps, and
    notifications are experimental. It does *not* cover qBittorrent itself, Bazarr or Seerr,
    so a Job would still be needed. It is not clearly better: it would split the wiring across
    two tools and replace Recyclarr, which the TRaSH guide points to.
  - **Recyclarr** stays the TRaSH tool: a daily CronJob with its v8 config in git
    (`WEB-1080p`, `HD Bluray + WEB`). v8 dropped include templates, so the profiles are
    referenced by TRaSH `trash_id`.
  - **Revisit Configarr** if the Job grows, or if one tool for profiles and wiring becomes
    attractive.
- **arr apps on Postgres (CNPG `media/arr-db`), not SQLite on NFS.** Sonarr, Radarr, Lidarr and
  Prowlarr started on their default SQLite (WAL mode) on the `nfs-artnas` config PVCs. Those are
  NFSv3 mounts, so every SQLite lock is an NLM round trip to the NAS, and lock waits came in steps
  of ~30 s (the kernel lockd client's blocked-lock poll). Proven 2026-11-24 from the apps' own
  request logs: `GET config/naming` 61 s, `PUT downloadclient` 92 s, provider init at startup
  30-60 s; `/ping` timed out, and the liveness probe restarted each app every few minutes
  (Sonarr 24 times in 3 h), so the wiring Job's calls timed out. They now use one CNPG cluster
  (`k8s/apps/media/database.yaml`, a database per app, role `arr`, `*__POSTGRES__*` env) with the
  same backups and monthly restore self-check as the other CNPG databases. The config PVC keeps
  `config.xml`, logs and covers; the old `*.db` files there are no longer read.
  - **Bazarr stays on SQLite:** no stalls seen (it writes little), and its Postgres support is a
    separate code path. Move it the same way if it ever shows them.
  - **The switch started the apps empty** (they held only what the wiring Job and Recyclarr
    create, plus the adopted rips, which the Job re-adopts). Seerr's Sonarr/Radarr entries keep
    working: the Job re-points a quality-profile id the arr no longer has to the profile of the
    same name.
- **Seerr on Postgres (CNPG `media/seerr-db`), not SQLite.** Seerr's SQLite runs in WAL mode (a
  `-shm` shared-memory file), which is a corruption risk on the NFSv3 `nfs-artnas` volume, and its
  requests and users deserve real database backups. Seerr therefore uses its own CNPG cluster
  (`k8s/apps/media/database.yaml`), like Vaultwarden and Paperless, with the same backups and
  monthly restore self-check. The config PVC keeps only `settings.json` and logs.
  - **First-start pitfall:** Seerr 3.5 reads `settings.json` raw, and a file without
    `main.mediaServerType` sends it into the Overseerr→Seerr merge. On a brand-new database
    that merge exits with `Failed to insert migration records … migrations` (SQLite or
    Postgres alike). The seed-config init container therefore writes `mediaServerType: 4`
    (Seerr's own "not configured" default) next to the API key (`k8s/apps/media/wiring/seed.py`).
- **FlareSolverr** is only needed for some Cloudflare-protected public indexers. It ships
  suspended (rendered and linted, not deployed), and enabling it is two edits.
- **Existing rips: hardlinks, then "owned" titles.** The operator's existing rips must
  never be lost ([README](../README.md#bringing-in-your-existing-rips)).
  - **`make media-adopt` hardlinks, never moves.** Each file gets a second name in
    `/data/media/{movies,tv}` (same inode, no extra space). The original path stays as the
    untouched safety net: whatever an app does to its name, the original name keeps the bytes.
  - **The script only works where that's possible.** It runs on the NAS, compares `st_dev` and
    refuses across filesystems. UGOS shares can be separate btrfs subvolumes. The data folder is
    never moved out of `/volume1/Artzima` to follow the rips: a verified copy, or the operator
    moving the rips, are the options.
  - **It is safe to re-run.** It skips existing links and reports conflicts instead of overwriting.
  - **It proves the result.** It counts and sizes both sides and checks inode equality for every file.
  - **Why not `cp -al` or `rsync --link-dest`:** both would also link, but a per-file `ln` with
    explicit checks gives exact conflict and verification reporting.
  - **Why the arrs must never chown or chmod adopted files:** they share the original's inode.
    That is why `setPermissionsLinux` stays off and the NAS root is pre-set so kubelet's `fsGroup`
    pass (`OnRootMismatch`) never walks the library.
- **How adopted titles are protected in Sonarr/Radarr.** All of these are applied by the wiring
  Job and re-enforced on every run for every title tagged `owned-rip`:
  1. **No renaming:** "Rename Movies/Episodes" is off.
  2. **A quality profile "Owned rip"** that allows every quality with **`upgradeAllowed: false`**,
     created from the API's own `/qualityprofile/schema`.
  3. **Unmonitored:** the movie, or every Sonarr episode that has a file.
  4. **Propers/repacks** are set to "Do not prefer" (also the TRaSH recommendation).
  5. **A recycle bin** on the same volume (`/data/.recycle/{sonarr,radarr}`, `media_recycle_days`).

  **Why both a profile and unmonitoring:** both are plain, long-standing fields of the v3 API
  (`qualityProfileId`/`upgradeAllowed` and `monitored` via the `editor` and `episode/monitor`
  endpoints), so both are reliable, and they cover different paths. Unmonitored items are never
  searched or grabbed from RSS. The no-upgrade profile makes the import step reject anything that
  would *replace* an existing file, even if something gets monitored again, e.g. a Seerr request
  for a missing season (the next run re-freezes the episodes that have files).

  **Auto-import is limited to unambiguous matches** (an id in the folder name, or an exact title
  + year). Everything else is listed for a manual Library Import, because a wrong match is worse
  than a manual step. Removing the tag releases a title.

## The NAS outlives the lab

- **Storage is independent of compute.** The NAS holds persistent app data, media and
  personal backup sources. Replacing compute hosts does not require replacing storage.
- **Writes stay inside the configured export.** `scripts/labdata.py` rejects paths that
  escape it, and manifest validation checks NFS storage paths. Media adoption verifies
  the actual NAS paths before writing library links.
- **Personal sources are read-only.** Media adoption reads the configured rip sources;
  Backrest mounts personal shares read-only. A source on another filesystem requires
  a verified copy or a separately reviewed move before hardlink adoption.
- **Persistent volumes use Retain.** Removing a PVC or namespace leaves its data on the
  NAS. Deterministic backing paths allow recovery, but also require inspection before
  reusing an existing volume for an application.
- **Deletion stays within documented app behaviour.** Vaultwarden rotates its own
  encrypted dumps; Paperless consumes import files and mirrors document deletions
  into its export folder. Other volume retirement requires an independent backup,
  a test restore and review of the exact backing directory. See
  [NAS volume lifecycle](manual-steps.md#11-nas-volume-lifecycle).

## Vaultwarden

- **Public, but never behind Cloudflare Access.** The Bitwarden mobile, desktop and extension
  clients talk to the server directly and can't complete a browser Access login. So
  `vault.<domain>` is a normal `/apps.yaml` entry with **`access: never`**, which
  `tofu/cloudflare` enforces: the plan fails if Access is configured for it (covered by `tofu test`).
  What protects it instead:
  - end-to-end encryption: the server only stores ciphertext;
  - closed signups, invitations only;
  - no password hints;
  - users' own 2FA.
- **The admin page is not on the internet.**
  - **Public host:** `/apps.yaml` `deny_paths: [/admin]` makes `make render` generate a
    higher-priority route for `Host(vault…) && PathPrefix(/admin)` with a Traefik `ipAllowList`
    that matches nobody, so every request gets **403**. Traefik sanitises paths (`//admin`,
    `/./admin`) before matching.
  - **Tailnet:** the same pod gets a second Service, `vaultwarden-admin`, exposed **only on the
    tailnet** by the Tailscale operator (`http://vault-admin/admin`). That is plain HTTP inside
    WireGuard. Vaultwarden marks its admin cookie `Secure` only when `X-Forwarded-Proto: https`,
    and its admin pages use relative URLs (`DOMAIN` has no path), so the login works there. This
    was checked in the 1.37.5 source.
  - **Why not a second Traefik entrypoint:** that would need a LAN/tailnet listener and an
    exception to the "every route is in /apps.yaml" lint (see Media stack, Exposure).
- **`ADMIN_TOKEN` is an Argon2id PHC hash** (Vaultwarden ≥ 1.28). The plaintext never
  enters Infisical, git, the cluster or `config.json`. It lives in the operator's password manager.
  gitleaks has an `argon2-phc-hash` rule.
- **Websocket notifications** are served on the main port since 1.29 (`/notifications/hub`,
  `ENABLE_WEBSOCKET` defaults to on). Traefik and cloudflared proxy the HTTP Upgrade unchanged,
  so no extra route, port or Service is needed.
- **Optional SMTP without blocking the cluster.** Every `ExternalSecret` key must exist, or the
  `secrets` Kustomization (and everything after it) stays not-Ready. So the `SMTP_*` keys must
  exist but may be empty. An ESO template (`templateFrom` literal) emits only the non-empty ones,
  so an unconfigured SMTP means *no* `SMTP_*` env, not empty values.
- **Postgres on CNPG: the repo's first `Cluster`.**
  - **Layout:** one instance on `nfs-artnas` (Retain), credentials generated by the operator.
  - **Backups follow the repo's documented CNPG approach:** a nightly `ScheduledBackup` with
    `method: volumeSnapshot` through `nfs-artnas-snapshot`. The snapshots are **hot**, with
    `waitForArchive: false`: there is no WAL archive, and `pg_wal` sits inside the snapshotted
    volume.
  - **barman-cloud plus S3** (off-NAS, PITR) is still the open TODO.
- **Plus an independent, encrypted NAS backup.** Snapshots live next to the data, depend on CNPG
  and the CSI driver to restore, and don't cover `/data` (RSA keys, attachments).
  - **What it is:** a nightly CronJob (pinned `postgres:18.7-trixie`, same major as the server)
    writes a `pg_dump -Fc` and a `/data` tarball to `/volume1/Artzima/backups/vaultwarden`.
  - **Encryption:** gpg AES256 with an Infisical passphrase.
  - **Checks and rotation:** it decrypt-verifies each file before renaming it into place, and keeps
    the newest `vaultwarden_backup_keep`.
  - **The one exception to "nothing deletes on the NAS":** it only rotates its own
    `vaultwarden-*.gpg` files.
  - **Restorable without Kubernetes**, using gpg, `pg_restore` and the image.
  - **`/data` is a RWX PVC** so the CronJob can read it from any node while Vaultwarden runs.
    With Postgres there is no SQLite file in `/data` to copy inconsistently.
- **Flux substitution and scripts.** The backup script is shipped as a ConfigMap, and Flux
  substitutes `${…}` in *everything*, including shell parameter expansions, which its envsubst
  evaluates. The script therefore uses no dollar-brace syntax, and `make lint` now fails on any
  non-plain `${…}` in what Flux substitutes.

## Lidarr metadata

Lidarr (music, `k8s/apps/media/lidarr.yaml`) is wired like Sonarr/Radarr: same `/data`
volume, qBittorrent category `music`, Prowlarr sync, Jellyfin Connect, renaming off, recycle bin.
Lidarr additionally has **audio-tag writing off**, enforced by the Job (`writeAudioTags: no`,
`scrubAudioTags: false`). Adopted rips share inodes with the originals, so a retag would change the
originals.

**The metadata service (state as of 2026-11-23).** Lidarr doesn't query MusicBrainz directly. It
uses the Lidarr team's hosted metadata API, `api.lidarr.audio` ("LMD"), built from MusicBrainz data.
- **From April 2025** that service was badly degraded: 500/503 errors, rate limiting, albums
  showing 0 tracks ([Lidarr#5498](https://github.com/Lidarr/Lidarr/issues/5498)).
- **Autumn 2025:** the team moved it to new servers and reported it "mostly back" in October 2025.
- **January 2026:** the issue was **re-opened**. Text searches were "functionally broken", while
  lookups by MusicBrainz id worked much better, and images were partly missing. It is still open.
- **Status page:** [status.lidarr.audio](https://status.lidarr.audio/) shows the production API
  operational, with some components (e.g. the testing API) not operational.
- **Our own spot check on 2026-11-23:** one artist text search and one artist-id lookup both
  answered 200 in about 1.5 s. That is one probe of a well-known (likely cached) artist, not proof of
  health.
- A November 2026 write-up, ["Self-Hosted Music Still Sucks in 2026"](https://www.joekarlsson.com/blog/self-hosted-music-still-sucks-in-2026/),
  reports the same unreliability (adding artists, library imports).
- **MusicBrainz itself is fine.** The bottleneck is Lidarr's own service.

**What this repo does:** stays on **official Lidarr stable** (home-operations image,
3.1.6.x) and the **official** metadata service. It recommends adding artists **by MusicBrainz id**
(`lidarr:<mbid>`), and the Job never touches the `metadataSource` setting.

**Workarounds, documented but NOT adopted.** Use one only if the official service fails for you.
1. **A community LMD mirror** (`api.musicinfo.pro`, from the
   [hearring-aid](https://github.com/blampe/hearring-aid) project). Set it in Lidarr's
   `metadataSource` config value, which upstream exposes as `/api/v1/config/metadataprovider`; the
   hearring-aid README shows the equivalent SQLite `Config` row. Reverting means clearing that value.
   - **Pros:** no plugins, works with official stable builds.
   - **Cons:** a third party sees which artists you look up, and its availability is its own. The
     project was last updated in June 2026: maintained, but slowly.
2. **Self-host a MusicBrainz mirror plus the open-source LMD** (hearring-aid's guide, based on
   `metabrainz/musicbrainz-docker`).
   - **Pros:** no third party.
   - **Cons:** the MusicBrainz database and its replication are far beyond what these mini PCs
     should carry. Not recommended here.
3. **Lidarr's `plugins` branch plus the Tubifarry plugin** ("Custom Metadata Sources").
   - **Plugins branch:** a separate pre-release line; its last commit was 2026-01-18, about 10
     months old at the time of writing.
   - **Tubifarry:** itself very active. Its main features, though, are YouTube and Soulseek
     download clients and Spotify-based indexing, which are out of scope here.
   - **Not adopted:** it would mean leaving official stable for a stale branch.

If the official service degrades badly again, prefer option 1 (a reversible config value) over
switching builds.

**Recyclarr / TRaSH:** Recyclarr supports **Sonarr and Radarr only** (checked 2026-11-23), so there is
no Lidarr sync. Lidarr keeps its built-in quality and metadata profiles, plus the Job's "Owned rip"
profile.

**Existing music:** `make media-adopt` supports `kind: music`, with the same hardlink-only and
inode-verified guarantees. Lidarr's API has no unmapped-folder list, and name matching depends on
the degraded text search, so the import into Lidarr is a documented manual step
([manual steps §10.8](manual-steps.md#108-existing-music-lidarr)). The Job then enforces the same
protections as for movies and TV, re-applied on every run for every `owned-rip` artist: the no-upgrade
profile, unmonitored albums that have files, the recycle bin, and no renaming or retagging.

## Paperless-ngx

- **Tailnet only, deliberately.** Scanned documents (IDs, contracts, tax papers) are the most
  sensitive data in the lab, so there is no public hostname, no tunnel rule and no Cloudflare in the path.
  The Tailscale operator exposes the Service as `http://paperless` (WireGuard-encrypted, ACL-governed).
  `PAPERLESS_URL` is that name, so Django's host and CSRF checks accept nothing else (plus
  `localhost`, which the probes use).
- **Upstream's own tested combination:** Paperless-ngx 3.5.1 with Tika **3.3.1.0** and Gotenberg
  **8.38**, as pinned in upstream's `docker-compose` at that tag (Tika 4.x and Gotenberg 9.x exist,
  but are not what Paperless tests). The broker is **Valkey** 9.2, ephemeral (only a task queue).
- **Database: its own CNPG cluster,** with the same backup layers as Vaultwarden (volume
  snapshot, plus off-site WAL and base backups).
- **Files on fixed NAS folders** (`/volume1/Artzima/paperless/{data,media,consume}`, static PVs,
  `Retain`), not dynamic PVCs. The documents must stay findable on the NAS by path, independent of
  any cluster. NFS has no inotify, so the consume folder is polled every 30 s.
- **Backup:** the nightly `document_exporter` is Paperless' own portable format (documents plus all
  metadata), restorable into any fresh install with `document_importer`. It runs inside the live
  container (its wrapper needs the s6 environment) through `kubectl exec`, from a CronJob whose
  ServiceAccount may only exec into that namespace's pods. It mirrors incrementally;
  history comes from the off-site restic copy of the export folder.
- **API access for Headquarters (G.A.R.T.H.):** a per-user token, created by the operator for a
  dedicated least-privilege user and kept in HQ's credential store. This repo never sees it, so
  rotating or revoking it is a Paperless action, not a deploy.
- **Trade-off:** the Paperless container starts as root (s6 init, then drops to uid 1000 and chowns
  its folders). It is the one app here that isn't non-root from the first process.

## Off-site backups: Backblaze B2

Details, schedules and restore runbooks: [backups](backups.md).

- **One provider, three buckets, three keys.**
  - **Buckets:** `tofu/backblaze` creates private, SSE-B2 encrypted buckets with lifecycle rules.
    B2 keeps every version, and a delete only hides a file, so purging hidden files is part of
    retention.
  - **Keys:** one application key per bucket, scoped to that bucket alone, with no key or bucket
    management. Each key is written into Infisical for its one consumer.
  - **The only manual Backblaze secret** is the key OpenTofu uses to manage buckets and keys. It is
    read ephemerally, never stored in state.
  - **Buckets have `prevent_destroy`.**
  - `tofu test` (mocked) asserts that buckets are private and encrypted, keys are single-bucket,
    and each key lands at the right Infisical path.
- **Postgres: the CNPG Barman Cloud plugin**, which is CNPG's current way to back up to object
  storage. The in-tree `barmanObjectStore` is deprecated and removed in CNPG 1.32.
  - **What it gives:** continuous WAL plus nightly base backups, so point-in-time recovery within
    `backup_pg_retention`.
  - **One `ObjectStore` per cluster;** credentials reach labelled namespaces through one
    ClusterExternalSecret.
  - **On B2:** the AWS checksum env vars are set on the sidecar, as the plugin docs recommend
    for non-AWS S3.
- **Volumes and NAS folders: VolSync with its restic mover, not hand-written restic/Kopia CronJobs.**
  - **Maintained:** VolSync is actively maintained (backube, 0.17.0 in October 2026), and restic is
    the most widely used open-source backup tool with S3 support.
  - **Declarative:** one small `ReplicationSource` per volume, generated from the same list in
    `backups.yml`. VolSync handles scheduling, `forget`/`prune`, the restic cache, status and
    metrics. It has a declarative restore (`ReplicationDestination`, including `restoreAsOf`).
  - **CronJobs would mean re-implementing that per volume:** a script, locking, repository init,
    cache and status.
  - **Why not Kopia:** it is a fine engine, but upstream VolSync has no Kopia mover. Its CRDs
    list rclone, restic, rsync, rsync-TLS and syncthing. Plain Kopia CronJobs have the same
    do-it-yourself cost.
  - **Restore without Kubernetes:** the restic CLI can still restore everything.
  - **Copy method:** `Direct`. The mover mounts the live NFS volume, which needs no snapshots and
    works for static PVs (Paperless).
- **PBS: an S3 datastore.** PBS 4.2 made S3-backed datastores fully supported. A local sync job copies
  the local datastore into the B2-backed one, which keeps its own prune/GC history, so local mistakes
  are not mirrored. The alternatives were a second PBS off-site (more hardware) or rclone of the
  datastore directory (not consistent, no dedup awareness).
- **NAS media and personal files: restic on the NAS (Backrest).**
  Ansible manages its configuration, schedules, checks and alert hooks.
  - **Why on the NAS:** restic runs **on the NAS itself**, not in the cluster, so the most
    irreplaceable data keeps being backed up while the compute hosts are unavailable. It needs
    nothing but Docker on the NAS and B2.
  - **Why Backrest:** it is a maintained restic front-end (v1.14, November 2026) with a scheduler,
    retention, prune and check policies, hooks (Shoutrrr, healthchecks.io) and a restore UI. Its
    config is plain JSON, so the repo templates it (Ansible role `nas_backup`).
  - **Why restic:** client-side encryption with its own password, deduplication, upload limits,
    and restores that work without Backrest or the NAS.
  - **Bucket:** a dedicated bucket with a single-bucket key (`tofu/backblaze`), separate from the
    cluster's restic bucket and password, so neither side's credentials open the other's backups.
  - **Shares are only ever read:** they are bind-mounted **read-only**. Backrest writes only inside
    `/volume1/Artzima/backrest`, and restores go to its `restore/` folder; copying files back is a
    human decision.
  - **Missed runs** are caught by healthchecks.io (a dead-man's switch, one check per plan), and
    errors by a Shoutrrr URL. Backrest 1.14's generic webhook action has no handler, so the future
    Headquarters endpoint is used through Shoutrrr's `generic+https://…`.
  - **The migration is manual and verified first:** upload to completion, `restic check`, test
    restores, and only then retire the UGOS job and delete its B2 data. The temporary double
    storage cost is accepted. `existing` (the UGOS job) and `off` stay as options.

## Disaster recovery (total loss)

Runbook: [disaster recovery](disaster-recovery.md).

- **Everything needed to rebuild is off-site:** GitHub (code), Infisical (secrets), Backblaze B2
  (data), Cloudflare and Tailscale (edge, admin access). Plus a printed or offline **emergency
  kit** for what can't live in those: Infisical's own recovery, the restic passwords, a read-all
  B2 key, and the accounts' recovery codes. `emergency-kit.yaml` lists them by name, and
  `make emergency-kit` checks rotation dates, never values.
- **Pause switches, because the most dangerous moment of a restore is the first backup after
  it.** A rebuilt cluster or NAS would otherwise back up its still-empty volumes into the
  existing restic repositories, and retention would then gradually forget the last good
  snapshots. `backup_volsync_paused` (VolSync `spec.paused`) and `nas_backup_paused` (Backrest
  schedules disabled) are set before the rebuild and cleared after verification. Restores pick
  snapshots **before** the disaster (`restoreAsOf`, explicit snapshot IDs), never a blind "latest".
- **CNPG restores into a new `serverName`,** so a recovered cluster never writes into the
  archive it was restored from.
- **Lost OpenTofu state is expected:** it lived on the workstation. The Backblaze buckets are
  **imported**, never re-created. The Cloudflare objects are deleted or imported. Everything
  else is new hardware, so fresh state is correct.

## Few touch points, gates and guardrails

- **Five root credentials, everything else derived.** Each extra hand-made secret is one more
  thing to rotate, mistype or lose. The Infisical bootstrap identity, Cloudflare token, Tailscale
  OAuth client, Backblaze admin key and GitHub PAT are the only ones a person creates; OpenTofu
  derives the rest (`tofu/bootstrap`: Tailscale keys, the ESO identity; `tofu/backblaze`; the
  tunnel), and `make gen-secrets` creates random values directly in Infisical. Rejected: a
  password-generator step on the workstation (values would pass through a terminal or file), and
  Infisical's own secret generation (not available as code on the free plan).
- **Gates:** `make preflight` (keys, MACs, disk serials, NAS) before anything changes; every apply
  shows a plan and needs `yes` (`scripts/confirm.sh`, `AUTO_APPROVE=1` for automation). Ansible's
  "plan" is the host + task list; a real `--check` dry run is available but not the default,
  because many tasks depend on earlier ones (packages, users) and report misleading failures.
- **Guardrails:** the answer server installs only onto a disk selected by serial
  (`filter-match = "all"`), never "the first disk"; `prevent_destroy` on buckets, tunnel and DNS;
  PVs Retain (one documented scratch exception for restore tests); B2 keys without `deleteFiles`,
  Object Lock (governance) and separate maintenance keys, so a stolen backup key can hide but not
  destroy history. Governance, not compliance mode: a person with the account can still fix a
  mistake; compliance mode would lock even the owner out for the whole period.
- **Self-checks over trust:** monthly restore tests that actually restore (restic `--verify`, CNPG
  recovery into a scratch cluster) and alerts to ONE webhook, so Headquarters can be the single
  inbox later.

