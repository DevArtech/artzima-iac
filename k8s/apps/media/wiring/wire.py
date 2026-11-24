#!/usr/bin/env python3
"""media-wiring: wire the arr stack together through its APIs. Idempotent.

Run by the Job k8s/apps/media/wiring.yaml (python stdlib only, pinned image),
which Flux re-creates on every sync of the `apps` Kustomization. Every step
reads the current state first and only creates/updates what differs, so it is
safe to run any number of times. API keys and passwords come from env (ESO
Secrets from Infisical /k8s/media) and are never printed.

  1. /data tree (TRaSH layout): torrents/{tv,movies}, media/{tv,movies}
  2. Sonarr + Radarr + Lidarr: root folder, hardlinks on, renaming OFF (Lidarr: and
     never write audio tags; Radarr: no folder renames), no chmod / file-date changes
     (adopted rips share their inode with the originals), recycle bin
     (/data/.recycle), qBittorrent download client (category tv / movies),
     Jellyfin "Connect" (library refresh on import)
  2b. existing rips: import unmapped folders of the root folders (only on an
     unambiguous match), tag `owned-rip`, profile "Owned rip" (no upgrades),
     unmonitored; re-enforced for every tagged title on every run
  3. Prowlarr: Sonarr + Radarr + Lidarr as applications (indexer sync); optional
     FlareSolverr indexer proxy (only if FLARESOLVERR_URL is set); the indexer
     "Internet Archive" (public, no account), synced to the three apps
  4. Bazarr: Sonarr + Radarr connection
  4b. UI logins: forms login on Sonarr/Radarr/Lidarr/Prowlarr/Bazarr = the Jellyfin admin
     login (checked by a test login / Bazarr's stored digest; written only on difference)
  5. qBittorrent: default save path, categories tv / movies / music (WebUI password:
     seeded from Infisical by the qbittorrent pod's init container)
  6. Seerr: the whole setup wizard (first sign-in with the Jellyfin admin login,
     Jellyfin server + libraries, application URL, initialized), then Sonarr + Radarr

Exit code 1 if any step failed (the Job retries); steps are independent.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MASK = "********"  # how the *arr APIs return stored secrets
WAIT = int(os.environ.get("WAIT_SECONDS", "900"))
# Per request. Generous: a root folder listing scans the NFS media tree, and an arr
# that is still starting answers only once it is up.
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT_SECONDS", "120"))
errors: list[str] = []


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise SystemExit(f"missing environment variable {name}")
    return val


def log(msg: str) -> None:
    print(msg, flush=True)


class Api:
    def __init__(self, name: str, base: str, headers: dict | None = None, cookies: bool = False,
                 prefix: str = ""):
        self.name, self.base = name, base.rstrip("/")
        self.prefix = prefix  # *arr API root: /api/v3 (Sonarr, Radarr), /api/v1 (Lidarr, Prowlarr)
        self.headers = {"Accept": "application/json", **(headers or {})}
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)] if cookies else []
        self.opener = urllib.request.build_opener(*handlers)

    def call(self, method: str, path: str, body=None, form: dict | None = None, raw: bool = False):
        data, headers = None, dict(self.headers)
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as res:
                text = res.read().decode() or "null"
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{self.name}: {method} {path} -> HTTP {e.code}: {detail}") from None
        if raw:
            return text
        try:
            return json.loads(text)
        except ValueError:
            return text

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None, form=None):
        return self.call("POST", path, body, form)

    def put(self, path, body):
        return self.call("PUT", path, body)


def wait_for(api: Api, path: str) -> bool:
    deadline = time.monotonic() + WAIT
    last = ""
    while time.monotonic() < deadline:
        try:
            api.get(path)
            return True
        except (RuntimeError, OSError) as e:
            last = str(e)
            time.sleep(10)
    errors.append(f"{api.name}: not reachable after {WAIT}s ({last})")
    log(f"FAIL {api.name}: not reachable after {WAIT}s ({last})")
    return False


def step(title: str):
    """Decorator: run a step, record (not raise) its failure."""
    def deco(fn):
        def run(*a, **kw):
            log(f"--- {title}")
            try:
                fn(*a, **kw)
            except Exception as e:  # noqa: BLE001 (report and continue with the next step)
                errors.append(f"{title}: {e}")
                log(f"FAIL {title}: {e}")
        return run
    return deco


# ---------------------------------------------------------------- generic *arr helpers
def upsert(api: Api, base: str, impl: str, name: str, fields: dict, attrs: dict | None = None,
           enable_events: list[str] | None = None) -> None:
    """Create or update the provider `name` (download client, notification,
    application, ...) of implementation `impl`, starting from the API's schema
    so fields we don't set keep their defaults."""
    schema = [s for s in api.get(f"{base}/schema") if s.get("implementation") == impl]
    if not schema:
        raise RuntimeError(f"{api.name}: no {impl} in {base}/schema")
    existing = [x for x in api.get(base) if x.get("name") == name]
    item = json.loads(json.dumps(existing[0] if existing else schema[0]))
    known = {f["name"] for f in item["fields"]}
    unknown = set(fields) - known
    if unknown:
        raise RuntimeError(f"{api.name}: {impl} has no field(s) {sorted(unknown)} (API changed?)")
    desired = dict(attrs or {})
    for ev in enable_events or []:  # only events this implementation supports
        if schema[0].get("supports" + ev[0].upper() + ev[1:], False):
            desired[ev] = True
    changed = not existing
    for f in item["fields"]:
        if f["name"] in fields:
            want = fields[f["name"]]
            have = f.get("value")
            if have != want and have != MASK:
                changed = True
            f["value"] = want
    for k, v in desired.items():
        if item.get(k) != v:
            changed = True
        item[k] = v
    item.update(name=name, implementation=impl, configContract=schema[0]["configContract"])
    if not changed:
        log(f"ok   {api.name}: {base} '{name}' unchanged")
        return
    # forceSave: skip the connection test (e.g. Jellyfin or the VPN briefly down);
    # the app re-tests on use and shows a health warning if it really is broken.
    if existing:
        api.put(f"{base}/{item['id']}?forceSave=true", item)
        log(f"upd  {api.name}: {base} '{name}'")
    else:
        item.pop("id", None)
        api.post(f"{base}?forceSave=true", item)
        log(f"add  {api.name}: {base} '{name}'")


