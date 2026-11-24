#!/usr/bin/env python3
"""Disaster recovery: print one VolSync ReplicationDestination per backed-up volume.

    make dr-volsync-restore AS_OF=2026-12-01T00:00:00Z > /tmp/restore.yaml   (or without AS_OF = latest)
    kubectl apply -f /tmp/restore.yaml

Reads the same list as the backups (ansible/group_vars/all/backups.yml backup_volumes)
and restores each restic repository INTO the existing PVC of the same name (copyMethod
Direct), with the uid/gid the source mover uses. Stop the apps first (scale to 0).
With --as-of, restic picks the newest snapshot BEFORE that time: use the time just
before the disaster, so no snapshot of an empty rebuilt volume can be chosen.
Prints manifests only: no secrets (the movers use the existing <pvc>-restic Secrets).
Runbook: docs/disaster-recovery.md step 6.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", help="restore the newest snapshot before this RFC 3339 time (e.g. 2026-12-01T00:00:00Z)")
    ap.add_argument("--namespace", help="only volumes in this namespace")
    ap.add_argument("--trigger", default="dr-restore-1", help="manual trigger value (change it to re-run)")
    args = ap.parse_args()
    if args.as_of:
        try:
            datetime.datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        except ValueError:
            sys.exit(f"--as-of {args.as_of!r} is not an RFC 3339 time, e.g. 2026-12-01T00:00:00Z")

    vols = [v for v in labdata.load()["backups"]["backup_volumes"]
            if not args.namespace or v["namespace"] == args.namespace]
    skipped = [v for v in vols if v.get("read_only")]
    vols = [v for v in vols if not v.get("read_only")]
    docs = []
    for v in vols:
        restic = {
            "repository": f"{v['pvc']}-restic",
            "destinationPVC": v["pvc"],
            "copyMethod": "Direct",
            "cacheCapacity": "2Gi",
            "cacheStorageClassName": "nfs-artnas",
            "cacheAccessModes": ["ReadWriteOnce"],
            "moverSecurityContext": {"runAsUser": v["uid"], "runAsGroup": v["gid"], "fsGroup": v["gid"]},
        }
        if args.as_of:
            restic["restoreAsOf"] = args.as_of
        docs.append({
            "apiVersion": "volsync.backube/v1alpha1",
            "kind": "ReplicationDestination",
            "metadata": {"name": f"{v['pvc']}-dr", "namespace": v["namespace"]},
            "spec": {"trigger": {"manual": args.trigger}, "restic": restic},
        })
    print("# DR restore manifests (scripts/dr_volsync_restore.py); stop the apps first.")
    print("# Watch: kubectl get replicationdestination -A  (latestMoverStatus.result: Successful)")
    for v in skipped:
        print(f"# SKIPPED {v['namespace']}/{v['pvc']}: read-only volume, restore it by hand "
              "(docs/disaster-recovery.md)")
    for d in docs:
        print("---")
        print(yaml.safe_dump(d, sort_keys=False), end="")
    print(f"# {len(docs)} volume(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
