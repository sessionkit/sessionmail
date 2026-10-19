"""`pager status` - the ride-along. The first verb with a body.

Hermetic by construction: every test builds its own seat under `tmp_path`,
points `SESSIONMAIL_HOME` at it, and replaces the search engine with a fake
`notmuch` that records the argv it was handed. No maildir is read, no index
is queried, and the suite passes on a machine with no notmuch at all.

Fixtures are `alpha` and `beta` at `example.invalid`, never a real seat or
host - `slipbox/project-conventions.md` convention 9.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from sessionmail import _notmuch, _seat, pager
from sessionmail.pager import cli
from fakes import forest, message, write_fake

EX_PAGE, EX_COULD_NOT_LOOK, EX_NOTHING = 0, 2, 3

#: The session every test here runs as. The queue, the stamps and the marks live under `<home>/sessions/<id>/`.
SESSION = "sess-a"


def _under(session: str = SESSION):
    return _seat.session_root(session)


def _marks(session: str = SESSION):
    """The session's marks file. Its directory is made on the way past, so
    a test can plant a history the way the code would have written it -
    the file itself is never created here, so `not _marks().exists()`
    still means "nothing was recorded"."""
    root = _under(session)
    root.mkdir(parents=True, exist_ok=True)
    return root / "marks"


def _stamps(session: str = SESSION):
    return _under(session) / _seat.STAMPS_DIR

@pytest.fixture
def seat(tmp_path, monkeypatch):
    """A seat named `alpha` with a working fake search engine behind it."""
    home = tmp_path / "sessionmail"
    root = home / "seats" / "alpha"
    root.mkdir(parents=True)
    # message-subscriptions 4.2: plain status reads THIS SESSION'S BOX. The
    # mail root is the sandbox's, never the developer's `~/.mail`, and the
    # box exists, as it does once the session's first `pager listen` ran.
    mail = tmp_path / "mail"
    for d in ("tmp", "new", "cur"):
        (mail / SESSION / d).mkdir(parents=True)
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(mail))

    fake = write_fake(tmp_path / "fake-notmuch")

    payload = tmp_path / "answer.json"
    payload.write_text(json.dumps(forest()))

    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    # The queue, the stamps and the marks key on the SESSION id, so the
    # session is part of the sandbox:
    # without this a test would read and write under whatever session the
    # developer's own shell is in. Tests that want another id set it.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(fake))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_JSON", str(payload))
    # Rulings 1320: the scoop and the Stop hook say "arm your pager" when
    # this session has no live listener. These tests are about the scoop
    # itself, so the seat is ARMED: a listening stamp holding a pid that is
    # alive (our own), and the process-ancestry fallback stubbed empty so
    # a real listener on the developer's machine cannot leak in.
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    real_is_listening = _seat.is_listening
    monkeypatch.setattr(_seat, "is_listening", lambda seat, session, ps=None: os.getpid())

    class Fixture:
        def __init__(self) -> None:
            self.root = root
            self.mail = mail
            self.payload = payload
            self.log = tmp_path / "argv.log"

        def disarm(self) -> None:
            """Back to the real check: no stamp, empty ps -> not armed."""
            monkeypatch.setattr(_seat, "is_listening", real_is_listening)

        def deliver(self, mid: str, subject: str, when: int = 0,
                    to: str = "alpha", session: str = SESSION) -> None:
            """One message into SESSION's box, as postfix writes it.
            WHEN orders the files (the box is read oldest first)."""
            box = mail / session
            for d in ("tmp", "new", "cur"):
                (box / d).mkdir(parents=True, exist_ok=True)
            name = f"{1923171200 + when}.M{abs(hash(mid)) % 10**6}P1.test"
            (box / "new" / name).write_text(
                f"X-Original-To: {to}@example.invalid\nFrom: beta@example.invalid\n"
                f"To: {to}@example.invalid\nSubject: {subject}\nMessage-ID: <{mid}>\n"
                f"Date: Tue, 01 Jan 2030 12:00:00 +0000\n\nbody\n")

        def answer(self, *messages: dict) -> None:
            payload.write_text(json.dumps(forest(*messages)))

        def argv(self) -> list[list[str]]:
            if not self.log.exists():
                return []
            return [ln.split("\x1f") for ln in self.log.read_text().splitlines()]

    return Fixture()


def run(*argv: str):
    return CliRunner().invoke(cli, ["status", *argv], prog_name="pager")


# ── identity ─────────────────────────────────────────────────────────────

def test_no_identity_refuses_and_names_the_variables_in_order(monkeypatch):
    """specs/pager, "Identity comes from the environment": with none of them
    set it refuses, naming what it looked for."""
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    result = run()
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert "no identity" in result.stderr
    for var in _seat.IDENTITY_VARS:
        assert var in result.stderr
    order = [result.stderr.index(v) for v in _seat.IDENTITY_VARS]
    assert order == sorted(order), "the variables are listed out of order"


def test_the_seat_variable_outranks_the_session_id(monkeypatch):
    env = {"CLAUDE_CODE_SESSION_ID": "7ed6b16b-31bd", "TM_AGENT_ADDRESS": "alpha"}
    resolved = _seat.resolve(env)
    assert (resolved.name, resolved.source) == ("alpha", "TM_AGENT_ADDRESS")


def test_a_seats_directory_name_drops_the_domain_and_the_extension():
    """One seat, several addresses: specs/pager, "The banner tells the truth"
    treats `alpha` and `alpha+role` as one listener's set."""
    assert _seat.seat_name("Alpha+role@host.example.invalid") == "alpha"
    assert _seat.seat_name("foreman.TM-123") == "foreman.tm-123"
    assert "/" not in _seat.seat_name("../../etc/passwd")


# ── the resident view reads this session's box (message-subscriptions 4.2)
#
# It used to run each `<seat>/subscriptions/` query through notmuch. The
# files are retired and the pager reads none; what they named lands in the
# session's box, so that is what plain `pager status` reads - from the files,
# with no index. The retirement itself is gated in
# test_pager_reads_no_subscriptions.py; these are the view's own promises,
# carried over from the query form.

def test_no_box_yet_is_deaf_not_quiet(seat):
    """Charter 2: a null that agrees with you is the one to audit. A missing
    box is "could not look", never "nothing would page you"."""
    import shutil
    shutil.rmtree(seat.mail / SESSION)
    result = run()
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert str(seat.mail / SESSION) in result.stderr and "pager listen" in result.stderr


def test_an_empty_box_is_nothing(seat):
    result = run()
    assert result.exit_code == EX_NOTHING
    assert "nothing" in result.output


def test_one_line_each_oldest_first(seat):
    seat.deliver("late@example.invalid", "the second", when=200)
    seat.deliver("early@example.invalid", "the first", when=100)
    result = run()
    assert result.exit_code == EX_PAGE
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert "the first" in lines[0] and "the second" in lines[1]
    assert lines[0].startswith("[alpha] ")
    assert "beta" in lines[0]


