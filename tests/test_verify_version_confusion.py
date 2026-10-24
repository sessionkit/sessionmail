"""SM-787: session-mail-verify reads the signature header with ONE parser, _sig's byte parse.

The version used to be chosen from a TEXT parse (Unicode whitespace stripped, the last v=
winning) while v2 parsed BYTES. So a captured, honestly signed v1 mail could carry a header
that a byte reader takes for a complete v2 with any h= (sessionvoice EARS-335), and the
verifier still passed it as v1: rc 0 plain, rc 4 UNBLESSED under --trust. Every case here is
REAL crypto: a key made for the test, a registry cert so --trust reaches leg 3, and the
shipped verifier run as it ships. Each forged case rewrites a captured honest mail's header
WITHOUT the key. The rows are findings/repro.md's (SM-787 task store), at 7407dfb.

Run: uv run pytest -q tests/test_verify_version_confusion.py
"""
import ast
import base64
import email
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs, _sig

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
NS = "session-mail@example.com"          # MAIL_NS_DEFAULT, SESSION_MAIL_SIG_NS unset
V1_H = "from:to:subject:date:message-id"
WIDE = ("from:to:cc:subject:date:message-id:in-reply-to:references:supersedes:mime-version:"
        "content-type:x-session-from:x-mouth-device:x-mouth-priority:x-mouth-when:x-mouth-extra")
ASK = "X-Mouth-Device: call:1317\nX-Mouth-Priority: now\n"
AMBIGUOUS = "TRUST: FAIL leg=1-signature reason=sig-header-ambiguous"
UNICODE_SPACES = {"U+00A0": "\u00a0", "U+0085": "\u0085", "U+2028": "\u2028",
                  "U+3000": "\u3000", "U+2003": "\u2003", "U+FEFF": "\ufeff"}

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")


def _reference_canonical():
    """The verifier's own v1 canonical(), lifted from its heredoc, to SIGN with."""
    text = VERIFIER.read_text()
    head = "<<'PYEOF'\n"
    src = text[text.index(head) + len(head):text.index("\nPYEOF\n")]
    keep = [n for n in ast.parse(src).body
            if (isinstance(n, ast.FunctionDef) and n.name in ("local_part", "canonical"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "HDRS" for t in n.targets))]
    ns = {"sys": sys, "email": email}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(VERIFIER), "exec"), ns)
    return ns["canonical"]


CANON = _reference_canonical()


class Rig:
    """A scratch key and a registry whose cert names it `agent: ivan`."""

    def __init__(self, d: Path):
        self.d = d
        self.key = d / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)],
                       check=True)
        kt, kb = (d / "key.pub").read_text().split()[:2]
        h = hashlib.sha256(base64.b64decode(kb)).digest()[:16].hex()
        sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        self.registry = d / "registry"
        (self.registry / "certs").mkdir(parents=True)
        (self.registry / "certs" / f"{sid}.cert").write_text(
            f"session-birth-v1\nsession-id: {sid}\npubkey: {kt} {kb}\nagent: ivan\n")
        self.n = 0

    def sign(self, data: bytes) -> str:
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n", NS],
                              input=data, capture_output=True, check=True)
        return "".join(x for x in done.stdout.decode().splitlines() if x and "SSH SIGNATURE" not in x)

    def v1(self, body: str = "hello there\n") -> str:
        text = base("v1", body)
        b = self.sign(CANON(email.message_from_string(text)))
        return insert(text, f"X-Session-Signature: v=1; a=sshsig; n={NS}; h={V1_H}; b={b}")

    def v2(self, body: bytes = b"hello there\n") -> bytes:
        raw = base("v2", "").encode() + body
        h, p = _sig.h_for(raw), _sig.digests(raw)
        b = self.sign(_sig.canonical(raw, NS, h, p, ""))
        return _sig.insert_header(raw, _sig.format_header(NS, h, p, b))

    def run(self, raw, *flags):
        self.n += 1
        f = self.d / f"mail{self.n}.eml"
        f.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        env = {k: v for k, v in os.environ.items()
               if k not in ("SESSIONMAIL_ORIGIN", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_VERIFY_SELF",
                        "SESSIONMAIL_PYTHON")}
        env["SESSION_REGISTRY"] = str(self.registry)
        return subprocess.run([str(VERIFIER), str(f), *flags], capture_output=True, text=True,
                              env=env)


