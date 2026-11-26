"""recs core: seeds, taste profile, classic score, AI-answer validation, fusion, diversity.

Pure functions only (no network, no files): recs_server.py feeds them the data it read
from Jellyfin, Seerr, Radarr/Sonarr and the local LLM, and k8s/apps/recs/tests checks
them offline. Python stdlib only. Design: docs/decisions.md "Recommendations".

Keys: every title is (mediaType, tmdbId) with mediaType "movie" or "tv".
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re

# ---------------------------------------------------------------------------- config
# Every weight is overridable from the environment (recs_server.load_config): RECS_<NAME>.
DEFAULTS = {
    # Seed weights (positive = taste, negative = anti-taste). The order is the operator's:
    # favourite > completed watch / high rating > own request > watchlist > partial watch.
    "W_FAVOURITE": 1.0,
    "W_COMPLETED": 0.8,  # also a high rating (>= RATING_HIGH) or a "like"
    "W_REQUEST": 0.6,  # the user's OWN Seerr requests only
    "W_WATCHLIST": 0.4,
    "W_PARTIAL": 0.25,
    "W_DISLIKED": -0.8,  # "dislike" or rating <= RATING_LOW
    "W_ABANDONED": -0.3,  # started, stopped early, untouched for ABANDON_DAYS
    # Each extra play of a title adds this much (relative), at most PLAYCOUNT_MAX_BONUS.
    "PLAYCOUNT_STEP": 0.1,
    "PLAYCOUNT_MAX_BONUS": 0.3,
    # Recency: weight x max(floor, 0.5 ** (age_days / half_life)).
    "HALF_LIFE_WATCH_DAYS": 180.0,
    "HALF_LIFE_REQUEST_DAYS": 90.0,
    "HALF_LIFE_WATCHLIST_DAYS": 180.0,
    "RECENCY_FLOOR": 0.35,
    "RATING_HIGH": 8.0,  # Jellyfin user rating, 0-10
    "RATING_LOW": 4.0,
    "COMPLETED_FRACTION": 0.8,  # series: share of episodes played that counts as completed
    "ABANDON_MAX_PCT": 20.0,  # movie: stopped before this % ...
    "ABANDON_DAYS": 21.0,  # ... and not touched for this many days
    # Candidate generation
    "MAX_SEEDS": 40,  # strongest positive seeds that generate candidates
    "SIMILAR_FACTOR": 0.5,  # TMDb "similar" counts half as much as "recommendations"
    "MAX_CANDIDATES": 120,  # pool size the AI rates (best classic first) + the AI's own proposals
    "AI_PROPOSALS": 10,
    # Classic score = weighted sum of its components (each 0-1), then normalised per user.
    "C_SEED": 0.45,
    "C_FREQUENCY": 0.15,
    "C_AFFINITY": 0.25,
    "C_PRIOR": 0.15,
    "GENRE_SHARE": 0.5,  # affinity = GENRE_SHARE x genre + (1 - GENRE_SHARE) x keyword
    "PRIOR_MIN_VOTES": 200,  # Bayesian average: m
    "PRIOR_MEAN": 6.5,  # Bayesian average: C
    # Fusion
    "W_CLASSIC": 0.5,
    "W_AI": 0.5,
    "RRF_K": 60,
    # Diversity: at most DIVERSITY_MAX titles per seed or franchise in the top DIVERSITY_TOP.
    "DIVERSITY_MAX": 3,
    "DIVERSITY_TOP": 20,
    "KEEP": 100,  # results kept per user
}

POSITIVE_KINDS = ("favourite", "completed", "request", "watchlist", "partial")
REASON_TEMPLATES = {
    "favourite": "Because you favourited {title}",
    "completed": "Because you watched {title}",
    "rated": "Because you rated {title} highly",
    "request": "Because you requested {title}",
    "watchlist": "Because {title} is on your watchlist",
    "partial": "Because you started {title}",
}


# TMDb's TV genres are combined names; map them onto the film genres so one profile fits both.
TV_GENRES = {
    "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"],
    "Action & Adventure": ["Action", "Adventure"],
    "War & Politics": ["War"],
    "Kids": ["Family"],
}
# Keywords that say nothing about taste (production trivia, source material).
NOISE_KEYWORDS = {
    "aftercreditsstinger", "duringcreditsstinger", "sequel", "prequel", "remake", "reboot",
    "based on novel or book", "based on comic", "based on true story", "based on video game",
    "based on young adult novel", "based on manga", "based on play or musical", "woman director",
    "3d", "live action remake", "short film",
}
# Keywords that name a shared universe: titles with one count as one franchise for diversity.
FRANCHISE_KEYWORD = re.compile(r"cinematic universe|extended universe|universe \(|wizarding world|star wars")


def norm_genres(names) -> list[str]:
    out: list[str] = []
    for n in names or []:
        for g in TV_GENRES.get(n, [n]):
            if g not in out:
                out.append(g)
    return out


def norm_keywords(names) -> list[str]:
    return [k for k in names or [] if k and k.lower() not in NOISE_KEYWORDS]


def parse_time(value) -> dt.datetime | None:
    """ISO 8601 (Jellyfin's 7-digit fractions and Seerr's Z included) -> aware UTC."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    m = re.fullmatch(r"(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)", s)
    if m:  # Python wants at most 6 fraction digits
        frac = (m.group(2) or "")[:7]
        s = m.group(1) + frac + (m.group(3) or "")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def recency(when: dt.datetime | None, now: dt.datetime, half_life_days: float, floor: float) -> float:
    """1.0 for now, halving every half_life_days, never below floor (unknown date: floor)."""
    if when is None:
        return floor
    age = max(0.0, (now - when).total_seconds() / 86400.0)
    return max(floor, 0.5 ** (age / half_life_days))


