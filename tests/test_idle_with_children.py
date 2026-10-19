"""SM-626: the ST-98 consumer. `idle with children` wakes like idle and is
never killed like idle.

sessiontools ST-98 (repo sessiontools, merged at 6e8288d) gives every session one of three
states: `busy`, `idle with children` or `idle`. A seat whose turn has ended
while a background agent runs is `idle with children`: Claude Code keeps its
sidecar `busy`, and a turn-ended mark (`sessiontools.turn`, written by the
Stop hook) says the turn is over. The ruling drops stopped-means-idle's "SHALL
NOT infer idleness from the Stop hook" (openspec/changes/idle-with-children).

The two rules this file pins (the task's design note,
designs/2030-10-14-idle-with-children-consumer.md in the store):

- WAKE: for the pager, `idle with children` is IDLE, measured from the turn
  end. It pages at T = 0, and the burst window does not hold it (a held line
  waits for a ride-along that, with the turn over, never comes; and a live
  child may take the ride-along, SM-446).
- KILL: nothing that ends, stops or reaps a seat treats it as idle. In
  sessionmail no such path reads idleness at all; each is pinned here so a
  later change cannot start to.

And a condition: an older sessiontools without `state` behaves EXACTLY
as today.

HERMETIC. The acceptance test writes a real v1 mark into a SCRATCH session
dir (conftest's sandbox HOME) and a busy sidecar into a scratch
SESSION_SIDECAR_DIR for THIS process's pid, so the liveness check passes on
a live process. It needs a sessiontools with ST-98 (`sessiontools.turn`):
the locked one (0.0.4) has none, so there it is skipped and the older-library
test below runs instead. To run it against ST-98 without touching
pyproject.toml or uv.lock:

    PYTHONPATH=<sessiontools checkout>/src uv run pytest tests/test_idle_with_children.py

The real `~/.session` and `~/.claude` are never touched.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import pytest
import sessiontools
from click.testing import CliRunner

from sessionmail import _boxes, _seat, pager, rules
from sessionmail.pager import cli

SID = "5e55a626-0000-4000-8000-00000000c0de"
RUNNING_AGE = 60.0                     # pager.DEFAULT_PAGE_AGE, the running age
ST98 = hasattr(sessiontools, "IDLE_WITH_CHILDREN")
needs_st98 = pytest.mark.skipif(
    not ST98, reason="sessiontools without ST-98 (no `turn` mark): run with "
    "PYTHONPATH=<sessiontools>/src, see the module docstring")
needs_old = pytest.mark.skipif(
    ST98, reason="the installed sessiontools has ST-98; the older-library "
    "case is proven by the stand-in tests in this file")


@pytest.fixture(autouse=True)
def fresh_verdicts():
    """The verdict cache is per process: every test reads from scratch."""
    _seat._idle_cache.clear()
    _seat._state_cache.clear() if hasattr(_seat, "_state_cache") else None
    yield
    _seat._idle_cache.clear()
    _seat._state_cache.clear() if hasattr(_seat, "_state_cache") else None


# ── the scratch harness: a busy sidecar for a live pid, and the mark ──────

def _proc_start(pid: int) -> str:
    """PID's start as Claude Code writes `procStart`: UTC ctime."""
    from sessiontools import procs
    started = procs.table()[pid].started
    assert started is not None
    return time.strftime(procs.CTIME, time.gmtime(started))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    sidecars = tmp_path / "claude-sessions"
    sidecars.mkdir()
    monkeypatch.setenv("SESSION_SIDECAR_DIR", str(sidecars))
    monkeypatch.delenv(pager.PAGE_AGE_VAR, raising=False)
    monkeypatch.delenv(_seat.IDLE_SECONDS_VAR, raising=False)
    home = Path(os.environ["HOME"])
    assert "hermetic" in str(home), "the mark must land in conftest's sandbox, never ~/.session"

    class Scratch:
        def sidecar(self, *, status: str = "busy", updated_ago: float = 30.0,
                    pid: int | None = None, proc_start: str | None = None) -> Path:
            pid = os.getpid() if pid is None else pid
            path = sidecars / f"{pid}.json"
            path.write_text(json.dumps({
                "pid": pid, "sessionId": SID, "status": status,
                "updatedAt": int((time.time() - updated_ago) * 1000),
                "procStart": proc_start if proc_start is not None else _proc_start(os.getpid()),
                "version": "2.1.296",
            }))
            return path

        def mark(self, *, ago: float = 5.0, body: dict | None = None) -> Path:
            from sessiontools import turn
            path = turn.mark_path(SID)
            assert str(path).startswith(str(home)), path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body if body is not None else {
                "v": 1, "at": time.time() - ago, "harness": "claude-code",
                "event": "Stop", "background": 1}) + "\n")
            return path

    return Scratch()


