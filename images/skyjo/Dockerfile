# syntax=docker/dockerfile:1
# Skyjo web app (DevArtech/skynet, web/): FastAPI backend + React/Vite frontend
# in ONE image on ONE origin (port 8080). Upstream is cloned at a pinned ref;
# nothing in it is modified. The MuZero weights come from upstream's Git LFS
# (runs/{muzero_baseline,muzero_belief}/checkpoints/checkpoint.pt) and are baked
# into the image: the app is stateless (no runs/ volume). Build (context = this directory):
#
#   docker buildx build --platform linux/amd64 \
#     --build-arg SKYNET_REF=<commit sha> -t skyjo:dev images/skyjo
#
# CI passes SKYNET_REF from images/skyjo/upstream.env.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

# ---------------------------------------------------------------- source
FROM ${PYTHON_IMAGE} AS source
ARG SKYNET_REPO=https://github.com/DevArtech/skynet.git
ARG SKYNET_REF
# The two checkpoints the image ships (upstream's own paths, Git LFS objects).
ARG SKYNET_CHECKPOINTS="runs/muzero_baseline/checkpoints/checkpoint.pt runs/muzero_belief/checkpoints/checkpoint.pt"
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git git-lfs ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# Fetch exactly one commit (LFS smudge off), then download ONLY the two
# checkpoints from LFS (~0.4 GB; counts against the repo owner's LFS bandwidth,
# docs/decisions.md). Then FAIL the build unless each one is a real file: not a
# ~134-byte LFS pointer, at least 1 MiB, and its sha256 equals the pointer's oid.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN test -n "${SKYNET_REF}" || { echo "build arg SKYNET_REF is required" >&2; exit 1; } \
    && git init -q . \
    && git remote add origin "${SKYNET_REPO}" \
    && git fetch -q --depth 1 origin "${SKYNET_REF}" \
    && GIT_LFS_SKIP_SMUDGE=1 git checkout -q FETCH_HEAD \
    && git rev-parse HEAD > /src/.upstream-revision \
    && git lfs install --local \
    && git lfs pull --include "$(tr ' ' ',' <<<"${SKYNET_CHECKPOINTS}")" \
    && for f in ${SKYNET_CHECKPOINTS}; do \
         oid="$(git show "HEAD:$f" | sed -n 's/^oid sha256://p')"; \
         size="$(stat -c %s "$f")"; \
         if [ -z "$oid" ]; then echo "$f: not an LFS pointer in git" >&2; exit 1; fi; \
         if head -c 64 "$f" | grep -q '^version https://git-lfs'; then echo "$f: still an LFS pointer ($size bytes)" >&2; exit 1; fi; \
         if [ "$size" -lt 1048576 ]; then echo "$f: only $size bytes, not a real checkpoint" >&2; exit 1; fi; \
         echo "$oid  $f" | sha256sum -c - || exit 1; \
         echo "checkpoint ok: $f ($size bytes, sha256 $oid)"; \
       done \
    && rm -rf .git

# ---------------------------------------------------------------- frontend
FROM ${NODE_IMAGE} AS frontend
WORKDIR /frontend
COPY --from=source /src/web/frontend/package.json /src/web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=source /src/web/frontend/ ./
# Empty API base: App.tsx uses `VITE_API_BASE ?? 'http://localhost:8000'`, and
# "" is not nullish, so the browser calls same-origin /api/... .
RUN printf 'VITE_API_BASE=\n' > .env.production.local \
    && npm run build \
    && test -f dist/index.html \
    && if grep -rq 'localhost:8000' dist; then echo "dist still points at localhost:8000" >&2; exit 1; fi

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
ARG TORCH_VERSION=2.15.0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SKYJO_STATIC_DIR=/app/static
# CPU-only torch wheel (no CUDA libraries: ~1/10 of the default wheel).
COPY requirements.txt /tmp/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

RUN useradd --system --uid 10001 --user-group --home-dir /app --shell /usr/sbin/nologin skyjo
WORKDIR /app
# The backend imports root modules (skyjo_env, skyjo_decision_env, muzero_*,
# heuristic_bots, ...) and resolves runs/ relative to the repo root (= /app).
COPY --from=source /src/*.py /src/.upstream-revision ./
COPY --from=source /src/web/__init__.py ./web/__init__.py
COPY --from=source /src/web/backend ./web/backend
COPY --from=frontend /frontend/dist ./static
COPY artzima_serve.py ./
# The weights (read-only, owned by root). Upstream's default lookup
# (web/backend/checkpoints.py) globs checkpoint_iter_*.pt, which doesn't match
# checkpoint.pt, so each folder gets checkpoint_iter_1.pt -> checkpoint.pt; the
# build then asserts that upstream's own lookup finds it.
COPY --from=source /src/runs ./runs
COPY check_weights.py /tmp/check_weights.py
RUN rm -rf web/backend/tests \
    && for d in runs/muzero_baseline/checkpoints runs/muzero_belief/checkpoints; do \
         test -s "$d/checkpoint.pt" || exit 1; \
         ln -s checkpoint.pt "$d/checkpoint_iter_1.pt" || exit 1; \
       done \
    && python /tmp/check_weights.py \
    && rm /tmp/check_weights.py

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "artzima_serve:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
