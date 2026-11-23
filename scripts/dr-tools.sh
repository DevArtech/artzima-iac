#!/usr/bin/env bash
# DISASTER RECOVERY tool installer: a blank Linux or macOS laptop (amd64/arm64) -> every tool
# docs/disaster-recovery.md needs. No sudo, no package manager, no account.
#
#   scripts/dr-tools.sh            install (re-runnable; skips what is already there)
#   scripts/dr-tools.sh --check    print the installed versions
#   eval "$(scripts/dr-tools.sh --env)"   put the tools on PATH in this shell
#
# Installs into $ARTZIMA_TOOLS (default ~/.local/share/artzima-tools), the SAME layout as
# `make deps`, so every make target works afterwards (also on macOS):
#   bin/      restic, tofu, talosctl, kubectl, flux, infisical, uv     (sha256-pinned below)
#   venv/     Python 3.13 + ansible-core, infisicalsdk, ...           (pins: scripts/install-tools.sh)
#   collections/  the Ansible collections of ansible/requirements.yml
# Needs: bash, curl, tar, bzip2, git, make, ssh (macOS: `xcode-select --install`; Debian/Ubuntu:
# `sudo apt install curl git make bzip2 openssh-client`). Every download is checked against the
# SHA256 written here; a mismatch stops the script.
set -euo pipefail

T="${ARTZIMA_TOOLS:-$HOME/.local/share/artzima-tools}"
repo="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--env" ]; then
  echo "export ARTZIMA_TOOLS='$T' PATH='$T/bin:$T/venv/bin:'\"\$PATH\" ANSIBLE_COLLECTIONS_PATH='$T/collections'"
  exit 0
fi

# ---- pinned versions (keep TOFU/TALOS/FLUX/KUBECTL/UV equal to scripts/install-tools.sh; `make lint` checks)
RESTIC=0.20.0
TOFU=1.13.9
TALOS=1.14.6
FLUX=2.10.1
KUBECTL=v1.37.2
INFISICAL_CLI=0.45.14
UV=0.13.9
# Python packages: the same pins as `make deps` (single source: scripts/install-tools.sh).
eval "$(grep -E '^(INFISICALSDK|ARGON2_CFFI|BCRYPT|ANSIBLE_CORE|ANSIBLE_LINT|YAMLLINT|PYYAML|NETADDR|JMESPATH)=' \
  "$repo/scripts/install-tools.sh" | sed 's/ *#.*//')"

