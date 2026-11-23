#!/usr/bin/env python3
"""Infisical vs. secrets.manifest.yaml: NAMES ONLY, never values.

    secrets_check.py              (make secrets-check)   which keys are missing in Infisical
    secrets_check.py --create-folders  (make secrets-folders) create missing folders (no values)
    secrets_check.py --lint       (make lint, offline)   manifest == what the code references

Online modes log in with the admin identity (scripts/infisical_exec.py loads
~/.config/artzima/infisical.env + infisical-project.env) and list secret
NAMES with view_secret_value=False: values are never requested, printed or logged.
Exit 1 if an operator-provided key (or a folder) is missing, or a `generated` key
is missing (run `make gen-secrets`); with --operator-only (make preflight) only the
operator keys count. Keys that automation writes later are "pending" (fail with --strict).
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402

REPO = labdata.REPO
MANIFEST = REPO / "secrets.manifest.yaml"


def load_manifest() -> tuple[list[str], dict[str, dict[str, dict]]]:
    m = yaml.safe_load(MANIFEST.read_text())
    secrets = {folder: {k: (v or {}) for k, v in keys.items()} for folder, keys in m["secrets"].items()}
    missing_folders = set(secrets) - set(m["folders"])
    if missing_folders:
        sys.exit(f"{MANIFEST.name}: secrets in folders not listed under `folders`: {sorted(missing_folders)}")
    return m["folders"], secrets


# ---------------------------------------------------------------- offline lint
def referenced() -> set[tuple[str, str]]:
    """(folder, KEY) pairs the repo actually reads or writes."""
    refs: set[tuple[str, str]] = set()

    def add(path: str, where: str) -> None:
        folder, _, key = path.rpartition("/")
        if not folder.startswith("/") or not key:
            sys.exit(f"{where}: cannot parse Infisical reference {path!r}")
        refs.add((folder, key))

    # Kubernetes: ExternalSecret remoteRef keys are absolute (/folder/KEY).
    for f in (REPO / "k8s/secrets").rglob("*.yaml"):
        for m in re.finditer(r"^\s*key:\s*(/\S+)\s*$", f.read_text(), re.M):
            add(m.group(1), str(f))
    # Ansible lookups: path='/x' ... secret_name='Y'
    for f in (REPO / "ansible").rglob("*.yml"):
        if "collections" in f.parts:
            continue
        text = f.read_text()
        for m in re.finditer(r"path='(/[^']*)'[^}]*?secret_name='([A-Z0-9_]+)'", text, re.S):
            refs.add((m.group(1), m.group(2)))
        # proxmox_api role destinations: { path: /x, name: Y, ... }
        for m in re.finditer(r"\{\s*path:\s*(/[^,\s]+),\s*name:\s*([A-Z0-9_]+)", text):
            refs.add((m.group(1), m.group(2)))
    # OpenTofu: folder_path + name blocks, and keys read from the /settings data source.
    for f in (REPO / "tofu").rglob("*.tf"):
        if ".terraform" in f.parts:
            continue
        text = f.read_text()
        for blk in re.finditer(r'folder_path\s*=\s*"(/[^"]*)"\s*\n\s*name\s*=\s*"([A-Z0-9_]+)"', text):
            refs.add((blk.group(1), blk.group(2)))
        for m in re.finditer(r'(?:settings\[|secrets\[|lookup\(local\.settings,\s*)"([A-Z0-9_]+)"', text):
            refs.add(("/settings", m.group(1)))
    # Makefile: --set / --optional VAR=/folder/KEY
    make = "\n".join(line for line in ((REPO / "Makefile").read_text() + "\n" + (REPO / "mk/targets.mk").read_text()).splitlines()
                     if not line.lstrip().startswith("#"))
    for m in re.finditer(r"--(?:set|optional) [A-Za-z_]+=(/\S+)", make):
        add(m.group(1), "Makefile")
    refs.add(("/settings", "PRIVATE_SITE_CONFIG"))
    return refs


GEN_SPEC = re.compile(r"^(hex:\d+|base64:\d+|literal:.+|empty|argon2id-of:/\S+|bcrypt-b64-of:/\S+)$")


def check_specs(secrets: dict) -> list[str]:
    """by:/generate: consistency; returns error messages."""
    errs = []
    listed = {f"{f}/{k}" for f, keys in secrets.items() for k in keys}
    for f, keys in secrets.items():
        for k, meta in keys.items():
            by = meta.get("by")
            if by not in ("operator", "generated", "automation"):
                errs.append(f"{f}/{k}: by must be operator, generated or automation")
            gen = meta.get("generate")
            if by == "generated":
                if not gen or not GEN_SPEC.match(str(gen)):
                    errs.append(f"{f}/{k}: by: generated needs a valid generate: spec (got {gen!r})")
                elif "-of:" in str(gen) and str(gen).split("-of:", 1)[1] not in listed:
                    errs.append(f"{f}/{k}: {gen} refers to a key that is not in the manifest")
            elif gen:
                errs.append(f"{f}/{k}: generate: only goes with by: generated")
    return errs


def lint() -> None:
    folders, secrets = load_manifest()
    listed = {(f, k) for f, keys in secrets.items() for k in keys}
    refs = referenced()
    # Keys used as the source of a generated hash, and logins for people, count as used.
    for f, keys in secrets.items():
        for k, meta in keys.items():
            gen = str(meta.get("generate") or "")
            if "-of:" in gen:
                src = gen.split("-of:", 1)[1]
                refs.add((src.rpartition("/")[0], src.rpartition("/")[2]))
            if meta.get("human"):
                refs.add((f, k))
    spec_errors = check_specs(secrets)
    bad = bool(spec_errors)
    for e in spec_errors:
        print(f"FAIL: {MANIFEST.name}: {e}", file=sys.stderr)
    for f, k in sorted(refs - listed):
        print(f"FAIL: {f}/{k} is used by the repo but missing from {MANIFEST.name}", file=sys.stderr)
        bad = True
    for f, k in sorted(listed - refs):
        print(f"FAIL: {f}/{k} is in {MANIFEST.name} but nothing uses it", file=sys.stderr)
        bad = True
    for f in folders:
        parent = f.rpartition("/")[0] or "/"
        if parent != "/" and parent not in folders:
            print(f"FAIL: folder {f}: parent {parent} not listed", file=sys.stderr)
            bad = True
    if bad:
        sys.exit(1)
    print(f"secrets manifest: {len(listed)} keys in {len(secrets)} folders, all referenced, nothing unlisted")


# ---------------------------------------------------------------- online
def online(create_folders: bool, strict: bool, operator_only: bool = False) -> None:
    from infisical_exec import client, load_local_env

    load_local_env()
    folders, secrets = load_manifest()
    c, cfg = client()
    project, env = cfg["infisical_project_id"], cfg["infisical_env"]
    print(f"Infisical {cfg['infisical_host']}  project {cfg.get('infisical_project_slug')}  environment {env}")
    # The cluster (ESO) only knows the committed values: they must match yours.
    tracked = labdata.load_infisical(env=False)
    drift = [k for k in ("infisical_host", "infisical_project_slug", "infisical_env")
             if str(tracked[k]).rstrip("/") != str(cfg[k]).rstrip("/")]
    if drift:
        print(f"WARNING: ansible/group_vars/all/infisical.yml differs from your local settings in {drift}: "
              "live targets use the private deployment copy; restore/check that configuration")

    def existing_folders(path: str) -> set[str]:
        res = c.folders.list_folders(project_id=project, environment_slug=env, path=path)
        return {f.name for f in res.folders}

    present: set[str] = {"/"}
    for f in folders:  # parents come first in the manifest
        parent, _, name = f.rpartition("/")
        parent = parent or "/"
        if parent not in present:
            continue
        if name in existing_folders(parent):
            present.add(f)
        elif create_folders:
            c.folders.create_folder(name=name, environment_slug=env, project_id=project, path=parent)
            print(f"  created folder {f}")
            present.add(f)

    missing_required, pending, missing_optional, unknown, to_generate = [], [], [], [], []
    missing_folders = [f for f in folders if f not in present]
    for folder, keys in secrets.items():
        if folder not in present:
            names: set[str] = set()
        else:
            res = c.secrets.list_secrets(project_id=project, environment_slug=env, secret_path=folder,
                                         view_secret_value=False, expand_secret_references=False,
                                         include_imports=False)
            names = {s.secretKey for s in res.secrets}
        for k, meta in keys.items():
            if k in names:
                continue
            label = f"{folder}/{k}"
            if meta.get("optional"):
                missing_optional.append(label)
            elif meta.get("by") == "automation":
                pending.append(f"{label}  (written by `{meta.get('after', 'automation')}`)")
            elif meta.get("by") == "generated":
                to_generate.append(f"{label}  ({meta['generate']})")
            else:
                note = "  (may be empty)" if meta.get("empty_ok") else ""
                missing_required.append(label + note)
        unknown += [f"{folder}/{n}" for n in sorted(names - set(keys))]

    def section(title: str, items: list[str]) -> None:
        print(f"\n{title}: {len(items)}")
        for i in items:
            print(f"  - {i}")

    section("missing folders", missing_folders)
    section("MISSING: you provide these (root credentials, settings, Surfshark)", missing_required)
    section("not generated yet: run `make gen-secrets`", to_generate)
    section("pending (automation writes these)", pending)
    section("missing optional", missing_optional)
    section("in Infisical but not used by the repo", unknown)
    total = sum(len(k) for k in secrets.values())
    print(f"\n{total - len(missing_required) - len(pending) - len(missing_optional)}/{total} keys present")
    if missing_folders or missing_required:
        sys.exit(1)
    if not operator_only and (to_generate or (strict and pending)):
        sys.exit(1)


if __name__ == "__main__":
    if "--lint" in sys.argv:
        lint()
    else:
        online("--create-folders" in sys.argv, "--strict" in sys.argv, "--operator-only" in sys.argv)
