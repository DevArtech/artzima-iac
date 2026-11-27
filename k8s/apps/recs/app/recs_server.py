#!/usr/bin/env python3
"""recs: personal film/TV recommendations for the Jellyfin users of the lab.

Classic (SuggestArr-like: what you watch -> similar titles via TMDb) and AI (Recommendarr-
like: a local LLM judging candidates against your taste) together, mixed by fusion scoring.
Scoring lives in recs_core.py; this file reads the data, runs the pipeline per user,
caches the results and serves them. Python stdlib only (image python:alpine, code from the
ConfigMap recs-app). Design: docs/decisions.md "Recommendations".

API (cluster-internal, Service recs.media:8080):
  GET  /healthz                         liveness/readiness
  GET  /v1/recs/{jellyfinUserId}?limit=N   {"generatedAt", "items": [{"tmdbId", "mediaType", "reason"}]}
  GET  /v1/recs/{jellyfinUserId}/debug  every component score per item, the seeds and the run stats
  POST /v1/refresh/{jellyfinUserId}     recompute one user (202; ?wait=1 blocks until done)
  POST /v1/refresh                      recompute every user (the nightly CronJob, ?wait=1)
  GET  /v1/status                       last runs and their durations
Secrets (API keys) come from the environment and are never logged.
"""

from __future__ import annotations

import datetime as dt
import http.server
import json
import os
import pathlib
import queue
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I drops the script dir
import recs_core as core  # noqa: E402

CACHE = pathlib.Path(os.environ.get("RECS_CACHE_DIR", "/cache"))
DETAILS_TTL = float(os.environ.get("RECS_DETAILS_TTL_DAYS", "7")) * 86400
LISTS_TTL = float(os.environ.get("RECS_LISTS_TTL_DAYS", "3")) * 86400
MF_SHADOW = os.environ.get("RECS_MF_SHADOW", "") in ("1", "true")  # compute MF for /debug even at W_MF = 0
HTTP_TIMEOUT = float(os.environ.get("RECS_HTTP_TIMEOUT_SECONDS", "60"))
LLM_TIMEOUT = float(os.environ.get("RECS_LLM_TIMEOUT_SECONDS", "900"))
LLM_BATCH = int(os.environ.get("RECS_LLM_BATCH", "12"))
# Optional MF factors file (built offline, see recs_core "matrix factorisation"); missing = MF off.
MF_FILE = pathlib.Path(os.environ.get("RECS_MF_FILE", str(CACHE / "models" / "recs-mf-v2.rmf")))
# A Jellyfin user id: 32 hex digits, or the dashed GUID form (fullmatch: no anchors needed;
# no dollar signs in this file, Flux substitutes the ConfigMap).
UID_RE = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def log(msg: str, **kv) -> None:
    extra = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} {msg} {extra}".rstrip(), flush=True)


def load_config() -> dict:
    cfg = dict(core.DEFAULTS)
    for name, default in core.DEFAULTS.items():
        raw = os.environ.get("RECS_" + name)
        if raw not in (None, ""):
            cfg[name] = type(default)(float(raw)) if isinstance(default, int) else float(raw)
    return cfg


def norm_uid(uid: str) -> str:
    return uid.replace("-", "").lower()


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, path)


