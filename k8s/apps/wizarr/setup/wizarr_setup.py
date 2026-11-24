#!/usr/bin/env python3
"""wizarr-setup: Wizarr's first run, through Wizarr's own web forms. Idempotent.

Run by the Job k8s/apps/wizarr/setup.yaml (python stdlib only, pinned image), which
Flux re-creates on every sync of the `apps` Kustomization. Written against Wizarr
v2026.10.1 (app/blueprints/setup, auth, media_servers). Wizarr's JSON API (/api)
needs an API key that only its admin UI can create, so this uses the same form
posts as the browser, with a session cookie.

Fresh install (rebuild): does the whole first run. Initialized install (an admin
exists): only ADDS what is missing and otherwise reports ok; it never rewrites,
deletes or re-scans anything the admin set up.

  1. admin account: while Wizarr has none, /setup/ shows its one form (CSRF token,
     username, password, confirm). It is filled with the Jellyfin admin login, the
     same one `make jellyfin` and Seerr use. Once an admin exists /setup/ only
     redirects, so this never creates, changes or overwrites an existing account.
  2. sign-in (/login) with that login, once. If the admin's login is a different
     one (set by hand), or the account has a passkey, the admin-only steps below are
     skipped and the run still succeeds: nothing is retried or guessed.
  3. Jellyfin server: added only if Wizarr has no Jellyfin server (in-cluster URL,
     API key, public URL for invite links; Wizarr tests the connection before it
     saves). An existing one is left exactly as it is; fields that differ from the
     values here are only reported.
  4. libraries: scanned only for a server this run added (new libraries enabled).
     An existing server's library selection is the admin's.

Invites, users and every other setting are never touched. Secrets come from env
(ESO Secret wizarr-setup, Infisical /k8s/media) and are never printed.
Exit code 1 only if something that should have been added could not be.
"""

from __future__ import annotations

import html.parser
import http.cookiejar
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 60
WAIT = int(os.environ.get("WAIT_SECONDS", "600"))


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val.strip() == "":
        raise SystemExit(f"missing environment variable {name}")
    return val.strip() if name != "JELLYFIN_ADMIN_PASSWORD" else val


def log(msg: str) -> None:
    print(msg, flush=True)


class Form(html.parser.HTMLParser):
    """The inputs of a page: {name: value} (checkboxes: present only when checked,
    selects: the selected option), plus every checkbox named `libraries`."""

    def __init__(self, text: str):
        super().__init__()
        self.values: dict[str, str] = {}
        self.libraries: list[tuple[str, bool]] = []
        self._select: str | None = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag == "input" and name:
            if a.get("type") == "checkbox":
                if name == "libraries":
                    self.libraries.append((a.get("value") or "", "checked" in a))
                elif "checked" in a:
                    self.values[name] = a.get("value") or "true"
            else:
                self.values[name] = a.get("value") or ""
        elif tag == "select":
            self._select = name
        elif tag == "option" and self._select and "selected" in a:
            self.values[self._select] = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


class Wizarr:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path: str, form: dict | list | None = None) -> tuple[str, str]:
        """GET (form None) or form POST; follows redirects. Returns (final path, body)."""
        data = urllib.parse.urlencode(form, doseq=True).encode() if form is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Referer": self.base + path})
        try:
            with self.opener.open(req, timeout=TIMEOUT) as res:
                return res.geturl()[len(self.base):], res.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Wizarr: {'POST' if data else 'GET'} {path} -> HTTP {e.code}") from None


def wait_up(wz: Wizarr) -> None:
    deadline, last = time.monotonic() + WAIT, ""
    while time.monotonic() < deadline:
        try:
            wz.call("/login")
            return
        except (RuntimeError, OSError) as e:
            last = str(e)
            time.sleep(10)
    raise RuntimeError(f"Wizarr not reachable after {WAIT}s ({last})")


def ensure_admin(wz: Wizarr, user: str, password: str) -> None:
    where, page = wz.call("/setup/")
    form = Form(page)
    if not where.startswith("/setup") or "password" not in form.values:
        log("ok   admin account exists (setup page closed)")
        return
    if "csrf_token" not in form.values:
        raise RuntimeError("setup page has no csrf_token; Wizarr's setup form changed")
    where, page = wz.call("/setup/", {"csrf_token": form.values["csrf_token"], "username": user,
                                      "password": password, "confirm": password})
    if where.startswith("/setup"):
        raise RuntimeError("Wizarr refused the admin account (username 3-15 chars; password 8+ "
                           "with upper, lower and a digit)")
    log("add  admin account (the Jellyfin admin login)")