# ---------------------------------------------------------------------------- seeds
def build_seeds(library: list[dict], requests: list[dict], watchlist: list[dict],
                now: dt.datetime, cfg: dict) -> dict:
    """The user's seeds: {key: {"key", "title", "kind", "weight", "when"}}.

    library: one entry per Jellyfin movie/series that has user data, already normalised by
      the server: {"key", "title", "favourite", "played", "playCount", "lastPlayed",
      "playedPct" (0-100, movies), "episodesPlayed", "episodesTotal" (series), "rating",
      "likes" (True/False/None)}.
    requests: the user's OWN Seerr requests: {"key", "title", "createdAt"}.
    watchlist: {"key", "title", "createdAt"}.
    A title keeps the strongest positive signal; a negative signal (disliked) always wins.
    """
    seeds: dict = {}

    def offer(key, title, kind, weight, when):
        if key is None:
            return
        cur = seeds.get(key)
        if cur is not None and cur["weight"] < 0:
            return  # disliked stays disliked
        if weight < 0 or cur is None or weight > cur["weight"]:
            seeds[key] = {"key": key, "title": title, "kind": kind, "weight": round(weight, 4),
                          "when": when.isoformat() if when else None}

    for it in library:
        key, title = it.get("key"), it.get("title") or "?"
        last = parse_time(it.get("lastPlayed"))
        rec_w = recency(last, now, cfg["HALF_LIFE_WATCH_DAYS"], cfg["RECENCY_FLOOR"])
        rating, likes = it.get("rating"), it.get("likes")
        if likes is False or (rating is not None and rating <= cfg["RATING_LOW"]):
            offer(key, title, "disliked", cfg["W_DISLIKED"], last)
            continue
        plays = int(it.get("playCount") or 0)
        bonus = 1.0 + min(cfg["PLAYCOUNT_MAX_BONUS"], cfg["PLAYCOUNT_STEP"] * max(0, plays - 1))
        if it.get("favourite"):
            offer(key, title, "favourite", cfg["W_FAVOURITE"] * max(rec_w, 0.75) * bonus, last)
        if likes is True or (rating is not None and rating >= cfg["RATING_HIGH"]):
            offer(key, title, "rated", cfg["W_COMPLETED"] * rec_w * bonus, last)
        ep_total = int(it.get("episodesTotal") or 0)
        ep_played = int(it.get("episodesPlayed") or 0)
        completed = bool(it.get("played")) or (ep_total > 0 and ep_played / ep_total >= cfg["COMPLETED_FRACTION"])
        if completed:
            offer(key, title, "completed", cfg["W_COMPLETED"] * rec_w * bonus, last)
            continue
        pct = it.get("playedPct")
        started = ep_played > 0 or (pct is not None and pct > 0)
        if not started:
            continue
        idle_days = (now - last).total_seconds() / 86400.0 if last else 0.0
        if (ep_total == 0 and pct is not None and pct < cfg["ABANDON_MAX_PCT"]
                and idle_days >= cfg["ABANDON_DAYS"] and key not in seeds):
            offer(key, title, "abandoned", cfg["W_ABANDONED"], last)
        else:
            offer(key, title, "partial", cfg["W_PARTIAL"] * rec_w, last)

    for r in requests:
        when = parse_time(r.get("createdAt"))
        w = cfg["W_REQUEST"] * recency(when, now, cfg["HALF_LIFE_REQUEST_DAYS"], cfg["RECENCY_FLOOR"])
        offer(r.get("key"), r.get("title") or "?", "request", w, when)
    for x in watchlist:
        when = parse_time(x.get("createdAt"))
        w = cfg["W_WATCHLIST"] * recency(when, now, cfg["HALF_LIFE_WATCHLIST_DAYS"], cfg["RECENCY_FLOOR"])
        offer(x.get("key"), x.get("title") or "?", "watchlist", w, when)
    return seeds