def ensure_root_folder(api: Api, path: str, kind: str = "") -> None:
    have = {r["path"].rstrip("/") for r in api.get(f"{api.prefix}/rootfolder")}
    if path.rstrip("/") in have:
        log(f"ok   {api.name}: root folder {path}")
        return
    body = {"path": path}
    if kind == "lidarr":
        # Lidarr root folders carry the defaults for artists added under them.
        quality = api.get(f"{api.prefix}/qualityprofile")
        meta = api.get(f"{api.prefix}/metadataprofile")
        pick = (lambda ps, want: next((x for x in ps if x["name"] == want), ps[0])["id"])
        body.update(name="Music", defaultQualityProfileId=pick(quality, "Any"),
                    defaultMetadataProfileId=pick(meta, "Standard"),
                    defaultMonitorOption="all", defaultNewItemMonitorOption="all", defaultTags=[])
    api.post(f"{api.prefix}/rootfolder", body)
    log(f"add  {api.name}: root folder {path}")


def ensure_config(api: Api, path: str, want: dict, what: str) -> None:
    cfg = api.get(path)
    unknown = set(want) - set(cfg)
    if unknown:
        raise RuntimeError(f"{api.name}: {path} has no {sorted(unknown)} (API changed?)")
    if all(cfg.get(k) == v for k, v in want.items()):
        log(f"ok   {api.name}: {what}")
        return
    cfg.update(want)
    api.put(f"{path}/{cfg['id']}", cfg)
    log(f"upd  {api.name}: {what}")


def ensure_media_management(api: Api, kind: str, recycle_bin: str, recycle_days: int) -> None:
    extra = {"autoRenameFolders": False} if kind == "radarr" else {}  # no folder renames on refresh
    ensure_config(api, f"{api.prefix}/config/mediamanagement", {
        "copyUsingHardlinks": True,
        # Never chmod/chown imported files, never touch their timestamps: an adopted rip
        # shares its inode with the original, so that would change the original too.
        "setPermissionsLinux": False,
        "fileDate": "none",
        "deleteEmptyFolders": False,
        **extra,
        # Deleting = moving into the recycle bin on the same volume (not a loss).
        "recycleBin": recycle_bin,
        "recycleBinCleanupDays": recycle_days,
        # TRaSH: let custom formats decide; also no "proper/repack" upgrade path
        # around the "no upgrades" rule of the Owned rip profile.
        "downloadPropersAndRepacks": "doNotPrefer",
    }, f"media management (hardlinks, no chmod/file dates, recycle bin {recycle_bin}, {recycle_days} d)")


def ensure_no_rename(api: Api, kind: str) -> None:
    key = {"sonarr": "renameEpisodes", "radarr": "renameMovies", "lidarr": "renameTracks"}[kind]
    ensure_config(api, f"{api.prefix}/config/naming", {key: False}, f"naming ({key} off: files keep their names)")


def ensure_tag(api: Api, label: str) -> int:
    tags = {t["label"]: t["id"] for t in api.get(f"{api.prefix}/tag")}
    if label not in tags:
        tags[label] = api.post(f"{api.prefix}/tag", {"label": label})["id"]
        log(f"add  {api.name}: tag {label}")
    return tags[label]


def _allow_all(items: list) -> None:
    for it in items:
        it["allowed"] = True
        _allow_all(it.get("items") or [])


def ensure_owned_profile(api: Api, name: str) -> int:
    """A quality profile that accepts any quality and NEVER upgrades."""
    existing = [p for p in api.get(f"{api.prefix}/qualityprofile") if p["name"] == name]
    if existing:
        prof = existing[0]
        if prof.get("upgradeAllowed") is False:
            log(f"ok   {api.name}: quality profile '{name}' (upgrades off)")
        else:
            prof["upgradeAllowed"] = False
            api.put(f"{api.prefix}/qualityprofile/{prof['id']}", prof)
            log(f"upd  {api.name}: quality profile '{name}': upgrades off")
        return prof["id"]
    prof = api.get(f"{api.prefix}/qualityprofile/schema")
    prof.pop("id", None)
    _allow_all(prof["items"])
    top = prof["items"][-1]  # highest quality (item or group)
    prof.update(name=name, upgradeAllowed=False,
                cutoff=top["quality"]["id"] if top.get("quality") else top["id"],
                minFormatScore=0, cutoffFormatScore=0)
    for fi in prof.get("formatItems") or []:
        fi["score"] = 0
    new = api.post(f"{api.prefix}/qualityprofile", prof)
    log(f"add  {api.name}: quality profile '{name}' (all qualities, upgrades off)")
    return new["id"]


# ---------------------------------------------------------------- steps
@step("data folders")
def data_dirs(root: pathlib.Path) -> None:
    if not root.is_dir():
        raise RuntimeError(f"{root} is not mounted")
    for sub in ("torrents/tv", "torrents/movies", "torrents/music", "media/tv", "media/movies", "media/music",
                ".recycle/sonarr", ".recycle/radarr", ".recycle/lidarr"):
        d = root / sub
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            log(f"add  {d}")
        if d.stat().st_uid == os.getuid():
            d.chmod(0o2775)  # setgid: new files keep the media group
    log(f"ok   {root}: torrents/{{tv,movies,music}} media/{{tv,movies,music}} .recycle/{{sonarr,radarr,lidarr}}")


