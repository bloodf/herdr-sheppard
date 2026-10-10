#!/usr/bin/env bash
# A focused native session reference is the authority; no transcript guessing.
set -euo pipefail
[ "${HERDR_ENV:-}" = 1 ] || { echo 'Start this action inside Herdr.' >&2; exit 2; }
herdr="${HERDR_BIN_PATH:-herdr}"
ctx="${HERDR_PLUGIN_CONTEXT_JSON:-{\}}"
pane_id=$(printf '%s' "$ctx" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("focused_pane_id") or "")')
[ -n "$pane_id" ] || { echo 'No focused agent pane in the native invocation context.' >&2; exit 1; }
resp=$("$herdr" pane get "$pane_id") || { echo 'Herdr pane get failed.' >&2; exit 1; }
printf '%s' "$resp" | python3 -c '
import json,sys
response=json.load(sys.stdin)
if response.get("error"): sys.exit("Herdr rejected the focused pane read.")
ref=response.get("result",{}).get("pane",{}).get("agent_session") or {}
agent,kind,value=(ref.get(k) for k in ("agent","kind","value"))
if agent in ("omp","pi"): sys.exit("OMP/Pi transcripts are not supported by the inherited transcript parser. Open the Sheppard worker manager instead.")
if agent not in ("claude","codex"): sys.exit("Focused pane has no supported Claude Code/Codex native session reference.")
if kind!="id" or not isinstance(value,str) or not value or "\x00" in value: sys.exit("Native Claude Code/Codex session id is missing or invalid.")
print(json.dumps({"provider":agent,"kind":kind,"value":value}))
'
