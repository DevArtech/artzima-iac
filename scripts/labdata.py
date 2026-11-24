"""Load the artzima inventory (single source of truth) for non-Ansible tools.

Reads ansible/hosts.yml and ansible/group_vars/all/{network,lab,media,backups,infisical}.yml and
computes every address as cidrhost(lab_subnet, host_index), exactly like
Ansible (group_vars/all/derived.yml) and OpenTofu (tofu/locals.tf).

Requires PyYAML (python3-yaml, or the tools venv).
"""

from __future__ import annotations

import ipaddress
import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
ANSIBLE = REPO / "ansible"


def _walk(group: dict, path: list[str], hosts: dict) -> None:
    gvars = group.get("vars") or {}
    for name, hvars in (group.get("hosts") or {}).items():
        entry = hosts.setdefault(name, {"groups": []})
        merged = {**gvars, **(hvars or {})}
        for k, v in merged.items():
            entry.setdefault(k, v)
        entry["groups"].extend(g for g in path if g not in entry["groups"])
    for child, sub in (group.get("children") or {}).items():
        _walk(sub or {}, path + [child], hosts)


def load() -> dict:
    inv = yaml.safe_load((ANSIBLE / "hosts.yml").read_text())
    net = yaml.safe_load((ANSIBLE / "group_vars/all/network.yml").read_text())
    lab = yaml.safe_load((ANSIBLE / "group_vars/all/lab.yml").read_text())
    media = yaml.safe_load((ANSIBLE / "group_vars/all/media.yml").read_text())
    backups = yaml.safe_load((ANSIBLE / "group_vars/all/backups.yml").read_text())
    infisical = load_infisical(env=False)  # current configuration: examples in source; private values in the runtime copy
    apps = load_apps()

    subnet = ipaddress.ip_network(net["lab_subnet"])
    hosts: dict = {}
    _walk(inv["all"], [], hosts)
    for name, h in hosts.items():
        h["name"] = name
        h["ip"] = str(subnet.network_address + int(h["host_index"]))
        h["fqdn"] = f"{name}.{net['lab_domain']}"
        if ipaddress.ip_address(h["ip"]) not in subnet:
            raise SystemExit(f"{name}: host_index {h['host_index']} outside {subnet}")

    # The PBS one-time format opt-in must be pinned to that host's datastore disk serial.
    for name, h in hosts.items():
        if h.get("pbs_datastore_format_disk") is True:
            want = str(h.get("pbs_datastore_disk_serial") or "").strip()
            got = str(h.get("pbs_datastore_format_confirm_serial") or "").strip()
            if not want or got != want:
                raise SystemExit(f"{name}: pbs_datastore_format_disk is true, but pbs_datastore_format_confirm_serial "
                                 f"({got or 'unset'}) is not pbs_datastore_disk_serial ({want or 'unset'})")
        elif h.get("pbs_datastore_format_disk") not in (None, False):
            raise SystemExit(f"{name}: pbs_datastore_format_disk must be true or false")

    # Documentation-only addresses (network.yml): unmanaged lab devices and the
    # Firewalla DHCP pool. The pool must never hand out a static address.
    vip_index = int(net["k8s_api_vip_index"])
    static = {int(h["host_index"]): name for name, h in hosts.items()}
    static[vip_index] = "k8s API VIP"
    unmanaged = []
    for u in net.get("lab_unmanaged") or []:
        idx = int(u["host_index"])
        if idx in static:
            raise SystemExit(f"network.yml: lab_unmanaged {u['name']} reuses host_index {idx} of {static[idx]}")
        static[idx] = u["name"]
        unmanaged.append({**u, "ip": str(subnet.network_address + idx)})
    pool = net.get("lab_dhcp_pool")
    if pool is not None:
        first, last = (int(x) for x in pool)
        if not 1 < first <= last < subnet.num_addresses - 1:
            raise SystemExit(f"network.yml: lab_dhcp_pool {pool} is not a valid range inside {subnet}")
        if ipaddress.ip_address(net["lab_gateway"]) in {subnet.network_address + i for i in range(first, last + 1)}:
            raise SystemExit("network.yml: lab_dhcp_pool contains lab_gateway")
        clash = sorted(n for i, n in static.items() if first <= i <= last)
        if clash:
            raise SystemExit(f"network.yml: lab_dhcp_pool {pool} overlaps static addresses: {', '.join(clash)}")
        pool = [str(subnet.network_address + first), str(subnet.network_address + last)]

    out = {
        "net": net,
        "unmanaged": unmanaged,
        "dhcp_pool": pool,
        "lab": lab,
        "media": media,
        "backups": backups,
        "subnet": subnet,
        "prefix": subnet.prefixlen,
        "hosts": hosts,
        "k8s_api_vip": str(subnet.network_address + int(net["k8s_api_vip_index"])),
        "apps": apps,
        "infisical": infisical,
    }
    # NAS media / personal files off-site mode (backups.yml, docs/backups.md).
    mode = backups.get("backup_nas_media")
    if mode not in ("restic-on-nas", "existing", "off"):
        raise SystemExit(f"backups.yml: backup_nas_media must be restic-on-nas, existing or off (got {mode!r})")
    for s in backups.get("nas_personal_shares") or []:
        name, path = str(s.get("name", "")), str(s.get("path", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name) or not path.startswith("/") or ".." in path.split("/"):
            raise SystemExit(f"backups.yml: bad nas_personal_shares entry {s!r} (name: lowercase-dashes, path: absolute)")
    ex = backups.get("nas_backup_excludes", [])
    if not isinstance(ex, list) or not all(isinstance(x, str) and x and "\n" not in x for x in ex):
        raise SystemExit("backups.yml: nas_backup_excludes must be a list of restic exclude patterns")
    for s in backups.get("nas_personal_shares") or []:
        if ":" in str(s.get("path", "")):
            raise SystemExit(f"backups.yml: nas_personal_shares path {s.get('path')!r} must not contain ':' (docker volume syntax)")
    for leg in lab.get("artnas_legacy_dirs") or []:
        name = str(leg.get("name", ""))
        if not name or "/" in name or name in ("..", "."):
            raise SystemExit(f"lab.yml: artnas_legacy_dirs entry {leg!r}: name is one folder directly in artnas_export")
    # Every NAS write location must be inside artnas_export (raises otherwise).
    for sub in (lab["artnas_k8s_subdir"], media["media_data_subdir"], lab["artnas_backups_subdir"],
                lab["artnas_paperless_subdir"],
                backups["nas_backrest_subdir"]):
        nas_path(out, sub)
    return out


def nas_path(d: dict, subdir: str) -> str:
    """A path the repo may WRITE on the NAS: always inside artnas_export
    (/volume1/Artzima). The NAS keeps the operator's personal files in other
    shares (README "What survives the wipe"): nothing may ever land there."""
    root = str(d["lab"]["artnas_export"]).rstrip("/")
    sub = str(subdir).strip("/")
    if not sub or str(subdir).startswith("/") or ".." in sub.split("/"):
        raise SystemExit(f"NAS sub-folder {subdir!r} must be a relative path inside {root}")
    return f"{root}/{sub}"


def load_infisical(env: bool = True) -> dict:
    """Which Infisical project/environment holds the secrets (not secrets themselves).
    With env=True, INFISICAL_* environment variables override the tracked defaults."""
    import os
    cfg = yaml.safe_load((ANSIBLE / "group_vars/all/infisical.yml").read_text())
    for key, var in (("infisical_host", "INFISICAL_HOST"), ("infisical_project_id", "INFISICAL_PROJECT_ID"),
                     ("infisical_project_slug", "INFISICAL_PROJECT_SLUG"), ("infisical_env", "INFISICAL_ENV"),
                     ("infisical_k8s_project_id", "INFISICAL_K8S_PROJECT_ID"),
                     ("infisical_k8s_project_slug", "INFISICAL_K8S_PROJECT_SLUG")):
        if env and os.environ.get(var):
            cfg[key] = os.environ[var]
    cfg["infisical_host"] = cfg["infisical_host"].rstrip("/")
    for key in ("infisical_k8s_project_slug", "infisical_k8s_project_id"):
        if not cfg.get(key):
            raise SystemExit(f"infisical.yml: {key} is missing (the cluster's Infisical project, docs/secrets.md)")
    if cfg["infisical_k8s_project_slug"] == cfg["infisical_project_slug"]:
        raise SystemExit("infisical.yml: the k8s project must be a different project than the main one")
    return cfg


# The public domain is NOT in git: it lives in Infisical (/settings/PUBLIC_DOMAIN).
# Generated manifests use this Flux variable, which ESO fills in the cluster.
DOMAIN_VAR = "${public_domain}"


def load_apps() -> dict:
    """/apps.yaml: the single list of public apps (Traefik routes + Cloudflare)."""
    apps = yaml.safe_load((REPO / "apps.yaml").read_text())
    names = [a["name"] for a in apps["apps"]]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SystemExit(f"apps.yaml: duplicate app names {sorted(dupes)}")
    for a in apps["apps"]:
        missing = {"name", "namespace", "service", "port"} - set(a)
        if missing:
            raise SystemExit(f"apps.yaml: {a.get('name')} lacks {sorted(missing)}")
        deny = a.get("deny_paths") or []
        if not isinstance(deny, list) or not all(isinstance(x, str) and x.startswith("/") and "`" not in x for x in deny):
            raise SystemExit(f"apps.yaml: {a['name']}: deny_paths must be a list of paths like /admin")
        setup = a.get("setup_paths") or []
        if not isinstance(setup, list) or not all(
                isinstance(x, str) and re.fullmatch(r"(/[A-Za-z0-9._-]+)+", x) for x in setup):
            raise SystemExit(f"apps.yaml: {a['name']}: setup_paths must be a list of plain path "
                             "prefixes like /Startup or /api/v1/auth/plex")
        if a.get("access", "optional") not in ("optional", "never"):
            raise SystemExit(f"apps.yaml: {a['name']}: access must be optional or never")
        a["host"] = f"{a['name']}.{DOMAIN_VAR}"
    if "domain" in apps:
        raise SystemExit("apps.yaml: `domain` moved to Infisical /settings/PUBLIC_DOMAIN (docs/secrets.md)")
    return apps
