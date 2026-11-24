#!/usr/bin/env python3
"""Print the EMERGENCY KIT checklist (emergency-kit.yaml): names only, never values.

    make emergency-kit                         checklist + last rotation dates (offline)
    make emergency-kit EK_ARGS=--infisical     + Infisical's last-change date of mirrored items
                                               (lists secret NAMES with values hidden)
    emergency_kit.py --lint                    (make lint) kit references exist in secrets.manifest.yaml
    emergency_kit.py --markdown                the checklist as Markdown (make dr-pdf)

The kit is what must exist OUTSIDE the lab and OUTSIDE Infisical: docs/disaster-recovery.md.
"""

from __future__ import annotations

import datetime
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402

KIT = labdata.REPO / "emergency-kit.yaml"
MANIFEST = labdata.REPO / "secrets.manifest.yaml"
STALE_DAYS = 365  # flag kit entries not confirmed for a year


def load() -> list[dict]:
    items = yaml.safe_load(KIT.read_text())["items"]
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        sys.exit("emergency-kit.yaml: duplicate ids")
    for i in items:
        for k in ("id", "title", "needed_for", "kept_at"):
            if not i.get(k):
                sys.exit(f"emergency-kit.yaml: item {i.get('id')!r} lacks {k}")
    return items


def lint() -> None:
    items = load()
    m = yaml.safe_load(MANIFEST.read_text())["secrets"]
    listed = {f"{folder}/{k}" for folder, keys in m.items() for k in keys}
    bad = [i for i in items if i.get("infisical") and i["infisical"] not in listed]
    for i in bad:
        print(f"FAIL: emergency-kit.yaml {i['id']}: {i['infisical']} is not in {MANIFEST.name}", file=sys.stderr)
    if bad:
        sys.exit(1)
    print(f"emergency kit: {len(items)} items, Infisical references valid")


def infisical_dates(items: list[dict]) -> dict[str, str]:
    """Last change of the Infisical copies: secret NAMES/metadata only, values never requested."""
    from infisical_exec import client, load_local_env

    load_local_env()
    c, cfg = client()
    out: dict[str, str] = {}
    folders: dict[str, dict] = {}
    for i in items:
        ref = i.get("infisical")
        if not ref:
            continue
        folder, _, key = ref.rpartition("/")
        if folder not in folders:
            from infisical_projects import project_id
            res = c.secrets.list_secrets(project_id=project_id(cfg, folder), environment_slug=cfg["infisical_env"],
                                         secret_path=folder, view_secret_value=False,
                                         expand_secret_references=False, include_imports=False)
            folders[folder] = {s.secretKey: s for s in res.secrets}
        s = folders[folder].get(key)
        when = (getattr(s, "updatedAt", None) or getattr(s, "updated_at", None)) if s else None
        out[i["id"]] = str(when)[:10] if when else ("MISSING in Infisical" if s is None else "unknown")
    return out


def markdown(items: list[dict]) -> str:
    """The checklist as Markdown (make dr-pdf prints it behind the runbook)."""
    lines = ["# Emergency kit checklist", "",
             "Keep OUTSIDE the lab and OUTSIDE Infisical: printed or offline, away from home. Names only, "
             "never values. Tick each item when you have checked it is present and current.", ""]
    for n, i in enumerate(items, 1):
        lines += [f"- [ ] **{n}. {i['title']}**",
                  f"  - needed for: {i['needed_for']}",
                  f"  - kept at: {i['kept_at']}; last rotated: {i.get('last_rotated') or 'unknown'}"
                  + (f"; Infisical copy: `{i['infisical']}`" if i.get("infisical") else "")]
    return "\n".join(lines) + "\n"


def main() -> None:
    if "--markdown" in sys.argv:
        print(markdown(load()), end="")
        return
    if "--lint" in sys.argv:
        lint()
        return
    items = load()
    inf = infisical_dates(items) if "--infisical" in sys.argv else {}
    today = datetime.date.today()
    print("EMERGENCY KIT: keep OUTSIDE the lab and OUTSIDE Infisical (printed / offline, away from home).")
    print("Names only. Values are never printed. Runbook: docs/disaster-recovery.md\n")
    todo = 0
    for n, i in enumerate(items, 1):
        last = i.get("last_rotated")
        age = ""
        if last:
            days = (today - datetime.date.fromisoformat(str(last))).days
            age = f" ({days} d ago{', CONFIRM it is still current' if days > STALE_DAYS else ''})"
        kept = i["kept_at"]
        todo += kept == "TODO" or not last
        print(f"[ ] {n:2}. {i['title']}")
        print(f"        id: {i['id']}   needed for: {i['needed_for']}")
        print(f"        kept at: {kept}{'   <-- fill in emergency-kit.yaml' if kept == 'TODO' else ''}")
        print(f"        last rotated: {last or 'unknown'}{age}"
              + (f"   Infisical copy last changed: {inf[i['id']]}" if i["id"] in inf else ""))
    print(f"\n{len(items)} items, {todo} with an unknown location or rotation date.")
    print("After rotating anything listed here: update the kit copy, then last_rotated in emergency-kit.yaml.")


if __name__ == "__main__":
    main()
