# Sheppard Herdr plugin

The unified `bloodf.herdr-sheppard` plugin provides OMP worker management and the forked Zoetrope transcript viewer. It requires Herdr 0.9.1 or later and Python 3 on macOS or Linux. It has no install-time graph binary gate and downloads no executable.

Register the source directory with `herdr plugin link /absolute/path/to/herdr-sheppard/herdr-plugin` from a genuine Herdr session. Registration is separate from copying files or enabling the optional OMP reporter. The [manager guide](../manager/README.md) describes scoped local and saved-remote observation, controls, telemetry, and the reporter installer.

| Action | Result |
| --- | --- |
| `open` | Starting-workspace orchestrator/worker popup |
| `browser` | Starting-workspace tab serving the localhost browser dashboard |
| `graph-open` | Focused Claude Code/Codex transcript graph in an overlay |
| `graph-split` | Focused transcript graph in a persistent split |
| `graph-tab` | Focused transcript graph in its own tab |
| `setup-keys` / `remove-keys` | Explicitly install/remove an optional managed graph keybinding |

Graph actions use `target/release/sheppard` from this fork or the executable explicitly selected by `SHEPPARD_BIN`. Build it with `cargo build --release --bin sheppard` from the fork root. Missing binaries show that instruction while leaving the Python manager usable. Stock `zoe` is never installed or selected.

The inherited transcript parser supports Claude Code and Codex. Their native Herdr session IDs select the transcript; the bridge never guesses an OMP format or session file. OMP/Pi workers are managed in the popup/browser via native Herdr state and optional OMP metadata; requesting their focused transcript graph reports that parsing is unsupported. A graph action opens a new plugin pane and does not close panels by matching their titles.

Shell bridge checks use temporary fake Herdr and Sheppard executables. They do not prove real plugin registration, a real graph render, or live attached remote connectivity.
