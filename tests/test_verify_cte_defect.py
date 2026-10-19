"""SM-819: a v2 part whose transfer encoding is not strict is refused, `reason=cte-defect`.

p= covers each leaf's CTE-DECODED bytes and Content-Transfer-Encoding is unsigned, so that an
honest re-encoding in transit still verifies. That holds only while every decoder reads the
encoding one way. Where Python (the verifier) and GMime (notmuch) read a loose encoding
differently, the verifier now refuses the mail: BAD, rc 1, `TRUST: FAIL leg=1-signature
reason=cte-defect`. The rules (`_sig.cte_defects`):

- R2: a part has at most one CTE field, and it is one plain token read from the field's
  source BYTES (no fold, comment, quotes or trailing whitespace); a multipart or message/*
  container names only 7bit, 8bit or binary;
- R3: a quoted-printable or base64 body obeys the strict grammar of RFC 2045 6.7 / 6.8;
- R1: decoding the leaf adds no defect (a backstop).

Every shape below is one the task's measurement found decoding differently in Python and in
GMime (or on 3.9 only). Real crypto: a key made for the test. Each verifier case runs under
PATH's python3 AND /usr/bin/python3 (macOS 3.9), the two interpreters the verifier meets.

Run: uv run pytest -q tests/test_verify_cte_defect.py
"""
import base64
import email.errors
import email.quoprimime
import hashlib
import inspect
import json
import os
import quopri
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs, _sender, _sig
from sessionmail._sender import qp_encode

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
NS = "session-mail@example.com"
FAIL = "TRUST: FAIL leg=1-signature reason=cte-defect"
TEXT = b"hello there\nsecond line\n"
B = base64.b64encode(TEXT).decode()                      # 32 chars, no padding
B2 = base64.b64encode(b"hello there!\n").decode()        # 20 chars, '=='
SYS_PY = "/usr/bin/python3"

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")


# ── the rig: a key, a registry, a verifier run under a chosen python3 ───────

class Rig:
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

    def sign(self, raw: bytes) -> bytes:
        """Sign `raw` as it stands: p= is whatever this python's decode gives."""
        h, p = _sig.h_for(raw), _sig.digests(raw)
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n", NS],
                              input=_sig.canonical(raw, NS, h, p, ""), capture_output=True,
                              check=True)
        b = "".join(x for x in done.stdout.decode().splitlines()
                    if x and "SSH SIGNATURE" not in x)
        return _sig.insert_header(raw, _sig.format_header(NS, h, p, b))

    def run(self, raw: bytes, python: str, *flags):
        self.n += 1
        f = self.d / f"mail{self.n}.eml"
        f.write_bytes(raw)
        shim = self.d / ("py-" + hashlib.sha256(python.encode()).hexdigest()[:8])
        if not shim.exists():
            shim.mkdir()
            (shim / "python3").symlink_to(shutil.which(python) or python)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SESSIONMAIL_ORIGIN", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_VERIFY_SELF",
                        "SESSIONMAIL_PYTHON")}
        env["SESSION_REGISTRY"] = str(self.registry)
        env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run([str(VERIFIER), str(f), *flags], capture_output=True, text=True,
                              env=env)


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return Rig(tmp_path_factory.mktemp("sm819"))


def _pythons():
    """PATH's python3, and the system one when it is a DIFFERENT file: where
    PATH's python3 is the system one, the second param would run the same
    interpreter twice and test nothing new (SM-878)."""
    out = [pytest.param("python3", id="path-python3")]
    path_py3 = shutil.which("python3")
    if os.access(SYS_PY, os.X_OK) and not (
            path_py3 and os.path.realpath(path_py3) == os.path.realpath(SYS_PY)):
        out.append(pytest.param(SYS_PY, id="usr-bin-python3"))
    return out


@pytest.fixture(params=_pythons())
def py(request):
    return request.param


def test_both_interpreters_are_really_used(rig, tmp_path):
    """The shim is what the verifier's `exec python3` finds: each param runs its own."""
    if len(_pythons()) < 2:
        why = (f"PATH's python3 is {SYS_PY} itself" if os.access(SYS_PY, os.X_OK)
               else f"there is no {SYS_PY}")
        pytest.skip(f"one interpreter here ({why}): there is no second to tell apart")
    seen = set()
    for python in [p.values[0] for p in _pythons()]:
        probe = tmp_path / "probe"
        probe.mkdir(exist_ok=True)
        (probe / "python3").unlink(missing_ok=True)
        (probe / "python3").symlink_to(shutil.which(python) or python)
        v = subprocess.run(["python3", "-c", "import sys; print(sys.version.split()[0])"],
                           capture_output=True, text=True, check=True,
                           env={**os.environ, "PATH": f"{probe}{os.pathsep}{os.environ['PATH']}"})
        seen.add(v.stdout.strip())
    assert len(seen) == len(_pythons())


# ── mail shapes ──────────────────────────────────────────────────────────────

HEAD = (b"From: ivan@example.com\nTo: uma@example.com\nSubject: s sm819\n"
        b"Date: Wed, 02 Jan 2030 08:00:00 +0000\nMessage-ID: <sm819@example.com>\n"
        b"MIME-Version: 1.0\n")