# ── the acceptance test: a real mark, a scratch session dir ────────────────

@needs_st98
def test_a_valid_mark_on_a_busy_live_sidecar_pages_at_T0(scratch):
    """busy sidecar (a background agent keeps it busy) + a valid v1
    turn-ended mark + a live pid: the page age is 0, the wake path's own
    number, measured from the turn end."""
    scratch.sidecar(updated_ago=30)
    scratch.mark(ago=5)
    assert _seat.is_idle(SID) is True
    assert 4.0 <= _seat.idle_seconds(SID) < 30.0, "idle since the TURN END, not updatedAt"
    assert pager.page_age(None, SID) == 0.0


@needs_st98
def test_the_same_seat_with_no_mark_waits_the_running_age_exactly_as_today(scratch):
    scratch.sidecar(updated_ago=30)
    assert _seat.idle_seconds(SID) == 0.0
    assert _seat.is_idle(SID) is False
    assert pager.page_age(None, SID) == RUNNING_AGE


@needs_st98
@pytest.mark.parametrize("case", ["stale", "dead-pid", "bad-version"])
def test_a_mark_that_does_not_count_is_today(scratch, case):
    """sessiontools' own guards, seen from the pager: a mark older than the
    sidecar's last status write, a sidecar whose process is gone, a mark of
    an unknown version - all busy, all the running age."""
    if case == "stale":
        scratch.sidecar(updated_ago=2)
        scratch.mark(ago=10)
    elif case == "dead-pid":
        scratch.sidecar(updated_ago=30, proc_start="Thu Jan  1 00:00:00 1970")
        scratch.mark(ago=5)
    else:
        scratch.sidecar(updated_ago=30)
        scratch.mark(body={"v": 2, "at": time.time() - 5})
    assert pager.page_age(None, SID) == RUNNING_AGE


@needs_st98
def test_the_banner_names_the_rule_from_a_real_mark(scratch):
    scratch.sidecar(updated_ago=30)
    scratch.mark(ago=5)
    line = pager.idle_state(SID)
    assert "-> at once" in line
    assert "idle with children" in line, line


# ── the older library: exactly today ─────────────────────────────────────

@needs_old
def test_an_older_sessiontools_ignores_the_mark_exactly_as_today(scratch, monkeypatch):
    """The LOCKED library (0.0.4) has no `state` and no mark reader: the same
    busy sidecar with a valid v1 mark beside it reads busy, waits the running
    age, and the banner is today's text, word for word."""
    scratch.sidecar(updated_ago=30)
    mark = Path(os.environ["HOME"]) / ".session" / "sessions" / SID / "turn-ended"
    mark.parent.mkdir(parents=True)
    mark.write_text(json.dumps({"v": 1, "at": time.time() - 5, "harness": "claude-code",
                                "event": "Stop", "background": 1}) + "\n")
    assert _seat.idle_seconds(SID) == 0.0
    assert pager.page_age(None, SID) == RUNNING_AGE
    assert pager.idle_state(SID) == f"busy -> {RUNNING_AGE:g}s"
    assert _seat.seat_state(SID) == _seat.BUSY


# ── stand-ins: the library's Session, with and without ST-98's `state` ────

