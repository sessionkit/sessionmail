"""SM-726 do-003 (R3; design.md (l3) and (l5)): `pager mute` / `pager unmute`,
and `pager off`/`pager stop` MUTE the pager monitor instead of ending it.

- The `off` stamp mutes WHATEVER listens: a muted listener, monitor or hand,
  holds its lines and pages nothing; `pager unmute` resumes it, and so does
  the calm `pager listen` beside a monitor (do-001), which says so.
- `pager off`/`stop` on the monitor REFUSE (do-007, design.md (l7), amending
  do-003's off=mute): one line naming `pager mute` and `pager stop --kill`,
  exit 4, nothing written, the pid alive. On a hand listener it stops, as today. `--kill` is the only real
  end of a monitor, and says "ended for good".
- With nothing listening, `unmute` (and `mute`) say the (l5) sentence from
  hook_lines.toml, with both ways out, and exit 3.

Hermetic, on test_pager_monitor.py's rig (a maildir root and a seat home
under tmp_path). What `ps` says about a stand-in listener is injected; the
end-to-end tests run a REAL `pager listen --monitor` child on a pipe, as
test_pager_monitor.py's flush test does.
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import threading
import time

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, _seat, pager
from sessionmail.pager import cli
from test_pager_monitor import DEADLINE, rig, sid, until  # noqa: F401

SESSION = "sess-a"
CLAUDE = 7_000_001           # a claude process that exists only in the table
MONITOR_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor"
LISTEN_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen"


def invoke(*args: str):
    return CliRunner().invoke(cli, list(args), prog_name="pager")


@pytest.fixture
def holder():
    """A live, harmless process to stand in for this session's listener,
    reaped as it exits, so a stop's liveness poll sees it go."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        reaper.join(DEADLINE)


def _hold(rig, monkeypatch, pid: int, cmd: str, session: str = SESSION) -> None:
    """Stamp PID as SESSION's listener, and make `ps` say it runs CMD."""
    path = rig.listening(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({pid: CLAUDE}, {pid: cmd}))
    monkeypatch.setattr(_seat, "_ps_one", lambda p: ({pid: CLAUDE}, {pid: cmd}))


def _muted(session: str = SESSION) -> bool:
    return _seat.stopped_on_purpose(None, session)


# ── `pager mute` / `pager unmute` on the monitor ──────────────────────────

