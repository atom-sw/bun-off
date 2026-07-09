"""``boff`` command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from boff import __version__
from boff.console import console
from boff.context import get_provider, provider_names
from boff.context.bundle import read_bundle, write_bundle
from boff.context.migrate import migrate
from boff.deploy import build_clear_ops, plan_deploy, plan_units
from boff.errors import BoffError
from boff.executor import execute
from boff.hooks import HookContext, run_hooks
from boff.manifest import Manifest, load_manifest
from boff.manifest_sources import resolve_ref
from boff.state import (
    load_state,
    recorded_owners,
    save_state,
    state_path,
    tool_owner,
    update_workspace_gitignore,
    without_owners,
)
from boff.types import DeleteOperation, HookPhase, Operation, Scope, ScopeKind
from boff.verify import CheckReport, Finding, Status, probe, verify


class ExitCode(IntEnum):
    """Process exit codes returned by ``main``."""

    OK = 0
    ERROR = 1
    USAGE = 2


if TYPE_CHECKING:
    # argparse exports no public name for what add_subparsers() returns.
    SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]  # pyright: ignore[reportPrivateUsage]


class _BoffParser(argparse.ArgumentParser):
    """Custom parser to standardize the default -h/--help message formatting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self.add_argument(
            "-h",
            "--help",
            action="help",
            default=argparse.SUPPRESS,
            help="Show this help message and exit.",
        )


def _build_parser() -> argparse.ArgumentParser:
    """Construct and return the command-line argument parser."""
    parser = _BoffParser(prog="boff", description="Deploy AI coding-assistant stacks.")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print all executed operations."
    )
    # `-v` is taken by `--verbose`, so the version flag is long-form only.
    parser.add_argument(
        "--version",
        action="version",
        version=f"boff {__version__}",
        help="Show the package version and exit.",
    )
    # `add_subparsers` is generic in the parser type, and `_BoffParser`'s custom -h belongs on
    # the root parser only. Naming the class keeps `sub` assignable to the `SubParsers` alias.
    sub = parser.add_subparsers(dest="command", required=True, parser_class=argparse.ArgumentParser)

    deploy_cmd = sub.add_parser("deploy", help="Deploy a manifest to one or more platforms.")
    deploy_cmd.add_argument(
        "path", metavar="MANIFEST", help="Path or Git URL of the manifest directory."
    )
    deploy_cmd.add_argument(
        "--platform",
        dest="platforms",
        action="append",
        required=True,
        metavar="NAME",
        help="Target platform (repeatable).",
    )
    deploy_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned operations without applying them or running hooks.",
    )
    deploy_cmd.add_argument(
        "--clean",
        action="store_true",
        help="Remove only the files boff previously installed before deploying again.",
    )
    deploy_cmd.add_argument(
        "--wipe",
        action="store_true",
        help="Factory reset: delete the target platform's entire configuration directory before deploying.",
    )
    deploy_cmd.add_argument(
        "--no-ignore",
        action="store_true",
        help="Do not add deployed artifacts to the workspace .gitignore.",
    )

    _add_check_parser(sub)
    _add_clean_parser(sub)
    _add_context_parser(sub)
    return parser


def _add_check_parser(sub: SubParsers) -> None:
    """Add the 'check' subcommand parser."""
    check_cmd = sub.add_parser(
        "check", help="Verify a deployed manifest still matches the workspace."
    )
    check_cmd.add_argument(
        "path", metavar="MANIFEST", help="Path or Git URL of the manifest directory."
    )
    check_cmd.add_argument(
        "--platform",
        dest="platforms",
        action="append",
        required=True,
        metavar="NAME",
        help="Target platform (repeatable).",
    )
    check_cmd.add_argument(
        "--no-probe",
        dest="probe",
        action="store_false",
        help="Skip checking that each platform's CLI binary is on PATH.",
    )


def _add_clean_parser(sub: SubParsers) -> None:
    """Add the 'clean' subcommand parser."""
    clean_cmd = sub.add_parser(
        "clean", help="Uninstall files managed by boff (or factory reset platform configuration)."
    )
    clean_cmd.add_argument(
        "--platform",
        dest="platforms",
        action="append",
        metavar="NAME",
        help="Restrict to these owners (repeatable). Required with --wipe.",
    )
    clean_cmd.add_argument("--root", type=Path, default=None, help="Project root (default: cwd).")
    clean_cmd.add_argument(
        "--wipe",
        action="store_true",
        help="Factory reset: delete the target platform's entire configuration directory.",
    )
    clean_cmd.add_argument(
        "--dry-run", action="store_true", help="Print planned operations without applying them."
    )
    clean_cmd.add_argument(
        "--no-ignore", action="store_true", help="Do not update the workspace .gitignore."
    )


