---
title: Install
description: Install herdr-sheppard from source, register its Herdr manager and build the optional transcript graph.
---

herdr-sheppard is a fork of Zoetrope with OMP Titanium visuals and a Herdr popup/browser manager.

## Herdr manager

Requires Python 3 and Herdr 0.9.3 or later. Register inside a genuine Herdr terminal:

```sh
git clone https://github.com/bloodf/herdr-sheppard
cd herdr-sheppard
herdr plugin link "$PWD/herdr-plugin"
```

Choose **Sheppard: orchestrators and workers** or **Sheppard: browser dashboard**. Attached remote inventory is read-only until you explicitly pin its workspace. The manager needs no Rust build or model calls.

## Optional transcript graph

Requires Rust 1.88 or later:

```sh
cargo build --release --locked --bin sheppard
./target/release/sheppard --help
```

Graph actions open supported Claude Code or Codex transcripts using the fork binary. OMP/Pi agents use the fleet manager; their transcript format has no graph adapter here. This fork has no published Homebrew or crates.io package.

## Browser replay

The [browser app](/app) runs the shared Rust core compiled to WASM. Build locally with `trunk`, the `wasm32-unknown-unknown` Rust target and pnpm:

```sh
rustup target add wasm32-unknown-unknown
cd web
pnpm install --frozen-lockfile
pnpm run build
pnpm run preview
```

`build:site` builds only the static pages and requires an already-built WASM module for replay. There is no deployed fork website configured.

See the [repository README](https://github.com/bloodf/herdr-sheppard#readme) and [manager documentation](https://github.com/bloodf/herdr-sheppard/blob/main/manager/README.md) for controls and optional OMP metadata.
