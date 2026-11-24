#!/usr/bin/env python3
"""Firewalla (lab router): keep the rules of firewalla/baseline.yaml on the box.

    make firewalla-plan     read-only: rule diff + PASS/FAIL of the settings
    make firewalla-apply    the plan, "yes" (scripts/confirm.sh), then apply

    scripts/firewalla.py plan | apply | --lint

How: SSH to pi@<first address of firewalla_vpn_subnet> (through the WireGuard VPN;
key ~/.ssh/id_ed25519, known hosts ~/.ssh/known_hosts_lab), then a short Python
program on the box (python3 -, from stdin) that calls the app's own local API
(http://localhost:8834/v1/encipher/simple: get policies, policy:create, policy:delete)
and reads redis and FireRouter's active config (localhost:8837) read-only.
The box prints only the fields used here (never keys or the whole config).

Managed rules: notes == "artzima-iac:<id>". Only those are ever created, replaced or
deleted; every other rule is listed as info. A changed rule is replaced: the new one is
created first, then the old one deleted (so the lab is never without the rule).
Settings (Router mode, Active Protect, quarantine, ad-block, port forwards, UPnP, WAN
DNS, WireGuard, ingress firewall, no country/category rules) are only READ: PASS/FAIL.

Environment: FIREWALLA_SSH (user@host, default pi@<VPN server address>), SSH_KEY,
KNOWN_HOSTS.
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINE = REPO / "firewalla/baseline.yaml"
NETWORK = REPO / "ansible/group_vars/all/network.yml"
MARK = "artzima-iac:"
KEY = os.environ.get("SSH_KEY", str(pathlib.Path.home() / ".ssh/id_ed25519"))
KNOWN = os.environ.get("KNOWN_HOSTS", str(pathlib.Path.home() / ".ssh/known_hosts_lab"))
# Fields that decide whether a box rule equals a baseline rule ("" / "0" / false = unset).
COMPARE = ("action", "type", "target", "direction", "protocol", "tag", "scope", "guids",
           "localPort", "remotePort", "expire", "cronTime", "disabled", "seq",
           "trafficDirection", "dnsmasq_only", "trust", "upnp")
# Fields the box reports per rule (for the diff and the info lines).
KEEP = COMPARE + ("pid", "notes", "purpose")

# ---------------------------------------------------------------- the box side
# Runs on the Firewalla (python3 3.10, stdlib only). ARGS is substituted below.
BOX = r'''
import json, subprocess, urllib.request
ARGS = json.loads(%s)
API = "http://localhost:8834/v1/encipher/simple?command=%%s&item=%%s"

def api(command, item, body):
    req = urllib.request.Request(API %% (command, item), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def redis(*a):
    return subprocess.run(["redis-cli", *a], capture_output=True, text=True, check=True).stdout.strip()

def jload(s):
    try:
        return json.loads(s)
    except Exception:
        return s or None

out = {}
if ARGS["op"] == "state":
    reply = api("get", "policies", {})
    out["policies"] = [{k: p.get(k) for k in ARGS["keep"] if k in p}
                       for p in reply["data"]["policies"]]
    out["mode"] = redis("get", "mode")
    out["system"] = {k: jload(redis("hget", "policy:system", k)) for k in ARGS["policy_keys"]}
    out["portforwards"] = len((jload(redis("get", "extension.portforward.config")) or {}).get("maps") or [])
    with urllib.request.urlopen("http://localhost:8837/v1/config/active", timeout=30) as r:
        cfg = json.loads(r.read())
    intf = cfg.get("interface", {})
    out["lans"], out["wans"], out["wireguard"] = [], [], []
    for kind in ("bridge", "phy", "vlan"):
        for name, c in (intf.get(kind) or {}).items():
            meta = c.get("meta") or {}
            if meta.get("type") == "lan" and c.get("ipv4"):
                d = (cfg.get("dhcp") or {}).get(name) or {}
                out["lans"].append({"name": name, "uuid": meta.get("uuid"), "ipv4": c.get("ipv4"),
                                    "enabled": c.get("enabled"), "range": d.get("range"),
                                    "dns": d.get("nameservers"),
                                    "policy": {k: jload(redis("hget", "policy:network:" + str(meta.get("uuid")), k))
                                               for k in ARGS["policy_keys"]}})
            if meta.get("type") == "wan":
                try:
                    with open("/home/pi/.router/run/%%s.resolv.conf" %% name) as f:
                        eff = [l.split()[1] for l in f if l.startswith("nameserver ")]
                except OSError:
                    eff = []
                out["wans"].append({"name": name, "enabled": c.get("enabled"), "dhcp": c.get("dhcp"),
                                    "nameservers": c.get("nameservers") or [], "effective": eff})
    for name, c in (intf.get("wireguard") or {}).items():
        out["wireguard"].append({"name": name, "enabled": c.get("enabled"), "ipv4": c.get("ipv4"),
                                 "listenPort": c.get("listenPort"), "peers": len(c.get("peers") or [])})
elif ARGS["op"] == "create":
    out = api("cmd", "policy:create", ARGS["policy"])
elif ARGS["op"] == "delete":
    out = api("cmd", "policy:delete", {"policyID": ARGS["pid"]})
print(json.dumps(out))
'''


def box(target: str, args: dict) -> dict:
    program = BOX % repr(json.dumps(args))
    cmd = ["ssh", "-i", KEY, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN}",
           target, "python3", "-"]
    r = subprocess.run(cmd, input=program, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["?"])[-1]
        raise SystemExit(f"firewalla: {args['op']} on {target} failed: {tail}\n"
                         "  Is the WireGuard VPN up, and is your key in pi's authorized_keys? "
                         "(docs/disaster-recovery.md, Firewalla: replace or factory-reset)")
    return json.loads(r.stdout)


# ---------------------------------------------------------------- the repo side
def values(net: dict, baseline: dict) -> dict:
    home = ipaddress.ip_network(net["home_subnet"])
    lab = ipaddress.ip_network(net["lab_subnet"])
    vpn = ipaddress.ip_network(net["firewalla_vpn_subnet"])
    ws = [u for u in net.get("lab_unmanaged") or [] if u.get("name") == "workstation"]
    if len(ws) != 1:
        raise SystemExit("network.yml: lab_unmanaged needs exactly one entry named workstation")
    pool = net["lab_dhcp_pool"]
    return {
        "home_subnet": str(home),
        "workstation_home_ip": str(home.network_address + int(ws[0]["host_index"])),
        "answer_port": int(baseline["answer_port"]),
        "lab_subnet": str(lab),
        "lab_gateway": str(net["lab_gateway"]),
        "lab_if": f"{net['lab_gateway']}/{lab.prefixlen}",
        "lab_pool": [str(lab.network_address + int(pool[0])), str(lab.network_address + int(pool[1]))],
        "vpn_if": f"{vpn.network_address + 1}/{vpn.prefixlen}",
        "vpn_server": str(vpn.network_address + 1),
    }


def render_rules(baseline: dict, v: dict, lan_uuid: str) -> list[dict]:
    rules, seen = [], set()
    for r in baseline["rules"]:
        rid = r["id"]
        if rid in seen or not rid.replace("-", "").isalnum():
            raise SystemExit(f"baseline: rule id {rid!r} is repeated or not [a-z0-9-]")
        seen.add(rid)
        pol = {}
        for k, val in r["policy"].items():
            if k == "scope":
                if val != "lab":
                    raise SystemExit(f"baseline {rid}: scope must be lab")
                pol["tag"] = [f"intf:{lan_uuid}"]
            else:
                pol[k] = val.format(**v) if isinstance(val, str) else val
        pol["notes"] = MARK + rid
        rules.append({"id": rid, "description": " ".join(str(r.get("description", "")).format(**v).split()),
                      "policy": pol})
    return rules


def norm(value):
    if value in (None, "", "0", 0, False, "false", [], {}):
        return None
    if isinstance(value, str) and value.startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, list):
        return sorted(str(x) for x in value) or None
    return str(value).lower() if isinstance(value, (bool, int, float)) else value


def same(box_rule: dict, policy: dict) -> bool:
    return all(norm(box_rule.get(k)) == norm(policy.get(k)) for k in COMPARE)


def shape(p: dict) -> str:
    bits = [f"{p.get('action', 'block')} {p.get('type')} {p.get('target') or '-'}"]
    for k in ("protocol", "direction", "tag", "scope", "purpose"):
        if p.get(k):
            bits.append(f"{k}={json.dumps(p[k]) if isinstance(p[k], list) else p[k]}")
    if norm(p.get("disabled")):
        bits.append("DISABLED")
    return " ".join(bits)


def diff(rules: list[dict], policies: list[dict]) -> dict:
    """-> {"create": [rule], "replace": [(rule, [old])], "keep": [(rule, pid)],
           "delete": [old], "unmanaged": [p], "conflict": [(rule, p)]}"""
    managed, unmanaged = {}, []
    for p in policies:
        notes = str(p.get("notes") or "").strip()
        if notes.startswith(MARK):
            managed.setdefault(notes[len(MARK):].split()[0] if notes[len(MARK):] else "", []).append(p)
        else:
            unmanaged.append(p)
    plan = {"create": [], "replace": [], "keep": [], "delete": [], "unmanaged": unmanaged, "conflict": []}
    for r in rules:
        olds = managed.pop(r["id"], [])
        match = next((p for p in olds if same(p, r["policy"])), None)
        if match:
            plan["keep"].append((r, match["pid"]))
            plan["delete"] += [p for p in olds if p is not match]  # duplicates
        else:
            # The app refuses a second rule with the same shape (409), notes aside.
            clash = next((p for p in unmanaged if same(p, r["policy"])), None)
            if clash:
                plan["conflict"].append((r, clash))
            elif olds:
                plan["replace"].append((r, olds))
            else:
                plan["create"].append(r)
    for olds in managed.values():
        plan["delete"] += olds
    return plan


def is_active_protect(p):
    return p.get("type") == "category" and p.get("target") == "default_c" and p.get("action") == "block"


def is_ingress_firewall(p):
    return (p.get("direction") == "inbound" and p.get("action") == "block"
            and (p.get("target") in (None, "", "TAG")) and p.get("type") in ("mac", "internet")
            and not (p.get("scope") or p.get("tag") or p.get("guids")))


def on(value) -> bool:
    if isinstance(value, dict):
        return bool(value.get("state"))
    return value is True or value in ("true", "1", 1)


def checks(st: dict, v: dict, want: dict) -> list[tuple[str, bool, str]]:
    pols = st["policies"]
    live = [p for p in pols if not norm(p.get("disabled"))]
    out = []
    out.append(("Router mode", st["mode"] == want["mode"], f"mode={st['mode']}"))
    lans = [lan for lan in st["lans"] if lan["ipv4"] == v["lab_if"]]
    lan = lans[0] if len(lans) == 1 else None
    out.append(("LAN address", lan is not None,
                f"{lan['name']} {lan['ipv4']}" if lan else f"{len(lans)} LANs with {v['lab_if']}"))
    if lan:
        rng = lan.get("range") or {}
        out.append(("LAN DHCP pool", [rng.get("from"), rng.get("to")] == v["lab_pool"],
                    f"{rng.get('from')}-{rng.get('to')} (want {v['lab_pool'][0]}-{v['lab_pool'][1]})"))
        out.append(("LAN DHCP DNS = Firewalla", lan.get("dns") == [v["lab_gateway"]], f"{lan.get('dns')}"))
    ap = [p["pid"] for p in live if is_active_protect(p)]
    out.append(("Active Protect on", bool(ap) == want["active_protect"], f"rule {ap[0]}" if ap else "no rule"))
    ing = [p["pid"] for p in live if is_ingress_firewall(p)]
    out.append(("Ingress firewall on", bool(ing) == want["ingress_firewall"], f"rule {ing[0]}" if ing else "no rule"))
    lanp = lan["policy"] if lan else {}
    q = on(st["system"].get("newDeviceTag")) or on(lanp.get("newDeviceTag"))
    out.append(("New-device quarantine off", q == want["new_device_quarantine"],
                f"system={'on' if on(st['system'].get('newDeviceTag')) else 'off'}, "
                f"lab={'on' if on(lanp.get('newDeviceTag')) else 'off'}"))
    sys_ad, lab_ad = st["system"].get("adblock"), lanp.get("adblock")
    # The lab network's own switch wins (true = on, null = off); unset follows the system.
    ad = on(lab_ad) if lab_ad is not None else on(sys_ad)
    out.append(("Ad-block off (lab)", ad == want["ad_block"],
                f"system={'on' if on(sys_ad) else 'off'}, lab={'unset' if lab_ad is None else lab_ad}"))
    out.append(("No port forwards", st["portforwards"] == want["port_forwards"], f"{st['portforwards']} forwards"))
    up = on(st["system"].get("upnp")) or on(lanp.get("upnp"))
    out.append(("UPnP off", up == want["upnp"], "on" if up else "off"))
    for w in st["wans"] or [{"name": "WAN", "nameservers": [], "effective": []}]:
        ok = w["nameservers"] == want["wan_dns"] and w["effective"][:len(want["wan_dns"])] == want["wan_dns"]
        out.append((f"WAN DNS {w['name']} = {'/'.join(want['wan_dns'])}", ok,
                    f"set={w['nameservers'] or 'from DHCP'}, in use={w['effective'][:2]}"))
    wg = [w for w in st["wireguard"] if w["enabled"] and w["ipv4"] == v["vpn_if"]]
    out.append(("WireGuard VPN server", bool(wg) and wg[0]["listenPort"] == want["wireguard_port"]
                and wg[0]["peers"] > 0,
                f"{wg[0]['ipv4']} port {wg[0]['listenPort']}, {wg[0]['peers']} client(s)" if wg
                else f"no enabled server on {v['vpn_if']}"))
    cty = [p["pid"] for p in live if p.get("type") == "country" and p.get("action") in ("block", "app_block")]
    out.append(("No country blocks", not cty, f"rules {cty}" if cty else "none"))
    cat = [p["pid"] for p in live if p.get("type") == "category" and not is_active_protect(p)
           and p.get("action") in ("block", "app_block")]
    out.append(("No category blocks", not cat, f"rules {cat}" if cat else "none (Active Protect aside)"))
    return out


def report(plan: dict, results: list) -> int:
    print("Rules (managed: notes = artzima-iac:<id>):")
    for r, pid in plan["keep"]:
        print(f"  ok       {r['id']} (rule {pid})")
    for r in plan["create"]:
        print(f"  + create {r['id']}: {json.dumps(r['policy'])}")
    for r, olds in plan["replace"]:
        print(f"  ~ replace {r['id']} (rules {[p['pid'] for p in olds]}): {json.dumps(r['policy'])}")
    for p in plan["delete"]:
        print(f"  - delete rule {p['pid']} ({p.get('notes')}): {shape(p)}")
    for r, p in plan["conflict"]:
        print(f"  ! CONFLICT {r['id']}: unmanaged rule {p['pid']} has the same shape; the app refuses a "
              f"second one. Delete rule {p['pid']} in the app, then apply")
    changes = len(plan["create"]) + len(plan["replace"]) + len(plan["delete"])
    print(f"  {changes} change(s)" + (", nothing to do" if not changes else ""))
    print("Unmanaged rules (info, never touched):")
    for p in plan["unmanaged"]:
        kind = ("Active Protect" if is_active_protect(p) else
                "ingress firewall" if is_ingress_firewall(p) else "")
        print(f"  rule {p['pid']}: {shape(p)}" + (f"  [{kind}]" if kind else ""))
    if not plan["unmanaged"]:
        print("  none")
    print("Settings (read-only; fix a FAIL in the app):")
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failed}/{len(results)} settings pass")
    return changes


def load(st_fn):
    net = yaml.safe_load(NETWORK.read_text())
    baseline = yaml.safe_load(BASELINE.read_text())
    v = values(net, baseline)
    st = st_fn(v)
    lans = [lan for lan in st["lans"] if lan["ipv4"] == v["lab_if"]]
    if len(lans) != 1:
        raise SystemExit(f"firewalla: expected exactly one LAN with address {v['lab_if']} on the box, "
                         f"found {len(lans)}: set up the LAN first (Router mode), then plan again")
    rules = render_rules(baseline, v, lans[0]["uuid"])
    return baseline, v, st, rules


def lint() -> None:
    """Offline: the baseline renders against network.yml, and the diff logic does what it says."""
    def fake(v):
        return {"mode": "router", "system": {}, "portforwards": 0, "wans": [], "wireguard": [],
                "lans": [{"name": "br0", "uuid": "lan-uuid", "ipv4": v["lab_if"], "policy": {}}],
                "policies": []}
    baseline, v, st, rules = load(fake)
    assert rules and all(r["policy"]["tag"] == ["intf:lan-uuid"] for r in rules)
    for r in rules:
        assert not any("{" in str(x) for x in r["policy"].values()), f"unrendered placeholder in {r['id']}"
    a, b = rules[0], rules[1]
    box_a = {**{k: str(x).lower() if isinstance(x, bool) else x for k, x in a["policy"].items()},
             "pid": "1", "disabled": "0", "upnp": False, "trust": False}
    box_b_old = {**b["policy"], "target": "192.0.2.0/30", "pid": "2"}
    stale = {"pid": "3", "type": "dns", "target": "x.invalid", "notes": MARK + "gone"}
    theirs = {"pid": "4", "type": "category", "target": "default_c", "action": "block"}
    plan = diff(rules, [box_a, box_b_old, stale, theirs])
    assert [pid for _, pid in plan["keep"]] == ["1"], plan
    assert [(r["id"], [p["pid"] for p in o]) for r, o in plan["replace"]] == [(b["id"], ["2"])], plan
    assert [p["pid"] for p in plan["delete"]] == ["3"] and [p["pid"] for p in plan["unmanaged"]] == ["4"]
    clash = {**{k: x for k, x in a["policy"].items() if k != "notes"}, "pid": "5"}
    assert diff(rules[:1], [clash])["conflict"], "an identical unmanaged rule must be a conflict"
    assert not diff(rules, [])["delete"] and len(diff(rules, [])["create"]) == len(rules)
    checks(st, v, baseline["settings"])
    print(f"firewalla baseline ok: {len(rules)} rules")


def main() -> int:
    if sys.argv[1:] == ["--lint"]:
        lint()
        return 0
    if sys.argv[1:] not in (["plan"], ["apply"]):
        raise SystemExit(__doc__)
    if os.environ.get("ARTZIMA_PRIVATE_RUNTIME") != "1":
        raise SystemExit("firewalla: needs the private runtime (run it through make)")
    holder = {}

    def state(v):
        holder["target"] = os.environ.get("FIREWALLA_SSH") or f"pi@{v['vpn_server']}"
        return box(holder["target"], {"op": "state", "keep": list(KEEP),
                                      "policy_keys": ["adblock", "newDeviceTag", "upnp"]})
    baseline, v, st, rules = load(state)
    target = holder["target"]
    print(f"Firewalla {target}: {len(st['policies'])} rules on the box, {len(rules)} in firewalla/baseline.yaml")
    plan = diff(rules, st["policies"])
    if sys.argv[1] == "plan":
        report(plan, checks(st, v, baseline["settings"]))
        return 0
    # apply: the make target showed the plan and got "yes"; act on the CURRENT state.
    if plan["conflict"]:
        report(plan, checks(st, v, baseline["settings"]))
        raise SystemExit("firewalla: conflicts above; nothing applied")
    existing = {p["pid"] for p in st["policies"]}
    for r in plan["create"] + [r for r, _ in plan["replace"]]:
        reply = box(target, {"op": "create", "policy": r["policy"]})
        data = reply.get("data") or {}
        if reply.get("code") != 200 or not data.get("pid"):
            raise SystemExit(f"firewalla: create {r['id']} refused: code {reply.get('code')} "
                             f"{reply.get('message') or reply.get('msg') or ''} (later steps not run)")
        if data.get("updated") or data["pid"] in existing:
            raise SystemExit(f"firewalla: create {r['id']} merged into existing rule {data['pid']}; "
                             "stopping before any delete. Run make firewalla-plan")
        print(f"created {r['id']}: rule {data['pid']}")
    olds = plan["delete"] + [p for _, o in plan["replace"] for p in o]
    for p in olds:
        reply = box(target, {"op": "delete", "pid": p["pid"]})
        if reply.get("code") != 200:
            raise SystemExit(f"firewalla: delete rule {p['pid']} refused: code {reply.get('code')}")
        print(f"deleted rule {p['pid']} ({p.get('notes')})")
    if not plan["create"] and not plan["replace"] and not olds:
        print("nothing to do: the box matches firewalla/baseline.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
