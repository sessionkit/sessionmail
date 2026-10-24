"""SM-827: `_sig.signed_text`, the body a GOOD vouches for.

memory cat / tree -b / --boot, memory run and the wake shelf decided trust on the verifier
(Python's email parse, `_sig.leaves()`), but took the body they showed and RAN from notmuch's
JSON (GMime's parse). A parse differential let a keyless edit change what a GOOD item showed
or ran. `signed_text` is the one reading they now share: the FIRST text/plain leaf of the
verifier's own `leaves()`, its bytes exactly as p= hashes them (`leaf_data`), read in its
signed charset. For v2 it re-hashes that leaf against its p= entry in the reader's own
process; for v1 it needs the v1 shape (`v1_defects == []`).

These tests are pure (no verifier, no notmuch): the crypto is the verifier's job, and
`signed_text` never claims it. Mails here carry a v2 header with a placeholder `b=`, which
`signed_text` reads for `p=` only. The end-to-end readers are in
tests/test_memory_signed_body.py. The last test runs the same readings under PATH's python3
AND /usr/bin/python3 (macOS 3.9), the two interpreters the readers meet.

Run: uv run pytest -q tests/test_signed_text.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _sender, _sig

HERE = Path(__file__).parent
GOLDEN = HERE / "fixtures" / "sig_v2"
MANIFEST = json.loads((GOLDEN / "vectors.json").read_text())["vectors"]
SIG_PY = Path(_sig.__file__)
SYS_PY = "/usr/bin/python3"
NS = "session-mail@example.com"
BOUNDARY = "=_sesh890_" + "0" * 32
BODY = "Pay Alïce 100 €.\n\nOnly after the audit.\n".encode()
PAGE = "<p>Pay Alïce 100 €.</p>\n".encode()


def wire(body: bytes = BODY, page: bytes | None = None) -> bytes:
    """What an honest send writes: the sender's own `build`, unchanged."""
    req = _sender.Request(to="bob@sm827.invalid", from_="alice@sm827.invalid",
                          subject="SM-827 signed text")
    return _sender.build(req, body, host="sm827.invalid", date="Sun, 08 Dec 2030 14:00:00 +0000",
                         message_id="<st@sm827.invalid>", references=[], session="",
                         signature=None, page=page, boundary=BOUNDARY if page else "")


def seal(raw: bytes, p: list[str] | None = None) -> bytes:
    """RAW with a v2 header whose p= is RAW's own digests (or P). b= is a placeholder:
    signed_text never checks the crypto, which is the verifier's."""
    h = _sig.h_for(raw)
    return _sig.insert_header(raw, _sig.format_header(NS, h, p or _sig.digests(raw), "AAAA"))


def v1(body: bytes, mime: bytes = b"Content-Type: text/plain; charset=utf-8\n"
       b"Content-Transfer-Encoding: 8bit\n") -> bytes:
    return (b"From: alice@sm827.invalid\nTo: alice@sm827.invalid\n"
            b"Date: Sun, 08 Dec 2030 14:00:00 +0000\nSubject: 1 v1 item\n"
            b"Message-ID: <v1@sm827.invalid>\nMIME-Version: 1.0\n" + mime
            + _sig.format_v1(NS, "AAAA").encode() + b"\n\n" + body)


def leaf_only(ctype: bytes, payload: bytes) -> bytes:
    """A single-part mail whose one leaf is CTYPE, sealed."""
    raw = (b"From: alice@sm827.invalid\nTo: alice@sm827.invalid\n"
           b"Date: Sun, 08 Dec 2030 14:00:00 +0000\nSubject: 1 no text\n"
           b"Message-ID: <nt@sm827.invalid>\nMIME-Version: 1.0\n"
           b"Content-Type: " + ctype + b"\nContent-Transfer-Encoding: 8bit\n\n" + payload)
    return seal(raw)


# ── the reading ──────────────────────────────────────────────────────────────

def test_a_single_part_reads_its_body():
    assert _sig.signed_text(seal(wire())) == (BODY.decode(), "")


def test_an_alternative_reads_the_first_text_plain_leaf_only():
    raw = seal(wire(page=PAGE))
    assert _sig.leaf_types(raw) == ["text/plain", "text/html"]
    assert _sig.signed_text(raw) == (BODY.decode(), "")


