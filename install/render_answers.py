#!/usr/bin/env python3
"""Render the Proxmox auto-install answer file for each bare-metal host, IN MEMORY.

Nothing is written to the repo: install/answer_server.py imports render() and
renders at serve time. Running this file directly (`make answers`) renders
into a temporary directory, runs `proxmox-auto-install-assistant
validate-answer` if available, and deletes it again.

Answer-file schema: https://pve.proxmox.com/wiki/Automated_Installation
(kebab-case keys, PVE >= 9.1 / PBS >= 4.1 for interface-name pinning).

Inputs from the environment (filled from Infisical by scripts/infisical_exec.py,
see the Makefile targets `answers` / `answer-server`; nothing personal in git):
  ADMIN_SSH_KEYS        root SSH public keys, one per line  (/settings/ADMIN_SSH_KEYS)
  ADMIN_EMAIL           mailbox for notifications            (/settings/ADMIN_EMAIL)
  ROOT_PASSWORD_HASHED  optional crypt hash                  (/bare-metal/ROOT_PASSWORD_HASHED)

Root password policy (no hashes in git, ever): if ROOT_PASSWORD_HASHED is not set,
a random 48-byte password is hashed with `openssl passwd -6` and thrown away:
nobody knows it, so root is SSH-key-only (console recovery: single-user boot).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import labdata  # noqa: E402


PRODUCT_BY_GROUP = {"pve_nodes": "pve", "pbs_servers": "pbs"}


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else toml_value(k)


def to_toml(doc: dict) -> str:
    """Minimal TOML writer: top-level tables of scalars/lists, dotted sub-tables."""
    lines: list[str] = []

    def emit(prefix: str, table: dict) -> None:
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        subs = {k: v for k, v in table.items() if isinstance(v, dict)}
        lines.append(f"[{prefix}]")
        for k, v in scalars.items():
            if v is not None:
                lines.append(f"{toml_key(k)} = {toml_value(v)}")
        lines.append("")
        for k, v in subs.items():
            emit(f"{prefix}.{toml_key(k)}", v)

    for name, table in doc.items():
        emit(name, table)
    return "\n".join(lines)


def root_hash() -> str:
    given = os.environ.get("ROOT_PASSWORD_HASHED", "").strip()
    if given:
        return given
    if not shutil.which("openssl"):
        raise SystemExit("openssl not found (needed to hash a random root password)")
    pw = secrets.token_urlsafe(48)
    res = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=pw, capture_output=True, text=True, check=True,
    )
    del pw
    return res.stdout.strip()


def admin_inputs(allow_placeholder: bool = False) -> tuple[list[str], str]:
    keys = [k.strip() for k in os.environ.get("ADMIN_SSH_KEYS", "").splitlines() if k.strip()]
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    if not keys or not email:
        if not allow_placeholder:
            raise SystemExit("ADMIN_SSH_KEYS / ADMIN_EMAIL not set: run via `make answers` / "
                             "`make answer-server` (they come from Infisical /settings)")
        keys = keys or ["ssh-ed25519 AAAAPLACEHOLDER placeholder@validation"]
        email = email or "root@example.invalid"
    return keys, email


def render(webhook: str | None = None, allow_placeholder: bool = False) -> dict[str, dict]:
    """-> {mac: {"host", "product", "ip", "text"}} for every PVE/PBS host."""
    data = labdata.load()
    if not allow_placeholder and not (labdata.REPO / ".private-runtime").is_file():
        raise SystemExit("Actual installer answers require the private deployment runtime; use make answers")
    net, lab = data["net"], data["lab"]
    keys, email = admin_inputs(allow_placeholder)
    pw_hash = root_hash()  # one hash for all hosts in this render
    out: dict[str, dict] = {}

    for name, h in sorted(data["hosts"].items()):
        product = next((PRODUCT_BY_GROUP[g] for g in h["groups"] if g in PRODUCT_BY_GROUP), None)
        if not product:
            continue
        mac = h["mac"].lower()
        serial = str(h.get("install_disk_serial") or "").strip()
        if not serial or serial.upper().startswith("TODO"):
            if not allow_placeholder:
                raise SystemExit(f"{name}: install_disk_serial is not set in ansible/hosts.yml "
                                 "(the answer file targets the install disk by serial)")
            serial = "PLACEHOLDER-SERIAL"
        doc = {
            "global": {
                "keyboard": lab["keyboard"],
                "country": lab["country"],
                "fqdn": h["fqdn"],
                "mailto": email,
                "timezone": lab["timezone"],
                "root-password-hashed": pw_hash,
                "root-ssh-keys": keys,
                "reboot-mode": "reboot",
            },
            "network": {
                "source": "from-answer",
                "cidr": f"{h['ip']}/{data['prefix']}",
                "gateway": net["lab_gateway"],
                "dns": net["lab_dns"][0],
                # ID_NET_NAME_MAC is "enx" + MAC without colons.
                "filter": {"ID_NET_NAME_MAC": "*" + mac.replace(":", "")},
                "interface-name-pinning": {
                    "enabled": True,
                    "mapping": {mac: lab["primary_nic_name"]},
                },
            },
            # Target the install disk by its SERIAL (udev ID_SERIAL), never by a device
            # name that can change with enumeration order: a wrong or missing disk
            # matches nothing and the installer stops instead of wiping another disk.
            "disk-setup": {
                "filesystem": "ext4",
                "filter-match": "all",
                "filter": {"ID_SERIAL": f"*{serial}*"},
            },
            "first-boot": {
                "source": "from-iso",
                "ordering": "network-online",
            },
        }
        if webhook:
            doc["post-installation-webhook"] = {"url": webhook}
        text = (
            f"# {name} ({product}), generated by install/render_answers.py; DO NOT EDIT.\n"
            f"# NOTE: on a VLAN later, the installer still runs untagged/native.\n"
            + to_toml(doc)
        )
        out[mac] = {"host": name, "product": product, "ip": h["ip"], "text": text, "serial": serial}
    return out


def main() -> None:
    """Validate: render to a temp dir, validate-answer, delete."""
    answers = render(os.environ.get("POSTINST_WEBHOOK_URL"),
                     allow_placeholder="--allow-placeholder" in sys.argv)
    with tempfile.TemporaryDirectory(prefix="artzima-answers-") as tmp:
        os.chmod(tmp, 0o700)
        for mac, a in answers.items():
            path = pathlib.Path(tmp) / f"{mac}.toml"
            path.write_text(a["text"])
            print(f"rendered {a['host']} ({a['product']}, {a['ip']}, {mac})")
            if shutil.which("proxmox-auto-install-assistant"):
                subprocess.run(["proxmox-auto-install-assistant", "validate-answer", str(path)], check=True)
        if not shutil.which("proxmox-auto-install-assistant"):
            print("note: proxmox-auto-install-assistant not on PATH, skipped validate-answer")
    print("nothing written: `make answer-server` renders the same answers in memory at serve time")


if __name__ == "__main__":
    main()
