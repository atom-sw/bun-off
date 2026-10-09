# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.1] - 2026-10-09

### Fixed

- Restore the 0.5.2 fix that 0.6.0 accidentally dropped: `rules[].globs:` scoping on Claude
  Code renders a `paths:` frontmatter block again. 0.6.0 was built without it and went back to
  `globs:` frontmatter, which Claude Code ignores, so every scoped rule loaded on every file.
  Upgrade from 0.6.0 and deploy again to rewrite the affected rule files; no manifest changes
  are needed.

## [0.6.0] - 2026-10-09

### Added

- A skill or rule entry can take its content from a Git repository with `from: <git URL>`,
  naming a directory that stands in for the local `skills/` or `rules/`. The fetched skill
  deploys to every platform and `boff check` verifies it, so a skill collection published as a
  repository reaches OpenCode and Antigravity as well as Claude Code. Each repository is fetched
  once per run, into the same cache as `extends:`.

### Fixed

- Combining manifests from one Git repository at different refs (for example, two bundles of
  one bundle repository at their own tags) no longer runs one manifest's hooks, or reads its
  `mise:` files and local plugin folders, from the other ref. Each referenced commit now gets
  its own checkout in the cache.
- `--dry-run` lists each planned operation. Before, it printed only the count unless
  `--verbose` was also given. The header now pluralizes correctly: "planned 1 operation:" or
  "planned N operations:".

## [0.5.2] - 2026-08-23

### Fixed

- `rules[].globs:` scoping on Claude Code now renders a `paths:` frontmatter block instead of
  `globs:`. Previously, `globs:` was used to this end, possibly because older versions of Claude
  Code lacked a precise documentation. Live testing against claude 2.1.241 found that `globs:`
  frontmatter loads a rule unconditionally on every file, while `paths:` (the currently documented key)
  loads it only when a matching file is read. No manifest changes are needed.

## [0.5.1] - 2026-08-20

### Fixed

- A `SKILL.md` whose frontmatter is not valid YAML no longer aborts the command with a stack
  trace from the YAML parser. 0.5.0 began parsing skill frontmatter and did not handle a block
  that fails to parse, so an unquoted `description` containing `": "` turned `boff deploy`
  and `boff check` into a traceback naming library internals rather than the file at fault.
  Bun Off now reports the skill and the path, and treats the block the same as one missing a
  required key.

## [0.5.0] - 2026-08-20

### Added

- **A global (user-level) install scope.** `boff deploy`, `boff check`, and `boff clean` accept
  `--global`, which targets your user-level configuration instead of the current workspace.
  The two scopes are complementary and independent: each records its own state
  (`~/.boff/state.json` against `<project>/.boff/state.json`) and its own manifest stack — so a
  personal stack deployed once per machine coexists with a project stack deployed per repository.
- Antigravity CLI gains user-level configuration features it does not possess at the workspace level:
  `skills:`, `agents:`,  `mcp_servers:`, `event_hooks:` and `plugins:` all deploy to
  `~/.gemini/config/`, and `settings:` merges into `~/.gemini/antigravity-cli/settings.json`.
- Antigravity CLI also reads rules at user level, which it does not do in a workspace. A global
  deploy writes one aggregate `~/.gemini/config/rules/boff.md`.
- Skills are validated when the manifest loads: a `SKILL.md` must declare `name` and a non-empty
  `description`.

### Changed

- The `mise` tool installer now honors `$XDG_CONFIG_HOME` for a global install, instead of
  assuming `~/.config`.
- `boff check`'s `dropped` status now means "the platform has no target for this artifact *in
  this scope*". A surface can exist at one level and not the other, in both directions.

### Fixed

- `--wipe` is refused together with `--global`. A wipe deletes a platform's whole configuration
  directory, which would be the whole `~/.claude` at global level, but that directory also stores
  credentials and session history. Use `boff clean --global`, which removes only Bun Off's own
  recorded footprint.

### Known limitations

- `mcp_servers:` are dropped for Claude Code at user level. Its only user-scope target,
  `~/.claude.json`, holds OAuth credentials and is rewritten by every session, and Bun Off merges
  by rewriting the whole file. Use `claude mcp add --scope user`.
- OpenCode's user-level rules directory is flat: a relative `instructions` glob resolves against
  whichever project you are in, so Bun Off registers an absolute entry, and OpenCode globs only
  its last path segment. A rule with a `category:` still deploys, with a warning.
- `permissions:` are dropped for Antigravity CLI in every scope. It does hold user-level
  permissions, but the JSON key they live under is not established, and a wrong guess would
  deploy settings the tool ignores while `boff check` reported success.
- A `plugins:` entry using `source: local` has no user-level target and is skipped, with a
  warning, by a global deploy.

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

[Unreleased]: https://github.com/atom-sw/bun-off/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/atom-sw/bun-off/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/atom-sw/bun-off/compare/v0.5.2...v0.6.0
[0.5.2]: https://github.com/atom-sw/bun-off/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/atom-sw/bun-off/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/atom-sw/bun-off/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/atom-sw/bun-off/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/atom-sw/bun-off/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/atom-sw/bun-off/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/atom-sw/bun-off/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/atom-sw/bun-off/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/atom-sw/bun-off/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/atom-sw/bun-off/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/atom-sw/bun-off/releases/tag/v0.1.0
