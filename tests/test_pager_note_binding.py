"""The Pager-Note proof, bound to its mail (N2) and made atomically (N1): SM-474 do-015.

Ruled in from the do-014 review's N1 and N2:

- **N2.** do-014's MAC covered the session, the note, the nonce and the
  Subject. So a genuine confirmation read out of a box could have its
  `Pager-Note`, `Pager-Proof` and `Subject` lifted onto a mail with any From
  (Judy's too) and any body, and that mail would RIDE. The proof now also
  covers From, Date, Message-ID and the SHA-256 of the CANONICAL body. A lift
  onto a mail that differs in any one of them is no note, and the rules
  decide it. An honest re-delivery of the same message (byte for byte, or
  re-encoded in transport) still rides. A proof is also FRESH only when its
  Date is at most 15 minutes before the mail arrived and at most 2 minutes
  after it. There is no once-per-Message-ID ledger (ruled).
- **N1.** The key was created with O_EXCL and only then written, so a racer
  could read it empty, and a crash in between left an empty key for good.
  It is now written whole to a temp file and linked into place. A key that
  is empty or not a key is replaced when a confirmation is next sent.

Genuine notes are made through the REAL `_sender.send`, against a fake
sendmail under tmp (no real mail), and landed as postfix would. They are
judged by `pager.arrival_verdict`, or by a real `pager listen` run.

Written to fail first against `43d4f81`.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from datetime import datetime, timedelta
from email.parser import BytesParser
from email.policy import compat32
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import pager, rules
from test_pager_listen_judges import (  # noqa: F401  (the rig is a fixture)
    EX_PAGE,
    EX_TIMEOUT,
    FQDN,
    HOST,
    SESSION,
    later,
    rig,
    run,
)

FAKE_SENDMAIL = """#!{python}
import os, sys
open(os.environ["FAKE_WIRE"], "wb").write(sys.stdin.buffer.read())
"""

#: The fields the P2 lift may change, one at a time.
FIELDS = ["from", "body", "date", "message-id"]

_n = 0


@pytest.fixture
def wire(tmp_path, monkeypatch):
    """make(note, from_) -> the bytes the REAL sender hands the MTA for one
    confirmation to SESSION (`send_note`, through `_deliver_by_mta`)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL)
    out = tmp_path / "wire"
    env = {"HOME": str(tmp_path), "PATH": str(bin_dir), "TMPDIR": str(tmp_path),
           "FAKE_WIRE": str(out), "SESSION_MAIL_ROOT": str(tmp_path / "mail"),
           "SESSIONMAIL_HOST": HOST, "SESSIONMAIL_SENDMAIL": str(bin_dir / "sendmail")}
    monkeypatch.setattr(pager, "_mta_send", pager._deliver_by_mta)

    def make(note: str = pager.NOTE_FOCUS, from_: str = "alpha") -> bytes:
        pager.send_note(note, SESSION, f"pager {note}, session {SESSION}", from_=from_, env=env)
        return out.read_bytes()

    return make


def fields(raw: bytes) -> dict[str, str]:
    """The genuine mail's values, as the wire carries them."""
    msg = BytesParser(policy=compat32).parsebytes(raw)
    got = {name: str(msg[name]) for name in ("From", "Date", "Message-ID", "Subject",
                                            "Pager-Note", "Pager-Proof")}
    got["body"] = msg.get_payload()
    return got


def lifted(raw: bytes, *, alter: str | None = None, to: str = SESSION,
           new_from: str = f"judy@{FQDN}") -> bytes:
    """The P2 lift: the genuine `Pager-Note`, `Pager-Proof` and `Subject`
    on a mail of the attacker's own, which copies the genuine From, Date,
    Message-ID and body except ALTER (None: all copied). No `Priority`, as
    an attacker's mail would not carry one, so the rules see a plain mail.
    From is qualified as postfix qualifies a bare one. TO is the address it
    came through (`X-Original-To`)."""
    f = fields(raw)
    local = f["From"].split("@", 1)[0]
    sender = f"{local}@{FQDN}"
    date, mid, body = f["Date"], f["Message-ID"], f["body"]
    if alter == "from":
        sender = new_from
    elif alter == "body":
        body = "URGENT: db1 is down, call me now\n"
    elif alter == "date":
        date = format_datetime(parsedate_to_datetime(date) + timedelta(seconds=60))
    elif alter == "message-id":
        mid = "<lifted.1@x.invalid>"
    elif alter is not None:
        raise AssertionError(alter)
    return (f"X-Original-To: {to}\nFrom: {sender}\nTo: {SESSION}@{FQDN}\nDate: {date}\n"
            f"Subject: {f['Subject']}\nMessage-ID: {mid}\nPager-Note: {f['Pager-Note']}\n"
            f"Pager-Proof: {f['Pager-Proof']}\n\n{body}").encode()


