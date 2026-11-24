#!/usr/bin/env python3
"""Infisical vs. secrets.manifest.yaml: NAMES ONLY, never values.

    secrets_check.py              (make secrets-check)   which keys are missing in Infisical
    secrets_check.py --create-folders  (make secrets-folders) create missing folders (no values)
    secrets_check.py --lint       (make lint, offline)   manifest == what the code references

Two Infisical projects (secrets.manifest.yaml `projects:`): main (operator, Ansible,
OpenTofu) and k8s (only what the cluster reads). Every folder is checked in its own
project. Online modes log in with the admin identity (scripts/infisical_exec.py loads
~/.config/artzima/infisical.env + infisical-project.env) and list secret NAMES with
view_secret_value=False: values are never printed or logged. Only `mirror_of` keys are
read, to compare a mirror with its source by SHA-256 in memory (never printed).
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
import infisical_projects  # noqa: E402

REPO = labdata.REPO
MANIFEST = REPO / "secrets.manifest.yaml"


def load_manifest() -> tuple[list[str], dict[str, dict[str, dict]]]:
    m = yaml.safe_load(MANIFEST.read_text())
    infisical_projects.prefixes(m)  # `projects:` must be valid
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
        # for_each over a local map of KEY = {...} with name = each.key (e.g. tofu/backblaze k8s_backup)
        for blk in re.finditer(r'for_each\s*=\s*local\.(\w+)\s*\n(?:[^\n]*\n){0,3}?\s*folder_path\s*=\s*"(/[^"]*)"'
                               r'\s*\n\s*name\s*=\s*each\.key', text):
            m = re.search(r'\n\s*' + blk.group(1) + r'\s*=\s*\{(.*?)\n\s*\}\n', text, re.S)
            if not m:
                sys.exit(f"{f}: for_each map local.{blk.group(1)} not found")
            for key in re.findall(r'^\s*([A-Z0-9_]+)\s*=\s*\{', m.group(1), re.M):
                refs.add((blk.group(2), key))
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
            src = meta.get("mirror_of")
            if src is not None:
                if by != "automation":
                    errs.append(f"{f}/{k}: mirror_of needs by: automation")
                elif str(src) not in listed:
                    errs.append(f"{f}/{k}: mirror_of {src} is not in the manifest")
                elif infisical_projects.project_of(str(src)) == infisical_projects.project_of(f):
                    errs.append(f"{f}/{k}: a mirror must live in the other project than {src}")
    return errs


def project_wiring_errors() -> list[str]:
    """OpenTofu and Ansible can't import infisical_projects: check their /k8s accesses
    use the k8s project, and everything else the main project."""
    errs = []
    for f in sorted((REPO / "tofu").rglob("*.tf")):
        if ".terraform" in f.parts:
            continue
        text = f.read_text()
        for blk in re.finditer(r'(resource|ephemeral|data)\s+"infisical_secrets?"\s+"(\w+)"\s*\{(.*?)\n\}', text, re.S):
            body = blk.group(3)
            path = re.search(r'folder_path\s*=\s*"([^"]+)"', body)
            ws = re.search(r'workspace_id\s*=\s*([^\n]+)', body)
            if not path or not ws:
                continue
            want = infisical_projects.project_of(path.group(1))
            uses_k8s = "k8s_project_id" in ws.group(1)
            if (want == "k8s") != uses_k8s:
                errs.append(f"{f.relative_to(REPO)}: {blk.group(1)} {blk.group(2)} ({path.group(1)}) must use the "
                            f"{'k8s' if want == 'k8s' else 'main'} project id")
    for f in sorted((REPO / "ansible").rglob("*.yml")):
        if "collections" in f.parts:
            continue
        for m in re.finditer(r"project_id=(\w+),[^}]*?path='(/[^']*)'", f.read_text(), re.S):
            want = infisical_projects.project_of(m.group(2))
            uses_k8s = m.group(1) == "artzima_infisical_k8s_project"
            if (want == "k8s") != uses_k8s:
                errs.append(f"{f.relative_to(REPO)}: lookup of {m.group(2)} must use "
                            f"{'artzima_infisical_k8s_project' if want == 'k8s' else 'artzima_infisical_project'}")
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
            if meta.get("mirror_of"):
                src = str(meta["mirror_of"])
                refs.add((src.rpartition("/")[0], src.rpartition("/")[2]))
    spec_errors = check_specs(secrets) + project_wiring_errors()
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
def _digest(value) -> str:
    import hashlib
    return hashlib.sha256(str(value or "").encode()).hexdigest()


def check_projects(c, cfg: dict, folders: list[str], secrets: dict, create_folders: bool = False) -> dict:
    """Pure logic over an Infisical client (tests use a fake). Returns the report lists."""
    env = cfg["infisical_env"]
    pid = {w: infisical_projects.project_id(cfg, "/k8s" if w == "k8s" else "/settings") for w in ("main", "k8s")}

    def project(path: str) -> str:
        return pid[infisical_projects.project_of(path)]

    def existing_folders(proj: str, path: str) -> set[str]:
        res = c.folders.list_folders(project_id=proj, environment_slug=env, path=path)
        return {f.name for f in res.folders}

    def names(proj: str, folder: str) -> set[str]:
        res = c.secrets.list_secrets(project_id=proj, environment_slug=env, secret_path=folder,
                                     view_secret_value=False, expand_secret_references=False,
                                     include_imports=False)
        return {s.secretKey for s in res.secrets}

    def value(path: str):
        folder, _, key = path.rpartition("/")
        s = c.secrets.get_secret_by_name(secret_name=key, project_id=project(folder), environment_slug=env,
                                         secret_path=folder, view_secret_value=True,
                                         expand_secret_references=False, include_imports=False)
        return s.secretValue

    r = {k: [] for k in ("created_folders", "missing_folders", "missing_required", "to_generate", "pending",
                         "missing_optional", "unknown", "mirror_drift", "old_main_copies")}
    present: set[str] = set()
    for f in folders:  # parents come first in the manifest
        parent, _, name = f.rpartition("/")
        parent = parent or "/"
        if parent != "/" and parent not in present:
            continue
        if name in existing_folders(project(f), parent):
            present.add(f)
        elif create_folders:
            c.folders.create_folder(name=name, environment_slug=env, project_id=project(f), path=parent)
            r["created_folders"].append(f"{f}  ({infisical_projects.project_of(f)})")
            present.add(f)
    r["missing_folders"] = [f"{f}  ({infisical_projects.project_of(f)} project)" for f in folders if f not in present]

    found: dict[str, set[str]] = {}
    for folder, keys in secrets.items():
        found[folder] = names(project(folder), folder) if folder in present else set()
        for k, meta in keys.items():
            label = f"{folder}/{k}"
            if k in found[folder]:
                continue
            if meta.get("optional"):
                r["missing_optional"].append(label)
            elif meta.get("by") == "automation":
                r["pending"].append(f"{label}  (written by `{meta.get('after', 'automation')}`)")
            elif meta.get("by") == "generated":
                r["to_generate"].append(f"{label}  ({meta['generate']})")
            else:
                r["missing_required"].append(label + ("  (may be empty)" if meta.get("empty_ok") else ""))
        r["unknown"] += [f"{folder}/{n}" for n in sorted(found[folder] - set(keys))]

    # Mirrors: equal to their source (hash compared in memory; values never printed).
    for folder, keys in secrets.items():
        for k, meta in keys.items():
            src = meta.get("mirror_of")
            if not src or k not in found.get(folder, set()):
                continue
            sf, _, sk = str(src).rpartition("/")
            if sk not in found.get(sf, set()):
                continue
            if _digest(value(f"{folder}/{k}")) != _digest(value(str(src))):
                r["mirror_drift"].append(f"{folder}/{k} differs from {src}: run `make secrets-migrate-k8s MIGRATE_ARGS=--apply`")

    # Old copies of k8s folders left in the MAIN project (before `secrets-migrate-k8s --delete-old`).
    for f in folders:
        if infisical_projects.project_of(f) != "k8s":
            continue
        parent, _, name = f.rpartition("/")
        try:
            if name in existing_folders(pid["main"], parent or "/"):
                r["old_main_copies"] += [f"{f}/{n}" for n in sorted(names(pid["main"], f))]
        except Exception:  # noqa: BLE001 (parent folder absent in main: nothing left there)
            continue
    return r


def online(create_folders: bool, strict: bool, operator_only: bool = False) -> None:
    from infisical_exec import client, load_local_env

    load_local_env()
    folders, secrets = load_manifest()
    c, cfg = client()
    print(f"Infisical {cfg['infisical_host']}  environment {cfg['infisical_env']}  projects: "
          f"main {cfg.get('infisical_project_slug')}, k8s {cfg.get('infisical_k8s_project_slug')}")
    # The cluster (ESO) only knows the committed values: they must match yours.
    tracked = labdata.load_infisical(env=False)
    drift = [k for k in ("infisical_host", "infisical_project_slug", "infisical_k8s_project_slug", "infisical_env")
             if str(tracked[k]).rstrip("/") != str(cfg[k]).rstrip("/")]
    if drift:
        print(f"WARNING: ansible/group_vars/all/infisical.yml differs from your local settings in {drift}: "
              "live targets use the private deployment copy; restore/check that configuration")
    r = check_projects(c, cfg, folders, secrets, create_folders)

    def section(title: str, items: list[str]) -> None:
        print(f"\n{title}: {len(items)}")
        for i in items:
            print(f"  - {i}")

    if create_folders:
        section("created folders", r["created_folders"])
    section("missing folders", r["missing_folders"])
    section("MISSING: you provide these (root credentials, settings, Surfshark)", r["missing_required"])
    section("not generated yet: run `make gen-secrets`", r["to_generate"])
    section("pending (automation writes these)", r["pending"])
    section("missing optional", r["missing_optional"])
    section("MIRROR DIFFERS from its source", r["mirror_drift"])
    section("in Infisical but not used by the repo", r["unknown"])
    if r["old_main_copies"]:
        section("old /k8s copies still in the MAIN project (remove after migration: "
                "make secrets-migrate-k8s MIGRATE_ARGS=--delete-old)", r["old_main_copies"])
    total = sum(len(k) for k in secrets.values())
    print(f"\n{total - len(r['missing_required']) - len(r['pending']) - len(r['missing_optional'])}/{total} keys present")
    if r["missing_folders"] or r["missing_required"] or r["mirror_drift"]:
        sys.exit(1)
    if not operator_only and (r["to_generate"] or (strict and r["pending"])):
        sys.exit(1)


if __name__ == "__main__":
    if "--lint" in sys.argv:
        lint()
    else:
        online("--create-folders" in sys.argv, "--strict" in sys.argv, "--operator-only" in sys.argv)
