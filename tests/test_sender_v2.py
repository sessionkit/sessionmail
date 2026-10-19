"""The sender signs v2 (SM-594 do-003, design.md section 7).

What is pinned here:

  - the sender's own signing code (`sign_wire`, the scratch key of the
    `keyed` fixture) over EVERY golden vector's wire gives the vector's h=
    and p=, and the shipped verifier says good on it, as captured and as
    delivered (the three-part atom shape included);
  - a real send - display-name and bare addresses, Cc, Bcc, claim headers,
    X-Session-From - round-trips through the verifier, as captured and with
    postfix's work done to it (trace headers, addresses qualified);
  - the wire stays as it was: address headers bare, Bcc written for
    `sendmail -t` to strip and never signed, X-Original-Subject outside h=;
  - ORIGIN from $SESSIONMAIL_ORIGIN, from `postconf -xh myorigin`, or none
    (the addresses sign bare and the receipt says so), and `postconf` is
    asked only when keyed AND a bare address is signed;
  - the SM-590 seam: `sign_bytes` returns None, never raises, when unkeyed
    or when the keyholder is dead.

No live key and no live box: the `keyed` fixture's throwaway agent, and
SESSIONMAIL_SENDMAIL at a capture.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from conftest import TEST_ORIGIN
from sessionmail import _sig
from test_sender import (EX_OK, HOST, body_file, fake_sendmail, headers_of,  # noqa: F401
                         bin_dir, env, fake_postfix, keyed, root, sender, verifier)

D = Path(__file__).parent / "fixtures" / "sig_v2"
MANIFEST = json.loads((D / "vectors.json").read_text())["vectors"]
NAMES = sorted(MANIFEST)


# ── helpers ──────────────────────────────────────────────────────────────────

def tags_of(wire: bytes) -> dict[str, str]:
    return _sig.parse_header(_sig.signature_value(wire))


def without_signature(raw: bytes) -> bytes:
    """`raw` less its X-Session-Signature field and that field's folded lines."""
    head, sep, body = raw.partition(b"\n\n")
    kept, dropping = [], False
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        dropping = line.lower().startswith(b"x-session-signature:")
        if not dropping:
            kept.append(line)
    return b"\n".join(kept) + sep + body


def verify(verifier, tmp_path, raw: bytes, origin: str | None = TEST_ORIGIN):
    """The shipped verifier on these bytes; ORIGIN as the caller says (None: unset)."""
    f = tmp_path / "verify-me.eml"
    f.write_bytes(raw)
    child = {k: v for k, v in os.environ.items() if k != "SESSIONMAIL_ORIGIN"}
    if origin is not None:
        child["SESSIONMAIL_ORIGIN"] = origin
    return subprocess.run([str(verifier), str(f)], capture_output=True, text=True, env=child)


def crypto_ok(wire: bytes, origin: str) -> bool:
    """`ssh-keygen -Y check-novalidate` over `_sig.canonical(..., origin)` of a signed wire:
    the signature checked against ONE named origin, whatever the host's postfix says."""
    t = tags_of(wire)
    h, p = t["h"].split(":"), t["p"].split(",")
    data = _sig.canonical(without_signature(wire), t["n"], h, p, origin)
    armor = ("-----BEGIN SSH SIGNATURE-----\n" + "\n".join(textwrap.wrap(t["b"], 70))
             + "\n-----END SSH SIGNATURE-----\n")
    sig = Path(os.environ["TMPDIR"] if "TMPDIR" in os.environ else "/tmp")
    f = sig / f"crypto-{os.getpid()}.sig"
    f.write_text(armor)
    try:
        return subprocess.run(["ssh-keygen", "-Y", "check-novalidate", "-n", t["n"], "-s", str(f)],
                              input=data, capture_output=True).returncode == 0
    finally:
        f.unlink()