def cte_lines(ctes) -> bytes:
    if ctes is None:
        return b""
    return b"".join((b"Content-Transfer-Encoding" + v.encode() + b"\n") if v.lstrip(" \t").startswith(":")
                    else (b"Content-Transfer-Encoding:" + v.encode() + b"\n") if v.startswith("\n")
                    else f"Content-Transfer-Encoding: {v}\n".encode() for v in ctes)


def single(ctes, body: bytes) -> bytes:
    return HEAD + b"Content-Type: text/plain; charset=utf-8\n" + cte_lines(ctes) + b"\n" + body


def multi(ctes, body: bytes) -> bytes:
    if not body.endswith(b"\n"):
        body += b"\n"
    return (HEAD + b'Content-Type: multipart/mixed; boundary="=-b"\n\n'
            + b"--=-b\nContent-Type: text/plain; charset=utf-8\n" + cte_lines(ctes) + b"\n" + body
            + b"--=-b\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
            + b"second part.\n--=-b--\n")


SHAPES = {"single": single, "multi": multi}


def refused(rig, raw, python, where="part 1"):
    plain = rig.run(raw, python)
    assert plain.returncode == 1, plain.stdout + plain.stderr
    assert f"{where:<18}: ❌ CTE-DEFECT" in plain.stdout, plain.stdout
    assert "signature-crypto  : ✅ good" in plain.stdout
    assert "BAD: a transfer encoding readers may decode differently" in plain.stderr
    assert "Traceback" not in plain.stderr
    trusted = rig.run(raw, python, "--trust")
    assert (trusted.returncode, trusted.stdout.strip().splitlines()[-1]) == (1, FAIL)
    j = json.loads(rig.run(raw, python, "--json").stdout)
    assert (j["rc"], j["result"], j["reason"], j["signature"]) == (1, "bad", "cte-defect", None)


def refused_as_mime_ambiguous(rig, raw, python, cte_too=False):
    """SM-823: a mail the email package and GMime may split differently (a bare CR that moves
    what the email package reads, R2p; a header block it ends at a line it cannot read, R3) is
    refused FIRST as mime-ambiguous: its structure reads two ways, so its CTE does too. Still
    BAD, rc 1. `cte_too`: SM-819's own rule still flags the same mail."""
    assert _sig.mime_defects(raw)
    if cte_too:
        assert _sig.cte_defects(raw)
    plain = rig.run(raw, python)
    assert plain.returncode == 1, plain.stdout + plain.stderr
    assert "signature-crypto  : ✅ good" in plain.stdout
    assert "Traceback" not in plain.stderr
    trusted = rig.run(raw, python, "--trust")
    assert (trusted.returncode, trusted.stdout.strip().splitlines()[-1]) == \
        (1, "TRUST: FAIL leg=1-signature reason=mime-ambiguous")


def verified(rig, raw, python):
    plain = rig.run(raw, python)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "CTE-DEFECT" not in plain.stdout and "ALTERED" not in plain.stdout
    trusted = rig.run(raw, python, "--trust")
    assert trusted.returncode == 4, trusted.stdout + trusted.stderr


# ── every differing shape, signed as it stands (what a signer can make) ─────
# (name, CTE field values, body). One row per case the measurement found DIFFER.

DIFFERING = [
    ("qp-eq-cr", ["quoted-printable"], b"hello=\rthere\n"),
    ("qp-eqeq", ["quoted-printable"], b"hello ==41 there\n"),
    ("qp-on-base64", ["quoted-printable"], (B2 + "\n").encode()),
    ("cte-comment", ["base64 (x)"], (B + "\n").encode()),
    ("cte-ws-padded", ["base64 "], (B + "\n").encode()),
    ("cte-ws-tab", ["base64\t"], (B + "\n").encode()),
    ("cte-folded", ["\n base64"], (B + "\n").encode()),
    ("cte-space-colon", [" : base64"], (B + "\n").encode()),
    ("cte-tab-colon-second", ["8bit", "\t: base64"], (B + "\n").encode()),
    ("dup-8bit-then-b64", ["8bit", "base64"], (B + "\n").encode()),
    ("dup-qp-then-b64", ["quoted-printable", "base64"], (B + "\n").encode()),
    ("dup-b64-then-qp", ["base64", "quoted-printable"], (B + "\n").encode()),
    ("b64-trunc-1", ["base64"], (B[:16] + "a\n").encode()),
    ("b64-trunc-2", ["base64"], (B2[:18] + "\n").encode()),
    ("b64-trunc-3", ["base64"], (base64.b64encode(b"hello there\n!!").decode()[:19] + "\n").encode()),
    ("b64-after-pad", ["base64"], (B2 + B + "\n").encode()),
    ("b64-after-pad-nl", ["base64"], (B2 + "\n" + B + "\n").encode()),
    ("b64-garbage-tail", ["base64"], (B + "\n-- \nplain tail\n").encode()),
    ("b64-mid-eq", ["base64"], (B[:6] + "=" + B[6:] + "\n").encode()),
    ("b64-concat", ["base64"], b"aGk=dGhlcmU=\n"),
    ("b64-urlsafe", ["base64"], b"aGVsbG8-_8_\n"),
]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("name,ctes,body", DIFFERING, ids=[c[0] for c in DIFFERING])
def test_every_differing_shape_is_refused(rig, py, shape, name, ctes, body):
    raw = rig.sign(SHAPES[shape](ctes, body))
    if name in SM823_R3_FIRST:
        refused_as_mime_ambiguous(rig, raw, py, cte_too=True)
    else:
        refused(rig, raw, py)


