#!/usr/bin/env python3
"""Seed app config files with credentials from Infisical, before the app starts.

Init container (pinned python image, stdlib only) in the qbittorrent, bazarr and
seerr pods: these apps cannot take their own credentials from env vars. It never
prints a secret, and it is idempotent: it changes only what differs.

  seed.py qbittorrent   /config/qBittorrent/qBittorrent.conf: WebUI user + PBKDF2
                        password hash (QBITTORRENT_USERNAME/_PASSWORD); turns OFF the
                        image's "bypass auth for 10/8, 172.16/12, 192.168/16" default
                        (the Tailscale proxy pod would be in 10/8); binds BitTorrent
                        traffic to the VPN interface tun0
  seed.py bazarr        /config/config/config.yaml: auth.apikey (BAZARR_API_KEY),
                        only on first start (Bazarr owns the file afterwards)
  seed.py seerr         /app/config/settings.json: main.apiKey (SEERR_API_KEY),
                        main.mediaServerType and network.trustProxy, only on first
                        start (Seerr also reads API_KEY from env on every later start)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import re
import sys


def env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        sys.exit(f"missing environment variable {name}")
    return v


# ---------------------------------------------------------------- qBittorrent
QBT_ITERATIONS = 100_000  # qBittorrent's PBKDF2-HMAC-SHA512 parameters


def qbt_hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, QBT_ITERATIONS, 64)
    return f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()})"


def qbt_matches(stored: str, password: str) -> bool:
    m = re.fullmatch(r'"?@ByteArray\(([^:]+):([^)]+)\)"?', stored.strip())
    if not m:
        return False
    salt = base64.b64decode(m.group(1))
    return hmac.compare_digest(qbt_hash(password, salt), f"@ByteArray({m.group(1)}:{m.group(2)})")


def ini_set(text: str, section: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Set keys in one [section] of a Qt ini file, keeping everything else as is."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"[{section}]"), None)
    if start is None:
        lines += ["", f"[{section}]"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():  # insert before trailing blank lines
        end -= 1
    changed = []
    for key, val in values.items():
        idx = next((i for i in range(start + 1, end) if lines[i].split("=", 1)[0] == key), None)
        if idx is None:
            lines.insert(end, f"{key}={val}")
            end += 1
            changed.append(key)
        elif lines[idx] != f"{key}={val}":
            lines[idx] = f"{key}={val}"
            changed.append(key)
    return "\n".join(lines).strip("\n") + "\n", changed


def qbittorrent() -> None:
    conf = pathlib.Path(os.environ.get("QBT_CONF", "/config/qBittorrent/qBittorrent.conf"))
    user, password = env("QBITTORRENT_USERNAME"), env("QBITTORRENT_PASSWORD")
    text = conf.read_text() if conf.exists() else ""
    m = re.search(r"^WebUI\\Password_PBKDF2=(.*)$", text, re.M)
    pw = m.group(1) if m and qbt_matches(m.group(1), password) else f'"{qbt_hash(password, os.urandom(16))}"'
    text, changed = ini_set(text, "Preferences", {
        "WebUI\\Username": user,
        "WebUI\\Password_PBKDF2": pw,
        "WebUI\\LocalHostAuth": "true",
        "WebUI\\AuthSubnetWhitelistEnabled": "false",
        "WebUI\\AuthSubnetWhitelist": "@Invalid()",
        "WebUI\\Port": "8080",
    })
    # Second leak guard behind gluetun's firewall: qBittorrent only uses the
    # tunnel interface (VPN_INTERFACE=tun0 in gluetun), from its very first start.
    text, changed_bt = ini_set(text, "BitTorrent", {
        "Session\\Interface": "tun0",
        "Session\\InterfaceName": "tun0",
    })
    changed += changed_bt
    if changed:
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text)
        print(f"qBittorrent: updated {', '.join(k for k in changed)} in {conf}")
    else:
        print(f"qBittorrent: {conf} already seeded")


# ---------------------------------------------------------------- Bazarr / Seerr (first start only)
def bazarr() -> None:
    conf = pathlib.Path(os.environ.get("BAZARR_CONF", "/config/config/config.yaml"))
    if conf.exists():
        print(f"Bazarr: {conf} exists, not touched (API key rotation: docs/manual-steps.md §10)")
        return
    key = env("BAZARR_API_KEY")
    if not re.fullmatch(r"[A-Za-z0-9]+", key):
        sys.exit("BAZARR_API_KEY must be alphanumeric (openssl rand -hex 16)")
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"auth:\n  apikey: {key}\n")
    print(f"Bazarr: seeded auth.apikey in {conf}")


# Seerr's MediaServerType.NOT_CONFIGURED (its own default for a fresh install). Seerr
# reads settings.json RAW (no defaults) to decide whether this is an Overseerr install
# to merge: a file without main.mediaServerType sends a brand-new database into the
# Overseerr merge, which dies with "Failed to insert migration records ... migrations".
SEERR_NOT_CONFIGURED = 4


def seerr() -> None:
    conf = pathlib.Path(os.environ.get("SEERR_CONF", "/app/config/settings.json"))
    if conf.exists():
        data = json.loads(conf.read_text())
        main = data.setdefault("main", {})
        # Repair only a file this script seeded and Seerr never started with (Seerr
        # writes clientId on its first successful start); never a real Overseerr file.
        if "mediaServerType" not in main and "clientId" not in data:
            main["mediaServerType"] = SEERR_NOT_CONFIGURED
            conf.write_text(json.dumps(data, indent=1))
            print(f"Seerr: added main.mediaServerType (not configured) to never-started {conf}")
            return
        print(f"Seerr: {conf} exists, not touched (API_KEY env applies on every start)")
        return
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(json.dumps({"main": {"apiKey": env("SEERR_API_KEY"),
                                         "mediaServerType": SEERR_NOT_CONFIGURED},
                                "network": {"trustProxy": True}}, indent=1))
    print(f"Seerr: seeded main.apiKey, main.mediaServerType and network.trustProxy in {conf}")


if __name__ == "__main__":
    os.umask(0o077)
    {"qbittorrent": qbittorrent, "bazarr": bazarr, "seerr": seerr}[sys.argv[1]]()