def delivered(wire: bytes, qualify: dict[bytes, bytes]) -> bytes:
    """What postfix makes of a captured wire: trace headers on top, `Bcc:` gone (`-t`), and
    each bare address in `qualify` rewritten as given (design 4.1)."""
    head, sep, body = wire.partition(b"\n\n")
    head = b"\n".join(l for l in head.split(b"\n") if not l.startswith(b"Bcc:"))
    for old, new in qualify.items():
        head = head.replace(old, new)
    trace = (b"Return-Path: <eve@" + HOST.encode() + b">\n"
             b"X-Original-To: ivan\nDelivered-To: ivan@" + HOST.encode() + b"\n"
             b"Received: by " + HOST.encode() + b" (Postfix, from userid 501)\n"
             b"\tid 4ABC; Tue,  1 Jan 2030 12:00:00 +0000 (UTC)\n")
    return trace + head + sep + body


def send_req(sender, env, **kw):
    req = sender.Request(**{"to": "eve", "from_": "ivan", "subject": "Build green", **kw})
    return sender.send(req, b"signed body\n", env)


# ── every golden vector, signed by the sender's own code ────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_the_sender_signs_every_vector_and_the_verifier_says_good(name, sender, keyed, tmp_path,
                                                                    verifier):
    v = MANIFEST[name]
    keyed["SESSIONMAIL_ORIGIN"] = v["origin"]
    wire = (D / f"{name}.wire").read_bytes()
    err: list[str] = []
    header = sender.sign_wire(wire, keyed, err)
    assert header is not None and err == []
    signed = _sig.insert_header(wire, header)
    t = tags_of(signed)
    assert (t["v"], t["n"]) == ("2", v["ns"])
    assert t["h"].split(":") == v["h"] and t["p"].split(",") == v["p"]
    done = verify(verifier, tmp_path, signed, origin=v["origin"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "covers            : v2 h=" in done.stdout
    assert f"{len(v['p'])} part" in done.stdout
    # The vector's DELIVERED file, carrying this signature: postfix's work is equivalent.
    eml = _sig.insert_header(without_signature((D / f"{name}.eml").read_bytes()), header)
    done = verify(verifier, tmp_path, eml, origin="another.host.example")
    assert done.returncode == 0, done.stdout + done.stderr


# ── the vectors ARE what build() writes today (tasks.md 3.2) ─────────────────
# The six vectors `build` makes alone, from the same inputs as fixtures/sig_v2/make_vectors.py:
# an independent copy, so this pin does not just compare the script with itself. The atom pair is hand-built multipart and the two postfix ones are real
# probes, so neither is build()'s to reproduce.

VDATE, VSESSION = "Tue, 01 Jan 2030 12:00:00 +0000", "0a1b2c3d"
VPAGE = (b"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>t</title></head>\n"
         b"<body><p>The decision: <strong>go</strong>.</p></body></html>\n")
VMD = "The decision: **go**.\n\nSigned by the whole mail \u2014 every part.\n".encode()
ATOM_HDRS = [("Atom-Type", "decision"), ("Atom-Authority", "judy"), ("Atom-Id", "D2")]


def _vreq(sender, **kw):
    r = sender.Request(to=kw.pop("to"), from_=kw.pop("from_"), subject=kw.pop("subject"))
    for k, v in kw.items():
        setattr(r, k, v)
    r.subject = sender.rfc2047(r.subject)
    if r.original_subject:
        r.original_subject = sender.rfc2047(r.original_subject)
    r.from_, r.to = sender.encode_addr_display(r.from_), sender.encode_addr_display(r.to)
    return r


def _vbuilt(sender, r, body, n, refs=(), page=None, boundary=""):
    return sender.build(r, body, host="hq", date=VDATE,
                        message_id=f"<1901000000.{n}.1@hq.session-mail>", references=list(refs),
                        session=VSESSION, signature=None, page=page, boundary=boundary)


def _vectors_from_build(sender) -> dict[str, bytes]:
    w, sup = "acme+t-1.worker", "acme+lead"
    return {
        "single": _vbuilt(sender, _vreq(sender, from_=w, to=sup, subject="design done"),
                          b"The design is in design.md.\n", 1),
        "empty-body": _vbuilt(sender, _vreq(sender, from_=w, to=w, subject="[BOOT]"), b"", 2),
        "two-part": _vbuilt(sender, _vreq(sender, from_=f"Acme <{sup}>", to="Judy <judy>",
                                          subject="Sign the whole mail: go?"), VMD, 3,
                            page=VPAGE, boundary="=_sesh890_ffeeddccbbaa99887766554433221100"),
        "cc": _vbuilt(sender, _vreq(sender, from_=sup, to="ivan", subject="cc vector",
                                    cc=["acmecorp+ops", "Uma <uma@hq.example.net>", "Eve <eve@hq>"]),
                      b"Three on Cc: bare, qualified, short host.\n", 4),
        "reply": _vbuilt(sender, _vreq(sender, from_=sup, to="ivan", subject="Re: cc vector",
                                       in_reply_to="<1901000000.4.1@hq.session-mail>",
                                       supersedes="<1901000000.4.0@hq.session-mail>"),
                         b"A reply, superseding a draft.\n", 5,
                         refs=["<1900000000.1.1@hq.session-mail>", "<1900500000.2.1@hq.session-mail>",
                               "<1901000000.4.1@hq.session-mail>"]),
        "atom-headers": _vbuilt(sender, _vreq(
            sender, from_="J\u00e9r\u00f4me <uma>", to="acme",
            subject="D\u00e9cision: sign every part",
            original_subject="D\u00e9cision: sign every part, the long way round",
            project="acme", task="T-1", headers=ATOM_HDRS),
            b"Atom headers on a single part.\n", 6),
    }


def test_the_build_made_vectors_are_byte_for_byte_what_build_writes_today(sender):
    built = _vectors_from_build(sender)
    assert set(built) <= set(NAMES)
    for name, wire in built.items():
        assert wire == (D / f"{name}.wire").read_bytes(), name


# ── a real send, end to end ──────────────────────────────────────────────────

def test_a_real_send_round_trips_captured_and_delivered(sender, keyed, tmp_path, fake_sendmail,
                                                        verifier):
    receipt = send_req(sender, keyed, to="Judy Hopper <eve>", from_="Ivan <ivan>",
                       cc=["Uma Lee <uma>", "carol"], bcc=["foreman.sm-18"],
                       headers=[("Atom-Type", "decision"), ("Atom-Authority", "judy")])
    wire = fake_sendmail.wire
    assert receipt.message_id
    assert not any("BARE" in n for n in receipt.notices), receipt.notices
    # The wire is as `build` made it, bare, with Bcc for `sendmail -t` to strip.
    assert wire.startswith(b"From: Ivan <ivan>\nTo: Judy Hopper <eve>\nCc: Uma Lee <uma>, carol\n"
                           b"Bcc: foreman.sm-18\n")
    assert fake_sendmail.argv == ["-t", "-i", "-f", "ivan"]
    h = tags_of(wire)["h"].split(":")
    assert h[:len(_sig.FLOOR)] == list(_sig.FLOOR)
    assert {"atom-type", "atom-authority", "x-session-from", "x-machine"} <= set(h)
    assert "bcc" not in h and "content-transfer-encoding" not in h
    assert b"X-Session-From: 35bf6365\n" in wire
    done = verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr
    # As delivered: trace headers, Bcc stripped, every bare address qualified by postfix,
    # and no ORIGIN on the verifying side at all (design 4.4).
    o = TEST_ORIGIN.encode()
    landed = delivered(wire, {b"<eve>": b"<eve@" + o + b">", b"<ivan>": b"<ivan@" + o + b">",
                              b"<uma>, carol": b"<uma@" + o + b">,\n\tcarol@" + o})
    done = verify(verifier, tmp_path, landed, origin=None)
    assert done.returncode == 0, done.stdout + done.stderr
    # ...and a claim header changed after signing is BAD.
    forged = landed.replace(b"Atom-Authority: judy\n", b"Atom-Authority: eve\n")
    assert verify(verifier, tmp_path, forged, origin=None).returncode == 1


def test_a_rendered_two_part_mail_signs_both_parts(sender, keyed, tmp_path, fake_sendmail,
                                                   verifier):
    """`judy` is a human: the markdown and the rendered page, two parts, one signature."""
    rc, _, err = sender.run(["judy", "--from", "ivan", "--subject", "Two parts", "--body-file",
                             body_file(tmp_path, b"# hi\n\nsigned markdown\n")], keyed)
    assert rc == EX_OK, err
    wire = fake_sendmail.wire
    assert len(tags_of(wire)["p"].split(",")) == 2
    done = verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "part 2            : ✅ text/html" in done.stdout
    head, _, tail = wire.rpartition(b"signed markdown")            # the PAGE's copy, part 2
    done = verify(verifier, tmp_path, head + b"signed MARKDOWN" + tail)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "part 2            : ❌ ALTERED text/html" in done.stdout
    assert "part 1            : ✅ text/plain" in done.stdout


def test_the_original_subject_stays_outside_h(sender, keyed, tmp_path, fake_sendmail, verifier):
    send_req(sender, keyed, subject="sent", original_subject="SM-538 — the original")
    wire = fake_sendmail.wire
    assert b"X-Original-Subject:" in wire
    assert "x-original-subject" not in tags_of(wire)["h"].split(":")
    changed = wire.replace(b"X-Original-Subject: ", b"X-Original-Subject: changed ")  # encoded
    assert changed != wire
    assert verify(verifier, tmp_path, changed).returncode == 0          # RULED Q5


def test_every_send_door_emits_v2(sender, keyed, tmp_path, fake_sendmail):
    """`run` (the argv door) and `send` (the structured one: MCP, --json, the pager)."""
    rc, _, err = sender.run(["eve", "--from", "ivan", "--subject", "s",
                             "--body-file", body_file(tmp_path, b"b\n")], keyed)
    assert rc == EX_OK, err
    assert tags_of(fake_sendmail.wire)["v"] == "2"
    send_req(sender, keyed)
    assert tags_of(fake_sendmail.wire)["v"] == "2"
    assert b"v=1;" not in fake_sendmail.wire


# ── ORIGIN (design 4.2, 7.3) ─────────────────────────────────────────────────

def test_origin_from_the_environment(sender, keyed, tmp_path, fake_sendmail):
    keyed["SESSIONMAIL_ORIGIN"] = "env.origin.example"
    send_req(sender, keyed)
    assert b"\nTo: eve\n" in fake_sendmail.wire                         # the wire stays bare
    assert crypto_ok(fake_sendmail.wire, "env.origin.example")
    assert not crypto_ok(fake_sendmail.wire, "another.example")


def test_origin_from_postconf_myorigin(sender, keyed, tmp_path, fake_sendmail, fake_postfix):
    fake_postfix(f"eve@{HOST}", f"ivan@{HOST}")
    del keyed["SESSIONMAIL_ORIGIN"]
    keyed["FAKE_ORIGIN"] = "postconf.origin.example"
    receipt = send_req(sender, keyed)
    assert not any("BARE" in n for n in receipt.notices)
    assert crypto_ok(fake_sendmail.wire, "postconf.origin.example")


def test_no_origin_signs_bare_and_the_receipt_says_so(sender, keyed, tmp_path, fake_sendmail):
    del keyed["SESSIONMAIL_ORIGIN"]                  # no postconf on PATH; conftest empties
    receipt = send_req(sender, keyed)                # the system fallbacks
    assert [n for n in receipt.notices if "BARE address unqualified" in n]
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))  # the send still goes
    assert crypto_ok(fake_sendmail.wire, "")
    assert not crypto_ok(fake_sendmail.wire, TEST_ORIGIN)


