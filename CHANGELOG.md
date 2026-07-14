# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.3] - 2026-07-14

### Fixed

- `boff deploy` now adds the config files it creates by merge — `.claude/settings.json`,
  `.mcp.json`, `opencode.json` — to the managed `.gitignore` block when boff owns every key in
  them, so they no longer show up as untracked in `git status`. A merge file that also holds keys
  you wrote is left tracked and listed commented-out, with a note, so ignoring the whole file (and
  hiding your own keys from git) stays an explicit opt-in.

## [0.1.2] - 2026-07-14

### Fixed

- `boff deploy` now writes its managed `.gitignore` block whenever the deploy directory is
  inside a git repository, including a subfolder below the repository root. Previously it wrote
  the block only when `.git` sat directly in the deploy directory, so deploying a bundle into a
  subfolder left the generated files tracked instead of ignored. The block is written as a
  nested `.gitignore` in the deploy directory, scoped to that subfolder.

## [0.1.1] - 2026-07-09

### Fixed

- `mise run sync` failed with `uv: not found` on a machine with mise but no separately installed
  uv, because `.mise.toml` declared only Python. It now declares `uv` too, so `mise run <task>`
  provisions the whole toolchain. This affects contributors and CI; installed packages are
  unchanged.

## [0.1.0] - 2026-07-09

Initial public release.

### Added

- `boff deploy`: renders a `boff.yaml` manifest to a platform's native config locations, with
  `--dry-run`, `--clean`, `--wipe`, and `--no-ignore`.
- `boff check`: read-only verification of a deployed workspace, reporting missing, drifted, and
  stale artifacts.
- `boff clean`: removes Bun Off's recorded footprint while preserving hand-authored files.
- `boff context`: migrates assistant context (handoff digest, memory) between platforms.
- `boff --version`.
- Platforms: `claude`, `opencode`, `antigravity`.
- Artifact types: rules, skills, slash commands, agents, MCP servers, permissions, settings,
  lifecycle hooks, and event hooks.
- `extends:` manifest inheritance over local paths and git references.
- Plugin sources (`local`) and tool installers (`mise`).
- Deploy-state tracking in `.boff/state.json`, so re-deploying a different manifest cleans up the
  previous one instead of accumulating drift.
- PEP 561 `py.typed` marker: the package ships its inline type annotations.

[Unreleased]: https://github.com/atom-sw/bun-off/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/atom-sw/bun-off/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/atom-sw/bun-off/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/atom-sw/bun-off/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/atom-sw/bun-off/releases/tag/v0.1.0
