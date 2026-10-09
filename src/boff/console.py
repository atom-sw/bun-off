"""Console output formatting and routing."""

from __future__ import annotations

import sys
from enum import StrEnum
from typing import TextIO


class Color(StrEnum):
    """ANSI color codes for terminal output."""

    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"


class Symbol(StrEnum):
    """Symbols used to prefix log messages."""

    CHECK = "✓"
    DOT = "•"
    WARN = "⚠"
    CROSS = "✗"


class Console:
    """A simple message emitter for console output with optional colors and verbosity."""

    def __init__(self) -> None:
        self.verbose = False

    def _format(self, color: Color, symbol: Symbol, msg: str, dest: TextIO) -> str:
        if dest.isatty():
            return f"{color}{symbol}{Color.RESET} {msg}"
        return f"{symbol} {msg}"

    def info(self, msg: str, dest: TextIO | None = None) -> None:
        """Print an informational message, prefixed with a green checkmark."""
        # Bind a fresh local: `sys.stdout` is typed `TextIO | Any`, so assigning it back to
        # `dest` would leave the parameter's declared `TextIO | None` unnarrowed.
        out: TextIO = dest if dest is not None else sys.stdout
        print(self._format(Color.GREEN, Symbol.CHECK, msg, out), file=out)

    def debug(self, msg: str, dest: TextIO | None = None, *, force: bool = False) -> None:
        """Print a debug message, prefixed with a blue dot, only if verbose or ``force``."""
        out: TextIO = dest if dest is not None else sys.stdout
        if self.verbose or force:
            print(self._format(Color.BLUE, Symbol.DOT, msg, out), file=out)

    def warning(self, msg: str, dest: TextIO | None = None) -> None:
        """Print a warning message, prefixed with a yellow warning symbol."""
        out: TextIO = dest if dest is not None else sys.stderr
        print(self._format(Color.YELLOW, Symbol.WARN, msg, out), file=out)

    def error(self, msg: str, dest: TextIO | None = None) -> None:
        """Print an error message, prefixed with a red cross."""
        out: TextIO = dest if dest is not None else sys.stderr
        print(self._format(Color.RED, Symbol.CROSS, msg, out), file=out)


console = Console()
