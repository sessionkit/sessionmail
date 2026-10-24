"""SM-454 repro-001: "this session's listener" is matched on ANCESTRY, not on
the session id, so `pager off` and the `pager listen` refusal can take another
session's listener as their own.

Three rows, each RED on 5dc20f8 (repro-001 committed them as strict xfails)
and GREEN since fix-001, which checks two legs on every candidate listener
(`_seat.listening_by_ancestry`): (a) its OWN nearest claude is this
session's, and (b) no other session's live `listening` stamp names it:

(A) DESCENDANT, a measured tree (SM-706): `pager listen` 89479 <- bash 89471 <- `claude --session-id
    6189c015` 77901 (a headless worker) <- tm 76582 <- bash 76563 <-
    `claude --session-id 32c90a81` 30688 (the foreman). Asked for the
    FOREMAN's session, `is_listening` / `other_listener` answer the worker's
    listener, because a candidate counts when ANY claude of this session is
    on its chain, and the nearer claude carrying another id does not stop it.
(B) OFF under a fake sid, a measured tree (TM-5442): `pager off` for a session F that owns no listener,
    run inside a tree whose live listener is session A's, stops A's
    listener. Red today only under an ID-LESS claude (an interactive
    `claude`, `--resume`, `--continue`); under `claude --session-id A`
    SM-558 already answers None (the control test below pins that).
(C) ID-LESS: an interactive `claude` (no --session-id) with its own
    listener, stamped in session A's `stamps/listening`; a query for a
    different sid F run under it is credited A's listener.

Hermetic, on test_pager_listen.py's rig (a maildir root and a seat home under
tmp_path, so SESSIONMAIL_HOME is scratch). The process table is INJECTED
(`_seat._ps_table`, or the `ps=` seam): every pid in it is a table-only pid
(7_454_xxx, past any real pid) except this test's own pid and a harmless
Python sleeper standing in for the listener. Nothing here runs a real
`pager listen`, and `pager off` runs in-process against the sleeper only,
under a made-up session id: never against a live session.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest
from click.testing import CliRunner

from conftest import REAL_CLAUDE_FOR
from sessionmail import _seat, pager
from test_pager_listen import EX_TIMEOUT, rig  # noqa: F401

#: The two "this session's listener" questions, each its own red: the NOT
#: ARMED check (status, the hooks, `pager off`) and the `pager listen` refusal.
ASK = pytest.mark.parametrize("ask", ["is_listening", "other_listener"])

SESSION_A = "sess-a"                 # the rig's session: the listener's owner
LISTEN_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen"

# Row A's tree, the measurement's shape with table-only pids (the
# measured pid each stands for is in the comment).
FOREMAN = 7_454_001                  # claude --session-id <foreman>   (30688)
F_BASH = 7_454_002                   # bash                            (76563)
TM = 7_454_003                       # tm task work                    (76582)
WORKER = 7_454_004                   # claude --session-id <worker>    (77901)
W_BASH = 7_454_005                   # bash                            (89471)
                                     # the worker's `pager listen` is the sleeper (89479)
ME_BASH = 7_454_006                  # the bash this test's pid hangs off
CLAUDE = 7_454_010                   # rows B, C: the one claude
L_BASH = 7_454_011                   # rows B, C: the bash under the listener
SESSION_W = "sess-worker"


@pytest.fixture
def listener():
    """A live, harmless process to stand in for a `pager listen`, reaped as
    it exits so `pager off`'s liveness poll sees it go if it is signalled."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        reaper.join(5)


def _ps(monkeypatch, ppid: dict[int, int], cmd: dict[int, str]):
    """Inject the table everywhere `_seat` reads `ps`, and return the seam."""
    table = lambda: (dict(ppid), dict(cmd))  # noqa: E731
    monkeypatch.setattr(_seat, "_ps_table", table)
    return table


def _stamp_as(pid: int, session: str) -> None:
    path = _seat.stamp_dir(None, session) / _seat.LISTENING_STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")