def base(mid: str, body: str) -> str:
    return (f"From: ivan@example.com\nTo: uma@example.com\nSubject: s {mid}\n"
            f"Date: Sun, 09 Jun 2030 12:00:00 +0000\nMessage-ID: <{mid}@example.com>\n"
            f"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n\n{body}")


def insert(text: str, line: str) -> str:
    head, _, body = text.partition("\n\n")
    return head + "\n" + line + "\n\n" + body


def sigh(text: str) -> str:
    return re.search(r"^X-Session-Signature: ([^\n]*(?:\n[ \t][^\n]*)*)\n", text, re.M).group(1)


def b_of(text: str) -> str:
    return re.search(r"b=([A-Za-z0-9+/=\s]+)", sigh(text)).group(1).replace("\n", "").replace(" ", "")


def resig(text: str, value: str) -> str:
    return text.replace("X-Session-Signature: " + sigh(text), "X-Session-Signature: " + value, 1)


def last(done) -> str:
    return done.stdout.strip().splitlines()[-1]


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return Rig(tmp_path_factory.mktemp("sm787"))


def refused_ambiguous(rig, raw, why):
    """Plain rc 1 naming why; --trust rc 1, one TRUST: line, the named token; never v1 GOOD."""
    plain = rig.run(raw)
    assert plain.returncode == 1, plain.stdout + plain.stderr
    assert "signature header cannot be read one way" in plain.stderr and why in plain.stderr
    assert "signature-header  : ❌ AMBIGUOUS: " in plain.stdout
    assert "covers" not in plain.stdout and "Traceback" not in plain.stderr
    trusted = rig.run(raw, "--trust")
    assert (trusted.returncode, last(trusted)) == (1, AMBIGUOUS)
    quiet = rig.run(raw, "--trust", "--quiet")
    assert quiet.stdout == AMBIGUOUS + "\n"


# ── the controls: honest mail still verifies ────────────────────────────────

def test_an_honest_v1_still_verifies(rig):
    raw = rig.v1()
    plain = rig.run(raw)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "covers            : v1 " in plain.stdout
    assert (rig.run(raw, "--trust").returncode, last(rig.run(raw, "--trust"))) == \
        (4, "TRUST: UNBLESSED reason=bless-absent")
    assert rig.run(raw.replace("hello there", "hello THERE")).returncode == 1


def test_an_honest_v2_still_verifies(rig):
    raw = rig.v2()
    plain = rig.run(raw)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "covers            : v2 " in plain.stdout
    trusted = rig.run(raw, "--trust")
    assert (trusted.returncode, last(trusted)) == (4, "TRUST: UNBLESSED reason=bless-absent")


def test_a_v1_header_folded_or_respaced_still_verifies(rig):
    """The byte parse drops ASCII whitespace inside a value, so transit refolding is benign."""
    raw = rig.v1()
    b = b_of(raw)
    folded = resig(raw, f"v=1;  a=sshsig ; n={NS};\n h={V1_H};\n\tb={b[:50]}\n {b[50:]}")
    assert rig.run(folded).returncode == 0
    assert rig.run(resig(raw, f"a=sshsig; n={NS}; h={V1_H}; b={b}")).returncode == 0   # no v=: v1


