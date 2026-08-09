# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-08-09

### Added

- **`output_styles:`**, a new artifact type for Claude Code output styles. Each name resolves to
  `output_styles/<name>.md` and deploys verbatim to `.claude/output-styles/<name>.md`, frontmatter
  included: Claude validates that frontmatter against a strict schema, so Bun Off injects nothing
  of its own. Selecting a style stays a separate act through `settings.claude.outputStyle`.
  OpenCode and Antigravity CLI have no equivalent surface and warn and skip; the OpenCode analogue
  is an `agents:` entry with `mode: primary`, which the manifest could already express.

## [0.3.1] - 2026-07-27

### Fixed

- A skill folder no longer ships build and editor droppings. Tooling run inside a skill folder
  leaves files behind — running a template's own tests writes `templates/__pycache__/*.pyc` —
  and 0.3.0 deployed them to every platform. They are untracked, so a bundle fetched from a git
  reference never carried them: only a local manifest path did, which is the path a bundle
  author uses while iterating, so the author was the last to notice. The `__pycache__`, `.git`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, and `.ipynb_checkpoints` directories are now
  skipped at any depth, along with `*.pyc`, `*.pyo`, `.DS_Store`, `Thumbs.db`, `*.swp`, `*.swo`,
  `*~`, `*.orig`, and `*.rej`. Everything else still ships. Files deployed by 0.3.0 are removed
  on the next `boff deploy`, like any other supporting file a bundle stops shipping.
- `boff.artifacts.__all__` listed `SKILL_FILENAME` and `SkillFile` twice.

## [0.3.0] - 2026-07-27

### Added

- Skills can ship supporting files. A skill listed in `skills:` may now be a `skills/<name>/`
  folder holding `SKILL.md` plus its own subtree, instead of a single `skills/<name>.md`. The
  whole subtree deploys beside the entry point, keeping its layout, so the references and
  templates a skill links to land where `SKILL.md` expects them. Supporting files are read by
  the assistant rather than parsed by the platform, so this works identically on Claude Code,
  OpenCode, and the Antigravity CLI. A skill written as a single markdown file keeps working
  unchanged, and `boff check` verifies every supporting file while `boff deploy` removes the
  ones a bundle stops shipping.

## [0.2.0] - 2026-07-27

### Added

- `boff deploy` and `boff check` accept several manifests at once. They merge left to right by the
  same last-wins rules as `extends:`, so the last one given wins on a name collision. This is
  `extends:` without having to author a wrapper manifest.
- `boff deploy --add <manifest>` and `--remove <manifest>` change what is deployed in a directory
  without restating the whole stack. Boff records the manifests it deployed in `.boff/state.json`,
  re-merges the amended list, and re-deploys the result, so a deploy stays authoritative: adding a
  manifest is a full re-deploy of a longer stack, not a partial install, and removing one reclaims
  its files. Both flags are repeatable, and `--remove` applies before `--add`.
- `--platform` is now optional on `deploy` and `check`, defaulting to the platforms already
  deployed in the directory. `boff deploy` with no arguments re-deploys the recorded stack, and
  `boff check` with no arguments verifies it.

### Changed

- Name collisions between merged manifests now print a warning naming the section, the name, and
  the manifest that won. These warnings went to a `logging` logger boff never configured, so they
  were never shown; they now go to the console, for `extends:` as well as command-line stacks.
- A full `boff clean` also forgets the recorded stack, so a later `--add` does not resurrect
  manifests you just uninstalled. `boff clean --platform <name>` leaves the stack recorded.
- `.boff/state.json` is now schema version 2, adding the recorded stack. Existing version-1 files
  load and upgrade in place on the next deploy, with no change to what is installed. A state file
  written by a newer boff is now reported as such instead of being misread.

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

[Unreleased]: https://github.com/atom-sw/bun-off/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/atom-sw/bun-off/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/atom-sw/bun-off/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/atom-sw/bun-off/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/atom-sw/bun-off/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/atom-sw/bun-off/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/atom-sw/bun-off/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/atom-sw/bun-off/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/atom-sw/bun-off/releases/tag/v0.1.0