class OldSession:
    """A Session from a sessiontools WITHOUT ST-98: `busy` and the number,
    no `state`, no `turn_ended_at` (0.0.4's shape)."""

    def __init__(self, busy: bool, since: float, pid: int | None = None):
        self.id, self.busy, self.updated_at, self.pid = SID, busy, since, pid
        self.agent = None

    def idle_seconds(self, now: float | None = None) -> float:
        if self.busy:
            return 0.0
        return max(0.0, (time.time() if now is None else now) - self.updated_at)


class NewSession(OldSession):
    """ST-98's Session: `state` says which of the three it is."""

    def __init__(self, state: str, since: float, pid: int | None = None):
        super().__init__(state == "busy", since, pid)
        self.turn_ended_at = since if state == "idle with children" else None
        self.state = state


@pytest.fixture
def harness(monkeypatch):
    """Point `_seat`'s one library call at a stand-in Session."""
    monkeypatch.delenv(pager.PAGE_AGE_VAR, raising=False)
    monkeypatch.delenv(_seat.IDLE_SECONDS_VAR, raising=False)
    box = {"s": None}
    monkeypatch.setattr(_seat.sessiontools, "find",
                        lambda sid, env=None: box["s"] if sid == SID else None)

    def put(s):
        box["s"] = s
        _seat._idle_cache.clear()
        _seat._state_cache.clear()
    return put


def test_idle_with_children_is_idle_for_the_wake_and_named(harness):
    harness(NewSession("idle with children", time.time() - 12))
    assert _seat.seat_state(SID) == _seat.IDLE_WITH_CHILDREN
    assert _seat.is_idle(SID) is True
    assert pager.page_age(None, SID) == 0.0
    line = pager.idle_state(SID)
    assert line.startswith("idle 12") and line.endswith(
        "-> at once (idle with children: turn ended, children running)"), line


def test_inside_the_threshold_idle_with_children_waits_but_says_why(harness):
    """The turn ended 0.3 s ago: not yet the 1 s threshold, so not yet T=0
    (one poll later it is), and the banner already names the rule."""
    harness(NewSession("idle with children", time.time() - 0.3))
    assert pager.page_age(None, SID) == RUNNING_AGE
    assert pager.idle_state(SID).endswith("(idle with children: turn ended, children running)")


@pytest.mark.parametrize("state,secs_ago,want_age,want_text", [
    ("busy", 0, RUNNING_AGE, "busy -> 60s"),
    ("idle", 75, 0.0, "idle 75s >= 1s -> at once"),
])
def test_busy_and_idle_read_exactly_as_today(harness, state, secs_ago, want_age, want_text):
    harness(NewSession(state, time.time() - secs_ago))
    assert pager.page_age(None, SID) == want_age
    assert re.sub(r"(\d+)\.\d+s", r"\1s", pager.idle_state(SID)) == want_text


@pytest.mark.parametrize("busy,want", [(True, "busy"), (False, "idle")])
def test_an_older_session_without_state_reads_todays_two_states(harness, busy, want):
    """The condition, on every library: a Session with no `state`
    attribute is today's reading - busy or idle by `busy`, today's number,
    today's T and today's banner text (no rule named)."""
    s = OldSession(busy, time.time() - 75)
    assert not hasattr(s, "state")
    harness(s)
    assert _seat.seat_state(SID) == want
    assert pager.page_age(None, SID) == (RUNNING_AGE if busy else 0.0)
    assert "idle with children" not in pager.idle_state(SID)


def test_an_unknown_state_word_falls_back_to_busy_flag(harness):
    s = NewSession("busy", time.time() - 75)
    s.state = "napping"                       # a word this pager does not know
    harness(s)
    assert _seat.seat_state(SID) == _seat.BUSY


