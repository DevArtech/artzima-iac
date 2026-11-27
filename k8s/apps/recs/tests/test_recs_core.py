#!/usr/bin/env python3
"""Offline tests of k8s/apps/recs/app/recs_core.py (stdlib unittest; `make lint` runs them)."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import recs_core as core  # noqa: E402

NOW = dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)
CFG = dict(core.DEFAULTS)


def ago(days: float) -> str:
    return (NOW - dt.timedelta(days=days)).isoformat()


def lib(key, title, **kw):
    base = {"key": key, "title": title, "favourite": False, "played": False, "playCount": 0,
            "lastPlayed": ago(1), "playedPct": None, "rating": None, "likes": None}
    base.update(kw)
    return base


def cand(key, title="t", seeds=None, score=0.0, genres=(), keywords=(), va=7.0, vc=1000, collection=None):
    c = core.new_candidate(key, {"title": title, "voteAverage": va, "voteCount": vc, "popularity": 20.0})
    c.update(seedScore=score, seeds=dict(seeds or {}), genres=list(genres), keywords=list(keywords),
             collection=collection)
    return c


class Seeds(unittest.TestCase):
    def test_order_of_weights(self):
        """favourite > completed > own request > watchlist > partial (all equally recent)."""
        seeds = core.build_seeds(
            [lib(("movie", 1), "Fav", favourite=True, played=True, playCount=1),
             lib(("movie", 2), "Done", played=True, playCount=1),
             lib(("movie", 5), "Half", playedPct=55.0)],
            [{"key": ("movie", 3), "createdAt": ago(1)}],
            [{"key": ("tv", 4), "title": "Later", "createdAt": ago(1)}], NOW, CFG)
        w = {k[1]: s["weight"] for k, s in seeds.items()}
        self.assertGreater(w[1], w[2])
        self.assertGreater(w[2], w[3])
        self.assertGreater(w[3], w[4])
        self.assertGreater(w[4], w[5])
        self.assertGreater(w[5], 0)
        self.assertEqual(seeds[("movie", 3)]["kind"], "request")

    def test_recent_requests_weigh_more(self):
        seeds = core.build_seeds([], [{"key": ("movie", 1), "createdAt": ago(2)},
                                      {"key": ("movie", 2), "createdAt": ago(200)}], [], NOW, CFG)
        self.assertGreater(seeds[("movie", 1)]["weight"], seeds[("movie", 2)]["weight"])
        self.assertGreaterEqual(seeds[("movie", 2)]["weight"], CFG["W_REQUEST"] * CFG["RECENCY_FLOOR"] - 1e-9)

    def test_request_below_completed_watch_above_nothing(self):
        seeds = core.build_seeds([lib(("movie", 1), "W", played=True, lastPlayed=ago(1))],
                                 [{"key": ("movie", 2), "createdAt": ago(1)}], [], NOW, CFG)
        self.assertLess(seeds[("movie", 2)]["weight"], seeds[("movie", 1)]["weight"])
        self.assertGreater(seeds[("movie", 2)]["weight"], 0)

    def test_playcount_bonus_is_capped(self):
        one = core.build_seeds([lib(("movie", 1), "a", played=True, playCount=1)], [], [], NOW, CFG)
        many = core.build_seeds([lib(("movie", 1), "a", played=True, playCount=50)], [], [], NOW, CFG)
        ratio = many[("movie", 1)]["weight"] / one[("movie", 1)]["weight"]
        self.assertAlmostEqual(ratio, 1 + CFG["PLAYCOUNT_MAX_BONUS"], places=3)

    def test_negative_signals(self):
        seeds = core.build_seeds(
            [lib(("movie", 1), "Hated", played=True, likes=False),
             lib(("movie", 2), "Low", played=True, rating=2.0),
             lib(("movie", 3), "Dropped", playedPct=8.0, lastPlayed=ago(60)),
             lib(("movie", 4), "Just started", playedPct=8.0, lastPlayed=ago(2))],
            [{"key": ("movie", 1), "createdAt": ago(1)}], [], NOW, CFG)
        self.assertEqual(seeds[("movie", 1)]["kind"], "disliked")  # a later request does not undo it
        self.assertLess(seeds[("movie", 1)]["weight"], 0)
        self.assertEqual(seeds[("movie", 2)]["kind"], "disliked")
        self.assertEqual(seeds[("movie", 3)]["kind"], "abandoned")
        self.assertLess(seeds[("movie", 3)]["weight"], 0)
        self.assertEqual(seeds[("movie", 4)]["kind"], "partial")

    def test_series_completion(self):
        seeds = core.build_seeds(
            [lib(("tv", 1), "Binged", episodesPlayed=9, episodesTotal=10),
             lib(("tv", 2), "Sampled", episodesPlayed=1, episodesTotal=30)], [], [], NOW, CFG)
        self.assertEqual(seeds[("tv", 1)]["kind"], "completed")
        self.assertEqual(seeds[("tv", 2)]["kind"], "partial")

    def test_jellyfin_timestamps(self):
        t = core.parse_time("2026-11-25T00:29:13.4877688Z")
        self.assertEqual((t.year, t.month, t.day, t.tzinfo is not None), (2026, 11, 25, True))
        self.assertIsNone(core.parse_time("garbage"))


class Scoring(unittest.TestCase):
    def test_profile_and_affinity(self):
        seeds = core.build_seeds([lib(("movie", 1), "A", played=True), lib(("movie", 2), "B", likes=False)],
                                 [], [], NOW, CFG)
        details = {("movie", 1): {"genres": ["Science Fiction"], "keywords": ["time loop"]},
                   ("movie", 2): {"genres": ["Horror"], "keywords": ["gore"]}}
        prof = core.build_profile(seeds, details)
        self.assertEqual(prof["genres"]["Science Fiction"], 1.0)
        self.assertLess(prof["genres"]["Horror"], 0)
        hi = core.affinity(prof, ["Science Fiction"], ["time loop"], 0.5)
        lo = core.affinity(prof, ["Horror"], ["gore"], 0.5)
        self.assertGreater(hi, 0.9)
        self.assertEqual(lo, 0.0)

    def test_candidates_rank_and_frequency(self):
        pool = {}
        s1 = {"key": ("movie", 1), "title": "One", "kind": "completed", "weight": 0.8}
        s2 = {"key": ("movie", 2), "title": "Two", "kind": "request", "weight": 0.6}
        res = [{"id": 10, "mediaType": "movie", "title": "Ten"}, {"id": 11, "mediaType": "movie", "title": "Eleven"},
               {"id": 12, "mediaType": "person"}]
        core.add_candidates(pool, s1, res, 1.0)
        core.add_candidates(pool, s2, res[:1], 1.0)
        self.assertNotIn(("person", 12), pool)
        self.assertGreater(pool[("movie", 10)]["seedScore"], pool[("movie", 11)]["seedScore"])
        self.assertEqual(len(pool[("movie", 10)]["seeds"]), 2)
        comps = core.classic_scores(pool, {"genres": {}, "keywords": {}}, CFG)
        self.assertEqual(comps[("movie", 10)]["classic"], 1.0)  # normalised per user
        self.assertEqual(comps[("movie", 11)]["classic"], 0.0)
        self.assertEqual(comps[("movie", 10)]["frequency"], 1.0)

    def test_ai_only_candidates_get_affinity_only(self):
        prof = {"genres": {"Drama": 1.0}, "keywords": {}}
        pool = {("movie", 1): cand(("movie", 1), seeds={("movie", 9): 0.5}, score=0.5, genres=["Comedy"]),
                ("movie", 2): cand(("movie", 2), genres=["Drama"])}
        comps = core.classic_scores(pool, prof, CFG)
        self.assertIsNone(comps[("movie", 2)]["prior"])
        self.assertEqual(comps[("movie", 2)]["raw"], comps[("movie", 2)]["affinity"])
        self.assertEqual(comps[("movie", 2)]["seed"], 0.0)

    def test_prior_is_bayesian(self):
        self.assertLess(core.prior(10.0, 3, CFG), core.prior(8.0, 5000, CFG))

    def test_exclusion(self):
        pool = {("movie", 1): cand(("movie", 1)), ("movie", 2): cand(("movie", 2)), ("tv", 1): cand(("tv", 1))}
        left = core.exclude(pool, {("movie", 1)})
        self.assertEqual(set(left), {("movie", 2), ("tv", 1)})  # same id, other media type, stays

    def test_reasons(self):
        seeds = {("movie", 1): {"key": ("movie", 1), "title": "Edge of Tomorrow", "kind": "completed", "weight": 0.8},
                 ("tv", 2): {"key": ("tv", 2), "title": "Severance", "kind": "request", "weight": 0.6}}
        c1 = cand(("movie", 5), seeds={("movie", 1): 0.8})
        c2 = cand(("movie", 6), seeds={("tv", 2): 0.6, ("movie", 1): 0.1})
        c3 = cand(("movie", 7), genres=["Drama"])
        self.assertEqual(core.classic_reason(c1, seeds, {}), "Because you watched Edge of Tomorrow")
        self.assertEqual(core.classic_reason(c2, seeds, {}), "Because you requested Severance")
        self.assertEqual(core.classic_reason(c3, seeds, {"genres": {"Drama": 0.7}}), "Matches your taste in Drama")


class LlmAnswers(unittest.TestCase):
    def test_valid(self):
        got = core.parse_ratings(json.dumps({"ratings": [{"id": 1, "score": 8, "reason": "Fits\nwell"},
                                                         {"id": 2, "score": 3.5, "reason": ""}]}), [1, 2])
        self.assertEqual(got, {1: (8.0, "Fits well"), 2: (3.5, "")})

    def test_invalid_rows_dropped(self):
        rows = [{"id": 99, "score": 5, "reason": "not in batch"}, {"id": 1, "score": 11, "reason": "range"},
                {"id": 2, "score": "7", "reason": "string"}, {"id": 3, "score": True, "reason": "bool"},
                {"id": 4, "score": 6, "reason": "ok"}, {"id": 4, "score": 1, "reason": "repeat"}]
        self.assertEqual(core.parse_ratings(json.dumps({"ratings": rows}), [1, 2, 3, 4]), {4: (6.0, "ok")})

    def test_garbage(self):
        self.assertEqual(core.parse_ratings("not json", [1]), {})
        self.assertEqual(core.parse_ratings(json.dumps([1, 2]), [1]), {})

    def test_schema_constrains_ids(self):
        s = core.rating_schema([5, 6])
        item = s["properties"]["ratings"]["items"]
        self.assertEqual(item["properties"]["id"]["enum"], [5, 6])
        self.assertEqual(s["properties"]["ratings"]["minItems"], 2)

    def test_proposals_resolution(self):
        props = core.parse_proposals(json.dumps({"titles": [
            {"title": "Arrival", "year": 2016, "mediaType": "movie"}, {"title": "x", "year": "2016", "mediaType": "movie"},
            {"title": "Dark", "year": 2017, "mediaType": "tv"}]}), 10)
        self.assertEqual([p["title"] for p in props], ["Arrival", "Dark"])
        results = [{"mediaType": "movie", "id": 10547, "title": "The Arrival", "releaseDate": "1996-05-31"},
                   {"mediaType": "movie", "id": 329865, "title": "Arrival", "releaseDate": "2016-11-10"}]
        self.assertEqual(core.resolve_proposal(props[0], results)["id"], 329865)
        self.assertIsNone(core.resolve_proposal({"title": "Nope", "year": 2016, "mediaType": "movie"}, results))
        self.assertIsNone(core.resolve_proposal({"title": "Arrival", "year": 2016, "mediaType": "tv"}, results))

    def test_normalise_ai(self):
        self.assertEqual(core.normalise_ai({"a": 2, "b": 8, "c": 5}), {"a": 0.0, "b": 1.0, "c": 0.5})
        self.assertEqual(core.normalise_ai({"a": 7, "b": 7}), {"a": 0.7, "b": 0.7})  # flat answer


class Fusion(unittest.TestCase):
    def test_weighted_sum(self):
        f = core.fuse({"a": 1.0, "b": 0.0}, {"a": 0.0, "b": 1.0}, {**CFG, "W_CLASSIC": 0.7, "W_AI": 0.3})
        self.assertAlmostEqual(f["a"]["final"], 0.7)
        self.assertAlmostEqual(f["b"]["final"], 0.3)

    def test_rrf_breaks_ties(self):
        classic = {"a": 0.6, "b": 0.4, "c": 0.0}
        ai = {"a": 0.4, "b": 0.6, "c": 1.0}  # a and b tie on final (0.5); b ranks better overall
        f = core.fuse(classic, ai, CFG)
        self.assertEqual(round(f["a"]["final"], 3), round(f["b"]["final"], 3))
        ranks = core.order({"a": f["a"], "b": f["b"]})
        self.assertEqual(ranks, ["a", "b"])  # equal rank sums: a (1+2) vs b (2+3)? check rrf
        self.assertGreater(f["a"]["rrf"], f["b"]["rrf"])

    def test_fallback_classic_only(self):
        """LLM down (ai None): final == classic, order == classic order."""
        classic = {"a": 0.2, "b": 0.9, "c": 0.5}
        f = core.fuse(classic, None, CFG)
        self.assertEqual({k: v["final"] for k, v in f.items()}, classic)
        self.assertEqual(core.order(f), ["b", "c", "a"])
        self.assertTrue(all(v["aiRank"] is None for v in f.values()))

    def test_partial_ai_falls_back_per_item(self):
        f = core.fuse({"a": 0.8, "b": 0.6}, {"a": 0.0}, CFG)
        self.assertAlmostEqual(f["a"]["final"], 0.4)
        self.assertAlmostEqual(f["b"]["final"], 0.6)  # unrated: its classic score

    def test_diversity(self):
        keys = [f"k{i}" for i in range(8)]
        groups = {k: ["seed:x"] for k in keys[:5]}
        groups["k5"] = ["collection:1"]
        out = core.diversify(keys, groups, 3, 6)
        self.assertEqual(out[:6], ["k0", "k1", "k2", "k5", "k6", "k7"])
        self.assertEqual(out[6:], ["k3", "k4"])
        self.assertEqual(sorted(out), sorted(keys))

    def test_diversity_groups(self):
        seeds = {("movie", 1): {"key": ("movie", 1), "title": "S", "kind": "completed", "weight": 0.8}}
        c = cand(("movie", 2), seeds={("movie", 1): 0.5}, collection=99)
        self.assertEqual(core.diversity_groups(c, seeds), ["seed:movie:1", "collection:99"])


class Balance(unittest.TestCase):
    def test_tv_genres_and_noise_keywords(self):
        self.assertEqual(core.norm_genres(["Sci-Fi & Fantasy", "Drama", "Science Fiction"]),
                         ["Science Fiction", "Fantasy", "Drama"])
        self.assertEqual(core.norm_keywords(["aftercreditsstinger", "time loop", "Sequel"]), ["time loop"])

    def test_seed_strength_is_scaled_per_media_type(self):
        pool = {("movie", 1): cand(("movie", 1), seeds={("movie", 9): 3.0}, score=3.0),
                ("tv", 2): cand(("tv", 2), seeds={("tv", 8): 0.4}, score=0.4)}
        comps = core.classic_scores(pool, {"genres": {}, "keywords": {}}, CFG)
        self.assertEqual(comps[("movie", 1)]["seed"], 1.0)
        self.assertEqual(comps[("tv", 2)]["seed"], 1.0)

    def test_cap_pool_keeps_room_for_series(self):
        seeds = {("movie", i): {"key": ("movie", i), "weight": 1.0} for i in range(9)}
        seeds[("tv", 1)] = {"key": ("tv", 1), "weight": 0.5}
        pool = {("movie", i): cand(("movie", i), seeds={("movie", 0): 1}, score=5.0) for i in range(100, 200)}
        pool.update({("tv", i): cand(("tv", i), seeds={("tv", 1): 1}, score=0.1) for i in range(100, 200)})
        out = core.cap_pool(pool, seeds, 40, CFG)
        self.assertEqual(len(out), 40)
        self.assertEqual(sum(1 for k in out if k[0] == "tv"), 10)  # 25% floor

    def test_cap_pool_fills_from_the_other_type(self):
        seeds = {("movie", 1): {"key": ("movie", 1), "weight": 1.0}, ("tv", 1): {"key": ("tv", 1), "weight": 1.0}}
        pool = {("movie", i): cand(("movie", i), seeds={("movie", 1): 1}, score=1.0) for i in range(50)}
        pool[("tv", 7)] = cand(("tv", 7), seeds={("tv", 1): 1}, score=1.0)
        self.assertEqual(len(core.cap_pool(pool, seeds, 20, CFG)), 20)

    def test_shared_universe_is_one_franchise(self):
        c = cand(("movie", 2), keywords=["superhero", "marvel cinematic universe (mcu)"])
        self.assertEqual(core.diversity_groups(c, {}), ["franchise:marvel cinematic universe (mcu)"])


class EndToEnd(unittest.TestCase):
    def test_pipeline_without_network(self):
        seeds = core.build_seeds([lib(("movie", 1), "Edge of Tomorrow", played=True, playCount=4)],
                                 [{"key": ("tv", 7), "title": "Severance", "createdAt": ago(1)}], [], NOW, CFG)
        details = {("movie", 1): {"genres": ["Action", "Science Fiction"], "keywords": ["time loop"]},
                   ("tv", 7): {"genres": ["Drama", "Mystery"], "keywords": ["office"]}}
        prof = core.build_profile(seeds, details)
        pool = {}
        for s in core.positive_seeds(seeds, 10):
            core.add_candidates(pool, s, [{"id": 100 + s["key"][1], "mediaType": "movie", "title": "x"},
                                          {"id": 1, "mediaType": "movie", "title": "seed itself"}], 1.0)
        pool = core.exclude(pool, set(seeds) | {("movie", 200)})
        self.assertNotIn(("movie", 1), pool)
        comps = core.classic_scores(pool, prof, CFG)
        fused = core.fuse({k: v["classic"] for k, v in comps.items()}, None, CFG)
        self.assertEqual(len(core.order(fused)), 2)
        txt = core.profile_text(prof, seeds)
        self.assertIn("Recently requested", txt)
        self.assertIn("Severance", txt)


# ---------------------------------------------------------------------------- MF (optional)
def tiny_model(f=3):
    """A hand-made RMF2 model: 4 films x 3 factors, a 1-feature-per-genre content map."""
    return core.mf_parse(tiny_blob(f))


def tiny_blob(f=3):
    """The RMF2 bytes behind tiny_model."""
    import array
    import struct
    Y = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.5, 0.5, 0.0]
    G = [0.0] * (f * f)
    for r in range(4):
        for i in range(f):
            for j in range(f):
                G[i * f + j] += Y[r * f + i] * Y[r * f + j]
    vocab = ["g:Action", "g:Drama", "n:bias"]
    W = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    maps = {t: {"p": 3, "vocab": vocab, "idf": {}, "gw": {"g": 1.0, "y": 1.0, "n": 1.0, "l": 1.0, "k": 1.0, "c": 1.0,
                                                           "d": 1.0, "pc": 1.0, "col": 1.0, "ov": 1.0}} for t in ("full", "lite")}
    blocks = [["tmdb", "q", 4], ["imdb", "q", 4], ["Y", "f", 12], ["G", "f", 9]]
    payload = array.array("q", [10, 11, 12, 13]).tobytes() + array.array("q", [100, 0, 0, 0]).tobytes() \
        + array.array("f", Y).tobytes() + array.array("f", G).tobytes()
    for t in ("full", "lite"):
        blocks += [[t + ".mu", "f", 3], [t + ".W", "f", 9]]
        payload += array.array("f", [0.0, 0.0, 0.1]).tobytes() + array.array("f", W).tobytes()
    head = json.dumps({"n": 4, "f": f, "reg": 1.0, "alpha": 10.0, "maps": maps, "blocks": blocks}).encode()
    return b"RMF2" + struct.pack("<I", len(head)) + head + payload


class Mf(unittest.TestCase):
    def test_parse_and_lookup(self):
        m = tiny_model()
        self.assertEqual(m["row"][12], 2)
        self.assertEqual(core.mf_row(m, ("movie", 99), "tt100"), 0)  # IMDb fallback
        self.assertIsNone(core.mf_row(m, ("tv", 10)))
        with self.assertRaises(ValueError):
            core.mf_parse(b"XXXX")

    def test_fold_in_closed_form(self):
        """u = (G + (c-1) y y^T + reg I)^-1 c y for one seed with weight 1 (alpha 10): y = e1."""
        m = tiny_model()
        u = core.mf_user_vector(m, [([1.0, 0.0, 0.0], 1.0)])
        # G = diag(1.25, 1.25, 1) + offdiag 0.25 (rows 0,1); A00 = 1.25 + 10 + 1
        A = [[12.25, 0.25, 0.0], [0.25, 2.25, 0.0], [0.0, 0.0, 2.0]]
        b = [11.0, 0.0, 0.0]
        x = core._cholesky_solve(A, b)
        for a, e in zip(u, x):
            self.assertAlmostEqual(a, e, places=5)
        self.assertGreater(u[0], 0.8)
        self.assertIsNone(core.mf_user_vector(m, []))

    def test_predicted_and_real_scores(self):
        m = tiny_model()
        sc = core.mf_scorer(m, [1.0, 0.0, 0.0])
        s, src = core.mf_score(m, sc, ("movie", 10), None, lite=False)
        self.assertEqual((round(s, 5), src), (1.0, "real"))
        raw = core.mf_raw_features({"genres": [{"id": 28, "name": "Action"}], "voteCount": 0}, "movie")
        s, src = core.mf_score(m, sc, ("movie", 999), raw, lite=False)
        self.assertEqual(src, "pred")
        self.assertGreater(s, 0.5)
        vec, src = core.mf_item_vector(m, ("movie", 999), raw)
        self.assertEqual(src, "pred")
        self.assertAlmostEqual(sum(a * b for a, b in zip(vec, [1.0, 0.0, 0.0])), s, places=5)
        self.assertEqual(core.mf_score(m, sc, ("tv", 5), None, lite=True), (None, None))

    def test_percentiles_per_media_and_source(self):
        raw = {("movie", 1): (3.0, "real"), ("movie", 2): (1.0, "real"), ("movie", 3): (9.0, "pred"),
               ("tv", 1): (0.1, "pred"), ("tv", 2): (0.2, "pred")}
        keys = list(raw) + [("tv", 3)]
        p = core.mf_percentiles(raw, keys)
        self.assertEqual(p[("movie", 1)], 1.0)
        self.assertEqual(p[("movie", 2)], 0.0)
        self.assertEqual(p[("movie", 3)], 0.0)  # alone in its group: never ahead of the real ones by default
        self.assertEqual((p[("tv", 1)], p[("tv", 2)]), (0.0, 1.0))
        self.assertEqual(p[("tv", 3)], 0.5)  # no score: neutral

    def test_fuse_unchanged_without_mf(self):
        classic = {("movie", 1): 0.9, ("movie", 2): 0.4, ("tv", 3): 0.6}
        ai = {("movie", 1): 0.2, ("tv", 3): 0.8}
        base = core.fuse(classic, ai, CFG)
        self.assertEqual(core.fuse(classic, ai, CFG, None), base)
        self.assertEqual(core.fuse(classic, ai, CFG, {k: 1.0 for k in classic}), base)  # W_MF = 0

    def test_fuse_with_mf(self):
        cfg = dict(CFG, W_CLASSIC=0.4, W_AI=0.4, W_MF=0.2)
        classic = {("movie", 1): 1.0, ("movie", 2): 0.0}
        out = core.fuse(classic, {("movie", 1): 0.0}, cfg, {("movie", 1): 1.0, ("movie", 2): 0.5})
        self.assertAlmostEqual(out[("movie", 1)]["final"], (0.4 + 0.2) / 1.0)
        self.assertAlmostEqual(out[("movie", 2)]["final"], 0.1 / 0.6)  # AI missing: dropped, MF kept
        self.assertEqual(out[("movie", 1)]["mfRank"], 1)

    def test_cap_unchanged_without_cap_mf(self):
        pool = {("movie", i): cand(("movie", i), score=i / 10) for i in range(10)}
        seeds = {("movie", 99): {"key": ("movie", 99), "weight": 1.0}}
        base = core.cap_pool(pool, seeds, 3, CFG)
        self.assertEqual(core.cap_pool(pool, seeds, 3, CFG, {k: 1.0 for k in pool}), base)
        mf = {k: (1.0 if k == ("movie", 0) else 0.0) for k in pool}
        got = core.cap_pool(pool, seeds, 3, dict(CFG, CAP_MF=1.0), mf)
        self.assertIn(("movie", 0), got)  # weakest seed score, best MF: now rated by the AI

    def test_page_offset(self):
        pool = {}
        s = {"key": ("movie", 1), "weight": 1.0}
        core.add_candidates(pool, s, [{"id": 5, "mediaType": "movie"}], 1.0, offset=20)
        self.assertAlmostEqual(pool[("movie", 5)]["seedScore"], 1.0 / 3.0)


class MfFile(unittest.TestCase):
    """recs_server.MfModel: the factors file is optional; missing or broken = MF off, never fatal."""

    def setUp(self):
        import contextlib
        import io
        import tempfile
        import recs_server  # stdlib only; no environment needed at import
        self.MfModel = recs_server.MfModel
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = pathlib.Path(self.dir.name) / "models" / "recs-mf-v2.rmf"
        self.out = io.StringIO()  # MfModel logs to stdout
        quiet = contextlib.redirect_stdout(self.out)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_missing_file_is_off(self):
        self.assertIsNone(self.MfModel(self.path).get())

    def test_broken_file_is_off_and_logged(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"RMF2\xff\xff")
        self.assertIsNone(self.MfModel(self.path).get())
        self.assertIn("MF off", self.out.getvalue())

    def test_load_then_file_removed(self):
        self.path.parent.mkdir()
        self.path.write_bytes(tiny_blob())
        mm = self.MfModel(self.path)
        self.assertEqual(mm.get()["n"], 4)
        self.assertIs(mm.get(), mm.get())  # cached until the mtime changes
        self.path.unlink()
        self.assertIsNone(mm.get())

    def test_pipeline_without_mf_is_todays(self):
        """No factors (mf=None, as the server passes when the file is missing): with the deployed
        weights 0.4/0.4/0.2 the fusion is the plain classic/AI mean, the cap today's."""
        cfg = dict(CFG, W_CLASSIC=0.4, W_AI=0.4, W_MF=0.2, CAP_MF=1.0)
        classic = {("movie", 1): 0.9, ("movie", 2): 0.4, ("tv", 3): 0.6}
        ai = {("movie", 1): 0.2, ("tv", 3): 0.8}
        out = core.fuse(classic, ai, cfg, None)
        self.assertAlmostEqual(out[("movie", 1)]["final"], 0.55)
        self.assertAlmostEqual(out[("movie", 2)]["final"], 0.4)  # AI missing: classic alone
        self.assertNotIn("mfRank", out[("movie", 1)])
        pool = {("movie", i): cand(("movie", i), score=i / 10) for i in range(10)}
        seeds = {("movie", 99): {"key": ("movie", 99), "weight": 1.0}}
        self.assertEqual(core.cap_pool(pool, seeds, 3, cfg, None), core.cap_pool(pool, seeds, 3, CFG))


if __name__ == "__main__":
    unittest.main(verbosity=1)
