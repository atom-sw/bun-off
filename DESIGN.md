# bun-off: Design and Implementation Notes

This document explains the architectural decisions behind `bun-off` and how the major
subsystems work. It targets contributors who want to add a new platform, plugin source, tool
installer, or artifact type, and anyone who wants to understand why the code is structured the
way it is.

---

## Guiding principles

**Modularity-first.** Every extension axis — platforms, plugin sources, tool installers, context
providers — is an explicit registry. Adding a new entry requires one new file and one
registration line; nothing else changes. This invariant must hold as the codebase grows.

**Declarative configuration.** `boff.yaml` is the single source of truth. There is no
auto-detection, no scanning for installed platforms, and no inferred scope. Every meaningful
choice is explicit: which platforms to target, which artifacts to deploy, which hooks to run.

**Plan-then-apply.** The deploy and context pipelines produce a flat list of operations before
any files are written. The executor applies that list. These two stages are separate by design:
dry-run, hooks, and future inspection or serialization all depend on it.

**Adapters are pure.** Platform adapters take an artifact and return a list of operations. They
hold no state, make no file system calls, and have no side effects. This makes them trivial to
test and compose.

---

## The operation model

The core data model is a list of `Operation` values produced by planning and consumed by the
executor. There are four kinds:

```
FileOperation(target, content, merge, description)
ShellAction(argv, cwd, env, description)
DeleteOperation(target, description, prune_until)
PruneKeysOperation(target, key_paths, description)
```

`FileOperation` supports two merge strategies (`MergeStrategy`, a `StrEnum`):

- `OVERWRITE`: write the content directly, replacing whatever was there.
- `MERGE`: JSON deep-merge the incoming content into the existing file. Used for aggregated
  platform config files (`.mcp.json`, `opencode.json`) that accumulate entries from multiple
  operations.

An `APPEND` strategy was removed: it had no producer and the executor rejected it, so a manifest
author could declare a strategy that always crashed. Re-add it when a real artifact needs
file-append semantics.

`ShellAction` runs a subprocess. It is used sparingly — currently only by the OpenCode context
provider to call `opencode import`.