def test_the_instrument_can_find_something_it_is_told_about(seat):
    """Charter 2: prove the yes before trusting any of its nos."""
    seat.deliver("a@example.invalid", "hello")
    assert run().exit_code == EX_PAGE
    for p in (seat.mail / SESSION / "new").iterdir():
        p.unlink()
    assert run().exit_code == EX_NOTHING


def test_the_default_shows_three_and_counts_the_rest(seat):
    for i in range(5):
        seat.deliver(f"m{i}@example.invalid", f"subject {i}", when=i)
    lines = run().stdout.splitlines()
    assert len(lines) == 4
    assert lines[-1].startswith("[mail] +2 more")


def test_all_shows_every_one_and_counts_nothing(seat):
    for i in range(5):
        seat.deliver(f"m{i}@example.invalid", f"subject {i}", when=i)
    lines = run("--all").stdout.splitlines()
    assert len(lines) == 5
    assert not any("more" in ln for ln in lines)


def test_a_line_is_one_line_however_long_the_subject(seat):
    seat.deliver("a@example.invalid", "x" * 400)
    lines = run().stdout.splitlines()
    assert len(lines) == 1
    assert len(lines[0]) <= 96


def test_mail_in_cur_is_still_shown(seat):
    """Read is not terminal, and neither is a file moved to cur/ by a mail
    client: only a `done` mark quiets a line."""
    seat.deliver("a@example.invalid", "moved along")
    for p in (seat.mail / SESSION / "new").iterdir():
        p.rename(seat.mail / SESSION / "cur" / (p.name + ":2,S"))
    assert "moved along" in run().output


# ── marks: done is terminal, read is not ─────────────────────────────────

def test_done_quiets_a_line(seat):
    seat.deliver("a@example.invalid", "handled")
    _marks().write_text(
        "a@example.invalid done 7ed6b16b 2030-06-19T22:34:56+10:00\n"
    )
    assert run().exit_code == EX_NOTHING


def test_read_is_not_terminal(seat):
    """specs/pager, "Read is not terminal": a message read and not marked done is presented again."""
    seat.deliver("a@example.invalid", "unfinished")
    _marks().write_text(
        "a@example.invalid seen 7ed6b16b 2030-06-19T22:34:56+10:00\n"
        "a@example.invalid read 7ed6b16b 2030-06-19T22:34:56+10:00\n"
    )
    result = run()
    assert result.exit_code == EX_PAGE
    assert "unfinished" in result.output


def test_a_ruled_six_field_done_line_quiets_a_line(seat):
    """_seat.MARK_LINE (1040): the same `done`, in the ruled order, with a
    reason on the end. The four-field cases above are the pre-ruling
    lines still on disk; both must quiet."""
    seat.deliver("a@example.invalid", "handled")
    _marks().write_text(
        "2030-06-20T22:34:56+10:00 a@example.invalid done alpha 7ed6b16b created TM-99\n"
    )
    assert run().exit_code == EX_NOTHING


def test_marks_are_matched_with_or_without_angle_brackets(seat):
    seat.deliver("a@example.invalid", "handled")
    _marks().write_text(
        "<a@example.invalid> done 7ed6b16b 2030-06-19T22:34:56+10:00\n"
    )
    assert run().exit_code == EX_NOTHING


def test_another_sessions_done_does_not_quiet_this_one(seat):
    """Marks are the SESSION's."""
    seat.deliver("a@example.invalid", "still mine")
    _marks("sess-z").write_text(
        "2030-06-20T22:34:56+10:00 a@example.invalid done alpha sess-z fixed\n")
    assert "still mine" in run().output


# ── it looks, and looks only ─────────────────────────────────────────────

def test_quiet_is_exit_code_only(seat):
    seat.deliver("a@example.invalid", "loud")
    result = run("--quiet")
    assert result.exit_code == EX_PAGE
    assert result.output == ""


def test_quiet_never_hides_a_could_not_look(seat):
    """`help/pager/status.txt`: 2 is "never reported as quiet"."""
    import shutil
    shutil.rmtree(seat.mail / SESSION)
    result = run("--quiet")
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert "could not look" in result.stderr


def test_it_writes_nothing_at_all(seat):
    """specs/pager, "State never mails": state is looked at, never stored.
    A ride-along that creates a file changes what it is measuring."""
    seat.deliver("a@example.invalid", "hello")

    def snapshot() -> set:
        out = set()
        for top in (seat.root.parent.parent, seat.mail):
            for base, dirs, files in os.walk(top):
                for name in list(dirs) + list(files):
                    path = os.path.join(base, name)
                    out.add((path, os.stat(path).st_mtime_ns, os.path.getsize(path)))
        return out

    before = snapshot()
    assert run().exit_code == EX_PAGE
    assert snapshot() == before
    assert seat.argv() == []                       # and it asked no index


# ── the scoop: `--new` gives each alert once (RULINGS 1110) ───────────────


def _ago(days: int) -> str:
    """A marks stamp DAYS before now, on the REAL clock, at +10:00: pager status
    ages a seen/read stamp against now, so an "old" stamp is written relative to
    it (SM-869 method rule 4), never as a fixed date that a later year outruns."""
    return (datetime.now(timezone(timedelta(hours=10))) - timedelta(days=days)).isoformat(timespec="seconds")


def _six_field(mid: str, mark: str, session: str, stamp: str, reason: str = "") -> str:
    line = f"{stamp} {mid} {mark} alpha {session}"
    return line + (f" {reason}\n" if reason else "\n")










def test_stopped_stamps_nothing_and_marks_nothing(seat, monkeypatch):
    """The Stop hook's form: the turn ended. Its stdout reaches nobody
    unless the hook blocks with it, so it marks nothing, stamps nothing
    (stopped-means-idle: a turn boundary is not idle), and prints nothing
    when the pager is armed (exit 3)."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    seat.answer(message("m1@x", 1, "judy", "hi"))
    r = run("--stopped")
    assert r.exit_code == EX_NOTHING and r.output == ""
    assert not (_stamps() / "stopped").exists()
    assert not _marks().exists()


# --- Rulings 1320: "arm your pager" from the Stop hook and as a ride-along ---

def test_the_scoop_says_not_armed_when_no_listener_is_alive(seat, monkeypatch):
    """#4: the ride-along carries it, so it does not wait for a stop."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    seat.disarm()
    r = run("--new")
    assert r.exit_code == EX_PAGE
    assert r.output.startswith("pager is not listening") and "pager listen" in r.output