def positive_seeds(seeds: dict, limit: int) -> list[dict]:
    pos = [s for s in seeds.values() if s["weight"] > 0]
    pos.sort(key=lambda s: (-s["weight"], str(s["key"])))
    return pos[:limit]


# ---------------------------------------------------------------------------- profile
def build_profile(seeds: dict, details: dict) -> dict:
    """Genre and keyword affinity from the seeds' TMDb details, each scaled to [-1, 1].

    details: {key: {"genres": [names], "keywords": [names], ...}} (missing = skipped).
    """
    genres: dict[str, float] = {}
    keywords: dict[str, float] = {}
    for key, s in seeds.items():
        d = details.get(key)
        if not d:
            continue
        for g in d.get("genres") or []:
            genres[g] = genres.get(g, 0.0) + s["weight"]
        for k in d.get("keywords") or []:
            keywords[k] = keywords.get(k, 0.0) + s["weight"]

    def scale(m):
        # the strongest liked term = 1.0; dislikes on the same scale, clipped at -1
        top = max([v for v in m.values() if v > 0] + [0.0]) or max([abs(v) for v in m.values()] + [0.0])
        return {k: round(max(-1.0, v / top), 4) for k, v in m.items()} if top > 0 else {}

    return {"genres": scale(genres), "keywords": scale(keywords)}


def top_terms(weights: dict, n: int) -> list[str]:
    return [k for k, v in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])) if v > 0][:n]


def affinity(profile: dict, genres: list[str], keywords: list[str], genre_share: float) -> float:
    """0-1: how well a title's genres/keywords match the profile (dislikes pull it down)."""
    def part(table, terms):
        vals = [table.get(t, 0.0) for t in terms or []]
        if not vals:
            return None
        # mean of the matched terms, but a single strong match counts for a lot
        return 0.5 * (sum(vals) / len(vals)) + 0.5 * max(vals)
    g = part(profile.get("genres", {}), genres)
    k = part(profile.get("keywords", {}), keywords)
    if g is None and k is None:
        return 0.0
    if g is None:
        val = k
    elif k is None:
        val = g
    else:
        val = genre_share * g + (1.0 - genre_share) * k
    return min(1.0, max(0.0, val))


