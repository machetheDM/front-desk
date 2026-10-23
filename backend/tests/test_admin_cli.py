"""Every `python -m frontdesk.admin ...` line in the docs and docstrings parses with the real CLI.

The owner pastes these commands. A first version defined --profile only on the top-level
parser, so every documented command ("admin create-staff --profile fd-dev ...") failed with
"unrecognized arguments". This test would have caught it, and now does.
"""

from __future__ import annotations

import pathlib
import re
import shlex

import pytest

from frontdesk import admin

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES = [*ROOT.joinpath("docs").rglob("*.md"), ROOT / "AGENTS.md", *ROOT.joinpath("backend", "frontdesk").rglob("*.py")]


def documented_commands() -> list[tuple[str, str]]:
    found = []
    for f in SOURCES:
        text = f.read_text(encoding="utf-8").replace("`\n", " ").replace("\\\\\n", " ")  # PowerShell and docstring continuations
        for line in text.splitlines():
            if "python -m frontdesk.admin " not in line:
                continue
            cmd = line.split("python -m frontdesk.admin ", 1)[1].split("`")[0].strip()  # inline code ends at its backtick
            if len(cmd.split()) < 2:
                continue  # prose naming a subcommand, not a command to paste
            found.append((f"{f.relative_to(ROOT)}", cmd))
    return found


def _argv(cmd: str) -> list[str]:
    cmd = re.sub(r"\$\w+", "x", cmd)                                   # PowerShell variables
    cmd = re.sub(r"<uuid>|<tenant uuid>", "00000000-0000-4000-8000-000000000001", cmd)
    cmd = re.sub(r"<[^>]+>", "x", cmd).replace("\\\\", "\\")           # placeholders, docstring escapes
    return shlex.split(cmd.replace("...", "x"), posix=True)


def test_the_docs_show_at_least_one_command_for_every_subcommand_that_writes():
    shown = {_argv(c)[0] for _, c in documented_commands()}
    assert {"create-staff", "map-whatsapp", "pause-assistant", "deactivate-staff", "offboard-erase", "start-experiment"} <= shown


@pytest.mark.parametrize("where, cmd", documented_commands())
def test_every_documented_admin_command_parses(where, cmd):
    try:
        admin.build_parser().parse_args(_argv(cmd))
    except SystemExit as e:
        pytest.fail(f"{where}: `admin {cmd}` does not parse (exit {e.code})")
