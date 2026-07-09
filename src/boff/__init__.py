"""bun-off: author once, deploy direct."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bun-off")
except PackageNotFoundError:  # a source tree that was never installed has no metadata to read
    __version__ = "0.0.0+unknown"