@step("Sonarr / Radarr / Lidarr")
def arr(api: Api, kind: str, cfg: dict) -> None:
    if not wait_for(api, f"{api.prefix}/system/status"):
        return
    ensure_root_folder(api, cfg["root"], kind)
    ensure_media_management(api, kind, cfg["recycle_bin"], cfg["recycle_days"])
    ensure_no_rename(api, kind)
    if kind == "lidarr":
        # Never write tags into audio files: an adopted rip shares its inode with
        # the original, so a retag would change the original file. (metadataSource
        # is left alone: docs/decisions.md "Lidarr metadata".)
        ensure_config(api, f"{api.prefix}/config/metadataprovider",
                      {"writeAudioTags": "no", "scrubAudioTags": False},
                      "metadata provider (never write/scrub audio tags)")
    qbt_fields = {
        "host": cfg["qbt_host"], "port": cfg["qbt_port"], "useSsl": False, "urlBase": "",
        "username": cfg["qbt_user"], "password": cfg["qbt_pass"],
        {"sonarr": "tvCategory", "radarr": "movieCategory", "lidarr": "musicCategory"}[kind]: cfg["category"],
    }
    upsert(api, f"{api.prefix}/downloadclient", "QBittorrent", "qBittorrent", qbt_fields,
           # no "protocol": the schema item has it, and Lidarr reports it as "TorrentDownloadProtocol"
           # (Sonarr/Radarr "torrent"), which made every run rewrite Lidarr's client
           {"enable": True, "priority": 1,
            "removeCompletedDownloads": True, "removeFailedDownloads": True})
    events = ["onDownload", "onUpgrade", "onRename", "onImportComplete",
              "onReleaseImport", "onArtistDelete", "onAlbumDelete", "onTrackRetag",
              "onSeriesDelete", "onEpisodeFileDelete", "onEpisodeFileDeleteForUpgrade",
              "onMovieDelete", "onMovieFileDelete", "onMovieFileDeleteForUpgrade"]
    if not cfg["jf_key"]:
        # Infisical /k8s/media/JELLYFIN_API_KEY is written by `make jellyfin` (role
        # jellyfin_setup); until then it is missing or empty and the next run connects it.
        log(f"skip {kind} -> Jellyfin connect: JELLYFIN_API_KEY is empty")
        return
    upsert(api, f"{api.prefix}/notification", "MediaBrowser", "Jellyfin",
           {"host": cfg["jf_host"], "port": cfg["jf_port"], "useSsl": False,
            "apiKey": cfg["jf_key"], "notify": False, "updateLibrary": True},
           enable_events=events)


# ---------------------------------------------------------------- adoption of existing rips
OWNED_TAG = "owned-rip"
ID_IN_NAME = re.compile(r"[\[{](tmdb|tmdbid|tvdb|tvdbid|imdb|imdbid)-(tt\d+|\d+)[\]}]", re.I)
TITLE_YEAR = re.compile(r"^(?P<title>.+?)[\s._-]*[(\[](?P<year>(?:19|20)\d{2})[)\]]")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_folder(api: Api, kind: str, folder: str) -> dict | None:
    """Look a folder name up; return the candidate only if the match is unambiguous:
    an id in the name ({tmdb-603}, [tvdbid-79126], {imdb-tt0133093}), or an exact
    (normalised) title + the year from "Title (Year)", or a unique exact title."""
    lookup = "/api/v3/series/lookup" if kind == "sonarr" else "/api/v3/movie/lookup"
    m = ID_IN_NAME.search(folder)
    if m:
        src, val = m.group(1).lower().removesuffix("id"), m.group(2)
        if (kind == "sonarr" and src == "tvdb") or (kind == "radarr" and src in ("tmdb", "imdb")):
            hits = api.get(f"{lookup}?term={urllib.parse.quote(f'{src}:{val}')}") or []
            return hits[0] if len(hits) == 1 else None
    ty = TITLE_YEAR.match(ID_IN_NAME.sub("", folder).strip())
    title, year = (ty.group("title"), int(ty.group("year"))) if ty else (ID_IN_NAME.sub("", folder).strip(), None)
    hits = api.get(f"{lookup}?term={urllib.parse.quote(title if year is None else f'{title} {year}')}") or []
    same = [h for h in hits if norm(title) in (norm(h.get("title")), norm(h.get("originalTitle")))]
    if year is not None:
        same = [h for h in same if h.get("year") == year]
    return same[0] if len(same) == 1 else None


