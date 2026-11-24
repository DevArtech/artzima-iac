#!/usr/bin/env python3
"""ONE-TIME: move the cluster's secrets (/k8s/**) from the main Infisical project into the
cluster's own project (docs/secrets.md "Two projects", docs/OPERATOR-SETUP.md A2b).

    make secrets-migrate-k8s                              dry run: what would happen (names only)
    make secrets-migrate-k8s MIGRATE_ARGS=--apply         copy, then verify (hash equality)
    make secrets-migrate-k8s MIGRATE_ARGS=--delete-old    later, separately: delete the main copies

Rules:
  - Values are read and written in memory only; never printed, logged or written to disk.
    Verification compares SHA-256 digests in memory and prints only equal/different.
  - Never overwrites: a key that already exists in the k8s project with a DIFFERENT value
    is a conflict (reported, exit 1). Equal keys count as done (re-runs are safe).
  - Keys that OpenTofu writes under /k8s (B2 keys, tunnel token) are NOT copied and NOT
    deleted: their next `make b2-apply` / `make cf-apply` creates them in the k8s project
    and removes the main copies itself (copying would collide with that create).
  - `mirror_of` keys (the cluster's /k8s/settings/PUBLIC_DOMAIN) are created or updated
    from their source in the main project on every --apply.
  - --delete-old is a separate run: it re-verifies everything first and refuses unless every
    copied key is present and equal in the k8s project.
Confirmation: type "yes", or AUTO_APPROVE=1.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import infisical_projects  # noqa: E402
import labdata  # noqa: E402
from secrets_check import load_manifest  # noqa: E402


def digest(value) -> str:
    return hashlib.sha256(str(value or "").encode()).hexdigest()


def tofu_written() -> set[tuple[str, str]]:
    """(folder, KEY) that an OpenTofu `infisical_secret` RESOURCE writes (not reads)."""
    out: set[tuple[str, str]] = set()
    for f in (labdata.REPO / "tofu").rglob("*.tf"):
        if ".terraform" in f.parts:
            continue
        text = f.read_text()
        for blk in re.finditer(r'resource\s+"infisical_secret"\s+"\w+"\s*\{(.*?)\n\}', text, re.S):
            body = blk.group(1)
            path = re.search(r'folder_path\s*=\s*"(/[^"]+)"', body)
            if not path:
                continue
            name = re.search(r'\n\s*name\s*=\s*"([A-Z0-9_]+)"', body)
            if name:
                out.add((path.group(1), name.group(1)))
                continue
            each = re.search(r'for_each\s*=\s*local\.(\w+)', body)
            if each and re.search(r'\n\s*name\s*=\s*each\.key', body):
                m = re.search(r'\n\s*' + each.group(1) + r'\s*=\s*\{(.*?)\n\s*\}\n', text, re.S)
                for key in re.findall(r'^\s*([A-Z0-9_]+)\s*=\s*\{', m.group(1) if m else "", re.M):
                    out.add((path.group(1), key))
    return out


class _Retrying:
    """Proxy over the SDK client: a call that fails on Infisical's rate limit (free plan: a few
    requests per second) waits and retries instead of aborting the migration half-way."""

    def __init__(self, obj, tries: int = 8):
        self._obj, self._tries = obj, tries

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        if callable(attr):
            def call(*a, **k):
                import time
                for i in range(self._tries):
                    try:
                        return attr(*a, **k)
                    except Exception as e:  # noqa: BLE001 (SDK raises a generic APIError)
                        if i == self._tries - 1 or not re.search(r"429|rate ?limit", str(e), re.I):
                            raise
                        time.sleep(min(30, 2 ** i + 1))
            return call
        return _Retrying(attr, self._tries)


class Store:
    """Thin wrapper over the Infisical SDK client for one environment (tests use a fake client)."""

    def __init__(self, c, env: str):
        self.c, self.env = _Retrying(c), env

    def folders(self, project: str, path: str) -> list[str]:
        res = self.c.folders.list_folders(project_id=project, environment_slug=self.env, path=path)
        return [f.name for f in res.folders]

    def values(self, project: str, folder: str) -> dict[str, str]:
        res = self.c.secrets.list_secrets(project_id=project, environment_slug=self.env, secret_path=folder,
                                          view_secret_value=True, expand_secret_references=False,
                                          include_imports=False)
        return {s.secretKey: s.secretValue for s in res.secrets}

    def exists(self, project: str, path: str) -> bool:
        parent, _, name = path.rpartition("/")
        try:
            return name in self.folders(project, parent or "/")
        except Exception:  # noqa: BLE001 (the parent itself is absent)
            return False

    def tree(self, project: str, root: str) -> list[str]:
        """root and every folder below it that exists in the project (parents first)."""
        if not self.exists(project, root):
            return []
        out, todo = [], [root]
        while todo:
            f = todo.pop(0)
            out.append(f)
            todo += [f"{f}/{n}" for n in sorted(self.folders(project, f))]
        return out


def plan(store: Store, main: str, k8s: str, manifest_folders: list[str], secrets: dict,
         tofu_keys: set[tuple[str, str]]) -> dict:
    """What to do. Values stay inside this function's data (never returned in reports)."""
    k8s_roots = infisical_projects.prefixes()["k8s"]
    src_folders: list[str] = []
    for root in k8s_roots:
        src_folders += store.tree(main, root)
    want_folders = [f for f in manifest_folders if infisical_projects.project_of(f) == "k8s"]
    for f in src_folders:
        if f not in want_folders:
            want_folders.append(f)
    have_k8s = {f: store.values(k8s, f) for f in want_folders if store.exists(k8s, f)}
    items = []  # (folder, key, action, source digest or None)
    for f in src_folders:
        listed = set(secrets.get(f, {}))
        for key, value in sorted(store.values(main, f).items()):
            target = have_k8s.get(f, {})
            if (f, key) in tofu_keys:
                action = "tofu"
            elif key in target:
                action = "same" if digest(target[key]) == digest(value) else "CONFLICT"
            else:
                action = "copy"
            items.append({"folder": f, "key": key, "action": action, "listed": key in listed,
                          "digest": digest(value)})
    mirrors = []
    main_cache: dict[str, dict[str, str]] = {}
    for f, keys in secrets.items():
        for key, meta in keys.items():
            src = meta.get("mirror_of")
            if not src:
                continue
            sf, _, sk = str(src).rpartition("/")
            if sf not in main_cache:
                main_cache[sf] = store.values(main, sf) if store.exists(main, sf) else {}
            if sk not in main_cache[sf]:
                mirrors.append({"folder": f, "key": key, "source": str(src), "action": "SOURCE MISSING", "digest": None})
                continue
            d = digest(main_cache[sf][sk])
            cur = have_k8s.get(f, {})
            action = ("same" if digest(cur[key]) == d else "update") if key in cur else "copy"
            mirrors.append({"folder": f, "key": key, "source": str(src), "action": action, "digest": d})
    missing_folders = [f for f in want_folders if f not in have_k8s]
    return {"items": items, "mirrors": mirrors, "missing_folders": missing_folders, "folders": want_folders}


