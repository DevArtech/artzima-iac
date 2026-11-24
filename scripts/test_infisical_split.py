#!/usr/bin/env python3
"""Two Infisical projects: routing, secrets-check logic and the one-time migration.
Offline: a fake Infisical client; fictional values only (make lint)."""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import infisical_projects  # noqa: E402
import secrets_check  # noqa: E402
import secrets_migrate_k8s as mig  # noqa: E402

MAIN, K8S = "proj-main", "proj-k8s"
CFG = {"infisical_env": "prod", "infisical_project_id": MAIN, "infisical_k8s_project_id": K8S,
       "infisical_project_slug": "example-project", "infisical_k8s_project_slug": "example-k8s-project"}
SECRET_MARK = "fictional-value-"


class FakeInfisical:
    """projects -> {folder path: {KEY: value}}; a folder exists iff it is a key."""

    def __init__(self, data: dict[str, dict[str, dict[str, str]]]):
        self.data = {p: {"/": {}, **{f: dict(v) for f, v in d.items()}} for p, d in data.items()}
        self.calls: list[tuple] = []
        outer = self

        class Folders:
            def list_folders(self, project_id, environment_slug, path):
                proj = outer.data[project_id]
                if path not in proj:
                    raise RuntimeError("folder not found")
                pre = path.rstrip("/") + "/"
                names = {f[len(pre):].split("/")[0] for f in proj if f.startswith(pre) and f != path}
                return types.SimpleNamespace(folders=[types.SimpleNamespace(name=n) for n in sorted(names)])

            def create_folder(self, name, environment_slug, project_id, path):
                outer.calls.append(("create_folder", project_id, path, name))
                outer.data[project_id][(path.rstrip("/") + "/" + name) if path != "/" else "/" + name] = {}

        class Secrets:
            def _folder(self, project_id, secret_path):
                proj = outer.data[project_id]
                if secret_path not in proj:
                    raise RuntimeError("folder not found")
                return proj[secret_path]

            def list_secrets(self, project_id, environment_slug, secret_path, view_secret_value=False, **_):
                f = self._folder(project_id, secret_path)
                return types.SimpleNamespace(secrets=[
                    types.SimpleNamespace(secretKey=k, secretValue=v if view_secret_value else None)
                    for k, v in f.items()])

            def get_secret_by_name(self, secret_name, project_id, environment_slug, secret_path, **_):
                return types.SimpleNamespace(secretValue=self._folder(project_id, secret_path)[secret_name])

            def create_secret_by_name(self, secret_name, project_id, environment_slug, secret_path, secret_value, **_):
                f = self._folder(project_id, secret_path)
                if secret_name in f:
                    raise RuntimeError("already exists")
                outer.calls.append(("create", project_id, secret_path, secret_name))
                f[secret_name] = secret_value

            def update_secret_by_name(self, current_secret_name, project_id, environment_slug, secret_path, secret_value, **_):
                outer.calls.append(("update", project_id, secret_path, current_secret_name))
                self._folder(project_id, secret_path)[current_secret_name] = secret_value

            def delete_secret_by_name(self, secret_name, project_id, environment_slug, secret_path, **_):
                outer.calls.append(("delete", project_id, secret_path, secret_name))
                del self._folder(project_id, secret_path)[secret_name]

        self.folders, self.secrets = Folders(), Secrets()


def quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*a, **kw)
    return result, out.getvalue()


class RoutingTests(unittest.TestCase):
    def test_k8s_paths_go_to_the_k8s_project(self):
        for path in ("/k8s", "/k8s/media", "/k8s/media/SONARR_API_KEY", "/k8s/settings/PUBLIC_DOMAIN"):
            self.assertEqual(infisical_projects.project_of(path), "k8s", path)
            self.assertEqual(infisical_projects.project_id(CFG, path), K8S)

    def test_everything_else_stays_main(self):
        for path in ("/settings", "/tofu/ESO_CLIENT_ID", "/ansible", "/operator", "/bare-metal", "/k8sfoo", "/"):
            self.assertEqual(infisical_projects.project_of(path), "main", path)

    def test_unset_project_id_refuses(self):
        with self.assertRaises(SystemExit):
            infisical_projects.project_id({**CFG, "infisical_k8s_project_id": "REPLACE_VIA_INFISICAL_PROJECT_ENV"}, "/k8s/x")

    def test_manifest_and_code_agree(self):
        folders, secrets = secrets_check.load_manifest()
        self.assertEqual(infisical_projects.project_of("/k8s/settings"), "k8s")
        self.assertIn("/k8s/settings", folders)
        self.assertEqual(secrets["/k8s/settings"]["PUBLIC_DOMAIN"]["mirror_of"], "/settings/PUBLIC_DOMAIN")
        self.assertEqual(secrets_check.project_wiring_errors(), [])  # tofu + ansible use the right projects