@step("Adopt existing rips")
def adopt(api: Api, kind: str, root: str, owned_profile: str, import_unmapped: bool) -> None:
    """Existing rips (hardlinked into the root folder by `make media-adopt`) are
    imported WITHOUT renaming, tagged `owned-rip`, put on the no-upgrade profile
    and unmonitored, so no download ever replaces or deletes them. Re-applied to
    every tagged title on every run (remove the tag to release a title)."""
    if not wait_for(api, f"{api.prefix}/system/status"):
        return
    profile_id = ensure_owned_profile(api, owned_profile)
    tag = ensure_tag(api, OWNED_TAG)
    items_path = f"{api.prefix}/" + {"sonarr": "series", "radarr": "movie", "lidarr": "artist"}[kind]

    # 1. import folders in the root folder that the app doesn't manage yet.
    # Lidarr's API has no unmapped-folder list (and its text search is unreliable,
    # docs/decisions.md): music is imported by hand, then protected by step 2.
    if kind == "lidarr":
        log(f"note Lidarr: import existing music by hand (docs/manual-steps.md §10.8): profile "
            f"'{owned_profile}', monitor none, tag {OWNED_TAG}; step 2 then protects it")
        import_unmapped = False
    unmapped = [u for r in api.get(f"{api.prefix}/rootfolder") if r["path"].rstrip("/") == root.rstrip("/")
                for u in (r.get("unmappedFolders") or [])]
    if unmapped and not import_unmapped:
        log(f"skip {api.name}: {len(unmapped)} unmapped folder(s), ADOPT_UNMAPPED is off")
        unmapped = []
    manual = []
    for u in sorted(unmapped, key=lambda x: x["name"]):
        try:
            hit = match_folder(api, kind, u["name"])
        except RuntimeError as e:
            log(f"warn {api.name}: lookup of '{u['name']}' failed: {e}")
            hit = None
        if hit is None:
            manual.append(u["name"])
            continue
        body = {k: v for k, v in hit.items() if k != "id"}
        body.update(path=u["path"], qualityProfileId=profile_id, tags=[tag], monitored=False)
        if kind == "sonarr":
            body.update(seasonFolder=True, seriesType=body.get("seriesType") or "standard",
                        monitorNewItems="none",
                        addOptions={"monitor": "none", "searchForMissingEpisodes": False,
                                    "searchForCutoffUnmetEpisodes": False})
        else:
            body.update(minimumAvailability="released",
                        addOptions={"monitor": "none", "searchForMovie": False})
        try:
            api.post(items_path, body)
        except RuntimeError as e:  # e.g. the title is already in the library under another folder
            log(f"warn {api.name}: could not add '{u['name']}': {e}")
            manual.append(u["name"])
            continue
        log(f"add  {api.name}: adopted '{u['name']}' as '{hit.get('title')} ({hit.get('year')})'")
    if manual:
        log(f"note {api.name}: {len(manual)} folder(s) without an unambiguous match, import them in the UI "
            f"(Library Import, profile '{owned_profile}', monitor none, tag {OWNED_TAG}): {manual}")

    # 2. protect every tagged title (also ones imported by hand)
    owned = [x for x in api.get(items_path) if tag in (x.get("tags") or [])]
    if kind == "radarr":
        fix = [m["id"] for m in owned if m.get("monitored") or m.get("qualityProfileId") != profile_id]
        if fix:
            api.put(f"{api.prefix}/movie/editor", {"movieIds": fix, "monitored": False, "qualityProfileId": profile_id})
            log(f"upd  Radarr: {len(fix)} owned movie(s) -> unmonitored, profile '{owned_profile}'")
    elif kind == "lidarr":
        fix = [a["id"] for a in owned if a.get("qualityProfileId") != profile_id]
        if fix:
            api.put(f"{api.prefix}/artist/editor", {"artistIds": fix, "qualityProfileId": profile_id})
            log(f"upd  Lidarr: {len(fix)} owned artist(s) -> profile '{owned_profile}'")
        # Albums that HAVE files are frozen (unmonitored); missing albums stay yours to monitor.
        albums = [al["id"] for a in owned for al in api.get(f"{api.prefix}/album?artistId={a['id']}")
                  if (al.get("statistics") or {}).get("trackFileCount", 0) > 0 and al.get("monitored")]
        if albums:
            api.put(f"{api.prefix}/album/monitor", {"albumIds": albums, "monitored": False})
            log(f"upd  Lidarr: {len(albums)} owned album(s) with files -> unmonitored")
    else:
        fix = [s["id"] for s in owned if s.get("qualityProfileId") != profile_id]
        if fix:
            api.put(f"{api.prefix}/series/editor", {"seriesIds": fix, "qualityProfileId": profile_id})
            log(f"upd  Sonarr: {len(fix)} owned series -> profile '{owned_profile}'")
        # Episodes that HAVE a file are frozen (unmonitored); missing ones stay
        # yours to monitor (e.g. by a Seerr request for a missing season).
        eps = [e["id"] for s in owned for e in api.get(f"{api.prefix}/episode?seriesId={s['id']}")
               if e.get("hasFile") and e.get("monitored")]
        if eps:
            api.put(f"{api.prefix}/episode/monitor", {"episodeIds": eps, "monitored": False})
            log(f"upd  Sonarr: {len(eps)} owned episode file(s) -> unmonitored")
    log(f"ok   {api.name}: {len(owned)} owned title(s) protected (tag {OWNED_TAG}, profile '{owned_profile}')")


@step("Prowlarr")
def prowlarr(api: Api, apps: list[dict], flaresolverr: str) -> None:
    if not wait_for(api, "/api/v1/system/status"):
        return
    for a in apps:
        upsert(api, "/api/v1/applications", a["impl"], a["impl"],
               {"prowlarrUrl": a["prowlarr_url"], "baseUrl": a["url"], "apiKey": a["key"]},
               {"syncLevel": "fullSync"})
    if not flaresolverr:
        log("skip Prowlarr: FlareSolverr (FLARESOLVERR_URL empty)")
        return
    tags = {t["label"]: t["id"] for t in api.get("/api/v1/tag")}
    if "flaresolverr" not in tags:
        tags["flaresolverr"] = api.post("/api/v1/tag", {"label": "flaresolverr"})["id"]
    upsert(api, "/api/v1/indexerProxy", "FlareSolverr", "FlareSolverr",
           {"host": flaresolverr.rstrip("/") + "/"}, {"tags": [tags["flaresolverr"]]})
    log("note Prowlarr: give indexers that need it the tag 'flaresolverr'")


@step("Prowlarr indexers")
def prowlarr_indexers(api: Api, definitions: list[str], app_profile: str = "Standard") -> None:
    """Ensure the given Prowlarr indexer definitions exist and are enabled (public ones
    that need no account; the operator's choice, docs/decisions.md). Only ADDS/fixes these:
    indexers added by hand are never touched or removed. Every indexer is a "Cardigann"
    definition, so the schema is picked by definitionName, not by implementation
    (upsert() would take the first of 600+). Full Sync to Sonarr/Radarr/Lidarr (step
    "Prowlarr") pushes each one into the apps whose categories it covers."""
    if not wait_for(api, "/api/v1/system/status"):
        return
    profiles = {p["name"]: p["id"] for p in api.get("/api/v1/appprofile")}
    if app_profile not in profiles:
        raise RuntimeError(f"Prowlarr has no app profile '{app_profile}' ({sorted(profiles)})")
    schemas = {s.get("definitionName"): s for s in api.get("/api/v1/indexer/schema")}
    existing = {i.get("definitionName"): i for i in api.get("/api/v1/indexer")}
    changed = False
    for d in definitions:
        if d not in schemas:
            raise RuntimeError(f"Prowlarr has no indexer definition '{d}' (renamed upstream?)")
        cur = existing.get(d)
        item = json.loads(json.dumps(cur or schemas[d]))
        base = next((f for f in item["fields"] if f["name"] == "baseUrl"), None)
        want = {"enable": True, "appProfileId": profiles[app_profile]}
        diff = [k for k, v in want.items() if item.get(k) != v]
        if base is not None and not base.get("value"):  # the definition's first (official) URL
            base["value"] = (item.get("indexerUrls") or [None])[0]
            diff.append("baseUrl")
        if cur and not diff:
            log(f"ok   Prowlarr: indexer '{item['name']}'")
            continue
        item.update(want)
        if not cur:
            item.pop("id", None)
        # Prowlarr tests the site on every save, even with forceSave, and answers HTTP 400
        # "Unable to connect to indexer ... timed out" when it is slow (archive.org at times):
        # retried briefly; still down = the step fails and the next run adds it.
        for attempt in range(4):
            try:
                if cur:
                    api.put(f"/api/v1/indexer/{cur['id']}?forceSave=true", item)
                else:
                    api.post("/api/v1/indexer?forceSave=true", item)
                break
            except RuntimeError as e:
                if attempt == 3 or "Unable to connect to indexer" not in str(e):
                    raise
                log(f"warn Prowlarr: '{item['name']}' did not answer Prowlarr's test, retrying in 20 s")
                time.sleep(20)
        if cur:
            log(f"upd  Prowlarr: indexer '{item['name']}' {sorted(diff)}")
        else:
            log(f"add  Prowlarr: indexer '{item['name']}' (app profile '{app_profile}')")
        changed = True
    if changed:  # push to the apps now, not at the next scheduled sync
        api.post("/api/v1/command", {"name": "ApplicationIndexerSync"})
        log("upd  Prowlarr: indexer sync to the apps started")


