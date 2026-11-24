#!/usr/bin/env python3
"""Set root@pam's password on every PVE host and PBS (inventory groups pve_nodes and pbs_servers) to the
operator's own value in Infisical /operator/PVE_ROOT_PASSWORD, passed in the environment by
`make pve-root-password`. The value goes to `chpasswd` on stdin over SSH; it is never printed,
logged or put on a command line."""
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import labdata  # noqa: E402

pw = os.environ.get("PVE_ROOT_PASSWORD", "")
if not pw:
    sys.exit("PVE_ROOT_PASSWORD is empty: set /operator/PVE_ROOT_PASSWORD in Infisical first")
data = labdata.load()
inv = data["hosts"] if "hosts" in data else data
hosts = sorted((n, h["ip"]) for n, h in inv.items() if {"pve_nodes", "pbs_servers"} & set(h.get("groups", [])))
if not hosts:
    sys.exit("no hosts in the inventory groups pve_nodes/pbs_servers")
failed = 0
for name, ip in hosts:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", f"root@{ip}", "chpasswd"],
                       input=f"root:{pw}\n", text=True, capture_output=True)
    print(f"{name} ({ip}): {'set' if r.returncode == 0 else 'FAILED'}")
    failed += r.returncode != 0
sys.exit(1 if failed else 0)