def test_v1_now_reads_tag_values_as_v2_does_whitespace_removed(rig):
    """A CHANGE, named: v1's n= is read by the byte parse, which removes ALL whitespace
    inside a value (design 3.2, as v2 always has). At 7407dfb the text parse kept the space
    in `n= NS` and ssh-keygen refused the namespace (rc 1); now it is NS. The signature is
    still checked in the namespace that was signed, so this verifies nothing new."""
    raw = rig.v1()
    assert rig.run(resig(raw, f"v=1; a=sshsig; n= {NS}; h={V1_H}; b={b_of(raw)}")).returncode == 0
    assert rig.run(resig(raw, f"v=1; a=sshsig; n=x{NS}; h={V1_H}; b={b_of(raw)}")).returncode == 1


# ── (a) the EARS-335 shape ──────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(UNICODE_SPACES))
def test_the_ears_335_shape_is_refused(rig, name):
    """v=2 ... h=<anything>; b=<the v1 b>; <a Unicode space>v=1, on a captured v1 mail: the
    text parse read v=1 (rc 0 / rc 4), a byte reader reads v=2. Now refused."""
    v1 = rig.v1()
    forged = resig(v1, f"v=2; a=sshsig; n={NS}; h={WIDE}; b={b_of(v1)}; {UNICODE_SPACES[name]}v=1")
    refused_ambiguous(rig, insert(forged, ASK.rstrip("\n")), "non-ASCII")


def test_the_ears_335_shape_with_a_well_formed_p_is_refused(rig):
    """Repro A7: with p= (the honest digests, no key needed) _sig.parse_v2 takes the whole
    header as v2, h= naming x-mouth-device. The verifier passed it as v1. Now refused."""
    v1 = rig.v1()
    p = ",".join(_sig.digests(v1.encode()))
    forged = resig(v1, f"v=2; a=sshsig; n={NS}; h={WIDE}; p={p}; b={b_of(v1)}; \u00a0v=1")
    assert _sig.parse_v2(_sig.parse_header(_sig.signature_value(forged.encode())))[1][-4] == "x-mouth-device"
    refused_ambiguous(rig, forged, "non-ASCII")


# ── (b) a repeated v=, agreeing or not ──────────────────────────────────────

@pytest.mark.parametrize("shape", ["v=1 ... v=1", "v=2 ... v=1", "v=1 ... v=2"])
def test_a_repeated_version_on_a_v1_is_refused(rig, shape):
    v1 = rig.v1()
    first, _, then = shape.partition(" ... ")
    refused_ambiguous(rig, resig(v1, f"{first}; a=sshsig; n={NS}; h={V1_H}; b={b_of(v1)}; {then}"),
                      "tag v= appears twice")


@pytest.mark.parametrize("tail", ["; v=1", "; v=2"])
def test_a_repeated_version_on_a_v2_is_refused(rig, tail):
    v2 = rig.v2().decode()
    refused_ambiguous(rig, resig(v2, sigh(v2) + tail), "tag v= appears twice")


def test_any_repeated_tag_or_a_segment_without_equals_is_refused(rig):
    """The text parse kept the last copy of a tag and dropped a segment with no '='."""
    v1 = rig.v1()
    b = b_of(v1)
    refused_ambiguous(rig, resig(v1, f"v=1; a=sshsig; n={NS}; n=other@example.com; h={V1_H}; b={b}"),
                      "tag n= appears twice")
    refused_ambiguous(rig, resig(v1, f"v=1; junk; a=sshsig; n={NS}; h={V1_H}; b={b}"),
                      "not a tag=value")


# ── (c) non-ASCII in the header, anywhere ───────────────────────────────────

@pytest.mark.parametrize("where", ["between tags", "before b=", "inside b=", "inside v="])
def test_non_ascii_whitespace_in_a_v1_header_is_refused(rig, where):
    v1 = rig.v1()
    b = b_of(v1)
    value = {
        "between tags": f"v=1;\u00a0a=sshsig; n={NS}; h={V1_H}; b={b}",
        "before b=": f"v=1; a=sshsig; n={NS}; h={V1_H};\u3000b={b}",
        "inside b=": f"v=1; a=sshsig; n={NS}; h={V1_H}; b={b[:40]}\u2028{b[40:]}",
        "inside v=": f"v=\u00a01; a=sshsig; n={NS}; h={V1_H}; b={b}",
    }[where]
    refused_ambiguous(rig, resig(v1, value), "non-ASCII")