def test_postconf_is_asked_only_when_keyed_and_a_bare_address_is_signed(
        sender, keyed, tmp_path, fake_sendmail, fake_postfix):
    log = tmp_path / "postconf.log"
    fake_postfix(f"eve@{HOST}", f"ivan@{HOST}", "eve@hq", "ivan@hq")
    del keyed["SESSIONMAIL_ORIGIN"]
    keyed["FAKE_POSTCONF_LOG"] = str(log)

    def asked() -> int:
        n = log.read_text().count("myorigin") if log.exists() else 0
        log.unlink(missing_ok=True)
        return n

    unkeyed = {k: v for k, v in keyed.items() if k != "CLAUDE_SESSION_AGENT_SOCK"}
    send_req(sender, unkeyed)
    assert asked() == 0                                                 # unkeyed: never
    send_req(sender, keyed, to="eve@hq", from_="ivan@hq")
    assert asked() == 0                                                 # nothing bare: never
    send_req(sender, keyed)
    assert asked() == 1                                                 # keyed + bare: once


# ── the SM-590 seam (design 7.1) ─────────────────────────────────────────────

def test_sign_bytes_is_none_and_never_raises_when_unkeyed_or_dead(sender, keyed, tmp_path):
    unkeyed = {k: v for k, v in keyed.items() if k != "CLAUDE_SESSION_AGENT_SOCK"}
    assert sender.sign_bytes(b"x\n", unkeyed, "ns") is None             # unkeyed
    good = sender.sign_bytes(b"x\n", keyed, "ns")
    assert good and base64.b64decode(good, validate=True)               # a BARE blob
    dead = {**keyed, "CLAUDE_SESSION_AGENT_SOCK": str(tmp_path / "no-such-socket")}
    (tmp_path / "session-key").unlink()          # or ssh-keygen falls back to the file
    assert sender.sign_bytes(b"x\n", dead, "ns") is None


