#!/usr/bin/env python3
"""Keep actual deployment metadata out of the public checkout.

Public files are a fictional, non-deployable example. Live make targets execute in
.private/runtime with .private/site overlays. State and generated files remain there.
Commands: run <make targets...>, prepare, backup, restore, check.
"""
from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE = ROOT / ".private"
SITE = PRIVATE / "site"
RUNTIME = PRIVATE / "runtime"
FILES = ["ansible/hosts.yml"] + [
    f"ansible/group_vars/all/{name}.yml"
    for name in ("network", "lab", "infisical", "media", "backups")
] + ["ansible/roles/nut/defaults/main.yml", "emergency-kit.yaml"]
# /settings/PRIVATE_SITE_CONFIG: private metadata backup, not accessible to ESO.
REMOTE_KEY = "PRIVATE_SITE_CONFIG"


def secure_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SystemExit("Refusing a symlink at a private configuration destination")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)


def read_site():
    if not SITE.is_dir() or SITE.is_symlink():
        raise SystemExit("Private configuration missing; run make private-restore before live targets.")
    result = {}
    for name in FILES:
        path = SITE / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit("Private configuration is incomplete; restore it before live targets.")
        result[name] = path.read_text()
    return result


def backup():
    from infisical_exec import client, load_local_env
    load_local_env()
    c, cfg = client()
    payload = json.dumps({"version": 1, "files": read_site()}, sort_keys=True)
    # List metadata first; do not confuse a network failure with a missing secret.
    current = c.secrets.list_secrets(
        project_id=cfg["infisical_project_id"], environment_slug=cfg["infisical_env"],
        secret_path="/settings", view_secret_value=False, expand_secret_references=False,
        include_imports=False)
    args = dict(project_id=cfg["infisical_project_id"], environment_slug=cfg["infisical_env"],
                secret_path="/settings", secret_value=payload)
    if REMOTE_KEY in {s.secretKey for s in current.secrets}:
        c.secrets.update_secret_by_name(current_secret_name=REMOTE_KEY, **args)
    else:
        c.secrets.create_secret_by_name(secret_name=REMOTE_KEY, **args)
    print("Private deployment metadata backed up to Infisical; values hidden.")


def restore():
    from infisical_exec import client, load_local_env
    if SITE.exists():
        raise SystemExit("Private site already exists; refusing to replace it.")
    load_local_env()
    c, cfg = client()
    value = c.secrets.get_secret_by_name(
        secret_name=REMOTE_KEY, project_id=cfg["infisical_project_id"],
        environment_slug=cfg["infisical_env"], secret_path="/settings",
        expand_secret_references=False)
    payload = json.loads(value.secretValue)
    if payload.get("version") != 1 or set(payload.get("files", {})) != set(FILES):
        raise SystemExit("Unsupported or incomplete private site backup")
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    for name in FILES:
        if not isinstance(payload["files"][name], str):
            raise SystemExit("Invalid private configuration file")
    for name, content in payload["files"].items():
        secure_write(SITE / name, content)
    print("Private deployment metadata restored; values hidden.")


def prepare():
    data = read_site()
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    if RUNTIME.is_symlink():
        raise SystemExit("Refusing a symlink at the private runtime path")
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    os.chmod(RUNTIME, 0o700)
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT).split(b"\0")
    selected = set()
    for raw in paths:
        if not raw:
            continue
        name = os.fsdecode(raw)
        source = ROOT / name
        if ".private" in pathlib.PurePosixPath(name).parts:
            raise SystemExit("Private data selected by Git; repair the publication exclusions.")
        if source.is_symlink() or not source.is_file():
            raise SystemExit("Publication source contains a non-regular file; run make check-public.")
        target = RUNTIME / name
        if target.is_symlink():
            raise SystemExit("Refusing to overwrite a symlink in the private runtime")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        selected.add(name)
    # Remove obsolete copied source files, never generated state or local runtime data.
    previous = RUNTIME / ".public-source-files.json"
    for stale in set(json.loads(previous.read_text()) if previous.exists() else []) - selected:
        path = RUNTIME / stale
        if path.is_file() or path.is_symlink():
            path.unlink()
    secure_write(previous, json.dumps(sorted(selected)))
    for name, content in data.items():
        secure_write(RUNTIME / name, content)
    secure_write(RUNTIME / ".private-runtime", "private deployment\n")
    env = dict(os.environ, ARTZIMA_PRIVATE_RUNTIME="1")
    subprocess.run([sys.executable, "scripts/render.py"], cwd=RUNTIME, env=env,
                   check=True, stdout=subprocess.DEVNULL)
    for name in ("k8s/apps/routes/routes.yaml", "k8s/apps/backups/volsync.yaml",
                 "k8s/secrets/backups/restic.yaml", "k8s/apps/backups/maintenance.yaml"):
        if (RUNTIME / name).read_bytes() != (ROOT / name).read_bytes():
            raise SystemExit("Private generated app resources differ from public templates; parameterize them before deployment.")
    # Printed operator documents use actual addresses, but stay in the private tree.
    import yaml
    example_net = yaml.safe_load((ROOT / "ansible/group_vars/all/network.yml").read_text())
    real_net = yaml.safe_load(data["ansible/group_vars/all/network.yml"])
    for doc in (RUNTIME / "docs").glob("*.md"):
        text = doc.read_text()
        for key in ("lab_subnet", "lab_gateway", "k8s_pod_subnet", "k8s_service_subnet"):
            text = text.replace(str(example_net[key]), str(real_net[key]))
        # The Firewalla's VPN: only its subnet and server address (the first one), exactly;
        # its example prefix also appears in unrelated examples.
        if "firewalla_vpn_subnet" in example_net and "firewalla_vpn_subnet" in real_net:
            ex_vpn = ipaddress.ip_network(example_net["firewalla_vpn_subnet"])
            real_vpn = ipaddress.ip_network(real_net["firewalla_vpn_subnet"])
            for old, new in ((ex_vpn, real_vpn), (ex_vpn.network_address + 1, real_vpn.network_address + 1)):
                text = re.sub(r"(?<![\d.])" + re.escape(str(old)) + r"(?!\d)", str(new), text)
        # Lab prefix, and the home network on the Firewalla WAN side (documentation only).
        for key in ("lab_subnet", "home_subnet"):
            if key not in example_net or key not in real_net:
                continue
            text = text.replace(str(example_net[key]), str(real_net[key]))
            example_prefix = str(example_net[key]).rsplit(".", 1)[0] + "."
            real_prefix = str(real_net[key]).rsplit(".", 1)[0] + "."
            if str(real_net[key]).endswith("/24") and str(example_net[key]).endswith("/24"):
                text = text.replace(example_prefix, real_prefix)
        secure_write(doc, text)
    print("Private runtime prepared.", flush=True)
    return env


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "run":
        env = prepare()
        goals = sys.argv[2:]
        if not goals:
            raise SystemExit("No make target supplied")
        raise SystemExit(subprocess.call(["make", "--no-print-directory", *goals],
                                        cwd=RUNTIME, env=env))
    if command == "prepare":
        prepare()
    elif command == "backup":
        backup()
    elif command == "restore":
        restore()
    elif command == "check":
        read_site()
        print("Private deployment configuration present; values hidden.")
    else:
        raise SystemExit("Unknown private-site command")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SDK exceptions can contain the whole private JSON request or response.
        raise SystemExit(f"Private configuration operation failed: {type(error).__name__} (details hidden)") from None