def test_the_stop_hook_says_not_armed_and_only_that(seat, monkeypatch):
    """#2, the first priority: one line, what to do next, nothing else.

    The expected line is READ from the page text, not written out again:
    specs/pager, "The arm instruction has one source" - a test that spelled
    the arm sentence a second time would be the copy 3.5 exists to remove
    (this file was the fifth site, measured Mon 2030-09-16). What the
    line must SAY is pinned by `arm.txt` and tests/test_arm_instruction.py;
    what this test pins is that the Stop hook prints it and nothing else.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    seat.disarm()
    r = run("--stopped")
    assert r.exit_code == EX_PAGE
    # The words are hook_lines.toml's (one place, both hooks) - this test pins ONE line and what to do next, not the wording.
    assert r.output.splitlines() == [pager.LINE_NOT_LISTENING]
    assert "pager listen" in pager.LINE_NOT_LISTENING
    assert not (_stamps() / "stopped").exists()


def test_opt_out_silences_both_nudges(seat, monkeypatch):
    """#5: opt-out by configuration, default ON. One key."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    seat.disarm()
    assert run("--new").exit_code == EX_NOTHING
    assert run("--stopped").exit_code == EX_NOTHING


def test_a_stale_listening_stamp_is_a_dead_listener_and_is_cleared(seat, monkeypatch):
    """A SIGKILLed listener leaves its stamp; a dead pid is not armed."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    seat.disarm()
    stamp = _stamps() / "listening"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("999999999\n")
    r = run("--stopped")
    assert r.exit_code == EX_PAGE and r.output.startswith("pager is not listening")
    assert not stamp.exists()


def test_a_listener_born_before_the_stamp_is_found_by_ancestry():
    """A `pager listen` started by older code has no stamp; it is found
    by walking its parent chain to this session's claude. Without this,
    every live listener on the box would be called deaf the moment the
    stamp landed (a false positive)."""
    ppid = {10: 1, 20: 10, 30: 20, 40: 1, 50: 40}
    cmd = {10: "claude --session-id sess-a", 20: "bash", 30: "/x/bin/python /x/bin/pager listen",
           40: "claude --session-id sess-b", 50: "/x/bin/pager listen --timeout 5"}
    assert _seat.listening_by_ancestry("sess-a", ppid, cmd, me=99) == 30
    assert _seat.listening_by_ancestry("sess-b", ppid, cmd, me=99) == 50
    assert _seat.listening_by_ancestry("sess-c", ppid, cmd, me=99) is None
    # No --session-id on argv: the claude is found by walking up from ME.
    cmd2 = {10: "claude", 20: "bash", 30: "/x/bin/pager listen", 60: "sh hook"}
    ppid2 = {10: 1, 20: 10, 30: 20, 60: 10}
    assert _seat.listening_by_ancestry("", ppid2, cmd2, me=60) == 30


# --- SM-558: the walk from ME stops at the nearest claude, and a claude
# launched with ANOTHER --session-id is never this session's. A test child
# (session X) under a live seat (claude --session-id Y) took the seat's own
# listener as X's and was refused "already listening" on it. ---------------

#: ME (99) runs under a shell under the claude (10); the claude's own
#: `pager listen` (30) hangs off the same claude.
_TREE = {10: 1, 20: 10, 30: 20, 40: 10, 99: 40}


def _tree_under(claude: str) -> dict[int, str]:
    return {10: claude, 20: "bash", 30: "/x/bin/python /x/bin/pager listen",
            40: "/x/bin/python -m pytest", 99: "/x/bin/pager listen --quiet"}


def test_a_session_under_another_sessions_claude_finds_no_listener():
    """The leak itself: asked for session X from under
    `claude --session-id Y`, the walk answers None, never Y's listener.
    Red before SM-558 (it answered 30)."""
    cmd = _tree_under("claude --session-id sess-y")
    assert _seat.listening_by_ancestry("sess-x", _TREE, cmd, me=99) is None
    cmd = _tree_under("/opt/bin/claude --model opus --session-id=sess-y")
    assert _seat.listening_by_ancestry("sess-x", _TREE, cmd, me=99) is None


def test_the_walk_never_climbs_past_a_foreign_claude_to_an_id_less_one():
    """The nearest claude decides. A foreign-id claude between ME and an
    interactive `claude` above it ends the walk: no other claude is tried."""
    ppid = {5: 1, 6: 5, 10: 5, 20: 10, 30: 20, 40: 10, 99: 40}
    cmd = {5: "claude", 6: "/x/bin/pager listen", **_tree_under("claude --session-id sess-y")}
    assert _seat.listening_by_ancestry("sess-x", ppid, cmd, me=99) is None


def test_a_session_under_its_own_or_an_id_less_claude_is_still_found():
    """What SM-558 must never break: a claude with no --session-id (a person's
    interactive `claude`, a resume, a continue) still counts, and so does
    one carrying this session's own id, in either argv form."""
    for claude in ("claude", "claude --resume sess-a", "claude -r sess-a",
                   "claude --continue", "claude -c",
                   "claude --resume sess-a --fork-session",
                   "claude --session-id sess-x",
                   "claude --model opus --session-id=sess-x",
                   "claude --resume sess-a --fork-session --session-id sess-x"):
        assert _seat.listening_by_ancestry("sess-x", _TREE, _tree_under(claude), me=99) == 30, claude


def test_the_id_on_a_claude_is_compared_whole_never_as_a_substring():
    """`--session-id sess-xy` is not session sess-x's claude, nor is
    `--session-id sess-x` session sess-xy's: from under either, the other
    session finds nothing."""
    cmd = _tree_under("claude --session-id=sess-xy")
    assert _seat.listening_by_ancestry("sess-x", _TREE, cmd, me=99) is None
    cmd = _tree_under("claude --session-id sess-x")
    assert _seat.listening_by_ancestry("sess-xy", _TREE, cmd, me=99) is None


def test_no_session_under_an_id_carrying_claude_finds_no_listener():
    """The one named consequence (design/rca-001 §1): with no session id
    (`""`, or `-`), a claude that carries an id is not this session's
    either. Under an id-less claude it still counts (the case above)."""
    cmd = _tree_under("claude --session-id sess-y")
    assert _seat.listening_by_ancestry("", _TREE, cmd, me=99) is None
    assert _seat.listening_by_ancestry("-", _TREE, cmd, me=99) is None
    assert _seat.listening_by_ancestry("", _TREE, _tree_under("claude"), me=99) == 30


def test_other_listener_under_another_sessions_claude_refuses_nothing(seat, monkeypatch):
    """The guard at `pager listen` start, end to end through
    `other_listener`: a listener for session X starting under
    `claude --session-id Y`, with Y's own listener live beside it, finds no
    other listener. Red before SM-558 (it found Y's)."""
    import os as _os
    me, parent = _os.getpid(), _os.getppid()
    ppid = {10: 1, 20: 10, 30: 20, parent: 10, me: parent}
    cmd = {10: "claude --session-id sess-y", 20: "bash",
           30: "/x/bin/python /x/bin/pager listen", parent: "/x/bin/python -m pytest",
           me: "/x/bin/python /x/bin/pager listen --quiet"}
    s = _seat.resolve()
    assert _seat.other_listener(s, "sess-x", ps=lambda: (ppid, cmd)) is None
    assert _seat.other_listener(s, "sess-y", ps=lambda: (ppid, cmd)) == 30








# --- Rulings 1330 + changes/stopped-means-idle: T is 0 when IDLE, the running
# setting otherwise. Idle is Claude Code's own status through `session get
# idle`, not a stamp (RULED). -----------------------------------------------