def test_the_v1_line_is_still_mintable_for_tests_and_history(sender):
    assert _sig.V1_FIELDS == sender.SIG_FIELDS
    assert _sig.format_v1("ns", "B64") == (
        "X-Session-Signature: v=1; a=sshsig; n=ns; h=from:to:subject:date:message-id; b=B64")


# ── Request.unthreaded (SM-186 do-007, ruled) ──────────────────────────────────
# A superseding [ROOT] is a NEW TREE: `Supersedes:` and no `In-Reply-To:`. The
# sender threads every supersede under its target (send:1093), so `memory init
# --supersede` asks for it unthreaded. Two lines, additive: unset, nothing moves.

SUP = "<1902000000.9.9@testhost.session-mail>"


def _send_fixed(sender, env, **kw):
    req = sender.Request(**{"to": "eve", "from_": "ivan", "subject": "[ROOT]", **kw})
    sender.send(req, b"a new tree\n", env, now=1902000100, pid=42, rand=7)
    return req


def test_unthreaded_unset_leaves_a_supersede_threaded_byte_for_byte(sender, env,
                                                                     fake_sendmail):
    """Condition 2a of the ruling: unset, the wire of a supersedes-only send is
    IDENTICAL to master's. Master's validate set `in_reply_to = supersedes`, so
    master's wire is the wire of a request that names both; nothing else differs."""
    assert sender.Request(to="eve").unthreaded is False
    req = _send_fixed(sender, env, supersedes=SUP)
    threaded = fake_sendmail.wire
    assert req.in_reply_to == SUP                              # master's :391 still applies
    assert f"In-Reply-To: {SUP}".encode() in threaded
    assert b"\nReferences: " in threaded and f"Supersedes: {SUP}".encode() in threaded
    _send_fixed(sender, env, supersedes=SUP, in_reply_to=SUP)
    assert fake_sendmail.wire == threaded