#: SM-823 R3: the email package reads these CTE fields as BODY (whitespace before the colon
#: ends its header block), so the mail is refused first as mime-ambiguous.
SM823_R3_FIRST = {"cte-space-colon", "cte-tab-colon-second", "rfc822-base64-encoded"}


# ── the relay shapes: an HONEST signed mail, re-encoded with NO key ─────────

def _honest(body: bytes, shape="single") -> bytes:
    return SHAPES[shape](["8bit"], body)


def _reencode(signed: bytes, cte: str, body: bytes, orig: bytes, shape="single") -> bytes:
    """Swap the signed 8bit leaf `orig` for `body` under CTE `cte`, with NO key: the
    signature header is untouched (CTE is never in h=)."""
    if shape == "single":
        head, _, rest = signed.partition(b"\n\n")
        assert rest == orig
        return head.replace(b"Content-Transfer-Encoding: 8bit\n",
                            f"Content-Transfer-Encoding: {cte}\n".encode(), 1) + b"\n\n" + body
    old = b"Content-Transfer-Encoding: 8bit\n\n" + orig
    assert signed.count(old) == 1
    if not body.endswith(b"\n"):
        body += b"\n"
    return signed.replace(old, f"Content-Transfer-Encoding: {cte}\n\n".encode() + body)


HONEST = b"hello there\nsecond line\n"
PADDED = b"hello there!\n"                      # 13 octets: base64 ends '=='


@pytest.mark.parametrize("shape", SHAPES)
def test_a_relay_injected_qp_line_is_refused(rig, py, shape):
    signed = rig.sign(_honest(HONEST, shape))
    relayed = _reencode(signed, "quoted-printable",
                       b"hello there\n=\rinjected line\nsecond line\n", HONEST, shape)
    assert _sig.digests(relayed) == _sig.digests(signed)     # Python reads the honest text
    refused(rig, relayed, py)


@pytest.mark.parametrize("shape", SHAPES)
def test_a_relay_stripped_base64_padding_is_refused(rig, py, shape):
    signed = rig.sign(_honest(PADDED, shape))
    relayed = _reencode(signed, "base64", base64.b64encode(PADDED).rstrip(b"=") + b"\n",
                       PADDED, shape)
    assert _sig.digests(relayed) == _sig.digests(signed)
    refused(rig, relayed, py)


@pytest.mark.parametrize("shape", SHAPES)
def test_a_relay_second_base64_block_is_refused(rig, py, shape):
    signed = rig.sign(_honest(PADDED, shape))
    relayed = _reencode(signed, "base64",
                       base64.b64encode(PADDED) + base64.b64encode(b"more\n") + b"\n",
                       PADDED, shape)
    refused(rig, relayed, py)


# ── containers (the GO's condition 2) ───────────────────────────────────────

INNER = (b"--=-b\nContent-Type: text/plain; charset=utf-8\n\nhello =41 there\n"
         b"--=-b\nContent-Type: text/plain; charset=utf-8\n\n" + TEXT + b"--=-b--\n")
RFC822 = (b"From: uma@example.com\nSubject: inner\nContent-Type: text/plain; charset=utf-8\n\n"
          b"hello =41 there\n" + TEXT)


def _multipart_with(cte, body):
    return (HEAD + b'Content-Type: multipart/mixed; boundary="=-b"\n'
            + f"Content-Transfer-Encoding: {cte}\n\n".encode() + body)


def _rfc822_with(cte, body):
    return (HEAD + b'Content-Type: multipart/mixed; boundary="=-b"\n\n'
            b"--=-b\nContent-Type: text/plain; charset=utf-8\n\nfirst part.\n"
            b"--=-b\nContent-Type: message/rfc822\n" + cte_lines([cte] if cte else None)
            + b"\n" + body + b"--=-b--\n")


CONTAINERS = [
    ("multipart-base64", lambda: _multipart_with("base64", INNER), "container multipart/mixed"),
    ("multipart-qp", lambda: _multipart_with("quoted-printable", INNER),
     "container multipart/mixed"),
    ("multipart-base64-encoded",
     lambda: _multipart_with("base64", base64.encodebytes(INNER)), "part 1"),
    ("multipart-qp-encoded",
     lambda: _multipart_with("quoted-printable", quopri.encodestring(INNER)), "part 1"),
    ("rfc822-base64", lambda: _rfc822_with("base64", RFC822), "container message/rfc822"),
    ("rfc822-qp", lambda: _rfc822_with("quoted-printable", RFC822), "container message/rfc822"),
    ("rfc822-base64-encoded", lambda: _rfc822_with("base64", base64.encodebytes(RFC822)),
     "container message/rfc822"),
    ("rfc822-qp-encoded", lambda: _rfc822_with("quoted-printable", quopri.encodestring(RFC822)),
     "container message/rfc822"),
]