# ---- sha256 per <tool>_<os>_<arch> (looked up 2026-11-23 from the projects' release checksum files)
sum_for() {
  case "$1" in
    restic_linux_amd64) echo 0ccd9fa7885074a24603b051e01bf9bd8b5e45d6a1791e9d9887e9e9372107f7 ;;
    restic_linux_arm64) echo 1188daded239bccb5952a01d2ad33e77a0d3dc34f9d9fc1e7fdda618d44938ef ;;
    restic_darwin_amd64) echo 21f0070ef2bc6f3da2e33c070628e3e026e5237ba5fcddb1f9e0e8cabdd809d5 ;;
    restic_darwin_arm64) echo 0962c515acd9fb6937a658e9d8505200f72a6a0c6a2d334c0290b8002377dfb7 ;;
    tofu_linux_amd64) echo b34b327cc1ef07178207b6f2cd1f5276537d3e5b7be316500f6dac50d4c0a857 ;;
    tofu_linux_arm64) echo 5dc196dde75b667e4806d358310ec96742774cd0993c54c1237efee5644b69b8 ;;
    tofu_darwin_amd64) echo 347e2e5a2cfe2f368829572d308b2e7c1e0486b20a1779090ff06360f7551e4f ;;
    tofu_darwin_arm64) echo 496f4c3064b5c3bff79174b3dae4faaf65a66774eb8e2bf074f895412c5f124f ;;
    talosctl_linux_amd64) echo 11acde7f7578ffe9cbe6fb8349b34595065f597de0842615982e350b365de987 ;;
    talosctl_linux_arm64) echo fd22e0678d783105646d824c23ee45cf7a57f737edae274ca41376d03a4def65 ;;
    talosctl_darwin_amd64) echo cb460e388de00628d7d42beb6ca037603025195c05115de9ec25442a870aa1ed ;;
    talosctl_darwin_arm64) echo dd290798702d60ce8c1f59f4c223d5bdbc9c0638db45a9827d7f6cb4bd4a3f39 ;;
    flux_linux_amd64) echo 967365ff84aabc97871827ff33713ef0c09f62dd83c18759e3a5e39adcea9285 ;;
    flux_linux_arm64) echo da18ddb25c9614405d9e2f13ad014fd660bfcbe7218674cabf9325db73f811e2 ;;
    flux_darwin_amd64) echo 0e083b1a53887ffee1624c67de74eea3ed9d7fd0215efb4b4090c8cc97abb824 ;;
    flux_darwin_arm64) echo c0a8dc285c568905ae38e25074985f3181c82a3c8d66316959561f27a626909d ;;
    kubectl_linux_amd64) echo c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4 ;;
    kubectl_linux_arm64) echo fcc16f63186659d9722014b7e02d5fdd2d4e669cb8c0ab66365d7d3b8ba03509 ;;
    kubectl_darwin_amd64) echo 88379aff5c8111de6be0a394adffba2819e44cde269f3ab23cef76de1a48eaa8 ;;
    kubectl_darwin_arm64) echo 55701af0d51815ee335f240e48acf0959c3c23017ed3fa816e880c4a92bae581 ;;
    infisical_linux_amd64) echo aee706dfee561432cf85ed4d33a2ab8d3576cec55a34e0ae92c2350f35929b1e ;;
    infisical_linux_arm64) echo 7e21226fed397501a2f0734450c9caf3087d99b10e9035a8a1cc71fe2141131a ;;
    infisical_darwin_amd64) echo 3587350a5f4c0eb1a88fa5f2e45a2caa28226d602afd845cae7fbdb4b89a042a ;;
    infisical_darwin_arm64) echo 67a7f6147f40c2482ce8ca3e3344a087c69eb7d4642699b6eb9bb22beee6db3e ;;
    uv_linux_amd64) echo a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5 ;;
    uv_linux_arm64) echo 0797542018e541a9288855df555c8077eed06f7a47d206362030480e8ecc9f3d ;;
    uv_darwin_amd64) echo 617a3211261b0fc739562f0071aa34e9739797c510208ede8e2977c30903d041 ;;
    uv_darwin_arm64) echo 5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca ;;
    *) echo "no checksum for $1" >&2; return 1 ;;
  esac
}

if [ "${1:-}" = "--check" ]; then
  export PATH="$T/bin:$T/venv/bin:$PATH"
  for c in "restic version" "tofu version" "kubectl version --client" \
           "flux version --client" "infisical --version" "uv --version" "ansible --version"; do
    printf '%-10s ' "${c%% *}"; $c 2>/dev/null | head -1 || echo "MISSING"
  done
  printf '%-10s ' talosctl; talosctl version --client 2>/dev/null | grep -m1 Tag | tr -s ' ' || echo "MISSING"
  exit 0
fi

# ---- platform
case "$(uname -s)" in Linux) OS=linux ;; Darwin) OS=darwin ;; *) echo "unsupported OS $(uname -s)" >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64|amd64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;; *) echo "unsupported CPU $(uname -m)" >&2; exit 1 ;; esac
for c in curl tar bzip2 git make ssh; do
  command -v "$c" >/dev/null || { echo "missing '$c': macOS: xcode-select --install; Debian/Ubuntu: sudo apt install curl git make bzip2 openssh-client" >&2; exit 1; }
done
if command -v sha256sum >/dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi

