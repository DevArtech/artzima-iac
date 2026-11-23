#!/usr/bin/env bash
# Confirmation gate for every "apply" make target (docs/OPERATOR-SETUP.md).
#   scripts/confirm.sh "<what is about to happen>" [extra args of the command]
# Proceeds when:
#   - AUTO_APPROVE=1 is set (e.g. `make up AUTO_APPROVE=1`), or
#   - the extra args contain --check (an Ansible dry run changes nothing), or
#   - a human types "yes" on a terminal.
# Refuses otherwise, including in non-interactive runs (CI): nothing applies by accident.
set -euo pipefail

what="${1:-this step}"
shift || true
case " $* " in
  *" --check "*) exit 0 ;;
esac
if [ "${AUTO_APPROVE:-0}" = "1" ]; then
  echo ">>> AUTO_APPROVE=1: proceeding with: $what"
  exit 0
fi
if [ ! -t 0 ]; then
  echo "refusing: '$what' needs confirmation (run it in a terminal, or set AUTO_APPROVE=1)" >&2
  exit 1
fi
printf '\n>>> %s\n>>> Type "yes" to continue: ' "$what"
read -r answer
if [ "$answer" != "yes" ]; then
  echo "cancelled" >&2
  exit 1
fi