def landed(rig, raw: bytes, *, to: str = SESSION, arrived: float | None = None,
           qualify: bool = True) -> Path:
    """RAW delivered into the session box as postfix would: `X-Original-To`
    first, a bare From qualified. ARRIVED sets the arrival time (mtime)."""
    global _n
    _n += 1
    if not raw.startswith(b"X-Original-To:"):
        nl = b"\r\n" if b"\r\n" in raw else b"\n"     # a CRLF mail stays CRLF throughout
        if qualify:
            head, sep, body = raw.partition(nl + nl)
            lines = [(ln + b"@" + FQDN.encode()) if ln.startswith(b"From: ") and b"@" not in ln
                     else ln for ln in head.split(nl)]
            raw = nl.join(lines) + sep + body
        raw = f"X-Original-To: {to}".encode() + nl + raw
    path = rig.box() / "new" / f"1908540800.M{_n:06d}P{os.getpid()}.bind"
    path.write_bytes(raw)
    if arrived is not None:
        os.utime(path, (arrived, arrived))
    return path


def layer_of(rig, path: Path) -> str:
    me = pager.listener_me(rig.seat, SESSION)
    layers = rules.load_layers(rig.seat, SESSION)
    verdict = pager.arrival_verdict(path, layers, me, session=SESSION)
    assert verdict is not None
    return verdict.layer


def date_of(raw: bytes) -> float:
    return parsedate_to_datetime(fields(raw)["Date"]).timestamp()


# ── the honest mail rides, and survives transport ──────────────────────────

def test_the_genuine_note_as_sent_is_the_pagers_own(rig, wire):
    assert layer_of(rig, landed(rig, wire())) == pager.NOTE_LAYER


def test_the_lift_with_nothing_changed_is_still_the_same_mail(rig, wire):
    """The control for every lift below: the builder itself does not break
    a proof (it drops `Priority`, which the proof does not cover)."""
    assert layer_of(rig, landed(rig, lifted(wire()))) == pager.NOTE_LAYER


def test_a_byte_identical_re_delivery_still_rides(rig, wire):
    raw = wire()
    first, second = landed(rig, raw), landed(rig, raw)
    assert layer_of(rig, first) == pager.NOTE_LAYER
    assert layer_of(rig, second) == pager.NOTE_LAYER


def reencode(raw: bytes, how: str) -> bytes:
    """RAW as a transport might re-encode it, meaning the same thing."""
    head, _, body = raw.partition(b"\n\n")
    if how == "crlf":
        return raw.replace(b"\n", b"\r\n")
    if how == "trailing-whitespace":
        return head + b"\n\n" + body + b"   \n\t\n\n"
    if how == "quoted-printable":
        head = head.replace(b"Content-Transfer-Encoding: 8bit",
                            b"Content-Transfer-Encoding: quoted-printable")
        return head + b"\n\n" + body + b"=20\n"
    if how == "base64":
        head = head.replace(b"Content-Transfer-Encoding: 8bit",
                            b"Content-Transfer-Encoding: base64")
        return head + b"\n\n" + base64.encodebytes(body + b" \r\n")
    raise AssertionError(how)


@pytest.mark.parametrize("how", ["crlf", "trailing-whitespace", "quoted-printable", "base64"])
def test_an_honest_proof_survives_re_encoding_in_transport(rig, wire, how):
    """The body is hashed in its CANONICAL form: the transfer encoding
    undone, line endings as LF, trailing whitespace on each line and
    trailing blank lines dropped."""
    raw = wire()
    assert b"Content-Transfer-Encoding: 8bit" in raw, "the wire says what it is"
    assert layer_of(rig, landed(rig, reencode(raw, how))) == pager.NOTE_LAYER


# ── N2: the P2 lift, one field at a time, is no note ───────────────────────

@pytest.mark.parametrize("alter", FIELDS)
def test_the_lift_onto_a_mail_differing_in_one_field_is_no_note(rig, wire, alter):
    raw = wire()
    assert layer_of(rig, landed(rig, lifted(raw, alter=alter))) != pager.NOTE_LAYER