def _descendant_tree(listen: int) -> tuple[dict[int, int], dict[int, str]]:
    """Row A: the worker's listener under the worker's claude, nested under
    the foreman's claude; this test's own pid is the foreman's next
    `pager listen`, under a bash under the foreman's claude."""
    me = os.getpid()
    ppid = {FOREMAN: 1, F_BASH: FOREMAN, TM: F_BASH, WORKER: TM,
            W_BASH: WORKER, listen: W_BASH, ME_BASH: FOREMAN, me: ME_BASH}
    cmd = {FOREMAN: f"claude --session-id {SESSION_A}", F_BASH: "bash",
           TM: "/x/bin/python /x/bin/tm task work SM-454",
           WORKER: f"claude --session-id {SESSION_W} --model opus",
           W_BASH: "/bin/bash -c pager listen", listen: LISTEN_CMD,
           ME_BASH: "bash", me: "/x/.venv/bin/python /x/.venv/bin/pager listen --quiet"}
    return ppid, cmd


def _one_claude_tree(claude: str, listen: int) -> tuple[dict[int, int], dict[int, str]]:
    """Rows B and C: one claude; its listener under one bash, and this test's
    own pid (the probe run for another sid) under another."""
    me = os.getpid()
    ppid = {CLAUDE: 1, L_BASH: CLAUDE, listen: L_BASH, ME_BASH: CLAUDE, me: ME_BASH}
    cmd = {CLAUDE: claude, L_BASH: "bash", listen: LISTEN_CMD,
           ME_BASH: "bash", me: "/x/.venv/bin/python /x/.venv/bin/pager off"}
    return ppid, cmd


ID_LESS = ("claude", "claude --resume sess-a", "claude --continue")


# ── (A) DESCENDANT: a nested claude's listener is not this session's ─────

@ASK
@pytest.mark.parametrize("stamped", [False, True], ids=["stampless", "stamped-by-worker"])
def test_a_descendant_seats_listener_is_not_this_sessions(rig, listener, monkeypatch, stamped, ask):
    """Row A. Asked for the FOREMAN's session (A), with no stamp of A's own,
    neither `is_listening` nor `other_listener` may answer the WORKER's
    listener: its nearest claude is the worker's (`--session-id
    sess-worker`), not A's. Today both answer the listener pid, in either
    variant (the worker's own stamp, under its own session, changes
    nothing today).

    For `is_listening` this test's own pid is a status probe, not the
    foreman's next `pager listen`: `is_listening` excludes nothing, so a
    stampless `pager listen` under the foreman's own claude IS a foreman
    listener (the ratified pre-stamp scenario) and would rightly be found.
    `other_listener` excludes the asker, so its row keeps the listen argv.
    (On 5dc20f8 the worker's listener came first in the table and hid it.)"""
    if stamped:
        _stamp_as(listener.pid, SESSION_W)
    ppid, cmd = _descendant_tree(listener.pid)
    if ask == "is_listening":
        cmd[os.getpid()] = "/x/.venv/bin/python /x/.venv/bin/pager status"
    table = _ps(monkeypatch, ppid, cmd)
    assert getattr(_seat, ask)(rig.seat, SESSION_A, ps=table) is None


def test_a_descendant_seats_listener_does_not_refuse_the_foremans_listen(
        rig, listener, monkeypatch):
    """Row A end to end, as the foreman saw it: its bare `pager listen`
    must ARM (here: arm and time out, 124), not exit 3. Today it exits 3,
    "refused: this session is already listening, pid <worker's listener>"."""
    rig.box(SESSION_A)
    _ps(monkeypatch, *_descendant_tree(listener.pid))
    result = CliRunner().invoke(pager.cli, ["listen", "--timeout", "0"], prog_name="pager")
    assert result.exit_code == EX_TIMEOUT, result.output
    assert listener.poll() is None


# ── (B) `pager off` under a sid that owns no listener stops nothing ─────

