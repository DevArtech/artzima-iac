#!/usr/bin/env python3
"""Read-only health report after `make up` (PASS/FAIL table; exit 1 on any FAIL).

    make verify          (KUBECONFIG=tofu/out/kubeconfig; PUBLIC_DOMAIN from Infisical)

Checks: nodes Ready; every Flux Kustomization + HelmRelease Ready; every
ExternalSecret synced; every Certificate Ready and > 14 days from expiry; PVCs
Bound; CNPG clusters healthy with a backup in the last 26 h; every VolSync
ReplicationSource synced in the last 26 h; the last run of every self-check
CronJob (label artzima.io/self-check) succeeded; every public app in /apps.yaml
answers over HTTPS (< 500). Nothing is changed. Right after `make up` some checks
fail until Flux has reconciled (~15 min) and the first nightly backups ran:
"no backup yet" is reported as FAIL on purpose (it is what you must look at).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def kget(resource: str) -> list[dict]:
    r = subprocess.run(["kubectl", "get", resource, "-A", "-o", "json"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        if "the server doesn't have a resource type" in r.stderr:
            return []
        record(f"kubectl get {resource}", False, r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "failed")
        return []
    return json.loads(r.stdout)["items"]


def ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def ready(obj: dict, cond: str = "Ready") -> tuple[bool, str]:
    for c in obj.get("status", {}).get("conditions", []):
        if c.get("type") == cond:
            return c.get("status") == "True", c.get("message", "")[:90]
    return False, "no status yet"


def name(o: dict) -> str:
    return f"{o['metadata'].get('namespace', '-')}/{o['metadata']['name']}"


def group(title: str, items: list[dict], check) -> None:
    bad = []
    for o in items:
        ok, why = check(o)
        if not ok:
            bad.append(f"{name(o)}: {why}")
    record(f"{title} ({len(items)})", bool(items) and not bad, "; ".join(bad[:4]) + (" ..." if len(bad) > 4 else "")
           if bad else ("none found" if not items else ""))


def recent(t: dt.datetime | None, hours: int = 26) -> tuple[bool, str]:
    if not t:
        return False, "never"
    age = NOW - t
    return age < dt.timedelta(hours=hours), f"last {age.total_seconds() / 3600:.0f} h ago"


def main() -> None:
    if not os.environ.get("KUBECONFIG") or not pathlib.Path(os.environ["KUBECONFIG"]).exists():
        sys.exit("no kubeconfig: run `make up` first (tofu/out/kubeconfig)")

    group("nodes Ready", kget("nodes"), ready)
    group("Flux Kustomizations Ready", kget("kustomizations.kustomize.toolkit.fluxcd.io"), ready)
    group("HelmReleases Ready", kget("helmreleases.helm.toolkit.fluxcd.io"), ready)
    group("ExternalSecrets synced", kget("externalsecrets.external-secrets.io"), ready)
    group("PVCs Bound", kget("pvc"), lambda o: (o.get("status", {}).get("phase") == "Bound", o.get("status", {}).get("phase", "?")))

    def cert(o: dict) -> tuple[bool, str]:
        ok, why = ready(o)
        exp = ts(o.get("status", {}).get("notAfter"))
        if ok and exp and exp - NOW < dt.timedelta(days=14):
            return False, f"expires {exp:%Y-%m-%d}"
        return ok, why
    group("Certificates valid > 14 d", kget("certificates.cert-manager.io"), cert)

    def cnpg(o: dict) -> tuple[bool, str]:
        st = o.get("status", {})
        if st.get("phase") != "Cluster in healthy state":
            return False, st.get("phase", "?")
        return recent(ts(st.get("lastSuccessfulBackup")))
    group("CNPG clusters healthy + backup < 26 h", kget("clusters.postgresql.cnpg.io"), cnpg)
    group("VolSync backups < 26 h", kget("replicationsources.volsync.backube"),
          lambda o: recent(ts(o.get("status", {}).get("lastSyncTime"))))

    def selfcheck(o: dict) -> tuple[bool, str]:
        st = o.get("status", {})
        last_ok, last_run = ts(st.get("lastSuccessfulTime")), ts(st.get("lastScheduleTime"))
        if not last_run:
            return True, ""  # monthly: not run yet is fine
        return bool(last_ok and last_ok >= last_run), "last run FAILED (kubectl -n <ns> logs job/<latest>)"
    group("self-checks (restore tests) last run OK",
          [c for c in kget("cronjobs") if c["metadata"].get("labels", {}).get("artzima.io/self-check") == "true"],
          selfcheck)

    domain = os.environ.get("PUBLIC_DOMAIN", "").strip()
    if domain:
        bad = []
        apps = labdata.load_apps()["apps"]
        for a in apps:
            url = f"https://{a['name']}.{domain}/"
            try:
                req = urllib.request.Request(url, method="GET", headers={"User-Agent": "artzima-verify"})
                code = urllib.request.urlopen(req, timeout=15).status
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception as e:  # noqa: BLE001
                code = f"{type(e).__name__}"
            if not isinstance(code, int) or code >= 500:
                bad.append(f"{a['name']}: {code}")
        record(f"public apps answer over HTTPS ({len(apps)})", not bad, "; ".join(bad))
    else:
        record("public apps answer over HTTPS", True, "skipped: PUBLIC_DOMAIN not available")

    width = max(len(n) for n, _, _ in results)
    print("\nVERIFY")
    for n, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {n.ljust(width)}  {detail}")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