def login(wz: Wizarr, user: str, password: str) -> bool:
    """One sign-in attempt. False (skip the admin-only steps, run still ok) when the
    admin's login is not the Jellyfin admin login, or the account has a passkey."""
    _, page = wz.call("/login", {"username": user, "password": password, "auth_method": "local"})
    if "Two-Factor Authentication" in page:  # login.html with show_2fa
        log("skip admin-only steps: the admin account has a passkey (2FA)")
        return False
    where, _ = wz.call("/settings/servers")
    if where.startswith("/login"):
        log("skip admin-only steps: Wizarr's admin login is not the Jellyfin admin login "
            "(/k8s/media/JELLYFIN_ADMIN_*); set up by hand, left alone")
        return False
    log("ok   signed in")
    return True


def servers(wz: Wizarr) -> list[tuple[int, dict]]:
    _, page = wz.call("/settings/servers")
    ids, seen = [], set()
    for part in page.split('data-server-id="')[1:]:
        sid = int(part.split('"', 1)[0])
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return [(sid, Form(wz.call(f"/settings/servers/{sid}/edit")[1]).values) for sid in ids]


def ensure_server(wz: Wizarr, want: dict) -> int | None:
    """The id of the Jellyfin server this run added, None if one already existed."""
    jf = [(sid, v) for sid, v in servers(wz) if v.get("server_type") == "jellyfin"]
    if not jf:
        form = {"server_name": "Jellyfin", "server_type": "jellyfin", **want}
        where, _ = wz.call("/settings/servers/create", form)
        if where.startswith("/settings/servers/create"):  # no redirect: the connection test failed
            raise RuntimeError("Wizarr could not connect to Jellyfin at "
                               f"{want['server_url']} with JELLYFIN_API_KEY")
        jf = [(sid, v) for sid, v in servers(wz) if v.get("server_type") == "jellyfin"]
        if not jf:
            raise RuntimeError("Jellyfin server not listed after adding it")
        log(f"add  Jellyfin server {want['server_url']} (public {want['external_url']})")
        return jf[0][0]
    # Already there: the admin's server. Report, never rewrite (name, URLs, key, toggles).
    sid, cur = next(((s, v) for s, v in jf if v.get("server_url") == want["server_url"]), jf[0])
    diff = sorted(k for k, v in want.items() if cur.get(k) != v)
    note = f"; differs from the repo in {diff}, left as is" if diff else ""
    log(f"ok   Jellyfin server '{cur.get('server_name')}' exists{note}")
    return None


def ensure_libraries(wz: Wizarr, sid: int) -> None:
    _, page = wz.call(f"/settings/servers/{sid}/scan-libraries", {})
    libs = Form(page).libraries
    if not libs:
        raise RuntimeError("Wizarr sees no Jellyfin libraries (`make jellyfin` creates them)")
    log(f"ok   libraries: {len(libs)} from Jellyfin, {sum(on for _, on in libs)} enabled for invites")


def main() -> None:
    user, password = env("JELLYFIN_ADMIN_USER"), env("JELLYFIN_ADMIN_PASSWORD")
    want = {"server_url": env("JELLYFIN_URL"), "api_key": env("JELLYFIN_API_KEY"),
            "external_url": env("JELLYFIN_PUBLIC_URL")}
    wz = Wizarr(env("WIZARR_URL", "http://wizarr.wizarr.svc.cluster.local:5690"))
    try:
        wait_up(wz)
        ensure_admin(wz, user, password)
        if login(wz, user, password):
            added = ensure_server(wz, want)
            if added is not None:  # a server we just added: pick up its libraries
                ensure_libraries(wz, added)
            else:
                log("ok   libraries: the existing server's selection is left as is")
    except (RuntimeError, OSError) as e:
        log(f"FAIL {e}")
        sys.exit(1)
    log("\nWizarr: done")


if __name__ == "__main__":
    main()
