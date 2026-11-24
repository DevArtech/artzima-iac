#!/usr/bin/env python3
"""Tiny answer server for the Proxmox automated installer (stdlib only).

  POST /answer     installer system-info JSON -> that MAC's answer.toml
  POST /installed  post-installation webhook -> answers/installed/<host>.json
  GET  /healthz    "ok"

The installer POSTs JSON with "network_interfaces": [{"link":..,"mac":..}]
and "product": {"product": "pve"|"pbs"}; see
https://pve.proxmox.com/wiki/Automated_Installation#System_information_POST_data

Answers are rendered IN MEMORY at startup by install/render_answers.py from
the inventory plus ADMIN_SSH_KEYS / ADMIN_EMAIL / optional ROOT_PASSWORD_HASHED
in the environment (`make answer-server` fetches them from Infisical). Nothing
is written to disk except the install webhook reports (gitignored). Run on a
trusted LAN only, and only while installing (plain HTTP; an answer contains the
root password hash, a random discarded one unless you set your own). Usage:

  make answer-server [ANSWER_PORT=8000] [POSTINST_WEBHOOK_URL=http://<ws>:8000/installed]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import render_answers  # noqa: E402

ANSWERS = pathlib.Path(__file__).resolve().parent / "answers"  # webhook reports only (gitignored)
MAX_BODY = 1 << 20
RENDERED: dict[str, dict] = {}  # mac -> {"host", "product", "text"}, filled in main()


class Handler(BaseHTTPRequestHandler):
    server_version = "artzima-answer/1"

    def _send(self, code: int, body: str, ctype: str = "text/plain") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return None

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, "ok\n")
        else:
            self._send(404, "not found\n")

    def do_POST(self) -> None:  # noqa: N802
        info = self._json_body()
        if info is None:
            self._send(400, "expected a JSON body\n")
            return
        if self.path.rstrip("/") == "/answer":
            self.answer(info)
        elif self.path.rstrip("/") == "/installed":
            self.installed(info)
        else:
            self._send(404, "not found\n")

    def answer(self, info: dict) -> None:
        product = (info.get("product") or {}).get("product")
        macs = [str(n.get("mac", "")).lower() for n in info.get("network_interfaces", [])]
        for mac in macs:
            entry = RENDERED.get(mac)
            if not entry:
                continue
            if product and entry["product"] != product:
                logging.error("%s (%s) booted a %s ISO but is a %s host; refusing",
                              entry["host"], mac, product, entry["product"])
                self._send(409, f"wrong ISO: {entry['host']} needs {entry['product']}\n")
                return
            # Second guardrail (the answer's disk filter is the first): if the installer
            # reports its disks, the expected install disk serial must be among them.
            disks = info.get("disks")
            if disks is not None and entry.get("serial") and entry["serial"] not in json.dumps(disks):
                logging.error("%s (%s): install disk serial %s not found on this machine; refusing",
                              entry["host"], mac, entry["serial"])
                self._send(412, f"install disk serial for {entry['host']} not present: check hosts.yml\n")
                return
            # Third guardrail: a machine that already reported a finished install (the
            # stick was left in and it booted the installer again) gets no second answer,
            # so the installer stops before touching a disk. Delete
            # answers/installed/<host>.json (or `make clean-answers`) to reinstall on purpose.
            if (ANSWERS / "installed" / f"{entry['host']}.json").exists():
                logging.error("%s (%s) already installed; refusing a second install "
                              "(remove the stick, or delete answers/installed/%s.json)",
                              entry["host"], mac, entry["host"])
                self._send(409, f"{entry['host']} is already installed: remove the USB stick\n")
                return
            logging.info("serving the answer for %s (%s, peer %s)", entry["host"], mac,
                         self.client_address[0])
            self._send(200, entry["text"], "application/toml")
            return
        logging.warning("no answer for MACs %s (peer %s)", macs, self.client_address[0])
        self._send(404, "no answer file for this machine\n")

    def installed(self, info: dict) -> None:
        out = ANSWERS / "installed"
        out.mkdir(parents=True, exist_ok=True)
        host = str(info.get("fqdn") or info.get("machine-id") or "unknown").split(".")[0]
        host = "".join(c for c in host if c.isalnum() or c in "-_") or "unknown"
        (out / f"{host}.json").write_text(json.dumps(info, indent=2))
        keys = info.get("ssh-public-host-keys") or {}
        logging.info("install finished: %s; SSH host keys: %s", host,
                     ", ".join(f"{k}={v}" for k, v in keys.items()) or "n/a")
        self._send(200, "ok\n")

    def log_message(self, fmt: str, *args) -> None:
        logging.debug("%s " + fmt, self.client_address[0], *args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    RENDERED.update(render_answers.render(os.environ.get("POSTINST_WEBHOOK_URL")))
    for mac, a in sorted(RENDERED.items(), key=lambda kv: kv[1]["host"]):
        logging.info("answer ready (in memory): %s %s %s", a["host"], a["product"], mac)
    logging.info("answer server on http://%s:%d/answer", args.bind, args.port)
    ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