@pytest.mark.parametrize("name,make,where", CONTAINERS, ids=[c[0] for c in CONTAINERS])
def test_a_container_that_declares_an_encoding_is_refused(rig, py, name, make, where):
    if name in SM823_R3_FIRST:      # base64 text read as the inner message's header block
        refused_as_mime_ambiguous(rig, rig.sign(make()), py, cte_too=True)
    else:
        refused(rig, rig.sign(make()), py, where)


@pytest.mark.parametrize("cte", [None, "7bit", "8bit", "binary"])
def test_a_container_with_an_identity_encoding_still_verifies(rig, py, cte):
    verified(rig, rig.sign(_rfc822_with(cte, RFC822)), py)
    if cte:
        verified(rig, rig.sign(_multipart_with(cte, INNER)), py)


# ── the honest-transport controls (condition 3): they stay VERIFIED ─────────

LONG = ("Long line " + "x" * 150 + " end=end\ttab\n" + "a " * 80 + "\n"
        "café ☃ with a trailing space \nsecond paragraph, short.\n").encode("utf-8")


def _qp_body_encode(data: bytes) -> bytes:
    return email.quoprimime.body_encode(data.decode("latin-1")).encode("latin-1") + b"\n"


def _quopri_pure(data: bytes) -> bytes:
    """quopri's own Python encoder (used where binascii has no b2a_qp): its '=20=' line
    ends make the 77-78 character lines the GO names."""
    keep, quopri.b2a_qp = quopri.b2a_qp, None
    try:
        return quopri.encodestring(data)
    finally:
        quopri.b2a_qp = keep


ENCODERS = {
    "quoprimime.body_encode": ("quoted-printable", _qp_body_encode),
    "quopri.encodestring": ("quoted-printable", quopri.encodestring),
    "quopri-python-encoder": ("quoted-printable", _quopri_pure),
    "base64-76": ("base64", base64.encodebytes),
    "sm818-qp_encode": ("quoted-printable", qp_encode),
}


def _crlf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


@pytest.mark.parametrize("crlf", [False, True], ids=["lf", "crlf"])
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("encoder", ENCODERS)
def test_an_honest_reencoding_in_transit_still_verifies(rig, py, encoder, shape, crlf):
    signed = rig.sign(_honest(LONG, shape))
    cte, enc = ENCODERS[encoder]
    moved = _reencode(signed, cte, enc(LONG), LONG, shape)
    assert _sig.cte_defects(moved) == []
    if encoder == "quopri-python-encoder":     # its 77-78 character lines, as the GO says
        assert max(len(x) for x in enc(LONG).split(b"\n")) in (77, 78)
    verified(rig, _crlf(moved) if crlf else moved, py)


def test_quopris_python_encoder_can_split_an_escape_and_that_never_verified(rig, py):
    """The one honest-encoder output R3 refuses, and why it is not a lost control: quopri's
    Python encoder can break a line INSIDE an escape ('=C=' LF '3'), which Python's own
    decoder cannot read back, so the digest never matched (ALTERED at the base, 0539fd2).
    Now it is named as what it is. The C encoder (quopri.encodestring here) never does it."""
    text = ("ab é " * 40 + "\n").encode("utf-8")
    enc = _quopri_pure(text)
    assert b"=C=\n3" in enc and quopri.decodestring(enc) != text
    signed = rig.sign(_honest(text))
    refused(rig, _reencode(signed, "quoted-printable", enc, text), py)


def test_the_crlf_control_really_has_crlf(rig):
    raw = _crlf(rig.sign(_honest(LONG)))
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


@pytest.mark.parametrize("pad", [" ", "\t", " \t "])
def test_whitespace_after_a_soft_break_is_not_a_cte_defect(rig, py, pad):
    """RFC 2045 6.7 (3): a transport may pad a soft break's '=' before the line end. R3 lets
    it through. Python's decoder (and GMime, measured) then keep the '=' and the padding as
    TEXT, so the honest digest no longer matches: ALTERED, at the base and on the fix alike.
    The text a reader shows really differs from what was signed; this fix does not touch it."""
    signed = rig.sign(_honest(LONG))
    enc = quopri.encodestring(LONG)
    assert b"=\n" in enc
    moved = _reencode(signed, "quoted-printable", enc.replace(b"=\n", f"={pad}\n".encode()), LONG)
    assert _sig.cte_defects(moved) == []
    plain = rig.run(moved, py)
    assert "CTE-DEFECT" not in plain.stdout and "cte-defect" not in plain.stderr
    assert (plain.returncode, "❌ ALTERED" in plain.stdout) == (1, True), plain.stdout


def test_whitespace_before_a_soft_break_still_verifies(rig, py):
    """Trailing SP/TAB on an encoded line BEFORE its '=': both decoders keep it as text."""
    text = b"hello there \t\nsecond line\n"
    signed = rig.sign(_honest(text))
    verified(rig, _reencode(signed, "quoted-printable", b"hello there \t=\n=0Asecond line\n",
                            text), py)