def _add_context_parser(sub: SubParsers) -> None:
    """Add the 'context' subcommand parser."""
    platforms = ", ".join(provider_names())
    context_cmd = sub.add_parser(
        "context", help="Export, import, or migrate a project's context and plans."
    )
    context_sub = context_cmd.add_subparsers(dest="context_command", required=True)

    export_cmd = context_sub.add_parser("export", help="Save context+plans to a tarball.")
    export_cmd.add_argument(
        "--platform", required=True, metavar="NAME", help=f"One of: {platforms}."
    )
    export_cmd.add_argument("-o", "--out", type=Path, required=True, help="Output .tar.gz path.")
    export_cmd.add_argument("--root", type=Path, default=None, help="Project root (default: cwd).")
    export_cmd.add_argument("--full", action="store_true", help="Include raw session transcripts.")
    export_cmd.add_argument(
        "--sanitize", action="store_true", help="Redact common secret patterns."
    )

    import_cmd = context_sub.add_parser("import", help="Restore a tarball onto this machine.")
    import_cmd.add_argument("bundle", type=Path, help="Path to a bundle .tar.gz.")
    import_cmd.add_argument("--into", type=Path, default=None, help="Project root (default: cwd).")
    import_cmd.add_argument(
        "--dry-run", action="store_true", help="Print planned operations without applying them."
    )

    migrate_cmd = context_sub.add_parser("migrate", help="Hand off context between platforms.")
    migrate_cmd.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="NAME",
        help=f"Source platform: {platforms}.",
    )
    migrate_cmd.add_argument(
        "--to", dest="target", required=True, metavar="NAME", help=f"Target platform: {platforms}."
    )
    migrate_cmd.add_argument("--into", type=Path, default=None, help="Project root (default: cwd).")
    migrate_cmd.add_argument(
        "--dry-run", action="store_true", help="Print planned operations without applying them."
    )


def _missing_hook_scripts(manifest: Manifest) -> list[Path]:
    """Return the resolved paths of hook scripts that do not exist on disk."""
    paths = (*manifest.pre_install, *manifest.post_install)
    return [p for p in paths if not p.is_file()]


def _print_ops(ops: list[Operation], dest: TextIO, *, force_verbose: bool = False) -> None:
    """Print a list of planned operations to the given output stream."""
    if not ops:
        console.info("no operations to perform", dest=dest)
        return
    console.info(f"planned {len(ops)} operation(s):", dest=dest)
    for op in ops:
        if console.verbose or force_verbose:
            console.debug(f"{type(op).__name__}: {op.description or '<no description>'}", dest=dest)


def _print_meta(manifest: Manifest, dest: TextIO) -> None:
    """Print the manifest's documentation header and resolved extends chain."""
    meta = manifest.meta
    title = f"{meta.name} v{meta.version}" if meta.version else meta.name
    console.info(title, dest=dest)
    print(f"  {meta.description}", file=dest)
    if meta.author:
        print(f"  author: {meta.author}", file=dest)
    if meta.homepage:
        print(f"  homepage: {meta.homepage}", file=dest)
    for ref in manifest.extends:
        print(f"  extends: {ref}", file=dest)


def main(argv: Sequence[str] | None = None) -> ExitCode:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    console.verbose = args.verbose

    try:
        if args.command == "deploy":
            return _cmd_deploy(args)
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "clean":
            return _cmd_clean(args)
        # Subparsers are declared required=True, so "context" is the only remaining command.
        return _cmd_context(args)
    except BoffError as exc:
        console.error(str(exc), dest=sys.stderr)
        return ExitCode.ERROR


def _scope(root: Path | None) -> Scope:
    """Create a workspace scope from a root path."""
    return Scope(kind=ScopeKind.WORKSPACE, workspace_root=root or Path.cwd())


def _load(ref: str) -> Manifest:
    """Resolve a manifest reference (a local path or a Git URL) and load what it names.

    A reference says *what* to deploy, never *where*: the scope stays the current directory,
    which is what a relative reference resolves against.
    """
    return load_manifest(resolve_ref(ref, base_root=Path.cwd()))


