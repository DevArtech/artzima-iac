#!/usr/bin/env bash
# Install the gitleaks pre-commit hook for this repo (it is PUBLIC).
#   scripts/install-git-hooks.sh        (also run by `make hooks`)
# Every commit is scanned (staged changes only, fast) with .gitleaks.toml, and
# SOPS/age leftovers are refused. Bypass only if you are sure: git commit --no-verify.
# Needs gitleaks (make deps puts a pinned one in the tools dir).
set -euo pipefail
cd "$(dirname "$0")/.."
hooks="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks"
if [ -e "$hooks/pre-commit" ] && ! grep -q 'artzima-iac gitleaks hook' "$hooks/pre-commit"; then
  mv "$hooks/pre-commit" "$hooks/pre-commit.before-artzima"
  echo "kept your previous hook as $hooks/pre-commit.before-artzima"
fi
cat > "$hooks/pre-commit" <<'EOF'
#!/usr/bin/env bash
# artzima-iac gitleaks hook (scripts/install-git-hooks.sh)
set -euo pipefail
T="${ARTZIMA_TOOLS:-$HOME/.local/share/artzima-tools}"
GITLEAKS="$(command -v gitleaks || echo "$T/bin/gitleaks")"
[ -x "$GITLEAKS" ] || { echo "pre-commit: gitleaks missing (make deps)" >&2; exit 1; }
"$GITLEAKS" git --pre-commit --staged --redact --no-banner --config .gitleaks.toml --exit-code 1
bad="$(git diff --cached --name-only --diff-filter=ACMR |
  grep -E '(^|/)\.sops\.ya?ml$|\.sops\.|\.agekey$|(^|/)local\.env$|(^|/)local\.ya?ml$|\.tfstate|kubeconfig|talosconfig' || true)"
if [ -n "$bad" ]; then
  printf 'pre-commit: refusing secret-bearing or SOPS files:\n%s\n' "$bad" >&2
  exit 1
fi
# Fail closed on forbidden publication paths and unreadable/symlinked files, too.
scripts/check_public.sh
EOF
chmod +x "$hooks/pre-commit"
echo "installed $hooks/pre-commit (gitleaks $("${ARTZIMA_TOOLS:-$HOME/.local/share/artzima-tools}/bin/gitleaks" version 2>/dev/null || echo '?'))"
