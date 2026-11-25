#!/usr/bin/env python3
"""The home dashboard "artzima: overview", as code (scripts/grafana_dashboards.py overview).

Layout, read mostly on a phone (Grafana stacks every panel full-width there):
  Summary      8 tiles of the same size: is anything wrong right now?
  then one section per layer, each the same pattern: a few tiles, a detail table (one row
  per host / node / database / volume), history charts. Rarely needed sections collapsed.
Style: colour only signals state (green ok, orange warning, red bad), as the value's
colour, never a solid background; neutral numbers stay in the text colour.

Every query uses metrics this cluster's Prometheus has (checked with
`grafana_dashboards.py check`). Not here because nothing exports them: host
temperatures (Talos VMs and pve-exporter have none), Proxmox Backup Server and
Backrest (no exporter), Tailscale, the media apps. PBS and the NAS show as Proxmox
sees its `pbs` and `artnas` storages.
"""

from __future__ import annotations

# Traefik's service label for an /apps.yaml route: <ns>-public-<app>-<hash>@kubernetescrd
APP = 'label_replace({q}, "app", "$1", "service", ".*-public-(.+)-[0-9a-f]{{20}}@kubernetescrd")'
PUBLIC = 'service=~".*-public-.*"'
# pve-exporter ids: node/<name>, storage/<node>/<storage>
NODE = 'label_replace({q}, "host", "$1", "id", "node/(.+)")'
# node-exporter series carry the node's name only through node_uname_info
BY_NODE = ('label_replace({q} * on (instance) group_left (nodename) node_uname_info, '
           '"node", "$1", "nodename", "(.+)")')
# CNPG instance metrics have no cluster label: pod <cluster>-<n>
CNPG = 'label_replace({q}, "cluster", "$1", "pod", "(.+)-[0-9]+")'
# kube-state-metrics custom resource metrics: exported_namespace/name of the object
KSM = 'label_replace(label_replace({q}, "{label}", "$1", "name", "{rx}"), "namespace", "$1", "exported_namespace", "(.+)")'

GREEN, ORANGE, RED = "green", "orange", "red"
PCT = [(None, GREEN), (0.75, ORANGE), (0.9, RED)]
AGE = [(None, GREEN), (26 * 3600, ORANGE), (49 * 3600, RED)]  # daily backups: a day late = orange
ZERO_OK = [(None, GREEN), (1, ORANGE)]
NEUTRAL = [(None, "text")]
UP_DOWN = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": RED, "index": 0},
    "1": {"text": "UP", "color": GREEN, "index": 1}}}]


def up_of(sel: str) -> str:
    """"x/y up" in one number, decoded by UP_OF: down*100 + up*10 + total (single-digit
    counts; this lab has at most 4 of anything). Any down -> >= 100 -> red."""
    return (f"(count({sel} == 0) or vector(0)) * 100 + (count({sel} == 1) or vector(0)) * 10"
            f" + count({sel})")


# Grafana maps regexes only on strings (the tile converts the number) and treats a pattern
# without /.../ as a literal. First match wins: 3+ digits = something down = red.
UP_OF = [{"type": "regex", "options": {"pattern": r"/^[1-9]\d*(\d)(\d)$/",
                                       "result": {"text": "$1/$2", "color": RED, "index": 0}}},
         {"type": "regex", "options": {"pattern": r"/^(\d)(\d)$/",
                                       "result": {"text": "$1/$2", "color": GREEN, "index": 1}}}]
UP_OF_T = [(None, GREEN), (100, RED)]
TILE_H = 3


