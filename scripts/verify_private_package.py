#!/usr/bin/env python3
"""Fail closed unless a target GHCR package already exists and is private."""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.error
import urllib.request


def verify(owner, name, token):
    if not token or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in (owner, name)):
        raise SystemExit("A token and valid package owner/name are required")
    request = urllib.request.Request(
        f"https://api.github.com/users/{owner}/packages/container/{name}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            package = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Cannot verify private package {name}: HTTP {error.code}; publication refused") from None
    except Exception as error:
        raise SystemExit(f"Cannot verify package {name}: {type(error).__name__}; publication refused") from None
    if (package.get("visibility") != "private" or package.get("name") != name or
            (package.get("owner") or {}).get("login", "").lower() != owner.lower()):
        raise SystemExit(f"Package {name} is not a verified private package; publication refused")
    print(f"Verified private package: {name}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: verify_private_package.py OWNER PACKAGE")
    verify(sys.argv[1], sys.argv[2], os.environ.get("GH_TOKEN", ""))
