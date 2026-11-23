#!/usr/bin/env python3
"""Infisical lost, cluster alive: copy the values the cluster still holds into a NEW Infisical.

    make dr-secrets-from-cluster                       list what can be recovered (names only)
    make dr-secrets-from-cluster DR_ARGS=--apply       write the MISSING keys into Infisical

ESO keeps every synced Kubernetes Secret when Infisical disappears. For each ExternalSecret
this reads its Infisical path/key and the value from its target Secret (directly, or through a
simple `{{ .key }}` template) and creates that key in the Infisical project configured in
~/.config/artzima (the NEW one), only if missing. Values stay in memory: never printed, never
written to disk. Keys the cluster can't give back (/ansible, /operator, /tofu, most of
/settings) are listed with what to do instead. Runbook: docs/disaster-recovery.md P3.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secrets_check import load_manifest  # noqa: E402

SIMPLE = re.compile(r"^\{\{\s*\.(\w+)\s*\}\}$")


def kget(kind: str) -> list[dict]:
    r = subprocess.run(["kubectl", "get", kind, "-A", "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"kubectl get {kind}: {r.stderr.strip()} (KUBECONFIG=tofu/out/kubeconfig?)")
    return json.loads(r.stdout)["items"]


def recover() -> tuple[dict[str, str], list[str]]:
    secrets = {(s["metadata"]["namespace"], s["metadata"]["name"]): s.get("data") or {} for s in kget("secrets")}
    values: dict[str, str] = {}
    problems: list[str] = []
    for es in kget("externalsecrets.external-secrets.io"):
        ns, spec = es["metadata"]["namespace"], es["spec"]
        target = (spec.get("target") or {}).get("name") or es["metadata"]["name"]
        data = secrets.get((ns, target))
        if data is None:
            problems.append(f"{ns}/{target}: Secret missing")
            continue
        tmpl = ((spec.get("target") or {}).get("template") or {}).get("data")
        inverse = {}
        if tmpl:
            for out, t in tmpl.items():
                m = SIMPLE.match(str(t).strip())
                if m:
                    inverse[m.group(1)] = out
        for d in spec.get("data") or []:
            remote = d["remoteRef"]["key"]
            out = inverse.get(d["secretKey"]) if tmpl else d["secretKey"]
            if out is None or out not in data:
                problems.append(f"{remote}: not recoverable from {ns}/{target} (templated)")
                continue
            v = base64.b64decode(data[out]).decode()
            if remote in values and values[remote] != v:
                problems.append(f"{remote}: different values in the cluster, skipped")
                values.pop(remote)
                continue
            values[remote] = v
    return values, problems


def main() -> None:
    from infisical_exec import client, load_local_env

    apply = "--apply" in sys.argv
    load_local_env()
    _, manifest = load_manifest()
    c, cfg = client()
    project, env = cfg["infisical_project_id"], cfg["infisical_env"]
    values, problems = recover()

    existing: dict[str, set[str]] = {}
    for folder in manifest:
        try:
            res = c.secrets.list_secrets(project_id=project, environment_slug=env, secret_path=folder,
                                         view_secret_value=False, expand_secret_references=False,
                                         include_imports=False)
            existing[folder] = {s.secretKey for s in res.secrets}
        except Exception:  # noqa: BLE001
            sys.exit(f"folder {folder} missing in the new Infisical project: run `make secrets-folders` first")

    todo = sorted(k for k in values if k.rpartition("/")[2] not in existing.get(k.rpartition("/")[0], set()))
    print(f"Infisical {cfg['infisical_host']} project {cfg.get('infisical_project_slug')} env {env}")
    print(f"recoverable from the cluster: {len(values)}; missing in Infisical: {len(todo)}")
    for k in todo:
        print(f"  + {k}")
    not_in_cluster = sorted(f"{f}/{k}" for f, keys in manifest.items() for k in keys
                            if f"{f}/{k}" not in values and k not in existing.get(f, set()))
    if not_in_cluster:
        print("\nNOT in the cluster (do these by hand, docs/disaster-recovery.md P3):")
        for k in not_in_cluster:
            print(f"  - {k}")
    for p in problems:
        print(f"  ! {p}")
    if not apply or not todo:
        print("\n(dry run: DR_ARGS=--apply writes the missing keys)" if not apply else "\nnothing to write")
        return
    if os.environ.get("AUTO_APPROVE") != "1":
        if not sys.stdin.isatty() or input(f'\n>>> Write these {len(todo)} keys into Infisical? Type "yes": ').strip() != "yes":
            sys.exit("cancelled")
    for k in todo:
        folder, _, key = k.rpartition("/")
        c.secrets.create_secret_by_name(secret_name=key, project_id=project, environment_slug=env,
                                        secret_path=folder, secret_value=values[k],
                                        secret_comment="recovered from the cluster (dr_secrets_from_cluster.py)")
        print(f"  created {k}")


if __name__ == "__main__":
    main()
