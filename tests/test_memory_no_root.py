"""SM-1023: a fresh seat's first `memory ls` says "no memory yet", not "shipwreck".

A default seat is agent `claude`, has no `$CLAUDE_ROOT` (only a kicked seat's
launcher mints one), and has no [ROOT] (nothing mints one until `memory init`).
At sessionmail 963e307 `memory ls /` and `memory tree /` said, in that state:

    memory: no $CLAUDE_ROOT in the environment — falling back to the subject
    search (shipwreck path)
    no canonical root found (subject:"claude ROOT")

and exited 1, which their own exit tables call "usage". Now, through the
`memory` door: ONE stderr line naming the state and the variable it wanted, no
"shipwreck", exit 2 ("not found", as the epilogs now say). A root that EXISTS
with no hint is still the old launcher's shipwreck, said aloud.

notmuch is a stub on PATH (the oracle calls it by name): it answers the subject
search with nothing, or with one thread when NM_ROOT is set. HOME is the
conftest's scratch HOME, and the SAFETY env is set on every run.
`tests/mhs/test_session_mail_no_root.sh` covers the oracle by path, every caller.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs

STUB = """#!/usr/bin/env bash
echo "$*" >> "{log}"
case "$*" in
  *--output=threads*) [ -n "${{NM_ROOT:-}}" ] && echo "thread:00000000000000aa" ;;
  *show*) echo "[]" ;;
esac
exit 0
"""


def seat_env(tmp: Path, **extra: str) -> tuple[dict[str, str], Path]:
    """The sandbox env with a notmuch stub first on PATH, agent `claude`, no
    `$CLAUDE_ROOT`, and the SAFETY env. Returns it and the stub's call log."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    log = tmp / "notmuch.log"
    stub = bin_dir / "notmuch"
    stub.write_text(STUB.format(log=log))
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in (_mhs.ORACLE_VAR, "CLAUDE_ROOT")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", CLAUDE_CODE_AGENT="claude",
               SESSION_AGENT="", SESSIONMAIL_SENDMAIL="drop",
               SESSIONMAIL_VMAILBOX_GEN="/usr/bin/true", **extra)
    return env, log


def memory(env: dict[str, str], *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sessionmail.memory", *argv],
                          capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.parametrize("argv", [("ls", "/"), ("ls",), ("tree", "/")])
def test_no_root_is_one_plain_line_exit_2_and_no_shipwreck(tmp_path, argv):
    env, log = seat_env(tmp_path)
    p = memory(env, *argv)
    assert "Traceback" not in p.stderr, p.stderr
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    assert "shipwreck" not in p.stderr.lower()
    (line,) = p.stderr.splitlines()
    assert line.startswith("memory: no memory yet for claude: "), line
    assert "no [ROOT] from claude has been minted" in line, line
    assert "$CLAUDE_ROOT is not set" in line, line   # the variable it wanted (spec)
    # `memory init` refuses today on a seat with no box key (measured, SM-1023
    # fix-001): the line must not send a stranger to run it.
    assert "run `memory init" not in line and "memory init claude" not in line, line
    assert 'subject:"claude ROOT"' in log.read_text(), "the subject search must still run"


def test_a_found_root_with_no_hint_still_says_shipwreck(tmp_path):
    env, _ = seat_env(tmp_path, NM_ROOT="1")
    p = memory(env, "ls", "/")
    # The stub's `show` answers nothing, so the render refuses after the search.
    # The control is the search half's line: once, and never "no memory yet".
    assert p.stderr.count("shipwreck path") == 1, p.stderr
    assert "no memory yet" not in p.stderr, p.stderr


@pytest.mark.parametrize("verb", ["ls", "tree", "cat"])
def test_the_exit_table_names_the_no_root_state(verb):
    p = subprocess.run([sys.executable, "-m", "sessionmail.memory", verb, "--help"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    exit_line = " ".join(p.stdout[p.stdout.index("Exit:"):].split())
    assert "2 " in exit_line and "no root yet" in exit_line, exit_line