# ── condition 1: no CTE field is 7bit, and stays VERIFIED ───────────────────

@pytest.mark.parametrize("shape", SHAPES)
def test_a_signed_part_with_no_cte_field_still_verifies(rig, py, shape):
    raw = rig.sign(SHAPES[shape](None, b"hello there\nsecond line\n"))
    assert b"content-transfer-encoding" not in raw.lower().split(b"--=-b")[1 if shape == "multi"
                                                                          else 0]
    verified(rig, raw, py)


@pytest.mark.parametrize("value", ["base64", "BASE64", "Quoted-Printable", "8BIT", "7bit",
                                   "binary"])
def test_one_plain_token_in_any_case_still_verifies(rig, py, value):
    body = {"base64": (B + "\n").encode(), "quoted-printable": b"hello =41 there\n"}.get(
        value.lower(), b"hello there\n")
    verified(rig, rig.sign(single([value], body)), py)


# ── R2 reads the field's BYTES, whatever this python's parse hides ──────────

def test_a_folded_cte_is_seen_although_this_python_may_unfold_it():
    raw = single(["\n base64"], (B + "\n").encode())
    assert _sig.cte_defects(raw) == [
        ("part 1", "Content-Transfer-Encoding is not one plain token: "
                   "'Content-Transfer-Encoding:\\n base64'")]


def test_a_cte_field_with_space_before_its_colon_is_seen():
    """RFC 5322 obs-optional: `Content-Transfer-Encoding : base64`. Python's parser ends the
    header block there and keeps the line as BODY (a parse defect, so R1 never sees it);
    GMime reads it as the field and decodes base64 (measured, corpus cte-space-colon)."""
    raw = single([" : base64"], (B + "\n").encode())
    assert _sig.leaves(raw)[0].get_payload(decode=True).startswith(b"Content-Transfer-Encoding :")
    assert _sig.cte_defects(raw) == [
        ("part 1", "a Content-Transfer-Encoding field this parser reads as body")]


def test_a_body_line_that_looks_like_a_cte_field_is_not_one():
    """Only the lines Python pushed out of the HEADER block count: in a body it is text."""
    assert _sig.cte_defects(single(["8bit"], b"hello\nContent-Transfer-Encoding : base64\n")) == []


# A header line the email package cannot read (no colon, a space or a non-ASCII byte in the
# name) ends its header block there: every line after it, a well-formed CTE field included,
# is BODY to Python. A GMime reader skips the bad line and reads the field (measured on a
# scratch notmuch, 3.13 and 3.9). R2 counts any CTE field among those lines.
MALFORMED = {"no-colon": b"X no colon here", "space-in-name": b"X Y: z",
             "non-ascii-name": "X-é: y".encode("utf-8")}


def _after_malformed(shape, bad: bytes) -> bytes:
    raw = SHAPES[shape](["base64"], (B + "\n").encode())
    assert raw.count(b"Content-Transfer-Encoding: base64\n") == 1
    return raw.replace(b"Content-Transfer-Encoding: base64\n",
                       bad + b"\nContent-Transfer-Encoding: base64\n")


def _signed_on_top(rig, raw: bytes) -> bytes:
    signed = rig.sign(raw)    # its header lands at the blank line, past the bad line: on top
    end = raw.find(b"\n\n") + 1
    return signed[end:end + len(signed) - len(raw)] + raw


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("bad", MALFORMED, ids=list(MALFORMED))
def test_a_cte_field_after_an_unreadable_header_line_is_refused(rig, py, shape, bad):
    raw = _after_malformed(shape, MALFORMED[bad])
    assert _sig.leaves(raw)[0].get_payload(decode=True).startswith(MALFORMED[bad] + b"\n")
    refused_as_mime_ambiguous(rig, _signed_on_top(rig, raw), py, cte_too=True)


@pytest.mark.parametrize("shape", SHAPES)
def test_an_unreadable_header_line_with_no_cte_field_after_it_is_refused(rig, py, shape):
    """A DELIBERATE CONTRACT INVERSION (SM-823 R3). SM-819 (70a184c) pinned that this mail still verified: no CTE field is among the
    lines the email package reads as body, so it is no cte-defect, and it still is not. But
    GMime skips the unreadable line and reads on to the blank one, so it hides lines p=
    signed, and deleting the blank line after a part's headers makes the shape with no key.
    It is now BAD, mime-ambiguous. The measured false-positive basis: 0 of 68 honest files
    and 0 of 60 repo fixtures carry the defect (tests/test_verify_mime_ambiguous.py)."""
    raw = SHAPES[shape](["8bit"], b"hello there\n")
    cte = b"Content-Transfer-Encoding: 8bit\n"
    raw = raw.replace(cte, cte + b"X no colon here\nX-A: b\n", 1)
    assert any(isinstance(d, email.errors.MissingHeaderBodySeparatorDefect)
               for d in _sig.leaves(raw)[0].defects)
    assert _sig.cte_defects(raw) == []
    refused_as_mime_ambiguous(rig, _signed_on_top(rig, raw), py)