def test_page_age_is_zero_when_idle_and_sixty_otherwise(seat, monkeypatch):
    from sessionmail import pager
    monkeypatch.delenv(pager.PAGE_AGE_VAR, raising=False)
    monkeypatch.delenv(_seat.IDLE_SECONDS_VAR, raising=False)
    s = _seat.resolve()
    verdict = {"idle": None}
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: verdict["idle"])
    assert pager.page_age(s, "sess-a") == 60.0          # unknown: running, never at once
    verdict["idle"] = 75.0
    assert pager.page_age(s, "sess-a") == 0.0           # not busy 75 s >= 60: at once
    verdict["idle"] = 0.0
    assert pager.page_age(s, "sess-a") == 60.0          # busy (a tool in flight)
    verdict["idle"] = 0.5
    assert pager.page_age(s, "sess-a") == 60.0          # half a second is not idle yet (threshold 1 s; was 5 s under 60)
    _seat.touch(s, _seat.STOPPED_STAMP, "sess-a")       # an old stamp changes nothing
    assert pager.page_age(s, "sess-a") == 60.0
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "5")
    assert pager.page_age(s, "sess-a") == 5.0           # one setting, not a constant
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, "5")
    verdict["idle"] = 5.0                                # threshold raised to 5: 5 s is idle again
    assert pager.page_age(s, "sess-a") == 0.0           # and the idle number is ours too


# ── the scoop takes the queue (the-queue-chain, G2) ───────────────────────
#: openspec/changes/the-queue-chain: the scoop takes <session>/ride/new
#: by directory swap, gives each entry once from the file's own headers,
#: records the giving, files the link in done/. No query, no baseline, no
#: remind-window arithmetic. Entries are written straight into the queue
#: here - the LINK is the detector's half, gated in test_detect.py.

_serial = 0


def _queued(seat, session: str, mid: str, sender: str, subject: str,
            name: str | None = None, mtime: float | None = None):
    global _serial
    _serial += 1
    q = _seat.ride_dir(None, session) / _seat.QUEUE_NEW
    q.mkdir(parents=True, exist_ok=True)
    p = q / (name or f"{1923171200 + _serial}.M{_serial}P1.host")
    p.write_text(
        f"From: {sender}\nTo: alpha@example.invalid\nSubject: {subject}\n"
        f"Message-ID: <{mid}>\nDate: Tue, 01 Jan 2030 12:00:00 +0000\n\nbody\n"
    )
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _ride(seat, session: str = "sess-a"):
    return _seat.ride_dir(None, session)


def test_new_gives_a_queued_entry_once_and_records_the_giving_as_seen(seat, monkeypatch):
    """The ride-along knows what it has given, so it never gives it twice. Now
    by construction: the link moves to done/, so there is nothing to give twice."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    link = _queued(seat, "sess-a", "a@example.invalid", "beta@example.invalid", "once only")
    first = run("--new")
    assert first.exit_code == EX_PAGE and "once only" in first.output, first.output
    assert first.output.startswith("[mail] ") and "beta" in first.output
    marks = _marks().read_text().splitlines()
    # pager-genie 3.4 (D5): the body ("body") was given whole, which earns
    # `read` too, but that half is HELD until SM-446 is deployed
    # (pager.READ_ON_WHOLE), so the giving is `seen` only
    assert len(marks) == 1
    stamp, mid, mark, who, session, *reason = marks[0].split()
    assert (mid, mark, who, session, " ".join(reason)) == (
        "a@example.invalid", "seen", "alpha", "sess-a", "ride-along")
    assert not link.exists()
    assert (_ride(seat) / "done" / link.name).exists()
    assert list((_ride(seat) / "new").iterdir()) == []
    assert not [p for p in _ride(seat).iterdir() if p.name.startswith("taking.")]
    second = run("--new")
    assert second.exit_code == EX_NOTHING and second.output == "", second.output


def test_the_scoop_runs_no_query_and_keeps_no_baseline(seat, monkeypatch):
    """The scoop reads the queue and the marks and needs nothing else: the
    engine is never called, and no index revision is written anywhere."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv("FAKE_REVISION", "41")
    seat.answer(message("r@example.invalid", 1, "beta@example.invalid", "in the index only"))
    result = run("--new")
    assert result.exit_code == EX_NOTHING and result.output == "", result.output
    assert seat.argv() == []
    assert "residents" not in result.output
    assert not _marks().exists()
    assert not (_stamps() / "scoop-rev").exists()
    assert (_ride(seat) / "new").is_dir()


def test_the_scoop_needs_no_index(seat, monkeypatch):
    """A queue that is right there on disk cannot be silenced by an index
    that will not open."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv("FAKE_FAIL", "1")
    _queued(seat, "sess-a", "d@example.invalid", "beta@example.invalid", "on disk")
    result = run("--new")
    assert result.exit_code == EX_PAGE and "on disk" in result.output
    assert "could not look" not in result.output


def test_a_leftover_taking_directory_is_recovered_first_and_named(seat, monkeypatch):
    """D09: a leftover `taking.<session>.<pid>` IS the recovery record - the
    next scoop gives its entries, says where they came from, and the
    directory is gone afterwards. Shown twice at worst, never dropped."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    left = _ride(seat) / "taking.sess-a.999999"
    left.mkdir(parents=True)
    (left / "1.M1P1.host").write_text(
        "From: beta@example.invalid\nSubject: left behind\nMessage-ID: <l@example.invalid>\n\n")
    _queued(seat, "sess-a", "n@example.invalid", "beta@example.invalid", "fresh")
    result = run("--new")
    lines = result.output.splitlines()
    assert result.exit_code == EX_PAGE
    assert lines[0] == "[mail] recovered 1 from taking.sess-a.999999 - a scoop that did not finish"
    assert "left behind" in lines[1] and "(recovered from taking.sess-a.999999)" in lines[1]
    assert "fresh" in lines[2]
    assert not left.exists()
    assert (_ride(seat) / "done" / "1.M1P1.host").exists()
    marks = _marks().read_text()
    assert "l@example.invalid seen alpha sess-a ride-along recovered from taking.sess-a.999999" in marks
    assert run("--new").exit_code == EX_NOTHING