# ---------------------------------------------------------------------------- candidates
def add_candidates(pool: dict, seed: dict, results: list[dict], factor: float) -> None:
    """Fold one TMDb recommendations/similar page of a seed into the pool.

    results: Seerr's TMDb list items ({"id", "mediaType", "title"/"name", ...}).
    Contribution = seed weight x factor / (1 + rank / 10): early results count more.
    """
    for rank, r in enumerate(results):
        mt = r.get("mediaType")
        if mt not in ("movie", "tv") or not isinstance(r.get("id"), int) or r.get("adult"):
            continue
        key = (mt, r["id"])
        contrib = seed["weight"] * factor / (1.0 + rank / 10.0)
        c = pool.get(key)
        if c is None:
            c = pool[key] = new_candidate(key, r)
        c["seedScore"] += contrib
        c["seeds"][seed["key"]] = max(c["seeds"].get(seed["key"], 0.0), contrib)


def new_candidate(key, r: dict) -> dict:
    date = r.get("releaseDate") or r.get("firstAirDate") or ""
    return {
        "key": key, "title": r.get("title") or r.get("name") or "?",
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "genreIds": list(r.get("genreIds") or []), "genres": [], "keywords": [],
        "voteAverage": float(r.get("voteAverage") or 0.0), "voteCount": int(r.get("voteCount") or 0),
        "popularity": float(r.get("popularity") or 0.0), "overview": r.get("overview") or "",
        "collection": None, "seedScore": 0.0, "seeds": {}, "source": "classic",
    }


def cap_pool(pool: dict, seeds: dict, limit: int, cfg: dict) -> dict:
    """Keep the `limit` most promising candidates (seed score + a little rating prior), split
    between films and series by their share of the positive seed weight (at least 25% each
    when both have seeds), so a film-heavy history still leaves room for series."""
    weight = {"movie": 0.0, "tv": 0.0}
    for s in seeds.values():
        if s["weight"] > 0:
            weight[s["key"][0]] += s["weight"]
    total = sum(weight.values()) or 1.0
    share = {mt: weight[mt] / total for mt in weight}
    if all(weight.values()):
        share = {mt: min(0.75, max(0.25, v)) for mt, v in share.items()}

    def promise(c):
        return c["seedScore"] + 0.2 * prior(c["voteAverage"], c["voteCount"], cfg, c["popularity"])

    out: dict = {}
    for mt in ("movie", "tv"):
        rows = sorted((c for c in pool.values() if c["key"][0] == mt), key=lambda c: (-promise(c), str(c["key"])))
        for c in rows[:int(round(limit * share[mt]))]:
            out[c["key"]] = c
    if len(out) < limit:  # one side had fewer candidates than its share: fill from the rest
        rest = sorted((c for k, c in pool.items() if k not in out), key=lambda c: (-promise(c), str(c["key"])))
        for c in rest[:limit - len(out)]:
            out[c["key"]] = c
    return out


def exclude(pool: dict, excluded: set) -> dict:
    """Drop every key in excluded (watched, in the library, requested by anyone, disliked,
    and the user's own seeds)."""
    return {k: c for k, c in pool.items() if k not in excluded}


# ---------------------------------------------------------------------------- classic score
def prior(vote_average: float, vote_count: int, cfg: dict, popularity: float = 0.0) -> float:
    """0-1 from TMDb's rating (Bayesian average, so 3 votes of 10 don't win) and popularity."""
    m, c = cfg["PRIOR_MIN_VOTES"], cfg["PRIOR_MEAN"]
    bayes = (vote_count * vote_average + m * c) / (vote_count + m) if vote_count + m > 0 else c
    rating = min(1.0, max(0.0, (bayes - 5.0) / 3.5))  # 5.0 -> 0, 8.5 -> 1
    pop = min(1.0, math.log1p(max(0.0, popularity)) / math.log1p(200.0))
    return 0.8 * rating + 0.2 * pop


