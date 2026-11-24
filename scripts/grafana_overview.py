#!/usr/bin/env python3
"""The home dashboard "artzima: overview", as code (scripts/grafana_dashboards.py overview).

Every query uses metrics this cluster's Prometheus has (checked with
`grafana_dashboards.py check`). Not here because nothing exports them: host
temperatures (Talos VMs and pve-exporter have none), Proxmox Backup Server and
Backrest (no exporter), Tailscale. PBS shows as Proxmox sees its `pbs` storage.
"""

from __future__ import annotations

# Traefik's service label for an /apps.yaml route: <ns>-public-<app>-<hash>@kubernetescrd
APP = 'label_replace({q}, "app", "$1", "service", ".*-public-(.+)-[0-9a-f]{{20}}@kubernetescrd")'
PUBLIC = 'service=~".*-public-.*"'
# pve-exporter ids: node/<name>, storage/<node>/<storage>
NODE = 'label_replace({q}, "host", "$1", "id", "node/(.+)")'

UP_DOWN = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red", "index": 0},
    "1": {"text": "UP", "color": "green", "index": 1}}}]


class Board:
    def __init__(self, ds: dict) -> None:
        self.ds = ds
        self.panels: list[dict] = []
        self.y = 0
        self.x = 0
        self.row_h = 0
        self.next_id = 1

    def row(self, title: str) -> None:
        self.newline()
        self.panels.append({"type": "row", "title": title, "id": self._id(), "collapsed": False,
                            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": []})
        self.y += 1

    def newline(self) -> None:
        if self.x:
            self.y += self.row_h
        self.x, self.row_h = 0, 0

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id - 1

    def add(self, kind: str, title: str, targets: list[tuple[str, str]], w: int, h: int, *,
            unit: str = "", desc: str = "", thresholds: list[tuple[float | None, str]] | None = None,
            mappings: list | None = None, minv: float | None = None, maxv: float | None = None,
            options: dict | None = None, instant: bool = False, table: bool = False,
            overrides: list | None = None, transformations: list | None = None,
            custom: dict | None = None, decimals: int | None = None) -> None:
        if self.x + w > 24:
            self.newline()
        steps = [{"color": c, "value": v} for v, c in (thresholds or [(None, "green")])]
        defaults: dict = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps},
                          "mappings": mappings or [], "color": {"mode": "thresholds"}}
        if kind == "timeseries":
            defaults["color"] = {"mode": "palette-classic"}
            defaults["custom"] = {"lineWidth": 1, "fillOpacity": 10, "showPoints": "never",
                                  "spanNulls": True, **(custom or {})}
        if minv is not None:
            defaults["min"] = minv
        if maxv is not None:
            defaults["max"] = maxv
        if decimals is not None:
            defaults["decimals"] = decimals
        panel = {
            "type": kind, "title": title, "description": desc, "id": self._id(), "datasource": self.ds,
            "gridPos": {"h": h, "w": w, "x": self.x, "y": self.y},
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": options or {},
            "targets": [{"refId": chr(65 + i), "datasource": self.ds, "expr": e, "legendFormat": legend,
                         "instant": instant or table, "range": not (instant or table),
                         **({"format": "table"} if table else {})}
                        for i, (e, legend) in enumerate(targets)],
        }
        if transformations:
            panel["transformations"] = transformations
        self.panels.append(panel)
        self.x += w
        self.row_h = max(self.row_h, h)


def stat_opts(text_mode: str = "value_and_name", graph: bool = False) -> dict:
    return {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background", "graphMode": "area" if graph else "none",
            "textMode": text_mode, "justifyMode": "auto", "orientation": "auto"}


def bar_opts() -> dict:
    return {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
            "valueMode": "color", "namePlacement": "left", "sizing": "auto"}