mkdir -p "$T/bin" "$T/dl"
have() { [ -x "$T/bin/$1" ]; }
# fetch <tool> <url>: download into $T/dl and verify against sum_for <tool>_<os>_<arch>
fetch() {
  local want f
  want="$(sum_for "$1_${OS}_${ARCH}")"
  f="$T/dl/$(basename "$2")"
  echo ">> $1: $2"
  curl -fsSL --retry 3 -o "$f" "$2"
  echo "$want  $f" | $SHA -c - >/dev/null || { echo "CHECKSUM MISMATCH for $f: not installing" >&2; rm -f "$f"; exit 1; }
  echo "$f"
}

have restic || { f="$(fetch restic "https://github.com/restic/restic/releases/download/v$RESTIC/restic_${RESTIC}_${OS}_${ARCH}.bz2" | tail -1)"
  bzip2 -dc "$f" > "$T/bin/restic" && chmod 0755 "$T/bin/restic"; }
have tofu || { f="$(fetch tofu "https://github.com/opentofu/opentofu/releases/download/v$TOFU/tofu_${TOFU}_${OS}_${ARCH}.tar.gz" | tail -1)"
  tar -xzf "$f" -C "$T/bin" tofu; }
have talosctl || { f="$(fetch talosctl "https://github.com/siderolabs/talos/releases/download/v$TALOS/talosctl-${OS}-${ARCH}" | tail -1)"
  install -m 0755 "$f" "$T/bin/talosctl"; }
have kubectl || { f="$(fetch kubectl "https://dl.k8s.io/release/$KUBECTL/bin/$OS/$ARCH/kubectl" | tail -1)"
  install -m 0755 "$f" "$T/bin/kubectl"; }
have flux || { f="$(fetch flux "https://github.com/fluxcd/flux2/releases/download/v$FLUX/flux_${FLUX}_${OS}_${ARCH}.tar.gz" | tail -1)"
  tar -xzf "$f" -C "$T/bin" flux; }
have infisical || { f="$(fetch infisical "https://github.com/Infisical/cli/releases/download/v$INFISICAL_CLI/cli_${INFISICAL_CLI}_${OS}_${ARCH}.tar.gz" | tail -1)"
  tar -xzf "$f" -C "$T/bin" infisical; }
if ! have uv; then
  case "$OS/$ARCH" in
    linux/amd64) t=x86_64-unknown-linux-gnu ;; linux/arm64) t=aarch64-unknown-linux-gnu ;;
    darwin/amd64) t=x86_64-apple-darwin ;; darwin/arm64) t=aarch64-apple-darwin ;;
  esac
  f="$(fetch uv "https://github.com/astral-sh/uv/releases/download/$UV/uv-$t.tar.gz" | tail -1)"
  tar -xzf "$f" -C "$T/dl"
  install -m 0755 "$T/dl/uv-$t/uv" "$T/bin/uv"
fi

# ---- Python (uv brings its own interpreter) + Ansible + the Infisical SDK
export UV_CACHE_DIR="$T/uv-cache" UV_PYTHON_INSTALL_DIR="$T/python"
[ -x "$T/venv/bin/python" ] || "$T/bin/uv" venv -q "$T/venv" --python 3.13
echo ">> python packages"
VIRTUAL_ENV="$T/venv" "$T/bin/uv" pip install -q \
  "ansible-core==$ANSIBLE_CORE" "ansible-lint==$ANSIBLE_LINT" "yamllint==$YAMLLINT" "pyyaml==$PYYAML" \
  "netaddr==$NETADDR" "jmespath==$JMESPATH" "infisicalsdk==$INFISICALSDK" "argon2-cffi==$ARGON2_CFFI" "bcrypt==$BCRYPT"
echo ">> ansible collections"
ANSIBLE_COLLECTIONS_PATH="$T/collections" "$T/venv/bin/ansible-galaxy" collection install -p "$T/collections" -r "$repo/ansible/requirements.yml" >/dev/null

echo
echo "DR tools ready in $T ($OS/$ARCH). In every new shell:"
echo "  eval \"\$($repo/scripts/dr-tools.sh --env)\""
"$0" --check