@pytest.mark.parametrize("alter", FIELDS)
def test_the_lift_on_judys_mail_pages(rig, wire, alter):
    """Through a subscription, so only the breakthrough `carry! from:$operator`
    can wake it. The genuine note is Judy's own (a focus change she made),
    so every field but the altered one is hers; for `from`, the genuine note
    is alpha's and the lift says it is Judy's."""
    raw = wire(from_="alpha" if alter == "from" else "judy")
    mail = lifted(raw, alter=alter, to="atomnote", new_from=f"judy@{FQDN}")
    assert b"From: judy@" in mail
    rig.box()
    rig.stopped()
    later(0.2, lambda: landed(rig, mail))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (alter, result.stdout, result.stderr)
    assert "carry! from:$operator" in result.stdout


@pytest.mark.parametrize("alter", FIELDS)
def test_the_lift_on_a_direct_mail_pages(rig, wire, alter):
    """Direct to this session, so the built-in `wake to:$me` decides it."""
    raw = wire()
    mail = lifted(raw, alter=alter, new_from=f"beta@{FQDN}")
    rig.box()
    rig.stopped()
    later(0.2, lambda: landed(rig, mail))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (alter, result.stdout, result.stderr)


def test_the_unaltered_lift_on_a_direct_mail_rides(rig, wire):
    """The control for the two above, through the same listener."""
    mail = lifted(wire())
    rig.box()
    rig.stopped()
    later(0.2, lambda: landed(rig, mail))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


def test_a_second_from_header_is_no_note(rig, wire):
    """One From, one Date, one Message-ID, one Subject, one proof: a second
    copy of any of them could be the one a reader shows."""
    head, _, body = wire().partition(b"\n\n")
    raw = head + f"\nFrom: judy@{FQDN}".encode() + b"\n\n" + body
    assert layer_of(rig, landed(rig, raw)) != pager.NOTE_LAYER


@pytest.mark.parametrize("text", ["!!! *** ??? ---", "====", "@@@@ @@@@"])
def test_a_body_its_own_encoding_cannot_decode_is_no_note(rig, wire, text):
    """do-015 review F1: `genuine_note` refuses a body whose declared transfer
    encoding does not decode cleanly (a decode DEFECT). A base64 body of
    characters outside the alphabet decodes to b"", which is the body the
    genuine confirmation carries, so with the guard removed the genuine
    headers on such a body would RIDE as a note and the attacker's text would
    be shown under them. With it, the mail is no note and the rules decide
    (here: a wake, through the built-in `wake to:$me`)."""
    raw = wire()
    assert b"Content-Transfer-Encoding: 8bit" in raw
    head, _, _ = raw.partition(b"\n\n")
    head = head.replace(b"Content-Transfer-Encoding: 8bit", b"Content-Transfer-Encoding: base64")
    hostile = head + b"\n\n" + text.encode() + b"\n"
    decoded = BytesParser(policy=compat32).parsebytes(hostile).get_payload(decode=True)
    assert decoded == b"", "the premise: it decodes to the genuine (empty) body"
    assert layer_of(rig, landed(rig, hostile)) != pager.NOTE_LAYER


def test_the_same_headers_on_a_clean_base64_empty_body_still_ride(rig, wire):
    """The control for the test above: it is the DEFECT that refuses the
    mail, not base64. The genuine note, re-encoded as base64 with its real
    (empty) body, is still the pager's own."""
    raw = wire()
    head, _, body = raw.partition(b"\n\n")
    head = head.replace(b"Content-Transfer-Encoding: 8bit", b"Content-Transfer-Encoding: base64")
    mail = head + b"\n\n" + base64.encodebytes(body)
    assert layer_of(rig, landed(rig, mail)) == pager.NOTE_LAYER


# ── F2: judge_head never judges a proof over its headers alone ─────────────

def test_judge_head_requires_the_whole_mail_so_a_proof_is_never_judged_over_head_alone(rig):
    """do-015 review F2: `mail` was optional, and with None the body hash was
    taken over an empty body, so a body-swapped lift passed. It is REQUIRED:
    a caller that forgets it gets a TypeError, never a fail-open default."""
    import inspect
    param = inspect.signature(pager.judge_head).parameters["mail"]
    assert param.default is inspect.Parameter.empty, "no default: mail is required"
    layers = rules.load_layers(rig.seat, SESSION)
    me = pager.listener_me(rig.seat, SESSION)
    with pytest.raises(TypeError):
        pager.judge_head(b"From: a@x.invalid\n\n", layers, me,
                         datetime.now().astimezone(), session=SESSION)