def manifest_projects():
    folders, secrets = secrets_check.load_manifest()
    main = {f: {} for f in folders if infisical_projects.project_of(f) == "main"}
    k8s = {f: {} for f in folders if infisical_projects.project_of(f) == "k8s"}
    return folders, secrets, main, k8s


class SecretsCheckTests(unittest.TestCase):
    def full(self):
        folders, secrets, main, k8s = manifest_projects()
        for f, keys in secrets.items():
            target = k8s if infisical_projects.project_of(f) == "k8s" else main
            for k in keys:
                target[f][k] = SECRET_MARK + k
        k8s["/k8s/settings"]["PUBLIC_DOMAIN"] = main["/settings"]["PUBLIC_DOMAIN"]
        return folders, secrets, main, k8s

    def test_complete_split_is_clean(self):
        folders, secrets, main, k8s = self.full()
        r = secrets_check.check_projects(FakeInfisical({MAIN: main, K8S: k8s}), CFG, folders, secrets)
        for key in ("missing_folders", "missing_required", "to_generate", "pending", "mirror_drift", "old_main_copies", "unknown"):
            self.assertEqual(r[key], [], key)

    def test_k8s_keys_are_looked_up_in_the_k8s_project_only(self):
        folders, secrets, main, k8s = self.full()
        # a /k8s key that only exists in MAIN counts as missing (the cluster can't read main)
        main["/k8s/media"] = {"SONARR_API_KEY": "x"}
        main["/k8s"] = {}
        del k8s["/k8s/media"]["SONARR_API_KEY"]
        r = secrets_check.check_projects(FakeInfisical({MAIN: main, K8S: k8s}), CFG, folders, secrets)
        self.assertIn("/k8s/media/SONARR_API_KEY  (hex:16)", r["to_generate"])
        self.assertEqual(r["old_main_copies"], ["/k8s/media/SONARR_API_KEY"])

    def test_mirror_drift_fails_without_printing_values(self):
        folders, secrets, main, k8s = self.full()
        k8s["/k8s/settings"]["PUBLIC_DOMAIN"] = "old.example.org"
        r = secrets_check.check_projects(FakeInfisical({MAIN: main, K8S: k8s}), CFG, folders, secrets)
        self.assertEqual(len(r["mirror_drift"]), 1)
        self.assertNotIn("old.example.org", " ".join(r["mirror_drift"]))

    def test_create_folders_in_their_own_project(self):
        folders, secrets, _, _ = manifest_projects()
        fake = FakeInfisical({MAIN: {}, K8S: {}})
        r = secrets_check.check_projects(fake, CFG, folders, secrets, create_folders=True)
        self.assertEqual(r["missing_folders"], [])
        created = {(p, path, name) for op, p, path, name in fake.calls if op == "create_folder"}
        self.assertIn((K8S, "/", "k8s"), created)
        self.assertIn((K8S, "/k8s", "settings"), created)
        self.assertIn((MAIN, "/", "tofu"), created)
        self.assertNotIn((MAIN, "/", "k8s"), created)
        self.assertFalse(any(p == K8S and path == "/" and name != "k8s" for p, path, name in created))


