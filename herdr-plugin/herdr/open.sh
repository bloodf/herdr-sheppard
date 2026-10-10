#!/usr/bin/env bash
set -euo pipefail
[ "${HERDR_ENV:-}" = 1 ] || { echo 'Start this graph inside Herdr.' >&2; exit 2; }
root="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
die() {
  printf '\n%s\n\n' "$*" >&2
  read -r -p 'Press Enter to close ' _ || true
  exit 1
}
binary=$(bash "$root/herdr/ensure-sheppard.sh" 2>&1) || die "$binary"
target=$(bash "$root/herdr/resolve.sh" 2>&1) || die "$target"
provider=$(printf '%s' "$target" | python3 -c 'import json,sys; print(json.load(sys.stdin)["provider"])')
value=$(printf '%s' "$target" | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])')
"$binary" --provider "$provider" --follow "$value" || die "Sheppard exited with status $?"