def test_mute_on_the_monitor_leaves_it_running_and_writes_off(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    r = invoke("mute")
    assert r.exit_code == 0, r.output
    assert r.output.strip() == (f"pager mute: the pager monitor (pid {holder.pid}) is muted: it is "
                                "still running and pages nothing; `pager unmute` resumes")
    assert _muted()
    assert holder.poll() is None                         # alive: nothing was signalled
    assert rig.listening().read_text() == f"{holder.pid}\n"


def test_unmute_on_the_monitor_clears_off_and_says_it_pages_again(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    _seat.write_off(None, SESSION)
    r = invoke("unmute")
    assert r.exit_code == 0, r.output
    assert r.output.strip() == f"pager unmute: the pager monitor (pid {holder.pid}) pages again"
    assert not _muted()
    assert holder.poll() is None


@pytest.mark.parametrize("cmd", [MONITOR_CMD, LISTEN_CMD])
def test_unmute_when_not_muted_is_a_no_op(rig, holder, monkeypatch, cmd):
    """do-007 (design.md (l7)): a live listener, no `off` stamp - one line,
    exit 0, and nothing written (the stamp dir is byte-for-byte as before)."""
    _hold(rig, monkeypatch, holder.pid, cmd)
    stamps = rig.listening().parent
    before = {p.name: p.read_bytes() for p in stamps.iterdir()}
    r = invoke("unmute")
    assert r.exit_code == 0, r.output
    assert r.output.strip() == "pager unmute: not muted: already listening"
    assert len(r.output.strip().splitlines()) == 1
    assert {p.name: p.read_bytes() for p in stamps.iterdir()} == before
    assert not _muted() and holder.poll() is None


def test_unmute_when_not_muted_and_nothing_listens_is_still_the_l5_text_exit_3(rig):
    r = invoke("unmute")
    assert r.exit_code == pager.EX_NOTHING == 3
    assert r.output.strip() == f"pager unmute: {pager.LINE_NO_MONITOR}"


# ── the stamp mutes whatever listens: a hand listener too ──────────────────

def test_mute_and_unmute_on_a_hand_listener(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, LISTEN_CMD)
    r = invoke("mute")
    assert r.exit_code == 0, r.output
    assert f"the listener (pid {holder.pid}) is muted" in r.output
    assert "pager monitor" not in r.output
    assert _muted() and holder.poll() is None
    r = invoke("unmute")
    assert r.exit_code == 0, r.output
    assert r.output.strip() == f"pager unmute: the listener (pid {holder.pid}) pages again"
    assert not _muted()


# ── nothing listening: the (l5) sentence, both ways out, exit 3 ───────────

def test_unmute_with_nothing_listening_names_both_ways_out_exit_3(rig):
    _seat.write_off(None, SESSION)
    r = invoke("unmute")
    assert r.exit_code == pager.EX_NOTHING == 3
    assert r.output.strip() == f"pager unmute: {pager.LINE_NO_MONITOR}"
    assert "can only start when the seat launches" in r.output
    assert "run `pager listen` in the background" in r.output
    assert "restart the seat" in r.output
    assert not _muted()                                  # the stamp is cleared all the same


def test_mute_with_nothing_listening_writes_off_and_says_so_exit_3(rig):
    r = invoke("mute")
    assert r.exit_code == 3
    assert pager.LINE_NO_MONITOR in r.output
    assert _muted()


def test_the_no_monitor_sentence_is_kept_once_in_hook_lines():
    from pathlib import Path
    root = Path(pager.__file__).resolve().parent
    text = (root / "hook_lines.toml").read_text()
    assert text.count("can only start when the seat launches") == 1
    assert "can only start when the seat launches" not in (root / "pager.py").read_text()


# ── `pager off` / `pager stop`: REFUSE on the monitor, stop a hand listener ─

@pytest.mark.parametrize("verb", ["off", "stop"])
@pytest.mark.parametrize("muted", [False, True])
def test_off_on_the_monitor_refuses_and_writes_nothing(rig, holder, monkeypatch, verb, muted):
    """do-007 (design.md (l7)): exit 4, ONE line naming `pager mute` and
    `pager stop --kill`, the pid alive, and the stamp dir exactly as it was
    (no `off` stamp written - nor one cleared, on a seat already muted)."""
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    if muted:
        _seat.write_off(None, SESSION)
    stamps = rig.listening().parent
    before = {p.name: p.read_bytes() for p in stamps.iterdir()}
    r = invoke(verb)
    assert r.exit_code == pager.EX_MONITOR_REFUSED == 4, r.output
    lines = r.output.strip().splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"pager {verb}: refused: the pager monitor (pid {holder.pid})")
    assert "`pager mute`" in lines[0] and "`pager stop --kill` ends it for good" in lines[0]
    assert holder.poll() is None                         # alive: nothing was signalled
    assert {p.name: p.read_bytes() for p in stamps.iterdir()} == before
    assert _muted() is muted


@pytest.mark.parametrize("args", [("off",), ("stop",), ("stop", "--kill"), ("off", "--kill")])
def test_off_on_a_hand_listener_stops_it_as_today(rig, holder, monkeypatch, args):
    """do-008 (verify-001 F2): the line names the verb as typed, `pager
    stop:` or `pager off:`, as the refusal does."""
    _hold(rig, monkeypatch, holder.pid, LISTEN_CMD)
    r = invoke(*args)
    assert r.exit_code == 0, r.output
    assert r.output.strip() == (f"pager {args[0]}: stopped listener {holder.pid} for session "
                                f"{SESSION}")
    assert until(lambda: holder.poll() is not None)
    assert not rig.listening().exists()
    assert _muted()                                      # SM-410: on purpose


@pytest.mark.parametrize("verb", ["off", "stop"])
def test_kill_ends_the_monitor_for_good_and_says_so(rig, holder, monkeypatch, verb):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    r = invoke(verb, "--kill")
    assert r.exit_code == 0, r.output
    assert r.output.strip() == (f"pager {verb}: the pager monitor (pid {holder.pid}) for session "
                                f"{SESSION} is {pager.MONITOR_ENDED}")
    assert "ended for good: Claude Code does not restart a monitor" in r.output
    assert until(lambda: holder.poll() is not None)
    assert not rig.listening().exists()


@pytest.mark.parametrize("verb", ["off", "stop"])
def test_off_with_nothing_listening_is_todays_exit_3(rig, verb):
    r = invoke(verb, "--kill")
    assert r.exit_code == 3
    assert r.output.strip() == f"pager {verb}: nothing listening for session {SESSION}"
    assert _muted()


@pytest.mark.parametrize("verb", ["off", "stop"])
def test_no_stop_line_names_the_other_verb(rig, holder, monkeypatch, verb):
    """do-008 (verify-001 F2): every path, typed as VERB, says `pager VERB:`
    and never the other name - refuse, end, stop and nothing."""
    other = {"off": "stop", "stop": "off"}[verb]
    outs = []
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    outs.append(invoke(verb).output)                     # refused
    outs.append(invoke(verb, "--kill").output)           # ended
    outs.append(invoke(verb).output)                     # nothing listening
    for out in outs:
        assert out.startswith(f"pager {verb}: "), out
        assert f"pager {other}:" not in out, out


# ── the ride-along after `--kill`, on a seat that is not required ─────────

def test_the_ride_along_names_the_ended_monitor_after_kill(rig, holder, monkeypatch):
    """do-008 (verify-001 F1): not required. A monitor armed (its stamp),
    `pager stop --kill` ended it, and nothing listens: the state line names
    the ended monitor and the two ways out - never "`pager unmute` resumes",
    since there is nothing left to unmute - and is not a DEAF line."""
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    (rig.listening().parent / _seat.MONITOR_ARMED_STAMP).write_text("")
    assert pager._state_lines(rig.seat, SESSION) == [pager.LINE_LISTENING_MONITOR]
    assert invoke("stop", "--kill").exit_code == 0
    assert until(lambda: holder.poll() is not None)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    for required in (None, _seat.Required(False, "test")):
        lines = pager._state_lines(rig.seat, SESSION, required=required)
        assert lines == [pager.LINE_MONITOR_ENDED], lines
    line = pager.LINE_MONITOR_ENDED
    assert "pager unmute" not in line and "muted" not in line and "DEAF" not in line
    assert "pager monitor was ended" in line
    assert "run `pager listen` in the background" in line and "restart the seat" in line
    r = invoke("unmute")
    assert r.exit_code == 3 and "not running" in r.output, "unmute there would not resume"


def test_the_monitor_ended_line_is_kept_once_in_hook_lines():
    from pathlib import Path
    root = Path(pager.__file__).resolve().parent
    text = (root / "hook_lines.toml").read_text()
    assert text.count("\nmonitor_ended = ") == 1
    assert "pager monitor was ended, and Claude Code" not in (root / "pager.py").read_text()


# ── the ride-along says muted while a live listener is muted ──────────────

def test_the_ride_along_says_muted_over_a_live_monitor(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    assert pager._state_lines(rig.seat, SESSION) == [pager.LINE_LISTENING_MONITOR]
    _seat.write_off(rig.seat, SESSION)
    assert pager._state_lines(rig.seat, SESSION) == [pager.LINE_OFF]
    assert pager.LINE_OFF.startswith("pager is muted")


# ── the user text: the off epilog names no person and quotes no ruling ────

def test_the_off_help_and_tool_name_no_person():
    """Folded from SM-734: the rule (never `ps | grep`) stands, with no name
    and no quoted ruling, in the --help and the MCP tool description."""
    out = invoke("off", "--help").output
    assert "Never find a listener with `ps | grep`" in out
    desc = _mcp._description(cli.commands["off"])
    for text in (out, desc):
        assert "Mi" "ke" not in text and "voice" not in text
        assert not re.search(r"\b[0-9]{1,2}:[0-9]{2}\b", text)    # no quoted ruling clock
    assert "--kill" in out and "REFUSES" in out and "exits 4" in out


def test_mute_and_unmute_are_on_the_page_and_off_is_marked_old():
    out = invoke("--help").output
    assert " mute " in out and " unmute " in out
    assert "old name" in cli.commands["off"].get_short_help_str(80)


# ── a muted listener HOLDS: a hand `pager listen` pages nothing ───────────

def test_a_muted_hand_listener_pages_nothing_until_unmuted(rig, monkeypatch):
    """The stamp mutes WHATEVER listens. A hand listen armed, then muted,
    sees mail arrive and pages nothing; once unmuted, it pages (exit 0)."""
    rig.box()
    marks: list[int] = []

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        _seat.write_off(rig.seat, SESSION)
        rig.deliver("held@example.invalid")
        time.sleep(1.0)                                  # ample: T = 0, poll 0.05 s
        marks.append(rig.seen())
        _seat.clear_off(rig.seat, SESSION)

    t = threading.Thread(target=script, daemon=True)
    t.start()
    r = CliRunner().invoke(cli, ["listen", "--timeout", str(DEADLINE)], prog_name="pager")
    t.join(DEADLINE)
    assert marks == [0], "a muted listener paged"
    assert r.exit_code == 0, r.output
    assert rig.seen() == 1


def test_a_muted_hand_listener_times_out_with_nothing_paged(rig):
    rig.box()

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        _seat.write_off(rig.seat, SESSION)
        rig.deliver("held2@example.invalid")

    t = threading.Thread(target=script, daemon=True)
    t.start()
    r = CliRunner().invoke(cli, ["listen", "--timeout", "1.5"], prog_name="pager")
    t.join(DEADLINE)
    assert r.exit_code == 124, r.output
    assert rig.seen() == 0
    assert _seat.stopped_on_purpose(rig.seat, SESSION)   # still muted: the stamp stands


# ── end to end: a REAL monitor child, muted, unmuted, killed ──────────────

def _line(proc, wait: float) -> str | None:
    ready, _, _ = select.select([proc.stdout], [], [], wait)
    return proc.stdout.readline().decode() if ready else None


@pytest.fixture
def child(rig, sid, monkeypatch):
    """A real `pager listen --monitor` on a pipe, armed for SID, reaped as it
    exits. `is_monitor` is told its pid: its argv (`python -c ...`) is not
    the shape the regex reads, and the rig's `ps` table is empty."""
    rig.box(sid)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from sessionmail.pager import cli; cli()",
         "listen", "--monitor"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reaper = threading.Thread(target=proc.wait, daemon=True)
    try:
        assert until(lambda: rig.listening(sid).exists()
                     and rig.listening(sid).read_text().strip() == str(proc.pid)), \
            proc.stderr.read1(65536) if proc.poll() is not None else "never armed"
        monkeypatch.setattr(_seat, "is_monitor",
                            lambda pid, ps=None: True if pid == proc.pid else None)
        reaper.start()
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        if reaper.is_alive():
            reaper.join(DEADLINE)
        else:
            proc.wait()


def test_a_real_monitor_muted_pages_nothing_and_unmute_resumes(rig, sid, child):
    r = invoke("mute")
    assert r.exit_code == 0, r.output
    assert "is muted: it is still running" in r.output
    rig.deliver("muted@example.invalid", sid)
    assert _line(child, 1.5) is None, "a muted monitor said a line"
    assert child.poll() is None
    r = invoke("unmute")
    assert r.exit_code == 0, r.output
    line = _line(child, DEADLINE)
    assert line is not None and line.startswith("pager: 1 message waiting"), line
    assert child.poll() is None


def test_a_real_monitor_refuses_off_and_keeps_paging(rig, sid, child):
    """do-007: `pager off` on a real monitor refuses, and the monitor goes on
    paging - it was neither stopped nor muted."""
    r = invoke("off")
    assert r.exit_code == pager.EX_MONITOR_REFUSED, r.output
    assert "refused" in r.output
    assert child.poll() is None, "pager off ended the monitor"
    assert not _seat.stopped_on_purpose(rig.seat, sid)
    rig.deliver("refused@example.invalid", sid)
    line = _line(child, DEADLINE)
    assert line is not None and line.startswith("pager: 1 message waiting"), line


def test_a_real_monitor_muted_then_the_calm_listen_resumes(rig, sid, child):
    r = invoke("mute")
    assert r.exit_code == 0, r.output
    rig.deliver("off@example.invalid", sid)
    assert _line(child, 1.5) is None, "a muted monitor said a line"
    assert child.poll() is None, "pager mute ended the monitor"
    # The calm `pager listen` beside the monitor (do-001) clears the stamp,
    # and says so - an unmute.
    r = CliRunner().invoke(cli, ["listen", "--timeout", "5"], prog_name="pager")
    assert r.exit_code == pager.EX_MONITOR_HEARS, r.output
    said = [ln for ln in r.output.splitlines() if ln.strip()]
    assert said[0].startswith(f"the pager monitor (pid {child.pid}) hears for this session")
    assert said[1] == (f"it was muted: this `pager listen` unmuted it, so the pager monitor "
                       f"(pid {child.pid}) pages again.")
    line = _line(child, DEADLINE)
    assert line is not None and line.startswith("pager: 1 message waiting"), line


def test_a_real_monitor_ends_only_by_kill(rig, sid, child):
    r = invoke("stop", "--kill")
    assert r.exit_code == 0, r.output
    assert pager.MONITOR_ENDED in r.output
    assert until(lambda: child.returncode is not None)
    assert child.returncode == 0
    assert not rig.listening(sid).exists()
