#!/usr/bin/env bash
# Open the inherited transcript graph without closing unrelated panes.
set -euo pipefail
[ "${HERDR_ENV:-}" = 1 ] || { echo 'Start this action inside Herdr.' >&2; exit 2; }
herdr="${HERDR_BIN_PATH:-herdr}"
placement="${2:-overlay}"
case "${1:-open}" in
  open) ;;
  *) printf 'usage: pane.sh open [overlay|popup|split|tab|zoomed]\n' >&2; exit 2 ;;
esac
case "$placement" in
  overlay|popup|split|tab|zoomed) ;;
  *) echo 'Unsupported graph placement.' >&2; exit 2 ;;
esac
exec "$herdr" plugin pane open --plugin "${HERDR_PLUGIN_ID:-bloodf.herdr-sheppard}" --entrypoint graph --placement "$placement"
