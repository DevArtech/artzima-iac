# Grafana: admin login and dashboards as code

Grafana itself is kube-prometheus-stack (`k8s/infrastructure/controllers/kube-prometheus-stack.yaml`);
this folder (Flux Kustomization `apps`) holds what goes into it.

## Admin login: `grafana-admin-sync`

Infisical `/k8s/monitoring/GRAFANA_ADMIN_USER` / `_PASSWORD` (yours) → ESO Secret `grafana-admin`.
Grafana reads them only when it first creates its database, so the Job `grafana-admin-sync`
(`grafana-admin/sync.py`, re-created on every `apps` sync) applies a change to user id 1 through
the API, signing in with the login it last applied (Secret `grafana-admin-applied`). Changing
the login and the fallback when it cannot sign in: [manual steps §15.1](../../../docs/manual-steps.md#151-admin-login).

## Dashboards

| Folder | Dashboard | Source (pinned) |
|---|---|---|
| Artzima | **artzima: overview** (home) | `scripts/grafana_overview.py` |
| Hosts | Node Exporter Full | grafana.com 1860 rev 46 |
| Hosts | Proxmox via Prometheus (prometheus-pve-exporter) | grafana.com 10347 rev 5 |
| Platform | Flux Cluster Stats, Flux Control Plane | fluxcd/flux2-monitoring-example @7ab65dc |
| Platform | Traefik Official Kubernetes Dashboard | traefik/traefik v3.7.19 `contrib/grafana` |
| Databases | CloudNativePG | cloudnative-pg/grafana-dashboards @47e4c51 |

The sidecar loads every ConfigMap labelled `grafana_dashboard: "1"` into the folder named by its
`grafana_folder` annotation (`kustomization.yaml`). UI edits are not kept (`editable: false`).

Scrape targets they need: `k8s/infrastructure/configs/monitors.yaml` (CNPG instances, Flux
controllers); Flux object state comes from kube-state-metrics (`gotk_resource_info`, chart values).

```bash
python3 -I scripts/grafana_dashboards.py vendor     # download pinned JSON (sha256), adapt, write
python3 -I scripts/grafana_dashboards.py overview   # write the home dashboard
# every panel returns data (through Grafana's /api/ds/query), e.g. with a port-forward:
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 13000:80 &
GRAFANA_URL=http://localhost:13000 GRAFANA_USER=... GRAFANA_PASSWORD=... \
  python3 -I scripts/grafana_dashboards.py check
```

Adaptations (all in `scripts/grafana_dashboards.py`): datasource = the provisioned Prometheus
(`uid: prometheus`, no datasource picker), stale variable selections cleared, Flux Control Plane
preselects `flux-system`.

### Panels removed (cannot show data here)

- **Node Exporter Full:** SWAP Used (no swap on Talos); Processes Detailed States, PIDs/Threads
  Number and Limit (processes collector off); IRQ Detail (interrupts collector off); TCP Stat
  Persistent/Transient, TCP Socket Queue (tcpstat collector off); CPU Frequency Scaling, Hardware
  Temperature Monitor, Cooling Device Utilization, Hardware Fan Speed, CPU Throttling, ECC Memory
  Errors, Power Supply Status (Talos VMs: no such hardware); Systemd Units State and Sockets ×3 (no
  systemd on Talos). *Root FS Used* and the root size stat now show `/var` (Talos' `/` is the
  read-only image).
- **CloudNativePG:** the replication lag panels ×9 (every cluster has one instance); zone (nodes
  carry no `topology.kubernetes.io/zone`); Volume Space Usage: Tablespaces (none).

### Empty by design while all is well

Flux "Suspended Objects"; Traefik "Services failing SLO" ×2 and "5xx over $interval" (they list
only offenders). Flux "Failing Sources" shows 0 instead of no data.

Not available at all (no exporter): host temperatures, Proxmox Backup Server, Backrest, Tailscale,
VolSync's own operator metrics (the overview uses kube-state-metrics' `artzima_volsync_*`).
