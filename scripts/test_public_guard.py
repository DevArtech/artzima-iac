#!/usr/bin/env python3
"""Publication-guard regressions. Temporary repositories; no real credentials."""
from __future__ import annotations
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class PublicGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="artzima-public-guard-")
        self.addCleanup(self.temp.cleanup)
        self.repo = pathlib.Path(self.temp.name)
        (self.repo / "scripts").mkdir()
        for name in ("scripts/check_public.sh", "scripts/check_metadata.py", ".gitleaks.toml"):
            shutil.copy2(ROOT / name, self.repo / name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.env = dict(os.environ)
        tools = pathlib.Path(self.env.get("ARTZIMA_TOOLS",
                                        pathlib.Path.home() / ".local/share/artzima-tools"))
        self.env["PATH"] = str(tools / "bin") + os.pathsep + self.env["PATH"]

    def run_guard(self):
        return subprocess.run(["bash", "scripts/check_public.sh"], cwd=self.repo,
                              env=self.env, text=True, capture_output=True)

    def test_clean_examples_pass(self):
        (self.repo / "local.env.example").write_text("TOKEN=replace-me\n")
        self.assertEqual(self.run_guard().returncode, 0)

    def test_detectable_secret_is_rejected(self):
        # Synthetic credential, assembled so this fixture is not itself a finding.
        (self.repo / "accidental.txt").write_text("token=" + "ghp_" + "Ab9x" * 9 + "\n")
        self.assertNotEqual(self.run_guard().returncode, 0)

    def test_ignored_private_files_stay_out(self):
        (self.repo / ".gitignore").write_text("private.key\n")
        (self.repo / "private.key").write_text("opaque material\n")
        self.assertEqual(self.run_guard().returncode, 0)

    def test_forced_private_files_are_rejected_even_without_known_secret_format(self):
        for name in ("private.key", "backup.p12", "local.env", "production.tfvars",
                     ".env.production", "kubeconfig-admin"):
            with self.subTest(name=name):
                path = self.repo / name
                path.write_text("opaque material\n")
                subprocess.run(["git", "add", "-f", "--", name], cwd=self.repo, check=True)
                result = self.run_guard()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("secret-bearing", result.stderr)
                subprocess.run(["git", "rm", "--cached", "-q", "--", name],
                               cwd=self.repo, check=True)
                path.unlink()

    def test_private_metadata_is_rejected(self):
        # Construct synthetic values; the test source must itself remain publishable.
        cases = ["10" + ".12.34.56", "aa" + ":bb:cc:dd:ee:ff",
                 "infisical_project_slug: " + "private-project-id"]
        for value in cases:
            with self.subTest(kind=value.split(":")[0]):
                path = self.repo / "accidental.txt"
                path.write_text(value + "\n")
                self.assertNotEqual(self.run_guard().returncode, 0)
                path.unlink()

    def test_role_assignments_in_public_inventory_are_rejected(self):
        path = self.repo / "ansible/hosts.yml"
        path.parent.mkdir()
        path.write_text("example_inventory_only: true\nlab_roles: [private-role]\n")
        self.assertNotEqual(self.run_guard().returncode, 0)

    def test_force_added_private_directory_is_rejected(self):
        path = self.repo / ".private/site/metadata.txt"
        path.parent.mkdir(parents=True)
        path.write_text("private role assignments\n")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret-bearing", result.stderr)

    def test_symlink_is_rejected(self):
        (self.repo / "unscanned").symlink_to("/etc/hosts")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-regular publication path", result.stderr)

    def test_missing_indexed_file_is_not_silently_skipped(self):
        path = self.repo / "missing.txt"
        path.write_text("data\n")
        subprocess.run(["git", "add", "--", path.name], cwd=self.repo, check=True)
        path.unlink()
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-regular publication path", result.stderr)


if __name__ == "__main__":
    unittest.main()