def test_unthreaded_sends_supersedes_with_no_in_reply_to_and_signs_it(
        sender, keyed, fake_sendmail, tmp_path, verifier):
    """Condition 2b: set, no In-Reply-To and no References; Supersedes: present,
    in h=, and the signature good over it."""
    req = _send_fixed(sender, keyed, supersedes=SUP, unthreaded=True)
    wire = fake_sendmail.wire
    assert req.in_reply_to is None
    assert b"\nIn-Reply-To:" not in wire and b"\nReferences:" not in wire
    assert f"\nSupersedes: {SUP}\n".encode() in wire
    assert "supersedes" in tags_of(wire)["h"].split(":")
    done = verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr


def test_unthreaded_never_drops_an_in_reply_to_the_caller_named(sender, env, fake_sendmail):
    """It only declines to DERIVE one: a caller's own In-Reply-To still goes."""
    req = _send_fixed(sender, env, supersedes=SUP, in_reply_to="<p@testhost.session-mail>",
                      unthreaded=True)
    assert req.in_reply_to == "<p@testhost.session-mail>"
    assert b"In-Reply-To: <p@testhost.session-mail>" in fake_sendmail.wire


# ── SM-716 SC2: the signature covers a caller-chosen Message-ID ──────────────
# Design: store projects/sessionmail/tasks/SM-716_feat_send-msgid/design.md @ 8a4f26595,
# §7 (signing: `message-id` is in v1's SIG_FIELDS and v2's FLOOR; no signing code changes)
# and §10 do-005. README SC2: "On a keyed seat with a test key, the sent mail passes
# `message verify`. The same file with one byte of its Message-ID changed fails."
# A live host's mail is unsigned, so this is proven here, with the `keyed` fixture's scratch key.

