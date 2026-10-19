"""pager-genie 3.8: a second `pager listen` in one session refuses (D10, O17
RULED).

openspec/changes/pager-genie, tasks.md 3.8: exit 3, naming the live pid,
changing no stamp and no mark; another session's listener is not a second
arm. *(A second listener in one session refuses)*

Hermetic, on test_pager_listen.py's rig: a maildir root and a seat home under
tmp_path. The "first listener" is a real, live, harmless process (a Python
sleep) whose pid the stamp names; what `ps` says about it is injected, so no
real `pager listen` ever starts and no live box is touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _seat, pager
from test_pager_listen import EX_TIMEOUT, rig, run  # noqa: F401

SESSION = "sess-a"
CLAUDE = 7_000_001           # a claude process that exists only in the table
LISTEN_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen"


@pytest.fixture
def first():
    """A live process to stand in for the first listener."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _ps(monkeypatch, ppid: dict[int, int], cmd: dict[int, str]) -> None:
    monkeypatch.setattr(_seat, "_ps_table", lambda: (dict(ppid), dict(cmd)))


def _stamp(session: str = SESSION) -> Path:
    return _seat.stamp_dir(None, session) / _seat.LISTENING_STAMP


def _stamp_as(pid: int, session: str = SESSION) -> Path:
    path = _stamp(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")
    os.utime(path, (1_923_171_200, 1_923_171_200))    # an mtime a rewrite would move
    return path


def _tree(*roots: Path) -> dict[str, tuple[int, int]]:
    """Every file under ROOTS, with its size and mtime: what "changes
    nothing" is measured against."""
    out = {}
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                st = (Path(dirpath) / name).stat()
                out[str(Path(dirpath) / name)] = (st.st_size, st.st_mtime_ns)
    return out


# ── Scenario: Armed twice by habit ──────────────────────────────────────

def test_a_second_listen_refuses_with_exit_3_naming_the_first(rig, first, monkeypatch, tmp_path):
    """The first listener is live for this session (its stamp names it and
    `ps` says it is a `pager listen`): the second exits 3 with ONE line
    naming the first one's pid, and changes no stamp, no mark, no file."""
    rig.box()
    stamp = _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: LISTEN_CMD})
    before = _tree(tmp_path / "sessionmail", tmp_path / "mail")
    result = run("--timeout", "5")
    assert result.exit_code == pager.EX_SECOND_ARM == 3, result.output
    said = [ln for ln in result.stderr.splitlines() if ln.strip()]
    assert len(said) == 1 and str(first) in said[0] and "refused" in said[0], said
    assert result.stdout == ""
    assert stamp.read_text() == f"{first}\n"                 # its stamp is unchanged
    assert _tree(tmp_path / "sessionmail", tmp_path / "mail") == before
    assert rig.marks() == ""


def test_the_first_is_still_listening_after_the_refusal(rig, first, monkeypatch):
    rig.box()
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: LISTEN_CMD})
    assert run("--timeout", "5").exit_code == 3
    os.kill(first, 0)                                        # still alive: not signalled
    assert _seat.is_listening(rig.seat, SESSION) == first    # and still THE listener


def test_a_refusal_births_no_box(rig, first, monkeypatch, tmp_path):
    """Asked before anything is made: no session box is born by a refusal."""
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: LISTEN_CMD})
    assert run("--timeout", "5").exit_code == 3
    assert not (tmp_path / "mail" / SESSION).exists()


def test_a_stampless_first_is_found_by_ancestry(rig, monkeypatch):
    """As the NOT ARMED check finds one: no stamp, but a `pager listen`
    whose parent chain reaches this session's claude."""
    rig.box()
    listener = 7_000_002
    _ps(monkeypatch, {listener: CLAUDE, CLAUDE: 1},
        {CLAUDE: f"claude --session-id {SESSION}", listener: LISTEN_CMD})
    result = run("--timeout", "5")
    assert result.exit_code == 3
    assert str(listener) in result.stderr
    assert not _stamp().exists()                             # and none was written


# ── Scenario: Another session is not a second arm ───────────────────────

def test_another_sessions_stamped_listener_is_not_a_second_arm(rig, first, monkeypatch):
    rig.box()
    _stamp_as(first, "sess-b")
    _ps(monkeypatch, {first: CLAUDE}, {first: LISTEN_CMD})
    assert run("--timeout", "0").exit_code == EX_TIMEOUT      # A's listener starts


