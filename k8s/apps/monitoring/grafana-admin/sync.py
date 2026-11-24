#!/usr/bin/env python3
"""grafana-admin-sync: keep Grafana's admin (user id 1) equal to Infisical. Idempotent.

Grafana reads GF_SECURITY_ADMIN_USER/_PASSWORD (Secret grafana-admin, from Infisical
/k8s/monitoring/GRAFANA_ADMIN_USER/_PASSWORD) only when it first creates its database.
This Job (k8s/apps/monitoring/grafana-admin-sync.yaml, python stdlib only) applies a
later change through Grafana's API:

  1. log in with the wanted login/password (Secret grafana-admin); if that is user 1,
     nothing to do
  2. otherwise log in with what was last applied (Secret grafana-admin-applied, which
     this Job maintains), set user 1's password, then its login
  3. verify the wanted login/password, record them in grafana-admin-applied

Values come from env and are never printed. If no known login works (changed by hand
in Grafana, or Infisical changed before this Job ever ran), it fails and says so:
docs/manual-steps.md "Grafana admin login" has the one-time manual fix.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

GRAFANA = os.environ.get("GRAFANA_URL", "http://kube-prometheus-stack-grafana.monitoring.svc").rstrip("/")
APPLIED_SECRET = os.environ.get("APPLIED_SECRET", "grafana-admin-applied")  # empty: don't record (local test)
SA = "/var/run/secrets/kubernetes.io/serviceaccount"


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "")
    if required and not v:
        sys.exit(f"missing environment variable {name}")
    return v


def grafana(method: str, path: str, auth: tuple[str, str], body: dict | None = None) -> tuple[int, dict]:
    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req = urllib.request.Request(
        GRAFANA + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def whoami(auth: tuple[str, str]) -> dict | None:
    """The signed-in user for these credentials, or None if they don't work."""
    if not all(auth):
        return None
    code, user = grafana("GET", "/api/user", auth)
    return user if code == 200 else None


def wait_for_grafana(seconds: int) -> None:
    deadline = time.time() + seconds
    while True:
        try:
            with urllib.request.urlopen(GRAFANA + "/api/health", timeout=10) as r:
                if json.loads(r.read()).get("database") == "ok":
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.time() > deadline:
            sys.exit(f"Grafana not healthy at {GRAFANA} after {seconds}s")
        time.sleep(10)


def record_applied(auth: tuple[str, str]) -> None:
    """Store the credentials Grafana now has, for the next change (merge patch)."""
    if not APPLIED_SECRET:
        return
    if (env("APPLIED_USER", False), env("APPLIED_PASSWORD", False)) == auth:
        return
    ns = open(f"{SA}/namespace").read().strip()
    token = open(f"{SA}/token").read().strip()
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    data = {"admin-user": auth[0], "admin-password": auth[1]}
    body = {"data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}
    req = urllib.request.Request(
        f"https://{host}:{port}/api/v1/namespaces/{ns}/secrets/{APPLIED_SECRET}", method="PATCH",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"})
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    try:
        urllib.request.urlopen(req, context=ctx, timeout=30).close()
    except urllib.error.HTTPError as e:
        sys.exit(f"could not update Secret {ns}/{APPLIED_SECRET}: HTTP {e.code}")
    print(f"recorded the applied login in Secret {ns}/{APPLIED_SECRET}")


def main() -> None:
    want = (env("ADMIN_USER"), env("ADMIN_PASSWORD"))
    applied = (env("APPLIED_USER", False), env("APPLIED_PASSWORD", False))
    wait_for_grafana(int(os.environ.get("WAIT_SECONDS", "600")))

    me = whoami(want)
    if me and me.get("id") == 1:
        print("Grafana admin (user 1) already matches Infisical")
        record_applied(want)
        return
    if me:
        sys.exit(f"the Infisical login is Grafana user {me.get('id')}, not the admin (user 1): fix by hand")

    # Every pair of known login/password, in case an earlier run stopped half-way.
    candidates = [applied, (applied[0], want[1]), (want[0], applied[1])]
    auth = next((a for a in candidates if a != want and (u := whoami(a)) and u.get("id") == 1), None)
    if auth is None:
        sys.exit("no known login works for Grafana user 1 (Secret grafana-admin-applied is empty or stale): "
                 "apply it once by hand, docs/manual-steps.md \"Grafana admin login\"")

    if auth[1] != want[1]:
        code, _ = grafana("PUT", "/api/admin/users/1/password", auth, {"password": want[1]})
        if code != 200:
            sys.exit(f"setting the admin password failed: HTTP {code}")
        auth = (auth[0], want[1])
        print("set the admin password")
    if auth[0] != want[0]:
        code, user = grafana("GET", "/api/users/1", auth)
        if code != 200:
            sys.exit(f"reading user 1 failed: HTTP {code}")
        body = {"login": want[0], "email": user.get("email", ""), "name": user.get("name", "")}
        code, resp = grafana("PUT", "/api/users/1", auth, body)
        if code != 200:
            sys.exit(f"renaming the admin failed: HTTP {code} {resp.get('message', '')}")
        print("set the admin login")

    me = whoami(want)
    if not me or me.get("id") != 1:
        sys.exit("the new admin login does not work after the change")
    print("Grafana admin (user 1) now matches Infisical")
    record_applied(want)


if __name__ == "__main__":
    main()
