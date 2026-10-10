#!/usr/bin/env bash
# Resolve the explicitly selected fork binary. Never download or use stock zoe.
set -euo pipefail
plugin_root="${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source_root=$(cd "$plugin_root/.." && pwd)
binary="${SHEPPARD_BIN:-$source_root/target/release/sheppard}"
if [ ! -x "$binary" ] || [ -d "$binary" ]; then
  printf 'Sheppard graph binary is missing. From a herdr-sheppard source checkout run:\n  cargo build --release --bin sheppard\nOr set SHEPPARD_BIN to an explicitly selected executable. The Python manager remains available.\n' >&2
  exit 1
fi
if ! "$binary" --help 2>/dev/null | grep -q -- '--provider'; then
  printf 'The selected SHEPPARD_BIN does not support --provider. Build this fork.\n' >&2
  exit 1
fi
printf '%s\n' "$binary"