def test_the_constant_survives_a_library_without_it():
    """`_seat` imports on an older sessiontools: its constant is the literal.
    In a child, so this process's `_seat` (and every `Seat` imported from
    it) is never reloaded."""
    import subprocess
    import sys
    code = ("import sessiontools\n"
            "if hasattr(sessiontools, 'IDLE_WITH_CHILDREN'):\n"
            "    del sessiontools.IDLE_WITH_CHILDREN\n"
            "from sessionmail import _seat\n"
            "print(_seat.IDLE_WITH_CHILDREN, _seat.STATES)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=60, check=True).stdout
    assert out.strip() == "idle with children ('busy', 'idle with children', 'idle')"


def test_session_get_idle_fallback_keeps_its_state_word(monkeypatch):
    """No sidecar: `session get idle --json` answers. ST-98's carries `state`;
    an older one's number alone says busy or idle."""
    import subprocess

    class Res:
        returncode = 0

        def __init__(self, out):
            self.stdout = out
    monkeypatch.setattr(_seat.sessiontools, "find", lambda sid, env=None: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Res(
        '{"idle": 9, "state": "idle with children"}'))
    assert _seat.idle_seconds(SID) == 9.0
    assert _seat.seat_state(SID) == _seat.IDLE_WITH_CHILDREN
    _seat._idle_cache.clear()
    _seat._state_cache.clear()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Res('{"idle": 0}'))
    assert _seat.seat_state(SID) == _seat.BUSY


# ── KILL: nothing that ends, stops or reaps a seat reads it as idle ──────

@pytest.fixture
def no_idleness(monkeypatch, harness):
    """An idle-with-children seat whose idleness readers RAISE if a kill or
    reap path touches them: these paths judge by pid or by an explicit ask."""
    harness(NewSession("idle with children", time.time() - 600, pid=os.getpid()))

    def touched(*a, **k):
        raise AssertionError("a kill/reap path read idleness")
    for name in ("idle_seconds", "is_idle", "seat_state"):
        monkeypatch.setattr(_seat, name, touched)


@pytest.fixture
def seated(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    return _seat.resolve()


def test_kill_pager_off_never_reads_idleness(seated, no_idleness):
    """K1: `pager off`/`pager stop` stops THIS session's listener on the
    seat's own ask, by its stamp's pid; idleness is never consulted."""
    for verb in ("off", "stop"):
        result = CliRunner().invoke(cli, [verb], prog_name="pager")
        assert result.exit_code == 3, (result.output, result.exception)   # nothing listening
        assert "nothing listening" in result.output


def test_kill_a_live_listener_stamp_is_kept_whatever_the_state(seated, no_idleness):
    """K2: `is_listening` / `other_listener` clear a `listening` stamp only
    when its pid is dead; an idle-with-children seat's live one stays."""
    stamp = _seat.stamp_dir(seated, SID) / _seat.LISTENING_STAMP
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{os.getpid()}\n")
    assert _seat.is_listening(seated, SID, ps=lambda: ({}, {})) == os.getpid()
    assert stamp.exists()


def test_kill_a_live_scoops_taking_dir_is_not_reclaimed(seated, no_idleness):
    """K3: `leftovers` reclaims a taking dir only when its scoop's pid is
    dead. A live one (here: a pid that is alive and not ours) stays."""
    base = _seat.ride_dir(seated, SID)
    live = base / f"{_seat.TAKING_PREFIX}{os.getppid()}"
    dead = base / f"{_seat.TAKING_PREFIX}999999"
    live.mkdir(parents=True)
    dead.mkdir()
    assert _seat.leftovers(seated, SID) == [dead]


def test_kill_an_idle_with_children_seat_is_live_in_listings(monkeypatch, no_idleness):
    """K4: `live_sessions` (listings, routes) counts a session live by its pid;
    an idle-with-children seat with a live process is live."""
    s = NewSession("idle with children", time.time() - 600, pid=os.getpid())
    monkeypatch.setattr(_boxes.sessiontools, "sessions", lambda *a, **k: [s])
    assert _boxes.live_sessions() == {SID}


# ── the wake, end to end: T=0, through the burst window (SM-446) ─────────

EX_PAGE, EX_TIMEOUT = 0, 124
HOST, SUFFIX = "hq", "tailnet.example"
FQDN = f"{HOST}.{SUFFIX}"


@pytest.fixture
def rig(tmp_path, monkeypatch, harness):
    root = tmp_path / "mail"
    root.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("MAGICDNS_SUFFIX", SUFFIX)
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv(rules.PAGE_RULES_VAR, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "30")          # running age: far past any timeout here
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    n = {"i": 0}

    class Rig:
        seat = _seat.resolve()

        def state(self, word: str) -> None:
            harness(NewSession(word, time.time() - 20))

        def deliver(self, mid: str) -> None:
            n["i"] += 1
            b = root / SID
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            name = f"{int(time.time())}.M{n['i']:06d}P{os.getpid()}.iwc"
            (b / "tmp" / name).write_bytes(
                f"X-Original-To: {SID}\r\nFrom: beta@{FQDN}\r\nTo: {SID}@{FQDN}\r\n"
                f"Subject: s {mid}\r\nMessage-ID: <{mid}>\r\n\r\nTHE BODY\r\n".encode())
            os.rename(b / "tmp" / name, b / "new" / name)

        def marks(self) -> str:
            m = _seat.marks_path(self.seat, SID)
            return m.read_text() if m.exists() else ""

    return Rig()


def listen(seconds: float):
    return CliRunner().invoke(cli, ["listen", "--timeout", str(seconds)], prog_name="pager")


def later(seconds: float, fn, *args) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), fn(*args)))
    t.start()
    return t


