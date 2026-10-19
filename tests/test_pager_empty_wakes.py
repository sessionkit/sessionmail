"""SM-763: the pager MONITOR made empty wakes. repro-001 reproduced them; fix-001
(option (b), done-only) closes them. findings/rca-001.md has the root cause.

Cause (3): after every waking message was marked `done` in this session's
marks file, the monitor still paged ("N message(s) waiting") once the reminder
window (SESSIONMAIL_REMIND_MINUTES) had passed since the message was last
given, while `pager status` on the same fixture listed nothing. The monitor's
only quiet set was "given inside the window" (`_seat.given_since`, seen/read).
It never read `done`, and nothing drains the queue link
(`<session>/mail/ride/new/<name>`) when no scoop runs. FIXED: the queue
listener also skips this session's `done` (`Quiet.done`, the test `pager
status` makes), read in the same read of the marks as `given`.

Cause (1): a `read` quiets a re-page for one reminder window only. That is the
ratified "Read is not terminal", and it stands. The defect was that the page
did not say so: it pointed at `message list --unread`, which hides a read
mail. FIXED (option (b)): a re-page says it is a reminder ("read, not done")
and names `pager status` (which lists it) and `message mark <ID> done` (which
quiets it). A first page of new mail keeps today's text.

Hermetic: the rig is tests/test_pager_monitor.py's (maildir root and seat
under tmp_path, `wake *` from the env layer, an in-process monitor ended by
SIGTERM). No live seat, no live ~/.mail, no live pager.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _notmuch, _seat, pager
from sessionmail.message import cli as message_cli
from sessionmail.pager import cli

from fakes import FAKE_NOTMUCH_BY_ID, write_fake
from test_pager_monitor import (  # noqa: F401 - `rig` is a fixture
    DEADLINE, driven, lines, monitor, rig, settle, until)

#: The reminder window: 60 min on the fleet. Two here. FAST (0.6 s) for a
#: test that writes its marks BEFORE it waits. RUN (3 s) for a running
#: monitor whose test marks AFTER a page: marks are stamped to the SECOND,
#: so a 0.6 s window can expire inside the page itself and a reminder lands
#: before the test's mark does.
REMIND = "0.01"
WINDOW_S = 0.6
REMIND_RUN = "0.05"
WINDOW_RUN_S = 3.0
NEW_MAIL = "pager: 1 message waiting"
REMINDER_READ = "pager: reminder - 1 message read, not done"
REMINDER_SEEN = "pager: reminder - 1 message paged before, not done"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _mark(rig, mid: str, mark: str) -> None:
    """What `message mark <n> <mark>` writes for this session (the one writer)."""
    _seat.write_mark(rig.seat, mid, mark, "sess-a", _now(), f"SM-763 {mark}")


def _status():
    return CliRunner().invoke(cli, ["status", "--all"], prog_name="pager")


def _run(rig, monkeypatch, mid: str, after_page, then=None) -> tuple[list[str], int]:
    """Deliver MID, let the monitor page it, run AFTER_PAGE (the seat's
    marks), then wait two reminder windows (RUN) with no new mail. THEN,
    when given, runs after that, followed by two more windows. Returns the
    monitor's stdout lines and how many `seen` marks it wrote."""
    monkeypatch.setenv(_seat.REMIND_VAR, REMIND_RUN)
    window = WINDOW_RUN_S
    rig.box()

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        rig.deliver(mid)
        assert until(lambda: rig.seen() >= 1), "the first page never came"
        settle()
        after_page()
        # No new mail from here on: any further line is a re-page.
        time.sleep(2 * window + 1.0)
        if then is not None:
            then()
            time.sleep(2 * window + 1.0)

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    return lines(result), rig.seen()


def _assert_reminder_tail(line: str) -> None:
    """A reminder names what LISTS a read mail and what quiets it, and does
    not send the seat only to `message list --unread` (which hides it)."""
    assert "`pager status` lists" in line, line
    assert "`message mark <ID> done` quiets" in line, line
    assert not line.endswith("message list --unread"), line
    assert len(line) <= pager.MONITOR_LINE_MAX


# ── cause (3): done quiets the monitor ───────────────────────────────────

