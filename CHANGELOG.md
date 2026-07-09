# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `boff deploy` and `boff check` accept a Git URL as their manifest argument, not only a local
  path. The manifest names what to deploy; the workspace stays the current directory.
- Manifest references accept a repository URL without a `.git` suffix, and a forge's browser URL
  (`https://github.com/org/repo/tree/main/sub`, GitLab's `/-/tree/`). Both `extends:` and the CLI
  argument use this one scheme.

### Fixed

- A Git URL passed to `boff deploy` resolved as a local path, failing with an unhandled
  `FileNotFoundError` traceback. The CLI never consulted the manifest-source registry.
- A failed reference resolution (bad URL, failed `git` command, missing `boff.yaml`) raised a
  traceback instead of a `ManifestError`, so it printed no readable message.

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

[Unreleased]: https://github.com/atom-sw/bun-off/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/atom-sw/bun-off/releases/tag/v0.1.0
