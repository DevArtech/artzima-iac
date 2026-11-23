# images/

Build definitions for the operator's own app images. Run the workflow template
`private-builder/images.yml` in a separate **private builder repository**, not this public repository. The app repos
are never modified: Alexandria builds from its own Dockerfiles, and Skyjo is cloned at a pinned commit.

| Image | Source | Pin file |
|---|---|---|
| `ghcr.io/devartech/alexandria-mcp` | `github.com/DevArtech/alexandria` → `Dockerfile` | `alexandria/upstream.env` |
| `ghcr.io/devartech/alexandria-oauth-proxy` | same repo → `proxy/Dockerfile` | `alexandria/upstream.env` |
| `ghcr.io/devartech/alexandria-cli` | `alexandria-cli/Dockerfile`: upstream's **release binary** `alexandria` (for `alexandria init`) | `alexandria/upstream.env` (`UPSTREAM_CLI_VERSION`, `UPSTREAM_CLI_SHA256`) |
| `ghcr.io/devartech/skyjo` | `skyjo/Dockerfile`, which clones `github.com/DevArtech/skynet` **with its Git LFS weights** | `skyjo/upstream.env` |

Tags are the resolved upstream **commit SHA** (40 hex characters) plus `latest`, on `linux/amd64`.

## alexandria/

There is no Dockerfile here on purpose. CI uses the upstream repository at the pinned SHA as a
**git build context**, which is the same as:

```bash
ref=$(sed -n 's/^UPSTREAM_REF=//p' images/alexandria/upstream.env)
docker buildx build --platform linux/amd64 -t alexandria-mcp:dev \
  "https://github.com/DevArtech/alexandria.git#$ref"
docker buildx build --platform linux/amd64 -t alexandria-oauth-proxy:dev \
  "https://github.com/DevArtech/alexandria.git#$ref:proxy"
```

- `alexandria-mcp`: Rust, Debian slim runtime, **uid 10001**. Its CMD is
  `--transport http --bind 0.0.0.0:8080 --library /srv/alexandria`, and it needs `ALEXANDRIA_MCP_TOKEN`.
  It ships the in-process fastembed embedder, which downloads its ONNX model at first start, so the
  HelmRelease sets `FASTEMBED_CACHE_DIR` to a PVC.
- `alexandria-oauth-proxy`: Node 22, user `node` (**uid 1000**), `PORT` 8081,
  `/health`, and `DATA_DIR` `/data`.

## alexandria-cli/

The server image has no CLI, and the server refuses to start without an initialized library. So
this image carries upstream's release binary `alexandria`, which the Alexandria pod's
`init-library` initContainer uses for `alexandria init` on an empty PVC.

- **Download and verify:** it downloads `alexandria-<UPSTREAM_CLI_VERSION>-linux-x86_64.tar.gz` from
  the GitHub release and checks it against `UPSTREAM_CLI_SHA256`, the same digest GitHub shows
  for the asset.
- **Pin check:** CI fails unless that release tag is the same commit as `UPSTREAM_REF`.
- **Base and user:** runtime is `debian:trixie-slim`, because the binary needs glibc ≥ 2.39 and
  libssl 3. It runs as **uid 10001**, like the server.

```bash
v=$(sed -n 's/^UPSTREAM_CLI_VERSION=//p' images/alexandria/upstream.env)
s=$(sed -n 's/^UPSTREAM_CLI_SHA256=//p' images/alexandria/upstream.env)
docker buildx build --platform linux/amd64 --build-arg ALEXANDRIA_CLI_VERSION="$v" \
  --build-arg ALEXANDRIA_CLI_SHA256="$s" -t alexandria-cli:dev images/alexandria-cli
```

When bumping Alexandria, set `UPSTREAM_CLI_VERSION`/`_SHA256` to the release of the new ref.

## skyjo/

`Dockerfile` (multi-stage):

