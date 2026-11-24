# Manual steps (not automatable)

Addresses below are today's values from `ansible/group_vars/all/network.yml` +
`ansible/hosts.yml` (in this public copy: fictional examples; `make private-prepare` writes the
actual ones into `.private/runtime/docs/`). If those change, use the new ones (for example, `make render` and
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
| Example eMMC host | Boot from the external USB disk after install (it holds the OS; the eMMC is unused); remove the installer stick; second NIC unused for now | the auto-installer cannot target eMMC |

### 1.1 Power: UPS shutdown, coming back, Wake-on-LAN

- **After an outage:** NUT forces a shutdown (FSD) on battery. When mains returns, *Restore on
  AC power loss = Power On* starts the machines, **but only if the outlets really lost power**.
  If the UPS doesn't cut its outlets after the FSD (open item: NUT killpower, README TODOs), the
  halted machines stay off with power present. Then wake them with Wake-on-LAN.
- **Wake-on-LAN is on and stays on** (the plan relies on it). Checked read-only on 2026-11-23:
  all three mini PCs report `Wake-on: g` (magic packet only) and no RTC wake alarm. `first-boot.sh`
  sets `wol g` on `lan0` after the install.
- **Sending a magic packet:** from a lab Linux machine or the workstation's lab NIC (WSL with
  mirrored networking), never from the home network. Magic packets are broadcasts and don't cross
  the Firewalla or Tailscale. For example, from host01:
  `apt install wakeonlan && wakeonlan -i 192.0.2.255 <MAC from addresses.md>`.
- **Unexpected wake-ups:** the mini PCs woke unexpectedly twice (2026-11-22 and 2026-11-23). With
  `Wake-on: g` and no RTC alarm, the likely cause is a magic packet from a device that was then on
  the same LAN (a phone or NAS app, a router feature). Behind the Firewalla, home-network
  broadcasts no longer reach the lab. If it happens again after the move, the sender is on the lab
  side; look for WOL features on the NAS and the Firewalla. Don't disable WOL to stop it: the
  outage recovery above needs it.

## 2. Firewalla (lab router) and the Xfinity gateway