def minmax(values: dict) -> dict:
    """Per-user normalisation to 0-1 (all equal -> 1.0 each, so nothing is zeroed out)."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def classic_scores(pool: dict, profile: dict, cfg: dict) -> dict:
    """{key: {"seed", "frequency", "affinity", "prior", "raw", "classic"}}; classic is 0-1 per user.

    Candidates the classic side never saw (AI proposals: no seed) get affinity only.
    """
    # seed strength and frequency are scaled per media type: films outnumber series among
    # most people's seeds, and the best series should still be able to reach the top
    max_seed: dict = {}
    max_n: dict = {}
    for c in pool.values():
        mt = c["key"][0]
        max_seed[mt] = max(max_seed.get(mt, 0.0), c["seedScore"])
        max_n[mt] = max(max_n.get(mt, 0), len(c["seeds"]))
    comps = {}
    for key, c in pool.items():
        aff = affinity(profile, c["genres"], c["keywords"], cfg["GENRE_SHARE"])
        if not c["seeds"]:
            comps[key] = {"seed": 0.0, "frequency": 0.0, "affinity": aff, "prior": None, "raw": aff}
            continue
        ms, mn = max_seed.get(key[0], 0.0), max_n.get(key[0], 0)
        seed = c["seedScore"] / ms if ms > 0 else 0.0
        freq = math.log1p(len(c["seeds"])) / math.log1p(mn) if mn > 0 else 0.0
        pri = prior(c["voteAverage"], c["voteCount"], cfg, c["popularity"])
        raw = (cfg["C_SEED"] * seed + cfg["C_FREQUENCY"] * freq + cfg["C_AFFINITY"] * aff
               + cfg["C_PRIOR"] * pri)
        comps[key] = {"seed": seed, "frequency": freq, "affinity": aff, "prior": pri, "raw": raw}
    norm = minmax({k: v["raw"] for k, v in comps.items()})
    for k, v in comps.items():
        v["classic"] = norm[k]
    return comps


def best_seed(c: dict, seeds: dict) -> dict | None:
    if not c["seeds"]:
        return None
    key = max(c["seeds"], key=lambda k: (c["seeds"][k], str(k)))
    return seeds.get(key)


def classic_reason(c: dict, seeds: dict, profile: dict) -> str:
    s = best_seed(c, seeds)
    if s is not None:
        return REASON_TEMPLATES.get(s["kind"], "Because you watched {title}").format(title=s["title"])
    liked = [g for g in c["genres"] if profile.get("genres", {}).get(g, 0) > 0]
    liked.sort(key=lambda g: -profile["genres"][g])
    if liked:
        return "Matches your taste in " + " and ".join(liked[:2])
    return "Picked for your taste profile"


# ---------------------------------------------------------------------------- the LLM
def rating_schema(ids: list[int]) -> dict:
    """JSON schema for one scoring batch: exactly one rating per candidate id, ids from the
    batch only (enum), integer scores 0-10, a short reason."""
    return {
        "type": "object",
        "properties": {
            "ratings": {
                "type": "array",
                "minItems": len(ids),
                "maxItems": len(ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "enum": list(ids)},
                        "score": {"type": "integer", "minimum": 0, "maximum": 10},
                        "reason": {"type": "string", "maxLength": 90},
                    },
                    "required": ["id", "score", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["ratings"],
        "additionalProperties": False,
    }


PROPOSAL_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "titles": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 120},
                    "year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                    "mediaType": {"type": "string", "enum": ["movie", "tv"]},
                },
                "required": ["title", "year", "mediaType"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["titles"],
    "additionalProperties": False,
}


def proposal_schema(n: int) -> dict:
    s = json.loads(json.dumps(PROPOSAL_SCHEMA_TEMPLATE))
    s["properties"]["titles"]["maxItems"] = max(0, int(n))
    return s


def clean_line(text, limit: int = 140) -> str:
    """One printable line, no control characters, at most limit characters."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = "".join(ch for ch in s if ch.isprintable())
    return s[:limit].rstrip()


