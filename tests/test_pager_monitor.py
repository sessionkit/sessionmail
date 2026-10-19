"""`pager listen --monitor` (SM-678): the pager as a Claude Code plugin monitor.

design.md (SM-678 task dir) D1-D8: a hidden flag; ONE flushed stdout line per
page and nothing else on stdout; the loop goes on after a page with the marks,
the carry stamp and the woke records written as today; SIGTERM/INT/HUP end it
quietly and clear the `listening` stamp; a DEAF line and exit 2 when it cannot
look; lines at least the spacing apart; a second arm WAITS and takes over.

Hermetic, as tests/test_pager_listen.py is: a maildir root under tmp_path, a
seat under SESSIONMAIL_HOME, `wake *` from the env layer. The monitor never
returns by itself, so each run is ended the way claude ends one: a SIGTERM,
sent from a helper thread once the marks say the pages happened. The SIGTERM
is sent only while the monitor's own handler is installed, so a run that
ended early can never take the test process down with it. A run that ends
ITSELF (an exec it followed, a signal the script sent) is never sent one:
the helper waits for it to put its handlers back instead (SM-814).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat, pager, rules
from sessionmail import _settings
from sessionmail.pager import cli

DEADLINE = 20.0          # the longest any run here may take before it is ended
_n = 0


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    root.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "0.4")
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, "60")
    monkeypatch.setenv(pager.MONITOR_SPACING_VAR, "0.05")
    monkeypatch.delenv(pager.PAGE_LIST_VAR, raising=False)
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "wake *")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    # Stopped (idle past the threshold): T = 0, a page is immediate.
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 75.0)
    hooks_on = Path(os.environ["HOME"]) / _settings.RELATIVE
    hooks_on.parent.mkdir(parents=True, exist_ok=True)
    hooks_on.write_text('{"version": 1, "hooks": true}\n')

    class Rig:
        def __init__(self) -> None:
            self.root = root
            self.seat = _seat.resolve()

        def box(self, name: str = "sess-a") -> Path:
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def deliver(self, mid: str, name: str = "sess-a", subject: str = "SECRET-SUBJECT") -> str:
            global _n
            _n += 1
            b = self.box(name)
            fname = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.monitor"
            (b / "tmp" / fname).write_bytes(
                f"From: SENDER-NAME <beta@example.invalid>\r\nTo: {name}@example.invalid\r\n"
                f"Subject: {subject}\r\nMessage-ID: <{mid}>\r\n\r\nTHE BODY\r\n".encode())
            os.rename(b / "tmp" / fname, b / "new" / fname)
            return fname

        def seen(self) -> int:
            m = _seat.marks_path(self.seat, "sess-a")
            text = m.read_text() if m.exists() else ""
            return sum(1 for line in text.splitlines() if " seen " in f" {line} ")

        def listening(self, session: str = "sess-a") -> Path:
            return _seat.stamp_dir(self.seat, session) / _seat.LISTENING_STAMP

    return Rig()


@pytest.fixture
def sid(rig, fresh_session, monkeypatch):
    """SM-558: this test's own fresh session id (conftest `fresh_session`),
    set over the rig's `sess-a`, for a test that runs a REAL child."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", fresh_session)
    return fresh_session


def _ours() -> bool:
    """Is the monitor's handler (not pytest's, not the default) on SIGTERM?"""
    handler = signal.getsignal(signal.SIGTERM)
    return callable(handler) and getattr(handler, "__name__", "") == "ended"


def end_monitor() -> None:
    """End a running monitor as claude would: SIGTERM - only to our handler."""
    for _ in range(200):
        if _ours():
            os.kill(os.getpid(), signal.SIGTERM)
            return
        time.sleep(0.01)


def driven(script, ends_itself=lambda: False) -> threading.Thread:
    """Run SCRIPT beside the monitor, then end it - always, by DEADLINE.

    ENDS_ITSELF says the run is already ending by itself once SCRIPT has done
    its job: it followed a session (the fake exec raised) or the script sent
    its own signal. Such a run is not sent a SIGTERM; the helper waits for it
    to restore its handlers. A SIGTERM there lands in the monitor's own
    unwind, in its `exit` row or its handler restore, and replaces the end
    the test asserts on with SystemExit(0), or leaks `ended` onto SIGTERM for
    every later test (SM-814 rca: Forms A and B). The SIGTERM still goes if
    SCRIPT failed, or if the run has not restored its handlers by DEADLINE.
    """
    def body() -> None:
        done = False
        try:
            script()
            done = True
        finally:
            if not (done and ends_itself() and until(lambda: not _ours())):
                end_monitor()
    t = threading.Thread(target=body, daemon=True)
    t.start()
    return t


