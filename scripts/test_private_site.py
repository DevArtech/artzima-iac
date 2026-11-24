#!/usr/bin/env python3
"""Private deployment isolation regressions; fictional metadata only."""
from __future__ import annotations
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import private_site


class PrivateSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="artzima-site-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.private = self.root / ".private"
        self.site = self.private / "site"
        self.runtime = self.private / "runtime"
        for name in ("k8s/apps/routes/routes.yaml", "k8s/apps/backups/volsync.yaml",
                     "k8s/secrets/backups/restic.yaml", "k8s/apps/backups/maintenance.yaml"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("example\n")
        self.files = ["ansible/hosts.yml", "ansible/group_vars/all/network.yml"]
        for key, value in {"ROOT": self.root, "PRIVATE": self.private, "SITE": self.site,
                           "RUNTIME": self.runtime, "FILES": self.files}.items():
            p = patch.object(private_site, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.net = ("lab_subnet: 192.0.2.0/24\nlab_gateway: 192.0.2.1\n"
                    "k8s_pod_subnet: 198.18.0.0/16\nk8s_service_subnet: 198.19.0.0/16\n")
        for name in self.files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.net if name.endswith("network.yml") else "example-role\n")

    def create_site(self):
        for name in self.files:
            path = self.site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.net if name.endswith("network.yml") else "private-role\n")

    def prepare(self, paths=None):
        names = paths or self.files + [str(p.relative_to(self.root)) for p in (self.root / "k8s").rglob("*.yaml")]
        selected = b"\0".join(n.encode() for n in names) + b"\0"
        with patch.object(private_site.subprocess, "check_output", return_value=selected), \
             patch.object(private_site.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            return private_site.prepare()

    def test_missing_private_configuration_refuses(self):
        with self.assertRaises(SystemExit):
            private_site.read_site()

    def test_private_overlay_cannot_change_public_source(self):
        self.create_site()
        before = {n: (self.root / n).read_bytes() for n in self.files}
        self.prepare()
        self.assertEqual((self.runtime / self.files[0]).read_text(), "private-role\n")
        self.assertEqual(before, {n: (self.root / n).read_bytes() for n in self.files})
        self.assertEqual((self.runtime.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.runtime / self.files[0]).stat().st_mode & 0o777), 0o600)

    def test_preparation_preserves_generated_state(self):
        self.create_site()
        path = self.runtime / "tofu/terraform.tfstate"
        path.parent.mkdir(parents=True)
        path.write_text("private state\n")
        self.prepare()
        self.prepare()
        self.assertEqual(path.read_text(), "private state\n")

    def test_force_selected_private_path_refuses(self):
        self.create_site()
        with self.assertRaises(SystemExit):
            self.prepare([".private/site/ansible/hosts.yml"])

    def test_source_symlink_refuses(self):
        self.create_site()
        path = self.root / "link"
        path.symlink_to("/etc/hosts")
        with self.assertRaises(SystemExit):
            self.prepare(["link"])

    def test_operator_docs_get_lab_and_home_prefixes(self):
        public = self.net + "home_subnet: 198.51.100.0/24\n"
        site = ("lab_subnet: 203.0.113.0/24\nlab_gateway: 203.0.113.1\n"
                "k8s_pod_subnet: 198.18.0.0/16\nk8s_service_subnet: 198.19.0.0/16\n"
                "home_subnet: 100.64.0.0/24\n")  # fictional stand-ins for the private values
        (self.root / self.files[1]).write_text(public)
        self.create_site()
        (self.site / self.files[1]).write_text(site)
        doc = self.root / "docs/runbook.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("NAS 192.0.2.156 on 192.0.2.0/24; Firewalla WAN 198.51.100.251\n")
        self.prepare(self.files + ["docs/runbook.md"] +
                     [str(p.relative_to(self.root)) for p in (self.root / "k8s").rglob("*.yaml")])
        self.assertEqual((self.runtime / "docs/runbook.md").read_text(),
                         "NAS 203.0.113.156 on 203.0.113.0/24; Firewalla WAN 100.64.0.251\n")
        self.assertIn("192.0.2.156", doc.read_text())  # public source untouched

    def test_operator_docs_get_the_vpn_server_only(self):
        (self.root / self.files[1]).write_text(self.net + "firewalla_vpn_subnet: 203.0.113.0/24\n")
        self.create_site()
        (self.site / self.files[1]).write_text(self.net + "firewalla_vpn_subnet: 100.64.9.0/24\n")
        doc = self.root / "docs/runbook.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("ssh pi@203.0.113.1; AllowedIPs 203.0.113.1/32 of 203.0.113.0/24; "
                       "other example 203.0.113.2/32, 203.0.113.17\n")
        self.prepare(self.files + ["docs/runbook.md"] +
                     [str(p.relative_to(self.root)) for p in (self.root / "k8s").rglob("*.yaml")])
        self.assertEqual((self.runtime / "docs/runbook.md").read_text(),
                         "ssh pi@100.64.9.1; AllowedIPs 100.64.9.1/32 of 100.64.9.0/24; "
                         "other example 203.0.113.2/32, 203.0.113.17\n")

    def test_operator_docs_without_home_subnet_still_prepare(self):
        self.create_site()
        doc = self.root / "docs/runbook.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("198.51.100.7 stays\n")
        self.prepare(self.files + ["docs/runbook.md"] +
                     [str(p.relative_to(self.root)) for p in (self.root / "k8s").rglob("*.yaml")])
        self.assertEqual((self.runtime / "docs/runbook.md").read_text(), "198.51.100.7 stays\n")

    def test_dangling_symlink_still_refuses(self):
        self.create_site()
        link = self.root / "dangling"
        link.symlink_to(self.root / "does-not-exist")
        with self.assertRaises(SystemExit):
            self.prepare(["dangling"])

    def test_restore_cannot_overwrite_existing_site(self):
        self.create_site()
        with self.assertRaises(SystemExit):
            private_site.restore()


if __name__ == "__main__":
    unittest.main()
