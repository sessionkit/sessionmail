"""Two rulings, both in `pager listen`: SM-474 do-014.

- **F1 (b)**, ruled: on a listen path with NO ride-along (no session and no seat, so no queue:
  `$MAIL`'s box, a pulled mirror), a `ride` verdict PAGES, since "ride" there
  would mean "never heard". Fail toward waking. `$me` is not changed (D2).
  A listener WITH a ride queue keeps ride meaning ride. An explicit user
  `ride` line is promoted too, and the page says it was promoted because
  there is no ride-along.
- **PAGER-NOTE HARDENED**, ruled (1): `Pager-Note` (either spelling)
  is honoured ONLY on the pager's own confirmations, proven by provenance no
  send tool can forge. Otherwise the header is ignored and the rules decide.
  The proof is `Pager-Proof:`, an HMAC under a per-session secret (see
  `pager.note_key`); a mail without a valid one is judged like any other.

Drafts: no `Facts` with `draft=True` can reach the no-queue branch in SM-474
(the arrival path builds `Facts.from_bytes`, which never sets it; nothing in
`src/` does). The session log says so, with the evidence, and deliver-001
carries the note. Nothing is built for it here.

The send is the seam `pager._mta_send`: faked, NO real mail.

Written to fail first against `27abcda`.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import _sender, _seat, pager, rules
from test_pager_listen_judges import (  # noqa: F401  (the rig is a fixture)
    EX_PAGE,
    EX_TIMEOUT,
    FQDN,
    HOST,
    RUNNING_T,
    SESSION,
    counted,
    later,
    paged_mids,
    rig,
    run,
)

_n = 0


def land(rig, req, *, box: str = SESSION, mid: str | None = None, mint=None) -> str:
    """Deliver the Request REQ into BOX as postfix would: its To as
    `X-Original-To`, its own Subject, From and headers, whole. With MINT (the
    `pager.NoteMint` it was sent with), its own Date and Message-ID too, as a
    genuine note needs (do-015: the proof covers both); else MID, and no Date."""
    global _n
    _n += 1
    b = rig.box(box)
    extra = "".join(f"{n}: {v}\r\n" for n, v in req.headers)
    if mint is not None:
        stamp = f"Date: {mint.date}\r\nMessage-ID: {mint.message_id}\r\n"
    else:
        stamp = f"Message-ID: <{mid or f'note{_n}@x.invalid'}>\r\n"
    fname = f"1908540800.M{_n:06d}P{os.getpid()}.note"
    (b / "tmp" / fname).write_bytes(
        f"X-Original-To: {req.to}\r\nFrom: {req.from_ or 'alpha'}@{FQDN}\r\n"
        f"To: {req.to}@{FQDN}\r\nSubject: {req.subject}\r\n{stamp}"
        f"{extra}\r\n".encode())
    os.rename(b / "tmp" / fname, b / "new" / fname)
    return fname


@pytest.fixture
def delivering(rig, monkeypatch):
    """The send seam, faked to DELIVER what it is handed into the session's
    box (the MTA's job, done in-process; no real mail)."""
    sent: list = []

    def deliver(req, env, mint):
        sent.append(req)
        land(rig, req, mint=mint)
        return mint.message_id

    monkeypatch.setattr(pager, "_mta_send", deliver)
    return sent


@pytest.fixture
def seatless(rig, monkeypatch):
    """No session id and no seat: the listener watches $MAIL's new/ with NO
    queue (message-subscriptions D8), so nothing it lets ride is ever heard."""
    for var in (*_seat.SESSION_VARS, *_seat.SEAT_VARS):
        monkeypatch.delenv(var, raising=False)
    box = rig.box("human")
    monkeypatch.setenv("MAIL", str(box))
    return box


SPELLINGS = ["Pager-Note", "X-Pager-Note"]


def header(req, name: str) -> str | None:
    return next((v for n, v in req.headers if n.lower() == name.lower()), None)


# ── F1 (b): no ride-along, so a ride pages ──────────────────────────────────

def test_a_ride_on_a_listener_with_no_ride_along_pages_and_says_so(rig, seatless):
    """A mail through a subscription meets only the built-in `ride *`. With no
    queue there is no ride-along to carry it, so it is PROMOTED to a page, and
    the page line says so."""
    later(0.1, lambda: rig.deliver("p1@x.invalid", to="atomnote", box="human"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert counted(result) == 1, result.stdout
    why = [ln for ln in result.stdout.splitlines() if ln.startswith("pager: ")
           and "promoted" in ln]
    assert len(why) == 1, result.stdout
    assert "`ride *`" in why[0] and "no ride-along" in why[0], why


def test_an_explicit_user_ride_rule_is_promoted_too(rig, seatless, monkeypatch):
    """Bound 3 (a build-level choice): a user's own `ride` line
    on a seatless listener pages as well, and the page names that rule."""
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "ride from:beta@*")
    later(0.1, lambda: rig.deliver("p2@x.invalid", box="human"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    why = [ln for ln in result.stdout.splitlines() if "promoted" in ln]
    assert len(why) == 1, result.stdout
    assert "`ride from:beta@*`" in why[0] and "(env" in why[0], why
    assert "no ride-along" in why[0], why


def test_a_wake_on_the_no_queue_path_is_not_called_promoted(rig, seatless, monkeypatch):
    """Only a RIDE is promoted: a mail the rules already wake says the rule,
    not a promotion."""
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "wake from:beta@*")
    later(0.1, lambda: rig.deliver("p3@x.invalid", box="human"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert "promoted" not in result.stdout, result.stdout
    assert "`wake from:beta@*`" in result.stdout


def test_a_listener_with_a_ride_queue_still_lets_a_ride_ride(rig):
    """(b) cannot leak onto the seat path: the same subscription mail, on a
    session listener with a live ride queue, does not page; it waits for the
    ride-along."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("p4@x.invalid", to="atomnote"))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert len(rig.queued()) == 1
    assert "p4@x.invalid" not in paged_mids(rig)


# ── PAGER-NOTE HARDENED: a forged note is ignored, the rules decide ─────────

@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_forged_note_on_judys_mail_still_wakes(rig, spelling):
    """The built-in `carry! from:$operator`: a `Pager-Note` any sender can type
    must not silence it. Through a subscription (not to $me), so only the
    breakthrough can wake it."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("f1@x.invalid", sender=f"judy@{FQDN}", to="atomnote",
                                   extra=f"{spelling}: listening\r\n"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"f1@x.invalid"}
    assert "carry! from:$operator" in result.stdout


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_forged_note_on_an_urgent_senders_mail_still_wakes(rig, spelling):
    rig.box()
    rig.locked("set urgent $operator beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("f2@x.invalid", to="atomnote",
                                   extra=f"Priority: urgent\r\n{spelling}: focus\r\n"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"f2@x.invalid"}


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_forged_note_on_an_ordinary_direct_mail_still_wakes(rig, spelling):
    """The built-in `wake to:$me`."""
    rig.box()
    rig.stopped()
    # (no `Priority: non-urgent` here: the built-in `ride priority:non-urgent`
    # comes before `wake to:$me`, so that mail would ride by the RULES)
    later(0.2, lambda: rig.deliver("f3@x.invalid", extra=f"{spelling}: listening\r\n"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"f3@x.invalid"}


def test_a_forged_note_with_a_made_up_proof_still_wakes(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver(
        "f4@x.invalid", extra=f"Pager-Note: listening\r\n{pager.PAGER_PROOF}: v1 "
                              f"{'0' * 32} {'f' * 64}\r\n"))
    assert run("--timeout", "3").exit_code == EX_PAGE


def test_a_genuine_proof_copied_onto_another_subject_still_wakes(rig, notes_to_list):
    """Replay: a real confirmation's headers, lifted onto a mail with a
    different subject, do not pass. The proof covers the subject. (A session
    `wake *` decides the forged mail: the lifted `Priority: non-urgent` would
    otherwise ride it by the built-in rules, which is not what is tested.)"""
    pager.confirm_listening(SESSION, SESSION, from_="alpha")
    req = notes_to_list[0]
    rig.box()
    rig.session_rules("wake *\n")
    rig.stopped()
    extra = "".join(f"{n}: {v}\r\n" for n, v in req.headers)
    later(0.2, lambda: rig.deliver("f5@x.invalid", extra=extra))
    assert run("--timeout", "3").exit_code == EX_PAGE


def test_a_proof_made_for_another_session_does_not_pass_here(rig, notes_to_list):
    """The secret is per SESSION: a confirmation made for sess-b, landed in
    sess-a's box, is judged by the rules (here, the session's `wake *`)."""
    pager.confirm_listening("sess-b", "sess-b", from_="alpha")
    req = notes_to_list[0]
    req.to = SESSION                     # its headers, re-addressed here
    rig.box()
    rig.session_rules("wake *\n")         # decides it once the note is ignored
    rig.stopped()
    later(0.2, lambda: land(rig, req, mint=notes_to_list.mints[0]))
    assert run("--timeout", "3").exit_code == EX_PAGE


# ── the pager's OWN confirmations: still never paged ────────────────────────

class Sent(list):
    """The Requests sent, in order, and beside them the mints (`NoteMint`)
    their Date and Message-ID came from."""

    def __init__(self) -> None:
        super().__init__()
        self.mints: list = []


@pytest.fixture
def notes_to_list(monkeypatch):
    sent = Sent()

    def record(req, env, mint=None):
        sent.append(req)
        sent.mints.append(mint)
        return f"<rec{len(sent)}@test.invalid>"

    monkeypatch.setattr(pager, "_mta_send", record)
    return sent


def test_the_arms_own_listening_mail_never_pages_whatever_the_rules(rig, delivering):
    """Through the real send seam (faked to deliver): a session `wake *` and a
    locked `wake! *` notwithstanding, the arm's own confirmation rides."""
    rig.box()
    rig.session_rules("wake *\n")
    rig.locked("wake! *\n")
    rig.stopped()
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert len(delivering) == 1
    assert header(delivering[0], pager.PAGER_PROOF), "the proof was sent"
    assert len(rig.queued()) == 1


def test_a_genuine_focus_mail_never_pages_whatever_the_rules(rig, notes_to_list):
    until = datetime.now().astimezone().replace(hour=7, minute=0, second=0, microsecond=0)
    pager.confirm_focus(SESSION, "sleep", until, from_="ivan")
    req = notes_to_list[0]
    rig.box()
    rig.session_rules("wake *\n")
    rig.locked("wake! *\n")
    rig.stopped()
    later(0.2, lambda: land(rig, req, mint=notes_to_list.mints[0]))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert len(rig.queued()) == 1          # the focus mail (the arm's own was recorded, not delivered)


def test_the_session_secret_is_private_and_made_once(rig, notes_to_list):
    pager.confirm_listening(SESSION, SESSION, from_="alpha")
    pager.confirm_listening(SESSION, SESSION, from_="alpha")
    path = pager.note_key_path(SESSION)
    assert path is not None and path.is_file()
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert path.parent == _seat.session_root(SESSION)
    a, b = notes_to_list
    assert header(a, pager.PAGER_PROOF) != header(b, pager.PAGER_PROOF)


FAKE_SENDMAIL = """#!{python}
import os, sys
open(os.environ["FAKE_WIRE"], "wb").write(sys.stdin.buffer.read())
"""


def test_the_wire_form_of_a_genuine_note_passes_the_check(rig, tmp_path, monkeypatch):
    """Through the REAL `_sender.send` against a fake sendmail under tmp (no
    real mail): what reaches the wire, landed as postfix would, is judged the
    pager's own. The check reads the wire form, not the Request."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL)
    wire = tmp_path / "wire"
    env = {"HOME": str(tmp_path), "PATH": str(bin_dir), "TMPDIR": str(tmp_path),
           "FAKE_WIRE": str(wire), "SESSION_MAIL_ROOT": str(tmp_path / "mail"),
           "SESSIONMAIL_HOST": HOST, "SESSIONMAIL_SENDMAIL": str(bin_dir / "sendmail")}
    monkeypatch.setattr(pager, "_mta_send", pager._deliver_by_mta)
    pager.confirm_listening(SESSION, "some-box", from_="alpha", env=env)
    raw = wire.read_bytes().replace(b"\r\n", b"\n")
    landed = rig.box() / "new" / "1908540800.Mwire.note"
    landed.write_bytes(f"X-Original-To: {SESSION}\n".encode() + raw)
    me = pager.listener_me(rig.seat, SESSION)
    layers = rules.load_layers(rig.seat, SESSION)
    verdict = pager.arrival_verdict(landed, layers, me, session=SESSION)
    assert verdict is not None and verdict.layer == pager.NOTE_LAYER, verdict
    # ...and the same bytes, judged for ANOTHER session, are not
    other = pager.arrival_verdict(landed, layers, me, session="sess-b")
    assert other is not None and other.layer != pager.NOTE_LAYER, other


def test_a_note_can_never_be_genuine_without_a_session(rig, seatless, monkeypatch):
    """No session, no secret, so no confirmation is ever sent here: a note on
    the no-queue path is forged by definition, and the rules (and (b)) decide."""
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "wake *")
    later(0.1, lambda: rig.deliver("n3@x.invalid", box="human",
                                   extra="Pager-Note: listening\r\n"))
    assert run("--timeout", "3").exit_code == EX_PAGE