def _confirm_wipe(ops: list[Operation]) -> bool:
    """Prompt before a destructive wipe."""
    console.warning("about to delete (destructive):", dest=sys.stderr)
    for op in ops:
        if isinstance(op, DeleteOperation):
            console.warning(f"  - {op.target}", dest=sys.stderr)
    return input("proceed? [y/N] ").strip().lower() in {"y", "yes"}


def _confirm_or_abort(ops: list[Operation], *, wipe: bool) -> bool:
    """Return True to proceed; for a wipe, prompt and print an abort message if declined."""
    if wipe and not _confirm_wipe(ops):
        console.error("aborted: wipe not confirmed", dest=sys.stderr)
        return False
    return True


def _cmd_context(args: argparse.Namespace) -> ExitCode:
    """Dispatch a ``boff context`` subcommand (export, import, or migrate)."""
    if args.context_command == "export":
        return _cmd_context_export(args)
    if args.context_command == "import":
        return _cmd_context_import(args)
    # Subparsers are declared required=True, so "migrate" is the only remaining subcommand.
    return _cmd_context_migrate(args)


def _cmd_context_export(args: argparse.Namespace) -> ExitCode:
    """Collect the source platform's context and write it to a ``.tar.gz`` bundle."""
    provider = get_provider(args.platform)
    bundle = provider.collect(scope=_scope(args.root), full=args.full)
    write_bundle(bundle, args.out, sanitize=args.sanitize)
    console.info(
        f"exported {args.platform} context to {args.out}: "
        f"{len(bundle.instructions)} instruction file(s), {len(bundle.plans)} plan(s), "
        f"{len(bundle.memory)} memory file(s), {len(bundle.sessions)} session(s)."
    )
    return ExitCode.OK


def _cmd_context_import(args: argparse.Namespace) -> ExitCode:
    """Read a bundle and materialize its context onto this machine."""
    bundle = read_bundle(args.bundle)
    provider = get_provider(bundle.platform)
    ops = provider.materialize(bundle, scope=_scope(args.into))
    return _apply_or_print(ops, dry_run=args.dry_run)


def _cmd_context_migrate(args: argparse.Namespace) -> ExitCode:
    """Hand off context from the source platform to the target, writing it in place."""
    source = get_provider(args.source)
    target = get_provider(args.target)
    scope = _scope(args.into)
    target_bundle = migrate(source.collect(scope=scope), target)
    ops = target.materialize(target_bundle, scope=scope)
    return _apply_or_print(ops, dry_run=args.dry_run)


def _apply_or_print(ops: list[Operation], *, dry_run: bool) -> ExitCode:
    """Execute ``ops`` (or, when ``dry_run``, only print them) and return the exit code."""
    if dry_run:
        _print_ops(ops, sys.stdout, force_verbose=True)
        return ExitCode.OK
    execute(ops)
    _print_ops(ops, sys.stderr)
    return ExitCode.OK


def _hook_ctx(
    phase: HookPhase, args: argparse.Namespace, manifest: Manifest, scope: Scope, count: int
) -> HookContext:
    """Construct the context for a hook execution phase."""
    return HookContext(
        phase=phase,
        platforms=tuple(args.platforms),
        scope=scope,
        manifest_root=manifest.root,
        ops_count=count,
    )


def _cmd_deploy(args: argparse.Namespace) -> ExitCode:
    """Deploy a manifest: run hooks, apply the clear/forward/cleanup ops, and save state."""
    manifest = _load(args.path)
    scope = _scope(None)
    sp = state_path(scope)
    prior = load_state(sp)
    plan = plan_deploy(manifest, args.platforms, scope, prior, wipe=args.wipe, clean=args.clean)
    all_ops = plan.clear + plan.forward + plan.cleanup

    missing = _missing_hook_scripts(manifest)

    if args.dry_run:
        _print_meta(manifest, sys.stdout)
        _print_ops(all_ops, sys.stdout, force_verbose=True)
        for path in missing:
            console.error(f"missing hook script: {path}", dest=sys.stderr)
        return ExitCode.ERROR if missing else ExitCode.OK

    if missing:
        for path in missing:
            console.error(f"missing hook script: {path}", dest=sys.stderr)
        return ExitCode.ERROR

    if not _confirm_or_abort(plan.clear, wipe=args.wipe):
        return ExitCode.ERROR

    run_hooks(
        manifest.pre_install,
        _hook_ctx(HookPhase.PRE_INSTALL, args, manifest, scope, len(all_ops)),
    )
    execute(plan.clear)
    execute(plan.forward)
    execute(plan.cleanup)
    save_state(plan.next_state, sp)
    if not args.no_ignore:
        update_workspace_gitignore(plan.next_state, scope)
    run_hooks(
        manifest.post_install,
        _hook_ctx(HookPhase.POST_INSTALL, args, manifest, scope, len(all_ops)),
    )

    _print_meta(manifest, sys.stderr)
    _print_ops(all_ops, sys.stderr)
    return ExitCode.OK