def test_another_sessions_listener_by_ancestry_is_not_a_second_arm(rig, monkeypatch):
    rig.box()
    other_claude, listener = 7_000_003, 7_000_004
    _ps(monkeypatch, {listener: other_claude, other_claude: 1},
        {other_claude: "claude --session-id sess-b", listener: LISTEN_CMD})
    assert run("--timeout", "0").exit_code == EX_TIMEOUT


# ── what is NOT a first listener ────────────────────────────────────────

def test_a_listener_never_finds_itself_or_its_own_shim(rig, monkeypatch):
    """The asking process, and a `pager` shim above it with the same command
    line, are not a second arm: ancestry is asked past them."""
    rig.box()
    me, shim = os.getpid(), 7_000_005
    _ps(monkeypatch, {me: shim, shim: CLAUDE, CLAUDE: 1},
        {CLAUDE: f"claude --session-id {SESSION}", shim: "/bin/sh /x/bin/pager listen",
         me: LISTEN_CMD})
    assert run("--timeout", "0").exit_code == EX_TIMEOUT


def test_a_stamp_naming_this_process_is_not_a_second_arm(rig, monkeypatch):
    rig.box()
    _stamp_as(os.getpid())
    assert run("--timeout", "0").exit_code == EX_TIMEOUT


def test_a_dead_pid_in_the_stamp_is_not_a_second_arm(rig, monkeypatch):
    rig.box()
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()                                              # a pid that WAS a listener
    _stamp_as(proc.pid)
    assert run("--timeout", "0").exit_code == EX_TIMEOUT


def test_a_reused_pid_is_not_a_second_arm(rig, first, monkeypatch):
    """The stamp's pid is alive, but `ps` says it is something else now (the
    listener was SIGKILLed and its pid reused): refusing would leave the
    session unable to arm at all."""
    rig.box()
    _stamp_as(first)
    _ps(monkeypatch, {first: 1}, {first: "/usr/bin/python -c import time"})
    assert run("--timeout", "0").exit_code == EX_TIMEOUT


def test_with_no_ps_answer_a_live_stamp_is_believed(rig, first, monkeypatch):
    """The rig's `ps` answers nothing: the live stamp alone refuses, as the
    NOT ARMED check would call the session armed."""
    rig.box()
    _stamp_as(first)
    assert run("--timeout", "5").exit_code == 3


# ── SM-706 change B, SM-726 R2: the monitor hears; calm, never `pager stop` ─

MONITOR_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor"


SIGNPOST = ("the pager monitor (pid {pid}) hears for this session; "
            "you don't need pager listen. Do not re-run it.")


def test_a_second_arm_beside_the_monitor_is_calm_and_never_says_stop(
        rig, first, monkeypatch, tmp_path):
    """Test 17 (SM-706 change B, SM-726 R2): the live listener's argv is the
    pager monitor. Exit EX_MONITOR_HEARS (0), and the FIRST output line is
    the signpost: it names the monitor and its pid and says not to re-run
    (a backgrounded exit 0 reads as COMPLETED).
    `pager stop` appears nowhere - Claude Code does not restart a monitor.
    With no `off` stamp to clear, it changes nothing."""
    rig.box()
    stamp = _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    before = _tree(tmp_path / "sessionmail", tmp_path / "mail")
    result = run("--timeout", "5")
    assert pager.EX_MONITOR_HEARS == 0
    assert result.exit_code == pager.EX_MONITOR_HEARS, result.output
    said = [ln for ln in result.output.splitlines() if ln.strip()]
    assert said == [SIGNPOST.format(pid=first)], said
    assert result.stderr == ""
    assert "pager stop" not in result.output and "refused" not in result.output
    assert stamp.read_text() == f"{first}\n"
    assert _tree(tmp_path / "sessionmail", tmp_path / "mail") == before
    os.kill(first, 0)                                        # not signalled


def test_the_calm_exit_is_one_named_constant(monkeypatch, rig, first):
    """SM-726 R2: the code comes from EX_MONITOR_HEARS alone, so a ruling for
    another code is a one-line change."""
    rig.box()
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    monkeypatch.setattr(pager, "EX_MONITOR_HEARS", 4)
    result = run("--timeout", "5")
    assert result.exit_code == 4, result.output
    assert result.output.splitlines()[0] == SIGNPOST.format(pid=first)


