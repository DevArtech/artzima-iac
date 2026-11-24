#!/usr/bin/env python3
"""Run a command with secrets from Infisical in its environment (never on disk).

    infisical_exec.py [--set VAR=/folder/KEY]... [--optional VAR=/folder/KEY]... -- cmd [args...]

1. Loads plain KEY=value files (no shell; variables already set in the
   environment win; files earlier in this list win over later ones):
     $ARTZIMA_INFISICAL_ENV      default ~/.config/artzima/infisical.env
                                 admin identity: INFISICAL_UNIVERSAL_AUTH_CLIENT_ID / _SECRET
     $ARTZIMA_INFISICAL_PROJECT  default ~/.config/artzima/infisical-project.env
                                 INFISICAL_HOST / _PROJECT_ID / _PROJECT_SLUG / _ENV and
                                 INFISICAL_K8S_PROJECT_ID / _K8S_PROJECT_SLUG (the cluster's project)
     $ARTZIMA_LOCAL_ENV          default ./local.env (gitignored; e.g. ESO_* for eso-bootstrap)
   None of them is in the repo. Values are never printed.
2. Logs in to Infisical with the admin machine identity (universal auth:
   INFISICAL_UNIVERSAL_AUTH_CLIENT_ID / _SECRET), only if something is to be fetched.
3. Fetches every --set (required) / --optional secret into VAR, from the project that
   holds its folder (/k8s/** -> the k8s project, everything else -> main;
   scripts/infisical_projects.py).
4. Execs cmd. The child keeps INFISICAL_UNIVERSAL_AUTH_* (the OpenTofu provider and
   the Ansible lookups authenticate with them) and INFISICAL_HOST/_PROJECT_ID/_ENV
   (+ _K8S_PROJECT_ID), also as TF_VAR_infisical_* for OpenTofu; ESO_* bootstrap values
   are removed.

Host/project/environment: ansible/group_vars/all/infisical.yml, overridable with
INFISICAL_HOST / INFISICAL_PROJECT_ID / INFISICAL_ENV. Uses the official
`infisicalsdk` package (installed in the tools venv by `make deps`).
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import labdata  # noqa: E402
from infisical_projects import project_id  # noqa: E402

CONFIG_DIR = pathlib.Path.home() / ".config/artzima"
ENV_FILES = [
    pathlib.Path(os.environ.get("ARTZIMA_INFISICAL_ENV", CONFIG_DIR / "infisical.env")).expanduser(),
    pathlib.Path(os.environ.get("ARTZIMA_INFISICAL_PROJECT", CONFIG_DIR / "infisical-project.env")).expanduser(),
    pathlib.Path(os.environ.get("ARTZIMA_LOCAL_ENV", labdata.REPO / "local.env")).expanduser(),
]


def load_local_env() -> None:
    """Load every ENV_FILES entry that exists (see the module docstring)."""
    for path in ENV_FILES:
        load_env_file(path)


def load_env_file(path: pathlib.Path) -> None:
    """KEY=value lines (no shell, no expansion); existing env wins. Never echoes values."""
    if not path.exists():
        return
    if path.stat().st_mode & 0o077:
        print(f"warning: {path} is readable by others: chmod 600 {path}", file=sys.stderr)
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep or not key.strip().isidentifier():
            sys.exit(f"{path}:{n}: expected KEY=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def client():
    try:
        from infisical_sdk import InfisicalSDKClient
    except ImportError:
        sys.exit("infisicalsdk missing: run `make deps` (or use the tools venv python)")
    cid = os.environ.get("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID")
    secret = os.environ.get("INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("INFISICAL_UNIVERSAL_AUTH_CLIENT_ID / _SECRET not set "
                 "(put them in local.env, see docs/secrets.md)")
    cfg = labdata.load_infisical()
    c = InfisicalSDKClient(host=cfg["infisical_host"])
    try:
        c.auth.universal_auth.login(client_id=cid, client_secret=secret)
    except Exception as e:  # noqa: BLE001 (network/auth errors, message only)
        sys.exit(f"Infisical login at {cfg['infisical_host']} failed: {type(e).__name__} (response hidden)")
    return c, cfg


def parse(spec: str) -> tuple[str, str, str]:
    var, sep, ref = spec.partition("=")
    folder, _, key = ref.rpartition("/")
    if not sep or not var.isidentifier() or not ref.startswith("/") or not key:
        sys.exit(f"bad spec {spec!r}: expected VAR=/folder/KEY")
    return var, folder or "/", key


def main(argv: list[str]) -> None:
    if "--" not in argv:
        sys.exit(__doc__)
    i = argv.index("--")
    opts, cmd = argv[:i], argv[i + 1:]
    wanted: list[tuple[str, str, str, bool]] = []
    it = iter(opts)
    for flag in it:
        if flag not in ("--set", "--optional"):
            sys.exit(f"unknown option {flag}")
        wanted.append((*parse(next(it, "")), flag == "--set"))
    if not cmd:
        sys.exit("no command given")

    load_local_env()
    if wanted:
        c, cfg = client()
        for var, folder, key, required in wanted:
            try:
                s = c.secrets.get_secret_by_name(
                    secret_name=key, project_id=project_id(cfg, folder),
                    environment_slug=cfg["infisical_env"], secret_path=folder,
                    expand_secret_references=False)
                os.environ[var] = s.secretValue
            except Exception as e:  # noqa: BLE001 (SDK raises plain exceptions)
                if required:
                    sys.exit(f"Infisical {folder}/{key} ({cfg['infisical_env']}): {type(e).__name__} (response hidden)")
                os.environ.pop(var, None)
    cfg = labdata.load_infisical()
    os.environ.setdefault("TF_VAR_infisical_host", cfg["infisical_host"])
    os.environ.setdefault("TF_VAR_infisical_env", cfg["infisical_env"])
    for key in ("infisical_project_id", "infisical_k8s_project_id"):
        if "REPLACE" not in str(cfg[key]):
            os.environ.setdefault(f"TF_VAR_{key}", str(cfg[key]))
    os.environ.setdefault("TF_VAR_infisical_k8s_project_slug", str(cfg["infisical_k8s_project_slug"]))
    # ESO credentials only reach a command that asked for them explicitly (make eso-bootstrap).
    requested = {var for var, _, _, _ in wanted}
    for k in [k for k in os.environ if k.startswith("ESO_") and k not in requested]:
        del os.environ[k]
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
