#!/usr/bin/env bash
# Install every CLI this repo needs into $ARTZIMA_TOOLS (no sudo, no accounts).
# Re-runnable; skips what is already there. Linux amd64.
set -euo pipefail
T="${ARTZIMA_TOOLS:-$HOME/.local/share/artzima-tools}"
mkdir -p "$T/bin" "$T/dl"
repo="$(cd "$(dirname "$0")/.." && pwd)"

# Pinned tool versions (looked up 2026-11-22). Renovate bumps them via the
# "# renovate:" annotations (renovate.json). GITLEAKS_SHA256 must be updated with GITLEAKS
# (the PR fails `make deps` until it is: on purpose, a human checks the new checksum).
# renovate: datasource=github-releases depName=opentofu/opentofu extractVersion=^v(?<version>.+)$
TOFU=1.13.9
# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize extractVersion=^kustomize/v(?<version>.+)$
KUSTOMIZE=5.8.3
# renovate: datasource=github-releases depName=yannh/kubeconform extractVersion=^v(?<version>.+)$
KUBECONFORM=0.8.0
# renovate: datasource=github-releases depName=fluxcd/flux2 extractVersion=^v(?<version>.+)$
FLUX=2.10.1
# renovate: datasource=github-releases depName=helm/helm extractVersion=^v(?<version>.+)$
HELM=4.3.2
# renovate: datasource=github-releases depName=siderolabs/talos extractVersion=^v(?<version>.+)$
TALOS=1.14.6
# renovate: datasource=github-releases depName=hadolint/hadolint extractVersion=^v(?<version>.+)$
HADOLINT=2.15.1
# renovate: datasource=github-releases depName=rhysd/actionlint extractVersion=^v(?<version>.+)$
ACTIONLINT=1.7.12
# renovate: datasource=github-releases depName=koalaman/shellcheck extractVersion=^v(?<version>.+)$
SHELLCHECK=0.11.0
# renovate: datasource=github-releases depName=gitleaks/gitleaks extractVersion=^v(?<version>.+)$
GITLEAKS=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
# renovate: datasource=pypi depName=infisicalsdk
INFISICALSDK=1.4.1
# renovate: datasource=pypi depName=argon2-cffi
ARGON2_CFFI=25.1.0 # make gen-secrets: Argon2id PHC hashes (Vaultwarden ADMIN_TOKEN)
# renovate: datasource=pypi depName=bcrypt
BCRYPT=5.0.0       # make gen-secrets: bcrypt hashes (Backrest UI login)
# renovate: datasource=github-releases depName=kubernetes/kubernetes
KUBECTL=v1.37.2
# renovate: datasource=pypi depName=uv
UV=0.13.9
# renovate: datasource=pypi depName=ansible-core
ANSIBLE_CORE=2.22.0
# renovate: datasource=pypi depName=ansible-lint
ANSIBLE_LINT=26.11.0
# renovate: datasource=pypi depName=yamllint
YAMLLINT=1.38.0
# renovate: datasource=pypi depName=pyyaml
PYYAML=6.0.4
# renovate: datasource=pypi depName=netaddr
NETADDR=1.3.0
# renovate: datasource=pypi depName=jmespath
JMESPATH=1.1.0
# renovate: datasource=pypi depName=reportlab
REPORTLAB=5.1.1        # make dr-pdf: the PDF layout engine
# renovate: datasource=pypi depName=markdown-it-py
MARKDOWN_IT_PY=4.3.0   # make dr-pdf: parses docs/disaster-recovery.md
# renovate: datasource=pypi depName=pypdfium2
PYPDFIUM2=5.15.2       # make dr-pdf DR_PDF_ARGS="--png DIR": page previews to check the layout
# Fonts for make dr-pdf: IBM Plex Sans + Mono (SIL Open Font License), fetched once, never at
# render time. A version bump needs its new SHA256 (on purpose: a human checks it).
PLEX_SANS=1.3.6
PLEX_SANS_SHA256=a52bd405930040a6bfdb5c8aff67f1b7205538999986e2186d8f8895ca6a5de1
PLEX_MONO=2.5.0
PLEX_MONO_SHA256=6d23f01257663d8cc49a0d64c22ced630b79e0e2a0ac08a0da86e9a38bbc481c

fetch() { # url dir
  mkdir -p "$T/dl/$2"
  (cd "$T/dl/$2" && curl -fsSLO "$1")
}
have() { [ -x "$T/bin/$1" ]; }

have uv || { fetch https://github.com/astral-sh/uv/releases/download/$UV/uv-x86_64-unknown-linux-gnu.tar.gz uv
  tar -xzf "$T/dl/uv/uv-x86_64-unknown-linux-gnu.tar.gz" -C "$T/dl/uv"
  cp "$T/dl/uv/uv-x86_64-unknown-linux-gnu/uv" "$T/bin/"; }
have tofu || { fetch "https://github.com/opentofu/opentofu/releases/download/v$TOFU/tofu_${TOFU}_linux_amd64.tar.gz" tofu
  tar -xzf "$T/dl/tofu/tofu_${TOFU}_linux_amd64.tar.gz" -C "$T/bin" tofu; }
have kustomize || { fetch "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv$KUSTOMIZE/kustomize_v${KUSTOMIZE}_linux_amd64.tar.gz" kustomize
  tar -xzf "$T/dl/kustomize/kustomize_v${KUSTOMIZE}_linux_amd64.tar.gz" -C "$T/bin"; }
