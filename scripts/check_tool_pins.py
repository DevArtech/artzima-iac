#!/usr/bin/env python3
"""make lint: the DR installer (scripts/dr-tools.sh) pins the same versions as `make deps`
(scripts/install-tools.sh), so a rebuilt laptop runs exactly what the repo was tested with; and
the printed runbook (docs/disaster-recovery.md) quotes the current versions, endpoint and buckets."""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent


def pins(path: str) -> dict[str, str]:
    text = (REPO / path).read_text()
    return dict(re.findall(r"^([A-Z0-9_]+)=([^\s#]+)", text, re.M))


deps, dr = pins("scripts/install-tools.sh"), pins("scripts/dr-tools.sh")
bad = [f"{k}: install-tools.sh {deps.get(k)} != dr-tools.sh {dr.get(k)}"
       for k in ("TOFU", "TALOS", "FLUX", "KUBECTL", "UV") if deps.get(k) != dr.get(k)]
bad = [f"{b} (update both, and the sha256 table in dr-tools.sh)" for b in bad]

# The runbook is printed: what it quotes must match the repo.
doc = (REPO / "docs/disaster-recovery.md").read_text()
b = yaml.safe_load((REPO / "ansible/group_vars/all/backups.yml").read_text())
must = [f"restic {dr['RESTIC']}", f"docker.io/restic/restic:{dr['RESTIC']}", f"OpenTofu v{dr['TOFU']}",
        f"Client Version: {dr['KUBECTL']}", f"flux: v{dr['FLUX']}", f"infisical version {dr['INFISICAL_CLI']}",
        f"Tag: v{dr['TALOS']}", b["b2_s3_endpoint"], b["b2_bucket_nas"], *b["b2_buckets"].values()]
bad += [f"docs/disaster-recovery.md does not mention {m!r} (keep the runbook in sync)" for m in must if m not in doc]
for x in bad:
    print(f"FAIL: {x}", file=sys.stderr)
if bad:
    sys.exit(1)
print("tool pins: dr-tools.sh matches install-tools.sh; the DR runbook quotes the current versions and buckets")