def test_only_the_first_of_two_text_plain_leaves_is_read():
    """body_text joined every text/plain leaf: that is what let a relay append a part that
    memory run then ran. The first leaf is what v1 signs and what the v2 sender writes first."""
    raw = wire(page=PAGE).replace(b"Content-Type: text/html", b"Content-Type: text/plain")
    raw = seal(raw)
    assert _sig.leaf_types(raw) == ["text/plain", "text/plain"]
    text, why = _sig.signed_text(raw)
    assert (text, why) == (BODY.decode(), "")
    assert "<p>" not in text


def test_leaf_data_is_the_bytes_leaf_canon_hashes():
    for name in MANIFEST:
        raw = (GOLDEN / f"{name}.eml").read_bytes()
        for part in _sig.leaves(raw):
            cs = part.get_content_charset()
            head = (f"content-type: {part.get_content_type()}"
                    + (f"; charset={cs}" if cs else "") + "\n\n").encode()
            assert _sig.leaf_canon(part) == head + _sig.leaf_data(part).rstrip(b"\n") + b"\n"


def test_quoted_printable_and_base64_leaves_read_decoded():
    """leaf_data is CTE-decoded, as p= hashes it: a line over 998 octets goes QP (SM-818)."""
    long = b"echo " + b"a" * 1200 + b"\nsecond line\n"
    raw = seal(wire(body=long))
    assert b"quoted-printable" in raw.lower()
    assert _sig.signed_text(raw) == (long.decode(), "")


def test_crlf_reads_as_lf_because_a_cr_before_lf_is_not_signed():
    raw = seal(wire(body=b"line one\r\nline two\r\n"))
    assert _sig.signed_text(raw)[0] == "line one\nline two\n"


def test_invalid_utf8_reads_as_u_fffd_never_dropped():
    """The ONE change for honest mail (decision 2): notmuch dropped these bytes;
    the shelf and `message read` already showed U+FFFD, and now every reader does."""
    raw = seal(wire(body=b"caf\xe9 \xff\xfe bytes\nok line\n"))
    assert _sig.signed_text(raw) == ("caf� �� bytes\nok line\n", "")


@pytest.mark.parametrize("label", [None, "utf-8", "UTF-8", "utf8", "us-ascii"])
def test_utf8_and_ascii_labels_read_as_utf8(label):
    """The v1 shape allows only these, and the v2 sender writes utf-8 only: the shelf's
    rule, senders in this fleet write UTF-8 whatever the header claims."""
    ct = b"text/plain" + (b"; charset=" + label.encode() if label else b"")
    raw = leaf_only(ct, "café\n".encode())
    assert _sig.signed_text(raw) == ("café\n", "")


def test_another_charset_is_signed_so_the_bytes_read_in_it():
    raw = leaf_only(b"text/plain; charset=iso-8859-1", b"caf\xe9\n")
    assert _sig.signed_text(raw) == ("café\n", "")


def test_an_unknown_charset_is_withheld():
    raw = leaf_only(b"text/plain; charset=x-no-such-charset", b"abc\n")
    text, why = _sig.signed_text(raw)
    assert text is None and "charset 'x-no-such-charset' is unknown here" in why


@pytest.mark.parametrize("ctype", [b"text/html; charset=utf-8", b"application/octet-stream"])
def test_no_text_plain_leaf_is_no_text(ctype):
    """A signed mail with no text/plain part has no program and nothing signed to show."""
    assert _sig.signed_text(leaf_only(ctype, b"<p>echo SUBJECT</p>\n")) == (None, _sig.NO_TEXT)


# ── the checks: what the reader re-checks in its own process ─────────────────

def test_one_altered_byte_fails_the_p_entry_recheck():
    raw = seal(wire(page=PAGE))
    bad = raw.replace(b"Only after the audit.", b"Only after the audiT.")
    assert bad != raw
    text, why = _sig.signed_text(bad)
    assert text is None and why == "part 1 does not hash to its p= entry here"


def test_a_leaf_added_or_removed_is_a_count_mismatch():
    honest = seal(wire())
    p = _sig.digests(honest)
    two = seal(wire(), p + p)
    assert _sig.signed_text(two) == (None, "1 parts where 2 were signed")


def test_an_unsigned_mail_is_withheld():
    assert _sig.signed_text(wire()) == (None, "unsigned")


def test_a_malformed_v2_header_is_withheld():
    raw = _sig.insert_header(wire(), "X-Session-Signature: v=2; a=sshsig; n=x; h=from; p=zz; b=AAAA")
    text, why = _sig.signed_text(raw)
    assert text is None and why.startswith("signature header: ")


