"""The pager's confirmation mails, and what the agent sees: SM-474 do-007
(tasks.md 4.2 and 4.3; design D7, D9 and the task's design A7).

- 4.2 / D9 / A7: each `pager listen` arm sends ONE subject-only "listening"
  mail to its own session's box, carrying the session id; each focus change
  sends one to the changed session. Both carry `Pager-Note: listening` /
  `Pager-Note: focus` (no `X-`, /RULINGS 3970) AND `Priority: non-urgent`.
  The listener checks `Pager-Note` BEFORE the rules and never pages on it,
  whatever the rules say: it rides along like any other mail.
- 4.3 / D7: the banner names the focus mode and the rule count
  (`focus: sleep until 07:00; rules 4 (seat 3, session 1)`); `pager status`
  says what the next arrival would do (`a mail To ivan@hq now: WAKE
  (default: to:$me)`); the page and the ride-along lines carry the action
  that applied.
- do-006 review F3: the hook's `load_layers` and the banner's
  `live.current()` are guarded; a raise there never costs the ride-along.

The send is a seam (`pager._mta_send`). conftest points it at a recorder, so
NO test sends real mail; one test below drives the real `_sender.send`
against a fake sendmail under tmp, to see the headers on the wire.

Written to fail first against `687267f`.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _sender, _seat, pager, rules
from sessionmail.pager import cli
from test_pager_promote_and_provenance import land
from test_pager_listen_judges import (  # noqa: F401  (the rig is a fixture)
    EX_PAGE,
    EX_TIMEOUT,
    FQDN,
    HOST,
    SESSION,
    batch,
    counted,
    later,
    paged_mids,
    rig,
    run,
    sid,
)


@pytest.fixture
def notes(monkeypatch):
    """Every confirmation mail the pager tried to send, as its `Request`."""
    sent: list = []

    def record(req, env, mint=None):
        sent.append(req)
        return f"<note.{len(sent)}@test.invalid>"

    monkeypatch.setattr(pager, "_mta_send", record)
    return sent


def enqueue(rig, fname: str) -> None:
    """Link a delivered mail into the session's ride queue, as the listener
    does, so a ride-along (the batch hook) can take it."""
    os.link(rig.box() / "new" / fname, _seat.queue_new(rig.seat, SESSION) / fname)


def seat_rules(rig, text: str, name: str = rules.RULES_FILE) -> Path:
    path = Path(rig.seat.root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def header(req, name: str) -> str | None:
    return next((v for n, v in req.headers if n.lower() == name.lower()), None)


# ── 4.2: the listening mail ─────────────────────────────────────────────────

def test_an_arm_sends_one_listening_mail_to_its_own_session_box(rig, notes):
    rig.box()
    result = run("--timeout", "0.3")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert len(notes) == 1, notes
    req = notes[0]
    assert req.to == SESSION
    assert SESSION in req.subject
    assert header(req, "Pager-Note") == "listening"
    assert header(req, "Priority") == "non-urgent"
    assert header(req, "X-Pager-Note") is None, "no X- prefix (/RULINGS 3970)"
    assert req.no_body, "subject only"


def test_the_listening_mail_says_little(rig, notes):
    """D9: suggest nothing, since an agent does what it is told. The subject says what the arm listens on, and nothing that reads as an
    instruction."""
    rig.box()
    run("--timeout", "0.3")
    subject = notes[0].subject
    assert len(subject) <= 80, subject
    for word in ("`", "run", "re-arm", "please", "should", "must"):
        assert word not in subject.lower(), subject


def test_each_arm_sends_its_own(rig, notes):
    rig.box()
    run("--timeout", "0.2")
    run("--timeout", "0.2")
    assert len(notes) == 2


def test_a_seatless_listener_with_no_session_sends_nothing(rig, notes, monkeypatch):
    """No session: there is no session box to confirm to."""
    for var in (*_seat.SESSION_VARS, *_seat.SEAT_VARS):
        monkeypatch.delenv(var, raising=False)
    box = rig.box("human")
    monkeypatch.setenv("MAIL", str(box))
    assert run("--timeout", "0.2").exit_code == EX_TIMEOUT
    assert notes == []


def test_a_failed_send_never_costs_the_listener(rig, monkeypatch):
    """The confirmation is a courtesy: a refused send is named in the banner,
    and the listener listens and pages exactly as before."""
    def refuse(req, env, mint=None):
        raise _sender.Refused("no MTA: nothing was sent", kind="mta")

    monkeypatch.setattr(pager, "_mta_send", refuse)
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("w1@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert "not sent" in result.stderr


def test_a_send_that_raises_anything_never_costs_the_listener(rig, monkeypatch):
    def boom(req, env, mint=None):
        raise RuntimeError("fake sender crashed")

    monkeypatch.setattr(pager, "_mta_send", boom)
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("w2@x.invalid"))
    assert run("--timeout", "3").exit_code == EX_PAGE


# ── 4.2: never pages on a Pager-Note, whatever the rules ───────────────────

# SM-474 do-014 (Pager-Note HARDENED): only the pager's OWN
# confirmations are honoured, proven by `Pager-Proof`. So these tests build
# each note through the real `send_note` (the seam records it) and land what
# it made; a hand-typed `Pager-Note:` is a forgery now, and
# test_pager_promote_and_provenance.py pins that it is judged by the rules.
# The seatless case moved there too (a note with no session is never genuine).

def genuine(note: str, session: str = SESSION):
    """The Request `send_note` builds for NOTE to SESSION, and the mint its
    Date and Message-ID come from (do-015: the proof covers both), recorded,
    not sent."""
    made: list = []
    real = pager._mta_send
    pager._mta_send = lambda req, env, mint: (made.append((req, mint)), "<made@test.invalid>")[1]
    try:
        pager.send_note(note, session, f"pager {note}, session {session}", from_="alpha")
    finally:
        pager._mta_send = real
    return made[0]


@pytest.mark.parametrize("note", ["listening", "focus"])
def test_the_listener_never_pages_on_a_pager_note_mail_even_with_wake_star(rig, note):
    """The slice's criterion. A session rule `wake *` would wake on every
    mail; the pager's own Pager-Note mail, direct to this session, still never
    pages. It stays in the queue for the ride-along."""
    req, mint = genuine(note)
    rig.box()
    rig.session_rules("wake *\n")
    rig.stopped()
    later(0.2, lambda: land(rig, req, mint=mint))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert mint.message_id.strip("<>") not in rig.marks()   # marks hold the bare id
    assert len(rig.queued()) == 1


def test_a_pager_note_beats_a_breakthrough_too(rig):
    """`wake!` lines are tried first among the RULES; a genuine Pager-Note is
    checked before the rules, so even a locked `wake! *` does not page it."""
    req, mint = genuine("listening")
    rig.box()
    rig.locked("wake! *\n")
    rig.stopped()
    later(0.2, lambda: land(rig, req, mint=mint))
    assert run("--timeout", "1").exit_code == EX_TIMEOUT


def test_the_listening_mail_itself_lands_rides_along_and_never_pages(rig, monkeypatch):
    """End to end, with a fake MTA that DELIVERS what it is handed into the
    session box: the arm's own listening mail arrives, is not paged on (the
    session's `wake *` notwithstanding), and the next tool call carries it,
    saying it rode."""
    def deliver_it(req, env, mint):
        land(rig, req, mint=mint)    # its own subject, date and id: the proof covers them
        return mint.message_id

    monkeypatch.setattr(pager, "_mta_send", deliver_it)
    rig.box()
    rig.session_rules("wake *\n")
    rig.stopped()
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert len(rig.queued()) == 1
    lines = [ln for ln in batch() if ln.startswith("[mail]")]
    assert len(lines) == 1, lines
    assert "(ride: pager note)" in lines[0], lines


# ── 4.2: the focus-change mail ──────────────────────────────────────────────

def test_a_focus_change_mails_the_changed_session(notes):
    until = datetime.now().astimezone().replace(hour=7, minute=0, second=0, microsecond=0)
    mid = pager.confirm_focus("sess-b", "sleep", until, from_="ivan")
    assert mid == "<note.1@test.invalid>"
    req = notes[0]
    assert req.to == "sess-b"
    assert "sess-b" in req.subject and "sleep" in req.subject and "07:00" in req.subject
    assert header(req, "Pager-Note") == "focus"
    assert header(req, "Priority") == "non-urgent"
    assert req.from_ == "ivan"
    assert req.no_body


def test_focus_off_is_confirmed_too(notes):
    pager.confirm_focus("sess-b", rules.OFF, None, from_="ivan")
    assert "off" in notes[0].subject and "sess-b" in notes[0].subject


# ── 4.2: the real sender path, against a fake MTA (no real mail) ────────────

FAKE_SENDMAIL = """#!{python}
import os, sys
open(os.environ["FAKE_WIRE"], "wb").write(sys.stdin.buffer.read())
"""


def test_the_headers_reach_the_wire_through_the_real_sender(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL)
    wire = tmp_path / "wire"
    env = {"HOME": str(tmp_path), "PATH": str(bin_dir), "TMPDIR": str(tmp_path),
           "FAKE_WIRE": str(wire), "SESSION_MAIL_ROOT": str(tmp_path / "mail"),
           "SESSIONMAIL_HOST": HOST, "SESSIONMAIL_SENDMAIL": str(bin_dir / "sendmail")}
    monkeypatch.setattr(pager, "_mta_send", pager._deliver_by_mta)
    mid = pager.confirm_listening(SESSION, SESSION, from_="alpha", env=env)
    assert mid.startswith("<") and mid.endswith(">")
    raw = wire.read_bytes().decode()
    head, _, body = raw.partition("\n\n")
    assert f"To: {SESSION}" in head
    assert "\nPager-Note: listening\n" in head
    assert "\nPriority: non-urgent\n" in head
    assert "X-Pager-Note" not in head
    assert body == ""


def test_a_child_listener_under_test_cannot_reach_a_real_mta(rig, sid, tmp_path):
    """The seam is patched in THIS process only. A real `pager listen` child
    (as `_spawn_listener` runs one) sends through the real `_sender`, so the
    suite's own MTA variable must stop it before anything touches the host:
    the refusal is `no MTA`, never preflight's live postfix lookup (do-007
    review, 46cadd4d). The child runs on the REAL `ps`, with its own
    session, so a live listener of the seat running the suite never
    refuses it (SM-558)."""
    import subprocess
    rig.box(sid)
    env = dict(os.environ, PYTHONPATH="src")
    done = subprocess.run(
        [sys.executable, "-c", "from sessionmail.pager import cli; cli()",
         "listen", "--timeout", "0.3"],
        env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == EX_TIMEOUT, done.stderr
    confirm = [ln for ln in done.stderr.splitlines() if "confirm" in ln]
    assert len(confirm) == 1, done.stderr
    assert "not sent - no MTA:" in confirm[0], confirm
    assert "postmap" not in confirm[0], "preflight ran: the host's maps were read"


# ── 4.3: the banner names the focus mode and the rule count ────────────────

def test_the_banner_names_the_focus_and_counts_the_rules(rig):
    rig.box()
    seat_rules(rig, "ride from:a@*\nride from:b@*\nride from:c@*\n")
    rig.session_rules("ride from:d@*\n")
    later_at = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    rig.session_rules(f"sleep until {later_at}\n", rules.FOCUS_FILE)
    result = run("--timeout", "0.2")
    assert f"focus: sleep until {later_at}; rules 4 (seat 3, session 1)" in result.stderr, \
        result.stderr


def test_the_banner_says_focus_off_and_no_rules(rig):
    rig.box()
    result = run("--timeout", "0.2")
    assert "focus: off; rules 0" in result.stderr, result.stderr


# ── 4.3: pager status says what the next arrival would do ──────────────────

def status_lines() -> list[str]:
    """`pager status`'s D7 line is on stderr: stdout stays one line per mail."""
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    return r.stderr.splitlines()