CHOSEN = f"<x.1@{HOST}>"


def message_verify(path: Path):
    """`message verify FILE` at the click door, judged by the BUNDLED verifier (no oracle
    named), with the test ORIGIN the sender signed under."""
    from click.testing import CliRunner
    from sessionmail.message import cli
    return CliRunner().invoke(cli, ["verify", str(path)],
                              env={"SESSIONMAIL_MHS_ORACLE": None,
                                   "SESSIONMAIL_ORIGIN": TEST_ORIGIN})


def flip(wire: bytes, old: bytes, new: bytes) -> bytes:
    """`wire` with ONE byte of its Message-ID value changed, and nothing else."""
    line = f"\nMessage-ID: {CHOSEN}\n".encode()
    assert wire.count(line) == 1 and len(old) == len(new) == 1
    i = line.index(old, len(b"\nMessage-ID: "))
    changed = line[:i] + new + line[i + 1:]
    out = wire.replace(line, changed)
    assert len(out) == len(wire) and sum(a != b for a, b in zip(out, wire)) == 1
    return out


def sent_with_chosen_id(sender, keyed, tmp_path, fake_sendmail, door: str) -> bytes:
    """One keyed send carrying `--message-id`, through the argv door or the structured one."""
    if door == "argv":
        rc, out, err = sender.run(["eve", "--from", "ivan", "--subject", "Build green",
                                   "--body-file", body_file(tmp_path, b"signed body\n"),
                                   "--message-id", CHOSEN.strip("<>")], keyed)
        assert (rc, out) == (EX_OK, CHOSEN + "\n"), err
    else:
        assert send_req(sender, keyed, message_id=CHOSEN).message_id == CHOSEN
    wire = fake_sendmail.wire
    assert [v for k, v in headers_of(wire) if k.lower() == "message-id"] == [CHOSEN]
    return wire


@pytest.mark.parametrize("door", ["argv", "structured"])
def test_message_id_the_chosen_id_is_signed_and_message_verify_says_good(
        sender, keyed, tmp_path, fake_sendmail, verifier, door):
    """SC2, the good half: a keyed send with --message-id is v2-signed over `message-id`,
    and `message verify` on the file it sent exits 0."""
    wire = sent_with_chosen_id(sender, keyed, tmp_path, fake_sendmail, door)
    t = tags_of(wire)
    assert t["v"] == "2" and "message-id" in t["h"].split(":")
    assert crypto_ok(wire, TEST_ORIGIN)
    eml = tmp_path / "sent.eml"
    eml.write_bytes(wire)
    done = message_verify(eml)
    assert done.exit_code == 0, done.output
    assert "covers            : v2 h=" in done.output


@pytest.mark.parametrize("old,new", [(b"x", b"y"), (b"1", b"2"), (b"t", b"u"), (b">", b")")],
                         ids=["local-first", "local-last", "host-first", "close-bracket"])
@pytest.mark.parametrize("door", ["argv", "structured"])
def test_message_id_one_flipped_byte_of_the_chosen_id_fails_message_verify(
        sender, keyed, tmp_path, fake_sendmail, verifier, door, old, new):
    """SC2, the bad half: the SAME file with one byte of its Message-ID changed is a BAD
    signature (exit 1), not unsigned (2) or unreadable (3), at `message verify` and at the
    crypto check alike."""
    wire = sent_with_chosen_id(sender, keyed, tmp_path, fake_sendmail, door)
    eml = tmp_path / "sent.eml"
    eml.write_bytes(wire)
    assert message_verify(eml).exit_code == 0                   # the same file, good first
    eml.write_bytes(flip(wire, old, new))
    done = message_verify(eml)
    assert done.exit_code == 1, done.output
    assert not crypto_ok(eml.read_bytes(), TEST_ORIGIN)
