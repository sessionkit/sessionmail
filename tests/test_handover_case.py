"""The HANDOVER case: what a successor session sees of mail its predecessor
read and never finished (SM-374 verify-001).

Why this file exists. message-subscriptions 4.2 (do-009) made plain `pager
status` read THIS SESSION'S BOX, because the retired subscription files no
longer name anything to read. The base pager spec's *Read is not terminal*
scenario **"Read, not dealt with, new session"**
(`openspec/specs/pager/spec.md:486`) says a message session A read and never
marked done "is in B's return-now status". B's own box cannot hold mail
delivered before B existed, so that no longer holds for the resident view
(do-009 review). The rule this file follows: name the route a successor
uses, and test it; if no route exists, that is a finding.

**The route is the SEAT BOX.** Every seat row the generator writes lists the
box itself FIRST, `<seat>@host -> <seat>@host, <live sid>@host` (design D2:
"a COPY, never a redirect"). So a seat-addressed mail lands in the seat box
as well as in the live session's box, and the seat box outlives every
session. Successor B reaches it by NAMING the seat box:

    message list <seat> --unread      # A's unfinished mail, unread for B
    message read <seat> N

What this file pins, end to end over a synthetic fleet:

1. the generator (part 1): while A is live, the seat row copies to the seat
   box first and to A; once A is dead and B live, to the seat box and B.
   The seat box is on every row, whoever holds the seat;
2. the readers (part 2), given the delivery that row makes:
   - A: plain `pager status` shows the mail (read, not done);
   - B: plain `pager status` does NOT (it reads B's box). This is the
     FINDING, pinned so that a fix turns it red on purpose;
   - B: a bare `message list` reads B's box and does not show it either;
   - B: `message list <seat> --unread` DOES show it: the route;
   - the limits of the route, pinned as they are: B cannot see "seen by A
     at its time", and A's `done` does not quiet B's listing, because marks
     are per session (a rule older than SM-374;
     `tests/test_message_mark.py:136`).

Hermetic: `--print-valias` over a tmp root (writes nothing), a tmp mail root
and sessionmail home, no notmuch, no live map.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from sessionmail import _seat
from sessionmail import message as message_cli
from sessionmail import pager as pager_cli

from test_postfix_vmailbox_gen import FQDN, HOST, _valias, generator, needs_jq  # noqa: F401

SEAT = "ivan"
A, B = "sess-a", "sess-b"
MID = "handover-1@example.invalid"
SUBJECT = "please finish the migration note"

EX_PAGE, EX_NOTHING = 0, 3


# ── part 1: the generator puts the seat box on every seat row ────────────

def _seat_row(stdout: str, host: str) -> str:
    rows = [line for line in stdout.splitlines() if line.startswith(f"{SEAT}@{host}\t")]
    assert len(rows) == 1, stdout
    return rows[0].split("\t", 1)[1]


@needs_jq
def test_seat_mail_is_copied_to_the_seat_box_first_whichever_session_holds_it(
    generator, tmp_path
):
    """While A is live the row is `ivan -> ivan, A`; after A dies and B is
    live it is `ivan -> ivan, B`. The seat box is FIRST on both, so a mail
    A was given is also in the seat box for B to find."""
    during_a = tmp_path / "during-a"
    during_a.mkdir()
    # SM-218: the persona owns its seat box (as the `fleet` fixture below has
    # it). The generator mints a seat row only for a word that is a box.
    done = _valias(generator, during_a, [{"session_id": A, "agent": SEAT}], {}, [A, SEAT])
    assert done.returncode == 0, done.stderr
    assert _seat_row(done.stdout, HOST) == f"{SEAT}@{HOST}, {A}@{HOST}"
    assert _seat_row(done.stdout, FQDN) == f"{SEAT}@{FQDN}, {A}@{FQDN}"

    after_a = tmp_path / "after-a"
    after_a.mkdir()
    done = _valias(generator, after_a,
                   [{"session_id": A, "agent": SEAT, "alive": False},
                    {"session_id": B, "agent": SEAT}], {}, [A, B, SEAT])
    assert done.returncode == 0, done.stderr
    assert _seat_row(done.stdout, HOST) == f"{SEAT}@{HOST}, {B}@{HOST}"
    assert A not in done.stdout


# ── part 2: what each session's readers show ─────────────────────────────

@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """The mail root after the part-1 row delivered one seat mail while A
    held the seat: a copy in the seat box and one in A's box. B's box
    exists, empty (born by B's first listen; the birth is tested in
    tests/test_pager_listen.py)."""
    root = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", SEAT)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "is_listening", lambda seat, session, ps=None: os.getpid())
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    for box in (SEAT, A, B):
        for leaf in ("tmp", "new", "cur"):
            (root / box / leaf).mkdir(parents=True, exist_ok=True)
    raw = (f"X-Original-To: {SEAT}@example.invalid\r\n"
           f"From: beta@example.invalid\r\nTo: {SEAT}@example.invalid\r\n"
           f"Subject: {SUBJECT}\r\nMessage-ID: <{MID}>\r\n"
           f"Date: Sun, 09 Jun 2030 17:00:00 +1000\r\n\r\nbody\r\n").encode()
    for box in (SEAT, A):     # the part-1 row: the seat box first, then A
        (root / box / "new" / "1908540800.M1P1.test").write_bytes(raw)
    return root


def as_session(monkeypatch, sid: str) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)


def mark(what: str, sid: str) -> None:
    """Through `_seat.write_mark`, the ONE writer `message mark` calls (as
    tests/test_message_list.py does): `message mark` checks the id exists
    through notmuch, which this file does not run."""
    _seat.write_mark(_seat.resolve(), MID, what, sid, "2030-06-09T17:05:00+10:00", "test")


def pager(*argv: str):
    return CliRunner().invoke(pager_cli.cli, list(argv), prog_name="pager")


def message(*argv: str):
    return CliRunner().invoke(message_cli.cli, list(argv), prog_name="message")


def test_a_reads_the_seat_mail_and_leaves_it_unfinished(fleet, monkeypatch):
    """The predecessor's side: A marks it read, not done, and its resident
    view still carries it ("read is not terminal", same session)."""
    as_session(monkeypatch, A)
    mark("read", A)
    shown = pager("status")
    assert shown.exit_code == EX_PAGE, shown.output
    assert SUBJECT in shown.output
    assert SUBJECT not in message("list", SEAT, "--unread").output


def test_FINDING_the_successors_plain_status_does_not_carry_it(fleet, monkeypatch):
    """The base scenario "Read, not dealt with, new session" (pager/spec.md:486)
    no longer holds for plain `pager status`: it reads B's own box, which the
    mail never reached. Pinned as it is, so that a fix goes red here on
    purpose and this test is rewritten with it."""
    as_session(monkeypatch, A)
    mark("read", A)
    as_session(monkeypatch, B)
    quiet = pager("status")
    assert quiet.exit_code == EX_NOTHING, quiet.output
    assert f"box {B}, all quiet" in quiet.output
    bare = message("list")
    assert bare.exit_code == 0 and SUBJECT not in bare.output
    assert f"-- {B}: 0 shown" in bare.output


def test_ROUTE_the_successor_finds_it_by_naming_the_seat_box(fleet, monkeypatch):
    """The route: B names the seat box. A's read does not hide it (marks are
    per session), so it is unread for B, and B can read it."""
    as_session(monkeypatch, A)
    mark("read", A)
    assert SUBJECT not in message("list", SEAT, "--unread").output

    as_session(monkeypatch, B)
    listed = message("list", SEAT, "--unread")
    assert listed.exit_code == 0, listed.output
    assert SUBJECT in listed.output
    assert "marked read (--unread)" not in listed.output
    read = message("read", SEAT, "1")
    assert read.exit_code == 0, read.output
    assert SUBJECT in read.output


def test_the_routes_limits_are_per_session_marks(fleet, monkeypatch):
    """What the route does NOT give, pinned so nobody reads more into it:
    B sees no trace of A's marks, so neither "seen by A at its time" (the
    base scenario's second half) nor A's `done` reaches B. Both follow from
    per-session marks, not from SM-374."""
    as_session(monkeypatch, A)
    mark("done", A)
    assert [(m.mark, m.session) for m in _seat.read_mark_events(_seat.resolve(), A)] \
        == [("done", A)]                     # the control: A's done IS on disk
    assert SUBJECT not in pager("status").output

    as_session(monkeypatch, B)
    assert _seat.read_mark_events(_seat.resolve(), B) == []
    assert SUBJECT in message("list", SEAT, "--unread").output
