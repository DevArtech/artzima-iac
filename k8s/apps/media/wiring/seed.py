#!/usr/bin/env python3
"""Seed app config files with credentials from Infisical, before the app starts.

Init container (pinned python image, stdlib only) in the qbittorrent, bazarr and
seerr pods: these apps cannot take their own credentials from env vars. It never
prints a secret, and it is idempotent: it changes only what differs.

  seed.py qbittorrent   /config/qBittorrent/qBittorrent.conf: [WebUI] user + PBKDF2
                        password hash (QBITTORRENT_USERNAME/_PASSWORD); turns OFF the
                        image's "bypass auth for 10/8, 172.16/12, 192.168/16" default
                        (the Tailscale proxy pod would be in 10/8); binds BitTorrent
                        traffic to the VPN interface tun0; save paths on the shared /data
                        mount (default /data/torrents, categories tv/movies/music ->
                        /data/torrents/<cat>, automatic torrent management), so the arrs
                        see the same paths and can hardlink (the image default is
                        /config/Downloads, which the arrs don't have)
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


def ini_get(text: str, section: str, key: str) -> str | None:
    lines, inside = text.splitlines(), False
    for line in lines:
        if line.startswith("["):
            inside = line.strip() == f"[{section}]"
        elif inside and line.split("=", 1)[0] == key:
            return line.split("=", 1)[1]
    return None


def ini_drop_prefix(text: str, section: str, prefix: str) -> tuple[str, list[str]]:
    """Remove the keys starting with `prefix` from one [section]."""
    out, inside, dropped = [], False, []
    for line in text.splitlines():
        if line.startswith("["):
            inside = line.strip() == f"[{section}]"
        elif inside and line.startswith(prefix) and "=" in line:
            dropped.append(line.split("=", 1)[0])
            continue
        out.append(line)
    return ("\n".join(out).strip("\n") + "\n" if out else ""), dropped


def qbittorrent() -> None:
    conf = pathlib.Path(os.environ.get("QBT_CONF", "/config/qBittorrent/qBittorrent.conf"))
    user, password = env("QBITTORRENT_USERNAME"), env("QBITTORRENT_PASSWORD")
    text = conf.read_text() if conf.exists() else ""
    # qBittorrent 5.x reads the WebUI settings from [WebUI] (4.x: [Preferences] WebUI\...,
    # moved once by 5.x's settings migration and ignored afterwards). Writing them only
    # to [Preferences] worked on the very first start and never again: a rotated password
    # would not have reached qBittorrent. Old-style lines are removed.
    text, dropped = ini_drop_prefix(text, "Preferences", "WebUI\\")
    stored = ini_get(text, "WebUI", "Password_PBKDF2")
    pw = stored if stored and qbt_matches(stored, password) else f'"{qbt_hash(password, os.urandom(16))}"'
    text, changed = ini_set(text, "WebUI", {
        "Username": user,
        "Password_PBKDF2": pw,
        "LocalHostAuth": "true",
        "AuthSubnetWhitelistEnabled": "false",
        "AuthSubnetWhitelist": "@Invalid()",
        "Port": "8080",
    })
    changed += [f"(removed [Preferences] {k})" for k in dropped]
    # Second leak guard behind gluetun's firewall: qBittorrent only uses the
    # tunnel interface (VPN_INTERFACE=tun0 in gluetun), from its very first start.
    # Save paths: the same /data the arrs mount (TRaSH layout), category decides the folder.
    data = os.environ.get("QBT_DATA_ROOT", "/data").rstrip("/")
    text, changed_bt = ini_set(text, "BitTorrent", {
        "Session\\Interface": "tun0",
        "Session\\InterfaceName": "tun0",
        "Session\\DefaultSavePath": f"{data}/torrents",
        "Session\\TempPathEnabled": "false",
        "Session\\DisableAutoTMMByDefault": "false",
        "Session\\DisableAutoTMMTriggers\\CategorySavePathChanged": "false",
        "Session\\DisableAutoTMMTriggers\\DefaultSavePathChanged": "false",
    })
    changed += changed_bt
    if changed:
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text)
        print(f"qBittorrent: updated {', '.join(k for k in changed)} in {conf}")
    else:
        print(f"qBittorrent: {conf} already seeded")

    # Categories (merged: only a missing category or a wrong save_path changes; the
    # wiring Job sets the same over the API, this makes them right from the first start).
    cats_file = conf.parent / "categories.json"
    cats = json.loads(cats_file.read_text() or "{}") if cats_file.exists() else {}
    fixed = []
    for cat in ("tv", "movies", "music"):
        want = f"{data}/torrents/{cat}"
        entry = cats.get(cat)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("save_path") != want:
            entry["save_path"] = want
            cats[cat] = entry
            fixed.append(cat)
    if fixed:
        cats_file.write_text(json.dumps(cats, indent=4) + "\n")
        print(f"qBittorrent: categories {fixed} -> {data}/torrents/<category> in {cats_file}")
    else:
        print(f"qBittorrent: {cats_file} categories already set")


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
SEERR_SEEDED_KEYS = {"main", "network", "clientId", "sessionSecret", "vapidPrivate", "vapidPublic"}


def seerr() -> None:
    conf = pathlib.Path(os.environ.get("SEERR_CONF", "/app/config/settings.json"))
    if conf.exists():
        data = json.loads(conf.read_text())
        main = data.setdefault("main", {})
        # Repair only a file this script seeded (plus the ids Seerr generates and saves
        # before the merge crashes); a real Overseerr file has all sections (plex, ...).
        if ("mediaServerType" not in main and set(main) <= {"apiKey"}
                and set(data) <= SEERR_SEEDED_KEYS):
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