have kubeconform || { fetch "https://github.com/yannh/kubeconform/releases/download/v$KUBECONFORM/kubeconform-linux-amd64.tar.gz" kubeconform
  tar -xzf "$T/dl/kubeconform/kubeconform-linux-amd64.tar.gz" -C "$T/bin" kubeconform; }
have flux || { fetch "https://github.com/fluxcd/flux2/releases/download/v$FLUX/flux_${FLUX}_linux_amd64.tar.gz" flux
  tar -xzf "$T/dl/flux/flux_${FLUX}_linux_amd64.tar.gz" -C "$T/bin" flux; }
have helm || { fetch "https://get.helm.sh/helm-v$HELM-linux-amd64.tar.gz" helm
  tar -xzf "$T/dl/helm/helm-v$HELM-linux-amd64.tar.gz" -C "$T/dl/helm"
  cp "$T/dl/helm/linux-amd64/helm" "$T/bin/"; }
have talosctl || { fetch "https://github.com/siderolabs/talos/releases/download/v$TALOS/talosctl-linux-amd64" talosctl
  install -m 0755 "$T/dl/talosctl/talosctl-linux-amd64" "$T/bin/talosctl"; }
have kubectl || { fetch "https://dl.k8s.io/release/$KUBECTL/bin/linux/amd64/kubectl" kubectl
  install -m 0755 "$T/dl/kubectl/kubectl" "$T/bin/kubectl"; }

have hadolint || { fetch "https://github.com/hadolint/hadolint/releases/download/v$HADOLINT/hadolint-linux-x86_64" hadolint
  install -m 0755 "$T/dl/hadolint/hadolint-linux-x86_64" "$T/bin/hadolint"; }
have actionlint || { fetch "https://github.com/rhysd/actionlint/releases/download/v$ACTIONLINT/actionlint_${ACTIONLINT}_linux_amd64.tar.gz" actionlint
  tar -xzf "$T/dl/actionlint/actionlint_${ACTIONLINT}_linux_amd64.tar.gz" -C "$T/bin" actionlint; }
have shellcheck || { fetch "https://github.com/koalaman/shellcheck/releases/download/v$SHELLCHECK/shellcheck-v$SHELLCHECK.linux.x86_64.tar.xz" shellcheck
  tar -xJf "$T/dl/shellcheck/shellcheck-v$SHELLCHECK.linux.x86_64.tar.xz" -C "$T/dl/shellcheck"
  install -m 0755 "$T/dl/shellcheck/shellcheck-v$SHELLCHECK/shellcheck" "$T/bin/shellcheck"; }

have gitleaks || { fetch "https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS/gitleaks_${GITLEAKS}_linux_x64.tar.gz" gitleaks
  echo "$GITLEAKS_SHA256  $T/dl/gitleaks/gitleaks_${GITLEAKS}_linux_x64.tar.gz" | sha256sum -c - >/dev/null
  tar -xzf "$T/dl/gitleaks/gitleaks_${GITLEAKS}_linux_x64.tar.gz" -C "$T/bin" gitleaks; }

# Python tools in a venv (uv provides Python even without python3-venv).
export UV_CACHE_DIR="$T/uv-cache" UV_PYTHON_INSTALL_DIR="$T/python"
[ -x "$T/venv/bin/python" ] || "$T/bin/uv" venv -q "$T/venv" --python 3.13
VIRTUAL_ENV="$T/venv" "$T/bin/uv" pip install -q \
  "ansible-core==$ANSIBLE_CORE" "ansible-lint==$ANSIBLE_LINT" "yamllint==$YAMLLINT" "pyyaml==$PYYAML" \
  "netaddr==$NETADDR" "jmespath==$JMESPATH" "infisicalsdk==$INFISICALSDK" "argon2-cffi==$ARGON2_CFFI" "bcrypt==$BCRYPT" \
  "reportlab==$REPORTLAB" "markdown-it-py==$MARKDOWN_IT_PY" "pypdfium2==$PYPDFIUM2"

# Fonts for make dr-pdf (static TTFs, checksum-pinned).
plex() { # family version sha256
  local zip="$T/dl/plex-$1/ibm-plex-$1.zip"
  [ -f "$T/fonts/IBMPlex${1^}-Regular.ttf" ] && [ -f "$T/fonts/.plex-$1-$2" ] && return 0
  rm -rf "$T/dl/plex-$1"
  fetch "https://github.com/IBM/plex/releases/download/%40ibm/plex-$1%40$2/ibm-plex-$1.zip" "plex-$1"
  echo "$3  $zip" | sha256sum -c - >/dev/null
  mkdir -p "$T/fonts"
  "$T/venv/bin/python" - "$zip" "$T/fonts" "$1" <<'EOF'
import pathlib, sys, zipfile
zf, dest, fam = zipfile.ZipFile(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
for name in zf.namelist():
    if name.startswith(f"ibm-plex-{fam}/fonts/complete/ttf/") and name.endswith(".ttf"):
        (dest / pathlib.PurePosixPath(name).name).write_bytes(zf.read(name))
(dest / f"LICENSE-plex-{fam}.txt").write_bytes(zf.read(f"ibm-plex-{fam}/LICENSE.txt"))
EOF
  touch "$T/fonts/.plex-$1-$2"
}
plex sans "$PLEX_SANS" "$PLEX_SANS_SHA256"
plex mono "$PLEX_MONO" "$PLEX_MONO_SHA256"

# Ansible collections (pinned in ansible/requirements.yml).
"$T/venv/bin/ansible-galaxy" collection install -p "$T/collections" \
  -r "$repo/ansible/requirements.yml" >/dev/null

echo "tools ready in $T"
ls -1 "$T/bin" | sed "s/^/  /"
