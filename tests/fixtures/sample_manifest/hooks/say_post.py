"""Post-install hook: read context from stdin and print a confirmation."""

from boff.hooks import HookContext


def main() -> None:
    ctx = HookContext.from_stdin()
    print(f"[post_install] phase={ctx.phase} platforms={ctx.platforms} ops={ctx.ops_count}")


if __name__ == "__main__":
    main()