@pytest.mark.parametrize("claude", ID_LESS)
def test_off_under_a_fake_sid_stops_nothing_under_an_id_less_claude(
        rig, listener, monkeypatch, fresh_session, claude):
    """Row B. `pager off` for F (a made-up sid, no listener) runs inside a
    tree whose live listener is session A's (A's stamp names it). It must
    exit 3 and stop nothing: the listener alive, A's stamp intact, A not
    marked off. Today it SIGTERMs A's listener and says "stopped listener
    <pid> for session <F>"."""
    _stamp_as(listener.pid, SESSION_A)
    _ps(monkeypatch, *_one_claude_tree(claude, listener.pid))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", fresh_session)
    result = CliRunner().invoke(pager.cli, ["off"], prog_name="pager")
    assert listener.poll() is None, f"A's listener was stopped: {result.output!r}"
    assert result.exit_code == pager.EX_NOTHING, result.output
    assert (_seat.stamp_dir(None, SESSION_A) / _seat.LISTENING_STAMP).read_text() \
        == f"{listener.pid}\n"
    assert not _seat.stopped_on_purpose(rig.seat, SESSION_A)


def test_off_under_a_fake_sid_stops_nothing_under_an_id_carrying_claude(
        rig, listener, monkeypatch, fresh_session):
    """Row B's control, GREEN on 5dc20f8: under `claude --session-id A`
    SM-558's nearest-claude rule already ends the walk, so the 09-29 shape
    with an id-carrying claude stops nothing. fix-001 must keep it so."""
    _stamp_as(listener.pid, SESSION_A)
    _ps(monkeypatch, *_one_claude_tree(f"claude --session-id {SESSION_A}", listener.pid))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", fresh_session)
    result = CliRunner().invoke(pager.cli, ["off"], prog_name="pager")
    assert result.exit_code == pager.EX_NOTHING, result.output
    assert listener.poll() is None


# ── (C) an id-less claude's stamped listener is not another sid's ───────

@ASK
@pytest.mark.parametrize("claude", ID_LESS)
def test_an_id_less_claudes_stamped_listener_is_not_credited_to_another_sid(
        rig, listener, monkeypatch, fresh_session, claude, ask):
    """Row C. Under an id-less claude, the listener carries session A's
    stamp. A query for F, run under the same claude, must not be credited
    it: `is_listening` and `other_listener` for F answer None. Today both
    answer the listener's pid (the walk from ME reaches the id-less claude,
    counts it as F's, and never reads the candidate's stamp)."""
    _stamp_as(listener.pid, SESSION_A)
    table = _ps(monkeypatch, *_one_claude_tree(claude, listener.pid))
    assert getattr(_seat, ask)(rig.seat, fresh_session, ps=table) is None


@pytest.mark.parametrize("claude", ID_LESS)
def test_the_stamped_listener_is_still_its_own_sessions(rig, listener, monkeypatch, claude):
    """Row C's control, GREEN on 5dc20f8: the same tree asked for A, the
    session whose stamp names the listener, still finds it. fix-001 must
    not make A deaf to its own listener."""
    _stamp_as(listener.pid, SESSION_A)
    table = _ps(monkeypatch, *_one_claude_tree(claude, listener.pid))
    assert _seat.is_listening(rig.seat, SESSION_A, ps=table) == listener.pid


# ── fix-001: what the two legs must KEEP, and `_reach`'s owner label ─────

def test_a_stampless_listener_under_this_sessions_own_claude_is_still_found(
        rig, listener, monkeypatch):
    """The ratified scenario "A listener from before the stamp" (pager
    spec): a listener with NO stamp, still running under this session's
    claude (`--session-id A`), is found by its parent chain. Leg (a) keeps
    it: its nearest claude is A's."""
    table = _ps(monkeypatch, *_one_claude_tree(f"claude --session-id {SESSION_A}", listener.pid))
    assert _seat.is_listening(rig.seat, SESSION_A, ps=table) == listener.pid
    assert _seat.other_listener(rig.seat, SESSION_A, ps=table) == listener.pid


