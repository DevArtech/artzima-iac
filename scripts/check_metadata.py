#!/usr/bin/env python3
"""Reject private deployment metadata in the selected publication tree."""
from __future__ import annotations
import ipaddress
import pathlib
import re
import sys

IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
# RFC1918 ranges, expressed numerically so the detector does not match itself.
PRIVATE_NETS = [ipaddress.IPv4Network((167772160, 8)),
                ipaddress.IPv4Network((2886729728, 12)),
                ipaddress.IPv4Network((3232235520, 16))]


def check_tree(root):
    failures = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text()
        except UnicodeError:
            continue
        relative = str(path.relative_to(root))
        if relative == "ansible/hosts.yml":
            if "example_inventory_only: true" not in text:
                failures.append((relative, "inventory is not marked as an example"))
            if any(line.strip() != "lab_roles: []" for line in text.splitlines()
                   if line.strip().startswith("lab_roles:")):
                failures.append((relative, "device-role assignments belong in private configuration"))
        for match in IP.finditer(text):
            try:
                address = ipaddress.IPv4Address(match.group())
            except ipaddress.AddressValueError:
                continue
            if any(address in network for network in PRIVATE_NETS):
                failures.append((relative, "private network address"))
                break
        if any(not m.group().lower().startswith("02:00:00:00:00:")
               for m in MAC.finditer(text)):
            failures.append((relative, "non-example hardware MAC"))
        # Runtime values belong in a private ConfigMap, never in the Flux source.
        if relative == "k8s/clusters/artzima/cluster-vars.yaml":
            failures.append((relative, "private cluster substitution resource"))
        for match in re.finditer(r"(?m)^\s*infisical_project_slug:\s*([^\s#]+)", text):
            if match.group(1).strip("'\"") != "example-project":
                failures.append((relative, "non-example Infisical project slug"))
    return sorted(set(failures))


if __name__ == "__main__":
    findings = check_tree(pathlib.Path(sys.argv[1]))
    for filename, reason in findings:
        print(f"FAIL: {filename}: {reason}", file=sys.stderr)
    if findings:
        raise SystemExit(1)
    print("publication metadata: no private network addresses, real MACs or project slugs detected")