def parse_ratings(content: str, ids: list[int]) -> dict:
    """Validate a scoring answer: {id: (score 0-10 float, reason)} for batch ids only.

    Anything malformed is dropped (not guessed): unknown or repeated ids, non-numeric or
    out-of-range scores. A wholly unparsable answer gives {}.
    """
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return {}
    rows = data.get("ratings") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    allowed, out = set(ids), {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid, score = row.get("id"), row.get("score")
        if isinstance(rid, bool) or not isinstance(rid, int) or rid not in allowed or rid in out:
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not (0 <= score <= 10):
            continue
        if isinstance(score, float) and math.isnan(score):
            continue
        out[rid] = (float(score), clean_line(row.get("reason"), 140))
    return out


def parse_proposals(content: str, limit: int) -> list[dict]:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return []
    rows = data.get("titles") if isinstance(data, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title, year, mt = clean_line(row.get("title"), 120), row.get("year"), row.get("mediaType")
        if title and isinstance(year, int) and not isinstance(year, bool) and mt in ("movie", "tv"):
            out.append({"title": title, "year": year, "mediaType": mt})
    return out[:limit]


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def resolve_proposal(proposal: dict, results: list[dict]) -> dict | None:
    """Pick the TMDb search result for an AI proposal: same media type, same normalised
    title (or one contains the other), year within 1. None = unresolvable (dropped)."""
    want = norm_title(proposal["title"])
    best = None
    for r in results:
        if r.get("mediaType") != proposal["mediaType"] or not isinstance(r.get("id"), int):
            continue
        got = norm_title(r.get("title") or r.get("name") or "")
        date = r.get("releaseDate") or r.get("firstAirDate") or ""
        year = int(date[:4]) if date[:4].isdigit() else None
        if year is None or abs(year - proposal["year"]) > 1 or not got:
            continue
        exact = got == want
        if not exact and not (want in got or got in want):
            continue
        rank = (0 if exact else 1, abs(year - proposal["year"]))
        if best is None or rank < best[0]:
            best = (rank, r)
    return best[1] if best else None


def normalise_ai(scores: dict) -> dict:
    """0-10 model scores -> 0-1 per user (min-max; a flat answer keeps score/10)."""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi - lo < 1.0:
        return {k: v / 10.0 for k, v in scores.items()}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


# ---------------------------------------------------------------------------- fusion
def ranks(values: dict) -> dict:
    """1-based ranks, best (highest) first; ties broken by key for determinism."""
    order = sorted(values, key=lambda k: (-values[k], str(k)))
    return {k: i + 1 for i, k in enumerate(order)}


def fuse(classic: dict, ai: dict | None, cfg: dict) -> dict:
    """final = w_classic x classic + w_ai x ai, with reciprocal-rank fusion as the tie-break.

    classic: {key: 0-1}; ai: {key: 0-1} or None (LLM down: final = classic only). A
    candidate the AI did not rate (failed batch) falls back to its classic score.
    Returns {key: {"final", "rrf", "classicRank", "aiRank"}}.
    """
    wc, wa = float(cfg["W_CLASSIC"]), float(cfg["W_AI"])
    if wc + wa <= 0:
        wc, wa = 1.0, 0.0
    c_rank = ranks(classic)
    a_rank = ranks(ai) if ai else {}
    k = float(cfg["RRF_K"])
    out = {}
    for key, c in classic.items():
        a = ai.get(key) if ai else None
        final = c if a is None else (wc * c + wa * a) / (wc + wa)
        rrf = 1.0 / (k + c_rank[key]) + (1.0 / (k + a_rank[key]) if key in a_rank else 0.0)
        out[key] = {"final": final, "rrf": rrf, "classicRank": c_rank[key], "aiRank": a_rank.get(key)}
    return out


def order(fused: dict) -> list:
    """Keys by final score (3 decimals), then by RRF, then by key."""
    return sorted(fused, key=lambda k: (-round(fused[k]["final"], 3), -fused[k]["rrf"], str(k)))


def diversify(keys: list, groups: dict, max_per_group: int, top: int) -> list:
    """Keep at most max_per_group titles sharing a group (a seed or a franchise) among the
    first `top`; the rest move down, after the top, in their original order.

    groups: {key: [group ids]} (e.g. ["seed:movie:603", "collection:2344"]).
    """
    head, tail, counts = [], [], {}
    for key in keys:
        g = groups.get(key) or []
        if len(head) < top and all(counts.get(x, 0) < max_per_group for x in g):
            head.append(key)
            for x in g:
                counts[x] = counts.get(x, 0) + 1
        else:
            tail.append(key)
    return head + tail


def diversity_groups(c: dict, seeds: dict) -> list[str]:
    g = []
    s = best_seed(c, seeds)
    if s is not None:
        g.append("seed:%s:%s" % tuple(s["key"]))
    if c.get("collection"):
        g.append("collection:%s" % c["collection"])
    for k in c.get("keywords") or []:
        if FRANCHISE_KEYWORD.search(k.lower()):
            g.append("franchise:" + k.lower())
    return g


# ---------------------------------------------------------------------------- prompts
SYSTEM_PROMPT = (
    "You are a film and TV recommender for one person. You judge how well each title fits "
    "THEIR taste, using only the profile given. Be honest: a famous title that does not fit "
    "their taste gets a low score. Answer only with the requested JSON."
)


def profile_text(profile: dict, seeds: dict, n_terms: int = 12, n_titles: int = 12) -> str:
    """The taste profile as compact text for the model (no ids, no personal data)."""
    def titles(kinds, n):
        rows = [s for s in seeds.values() if s["kind"] in kinds]
        rows.sort(key=lambda s: (-abs(s["weight"]), s["title"]))
        return ", ".join(s["title"] for s in rows[:n]) or "(none)"
    disliked_genres = [k for k, v in sorted(profile.get("genres", {}).items(), key=lambda kv: kv[1]) if v < 0][:5]
    lines = [
        "Favourite genres: " + (", ".join(top_terms(profile.get("genres", {}), 8)) or "(unknown)"),
        "Favourite themes: " + (", ".join(top_terms(profile.get("keywords", {}), n_terms)) or "(unknown)"),
        "Favourites: " + titles(("favourite",), n_titles),
        "Watched and liked: " + titles(("completed", "rated"), n_titles),
        "Recently requested (they asked for these, so they are interested): " + titles(("request",), n_titles),
        "On their watchlist: " + titles(("watchlist",), n_titles),
        "Started: " + titles(("partial",), 6),
        "Disliked or abandoned: " + titles(("disliked", "abandoned"), 8),
    ]
    if disliked_genres:
        lines.append("Genres they avoid: " + ", ".join(disliked_genres))
    return "\n".join(lines)


def candidate_line(c: dict) -> str:
    kind = "film" if c["key"][0] == "movie" else "TV series"
    bits = [f'id {c["key"][1]}: {c["title"]}' + (f' ({c["year"]})' if c.get("year") else ""), kind]
    if c.get("genres"):
        bits.append("genres: " + ", ".join(c["genres"][:4]))
    if c.get("keywords"):
        bits.append("themes: " + ", ".join(c["keywords"][:6]))
    over = clean_line(c.get("overview"), 220)
    if over:
        bits.append("about: " + over)
    return " | ".join(bits)


def scoring_messages(profile_txt: str, batch: list[dict]) -> list[dict]:
    user = (
        "Taste profile:\n" + profile_txt + "\n\nRate EVERY title below for this person from 0 (not "
        "for them) to 10 (a perfect fit). Use the whole scale and compare the titles with each other: "
        "give 9 or 10 to at most two titles, most titles belong between 3 and 8, and titles that only "
        "share a broad genre get 5 or less. For each, write one short sentence (at most 12 words) "
        "saying why it suits them, naming a title from their history when you can (for example: "
        "'A time-loop thriller, like Edge of Tomorrow'). Use each id exactly once.\n\nTitles:\n"
        + "\n".join(candidate_line(c) for c in batch)
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def proposal_messages(profile_txt: str, n: int, avoid: list[str]) -> list[dict]:
    user = (
        "Taste profile:\n" + profile_txt + f"\n\nSuggest up to {n} films or TV series this person "
        "has probably not seen and would love. Real, released titles only, with the year of first "
        "release. Do not suggest any of these: " + ("; ".join(avoid[:60]) or "(none)") + "."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