def test_a_second_arm_beside_the_monitor_clears_a_deliberate_stop(rig, first, monkeypatch):
    """SM-726 R2: the seat meant to listen, so its `off` stamp (SM-410) goes,
    as any `pager listen` clears it; the hooks may nag again if the monitor
    later goes. The monitor's own stamp is untouched."""
    rig.box()
    stamp = _stamp_as(first)
    _seat.write_off(None, SESSION)
    assert _seat.stopped_on_purpose(None, SESSION)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    result = run("--timeout", "5")
    assert result.exit_code == pager.EX_MONITOR_HEARS, result.output
    assert not _seat.stopped_on_purpose(None, SESSION)
    assert stamp.read_text() == f"{first}\n"
    # SM-726 do-003 (design.md (l3)): clearing it is an UNMUTE, and says so,
    # under the signpost (which stays the FIRST line).
    said = [ln for ln in result.output.splitlines() if ln.strip()]
    assert said == [SIGNPOST.format(pid=first),
                    f"it was muted: this `pager listen` unmuted it, so the pager monitor "
                    f"(pid {first}) pages again."], said


def test_a_stampless_monitor_found_by_ancestry_is_named_as_the_monitor(rig, monkeypatch):
    """Test 17, the window before the monitor stamps: found by ancestry
    under this session's claude, still named as the monitor, still calm."""
    rig.box()
    monitor = 7_000_006
    _ps(monkeypatch, {monitor: CLAUDE, CLAUDE: 1},
        {CLAUDE: f"claude --session-id {SESSION}", monitor: MONITOR_CMD})
    result = run("--timeout", "5")
    assert result.exit_code == pager.EX_MONITOR_HEARS
    assert result.output.splitlines()[0] == SIGNPOST.format(pid=monitor)
    assert "pager stop" not in result.output


def test_a_second_arm_beside_a_hand_armed_listen_keeps_todays_text(rig, first, monkeypatch):
    """Test 18: a plain `pager listen` is not a monitor; `pager stop` is the
    right advice there, unchanged, and so is exit 3 (SM-726 R2)."""
    rig.box()
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: LISTEN_CMD})
    result = run("--timeout", "5")
    assert result.exit_code == pager.EX_SECOND_ARM == 3
    assert result.stdout == "" and "hears for this session" not in result.output
    assert result.stderr.strip() == (
        f"pager listen: refused: this session is already listening, pid {first} - "
        "that one hears for you; `pager stop` ends it first")


def test_a_second_arm_with_no_ps_answer_keeps_todays_text(rig, first):
    """Test 19: with no `ps` answer nothing can be said about the listener's
    argv, so the refusal is today's."""
    rig.box()
    _stamp_as(first)
    result = run("--timeout", "5")
    assert result.exit_code == 3
    assert "`pager stop` ends it first" in result.stderr
    assert "nothing to arm" not in result.stderr


# ── SM-758: a NAMED box beside the monitor; the monitor does not hear it ──
#
# The calm exit (SM-726 R2, above) is right for the bare `pager listen`: the
# monitor hears this session's box, and its subscriptions. It is wrong for
# `pager listen BOX` when BOX is neither: today the is_monitor branch
# (src/sessionmail/pager.py:1658-1682 @ 5dc20f8) never looks at BOX, so the
# seat is told "you don't need pager listen. Do not re-run it." and nothing
# hears BOX. The seat is deaf to BOX (a live repro).

NAMED = "proj"                   # a project box: not SESSION, not subscribed


def _named_box_preconditions(rig) -> None:
    """BOX is a real maildir, it is not this session's box, and nothing
    subscribes this session (or any seat) to it. pytest.fail, not assert:
    the xfail below is raises=AssertionError, so a broken precondition
    fails loudly instead of passing as the expected failure."""
    from sessionmail import _birth, _subscription
    if not (rig.root / NAMED / "new").is_dir():
        pytest.fail(f"precondition: no maildir at {rig.root / NAMED}")
    if NAMED == SESSION:
        pytest.fail("precondition: BOX is the session box")
    subscribed = {box for _who, box in _subscription.pairs(live={SESSION})}
    if NAMED in subscribed:
        pytest.fail(f"precondition: {NAMED} is subscribed: {sorted(subscribed)}")
    if os.environ.get(_birth.SUBSCRIBE_VAR):
        pytest.fail(f"precondition: {_birth.SUBSCRIBE_VAR} is set")


