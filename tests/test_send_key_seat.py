"""SM-802: a keyed send whose From is not the SEAT its key names.

Before this, `message send` signed any From with any key, under
$SESSIONMAIL_REQUIRE_SIGNATURE too (repro-001, at f9723c6: exit 0, a v=2
signature, From another seat). The contract now is ruling
(b'): the sender compares the SEAT IDENTITY.

- the key's principal is its comment less a trailing ` seat`;
- both sides are reduced by `_sender.canon_addr` (the signed reduction: the
  local part, lowercased, its `+extension` KEPT); the domain is not compared;
- only an address-shaped principal is compared: it holds `+` or `@`, or the
  key is a `<x> seat` key. A bare keyholder agent name (`worker`) is not;
- a `proj seat` key matches From `proj` only, never `proj+*`;
- under must-sign: a mismatch is REFUSED, kind identity, ONE line naming the
  key's principal, the From asked for, and the fix; the MTA never runs;
- without must-sign: the send signs as-is and says so on stderr (one line);
- a key naming no seat is not compared: one note under must-sign, silence
  without; a From that is this session's own id (a DM reply) is not compared.

This is NOT the receiver's `--trust` leg 2, but since SM-803 the two reduce
alike (canon_addr here, the verifier's local_part() there: SPECIFIED EQUAL,
pinned at the foot). The table at the foot pins where they agree, and the one
row they still differ on: a bare agent name, which the sender does not compare.

Hermetic: throwaway keys in throwaway agents, a fake sendmail, a scratch
registry. Fixture names (eve, alpha, proj, keyseat.alpha) are not seats.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from test_sender import (EX_OK, EX_REFUSED, bin_dir, body_file, env, fake_sendmail,  # noqa: F401
                         headers_of, keyed, root, sender, verifier, fake_postfix)
from test_mcp_send import SEND, call, real_child, texts  # noqa: F401

VAR = "SESSIONMAIL_REQUIRE_SIGNATURE"
SID = "35bf6365-6686-d084-55b4-1507b25b65a2"     # test_sender's keyed CLAUDE_CODE_SESSION_ID
REAL_PATH = os.environ.get("PATH", "")


def name_key(env, comment: str) -> None:
    """Rewrite the session pub's comment. The agent signs by the key blob,
    so the comment is the only thing that moves."""
    pub = Path(env["CLAUDE_SESSION_PUB"])
    kt, kb = pub.read_text().split()[:2]
    pub.write_text(f"{kt} {kb}" + (f" {comment}" if comment else "") + "\n")


def send_as(sender, env, tmp_path, frm: str, *, body=b"hello\n"):
    argv = ["beta", "--from", frm, "--subject", "SM-802", "--body-file", body_file(tmp_path, body)]
    return sender.run(argv, env)


def refusal_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if "NOTHING WAS SENT" in line]


def warnings(err: str) -> list[str]:
    """The mismatch warning, never the "names no seat" note."""
    return [line for line in err.splitlines()
            if "the session key names" in line and "names no seat" not in line]


# ── under must-sign: REFUSED, one line, the MTA never runs ───────────────────

REFUSED_ROWS = [
    ("keyseat.alpha seat", "eve"),                     # seat-init shape, another seat
    ("proj+t-2.worker", "proj+t-1.worker"),            # keyholder plus seat, another task's seat
    ("eve@testhost", "alpha"),                         # qualified keyholder principal, another seat
    ("proj seat", "proj+t-1.worker"),                  # a project key is not every proj+* seat
    ("keyseat.alpha seat", "keyseat.alpha+memory"),    # the +extension is part of the seat
    ("proj+t-1.worker seat", "proj"),                  # nor is a plus seat its base
]
REFUSED_IDS = ["seat-init-other", "plus-principal-other", "qualified-principal-other",
               "project-key-vs-plus-from", "tagged-from", "plus-seat-vs-base"]


@pytest.mark.parametrize("comment, frm", REFUSED_ROWS, ids=REFUSED_IDS)
def test_must_sign_refuses_a_from_the_key_does_not_name(sender, keyed, tmp_path, fake_sendmail,
                                                         comment, frm):
    name_key(keyed, comment)
    keyed[VAR] = "1"
    rc, out, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_REFUSED and out == ""
    assert fake_sendmail.argv is None and fake_sendmail.wire is None, "nothing reached the MTA"
    said = refusal_lines(err)
    assert len(said) == 1 and len(err.strip().splitlines()) == 1, err
    principal = comment.removesuffix(" seat")
    line = said[0]
    assert f"${VAR} is set" in line
    assert f"names {principal!r}" in line, "the key's principal"
    assert f"From is {frm!r}" in line, "the From asked for"
    assert f"TM_AGENT_ADDRESS={principal}" in line and "`message seat init`" in line, "the fix"


def test_the_refusal_is_kind_identity_sent_no(sender, keyed, fake_sendmail):
    name_key(keyed, "keyseat.alpha seat")
    keyed[VAR] = "1"
    with pytest.raises(sender.Refused) as refused:
        sender.send(sender.Request(to="beta", from_="eve", subject="s"), b"x\n", keyed)
    assert refused.value.kind == "identity" and refused.value.sent == "no"
    assert refused.value.code == EX_REFUSED
    assert fake_sendmail.argv is None


def test_must_sign_refuses_before_the_mta_check_and_preflight(sender, keyed, tmp_path, fake_postfix):
    """Knowable at once: neither a missing MTA nor an unmapped recipient speaks first."""
    fake_postfix()
    name_key(keyed, "keyseat.alpha seat")
    keyed[VAR] = "1"
    keyed["SESSIONMAIL_SENDMAIL"] = str(tmp_path / "no-such-sendmail")
    rc, _, err = send_as(sender, keyed, tmp_path, "eve")
    assert rc == EX_REFUSED and "names 'keyseat.alpha'" in err and "no MTA" not in err


# ── under must-sign: a From the key DOES name is sent, signed, silent ────────

SENT_ROWS = [
    ("eve seat", "eve"),                                           # seat-init shape
    ("Eve seat", "Eve <EVE@testhost.example>"),                    # case, display name, domain
    ("proj seat", "proj"),                                         # a project key, its own box
    ("proj+t-1.worker", "proj+t-1.worker"),                        # keyholder plus seat (leg 2 passes it too: SM-803)
    ("proj+t-1.worker seat", "Worker <Proj+T-1.Worker@testhost>"),  # seat-init plus seat, case
    ("eve@testhost", "eve"),                                       # the domain is not compared (SM-606)
    ("eve@testhost", "eve@otherhost.example"),                     # nor on both sides
]
SENT_IDS = ["seat-init", "case-name-domain", "project-key-own-box", "plus-principal",
            "seat-init-plus", "qualified-principal", "qualified-both-sides"]


@pytest.mark.parametrize("comment, frm", SENT_ROWS, ids=SENT_IDS)
def test_must_sign_sends_a_from_the_key_names(sender, keyed, tmp_path, fake_sendmail, comment, frm):
    name_key(keyed, comment)
    keyed[VAR] = "1"
    rc, out, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    assert warnings(err) == [] and "not checked" not in err, err


# ── without must-sign: signs as-is, one warning line ─────────────────────────

@pytest.mark.parametrize("comment, frm", REFUSED_ROWS, ids=REFUSED_IDS)
def test_without_must_sign_a_mismatch_signs_as_is_and_warns_once(sender, keyed, tmp_path,
                                                                 fake_sendmail, comment, frm):
    name_key(keyed, comment)
    rc, _, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire)), "signed as-is"
    said = warnings(err)
    assert len(said) == 1, err
    principal = comment.removesuffix(" seat")
    assert f"names {principal!r}" in said[0] and f"From is {frm!r}" in said[0]
    assert "signing as-is" in said[0] and f"${VAR}" in said[0]
    assert "NOTHING WAS SENT" not in err


@pytest.mark.parametrize("comment, frm", SENT_ROWS, ids=SENT_IDS)
def test_without_must_sign_a_match_says_nothing(sender, keyed, tmp_path, fake_sendmail, comment, frm):
    name_key(keyed, comment)
    rc, _, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_OK and warnings(err) == [] and "not checked" not in err, err


# ── a key that names no seat: not compared ───────────────────────────────────

NO_SEAT_ROWS = [
    ("", "eve"),                                # no comment
    ("my laptop key", "eve"),                   # not one token
    ("worker", "proj+t-1.worker"),              # the keyholder's default: a bare agent name
    ("otherseat.beta", "eve"),                  # a bare keyholder name with no + or @
    ("eve@a@b", "eve"),                         # address-shaped, but canon_addr cannot reduce it
    ("@testhost", "eve"),                       # address-shaped, an empty local part
]
NO_SEAT_IDS = ["no-comment", "not-one-token", "bare-agent-name-vs-full-from", "bare-keyholder-name",
               "unparseable", "empty-local-part"]


@pytest.mark.parametrize("comment, frm", NO_SEAT_ROWS, ids=NO_SEAT_IDS)
def test_a_key_naming_no_seat_is_not_compared_one_note_under_must_sign(sender, keyed, tmp_path,
                                                                     fake_sendmail, comment, frm):
    name_key(keyed, comment)
    keyed[VAR] = "1"
    rc, _, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    notes = [line for line in err.splitlines() if "names no seat" in line]
    assert len(notes) == 1 and f"{frm!r}" in notes[0] and "not checked" in notes[0], err
    assert warnings(err) == [] and "NOTHING WAS SENT" not in err


@pytest.mark.parametrize("comment, frm", NO_SEAT_ROWS, ids=NO_SEAT_IDS)
def test_a_key_naming_no_seat_without_must_sign_is_silent(sender, keyed, tmp_path, fake_sendmail,
                                                          comment, frm):
    name_key(keyed, comment)
    rc, _, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_OK and "names no seat" not in err and warnings(err) == []


def test_the_principal_is_the_comment_less_a_trailing_seat(sender, tmp_path):
    pub = tmp_path / "k.pub"
    for comment, want in [("desk.uma seat", ("desk.uma", True)), ("desk.uma", ("desk.uma", False)),
                          ("seat", ("seat", False)), ("a seat seat", (None, False)),
                          ("", (None, False)), ("two words", (None, False))]:
        pub.write_text("ssh-ed25519 AAAA" + (f" {comment}" if comment else "") + "\n")
        principal, seat_shape = want
        assert sender.key_principal({"CLAUDE_SESSION_PUB": str(pub)}) == (principal, comment, seat_shape)
    assert sender.key_principal({"CLAUDE_SESSION_PUB": str(tmp_path / "gone.pub")}) == (None, "", False)


# ── a session-id From (a DM reply): not compared ─────────────────────────────

def test_a_from_that_is_this_sessions_id_is_not_compared(sender, keyed, tmp_path, fake_sendmail):
    """`message._reply_from` answers a DM from the session id. That is not a
    seat, so it is not compared with the key's seat (a receiver's --trust
    leg 2 would FAIL it, agent-mismatch: the test after this one)."""
    assert keyed["CLAUDE_CODE_SESSION_ID"] == SID
    name_key(keyed, "keyseat.alpha seat")
    keyed[VAR] = "1"
    rc, _, err = send_as(sender, keyed, tmp_path, SID)
    assert rc == EX_OK, err
    assert warnings(err) == []


def test_another_sessions_id_is_compared(sender, keyed, tmp_path, fake_sendmail):
    name_key(keyed, "keyseat.alpha seat")
    keyed[VAR] = "1"
    rc, _, err = send_as(sender, keyed, tmp_path, "00000000-0000-0000-0000-000000000000")
    assert rc == EX_REFUSED and len(refusal_lines(err)) == 1


# ── one reducer: the sender's own canon_addr, no second normaliser ───────────

def test_both_sides_go_through_canon_addr(sender, keyed, monkeypatch):
    """The seat check reduces the principal AND the From with `canon_addr`,
    the reduction the signature itself carries. No new normaliser."""
    seen = []
    real = sender.canon_addr

    def spy(value):
        seen.append(value)
        return real(value)

    monkeypatch.setattr(sender, "canon_addr", spy)
    name_key(keyed, "proj+t-1.worker seat")
    sender.check_key_seat("Worker <proj+t-1.worker@testhost>", keyed, True, [])
    assert "proj+t-1.worker" in seen and "proj+t-1.worker@testhost" in seen, seen
    source = Path(sender.__file__).read_text()
    assert "def local_part(" not in source and "leg2_reduction" not in source, "a second normaliser"


# ── the sender and the receiver's --trust leg 2: where they agree, and differ ─

def write_cert(registry: Path, pub: Path, agent: str) -> None:
    kt, kb = pub.read_text().split()[:2]
    h = hashlib.sha256(base64.b64decode(kb)).digest()[:16].hex()
    sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    (registry / "certs").mkdir(parents=True, exist_ok=True)
    (registry / "certs" / f"{sid}.cert").write_text(
        f"session-birth-v1\nsession-id: {sid}\npubkey: {kt} {kb}\nagent: {agent}\n")


def leg2_says(sender_env, wire: bytes, tmp_path: Path, verifier, agent: str) -> str:
    """Run the REAL verifier --trust over the sent wire, with a scratch registry
    cert whose agent is the key's principal (a keyholder key's comment IS its
    cert's agent; a seat-init cert's agent is the seat address)."""
    registry = tmp_path / "registry"
    write_cert(registry, Path(sender_env["CLAUDE_SESSION_PUB"]), agent)
    eml = tmp_path / "sent.eml"
    eml.write_bytes(wire)
    done = subprocess.run([str(verifier), str(eml), "--trust"], capture_output=True, text=True,
                          env={**os.environ, "SESSION_REGISTRY": str(registry)})
    said = done.stdout + done.stderr
    assert "signature-crypto  : ✅ good" in said, said
    return said


#: (comment, From, the sender flags it, leg 2 flags it). MEASURED against the
#: real verifier. Until SM-803, leg 2 stripped the From's +tag and compared the
#: cert agent unreduced (f9723c6's session-mail-verify:398-404), and the two
#: disagreed on rows 3-5. SM-803 reduces both sides of leg 2 with local_part(),
#: the twin of canon_addr, so rows 3 and 5 now AGREE. Row 4 (the domain) STAYS
#: DIFFERENT, on purpose: leg 2 refuses a cert agent carrying '@' (SM-606).
DIVERGENCE = [
    # They agree, as they always did.
    ("eve seat", "eve", False, False),
    ("keyseat.alpha seat", "eve", True, True),
    # A keyed plus seat: the key comment proj+t-1.worker and From
    # proj+t-1.worker are the SAME seat. Both pass it (leg 2 FAILed it
    # before SM-803).
    ("proj+t-1.worker", "proj+t-1.worker", False, False),
    # STILL DIFFERENT, on purpose, in the SAFE direction (SM-606): the sender's
    # canon_addr drops the domain and does not flag 'eve@testhost' as 'eve';
    # leg 2 refuses any cert agent carrying '@' (ruled, NO
    # LOOSENING): a cert naming one host's address must not pass
    # for that local part From any host. The receiver is the stricter side.
    # It FAILed before SM-803 too. SM-606 owns whether a domain is compared.
    ("eve@testhost", "eve", False, True),
    # The +extension is part of the seat on both sides: a project key is not
    # every proj+* seat. Both flag it (leg 2 passed it before SM-803).
    ("proj seat", "proj+t-1.worker", True, True),
    # STILL DIFFERENT, on purpose: a bare keyholder agent name is not
    # address-shaped, so the sender does not compare it; leg 2 compares
    # every cert agent and FAILs 'worker' against the seat proj+t-1.worker.
    ("worker", "proj+t-1.worker", False, True),
]


@pytest.mark.parametrize("comment, frm, sender_flags, leg2_flags", DIVERGENCE,
                         ids=["agree-match", "agree-mismatch", "SM-803-plus-seat", "SM-606-domain-receiver-stricter",
                              "sender-stricter-tag", "bare-agent-name"])
def test_where_the_sender_and_leg_2_disagree(sender, keyed, tmp_path, fake_sendmail, verifier,
                                             comment, frm, sender_flags, leg2_flags):
    """Send WITHOUT must-sign (it signs as-is), then ask the real verifier."""
    name_key(keyed, comment)
    rc, _, err = send_as(sender, keyed, tmp_path, frm)
    assert rc == EX_OK, err
    assert bool(warnings(err)) is sender_flags, err
    said = leg2_says(keyed, fake_sendmail.wire, tmp_path, verifier, comment.removesuffix(" seat"))
    assert ("reason=agent-mismatch" in said) is leg2_flags, said


def verifier_local_part(verifier):
    """session-mail-verify runs at import, so its local_part() is lifted from
    the source text (as tests/mhs/test_named_from_readers.py does)."""
    src = Path(verifier).read_text()
    start = src.index("def local_part(")
    ns: dict = {}
    exec(src[start:src.index("\ndef ", start + 1)], ns)
    return ns["local_part"]


@pytest.mark.parametrize("comment, frm, _s, _l", DIVERGENCE,
                         ids=["agree-match", "agree-mismatch", "SM-803-plus-seat", "SM-606-domain-receiver-stricter",
                              "sender-stricter-tag", "bare-agent-name"])
def test_canon_addr_and_leg_2s_local_part_are_one_reduction(sender, verifier, comment, frm, _s, _l):
    """SPECIFIED EQUAL (SM-803): the sender's canon_addr and the verifier's
    local_part() are the same reduction (local part, lowercased, +extension
    KEPT, domain dropped), written twice only because the verifier is a
    standalone script. Pinned on both sides of every row above, so neither
    can drift from the other."""
    local_part = verifier_local_part(verifier)
    for value in (comment.removesuffix(" seat"), frm, SID):
        assert sender.canon_addr(value) == local_part(value), value


def test_the_receiver_fails_a_session_id_from_the_sender_lets_it_go(sender, keyed, tmp_path,
                                                                     fake_sendmail, verifier):
    """A DM reply's From is the session id, which no cert agent equals: the
    sender deliberately does not compare it; leg 2 FAILs it."""
    name_key(keyed, "keyseat.alpha seat")
    rc, _, err = send_as(sender, keyed, tmp_path, SID)
    assert rc == EX_OK and warnings(err) == []
    said = leg2_says(keyed, fake_sendmail.wire, tmp_path, verifier, "keyseat.alpha")
    assert "reason=agent-mismatch" in said


# ── the other doors: the --json CLI and the MCP tool (the CLI in a child) ────

@pytest.fixture
def keyholder(monkeypatch):
    """A throwaway key in a throwaway agent, exported as a keyed seat is;
    `keyholder(comment)` names it. Never a live key."""
    tools = {t: shutil.which(t, path=REAL_PATH) for t in ("ssh-keygen", "ssh-agent", "ssh-add")}
    for tool, where in tools.items():
        if where is None:
            pytest.skip(f"{tool} not on this machine - the keyed path cannot be exercised")
    d = tempfile.mkdtemp(prefix="sm802-", dir="/tmp")
    key = os.path.join(d, "k")
    pids = []

    def make(comment: str) -> None:
        subprocess.run([tools["ssh-keygen"], "-t", "ed25519", "-N", "", "-f", key, "-C", comment],
                       check=True, capture_output=True)
        out = subprocess.run([tools["ssh-agent"], "-s", "-a", os.path.join(d, "s")],
                             check=True, capture_output=True, text=True).stdout
        sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", out).group(1)
        pids.append(re.search(r"SSH_AGENT_PID=(\d+);", out).group(1))
        subprocess.run([tools["ssh-add"], key], check=True, capture_output=True,
                       env={**os.environ, "SSH_AUTH_SOCK": sock})
        os.unlink(key)
        monkeypatch.setenv("CLAUDE_SESSION_AGENT_SOCK", sock)
        monkeypatch.setenv("CLAUDE_SESSION_PUB", key + ".pub")
        monkeypatch.setenv("PATH", os.environ["PATH"] + os.pathsep
                           + os.path.dirname(tools["ssh-keygen"]))

    yield make
    for pid in pids:
        try:
            os.kill(int(pid), 15)
        except ProcessLookupError:
            pass
    shutil.rmtree(d, ignore_errors=True)


def test_the_mcp_door_refuses_a_mismatch_under_must_sign(real_child, keyholder, monkeypatch):
    """`message_send` runs the CLI in a child (_mcp.SEND_COMMAND) that inherits
    this environment, and signs at the same site: the same refusal."""
    keyholder("keyseat.alpha seat")                  # real_child's seat is `alpha`
    monkeypatch.setenv(VAR, "1")
    result, _ = call({**SEND, "to": ["beta"]})
    text = "\n".join(texts(result))
    assert result.is_error is True, text
    assert text.startswith("not sent: identity: "), text
    assert "names 'keyseat.alpha'" in text and "From is 'alpha'" in text
    assert real_child.wire() is None, "the MTA was handed a mail"


def test_the_mcp_door_sends_a_match_under_must_sign(real_child, keyholder, monkeypatch):
    keyholder("alpha seat")
    monkeypatch.setenv(VAR, "1")
    result, _ = call({**SEND, "to": ["beta"]})
    assert result.is_error is False, texts(result)
    assert b"X-Session-Signature" in real_child.wire()


def test_the_json_door_refuses_with_kind_identity(real_child, keyholder, monkeypatch):
    from test_message_send_json import run as json_send
    keyholder("keyseat.alpha seat")
    monkeypatch.setenv(VAR, "1")
    body = Path(os.environ["HOME"]) / "body.md"
    body.write_text("dear beta\n")
    r, said = json_send("beta", "-s", "the point", "-f", str(body), "--json")
    assert r.exit_code == EX_REFUSED
    assert said["sent"] == "no" and said["kind"] == "identity"
    assert "names 'keyseat.alpha'" in said["message"] and "From is 'alpha'" in said["message"]
    assert real_child.wire() is None