def report(p: dict) -> None:
    def count(action):
        return sum(1 for i in p["items"] if i["action"] == action)
    print(f"k8s project folders to create: {len(p['missing_folders'])}")
    for f in p["missing_folders"]:
        print(f"  + folder {f}")
    print(f"\n/k8s secrets in the MAIN project: {len(p['items'])}  (copy {count('copy')}, already equal "
          f"{count('same')}, left to OpenTofu {count('tofu')}, CONFLICT {count('CONFLICT')})")
    for i in p["items"]:
        note = "" if i["listed"] else "   (not in secrets.manifest.yaml: copied anyway)"
        what = {"copy": "copy", "same": "equal, nothing to do", "tofu": "OpenTofu moves it (b2-apply / cf-apply)",
                "CONFLICT": "CONFLICT: exists in the k8s project with a DIFFERENT value, not overwritten"}[i["action"]]
        print(f"  {i['folder']}/{i['key']}: {what}{note}")
    print(f"\nmirrors (from the main project): {len(p['mirrors'])}")
    for m in p["mirrors"]:
        print(f"  {m['folder']}/{m['key']} <- {m['source']}: {m['action']}")


def confirm(question: str) -> bool:
    if os.environ.get("AUTO_APPROVE") == "1":
        return True
    if not sys.stdin.isatty():
        print("refusing without confirmation (terminal, or AUTO_APPROVE=1)", file=sys.stderr)
        return False
    return input(f"\n>>> {question} Type \"yes\": ").strip() == "yes"


def apply(store: Store, main: str, k8s: str, p: dict, comment: str = "migrated from the main project (secrets_migrate_k8s.py)") -> None:
    c, env = store.c, store.env
    for f in p["missing_folders"]:  # parents first (manifest order, then discovered order)
        parent, _, name = f.rpartition("/")
        if name not in store.folders(k8s, parent or "/"):
            c.folders.create_folder(name=name, environment_slug=env, project_id=k8s, path=parent or "/")
            print(f"  created folder {f}")
    src_cache: dict[str, dict[str, str]] = {}

    def src(folder: str, key: str) -> str:
        if folder not in src_cache:
            src_cache[folder] = store.values(main, folder)
        return src_cache[folder][key]

    for i in p["items"]:
        if i["action"] != "copy":
            continue
        value = src(i["folder"], i["key"])
        c.secrets.create_secret_by_name(secret_name=i["key"], project_id=k8s, environment_slug=env,
                                        secret_path=i["folder"], secret_value=value, secret_comment=comment)
        del value
        print(f"  copied {i['folder']}/{i['key']}")
    for m in p["mirrors"]:
        if m["action"] not in ("copy", "update"):
            continue
        sf, _, sk = m["source"].rpartition("/")
        value = src(sf, sk)
        if m["action"] == "copy":
            c.secrets.create_secret_by_name(secret_name=m["key"], project_id=k8s, environment_slug=env,
                                            secret_path=m["folder"], secret_value=value,
                                            secret_comment=f"mirror of {m['source']} (main project)")
        else:
            c.secrets.update_secret_by_name(current_secret_name=m["key"], project_id=k8s, environment_slug=env,
                                            secret_path=m["folder"], secret_value=value)
        del value
        print(f"  mirrored {m['folder']}/{m['key']} <- {m['source']}")