class MigrationTests(unittest.TestCase):
    TOFU = {("/k8s/backup", "PG_B2_KEY_ID"), ("/k8s/cloudflared", "TUNNEL_TOKEN")}

    def setup_main(self):
        folders, secrets, main, k8s = manifest_projects()
        main["/settings"]["PUBLIC_DOMAIN"] = "fictional.example.org"
        main["/k8s"] = {}
        main["/k8s/media"] = {"SONARR_API_KEY": SECRET_MARK + "sonarr", "JELLYFIN_API_KEY": ""}
        main["/k8s/backup"] = {"RESTIC_PASSWORD": SECRET_MARK + "restic", "PG_B2_KEY_ID": SECRET_MARK + "tofu"}
        main["/k8s/extra"] = {"UNLISTED": SECRET_MARK + "unlisted"}  # not in the manifest: copied anyway
        return folders, secrets, main

    def run_plan(self, fake, folders, secrets):
        return mig.plan(mig.Store(fake, "prod"), MAIN, K8S, folders, secrets, self.TOFU)

    def test_dry_run_writes_nothing_and_prints_no_values(self):
        folders, secrets, main = self.setup_main()
        fake = FakeInfisical({MAIN: main, K8S: {}})
        p = self.run_plan(fake, folders, secrets)
        _, out = quiet(mig.report, p)
        self.assertEqual(fake.calls, [])
        self.assertNotIn(SECRET_MARK, out)
        self.assertNotIn("fictional.example.org", out)
        actions = {(i["folder"], i["key"]): i["action"] for i in p["items"]}
        self.assertEqual(actions[("/k8s/backup", "PG_B2_KEY_ID")], "tofu")
        self.assertEqual(actions[("/k8s/media", "SONARR_API_KEY")], "copy")
        self.assertEqual(actions[("/k8s/extra", "UNLISTED")], "copy")

    def test_apply_copies_verifies_and_is_idempotent(self):
        folders, secrets, main = self.setup_main()
        fake = FakeInfisical({MAIN: main, K8S: {}})
        p = self.run_plan(fake, folders, secrets)
        _, out = quiet(mig.apply, mig.Store(fake, "prod"), MAIN, K8S, p)
        self.assertNotIn(SECRET_MARK, out)
        k8s = fake.data[K8S]
        self.assertEqual(k8s["/k8s/media"]["SONARR_API_KEY"], SECRET_MARK + "sonarr")
        self.assertEqual(k8s["/k8s/media"]["JELLYFIN_API_KEY"], "")
        self.assertEqual(k8s["/k8s/extra"]["UNLISTED"], SECRET_MARK + "unlisted")
        self.assertEqual(k8s["/k8s/settings"]["PUBLIC_DOMAIN"], "fictional.example.org")
        self.assertNotIn("PG_B2_KEY_ID", k8s.get("/k8s/backup", {}))  # left to OpenTofu
        self.assertEqual(fake.data[MAIN]["/k8s/media"]["SONARR_API_KEY"], SECRET_MARK + "sonarr")  # main untouched
        ok, bad = mig.verify(mig.Store(fake, "prod"), K8S, p)
        self.assertEqual(bad, [])
        self.assertGreater(ok, 0)
        again = self.run_plan(fake, folders, secrets)
        self.assertFalse(any(i["action"] == "copy" for i in again["items"]))
        self.assertTrue(all(m["action"] == "same" for m in again["mirrors"]))

    def test_never_overwrites_a_different_value(self):
        folders, secrets, main = self.setup_main()
        fake = FakeInfisical({MAIN: main, K8S: {"/k8s": {}, "/k8s/media": {"SONARR_API_KEY": "different"}}})
        p = self.run_plan(fake, folders, secrets)
        actions = {(i["folder"], i["key"]): i["action"] for i in p["items"]}
        self.assertEqual(actions[("/k8s/media", "SONARR_API_KEY")], "CONFLICT")
        quiet(mig.apply, mig.Store(fake, "prod"), MAIN, K8S, p)
        self.assertEqual(fake.data[K8S]["/k8s/media"]["SONARR_API_KEY"], "different")
        _, bad = mig.verify(mig.Store(fake, "prod"), K8S, p)
        self.assertIn("/k8s/media/SONARR_API_KEY", bad)

    def test_mirror_follows_its_source(self):
        folders, secrets, main = self.setup_main()
        fake = FakeInfisical({MAIN: main, K8S: {"/k8s": {}, "/k8s/settings": {"PUBLIC_DOMAIN": "old.example.org"}}})
        p = self.run_plan(fake, folders, secrets)
        self.assertEqual([m["action"] for m in p["mirrors"]], ["update"])
        quiet(mig.apply, mig.Store(fake, "prod"), MAIN, K8S, p)
        self.assertEqual(fake.data[K8S]["/k8s/settings"]["PUBLIC_DOMAIN"], "fictional.example.org")

    def test_delete_old_only_removes_verified_non_tofu_copies(self):
        folders, secrets, main = self.setup_main()
        fake = FakeInfisical({MAIN: main, K8S: {}})
        store = mig.Store(fake, "prod")
        quiet(mig.apply, store, MAIN, K8S, self.run_plan(fake, folders, secrets))
        p = self.run_plan(fake, folders, secrets)  # the separate --delete-old run plans again
        ok, bad = mig.verify(store, K8S, p)
        self.assertEqual(bad, [])
        quiet(mig.delete_old, store, MAIN, p)
        self.assertNotIn("SONARR_API_KEY", fake.data[MAIN]["/k8s/media"])
        self.assertIn("PG_B2_KEY_ID", fake.data[MAIN]["/k8s/backup"])  # OpenTofu's next apply removes it
        self.assertEqual(fake.data[MAIN]["/settings"]["PUBLIC_DOMAIN"], "fictional.example.org")  # source stays
        self.assertFalse(any(op == "delete" and proj == K8S for op, proj, _, _ in fake.calls))

    def test_tofu_written_keys_found_in_the_repo(self):
        found = mig.tofu_written()
        for key in ("PG_B2_KEY_ID", "RESTIC_B2_MAINT_APPLICATION_KEY"):
            self.assertIn(("/k8s/backup", key), found)
        self.assertIn(("/k8s/cloudflared", "TUNNEL_TOKEN"), found)
        self.assertNotIn(("/k8s/tailscale", "OPERATOR_CLIENT_ID"), found)  # only READ by OpenTofu: migrated


if __name__ == "__main__":
    unittest.main()
