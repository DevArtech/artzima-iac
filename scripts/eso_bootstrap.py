#!/usr/bin/env python3
"""Create ESO's ONE bootstrap Secret: external-secrets/infisical-universal-auth.

    make eso-bootstrap        (KUBECONFIG=tofu/out/kubeconfig)

Reads the client id/secret of the read-only machine identity "artzima-eso" (created by
`make bootstrap`, tofu/bootstrap) from the environment: `make eso-bootstrap` fetches
Infisical /tofu/ESO_CLIENT_ID and /tofu/ESO_CLIENT_SECRET into
    ESO_INFISICAL_CLIENT_ID / ESO_INFISICAL_CLIENT_SECRET
and this pipes a Secret manifest to `kubectl apply -f -` on stdin. The values are
never printed, never on a command line, never written to disk or git.
Re-run it to rotate the credentials (ESO picks the new ones up).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from infisical_exec import load_local_env  # noqa: E402

NAMESPACE = "external-secrets"
NAME = "infisical-universal-auth"


def main() -> None:
    load_local_env()
    cid = os.environ.get("ESO_INFISICAL_CLIENT_ID", "").strip()
    secret = os.environ.get("ESO_INFISICAL_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        sys.exit("ESO_INFISICAL_CLIENT_ID/_SECRET missing: run `make bootstrap` (writes "
                 "/tofu/ESO_CLIENT_*), then `make eso-bootstrap`")
    manifest = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}},
            {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
             "metadata": {"name": NAME, "namespace": NAMESPACE,
                          "labels": {"app.kubernetes.io/managed-by": "artzima-eso-bootstrap"}},
             "stringData": {"clientId": cid, "clientSecret": secret}},
        ],
    }
    res = subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(manifest),
                         text=True, capture_output=True)
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.exit(f"kubectl apply failed (exit {res.returncode}); response hidden to protect secret values")
    print(f"ok: {NAMESPACE}/{NAME} (Flux's `secrets` Kustomization can now sync from Infisical)")


if __name__ == "__main__":
    main()