def test_an_unsupported_version_is_withheld():
    raw = _sig.insert_header(wire(), "X-Session-Signature: v=3; a=sshsig; n=x; b=AAAA")
    assert _sig.signed_text(raw) == (None, "unsupported v=3")


def test_v1_in_its_shape_reads_its_body():
    assert _sig.signed_text(v1(b"echo step 30\n")) == ("echo step 30\n", "")


def test_v1_off_its_shape_is_withheld():
    """A transfer encoding a v1 signature does not cover: SM-822's v1-shape refusal."""
    raw = v1(base64.encodebytes(b"echo other\n"),
             mime=b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: base64\n")
    assert _sig.v1_defects(raw)
    assert _sig.signed_text(raw) == (None, "not in the v1 shape")


def test_check_false_reads_the_leaf_alone():
    """ONLY for a caller whose trust came from a NAMED verifier: no signature at all here."""
    assert _sig.signed_text(wire(page=PAGE), check=False) == (BODY.decode(), "")
    assert _sig.signed_text(leaf_only(b"text/html", b"<p/>\n"), check=False) == (None, _sig.NO_TEXT)


@pytest.mark.parametrize("raw", [b"", b"\x00\xff garbage", b"Subject: x\n\n", b"\n\n\n"])
def test_it_never_raises(raw):
    text, why = _sig.signed_text(raw)
    assert text is None and why


# ── the pin: digests() and the golden vectors do not move ────────────────────

@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_digests_are_byte_identical_on_every_golden_vector(name):
    """leaf_canon now calls leaf_data: its output, and so every p=, is unchanged."""
    v = MANIFEST[name]
    for suffix in ("wire", "eml"):
        assert _sig.digests((GOLDEN / f"{name}.{suffix}").read_bytes()) == v["p"]


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_every_golden_vector_reads_its_first_text_plain_leaf(name):
    raw = (GOLDEN / f"{name}.eml").read_bytes()
    parts = _sig.leaves(raw)
    first = next(p for p in parts if p.get_content_type() == "text/plain")
    text, why = _sig.signed_text(raw)
    assert why == ""
    assert text == _sig.leaf_data(first).decode("utf-8", "replace")


def test_the_golden_vector_files_are_unmoved():
    """A change to a vector is a change to the wire format (fixtures/sig_v2/README.md).
    The TEST key's private half is not hashed: the release export leaves it out
    (.gitattributes), and tests/test_sig_v2_key_export.py pins it in a checkout."""
    h = hashlib.sha256()
    for f in sorted(GOLDEN.iterdir()):
        if f.name == "sessionmail-sig-TEST-VECTORS-ONLY":
            continue
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    assert h.hexdigest() == "df29c8831f3fe95fbefd77d658e23c104950a419d8c9c017048b37f27eb5526c"


# ── both interpreters ────────────────────────────────────────────────────────

PROBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("sig_probe", sys.argv[1])
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
out = []
for path in sys.argv[2:]:
    with open(path, "rb") as fh:
        out.append(list(S.signed_text(fh.read())))
print(json.dumps(out))
"""


def _pythons():
    out = [pytest.param(shutil.which("python3") or sys.executable, id="path-python3")]
    if os.access(SYS_PY, os.X_OK):
        out.append(pytest.param(SYS_PY, id="usr-bin-python3"))
    return out


@pytest.mark.parametrize("python", _pythons())
def test_both_interpreters_read_the_same_text(tmp_path, python):
    """The readers load _sig.py by path under PATH's python3 or /usr/bin/python3 (3.9)."""
    mails = {
        "single": seal(wire()), "alt": seal(wire(page=PAGE)),
        "bad-utf8": seal(wire(body=b"caf\xe9 \xff\xfe\n")),
        "qp": seal(wire(body=b"x" * 1200 + b"\n")),
        "altered": seal(wire(page=PAGE)).replace(b"audit.", b"audiT."),
        "html-only": leaf_only(b"text/html", b"<p/>\n"), "v1": v1(b"echo v1\n"),
        **{n: (GOLDEN / f"{n}.eml").read_bytes() for n in sorted(MANIFEST)},
    }
    paths = []
    for name, raw in mails.items():
        f = tmp_path / f"{name}.eml"
        f.write_bytes(raw)
        paths.append(str(f))
    done = subprocess.run([python, "-I", "-c", PROBE, str(SIG_PY), *paths],
                          capture_output=True, text=True, check=True)
    want = [list(_sig.signed_text(raw)) for raw in mails.values()]
    assert json.loads(done.stdout) == want