1. **source** stage: `git fetch --depth 1` of skynet at `SKYNET_REF` (required build arg; CI passes
   the SHA), then `git lfs pull` of exactly the two checkpoints
   (`runs/{muzero_baseline,muzero_belief}/checkpoints/checkpoint.pt`, ~0.4 GB from LFS; see
   the bandwidth note in `docs/decisions.md`). The build **fails** unless each is a real file:
   not an LFS pointer, at least 1 MiB, and with the sha256 of the pointer's oid.
2. **frontend** stage: `npm ci && npm run build` of `web/frontend` with `VITE_API_BASE=""`.
   `App.tsx` uses `VITE_API_BASE ?? 'http://localhost:8000'`, and the empty string is not nullish,
   so the browser calls same-origin `/api/...`. The build fails if `localhost:8000` is still in `dist/`.
3. **runtime** stage: `python:3.12-slim-bookworm`, **CPU-only** torch from
   `download.pytorch.org/whl/cpu`, plus `requirements.txt` (fastapi, uvicorn, numpy). It copies the upstream
   root modules (`skyjo_env`, `skyjo_decision_env`, `muzero_*`, `belief_muzero_*`,
   `heuristic_bots`, …) and `web/backend` to `/app`, and the built frontend to `/app/static`.
   It copies `runs/` (the weights, read-only), adds `checkpoint_iter_1.pt → checkpoint.pt` in each
   checkpoint folder (upstream's default lookup globs `checkpoint_iter_*.pt`), and runs
   `check_weights.py`: upstream's own lookup must find a real checkpoint in both folders.
   It runs as **uid 10001** on port **8080**.

`artzima_serve.py` is our wrapper. It imports the upstream `web.backend.main:app` and
mounts `/app/static` at `/` **after** the upstream routes, so `/api/*` (including
`/api/health`) still wins. The result is one image and one origin, and upstream is untouched. The backend resolves
`runs/` relative to the repo root (`/app`), where the image has the weights, so there's no volume.

Local build:

```bash
ref=$(sed -n 's/^UPSTREAM_REF=//p' images/skyjo/upstream.env)
docker buildx build --platform linux/amd64 --build-arg SKYNET_REF="$ref" -t skyjo:dev images/skyjo
docker run --rm -p 8080:8080 skyjo:dev   # http://localhost:8080 (weights included)
```

## Bumping

1. Set `UPSTREAM_REF` in the pin file (a SHA is best; a tag or branch also works, and CI resolves it).
2. Push. The workflow builds and tags `<sha>` + `latest`. Or use *Actions → images → Run
   workflow* with a ref, to try one out without changing the pin.
3. Set the same SHA as `image.tag` in `k8s/apps/<app>/helmrelease.yaml`, then commit.

The weekly scheduled run rebuilds the **same** pin, to pick up base-image security fixes. It
overwrites the `<sha>` tag with a fresh digest, and running pods keep their cached image
(`IfNotPresent`) until they are rescheduled. Pin by digest (`tag: <sha>@sha256:…`) if you want
the cluster to change only when you change git.

Lint: `make lint-images` (hadolint, actionlint, shellcheck).

## Private builder requirement

The public IaC repository must not publish application images. Copy this image tree,
`scripts/verify_private_package.py` and `private-builder/images.yml` into a private
builder repository (the workflow goes under `.github/workflows/images.yml`).

The workflow first verifies that its own repository is private. It publishes only empty
images while registering packages and checks their visibility. Application builds depend
on that step, and each build verifies its target package is already private before it
resolves or builds application source. Any failed or missing privacy check stops publication.

A public package cannot be made private again; removing it and recreating it is required.
Do not use a post-publication manual visibility change as a safety measure. See
[GitHub package visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

The prepared builder is `.private/image-builder/`. The planned GitHub repository is
`DevArtech/artzima-images`, with **Private** visibility and no initial files. It has not
been created or uploaded yet. Once created, verify its privacy before the first push.