def test_non_ascii_in_a_v2_header_is_refused_as_ambiguous(rig):
    v2 = rig.v2().decode()
    refused_ambiguous(rig, v2.replace("X-Session-Signature: v=2; ", "X-Session-Signature: v=2;\u00a0", 1),
                      "non-ASCII")


# ── the field itself: a bare CR (rca-001) ───────────────────────────────────

def test_a_bare_cr_cannot_make_the_two_parsers_read_different_fields(rig):
    """All ASCII, no repeated tag. Universal newlines made the bare CR a line end, so the
    TEXT parse found the real v1 field first, while the bytes' first field is a forged v2.
    fix-001 let the bytes decide (v2, crypto fails, rc 1 bad-signature). fix-002 review:
    a bare CR in the header block is itself refused, before any field is read."""
    v1 = rig.v1().encode()
    real = b"X-Session-Signature: " + sigh(v1.decode()).encode()
    p = ",".join(_sig.digests(v1)).encode()
    forged = (b"X-Session-Signature: v=2; a=sshsig; n=" + NS.encode() + b"; h=" + WIDE.encode()
              + b"; p=" + p + b"; b=" + b_of(v1.decode()).encode())
    raw = v1.replace(real + b"\n", b"X-Note: x\r" + real + b"\n" + forged + b"\nX-Mouth-Device: call:1317\n")
    assert _sig.parse_header(_sig.signature_value(raw))["v"] == "2"
    refused_ambiguous(rig, raw, "bare CR")


@pytest.mark.parametrize("where", ["before the real field", "after the real field"])
def test_a_field_hidden_behind_a_bare_cr_is_refused(rig, where):
    """fix-002 review: _sig splits fields on LF only, so a forged v2 field after a bare CR is
    INSIDE another field to it, and the byte count sees ONE X-Session-Signature. Python's
    email parser (every policy) and universal newlines end the line at the CR: to them it
    is a second field, and placed before the real one it is their FIRST. At 375c5aa the
    verifier passed it as v1 (rc 0 / rc 4 UNBLESSED). Now the bare CR is refused."""
    v1 = rig.v1()
    real = "X-Session-Signature: " + sigh(v1)
    hidden = "X-Note: x\r" + _forged_v2_field(v1)
    raw = v1.replace(real, hidden + "\n" + real if where.startswith("before") else real + "\n" + hidden)
    rb = raw.encode()
    assert sum(1 for n, _ in _sig.fields(rb) if n == "x-session-signature") == 1
    seen = email.message_from_bytes(rb).get_all("X-Session-Signature")
    assert len(seen) == 2 and seen[0].startswith("v=2" if where.startswith("before") else "v=1")
    refused_ambiguous(rig, raw, "bare CR")


def test_crlf_mail_is_not_a_bare_cr(rig):
    """Controls: CRLF line ends are not bare CRs, so honest v1 and v2 sent with CRLF still
    verify; and the rule reads the HEADER block only, so a v2 signed over a bare CR in its
    body still verifies (v2 signs bytes)."""
    assert rig.run(rig.v1().replace("\n", "\r\n")).returncode == 0
    assert rig.run(rig.v2().replace(b"\n", b"\r\n")).returncode == 0
    body_cr = rig.v2(b"hello\rthere\n")
    done = rig.run(body_cr)
    assert done.returncode == 0 and "AMBIGUOUS" not in done.stdout, done.stdout + done.stderr


# ── F2: more than one X-Session-Signature field ────────────────────────────

def _forged_v2_field(v1: str) -> str:
    """A complete, parse_v2-valid v2 field made from a captured v1 without the key."""
    p = ",".join(_sig.digests(v1.encode()))
    return f"X-Session-Signature: v=2; a=sshsig; n={NS}; h={WIDE}; p={p}; b={b_of(v1)}"