def test_status_says_a_direct_mail_would_wake_by_default(rig):
    rig.box()
    assert f"pager: a mail To alpha@{HOST} now: WAKE (default: to:$me)" in status_lines()


def test_status_names_the_session_rule_that_decides(rig):
    rig.box()
    rig.session_rules("ride from:zed@*\nride *\n")
    assert f"pager: a mail To alpha@{HOST} now: RIDE (session: ride *)" in status_lines()


def test_status_names_the_focus_mode_that_decides(rig):
    rig.box()
    rig.session_rules("sleep\n", rules.FOCUS_FILE)
    assert f"pager: a mail To alpha@{HOST} now: RIDE (focus: sleep: ride *)" in status_lines()


# ── 4.3: the ride-along lines carry the action that applied ────────────────

def test_the_ride_along_lines_carry_the_action(rig):
    rig.box()
    enqueue(rig, rig.deliver("direct@x.invalid"))                     # wake to:$me
    enqueue(rig, rig.deliver("sub@x.invalid", to="atomnote"))          # ride
    lines = [ln for ln in batch() if ln.startswith("[mail]")]
    assert len(lines) == 2, lines
    # `(carry)`, was `(wake)`: every wake carries (SM-656 R5), so the
    # ride-along line says so. The page is untouched.
    assert lines[0].endswith("(carry)"), lines
    assert lines[1].endswith("(ride)"), lines


