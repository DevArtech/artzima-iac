"""Build-time check (images/skyjo/Dockerfile): upstream's own default lookup
(web/backend/checkpoints.py: newest checkpoint_iter_*.pt) must find a REAL
checkpoint in both runs/ folders of the image. Exits non-zero otherwise."""

import sys
from pathlib import Path

from web.backend.checkpoints import find_latest_checkpoint

MIN_BYTES = 1 << 20  # an LFS pointer is ~134 bytes

failed = False
for d in ("runs/muzero_baseline/checkpoints", "runs/muzero_belief/checkpoints"):
    p = find_latest_checkpoint(Path("/app") / d)
    size = p.resolve().stat().st_size if p else 0
    if p is None or size < MIN_BYTES:
        print(f"FAIL {d}: default lookup found {p} ({size} bytes)", file=sys.stderr)
        failed = True
    else:
        print(f"ok   {d}: {p.name} -> {p.resolve().name} ({size} bytes)")
sys.exit(1 if failed else 0)
