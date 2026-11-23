#!/usr/bin/env python3
"""Preflight gate: refuse to build unless the world matches the repo.

    make preflight                      everything (after the USB installs)
    make preflight PREFLIGHT_ARGS=--accounts-only   only Infisical (before the hardware)

Checks (PASS/FAIL table; exit 1 on any FAIL):
  1. Infisical: every `by: operator` key of secrets.manifest.yaml exists (names only).
  2. Every Proxmox/PBS host answers on SSH (port 22).
  3. On each host (ssh root@, read-only): the pinned primary NIC has the inventory MAC,
     and a disk with the inventory install_disk_serial exists.
  4. The NAS answers on NFS (2049) and exports artnas_export (showmount, if installed).
Nothing is changed anywhere. SSH: ~/.ssh/id_ed25519, known hosts in ~/.ssh/known_hosts_lab
(new hosts are accepted on first contact and pinned there).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402

results: list[tuple[str, bool, str]] = []
KEY = os.environ.get("SSH_KEY", str(pathlib.Path.home() / ".ssh/id_ed25519"))
KNOWN = os.environ.get("KNOWN_HOSTS", str(pathlib.Path.home() / ".ssh/known_hosts_lab"))


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def tcp(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ssh(ip: str, command: str) -> tuple[int, str]:
    cmd = ["ssh", "-i", KEY, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
           "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN}",
           f"root@{ip}", command]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    return r.returncode, r.stdout + r.stderr


def check_infisical() -> None:
    r = subprocess.run([sys.executable, str(labdata.REPO / "scripts/secrets_check.py"), "--operator-only"],
                       capture_output=True, text=True)
    # Count only the sections that block: missing folders + keys YOU provide.
    blocking, section = [], ""
    for line in r.stdout.splitlines():
        if line and not line.startswith(" "):
            section = line
        elif line.strip().startswith("- ") and section.startswith(("MISSING", "missing folders")):
            blocking.append(line.strip()[2:])
    detail = "all present" if r.returncode == 0 else (
        f"{len(blocking)} missing, e.g. {', '.join(blocking[:3])} (make secrets-check)" if blocking
        else (r.stdout + r.stderr).strip().splitlines()[-1][:120])
    record("Infisical: operator keys present", r.returncode == 0, detail)


def check_hosts(d: dict) -> None:
    nic = d["lab"]["primary_nic_name"]
    probe = (f"cat /sys/class/net/{nic}/address 2>/dev/null || echo NO-NIC; "
             "for b in /sys/block/*; do udevadm info -q property -n /dev/$(basename $b) 2>/dev/null "
             "| grep '^ID_SERIAL='; done")
    for name, h in sorted(d["hosts"].items()):
        if not ({"pve_nodes", "pbs_servers"} & set(h["groups"])):
            continue
        ip = h["ip"]
        if not tcp(ip, 22):
            record(f"{name} ({ip}): SSH reachable", False, "no answer on port 22 (installed? powered on?)")
            continue
        record(f"{name} ({ip}): SSH reachable", True)
        rc, out = ssh(ip, probe)
        if rc != 0:
            record(f"{name}: inventory match", False, "ssh root@ failed (keys from /settings/ADMIN_SSH_KEYS?)")
            continue
        lines = out.splitlines()
        mac = lines[0].strip().lower() if lines else ""
        record(f"{name}: {nic} MAC == inventory", mac == h["mac"].lower(), f"found {mac or '?'}")
        want = str(h.get("install_disk_serial") or "")
        serials = [line.split("=", 1)[1] for line in lines if line.startswith("ID_SERIAL=")]
        ok = bool(want) and not want.upper().startswith("TODO") and any(want in s for s in serials)
        record(f"{name}: install disk serial present", ok,
               f"inventory {want or 'unset'}" + ("" if ok else f"; disks: {', '.join(serials) or 'none'}"))


def check_nas(d: dict) -> None:
    ip, export = d["hosts"]["artnas"]["ip"], d["lab"]["artnas_export"]
    record(f"NAS ({ip}): NFS port 2049", tcp(ip, 2049))
    if shutil.which("showmount"):
        r = subprocess.run(["showmount", "-e", ip], capture_output=True, text=True, timeout=20)
        record(f"NAS exports {export}", export in r.stdout, "UGOS quirk: docs/manual-steps.md §3" if export not in r.stdout else "")
    else:
        record(f"NAS exports {export}", True, "showmount not installed: only the port was checked")


def main() -> None:
    d = labdata.load()
    check_infisical()
    if "--accounts-only" not in sys.argv:
        check_hosts(d)
        check_nas(d)
    width = max(len(n) for n, _, _ in results)
    print("\nPREFLIGHT")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + ("" if not failed else ": NOT ready, fix the FAILs first"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