`DeleteOperation` removes a file or directory tree, then prunes parent directories left empty,
stopping below `prune_until` (so a deploy never deletes the project root). `PruneKeysOperation`
removes specific JSON key paths from a merged file while preserving the rest. Both exist for the
cleanup pass described in [Deploy-state tracking](#deploy-state-tracking): they are produced by
reconciliation, not by adapters.

The executor (`src/boff/executor.py`) is intentionally dumb: it processes the list in order,
applies each operation, and raises on failure. No retry, no rollback, no logging beyond what
the CLI prints. Complexity belongs in planning, not in application.

---

## The three registries

Three independent registries handle the three dispatch axes. All three follow the same pattern:
a base class uses `__init_subclass__` to auto-collect decorated methods, and a module-level
registry maps names to singleton instances. Callers use `get_adapter(name)`,
`get_source(name)`, or `get_tool_installer(name)` to look up registered instances.

The name-keyed lookup is a shared generic `Registry[T]` (`src/boff/registry.py`), instantiated
once per registry (adapters, sources, tool installers, and context providers); an unknown name
raises `RegistryError` (a `BoffError`). The ordered `manifest_sources` registry stays bespoke:
it dispatches by prefix matching with a catch-all, not by exact name.

### PlatformAdapter — dispatches on artifact type

```python
class ClaudeAdapter(PlatformAdapter):
    name: ClassVar[str] = "claude"

    @renders(Rule)
    def _rule(self, artifact: Rule, *, platform: str, scope: Scope) -> list[Operation]:
        ...

    @renders(MCPServer, drops={ScopeKind.GLOBAL})
    def _mcp_server(self, artifact: MCPServer, *, platform: str, scope: Scope) -> list[Operation]:
        ...
```

`@renders(ArtifactType)` registers a method as the handler for that artifact type. When the
deploy engine calls `adapter.render(artifact, ...)`, the base class dispatches to the
registered method for `type(artifact)`. An adapter that does not register a handler for a given
type simply does not support that type; `adapter.supports(t)` returns false.

The decorator is generic over the artifact type: `renders[A](artifact_type: type[A])` accepts
only a method matching the `RenderMethod[A]` callable protocol. A renderer whose parameter
disagrees with the type it claims to render, or that misspells a keyword, is a type error
rather than a runtime surprise. The dispatch table itself stays `dict[type[Any],
RenderMethod[Any]]`: the lookup key is a runtime type, so the binding cannot be expressed
statically.

The `platform` argument is passed to every handler even though most handlers currently ignore
it. It is there for adapters that need to behave differently based on whether they are the
primary or a secondary target in a multi-platform deploy.

Each adapter carries a `layout: ClassVar[PlatformLayout]` (from `platform_layout.py`) that
holds every on-disk path fact for the platform. The renderers whose only per-platform
difference is that layout plus the platform name (`_skill`, `_slash_command`, `_settings`,
`_mcp_server`) are defined once on `PlatformAdapter` and inherited; subclasses override only
the genuinely divergent renderers. `PlatformLayout` is the single source of truth shared with
the context providers (see "The context subsystem"), so a path like `.claude/rules` is spelled
in exactly one place.

Files: `src/boff/platform_layout.py`, `src/boff/adapters/base.py`,
`src/boff/adapters/claude.py`, `src/boff/adapters/opencode.py`, `src/boff/adapters/__init__.py`.

### PluginSource — dispatches on platform name

```python
class LocalSource(PluginSource):
    name: ClassVar[str] = "local"
    spec_class: ClassVar[type[PluginSpec]] = LocalSpec

    def _mirror(self, spec: LocalSpec, *, scope: Scope) -> list[Operation]:
        ...

    @installs_for("claude")
    def _claude(self, spec, *, scope): return self._mirror(spec, scope=scope)
    @installs_for("opencode")
    def _opencode(self, spec, *, scope): return self._mirror(spec, scope=scope)
    @installs_for("antigravity")
    def _antigravity(self, spec, *, scope): return self._mirror(spec, scope=scope)
```

`@installs_for("platform")` registers a method as the handler for that platform string. When
`source.install(spec, platform="claude", ...)` is called, the base class dispatches to the
matching method.

The `spec` argument is the parsed install spec for this entry. `LocalSpec` carries a resolved
`Path`; future sources will have their own spec classes. The manifest parser constructs the
spec from the raw YAML and passes it through. All specs satisfy the `PluginSpec` protocol,
whose only member is `source: str`: that is everything the generic layer needs to know.

`spec_class` names the one spec class a source accepts, and it is load-bearing. `install`
checks the incoming spec against it and raises a `BoffError` on a mismatch, which is what
lets `_claude` above annotate `spec: LocalSpec` honestly. `__init_subclass__` raises if a
subclass omits `spec_class`, so the omission surfaces at import time rather than as an
`AttributeError` on the first install.

Files: `src/boff/sources/base.py`, `src/boff/sources/local.py`,
`src/boff/sources/__init__.py`.

### ToolInstaller — simple override

```python
class MiseInstaller(ToolInstaller):
    name = "mise"

    def install_files(self, files, *, scope):
        ...
```

Tool installers receive the resolved file paths from `manifest.tool_files[name]` and produce
operations. There is no per-platform dispatch here: tool files apply equally to all platforms
in the current invocation. If that assumption ever breaks, the interface will need a `platform`
parameter.

`MiseInstaller` writes each listed file as its own boff-owned drop-in inside mise's `conf.d`
directory (`.config/mise/conf.d/boff-<n>.toml` relative to the workspace root, or
`~/.config/mise/conf.d/boff-<n>.toml` for global scope), one per source so repeated `[tools]`
tables never collide. This leaves the project's own `mise.toml` untouched: mise discovers and
merges all `*.toml` files in `conf.d` automatically.

Files: `src/boff/tool_installers/base.py`, `src/boff/tool_installers/mise.py`,
`src/boff/tool_installers/__init__.py`.

---

## The artifact types

Each artifact is a **frozen dataclass** with a `name`, a `content` field, and an `available_on`
frozenset. An empty `available_on` means "deploy to all platforms"; a non-empty set restricts
the artifact to those platforms.

The core markdown/raw types:

| Type | Content | Extra fields |
|---|---|---|
| `Rule` | markdown string | `category: str \| None`, `globs: tuple[str, ...]` |
| `Rules` | `rules: tuple[Rule, ...]` | — |
| `Skill` | markdown string (`SKILL.md`) | `files: tuple[SkillFile, ...]` |
| `SlashCommand` | markdown string | — |
| `OutputStyle` | markdown string | — |
| `MCPServer` | `raw: dict[str, dict]` (per-platform JSON) | — |
| `Agent` | markdown string (prompt body) | `description`, `model: str \| dict[str, str] \| None`, `mode`, `permissions` |

**One rule set, two shapes.** `iter_artifacts` yields each `Rule` *and* a `Rules` aggregate over
all of them. A platform that reads a rules *directory* (Claude, OpenCode) renders the individual
`Rule` and ignores the aggregate; a platform that reads rules only from one instructions file
(Antigravity) renders the aggregate and ignores the individual rules. `PlatformAdapter.supports`
dispatches on artifact type, so each adapter simply declares which shape it can render, and the
other is skipped with no branching in the deploy engine. `EventHooks` uses the same aggregate
pattern for a different reason: its glue is per-platform, not per-hook.

**An output style is Claude's alone, and stays that way.** An output style modifies the *system
prompt* rather than adding context around it, which is a surface only Claude Code exposes. The
tempting mapping is OpenCode's primary agent, and it is wrong: OpenCode composes its prompt as
`agent.prompt ? [agent.prompt] : provider(model)`, so an agent's prompt *replaces* the base prompt
and can never append to it. Claude's `keep-coding-instructions: true` therefore has no OpenCode
encoding, and a translation would silently invert the author's intent. Two further reasons to
refuse: the target path `.opencode/agents/<name>.md` already belongs to `agents:`, so translating
would put two manifest sections on one file (see "One writer per file"), and the rendered agent
would stay inert until `default_agent` was set by hand. Both other adapters therefore register
`@renders(OutputStyle, drops=True)`. Authors who want the OpenCode behavior write it as an
`agents:` entry with `mode: primary`, scoped with `available_on:` — the same paired-artifact
recipe the format-on-edit hook uses.

**A skill is one file or a whole folder.** Every platform discovers a skill as
`<name>/SKILL.md`, and a real skill often carries more: reference documents it loads only when
needed, templates the assistant copies. So `skills/<name>/` in a manifest folder ships its whole
subtree, with `SKILL.md` in `Skill.content` and everything else in `Skill.files` as
`SkillFile(path, content: bytes)`, rendered beside the entry point so the relative links inside
`SKILL.md` resolve as authored. `skills/<name>.md` stays valid for a skill that needs nothing
else, and the loader picks the form from what is on disk rather than from a schema key: the two
spellings are the same artifact, not two kinds of it. Declaring both for one name is an error,
because a silent precedence rule would hide a typo.

This costs the rest of the system nothing. `FileOperation.content` was already `str | bytes`,
the executor already wrote bytes, and state tracking already recorded and reconciled each file
individually — so a supporting file dropped from a bundle is deleted on the next deploy and its
emptied directory pruned, with no new machinery. It is also platform-neutral: supporting files
are read by the *assistant* at run time with its own file tools, not parsed by any platform's
config loader, so all three platforms gain the feature through the one shared `_skill` renderer.

**A skill folder is not quite verbatim.** The first version of this shipped the subtree
unfiltered, on the same "the author decides what ships" reasoning that governs
`sources/local.py`. That was wrong here, and wrong within a day: running a template's own test
script inside the folder wrote `templates/__pycache__/*.pyc`, and the next deploy copied it to
all three platforms. The asymmetry is what makes it a trap. Untracked files never reach a
bundle fetched from git, so the droppings are invisible to everyone except someone deploying
from a **local** path — which is precisely what a bundle author does while iterating. The
author is therefore the one person who cannot see the problem, and their users are the ones who
get the stale bytecode. So `skill.ships` skips a fixed set of build and editor droppings
(`EXCLUDED_DIRS` matched against any parent component, `EXCLUDED_GLOBS` against the filename)
and passes everything else through. `sources/local.py` keeps mirroring unfiltered: it copies an
explicitly named subtree to the workspace root, rather than a folder that tooling runs inside.

**A skill or rule can come from Git, and becomes an ordinary one.** Skill collections are
published as repositories in the cross-platform `SKILL.md` layout, often alongside a Claude
plugin marketplace. Linking the marketplace reaches Claude Code only, so a skill or rule entry
may instead carry `from: <git URL>`, naming a directory that stands in for the local `skills/` or
`rules/`. The loader resolves the entry there with the unchanged file-or-folder lookup and
produces a plain `Skill` or `Rule`. That is the whole design: fetching happens at load time, as
for `extends:`, not as a deploy operation or a `PluginSource`, so rendering, state tracking,
merge-by-name, and `boff check` all apply without knowing where the content came from. Two
choices keep it consistent with the rest of boff. `from:` names the *collection* directory, not
the skill, so the name in `boff.yaml` is still what is looked up and deployed. And there is no
"import everything in the directory" form: every entry is still listed, so a bundle states what
it deploys and an upstream addition never appears unannounced.

`git.fetch` fetches each repository once per process: a bundle listing nine skills from one
repository would otherwise make nine network round trips.

`MCPServer.raw` is a map from platform name to the verbatim JSON object that platform expects.
This is a deliberate escape hatch: MCP server configuration is platform-specific and changes
frequently, so rather than trying to model it abstractly, the manifest author supplies the raw
JSON per platform. The adapter simply reads `artifact.raw["claude"]` and injects it.

`Agent.model` applies the same per-platform escape hatch to model selection. A bare string
deploys verbatim to every platform; a `dict[str, str]` supplies a distinct model id per
platform, resolved through `Agent.model_for(platform)` (a platform absent from the map inherits
that platform's default). boff deliberately does not maintain a model-name translation table:
Claude and OpenCode name models differently (`opus` vs. `anthropic/claude-opus-4-8`), and a
version table would drift, so the author states each platform's id explicitly. This keeps the
artifact consistent with the "explicit, no auto-detection" principle.

Files: `src/boff/artifacts/`.

---

## Scope: workspace and user level

A `Scope` names the deploy target: `WORKSPACE` (a project root) or `GLOBAL` (the user's own
configuration). It is a CLI flag (`--global`), never a manifest field, so the same bundle can be
deployed either way.

**Global is not the workspace tree rehomed under `$HOME`.** Each platform's user-level layout
differs structurally from its workspace one:

- Claude keeps the same relative shape under `~/.claude/`, but `CLAUDE.md` moves *inside* the
  config root, and it has no safe MCP target: user-scope servers live in `~/.claude.json`,
  which holds OAuth credentials and is rewritten by every session.
- OpenCode's config root *is* the base directory. Agents, commands, and skills sit directly
  under `~/.config/opencode/`, not under a nested `.opencode/`.
- Antigravity splits across two unrelated trees: customizations in `~/.gemini/config/`,
  settings in `~/.gemini/antigravity-cli/settings.json`.

So the layout does not hand out a root for callers to join strings onto. `PlatformLayout.paths(scope)`
returns a `ScopePaths` of resolved absolute paths — one field per surface, `None` where the
platform has no such surface *in that scope*. Adapters read `paths.skills_dir`; they never
compute one. The global paths come from a `global_factory` callable rather than a stored value,
because it reads `$HOME` and `$XDG_CONFIG_HOME` at call time, which is also what lets tests
point them at a temporary directory.

`Scope.root` (the workspace root, or `$HOME`) is what deploy state records paths relative to and
what directory pruning stops at. It is a property of the scope, not of any platform, so it is
deliberately absent from `ScopePaths`.

### Support is scope-dependent

A surface can exist in one scope and not the other, in **both** directions: Antigravity's
settings have only a user-level home, while Claude's MCP config has only a workspace one. So
`@renders(T, drops=...)` takes either `True` (drop everywhere) or a set of `ScopeKind`, and
`PlatformAdapter.drops(artifact_type, scope)` answers per scope.

An empty op list could not encode this: `verify` treats `ops == []` as "nothing to emit for this
manifest", never as a deliberate drop. The declaration has to be separate from the output.

### Why `--wipe` is refused at user level

`--wipe` deletes a platform's whole native config directory. At user level that is `~/.claude`,
which also holds credentials, session history, and per-project state that no manifest can
regenerate. The CLI rejects the combination outright. `--clean` still works globally: it is
driven by recorded state, so it removes only what boff wrote.

---

## The deploy engine

`plan_units(manifest, platforms, scope)` in `src/boff/deploy.py` is the primitive. It walks the
three groups and returns one `PlannedUnit` per unit of work, each recording its **owner** — the
platform name for adapter and plugin ops, `tool:<name>` (via `state.tool_owner`) for tool
installers — plus a `label` and a `Support` verdict:

1. For each platform, for each artifact available on it: classify the adapter's treatment of the
   artifact type (`_support`) and, when the adapter has a renderer, call `adapter.render(...)`.

2. For each plugin, for each platform in `plugin.install`: if the platform is in the current
   target list, call `source.install(spec, ...)`.

3. For each tool installer name, call `installer.install_files(paths, ...)`.

The order matters: artifacts come first, then plugins, then tool files. Within each group the
iteration order is deterministic (manifest order for artifacts and plugins, dict order for tool
files).

`deploy_plan(manifest, platforms, scope)` is then a fold over those units: it buckets their ops by
owner and drops the units that emitted nothing. The buckets exist so cleanup can be attributed per
owner (see below). Splitting the walk from the fold is what lets `boff check` report per artifact:
`deploy_plan`'s `dict[owner, list[Operation]]` has already thrown that attribution away.

`plan_deploy(...)` composes the full deploy plan on top: it computes the clear ops (a wipe or a
purge), the forward ops (`deploy_plan` flattened), and the cleanup ops (`reconcile`), returning a
`DeployPlan`. The CLI calls `plan_deploy`, then `execute(...)` per phase with hooks around it; the
planner itself knows nothing about hooks, dry-run, or I/O.

Note that `plan_deploy` flattens `forward_by_owner.values()`, so the forward ops execute in
owner-bucket order, not unit order. With two platforms, every op for the first platform runs before
any op for the second. Anything that replays the plan must consume it in that same flattened order.

---

## Error handling and exit codes

Every user-facing failure derives from `BoffError` (`src/boff/errors.py`), with
`ManifestError`, `BundleError`, and `HookError` as the specific cases. The CLI catches
`BoffError` once, in `main`, prints a single `error: <message>` line to stderr, and returns
exit code `1`. A bare exception that escapes to the user therefore signals a boff bug, not
user error: it is the one case that still yields a traceback.

The guarded boundaries are the points where boff parses data it did not produce: the
`boff.yaml` and raw-MCP JSON parses and every manifest-reference resolution, including the
`git` subprocess behind a remote one (`ManifestError`), the deploy-time merge/prune JSON
targets and the `state.json` load (`BoffError`), hook stdin and hook subprocess exit
(`HookError`), the context-bundle archive (`BundleError`, which also verifies the embedded
schema version), and the local/mise source reads. Subprocess calls (hooks and `ShellAction`)
carry a timeout so a hung child fails instead of blocking forever.

Exit codes live in the `ExitCode` `IntEnum` in `cli.py`: `OK = 0`, `ERROR = 1`,
`USAGE = 2`. The `execute` dispatch closes its `match` with `case _: assert_never(op)`,
so a new `Operation` variant is a type error rather than a silent no-op.

`boff check` reuses `ERROR` for a different meaning: not "boff failed" but "the workspace does not
match the manifest". A drifted or missing artifact is a finding, printed to stdout as part of the
report, never raised. Only genuine failures — an unreadable manifest, an unregistered platform
name, an absent platform CLI — take the `BoffError` path and print to stderr.

The manifest *validation* errors (a bad permission tool, a malformed agent entry) remain
`ValueError`: they already carry precise messages and are the province of a later cleanup;
only the enumerated external-input boundaries above are wrapped so far.

---

## Deploy-state tracking

Planning is a pure function of the manifest, but a manifest only says what *should* exist, not
what a *previous* deploy created. Without memory, re-deploying a changed manifest would leave
orphaned files behind and let merged config files (`.mcp.json`, `settings.json`,
`opencode.json`) accumulate keys forever. The design goal: **every deploy is authoritative** —
after it runs, the on-disk footprint matches the current manifest, and switching a project
between manifests (for example a `design` stack and a `maintenance` stack) is a clean swap.

The enabling primitive is a state record at `<Scope.root>/.boff/state.json` (`src/boff/state.py`),
keyed by `(scope, owner)`. Because the root *is* the scope's anchor, the two scopes land in
different files — `<project>/.boff/state.json` and `~/.boff/state.json` — and are therefore
independent by construction: a workspace clean cannot reach a user-level deploy, and neither can
reconcile the other's files away. The `scope` half of the key (`str(scope.kind)`) still
distinguishes them inside a file, for the case where a workspace root *is* the home directory.

For each owner it stores:

- `files`: targets boff wrote with `OVERWRITE` (rules, skills, agents, plugin files, mise config).
- `merged`: for each `MERGE` target, the leaf JSON key paths boff injected. `leaf_paths` mirrors
  the executor's deep-merge exactly (recurse into dicts; lists and scalars are owned wholesale),
  so the recorded provenance is precisely what boff would re-merge.

Beside the per-owner records, keyed by scope alone, sits `stacks`: the ordered manifest references
the last deploy was given. Schema version 2 added it; a version-1 file loads as a version-2 state
with no recorded stack, and `load_state` rejects a version it does not understand rather than
misreading it.

`reconcile(prior, forward_by_owner, active_owners, scope)` diffs the recorded state against the
new plan and returns `(cleanup_ops, next_state)`. For each active owner it emits a
`DeleteOperation` for every recorded file the new plan no longer produces, and a
`PruneKeysOperation` for the merge keys in `old_leaves - new_leaves`. Keys boff never recorded
are never pruned, so hand-authored config survives. Owners absent from `active_owners` (a
platform not in this `--platform` invocation) are carried into `next_state` untouched: cleanup
is strictly per owner.

The CLI orchestrates: load prior state, plan, reconcile, run forward ops before cleanup ops (so
merged files exist when keys are pruned), then persist `next_state`. The `.boff/` directory is
self-ignored from git via a generated `.boff/.gitignore`, since it records local install state.

Two clean-slate operations layer on top, exposed both standalone (`boff clean`) and as
`deploy` flags. `--clean` reconciles the entire recorded footprint against an empty plan (a
non-destructive purge of everything boff owns). `--wipe` deletes each targeted platform's
`native_roots(scope)` wholesale — destructive, including non-boff content — and is guarded by an
unconditional interactive confirmation prompt. Both treat the cleared owners as empty when
reconciling the forward pass, so their footprint is not removed twice.

This keeps the executor dumb and planning pure: state lives in its own module, reconciliation is
a pure diff producing ordinary `Operation` values, and only the CLI ties load/plan/reconcile/save
together.

### Why the stack is a list of references, not per-bundle ownership

`boff deploy` takes several manifests, and `--add` / `--remove` change what is deployed without
restating it. The obvious implementation — record *which bundle* wrote each file, then install or
reclaim one bundle's footprint in isolation — was rejected. It would put a third component in the
state key, make every merged JSON leaf jointly owned by however many bundles produced it, and,
worst, break the authoritative-deploy invariant: two bundles could each hold a stale opinion about
the same file with no single answer to "what should be here".

Recording the *reference list* instead keeps the invariant intact. `--add` and `--remove` are list
arithmetic over `stacks[scope]`; the result is loaded, merged into one `Manifest`, and deployed by
the unchanged path. Everything downstream of `cli._load_stack` — `plan_deploy`, `reconcile`, the
executor, the `.gitignore` block — still sees exactly one manifest and one authoritative plan, so
adding a bundle is not a partial install: it is a full re-deploy of a longer stack.

The cost is that a recorded reference must still resolve when it is next used, which is a
deliberate trade: a Git reference re-fetches (picking up upstream changes, as a redeploy should)
and a relative path resolves against the deploy directory (which is always cwd, so it stays
valid). Storing resolved paths instead would pin a Git bundle to a stale cache checkout.

---

## Verification (`boff check`)

Deploy writes; nothing read the workspace back. A user who hand-edits `.claude/settings.json`,
deletes a rule, or edits `boff.yaml` without redeploying has a workspace that silently no longer
matches its manifest. `boff check` (`src/boff/verify.py`) closes that loop: it re-plans the deploy
with `plan_units`, compares the plan against disk, and reports per artifact. It is strictly
read-only — no `save_state`, no `update_workspace_gitignore`, no file writes.

Three decisions shape it.

**A folded expectation per target, not per operation.** Several ops write one file:
`.claude/settings.json` receives one `MERGE` op each from permissions, settings, and event hooks.
Comparing each op's content against the file in isolation reports drift the moment a later op
overrides an earlier op's key. So `fold_expectations` replays the flattened op list, per target,
with `executor._apply_file`'s semantics, producing one of two shapes:

- `_Exact(content, writer)` — the target's content is fully determined by the plan, because some
  op `OVERWRITE`s it. An `OVERWRITE` destroys whatever preceded it, so only the ops from the *last*
  `OVERWRITE` onward matter. Compare the whole file.
- `_Owned(value)` — the target only ever sees `MERGE`s: it is a shared file. boff owns the leaf key
  paths it wrote (`state.leaf_paths`, the same function the state record uses) and nothing else.
  Compare those paths; a key the user added is invisible, and a key boff no longer writes is the
  business of `reconcile`, not of drift.

**`ops == []` does not mean the platform dropped the artifact.** `ClaudeAdapter._event_hooks`
returns `[]` when every hook is scoped to opencode; the shared `_settings` returns `[]` for an
empty settings block. Nothing is wrong in either case. A deliberate drop must therefore be
*declared*, not inferred: `@renders(SlashCommand, drops=True)` marks a renderer whose only job is to
warn. `supports()` still returns True, so deploy behaves exactly as before; `check` reads the flag.
A drop can also be scope-specific — `drops={ScopeKind.GLOBAL}` — since a surface may exist at one
level and not the other (see [Scope](#scope-workspace-and-user-level)).

**`Support` is four-valued, not two.** `Rule` and `Rules` are two shapes of the same manifest
section: Claude and OpenCode read a rules directory, Antigravity reads a single instructions file,
and each adapter renders exactly one shape. Treating "no renderer" as `UNSUPPORTED` would emit a
bogus finding per rule on Antigravity, and one for `Rules` on the other two. So `EQUIVALENT_SHAPES`
(in `artifacts/__init__.py`) names the pairing, and `_support` distinguishes:

| Verdict | Meaning | Reported? |
|---|---|---|
| `RENDERED` | The adapter renders this type | per-op findings |
| `DROPPED` | The adapter declared a warn-and-skip renderer | one `dropped` finding |
| `SHADOWED` | The adapter renders an equivalent shape instead | nothing |
| `UNSUPPORTED` | The adapter has no renderer at all | one `unsupported` finding |

`SHADOWED` suppresses noise without suppressing signal: a future platform that renders *neither*
shape still gets a genuine `UNSUPPORTED`. The pairing also codifies `Rules`' docstring
mechanically, via a test parametrized over every registered adapter.

Stale footprint reuses `reconcile`, once per active owner so each finding keeps its attribution,
and discards the `next_state`. Its cleanup ops are then filtered by on-disk reality: the executor
no-ops on an already-absent delete target or an already-pruned key, so reporting those would make
`check` fail on a workspace where nothing is actually wrong.

`Status.MISSING`, `DRIFTED`, and `STALE` are the `FAILING` set and drive exit code `1`. A malformed
JSON file is reported as `drifted`, deliberately *not* raised as a `BoffError`: `check` describes
the workspace, it does not abort on it.

---

## Closed vs. open string sets

**Use `StrEnum` for closed sets** (finite, fixed valid values):

- `MergeStrategy`: `OVERWRITE | MERGE`
- `ScopeKind`: `WORKSPACE | GLOBAL`
- `HookPhase`: `PRE_INSTALL | POST_INSTALL`

`StrEnum` gives autocomplete, static-analysis support, and typo prevention while remaining
JSON-serializable as a plain string.

**Use plain `str` for open sets** (extensible via registry):

- Platform names: `"claude"`, `"opencode"`, `"antigravity"`, and any future platform.
- Plugin source names: `"local"`, and any future source.
- Tool installer names: `"mise"`, and any future installer.

Closing these to an enum would break the modularity guarantee: adding a new platform would
require editing the enum in `types.py`, touching a core shared file instead of an isolated
registry module.

---

## Hooks

Hooks are Python scripts in the manifest's `hooks/` directory. The CLI runs `pre_install`
hooks before `execute()` and `post_install` hooks after it.

Each hook receives a `HookContext` value encoded as JSON on stdin. A hook imports
`boff.types.HookContext` and calls `HookContext.from_stdin()` to decode it. The hook exits 0
on success or non-zero to abort the run.

The hook runner (`src/boff/hooks.py`) launches each script with `subprocess.run(...,
check=True)`, using the same Python interpreter as the `boff` process itself. The manifest
root is the working directory, so hooks can reference manifest-relative paths without
qualification.

Hooks do not run in `--dry-run` mode.

---

## The context subsystem

The context subsystem (`src/boff/context/`) handles three operations: export a project's
context to a portable bundle, import a bundle onto a machine, and migrate a bundle from one
platform's format to another.

### The intermediate representation

`ContextBundle` is a platform-neutral representation of everything bun-off knows how to move:
instructions (the primary doc and rules), plans, memory, todos, session transcripts, and a
digest summary. The bundle records the source platform and a `ProjectIdentity` used for
re-keying on import.

`ProjectIdentity` contains:

- `abs_path`: the absolute filesystem path on the source machine.
- `git_root_commit`: the repository's root-commit hash, stable across clones and machines.
- `remote`: the git remote URL, for identification only.
- `branch`: the current branch.

Claude Code keys its project state by `abs_path`. OpenCode keys it by `git_root_commit`. Both
fields travel in the bundle so either platform can look up its stored sessions on the target
machine.

### Context and memory across platforms

Two layers are easy to conflate. *Context* is the ephemeral per-session context
window the assistant reasons over: it is not durable and not portable, so bun-off
does not move it. *Memory* is the durable on-disk state that seeds future sessions:
this is what a bundle carries.

The supported platforms expose the durable layer differently:

- **Claude Code** has both author-written instructions (`CLAUDE.md` and
  `.claude/rules/`) and an auto-memory store it writes itself, under
  `~/.claude/projects/<encoded-path>/memory/` and indexed by `MEMORY.md`.
- **OpenCode** has the instructions half only (`AGENTS.md`, the global
  `~/.config/opencode/AGENTS.md`, and `opencode.json` `instructions`). It has no
  native persistent auto-memory, so its provider sets `supports_memory = False`.
- **Antigravity CLI** has the instructions half only, and only as a single file: it reads
  no rules directory. Its conversations live in a SQLite store with no export command, so its
  provider sets `supports_memory = False` and collects no sessions.

### One writer per file

Deploy and context are separate subsystems that write the same workspace. They must never
claim the same path, or the authoritative deploy will discard context that only a bundle
carries — a migrated handoff digest and the source platform's folded-in auto-memory are not
derivable from the manifest, so overwriting them loses them for good.

On Claude and OpenCode this falls out for free: the adapter writes a rules *directory*
(`.claude/rules/`), while the provider writes the primary file (`CLAUDE.md`) and a handoff
rule beside the rules. Antigravity breaks the coincidence, because it reads rules only from
an instructions file, so the adapter must write one.

Antigravity loads **and merges** two instructions files, and that is what resolves the
conflict. The adapter keeps `PlatformLayout.primary_filename` (`GEMINI.md`); the provider
overrides `ContextProvider.primary_filename` to `AGENTS.md`:

```python
class ContextProvider:
    @property
    def primary_filename(self) -> str:
        return self.layout.primary_filename       # claude, opencode
```

Making it a property defaulting to the layout, rather than a second `PlatformLayout` field,
keeps "one layout, one place" intact: only a provider whose adapter already owns the layout's
primary file overrides it. `GEMINI.md` is then collected as a `config` doc, which `migrate`
drops — its content is generated from the manifest, so it is reproduced by deploying, not by
migrating.

A regression test pins the invariant across every registered platform. Note it cannot assert
that deploy's and migrate's targets are disjoint: migrate deliberately rewrites rule files
deploy also owns, since both derive from the same manifest, and OpenCode's migrate merges into
`opencode.json` exactly as deploy does. The narrower, true invariant is that *the doc carrying
the handoff digest never lands on a target deploy would `OVERWRITE`*.

The IR keeps these two kinds distinct: `InstructionsDoc` carries the primary doc and
rules, while `MemoryDoc` carries auto-memory files. A bundle's `memory` list is
therefore populated for Claude and empty for the others. The
`ContextProvider.supports_memory` flag drives the migration fold-in described in
"Migration" step 4 below: when the target cannot store memory natively, the source's
`MemoryDoc` files move into the handoff rule instead of a memory directory.

Capture is workspace-scoped. A provider collects the project's primary doc, its
rules subdirectory, and (for Claude) the per-project auto-memory directory. The
project primary doc is captured as instructions, not as memory. User-scope and
managed-policy `CLAUDE.md` (such as `~/.claude/CLAUDE.md`) and any `@import` targets
outside the project root are not included.

### ContextProvider

Each platform implements a `ContextProvider` subclass with two methods:

- `collect(*, scope, full)`: reads the platform's on-disk state and returns a `ContextBundle`.
- `materialize(bundle, *, scope)`: plans the operations needed to write a bundle back.

`collect()` populates `summary` from a `DigestFacts` scan of session transcripts (user goals,
recently edited files, referenced plan files, todo items). This digest is the handoff document
used by `migrate()`.

The `full` flag controls whether raw session transcripts are included. For Claude Code, full
export includes `.jsonl` transcript files. For OpenCode, full export calls `opencode export
<session-id>` for each session; if the `opencode` CLI is not on `PATH`, sessions are silently
skipped.

`materialize()` returns operations, not a side-effecting call. The CLI passes the list to
`execute()`.

Providers share the same `PlatformLayout` (`platform_layout.py`) as the adapters, so
`primary_filename`, `rules_subdir`, and the config file live in one place. The base class owns
the layout-driven shared logic — `_collect_instructions` (primary file, an overridable
`_config_docs` hook, then the rules glob), `_instruction_op`, and the `_rewrite_session`
path re-keying — so each provider carries only its platform-specific storage handling.

Files: `src/boff/platform_layout.py`, `src/boff/context/base.py`,
`src/boff/context/claude.py`, `src/boff/context/opencode.py`.

### Claude Code path encoding

Claude Code names its per-project directory by replacing every non-alphanumeric character in
the absolute path with a dash: `/home/caf/myproject` becomes `-home-caf-myproject`. The
function `encode_project_dir(abs_path)` in `src/boff/context/claude.py` must match Claude's
own encoding exactly, because an import to the wrong directory means no session resume.

### Bundle serialization

`write_bundle` and `read_bundle` in `src/boff/context/bundle.py` serialize `ContextBundle` as
a gzipped tar archive. The archive is human-inspectable: content blobs live in named
directories (`instructions/`, `plans/`, `memory/`, etc.) and a `manifest.json` at the root
indexes them.

The `--sanitize` flag applies regex-based redaction before writing. It targets: OpenAI and
Anthropic API key patterns, GitHub tokens, AWS access keys, bearer tokens, and common
`key = value` assignments containing secrets. It is best-effort — it covers known patterns but
is not a comprehensive secret scanner.

### Migration

`migrate(source_bundle, target_provider)` in `src/boff/context/migrate.py` reshapes a bundle
for the target platform:

1. Renames the primary document to `target_provider.primary_filename` (e.g. `CLAUDE.md`
   becomes `AGENTS.md`).

2. Re-roots rule paths from the source platform's rules subdirectory to the target's. The path
   tail after `rules/` is preserved, so category subdirectories survive the migration.

3. Drops platform-specific config documents (kind `"config"`) — they are not portable.

4. Creates a `handoff/migrated-context.md` rule in the target's rules directory. This document
   carries the session digest and, when the target platform does not support persistent memory
   (`supports_memory = False`), the source platform's memory files.

5. Carries plans over verbatim.

Migration is stateless and deterministic. It does not call any LLM and does not require the
source platform to be installed on the current machine.

---

## Manifest parsing

`load_manifest(path)` in `src/boff/manifest.py` reads `<path>/boff.yaml` and resolves every
artifact to concrete files on disk. A missing `boff.yaml` raises `ManifestError`, and a missing
artifact file raises `FileNotFoundError`. There is no partial-load or warning mode: a missing
file is always an error.

The parsed `Manifest` dataclass is frozen and fully resolved: all paths are absolute, all
content is loaded into memory. Downstream code does not re-read files. The required `meta:`
block (`ManifestMeta`: `name` + `description` mandatory, the rest optional) is parsed alongside
the artifacts; it is informational only and is excluded from `iter_artifacts`.

### The untyped-payload boundary

`yaml.safe_load` and `json.loads` return `Any`, and narrowing that with `isinstance` yields an
unparameterized container: the element types stay unknown. Rather than scatter that unknown
through every validator, the decoded payload becomes typed at one boundary and stays typed
afterwards. `manifest.py` owns `_as_mapping` / `_require_mapping` / `_require_list` /
`_str_list` / `_str_map` for YAML, and `jsonutil.py` owns `as_json_object` / `as_json_array`
for JSON. Each checks the shape it promises and casts once. They hold the only `cast` calls in
the package, which is what lets the rest of it pass pyright in strict mode.

### Inheritance: `extends` and the manifest-source registry

`load_manifest` resolves inheritance in three steps: parse the folder's own artifacts into a
`Manifest` (`_load_own`, which records but does not resolve `extends`); resolve each `extends`
reference to a local directory and recursively load it; then merge the parents under the child.
A `_seen` set of resolved roots threads through the recursion to detect cycles.

References are resolved by a small registry mirroring the plugin-source and tool-installer
registries. `ManifestSource` (`src/boff/manifest_sources/base.py`) declares `matches(ref)` and
`resolve(ref, base_root) -> Path`; `resolve_ref` tries each registered source in order and the
first match wins. `LocalManifestSource` is the catch-all (any path relative to `base_root`);
`GitManifestSource` matches every remote URL and clones/fetches into `$XDG_CACHE_HOME/boff/git/`.
Resolution is a load-time concern, so the git source shells out to `git` directly rather than
emitting deploy `Operation`s.

**One checkout per commit.** Each repository has one clone, used only to fetch, and every commit a
reference resolves to gets its own worktree beside it (`<clone>-commits/<sha>/`). A single shared
checkout looks sufficient and is not: three manifest fields keep *paths* into the cache and read
them only after the whole stack has loaded (`pre_install`/`post_install` scripts at run time,
`mise:` files and `source: local` plugin folders at plan time). With one checkout, resolving a
second ref of the same repository moved those paths under the first manifest, so it ran the other
ref's hooks or failed on files that ref lacks. That is reachable without anything exotic: a bundle
repository tagging each bundle separately, and a stack naming two of its bundles at their own tags.
A commit's directory never changes content, so the paths stay right for the whole run. Resolving
a branch through `origin/<branch>` keeps it following the remote tip, and a reused checkout is
`reset --hard` once per run, so an edit inside the cache does not persist. The cost is disk: one
checkout per commit ever used, which accumulates as pins move. The cache is safe to delete.

**The CLI's manifest arguments go through `resolve_ref` too** (`cli._load`, with
`base_root=Path.cwd()`), so `boff deploy` and `boff check` accept exactly what `extends:` accepts.
A reference selects the manifest; the deploy scope stays the current directory either way. Before
this, the CLI called `load_manifest` on a `Path` built from the raw argument, so a URL silently
became a nonexistent local path.

Splitting a URL into repository, subdirectory, and ref is guesswork, because no forge marks the
boundary in a browser URL. `parse_git_ref` takes the first rule that applies: a segment ending in
`.git` (host-agnostic, and the only form that reaches a repository nested deeper than
`<owner>/<repo>`, such as a GitLab subgroup or a `file://` path); a `tree`/`blob` marker segment,
which also names the ref; or the `<owner>/<repo>` convention, which needs a host and so never
applies to `file://`. An explicit `@<ref>` always wins over a marker's ref, since a browser URL
cannot express a branch name containing a slash. Refusing to guess is a real outcome: a URL that
matches no rule raises rather than cloning something arbitrary.

`merge_manifests(parents, child)` (`src/boff/merge.py`) merges fully-resolved `Manifest`
objects, not raw YAML: each artifact already carries content read from its own root, so the
merge never tracks per-artifact source roots. Last definition wins: named artifacts override by
`name` (warning per shadow), permission rules and tool-installer file lists (`mise:`)
concatenate parent-first with de-duplication, lifecycle-hook scripts merge by name (last definer
wins, each carried as an absolute path resolved against its defining manifest so an inherited
hook runs from its own bundle), `settings` deep-merge per
platform via `_json_deep_merge`, and `meta` stays the child's own (identity is not inherited).

**A command-line stack is the same merge.** `cli._load_stack` loads each reference given to
`boff deploy` / `boff check` and calls `merge_manifests(members[:-1], members[-1])`, so the last
argument plays the child's role and the ordering rule a user learns for `extends:` transfers
unchanged. Only the single-manifest case is special: it returns the member *unmerged*, because
`merge_manifests` is not the identity on one manifest (it recomputes `Settings.available_on` and
rebuilds `EventHooks`), and an ordinary one-manifest deploy must not change behavior.

The shadow warning goes to `console`, not the `logging` module the adapters use for warn-and-skip.
That is a deliberate exception: boff configures no logging handler, so every shadow was previously
swallowed, and a collision between two manifests a user just combined on one command line is both
surprising and actionable. Adapter warnings stay on `logging`, where a warn-and-skip on a platform
that structurally cannot express an artifact is expected rather than newsworthy.

> Security: resolving a remote `extends:` runs `git` against an author-declared URL. This is
> trusted by construction (the author wrote the reference into their own manifest) but means a
> manifest can trigger network access at load time.

---

## Adding a new entry to each registry

| What to add | Files to create | Registration |
|---|---|---|
| New platform | `src/boff/adapters/<name>.py` + a `PlatformLayout` in `src/boff/platform_layout.py` (with a `global_factory`, or None if the platform has no user-level config) | One line in `src/boff/adapters/__init__.py` |
| New context provider | `src/boff/context/<name>.py` | One line in `src/boff/context/__init__.py` |
| New plugin source | `src/boff/sources/<name>.py` | One line in `src/boff/sources/__init__.py` |
| New manifest source | `src/boff/manifest_sources/<name>.py` | One line in `src/boff/manifest_sources/__init__.py` (`_SOURCES`, before the local catch-all) |
| New tool installer | `src/boff/tool_installers/<name>.py` | One line in `src/boff/tool_installers/__init__.py` |
| New artifact type | `src/boff/artifacts/<name>.py` | `@renders(<Type>)` method per supporting adapter + manifest schema field + a `_merge_named` line in `merge.py` + an arm in `deploy.artifact_label` and in the `Artifact` union |

The registry keeps `cli.py`, `deploy.py`, `state.py`, and `manifest.py` untouched: `--platform`
has no `choices=` list, validation defers to `get_adapter`, and state records owners as opaque
strings.

A new platform owes `boff check` three things beyond its adapter:

- **`PlatformLayout.binary`**: the CLI's executable name on `PATH`. It is not derivable from
  `name` (Antigravity's is `agy`), and `check` probes it before verifying.
- **`@renders(<Type>, drops=True)`**, or `drops={ScopeKind.GLOBAL}` for a scope-specific drop,
  on any renderer that exists only to warn, so `check` can tell
  a deliberate drop apart from a renderer that had nothing to emit.
- **`EQUIVALENT_SHAPES`** in `artifacts/__init__.py`, if the platform reads an existing artifact
  section through a different shape. Rendering more than one member of a group deploys the same
  content twice; a test parametrized over every adapter enforces that.

Three tables outside the registries are keyed by platform name, and a new platform must be
considered against each. This is deliberate: they encode *translations* between the neutral
model and each platform's vocabulary, which is exactly the knowledge a registry cannot infer.

| Table | Purpose | Omitting an entry means |
|---|---|---|
| `EVENT_MAP` in `artifacts/event_hooks.py` | Normalized event > native hook, per platform | The event has no equivalent there; the adapter warns and skips (Antigravity's `session_start`/`session_end`) |
| `RESERVED_KEYS` in `artifacts/settings.py` | Settings keys a dedicated artifact already owns | No `settings:` passthrough is rejected for that platform (correct only when it has no settings target, as Antigravity does not) |
| `@installs_for(<platform>)` in `sources/local.py` | Which platforms a plugin source can install to (currently all three: claude, opencode, antigravity) | A plugin `install:` block naming an unregistered platform raises |

Each adapter also owns its own canonical-tool mapping (`_CLAUDE_TOOL`, `_OC_TOOL`), projecting
`CANONICAL_TOOLS` onto the platform's tool names. A platform with no permission surface, like
Antigravity, needs no such table.
