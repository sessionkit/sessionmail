"""SM-983 Q2 (ruled YES by the super): inside a
subagent, the mail-guard denies `pager settings hooks on|off`, and the bare
read stays allowed.

The host file is HOST-WIDE: a subagent that ran `off` would silence every
seat's hooks on the host, so the switch is wider than a seat's state, the
kind SM-837 already denies (`SUBAGENT_SEAT_ROWS`). One more row, one more
branch in `subagent_verb`, deny direction only; a main thread's decision
cannot move. `pager settings` is no MCP tool (hidden), so the MCP door
never builds these words; the row's MCP way only points at a read.

Every cell runs the hook as Claude Code does: the shim on stdin, the
decision on stdout.
"""

from __future__ import annotations

import pytest
import test_mail_guard_subagent as bash_suite
from test_mail_guard import guard
from test_mail_guard_subagent import decision

WHAT = "pager settings hooks"

#: (cell id, command): each DENIED inside a subagent.
BASH_DENY = [
    ("on", "pager settings hooks on"),
    ("off", "pager settings hooks off"),
    ("dd-on", "pager settings hooks -- on"),
    ("group-dd", "pager -- settings hooks off"),
    ("noun-dd", "pager settings -- hooks on"),
    ("help-after-cut", "pager settings hooks on -- --help"),
    ("a-typo-is-still-a-write-attempt", "pager settings hooks yes"),
    ("path", "~/.local/bin/pager settings hooks on"),
    ("after-and", "cd /scratch && pager settings hooks off"),
    ("bash-c", "bash -c 'pager settings hooks off'"),
    ("env", "env X=1 pager settings hooks on"),
]

#: (cell id, command): each ALLOWED inside a subagent.
BASH_ALLOW = [
    ("bare-read", "pager settings hooks"),
    ("bare-read-dd", "pager settings hooks --"),
    ("help", "pager settings hooks --help"),
    ("h", "pager settings hooks -h on"),
    ("group-bare", "pager settings"),
    ("install", "pager settings install"),      # not this row: unchanged
    ("migrate-dry", "pager settings migrate --dry-run"),
    ("quoted", "grep 'pager settings hooks off' notes.md"),
    ("echo", "echo pager settings hooks off"),
]


@pytest.mark.parametrize("cell,command", BASH_DENY, ids=[c for c, _ in BASH_DENY])
def test_subagent_is_denied(cell, command):
    proc = bash_suite.run(command, subagent=True)
    assert decision(proc) == "deny", f"{cell}: {command!r} was allowed"
    assert guard.decide_subagent(command) == guard.subagent_reason(WHAT), cell


@pytest.mark.parametrize("cell,command", BASH_ALLOW, ids=[c for c, _ in BASH_ALLOW])
def test_subagent_is_allowed(cell, command):
    proc = bash_suite.run(command, subagent=True)
    assert decision(proc) == "allow", f"{cell}: {command!r} was denied: {proc.stdout}"


@pytest.mark.parametrize("cell,command", BASH_DENY, ids=[c for c, _ in BASH_DENY])
def test_a_main_thread_is_never_denied_by_the_row(cell, command):
    proc = bash_suite.run(command, subagent=False)
    assert decision(proc) == "allow", f"{cell}: main thread {command!r} was denied"


def test_the_row_says_what_it_would_do_and_the_read():
    reason = guard.subagent_reason(WHAT)
    assert f"`{WHAT}` here" in reason
    assert "every seat's hooks on this host" in reason
    assert f"`{WHAT}` (bare)" in reason
    assert "take or mark" not in reason and "\n" not in reason


def test_the_row_is_in_the_table():
    assert WHAT in guard.SUBAGENT_SEAT_ROWS
    assert guard.subagent_verb("pager", ["settings", "hooks", "on"]) == WHAT
    assert guard.subagent_verb("pager", ["settings", "hooks"]) is None
    assert guard.subagent_verb("pager", ["settings", "install"]) is None