def test_cause3_done_quiets_the_monitor(rig, monkeypatch):
    """Once the seat marks the paged mail `done`, the monitor pages it no
    more, and agrees with `pager status`, which lists nothing. The link is
    still in the queue (no scoop ran): `done` alone quiets it."""
    mid = "done-2@example.invalid"
    out, _ = _run(rig, monkeypatch, mid, lambda: _mark(rig, mid, "done"))
    assert out == [out[0]] and out[0].startswith(NEW_MAIL), out
    assert len(os.listdir(_seat.queue_new(rig.seat, "sess-a"))) == 1
    st = _status()
    assert st.exit_code == pager.EX_NOTHING, (st.exit_code, st.stdout, st.stderr)
    assert "all quiet" in st.stdout


def test_cause3_done_after_a_reminder_stops_the_reminders(rig, monkeypatch):
    """Read: the reminders come. Then done: they stop."""
    mid = "done-late@example.invalid"
    marked_at: list[int] = []

    def then() -> None:
        _mark(rig, mid, "done")
        marked_at.append(rig.seen())

    out, seen = _run(rig, monkeypatch, mid, lambda: _mark(rig, mid, "read"), then=then)
    assert len(out) >= 2 and out[1].startswith(REMINDER_READ), out
    # Not one page after the done (the line is said after its seen mark, so
    # at most the one already in flight when done was written).
    assert seen - marked_at[0] <= 1, (seen, marked_at)
    st = _status()
    assert st.exit_code == pager.EX_NOTHING, (st.exit_code, st.stdout)


def _resident_done(rig, monkeypatch, tmp_path, mid: str, how: str) -> None:
    """The live datum's shape (findings/foreman-live-datum-2030-10-16.md):
    the link sits in ride/new, the mail was given, read and marked `done`
    more than a reminder window ago, and no scoop has run. HOW the `done`
    is written: `write` (the one writer, direct), `bare` (`message mark 1
    done`, the index in the box a bare list numbers) or `box` (`message mark
    sess-a 1 done`, kaizen #36's explicit box argument)."""
    monkeypatch.setenv(_seat.REMIND_VAR, REMIND)
    box = rig.box()
    name = rig.deliver(mid)
    queue = _seat.queue_new(rig.seat, "sess-a")
    os.link(box / "new" / name, queue / name)
    _mark(rig, mid, "seen")
    _mark(rig, mid, "read")
    if how == "write":
        _mark(rig, mid, "done")
    else:
        # `message mark` checks the id exists in notmuch (SM-714's lookup):
        # a fake that knows exactly MID, so resolution is not under test.
        engine = write_fake(tmp_path / "by-id-notmuch", FAKE_NOTMUCH_BY_ID)
        monkeypatch.setenv(_notmuch.BINARY_VAR, str(engine))
        monkeypatch.setenv("FAKE_KNOWN", mid)
        monkeypatch.setenv("FAKE_LOG", str(tmp_path / "argv.log"))
        argv = ["mark", "1", "done"] if how == "bare" else ["mark", "sess-a", "1", "done"]
        r = CliRunner().invoke(message_cli, argv, prog_name="message")
        assert r.exit_code == 0, (r.output, r.stderr)
    done = [m for m in _seat.read_mark_events(rig.seat, "sess-a") if m.mark == "done"]
    assert [(m.message_id, m.session) for m in done] == [(mid, "sess-a")], done
    time.sleep(WINDOW_S + 0.3)               # the marks age past the window
    assert _status().exit_code == pager.EX_NOTHING


def _boot_monitor(rig) -> list[str]:
    def script() -> None:
        assert until(lambda: rig.listening().exists())
        time.sleep(1.0)

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    return lines(result)


@pytest.mark.parametrize("how", ["write", "bare", "box"])
def test_cause3_done_resident_link_does_not_page_at_boot(rig, monkeypatch, tmp_path, how):
    """A mail this session marked `done` never pages it, however the mark
    was written and however long ago. Kaizen #36 (a box-argument mark does
    not quiet) does not reproduce: both CLI forms write the same row."""
    _resident_done(rig, monkeypatch, tmp_path, f"want-{how}@example.invalid", how)
    assert _boot_monitor(rig) == []