def ts_opts() -> dict:
    return {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"}}


def build(ds: dict) -> dict:
    b = Board(ds)
    pct = [(None, "green"), (0.75, "orange"), (0.9, "red")]

    # ---------------------------------------------------------------- at a glance
    b.row("At a glance")
    b.add("stat", "Proxmox hosts", [(NODE.format(q='pve_up{id=~"node/.*"}'), "{{host}}")], 6, 4,
          mappings=UP_DOWN, thresholds=[(None, "red"), (1, "green")], options=stat_opts(), instant=True,
          desc="pve-exporter: each Proxmox VE node, as the cluster API reports it.")
    b.add("stat", "Kubernetes nodes Ready",
          [('kube_node_status_condition{condition="Ready",status="true"}', "{{node}}")], 5, 4,
          mappings=UP_DOWN, thresholds=[(None, "red"), (1, "green")], options=stat_opts(), instant=True)
    b.add("stat", "Cloudflare tunnel connections", [("sum(cloudflared_tunnel_ha_connections) or vector(0)", "")],
          3, 4, thresholds=[(None, "red"), (1, "orange"), (4, "green")], options=stat_opts("value"),
          instant=True, desc="Registered tunnel connections to Cloudflare's edge (all cloudflared "
                             "replicas; 4 each). 0 = the public apps are unreachable.")
    b.add("stat", "PBS storage (seen by Proxmox)",
          [('max(pve_up{id=~"storage/.*/pbs"})', "")], 3, 4, mappings=UP_DOWN,
          thresholds=[(None, "red"), (1, "green")], options=stat_opts("value"), instant=True,
          desc="Whether Proxmox reaches its Proxmox Backup Server storage `pbs`. PBS itself "
               "exports no metrics here.")
    b.add("stat", "UPS battery", [("network_ups_tools_battery_charge", "")], 3, 4, unit="percent",
          thresholds=[(None, "red"), (50, "orange"), (90, "green")], options=stat_opts("value"), instant=True)
    b.add("stat", "Alerts firing",
          [('count(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}) or vector(0)', "")], 4, 4,
          thresholds=[(None, "green"), (1, "orange")], options=stat_opts("value"), instant=True,
          desc="Prometheus alerts firing now (Alertmanager has the details).")
    b.newline()
    b.add("stat", "Flux objects not Ready",
          [('count(gotk_resource_info{ready="False", suspended!="true"}) or vector(0)', "")], 4, 4,
          thresholds=[(None, "green"), (1, "red")], options=stat_opts("value"), instant=True,
          desc="Kustomizations, HelmReleases, Git and Helm repositories whose Ready condition is False.")
    b.add("stat", "Pods not running",
          [('sum(kube_pod_status_phase{phase=~"Pending|Failed|Unknown"}) or vector(0)', "")], 4, 4,
          thresholds=[(None, "green"), (1, "orange"), (3, "red")], options=stat_opts("value"), instant=True)
    b.add("stat", "Container restarts (24h)",
          [("round(sum(increase(kube_pod_container_status_restarts_total[24h])))", "")], 4, 4,
          thresholds=[(None, "green"), (5, "orange"), (25, "red")], options=stat_opts("value"), instant=True)
    b.add("stat", "Postgres clusters up", [("cnpg_collector_up", "{{cluster}}")], 6, 4, mappings=UP_DOWN,
          thresholds=[(None, "red"), (1, "green")], options=stat_opts(), instant=True)
    b.add("stat", "Oldest backup", [
        ("max(time() - artzima_volsync_last_sync_timestamp_seconds)", "VolSync"),
        ("max(time() - artzima_cnpg_last_successful_backup_timestamp_seconds)", "Postgres"),
    ], 6, 4, unit="s", thresholds=[(None, "green"), (26 * 3600, "orange"), (49 * 3600, "red")],
        options=stat_opts(), instant=True, desc="Age of the stalest off-site backup of each kind.")

    # ---------------------------------------------------------------- Proxmox
    b.row("Proxmox hosts")
    b.add("timeseries", "CPU", [(NODE.format(q='pve_cpu_usage_ratio{id=~"node/.*"}'), "{{host}}")], 8, 7,
          unit="percentunit", minv=0, maxv=1, options=ts_opts())
    b.add("timeseries", "Memory", [(NODE.format(
        q='pve_memory_usage_bytes{id=~"node/.*"} / pve_memory_size_bytes{id=~"node/.*"}'), "{{host}}")], 8, 7,
        unit="percentunit", minv=0, maxv=1, options=ts_opts())
    b.add("bargauge", "Storage used", [(
        'label_replace(pve_disk_usage_bytes{id=~"storage/.*"} / pve_disk_size_bytes{id=~"storage/.*"} > 0, '
        '"store", "$1 ($2)", "id", "storage/([^/]+)/(.+)")', "{{store}}")], 8, 7,
        unit="percentunit", minv=0, maxv=1, thresholds=pct, options=bar_opts(), instant=True,
        desc="Each Proxmox storage per host, root disks (local, local-lvm) and shared ones. "
             "No temperature: pve-exporter has none.")

    # ---------------------------------------------------------------- Kubernetes nodes
    b.row("Kubernetes nodes (Talos VMs)")
    by_node = ' * on (instance) group_left (nodename) node_uname_info'
    b.add("timeseries", "CPU", [(f"instance:node_cpu_utilisation:rate5m{by_node}", "{{nodename}}")], 6, 7,
          unit="percentunit", minv=0, maxv=1, options=ts_opts())
    b.add("timeseries", "Memory", [(f"instance:node_memory_utilisation:ratio{by_node}", "{{nodename}}")], 6, 7,
          unit="percentunit", minv=0, maxv=1, options=ts_opts())
    b.add("bargauge", "System disk (/var)", [(
        f'(1 - node_filesystem_avail_bytes{{mountpoint="/var"}} / node_filesystem_size_bytes{{mountpoint="/var"}})'
        f'{by_node}', "{{nodename}}")], 6, 7, unit="percentunit", minv=0, maxv=1, thresholds=pct,
        options=bar_opts(), instant=True)
    b.add("bargauge", "Pods per node", [("count by (node) (kube_pod_info{node!=\"\"})", "{{node}}")], 6, 7,
          thresholds=[(None, "blue")], options=bar_opts(), instant=True)
    b.add("timeseries", "Pods by phase", [("sum by (phase) (kube_pod_status_phase) > 0", "{{phase}}")], 12, 7,
          options=ts_opts())
    b.add("timeseries", "Network (all nodes)", [
        ("sum(instance:node_network_receive_bytes_excluding_lo:rate5m)", "receive"),
        ("-sum(instance:node_network_transmit_bytes_excluding_lo:rate5m)", "transmit"),
    ], 12, 7, unit="Bps", options=ts_opts())

    # ---------------------------------------------------------------- Flux
    b.row("Flux")
    b.add("table", "Flux objects", [(
        "gotk_resource_info", "")], 24, 8, table=True,
        options={"showHeader": True, "sortBy": [{"displayName": "ready", "desc": False}]},
        transformations=[{"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value": True, "__name__": True, "container": True, "endpoint": True,
                              "instance": True, "job": True, "namespace": True, "pod": True, "service": True,
                              "customresource_group": True, "customresource_version": True},
            "renameByName": {"customresource_kind": "kind", "exported_namespace": "namespace"},
            "indexByName": {"customresource_kind": 0, "exported_namespace": 1, "name": 2, "ready": 3,
                            "suspended": 4, "revision": 5}}}],
        overrides=[{"matcher": {"id": "byName", "options": "ready"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "mappings", "value": [{"type": "value", "options": {
                "True": {"color": "green", "index": 0}, "False": {"color": "red", "index": 1}}}]}]}],
        desc="Every Kustomization, HelmRelease, GitRepository and HelmRepository (kube-state-metrics).")

    # ---------------------------------------------------------------- data & backups
    b.row("Databases and backups")
    b.add("bargauge", "Postgres: last backup age", [(
        "time() - artzima_cnpg_last_successful_backup_timestamp_seconds", "{{exported_namespace}}/{{name}}")],
        8, 7, unit="s", thresholds=[(None, "green"), (26 * 3600, "orange"), (49 * 3600, "red")],
        options=bar_opts(), instant=True, desc="CNPG: last successful backup (barman-cloud to B2).")
    b.add("bargauge", "Postgres: database size", [(
        'sum by (cluster) (cnpg_pg_database_size_bytes{datname!~"template.*|postgres"})', "{{cluster}}")],
        8, 7, unit="bytes", thresholds=[(None, "blue")], options=bar_opts(), instant=True)
    b.add("bargauge", "Postgres: connections", [(
        "sum by (cluster) (cnpg_backends_total)", "{{cluster}}")], 8, 7,
        thresholds=[(None, "blue")], options=bar_opts(), instant=True)
    b.add("bargauge", "VolSync: last backup age", [(
        "time() - artzima_volsync_last_sync_timestamp_seconds", "{{exported_namespace}}/{{name}}")],
        16, 9, unit="s", thresholds=[(None, "green"), (26 * 3600, "orange"), (49 * 3600, "red")],
        options=bar_opts(), instant=True, desc="Each VolSync ReplicationSource (restic to B2).")
    b.add("stat", "VolSync sources paused", [("sum(artzima_volsync_paused) or vector(0)", "")], 8, 9,
          thresholds=[(None, "green"), (1, "orange")], options=stat_opts("value"), instant=True,
          desc="Paused = the disaster-recovery switch is on (docs/backups.md).")

    # ---------------------------------------------------------------- traffic
    b.row("Traffic (Traefik, public apps)")
    req = f'rate(traefik_service_requests_total{{{PUBLIC}}}[$__rate_interval])'
    b.add("timeseries", "Requests per app", [(
        f"sum by (app) ({APP.format(q=req)})", "{{app}}")], 8, 8, unit="reqps", options=ts_opts())
    err = f'rate(traefik_service_requests_total{{{PUBLIC}, code=~"5.."}}[$__rate_interval])'
    b.add("timeseries", "5xx errors per app", [(
        f"sum by (app) ({APP.format(q=err)}) or sum by (app) ({APP.format(q=req)}) * 0", "{{app}}")], 8, 8,
        unit="reqps", options=ts_opts(), desc="Server errors (HTTP 5xx) per second; 0 when there are none.")
    lat = f'rate(traefik_service_request_duration_seconds_bucket{{{PUBLIC}}}[$__rate_interval])'
    b.add("timeseries", "Latency p95 per app", [(
        f"histogram_quantile(0.95, sum by (app, le) ({APP.format(q=lat)}))", "{{app}}")], 8, 8, unit="s",
        options=ts_opts(), desc="Gaps: no requests to that app in the interval.")
    b.add("timeseries", "Cloudflare tunnel requests", [
        ("sum(rate(cloudflared_tunnel_total_requests[$__rate_interval]))", "requests"),
        ("sum(rate(cloudflared_tunnel_request_errors[$__rate_interval]))", "errors"),
    ], 12, 7, unit="reqps", options=ts_opts())
    b.add("timeseries", "Responses by status class", [(
        f'sum by (class) (label_replace({req}, "class", "${{1}}xx", "code", "([0-9]).."))', "{{class}}")],
        12, 7, unit="reqps", options=ts_opts())

    return {
        "uid": "artzima-overview",
        "title": "artzima: overview",
        "description": "Home: hosts, cluster, Flux, databases, backups, traffic. As code: "
                       "scripts/grafana_overview.py",
        "tags": ["artzima"],
        "editable": False,
        "graphTooltip": 1,
        "refresh": "1m",
        "schemaVersion": 41,
        "time": {"from": "now-6h", "to": "now"},
        "timezone": "browser",
        "templating": {"list": []},
        "annotations": {"list": []},
        "links": [{"title": "Dashboards", "type": "dashboards", "tags": ["artzima"], "asDropdown": True,
                   "includeVars": False, "keepTime": True, "icon": "external link"}],
        "panels": b.panels,
    }
