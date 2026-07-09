"""The package version has exactly one source of truth: the installed distribution metadata."""

from __future__ import annotations

from importlib.metadata import version

import pytest

import boff
from boff.cli import main

DISTRIBUTION = "bun-off"


def test_dunder_version_mirrors_the_installed_distribution_metadata() -> None:
    assert boff.__version__ == version(DISTRIBUTION)


def test_version_flag_prints_the_package_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"boff {boff.__version__}"