# Only an EMPTY line ends a header block. GMime reads a line of spaces or tabs as a fold and
# reads on to the CTE field (measured on a scratch notmuch, 3.13 and 3.9).
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("gap", [b"   ", b"\t"], ids=["spaces", "tab"])
def test_a_line_of_whitespace_does_not_end_the_lines_the_parser_read_as_body(rig, py, shape,
                                                                              gap):
    bad = MALFORMED["no-colon"] + b"\n" + gap
    raw = _after_malformed(shape, bad)
    assert _sig.leaves(raw)[0].get_payload(decode=True).startswith(bad + b"\n")
    assert _sig.cte_defects(raw) == [
        ("part 1", "a Content-Transfer-Encoding field this parser reads as body")]
    refused_as_mime_ambiguous(rig, _signed_on_top(rig, raw), py)


# The email package ends a line at a bare CR; GMime reads the CR inside the line. A CTE
# field written after one is read and decoded by Python and never seen by GMime, which shows
# the encoded text (measured on a scratch notmuch, 3.13 and 3.9). Whatever Python makes of
# the line before (a field, a fold, a line it drops, a boundary), the field is refused.
# Multipart only: a bare CR in the TOP header block is already refused as undecodable (SM-787).
CTE_B64 = b"Content-Transfer-Encoding: base64\n"
BEFORE_CR = {
    "after-a-field": (b"charset=utf-8\n" + CTE_B64, b"charset=utf-8\r" + CTE_B64),
    "after-a-fold": (CTE_B64, b"X-A: y\n z\r" + CTE_B64),
    "after-an-empty-name": (CTE_B64, b":x\r" + CTE_B64),
    "after-a-misplaced-from": (CTE_B64, b"From x\r" + CTE_B64),
    "after-a-first-line-fold": (b"--=-b\nContent-Type: text/plain; charset=utf-8\n" + CTE_B64,
                                b"--=-b\n x\r" + CTE_B64 + b"Content-Type: text/plain\n"),
    "after-a-boundary": (b"--=-b\nContent-Type: text/plain; charset=utf-8\n" + CTE_B64,
                         b"--=-b\r" + CTE_B64 + b"Content-Type: text/plain\n"),
}


@pytest.mark.parametrize("where", BEFORE_CR, ids=list(BEFORE_CR))
def test_a_cte_field_after_a_bare_cr_is_refused(rig, py, where):
    old, new = BEFORE_CR[where]
    raw = multi(["base64"], (B + "\n").encode())
    assert raw.count(old) == 1
    raw = raw.replace(old, new)
    assert _sig.leaves(raw)[0].get_payload(decode=True) == TEXT     # Python reads the field
    assert ("the mail", "a Content-Transfer-Encoding field after a bare CR") in \
        _sig.cte_defects(raw)
    refused_as_mime_ambiguous(rig, rig.sign(raw), py)


def test_a_bare_cr_alone_is_not_a_cte_defect():
    assert _sig.cte_defects(multi(["8bit"], b"hello\rthere\n")) == []
    assert _sig.cte_defects(multi(["8bit"], b"hello\r Content-Transfer-Encoding: x\n")) == []
    assert _sig.cte_defects(multi(["8bit"], b"hello\rContent-Transfer-Encoding: x\n")) == []


def test_a_cte_field_after_a_bare_cr_in_an_inner_message_is_refused(rig, py):
    inner = b"Subject: inner\rContent-Transfer-Encoding: base64\n\n" + (B + "\n").encode()
    raw = _rfc822_with(None, inner)
    assert _sig.leaves(raw)[1].get_payload(decode=True) == TEXT     # Python reads the field
    assert ("the mail", "a Content-Transfer-Encoding field after a bare CR") in \
        _sig.cte_defects(raw)
    refused_as_mime_ambiguous(rig, rig.sign(raw), py)


# A bare CR in a BODY is body text to Python and to GMime alike (measured on a scratch
# notmuch, 3.13 and 3.9), and an honest send passes a body's bare CRs through untouched
# (`_sender.check_body`, `_leaf_cte`). So only a field the email package reads in a HEADER
# block counts; the same bytes in a body still verify.
CR_BODIES = {
    "quoting-headers-with-cr-line-ends": b"forwarded headers, old Mac line ends:\rContent-Type: "
                                         b"text/plain\rContent-Transfer-Encoding: 8bit\r\rhi\n",
    "one-bare-cr-then-the-field-name": b"see below\rContent-Transfer-Encoding: base64 is what "
                                       b"the relay wrote\n",
    "the-body-starts-with-it": b"\rContent-Transfer-Encoding: base64\n\nnot a header\n",
    "after-an-lf-line": b"hello\n\rcontent-transfer-encoding:quoted-printable\nthere\n",
}