**Topology:** Xfinity gateway (home network `198.51.100.0/24`, i.e. `home_subnet`) → Firewalla
Purple SE **WAN** port → Firewalla **LAN** port → the lab switch (UGREEN 2.5G, unmanaged) → every
lab machine **and the NAS**. The lab is `192.0.2.0/24` (`lab_subnet`), gateway and DNS
`192.0.2.1` (the Firewalla), untagged. Why: [decisions](decisions.md#the-lab-network-behind-a-firewalla-no-vlans-2026-11-23).
Addresses: the generated [`addresses.md`](addresses.md).

Do this on build day, **before the USB installs** ([OPERATOR-SETUP B0](OPERATOR-SETUP.md#b0-network-firewalla-nas-workstation)).
The installers need the Firewalla's DHCP and the workstation's lab NIC to fetch their answers.
Exact menu names vary between Firewalla app versions; the values do not.

**Setting up a new or factory-reset Firewalla** (app, WireGuard VPN, SSH key, then the rules from
git with `make firewalla-apply`): [disaster recovery P5](disaster-recovery.md#p5-firewalla-replace-or-factory-reset).
The rules live in [`firewalla/baseline.yaml`](../firewalla/baseline.yaml); `make firewalla-plan`
shows the diff and checks the settings below (read-only).

**Lessons (2026-11-23): no country blocks.** A "block all countries except US" rule on the
Firewalla silently broke Tailscale (its relays and coordination servers sit abroad) and `1.1.1.1`
(anycast, geolocated abroad). Nothing in the app pointed at the rule; it took a while to find. The
lab needs the whole internet (updates, images, Tailscale, Cloudflare). Keep only Active Protect and
the ingress firewall; no country, category or ad-block rules. `make firewalla-plan` reports them
as `FAIL`.

### 2.1 Firewalla: Router mode

1. Cabling: the Xfinity gateway to the Firewalla port you choose as **WAN**; the other port to
   the lab switch (**LAN**). Nothing else from the home network is plugged into the lab switch.
2. Firewalla app → the box → *Settings → Mode* (in some versions *Network Manager*): switch from
   **Bridge** (today `198.51.100.251` on the home network) to **Router**. Assign the
   Xfinity-facing port as WAN (connection type **DHCP** from the Xfinity) and the switch-facing
   port as LAN. The app reconnects through the cloud, so your phone does not need to be on the lab.
3. *Network Manager → LAN* network:
   - Address **`192.0.2.1`**, netmask **`255.255.255.0`** (`/24`).
   - DHCP **on**. Pool **`192.0.2.210` - `192.0.2.249`** (`lab_dhcp_pool`). It stays clear of the
     static hosts (`.100`-`.160`), the workstation (`.72`) and the reserved `.204`. `make render`
     refuses a pool that overlaps a static address. The DHCP is needed on build day: the
     installers take a lease before they fetch their answers.
   - DNS handed out by DHCP: the Firewalla itself (`192.0.2.1`).
4. *Network Manager → WAN* DNS (the Firewalla's upstreams): **`9.9.9.9`** and **`1.1.1.1`**.
   The static hosts already list them as fallbacks after the Firewalla (`lab_dns`).
5. **DHCP reservations:** none. Every lab machine and guest has a static address.
6. **Outbound:** allow everything from the lab to the internet. The only block rule comes from git
   (`make firewalla-apply`): the lab may not open connections to the home network, except the
   answer server on the workstation. Make sure **new
   device quarantine** (*New Device* / *Quarantine*) is **off** for the LAN, or new lab machines
   are cut off on build day. If something outbound breaks later, check the Firewalla's blocked
   flows first.
7. **Inbound:** **no port forwards, no DMZ, no UPnP needed.** Cloudflare Tunnel and Tailscale
   only make outbound connections and handle the double NAT (Xfinity + Firewalla).
8. Check from the workstation's lab NIC (2.3): `ping 192.0.2.1`, then
   `nslookup cloudflare.com 192.0.2.1`.

### 2.2 Xfinity gateway: remove the old lab reservations

Once the lab is behind the Firewalla, the Xfinity reservations made on 2026-11-22 for the lab are
obsolete: those MACs are no longer on the home network. In the Xfinity admin site
(<http://198.51.100.1>, or the Xfinity app) → *Connected Devices → Reserved IP*, delete the
reservations for **`198.51.100.100`-`198.51.100.103`, `198.51.100.127` and `198.51.100.156`**
(host01, node01-03, pbs, artnas), plus any other lab row you reserved there. Do not delete the
home devices' reservations. Optional: reserve the Firewalla's **WAN** MAC (for example at
`198.51.100.251`) so its home-side address stays stable for troubleshooting. Nothing in the repo
depends on it.

The Xfinity gateway keeps serving the home network (DHCP, DNS) unchanged. It cannot add a static
route to the lab, so home devices reach the lab only over Tailscale ([§4a](#4a-reaching-the-lab-from-the-home-network)).

### 2.3 Admin workstation (Windows): wired lab NIC

The workstation is **not managed** by this repo. Its Wi-Fi stays on the home network, and a wired
NIC on the lab switch has a static address:

- *Settings → Network → Ethernet → IP assignment: Manual*, IPv4 **`192.0.2.72`**, subnet
  prefix **24**. Leave the **gateway empty** (recommended) and DNS empty. The `/24` on-link route
  covers the whole lab, while internet traffic stays on the Wi-Fi with only one default route.
- Windows marks a network without a gateway as *Public*. For `make answer-server` (WSL2, mirrored
  networking), allow inbound TCP 8000 on that profile while the installs run, or set the network to
  *Private*.
- **Tailscale on this workstation:** run `tailscale set --accept-routes=false` (or untick
  *Use Tailscale subnets* in the tray menu). Windows accepts subnet routes by default. Tailscale
  sets up the routing table to prefer the subnet router, so the lab route from ts-router-1/2 would
  shadow the wired NIC, and lab traffic would detour over the tailnet (and fail entirely while the
  lab is being rebuilt). Tailscale has no automatic LAN-first preference on Windows
  ([Tailscale: overlapping subnet routes](https://tailscale.com/docs/reference/troubleshooting/network-configuration/lan-traffic-overlapping-subnets)).
  Tailnet devices (MagicDNS names such as `http://paperless`) still work. Turn it back on only
  when the workstation is away from the lab switch. If Tailscale also runs inside WSL, leave its
  route acceptance off there too (the Linux default).
- Check: `route print 192.0.2.*` shows the Ethernet interface, not Tailscale.

**DGX Spark:** its wired port (formerly at `192.0.2.204`) is **not part of the lab**. `.204` stays
reserved (`lab_unmanaged` in `network.yml`, outside the DHCP pool). Don't plug it into the lab
switch.

## 3. UGREEN NAS (UGOS)

**Address (build day, with the Firewalla switch-over, [§2](#2-firewalla-lab-router-and-the-xfinity-gateway)):**
the NAS moves from the home network to the lab LAN and keeps its offset `.156`.
*Control Panel → Network → Network connection → (the wired LAN) → Edit → IPv4: Manual*:

- IP **`192.0.2.156`**, subnet mask **`255.255.255.0`**, gateway **`192.0.2.1`**, DNS **`192.0.2.1`**.
- Easiest order: change it from the home network as the **last step before** switching the
  Firewalla to Router mode. The UI drops and comes back at `192.0.2.156` from the workstation's lab
  NIC. If you miss that window, the NAS takes a lease from the Firewalla pool; find it in the
  Firewalla app's device list, open UGOS there and set the manual address.
- Home devices that used the NAS (SMB shares, apps) now reach it only through Tailscale
  ([§4a](#4a-reaching-the-lab-from-the-home-network)).
- If Backrest already runs on the NAS, run `make nas-backup` after the change. Docker publishes
  the UI on the NAS address (`artnas_ip`) and cannot start on the old one.

**NFS share:** *Control Panel → File Service → NFS* (enable NFSv4.1), then on the
shared folder **Artzima** → NFS permissions:

- Allow `192.0.2.0/24` (i.e. `lab_subnet`), read/write, **no_root_squash**. **Delete** the old
  rule for the home network (`198.51.100.0/24`), if there is one
  (Proxmox vzdump and the k8s CSI driver create files as root), sync.
- Create the sub-folder `k8s` (used by the `nfs-artnas` StorageClass:
  `/volume1/Artzima/k8s`).
- Create the app-backup folder for Vaultwarden's nightly encrypted backup (static PV
  `vaultwarden-backups`; owner = the uid the backup job runs as):
  ```bash
  sudo mkdir -p /volume1/Artzima/backups/vaultwarden
  sudo chown 1000:1000 /volume1/Artzima/backups/vaultwarden && sudo chmod 0700 /volume1/Artzima/backups/vaultwarden
  ```
- **Storage boundary:** normal deployment writes only inside the configured lab export.
  Inspect mount paths and permissions before deployment; see [NAS volume lifecycle](#11-nas-volume-lifecycle).
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
  The NAS may need this again after a UGOS update or a reboot. After the move to the lab LAN,
  `sudo exportfs -v` must list `192.0.2.0/24` and **no** `198.51.100.0/24` entry. Remove a leftover
  one by hand with `sudo exportfs -u 198.51.100.0/24:/volume1/Artzima`.
- **Who matches the rule:** the Tailscale subnet routers SNAT, so tailnet clients reach the NAS
  *as ts-router-1/2's lab addresses*, which this rule allows. Access from the tailnet (including
  home devices) is therefore limited by the Tailscale policy, not by NFS ([§4a](#4a-reaching-the-lab-from-the-home-network)).

**UPS:** *Control Panel → Hardware → UPS* → enable, mode **Network UPS slave**,
UPS server IP **192.0.2.100** (host01; it was the home-network address before the Firewalla). UGOS logs in as `monuser`/`secret` to
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

## 4a. Reaching the lab from the home network

Home devices (the Xfinity side) can't reach `192.0.2.0/24` directly: the Firewalla NATs the lab,
and the Xfinity gateway can't hold a static route to it. They go through **Tailscale**, using
the two subnet routers that advertise `lab_subnet` (ts-router-1/2, HA failover):

1. The route `192.0.2.0/24` is approved on both routers (§4, or the `autoApprovers` snippet).
2. On each home client, install Tailscale, log in to the tailnet, and **accept routes**:
   Windows, macOS, iOS and Android do so by default (*Use Tailscale subnets* in the client
   menu); Linux needs `sudo tailscale set --accept-routes`.
3. The policy must let those devices reach the route. With a default allow-all policy nothing is
   needed. With grants, add one per user/group, and keep it narrow, because the NAS sees these
   clients as a subnet router (above, §3). For example, everyone gets SMB on the NAS and the admin gets everything:
   ```json
   {"src": ["autogroup:member"], "dst": ["192.0.2.156/32"], "ip": ["tcp:445"]},
   {"src": ["autogroup:admin"],  "dst": ["192.0.2.0/24"],   "ip": ["*"]}
   ```
4. Use the lab addresses directly (`\\192.0.2.156\<share>`, `http://192.0.2.156:9898`). The public
   apps (`https://<app>.<domain>`) keep working from anywhere without Tailscale.

The **admin workstation** is the exception: it is wired into the lab and must **not** accept the
routes ([§2.3](#23-admin-workstation-windows-wired-lab-nic)).

## 5. Later: VLANs (only with a managed switch)

**Not needed for separation any more.** The Firewalla already gives the lab its own network,
because the lab has its own physical switch ([decisions](decisions.md#the-lab-network-behind-a-firewalla-no-vlans-2026-11-23)).
The current **UGREEN 2.5G switch is unmanaged**. Its "VLAN mode" is only *port isolation*, not
802.1Q, so it can't carry tagged VLANs anyway.

VLANs remain an option **only if a managed switch is added**, for example when other networks
(IoT, guests) have to share a cable or a switch with the lab:

1. A managed 802.1Q switch (for example a TP-Link TL-SG108E). The **Firewalla** does the VLANs
   and the routing between them (*Network Manager → Create Network → VLAN*); no extra router.
2. On the switch: VLAN `lab_vlan_id` (e.g. 20), the Firewalla uplink and hypervisor ports
   **tagged**, untagged access ports (PVID 20) for untagged lab devices such as the NAS. Keep one
   untagged rescue port.
3. Then follow [README: Moving the lab to another subnet or a VLAN](../README.md#moving-the-lab-to-another-subnet-or-a-vlan):
   edit `network.yml`, then `make render network common tofu-apply`.
4. The NAS: UGOS supports VLAN tagging on its NIC; otherwise use an untagged access port. If the
   subnet changes, update the NFS rule and the UPS server IP.

**Pi-hole (on hold, not in this repo):** the Firewalla can hand out a Pi-hole as the lab's DNS
server (*LAN network → DNS*) and use it as its own upstream. Then add its address first in
`lab_dns` for the static hosts. The home side keeps using the Xfinity's DNS unless you change
the Xfinity separately.

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
   `[{"app":"grafana","emails":["you@…"]}]`,
   then `make cf-apply`. Recommended: `grafana`. The one-time PIN login
   works without an IdP: enable it under *Zero Trust → Settings → Authentication*.
   Don't put `jellyfin` behind browser Access if you use its mobile or TV apps, because
   they can't complete the Access login. If `/settings/CLOUDFLARE_ACCESS_APPS` still lists an app
   that is no longer in `/apps.yaml` (for example `homeassistant`, removed 2026-11-23), remove that
   entry first: `make cf-plan` refuses unknown apps.
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

Traefik reaches the Jellyfin LXC, the only app outside Kubernetes, via `k8s/apps/external/`
(Service + EndpointSlice, with its IP and port from the inventory). Its requests come
from the Talos node IPs (pod traffic is SNATed when it leaves the cluster), so:

- **Jellyfin:** *Known proxies* = the same Talos node IPs and *Published server URIs* =
  `all=https://jellyfin.<domain>` are set by `make jellyfin` (role `jellyfin_setup`), nothing to click.

## 7. Private images: GHCR PAT and pull secret

Application image publishing runs in a separate **private builder repository**.
The public IaC repository contains definitions and pins, not an active publishing workflow.
The four targets are `alexandria-mcp`, `alexandria-oauth-proxy`, `alexandria-cli` and `skyjo`.

1. **Prepare the private builder:** follow [the image build guide](../images/README.md#private-builder-requirement).
2. **Verify privacy before application builds:** the builder checks repository privacy,
   registers empty packages and checks private visibility, then verifies every target
   before building. Do not proceed on a failed check. Public packages cannot be changed
   back to private; a private replacement is required.
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

**Library: initialised automatically when absent.**
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
by default it is also the legacy bearer for Cursor/Codex. To use an existing client token, put it into Infisical **before** `make gen-secrets`: existing keys are never changed.

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
   requires the clients to register again.

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
Lidarr directly. Seerr keeps its data in its own CNPG Postgres `seerr-db` (`k8s/apps/media/database.yaml`;
credentials generated by CNPG, nothing to create), not in SQLite on NFS
([decisions](decisions.md#media-stack-arr--qbittorrent-via-surfshark--seerr)).
**Wizarr** (Jellyfin invites, `https://wizarr.<domain>`, namespace `wizarr`) is set up by its own
idempotent Job, `wizarr-setup` (§10.3).

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
4. **Jellyfin API key:** automated. `make jellyfin` creates the key `artzima-iac` in Jellyfin and
   writes it to `/k8s/media/JELLYFIN_API_KEY`. Sonarr, Radarr and Lidarr use it for their
   "Connect → Emby / Jellyfin" notification (library refresh on every import); while it is missing
   or empty, the wiring Job skips only that connection. The same run turns on Jellyfin's *legacy
   authorization* (off by default since Jellyfin 12), because that notification sends the key in the
   legacy `X-MediaBrowser-Token` header. It also copies the Jellyfin admin login
   (`/operator/JELLYFIN_ADMIN_*`) to `/k8s/media/JELLYFIN_ADMIN_USER` / `_PASSWORD` on every run, for
   the wiring Job's Seerr setup (§10.3). Run `make jellyfin` before the first Flux sync of the media
   stack: the `secrets` Kustomization waits for these keys.
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
The Job waits up to 15 min for each app. Steps that can't run yet (Seerr before `make jellyfin` has written the
Jellyfin admin login, Seerr's Sonarr/Radarr before Recyclarr has created the profiles) are **skipped** and picked up by a later run.
Wizarr's setup works the same way: `kubectl -n wizarr logs job/wizarr-setup`; to re-run it,
`kubectl -n wizarr delete job wizarr-setup && flux reconcile kustomization apps --with-source`.

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
| Jellyfin | `https://jellyfin.<domain>` | **Nothing to set up** (a fresh start: no users, watch history or settings are carried over). `make jellyfin` (role `ansible/roles/jellyfin_setup`, part of `make up`) does it over Jellyfin's API, idempotently: the first-run wizard (en-US, US metadata, remote access on, no UPnP) with the **admin login from Infisical** `/operator/JELLYFIN_ADMIN_USER` / `JELLYFIN_ADMIN_PASSWORD` (copy it to your password manager); the libraries **Movies** → `/data/media/movies`, **Shows** → `/data/media/tv`, **Music** → `/data/media/music` (created if missing, never deleted, not scanned by the run); NVENC transcoding (hardware decoding of H.264/HEVC/VC-1/VP9/AV1, HEVC encoding, tone mapping); known proxies and the published URL; the API key. If someone set a password in the unfinished wizard first (it is reachable on the public URL until `make jellyfin` runs), the run takes the wizard user back with Jellyfin's local password-reset PIN. "Real-time monitoring" doesn't see NFS changes; the Sonarr/Radarr Connect notifications refresh the library instead. Existing rips appear there after `make media-adopt`: [README](../README.md#bringing-in-your-existing-rips) |
| Seerr | `https://seerr.<domain>` | **Nothing to set up.** The wiring Job (`k8s/apps/media/wiring/wire.py`, steps *Seerr setup* and *Seerr: Sonarr / Radarr*) does the setup wizard over Seerr's API, idempotently (reads each setting, writes only what differs): it signs in once with the Jellyfin **admin** login (Infisical `/k8s/media/JELLYFIN_ADMIN_*`, the cluster's copies of `/operator/JELLYFIN_ADMIN_*` that `make jellyfin` keeps equal), which makes that account Seerr's admin and connects Jellyfin (`jellyfin.external.svc.cluster.local:80`, no SSL, external URL `https://jellyfin.<domain>`); it enables the **Movies** and **Shows** libraries (Seerr has no music), sets the application URL `https://seerr.<domain>` and marks Seerr initialized; then it adds Sonarr and Radarr (Recyclarr's profiles `WEB-1080p` / `HD Bluray + WEB`, root folders `/data/media/{tv,movies}`) as soon as those profiles exist. Sign in with the Jellyfin admin login. Left to you: *Settings → Users → Import Jellyfin users*, or allow new Jellyfin sign-ins. Changing the Jellyfin admin password: change `/operator/JELLYFIN_ADMIN_PASSWORD` *and* the password in Jellyfin, then `make jellyfin` (Seerr itself talks to Jellyfin with its own API key) |
| Wizarr | `https://wizarr.<domain>` | **Nothing to set up.** The Job `wizarr-setup` (`k8s/apps/wizarr/setup.yaml`, script `setup/wizarr_setup.py`) runs Wizarr's first run through Wizarr's own forms on every Flux sync, idempotently: while no admin exists it creates one with the **Jellyfin admin login** (Infisical `/k8s/media/JELLYFIN_ADMIN_*`, ExternalSecret `wizarr/wizarr-setup`), signs in with it, adds the Jellyfin server if Wizarr has none (`http://jellyfin.external.svc.cluster.local:80`, API key `/k8s/media/JELLYFIN_API_KEY`, external URL `https://jellyfin.<domain>` for invite links) or corrects only its URL, external URL or API key, then scans the libraries (new ones enabled, existing ones keep their setting). It never touches invites, users or other settings. Log in with the Jellyfin admin login; invites are yours to create. `/setup` answers 403 on the public host (`deny_paths` in `/apps.yaml`). If you change the Wizarr admin password in Wizarr, the Job's sign-in fails: keep it equal to `/operator/JELLYFIN_ADMIN_PASSWORD`. A passkey (2FA) on the account makes the Job skip after the admin check. Don't put the whole `wizarr` host behind Cloudflare Access: invitees open `/j/<code>` without an account |

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

## 11. NAS volume lifecycle

The IaC stack writes application data only within the configured lab export. Personal
shares and media-adoption source folders remain outside normal deployment writes.

- NFS persistent volumes and storage classes use `Retain`. Deleting a PVC leaves its
  data on the NAS; it does not authorise removing the backing directory.
- Reusing a namespace and PVC name can attach existing application state. Inspect the
  backing path before deployment and follow the recovery runbook when restoring data.
- Record media sources and personal backup shares in private configuration. Check
  mounts, symlinks, permissions and overlapping paths before changing storage.
- Before removing an application volume, stop its consumers, verify an independent
  backup and test restore, and review the exact volume and backing directory.
- Backup rotation and Paperless import/export perform the documented deletions within
  their own managed directories. They do not grant general deletion access to other shares.

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
default; role `nas_backup`). It is independent of the lab and continues running independently of the compute hosts. The
personal shares are outside the lab: the repo never writes to them, and Backrest mounts them
**read-only**. Use a verified backup-provider transition when replacing another backup system.

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
5. **Open the UI** at `http://192.0.2.156:9898` (from the lab LAN, or over Tailscale from anywhere including home, §4a) and start the first runs.
   Set up healthchecks.io ([alerts](backups.md#alerts-failures-and-missed-runs)).
6. **Verify the backup:** let the first full upload finish, verify it (`restic check` plus test restores), and
   **only then** review retirement of the previous backup provider according to its retention policy. Both are manual steps;
   the repo never does them ([backups: Transition](backups.md#backup-provider-transition)).
7. **Monthly:** [check the last successful run](backups.md#check-the-last-successful-run-monthly-and-in-the-quarterly-test).
