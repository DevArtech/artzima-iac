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

    out = {
        "net": net,
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
    # Every NAS write location must be inside artnas_export (raises otherwise).
    for sub in (lab["artnas_k8s_subdir"], media["media_data_subdir"], lab["artnas_backups_subdir"],
                lab["artnas_paperless_subdir"], backups["artnas_homeassistant_backups_subdir"],
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
                     ("infisical_project_slug", "INFISICAL_PROJECT_SLUG"), ("infisical_env", "INFISICAL_ENV")):
        if env and os.environ.get(var):
            cfg[key] = os.environ[var]
    cfg["infisical_host"] = cfg["infisical_host"].rstrip("/")
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
        if a.get("access", "optional") not in ("optional", "never"):
            raise SystemExit(f"apps.yaml: {a['name']}: access must be optional or never")
        a["host"] = f"{a['name']}.{DOMAIN_VAR}"
    if "domain" in apps:
        raise SystemExit("apps.yaml: `domain` moved to Infisical /settings/PUBLIC_DOMAIN (docs/secrets.md)")
    return apps