def _cmd_check(args: argparse.Namespace) -> ExitCode:
    """Verify the workspace still matches what a deploy of this manifest would write."""
    # Probe before loading the manifest, so an unknown --platform fails fast.
    found = probe(args.platforms) if args.probe else {}
    absent = [name for name, path in found.items() if path is None]
    if absent:
        raise BoffError(f"platform CLI not found on PATH: {', '.join(absent)}")

    manifest = _load(args.path)
    scope = _scope(None)
    sp = state_path(scope)
    _print_meta(manifest, sys.stdout)
    if not sp.exists():
        console.warning(f"no deploy state at {sp}: has this workspace been deployed?")

    units = plan_units(manifest, args.platforms, scope)
    active = [*args.platforms, *(tool_owner(name) for name in manifest.tool_files)]
    report = verify(units, load_state(sp), scope, active)
    _print_report(report, found, sys.stdout)
    return ExitCode.ERROR if report.failed else ExitCode.OK


# One console style per status. Failing statuses print red, degraded ones yellow, `ok` green.
_STATUS_STYLE: dict[Status, Callable[[str, TextIO], None]] = {
    Status.OK: console.info,
    Status.MISSING: console.error,
    Status.DRIFTED: console.error,
    Status.STALE: console.error,
    Status.DROPPED: console.warning,
    Status.UNSUPPORTED: console.warning,
    Status.UNVERIFIABLE: console.warning,
}


def _print_report(report: CheckReport, found: dict[str, str | None], dest: TextIO) -> None:
    """Print a check report grouped by owner, then a one-line tally."""
    by_owner: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_owner.setdefault(finding.owner, []).append(finding)

    for owner, findings in by_owner.items():
        shown = [f for f in findings if _is_shown(f)]
        if not shown:
            continue  # every finding for this owner is an `ok` the user did not ask to see
        binary = found.get(owner)
        suffix = f"  (found: {binary})" if binary else ""
        print(f"\n{owner}{suffix}", file=dest)
        for finding in shown:
            _print_finding(finding, dest)

    counts = report.counts()
    tally = ", ".join(f"{total} {status}" for status, total in counts.items())
    print(f"\n{tally or 'nothing to check'}", file=dest)


def _is_shown(finding: Finding) -> bool:
    """Return True unless the finding is an ``ok`` line and the user did not pass --verbose."""
    return finding.status is not Status.OK or console.verbose


def _print_finding(finding: Finding, dest: TextIO) -> None:
    """Print one finding, with its detail indented underneath."""
    where = f"  {_display_path(finding.target)}" if finding.target else ""
    _STATUS_STYLE[finding.status](f"  {finding.status:<10}{finding.label:<24}{where}", dest)
    if finding.detail and finding.status is not Status.OK:
        print(f"      {finding.detail}", file=dest)


def _display_path(target: Path) -> str:
    """Render a target relative to the current directory when it lies below it."""
    try:
        return str(target.relative_to(Path.cwd()))
    except ValueError:
        return str(target)


def _cmd_clean(args: argparse.Namespace) -> ExitCode:
    """Remove boff's recorded footprint (or wipe native config) for the scope."""
    scope = _scope(args.root)
    sp = state_path(scope)
    prior = load_state(sp)
    platforms: list[str] = args.platforms or []

    if args.wipe and not platforms:
        console.error("--wipe requires --platform", dest=sys.stderr)
        return ExitCode.USAGE

    cleared_owners = list(platforms) if platforms else recorded_owners(prior, scope)
    ops = build_clear_ops(prior, scope, cleared_owners, wipe=args.wipe)

    if args.dry_run:
        _print_ops(ops, sys.stdout, force_verbose=True)
        return ExitCode.OK

    if not _confirm_or_abort(ops, wipe=args.wipe):
        return ExitCode.ERROR

    execute(ops)
    new_state = without_owners(prior, scope, cleared_owners)
    save_state(new_state, sp)
    if not args.no_ignore:
        update_workspace_gitignore(new_state, scope)
    _print_ops(ops, sys.stderr)
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
