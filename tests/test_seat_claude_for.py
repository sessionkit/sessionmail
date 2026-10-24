"""SM-706: `_seat.claude_for` - is a claude running for THIS session now?

A `session start` context generator (a wake item) runs before claude is
exec'd, so nothing CAN be listening yet. `pager status` tells that state
apart from deafness with this one question (rca-001 sec 2, MEASURED): a
claude with `--session-id SESSION` on its argv, else a claude ancestor of
the asker, else a LIVE sidecar naming the session (pid AND procStart, so a
reused pid is not a session).

Hermetic: the `ps` table and the live-sidecar source are injected, and the
sidecar directory is the sandbox's (`SESSION_SIDECAR_DIR`, set by conftest).
No test here reads the runner's `~/.claude/sessions` or its process table.
The real function is `conftest.REAL_CLAUDE_FOR` (conftest pins "claude is
up" for every other suite).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import sessiontools.procs
from sessiontools import claude_code
from sessiontools.procs import Proc, parse_ctime

from conftest import REAL_CLAUDE_FOR as claude_for
from sessionmail import _seat

SESSION = "sess-a"
ME = 7_100_001              # the asking process, in the table only
CLAUDE = 7_100_002          # a claude, in the table only
SHELL = 7_100_003
PROC_START = "Sun Jun 09 12:00:00 2030"     # UTC ctime, as Claude Code writes it


def _never():
    """A live-sidecar source that must not be asked."""
    raise AssertionError("the sidecar source was read")


def _sidecar(pid: int, session: str = SESSION, proc_start: str = PROC_START) -> Path:
    d = Path(os.environ["SESSION_SIDECAR_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{pid}.json"
    path.write_text(json.dumps({
        "pid": pid, "sessionId": session, "status": "idle",
        "updatedAt": 1_923_482_096_000, "procStart": proc_start,
    }))
    return path


def _live_with(table: dict[int, Proc]):
    """sessiontools' own liveness rule, over an injected process table."""
    return lambda: claude_code.live(procs=table)


# ── the three sources ────────────────────────────────────────────────────

def test_a_claude_ancestor_of_the_asker_is_found_with_no_sidecar():
    """Test 1: the in-claude world - a hook, the Bash tool, the monitor."""
    ps = lambda: ({ME: SHELL, SHELL: CLAUDE, CLAUDE: 1},
                  {ME: "pager status --new", SHELL: "/bin/zsh -c x", CLAUDE: "claude"})
    assert claude_for(SESSION, ps=ps, me=ME, live=_never) == CLAUDE


def test_a_claude_with_the_session_id_on_its_argv_is_found():
    """Test 2: no ancestor (a check from a plain terminal), but the
    session's claude carries `--session-id SESSION`."""
    ps = lambda: ({ME: 1, CLAUDE: 1},
                  {ME: "pager status", CLAUDE: f"claude --session-id {SESSION} --plugin-dir /x"})
    assert claude_for(SESSION, ps=ps, me=ME, live=_never) == CLAUDE


def test_a_live_sidecar_naming_the_session_is_found():
    """Test 3: no ancestor and no argv match (`claude --resume`, a hand
    launch): a sidecar naming SESSION whose pid and procStart match."""
    _sidecar(CLAUDE)
    table = {CLAUDE: Proc(CLAUDE, 1, parse_ctime(PROC_START))}
    ps = lambda: ({ME: 1, CLAUDE: 1}, {ME: "pager status", CLAUDE: "claude --resume"})
    assert claude_for(SESSION, ps=ps, me=ME, live=_live_with(table)) == CLAUDE


def test_a_sidecar_whose_pid_is_dead_or_reused_is_not_a_claude():
    """Test 4: a sidecar a crash left behind (pid gone), or whose pid the OS
    handed to something that started later (procStart does not match)."""
    _sidecar(CLAUDE)
    ps = lambda: ({ME: 1}, {ME: "pager status"})
    assert claude_for(SESSION, ps=ps, me=ME, live=_live_with({})) is None
    reused = {CLAUDE: Proc(CLAUDE, 1, parse_ctime(PROC_START) + 3600)}
    assert claude_for(SESSION, ps=ps, me=ME, live=_live_with(reused)) is None


def test_nothing_at_all_is_none_the_pre_claude_case():
    """Test 5: a detached `session start` runner (ppid 1), a fresh session
    id, no sidecar: claude is not running for this session."""
    ps = lambda: ({ME: 1}, {ME: "python session-start"})
    assert claude_for(SESSION, ps=ps, me=ME, live=lambda: []) is None


def test_the_sidecar_source_is_not_read_when_ps_already_answered():
    """Test 6: the cost pin. An in-claude caller pays for one `ps`, never
    for a sidecar scan."""
    ancestor = lambda: ({ME: CLAUDE, CLAUDE: 1}, {ME: "pager", CLAUDE: "claude"})
    by_argv = lambda: ({ME: 1, CLAUDE: 1},
                       {ME: "pager", CLAUDE: f"claude --session-id {SESSION}"})
    assert claude_for(SESSION, ps=ancestor, me=ME, live=_never) == CLAUDE
    assert claude_for(SESSION, ps=by_argv, me=ME, live=_never) == CLAUDE


# ── the edges ────────────────────────────────────────────────────────────