@step("Bazarr")
def bazarr(api: Api, targets: dict) -> None:
    if not wait_for(api, "/api/system/status"):
        return
    cur = api.get("/api/system/settings")
    form = {}
    for kind, t in targets.items():
        sect = cur.get(kind, {})
        want = {"ip": t["host"], "port": t["port"], "apikey": t["key"], "base_url": "/", "ssl": False}
        # Bazarr stores base_url "/" as "": compare it without slashes.
        norm_val = (lambda k, v: str(v).strip("/") if k == "base_url" else str(v).lower())
        if not cur.get("general", {}).get(f"use_{kind}") or any(
                norm_val(k, sect.get(k)) != norm_val(k, v) for k, v in want.items()):
            form[f"settings-general-use_{kind}"] = "true"
            for k, v in want.items():
                form[f"settings-{kind}-{k}"] = str(v).lower() if isinstance(v, bool) else str(v)
    if not form:
        log("ok   Bazarr: Sonarr + Radarr connections unchanged")
        return
    api.post("/api/system/settings", form=form)
    log(f"upd  Bazarr: {', '.join(sorted({k.split('-')[1] for k in form if not k.startswith('settings-general')}))}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # see the 302 itself, don't follow it
        return None


def arr_login_ok(api: Api, user: str, password: str) -> bool:
    """A real forms login (POST /login, outside the API): success = 302 to "/" with the app's
    auth cookie (SonarrAuth, ...); a wrong login = 302 to /login?...loginFailed=true. The
    *arr host API returns only a hash of the password, so this is how a changed Jellyfin
    password is noticed without keeping any state. The *arrs have no login lockout."""
    data = urllib.parse.urlencode({"username": user, "password": password, "rememberMe": "on"}).encode()
    try:
        res = urllib.request.build_opener(_NoRedirect).open(
            urllib.request.Request(api.base + "/login", data=data, method="POST"), timeout=TIMEOUT)
        headers = res.headers
    except urllib.error.HTTPError as e:
        headers = e.headers
    cookies = headers.get_all("Set-Cookie") or []
    return "loginFailed" not in (headers.get("Location") or "") and any(
        c.split("=", 1)[0].endswith("Auth") for c in cookies)


@step("UI logins")
def ui_logins(arrs: list[Api], bazarr_api: Api, user: str, password: str) -> None:
    """Forms login on Sonarr, Radarr, Lidarr, Prowlarr and Bazarr = the operator's Jellyfin
    admin login (Infisical /k8s/media/JELLYFIN_ADMIN_USER/_PASSWORD, written by `make jellyfin`).
    Read first, write only on difference; the password itself is never printed or compared
    in clear: *arrs by a test login, Bazarr by its stored MD5 digest."""
    if not (user and password):
        log("skip UI logins: JELLYFIN_ADMIN_USER / _PASSWORD empty (written by `make jellyfin`)")
        return
    failed = []
    for api in arrs:
        try:
            path = f"{api.prefix}/config/host"
            cfg = api.get(path)
            same = (cfg.get("authenticationMethod") == "forms" and cfg.get("authenticationRequired") == "enabled"
                    and cfg.get("username") == user)
            if same and arr_login_ok(api, user, password):
                log(f"ok   {api.name}: forms login (user {user})")
                continue
            cfg.update(authenticationMethod="forms", authenticationRequired="enabled", username=user,
                       password=password, passwordConfirmation=password)
            api.put(f"{path}/{cfg['id']}", cfg)
            if not arr_login_ok(api, user, password):
                raise RuntimeError("login still refused after saving it")
            log(f"upd  {api.name}: forms login (user {user}{'' if not same else ', password changed'})")
        except (RuntimeError, OSError) as e:
            log(f"FAIL {api.name}: forms login: {e}")
            failed.append(api.name)

    # Bazarr: settings auth.type "form" (None = no login at all); it stores md5(password)
    # and hashes a new password itself on save (bazarr/app/config.py save_settings).
    try:
        digest = hashlib.md5(password.encode()).hexdigest()  # Bazarr's own scheme, not ours
        auth = (bazarr_api.get("/api/system/settings").get("auth") or {})
        if auth.get("type") == "form" and auth.get("username") == user and auth.get("password") == digest:
            log(f"ok   Bazarr: form login (user {user})")
        else:
            was = auth.get("type")
            bazarr_api.post("/api/system/settings", form={
                "settings-auth-type": "form", "settings-auth-username": user, "settings-auth-password": password})
            auth = (bazarr_api.get("/api/system/settings").get("auth") or {})
            if not (auth.get("type") == "form" and auth.get("username") == user and auth.get("password") == digest):
                raise RuntimeError("settings not stored as expected (type/username/password digest differ)")
            log(f"upd  Bazarr: form login (user {user}; was auth type {was})")
    except (RuntimeError, OSError) as e:
        log(f"FAIL Bazarr: form login: {e}")
        failed.append("Bazarr")
    if failed:
        raise RuntimeError(f"{', '.join(failed)}: login not set (see above)")


@step("qBittorrent")
def qbittorrent(api: Api, user: str, password: str, data_root: str, categories: dict) -> None:
    # Retry only while it is unreachable (VPN still connecting): a wrong password
    # fails at once, because qBittorrent bans an IP after repeated failed logins.
    deadline = time.monotonic() + WAIT
    while True:
        try:
            answer = api.call("POST", "/api/v2/auth/login", form={"username": user, "password": password},
                              raw=True).strip()
            break
        except (RuntimeError, OSError) as e:
            # 5.x: wrong login = HTTP 401, banned IP = 403. Never retry those (retries would
            # earn the ban); only "not reachable yet" is retried.
            if isinstance(e, RuntimeError) and ("HTTP 401" in str(e) or "HTTP 403" in str(e)):
                raise RuntimeError("login refused (HTTP 401/403): check /k8s/media/QBITTORRENT_* and the "
                                   "qbittorrent pod's seed-config init container log") from None
            if time.monotonic() > deadline:
                raise RuntimeError(f"not reachable after {WAIT}s: {e}") from None
            time.sleep(10)
    # Success = a session cookie (QBT_SID_<port>; "SID" before 5.x). qBittorrent 5.x answers
    # 204 with an empty body (which call() returns as "null"), 4.x answered 200 "Ok.";
    # a wrong login is 401 in 5.x (above), 200 "Fails." in 4.x (no cookie: refused here).
    if not any(c.name.startswith(("QBT_SID", "SID")) for c in api.jar):
        raise RuntimeError(f"login refused ({answer[:20]!r}): check /k8s/media/QBITTORRENT_* and the "
                           "qbittorrent pod's seed-config init container log")
    log("ok   qBittorrent: logged in")
    prefs = api.get("/api/v2/app/preferences")
    want = {
        "save_path": f"{data_root}/torrents",
        "temp_path_enabled": False,
        "auto_tmm_enabled": True,  # category decides the save path
        "torrent_changed_tmm_enabled": True,
        "save_path_changed_tmm_enabled": True,
        "category_changed_tmm_enabled": True,
        "upnp": False,  # no port forwarding through Surfshark anyway
        "current_network_interface": "tun0",  # gluetun's VPN_INTERFACE: no traffic without the tunnel
        "bypass_local_auth": False,
        "bypass_auth_subnet_whitelist_enabled": False,
    }
    diff = {k: v for k, v in want.items() if prefs.get(k) != v}
    if diff:
        api.post("/api/v2/app/setPreferences", form={"json": json.dumps(diff)})
        log(f"upd  qBittorrent: preferences {sorted(diff)}")
    else:
        log("ok   qBittorrent: preferences")
    have = api.get("/api/v2/torrents/categories") or {}
    for cat, path in categories.items():
        if cat not in have:
            api.post("/api/v2/torrents/createCategory", form={"category": cat, "savePath": path})
            log(f"add  qBittorrent: category {cat} -> {path}")
        elif have[cat].get("savePath", "").rstrip("/") != path:
            api.post("/api/v2/torrents/editCategory", form={"category": cat, "savePath": path})
            log(f"upd  qBittorrent: category {cat} -> {path}")
        else:
            log(f"ok   qBittorrent: category {cat}")


# Seerr's MediaServerType (server/constants/server.ts in Seerr 3.5): PLEX 1, JELLYFIN 2,
# EMBY 3, NOT_CONFIGURED 4 (what wiring/seed.py writes into a new settings.json).
SEERR_JELLYFIN, SEERR_NOT_CONFIGURED = 2, 4


@step("Seerr setup")
def seerr_setup(api: Api, jf: dict, app_url: str) -> None:
    """Seerr's setup wizard, over its API (Seerr 3.5.2, routes in server/routes/auth.ts and
    server/routes/settings/index.ts). Every setting is read first and written only when it
    differs; nothing is reset.
      a. first sign-in with the Jellyfin admin (POST /auth/jellyfin, serverType 2): makes
         that account Seerr's admin (user 1, which SEERR_API_KEY then acts as), connects
         the Jellyfin server and has Seerr create its own Jellyfin API key. Only while
         Seerr has no admin / no media server yet.
      b. Jellyfin server: in-cluster address, public URL for links
      c. libraries: Seerr reads Jellyfin's library list (Jellyfin is not scanned or
         changed); all are enabled if none is (later on/off is the operator's choice)
      d. application URL, then `initialized` (wizard done)."""
    if not wait_for(api, "/api/v1/settings/public"):
        return
    pub = api.get("/api/v1/settings/public")
    server_type = pub.get("mediaServerType")
    try:
        jcur = api.get("/api/v1/settings/jellyfin")
        has_admin = True
    except RuntimeError as e:
        if "HTTP 403" not in str(e):
            raise
        # The API key acts as user 1; no user 1 = nobody has signed in yet.
        if server_type != SEERR_NOT_CONFIGURED:
            raise RuntimeError("Seerr refuses SEERR_API_KEY (check /k8s/media/SEERR_API_KEY)") from None
        jcur, has_admin = {}, False

    if server_type not in (SEERR_JELLYFIN, SEERR_NOT_CONFIGURED):
        raise RuntimeError(f"Seerr is set up for another media server (type {server_type}); not changing it")
    if server_type == SEERR_NOT_CONFIGURED or not has_admin:
        if not (jf["admin_user"] and jf["admin_password"]):
            log("skip Seerr: JELLYFIN_ADMIN_USER / _PASSWORD empty (written by `make jellyfin`); "
                "the next run sets Seerr up")
            return
        body = {"username": jf["admin_user"], "password": jf["admin_password"], "serverType": SEERR_JELLYFIN}
        if not jcur.get("ip"):  # Seerr refuses a hostname once its Jellyfin server is set
            body.update(hostname=jf["host"], port=jf["port"], useSsl=False, urlBase="")
        api.post("/api/v1/auth/jellyfin", body)
        log(f"add  Seerr: signed in as the Jellyfin admin (Seerr admin, Jellyfin {jf['host']}:{jf['port']})")
        jcur = api.get("/api/v1/settings/jellyfin")

    want = {"ip": jf["host"], "port": jf["port"], "useSsl": False, "urlBase": "",
            "externalHostname": jf["public_url"]}
    diff = {k: v for k, v in want.items() if jcur.get(k) != v}
    if diff:
        api.post("/api/v1/settings/jellyfin", diff)  # Seerr tests the connection before saving
        log(f"upd  Seerr: Jellyfin settings {sorted(diff)}")
    else:
        log("ok   Seerr: Jellyfin server")

    libs = api.get("/api/v1/settings/jellyfin/library") or []
    if not libs:
        libs = api.post("/api/v1/settings/jellyfin/library/sync") or []
        log(f"add  Seerr: Jellyfin libraries {[lib['name'] for lib in libs]}")
    if not libs:
        raise RuntimeError("Jellyfin has no movie/show library yet (`make jellyfin` creates them)")
    if not any(lib.get("enabled") for lib in libs):
        for lib in libs:
            api.put(f"/api/v1/settings/jellyfin/library/{urllib.parse.quote(lib['id'], safe='')}",
                    {"enabled": True})
        log(f"upd  Seerr: enabled libraries {[lib['name'] for lib in libs]}")
    else:
        log(f"ok   Seerr: libraries enabled {[lib['name'] for lib in libs if lib.get('enabled')]}")

    main = api.get("/api/v1/settings/main")
    if main.get("applicationUrl") != app_url:
        api.post("/api/v1/settings/main", {"applicationUrl": app_url})  # merged into the rest
        log(f"upd  Seerr: application URL {app_url}")
    else:
        log("ok   Seerr: application URL")

    if api.get("/api/v1/settings/public").get("initialized"):
        log("ok   Seerr: initialized")
    else:
        api.post("/api/v1/settings/initialize")
        log("upd  Seerr: initialized (setup wizard done)")


@step("Seerr: Sonarr / Radarr")
def seerr_servers(api: Api, servers: list[dict]) -> None:
    if not wait_for(api, "/api/v1/settings/public"):
        return
    if not api.get("/api/v1/settings/public").get("initialized"):
        log("skip Seerr: not set up yet (step 'Seerr setup'); the next run adds Sonarr/Radarr")
        return
    failed = []
    for s in servers:  # independent: one unreachable arr doesn't hold up the other
        try:
            seerr_server(api, s)
        except RuntimeError as e:
            log(f"FAIL Seerr: {s['name']}: {e}")
            failed.append(s["name"])
    if failed:
        raise RuntimeError(f"{', '.join(failed)} not set (see above)")


def seerr_test(api: Api, path: str, conn: dict, tries: int = 6) -> dict:
    """Seerr's own connection test of an arr (also lists its profiles and root folders);
    retried briefly, as an arr can be busy for a moment."""
    for i in range(tries):
        try:
            return api.post(f"{path}/test", conn)
        except RuntimeError:
            if i == tries - 1:
                raise
            time.sleep(10)
    return {}


def flushed_test(api: Api, s: dict, conn: dict) -> dict:
    """Seerr caches an arr's profile list for an hour: drop that cache (only Seerr's copy
    of the arr API answers) and test again, e.g. right after Recyclarr's first run."""
    api.post(f"/api/v1/settings/cache/{s['name'].lower()}/flush")
    return seerr_test(api, s["path"], conn)


def profile_list(api: Api, s: dict, conn: dict, want: tuple) -> list[dict] | None:
    """The arr's quality profiles as Seerr sees them (fresh if the (id, name) pair `want` is
    not in the cached list); None if the arr can't be reached right now (nothing is changed)."""
    try:
        profiles = seerr_test(api, s["path"], conn, tries=2).get("profiles")
        if profiles is not None and want not in {(p["id"], p["name"]) for p in profiles}:
            profiles = flushed_test(api, s, conn).get("profiles")
    except RuntimeError as e:
        log(f"warn Seerr: {s['name']} profile check skipped: {e}")
        return None
    return profiles


def seerr_server(api: Api, s: dict) -> None:
    conn = {"name": s["name"], "hostname": s["host"], "port": s["port"], "apiKey": s["key"],
            "useSsl": False, "baseUrl": ""}
    existing = [x for x in api.get(s["path"]) if x.get("hostname") == s["host"] and not x.get("is4k")]
    if existing:
        # Keep what the operator changed in the UI (profile, folder, tags); fix only the
        # connection, and a profile id that no longer names the saved profile (after the arr's
        # database was rebuilt the id can be gone, or belong to another profile, e.g. 8 =
        # "Owned rip"): re-pointed to the profile of the saved name, else to s["profile"].
        cur = existing[0]
        upd = [] if all(cur.get(k) == v for k, v in conn.items()) else ["connection"]
        want = (cur.get("activeProfileId"), cur.get("activeProfileName"))
        profiles = profile_list(api, s, conn, want)
        if profiles is not None and want not in {(p["id"], p["name"]) for p in profiles}:
            prof = (next((p for p in profiles if p["name"] == cur.get("activeProfileName")), None)
                    or next((p for p in profiles if p["name"] == s["profile"]), None))
            if prof is None:
                log(f"skip Seerr: {s['name']} has no quality profile '{s['profile']}' yet "
                    "(recyclarr creates it; the next run re-points the server)")
            else:
                cur.update(activeProfileId=prof["id"], activeProfileName=prof["name"])
                upd.append(f"profile -> '{prof['name']}'")
        if not upd:
            log(f"ok   Seerr: {s['name']}")
            return
        cur.update(conn)
        # the id goes in the path only: Seerr's API schema marks body.id read-only (HTTP 400)
        api.put(f"{s['path']}/{cur['id']}", {k: v for k, v in cur.items() if k != "id"})
        log(f"upd  Seerr: {s['name']} {', '.join(upd)}")
        return
    test = seerr_test(api, s["path"], conn)
    prof = next((p for p in test.get("profiles") or [] if p["name"] == s["profile"]), None)
    if prof is None:
        test = flushed_test(api, s, conn)
        prof = next((p for p in test.get("profiles") or [] if p["name"] == s["profile"]), None)
    if prof is None:
        log(f"skip Seerr: {s['name']} has no quality profile '{s['profile']}' yet "
            "(recyclarr creates it; the next run adds the server)")
        return
    if s["root"].rstrip("/") not in {f["path"].rstrip("/") for f in test.get("rootFolders") or []}:
        log(f"skip Seerr: {s['name']} has no root folder {s['root']} yet (the next run adds the server)")
        return
    api.post(s["path"], {
        **conn, "activeProfileId": prof["id"], "activeProfileName": prof["name"],
        "activeDirectory": s["root"], "tags": [], "is4k": False, "isDefault": True,
        "externalUrl": "", "syncEnabled": True, "preventSearch": False, "tagRequests": False,
        "overrideRule": [], **s["extra"],
    })
    log(f"add  Seerr: {s['name']} (profile '{prof['name']}', {s['root']})")


# ---------------------------------------------------------------- main
def main() -> None:
    data = env("DATA_ROOT", "/data")
    ns = env("SERVICE_DOMAIN", "media.svc.cluster.local")
    host = {a: f"{a}.{ns}" for a in ("sonarr", "radarr", "lidarr", "prowlarr", "bazarr", "qbittorrent", "seerr")}
    port = {"sonarr": 8989, "radarr": 7878, "lidarr": 8686, "prowlarr": 9696, "bazarr": 6767,
            "qbittorrent": 8080, "seerr": 5055}
    url = {a: f"http://{host[a]}:{port[a]}" for a in host}
    keys = {a: env(f"{a.upper()}_API_KEY") for a in ("sonarr", "radarr", "lidarr", "prowlarr", "bazarr", "seerr")}
    qbt_user, qbt_pass = env("QBITTORRENT_USERNAME"), env("QBITTORRENT_PASSWORD")
    jf_host, jf_port, jf_key = env("JELLYFIN_HOST"), int(env("JELLYFIN_PORT", "80")), os.environ.get("JELLYFIN_API_KEY", "").strip()

    sonarr = Api("Sonarr", url["sonarr"], {"X-Api-Key": keys["sonarr"]}, prefix="/api/v3")
    radarr = Api("Radarr", url["radarr"], {"X-Api-Key": keys["radarr"]}, prefix="/api/v3")
    lidarr = Api("Lidarr", url["lidarr"], {"X-Api-Key": keys["lidarr"]}, prefix="/api/v1")

    recycle_days = int(env("RECYCLE_DAYS", "30d").rstrip("d"))
    owned_profile = env("OWNED_PROFILE", "Owned rip")
    import_unmapped = env("ADOPT_UNMAPPED", "true").lower() == "true"

    data_dirs(pathlib.Path(data))
    common = {"qbt_host": host["qbittorrent"], "qbt_port": port["qbittorrent"], "qbt_user": qbt_user,
              "qbt_pass": qbt_pass, "jf_host": jf_host, "jf_port": jf_port, "jf_key": jf_key,
              "recycle_days": recycle_days}
    arr(sonarr, "sonarr", {**common, "root": f"{data}/media/tv", "category": "tv",
                           "recycle_bin": f"{data}/.recycle/sonarr"})
    arr(radarr, "radarr", {**common, "root": f"{data}/media/movies", "category": "movies",
                           "recycle_bin": f"{data}/.recycle/radarr"})
    arr(lidarr, "lidarr", {**common, "root": f"{data}/media/music", "category": "music",
                           "recycle_bin": f"{data}/.recycle/lidarr"})
    adopt(sonarr, "sonarr", f"{data}/media/tv", owned_profile, import_unmapped)
    adopt(radarr, "radarr", f"{data}/media/movies", owned_profile, import_unmapped)
    adopt(lidarr, "lidarr", f"{data}/media/music", owned_profile, import_unmapped)
    prowlarr(Api("Prowlarr", url["prowlarr"], {"X-Api-Key": keys["prowlarr"]}), [
        {"impl": "Sonarr", "url": url["sonarr"], "key": keys["sonarr"], "prowlarr_url": url["prowlarr"]},
        {"impl": "Radarr", "url": url["radarr"], "key": keys["radarr"], "prowlarr_url": url["prowlarr"]},
        {"impl": "Lidarr", "url": url["lidarr"], "key": keys["lidarr"], "prowlarr_url": url["prowlarr"]},
    ], os.environ.get("FLARESOLVERR_URL", ""))
    # The operator's choice of indexers (Prowlarr definitionName): only public ones without
    # an account belong in git. More are added by hand in Prowlarr and sync the same way.
    prowlarr_indexers(Api("Prowlarr", url["prowlarr"], {"X-Api-Key": keys["prowlarr"]}),
                      ["internetarchive"])
    bazarr(Api("Bazarr", url["bazarr"], {"X-API-KEY": keys["bazarr"]}), {
        "sonarr": {"host": host["sonarr"], "port": port["sonarr"], "key": keys["sonarr"]},
        "radarr": {"host": host["radarr"], "port": port["radarr"], "key": keys["radarr"]},
    })
    ui_logins([sonarr, radarr, lidarr, Api("Prowlarr", url["prowlarr"], {"X-Api-Key": keys["prowlarr"]}, prefix="/api/v1")],
              Api("Bazarr", url["bazarr"], {"X-API-KEY": keys["bazarr"]}),
              os.environ.get("JELLYFIN_ADMIN_USER", "").strip(), os.environ.get("JELLYFIN_ADMIN_PASSWORD", ""))
    qbittorrent(Api("qBittorrent", url["qbittorrent"], cookies=True), qbt_user, qbt_pass, data,
                {"tv": f"{data}/torrents/tv", "movies": f"{data}/torrents/movies",
                 "music": f"{data}/torrents/music"})
    seerr = Api("Seerr", url["seerr"], {"X-Api-Key": keys["seerr"]})
    seerr_setup(seerr, {"host": jf_host, "port": jf_port, "public_url": env("JELLYFIN_PUBLIC_URL"),
                        "admin_user": os.environ.get("JELLYFIN_ADMIN_USER", "").strip(),
                        "admin_password": os.environ.get("JELLYFIN_ADMIN_PASSWORD", "")},
                env("SEERR_APPLICATION_URL"))
    seerr_servers(seerr, [
        {"name": "Sonarr", "profile": env("SEERR_SONARR_PROFILE", "WEB-1080p"), "host": host["sonarr"],
         "port": port["sonarr"], "key": keys["sonarr"], "root": f"{data}/media/tv", "path": "/api/v1/settings/sonarr",
         "extra": {"seriesType": "standard", "animeSeriesType": "anime", "enableSeasonFolders": True,
                   "monitorNewItems": "all"}},
        {"name": "Radarr", "profile": env("SEERR_RADARR_PROFILE", "HD Bluray + WEB"), "host": host["radarr"],
         "port": port["radarr"], "key": keys["radarr"], "root": f"{data}/media/movies",
         "path": "/api/v1/settings/radarr", "extra": {"minimumAvailability": "released"}},
    ])

    if errors:
        log(f"\n{len(errors)} step(s) failed:")
        for e in errors:
            log(f"  - {e}")
        sys.exit(1)
    log("\nall wired")


if __name__ == "__main__":
    main()
