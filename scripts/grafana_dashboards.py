#!/usr/bin/env python3
"""Grafana dashboards as code: k8s/apps/monitoring/dashboards/<folder>/*.json.

    grafana_dashboards.py vendor     download the pinned community dashboards (URL + sha256
                                     below), point them at the provisioned Prometheus
                                     (uid `prometheus`), drop the panels listed in DROP,
                                     write the JSON
    grafana_dashboards.py overview   write the home dashboard "artzima: overview"
    grafana_dashboards.py check      ask Grafana (/api/ds/query) whether every panel of
                                     every file returns data; exit 1 if one does not

Nothing is fetched at runtime: the JSON files are committed and loaded by the Grafana
sidecar (kube-prometheus-stack) from ConfigMaps (k8s/apps/monitoring/kustomization.yaml).
`check` needs a Grafana: GRAFANA_URL (e.g. a port-forward to
svc/kube-prometheus-stack-grafana) and GRAFANA_USER / GRAFANA_PASSWORD in env.
Stdlib only. Downloads are parsed as data (json), never executed.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "k8s/apps/monitoring/dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}  # kube-prometheus-stack's provisioned datasource

# name: (folder, url, sha256). Folders: Artzima (home), Hosts, Platform, Databases.
SOURCES = {
    "node-exporter-full": ("hosts", "https://grafana.com/api/dashboards/1860/revisions/46/download",
                           "e58ddc5dc535d4c901f94da9648bc3e3d5ae7b6f78e98e185bce8b289961cea3"),
    "proxmox": ("hosts", "https://grafana.com/api/dashboards/10347/revisions/5/download",
                "ac7b52531baf0fd6563b8419742d80b738439ad5db6819aa2cbb7b11c6858eff"),
    "traefik": ("platform", "https://raw.githubusercontent.com/traefik/traefik/v3.7.19/contrib/grafana/"
                "traefik-kubernetes.json", "4412c81ce612121dc7c7bc74f413c95be18d778b6e371b85f8c833513192c16c"),
    "flux-cluster": ("platform", "https://raw.githubusercontent.com/fluxcd/flux2-monitoring-example/"
                     "7ab65dc8b90f7a6751d88f18bbb4e1bee33bf334/monitoring/configs/dashboards/cluster.json",
                     "2a52d416ca7fee166c7703524332d3c8e586808c21ff5679a259c9dd744ed309"),
    "flux-control-plane": ("platform", "https://raw.githubusercontent.com/fluxcd/flux2-monitoring-example/"
                           "7ab65dc8b90f7a6751d88f18bbb4e1bee33bf334/monitoring/configs/dashboards/control-plane.json",
                           "604dd55eadc2fc66ee6933d339d51ed475595699fc11c6fec26b91f6831a8e27"),
    "cloudnative-pg": ("databases", "https://raw.githubusercontent.com/cloudnative-pg/grafana-dashboards/"
                       "47e4c51219fdbb3c7ef2c4d91a2d8855e9ca1880/charts/cluster/grafana-dashboard.json",
                       "89290731e516104f57673746fff65916ba2e749c4b942144a03eb53ff9dae7a8"),
}

# Panels (by id) that cannot show data here, with the reason (listed in
# k8s/apps/monitoring/README.md). Checked with `check` against the live cluster.
_TALOS = "Talos VM: no such collector/hardware"
_SINGLE = "every CNPG cluster has one instance: no replication"
DROP: dict[str, dict[int, str]] = {
    "node-exporter-full": {
        21: "no swap on Talos",
        313: "processes collector off", 314: "processes collector off", 315: "processes collector off",
        259: "interrupts collector off", 320: "tcpstat collector off", 340: "tcpstat collector off",
        341: "tcpstat collector off",
        321: _TALOS + " (cpufreq)", 158: _TALOS + " (hwmon)", 300: _TALOS + " (thermal)",
        325: _TALOS + " (hwmon)", 344: _TALOS + " (thermal throttle)", 302: _TALOS + " (edac)",
        345: _TALOS + " (power supply)",
        297: "no systemd on Talos", 298: "no systemd on Talos", 331: "no systemd on Talos",
        332: "no systemd on Talos",
    },
    "cloudnative-pg": {
        590: _SINGLE, 465: _SINGLE, 466: _SINGLE, 467: _SINGLE, 468: _SINGLE,
        14: _SINGLE, 16: _SINGLE, 20: _SINGLE, 59: _SINGLE,
        386: "nodes carry no topology.kubernetes.io/zone label",
        564: "no tablespaces",
    },
}

# Per panel: retitle and/or replace text in its queries (adapt to this cluster).
PATCH: dict[str, dict[int, dict]] = {
    # Talos: "/" is the read-only squashfs image; the writable system disk is /var.
    "node-exporter-full": {
        154: {"title": "/var FS Used", "replace": [('mountpoint="/"', 'mountpoint="/var"')]},
        23: {"replace": [('mountpoint="/"', 'mountpoint="/var"')]},
    },
    # count() of nothing is "No data": show 0 failing sources instead
    "flux-cluster": {
        30: {"replace": [('ready="False"})', 'ready="False"}) or vector(0)')]},
    },
}

# Panels that are empty by design while all is well (the check reports, does not fail).
ALLOW_EMPTY: dict[str, dict[int, str]] = {
    "flux-cluster": {36: "lists suspended Flux objects: none while nothing is suspended"},
    "traefik": {
        3: "lists services failing the 1200 ms SLO (> 0): none while all meet it",
        4: "lists services failing the 300 ms SLO (> 0): none while all meet it",
        18: "lists services answering 5xx (> 0): none while there are no server errors",
    },
}

# Template variables to pre-select (name -> value); everything else: Grafana's first option.
DEFAULTS: dict[str, dict[str, str]] = {
    "flux-control-plane": {"namespace": "flux-system"},
}


def walk(node, fn):
    """Apply fn to every dict in a JSON tree (depth first)."""
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def panels(dash: dict):
    """Every panel, including those inside collapsed rows."""
    for p in dash.get("panels", []):
        yield p
        yield from p.get("panels", [])


def fix_datasource(node: dict) -> None:
    ds = node.get("datasource")
    if ds is None:
        return
    uid = ds.get("uid", "") if isinstance(ds, dict) else str(ds)
    kind = ds.get("type", "") if isinstance(ds, dict) else ""
    if kind == "__expr__" or uid == "__expr__":  # server-side expressions (upstream: ${DS_EXPRESSION})
        node["datasource"] = {"type": "__expr__", "uid": "__expr__"}
        return
    if uid in ("-- Grafana --", "grafana") or kind in ("datasource", "grafana"):
        return
    if "$" in uid or kind in ("", "prometheus") or uid.lower().startswith(("prometheus", "ds_")):
        node["datasource"] = dict(DS)


def transform(name: str, dash: dict) -> dict:
    dash = copy.deepcopy(dash)
    for k in ("__inputs", "__requires", "__elements", "id", "iteration"):
        dash.pop(k, None)
    templ = dash.setdefault("templating", {}).setdefault("list", [])
    templ[:] = [v for v in templ if v.get("type") != "datasource"]  # one Prometheus: no picker
    walk(dash, fix_datasource)
    for v in templ:
        if v.get("type") == "query":
            v["current"] = {}
            if v.get("refresh") in (0, None):
                v["refresh"] = 1
        if v["name"] in DEFAULTS.get(name, {}):
            val = DEFAULTS[name][v["name"]]
            v["current"] = {"selected": True, "text": val, "value": val}
    drop = DROP.get(name, {})
    patch = PATCH.get(name, {})
    unknown = (set(drop) | set(patch)) - {p.get("id") for p in panels(dash)}
    if unknown:
        sys.exit(f"{name}: DROP/PATCH ids not in the dashboard: {sorted(unknown)}")
    for p in panels(dash):
        change = patch.get(p.get("id"))
        if not change:
            continue
        p["title"] = change.get("title", p.get("title"))
        for old, new in change.get("replace", []):
            hits = [t for t in p.get("targets", []) if old in t.get("expr", "")]
            if not hits:
                sys.exit(f"{name}: panel {p['id']}: {old!r} not in its queries")
            for t in hits:
                t["expr"] = t["expr"].replace(old, new)
    def keep(ps):
        out = []
        for p in ps:
            if p.get("id") in drop:
                continue
            if "panels" in p:
                p["panels"] = keep(p["panels"])
            out.append(p)
        return out
    dash["panels"] = keep(dash.get("panels", []))
    # rows left without panels (collapsed rows carry theirs, open rows own what follows)
    ps = dash["panels"]
    dash["panels"] = [p for i, p in enumerate(ps) if not (
        p.get("type") == "row" and not p.get("panels") and (
            p.get("collapsed") or i + 1 == len(ps) or ps[i + 1].get("type") == "row"))]
    dash["editable"] = False
    tags = set(dash.get("tags") or []) | {"artzima", "vendored"}
    dash["tags"] = sorted(tags)
    return dash


def write(folder: str, name: str, dash: dict) -> None:
    path = OUT / folder / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dash, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(REPO)}")


def vendor() -> None:
    for name, (folder, url, sha) in SOURCES.items():
        req = urllib.request.Request(url, headers={"User-Agent": "grafana-dashboards-vendor"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
        got = hashlib.sha256(raw).hexdigest()
        if got != sha:
            sys.exit(f"{name}: sha256 {got} != pinned {sha} ({url})")
        write(folder, name, transform(name, json.loads(raw)))


# ------------------------------------------------------------------ overview
def overview() -> None:
    from grafana_overview import build  # noqa: PLC0415 (sibling module, kept apart for size)
    write("artzima", "artzima-overview", build(DS))


# ------------------------------------------------------------------ check
class Grafana:
    def __init__(self) -> None:
        self.url = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
        cred = f"{os.environ['GRAFANA_USER']}:{os.environ['GRAFANA_PASSWORD']}"
        self.auth = "Basic " + base64.b64encode(cred.encode()).decode()

    def call(self, path: str, body: dict | None = None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read()[:300]!r}"}

    def series_labels(self, match: str, label: str) -> list[str]:
        params = {"start": str(int(time.time()) - 3 * 3600)}
        if match:
            params["match[]"] = match
        # The datasource resource proxy: Prometheus' own label API, through Grafana.
        path = f"/api/datasources/uid/{DS['uid']}/resources/api/v1/label/{label}/values"
        return sorted(self.call(f"{path}?{urllib.parse.urlencode(params)}").get("data") or [])

    def series(self, expr: str) -> list[str]:
        """What a Grafana query variable sees for an expression: one string per series."""
        res = self.query([{"refId": "A", "datasource": DS, "expr": expr, "instant": True}])
        out = []
        for fr in res.get("results", {}).get("A", {}).get("frames", []):
            for f in fr["schema"]["fields"]:
                lab = dict(f.get("labels") or {})
                if f.get("type") == "time" or not lab:
                    continue
                metric = lab.pop("__name__", "")
                out.append(metric + "{" + ",".join(f'{k}="{v}"' for k, v in sorted(lab.items())) + "}")
        return out

    def query(self, targets: list[dict], hours: int = 1) -> dict:
        return self.call("/api/ds/query", {"from": f"now-{hours}h", "to": "now", "queries": targets})


def fmt(values: list[str], all_value: str | None, multi: bool) -> str:
    if all_value is not None:
        return all_value
    if not multi or len(values) == 1:
        return values[0] if values else ""
    # Grafana's Prometheus multi-value format: (a|b), each regex metacharacter as \\x
    # (a PromQL string, so the regex sees \x)
    special = set("\\^$.|?*+()[]{}")
    return "(" + "|".join("".join("\\\\" + c if c in special else c for c in v) for v in values) + ")"


def substitute(expr: str, vars_: dict[str, str]) -> str:
    expr = re.sub(r"\$__auto_interval_\w+", "5m", expr)  # interval variables set to "auto"

    def rep(m):
        name = m.group(1) or m.group(2) or m.group(3)
        return vars_.get(name, m.group(0))
    for _ in range(3):  # values may hold variables themselves (allValue "$cluster-...")
        expr = re.sub(r"\$\{(\w+)(?::\w+)?\}|\[\[(\w+)\]\]|\$(\w+)", rep, expr)
    return re.sub(r"\$__auto_interval_\w+", "5m", expr)


def resolve_vars(g: Grafana, dash: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in dash.get("templating", {}).get("list", []):
        t, name = v.get("type"), v["name"]
        cur = (v.get("current") or {}).get("value")
        if t in ("custom", "interval"):
            opts = [o.strip() for o in str(v.get("query", "")).split(",") if o.strip()]
            out[name] = cur if isinstance(cur, str) and cur and cur != "$__all" else (opts[0] if opts else "")
            if v.get("includeAll") and (not cur or cur == ["$__all"] or cur == "$__all"):
                out[name] = fmt(opts, v.get("allValue") or None, True)
            continue
        if t in ("constant", "textbox"):
            out[name] = str(v.get("query", "") or cur or "")
            continue
        if t != "query":
            continue
        q = v.get("query")
        q = q.get("query", "") if isinstance(q, dict) else str(q or v.get("definition", ""))
        q = substitute(q, out)
        values: list[str] = []
        m = re.fullmatch(r"\s*label_values\((?:(.*),)?\s*(\w+)\s*\)\s*", q, re.S)
        if m:
            values = g.series_labels((m.group(1) or "").strip(), m.group(2))
        elif q.strip():
            # query_result(expr) or a bare series selector: one "name{k="v",...}" string per series
            expr = q.strip()
            expr = expr[len("query_result("):-1] if expr.startswith("query_result(") else expr
            values = g.series(expr)
        rx = v.get("regex") or ""
        if rx:
            rx = rx[1:rx.rindex("/")] if rx.startswith("/") else rx
            rx = rx.replace("(?<", "(?P<")  # JavaScript named groups
            picked = []
            for val in values:
                mm = re.search(rx, val)
                if mm:
                    gd = mm.groupdict()
                    picked.append(gd.get("value") or gd.get("text") or (mm.group(1) if mm.groups() else val))
            values = list(dict.fromkeys(picked))
        if v.get("includeAll") and not (isinstance(cur, str) and cur and cur != "$__all"):
            out[name] = fmt(values, v.get("allValue") or None, True)
        elif isinstance(cur, str) and cur and cur in values:
            out[name] = cur
        else:
            out[name] = values[0] if values else ""
        if not values:
            print(f"  variable ${name}: no values ({q})")
    return out


def has_data(res: dict, ref_ids: list[str]) -> bool:
    for ref in ref_ids:
        r = res.get("results", {}).get(ref, {})
        for fr in r.get("frames", []):
            vals = fr.get("data", {}).get("values", [])
            if len(vals) > 1 and any(len(c) for c in vals[1:]):
                return True
            if len(vals) == 1 and vals[0]:
                return True
    return False


def check(files: list[pathlib.Path]) -> int:
    g = Grafana()
    empty_total = 0
    for path in files:
        dash = json.loads(path.read_text())
        print(f"{path.relative_to(REPO)}: {dash.get('title')}")
        vars_ = resolve_vars(g, dash)
        allowed = ALLOW_EMPTY.get(path.stem, {})
        n = 0
        for p in panels(dash):
            # Grafana skips Prometheus queries without an expression, and hidden ones unless
            # a server-side expression (math) refers to them; only visible results count.
            targets = [t for t in p.get("targets", []) if not ("expr" in t and not str(t["expr"]).strip())]
            has_expr = any((t.get("datasource") or {}).get("type") == "__expr__" for t in targets)
            visible = [t.get("refId", "A") for t in targets if not t.get("hide")]
            targets = [t for t in targets if has_expr or not t.get("hide")]
            if p.get("type") in ("row", "text") or not targets:
                continue
            n += 1
            qs = []
            for t in targets:
                q = {k: v for k, v in t.items() if k not in ("datasource",)}
                q["datasource"] = t.get("datasource") or p.get("datasource") or DS
                if isinstance(q["datasource"], dict) and q["datasource"].get("type") != "__expr__":
                    q["datasource"] = DS
                if "expr" in q:
                    q["expr"] = substitute(q["expr"], vars_)
                if "interval" in q and isinstance(q["interval"], str):
                    q["interval"] = substitute(q["interval"], vars_)
                q.setdefault("refId", "A")
                qs.append(q)
            res = g.query(qs, hours=3)
            if "error" in res or not has_data(res, visible):
                if p.get("id") in allowed and "error" not in res:
                    print(f"  empty by design: id={p.get('id')} {p.get('title')!r}: {allowed[p['id']]}")
                    continue
                empty_total += 1
                errs = [r.get("error") for r in res.get("results", {}).values() if r.get("error")]
                print(f"  EMPTY id={p.get('id')} [{p.get('type')}] {p.get('title')!r}"
                      f"{'  ' + res['error'] if 'error' in res else ''}{'  ' + errs[0][:200] if errs else ''}")
                for q in qs:
                    if q.get("expr"):
                        print(f"      {q['expr'][:220]}")
        print(f"  {n} panels with queries")
    print(f"{empty_total} panel(s) without data")
    return 1 if empty_total else 0


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "vendor":
        vendor()
    elif cmd == "overview":
        overview()
    elif cmd == "check":
        files = [pathlib.Path(a).resolve() for a in sys.argv[2:]] or sorted(OUT.glob("*/*.json"))
        sys.exit(check(files))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    main()
