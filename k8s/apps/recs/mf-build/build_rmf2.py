#!/usr/bin/env python3
"""One-off build of the recs MF file (RMF2): MovieLens 32M -> implicit ALS item factors, TMDb
metadata (through Seerr, like recs) of the ~2500 most-rated MovieLens films -> ridge map from
content to factors, written atomically to OUT. Runs in the recs-mf-build Job (job.yaml next to it).

Needs numpy, scipy, pandas, implicit, threadpoolctl (pinned in the Job). Content features come
from recs_core.mf_raw_features (the ConfigMap's recs_core.py), so training and serving can't drift.

Env: SEERR_URL, SEERR_API_KEY (the recs secret), OUT (default /cache/models/recs-mf-v2.rmf),
WORK (default /work), THREADS (default 2), RECS_APP (default /app).
Options for an offline rerun: --zip PATH (skip the download), --details DIR_OR_JSONL (skip Seerr).
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import os
import re
import struct
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, os.environ.get("PYDEPS", "/work/py"))  # pip --target dir of the Job (python -I ignores PYTHONPATH)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import scipy.sparse as sp  # noqa: E402

ML_URL = "https://files.grouplens.org/datasets/movielens/ml-32m.zip"
ML_MD5 = "d472be332d4daa821edc399621853b57"
F, REG, ALPHA, ITERS = 128, 1.0, 10.0, 15     # chosen on a MovieLens holdout (MovieLens holdout HR@20)
LAM, SCALE = 3.0, 1.21                         # ridge lambda and residual scale (held-out 2019-23 films)
N_TOP, N_RECENT = 2000, 500                    # films whose TMDb metadata trains the content map
GROUP_W = {"g": 1.0, "y": 0.6, "n": 0.6, "l": 0.4, "k": 1.0, "c": 0.8, "d": 0.8, "pc": 0.5, "col": 0.8, "ov": 0.8}
LICENCE = ("MovieLens usage licence: research, non-commercial, no endorsement; transformations only under "
           "the same licence. Do not commit or publish.")

sys.path.insert(0, os.environ.get("RECS_APP", "/app"))
import recs_core as core  # noqa: E402


def log(msg, **kv):
    print(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), msg,
          " ".join(f"{k}={v}" for k, v in kv.items()), flush=True)


def arg(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def download(work):
    path = os.path.join(work, "ml-32m.zip")
    if not os.path.exists(path):
        log("downloading", url=ML_URL)
        urllib.request.urlretrieve(ML_URL, path + ".part")
        os.replace(path + ".part", path)
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != ML_MD5:
        raise SystemExit(f"md5 mismatch for {path}")
    return path


def load_ml(zpath):
    with zipfile.ZipFile(zpath) as z:
        r = pd.read_csv(z.open("ml-32m/ratings.csv"), usecols=[0, 1, 2],
                        dtype={"userId": np.int32, "movieId": np.int32, "rating": np.float32})
        links = pd.read_csv(z.open("ml-32m/links.csv"))
    r = r[r.rating >= 3.5]
    cnt = r.movieId.value_counts()
    r = r[r.movieId.isin(cnt.index[cnt >= 20])]
    uids, ui = np.unique(r.userId.values, return_inverse=True)
    item_movie, mi = np.unique(r.movieId.values, return_inverse=True)
    X = sp.csr_matrix((np.ones(len(ui), np.float32), (ui, mi)), shape=(len(uids), len(item_movie)))
    tm = dict(zip(links.movieId, links.tmdbId.fillna(-1).astype(np.int64)))
    im = dict(zip(links.movieId, links.imdbId.astype(np.int64)))
    return X, item_movie, np.array([tm.get(m, -1) for m in item_movie]), np.array([im.get(m, 0) for m in item_movie])


def train(X, threads):
    from implicit.cpu.als import AlternatingLeastSquares
    from threadpoolctl import threadpool_limits
    with threadpool_limits(1, "blas"):
        m = AlternatingLeastSquares(factors=F, regularization=REG, alpha=ALPHA, iterations=ITERS,
                                    num_threads=threads, random_state=0)
        m.fit(X, show_progress=False)
    return np.asarray(m.item_factors, np.float32)


def seerr_details(ids, threads):
    base = os.environ.get("SEERR_URL", "http://seerr.media.svc.cluster.local:5055").rstrip("/")
    key = os.environ["SEERR_API_KEY"]

    def one(tid):
        for attempt in range(3):
            try:
                req = urllib.request.Request(f"{base}/api/v1/movie/{tid}", headers={"X-Api-Key": key, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as res:
                    return tid, json.loads(res.read())
            except Exception:  # noqa: BLE001 (a few failures only shrink the training set)
                time.sleep(2 * (attempt + 1))
        return tid, None
    out = {}
    with cf.ThreadPoolExecutor(threads) as ex:
        for tid, d in ex.map(one, ids):
            if d:
                out[tid] = d
    return out


def local_details(src):
    out = {}
    if os.path.isdir(src):  # a recs cache directory (/cache/tmdb): {"at", "data"}
        for f in os.listdir(src):
            m = re.fullmatch(r"movie-(\d+)\.json", f)
            if m:
                out[int(m.group(1))] = json.load(open(os.path.join(src, f)))["data"]
    else:  # JSON lines {"p": "/api/v1/movie/<id>", "d": {...}}
        for line in open(src):
            r = json.loads(line)
            m = re.fullmatch(r"/api/v1/movie/(\d+)", r.get("p", ""))
            if m and r.get("d"):
                out[int(m.group(1))] = r["d"]
    return out


def fit_map(dets, T, lite):
    raws = [core.mf_raw_features(d, "movie", lite) for d in dets]
    df = {}
    for raw in raws:
        for fs in raw.values():
            for n in fs:
                df[n] = df.get(n, 0) + 1
    vocab, idf, N = {}, {}, len(raws)
    for n, c in sorted(df.items()):
        need = 5 if n.startswith("ov:") else (1 if n.startswith(("g:", "y:", "n:", "media:")) else 2)
        if c >= need:
            vocab[n] = len(vocab)
            if n.startswith(("ov:", "k:", "p:", "pc:", "nw:", "l:")):
                idf[n] = round(math.log((1 + N) / (1 + c)) + 1.0, 6)
    mp = {"vocab": vocab, "idf": idf, "gw": GROUP_W}
    rows, cols, vals = [], [], []
    for i, raw in enumerate(raws):
        for j, x in core._mf_vectorise(mp, raw):
            rows.append(i); cols.append(j); vals.append(x)
    Xf = sp.csr_matrix((vals, (rows, cols)), shape=(N, len(vocab)))
    mu = T.mean(0)
    K = (Xf @ Xf.T).toarray()
    A = np.linalg.solve(K + LAM * np.eye(N), T - mu)
    W = SCALE * np.asarray(Xf.T @ A)
    return {"p": len(vocab), "vocab": sorted(vocab, key=vocab.get), "idf": idf, "gw": GROUP_W}, mu, W


def export(out, Y, tmdb, imdb, pop, maps, n_films):
    order = np.argsort(-pop, kind="stable")
    seen, rows = set(), []
    for i in order:
        if tmdb[i] > 0 and int(tmdb[i]) not in seen:
            seen.add(int(tmdb[i])); rows.append(int(i))
    rows.sort()
    G = (Y.T.astype(np.float64) @ Y.astype(np.float64)).astype("<f4")
    blocks, payload = [], []

    def add(name, arr, code):
        arr = np.ascontiguousarray(np.asarray(arr).astype("<i8" if code == "q" else "<f4"))
        blocks.append([name, code, int(arr.size)]); payload.append(arr.tobytes())
    add("tmdb", tmdb[rows], "q"); add("imdb", imdb[rows], "q"); add("Y", Y[rows], "f"); add("G", G, "f")
    head_maps = {}
    for tag, (meta, mu, W) in maps.items():
        head_maps[tag] = meta
        add(tag + ".mu", mu, "f"); add(tag + ".W", W, "f")
    head = {"n": len(rows), "f": F, "reg": REG, "alpha": ALPHA,
            "source": f"MovieLens 32M (GroupLens), implicit ALS {F} f, ratings >= 3.5; content map: ridge on TMDb metadata of {n_films} MovieLens films",
            "licence": LICENCE, "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "maps": head_maps, "blocks": blocks}
    hb = json.dumps(head, separators=(",", ":")).encode()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "wb") as fh:
        fh.write(b"RMF2" + struct.pack("<I", len(hb)) + hb)
        for b in payload:
            fh.write(b)
    core.mf_parse(open(out + ".tmp", "rb").read())  # the stdlib reader must accept it
    os.replace(out + ".tmp", out)
    return os.path.getsize(out)


def main():
    t0 = time.time()
    work, out = os.environ.get("WORK", "/work"), os.environ.get("OUT", "/cache/models/recs-mf-v2.rmf")
    threads = int(os.environ.get("THREADS", "2"))
    os.makedirs(work, exist_ok=True)
    zpath = arg("--zip") or download(work)
    X, item_movie, tmdb, imdb = load_ml(zpath)
    pop = np.asarray(X.sum(axis=0)).ravel()
    log("positives", users=X.shape[0], films=X.shape[1], nnz=X.nnz, s=round(time.time() - t0))
    Y = train(X, threads)
    del X
    log("trained", s=round(time.time() - t0))
    # training films for the content map: most-rated + most-rated recent (titles hold the year)
    with zipfile.ZipFile(zpath) as z:
        mv = pd.read_csv(z.open("ml-32m/movies.csv"))
    year = {m: int(y) for m, y in zip(mv.movieId, mv.title.str.extract(r"\((\d{4})\)\s*$")[0].fillna("0"))}
    order = [i for i in np.argsort(-pop) if tmdb[i] > 0]
    top = order[:N_TOP]
    top_set = set(top)
    recent = [i for i in order if year.get(int(item_movie[i]), 0) >= 2016 and i not in top_set][:N_RECENT]
    ids = sorted({int(tmdb[i]) for i in top + recent})
    det = {}
    for src in (arg("--details") or "").split(","):
        if src:
            det.update(local_details(src))
    if os.path.isdir("/cache/tmdb") and not arg("--details"):
        det.update(local_details("/cache/tmdb"))  # what recs already cached
    missing = [t for t in ids if t not in det]
    if missing and not os.environ.get("SEERR_API_KEY"):
        log("no SEERR_API_KEY: training on the details at hand", missing=len(missing))
    elif missing:
        log("fetching TMDb details through Seerr", calls=len(missing))
        det.update(seerr_details(missing, 4))
    col = {}
    for i in np.argsort(-pop):
        if tmdb[i] > 0:
            col.setdefault(int(tmdb[i]), int(i))
    films = [(col[t], det[t]) for t in sorted(det) if t in col and str(det[t].get("releaseDate") or "")[:4].isdigit()]
    log("content map", films=len(films), s=round(time.time() - t0))
    T = Y[[c for c, _ in films]].astype(np.float64)
    maps = {tag: fit_map([d for _, d in films], T, lite) for tag, lite in (("full", False), ("lite", True))}
    size = export(out, Y, tmdb, imdb, pop, maps, len(films))
    log("done", out=out, bytes=size, s=round(time.time() - t0))


if __name__ == "__main__":
    main()