@pytest.mark.parametrize("claude", ID_LESS)
def test_a_stampless_listener_under_an_id_less_claude_is_still_credited(
        rig, listener, monkeypatch, fresh_session, claude):
    """Today's behaviour, KEPT until leg (c) (an id-less claude identified
    by its sidecar) is ruled: under an id-less claude, a listener that NO
    session has stamped is still credited to the sid asking from under the
    same claude. Leg (b) only excludes a listener another session stamped."""
    table = _ps(monkeypatch, *_one_claude_tree(claude, listener.pid))
    assert _seat.is_listening(rig.seat, fresh_session, ps=table) == listener.pid


def test_a_dead_pid_in_another_sessions_stamp_excludes_nothing(
        rig, listener, monkeypatch, fresh_session):
    """Leg (b) reads only LIVE pids: a stale stamp of another session's that
    names a dead pid does not hide a live stampless listener, and the
    foreign stamp is left where it is (read-only)."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _stamp_as(dead.pid, SESSION_A)
    table = _ps(monkeypatch, *_one_claude_tree("claude", listener.pid))
    assert _seat.is_listening(rig.seat, fresh_session, ps=table) == listener.pid
    assert (_seat.stamp_dir(None, SESSION_A) / _seat.LISTENING_STAMP).read_text() \
        == f"{dead.pid}\n"


def test_stamped_elsewhere_names_other_sessions_live_stamps_only(rig, listener):
    """`_stamped_elsewhere` maps each live pid another session's stamp names
    to that session, at the session-dir root and the pre-3.3a root, and
    never the asking session's own stamp."""
    _stamp_as(listener.pid, SESSION_A)
    assert _seat._stamped_elsewhere(SESSION_W) == {listener.pid: SESSION_A}
    assert _seat._stamped_elsewhere(SESSION_A) == {}
    old = _seat.old_session_root(SESSION_W) / _seat.STAMPS_DIR / _seat.LISTENING_STAMP
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(f"{os.getpid()}\n")
    assert _seat._stamped_elsewhere(SESSION_A) == {os.getpid(): SESSION_W}


def test_nearest_claude_starts_above_the_listener():
    """Leg (a)'s walk: the FIRST claude above a pid, never the pid itself
    (even one whose own line reads as a claude), None when no claude is
    above it."""
    ppid, cmd = _descendant_tree(7_454_099)
    assert _seat._nearest_claude(7_454_099, ppid, cmd) == WORKER
    assert _seat._nearest_claude(TM, ppid, cmd) == FOREMAN
    assert _seat._nearest_claude(FOREMAN, ppid, cmd) is None
    assert _seat._nearest_claude(WORKER, ppid, cmd) == FOREMAN


def test_reach_owner_is_the_nearest_claudes_id():
    """`_reach._owner_by_ancestry` labels a listener with its NEAREST
    claude's id: the worker's, never the foreman's above it (row A's
    shape); "" under an id-less claude, never a grandparent's id; and the
    `--session-id=X` form is read (it uses `_seat`'s one parse)."""
    from sessionmail import _reach

    listen = 7_454_099
    ppid, cmd = _descendant_tree(listen)
    assert _reach._owner_by_ancestry(listen, ppid, cmd) == SESSION_W
    cmd[WORKER] = f"claude --session-id={SESSION_W} --model opus"
    assert _reach._owner_by_ancestry(listen, ppid, cmd) == SESSION_W
    cmd[WORKER] = "claude --resume sess-worker"
    assert _reach._owner_by_ancestry(listen, ppid, cmd) == ""
    assert _reach._owner_by_ancestry(FOREMAN, ppid, cmd) == ""


# ── fix-001: a claude is known by its BINARY, never by a path ────────────

SCRATCH_PYTEST = ("/private/tmp/claude-502/-Users-x-proj/9f9cb189/scratchpad/base/.venv/bin/python "
                  "/private/tmp/claude-502/-Users-x-proj/9f9cb189/scratchpad/base/.venv/bin/pytest -q")