def read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------- HTTP clients
class Api:
    def __init__(self, name: str, base: str, headers: dict):
        self.name, self.base, self.headers = name, base.rstrip("/"), {"Accept": "application/json", **headers}

    def call(self, method: str, path: str, body=None, timeout: float = HTTP_TIMEOUT, retries: int = 2):
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    return json.loads(res.read() or b"null")
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == retries:
                    # path only: query strings never carry secrets here, but keep it short
                    raise RuntimeError(f"{self.name}: {method} {path.split('?')[0]} -> HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt == retries:
                    raise RuntimeError(f"{self.name}: {method} {path.split('?')[0]} -> {type(e).__name__}") from None
            time.sleep(2 * (attempt + 1))
        raise RuntimeError("unreachable")

    def get(self, path: str):
        return self.call("GET", path)


class Jellyfin:
    def __init__(self, base: str, key: str):
        self.api = Api("jellyfin", base, {"Authorization": f'MediaBrowser Token="{key}"'})

    def users(self) -> list[dict]:
        return [{"id": norm_uid(u["Id"]), "name": u.get("Name", "")} for u in self.api.get("/Users")]

    def items(self, query: str) -> list[dict]:
        out, start = [], 0
        while True:
            page = self.api.get(f"/Items?{query}&StartIndex={start}&Limit=500")
            got = page.get("Items") or []
            out += got
            start += len(got)
            if not got or start >= int(page.get("TotalRecordCount") or 0):
                return out

    @staticmethod
    def key(it: dict):
        tmdb = (it.get("ProviderIds") or {}).get("Tmdb")
        mt = {"Movie": "movie", "Series": "tv"}.get(it.get("Type"))
        return (mt, int(tmdb)) if mt and tmdb and str(tmdb).isdigit() else None

    def owned(self) -> set:
        """Every movie and series in Jellyfin (any user can see it: it's in the library)."""
        rows = self.items("Recursive=true&IncludeItemTypes=Movie,Series&Fields=ProviderIds")
        return {k for k in (self.key(i) for i in rows) if k}

    def library(self, uid: str) -> list[dict]:
        """The user's activity per movie/series, normalised for recs_core.build_seeds."""
        q = f"userId={uid}&Recursive=true&EnableUserData=true&Fields=ProviderIds,ProductionYear"
        out = []
        for it in self.items(q + "&IncludeItemTypes=Movie"):
            ud, key = it.get("UserData") or {}, self.key(it)
            if not key:
                continue
            if not (ud.get("Played") or ud.get("PlaybackPositionTicks") or ud.get("IsFavorite")
                    or ud.get("Likes") is not None or ud.get("Rating") is not None or ud.get("PlayCount")):
                continue
            out.append({"key": key, "title": it.get("Name"), "favourite": bool(ud.get("IsFavorite")),
                        "played": bool(ud.get("Played")), "playCount": ud.get("PlayCount") or 0,
                        "lastPlayed": ud.get("LastPlayedDate"), "playedPct": ud.get("PlayedPercentage"),
                        "rating": ud.get("Rating"), "likes": ud.get("Likes")})
        # Series: their own user data (favourite, rating, unplayed count) + the played episodes.
        eps: dict = {}
        for e in self.items(q + "&IncludeItemTypes=Episode&Filters=IsPlayed"):
            sid, ud = e.get("SeriesId"), e.get("UserData") or {}
            if not sid:
                continue
            agg = eps.setdefault(sid, {"n": 0, "plays": 0, "last": None})
            agg["n"] += 1
            agg["plays"] += int(ud.get("PlayCount") or 1)
            last = ud.get("LastPlayedDate")
            if last and (agg["last"] is None or last > agg["last"]):
                agg["last"] = last
        for it in self.items(q + "&IncludeItemTypes=Series"):
            ud, key = it.get("UserData") or {}, self.key(it)
            agg = eps.get(it.get("Id")) or {"n": 0, "plays": 0, "last": None}
            if not key:
                continue
            if not (agg["n"] or ud.get("IsFavorite") or ud.get("Likes") is not None or ud.get("Rating") is not None):
                continue
            n = agg["n"]
            out.append({"key": key, "title": it.get("Name"), "favourite": bool(ud.get("IsFavorite")),
                        "played": bool(ud.get("Played")) and n > 0,
                        "playCount": round(agg["plays"] / n) if n else 0,
                        "lastPlayed": agg["last"] or ud.get("LastPlayedDate"), "playedPct": None,
                        "episodesPlayed": n, "episodesTotal": n + int(ud.get("UnplayedItemCount") or 0),
                        "rating": ud.get("Rating"), "likes": ud.get("Likes")})
        return out


class Seerr:
    def __init__(self, base: str, key: str):
        self.api = Api("seerr", base, {"X-Api-Key": key})

    def paged(self, path: str) -> list[dict]:
        out, skip = [], 0
        sep = "&" if "?" in path else "?"
        while True:
            page = self.api.get(f"{path}{sep}take=100&skip={skip}")
            got = page.get("results") or []
            out += got
            skip += len(got)
            if not got or skip >= int((page.get("pageInfo") or {}).get("results") or 0):
                return out

    def users(self) -> list[dict]:
        return self.paged("/api/v1/user")

    def requests(self) -> list[dict]:
        """Every request by anyone: {"key", "userId", "createdAt"}."""
        out = []
        for r in self.paged("/api/v1/request?filter=all&sort=added"):
            m = r.get("media") or {}
            mt, tmdb = m.get("mediaType") or r.get("type"), m.get("tmdbId")
            if mt in ("movie", "tv") and isinstance(tmdb, int):
                out.append({"key": (mt, tmdb), "userId": (r.get("requestedBy") or {}).get("id"),
                            "createdAt": r.get("createdAt")})
        return out

    def known_media(self) -> set:
        """Titles Seerr tracks as pending, processing, (partly) available or blacklisted."""
        keys = set()
        for m in self.paged("/api/v1/media?filter=all&sort=added"):
            if m.get("mediaType") in ("movie", "tv") and isinstance(m.get("tmdbId"), int) \
                    and int(m.get("status") or 1) in (2, 3, 4, 5, 6):
                keys.add((m["mediaType"], m["tmdbId"]))
        return keys

    def watchlist(self, seerr_uid: int) -> list[dict]:
        out, page = [], 1
        while True:
            d = self.api.get(f"/api/v1/user/{seerr_uid}/watchlist?page={page}")
            for w in d.get("results") or []:
                if w.get("mediaType") in ("movie", "tv") and isinstance(w.get("tmdbId"), int):
                    out.append({"key": (w["mediaType"], w["tmdbId"]), "title": w.get("title"),
                                "createdAt": w.get("createdAt")})
            if page >= int(d.get("totalPages") or 1):
                return out
            page += 1

    # TMDb data through Seerr (no TMDb key of our own), cached on the volume
    def _cached(self, name: str, ttl: float, path: str):
        f = CACHE / "tmdb" / name
        hit = read_json(f)
        if hit and time.time() - hit.get("at", 0) < ttl:
            return hit["data"]
        data = self.api.get(path)
        write_json(f, {"at": time.time(), "data": data})
        return data

    def details(self, key) -> dict | None:
        mt, tid = key
        try:
            d = self._cached(f"{mt}-{tid}.json", DETAILS_TTL, f"/api/v1/{mt}/{tid}")
        except RuntimeError as e:
            log("details failed", key=f"{mt}/{tid}", error=e)
            return None
        date = d.get("releaseDate") or d.get("firstAirDate") or ""
        kw = d.get("keywords") or []
        return {
            "title": d.get("title") or d.get("name") or "?",
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "genres": core.norm_genres(g.get("name") for g in d.get("genres") or [] if g.get("name")),
            "keywords": core.norm_keywords(k.get("name") for k in kw if isinstance(k, dict))[:20],
            "collection": (d.get("collection") or {}).get("id") if mt == "movie" else None,
            "voteAverage": float(d.get("voteAverage") or 0.0), "voteCount": int(d.get("voteCount") or 0),
            "popularity": float(d.get("popularity") or 0.0), "overview": d.get("overview") or "",
            "tvdbId": (d.get("externalIds") or {}).get("tvdbId"), "adult": bool(d.get("adult")),
            "imdbId": d.get("imdbId") or (d.get("externalIds") or {}).get("imdbId"),
            "mfRaw": mf_features(d, mt),
        }

    def related(self, key, kind: str, page: int = 1) -> list[dict]:
        mt, tid = key
        name = f"{mt}-{tid}-{kind}.json" if page == 1 else f"{mt}-{tid}-{kind}-p{page}.json"
        try:
            d = self._cached(name, LISTS_TTL, f"/api/v1/{mt}/{tid}/{kind}?page={page}")
        except RuntimeError as e:
            log("related failed", key=f"{mt}/{tid}", kind=kind, error=e)
            return []
        return d.get("results") or []

    def search(self, query: str) -> list[dict]:
        q = urllib.parse.quote(query, safe="")
        return self.api.get(f"/api/v1/search?query={q}&page=1").get("results") or []


class Arr:
    """Radarr/Sonarr: everything they manage (in the library or queued) is excluded."""

    def __init__(self, name: str, base: str, key: str):
        self.name, self.api = name, Api(name, base, {"X-Api-Key": key})

    def keys(self) -> tuple[set, set]:
        if self.name == "radarr":
            return {("movie", m["tmdbId"]) for m in self.api.get("/api/v3/movie") if m.get("tmdbId")}, set()
        series = self.api.get("/api/v3/series")
        return ({("tv", s["tmdbId"]) for s in series if s.get("tmdbId")},
                {s["tvdbId"] for s in series if s.get("tvdbId")})


class Llm:
    def __init__(self, base: str, key: str, model: str):
        self.api = Api("llm", base, {"Authorization": f"Bearer {key}"} if key else {})
        self.model = model

    def json_chat(self, messages: list[dict], schema: dict, max_tokens: int):
        body = {"model": self.model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens,
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "answer", "strict": True, "schema": schema}}}
        t = time.monotonic()
        res = self.api.call("POST", "/v1/chat/completions", body, timeout=LLM_TIMEOUT, retries=0)
        choice = (res.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = dict(res.get("usage") or {}, finish_reason=choice.get("finish_reason"))
        return content, time.monotonic() - t, usage


# ---------------------------------------------------------------------------- MF
def mf_features(d: dict, mt: str):
    """MF content features of a details response; None (title gets no predicted factors) on
    odd data, so MF can never break details."""
    try:
        return core.mf_raw_features(d, mt)
    except Exception:  # noqa: BLE001
        return None


class MfModel:
    """The factors file, (re)loaded when its mtime changes. Any problem: MF off, never fatal."""

    def __init__(self, path: pathlib.Path):
        self.path, self.mtime, self.model = path, None, None

    def get(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.model = self.mtime = None
            return None
        if mtime != self.mtime:
            self.mtime = mtime
            try:
                t = time.monotonic()
                self.model = core.mf_parse(self.path.read_bytes())
                log("mf factors loaded", path=self.path, items=self.model["n"], built=self.model.get("built"),
                    seconds=round(time.monotonic() - t, 2))
            except Exception as e:  # noqa: BLE001 (any broken file: MF off, the service runs on)
                log("mf factors unusable, MF off", path=self.path, error=f"{type(e).__name__}: {e}"[:200])
                self.model = None
        return self.model


# ---------------------------------------------------------------------------- the pipeline
class Engine:
    def __init__(self):
        self.cfg = load_config()
        self.jf = Jellyfin(os.environ.get("JELLYFIN_URL", "http://jellyfin.external.svc.cluster.local"),
                           os.environ["JELLYFIN_API_KEY"])
        self.seerr = Seerr(os.environ.get("SEERR_URL", "http://seerr.media.svc.cluster.local:5055"),
                           os.environ["SEERR_API_KEY"])
        self.radarr = Arr("radarr", os.environ.get("RADARR_URL", "http://radarr.media.svc.cluster.local:7878"),
                          os.environ["RADARR_API_KEY"])
        self.sonarr = Arr("sonarr", os.environ.get("SONARR_URL", "http://sonarr.media.svc.cluster.local:8989"),
                          os.environ["SONARR_API_KEY"])
        llm_url = os.environ.get("LLM_URL", "")
        self.llm = Llm(llm_url, os.environ.get("LLM_API_KEY", ""), os.environ.get("LLM_MODEL", "local")) if llm_url else None
        self.mf = MfModel(MF_FILE)

    # shared data of one run (all users) -------------------------------------------------
    def shared(self) -> dict:
        owned = self.jf.owned()
        radarr, _ = self.radarr.keys()
        sonarr, sonarr_tvdb = self.sonarr.keys()
        reqs = self.seerr.requests()
        known = self.seerr.known_media()
        seerr_users = {norm_uid(u["jellyfinUserId"]): u["id"] for u in self.seerr.users() if u.get("jellyfinUserId")}
        return {"owned": owned | radarr | sonarr | known, "tvdb": sonarr_tvdb, "requests": reqs,
                "requested": {r["key"] for r in reqs}, "seerrUsers": seerr_users,
                "jellyfinUsers": {u["id"]: u["name"] for u in self.jf.users()}}

    def refresh_user(self, uid: str, sh: dict | None = None) -> dict:
        t0 = time.monotonic()
        sh = sh or self.shared()
        cfg, now = self.cfg, now_utc()
        stats: dict = {"user": uid, "startedAt": now.isoformat(timespec="seconds")}
        if uid not in sh["jellyfinUsers"]:
            raise KeyError(uid)
        seerr_uid = sh["seerrUsers"].get(uid)

        # 1. inputs and seeds
        library = self.jf.library(uid)
        own_requests = [r for r in sh["requests"] if seerr_uid is not None and r["userId"] == seerr_uid]
        watchlist = self.seerr.watchlist(seerr_uid) if seerr_uid is not None else []
        seeds = core.build_seeds(library, own_requests, watchlist, now, cfg)
        details = {k: self.seerr.details(k) for k in seeds}
        for k, s in seeds.items():  # requests carry no title: take it from TMDb
            if details.get(k) and s["title"] in ("?", None):
                s["title"] = details[k]["title"]
        profile = core.build_profile(seeds, details)
        stats.update(seeds=len(seeds), seedKinds={k: sum(1 for s in seeds.values() if s["kind"] == k)
                                                    for k in sorted({s["kind"] for s in seeds.values()})},
                     ownRequests=len(own_requests), watchlist=len(watchlist), libraryActivity=len(library))

        # what never comes back: watched/owned/queued, requested by anyone, disliked, own seeds
        excluded = set(sh["owned"]) | sh["requested"] | set(seeds)

        # MF user vector: positive film seeds (MovieLens factors, else predicted from content)
        mf_model = self.mf.get() if (cfg["W_MF"] > 0 or cfg["CAP_MF"] > 0 or MF_SHADOW) else None
        mf_sc, mf_stats = None, {"loaded": mf_model is not None}
        t_mf = time.monotonic()
        if mf_model is not None:
            try:
                rows, srcs = [], {"real": 0, "pred": 0}
                for k, s in seeds.items():
                    d = details.get(k)
                    if s["weight"] <= 0 or k[0] != "movie" or not d:
                        continue
                    vec, src = core.mf_item_vector(mf_model, k, d.get("mfRaw"), d.get("imdbId"))
                    if vec is not None:
                        rows.append((vec, s["weight"]))
                        srcs[src] += 1
                u = core.mf_user_vector(mf_model, rows)
                mf_sc = core.mf_scorer(mf_model, u) if u is not None else None
                mf_stats.update(seeds=srcs, seconds=round(time.monotonic() - t_mf, 2))
            except Exception as e:  # noqa: BLE001 (MF is optional: off for this run, never fatal)
                mf_sc = None
                mf_stats["error"] = f"{type(e).__name__}: {e}"[:200]
                log("mf user vector failed, MF off", user=uid, error=mf_stats["error"])

        # 2. classic candidates: TMDb recommendations + similar of the strongest seeds
        pool: dict = {}
        top = core.positive_seeds(seeds, int(cfg["MAX_SEEDS"]))
        for s in top:
            for page in range(1, int(cfg["LIST_PAGES"]) + 1):
                off = 20 * (page - 1)
                core.add_candidates(pool, s, self.seerr.related(s["key"], "recommendations", page), 1.0, off)
                core.add_candidates(pool, s, self.seerr.related(s["key"], "similar", page), cfg["SIMILAR_FACTOR"], off)
        pool = core.exclude(pool, excluded)
        stats["classicCandidates"] = len(pool)

        # cap what the AI rates: the most promising per media type (before details: cheap; MF
        # from the list data only: real factors or the lite content map)
        mf_cap = None
        if mf_sc is not None and cfg["CAP_MF"] > 0:
            try:
                raw = {k: core.mf_score(mf_model, mf_sc, k, core.mf_raw_features(c, k[0], lite=True), lite=True)
                       for k, c in pool.items()}
                mf_cap = core.mf_percentiles(raw, list(pool))
            except Exception as e:  # noqa: BLE001
                mf_stats["error"] = f"{type(e).__name__}: {e}"[:200]
                log("mf cap scores failed, today's cap", user=uid, error=mf_stats["error"])
        pool = core.cap_pool(pool, seeds, int(cfg["MAX_CANDIDATES"]), cfg, mf_cap)

        # 3. AI-proposed titles (resolved through TMDb search; unresolvable ones dropped)
        profile_txt = core.profile_text(profile, seeds)
        llm_state = {"up": self.llm is not None, "error": None if self.llm else "LLM_URL not set",
                     "seconds": 0.0, "batches": 0, "failedBatches": 0, "tokens": 0}
        proposed = 0
        if self.llm is not None and top:
            avoid = [s["title"] for s in sorted(seeds.values(), key=lambda s: -abs(s["weight"]))]
            try:
                content, secs, usage = self.llm.json_chat(
                    core.proposal_messages(profile_txt, int(cfg["AI_PROPOSALS"]), avoid),
                    core.proposal_schema(int(cfg["AI_PROPOSALS"])), 600)
                llm_state["seconds"] += secs
                llm_state["tokens"] += int(usage.get("total_tokens") or 0)
                for p in core.parse_proposals(content, int(cfg["AI_PROPOSALS"])):
                    try:
                        hit = core.resolve_proposal(p, self.seerr.search(p["title"]))
                    except RuntimeError:
                        hit = None
                    if hit is None:
                        log("ai proposal unresolved, dropped", title=json.dumps(p["title"]), year=p["year"])
                        continue
                    key = (hit["mediaType"], hit["id"])
                    if key in excluded:
                        continue
                    if key in pool:
                        pool[key]["source"] = "classic+ai"
                    else:
                        c = core.new_candidate(key, hit)
                        c["source"] = "ai"
                        pool[key] = c
                    proposed += 1
            except RuntimeError as e:
                llm_state.update(up=False, error=str(e))
                log("LLM unavailable: classic scores only", user=uid, error=e)
        stats["aiProposals"] = proposed

        # 4. details of the pool (genres, keywords, franchise, tvdb id), last exclusions
        for key in list(pool):
            d = self.seerr.details(key)
            if d is None:
                continue
            if d["adult"] or (key[0] == "tv" and d.get("tvdbId") in sh["tvdb"]):
                del pool[key]
                continue
            c = pool[key]
            c.update({k: d[k] for k in ("genres", "keywords", "collection", "overview", "imdbId", "mfRaw")})
            c["title"], c["year"] = d["title"], d["year"] or c["year"]
            if d["voteCount"]:
                c.update(voteAverage=d["voteAverage"], voteCount=d["voteCount"], popularity=d["popularity"])
        stats["candidates"] = len(pool)

        # 5. classic score
        comps = core.classic_scores(pool, profile, cfg)
        classic = {k: v["classic"] for k, v in comps.items()}

        # 6. AI score: the model rates EVERY candidate, in batches, JSON-schema constrained
        ai_raw: dict = {}
        reasons: dict = {}
        if self.llm is not None and llm_state["up"] and pool:
            ordered = sorted(pool.values(), key=lambda c: -classic[c["key"]])
            by_id: dict = {}
            batches, cur = [], []
            for c in ordered:  # TMDb ids are unique per media type, not across: never mix twins in a batch
                if c["key"][1] in {x["key"][1] for x in cur} or len(cur) >= LLM_BATCH:
                    batches.append(cur)
                    cur = []
                cur.append(c)
            if cur:
                batches.append(cur)
            for batch in batches:
                ids = [c["key"][1] for c in batch]
                by_id = {c["key"][1]: c["key"] for c in batch}
                got = {}
                for attempt in (1, 2):
                    try:
                        content, secs, usage = self.llm.json_chat(
                            core.scoring_messages(profile_txt, batch), core.rating_schema(ids), 64 * len(ids) + 128)
                        llm_state["seconds"] += secs
                        llm_state["tokens"] += int(usage.get("total_tokens") or 0)
                        got = core.parse_ratings(content, ids)
                        if got:
                            break
                        llm_state["error"] = f"unusable answer (finish_reason={usage.get('finish_reason')})"
                        log("LLM batch answer unusable", user=uid, attempt=attempt,
                            finish_reason=usage.get("finish_reason"), chars=len(content))
                    except RuntimeError as e:
                        llm_state["error"] = str(e)
                        if attempt == 2 or "HTTP 4" in str(e):
                            break
                llm_state["batches"] += 1
                if not got:
                    llm_state["failedBatches"] += 1
                    if not ai_raw and llm_state["failedBatches"] >= 2:
                        llm_state["up"] = False  # down, not just one bad answer: stop trying
                        log("LLM unavailable: classic scores only", user=uid, error=llm_state["error"])
                        break
                for rid, (score, reason) in got.items():
                    ai_raw[by_id[rid]] = score
                    if reason:
                        reasons[by_id[rid]] = reason
        ai = core.normalise_ai(ai_raw) if ai_raw else None
        if ai is None and pool:
            log("fusion: classic only", user=uid, reason=llm_state["error"] or "no AI scores")
        stats["llm"] = {**llm_state, "rated": len(ai_raw), "seconds": round(llm_state["seconds"], 1)}

        # 7. MF score (full content map now that details are known), fusion + diversity
        mf, mf_raw = None, {}
        if mf_sc is not None:
            try:
                mf_raw = {k: core.mf_score(mf_model, mf_sc, k, c.get("mfRaw"), lite=False, imdb=c.get("imdbId"))
                          for k, c in pool.items()}
                mf = core.mf_percentiles(mf_raw, list(pool))
                mf_stats["sources"] = {str(s): sum(1 for v in mf_raw.values() if v[1] == s)
                                       for s in ("real", "pred", None)}
                mf_stats["seconds"] = round(time.monotonic() - t_mf, 2)
            except Exception as e:  # noqa: BLE001
                mf, mf_raw = None, {}
                mf_stats["error"] = f"{type(e).__name__}: {e}"[:200]
                log("mf scores failed, fusion without MF", user=uid, error=mf_stats["error"])
        stats["mf"] = mf_stats
        fused = core.fuse(classic, ai, cfg, mf)
        keys = core.order(fused)
        groups = {k: core.diversity_groups(pool[k], seeds) for k in keys}
        keys = core.diversify(keys, groups, int(cfg["DIVERSITY_MAX"]), int(cfg["DIVERSITY_TOP"]))[:int(cfg["KEEP"])]

        items, debug = [], []
        for rank, k in enumerate(keys, 1):
            c, comp, f = pool[k], comps[k], fused[k]
            reason = reasons.get(k) or core.classic_reason(c, seeds, profile)
            items.append({"tmdbId": k[1], "mediaType": k[0], "reason": reason})
            bs = core.best_seed(c, seeds)
            debug.append({
                "rank": rank, "tmdbId": k[1], "mediaType": k[0], "title": c["title"], "year": c["year"],
                "source": c["source"], "final": round(f["final"], 4), "rrf": round(f["rrf"], 5),
                "classic": round(comp["classic"], 4), "ai": None if not ai or k not in ai else round(ai[k], 4),
                "aiRaw": ai_raw.get(k), "classicRank": f["classicRank"], "aiRank": f["aiRank"],
                "mf": None if mf is None else round(mf[k], 4), "mfSource": (mf_raw.get(k) or (None, None))[1],
                "mfRank": f.get("mfRank"),
                "components": {n: (None if comp[n] is None else round(comp[n], 4))
                               for n in ("seed", "frequency", "affinity", "prior", "raw")},
                "seeds": len(c["seeds"]), "bestSeed": bs and {"title": bs["title"], "kind": bs["kind"]},
                "groups": groups.get(k), "genres": c["genres"], "reason": reason,
                "reasonSource": "ai" if k in reasons else "classic",
            })
        stats["seconds"] = round(time.monotonic() - t0, 1)
        stats["mode"] = "fusion" if ai else "classic-only"
        result = {
            "generatedAt": now_utc().isoformat(timespec="seconds").replace("+00:00", "Z"),
            "items": items,
            "debug": {"stats": stats, "weights": cfg, "items": debug,
                      "seeds": sorted(({**s, "key": list(s["key"])} for s in seeds.values()),
                                      key=lambda s: -s["weight"]),
                      "profile": {"genres": dict(sorted(profile["genres"].items(), key=lambda kv: -kv[1])[:20]),
                                  "keywords": dict(sorted(profile["keywords"].items(), key=lambda kv: -kv[1])[:30])}},
        }
        write_json(CACHE / "results" / f"{uid}.json", result)
        log("refreshed", user=uid, items=len(items), mode=stats["mode"], seconds=stats["seconds"],
            llm_seconds=stats["llm"]["seconds"], rated=len(ai_raw), candidates=len(pool))
        return stats


# ---------------------------------------------------------------------------- worker
class Worker:
    """One refresh at a time (the LLM is one CPU-bound slot); jobs are deduplicated."""

    def __init__(self, engine: Engine):
        self.engine, self.q = engine, queue.Queue()
        self.lock = threading.Lock()
        self.pending: dict = {}  # job -> threading.Event
        self.status: dict = {"running": None, "runs": {}, "lastAll": None}
        threading.Thread(target=self.loop, daemon=True).start()

    def submit(self, job: str) -> threading.Event:
        with self.lock:
            ev = self.pending.get(job)
            if ev is None:
                ev = self.pending[job] = threading.Event()
                self.q.put(job)
            return ev

    def loop(self):
        while True:
            job = self.q.get()
            self.status["running"] = job
            t0 = time.monotonic()
            try:
                if job == "*":
                    sh = self.engine.shared()
                    per = {}
                    for uid in sorted(sh["jellyfinUsers"]):
                        per[uid] = self.run_one(uid, sh)
                    self.status["lastAll"] = {"finishedAt": now_utc().isoformat(timespec="seconds"),
                                              "seconds": round(time.monotonic() - t0, 1), "users": per}
                    log("refresh of all users done", users=len(per), seconds=round(time.monotonic() - t0, 1))
                else:
                    self.run_one(job, None)
            except Exception as e:  # noqa: BLE001 (keep the worker alive, log the error)
                log("refresh failed", job=job, error=type(e).__name__, detail=str(e)[:300])
                traceback.print_exc()
            finally:
                self.status["running"] = None
                with self.lock:
                    ev = self.pending.pop(job, None)
                if ev:
                    ev.set()

    def run_one(self, uid: str, sh):
        try:
            st = self.engine.refresh_user(uid, sh)
            self.status["runs"][uid] = {k: st.get(k) for k in ("startedAt", "seconds", "mode", "candidates", "seeds")}
            self.status["runs"][uid]["llm"] = st.get("llm")
            return self.status["runs"][uid]
        except KeyError:
            log("unknown Jellyfin user, nothing to do", user=uid)
            return {"error": "unknown user"}
        except Exception as e:  # noqa: BLE001
            log("refresh failed", user=uid, error=type(e).__name__, detail=str(e)[:300])
            self.status["runs"][uid] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            return self.status["runs"][uid]


# ---------------------------------------------------------------------------- HTTP
class Handler(http.server.BaseHTTPRequestHandler):
    worker: Worker = None  # type: ignore[assignment]
    server_version = "recs/1"

    def log_message(self, fmt, *args):  # quiet access log: only errors and refreshes
        pass

    def send(self, code: int, data) -> None:
        body = json.dumps(data, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        return [p for p in u.path.split("/") if p], urllib.parse.parse_qs(u.query)

    def do_GET(self):  # noqa: N802
        parts, qs = self.route()
        if parts == ["healthz"]:
            return self.send(200, {"status": "ok"})
        if parts == ["v1", "status"]:
            return self.send(200, self.worker.status)
        if len(parts) in (3, 4) and parts[:2] == ["v1", "recs"] and (len(parts) == 3 or parts[3] == "debug"):
            uid = parts[2]
            if not UID_RE.fullmatch(uid):
                return self.send(400, {"error": "jellyfinUserId must be a Jellyfin user id"})
            cached = read_json(CACHE / "results" / f"{norm_uid(uid)}.json")
            if len(parts) == 4:
                if not cached:
                    return self.send(404, {"error": "no results for this user (yet)"})
                return self.send(200, {"generatedAt": cached["generatedAt"], **cached.get("debug", {})})
            try:
                limit = max(0, min(100, int((qs.get("limit") or ["20"])[0])))
            except ValueError:
                return self.send(400, {"error": "limit must be an integer"})
            if not cached:  # unknown user (or not computed yet): empty, never an error
                return self.send(200, {"generatedAt": now_utc().isoformat(timespec="seconds").replace("+00:00", "Z"),
                                       "items": []})
            return self.send(200, {"generatedAt": cached["generatedAt"], "items": cached["items"][:limit]})
        return self.send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        parts, qs = self.route()
        wait = (qs.get("wait") or ["0"])[0] in ("1", "true")
        if parts == ["v1", "refresh"]:
            job = "*"
        elif len(parts) == 3 and parts[:2] == ["v1", "refresh"] and UID_RE.fullmatch(parts[2]):
            job = norm_uid(parts[2])
        else:
            return self.send(404, {"error": "not found"})
        log("refresh requested", job=job, wait=wait)
        ev = self.worker.submit(job)
        if not wait:
            return self.send(202, {"status": "queued", "job": job})
        ev.wait()
        st = self.worker.status
        return self.send(200, {"status": "done", "job": job,
                               "result": st["lastAll"] if job == "*" else st["runs"].get(job)})


def main():
    engine = Engine()
    worker = Worker(engine)
    Handler.worker = worker
    (CACHE / "results").mkdir(parents=True, exist_ok=True)
    # First start (or a user without results): compute in the background; serve meanwhile.
    if os.environ.get("RECS_REFRESH_ON_START", "missing") == "missing":
        try:
            users = engine.jf.users()
            if any(not (CACHE / "results" / f"{u['id']}.json").exists() for u in users):
                worker.submit("*")
        except RuntimeError as e:
            log("startup refresh skipped", error=e)
    port = int(os.environ.get("PORT", "8080"))
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    log("recs listening", port=port, llm="on" if engine.llm else "off",
        w_classic=engine.cfg["W_CLASSIC"], w_ai=engine.cfg["W_AI"], w_mf=engine.cfg["W_MF"],
        cap_mf=engine.cfg["CAP_MF"], mf_file="present" if MF_FILE.exists() else "missing (MF off)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