def until(pred, timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ── SM-814: the helper never signals a run that ends itself ───────────────

@pytest.mark.parametrize("ends_itself,signalled", [
    (lambda: True, False),     # it ended itself: wait for its restore, no SIGTERM
    (lambda: [], True),        # it did not: ended as claude would
    (None, True),              # the default: every run is ended
], ids=["ends-itself", "does-not", "default"])
def test_driven_signals_only_a_run_that_does_not_end_itself(ends_itself, signalled):
    got: list[int] = []

    def ended(signum, frame) -> None:        # the monitor's handler, by name
        got.append(signum)

    previous = signal.signal(signal.SIGTERM, ended)
    try:
        kw = {} if ends_itself is None else {"ends_itself": ends_itself}
        t = driven(lambda: None, **kw)
        until(lambda: got, timeout=0.5)       # a slow unwind, as under load
        signal.signal(signal.SIGTERM, previous)   # ... then the run's restore
        t.join(DEADLINE)
        assert not t.is_alive()
        assert got == ([signal.SIGTERM] if signalled else [])
    finally:
        signal.signal(signal.SIGTERM, previous)

def settle() -> None:
    """The line is said AFTER the marks (D3: the carry stamp must be there
    before the wake turn starts), so a mark seen is a line about to be said."""
    time.sleep(0.4)


def monitor(*argv: str, runner: "CliRunner | None" = None):
    return (runner or CliRunner()).invoke(cli, ["listen", "--monitor", *argv],
                                          prog_name="pager")


def lines(result) -> list[str]:
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


class WatchedRunner(CliRunner):
    """A CliRunner whose stdout can be read WHILE the run is going (SM-754).

    `CliRunner.isolation` (public API) yields the bytes stream the run's
    stdout lands in, stdout first in every click from 8.1 on. The monitor
    flushes each line as it says it (D2), so a helper thread can watch the
    lines arrive here before the run has ended. Nothing in the run changes.
    """

    stdout_bytes = None

    @contextlib.contextmanager
    def isolation(self, *args, **kwargs):
        with super().isolation(*args, **kwargs) as streams:
            self.stdout_bytes = streams[0]
            yield streams

    def said(self) -> int:
        """How many whole, non-blank lines the run has said so far."""
        buf = self.stdout_bytes
        if buf is None:
            return 0
        *whole, _partial = buf.getvalue().split(b"\n")
        return sum(1 for ln in whole if ln.strip())


# ── SM-754: the helper sees a line WHILE the run is still going ──────────

def test_watched_runner_sees_a_flushed_line_mid_run():
    """The three-mails test claims the carry stamp once the page's line is
    said, read off WatchedRunner mid-run. If `said()` only saw the line once
    the run ended, that wait would deadlock here instead of passing: the run
    holds until the helper has seen its first line."""
    import click

    seen_mid_run = threading.Event()
    runner = WatchedRunner()

    @click.command()
    def two_lines() -> None:
        click.echo("first")                       # a whole line, flushed
        sys.stdout.write("second, half")          # no newline yet: not a line
        sys.stdout.flush()
        if not seen_mid_run.wait(DEADLINE):
            raise SystemExit(3)
        click.echo(" and the rest")

    counts: list[int] = []

    def watch() -> None:
        if until(lambda: runner.said() >= 1):
            time.sleep(0.05)                      # room for a wrong 2nd count
            counts.append(runner.said())
            seen_mid_run.set()

    assert runner.said() == 0                     # before any run: nothing
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    result = runner.invoke(two_lines)
    t.join(DEADLINE)
    assert result.exit_code == 0, result.output
    assert counts == [1]                          # the half line is not counted
    assert runner.said() == 2                     # the run's own end: both lines
    assert result.stdout == "first\nsecond, half and the rest\n"


# ── D2: N pages, N lines, nothing else on stdout ─────────────────────────

def test_three_mails_three_pages_three_lines_and_nothing_else(rig):
    rig.box()
    seen_carry: list[bool] = []
    runner = WatchedRunner()

    def script() -> None:
        for i, mid in enumerate(("m1@example.invalid", "m2@example.invalid",
                                 "m3@example.invalid"), start=1):
            rig.deliver(mid)
            assert until(lambda: rig.seen() >= i), f"page {i} never marked"
            # D3: the carry stamp is written per page (every wake carries),
            # BEFORE the page's line: the line starts the wake turn, and that
            # turn claims the stamp. So claim as the wake turn would, once the
            # line is said. The mark is written before the stamp (pager.py's
            # write_mark, then write_carry), so a claim made at the mark races
            # the stamp and loses it under load (SM-754 rca: findings/rca.md).
            assert until(lambda: runner.said() >= i), f"page {i} never said its line"
            seen_carry.append(_seat.claim_carry(rig.seat, "sess-a"))
            assert rig.listening().exists(), "listening stamp missing mid-run"
        settle()

    t = driven(script)
    result = monitor(runner=runner)
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    out = lines(result)
    assert len(out) == 3, result.stdout
    for line in out:
        assert line.startswith("pager: 1 message waiting"), line
        assert len(line) <= pager.MONITOR_LINE_MAX
        # the quiet page: no sender, no subject, no re-arm
        assert "SENDER-NAME" not in line and "beta@" not in line
        assert "SECRET-SUBJECT" not in line
        assert "re-arm" not in line.lower()
    assert rig.seen() == 3
    assert seen_carry == [True, True, True]
    # D5: SIGTERM ended it with exit 0 and the listening stamp went with it.
    assert not rig.listening().exists()
    # D4: the confirm once per monitor process, never per page.
    assert result.stderr.count("listening mail sent") == 1, result.stderr


def test_a_burst_is_one_page_one_line(rig):
    rig.box()

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        rig.deliver("b1@example.invalid")
        rig.deliver("b2@example.invalid")
        assert until(lambda: rig.seen() >= 2)
        time.sleep(0.3)        # room for a wrong second line to show

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    out = lines(result)
    assert result.exit_code == 0, result.stderr
    assert len(out) in (1, 2), result.stdout
    assert sum(int(line.split()[1]) for line in out) == 2, out


# ── D3: the reminder window pages an unscooped mail again ────────────────

def test_the_reminder_window_pages_again(rig, monkeypatch):
    monkeypatch.setenv(_seat.REMIND_VAR, "0.01")      # 0.6 s
    rig.box()

    def script() -> None:
        rig.deliver("again@example.invalid")
        assert until(lambda: rig.seen() >= 2), "never paged a second time"
        settle()

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    assert len(lines(result)) >= 2, result.stdout


def test_inside_the_reminder_window_it_pages_once(rig):
    rig.box()

    def script() -> None:
        rig.deliver("once@example.invalid")
        assert until(lambda: rig.seen() >= 1)
        time.sleep(0.6)        # many polls: none may page it again

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    assert len(lines(result)) == 1, result.stdout
    assert rig.seen() == 1


# ── D1: what --monitor refuses ───────────────────────────────────────────

@pytest.mark.parametrize("argv", [("--timeout", "1"), ("sess-a",), ("--quiet",)])
def test_monitor_refuses_timeout_box_and_quiet(rig, argv):
    rig.box()
    result = monitor(*argv)
    assert result.exit_code == pager.EX_USAGE, (result.exit_code, result.stderr)
    assert "refused: --monitor cannot take" in result.stderr
    assert result.stdout == ""
    assert not rig.listening().exists()


def test_the_flag_is_hidden_from_the_help(rig):
    result = CliRunner().invoke(cli, ["listen", "--help"], prog_name="pager")
    assert "--monitor" not in result.output


# ── D5: deaf says so, on stdout, and exits 2 ─────────────────────────────

def test_a_monitor_that_cannot_look_says_deaf_and_exits_2(rig, monkeypatch, tmp_path):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MAIL", str(tmp_path / "no-such-maildir"))
    result = monitor()
    assert result.exit_code == pager.EX_COULD_NOT_LOOK, result.stderr
    out = lines(result)
    assert len(out) == 1 and out[0].startswith("pager: DEAF - "), result.stdout
    assert "pager listen" in out[0]


# ── D8: a second arm waits and takes over ────────────────────────────────

def test_a_monitor_waits_for_the_other_listener_then_takes_over(rig):
    rig.box()
    other = subprocess.Popen(["sleep", "30"])
    try:
        _seat.write_listening(rig.seat, "sess-a", other.pid)

        def script() -> None:
            time.sleep(0.4)
            assert rig.listening().read_text().strip() == str(other.pid)
            other.kill()
            other.wait()
            assert until(lambda: rig.listening().exists()
                         and rig.listening().read_text().strip() == str(os.getpid()))
            rig.deliver("after@example.invalid")
            assert until(lambda: rig.seen() >= 1)
            settle()

        t = driven(script)
        result = monitor()
        t.join(DEADLINE)
    finally:
        if other.poll() is None:
            other.kill()
    assert result.exit_code == 0, result.stderr
    assert "the monitor waits and takes over" in result.stderr
    assert len(lines(result)) == 1, result.stdout


# ── the line and the spacing, as units ───────────────────────────────────

def test_monitor_out_spaces_its_lines(capsys):
    out = pager.MonitorOut(spacing=0.2)
    t0 = time.monotonic()
    out.say("one")
    out.say("two\nstill two")
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.19
    assert capsys.readouterr().out == "one\ntwo still two\n"


@pytest.mark.parametrize("raw,want", [("", 2.5), ("1", 1.0), ("-1", 2.5), ("x", 2.5)])
def test_monitor_spacing_reads_the_env_and_ignores_typos(raw, want):
    assert pager.monitor_spacing({pager.MONITOR_SPACING_VAR: raw}) == want


def test_monitor_line_is_bounded_and_keeps_its_tail():
    class A:
        def __init__(self, n):
            self.name = f"n{n}"
            self.path = Path("/nonexistent")
            self.message_id = f"{n}@x"

    entries = [A(i) for i in range(40)]
    line = pager.monitor_line(entries, {}, fresh=True)
    assert len(line) <= pager.MONITOR_LINE_MAX
    assert line.startswith("pager: 40 messages waiting")
    assert line.endswith(pager.MONITOR_TAIL_FRESH)
    stale = pager.monitor_line(entries[:1], {}, fresh=False)
    assert stale.startswith("pager: 1 message waiting")
    assert stale.endswith(pager.MONITOR_TAIL_STALE)


def test_deaf_line_names_the_way_back():
    line = pager.deaf_line("no maildir\nat /x")
    assert line.startswith("pager: DEAF - no maildir at /x.")
    assert "`pager listen`" in line


# ── a REAL child on a pipe, ended by `pager off` (D2 flush, O8 option (i)) ─

def test_a_child_monitor_flushes_its_line_down_a_pipe_and_pager_off_ends_it(rig, sid):
    """D2's flush is only proven through a real pipe: under CliRunner stdout
    is an in-memory buffer and a missing flush passes. And O8's option (i),
    RULED for this build: `pager off` ends a monitor - exit 0, its
    `listening` stamp gone, no line said for the stop - and nothing brings
    it back. The child runs on the REAL `ps`, with its own session: under a
    seat whose listener is live it still arms (SM-558)."""
    import select
    import sys

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
        rig.deliver("piped@example.invalid", sid)
        ready, _, _ = select.select([proc.stdout], [], [], DEADLINE)
        assert ready, "no line down the pipe: the page was not flushed"
        line = proc.stdout.readline().decode()
        assert line.startswith("pager: 1 message waiting"), line
        assert proc.poll() is None, "the monitor exited on a page"
        # O8 (i): `pager off`, as a seat would run it. The reaper reaps the
        # child as it exits, so off's liveness poll sees it go (a zombie
        # would read as alive and earn a SIGKILL).
        reaper.start()
        result = CliRunner().invoke(cli, ["off"], prog_name="pager")
        assert result.exit_code == 0, result.output
        reaper.join(DEADLINE)
        assert proc.returncode == 0, (proc.returncode, proc.stderr.read())
        assert proc.stdout.read() == b"", "the stop said a line"
        assert not rig.listening(sid).exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize("signame", ["SIGINT", "SIGHUP"])
def test_sigint_and_sighup_end_it_like_sigterm(rig, signame):
    """D5: all three of MONITOR_SIGNALS end it with exit 0 and take the
    `listening` stamp with them; the runs above end only by SIGTERM."""
    rig.box()

    sent: list[str] = []

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        for _ in range(200):
            if _ours():
                os.kill(os.getpid(), getattr(signal, signame))
                sent.append(signame)
                return
            time.sleep(0.01)

    t = driven(script, ends_itself=lambda: sent)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    assert lines(result) == [], result.stdout
    assert not rig.listening().exists()


# ── SM-753: the stamp's startup lifecycle, signals injected at the seam ──
#
# Deterministic: the signal is sent by the code under test's own seam, not
# raced under load. Each test puts the caller's handlers back itself, so a
# red here can never leave `ended` installed for the rest of the suite.

@pytest.fixture
def caller_handlers():
    """The handlers MONITOR_SIGNALS had before the test, put back after it
    with the real `signal.signal` (a test may wrap that)."""
    saved = {n: signal.getsignal(getattr(signal, n)) for n in pager.MONITOR_SIGNALS}
    real = signal.signal
    yield saved
    for n, handler in saved.items():
        real(getattr(signal, n), handler)


@pytest.mark.parametrize("signame", pager.MONITOR_SIGNALS)
def test_a_signal_the_instant_the_stamp_is_written_still_clears_it(
        rig, caller_handlers, monkeypatch, signame):
    """No instant with a `listening` stamp and no clear armed: the clear is
    armed BEFORE the write. It was armed after it (and after the
    monitor-armed stamp), so a signal there ended the monitor with a stamp
    naming a dead pid - the SIGINT/SIGHUP red under I/O load (SM-814 rca s5)."""
    rig.box()
    real = _seat.write_listening

    def write_then_signal(*args, **kwargs):
        out = real(*args, **kwargs)
        os.kill(os.getpid(), getattr(signal, signame))     # handled at the next check
        return out

    monkeypatch.setattr(_seat, "write_listening", write_then_signal)
    result = monitor()
    assert result.exit_code == 0, result.stderr
    assert lines(result) == [], result.stdout
    assert not rig.listening().exists(), f"stale stamp: {rig.listening().read_text()!r}"


@pytest.mark.parametrize("first", ["a run that returns", "a SIGTERM"])
def test_a_signal_in_the_restore_loop_leaves_the_callers_handlers(
        rig, caller_handlers, monkeypatch, first):
    """listen()'s `finally` puts the caller's handlers back. `signal.signal`
    runs a PENDING handler before it switches, so a signal there ran `ended`,
    whose SystemExit aborted the loop and left `ended` on all three (SM-814
    rca s6). Both ways in: a run that ended by itself, and one a first
    SIGTERM ended (the second signal is the one in the loop). The first
    stands in for every end that reaches the `finally` with no signal - a
    page's ctx.exit, a refusal, a followed exec that failed."""
    rig.box()
    real = signal.signal
    fired: list[int] = []

    def restoring(sig, handler):
        if getattr(handler, "__name__", "") != "ended" and not fired:
            fired.append(sig)
            os.kill(os.getpid(), signal.SIGHUP)            # pending: real() runs it first
        return real(sig, handler)

    monkeypatch.setattr(signal, "signal", restoring)
    if first == "a run that returns":
        monkeypatch.setattr(pager, "_listen", lambda *args, **kwargs: None)
        result = monitor()
    else:
        t = driven(lambda: until(lambda: rig.listening().exists()))
        result = monitor()
        t.join(DEADLINE)
    assert fired, "the restore was never reached"
    assert result.exit_code == 0, result.stderr
    left = {n: signal.getsignal(getattr(signal, n)) for n in pager.MONITOR_SIGNALS}
    assert left == caller_handlers, f"not restored: {left}"


#: The bound a second end request is held to (condition (ii)): from the second signal to the process gone.
SECOND_SIGNAL_BOUND = 5.0


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_a_second_signal_ends_a_shutdown_that_hangs(rig, sid, signame, monkeypatch):
    """A repeated Ctrl-C or SIGTERM is how an operator escapes a shutdown
    that hangs, so the second one is never swallowed while the monitor
    unwinds (SM-753; only the bounded restore loop is quiet). A REAL child:
    the test holds the lock the `exit` row takes (`_injectlog.append`'s
    flock on `seq`, which has no deadline), so after the FIRST signal the
    child is stuck in its own shutdown - stamp already cleared, process
    still there. The SECOND ends it inside the bound."""
    import fcntl
    import sys

    from sessionmail import _injectlog

    # SM-817 bounds the first signal's wait on this lock (1 s by default),
    # which would end the child inside the 1.0 s sleep below and leave the
    # second signal nothing to escape. A long budget, set here only, keeps
    # the hang real; every assertion is as SM-753 wrote it. The default
    # budget's own test is `test_one_signal_ends_..._with_a_log_lock_held`.
    monkeypatch.setenv(_injectlog.LOCK_SECONDS_VAR, "30")
    rig.box(sid)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from sessionmail.pager import cli; cli()",
         "listen", "--monitor"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lock = None
    try:
        assert until(lambda: any(r.get("event") == "start" and r.get("pid") == proc.pid
                                 for r in _injectlog.read_rows(sid))), \
            proc.stderr.read1(65536) if proc.poll() is not None else "never logged start"
        assert rig.listening(sid).read_text().strip() == str(proc.pid)
        d = _injectlog.log_dir(sid)
        lock = os.open(d / _injectlog.SEQ_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)              # the `exit` row will wait on this

        sig = getattr(signal, signame)
        proc.send_signal(sig)
        assert until(lambda: not rig.listening(sid).exists(), timeout=5), \
            "the first signal did not clear the stamp"
        time.sleep(1.0)
        assert proc.poll() is None, "the shutdown did not hang: the test proves nothing"

        t0 = time.monotonic()
        proc.send_signal(sig)
        try:
            code = proc.wait(SECOND_SIGNAL_BOUND)
        except subprocess.TimeoutExpired:
            pytest.fail(f"a second {signame} was swallowed: still running "
                        f"{SECOND_SIGNAL_BOUND:g}s later")
        assert time.monotonic() - t0 < SECOND_SIGNAL_BOUND
        assert code == 0, proc.stderr.read()
        assert not rig.listening(sid).exists()
    finally:
        if lock is not None:
            os.close(lock)
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ── SM-817: ONE end signal is never held hostage by the session log's lock ─

#: The bound a FIRST end signal is held to with another process on a log
#: lock (SM-817): the default exit budget (`EXIT_BUDGET`,
#: 1 s) plus 1 s for the interpreter to unwind and the test to see it gone.
FIRST_SIGNAL_BOUND = 2.0
#: The documented knob, pinned by name (docs/pager-listen-internals.md).
LOG_LOCK_VAR = "SESSIONMAIL_LOG_LOCK_SECONDS"
#: The default budget it falls back to (`EXIT_BUDGET`).
EXIT_BUDGET = 1.0
_QUIET_KEYS = {"quiet", "quiet_ms_total", "quiet_ms_max"}


def _spawn_listen(rig, sid, *argv: str) -> subprocess.Popen:
    """A REAL `pager listen` child on SID, armed (its `start` row is in),
    with a `quiet` counter to read (so `read_quiet` takes its lock)."""
    import sys

    from sessionmail import _injectlog

    rig.box(sid)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from sessionmail.pager import cli; cli()", "listen", *argv],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert until(lambda: any(r.get("event") == "start" and r.get("pid") == proc.pid
                             for r in _injectlog.read_rows(sid))), \
        proc.stderr.read1(65536) if proc.poll() is not None else "never logged start"
    assert _injectlog.bump_quiet(sid, 7)
    return proc