def test_a_body_swapped_lift_cannot_pass_through_judge_head(rig, wire):
    """The same lift through `judge_head` itself, given its whole mail: the
    lift with its body swapped is no note, and the honest mail is. (Head-only
    is no longer expressible: see the test above.)"""
    raw = wire()
    me = pager.listener_me(rig.seat, SESSION)
    layers = rules.load_layers(rig.seat, SESSION)
    now = datetime.fromtimestamp(date_of(raw)).astimezone()

    def verdict(mail: bytes):
        head = mail.partition(b"\n\n")[0] + b"\n\n"
        return pager.judge_head(head, layers, me, now, session=SESSION, mail=mail)

    honest = lifted(raw)
    swapped = lifted(raw, alter="body")
    assert verdict(honest).layer == pager.NOTE_LAYER, "the control"
    assert verdict(swapped).layer != pager.NOTE_LAYER


def test_arrival_verdict_still_passes_the_whole_mail_of_a_note(rig, wire):
    """`arrival_verdict` is unchanged in behaviour: a genuine note rides, a
    body-swapped lift of it does not, each read from its file."""
    raw = wire()
    assert layer_of(rig, landed(rig, raw)) == pager.NOTE_LAYER
    assert layer_of(rig, landed(rig, lifted(raw, alter="body"))) != pager.NOTE_LAYER


# ── freshness: 15 minutes old at most, 2 minutes ahead at most ─────────────

@pytest.mark.parametrize("offset, fresh", [
    (15 * 60, True),            # Date exactly 15 minutes before arrival
    (15 * 60 + 1, False),       # a second more
    (-2 * 60, True),            # Date exactly 2 minutes after arrival (skew)
    (-2 * 60 - 1, False),       # a second more
])
def test_a_proof_is_fresh_for_15_minutes_with_2_minutes_skew(rig, wire, offset, fresh):
    raw = wire()
    path = landed(rig, raw, arrived=date_of(raw) + offset)
    assert (layer_of(rig, path) == pager.NOTE_LAYER) is fresh, (offset, fresh)


def test_the_window_is_named(rig):
    assert pager.NOTE_FRESH == timedelta(minutes=15)
    assert pager.NOTE_SKEW == timedelta(minutes=2)


def test_a_stale_genuine_note_on_a_direct_mail_pages(rig, wire, monkeypatch):
    """A genuine note minted 16 minutes ago and arriving now is refused as a
    note, and falls to the rules: `Priority: non-urgent` would ride it by the
    built-in rules, so the session's `wake *` decides."""
    monkeypatch.setattr(pager, "_note_epoch", lambda: int(time.time()) - 16 * 60)
    raw = wire()
    rig.box()
    rig.session_rules("wake *\n")
    rig.stopped()
    later(0.2, lambda: landed(rig, raw))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)


# ── N1: the key is made whole, or not at all ───────────────────────────────