class Board:
    def __init__(self, ds: dict) -> None:
        self.ds = ds
        self.panels: list[dict] = []
        self.target = self.panels  # a collapsed row keeps its panels inside itself
        self.y = 0
        self.x = 0
        self.row_h = 0
        self.next_id = 1

    def row(self, title: str, collapsed: bool = False) -> None:
        self.newline()
        row = {"type": "row", "title": title, "id": self._id(), "collapsed": collapsed,
               "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "panels": []}
        self.panels.append(row)
        self.target = row["panels"] if collapsed else self.panels
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
            custom: dict | None = None, decimals: int | None = None, no_value: str = "") -> None:
        if self.x + w > 24:
            self.newline()
        steps = [{"color": c, "value": v} for v, c in (thresholds or [(None, GREEN)])]
        defaults: dict = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps},
                          "mappings": mappings or [], "color": {"mode": "thresholds"}}
        if kind == "timeseries":
            defaults["color"] = {"mode": "palette-classic"}
            defaults["custom"] = {"lineWidth": 2, "fillOpacity": 12, "gradientMode": "opacity",
                                  "showPoints": "never", "spanNulls": True, "axisLabel": "",
                                  "axisBorderShow": False, **(custom or {})}
        if kind == "table":
            # minWidth: Grafana's default 150 px per column scrolls a 6-column table off a phone
            defaults["custom"] = {"align": "auto", "minWidth": 50, "cellOptions": {"type": "auto"},
                                  **(custom or {})}
        if minv is not None:
            defaults["min"] = minv
        if maxv is not None:
            defaults["max"] = maxv
        if decimals is not None:
            defaults["decimals"] = decimals
        if no_value:
            defaults["noValue"] = no_value
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
        self.target.append(panel)
        self.x += w
        self.row_h = max(self.row_h, h)

    # ------------------------------------------------------------ the three panel kinds
    def tile(self, title: str, expr: str, w: int, *, unit: str = "", thresholds=None, mappings=None,
             spark: bool = False, decimals: int | None = 0, desc: str = "") -> None:
        """A stat tile: one number, coloured by state, optionally a sparkline. All tiles TILE_H."""
        as_text = mappings is UP_OF
        self.add("stat", title, [(expr, "")], w, TILE_H, unit=unit, thresholds=thresholds or NEUTRAL,
                 mappings=mappings, decimals=decimals, desc=desc, instant=not spark,
                 transformations=[{"id": "convertFieldType", "options": {"fields": {}, "conversions": [
                     {"targetField": "Value", "destinationType": "string"}]}}] if as_text else None,
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^(?!Time$)/" if as_text else "",
                                            "values": False},
                          "colorMode": "value", "graphMode": "area" if spark else "none",
                          "textMode": "value", "justifyMode": "center", "orientation": "auto",
                          "wideLayout": True, "showPercentChange": False,
                          "text": {"valueSize": 34}})

    def chart(self, title: str, targets: list[tuple[str, str]], w: int, *, unit: str = "",
              minv: float | None = None, maxv: float | None = None, desc: str = "", h: int = 8,
              custom: dict | None = None) -> None:
        self.add("timeseries", title, targets, w, h, unit=unit, minv=minv, maxv=maxv, desc=desc,
                 custom=custom, options={
                     "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}})

    def table(self, title: str, labels: dict[str, str], cols: list[tuple[str, str, str]], w: int, h: int, *,
              sort: str = "", desc: str = "", no_value: str = "") -> None:
        """One row per label set. labels: {label: header}, the columns shown; cols: (expr,
        header, style), every expr aggregated `by` the same labels (those shown, maybe more).
        Styles: pct, age, up, ok0 (0 green, more orange), paused (coloured text); count,
        bytes, dur, num (plain)."""
        rename = {**labels, **{f"Value #{chr(65 + i)}": head for i, (_, head, _) in enumerate(cols)}}
        if len(cols) == 1:
            rename["Value"] = cols[0][1]
        order = {head: i for i, head in enumerate([*labels.values(), *[c[1] for c in cols]])}
        overrides = []
        for _, head, style in cols:
            props: list[dict] = []
            cell = {"type": "color-text"}
            if style == "pct":  # coloured text, not a bar: bars need width a phone does not have
                props += [{"id": "unit", "value": "percentunit"}, {"id": "min", "value": 0},
                          {"id": "max", "value": 1}, {"id": "decimals", "value": 0}]
                th = PCT
            elif style == "age":
                props += [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 0}]
                th = AGE
            elif style == "up":
                props += [{"id": "mappings", "value": UP_DOWN}]
                th = [(None, RED), (1, GREEN)]
            elif style == "ok0":
                props += [{"id": "decimals", "value": 0}]
                th = ZERO_OK
            elif style == "paused":
                props += [{"id": "mappings", "value": [{"type": "value", "options": {
                    "0": {"text": "no", "index": 0}, "1": {"text": "PAUSED", "color": ORANGE, "index": 1}}}]}]
                th = NEUTRAL
            else:  # neutral numbers
                cell = {"type": "auto"}
                unit = {"bytes": "bytes", "dur": "dtdurations", "count": "none", "num": "none"}[style]
                props += [{"id": "unit", "value": unit}, {"id": "decimals", "value": 0 if style != "bytes" else 1}]
                th = NEUTRAL
            if style in ("up", "paused", "count", "ok0"):  # short values: leave the room to the names
                props += [{"id": "custom.width", "value": 72}]
            props += [{"id": "custom.cellOptions", "value": cell},
                      {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                          {"color": c, "value": v} for v, c in th]}}]
            overrides.append({"matcher": {"id": "byName", "options": head}, "properties": props})
        keep = set(labels) | {f"Value #{chr(65 + i)}" for i in range(len(cols))} | ({"Value"} if len(cols) == 1 else set())
        self.add("table", title, [(e, "") for e, _, _ in cols], w, h, table=True, desc=desc,
                 no_value=no_value, overrides=overrides,
                 options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                          **({"sortBy": [{"displayName": sort, "desc": False}]} if sort else {})},
                 transformations=[
                     {"id": "merge", "options": {}},
                     {"id": "filterFieldsByName", "options": {"include": {"names": sorted(keep)}}},
                     {"id": "organize", "options": {"renameByName": rename, "indexByName": {
                         k: order[v] for k, v in rename.items()}}}])


