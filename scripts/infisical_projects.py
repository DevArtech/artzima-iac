#!/usr/bin/env python3
"""Which Infisical project holds a folder: ONE rule for every tool.

secrets.manifest.yaml `projects:` lists folder prefixes per project. A path belongs to
the k8s project if it equals a k8s prefix or lies below it; everything else is main.
    main  /settings /bare-metal /ansible /tofu /operator  (operator, Ansible, OpenTofu)
    k8s   /k8s/**  ONLY what the cluster reads; ESO's identity is Viewer there, nothing in main
OpenTofu and Ansible cannot import this module: `make lint` (secrets_check.py --lint)
checks that their /k8s reads and writes use the k8s project variables.
"""

from __future__ import annotations

import pathlib

import yaml

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "secrets.manifest.yaml"
PROJECTS = ("main", "k8s")


def prefixes(manifest: dict | None = None) -> dict[str, list[str]]:
    m = manifest if manifest is not None else yaml.safe_load(MANIFEST.read_text())
    p = m.get("projects") or {}
    if set(p) != set(PROJECTS):
        raise SystemExit(f"{MANIFEST.name}: `projects:` must list exactly {PROJECTS}")
    k8s = [str(x).rstrip("/") for x in p["k8s"] or []]
    if not k8s or any(not x.startswith("/") or x == "" for x in k8s):
        raise SystemExit(f"{MANIFEST.name}: projects.k8s needs absolute folder prefixes")
    return {"main": [str(x).rstrip("/") for x in p["main"] or []], "k8s": k8s}


def project_of(path: str, manifest: dict | None = None) -> str:
    """'k8s' or 'main' for an absolute folder (or /folder/KEY) path."""
    if not str(path).startswith("/"):
        raise SystemExit(f"Infisical path {path!r} must be absolute")
    path = str(path).rstrip("/") or "/"
    for pre in prefixes(manifest)["k8s"]:
        if path == pre or path.startswith(pre + "/"):
            return "k8s"
    return "main"


def project_id(cfg: dict, path: str, manifest: dict | None = None) -> str:
    """The project id for this path from the Infisical config (labdata.load_infisical)."""
    key = "infisical_k8s_project_id" if project_of(path, manifest) == "k8s" else "infisical_project_id"
    pid = str(cfg.get(key) or "")
    if not pid or "REPLACE" in pid:
        raise SystemExit(f"{key} is not set: add {'INFISICAL_K8S_PROJECT_ID' if key.startswith('infisical_k8s') else 'INFISICAL_PROJECT_ID'}"
                         " to ~/.config/artzima/infisical-project.env (docs/secrets.md)")
    return pid


def project_slug(cfg: dict, which: str) -> str:
    return str(cfg["infisical_k8s_project_slug" if which == "k8s" else "infisical_project_slug"])