def test_cause3_another_sessions_done_does_not_quiet_this_one(rig, monkeypatch):
    """`done` is THIS session's, as `given` is: a sibling's marks never
    silence this listener ("another session's marks do not silence this one")."""
    monkeypatch.setenv(_seat.REMIND_VAR, REMIND)
    box = rig.box()
    mid = "theirs@example.invalid"
    name = rig.deliver(mid)
    os.link(box / "new" / name, _seat.queue_new(rig.seat, "sess-a") / name)
    _seat.write_mark(rig.seat, mid, "done", "sess-b", _now(), "another session")
    out = _boot_monitor(rig)
    assert out and out[0].startswith(NEW_MAIL), out


def test_a_bare_listen_does_not_page_a_done_resident(rig, monkeypatch):
    """The skip is the queue listener's, not only the monitor's: a bare
    `pager listen` on a queue holding only a `done` mail times out."""
    monkeypatch.setenv(_seat.REMIND_VAR, REMIND)
    box = rig.box()
    mid = "bare-done@example.invalid"
    name = rig.deliver(mid)
    os.link(box / "new" / name, _seat.queue_new(rig.seat, "sess-a") / name)
    _mark(rig, mid, "done")
    result = CliRunner().invoke(cli, ["listen", "--timeout", "1"], prog_name="pager")
    assert result.exit_code == pager.EX_TIMEOUT, (result.exit_code, result.stdout, result.stderr)


# ── cause (1): a re-page says it is a reminder and how to quiet it ───────

def test_cause1_read_mail_repages_as_a_reminder(rig, monkeypatch):
    """Read, not done: re-paged after the window, as the ratified "Read is
    not terminal" says - and now the re-page SAYS it is a reminder, names
    `pager status` and `message mark <ID> done`. The first page is today's."""
    mid = "read-1@example.invalid"
    out, _ = _run(rig, monkeypatch, mid, lambda: _mark(rig, mid, "read"))
    assert len(out) >= 2, out
    assert out[0].startswith(NEW_MAIL) and "reminder" not in out[0], out[0]
    assert out[0].endswith("message list --unread"), out[0]      # today's tail
    for line in out[1:]:
        assert line.startswith(REMINDER_READ), line
        _assert_reminder_tail(line)
    # `pager status` does list it (read is not terminal).
    assert _status().exit_code == pager.EX_PAGE


def test_cause1_read_is_quieted_or_told_how(rig, monkeypatch):
    """Decision-neutral (the task README: "an actioned read quiets it, OR
    the page text says 'mark done to quiet'"): after a `read`, either the
    monitor does not page the message again, or every re-page names `done`
    as the way to quiet it."""
    mid = "read-2@example.invalid"
    out, _ = _run(rig, monkeypatch, mid, lambda: _mark(rig, mid, "read"))
    repages = out[1:]
    assert not repages or all("done" in ln for ln in repages), out


def test_a_paged_never_read_mail_is_a_reminder_paged_before(rig, monkeypatch):
    """Paged (seen) but never read: the re-page is a reminder too, and says
    `paged before` rather than claiming the seat read it."""
    out, _ = _run(rig, monkeypatch, "seen-1@example.invalid", lambda: None)
    assert len(out) >= 2, out
    assert out[0].startswith(NEW_MAIL), out[0]
    for line in out[1:]:
        assert line.startswith(REMINDER_SEEN), line
        _assert_reminder_tail(line)


def test_a_bare_listen_reminder_page_carries_the_reminder_line(rig, monkeypatch):
    """The bare page keeps its four items in order; the reminder is one more
    line of item 2, after the count and before the ride-along line."""
    monkeypatch.setenv(_seat.REMIND_VAR, REMIND)
    box = rig.box()
    mid = "bare-read@example.invalid"
    name = rig.deliver(mid)
    os.link(box / "new" / name, _seat.queue_new(rig.seat, "sess-a") / name)
    _mark(rig, mid, "seen")
    _mark(rig, mid, "read")
    time.sleep(WINDOW_S + 0.6)
    result = CliRunner().invoke(cli, ["listen", "--timeout", "3"], prog_name="pager")
    assert result.exit_code == pager.EX_PAGE, (result.exit_code, result.stdout, result.stderr)
    out = result.stdout.splitlines()
    rem = [i for i, ln in enumerate(out) if ln.startswith("pager: reminder - ")]
    assert len(rem) == 1, out
    assert out[rem[0]] == pager.reminder_line(1, "read"), out
    count = out.index("pager: 1 message waiting.")
    assert out[0] == pager.PAGE_OFF and count < rem[0] < len(out) - 2, out
    assert out[-1] == pager.PAGE_COMMANDS, out