def test_a_taking_directory_of_a_live_scoop_is_left_alone(seat, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    live = _ride(seat) / f"taking.sess-a.{os.getppid()}"
    live.mkdir(parents=True)
    (live / "1.M1P1.host").write_text("Subject: mid-swap\nMessage-ID: <m@example.invalid>\n\n")
    result = run("--new")
    assert result.exit_code == EX_NOTHING and "mid-swap" not in result.output
    assert (live / "1.M1P1.host").exists()


def test_a_claim_elsewhere_cannot_re_present_a_given_item(seat, monkeypatch):
    """F18 (SM-70, measured Sat 2030-09-14): a claim of another copy
    bumps lastmod and the remind window expires - and the item still does
    not ride again, because the scoop reads neither."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    _queued(seat, "sess-a", "f18@example.invalid", "beta@example.invalid", "given already")
    assert "given already" in run("--new").output
    monkeypatch.setenv("FAKE_REVISION", "999")
    seat.answer(message("f18@example.invalid", 1, "beta@example.invalid", "given already"))
    _marks().write_text(
        _six_field("f18@example.invalid", "seen", "sess-a", _ago(1100), "ride-along"))
    monkeypatch.setenv(_seat.REMIND_VAR, "99999")
    again = run("--new")
    assert "given already" not in again.output
    # what MAY ride is the depth gauge (Q05), never the item
    assert all(ln.startswith("[mail] ") and "not done" in ln for ln in again.output.splitlines())
    assert "f18@example.invalid seen alpha sess-a ride-along\n" not in _marks().read_text()[len(_six_field("f18@example.invalid", "seen", "sess-a", _ago(1100), "ride-along")):]


def test_not_done_is_a_gauge_line_once_per_interval_never_the_item(seat, monkeypatch):
    """"and to the same session after an interval" as ONE depth line
    per interval (Q05 on the-queue-chain): count and oldest age, no mark,
    no headline. The interval is inferred (60 min), overridable."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv(_seat.REMIND_VAR, "1")
    _marks().write_text(
        _six_field("old@example.invalid", "seen", "sess-a", _ago(1100), "ride-along")
        + _six_field("old2@example.invalid", "seen", "sess-a", _ago(734), "ride-along")
        + _six_field("closed@example.invalid", "seen", "sess-a", _ago(1100), "ride-along")
        + _six_field("closed@example.invalid", "done", "sess-z", _ago(369), "fixed"))
    first = run("--new")
    assert first.exit_code == EX_PAGE, first.output
    assert first.output.startswith("[mail] 2 not done, oldest ") and "d" in first.output
    assert "`message mark ID done" in first.output          # the line names its door
    assert "old@example.invalid" not in first.output
    assert (_stamps() / _seat.REMINDED_STAMP).exists()
    assert len(_marks().read_text().splitlines()) == 4
    second = run("--new")
    assert second.exit_code == EX_NOTHING and second.output == ""


def test_done_files_a_queued_entry_without_showing_it(seat, monkeypatch):
    """Done is terminal for the seat: a link whose message any session
    marked done is filed in done/ and never shown."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    link = _queued(seat, "sess-a", "h@example.invalid", "beta@example.invalid", "handled")
    _marks().write_text(
        _six_field("h@example.invalid", "done", "sess-z", _ago(1100), "closed"))
    result = run("--new")
    assert result.exit_code == EX_NOTHING and result.output == ""
    assert not link.exists() and (_ride(seat) / "done" / link.name).exists()
    assert len(_marks().read_text().splitlines()) == 1


def test_a_message_the_pager_woke_this_session_with_is_given_once_by_the_scoop(seat, monkeypatch):
    """"The re-arm is the delivery": the page was a nudge with a
    headline; the scoop is the giving. Once, then done/."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    _queued(seat, "sess-a", "w@example.invalid", "beta@example.invalid", "woke you")
    _marks().write_text(
        _six_field("w@example.invalid", "seen", "sess-a", "2999-01-01T00:00:00+10:00", "pager wake"))
    assert "woke you" in run("--new").output
    assert run("--new").exit_code == EX_NOTHING


def test_another_sessions_queue_is_not_this_ones(seat, monkeypatch):
    """One queue per consumer (D08 point 4): sessions share a seat and
    never scoop each other."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    theirs = _queued(seat, "sess-b", "b@example.invalid", "beta@example.invalid", "for b")
    result = run("--new")
    assert result.exit_code == EX_NOTHING and "for b" not in result.output
    assert theirs.exists()


def test_plain_status_still_presents_what_new_has_given(seat, monkeypatch):
    """specs/pager "Read is not terminal" is untouched: the resident view
    shows a seen-not-done message; only the scoop is once. The box holds the
    message, the queue holds its link (the listener's half)."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    seat.deliver("a@example.invalid", "still live")
    _queued(seat, "sess-a", "a@example.invalid", "beta@example.invalid", "still live")
    assert run("--new").exit_code == EX_PAGE
    assert "still live" not in run("--new").output          # the scoop is once
    plain = run()
    assert plain.exit_code == EX_PAGE and "still live" in plain.output


def test_new_touches_this_sessions_ride_along_stamp_even_when_nothing_is_new(seat, monkeypatch):
    """RULINGS 1100: a ride-along ran, so a tool call ran - this session is
    busy whether or not anything was given. Plain status touches nothing.
    Stamps are per session: sessions share a seat."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    assert run().exit_code == EX_NOTHING
    assert not (_stamps() / _seat.RIDE_ALONG_STAMP).exists()
    assert run("--new").exit_code == EX_NOTHING
    assert (_stamps() / _seat.RIDE_ALONG_STAMP).exists()
    assert not (seat.root / _seat.STAMPS_DIR).exists()   # nothing keys on the seat


def test_nothing_is_recorded_when_nothing_was_given(seat, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    assert run("--new").exit_code == EX_NOTHING
    assert not _marks().exists()


def test_the_scoop_is_silent_when_there_is_nothing(seat, monkeypatch):
    """A ride-along line prints nothing when quiet - the carrier repeats
    whatever it prints on every tool call (measured Thu 2030-09-12).
    Plain status keeps its sentence."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    result = run("--new")
    assert result.exit_code == EX_NOTHING
    assert result.output == ""
    assert "nothing (box sess-a, all quiet)" in run().output


def test_oldest_first_by_delivery_time_not_by_name(seat, monkeypatch):
    """Age is the inode's mtime - a hardlink carries delivery time."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    _queued(seat, "sess-a", "z@example.invalid", "beta@example.invalid", "the newer", name="9.M9P9.host", mtime=2_000_000_000)
    _queued(seat, "sess-a", "y@example.invalid", "beta@example.invalid", "the older", name="1.M1P1.host", mtime=1_000_000_000)
    # D4 (pager-genie 3.2a): each header line is followed by its text view,
    # so the order is read off the header lines alone
    lines = [ln for ln in run("--new").output.splitlines() if ln.startswith("[mail] ")]
    assert "the older" in lines[0] and "the newer" in lines[1]


# ── the burst coalesces, per address ─────────────────────────────────────
#
# specs/pager: "Counts SHALL coalesce per address across a burst, not be
# emitted per message." The measured case is a Delta Chat CALL: the bridge
# mails the seat EVERY CHUNK - 14 in five minutes on one box, one of them
# "[COUGH]" - so restoring that mail without grouping turns a call into a
# wake-per-cough storm. Grouping is what makes the restore safe, and this is
# the capability do-018's storm test is run against.

def _queued_to(seat, session: str, mid: str, subject: str, to: str,
               sender: str = "bridge@example.invalid"):
    """A queued entry that carries an X-Original-To, which `_queued` above
    deliberately does not - the burst is defined by the ADDRESS."""
    global _serial
    _serial += 1
    q = _seat.ride_dir(None, session) / _seat.QUEUE_NEW
    q.mkdir(parents=True, exist_ok=True)
    p = q / f"{1923171200 + _serial}.M{_serial}P1.host"
    p.write_text(
        f"X-Original-To: {to}\nFrom: {sender}\nTo: alpha@example.invalid\n"
        f"Subject: {subject}\nMessage-ID: <{mid}>\n"
        f"Date: Tue, 01 Jan 2030 12:00:00 +0000\n\nbody\n"
    )
    return p


def test_a_burst_from_one_address_becomes_one_line_not_one_each(seat, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    for i in range(14):
        _queued_to(seat, "sess-a", f"c{i}@example.invalid", f"chunk {i}", "ivan")
    out = run("--new")
    assert out.exit_code == EX_PAGE
    lines = [l for l in out.output.splitlines() if l.strip()]
    assert len(lines) == 1, lines
    assert "14 for ivan" in lines[0], lines[0]
    assert "message list" in lines[0]          # a count with a door, not a dead end
    assert "chunk" not in out.output           # no subject survived the coalescing


def test_the_whole_burst_is_still_given_even_though_it_printed_once(seat, monkeypatch):
    """Coalescing is a way of SAYING, never a way of skipping: all 14 are
    marked seen and filed, so none is silently dropped and none is given
    twice. This is the property that makes one line safe."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    for i in range(14):
        _queued_to(seat, "sess-a", f"g{i}@example.invalid", f"chunk {i}", "ivan")
    assert run("--new").exit_code == EX_PAGE
    marks = _marks().read_text()
    for i in range(14):
        assert f"g{i}@example.invalid" in marks, i
    assert run("--new").exit_code == EX_NOTHING          # given once, never twice


def test_two_addresses_burst_independently(seat, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    for i in range(5):
        _queued_to(seat, "sess-a", f"x{i}@example.invalid", f"call {i}", "ivan")
    for i in range(6):
        _queued_to(seat, "sess-a", f"y{i}@example.invalid", f"chat {i}", "ivan+kaizen")
    lines = [l for l in run("--new").output.splitlines() if l.strip()]
    assert len(lines) == 2, lines
    assert "5 for ivan" in lines[0] and "6 for ivan+kaizen" in lines[1], lines


def test_under_the_threshold_nothing_changes_and_the_subject_still_prints(seat, monkeypatch):
    """Ordinary traffic keeps its sender and subject. The threshold is not a
    sender or subject FILTER - only how many arrived for one address - so a
    filter cannot be what fixes the storm."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    for i in range(3):
        _queued_to(seat, "sess-a", f"s{i}@example.invalid", f"ordinary {i}", "ivan")
    out = run("--new").output
    for i in range(3):
        assert f"ordinary {i}" in out, out
    assert "for ivan" not in out


def test_the_threshold_is_what_decides_it_not_the_sender(seat, monkeypatch):
    """Same sender, same subjects, one below the threshold and one above:
    only the COUNT changes the outcome."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "3")
    for i in range(3):
        _queued_to(seat, "sess-a", f"t{i}@example.invalid", f"same {i}", "ivan")
    assert "3 for ivan" in run("--new").output


# --- SM-706: a check made before claude starts is not deafness -------------
#
# A `session start` context generator (a wake item running `pager status`)
# runs BEFORE claude is exec'd, so no listener - the plugin's monitor least
# of all - can exist yet. It used to read "pager is not listening - re-arm"
# and the seat obeyed with a hand-armed listen that fought the monitor
# (rca-001, findings/2030-10-13-rca-001.md). Now: the not-yet line, which
# still says how to arm once claude is up (it is true when no monitor will
# run) and never says "nothing to arm" except about a refused arm.
# `pre_claude` (conftest) makes `_seat.claude_for` answer None and records
# each call; every other suite runs with claude pinned up.

def _not_yet_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln == pager.LINE_NOT_YET]


def test_pre_claude_stopped_says_not_yet_and_exits_0(seat, pre_claude, monkeypatch):
    """Test 7, with amendment 1: --stopped,
    no listener, claude not up: exactly the not-yet line, exit 0 -
    informational, because sessiontools frames any non-zero generator exit
    as CONTEXT GENERATOR FAILED - and no NOT ARMED."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    r = run("--stopped")
    assert pager.EX_NOT_YET == 0
    assert r.exit_code == pager.EX_NOT_YET, r.output
    assert r.output.splitlines() == [pager.LINE_NOT_YET]
    assert pager.LINE_NOT_LISTENING not in r.output
    assert pre_claude == [SESSION]


def test_pre_claude_new_leads_with_not_yet_and_exits_0_mail_or_none(seat, pre_claude, monkeypatch):
    """Test 8, with amendment 1: --new, empty queue: the not-yet line alone,
    exit 0 (never 3: a wake item's non-zero reads as FAILED). With a queued
    mail: the not-yet line, then the mail line, exit 0."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    r = run("--new")
    assert r.exit_code == pager.EX_NOT_YET == 0, r.output
    assert r.output.splitlines() == [pager.LINE_NOT_YET]
    assert pager.LINE_NOT_LISTENING not in r.output
    _queued(seat, SESSION, "w@example.invalid", "beta@example.invalid", "a wake mail")
    r = run("--new")
    assert r.exit_code == EX_PAGE, r.output
    lines = r.output.splitlines()
    assert lines[0] == pager.LINE_NOT_YET
    assert lines[1].startswith("[mail] ") and "a wake mail" in lines[1]
    assert pager.LINE_NOT_LISTENING not in r.output


def test_pre_claude_plain_status_with_no_box_keeps_exit_2_with_the_not_yet_reason(seat, pre_claude):
    """Test 9, as ruled on rca-001: the
    ratified bare-seat scenario stays true - plain `pager status` with no
    box REFUSES (exit 2) and names `pager listen` - but its reason is the
    not-yet line, never the old "births it" deafness."""
    import shutil
    shutil.rmtree(seat.mail / SESSION)
    r = run()
    assert r.exit_code == EX_COULD_NOT_LOOK
    assert r.stdout == ""
    assert pager.LINE_NOT_YET in r.stderr
    assert "pager listen" in r.stderr
    assert "births it" not in r.stderr and "births it" not in r.stdout
    assert pre_claude == [SESSION]


def test_the_not_yet_line_still_says_how_to_arm_and_claims_no_monitor():
    """Test 10, the fence: it carries arm.txt's instruction verbatim (so a
    seat with NO monitor is still told to arm, in the background), and
    "nothing to arm" appears only inside the refused-arm clause."""
    from sessionmail import _arm
    import tomllib
    from importlib import resources
    raw = tomllib.loads(resources.files("sessionmail").joinpath("hook_lines.toml")
                        .read_text(encoding="utf-8"))["not_yet"]
    assert pager.LINE_NOT_YET == raw.format(arm=_arm.instruction())
    line = pager.LINE_NOT_YET
    assert _arm.instruction() in line
    assert "pager listen" in line and "run_in_background" in line
    assert line.count("nothing to arm") == 1
    refused = line.index("that arm is refused")
    assert refused < line.index("nothing to arm")
    assert "not deaf" in line and "never `pager stop` it" in line


def test_in_claude_with_no_listener_is_still_not_armed(seat, monkeypatch):
    """Test 11: claude IS up (conftest's pin) and nothing listens: today's
    behaviour, untouched - NOT ARMED and exit 0 from --new/--stopped, and a
    missing box is still the "births it" exit 2."""
    import shutil
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    r = run("--stopped")
    assert r.exit_code == EX_PAGE and r.output.splitlines() == [pager.LINE_NOT_LISTENING]
    r = run("--new")
    assert r.exit_code == EX_PAGE and r.output.startswith("pager is not listening")
    assert pager.LINE_NOT_YET not in r.output
    shutil.rmtree(seat.mail / SESSION)
    r = run()
    assert r.exit_code == EX_COULD_NOT_LOOK and "births it" in r.stderr
    assert pager.LINE_NOT_YET not in r.stderr


def test_pre_claude_with_the_pager_off_says_nothing_about_arming(seat, pre_claude, monkeypatch):
    """Test 12: SESSIONMAIL_PAGER=off stays silent pre-claude, and a missing
    box is still the plain "births it" exit 2."""
    import shutil
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    seat.disarm()
    for flag in ("--new", "--stopped"):
        r = run(flag)
        assert r.exit_code == EX_NOTHING and r.output == "", (flag, r.output)
    shutil.rmtree(seat.mail / SESSION)
    r = run()
    assert r.exit_code == EX_COULD_NOT_LOOK and "births it" in r.stderr
    assert pager.LINE_NOT_YET not in r.stderr
    assert pre_claude == []                       # never asked with the pager off


def test_pre_claude_quiet_prints_nothing_and_exits_0(seat, pre_claude, monkeypatch):
    """Test 13, with amendment 1: --quiet pre-claude prints nothing and
    exits 0 too, for --new and --stopped alike."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    for flag in ("--new", "--stopped"):
        r = run(flag, "--quiet")
        assert r.exit_code == pager.EX_NOT_YET == 0 and r.output == "", (flag, r.output)
    assert pre_claude == [SESSION, SESSION]       # asked, so the 0 is the not-yet 0


def test_a_listening_session_never_asks_whether_claude_is_up(seat, pre_claude, monkeypatch):
    """Test 14: a live stamp is listening; the not-yet question is asked
    only where NOT ARMED would have fired, so it is never asked here."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    stamp = _stamps() / "listening"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{os.getpid()}\n")
    for flag in ("--new", "--stopped"):
        r = run(flag)
        assert r.exit_code == EX_NOTHING and pager.LINE_NOT_YET not in r.output, (flag, r.output)
    assert pre_claude == []


# --- SM-706, the monitor path ----------------------------------------------

MONITOR_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor"


def test_pre_claude_output_is_the_same_whether_or_not_a_monitor_will_run(
        seat, pre_claude, monkeypatch, tmp_path):
    """Test 15: nothing before claude can know whether the pager-monitor
    plugin will run (its gate, trust and interactivity decide), so nothing
    pre-claude may claim one. A seat configured for the plugin - its
    settings name it, and another session's monitor is live on the box -
    gets byte-identical output to a seat with none."""
    import shutil
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()

    def outputs():
        said = []
        for flag in ("--stopped", "--new"):
            r = run(flag)
            said.append((r.exit_code, r.output))
        box = seat.mail / SESSION
        moved = tmp_path / "box-aside"
        shutil.move(box, moved)
        try:
            plain = run()
            said.append((plain.exit_code, plain.stdout, plain.stderr))
        finally:
            shutil.move(moved, box)
        return said

    bare = outputs()
    settings = tmp_path / "user-claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"enabledPlugins": {"pager-monitor@sessionmail": True}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(settings.parent))
    other_claude, other_monitor = 7_200_001, 7_200_002
    monkeypatch.setattr(_seat, "_ps_table", lambda: (
        {other_monitor: other_claude, other_claude: 1},
        {other_claude: "claude --session-id sess-b --plugin-dir /x/plugins/pager-monitor",
         other_monitor: MONITOR_CMD}))
    assert outputs() == bare
    for said in bare:
        text = "".join(str(part) for part in said[1:])
        if "nothing to arm" in text:
            assert text.index("that arm is refused") < text.index("nothing to arm")


def test_in_claude_a_stampless_monitor_is_found_and_never_reads_deaf(seat, monkeypatch):
    """Test 16 (rca-001 sec 3, MEASURED, pinned): in claude, the monitor has
    exec'd under this session's claude but has not stamped `listening` yet.
    Ancestry finds it: --new/--stopped exit 3 with no NOT ARMED and no
    not-yet line, and no stamp is written."""
    from conftest import REAL_CLAUDE_FOR
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    monkeypatch.setattr(_seat, "claude_for", REAL_CLAUDE_FOR)
    claude, monitor = 7_200_003, 7_200_004
    me = os.getpid()
    monkeypatch.setattr(_seat, "_ps_table", lambda: (
        {monitor: claude, me: claude, claude: 1},
        {claude: "claude", monitor: MONITOR_CMD, me: "pager status"}))
    for flag in ("--new", "--stopped"):
        r = run(flag)
        assert r.exit_code == EX_NOTHING, (flag, r.output)
        assert pager.LINE_NOT_LISTENING not in r.output
        assert pager.LINE_NOT_YET not in r.output
    assert not (_stamps() / "listening").exists()


# --- SM-706, amendment 1: pre-claude is exit 0 only where it is a nudge ----

def test_pre_claude_exit_0_never_reaches_plain_status_or_an_in_claude_run(seat, pre_claude, monkeypatch):
    """Amendment 1 is scoped to --new/--stopped: plain status with no box
    keeps exit 2 (test 9), and with a box it is the box's answer - an empty
    box is still 3, nothing. The not-yet 0 is never asked for there."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    r = run()
    assert r.exit_code == EX_NOTHING, r.output
    assert pager.LINE_NOT_YET not in r.output
    assert pre_claude == []


def test_the_status_page_says_pre_claude_exits_0():
    """The page documents the code it now gives (`pager status --help`)."""
    r = CliRunner().invoke(cli, ["status", "--help"], prog_name="pager")
    assert r.exit_code == 0
    text = " ".join(r.output.split())
    assert "claude not up yet (--new/--stopped)" in text


# --- SM-706, amendment 2: claude FOR THIS SESSION, not any claude ----------

def test_a_generator_for_b_under_a_live_claude_for_a_reads_not_yet(seat, monkeypatch):
    """A `session start` generator for session B, run inside a Bash call of
    a live seat whose claude carries `--session-id sess-other` (repro sec 5,
    the SM-454 class): B's claude has not started, so --stopped and --new
    read the not-yet line, exit 0 - not NOT ARMED for B, as if A's claude
    were B's. Owed by fix-002; SM-558's other-id check in `_session_claudes`
    (rebase-001) is what makes it hold."""
    from conftest import REAL_CLAUDE_FOR
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    seat.disarm()
    monkeypatch.setattr(_seat, "claude_for", REAL_CLAUDE_FOR)
    claude_a, shell = 7_200_005, 7_200_006
    me = os.getpid()
    monkeypatch.setattr(_seat, "_ps_table", lambda: (
        {me: shell, shell: claude_a, claude_a: 1},
        {claude_a: "claude --session-id sess-other --plugin-dir /x/plugins/pager-monitor",
         shell: "/bin/zsh -c session start", me: "pager status"}))
    for flag in ("--stopped", "--new"):
        r = run(flag)
        assert r.output.splitlines() == [pager.LINE_NOT_YET], (flag, r.output)
        assert r.exit_code == pager.EX_NOT_YET, (flag, r.output)


# ── SM-812 item 2: the not-done line names its boxes ──────────────────────
#: "[mail] 141 not done, oldest 4h - ..." named no box, so a seat could not
#: tell its own backlog from a subscribed box's (#62: 55 of 58 were kaizen
#: mail). The ride's done/ keeps a hard link of every mail it gave, and its
#: X-Original-To is the address it came for; the split is read from there,
#: only when the line is due. The rulings: AFTER `oldest Xh`, top two keys, the rest rolled into
#: `+N more C`, the session box named `own`, an id with no done/ copy
#: `other`, and the whole line within SCOOP_TAIL_RESERVE's 120 characters.


def _given(session: str, mid: str, to: str, n: int) -> None:
    """A mail the ride gave this session: its link in done/, with TO as its
    X-Original-To, and its `seen ride-along` row long before the interval."""
    done = _seat.ride_dir(None, session) / _seat.QUEUE_DONE
    done.mkdir(parents=True, exist_ok=True)
    (done / f"{1923171200 + n}.M{n}P1.host:2,").write_text(
        f"X-Original-To: {to}\nFrom: beta@example.invalid\nTo: {to}\n"
        f"Subject: given {n}\nMessage-ID: <{mid}>\n\nbody\n")
    with _marks(session).open("a") as f:
        f.write(_six_field(mid, "seen", session, _ago(1100), "ride-along"))


def _not_done_line(output: str) -> str:
    lines = [l for l in output.splitlines() if "not done" in l]
    assert len(lines) == 1, output
    return lines[0]


def test_the_not_done_line_names_each_box_and_its_count(seat, monkeypatch):
    """Two boxes: a subscription's (kaizen) and the session's own."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_seat.REMIND_VAR, "1")
    _given(SESSION, "k1@example.invalid", "kaizen@hq.example.invalid", 1)
    _given(SESSION, "k2@example.invalid", "kaizen@hq.example.invalid", 2)
    _given(SESSION, "o1@example.invalid", f"{SESSION}@hq.example.invalid", 3)
    result = run("--new")
    assert result.exit_code == EX_PAGE, result.output
    line = _not_done_line(result.output)
    assert line.startswith("[mail] 3 not done, oldest "), line     # the old pins hold
    assert "(kaizen 2, own 1)" in line, line
    assert "`message mark ID done" in line                        # the door stays
    assert len(line) <= 120, (len(line), line)


def test_an_id_the_ride_never_gave_is_counted_as_other(seat, monkeypatch):
    """A `message read <BOX>` or hand mark has no done/ copy: no box to name."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_seat.REMIND_VAR, "1")
    _given(SESSION, "k1@example.invalid", "kaizen", 1)
    _marks().open("a").write(
        _six_field("byhand@example.invalid", "read", SESSION, _ago(1100)))
    line = _not_done_line(run("--new").output)
    assert line.startswith("[mail] 2 not done, oldest "), line
    assert "(kaizen 1, other 1)" in line, line


def test_the_not_done_line_stays_within_120_with_long_keys_and_big_counts(seat, monkeypatch):
    """The cap is SCOOP_TAIL_RESERVE's: each tail line under 120 characters.
    Long plus-addresses, 3-digit counts, and more than two boxes."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_seat.REMIND_VAR, "1")
    n = 0
    for to, k in (("sessionmail+sm-793.foreman.b7da531b@hq", 160),
                  ("sessionmail+ready-for-the-long-review@hq", 140),
                  ("kaizen@hq", 120), ("ivan+kaizen@hq", 110)):
        for _ in range(k):
            n += 1
            _given(SESSION, f"id{n}@example.invalid", to, n)
    line = _not_done_line(run("--new").output)
    assert line.startswith("[mail] 530 not done, oldest "), line
    assert len(line) <= 120, (len(line), line)
    assert "+2 more 230" in line, line
    assert "`message mark ID done" in line


def test_the_split_is_read_only_when_the_line_is_due(seat, monkeypatch):
    """The done/ read is paid once per interval, never on every empty scoop:
    inside the interval no header of done/ is opened."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_seat.REMIND_VAR, "1")
    _given(SESSION, "k1@example.invalid", "kaizen", 1)
    assert "kaizen 1" in run("--new").output
    calls = []
    real = _seat.overdue_boxes
    monkeypatch.setattr(_seat, "overdue_boxes", lambda *a, **k: calls.append(a) or real(*a, **k))
    again = run("--new")
    assert again.output == "" and calls == []


def test_the_not_done_line_never_passes_the_cap_whatever_the_boxes():
    """The renderer alone, over extremes: no box, one, many, keys up to 200
    characters, counts up to 7 digits. Each line keeps the door's verb and
    stays within TAIL_LINE_MAX."""
    from sessionmail.pager import TAIL_LINE_MAX, not_done_line
    for count in (1, 999, 1_234_567):
        for width in (1, 12, 40, 200):
            for n_boxes in (0, 1, 2, 3, 9):
                boxes = {f"{'k' * width}{i}": count for i in range(n_boxes)}
                line = not_done_line(count, "2480d", boxes, SESSION)
                assert len(line) <= TAIL_LINE_MAX, (len(line), line)
                assert line.startswith(f"[mail] {count} not done, oldest 2480d")
                assert "`message mark ID done" in line, line


def test_the_reason_hint_goes_before_any_box_name_is_cut():
    """The door's `--reason ...` is the first thing dropped for room: a line
    that shortens or drops a box name never still carries it (review of
    fix-001: 119 chars, `(a 1000, bbbbbbbbbbbbbbbbbbbbbbb… 999)` kept
    `--reason ...` while the second name was clipped)."""
    from sessionmail.pager import NOT_DONE_DOORS, TAIL_LINE_MAX, not_done_line
    for l1 in range(1, 60):
        for l2 in range(1, 60):
            for c in (1, 99, 999):
                a, b = "a" * l1, "b" * l2
                line = not_done_line(2 * c + 1, "4h", {a: c + 1, b: c}, SESSION)
                assert len(line) <= TAIL_LINE_MAX, (len(line), line)
                if line.endswith(NOT_DONE_DOORS[0]):
                    assert f"({a} {c + 1}, {b} {c})" in line, line