def test_sm758_named_box_preconditions_hold(rig, monkeypatch):
    """The repro's setup, checked on its own so it is seen to pass."""
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    rig.box()
    rig.box(NAMED)
    _named_box_preconditions(rig)


def test_sm758_listen_on_the_session_box_by_name_beside_the_monitor_is_calm(
        rig, first, monkeypatch):
    """Contrast (passes today, must keep passing): BOX is this session's own
    box, which the monitor DOES hear, so the calm exit is right."""
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    rig.box()
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    result = run(SESSION, "--timeout", "5")
    assert result.exit_code == pager.EX_MONITOR_HEARS, result.output
    assert SIGNPOST.format(pid=first) in result.stdout.splitlines()


def test_sm758_listen_on_an_unheard_named_box_beside_the_monitor_is_not_calm(
        rig, first, monkeypatch, tmp_path):
    """SM-758 repro, now the fix (option A). The live listener is the pager monitor (its stamp,
    and `ps` says `pager listen --monitor`). Mail rests in BOX, a project box
    that is neither this session's box nor a subscription. `pager listen BOX`
    must not tell the seat the monitor hears for it: it REFUSES, loudly, exit
    EX_SECOND_ARM (3), naming the repair `message subscription add BOX`, and
    changes no stamp and no file (D10: a refusal changes nothing)."""
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    rig.box()
    rig.deliver(NAMED, "sm758@example.invalid", "for the project box")
    rig.stopped()
    stamp = _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    _named_box_preconditions(rig)
    before = _tree(tmp_path / "sessionmail", tmp_path / "mail")
    result = run(NAMED, "--timeout", "3")
    signpost = SIGNPOST.format(pid=first)
    assert signpost not in result.output, (
        f"deaf: told the monitor hears, but it does not hear {NAMED!r}; "
        f"exit {result.exit_code}; stdout={result.stdout!r}; stderr={result.stderr!r}")
    assert result.exit_code == pager.EX_SECOND_ARM == 3, result.output
    assert result.stdout == ""
    assert REFUSAL.format(pid=first, box=NAMED) in result.stderr.splitlines(), result.stderr
    assert f"message subscription add {NAMED}" in result.stderr.splitlines()[-1]
    assert "hears for you" not in result.output and "Do not re-run it" not in result.output
    assert stamp.read_text() == f"{first}\n"                 # the monitor's stamp stands
    assert _tree(tmp_path / "sessionmail", tmp_path / "mail") == before
    assert rig.marks() == ""
    os.kill(first, 0)                                        # not signalled


REFUSAL = ("pager listen: refused: the pager monitor (pid {pid}) hears this session's "
           "box, not {box} - to hear {box}, run message subscription add {box}")


def test_sm758_an_unheard_box_beside_a_muted_monitor_leaves_it_muted(
        rig, first, monkeypatch):
    """The refusal is a refusal: a muted monitor (its `off` stamp, SM-410)
    stays muted. Only the calm exit, for a box the monitor hears, unmutes
    (SM-726 do-003); an unheard BOX is not a seat asking the monitor to page."""
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    rig.box()
    rig.box(NAMED)
    _stamp_as(first)
    _seat.write_off(None, SESSION)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    _named_box_preconditions(rig)
    result = run(NAMED, "--timeout", "3")
    assert result.exit_code == pager.EX_SECOND_ARM, result.output
    assert _seat.stopped_on_purpose(None, SESSION)           # still muted
    assert "unmuted" not in result.output and "it was muted" not in result.output


def test_sm758_a_session_subscribed_box_beside_the_monitor_is_calm(rig, first, monkeypatch):
    """BOX is in this SESSION's subscriptions map, so postfix copies it into
    the session box and the monitor hears it: today's calm exit, unchanged."""
    from _seatmap import session_subscribe
    from sessionmail import _subscription
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    rig.box()
    rig.box(NAMED)
    session_subscribe(SESSION, NAMED)
    assert (SESSION, NAMED) in _subscription.pairs(live={SESSION})   # precondition
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    result = run(NAMED, "--timeout", "5")
    assert result.exit_code == pager.EX_MONITOR_HEARS, result.output
    assert result.stdout.splitlines()[0] == SIGNPOST.format(pid=first)
    assert "refused" not in result.output