def test_another_sessions_sidecar_is_not_this_sessions_claude():
    _sidecar(CLAUDE, session="sess-b")
    table = {CLAUDE: Proc(CLAUDE, 1, parse_ctime(PROC_START))}
    ps = lambda: ({ME: 1}, {ME: "pager status"})
    assert claude_for(SESSION, ps=ps, me=ME, live=_live_with(table)) is None


def test_another_sessions_argv_is_not_this_sessions_claude():
    ps = lambda: ({ME: 1, CLAUDE: 1}, {ME: "pager", CLAUDE: "claude --session-id sess-b"})
    assert claude_for(SESSION, ps=ps, me=ME, live=lambda: []) is None


def test_a_claude_ancestor_carrying_this_sessions_id_counts():
    """SM-706 amendment 2, the side that holds today and after SM-558: an
    ancestor claude launched with THIS session's id is this session's."""
    ps = lambda: ({ME: SHELL, SHELL: CLAUDE, CLAUDE: 1},
                  {ME: "pager", SHELL: "/bin/zsh -c x",
                   CLAUDE: f"claude --session-id {SESSION}"})
    assert claude_for(SESSION, ps=ps, me=ME, live=_never) == CLAUDE


def test_a_claude_ancestor_for_another_session_is_not_this_sessions_claude():
    """SM-706 amendment 2: a generator for
    session B run under a live claude for session A (`--session-id` A on its
    argv; repro sec 5, the SM-454 class) is pre-claude for B. One
    implementation: SM-558's other-id check, inside `_session_claudes`
    (ruling, brief 2030-12-08-2320-super-414839f4.md). Owed by fix-002,
    written here at rebase-001."""
    ps = lambda: ({ME: SHELL, SHELL: CLAUDE, CLAUDE: 1},
                  {ME: "session start", SHELL: "/bin/zsh -c x",
                   CLAUDE: "claude --session-id sess-b"})
    assert claude_for(SESSION, ps=ps, me=ME, live=lambda: []) is None


def test_the_session_id_is_read_as_one_whole_token_in_both_forms():
    """rebase-001: the argv pass reads the id through SM-558's
    `_claude_session` too, so `_seat.py` parses `--session-id` once: the
    `--session-id=SESSION` form is found, and a longer id that merely starts
    with SESSION is another session's."""
    by_eq = lambda: ({ME: 1, CLAUDE: 1},
                     {ME: "pager", CLAUDE: f"claude --session-id={SESSION} --plugin-dir /x"})
    assert claude_for(SESSION, ps=by_eq, me=ME, live=_never) == CLAUDE
    longer = lambda: ({ME: 1, CLAUDE: 1},
                      {ME: "pager", CLAUDE: f"claude --session-id {SESSION}b"})
    assert claude_for(SESSION, ps=longer, me=ME, live=lambda: []) is None


def test_no_session_id_never_reads_the_sidecars():
    """`-` (no session id at all) names no sidecar; only `ps` is asked."""
    ps = lambda: ({ME: 1}, {ME: "pager"})
    assert claude_for("-", ps=ps, me=ME, live=_never) is None
    assert claude_for("", ps=ps, me=ME, live=_never) is None


def test_the_default_sidecar_source_honours_session_sidecar_dir(monkeypatch):
    """No `live` injected: the sidecars are read from SESSION_SIDECAR_DIR
    (the conftest sandbox), through sessiontools' one driver."""
    _sidecar(CLAUDE)
    monkeypatch.setattr(sessiontools.procs, "table",
                        lambda: {CLAUDE: Proc(CLAUDE, 1, parse_ctime(PROC_START))})
    ps = lambda: ({ME: 1}, {ME: "pager status"})
    assert claude_for(SESSION, ps=ps, me=ME) == CLAUDE
    monkeypatch.setenv("SESSION_SIDECAR_DIR", str(Path(os.environ["HOME"]) / "elsewhere"))
    assert claude_for(SESSION, ps=ps, me=ME) is None


def test_listening_by_ancestry_still_finds_through_the_shared_walk():
    """The extraction (rca-001 sec 4: shared, not copied) kept
    `listening_by_ancestry`'s answers: by argv, and by ancestor."""
    listener = 7_100_004
    cmd = {CLAUDE: f"claude --session-id {SESSION}", listener: "/x/bin/pager listen"}
    assert _seat.listening_by_ancestry(SESSION, {listener: CLAUDE, CLAUDE: 1}, cmd, ME) == listener
    cmd = {CLAUDE: "claude", listener: "/x/bin/pager listen", ME: "pager status"}
    ppid = {listener: CLAUDE, CLAUDE: 1, ME: CLAUDE}
    assert _seat.listening_by_ancestry(SESSION, ppid, cmd, ME) == listener
    assert _seat.listening_by_ancestry(SESSION, {ME: 1}, {ME: "pager"}, ME) is None


# ── is_monitor (change B's question) ─────────────────────────────────────

def test_is_monitor_reads_the_command_line():
    mon, bare = 7_100_005, 7_100_006
    ps = lambda: ({}, {mon: "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor",
                       bare: "/x/.venv/bin/python /x/.venv/bin/pager listen"})
    assert _seat.is_monitor(mon, ps=ps) is True
    assert _seat.is_monitor(bare, ps=ps) is False
    assert _seat.is_monitor(7_100_007, ps=ps) is None       # ps does not know it
    assert _seat.is_monitor(mon, ps=lambda: ({}, {})) is None