def verify(store: Store, k8s: str, p: dict) -> tuple[int, list[str]]:
    """Every non-OpenTofu item and every mirror present and equal in the k8s project."""
    bad, ok = [], 0
    cache: dict[str, dict[str, str]] = {}
    for i in [x for x in p["items"] if x["action"] != "tofu"] + [m for m in p["mirrors"] if m["digest"]]:
        if i["folder"] not in cache:
            try:
                cache[i["folder"]] = store.values(k8s, i["folder"])
            except Exception:  # noqa: BLE001 (folder absent)
                cache[i["folder"]] = {}
        have = cache[i["folder"]]
        if i["key"] in have and digest(have[i["key"]]) == i["digest"]:
            ok += 1
        else:
            bad.append(f"{i['folder']}/{i['key']}")
    return ok, bad


def delete_old(store: Store, main: str, p: dict) -> int:
    n = 0
    for i in p["items"]:
        if i["action"] != "same":
            continue
        store.c.secrets.delete_secret_by_name(secret_name=i["key"], project_id=main, environment_slug=store.env,
                                              secret_path=i["folder"])
        print(f"  deleted {i['folder']}/{i['key']} from the main project")
        n += 1
    return n


def main() -> None:
    from infisical_exec import client, load_local_env

    do_apply, do_delete = "--apply" in sys.argv, "--delete-old" in sys.argv
    if do_apply and do_delete:
        sys.exit("--apply and --delete-old are separate runs: copy and verify first, delete later")
    load_local_env()
    folders, secrets = load_manifest()
    c, cfg = client()
    main_id = infisical_projects.project_id(cfg, "/settings")
    k8s_id = infisical_projects.project_id(cfg, "/k8s")
    if main_id == k8s_id:
        sys.exit("the k8s project id equals the main project id: check INFISICAL_K8S_PROJECT_ID")
    store = Store(c, cfg["infisical_env"])
    print(f"Infisical {cfg['infisical_host']} env {cfg['infisical_env']}: main {cfg.get('infisical_project_slug')} "
          f"-> k8s {cfg.get('infisical_k8s_project_slug')}\n")
    p = plan(store, main_id, k8s_id, folders, secrets, tofu_written())
    report(p)
    conflicts = [i for i in p["items"] if i["action"] == "CONFLICT"] + [m for m in p["mirrors"] if m["action"] == "SOURCE MISSING"]

    if do_delete:
        ok, bad = verify(store, k8s_id, p)
        todo = [i for i in p["items"] if i["action"] == "same"]
        if conflicts or bad or any(i["action"] == "copy" for i in p["items"]):
            sys.exit(f"\nrefusing to delete: {len(bad)} key(s) not verified in the k8s project, "
                     f"{len(conflicts)} conflict(s), {sum(1 for i in p['items'] if i['action'] == 'copy')} not copied yet. "
                     "Run MIGRATE_ARGS=--apply first.")
        print(f"\nverified: {ok} key(s) present and equal in the k8s project (hashes compared, not shown)")
        if not todo:
            print("nothing to delete in the main project")
            return
        if not confirm(f"DELETE these {len(todo)} old /k8s secrets from the MAIN project?"):
            sys.exit("cancelled")
        print(f"\ndeleted {delete_old(store, main_id, p)} old copies. Empty /k8s folders in the main project "
              "can be removed in the Infisical UI. OpenTofu-written keys are removed by their next apply.")
        return

    if not do_apply:
        print("\n(dry run. MIGRATE_ARGS=--apply copies and verifies; --delete-old later removes the main copies)")
        sys.exit(1 if conflicts else 0)
    if conflicts:
        sys.exit("\nrefusing: resolve the CONFLICT / SOURCE MISSING lines first (nothing was written)")
    n = sum(1 for i in p["items"] if i["action"] == "copy") + sum(1 for m in p["mirrors"] if m["action"] in ("copy", "update"))
    if n or p["missing_folders"]:
        if not confirm(f"Create {len(p['missing_folders'])} folder(s) and write {n} secret(s) into the k8s project?"):
            sys.exit("cancelled")
        apply(store, main_id, k8s_id, p)
    ok, bad = verify(store, k8s_id, p)
    print(f"\nverify: {ok} equal, {len(bad)} missing or different (SHA-256 compared in memory, values not shown)")
    for b in bad:
        print(f"  ! {b}")
    if bad:
        sys.exit(1)
    print("migration verified. Next: make secrets-check; later MIGRATE_ARGS=--delete-old removes the main copies.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 (SDK errors may echo request bodies: never print them)
        sys.exit(f"secrets-migrate-k8s failed: {type(e).__name__} (details hidden)")