def test_sm758_a_seat_subscribed_box_beside_the_monitor_is_calm(rig, first, monkeypatch):
    """BOX is in this SEAT's subscriptions map, keyed on the seat's folder
    name, which is `seat.address` (measured on a live seat:
    EMAIL=sessionmail+super@<host> resolves to
    address `sessionmail+super`, the folder `~/.session/seats/sessionmail+super`
    whose map the generator reads). Calm, as for the session's own map."""
    from _seatmap import seat_subscribe
    from sessionmail import _subscription
    seat = "proj+sm758.worker"
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    monkeypatch.setenv("EMAIL", seat)            # bare: this host, as `@<host>` is
    assert _seat.resolve().address == seat                    # precondition: the key
    rig.box()
    rig.box(NAMED)
    seat_subscribe(seat, NAMED)
    assert (seat, NAMED) in _subscription.pairs(live={SESSION})      # precondition
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    result = run(NAMED, "--timeout", "5")
    assert result.exit_code == pager.EX_MONITOR_HEARS, result.output
    assert result.stdout.splitlines()[0] == SIGNPOST.format(pid=first)


def test_sm758_another_seats_subscription_does_not_count(rig, first, monkeypatch):
    """Matching is exact on THIS session or THIS seat: a subscription another
    seat holds routes BOX to that seat, not here, so the listen refuses."""
    from _seatmap import seat_subscribe
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    monkeypatch.setenv("EMAIL", "proj+sm758.worker")
    rig.box()
    rig.box(NAMED)
    seat_subscribe("proj+super", NAMED)
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    result = run(NAMED, "--timeout", "3")
    assert result.exit_code == pager.EX_SECOND_ARM, result.output
    assert REFUSAL.format(pid=first, box=NAMED) in result.stderr.splitlines()


def test_sm758_subscribe_env_alone_is_not_heard(rig, first, monkeypatch):
    """rca point 3: SESSIONMAIL_SUBSCRIBE is never read by the check. Where the
    launcher applied it, it is in the session map; set but unapplied, nothing
    routes BOX, and the listen refuses rather than claim it is heard."""
    from sessionmail import _birth
    monkeypatch.setenv(_birth.SUBSCRIBE_VAR, NAMED)
    rig.box()
    rig.box(NAMED)
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: MONITOR_CMD})
    result = run(NAMED, "--timeout", "3")
    assert result.exit_code == pager.EX_SECOND_ARM, result.output


def test_sm758_an_unheard_box_beside_a_hand_armed_listen_drops_hears_for_you(
        rig, first, monkeypatch):
    """The same check, beside a hand-armed listener: still exit 3, but never
    "that one hears for you", which is false for a BOX nothing routes here."""
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    rig.box()
    rig.box(NAMED)
    _stamp_as(first)
    _ps(monkeypatch, {first: CLAUDE}, {first: LISTEN_CMD})
    result = run(NAMED, "--timeout", "3")
    assert result.exit_code == pager.EX_SECOND_ARM, result.output
    assert "hears for you" not in result.output
    assert result.stderr.splitlines()[-1] == (
        f"pager listen: refused: this session is already listening, pid {first}, and "
        f"{NAMED} is neither this session's box nor subscribed - to hear {NAMED}, run "
        f"message subscription add {NAMED}")


# ── the helper, pure ────────────────────────────────────────────────────

def test_other_listener_writes_nothing_even_for_a_stale_stamp(rig):
    """Unlike `is_listening`, which clears a stale stamp, the refusal's
    question is read-only."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    stamp = _stamp_as(proc.pid)
    assert _seat.other_listener(rig.seat, SESSION, ps=lambda: ({}, {})) is None
    assert stamp.read_text() == f"{proc.pid}\n"


def test_the_listen_help_names_exit_3():
    from click.testing import CliRunner
    out = CliRunner().invoke(pager.cli, ["listen", "--help"], prog_name="pager").output
    assert "3 already listening" in out