def _sent(body: bytes, shape: str, headers=()) -> bytes:
    """What an honest send writes: the sender's own `build`, unchanged."""
    page = b"<p>page</p>\n" if shape == "multi" else None
    req = _sender.Request(to="uma@example.com", from_="ivan@example.com", subject="s sm819",
                          headers=list(headers))
    return _sender.build(req, body, host="h", date="Thu, 03 Jan 2030 12:00:00 +0000",
                         message_id="<sm819-cr@h.session-mail>", references=[], session="",
                         signature=None, page=page,
                         boundary=("=_sesh890_" + "0" * 32) if page else "")


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("body", CR_BODIES, ids=list(CR_BODIES))
def test_an_honest_body_with_a_bare_cr_before_a_cte_field_name_still_verifies(rig, py, shape,
                                                                              body):
    data = CR_BODIES[body]
    _sender.check_body(data)                              # the sender refuses none of them
    assert _sender._leaf_cte(data)[0] == "8bit"
    wire = _sent(data, shape)
    assert _sig.CR_CTE.search(_sig.lf(wire))              # the bare-CR pattern IS there
    assert len(_sig.leaves(wire)) == (2 if shape == "multi" else 1)
    assert _sig.leaves(wire)[0].get_payload(decode=True) == data    # ...as body
    assert _sig.cte_defects(wire) == []
    verified(rig, rig.sign(wire), py)


CR_MAILS = {**{w: multi(["base64"], (B + "\n").encode()).replace(*BEFORE_CR[w])
                for w in BEFORE_CR},
            **{"body-" + w: _sent(CR_BODIES[w], "multi") for w in CR_BODIES}}


@pytest.mark.parametrize("name", CR_MAILS, ids=list(CR_MAILS))
def test_the_bare_cr_probe_changes_only_the_field_names(name):
    """The probe renames each match's field NAME at the same length, and the parse keeps
    its shape (leaf count and types), so where a probe lands is where the field was."""
    mail = CR_MAILS[name]
    probed = _sig._cr_probed(mail)
    names = [range(m.start() + 1, m.start() + 26) for m in _sig.CR_CTE.finditer(mail)]
    changed = [i for i in range(len(mail)) if mail[i] != probed[i]]
    assert len(probed) == len(mail) and changed
    assert all(any(i in r for r in names) for i in changed)
    assert _sig.leaf_types(probed) == _sig.leaf_types(mail)


# r5 N1: the probe renames a match to `X-Sm819-Cr-Probe-NNNNNNNN`, a name an honest sender
# may also write (`message send --header`). Such a field is not a CTE field: only the probes
# the un-probed parse lacks count. Collide is r5's own case (evidence/fix-001-r5/collide.out).
COLLIDE = {"probe-0": [("X-Sm819-Cr-Probe-00000000", "y")],
           "probe-0-lowercase": [("x-sm819-cr-probe-00000000", "y")],
           "probe-1": [("X-Sm819-Cr-Probe-00000001", "y")]}
COLLIDE_BODY = CR_BODIES["one-bare-cr-then-the-field-name"]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("hdrs", COLLIDE, ids=list(COLLIDE))
def test_an_honest_field_named_like_the_probe_still_verifies(rig, py, shape, hdrs):
    wire = _sent(COLLIDE_BODY, shape, COLLIDE[hdrs])
    name = COLLIDE[hdrs][0][0].encode()
    assert name + b": y\n" in wire                         # the honest field IS there
    assert _sig.CR_CTE.search(_sig.lf(wire))              # ...beside a body bare CR
    assert _sig.cte_defects(wire) == []
    verified(rig, rig.sign(wire), py)


@pytest.mark.parametrize("where", BEFORE_CR, ids=list(BEFORE_CR))
def test_a_probe_named_field_does_not_hide_a_cte_field_after_a_bare_cr(rig, py, where):
    """The other way round: the same honest field beside a real bare-CR CTE field."""
    raw = multi(["base64"], (B + "\n").encode()).replace(*BEFORE_CR[where])
    raw = raw.replace(b"Subject: ", b"X-Sm819-Cr-Probe-00000000: y\nSubject: ", 1)
    assert raw.count(b"X-Sm819-Cr-Probe-00000000: y\n") == 1
    assert ("the mail", "a Content-Transfer-Encoding field after a bare CR") in \
        _sig.cte_defects(raw)
    refused_as_mime_ambiguous(rig, rig.sign(raw), py)


@pytest.mark.parametrize("ctes,why", [
    (["base64", "base64"], "2 Content-Transfer-Encoding fields"),
    (["x-foo"], "unknown Content-Transfer-Encoding 'x-foo'"),
    (['"base64"'], "not one plain token"),
    ([""], "not one plain token"),
])
def test_r2_names_why(ctes, why):
    [(where, got)] = _sig.cte_defects(single(ctes, (B + "\n").encode()))
    assert where == "part 1" and why in got


def test_cte_defects_is_the_same_under_the_system_python():
    """The function itself, loaded BY PATH under /usr/bin/python3, as the verifier loads it."""
    if not os.access(SYS_PY, os.X_OK):
        pytest.skip("no /usr/bin/python3")
    cases = [single(c, b) for _, c, b in DIFFERING] + [m() for _, m, _ in CONTAINERS]
    prog = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('s',sys.argv[1]);m=importlib.util.module_from_spec(s)\n"
            "s.loader.exec_module(m)\n"
            "print(json.dumps([m.cte_defects(bytes.fromhex(x)) for x in json.load(sys.stdin)]))\n")
    done = subprocess.run([SYS_PY, "-I", "-c", prog, inspect.getsourcefile(_sig)],
                          input=json.dumps([c.hex() for c in cases]), capture_output=True,
                          text=True, check=True)
    theirs = json.loads(done.stdout)
    ours = [[list(x) for x in _sig.cte_defects(c)] for c in cases]
    assert theirs == ours and all(theirs)