# ── units: the line and the marks ────────────────────────────────────────

class _A:
    def __init__(self, n: int) -> None:
        self.name = f"n{n}"
        self.path = Path("/nonexistent")
        self.message_id = f"{n}@x"


def test_monitor_line_without_reminders_is_todays_text():
    entries = [_A(1), _A(2)]
    want = pager.monitor_line(entries, {}, fresh=False)
    assert pager.monitor_line(entries, {}, fresh=False, reminders={}) == want
    assert pager.monitor_line(entries, {}, fresh=False, reminders={"other": True}) == want
    assert want.startswith("pager: 2 messages waiting")
    assert want.endswith(pager.MONITOR_TAIL_STALE)


@pytest.mark.parametrize("fresh", [True, False])
def test_monitor_line_all_reminders(fresh):
    one = pager.monitor_line([_A(1)], {}, fresh=fresh, reminders={"n1": True})
    assert one.startswith(REMINDER_READ), one
    assert one.endswith(pager.MONITOR_TAIL_REMINDER_ONE), one
    two = pager.monitor_line([_A(1), _A(2)], {}, fresh=fresh,
                             reminders={"n1": True, "n2": False})
    assert two.startswith("pager: reminder - 2 messages paged before, not done"), two
    assert two.endswith(pager.MONITOR_TAIL_REMINDER), two


def test_monitor_line_mixed_keeps_todays_head_and_tail_and_adds_the_clause():
    line = pager.monitor_line([_A(1), _A(2), _A(3)], {}, fresh=True, reminders={"n2": True})
    assert line.startswith("pager: 3 messages waiting, 1 of them a reminder (read, not done)"), line
    assert pager.MONITOR_TAIL_FRESH in line
    assert line.endswith(f"Reminders: {pager.MONITOR_TAIL_REMINDER}"), line


def test_monitor_line_with_reminders_is_bounded_and_keeps_its_tail():
    entries = [_A(i) for i in range(40)]
    rem = {a.name: bool(i % 2) for i, a in enumerate(entries) if i % 3}
    line = pager.monitor_line(entries, {}, fresh=False, reminders=rem)
    assert len(line) <= pager.MONITOR_LINE_MAX
    assert line.endswith(f"Reminders: {pager.MONITOR_TAIL_REMINDER}")
    every = pager.monitor_line(entries, {}, fresh=False,
                               reminders={a.name: True for a in entries})
    assert len(every) <= pager.MONITOR_LINE_MAX
    assert every.endswith(pager.MONITOR_TAIL_REMINDER)


def test_quiet_marks_reads_this_session_only():
    def m(mid, mark, session, stamp):
        return _seat.Mark(stamp, mid, mark, "alpha", session, "")

    now = datetime.now().astimezone()
    old = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    new = now.isoformat(timespec="seconds")
    events = [m("a", "seen", "s", old), m("a", "read", "s", old),
              m("b", "seen", "s", new), m("c", "done", "s", old),
              m("d", "done", "other", new), m("e", "read", "other", new)]
    q = pager.quiet_marks(events, "s", now - timedelta(hours=1))
    assert q.given == {"b"}
    assert q.done == {"c"}
    assert q.seen == {"a", "b"}
    assert q.read == {"a"}


@pytest.mark.parametrize("reminders,state", [({"x": True}, "read"),
                                             ({"x": True, "y": False}, "paged before"),
                                             ({}, "paged before")])
def test_reminder_state(reminders, state):
    assert pager.reminder_state(reminders) == state


def test_reminder_line_names_status_and_done():
    assert pager.reminder_line(1, "read") == (
        "pager: reminder - 1 of these read, not done: `pager status` lists it; "
        "`message mark <ID> done` quiets it.")
    assert "lists them" in pager.reminder_line(2, "paged before")
    assert "quiets one" in pager.reminder_line(2, "paged before")