def test_an_idle_with_children_seat_is_paged_at_once_and_the_mark_says_why(rig):
    """SM-446: with children live, the PAGE is the wake - at T=0, not after
    the running age a ride-along was meant to beat."""
    rig.state("idle with children")
    later(0.2, rig.deliver, "c1@x.invalid")
    t0 = time.monotonic()
    result = listen(5)
    assert result.exit_code == EX_PAGE, result.stderr
    assert time.monotonic() - t0 < 2.0
    assert "pager wake (idle with children T=0s)" in rig.marks()
    assert "(idle with children: turn ended, children running)" in result.stderr
    assert "-> at once (idle with children: turn ended, children running)" in result.stderr


def test_a_plain_idle_page_still_says_idle(rig):
    rig.state("idle")
    later(0.2, rig.deliver, "i1@x.invalid")
    result = listen(5)
    assert result.exit_code == EX_PAGE, result.stderr
    assert "pager wake (idle T=0s)" in rig.marks()
    assert "idle with children" not in rig.marks() + result.stderr


def test_the_burst_window_does_not_hold_an_idle_with_children_seat(rig):
    """rca-001's consumer B: an address that already rang inside the window
    is held for a ride-along on a RUNNING seat. Turn over, children live: no
    ride-along comes (and a child may take it, SM-446), so it rings again."""
    rig.state("idle with children")
    later(0.2, rig.deliver, "b1@x.invalid")
    assert listen(5).exit_code == EX_PAGE
    later(0.2, rig.deliver, "b2@x.invalid")
    second = listen(5)
    assert second.exit_code == EX_PAGE, second.stderr
    assert "b2@x.invalid" in rig.marks()


def test_on_a_busy_seat_the_window_still_holds_the_second_line(rig):
    """The control, today's behaviour: busy, the same second line is held
    for the ride-along (no page inside the running age)."""
    rig.state("idle with children")
    later(0.2, rig.deliver, "h1@x.invalid")
    assert listen(5).exit_code == EX_PAGE
    rig.state("busy")
    later(0.2, rig.deliver, "h2@x.invalid")
    second = listen(1.5)
    assert second.exit_code == EX_TIMEOUT, second.stderr
    assert "h2@x.invalid" not in rig.marks()


def test_pager_why_names_the_idleness_that_let_it_page(rig):
    rig.state("idle with children")
    later(0.2, rig.deliver, "y1@x.invalid")
    assert listen(5).exit_code == EX_PAGE
    why = CliRunner().invoke(cli, ["why", "y1@x.invalid"], prog_name="pager")
    assert why.exit_code == 0, why.output
    assert re.search(r"pager why <y1@x\.invalid>: paged at \S+: "
                     r"pager wake \(idle with children T=0s\)", why.output), why.output
