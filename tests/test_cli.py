"""Smoke tests of the command-line interface (same tests in every repo with a CLI)."""

import pytest
from typer.testing import CliRunner

from snapchat_cli import __version__
from snapchat_cli.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"snapchat-cli {__version__}"


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "user",
        "search",
        "place",
        "nearby",
        "get",
    ):
        assert command in result.output


@pytest.mark.parametrize("command", ["user", "search", "place", "nearby", "get"])
def test_command_help(command):
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "--proxy" in result.output
