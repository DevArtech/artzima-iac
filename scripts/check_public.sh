#!/usr/bin/env bash
# `make check-public`: is the tree safe to push to a PUBLIC repo?
#   1. gitleaks over exactly what git would commit (tracked + untracked,
#      minus .gitignore): default rules + .gitleaks.toml (PVE tokens, tailscale
#      keys, tunnel tokens, password hashes, personal emails, SOPS values, ...).
#   2. gitleaks over the git history, if there is any.
#   3. No SOPS/age leftovers: .sops.yaml, *.sops.* files, `sops:` metadata.
#   4. No secret-bearing local files tracked (local.env, state, kubeconfig...).
# Exit 1 on any finding. Gitignored files (your local.env, tofu state) are
# not scanned: they never leave this machine.
set -euo pipefail
cd "$(dirname "$0")/.."
T="${ARTZIMA_TOOLS:-$HOME/.local/share/artzima-tools}"
GITLEAKS="$(command -v gitleaks || echo "$T/bin/gitleaks")"
[ -x "$GITLEAKS" ] || { echo "gitleaks not installed: run make deps" >&2; exit 1; }
rc=0

# What would be committed (NUL-separated, robust to odd names).
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git ls-files -z --cached --others --exclude-standard > "$tmp/files"
while IFS= read -r -d '' selected; do
  if [ -L "$selected" ] || [ ! -f "$selected" ]; then
    echo "FAIL: non-regular publication path: $selected" >&2
    exit 1
  fi
done < "$tmp/files"
if ! tar --null -T "$tmp/files" -cf - | tar -xf - -C "$tmp"; then
  echo "FAIL: could not prepare every committable file for scanning" >&2
  exit 1
fi
rm -f "$tmp/files"
python3 scripts/check_metadata.py "$tmp"
echo "check-public: scanning $(find "$tmp" -type f | wc -l) committable files"
if ! "$GITLEAKS" dir "$tmp" --config .gitleaks.toml --redact --no-banner --exit-code 1; then
  echo "FAIL: gitleaks found secrets/personal data in committable files (above)" >&2
  rc=1
fi

if git rev-parse --verify -q HEAD >/dev/null; then
  if ! "$GITLEAKS" git . --config .gitleaks.toml --redact --no-banner --exit-code 1; then
    echo "FAIL: gitleaks found secrets in git history: rewrite it before pushing" >&2
    rc=1
  fi
fi

sops_left="$(git ls-files --cached --others --exclude-standard |
  grep -E '(^|/)\.sops\.ya?ml$|\.sops\.|(^|/)[^/]*\.agekey$|(^|/)keys\.txt$' || true)"
sops_meta="$(git ls-files -z --cached --others --exclude-standard |
  xargs -0 -r grep -lE '^sops:|"sops": *\{|^ *mac: ENC\[' 2>/dev/null || true)"
if [ -n "$sops_left$sops_meta" ]; then
  printf 'FAIL: SOPS/age leftovers (secrets live in Infisical now):\n%s\n%s\n' "$sops_left" "$sops_meta" >&2
  rc=1
fi

local_left="$(git ls-files --cached --others --exclude-standard |
  grep -E '(^|/)(local\.(env|ya?ml)|\.env[^/]*|[^/]*\.env|\.envrc|kubeconfig[^/]*|talosconfig[^/]*|id_rsa[^/]*|id_ed25519[^/]*)$|\.(pem|key|p12|pfx|tfvars|tfstate|tfplan)(\.[^/]*)?$|(^|/)(\.private|tofu/out|ansible/out|install/answers|dist|\.terraform|\.ansible|node_modules)/' | grep -Ev '\.env\.example$|\.tfvars\.example$|^images/[^/]+/upstream\.env$' || true)"
if [ -n "$local_left" ]; then
  printf 'FAIL: local secret-bearing files are not gitignored:\n%s\n' "$local_left" >&2
  rc=1
fi

[ "$rc" = 0 ] && echo "check-public: clean (no detected secrets/personal emails or forbidden publication paths)"
exit "$rc"