@pytest.mark.parametrize("line", [
    "claude", "claude --session-id sess-a", "claude -p --session-id sess-a do the thing",
    "claude --resume sess-a --fork-session", "claude -c", "claude --print=json",
    "claude --session-id=sess-a --plugin-dir /x/plugins/pager-monitor",
    "/opt/bin/claude --model opus --session-id=sess-y", "/Users/u/.local/bin/claude",
    "node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js --session-id sess-a",
    "/opt/homebrew/bin/node --no-warnings /x/node_modules/@anthropic-ai/claude-code/cli.js",
])
def test_a_claude_is_known_by_its_binary(line):
    """Every claude form the suite (and a live host's table: all 53 claudes'
    argv[0] was `claude`, Sun 2030-12-08) uses still matches."""
    assert _seat._CLAUDE.match(line), line


@pytest.mark.parametrize("line", [
    SCRATCH_PYTEST,
    "/Users/u/.claude/plugins/cache/sessionmail/.venv/bin/python /x/bin/pager listen --monitor",
    "/Users/u/.claude/local/bin/pager listen --monitor",
    "claude-sessions list", "/x/claude-code/bin/helper", "claudette --session-id sess-a",
    "/x/bin/python /x/bin/tm task work SM-454", "node /x/server.js claude",
])
def test_a_path_containing_claude_is_not_a_claude(line):
    """The measured false positives (a pytest in a Claude Code scratchpad)
    and their kin are not claudes."""
    assert not _seat._CLAUDE.match(line), line


def _scratch_tree() -> tuple[dict[int, int], dict[int, str]]:
    """Session A's claude; under it a bash, a scratchpad pytest, and this
    test's pid (a pager verb run by that pytest for another sid)."""
    me = os.getpid()
    ppid = {CLAUDE: 1, L_BASH: CLAUDE, ME_BASH: L_BASH, me: ME_BASH}
    cmd = {CLAUDE: f"claude --session-id {SESSION_A}", L_BASH: "bash",
           ME_BASH: SCRATCH_PYTEST, me: "/x/.venv/bin/python /x/.venv/bin/pager off"}
    return ppid, cmd


def test_a_scratchpad_pytest_is_not_the_nearest_claude_of_session_claudes_or_claude_for(
        fresh_session):
    """The effect on `_session_claudes` and `claude_for` (SM-706), shown on
    a table: walking up from a pager verb run inside a scratchpad pytest,
    the nearest claude is session A's real claude, which carries ANOTHER
    id, so a made-up sid owns nothing and has no claude running. Under the
    old pattern the pytest was the nearest "id-less claude", credited to
    whatever sid asked. (`claude_for` is conftest's REAL one: the suite
    pins "claude is up" for every other test.)"""
    ppid, cmd = _scratch_tree()
    me = os.getpid()
    assert _seat._session_claudes(fresh_session, ppid, cmd, me) == set()
    assert REAL_CLAUDE_FOR(fresh_session, ps=lambda: (ppid, cmd), me=me, live=lambda: []) is None
    assert _seat._session_claudes(SESSION_A, ppid, cmd, me) == {CLAUDE}
    assert REAL_CLAUDE_FOR(SESSION_A, ps=lambda: (ppid, cmd), me=me, live=lambda: []) == CLAUDE


def test_a_scratchpad_pytest_does_not_hand_a_listener_to_another_sid(
        rig, listener, monkeypatch, fresh_session):
    """Row B under a scratchpad pytest: a STAMPLESS listener spawned by the
    pytest (so under it, under session A's claude). Asked for a made-up
    sid from inside the same pytest, neither ask is credited it. Under the
    old pattern the pytest was BOTH the asker's nearest "id-less claude"
    and the listener's, so the listener went to whatever sid asked; by its
    binary, the nearest claude of both is A's, which carries another id."""
    ppid, cmd = _scratch_tree()
    ppid[listener.pid], cmd[listener.pid] = ME_BASH, LISTEN_CMD
    table = _ps(monkeypatch, ppid, cmd)
    for ask in ("is_listening", "other_listener"):
        assert getattr(_seat, ask)(rig.seat, fresh_session, ps=table) is None, ask
