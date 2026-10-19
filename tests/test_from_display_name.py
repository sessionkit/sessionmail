"""SM-398: `From:` carries a short name - `Ivan <ivan@hq>` - not the bare address.

A mail client then shows the sender's short name, not the full address.

The hazard is the signature: it covers `from: <lowercased local-part>`, so the
name must stay OUTSIDE the signed set on both sides. These tests prove that
with the EXISTING verifier, unchanged: a named From verifies, a bare one still
does, a forged name alone does not break it, and a forged address does.
"""

from __future__ import annotations

import subprocess

import pytest

from sessionmail import _send
from test_message_send_json import run, world  # noqa: F401  (world is a fixture)
from test_sender import (EX_OK, HOST, body_file, fake_sendmail, headers_of,  # noqa: F401
                         bin_dir, env, keyed, root, sender, verifier)

SESSION = "35bf6365-6686-d084-55b4-1507b25b65a2"


# ── the door's rule: pure ────────────────────────────────────────────────────

@pytest.mark.parametrize("address, seat, env, want", [
    ("ivan", "ivan", {}, "Ivan <ivan>"),
    ("ivan@hq.example.ts.net", "ivan", {}, "Ivan <ivan@hq.example.ts.net>"),
    ("eve+repro-oracle", "eve", {}, "Eve <eve+repro-oracle>"),
    ("voice-cloner", "voice-cloner", {}, "Voice-Cloner <voice-cloner>"),
    ("ivan", "ivan", {"SESSIONMAIL_FROM_NAME": "Ivan 7"}, "Ivan 7 <ivan>"),
    ("zoe", "zoe", {"SESSIONMAIL_FROM_NAME": "Zoë"}, "Zoë <zoe>"),
    ("ivan", "ivan", {"SESSIONMAIL_FROM_NAME": "off"}, "ivan"),
    ("ivan", "ivan", {"SESSIONMAIL_FROM_NAME": "OFF"}, "ivan"),
    ("ivan", "ivan", {"SESSIONMAIL_FROM_NAME": "0"}, "ivan"),
    ("ivan", "ivan", {"SESSIONMAIL_FROM_NAME": "no"}, "ivan"),
    ("Someone <ivan>", "ivan", {}, "Someone <ivan>"),
], ids=["seat", "qualified", "plus-address", "hyphen", "override", "non-ascii", "off", "OFF",
        "zero", "no", "already-named"])
def test_the_name_is_the_seat_title_cased_or_the_override(address, seat, env, want):
    assert _send.with_display_name(address, seat, SESSION, env) == (want, "")


def test_a_reply_from_the_session_id_is_a_dm_and_stays_unnamed():
    """specs/message: a DM is answered from the session id so the seat is NOT
    on the record. The seat's name in front of it would undo that."""
    for address in (SESSION, f"{SESSION}@{HOST}", SESSION.upper()):
        assert _send.with_display_name(address, "ivan", SESSION, {}) == (address, "")
    assert _send.with_display_name("ivan", "ivan", "-", {}) == ("Ivan <ivan>", "")


@pytest.mark.parametrize("override", ['Evil <x@y>', 'a,b', 'semi;colon', '"quoted"', "back\\slash",
                                      "a@b", " ", "-dash-"])
def test_a_name_that_needs_quoting_is_not_used_and_the_note_says_so(override):
    got, note = _send.with_display_name("ivan", "ivan", SESSION, {"SESSIONMAIL_FROM_NAME": override})
    if not override.strip():
        assert (got, note) == ("Ivan <ivan>", ""), "a blank override is no override"
        return
    assert got == "ivan" and _send.FROM_NAME_VAR in note and "bare address" in note


# ── the door, --json road (what the MCP tool calls) ─────────────────────────

def test_a_json_send_names_the_seat_and_the_envelope_stays_bare(world):
    r, said = run("beta", "-s", "the point", "-f", str(world.body), "--json")
    assert r.exit_code == 0 and said["sent"] == "yes", r.output
    assert b"\nFrom: Alpha <alpha>\n" in b"\n" + world.wire()
    argv = (world.tmp / "sendmail.argv").read_text().split("\x1f")
    assert argv == ["-t", "-i", "-f", "alpha"], "-f is an address, never a name-addr"


