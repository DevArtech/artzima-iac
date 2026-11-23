#!/usr/bin/env python3
"""`flux build kustomization --dry-run` every Flux Kustomization of the cluster (offline).

Catches what plain kustomize misses: Flux's own build (components, postBuild
substitution syntax) exactly as kustomize-controller will do it. Part of `make lint`/CI.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
CLUSTER = REPO / "k8s/clusters/artzima"


def main() -> None:
    failed = 0
    for f in sorted(CLUSTER.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if not doc or doc.get("kind") != "Kustomization" or "kustomize.toolkit.fluxcd.io" not in doc.get("apiVersion", ""):
                continue
            name, path = doc["metadata"]["name"], doc["spec"]["path"]
            r = subprocess.run(["flux", "build", "kustomization", name, "--path", str(REPO / path),
                                "--kustomization-file", str(f), "--dry-run"],
                               capture_output=True, text=True, cwd=REPO)
            if r.returncode != 0:
                failed += 1
                print(f"FAIL flux build {name} ({path}):\n{r.stderr.strip()}")
            else:
                print(f"ok   flux build {name} ({path})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
