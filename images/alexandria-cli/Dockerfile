# syntax=docker/dockerfile:1
# Alexandria CLI (`alexandria`) for the library-init initContainer of
# k8s/apps/alexandria. The upstream image ships only `alexandria-mcp`, and the
# server refuses to start without an initialized library. Upstream's own
# release binary is used as is, pinned by version + sha256 (no rebuild of the
# Rust workspace). CI passes both from images/alexandria/upstream.env and checks
# that the release tag is the same commit as UPSTREAM_REF.
#
#   docker buildx build --platform linux/amd64 \
#     --build-arg ALEXANDRIA_CLI_VERSION=v0.1.2 --build-arg ALEXANDRIA_CLI_SHA256=<sha256> \
#     -t alexandria-cli:dev images/alexandria-cli

# trixie (glibc 2.41): the release binary is built on a current Ubuntu runner and
# needs GLIBC_2.39 + libssl 3, so bookworm (glibc 2.36) can't run it.
ARG DEBIAN_IMAGE=debian:trixie-slim

# ---------------------------------------------------------------- fetch + verify
FROM ${DEBIAN_IMAGE} AS fetch
ARG ALEXANDRIA_CLI_VERSION
ARG ALEXANDRIA_CLI_SHA256
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp/dl
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN test -n "${ALEXANDRIA_CLI_VERSION}" && test -n "${ALEXANDRIA_CLI_SHA256}" \
       || { echo "build args ALEXANDRIA_CLI_VERSION and ALEXANDRIA_CLI_SHA256 are required" >&2; exit 1; } \
    && name="alexandria-${ALEXANDRIA_CLI_VERSION}-linux-x86_64" \
    && curl -fsSL -o cli.tgz "https://github.com/DevArtech/alexandria/releases/download/${ALEXANDRIA_CLI_VERSION}/${name}.tar.gz" \
    && echo "${ALEXANDRIA_CLI_SHA256}  cli.tgz" | sha256sum -c - \
    && tar -xzf cli.tgz "${name}/alexandria" "${name}/LICENSE" \
    && install -m 0755 "${name}/alexandria" /tmp/alexandria \
    && install -m 0644 "${name}/LICENSE" /tmp/LICENSE

# ---------------------------------------------------------------- runtime
FROM ${DEBIAN_IMAGE}
# The binary's NEEDED libraries (readelf -d): libstdc++6, libssl3t64 (+ libc,
# libgcc_s from the base); same user as upstream's alexandria-mcp image.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libssl3t64 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --user-group --home-dir /srv/alexandria \
        --shell /usr/sbin/nologin alexandria
COPY --from=fetch /tmp/alexandria /usr/local/bin/alexandria
COPY --from=fetch /tmp/LICENSE /usr/share/doc/alexandria/LICENSE
RUN alexandria --help >/dev/null
USER 10001:10001
ENTRYPOINT ["alexandria"]
