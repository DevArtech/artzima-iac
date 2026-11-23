#!/usr/bin/env python3
"""Keep mixed offline/live make requests out of the wrong tree."""
from __future__ import annotations
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class MakeDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="artzima-make-dispatch-")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        shutil.copy2(ROOT / "Makefile", self.root / "Makefile")
        (self.root / "mk").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "mk/targets.mk").write_text(
            ".PHONY: render help\nrender:\n\t@echo render >> dispatch.log\n"
            "help:\n\t@echo help >> dispatch.log\n")
        (self.root / "scripts/private_site.py").write_text(
            "import pathlib,sys\n"
            "with pathlib.Path('dispatch.log').open('a') as f:\n"
            " f.write('private:' + ','.join(sys.argv[1:]) + '\\n')\n")

    def run_make(self, *goals):
        env = dict(os.environ)
        for key in ("ARTZIMA_PRIVATE_RUNTIME", "ARTZIMA_PUBLIC_OFFLINE", "MAKEFLAGS", "MFLAGS"):
            env.pop(key, None)
        result = subprocess.run(["make", "--no-print-directory", f"SITE_PY={sys.executable}", *goals],
                                cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.root / "dispatch.log").read_text().splitlines()

    def test_mixed_render_and_live_command_have_separate_execution(self):
        self.assertEqual(self.run_make("render", "dr-pdf"), ["render", "private:run,dr-pdf"])

    def test_private_management_command_is_not_repeated(self):
        self.assertEqual(self.run_make("private-check", "dr-pdf"),
                         ["private:check", "private:run,dr-pdf"])

    def test_offline_commands_do_not_prepare_a_private_runtime(self):
        self.assertEqual(self.run_make("render", "help"), ["render", "help"])


if __name__ == "__main__":
    unittest.main()
