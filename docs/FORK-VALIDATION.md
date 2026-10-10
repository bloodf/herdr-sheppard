# Fork validation

The initial herdr-sheppard fork uses Zoetrope commit `b1f31dd` as its base. Changes preserve the transcript provider/core boundary and introduce the OMP Titanium theme, fleet manager and unified Herdr plugin.

## Verified locally

- Rust tests: 216 passed.
- Rust formatting, clippy with warnings denied, release binary and standalone Cargo package build passed.
- Python controller and installer tests: 26 passed.
- Native bridge fixtures: 6 passed.
- OMP reporter: typecheck, bundled build and 4 tests with 17 assertions passed.
- Dashboard behavior and authentication fixtures passed.
- WASM replay and static website builds passed. The bundled demo renders in the browser with the Titanium theme and no console warnings or errors.
- OMP RPC smoke probes on the local host, CortexOS and bloodf Incus loaded the framework, Laya plugin and reporter with six unique tools and zero model requests.

A fresh independent AI chat reviewed the fork. Installer help side effects, an incorrect registration path, missing popup colors, undefined web font variables and omitted installer CI coverage were corrected. Its focused rechecks reported no remaining findings. A subsequent browser check corrected muted graph text contrast.

## Remaining live checks

Native Herdr registration is verified on the local host, CortexOS and bloodf Incus. Each host reports `bloodf.herdr-sheppard` enabled with its expected actions and panes. Registration is a host configuration operation and needs no focused-session access. The OMP reporter is also enabled on the three hosts; existing sessions were not restarted.

Real popup operation and attached-remote controls remain unverified. They require execution inside a genuine Herdr workspace. The fork binary is installed on all three hosts. The macOS build runs locally. One x86_64 Linux release build runs on both CortexOS and bloodf Incus; matching SHA-256 hashes and successful `--version` execution verify the installed binaries. No macOS binary is copied to Linux servers.

Fixtures use artificial Herdr responses. They prove scoped routing and rejection behavior, not connectivity or atomic compare-and-send guarantees from Herdr itself. No claim of complete project orchestration or quota recovery follows from these UI tests.