def test_racing_makers_all_get_the_one_whole_key(rig):
    """Many rounds of several threads making the key at once: none sees no
    key or a partial one, and all end with the same key, the one on disk."""
    path = pager.note_key_path(SESSION)
    assert path is not None
    threads = 8
    for _ in range(150):
        path.unlink(missing_ok=True)
        barrier = threading.Barrier(threads)
        got: list = [None] * threads

        def make(i: int) -> None:
            barrier.wait()
            got[i] = pager.note_key(SESSION, make=True)

        workers = [threading.Thread(target=make, args=(i,)) for i in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        assert None not in got, got
        assert len(set(got)) == 1, "every racer ends with the same key"
        assert all(len(k) == 32 for k in got)
        assert bytes.fromhex(path.read_text(encoding="ascii").strip()) == got[0]
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    left = [p.name for p in path.parent.iterdir() if p.name != path.name and "key" in p.name]
    assert not left, f"temp files left beside the key: {left}"


@pytest.mark.parametrize("content", [b"", b"   \n", b"not hex at all\n", b"abcd\n"])
def test_an_empty_or_broken_key_heals_when_a_note_is_next_sent(rig, wire, content):
    """A crash-orphaned EMPTY key (the old O_EXCL-then-write) or any file that
    is not a key is REPLACED by the next send that needs it, so a session is
    never left without confirmations for good. The note it sends is genuine."""
    path = pager.note_key_path(SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    assert pager.note_key(SESSION) is None, "a reader does not heal it, and proves nothing"
    raw = wire()
    key = pager.note_key(SESSION)
    assert key is not None and len(key) == 32
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert layer_of(rig, landed(rig, raw)) == pager.NOTE_LAYER


def test_an_empty_key_heals_on_the_next_arm_and_its_note_rides(rig, monkeypatch):
    """End to end: the arm's own confirmation, delivered by a fake MTA into
    the box (the real sender would), heals the key and rides."""
    path = pager.note_key_path(SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    captured: list[bytes] = []

    def deliver(req, env, mint=None):
        from sessionmail import _sender
        body = b""
        head = _sender.build(req, body, host=HOST, date=mint.date, message_id=mint.message_id,
                             references=[], session="", signature=None)
        captured.append(head)
        landed(rig, head)
        return mint.message_id

    monkeypatch.setattr(pager, "_mta_send", deliver)
    rig.box()
    rig.session_rules("wake *\n")
    rig.stopped()
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert captured, "the confirmation was sent"
    assert len(rig.queued()) == 1


def test_an_unreadable_key_is_refused_loudly_not_replaced(rig, wire):
    """A key that cannot be READ is not a broken key to replace (it may be
    fine, and another session's reader may hold it): the send is refused,
    naming the file, every time."""
    path = pager.note_key_path(SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ab" * 32 + "\n", encoding="ascii")
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running as a user who can read a mode-000 file")
        with pytest.raises(RuntimeError, match=str(path)):
            pager.note_proof(SESSION, pager.NOTE_FOCUS, "s", from_="alpha",
                             date="Sun, 09 Jun 2030 12:00:00 +0000",
                             message_id="<m@x.invalid>")
    finally:
        path.chmod(0o600)
    assert path.read_text(encoding="ascii").strip() == "ab" * 32


# ── ONE reader of Pager-Note (engineer F12, resolved at the merge: swap-001) ─

def test_the_unproven_claim_reader_is_gone():
    """`genuine_note` is the one reader; `arrival_verdict` asks only the
    byte prefilter before reading a body for it to prove."""
    assert not hasattr(pager, "pager_note")


@pytest.mark.parametrize("head, may", [
    (b"From: a@b\nPager-Note: listening\n\n", True),
    (b"From: a@b\nX-Pager-Note: listening\n\n", True),
    (b"From: a@b\npager-note: listening\n\n", True),
    (b"From: a@b\nX-PAGER-NOTE: listening\n\n", True),
    (b"From: a@b\nSubject: about pager-note: a false hit costs a read\n\n", True),
    (b"From: a@b\nSubject: a plain mail\n\n", False),
    (b"From: a@b\nPager-Proof: v1 n m\n\n", False),
])
def test_the_prefilter_is_a_superset_of_every_spelling(head, may):
    assert pager._may_be_note(head) is may


@pytest.mark.parametrize("spelling", [b"x-pager-note:", b"PAGER-NOTE:", b"X-Pager-Note:"])
def test_a_genuine_note_in_any_spelling_and_case_still_rides(rig, wire, spelling):
    """The prefilter lets every spelling through to the one reader, which
    proves it: field names are case-insensitive, and the proof covers the
    note, never the field name's spelling."""
    raw = wire()
    assert raw.count(b"Pager-Note:") == 1
    assert layer_of(rig, landed(rig, raw.replace(b"Pager-Note:", spelling))) == pager.NOTE_LAYER


def test_two_notes_in_one_second_never_share_a_message_id(monkeypatch):
    """SM-839: a pager is one process, so every note it mints shares the pid;
    a `$RANDOM` drawn twice in one second is drawn again, never minted twice."""
    from sessionmail import _sender
    draws = iter([77, 77, 78])
    monkeypatch.setattr(_sender.random, "randint", lambda a, b: next(draws))
    monkeypatch.setattr(_sender, "_DRAWN", {})
    monkeypatch.setattr(pager, "_note_epoch", lambda: 1908540800)
    env = {"SESSIONMAIL_HOST": "example.invalid"}
    one, two = pager.note_mint(env), pager.note_mint(env)
    assert (one.rand, two.rand) == (77, 78)
    assert one.message_id != two.message_id and one.pid == two.pid