def test_off_sends_the_bare_address_as_before(world, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_FROM_NAME", "off")
    r, said = run("beta", "-s", "the point", "-f", str(world.body), "--json")
    assert r.exit_code == 0 and said["sent"] == "yes", r.output
    assert world.wire().startswith(b"From: alpha\n")


def test_an_unusable_override_sends_bare_and_the_notice_rides_in_the_object(world, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_FROM_NAME", "Evil <x@y>")
    r, said = run("beta", "-s", "the point", "-f", str(world.body), "--json")
    assert r.exit_code == 0 and said["sent"] == "yes", r.output
    assert world.wire().startswith(b"From: alpha\n")
    assert any("SESSIONMAIL_FROM_NAME" in n for n in said["notices"])


def _parent(world, box: str, delivered_to: str, mid: str) -> None:
    """One parent in BOX, the way test_message_send's `_parent` writes it. The
    reply lookup reads $SESSION_MAIL_ROOT (the `replying` fixture sets that
    spelling), so the world's root is named there too."""
    d = world.tmp / "mail" / box
    for sub in ("new", "cur", "tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "new" / f"1.P1.{HOST}.{abs(hash(mid))}").write_text(
        f"From: beta\nTo: {delivered_to}\nX-Original-To: {delivered_to}\n"
        f"Subject: parent\nMessage-ID: {mid}\n\nhi\n")


@pytest.fixture
def replying(world, monkeypatch):
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(world.tmp / "mail"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return world


def test_a_reply_to_mail_at_the_seat_is_named(replying):
    _parent(replying, SESSION, "alpha", "<p1@h.session-mail>")
    r, said = run("beta", "-s", "re", "-f", str(replying.body), "-r", "<p1@h.session-mail>", "--json")
    assert r.exit_code == 0 and said["sent"] == "yes", r.output
    assert not any("declared address" in n for n in said["notices"]), said["notices"]
    assert replying.wire().startswith(b"From: Alpha <alpha>\n")


def test_a_reply_to_mail_at_the_session_id_goes_out_from_it_unnamed(replying):
    """The reply-From rule is unchanged by SM-398: the DM is answered from the
    session id - and, new here, without the seat's name in front of it."""
    _parent(replying, SESSION, SESSION, "<p2@h.session-mail>")
    r, said = run("beta", "-s", "re", "-f", str(replying.body), "-r", "<p2@h.session-mail>", "--json")
    assert r.exit_code == 0 and said["sent"] == "yes", r.output
    assert not any("declared address" in n for n in said["notices"]), said["notices"]
    frm = dict(headers_of(replying.wire()))["From"]
    assert frm.lower().startswith(SESSION) and "<" not in frm and "Alpha" not in frm, frm


# ── the signature: the EXISTING verifier, unchanged ─────────────────────────

def _signed(sender, keyed, tmp_path, fake_sendmail, frm: str) -> bytes:
    rc, _, err = sender.run(["eve", "--from", frm, "--subject", "Build green",
                             "--body-file", body_file(tmp_path, b"signed body\n")], keyed)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    return fake_sendmail.wire


def _verify(verifier, tmp_path, wire: bytes) -> subprocess.CompletedProcess:
    f = tmp_path / "delivered.eml"
    f.write_bytes(wire)
    return subprocess.run([str(verifier), str(f)], capture_output=True, text=True)


def test_a_named_from_is_signed_and_verifies(sender, keyed, tmp_path, fake_sendmail, verifier):
    wire = _signed(sender, keyed, tmp_path, fake_sendmail, "Ivan <ivan>")
    assert wire.startswith(b"From: Ivan <ivan>\n")
    assert fake_sendmail.argv == ["-t", "-i", "-f", "ivan"]
    done = _verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "signature-crypto  : \u2705 good" in done.stdout, done.stdout


def test_a_bare_from_still_verifies(sender, keyed, tmp_path, fake_sendmail, verifier):
    wire = _signed(sender, keyed, tmp_path, fake_sendmail, "ivan")
    assert wire.startswith(b"From: ivan\n")
    done = _verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "signature-crypto  : \u2705 good" in done.stdout, done.stdout


def test_the_name_is_inside_the_v2_signed_set(sender, keyed, tmp_path, fake_sendmail, verifier):
    """Under v1 the name was outside the signature (verify: local_part takes the
    innermost <...>), and forging the NAME alone verified. The sender now emits
    v2 only (SM-594), which signs the whole From, display name included (design
    4.2, 6.6 "From display name changed"): forging the name alone reads BAD.
    This change's requirement, *The name is outside the signature*, is one of
    the two that signature-v2's tasks.md 1.3 gates reconciling before archive."""
    wire = _signed(sender, keyed, tmp_path, fake_sendmail, "Ivan <ivan>")
    forged = wire.replace(b"From: Ivan <ivan>\n", b"From: Mallory <ivan>\n", 1)
    assert forged != wire
    assert _verify(verifier, tmp_path, wire).returncode == 0
    done = _verify(verifier, tmp_path, forged)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "signature-crypto  : \u274c BAD" in done.stdout, done.stdout


def test_a_forged_address_fails(sender, keyed, tmp_path, fake_sendmail, verifier):
    wire = _signed(sender, keyed, tmp_path, fake_sendmail, "Ivan <ivan>")
    forged = wire.replace(b"From: Ivan <ivan>\n", b"From: Ivan <eve>\n", 1)
    assert forged != wire
    done = _verify(verifier, tmp_path, forged)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "signature-crypto  : \u274c BAD" in done.stdout, done.stdout