def test_the_bare_cr_rule_is_the_same_under_the_system_python():
    """Refused and honest bare-CR mails, single and multipart, through `_sig` under 3.9."""
    if not os.access(SYS_PY, os.X_OK):
        pytest.skip("no /usr/bin/python3")
    collide = [_sent(COLLIDE_BODY, shape, h) for h in COLLIDE.values() for shape in SHAPES]
    hidden = [multi(["base64"], (B + "\n").encode()).replace(*BEFORE_CR[w]).replace(
        b"Subject: ", b"X-Sm819-Cr-Probe-00000000: y\nSubject: ", 1) for w in BEFORE_CR]
    cases = (list(CR_MAILS.values()) + [_sent(b, "single") for b in CR_BODIES.values()]
             + collide + hidden)
    prog = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('s',sys.argv[1]);m=importlib.util.module_from_spec(s)\n"
            "s.loader.exec_module(m)\n"
            "print(json.dumps([m.cte_defects(bytes.fromhex(x)) for x in json.load(sys.stdin)]))\n")
    done = subprocess.run([SYS_PY, "-I", "-c", prog, inspect.getsourcefile(_sig)],
                          input=json.dumps([c.hex() for c in cases]), capture_output=True,
                          text=True, check=True)
    theirs = json.loads(done.stdout)
    assert theirs == [[list(x) for x in _sig.cte_defects(c)] for c in cases]
    assert [bool(t) for t in theirs] == ([w in BEFORE_CR for w in CR_MAILS]
                                         + [False] * (len(CR_BODIES) + len(collide))
                                         + [True] * len(hidden))


# ── what p= covers did not change ───────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures" / "sig_v2"
#: sha256 over (name, sha256(bytes)) of every file in tests/fixtures/sig_v2/, as make_vectors.py
#: mints them under the TEST key (the generator and the key's .pub are in the directory too).
#: The key's PRIVATE half is left out: the release export does not ship it (.gitattributes),
#: and tests/test_sig_v2_key_export.py pins it in a checkout.
FIXTURES_AT_BASE = "62c14b1fc2026969dc6a51398ce41ef26215d0767958429da7f2ce91266e9d5e"
PRIVATE_KEY = "sessionmail-sig-TEST-VECTORS-ONLY"
#: sha256 of the source of lf, leaves, leaf_canon and digests, at 0539fd2.
CANON_AT_BASE = "ba66f8a35a3e3e5cbf76213da6631a7d71ec33d94fa2ef5e99b7145321bc5674"


def test_the_golden_vectors_are_untouched():
    h = hashlib.sha256()
    for f in sorted(FIXTURES.iterdir()):
        if f.name == PRIVATE_KEY:
            continue
        h.update(f.name.encode() + b"\0" + hashlib.sha256(f.read_bytes()).digest())
    assert h.hexdigest() == FIXTURES_AT_BASE


def test_digests_and_leaf_canon_are_byte_identical_and_never_call_the_check(monkeypatch):
    src = "".join(inspect.getsource(getattr(_sig, n)) for n in ("lf", "leaves", "leaf_canon",
                                                                 "digests"))
    assert hashlib.sha256(src.encode()).hexdigest() == CANON_AT_BASE

    def boom(raw):
        raise AssertionError("digests() must not call cte_defects()")

    monkeypatch.setattr(_sig, "cte_defects", boom)
    manifest = json.loads((FIXTURES / "vectors.json").read_text())["vectors"]
    for name, v in manifest.items():
        assert _sig.digests((FIXTURES / f"{name}.eml").read_bytes()) == v["p"]


def test_every_golden_vector_is_strict():
    for f in sorted(FIXTURES.glob("*.eml")):
        assert _sig.cte_defects(f.read_bytes()) == [], f.name


def test_a_traceback_in_the_check_fails_closed(rig, tmp_path):
    """A broken check is a refusal with a TRUST line, never a traceback."""
    lone = tmp_path / "mhs"
    lone.mkdir()
    shutil.copy(VERIFIER, lone / "session-mail-verify")
    sig = Path(inspect.getsourcefile(_sig)).read_text()
    (tmp_path / "_sig.py").write_text(
        sig + "\n\ndef cte_defects(raw):\n    raise RuntimeError('broken')\n")
    raw = rig.sign(single(["8bit"], b"hello there\n"))
    f = tmp_path / "m.eml"
    f.write_bytes(raw)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SESSIONMAIL_ORIGIN", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_VERIFY_SELF",
                        "SESSIONMAIL_PYTHON")}
    env["SESSION_REGISTRY"] = str(rig.registry)
    done = subprocess.run([str(lone / "session-mail-verify"), str(f), "--trust"],
                          capture_output=True, text=True, env=env)
    assert (done.returncode, done.stdout.strip().splitlines()[-1]) == (1, FAIL)
    assert "Traceback" not in done.stderr and "cannot be checked (RuntimeError)" in done.stderr