def build(ds: dict) -> dict:
    b = Board(ds)
    pve_node = 'pve_up{id=~"node/.*"}'
    k8s_ready = 'kube_node_status_condition{condition="Ready",status="true"}'

    # ---------------------------------------------------------------- summary
    # Two rows of four on a desktop (eight in one row truncate the titles), one column on a phone.
    b.row("Summary")
    b.tile("Hosts up", up_of(pve_node), 6, thresholds=UP_OF_T, mappings=UP_OF,
           desc="Proxmox VE hosts up / known (pve-exporter). Details: Proxmox hosts.")
    b.tile("Nodes up", up_of(k8s_ready), 6, thresholds=UP_OF_T, mappings=UP_OF,
           desc="Kubernetes nodes Ready / all nodes. Details: Kubernetes.")
    b.tile("Alerts", 'count(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}) or vector(0)', 6,
           thresholds=ZERO_OK, desc="Prometheus alerts firing now (Alertmanager has the details).")
    b.tile("Flux failing", 'count(gotk_resource_info{ready="False", suspended!="true"}) or vector(0)', 6,
           thresholds=[(None, GREEN), (1, RED)],
           desc="Kustomizations, HelmReleases and sources whose Ready condition is False (section Flux).")
    b.tile("Pods failing", 'sum(kube_pod_status_phase{phase=~"Pending|Failed|Unknown"}) or vector(0)', 6,
           thresholds=[(None, GREEN), (1, ORANGE), (3, RED)], desc="Pods Pending, Failed or Unknown.")
    b.tile("Restarts 24h", "round(sum(increase(kube_pod_container_status_restarts_total[24h])))", 6,
           thresholds=[(None, GREEN), (5, ORANGE), (50, RED)],
           desc="Container restarts in the last 24 hours, all pods (per workload: Kubernetes).")
    b.tile("Oldest backup", "max(time() - artzima_volsync_last_sync_timestamp_seconds"
           " or time() - artzima_cnpg_last_successful_backup_timestamp_seconds)", 6, unit="s",
           thresholds=AGE, desc="Age of the stalest off-site backup, volumes (VolSync) and Postgres "
                                "(barman-cloud), both to B2. Daily: orange after 26 h, red after 49 h.")
    b.tile("Tunnel links", "sum(cloudflared_tunnel_ha_connections) or vector(0)", 6,
           thresholds=[(None, RED), (1, ORANGE), (4, GREEN)],
           desc="Cloudflare tunnel connections to the edge (4 per cloudflared replica). "
                "0 = the public apps are unreachable.")

    # ---------------------------------------------------------------- Proxmox
    b.row("Proxmox hosts")
    b.tile("UPS battery", "min(network_ups_tools_battery_charge)", 8, unit="percent",
           thresholds=[(None, RED), (50, ORANGE), (90, GREEN)])
    b.tile("UPS power", 'max(network_ups_tools_ups_status{flag="OB"}) * 2 + max(network_ups_tools_ups_status{flag="OL"})',
           8, thresholds=[(None, ORANGE), (1, GREEN), (2, RED)], mappings=[{"type": "value", "options": {
               "0": {"text": "Unknown", "index": 0}, "1": {"text": "Mains", "index": 1},
               "2": {"text": "Battery", "index": 2}, "3": {"text": "Battery", "index": 3}}}],
           desc="NUT: on mains (OL) or on battery (OB).")
    b.tile("Guests up", up_of('pve_up{id=~"(qemu|lxc)/.*"}'), 8, thresholds=UP_OF_T, mappings=UP_OF,
           desc="Proxmox VMs and containers running / defined (Talos nodes, Tailscale routers, Jellyfin).")
    node_q = {m: f"sum by (host) ({NODE.format(q=q)})" for m, q in {
        "cpu": 'pve_cpu_usage_ratio{id=~"node/.*"}',
        "mem": 'pve_memory_usage_bytes{id=~"node/.*"} / pve_memory_size_bytes{id=~"node/.*"}',
        "uptime": 'pve_uptime_seconds{id=~"node/.*"}'}.items()}
    b.table("Hosts", {"host": "Host"}, [
        (f"sum by (host) ({NODE.format(q=pve_node)})", "Status", "up"),
        (node_q["cpu"], "CPU", "pct"),
        (node_q["mem"], "Mem", "pct"),
        ('sum by (host) (label_replace(pve_guest_info * on (id) group_left pve_up, "host", "$1", "node", "(.+)"))',
         "VMs", "count"),
        (node_q["uptime"], "Uptime", "dur"),
    ], 24, 6, sort="Host")
    b.chart("CPU", [(NODE.format(q='pve_cpu_usage_ratio{id=~"node/.*"}'), "{{host}}")], 12,
            unit="percentunit", minv=0, maxv=1)
    b.chart("Memory", [(NODE.format(
        q='pve_memory_usage_bytes{id=~"node/.*"} / pve_memory_size_bytes{id=~"node/.*"}'), "{{host}}")], 12,
        unit="percentunit", minv=0, maxv=1)

    # ---------------------------------------------------------------- storage
    b.row("Storage")
    shared = 'pve_disk_usage_bytes{{id=~"storage/.*/{s}"}} / pve_disk_size_bytes{{id=~"storage/.*/{s}"}}'
    b.tile("NAS used", f"max({shared.format(s='artnas')})", 6, unit="percentunit", thresholds=PCT, spark=True,
           decimals=1, desc="The NAS share `artnas` (NFS) as Proxmox sees it.")
    b.tile("NAS free", 'min(pve_disk_size_bytes{id=~"storage/.*/artnas"} - pve_disk_usage_bytes{id=~"storage/.*/artnas"})',
           6, unit="bytes", decimals=1)
    b.tile("PBS used", f"max({shared.format(s='pbs')})", 6, unit="percentunit", thresholds=PCT, decimals=1,
           desc="Proxmox Backup Server datastore, as Proxmox's `pbs` storage (PBS exports no metrics).")
    b.tile("PBS online", 'max(pve_up{id=~"storage/.*/pbs"})', 6, mappings=UP_DOWN,
           thresholds=[(None, RED), (1, GREEN)], desc="Whether Proxmox reaches its PBS storage.")
    local = 'pve_{m}_bytes{{id=~"storage/.*/local.*"}}'
    store = 'label_replace({q}, "host", "$1", "id", "storage/([^/]+)/.+"), "storage", "$1", "id", "storage/[^/]+/(.+)")'
    b.table("Host disks", {"host": "Host", "storage": "Storage"}, [
        (f"sum by (host, storage) (label_replace({store.format(q=local.format(m='disk_usage') + ' / ' + local.format(m='disk_size'))})",
         "Used", "pct"),
    ], 12, 10, sort="Host", desc="Each Proxmox host's own disks (local, local-lvm).")
    # Not per-volume usage: every PVC is on the NAS share, so kubelet reports the share's usage for each.
    b.chart("NAS usage", [
        ('max(pve_disk_usage_bytes{id=~"storage/.*/artnas"})', "used"),
        ('max(pve_disk_size_bytes{id=~"storage/.*/artnas"})', "size"),
    ], 12, unit="bytes", minv=0, h=10, desc="The NAS share `artnas`; all Kubernetes volumes live on it.")

    # ---------------------------------------------------------------- Kubernetes
    b.row("Kubernetes")
    b.tile("CPU", "avg(instance:node_cpu_utilisation:rate5m)", 8, unit="percentunit", thresholds=PCT,
           spark=True, desc="Average over the nodes.")
    b.tile("Memory", "avg(instance:node_memory_utilisation:ratio)", 8, unit="percentunit", thresholds=PCT,
           spark=True, desc="Average over the nodes.")
    b.tile("Pods running", 'sum(kube_pod_status_phase{phase="Running"})', 8)
    var = 'node_filesystem_avail_bytes{mountpoint="/var"} / node_filesystem_size_bytes{mountpoint="/var"}'
    b.table("Nodes", {"node": "Node"}, [
        (f"sum by (node) ({k8s_ready})", "Status", "up"),
        (f"sum by (node) ({BY_NODE.format(q='instance:node_cpu_utilisation:rate5m')})", "CPU", "pct"),
        (f"sum by (node) ({BY_NODE.format(q='instance:node_memory_utilisation:ratio')})", "Mem", "pct"),
        (f"sum by (node) ({BY_NODE.format(q=f'(1 - {var})')})", "Disk /var", "pct"),
        ('count by (node) (kube_pod_info{node!=""} * on (namespace, pod) group_left () '
         '(kube_pod_status_phase{phase="Running"} == 1))', "Pods", "count"),
    ], 24, 5, sort="Node")
    b.chart("CPU per node", [(BY_NODE.format(q="instance:node_cpu_utilisation:rate5m"), "{{node}}")], 12,
            unit="percentunit", minv=0, maxv=1)
    b.chart("Memory per node", [(BY_NODE.format(q="instance:node_memory_utilisation:ratio"), "{{node}}")], 12,
            unit="percentunit", minv=0, maxv=1)
    b.table("Restarts 24h", {"namespace": "Namespace", "workload": "Workload"}, [
        ('sort_desc(sum by (namespace, workload) (label_replace(round(increase('
         'kube_pod_container_status_restarts_total[24h])), "workload", "$1", "pod", '
         '"(.+?)(-[a-z0-9]{6,10})?(-[a-z0-9]{5})?")) > 0)', "Restarts", "ok0"),
    ], 12, 9, no_value="No restarts in 24 h",
        desc="Container restarts in the last 24 hours per workload (pod name without its hashes).")
    b.chart("Network (all nodes)", [
        ("sum(instance:node_network_receive_bytes_excluding_lo:rate5m)", "in"),
        ("-sum(instance:node_network_transmit_bytes_excluding_lo:rate5m)", "out"),
    ], 12, unit="Bps", h=9)

    # ---------------------------------------------------------------- databases & backups
    b.row("Databases and backups")
    b.tile("Postgres up", up_of("cnpg_collector_up"), 6, thresholds=UP_OF_T, mappings=UP_OF,
           desc="CloudNativePG clusters whose instance answers / all.")
    b.tile("Oldest DB backup", "max(time() - artzima_cnpg_last_successful_backup_timestamp_seconds)", 6,
           unit="s", thresholds=AGE, desc="CNPG: last successful backup (barman-cloud to B2).")
    b.tile("Oldest volume backup", "max(time() - artzima_volsync_last_sync_timestamp_seconds)", 6,
           unit="s", thresholds=AGE, desc="VolSync ReplicationSources (restic to B2).")
    b.tile("Backups paused", "sum(artzima_volsync_paused) or vector(0)", 6, thresholds=ZERO_OK,
           desc="Paused VolSync sources: the disaster-recovery switch is on (docs/backups.md).")
    # Queries keep `namespace` (so the merge joins correctly); the table shows only the name,
    # unique here, to leave a phone room for the numbers.
    pg = {"cluster": "Cluster"}
    pg_size = 'cnpg_pg_database_size_bytes{datname!~"template.*|postgres"}'
    b.table("Postgres", pg, [
        ("sum by (namespace, cluster) (cnpg_collector_up)", "Status", "up"),
        (f"sum by (namespace, cluster) ({CNPG.format(q=pg_size)})", "Size", "bytes"),
        (f"sum by (namespace, cluster) ({CNPG.format(q='cnpg_backends_total')})", "Conns", "count"),
        (f"sum by (namespace, cluster) ({KSM.format(q='time() - artzima_cnpg_last_successful_backup_timestamp_seconds', label='cluster', rx='(.+)')})",
         "Backup", "age"),
    ], 24, 6, sort="Cluster", desc="CloudNativePG clusters (one instance each). Size: the app "
                                    "databases; Backup: age of the last successful one.")
    vol = {"source": "Volume"}
    rx = "(.+?)(-b2)?"
    b.table("Volume backups", vol, [
        (f"sum by (namespace, source) ({KSM.format(q='time() - artzima_volsync_last_sync_timestamp_seconds', label='source', rx=rx)})",
         "Last backup", "age"),
        (f"sum by (namespace, source) ({KSM.format(q='artzima_volsync_paused', label='source', rx=rx)})",
         "Paused", "paused"),
    ], 24, 16, sort="Volume", desc="Each VolSync ReplicationSource (restic to B2).")

    # ---------------------------------------------------------------- Flux
    b.row("Flux", collapsed=True)
    b.add("table", "Flux objects", [("gotk_resource_info", "")], 24, 12, table=True,
          options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                   "sortBy": [{"displayName": "Ready", "desc": False}]},
          transformations=[{"id": "organize", "options": {
              "excludeByName": {"Time": True, "Value": True, "__name__": True, "container": True, "endpoint": True,
                                "instance": True, "job": True, "namespace": True, "pod": True, "service": True,
                                "customresource_group": True, "customresource_version": True,
                                "revision": True, "suspended": True},
              "renameByName": {"customresource_kind": "Kind", "exported_namespace": "Namespace", "name": "Name",
                               "ready": "Ready"},
              "indexByName": {"customresource_kind": 0, "exported_namespace": 1, "name": 2, "ready": 3}}}],
          overrides=[{"matcher": {"id": "byName", "options": "Ready"}, "properties": [
              {"id": "custom.cellOptions", "value": {"type": "color-text"}},
              {"id": "mappings", "value": [{"type": "value", "options": {
                  "True": {"text": "ready", "color": GREEN, "index": 0},
                  "False": {"text": "FAILING", "color": RED, "index": 1}}}]}]}],
          desc="Every Kustomization, HelmRelease, GitRepository and HelmRepository (kube-state-metrics).")

    # ---------------------------------------------------------------- traffic
    b.row("Traffic (public apps)", collapsed=True)
    req = f'rate(traefik_service_requests_total{{{PUBLIC}}}[$__rate_interval])'
    b.chart("Requests per app", [(f"sum by (app) ({APP.format(q=req)})", "{{app}}")], 12, unit="reqps")
    b.chart("Responses by status", [(
        f'sum by (class) (label_replace({req}, "class", "${{1}}xx", "code", "([0-9]).."))', "{{class}}")],
        12, unit="reqps")
    err = f'rate(traefik_service_requests_total{{{PUBLIC}, code=~"5.."}}[$__rate_interval])'
    b.chart("5xx errors per app", [(
        f"sum by (app) ({APP.format(q=err)}) or sum by (app) ({APP.format(q=req)}) * 0", "{{app}}")], 12,
        unit="reqps", desc="Server errors (HTTP 5xx) per second; 0 when there are none.")
    lat = f'rate(traefik_service_request_duration_seconds_bucket{{{PUBLIC}}}[$__rate_interval])'
    b.chart("Latency p95 per app", [(
        f"histogram_quantile(0.95, sum by (app, le) ({APP.format(q=lat)}))", "{{app}}")], 12, unit="s",
        desc="Gaps: no requests to that app in the interval.")
    b.chart("Cloudflare tunnel requests", [
        ("sum(rate(cloudflared_tunnel_total_requests[$__rate_interval]))", "requests"),
        ("sum(rate(cloudflared_tunnel_request_errors[$__rate_interval]))", "errors"),
    ], 24, unit="reqps")

    return {
        "uid": "artzima-overview",
        "title": "artzima: overview",
        "description": "Home: hosts, storage, cluster, databases, backups, Flux, traffic. As code: "
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