@pytest.mark.parametrize("order", ["v1 then v2", "v2 then v1"])
def test_a_v1_with_a_second_v2_field_is_refused(rig, order):
    """F2: a relay ADDS a v2 field to a captured v1. The first field alone verified (v1,
    rc 0 / rc 4), and a reader of the last or of every field saw v2. Now refused."""
    v1 = rig.v1()
    real = "X-Session-Signature: " + sigh(v1)
    two = real + "\n" + _forged_v2_field(v1) if order == "v1 then v2" else _forged_v2_field(v1) + "\n" + real
    refused_ambiguous(rig, insert(v1.replace(real + "\n", ""), two), "carries 2 X-Session-Signature fields")


def test_two_identical_fields_are_refused(rig):
    """Even a byte-identical copy: the rule is the count, not whether the copies agree."""
    v1 = rig.v1()
    refused_ambiguous(rig, insert(v1, "X-Session-Signature: " + sigh(v1)),
                      "carries 2 X-Session-Signature fields")
    v2 = rig.v2().decode()
    field = "X-Session-Signature:" + _sig.fields(v2.encode())[-1][1].decode()
    assert _sig.fields(v2.encode())[-1][0] == "x-session-signature"
    refused_ambiguous(rig, insert(v2, field), "carries 2 X-Session-Signature fields")


def test_an_empty_second_field_still_counts(rig):
    v1 = rig.v1()
    refused_ambiguous(rig, insert(v1, "X-Session-Signature:"), "carries 2 X-Session-Signature fields")
    refused_ambiguous(rig, insert(v1, "x-session-signature: v=1"), "carries 2 X-Session-Signature fields")


def test_one_field_folded_over_lines_is_still_one_field(rig):
    """The count is of FIELDS, as _sig.fields() reads them: continuation lines are not new ones."""
    v2 = rig.v2()
    assert sum(1 for n, _ in _sig.fields(v2) if n == "x-session-signature") == 1
    assert b"\n " in v2.split(b"X-Session-Signature:", 1)[1].split(b"\n\n", 1)[0]   # it IS folded
    assert rig.run(v2).returncode == 0


# ── a byte that does not decode: never a traceback ──────────────────────────

def test_a_v1_mail_that_does_not_decode_is_refused_with_a_trust_line(rig):
    """Repro D6: open().read() raised UnicodeDecodeError (not an OSError), so it escaped as a
    traceback: rc 1, and NO TRUST: line under --trust, against the one-line invariant."""
    raw = rig.v1().encode().replace(b"hello there", b"hello th\xffere")
    plain = rig.run(raw)
    assert plain.returncode == 1, plain.stdout + plain.stderr
    assert "Traceback" not in plain.stderr and "a v1 mail must decode as text" in plain.stderr
    trusted = rig.run(raw, "--trust", "--quiet")
    assert (trusted.returncode, trusted.stdout) == (1, "TRUST: FAIL leg=1-signature reason=undecodable\n")


def test_a_v2_mail_that_does_not_decode_still_verifies(rig):
    """v2 verifies BYTES: a byte the locale cannot decode must not turn it red (ruling 3)."""
    raw = rig.v2(b"hello th\xffere\n")
    assert b"\xff" in raw
    plain = rig.run(raw)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    trusted = rig.run(raw, "--trust")
    assert (trusted.returncode, last(trusted)) == (4, "TRUST: UNBLESSED reason=bless-absent")
    assert rig.run(raw.replace(b"th\xffere", b"th\xfeere")).returncode == 1   # and still checked


def test_a_non_ascii_byte_in_the_signature_header_is_ambiguous_not_a_traceback(rig):
    raw = rig.v1().encode().replace(b"; b=", b"; x=\xff; b=", 1)
    refused_ambiguous(rig, raw, "non-ASCII")
