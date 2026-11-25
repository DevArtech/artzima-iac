#!/usr/bin/env python3
"""Copy the ENCRYPTED OpenTofu state files to the NAS (`make tofu-state-backup`).

The state lives only on the workstation (docs/disaster-recovery.md P4). This copies every
tofu*/terraform.tfstate(.backup) into a dated folder on the NAS, <nas_backup_lab_state_dir>/<UTC time>/,
over SSH as the NAS login user; Backrest's plan lab-state-daily then sends it to B2 off-site.

- Refuses any state file that is NOT encrypted (no `encrypted_data`): plain state holds
  secrets and must never leave this machine.
- Keeps the newest KEEP copies on the NAS and removes only older dated folders it made itself.
- Runs after every `tofu apply` (mk/targets.mk tofu_apply) and can be run by hand.
- Restore: copy a dated folder's files back to tofu/ (same relative paths); they open with
  TOFU_STATE_PASSPHRASE (Infisical /tofu).
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402

KEEP = 20
STATE_DIR = "/volume1/Artzima/lab-state"  # role nas_backup: nas_backup_lab_state_dir


def main() -> int:
    repo = labdata.REPO
    files = sorted(p for p in (repo / "tofu").rglob("terraform.tfstate*")
                   if p.is_file() and p.name in ("terraform.tfstate", "terraform.tfstate.backup")
                   and ".terraform" not in p.parts)
    if not files:
        print("no OpenTofu state files under tofu/: nothing to back up")
        return 0
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except ValueError:
            sys.exit(f"{f.relative_to(repo)}: not JSON; refusing to copy it")
        if "encrypted_data" not in doc:
            sys.exit(f"{f.relative_to(repo)} is NOT encrypted: refusing to copy plain state off this machine")

    nas = labdata.load()["hosts"]["artnas"]
    target = f"{nas.get('ansible_user') or 'root'}@{nas['ip']}"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(repo)))
    remote = (
        f"set -e; d={STATE_DIR}; test -d \"$d\" && test -w \"$d\" || "
        f"{{ echo \"$d missing or not writable: run make nas-backup first\" >&2; exit 3; }}; "
        f"umask 077; mkdir \"$d/{stamp}\"; tar -x -C \"$d/{stamp}\"; "
        # prune: only our own dated folders, oldest first, keep the newest {KEEP}
        f"ls -1d \"$d\"/[0-9]*T[0-9]*Z 2>/dev/null | sort | head -n -{KEEP} | while read -r old; do rm -rf -- \"$old\"; done; "
        f"ls -1d \"$d\"/[0-9]*T[0-9]*Z | wc -l"
    )
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target, remote],
                       input=buf.getvalue(), capture_output=True)
    if r.returncode != 0:
        sys.exit(f"copy to the NAS failed (exit {r.returncode}): {r.stderr.decode(errors='replace').strip()[:300]}")
    kept = r.stdout.decode().strip().splitlines()[-1] if r.stdout.strip() else "?"
    assert re.fullmatch(r"\d+|\?", kept)
    print(f"OpenTofu state: {len(files)} encrypted file(s) copied to the NAS as {stamp} ({kept} copies kept); "
          "Backrest plan lab-state-daily sends them to B2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