def _hold(sid: str, name: str) -> int:
    """Another process's lock on log file NAME, as a hook stalled inside
    its write would hold it (this test process is not the child)."""
    import fcntl

    from sessionmail import _injectlog

    fd = os.open(_injectlog.log_dir(sid) / name, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _exit_rows(sid: str, pid: int) -> list[dict]:
    from sessionmail import _injectlog

    return [r for r in _injectlog.read_rows(sid)
            if r.get("event") == "exit" and r.get("pid") == pid]


def _reap(proc: subprocess.Popen, fd: int | None = None) -> None:
    if fd is not None:
        os.close(fd)
    if proc.poll() is None:
        proc.kill()                              # our own child, by its handle
        proc.wait()


def _ends_within_the_budget(proc, sig, sid, held) -> tuple[int, float, str]:
    """Hold HELD, send ONE SIG, and return (code, seconds to gone, stderr)."""
    fd = _hold(sid, held)
    try:
        t0 = time.monotonic()
        proc.send_signal(sig)
        try:
            code = proc.wait(FIRST_SIGNAL_BOUND)
        except subprocess.TimeoutExpired:
            pytest.fail(f"one {sig.name} with {held} held: still running "
                        f"{FIRST_SIGNAL_BOUND:g}s later")
        took = time.monotonic() - t0
    finally:
        _reap(proc, fd)
    return code, took, proc.stderr.read().decode()


@pytest.mark.parametrize("held", ["seq", "quiet"])
@pytest.mark.parametrize("signame", pager.MONITOR_SIGNALS)
def test_one_signal_ends_the_monitor_within_the_budget_with_a_log_lock_held(
        rig, sid, signame, held, monkeypatch):
    """SM-817: a harness ends a monitor with ONE SIGTERM (or a hang-up, or
    one Ctrl-C) while another process sits on `seq` (append's LOCK_EX) or
    `quiet` (read_quiet's LOCK_SH). At the DEFAULT budget it exits 0 inside
    the bound, stamp cleared, and the cost is SAID once on stderr, naming
    the lock and the budget: `seq` drops the `exit` row, `quiet` writes it
    without its counts. RED at 0539fd2 (still running 8 s later)."""
    from sessionmail import _injectlog

    monkeypatch.delenv(LOG_LOCK_VAR, raising=False)
    proc = _spawn_listen(rig, sid, "--monitor")
    code, took, err = _ends_within_the_budget(proc, getattr(signal, signame), sid, held)
    assert code == 0, err
    assert took >= EXIT_BUDGET * 0.8, f"{took:.3f}s: it never waited"
    assert not rig.listening(sid).exists()
    said = [ln for ln in err.splitlines() if "exit row" in ln]
    assert len(said) == 1, err                           # said once, never silently
    path = str(_injectlog.log_dir(sid) / held)
    assert path in said[0] and "1 s exit budget" in said[0], said[0]
    assert LOG_LOCK_VAR in said[0]
    assert proc.stdout.read() == b"", "stdout is the page channel: nothing about the log"
    rows = _exit_rows(sid, proc.pid)
    if held == "seq":
        assert rows == [] and "exit row not logged" in said[0]
    else:
        [row] = rows
        assert row["code"] == 0 and not _QUIET_KEYS & set(row), row
        assert "exit row logged without its quiet counts" in said[0]


@pytest.mark.parametrize("held", ["seq", "quiet"])
def test_one_ctrl_c_ends_a_hand_listen_within_the_budget_with_a_log_lock_held(
        rig, sid, held, monkeypatch):
    """SM-817, a hand `pager listen`: no handler of its own, so SIGINT is
    Python's KeyboardInterrupt, which unwinds through `_listen`'s `finally`
    and its log locks. (SIGTERM/SIGHUP kill a hand listen by default action
    before any `finally` runs: no lock is reached. Its stale stamp is
    SM-826, not this card.)"""
    from sessionmail import _injectlog

    monkeypatch.delenv(LOG_LOCK_VAR, raising=False)
    proc = _spawn_listen(rig, sid, "--quiet")
    code, took, err = _ends_within_the_budget(proc, signal.SIGINT, sid, held)
    assert took >= EXIT_BUDGET * 0.8, f"{took:.3f}s: it never waited"
    said = [ln for ln in err.splitlines() if "exit row" in ln]
    assert len(said) == 1, err
    assert str(_injectlog.log_dir(sid) / held) in said[0] and "1 s exit budget" in said[0]


@pytest.mark.parametrize("signame", pager.MONITOR_SIGNALS)
def test_an_unheld_lock_still_writes_the_exit_row_as_before(rig, sid, signame, monkeypatch):
    """SM-817's control: nothing holds a lock, so the `exit` row is written
    as it always was, quiet counts and all, at once, and nothing is said."""
    from sessionmail import _injectlog

    monkeypatch.delenv(LOG_LOCK_VAR, raising=False)
    proc = _spawn_listen(rig, sid, "--monitor")
    try:
        t0 = time.monotonic()
        proc.send_signal(getattr(signal, signame))
        assert proc.wait(FIRST_SIGNAL_BOUND) == 0
        assert time.monotonic() - t0 < EXIT_BUDGET
        [row] = _exit_rows(sid, proc.pid)
        assert (row["code"], row["quiet"], row["quiet_ms_total"], row["quiet_ms_max"]) \
            == (0, 1, 7, 7)
        assert b"exit row" not in proc.stderr.read()
    finally:
        _reap(proc)


def test_the_budget_knob_is_read_at_the_exit(rig, sid, monkeypatch):
    """`SESSIONMAIL_LOG_LOCK_SECONDS` sets the budget the child's exit uses:
    0.2 s ends it well inside the default's 1 s, and the line names 0.2."""
    from sessionmail import _injectlog

    monkeypatch.setenv(LOG_LOCK_VAR, "0.2")
    proc = _spawn_listen(rig, sid, "--monitor")
    code, took, err = _ends_within_the_budget(proc, signal.SIGTERM, sid, "seq")
    assert code == 0 and took < 0.9, f"{took:.3f}s"
    assert "0.2 s exit budget" in err, err


# ── SM-682 (SM-678 design D7): the monitor follows the session across /clear ─
#
# A /clear keeps claude and its sidecar (`<sidecar dir>/<claude pid>.json`)
# but changes the `sessionId` in it. Each tick the monitor reads that file;
# on a new id it clears THIS session's `listening` stamp, says so on stderr
# (never stdout), and execv's itself with CLAUDE_CODE_SESSION_ID=<new>. The
# exec is patched here: it records what it was handed and raises, so the run
# ends where a real exec would have replaced the process.

import json  # noqa: E402


class Execd(Exception):
    """What the patched os.execv raises: the process would be gone here."""


def write_sidecar(directory: Path, pid: int, body) -> Path:
    """Claude's sidecar for PID: a dict is JSON with the fields claude
    writes (a None sessionId leaves the key out), a str is written raw."""
    directory.mkdir(parents=True, exist_ok=True)
    if isinstance(body, str):
        text = body
    else:
        data = {"pid": pid, "status": "idle", "updatedAt": int(time.time() * 1000)}
        if body.get("sessionId") is not None:
            data["sessionId"] = body["sessionId"]
        text = json.dumps(data)
    tmp = directory / f".{pid}.json.tmp"
    tmp.write_text(text)
    os.replace(tmp, directory / f"{pid}.json")   # never a half-written read
    return directory / f"{pid}.json"


@pytest.fixture
def sidecars(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "sidecars"
    d.mkdir()
    monkeypatch.setenv(_seat.SIDECAR_DIR_ENV, str(d))
    return d


@pytest.fixture
def execv(monkeypatch):
    """os.execv, recorded: (path, argv, CLAUDE_CODE_SESSION_ID, stamp_was)."""
    calls: list[dict] = []
    probe: dict = {}

    def fake(path, argv):
        stamp = probe.get("stamp")
        calls.append({"path": path, "argv": list(argv),
                      "session": os.environ.get(pager.FOLLOW_VAR),
                      "stamped": stamp is not None and stamp().exists()})
        raise Execd(path)

    monkeypatch.setattr(pager.os, "execv", fake)
    fake.calls = calls
    fake.probe = probe
    return fake


@pytest.mark.parametrize("body,want", [
    ({"sessionId": "sess-a"}, "sess-a"),
    ({"sessionId": "  sess-b \n"}, "sess-b"),
    ({"sessionId": None}, None),                       # no sessionId
    ({"sessionId": ""}, None),                         # a blank one
    ("{not json", None),                               # bad JSON
    ("[1, 2]", None),                                  # JSON, not a sidecar
])
def test_sidecar_session_reads_one_callers_sidecar(sidecars, body, want):
    write_sidecar(sidecars, 4242, body)
    assert _seat.sidecar_session(4242) == want


def test_sidecar_session_with_no_sidecar_is_none(sidecars, tmp_path, monkeypatch):
    assert _seat.sidecar_session(4242) is None                  # no such file
    monkeypatch.setenv(_seat.SIDECAR_DIR_ENV, str(tmp_path / "absent"))
    assert _seat.sidecar_session(4242) is None                  # no such dir


@pytest.mark.parametrize("body", [
    {"sessionId": "sess-a"},                           # the same id
    {"sessionId": None},                               # no sessionId
    "{not json",                                       # bad JSON
    None,                                              # no sidecar at all
], ids=["same", "no-id", "bad-json", "missing"])
def test_follow_does_nothing_unless_the_id_changed(rig, sidecars, execv, capsys, body):
    _seat.write_listening(rig.seat, "sess-a", os.getpid())
    if body is not None:
        write_sidecar(sidecars, 4242, body)
    tried: set[str] = set()
    pager.follow_session("sess-a", 4242, rig.seat, tried)
    assert execv.calls == []
    assert tried == set()
    assert rig.listening().read_text().strip() == str(os.getpid())
    assert capsys.readouterr() == ("", "")


def test_follow_a_changed_id_clears_the_stamp_and_execs_under_it(rig, sidecars, execv, capsys):
    _seat.write_listening(rig.seat, "sess-a", os.getpid())
    execv.probe["stamp"] = rig.listening
    write_sidecar(sidecars, 4242, {"sessionId": "sess-b"})
    with pytest.raises(Execd):
        pager.follow_session("sess-a", 4242, rig.seat, set())
    assert len(execv.calls) == 1
    call = execv.calls[0]
    assert call["path"] == sys.executable
    assert call["argv"] == [sys.executable, *sys.argv]
    assert call["session"] == "sess-b"
    assert call["stamped"] is False, "the OLD session's stamp was left behind"
    out, err = capsys.readouterr()
    assert out == "", "a session change is not mail: stdout stays silent"
    assert err.count("\n") == 1 and "sess-a -> sess-b" in err, err


def test_follow_an_exec_that_fails_restamps_and_tries_once(rig, sidecars, monkeypatch, capsys):
    calls: list[str] = []

    def failing(path, argv):
        calls.append(os.environ.get(pager.FOLLOW_VAR))
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(pager.os, "execv", failing)
    _seat.write_listening(rig.seat, "sess-a", os.getpid())
    write_sidecar(sidecars, 4242, {"sessionId": "sess-b"})
    tried: set[str] = set()
    pager.follow_session("sess-a", 4242, rig.seat, tried)
    pager.follow_session("sess-a", 4242, rig.seat, tried)     # the next tick
    assert calls == ["sess-b"], "a failing exec is tried once per id, not every tick"
    assert os.environ[pager.FOLLOW_VAR] == "sess-a", "the environment was not put back"
    assert rig.listening().read_text().strip() == str(os.getpid()), "not re-stamped"
    out, err = capsys.readouterr()
    assert out == ""
    assert "could not start again under sess-b" in err and "still listening under sess-a" in err


def test_a_running_monitor_follows_a_clear(rig, sidecars, execv):
    """End to end in one process: the monitor ticks under the SAME id
    without exec'ing, then claude's sidecar changes and it execs."""
    rig.box()
    caller = os.getppid()          # under CliRunner the monitor's parent is ours
    write_sidecar(sidecars, caller, {"sessionId": "sess-a"})
    execv.probe["stamp"] = rig.listening

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        time.sleep(0.4)            # many ticks under the same id
        assert execv.calls == [], "exec'd with nothing changed"
        write_sidecar(sidecars, caller, {"sessionId": "sess-b"})
        assert until(lambda: execv.calls), "never followed the new session"

    t = driven(script, ends_itself=lambda: execv.calls)
    result = monitor()
    t.join(DEADLINE)
    assert isinstance(result.exception, Execd), (result.exception, result.stderr)
    assert len(execv.calls) == 1
    call = execv.calls[0]
    assert call["argv"] == [sys.executable, *sys.argv]
    assert call["session"] == "sess-b"
    assert call["stamped"] is False
    assert result.stdout == "", result.stdout
    assert "pager listen: session sess-a -> sess-b" in result.stderr


def test_a_waiting_monitor_follows_a_clear_and_leaves_the_others_stamp(rig, sidecars, execv):
    """D8 + D7: a monitor waiting on another listener of the OLD session
    follows claude to the new one; the stamp it waits on is not its own."""
    rig.box()
    caller = os.getppid()
    other = subprocess.Popen(["sleep", "30"])
    try:
        _seat.write_listening(rig.seat, "sess-a", other.pid)

        def script() -> None:
            time.sleep(0.3)
            write_sidecar(sidecars, caller, {"sessionId": "sess-b"})
            assert until(lambda: execv.calls)

        t = driven(script, ends_itself=lambda: execv.calls)
        result = monitor()
        t.join(DEADLINE)
        assert isinstance(result.exception, Execd), (result.exception, result.stderr)
        assert execv.calls[0]["session"] == "sess-b"
        assert rig.listening().read_text().strip() == str(other.pid)
        assert result.stdout == ""
    finally:
        other.kill()
        other.wait()


def test_a_bare_listen_never_follows(rig, sidecars, execv):
    rig.box()
    write_sidecar(sidecars, os.getppid(), {"sessionId": "sess-b"})
    result = CliRunner().invoke(cli, ["listen", "--timeout", "0.6"], prog_name="pager")
    assert result.exit_code == pager.EX_TIMEOUT, (result.exit_code, result.stderr)
    assert execv.calls == []
    assert "sess-a -> sess-b" not in result.stderr


def test_a_seatless_monitor_ticks_and_follows_too(rig, sidecars, execv, monkeypatch):
    """No seat means no queue: the monitor waits on the box's own new/,
    where `detect` used to block until mail. It now returns each interval
    so the sidecar is read every tick. No seat, no stamp to clear."""
    def no_seat(*a, **k):
        raise _seat.NoIdentity()

    monkeypatch.setattr(_seat, "resolve", no_seat)
    rig.box()
    caller = os.getppid()
    write_sidecar(sidecars, caller, {"sessionId": "sess-a"})

    def script() -> None:
        time.sleep(0.5)
        assert execv.calls == []
        write_sidecar(sidecars, caller, {"sessionId": "sess-b"})
        assert until(lambda: execv.calls)

    t = driven(script, ends_itself=lambda: execv.calls)
    result = monitor()
    t.join(DEADLINE)
    assert isinstance(result.exception, Execd), (result.exception, result.stderr)
    assert execv.calls[0]["session"] == "sess-b"
    assert result.stdout == ""