def test_a_long_ride_along_line_keeps_its_action(rig):
    rig.box()
    # the rig's Subject is `s <mid>`, so a long Message-ID is a long line
    enqueue(rig, rig.deliver("l" * 200 + "@x.invalid", to="atomnote"))
    line = next(ln for ln in batch() if ln.startswith("[mail]"))
    assert line.endswith("(ride)") and len(line) <= pager.LINE_WIDTH, line


def test_a_coalesced_ride_along_line_counts_the_actions(rig, monkeypatch):
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "2")
    rig.box()
    for i in range(3):
        enqueue(rig, rig.deliver(f"c{i}@x.invalid", to="atomnote"))
    lines = [ln for ln in batch() if ln.startswith("[mail]")]
    assert len(lines) == 1 and "3 for atomnote" in lines[0], lines
    assert lines[0].endswith("(ride 3)"), lines


# ── 4.3: the page carries the action that applied ─────────────────────────

def test_the_page_says_what_woke_it(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("p1@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert counted(result) == 1
    assert "pager: wake by `wake to:$me` (built-in)" in result.stdout.splitlines(), result.stdout


def test_the_page_names_a_session_rule_that_woke_it(rig):
    rig.box()
    rig.session_rules("wake subject:*p2@x.invalid\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("p2@x.invalid", to="atomnote"))    # rides by default
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert "pager: wake by `wake subject:*p2@x.invalid` (session)" in result.stdout.splitlines()


# ── do-006 review F3: a raise in the rules loader never costs the ride-along

def test_the_ride_along_still_delivers_when_the_rules_loader_raises(rig, monkeypatch):
    rig.box()
    enqueue(rig, rig.deliver("f3@x.invalid"))

    def broken(*args, **kwargs):
        raise RuntimeError("a bug in parse")

    monkeypatch.setattr(rules, "load_layers", broken)
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0, (r.stdout, r.stderr)
    assert "s f3@x.invalid" in r.stdout, r.stdout
    assert "f3@x.invalid" in rig.marks(), "given: marked seen by the ride-along"


def test_the_listener_still_arms_and_pages_when_the_rules_loader_raises(rig, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("a bug in parse")

    monkeypatch.setattr(rules, "load_layers", broken)
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("f4@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert "could not be read" in result.stderr
