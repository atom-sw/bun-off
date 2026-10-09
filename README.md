# 🚉 Bun Off

[![PyPI](https://img.shields.io/pypi/v/bun-off.svg)](https://pypi.org/project/bun-off/)
[![Python versions](https://img.shields.io/pypi/pyversions/bun-off.svg)](https://pypi.org/project/bun-off/)
[![CI](https://github.com/atom-sw/bun-off/actions/workflows/ci.yml/badge.svg)](https://github.com/atom-sw/bun-off/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/bun-off.svg)](LICENSE)

**Bun Off** (short for "bundle off") configures AI coding-assistant
stacks — rules, MCP servers, skills, slash commands, plugins, and
hooks — from a single `boff.yaml` manifest directly to each platform's
native config locations.

**Package:** `boff` (installed as `bun-off`) | **Python:** ≥ 3.12 | **License:** GPL-3.0-or-later


## 📦 Installation

Bun Off is a command-line tool, so install it into its own isolated environment:

```bash
uv tool install bun-off
```

The `boff` command becomes available after installation. Check it with:

```bash
boff --version
```

### Requirements

- **Python ≥ 3.12.**
- **A `git` binary on `PATH`**, for manifests that resolve a Git reference and for
  `boff context` (which reads the workspace's Git state). Everything else runs without it.

### Ready-made bundles

Looking for a stack to deploy right away? The companion repo
[bun-off-bundles](https://github.com/atom-sw/bun-off-bundles) ships ready-made `general-dev`
(language-agnostic rules and MCP servers) and `python` (uv, ruff, pytest, serena) bundles.
Deploy one into the current directory by its URL, with nothing to clone first:

```bash
boff deploy https://github.com/atom-sw/bun-off-bundles/tree/main/python --platform claude
```


## 🗂️ Manifest layout

A manifest is a **folder** containing `boff.yaml` and one subdirectory per artifact type:

```
my-stack/
  boff.yaml
  rules/            # one <name>.md per listed rule
  skills/           # one <name>.md per listed skill, or a <name>/ folder holding SKILL.md
  slash_commands/   # one <name>.md per listed command
  output_styles/    # one <name>.md per listed output style (Claude Code only)
  agents/           # one <name>.md per listed agent (system-prompt body)
  mcp_servers/
    raw/
      claude/       # <name>.json for each server on Claude
      opencode/     # <name>.json for each server on OpenCode
      antigravity/  # <name>.json for each server on Antigravity CLI
  plugins/          # subtrees referenced by plugin install specs
  mise/             # mise.toml files listed under mise:
  hooks/            # <name>.py per listed lifecycle hook
  event_hooks/      # <name> shell script per listed event hook using script:
```

Settings (`settings:`) and event hooks (`event_hooks:`) are declared inline in `boff.yaml`;
only event hooks that reference a `script:` file need the `event_hooks/` directory.

### `boff.yaml` reference

```yaml
meta:                          # required: documentation describing this manifest
                               # (name + description are themselves required)
  name: my-stack
  description: Team Python stack.
  long_description: |          # optional free-form markdown
    Rules, skills, and MCP servers shared across the team's Python services.
  version: "1.2.0"             # optional
  author: Platform Team        # optional
  homepage: https://example.com/stacks/python   # optional

extends: ../base-stack         # optional: inherit from one or more parent manifests
                               # (a single reference, or a list; later parents win, child wins over all)

rules:
  - style                      # short form: name only
  - name: lint
    category: python-dev       # optional: groups rules under a subdirectory
    globs: ["**/*.py"]         # optional: scope the rule to matching files (Claude only)
    available_on: [claude]     # optional: restrict to specific platforms

skills:
  - format
  - name: lean-proof           # fetched from a Git repository (see "Skills and rules from Git")
    from: https://github.com/leanprover/skills/tree/7d3da02/skills

slash_commands:
  - refactor

output_styles:
  - tutor

agents:
  - name: reviewer
    description: Read-only code reviewer
    model: haiku                 # string: same value on every platform
  - name: planner
    description: Deep planner
    model:                       # map: a distinct model per platform
      claude: opus
      opencode: anthropic/claude-opus-4-8
    permissions:
      allow: [{ tool: read }, { tool: grep }]
      deny: [{ tool: edit }, { tool: bash }]

mcp_servers:
  - context7
  - name: tldr
    available_on: [claude]

permissions:                   # tool-use rules, grouped by verdict
  allow:
    - { tool: read, pattern: "./src/**" }
  ask:
    - { tool: bash, pattern: "git push *" }
  deny:
    - { tool: mcp, pattern: "secret__*", available_on: [claude] }

plugins:
  my-plugin:
    install:
      claude:
        source: local
        path: plugins/my-plugin   # path relative to the manifest root

mise:
  - mise/mise.toml

settings:                      # verbatim per-platform settings, deep-merged natively
  claude:
    outputStyle: Explanatory
  opencode:
    theme: tokyonight

event_hooks:
  - name: lint
    event: after_edit          # normalized event: after_edit | after_bash | on_finish
                               #                 | session_start | session_end
    script: lint               # shell script at event_hooks/lint
  - name: notify
    event: on_finish
    command: "notify-send 'done'"   # inline shell, no script file

hooks:
  pre_install:
    - script: pre_hook
  post_install:
    - script: post_hook
```


## 🏷️ Manifest metadata

Every manifest carries a `meta:` block documenting it or the bundle it
defines. `name` and `description` are required, and a manifest missing
either fails to load; `long_description`, `version`, `author`, and
`homepage` are optional. Metadata is informational: it produces no
deploy operations and is never inherited through `extends:`. `boff deploy`
prints the metadata header (and the resolved `extends` chain) above
its operation summary, so `boff deploy --dry-run` doubles as a
validate-and-inspect command:

```bash
boff deploy ./ai-cannot-code-stack --platform claude --dry-run
# ai-cannot-code-stack v1.2.0
#   Python development tools.
#   author: The Anti-Automation League
#   extends: ../claude-poweruser-stack
# planned 12 operations:
#   ...
```


## 🧬 Combining manifests

Two ways to combine manifests, one set of merge rules:

- **`extends:`** — the manifest's author combines it with its parents. The combination ships with
  the manifest and applies wherever it is deployed.
- **A stack on the command line** — the person deploying combines manifests, without authoring a
  wrapper manifest: `boff deploy ../base-stack ./my-rules --platform claude`. See
  [Deploying a stack of manifests](#-deploying-a-stack-of-manifests).

Both resolve references the same way and merge with the same [last-wins
rules](#merge-semantics).

### Extending manifests

A manifest can build on one or more parents with `extends:`. Each reference resolves to another
manifest folder, which Bun Off loads and merges underneath the current one. `extends:` accepts a
single reference or an ordered list:

```yaml
extends: ../base-stack                 # one parent

extends:                               # several parents (mixins)
  - ../base-stack
  - ../python-preset
```

### Reference scheme

A manifest reference names either a local folder or a Git repository. The same scheme applies
wherever Bun Off takes a manifest: an `extends:` parent, and the `<manifest>` arguments of
[`boff deploy`](#boff-deploy) and [`boff check`](#boff-check).

| Form | Example | Resolves to |
|---|---|---|
| Local path | `../base-stack`, `/abs/path/stack` | A folder relative to the extending manifest (for a CLI argument: to the current directory), or absolute. |
| Git URL | `https://host/org/repo.git/sub/dir@v1.2.0` | The `sub/dir` subtree of `repo`, checked out at ref `v1.2.0`. |
| Forge URL | `https://github.com/org/repo/sub/dir@v1.2.0` | The same, with the `.git` left off. |
| Browser URL | `https://github.com/org/repo/tree/v1.2.0/sub/dir` | The same, pasted straight from the address bar. |
| Repository root | `https://github.com/org/repo` | The `boff.yaml` at the top of `repo`, on the default branch. |

Any URL is a Git reference; anything else is a local path. Bun Off finds where the repository
URL ends in three steps, taking the first that applies:

1. A path segment ending in `.git` ends it. This is the only form that works on every host, and
   the only one that can reach a repository nested below `<owner>/<repo>`, such as a GitLab
   subgroup or a `file://` path.
2. A browser URL's `tree` or `blob` segment (GitLab's `/-/tree/`) ends it, and the segment after
   it is the Git ref.
3. Otherwise the first two segments are `<owner>/<repo>`, the convention every forge follows.

The `/<subdir>` and the trailing `@<ref>` are both optional, and `@<ref>` defaults to the remote's
default branch. Spell a ref containing a slash (e.g., `writing/0.1.0`) with `@<ref>`. A ref containing
an `@` is not supported at all: Bun Off splits the reference on its first `@`.

Bun Off fetches into a cache under `$XDG_CACHE_HOME/boff/git/` (or `~/.cache/boff/git/`) and
reuses it on later runs. Every spelling of one repository shares a single clone, and each commit
you reference gets its own checkout beside it, so a stack can combine bundles from one repository
at different tags. Checkouts of commits you no longer reference stay on disk; deleting the whole
cache directory is safe, and Bun Off fetches again on the next run.

### Merge semantics

**Last definition wins.** For `extends:`, parents merge in declaration order and the child
overrides them all. For a command-line stack, manifests merge left to right, so the last argument
wins. The rules are identical either way:

| Section | How it merges |
|---|---|
| `rules`, `skills`, `slash_commands`, `output_styles`, `mcp_servers`, `agents`, `plugins` | By `name`: a later definition replaces an earlier one. Bun Off prints a warning for each override. |
| `event_hooks` | By `name`, same as above. |
| `permissions` | Rule lists concatenate; identical rules are de-duplicated. |
| `settings` | Deep-merged per platform; the later block wins on conflicting keys, earlier-only keys survive. |
| `mise` and other tool files | By tool: the file lists are concatenated in merge order and de-duplicated by path. |
| `hooks.pre_install` / `hooks.post_install` | By script name; concatenated in merge order. An inherited hook still runs from the bundle that declared it. |
| `meta` | Not inherited: for `extends:`, the child's own metadata is kept; for a stack, the last manifest's. |

Every name collision prints a warning naming the section, the name, and the manifest that won:

```
⚠ rules 'style' from /home/me/stacks/my-rules overrides an earlier definition
```

That is not an error — overriding an inherited rule is the point of `extends:`, and picking one of
two competing definitions is the point of a stack. The warning is there so a collision you did not
intend cannot pass unnoticed.

Inheritance cycles (a manifest that extends itself directly or transitively) raise an error.

> ⚠️ Resolving a remote reference runs `git` against the referenced URL, and deploying a manifest
> runs its `pre_install` and `post_install` hooks. Only extend and deploy manifests you trust, and
> only fetch [skills and rules](#skills-and-rules-from-git) from repositories you trust.


## 📄 Artifact types

### Rules

Rules are markdown files that provide persistent instructions to the AI assistant. Each rule
name resolves to `rules/<name>.md` in the manifest folder.

The optional `category:` field places the rule file under a named subdirectory on disk:

| Platform | Path without category | Path with `category: python-dev` |
|---|---|---|
| Claude Code | `.claude/rules/<name>.md` | `.claude/rules/python-dev/<name>.md` |
| OpenCode | `.opencode/rules/<name>.md` | `.opencode/rules/python-dev/<name>.md` |
| Antigravity CLI | all rules inlined into `GEMINI.md` | `category:` is ignored |

OpenCode also receives an `instructions` glob entry in `opencode.json` that makes it load all
rules from `.opencode/rules/**/*.md`.

Antigravity CLI reads rules only from its primary instructions file: it never loads
`.agents/rules/*.md`, and it does not expand `@`-includes. Bun Off therefore concatenates every
rule into a generated `GEMINI.md`, one `## <name>` section per rule, in manifest order. It writes
`GEMINI.md` rather than `AGENTS.md` for two reasons: `AGENTS.md` is also OpenCode's primary
instructions file, so deploying both platforms into one workspace would inject every rule twice,
and `AGENTS.md` is commonly hand-authored. Antigravity loads both and merges them, so your own
`AGENTS.md` keeps working alongside the generated `GEMINI.md`.

`boff deploy` never writes `AGENTS.md`. [`boff context`](#boff-context) does: it treats `AGENTS.md`
as Antigravity's instructions file, exactly as it treats `CLAUDE.md` on Claude. Splitting the two
files this way means a later `boff deploy` regenerates `GEMINI.md` without discarding migrated
context.

The optional `globs:` field scopes a rule so it applies only when the assistant works on matching
files, instead of loading unconditionally. It takes a list of glob patterns:

```yaml
rules:
  - name: csharp-style
    category: dotnet
    globs:
      - "**/*.cs"
      - "**/Controllers/**"
```

Platform support differs:

| Platform | Behavior |
|---|---|
| Claude Code | Bun Off prepends a `globs:` frontmatter block to the rule file (the key Claude Code honors: the documented `paths:` key is silently broken). The rule loads only when matching files are in play. |
| OpenCode | OpenCode has no conditional, path-scoped loading: its `instructions` globs only select which files to always load. Bun Off deploys the rule unscoped (as it does today) and logs a warning that the scope is not enforced. |
| Antigravity CLI | Rules are inlined into `GEMINI.md`, which loads wholesale. Bun Off deploys the rule unscoped and logs a warning that the scope is not enforced. |

Two caveats:

- Claude Code honors `globs:` only for workspace-level rules (`.claude/rules/`), not user-level
  rules. Indeed, Bun Off deploys to the workspace.
- `globs:` patterns are written verbatim into a double-quoted YAML string, so a pattern must not
  contain a double-quote character.

### Skills

Skills are markdown files that teach the assistant a repeatable workflow. A skill takes either
of two forms on disk, and Bun Off picks the one it finds:

```
skills/
  quick-fix.md              a skill that is just its own text
  cmon-monitor/             a skill that ships supporting files
    SKILL.md                the entry point; the name is fixed
    references/contract.md  loaded only when the skill needs it
    templates/monitor.py    a file the assistant copies
```

| Platform | Path |
|---|---|
| Claude Code | `.claude/skills/<name>/SKILL.md` |
| OpenCode | `.opencode/skills/<name>/SKILL.md` |
| Antigravity CLI | `.agents/skills/<name>/SKILL.md` |

**Every skill needs `name` and `description` frontmatter**, in either form. That is how each
platform discovers the skill, and `description` is what the model reads to decide whether to
activate it:

```markdown
---
name: cmon-monitor
description: Use when the user asks to add or change a production monitor.
---

# CMON monitor
```

Bun Off ships the body verbatim and rejects a skill that declares neither key, or a blank
`description`, when it loads the manifest. This is an error rather than a warning because such
a skill is not degraded but inert: every platform skips the file without complaint, so a deploy
would otherwise report success over a skill that never loads. If the frontmatter `name`
disagrees with the name in `boff.yaml`, Bun Off warns and deploys under the manifest's name.

Both forms are listed the same way, by name:

```yaml
skills:
  - quick-fix
  - cmon-monitor
```

Everything under a skill folder is deployed verbatim beside its `SKILL.md`, keeping its own
layout, so `.claude/skills/cmon-monitor/references/contract.md` is where a relative link in
`SKILL.md` expects it. Supporting files are read by the assistant with its own file tools
rather than loaded by the platform, so they work identically on all three platforms.

Three things to know:

- Antigravity requires `name` and `description` frontmatter in every `SKILL.md`: it reads the
  description to decide whether to activate the skill.
- Declaring a skill in both forms at once (`skills/<name>.md` *and* `skills/<name>/`) is an
  error rather than a silent preference for one, since it is almost always a leftover file.
- Build and editor droppings are skipped, so tooling run inside a skill folder does not end up
  in the deployed skill: the `__pycache__`, `.git`, `.pytest_cache`, `.ruff_cache`,
  `.mypy_cache`, and `.ipynb_checkpoints` directories at any depth, and the files `*.pyc`,
  `*.pyo`, `.DS_Store`, `Thumbs.db`, `*.swp`, `*.swo`, `*~`, `*.orig`, and `*.rej`. Everything
  else ships, so the author still decides what a skill carries.

Removing a supporting file from a bundle removes the deployed copy on the next `boff deploy`,
and prunes the directory if it empties. `boff check` verifies every supporting file, so an
edited template is reported as drift.

### Skills and rules from Git

A skill or rule entry can take its content from a Git repository instead of the manifest folder.
`from:` names a directory in the repository that stands in for the local `skills/` or `rules/`,
and Bun Off looks the entry's name up inside it exactly as it would locally: `<name>.md`, or a
`<name>/` folder holding `SKILL.md` for a skill.

```yaml
skills:
  - lean-lint                  # local: skills/lean-lint.md
  - name: lean-proof           # remote: skills/lean-proof/ in the repository, at 7d3da02
    from: &lean-fro https://github.com/leanprover/skills/tree/7d3da0282e7b724b07620e45cf212f2e05e19334/skills
  - { name: lean-mwe, from: *lean-fro }
rules:
  - { name: house-style, from: "https://github.com/org/conventions/rules@v2" }
```

A fetched skill or rule is an ordinary one from then on: it deploys to every platform, merges and
overrides by name, and `boff check` verifies it. This is how a bundle ships a skill collection
published in the cross-platform `SKILL.md` layout (such as the Lean FRO skills above) to OpenCode
and Antigravity as well as Claude Code, rather than through one platform's plugin marketplace.

- `from:` takes any Git URL in the [reference scheme](#reference-scheme), and must name a
  directory. A YAML anchor (`&lean-fro`, `*lean-fro`) saves repeating it across entries.
- Every entry is still listed by name: there is no "import the whole directory", so a bundle
  states exactly what it deploys.
- **Pin a tag or commit.** Without one, `from:` follows the default branch: every load needs the
  network, and `boff check` reports drift whenever upstream moves until you deploy again.
- Bun Off fetches each repository once per run, into the same cache as `extends:`, however many
  entries name it.
- The deployed name is the entry's `name`, which must match the remote file or folder. Frontmatter
  checks apply as for a local skill, so a remote skill whose frontmatter `name` differs from its
  folder name triggers the same warning.
- `from:` is for skills and rules only, and does not accept a local path.

### Slash commands

Slash commands are markdown files that define custom `/commands`. Each name resolves to
`slash_commands/<name>.md`.

| Platform | Path |
|---|---|
| Claude Code | `.claude/commands/<name>.md` |
| OpenCode | `.opencode/commands/<name>.md` |
| Antigravity CLI | not supported: warns and skips |

Antigravity CLI's slash commands are built in, and it discovers no author-supplied command
directory in the workspace. Scope your commands with `available_on: [claude, opencode]` to
silence the warning.

### Output styles

An output style changes the assistant's *system prompt*, not the context around it. Where a rule
adds guidance on top of the platform's built-in instructions, an output style **replaces** Claude
Code's software-engineering instructions unless the file opts to keep them. Each name resolves to
`output_styles/<name>.md`.

| Platform | Path |
|---|---|
| Claude Code | `.claude/output-styles/<name>.md` |
| OpenCode | not supported: warns and skips |
| Antigravity CLI | not supported: warns and skips |

The file ships **verbatim**, frontmatter included, so write it exactly as Claude Code expects:

```markdown
---
name: Tutor
description: Explain the plan before editing
keep-coding-instructions: true
---

Before each edit, state the plan in two sentences, then make the change.
```

Claude Code validates this frontmatter against a **strict** schema: only `name` (defaults to the
filename), `description`, and `keep-coding-instructions` (default `false`) are accepted, and any
other key makes the style fail to load. A fourth key, `force-for-plugin`, is reserved for
plugin-bundled styles; setting it in a bundle only produces a warning, so leave it out.

Defining a style does not activate it. Select one through the `settings:` block:

```yaml
output_styles:
  - tutor

settings:
  claude:
    outputStyle: Tutor        # the frontmatter `name`, not the filename
```

Two caveats worth knowing:

- Picking a style interactively through `/config` writes it to `.claude/settings.local.json`,
  which **overrides** the `.claude/settings.json` that Bun Off deploys. If a deployed
  `outputStyle` appears to have no effect, check for a local override.
- Built-in styles (`default`, `Proactive`, `Explanatory`, `Learning`) cannot be shadowed by a
  file of the same name.

**Neither other platform has this surface.** OpenCode's nearest analogue is a *primary agent*, but
an agent's prompt replaces the base system prompt outright rather than appending to it, so
`keep-coding-instructions: true` has no equivalent there. Bun Off does not translate silently.
Write the OpenCode side explicitly instead, and scope each artifact to its platform:

```yaml
output_styles:
  - { name: tutor, available_on: [claude] }

agents:
  - name: tutor
    description: Explain the plan before editing
    mode: primary
    available_on: [opencode]

settings:
  claude:   { outputStyle: Tutor }
  opencode: { default_agent: tutor }
```

Antigravity CLI has no output-style directory and no system-prompt override at all.

### MCP servers

MCP server entries inject per-platform verbatim JSON config into the platform's settings.
Each name requires a JSON file at `mcp_servers/raw/<platform>/<name>.json`. The content is the
raw server object exactly as the platform expects it (transport, command, args, env, etc.).

| Platform | Target file | Merge behaviour |
|---|---|---|
| Claude Code | `.mcp.json` | Deep-merged into `mcpServers` |
| OpenCode | `opencode.json` | Deep-merged into `mcp` |
| Antigravity CLI | `.agents/mcp_config.json` | Deep-merged into `mcpServers` |

Antigravity names a remote server's endpoint `serverUrl`: the legacy `url` and `httpUrl` keys
are not read.

Because MCP server config is platform-specific, you must supply a separate JSON file for each
platform the server targets.

### Permissions

The `permissions:` block states which tools the assistant may use, in platform-neutral terms.
Bun Off translates each rule into the target platform's native permission syntax. Rules are grouped
under three verdicts: `allow`, `ask`, and `deny`.

```yaml
permissions:
  allow:
    - { tool: read, pattern: "./src/**" }
    - { tool: webfetch, pattern: "github.com" }
  ask:
    - { tool: bash, pattern: "git push *" }
    - { tool: edit }
  deny:
    - { tool: bash, pattern: "curl *" }
    - { tool: mcp, pattern: "secret__*", available_on: [claude] }
```

Each rule needs a `tool`. The optional `pattern` narrows the rule to matching invocations, and
the optional `available_on` restricts it to specific platforms, exactly as on any other artifact.
A rule with no `pattern` covers every use of the tool.

Tool names are canonical, not platform-native. Bun Off accepts these fifteen:

`bash`, `read`, `edit`, `write`, `glob`, `grep`, `webfetch`, `websearch`, `agent`, `mcp`, `lsp`,
`skill`, `question`, `external_directory`, `doom_loop`

| Platform | Target file | Merged under |
|---|---|---|
| Claude Code | `.claude/settings.json` | `permissions`, split into `allow` / `ask` / `deny` lists |
| OpenCode | `opencode.json` | `permission`, keyed by tool |
| Antigravity CLI | not supported: warns and skips |  |

Antigravity is dropped in **both** scopes. It does keep user-level permissions, as an
allow/deny/ask triple, but the JSON key they live under is not established, and Bun Off will not
write a guess that the tool would silently ignore while `boff check` reported success.

**Not every tool exists on every platform, and a rule naming a tool the target cannot express is
an error, not a warning.** Scope such rules with `available_on:`. Claude rejects `lsp`, `skill`,
`question`, `external_directory`, and `doom_loop`; OpenCode rejects `mcp`. The error message names
the platform to scope the rule to.

Translation is otherwise mechanical, with three cases worth knowing:

- `agent` becomes `Task` on Claude and `task` on OpenCode.
- `webfetch` with a pattern becomes `WebFetch(domain:<pattern>)` on Claude, so write the pattern
  as a bare domain.
- `mcp` becomes `mcp__<pattern>` on Claude, or plain `mcp` when the rule has no pattern.
- `write` collapses into `edit` on OpenCode, which does not distinguish the two.

Where a tool carries several rules, Bun Off writes them to OpenCode ordered `allow`, then `ask`, then
`deny`. OpenCode applies the last match, so this ordering reproduces Claude's precedence, in which
a `deny` outranks an `ask` and an `ask` outranks an `allow`.

Per-agent permissions use this same rule shape inside an [agent](#agents) definition, and are
scoped to that subagent rather than the session.

### Settings

The `settings:` key carries a verbatim per-platform settings block that Bun Off deep-merges into
each platform's native settings file. Use it for any project-shareable setting Bun Off does not
model with a dedicated artifact: Claude's `outputStyle`, `env`, `cleanupPeriodDays`; OpenCode's
`theme`, `formatter`, `autoupdate`, and more.

```yaml
settings:
  claude:
    outputStyle: Explanatory
    cleanupPeriodDays: 14
  opencode:
    theme: tokyonight
```

| Platform | Target file | Merge behaviour |
|---|---|---|
| Claude Code | `.claude/settings.json` | Deep-merged at the top level |
| OpenCode | `opencode.json` | Deep-merged at the top level |
| Antigravity CLI | `~/.gemini/antigravity-cli/settings.json` (user level only) | Deep-merged at the top level |

Antigravity CLI has no workspace settings file at all: its settings live only in
`~/.gemini/antigravity-cli/settings.json`. A workspace deploy therefore warns and skips, because
writing that file on behalf of one project would affect every other. A
[`--global` deploy](#-scopes) does write it.

A settings block is a raw passthrough: Bun Off does not validate the values. To keep the modeled concepts
canonical, Bun Off rejects keys that a dedicated artifact already owns: `permissions` and
`mcpServers` on Claude; `permission`, `mcp`, and `instructions` on OpenCode. Configure those
through [`permissions:`](#permissions), [`mcp_servers:`](#mcp-servers), and [`rules:`](#rules)
instead.

### Agents

An agent is a focused subagent: a system-prompt body plus per-agent metadata. Each agent
needs a `name` and a `description`, and its prompt body resolves to `agents/<name>.md` in the
manifest folder. Bun Off deploys agents as markdown files with platform-native frontmatter:

| Platform | Target file |
|---|---|
| Claude Code | `.claude/agents/<name>.md` |
| OpenCode | `.opencode/agents/<name>.md` |
| Antigravity CLI | `.agents/agents/<name>.md` |

```yaml
agents:
  - name: reviewer
    description: Read-only code reviewer
    model: haiku
    mode: subagent               # OpenCode only; Claude ignores it
    permissions:
      allow: [{ tool: read }, { tool: grep }]
      deny: [{ tool: edit }, { tool: bash }]
```

**Portable models.** The optional `model` field takes either a string or a per-platform map.
A string applies verbatim to every platform; a map supplies a distinct model id per platform,
and a platform absent from the map inherits that platform's default model. This matters
because the platforms name models differently: Claude expects an alias (`haiku`, `opus`,
`sonnet`) or a full id (`claude-opus-4-8`), while OpenCode expects `provider/model-id`.

```yaml
agents:
  - name: planner
    description: Deep planner
    model:
      claude: opus
      opencode: anthropic/claude-opus-4-8
```

Bun Off applies no model-name translation: each value passes through to its platform verbatim, so
state the exact id each platform expects. Antigravity subagents always run on the parent's
model, so a `model` scoped to it is ignored with a warning.

**Per-agent permissions.** The optional `permissions` block scopes the agent's tool access,
using the same `allow`/`deny` rule shape as the top-level permissions artifact. Claude scopes
agent tools only coarsely: it accepts `allow`/`deny` verdicts for known tools but rejects a
per-agent `pattern` or an `ask` verdict. Scope those rules to OpenCode with
`available_on: [opencode]` on the rule. OpenCode renders the full `permission` block.
Antigravity cannot scope a subagent's tools at all and rejects any per-agent permission rule.


## 🔌 Plugins

A plugin copies a local file tree into the workspace root. Plugins use a `source: local`
install spec and a `path:` relative to the manifest root.

```yaml
plugins:
  my-plugin:
    install:
      claude:
        source: local
        path: plugins/my-plugin
```

Bun Off copies every file under `plugins/my-plugin/` verbatim into the workspace, preserving the
directory structure. Each `install:` block is platform-specific: only the platforms listed
receive the plugin.

**Limitation:** only the `local` source is supported. Network or registry-based sources are not
implemented at the moment.


## 🛠️ Tool installers

### mise

The `mise:` key lists `mise.toml` files (paths relative to the manifest root). Bun Off writes each
listed file as a drop-in it owns (`boff-0.toml`, `boff-1.toml`, ...), so files that
each declare a `[tools]` table never collide; mise merges every drop-in natively:

| Scope | Target |
|---|---|
| Workspace | `<workspace_root>/.config/mise/conf.d/boff-<n>.toml` |
| Global | `~/.config/mise/conf.d/boff-<n>.toml` |

```yaml
mise:
  - mise/mise.toml
```

`mise` auto-loads every file under `conf.d/`, so this drop-in is **non-destructive**: it never
touches a hand-authored `mise.toml` or `.config/mise/config.toml`. `mise` reads the Bun Off drop-in
alongside your own config. Bun Off records the drop-in under the `tool:mise` owner, so a later deploy
that drops `mise:` reconciles it like any other artifact (see [Deploy state & switching
stacks](#-deploy-state--switching-stacks)).

**Recommended workflow.** Use the drop-in to declare the runtimes your MCP servers and plugins
need. Many MCP server commands assume a runtime on `PATH` (for example `uvx`, `node`, `python`):
pin those in the manifest's `mise/mise.toml` so deploying the stack also pins its toolchain.

```toml
# mise/mise.toml
[tools]
node = "22"
uv = "latest"
python = "3.12"
```

Bun Off deploys this **config**, but it does not run `mise`: the tools install when `mise` next runs. If
you already enable `mise`'s shell activation, entering the directory installs them automatically.
For an explicit, reproducible install (fresh clones, CI), wire the recipe below.

#### Installing the tools (recipe)

Combine two hooks so a deploy leaves a working toolchain and each new session stays current:

- A `post_install` lifecycle hook installs the tools once, right after the deploy writes the
  drop-in. Save it as `hooks/mise-install.py`:

  ```python
  import subprocess

  from boff.hooks import HookContext

  ctx = HookContext.from_stdin()
  root = ctx.scope.workspace_root
  subprocess.run(["mise", "trust"], cwd=root, check=False)
  subprocess.run(["mise", "install"], cwd=root, check=False)
  ```

  ```yaml
  hooks:
    post_install:
      - script: mise-install     # hooks/mise-install.py
  ```

- A `session_start` event hook re-installs on a fresh checkout or after the tool list changes:

  ```yaml
  event_hooks:
    - name: mise-install
      event: session_start
      command: "mise trust 2>/dev/null; mise install 2>/dev/null || true"
  ```

This mirrors the [project indexing recipe](#project-indexing-recipe-tldr): install once on
deploy, refresh on `session_start`.


## 🪝 Lifecycle hooks

Lifecycle hooks run Python scripts before and after the deploy step. They receive a
JSON-encoded `HookContext` on stdin. They are distinct from [event hooks](#-event-hooks),
which run inside the assistant at runtime.

```yaml
hooks:
  pre_install:
    - script: check_deps
  post_install:
    - script: notify
```

Each `script:` name resolves to `hooks/<name>.py` in the manifest root. Bun Off runs the scripts
with the same Python interpreter it uses, from the root of the bundle that declared them. A hook
inherited through `extends:` therefore runs with its own bundle as the working directory, not the
extending manifest's, so a hook may rely on paths relative to the bundle it ships with.

### Reading hook context

A hook reads its context by importing `boff.hooks.HookContext` and calling `from_stdin()`:

```python
from boff.hooks import HookContext

ctx = HookContext.from_stdin()
print(f"Phase: {ctx.phase}")
print(f"Platforms: {ctx.platforms}")
print(f"Workspace: {ctx.scope.workspace_root}")
print(f"Operations planned: {ctx.ops_count}")
```

`HookContext` fields:

| Field | Type | Description |
|---|---|---|
| `phase` | `HookPhase` | `pre_install` or `post_install` |
| `platforms` | `tuple[str, ...]` | Target platforms for this deploy |
| `scope` | `Scope` | Kind and workspace root |
| `manifest_root` | `Path` | Absolute path to the manifest folder |
| `ops_count` | `int` | Number of file/shell operations planned |

Hooks run before (`pre_install`) the deploy executes operations and after (`post_install`) it
completes. A non-zero exit from any hook aborts the run with an error. Hooks do not run in
`--dry-run` mode.


## ⚡ Event hooks

Event hooks run *inside* the assistant in response to runtime events: after an edit, after a
shell command, when the assistant finishes. You author one shell body against a normalized
contract, and Bun Off deploys the platform glue so the same hook runs on every platform.

```yaml
event_hooks:
  - name: lint
    event: after_edit
    script: lint                  # shell script at event_hooks/lint
  - name: audit
    event: after_bash
    command: "logger boff: $BOFF_COMMAND"   # inline shell body
```

Each entry needs a `name` (the deployed script filename), an `event` from the normalized set
below, and exactly one of `command:` (inline shell) or `script:` (a file under `event_hooks/`).
Optional fields: `available_on:` and `timeout:` (honored by Claude and Antigravity; OpenCode has
no per-hook timeout and ignores it).

### Normalized events

| `event:` | Fires | Claude event | OpenCode hook | Antigravity event | Context provided |
|---|---|---|---|---|---|
| `after_edit` | after a file edit or write | `PostToolUse` (`Edit\|Write`) | `tool.execute.after` | `PostToolUse` (four file-writing tools, below) | `BOFF_FILE` |
| `after_bash` | after a bash command | `PostToolUse` (`Bash`) | `tool.execute.after` | `PostToolUse` (`run_command`) | `BOFF_COMMAND` |
| `on_finish` | when the assistant finishes | `Stop` | `session.idle` | `Stop` | none |
| `session_start` | when a session starts | `SessionStart` | `session.start` | not supported | none |
| `session_end` | when a session ends | `SessionEnd` | `session.deleted` | not supported | none |

On Antigravity, `after_edit` matches four file-writing tools:
`write_to_file|edit_notebook|propose_code|file_change`. Only `write_to_file` was observed to fire
in testing; the other three are named because a matcher alternative that never fires costs
nothing.

`session_end` is faithful on Claude (`SessionEnd`). OpenCode has no native end event, so Bun Off maps
it to the closest signal, `session.deleted`: it fires when a session is removed, not on every
graceful exit. Prefer `session_start` for work that must run once per session (the index-warming
recipe below relies on it).

Antigravity CLI has no session lifecycle event of any kind. A `session_start` or `session_end`
hook scoped to it is skipped with a warning: scope such hooks with
`available_on: [claude, opencode]`.

### Script contract

Bun Off populates the same environment variables on both platforms, so one shell body works
everywhere:

| Variable | Value |
|---|---|
| `BOFF_EVENT` | the normalized event (`after_edit`, `after_bash`, `on_finish`, `session_start`, `session_end`) |
| `BOFF_TOOL` | the underlying tool, lowercased (`edit`, `write`, `bash`); empty when not applicable |
| `BOFF_FILE` | edited file path for `after_edit`; empty otherwise |
| `BOFF_COMMAND` | the command for `after_bash`; empty otherwise |

Event hooks are **side-effect only**: Bun Off ignores their output and exit code. They cannot
block or modify a tool call. For blocking, context injection, or any Claude-native hook event,
write a raw `hooks` block through the [`settings:`](#settings) passthrough (Claude only).

### What Bun Off deploys

| Platform | Deployed |
|---|---|
| Claude Code | per-hook script at `.claude/hooks/<name>`; a shared dispatcher `.claude/hooks/_boff_dispatch.py`; a `hooks` block merged into `.claude/settings.json` |
| OpenCode | per-hook script at `.opencode/hooks/<name>`; an auto-loaded plugin `.opencode/plugins/boff-hooks.js` |
| Antigravity CLI | per-hook script at `.agents/hooks/<name>`; a shared dispatcher `.agents/hooks/_boff_dispatch.py`; a `.agents/hooks.json` entry per hook |

The Claude dispatcher reads the event payload Claude pipes on stdin and exports the `BOFF_*`
contract; the generated OpenCode plugin reads the equivalent fields from its hook arguments. You
never write platform-specific glue.

The Antigravity dispatcher also re-applies its own tool matcher. Antigravity runs a
`PostToolUse` handler at invocation boundaries with a null `toolCall`, without consulting the
matcher in `hooks.json`, so `after_bash` would otherwise fire on turns where no command ran, with
an empty `BOFF_COMMAND`. The dispatcher drops those, so the contract below holds on all three
platforms.

### Cross-platform formatting: use the native formatter

Do **not** configure a formatting `event_hook` for both platforms. OpenCode formats natively
and more efficiently through its built-in `formatter`, so a formatting plugin there is
redundant. Instead, scope the hook to Claude and configure OpenCode's formatter through the
`settings:` passthrough:

```yaml
event_hooks:
  - name: format
    event: after_edit
    command: "ruff format \"$BOFF_FILE\""
    available_on: [claude]        # Claude formats via the hook

settings:
  opencode:
    formatter:                    # OpenCode formats natively
      ruff:
        command: ["ruff", "format", "$FILE"]
        extensions: [".py"]
```

OpenCode's built-in formatters are disabled by default; the `formatter` block enables and
configures them. The `$FILE` placeholder is OpenCode's own (not the `BOFF_FILE` contract).

### Project indexing recipe (`tldr`)

Some MCP tools need a project index built before first use and refreshed when the code moves.
`llm-tldr` is the canonical example: it warms a persistent index (`.tldr/`) with `tldr warm .`.
Combine three hooks so the index stays current without manual steps:

- A `post_install` hook builds the index once when the stack is deployed (see [Lifecycle hooks](#-lifecycle-hooks)).
- A `session_start` hook re-warms when the working tree moved since the last warm.
- An `after_edit` hook feeds in-session edits to the running daemon.

```yaml
event_hooks:
  - name: tldr-warm
    event: session_start
    script: tldr-warm           # event_hooks/tldr-warm
  - name: tldr-notify
    event: after_edit
    command: '[ -n "$BOFF_FILE" ] && tldr daemon notify "$BOFF_FILE" 2>/dev/null || true'
```

The `event_hooks/tldr-warm` script guards on the git revision so it only re-indexes after a real
change, and runs in the background so session start never blocks:

```sh
#!/bin/sh
marker=".tldr/.boff-warm-rev"
head=$(git rev-parse HEAD 2>/dev/null || echo none)
[ -f "$marker" ] && [ "$(cat "$marker")" = "$head" ] && exit 0
tldr warm . --background
echo "$head" > "$marker"
```

This pattern generalizes to any tool with a one-time-plus-refresh index: warm on deploy, refresh
on `session_start`, and keep hot via `after_edit`.


## 🎯 Targeting platforms with `available_on:`

Any artifact accepts an optional `available_on:` list: rules, skills, slash commands, output
styles, MCP servers, agents, and event hooks, plus each individual permission rule (whether top-level or per-agent).
When set, Bun Off only deploys that artifact to the listed platforms. When omitted, Bun Off deploys
the artifact to every platform in the current `--platform` invocation.

```yaml
rules:
  - name: opencode-specific
    available_on: [opencode]
  - universal-rule          # deploys to all platforms
```


## 🖥️ Supported platforms

| Platform | Name flag | CLI binary | Workspace target | User-level target (`--global`) |
|---|---|---|---|---|
| Claude Code | `claude` | `claude` | `.claude/` plus `.mcp.json` | `~/.claude/` |
| OpenCode | `opencode` | `opencode` | `.opencode/` plus `opencode.json` | `~/.config/opencode/` |
| Antigravity CLI | `antigravity` | `agy` | `.agents/` plus a generated `GEMINI.md` | `~/.gemini/config/` plus `~/.gemini/antigravity-cli/settings.json` |

The **CLI binary** column is what [`boff check`](#boff-check) looks for on `PATH` before it
verifies a platform. Pass `--no-probe` to skip that gate.

Antigravity CLI supports a subset of the manifest. It has no target for slash commands or
permissions in either scope, no session lifecycle events, and no workspace target for output
styles or settings; Bun Off warns and skips each of those rather than writing a file the tool
would silently ignore. `boff check` reports those artifacts as `dropped` and still exits `0`:
the platform is doing what it declared. Which artifacts are dropped depends on the scope, and
the table in [Scopes](#-scopes) gives the full picture. See
[Known limitations](#-known-limitations).


## 🌐 Scopes

Every deploy targets one of two scopes.

- **Workspace** (the default): the current directory. Config lands in `.claude/`, `.opencode/`,
  `.agents/` and is normally committed with the project.
- **User level** (`--global`): your own configuration, shared by every project on the machine.

"Global" here means *your user account*, not the machine: Bun Off never writes outside your home
directory. The name matches what the tools themselves call it — OpenCode ships
`opencode plugin --global`, and Antigravity's docs call `~/.gemini/config/` its global
configuration.

The two scopes are **complementary and fully independent**. Each records its own state
(`~/.boff/state.json` for user level, `<project>/.boff/state.json` for a workspace) and its own
manifest stack, so cleaning one never touches the other. Deploy a personal stack once, then keep
deploying project stacks on top:

```bash
boff deploy ~/stacks/personal --platform claude --global   # once per machine
cd ~/projects/my-app
boff deploy ./project-stack --platform claude              # once per project
```

Within a scope a deploy stays authoritative as usual: re-deploying a different manifest removes
what the previous one installed (see [Deploy state & switching stacks](#-deploy-state--switching-stacks)).

### What each platform accepts at user level

A surface can exist in one scope and not the other, in *both* directions. Antigravity's settings
have only a user-level home; Claude's MCP config has only a workspace one.

| Manifest section | `claude` | `opencode` | `antigravity` |
|---|---|---|---|
| `rules:` | `~/.claude/rules/` | `~/.config/opencode/rules/` (flat) | `~/.gemini/config/rules/boff.md` |
| `skills:` | `~/.claude/skills/` | `~/.config/opencode/skills/` | `~/.gemini/config/skills/` |
| `agents:` | `~/.claude/agents/` | `~/.config/opencode/agents/` | `~/.gemini/config/agents/` |
| `slash_commands:` | `~/.claude/commands/` | `~/.config/opencode/commands/` | dropped |
| `output_styles:` | `~/.claude/output-styles/` | dropped | dropped |
| `settings:` | `~/.claude/settings.json` | `~/.config/opencode/opencode.json` | `~/.gemini/antigravity-cli/settings.json` |
| `permissions:` | `~/.claude/settings.json` | `~/.config/opencode/opencode.json` | dropped |
| `mcp_servers:` | **dropped** | `~/.config/opencode/opencode.json` | `~/.gemini/config/mcp_config.json` |
| `event_hooks:` | `~/.claude/hooks/` | `~/.config/opencode/hooks/` | `~/.gemini/config/hooks/` |
| `plugins:` (`source: local`) | dropped | dropped | dropped |
| `mise:` | `$XDG_CONFIG_HOME/mise/conf.d/` | — | — |

Three of those deserve an explanation.

**Claude MCP servers are dropped at user level.** Claude's user-scope MCP config lives in
`~/.claude.json`, which also holds your OAuth credentials and per-project history and is
rewritten by every Claude Code session. Bun Off merges by reading, modifying, and writing the
whole file, so a deploy racing a live session could clobber it. Add user-scope servers with
`claude mcp add --scope user` instead; project-scoped `mcp_servers:` are unaffected.

**OpenCode's user-level rules directory is flat.** OpenCode resolves a *relative* `instructions`
glob against the project you happen to be in, never against the config directory, so Bun Off
registers an absolute entry (`~/.config/opencode/rules/*.md`) — and OpenCode globs only the last
segment of an absolute path. A rule with a `category:` still deploys, with a warning, directly
into `~/.config/opencode/rules/`. Rule names are unique within a manifest, so nothing collides.
Bun Off deliberately does **not** write `~/.config/opencode/AGENTS.md`: OpenCode loads exactly one
user-level instructions file, preferring that one over `~/.claude/CLAUDE.md`, so merely creating
it would stop OpenCode reading your Claude instructions.

**Antigravity reads user-level rules but not workspace ones.** `.agents/rules/*.md` is never
loaded, which is why a workspace deploy inlines every rule into `GEMINI.md`. Its user-level
`~/.gemini/config/rules/` *is* loaded, provided each file declares `trigger: always_on`
frontmatter — a file without it is silently ignored. Bun Off writes one aggregate
`rules/boff.md` carrying that frontmatter.

`--global` cannot be combined with `--wipe`. A wipe deletes a platform's entire configuration
directory, and at user level that is `~/.claude`, which also holds credentials and session
history Bun Off never created. Use `boff clean --global`, which removes only Bun Off's own
recorded footprint.


## ♻️ Deploy state & switching stacks

Bun Off keeps track of every `boff deploy`: it records what it installs and, on the next deploy,
removes whatever the previous deploy left behind that the new manifest no longer produces. You
can therefore switch a project between stacks — for example a `design` stack and a `maintenance`
stack with different rules — just by deploying the other manifest. The previous stack's files
are cleaned up instead of piling up.

Bun Off tracks its footprint in `<workspace_root>/.boff/state.json`, recording for each platform
(and each tool installer) the files it created, the exact JSON keys it merged into shared
files (`.mcp.json`, `.claude/settings.json`, `opencode.json`), and the manifest references it
deployed (the [stack](#-deploying-a-stack-of-manifests)). On a new deploy in the same project
Bun Off:

- deletes files it created that the new manifest no longer emits, then prunes any directories
  left empty;
- removes only the JSON keys it previously injected and no longer produces, leaving keys you
  added by hand untouched.

The `.boff/` directory is self-ignored from Git (Bun Off writes a
`.boff/.gitignore`), since it reflects local install state. Cleanup is
per platform: for instance, deploying with `--platform claude` never
touches files Bun Off recorded for `opencode`.

### The managed `.gitignore` block

Every deploy also lists the files it owns in a `.gitignore` in the directory you deploy into,
inside a block delimited by `# BEGIN boff-managed` and `# END boff-managed`. Bun Off rewrites that
block on each deploy and leaves the rest of the file untouched, so deployed artifacts stay out of
version control while your own entries survive. Pass `--no-ignore` to not touch `.gitignore`.

Bun Off writes the block whenever the deploy directory sits inside a git work tree: the directory
itself or any ancestor holds a `.git`. This means deploying into a **subfolder** of a repository
still gets a `.gitignore`, written in that subfolder (a nested `.gitignore`). Its entries are
anchored to the subfolder, so git ignores exactly the deployed files. Bun Off does nothing here
when no ancestor is a git work tree, and `boff check` never verifies the block.

Bun Off writes files two ways, and treats them differently in the block. Files it owns outright
(rules, skills, commands, hooks, `GEMINI.md`) are always listed. Config files it *merges* into
(`.claude/settings.json`, `.mcp.json`, `opencode.json`) may also hold keys you wrote, and
`.gitignore` cannot ignore individual JSON keys, so Bun Off ignores a merged file only when it
owns **every** key in it. When such a file also holds keys of your own, Bun Off leaves it tracked
and lists it commented-out, with a note, so ignoring the whole file (and hiding your keys from git)
stays an explicit opt-in:

```gitignore
# BEGIN boff-managed
/.claude/rules/commit-conventions.md
/.mcp.json
#
# These files also contain settings you edited, so boff leaves them tracked.
# Uncomment a line to ignore the whole file (including your own keys):
#/.claude/settings.json
# END boff-managed
```

The classification is recomputed on every deploy: add your own key to a formerly all-Bun-Off file
and the next deploy moves it from ignored to commented-out.

The state directory `.boff/` never appears in this block. It self-ignores through its own
`.boff/.gitignore` (a single `*`), so the whole directory stays out of git regardless.

Commit the block itself if you want the ignore rules shared; delete it and re-deploy to rebuild
it.

[`boff check`](#boff-check) reads this same state file to tell you what a re-deploy would clean
up, reporting it as `stale`. It only reads: `check` never writes `state.json` or `.gitignore`.

### Switching example

```bash
boff deploy ./design-stack --platform claude         # install the design rules
# ...later...
boff deploy ./maintenance-stack --platform claude     # remove design's rules, install maintenance's
```

Both stacks are ordinary manifest folders. Only one is active at a time per platform.


## 🥞 Deploying a stack of manifests

`boff deploy` takes any number of manifests. They merge left to right by the same [last-wins
rules](#merge-semantics) that govern `extends:`, so **the last one wins** on a name collision:

```bash
boff deploy ../team-stack ./my-rules --platform claude
```

This is `extends:` without authoring a wrapper manifest. Reach for a stack when *you* are choosing
the combination for one project; reach for `extends:` when the combination belongs to the manifest
and should travel with it.

Bun Off records the stack in `.boff/state.json`, which is what lets you change it later without
restating it.

### Adding and removing manifests

```bash
boff deploy --add ./extra-rules          # append to the deployed stack
boff deploy --remove ../team-stack       # drop one from it
boff deploy                              # re-deploy the recorded stack unchanged
```

`--platform` is optional for all three: it defaults to the platforms already deployed in this
directory. Both flags are repeatable, and `--remove` applies before `--add`, so one command can
swap a manifest out for another.

Adding is **not** a partial install. Bun Off re-merges the whole stack and deploys the result, so
every deploy stays authoritative: the workspace matches the current stack exactly, with no
leftovers from an earlier one. That is why `--remove` reclaims a manifest's files, and why adding
a manifest that overrides an existing rule takes effect immediately.

`--add` puts the new manifest **last**, so it wins conflicts against everything already deployed.
Adding a manifest that is already in the stack moves it to the end (raising its precedence) and
says so.

A few guardrails, each a usage error rather than a guess:

| Situation | What Bun Off does |
|---|---|
| Manifest arguments together with `--add` / `--remove` | Refuses: arguments *replace* the stack, the flags *change* it. |
| `--add`, `--remove`, or a bare `boff deploy` with nothing recorded | Refuses, and tells you to deploy a manifest first. |
| `--remove` of a manifest not in the stack | Refuses, and lists what is in it. |
| `--remove` that would empty the stack | Refuses, and points at [`boff clean`](#boff-clean). |

References are compared by the manifest they resolve to, so `--remove ./team-stack` matches a
stack entry recorded as `team-stack`. They are stored as you typed them, so a Git reference
re-fetches on the next deploy and picks up any upstream change.

A full `boff clean` also forgets the recorded stack, so a later `--add` starts fresh instead of
resurrecting what you just uninstalled. A platform-scoped `boff clean --platform <name>` leaves
the stack recorded.

### Starting from a clean slate

Two flags (also available as the standalone [`boff clean`](#boff-clean) command) reset a project
before installing:

- `--clean` removes Bun Off's **entire** recorded footprint for the project, across every platform
  and tool it has deployed, then installs fresh. It is non-destructive: files and settings keys
  Bun Off never wrote are preserved.
- `--wipe` deletes all the targeted platforms' native configuration files, **including**
  hand-authored content, then installs fresh. It prompts for interactive confirmation before
  deleting.

  | Platform | Deleted by `--wipe` |
  |---|---|
  | Claude Code | `.claude/` and `.mcp.json` |
  | OpenCode | `.opencode/` and `opencode.json` |
  | Antigravity CLI | `.agents/` and `GEMINI.md` |

  Note that OpenCode's `opencode.json` is its settings file as well as its MCP file, so wiping
  OpenCode discards any settings you keep there by hand.

**Caveats:**

- A merged-file key that Bun Off once owned but you later overrode by hand is removed if Bun Off stops
  producing it. Keys Bun Off never wrote are always preserved.
- A tool installer's files (such as `mise`) are reconciled only while that tool stays in the
  manifest. If a later stack drops the tool entirely, its previously written file remains; clear
  it explicitly with `boff clean`.


## 🚀 Commands

### Top-level flags

These precede the subcommand:

| Flag | Description |
|---|---|
| `-v`, `--verbose` | Print every executed operation, not just the summary |
| `--version` | Print the installed package version and exit |
| `-h`, `--help` | Show usage and exit |

### `boff deploy`

Deploy one or more manifests to one or more platforms.

```
boff deploy [<manifest> ...] [--add <manifest>] [--remove <manifest>]
            [--platform <name> ...] [--global] [--dry-run] [--clean | --wipe] [--no-ignore]
```

| Argument | Description |
|---|---|
| `manifest` | Path or Git URL of a manifest directory (must contain `boff.yaml`). See [Reference scheme](#reference-scheme). Repeatable: the manifests merge left to right and the last one wins on conflicts. Omit to re-deploy the recorded stack |
| `--add MANIFEST` | Append a manifest to the deployed stack instead of replacing it, repeatable. See [Adding and removing manifests](#adding-and-removing-manifests) |
| `--remove MANIFEST` | Drop a manifest from the deployed stack, repeatable |
| `--platform NAME` | Target platform, repeatable (e.g. `--platform claude --platform opencode`). Defaults to the platforms already deployed in this directory |
| `--dry-run` | Print planned operations without writing any files or running hooks |
| `--clean` | Remove Bun Off's entire recorded footprint for this project before installing (non-destructive: keeps files Bun Off never wrote). See [Deploy state & switching stacks](#-deploy-state--switching-stacks) |
| `--wipe` | Delete all the targeted platforms' native configuration files before installing (destructive: removes hand-authored files too). Prompts for interactive confirmation |
| `--no-ignore` | Do not add the deployed artifacts to the workspace `.gitignore` |
| `--global` | Deploy to your user-level configuration instead of the current workspace. See [Scopes](#-scopes). Cannot be combined with `--wipe` |

**Example:**

```bash
boff deploy ./my-stack --platform claude
boff deploy ./my-stack --platform claude --platform opencode --dry-run
boff deploy ./my-stack --platform claude --clean      # purge boff's prior footprint, then install

# Combine manifests: ./my-rules wins where the two collide.
boff deploy ../team-stack ./my-rules --platform claude
boff deploy --add ./extra-rules                       # append to what is already deployed
boff deploy --remove ../team-stack                    # and drop one again

# Deploy a published bundle straight from GitHub, no clone of your own:
boff deploy https://github.com/atom-sw/bun-off-bundles/tree/main/python --platform claude

# Install a personal stack once, for every project on this machine:
boff deploy ~/stacks/personal --platform claude --global
```

The manifest arguments say *what* to deploy; the workspace is always the current directory. A
relative path therefore resolves against the current directory too.

Bun Off runs `pre_install` hooks, applies all operations, then runs `post_install` hooks. Every
deploy also removes whatever a previous deploy left behind that this
manifest no longer produces (see [Deploy state & switching stacks](#-deploy-state--switching-stacks)).


### `boff check`

Verify that a workspace still matches what deploying these manifests would write. `check` re-plans
the deploy, compares the plan against what is actually on disk, and reports each artifact. It
never writes anything: not the artifacts, not `.boff/state.json`, not `.gitignore`.

```
boff check [<manifest> ...] [--platform <name> ...] [--global] [--no-probe]
```

| Argument | Description |
|---|---|
| `manifest` | Path or Git URL of a manifest directory (must contain `boff.yaml`). See [Reference scheme](#reference-scheme). Repeatable, merging left to right. Omit to check the [recorded stack](#-deploying-a-stack-of-manifests) |
| `--platform NAME` | Target platform, repeatable. Defaults to the platforms already deployed in this directory |
| `--global` | Check your user-level configuration instead of the current workspace. See [Scopes](#-scopes) |
| `--no-probe` | Skip checking that each platform's CLI binary is on `PATH`. Use this in CI and containers, where the assistants themselves are not installed |
| `-v` | Also list every artifact that verified `ok` (a top-level flag: it goes *before* the subcommand) |

Each artifact reports one status:

| Status | Meaning | Exit code |
|---|---|---|
| `ok` | The artifact is on disk exactly as the manifest describes it | `0` |
| `missing` | Bun Off would write this file, and it is not there | `1` |
| `drifted` | The file exists but its content, or a key Bun Off owns in it, differs | `1` |
| `stale` | Something a previous deploy left behind that this manifest no longer produces. Re-deploying removes it | `1` |
| `dropped` | The platform has no target for this artifact *in this scope* and said so (see [Supported platforms](#-supported-platforms)) | `0` |
| `unsupported` | The platform does not support this artifact type at all | `0` |
| `unverifiable` | The operation runs a command rather than writing a file, so its effect cannot be verified | `0` |

**Shared files are verified only on the keys Bun Off owns.** `.claude/settings.json`, `.mcp.json`,
and `opencode.json` mix Bun Off's output with your own. `check` compares only the JSON key paths
Bun Off merged in; keys you added by hand are never reported as drift, and never as stale.

**Example:**

```bash
boff check                                           # check whatever is deployed here
boff check ./my-stack --platform claude              # quiet: only what is wrong, plus a tally
boff check ./my-stack --platform claude --no-probe   # do not require the `claude` binary
boff -v check ./my-stack --platform claude           # also list every artifact that verified ok
```

```
$ boff check ./my-stack --platform claude
✓ sample-stack v0.1.0
  Sample manifest exercising every artifact type.

claude  (found: /usr/local/bin/claude)
  ✗ missing   agent reviewer            .claude/agents/reviewer.md
  ✗ drifted   settings                  .claude/settings.json
      key permissions.allow: expected ['Read'], found ['Bash(rm:*)']

3 ok, 1 missing, 1 drifted
```

A clean workspace prints the manifest header and the tally, nothing else. Use `check` in CI to
fail a build whose committed assistant config has drifted from the manifest that generated it:

```bash
boff check ./my-stack --platform claude --no-probe || exit 1
```


### `boff clean`

Remove Bun Off's deployed footprint from a project without installing anything. This is the
standalone form of the `deploy --clean` / `--wipe` flags.

```
boff clean [--platform <name> ...] [--root <dir> | --global] [--wipe] [--dry-run] [--no-ignore]
```

| Flag | Description |
|---|---|
| `--platform NAME` | Restrict to these owners, repeatable. Omit to clean every platform Bun Off recorded. Required with `--wipe` |
| `--root DIR` | Project root (default: current directory). Cannot be combined with `--global` |
| `--global` | Clean your user-level configuration instead of a project. See [Scopes](#-scopes). Cannot be combined with `--wipe` |
| `--wipe` | Delete all the targeted platforms' native configuration files (destructive), instead of only Bun Off's recorded files. Prompts for interactive confirmation |
| `--dry-run` | Print planned operations without applying them |
| `--no-ignore` | Do not update the workspace `.gitignore` |

Without `--wipe`, `boff clean` is non-destructive: it removes only the files and merged JSON
keys Bun Off itself recorded, preserving anything you authored by hand.

Cleaning every platform also forgets the [recorded stack](#-deploying-a-stack-of-manifests), so a
later `boff deploy --add` starts from nothing rather than reinstalling what you just removed.
`boff clean --platform <name>` leaves the stack recorded.

**Example:**

```bash
boff clean                                   # remove boff's whole footprint for this project
boff clean --platform claude                 # remove only what boff deployed for Claude
boff clean --wipe --platform claude          # delete .claude/ and .mcp.json wholesale (asks first)
boff clean --global                          # remove boff's user-level footprint
```


### `boff context`

The `context` subcommand moves a project's AI assistant context — instructions, rules, plans,
memory, and session transcripts — between machines or between platforms.

"Context" here means the durable, on-disk state that seeds a session, not the live context
window (which is ephemeral and not portable). What each platform exposes differs:

- **Claude Code:** author-written instructions (`CLAUDE.md`, `.claude/rules/`) **and** Claude's
  auto-memory store (`~/.claude/projects/<encoded-path>/memory/`). Export captures both; the
  auto-memory files travel in the bundle's `memory/` directory.
- **OpenCode:** instructions only (`AGENTS.md`, `opencode.json` `instructions`). OpenCode has no
  native memory layer, so an OpenCode bundle carries no memory. On `migrate`, Claude's auto-memory
  folds into a handoff rule the target auto-loads (see `boff context migrate` below).
- **Antigravity CLI:** instructions only, carried in `AGENTS.md`. Its conversations live in a
  SQLite store with no export command, so an Antigravity bundle carries no sessions and no memory.
  Migrating *to* Antigravity **overwrites `AGENTS.md`** (as it overwrites `CLAUDE.md` on Claude),
  inlining the source's primary doc, its rules, and the handoff digest into that one file, since
  Antigravity reads rules from nowhere else. The deploy-generated `GEMINI.md` travels along as a
  config file and is restored on `import`, but `migrate` drops it: its content comes from the
  manifest, so redeploy rather than migrate it.

  Antigravity is the one platform where `boff deploy` and `boff context` would otherwise target the
  same file. Deploy owns `GEMINI.md`; context owns `AGENTS.md`. Antigravity merges both at session
  start, so nothing is lost, and neither command can clobber the other's work.

#### `boff context export`

Save the current project's context to a portable `.tar.gz` bundle.

```
boff context export --platform <name> -o <file.tar.gz> [--root <dir>] [--full] [--sanitize]
```

| Flag | Description |
|---|---|
| `--platform NAME` | Source platform to collect context from |
| `-o / --out PATH` | Output archive path (must end in `.tar.gz`) |
| `--root DIR` | Project root directory (default: current directory) |
| `--full` | Include raw session transcripts in the bundle |
| `--sanitize` | Redact common secret patterns (API keys, tokens, passwords) |

The archive contains instructions, plans, memory files, todo lists, an optional digest summary,
and (with `--full`) raw session transcripts. It is a human-inspectable gzipped tar with a
`manifest.json` index.

**Example:**

```bash
boff context export --platform claude -o context-backup.tar.gz
boff context export --platform claude -o context-transfer.tar.gz --sanitize
```

#### `boff context import`

Restore a bundle onto the current machine.

```
boff context import <bundle.tar.gz> [--into <dir>] [--dry-run]
```

| Argument | Description |
|---|---|
| `bundle` | Path to a `.tar.gz` bundle produced by `boff context export` |
| `--into DIR` | Project root to restore into (default: current directory) |
| `--dry-run` | Print planned operations without applying them |

Bun Off reads the platform from the bundle's embedded manifest and uses that platform's provider
to materialize the files. For Claude Code this re-keys paths to the target machine and restores
memory and plans to the correct locations. For OpenCode, session transcripts import via the
`opencode import` CLI command.

**Example:**

```bash
boff context import context-backup.tar.gz
boff context import context-backup.tar.gz --into /path/to/project --dry-run
```

#### `boff context migrate`

Convert a project's context from one platform's format to another and write it in place.

```
boff context migrate --from <source> --to <target> [--into <dir>] [--dry-run]
```

| Flag | Description |
|---|---|
| `--from NAME` | Platform to read context from |
| `--to NAME` | Platform to write context to |
| `--into DIR` | Project root (default: current directory) |
| `--dry-run` | Print planned operations without applying them |

Migration remaps the primary instructions file (e.g. `CLAUDE.md` becomes `AGENTS.md`), re-roots
rules to the target platform's rules directory, carries over plans, and folds any
platform-specific data that the target cannot store natively (such as Claude's auto-memory)
into a `handoff/migrated-context.md` rule that the target platform auto-loads.

**Example:**

```bash
# Hand off a Claude Code project to OpenCode
boff context migrate --from claude --to opencode

# Preview what would change
boff context migrate --from opencode --to claude --dry-run
```


### Exit codes

Every command returns one of three exit codes, so you can script against them:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error: a bad manifest, corrupt state, a failing or missing hook, or an unreadable file. Bun Off prints a single `error: ...` line to stderr, never a traceback. A `deploy --dry-run` that finds a missing hook script also returns `1`, and so does a `boff check` that finds a `missing`, `drifted`, or `stale` artifact |
| `2` | Usage error: invalid or missing arguments (for example `clean --wipe` without `--platform`) |


## ⚠️ Known limitations

- **Remote `extends:` refetches branch refs.** A branch reference is updated on every load;
  there is no `--no-cache`/pin-by-default flag yet. Pin to a tag or commit for reproducibility.

- **`meta.long_description` is inline only.** It takes a markdown string; pointing it at a
  separate file is not yet supported.

- **Plugin sources:** Only `source: local` is implemented. Remote or registry-based plugin
  sources are not yet available, and `source: local` has no user-level target at all: it mirrors
  a subtree to the deploy root, and there is no equivalent of that in a home directory. A
  `--global` deploy warns and skips such a plugin rather than failing, so one manifest stays
  deployable in both scopes.

- **No user-level MCP servers for Claude Code.** Its only user-scope target, `~/.claude.json`,
  holds credentials and is rewritten by every session. See [Scopes](#-scopes).

- **OpenCode's user-level rules are flat.** A `category:` cannot be honored there, because the
  `instructions` glob Bun Off registers can only match one directory level. See
  [Scopes](#-scopes).

- **Bun Off owns the whole `instructions` array in `opencode.json`.** JSON merging replaces
  lists rather than concatenating them, so hand-authored `instructions` entries in a file Bun
  Off also writes are replaced. This applies in both scopes, but matters more at user level,
  where you are likelier to have set your own.

- **Antigravity CLI supports a subset of the manifest.** It has no target for
  `slash_commands:` or `permissions:` in either scope, and no workspace target for
  `output_styles:` or `settings:` (settings live only in the user-level
  `~/.gemini/antigravity-cli/settings.json`, which `--global` does write). It has no
  `session_start` or `session_end` event. Bun Off warns and skips each. In a workspace it loads
  rules only from its primary instructions file, so every rule is inlined into a generated
  `GEMINI.md` and `rules[].category` and `rules[].globs` have no effect there. Per-agent
  `permissions:` raise an error, and a subagent `model:` is ignored because Antigravity
  subagents inherit the parent's model.

- **Antigravity `permissions:` are dropped in every scope.** It does hold user-level
  permissions, as an allow/deny/ask triple, but the JSON key they live under is not established.
  Bun Off will not guess: a wrong key would deploy settings the tool silently ignores while
  `boff check` reported success. Scope them with `available_on: [claude, opencode]`.

- **Output styles are Claude Code only, by design.** OpenCode's nearest analogue is a primary
  agent, but its prompt *replaces* the base system prompt rather than appending to it, so
  `keep-coding-instructions: true` cannot be expressed there; Antigravity has no such surface at
  all. Rather than translate into something that behaves differently, Bun Off warns and skips on
  both. Write the OpenCode side explicitly as an `agents:` entry with `mode: primary`.

- **Antigravity's own plugin format is not used.** Its `plugin.json` bundle (skills, agents,
  commands, MCP servers, and hooks) is discovered in the workspace, but Bun Off deploys those
  artifacts directly instead. A `source: local` plugin still copies its files into the workspace
  for `antigravity`, exactly as for the other platforms.

- **`boff check` cannot verify everything a deploy does.** It reads files back, so an operation
  that runs a command (a plugin installer, a tool installer that shells out) reports
  `unverifiable`. In shared JSON files it compares only the key paths Bun Off owns, so a key it
  owns that you overrode by hand is not reported until you re-deploy. It does not verify the managed
  `.gitignore` block. And `boff check --platform antigravity` inherits deploy's hard failure on a
  manifest whose agents carry per-agent `permissions:`.

- **Antigravity's `GEMINI.md` is Bun Off's.** `boff deploy` regenerates it from the manifest, and
  `boff clean` and `--wipe` delete it, so hand edits there are lost. Put durable instructions in
  `AGENTS.md`, which Antigravity merges with `GEMINI.md` and which deploy never touches.

- **OpenCode full export requires the `opencode` CLI.** `boff context export --platform
  opencode --full` calls `opencode export` for each session. If `opencode` is not on `PATH`,
  sessions are silently skipped.

- **`--sanitize` is best-effort.** The sanitizer redacts known patterns (OpenAI/Anthropic API
  keys, GitHub tokens, AWS access keys, bearer tokens, and common `key=value` pairs) but it is
  not a comprehensive secret scanner. Review exported bundles before sharing.

- **Context capture is workspace-scoped.** A bundle includes the project's own context only.
  User-scope and managed-policy instructions (such as `~/.claude/CLAUDE.md`) and any `@import`
  targets outside the project root are not captured.
