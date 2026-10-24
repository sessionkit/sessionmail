"""SM-818: a body line over 998 octets must arrive byte-identical.

RFC 5322 section 2.1.1 limits a line to 998 octets. `message send` writes
the body as `Content-Transfer-Encoding: 8bit` whatever its line lengths
(`src/sessionmail/_sender.py:1132 @ 8d35912`, sessionmail), so the MTA folds
any longer line, and the sender still exits 0. Measured on a live
host: single-line JSON scene files arrived with body
lines of exactly 998, 998 and 672 bytes, each break a CRLF plus one space
(postfix's `smtp_line_length_limit` folding), and `json.loads` failed at
char 998.

This file holds the REPRODUCTION (slice repro-001): a fake MTA, set as
`SESSIONMAIL_SENDMAIL`, that folds the way postfix did and writes what it
was handed into a scratch maildir. Never real postfix, never `~/.mail`,
never `drop`. The fake's own tests pin its fold to the measurement, so the
round trip's red is the sender's fault and not the fake's.

Then THE FIX (slice fix-001): `_sender._leaf_cte` sends a leaf with a line
over 998 octets quoted-printable (`_sender.qp_encode`), and leaves every
other leaf 8bit, byte for byte. Its tests cover the seam, no churn, the CLI
and MCP doors, signing (verify GREEN, a tamper RED), the page path, the
readers, and notmuch decoding what Python decodes.
"""

from __future__ import annotations

import email
import json
import os
import quopri
import shutil
import subprocess
import sys

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _sender, _views
from sessionmail.message import cli
from test_mcp_send import call, texts
from test_sender import (HOST, body_of, bin_dir, env, fake_sendmail,  # noqa: F401 (fixtures)
                         headers_of, keyed, root, send, sender, verifier)

#: RFC 5322 section 2.1.1: a line is at most 998 octets, CRLF excluded.
LIMIT = 998

#: The fake MTA. It reads the message on stdin, as sendmail does, folds
#: every BODY line longer than 998 octets the way postfix's
#: smtp_line_length_limit did on hq (a CRLF plus one space after octet 998;
#: the space starts the next line and counts toward ITS 998), and writes the
#: result into the scratch maildir named by $FAKE_MTA_MAILDIR, tmp/ then
#: new/, the way a delivery agent does. Headers are passed through as they
#: are: the measurement was of the body. `{{`/`}}` because write_fake
#: formats the source.
FAKE_FOLDING_MTA = """#!{python}
import os, sys, time

LIMIT = 998

def fold(line):
    out = [line[:LIMIT]]
    rest = line[LIMIT:]
    while rest:
        out.append(b" " + rest[:LIMIT - 1])
        rest = rest[LIMIT - 1:]
    return b"\\r\\n".join(out)

raw = sys.stdin.buffer.read()
head, sep, body = raw.partition(b"\\n\\n")
body = b"\\n".join(fold(l) if len(l) > LIMIT else l for l in body.split(b"\\n"))
box = os.environ["FAKE_MTA_MAILDIR"]
name = "{{}}.M{{}}P{{}}.fake".format(int(time.time()), time.time_ns() % 1000000, os.getpid())
tmp = os.path.join(box, "tmp", name)
with open(tmp, "wb") as f:
    f.write(head + sep + body)
os.rename(tmp, os.path.join(box, "new", name))
with open(os.environ["FAKE_MTA_ARGV"], "w") as f:
    f.write("\\x1f".join(sys.argv[1:]))
"""


def _scene(size: int = 3 * 1024) -> dict:
    """A single-line JSON scene like the ones that broke: an inline SVG in one
    long string, with UTF-8 in it, so a fold lands INSIDE a string (where a
    CR is illegal JSON) and a multi-byte character may be split."""
    obj = {"kind": "scene", "title": "Folded — at 998", "svg": ""}
    pad = len(json.dumps(obj, ensure_ascii=False).encode())
    unit = '<rect x="1" y="2" width="30" height="40" fill="#c0ffee"/>·'
    svg = (unit * (size // len(unit.encode()) + 1)).encode()[: size - pad]
    obj["svg"] = svg.decode("utf-8", "ignore")
    return obj


@pytest.fixture
def mta(tmp_path, monkeypatch):
    """The folding fake as $SESSIONMAIL_SENDMAIL, behind a scratch maildir, on
    an UNKEYED seat (the send goes unsigned and says so; signing is rca-001's
    to measure). The environment is test_message_send_json's `world`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_FOLDING_MTA)
    maildir = tmp_path / "scratch-maildir"
    for sub in ("tmp", "new", "cur"):
        (maildir / sub).mkdir(parents=True)
    root = tmp_path / "mail"
    root.mkdir()
    for var in ("SESSIONMAIL_SEND_ORACLE", "SESSIONMAIL_SUBJECT_GUARD", "CLAUDE_CODE_AGENT",
                "CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB",
                "SESSIONMAIL_REQUIRE_SIGNATURE", "SESSIONMAIL_RENDER",
                "SESSIONMAIL_PARENT_CHECK", "SESSION_MAIL_PARENT_CHECK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_MTA_MAILDIR", str(maildir))
    monkeypatch.setenv("FAKE_MTA_ARGV", str(tmp_path / "sendmail.argv"))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")

    class Mta:
        sendmail = bin_dir / "sendmail"

        @staticmethod
        def delivered() -> list[bytes]:
            return [p.read_bytes() for p in sorted((maildir / "new").iterdir())]

    return Mta()


def _fold(line: bytes) -> bytes:
    """Run the fake MTA's own fold on one body line, in a child, as it runs."""
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        box = Path(d) / "box"
        for sub in ("tmp", "new", "cur"):
            (box / sub).mkdir(parents=True)
        fake = write_fake(Path(d) / "sendmail", FAKE_FOLDING_MTA)
        env = {"FAKE_MTA_MAILDIR": str(box), "FAKE_MTA_ARGV": str(Path(d) / "argv")}
        subprocess.run([str(fake), "-t"], input=b"Subject: x\n\n" + line + b"\n",
                       env=env, check=True)
        (got,) = list((box / "new").iterdir())
        return got.read_bytes().partition(b"\n\n")[2]


# ── the fake itself: watched, not assumed ────────────────────────────────────

def test_the_fake_folds_like_the_measurement_998_998_672():
    """The hq measurement, rebuilt: a 2666-octet line comes out as lines of
    998, 998 and 672 octets, each break a CRLF plus one space, and removing
    exactly those CRLF-plus-space breaks gives the line back."""
    line = b"x" * 2666
    got = _fold(line)
    assert [len(l) for l in got.rstrip(b"\n").split(b"\r\n")] == [998, 998, 672]
    assert got.count(b"\r\n ") == 2
    assert got.rstrip(b"\n").replace(b"\r\n ", b"") == line


def test_the_fake_leaves_a_line_within_the_limit_alone():
    """The control: a 998-octet line is legal and is passed through as is."""
    line = b"y" * LIMIT
    assert _fold(line) == line + b"\n"


# ── the round trip: RED at 8d35912 ───────────────────────────────────────────

def test_a_3kb_single_line_json_body_round_trips_through_a_folding_mta(mta, tmp_path):
    """SM-818 REPRODUCED. `message send` with a 3 KB single-line JSON body,
    through an MTA that folds at 998 like postfix: the delivered body,
    transfer-decoded as any reader would, must parse back to the object sent
    and be byte-identical to the file. At 8d35912 it is sent 8bit, folded,
    and does not parse - while the send exits 0."""
    obj = _scene()
    sent = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
    assert len(sent) >= 3 * 1024 and sent.count(b"\n") == 1, "one line, 3 KB"
    body = tmp_path / "scene.json"
    body.write_bytes(sent)

    r = CliRunner().invoke(cli, ["send", "beta", "-s", "scene", "-f", str(body)])
    assert r.exit_code == 0, r.output
    (raw,) = mta.delivered()

    msg = email.message_from_bytes(raw)
    got = msg.get_payload(decode=True)
    lines = [len(l) for l in got.rstrip(b"\n").split(b"\r\n")]
    said = (f"sent Content-Transfer-Encoding: {msg.get('Content-Transfer-Encoding')}; "
            f"delivered body lines {lines} octets, {len(lines) - 1} CRLF-plus-space folds")
    try:
        parsed = json.loads(got.decode("utf-8"))
    except ValueError as e:
        pytest.fail(f"the body does not parse ({e}); {said}")
    assert parsed == obj, said
    assert got == sent, said


# ═══ THE FIX (slice fix-001) ══════════════════════════════════════════════════
#
# rca-001 (findings/rca-001 in the SM-818 task) measured that signature v2
# hashes each leaf AFTER its transfer encoding is undone and never signs the
# Content-Transfer-Encoding header, so a long-line leaf can go quoted-printable
# with no change to the signature format or the signed bytes. `_leaf_cte`
# decides per leaf; every leaf within the limit stays 8bit, byte for byte.

#: notmuch as this machine has it, looked up at import, before a fixture narrows
#: PATH to the fake MTA's bin dir.
NOTMUCH = shutil.which("notmuch")

#: Bodies that have to come back byte for byte, each past the limit somewhere
#: (rca-001's HQ set): what QP must escape, and what it must leave alone.
AWKWARD = {
    "trailing spaces and tabs": b"a  \nb\t\n" + b"x" * 1200 + b" \n",
    "CRLF lines": b"a\r\nb\r\n" + b"y" * 1200 + b"\r\n",
    "a lone CR": b"a\rb\n" + b"z" * 1200 + b"\n",
    "no final newline": b"q" * 1500,
    "dot and From lines": b".\nFrom me\n" + b"w" * 1200 + b"\n",
    "a non-UTF-8 byte": b"\xff\xfe ok\n" + b"v" * 1200 + b"\n",
    "equals signs": b"a=b==c=\n" + b"=" * 1200 + b"\n",
    "soft-break lookalike": b"end=\n" + b"=20 =3D\n" + b"u" * 1200 + b"\n",
    # a literal SPACE / TAB as the last octet before a soft break (column 75)
    "space before a soft break": b"s" * 74 + b" " + b"t" * 1000 + b"\n",
    "tab before a soft break": b"s" * 74 + b"\t" + b"t" * 1000 + b"\n",
}


def _scene_file(tmp_path) -> tuple[dict, bytes, str]:
    obj = _scene()
    sent = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
    path = tmp_path / "scene.json"
    path.write_bytes(sent)
    return obj, sent, str(path)


def _through_the_mta(wire: bytes, tmp_path) -> bytes:
    """WIRE handed to the folding fake as `sendmail -t` would be, and what it
    delivered: the postfix fold applied to whatever line is still too long."""
    box = tmp_path / "mta-box"
    for sub in ("tmp", "new", "cur"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    fake = write_fake(tmp_path / "folding-sendmail", FAKE_FOLDING_MTA)
    subprocess.run([str(fake), "-t"], input=wire, check=True,
                   env={"FAKE_MTA_MAILDIR": str(box), "FAKE_MTA_ARGV": str(tmp_path / "argv")})
    (got,) = list((box / "new").iterdir())
    raw = got.read_bytes()
    got.unlink()                       # the next send through the same box is alone in it
    return raw


def _verify(verifier, tmp_path, raw: bytes) -> subprocess.CompletedProcess:
    f = tmp_path / "delivered.eml"
    f.write_bytes(raw)
    return subprocess.run([str(verifier), str(f)], capture_output=True, text=True)


def _longest(raw: bytes) -> int:
    return max(len(line.rstrip(b"\r")) for line in raw.split(b"\n"))


# ── the seam: _leaf_cte ──────────────────────────────────────────────────────

def test_a_leaf_of_998_octet_lines_stays_8bit_and_untouched():
    data = b"a" * LIMIT + b"\n" + b"b" * LIMIT
    cte, out = _sender._leaf_cte(data)
    assert (cte, out) == ("8bit", data)
    assert _sender._leaf_cte(b"") == ("8bit", b"")


def test_one_octet_over_and_the_leaf_goes_quoted_printable():
    data = b"short\n" + b"c" * (LIMIT + 1) + b"\n"
    cte, out = _sender._leaf_cte(data)
    assert cte == "quoted-printable"
    assert quopri.decodestring(out) == data
    assert _longest(out) <= 76


def test_a_crlf_line_counts_its_cr_so_it_errs_toward_encoding():
    """The LF is the terminator; a CR before it is counted, so a 998-octet
    CRLF line is encoded (never under-encoded: the CR is a 999th octet to a
    sender that hands LF lines to `sendmail -t`)."""
    assert _sender._leaf_cte(b"d" * LIMIT + b"\r\n")[0] == "quoted-printable"
    assert _sender._leaf_cte(b"d" * (LIMIT - 1) + b"\r\n")[0] == "8bit"


@pytest.mark.parametrize("label", sorted(AWKWARD))
def test_awkward_bodies_decode_byte_for_byte_from_the_wire(label):
    """The receiver's reading: the leaf in a message, parsed from bytes,
    `get_payload(decode=True)`. Exact, and no parser defect (SM-819 will
    refuse leaves with decode defects: what this sender emits must have none)."""
    data = AWKWARD[label]
    cte, out = _sender._leaf_cte(data)
    assert cte == "quoted-printable" and _longest(out) <= 76
    msg = email.message_from_bytes(b"Content-Type: text/plain; charset=utf-8\n"
                                   b"Content-Transfer-Encoding: quoted-printable\n\n" + out)
    assert msg.get_payload(decode=True) == data
    assert not msg.defects


def test_the_qp_edges_the_super_named_are_each_exercised():
    """Each edge is really on the wire, not just in the input: the 76 limit
    reached, a lone CR escaped, a SPACE and a TAB literal just before a soft
    break, a literal `=` escaped. Python's exactness is the parametrized test
    above; notmuch's is the notmuch test below (both run AWKWARD)."""
    enc = {label: _sender._leaf_cte(AWKWARD[label])[1] for label in AWKWARD}
    assert max(_longest(out) for out in enc.values()) == 76
    assert b"a=0Db\n" in enc["a lone CR"]
    assert b" =\n" in enc["space before a soft break"]
    assert b"\t=\n" in enc["tab before a soft break"]
    assert enc["equals signs"].startswith(b"a=3Db=3D=3Dc=3D\n")
    for label in ("space before a soft break", "tab before a soft break", "a lone CR",
                  "equals signs"):
        assert quopri.decodestring(enc[label]) == AWKWARD[label], label


def test_the_encoder_is_exact_and_76_wide_on_random_bytes():
    """Seeded noise, weighted toward what QP must handle (space, tab, CR, LF,
    `=`, high octets) and lines past the limit: Python's decoder gives every
    case back exactly, and no encoded line is over 76 characters, a trailing
    space at the wrap edge included (binascii's encoder writes 78 there)."""
    import random
    rng = random.Random(818)
    alphabet = b"  \t\t\r\n==ab\x00\x7f\x80\xff\xe2\x80\x94" + bytes(range(33, 127))
    for n in range(300):
        size = rng.choice((1, 75, 76, 77, 999, 1500, 3000))
        data = bytes(rng.choice(alphabet) for _ in range(size))
        if n % 3 == 0:
            data = b"x" * 74 + b" " + data      # the wrap edge, a space on it
        out = _sender.qp_encode(data)
        assert quopri.decodestring(out) == data, data
        assert _longest(out) <= 76, out
        assert all(not row.endswith((b" ", b"\t")) for row in out.split(b"\n"))


# ── no churn: within the limit, the wire is what 8d35912 wrote ───────────────

def test_no_churn_a_short_body_builds_the_8d35912_bytes_exactly():
    """Pinned against the literal 8d35912 wrote (`_sender.py:1132 @ 8d35912`):
    CTE 8bit and the body's own bytes, a 998-octet line included."""
    body = b"line one\n" + b"e" * LIMIT + b"\n"
    req = _sender.parse_argv(["beta", "--from", "eve", "--subject", "s"])
    wire = _sender.build(req, body, host=HOST, date="Sun, 09 Jun 2030 12:00:00 +0000",
                         message_id="<1.2.3@testhost.session-mail>", references=[],
                         session="", signature=None)
    assert wire == (b"From: eve\nTo: beta\nDate: Sun, 09 Jun 2030 12:00:00 +0000\n"
                    b"Subject: s\nMessage-ID: <1.2.3@testhost.session-mail>\n"
                    b"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
                    b"Content-Transfer-Encoding: 8bit\nX-Machine: testhost\n\n" + body)


def test_no_churn_a_short_page_builds_the_8d35912_parts_exactly():
    body, page = b"# hi\n", b"<!DOCTYPE html>\n<p>hi</p>\n"
    req = _sender.parse_argv(["judy", "--from", "eve", "--subject", "s"])
    wire = _sender.build(req, body, host=HOST, date="D", message_id="<m@h>", references=[],
                         session="", signature=None, page=page, boundary="B")
    part = "Content-Type: text/{}; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
    assert body_of(wire) == (f"--B\n{part.format('plain')}".encode() + body
                             + f"\n--B\n{part.format('html')}".encode() + page + b"\n--B--\n")
    assert dict(headers_of(wire))["Content-Transfer-Encoding"] == "8bit"


def test_no_churn_through_the_mta_a_998_octet_body_arrives_8bit_and_raw(mta, tmp_path):
    sent = b"f" * LIMIT + b"\n"
    path = tmp_path / "b.txt"
    path.write_bytes(sent)
    r = CliRunner().invoke(cli, ["send", "beta", "-s", "edge", "-f", str(path)])
    assert r.exit_code == 0, r.output
    (raw,) = mta.delivered()
    assert email.message_from_bytes(raw)["Content-Transfer-Encoding"] == "8bit"
    assert raw.partition(b"\n\n")[2] == sent


# ── message send: QP on the wire, nothing for the MTA to fold ────────────────

def test_the_long_line_scene_goes_quoted_printable_and_nothing_is_folded(mta, tmp_path):
    obj, sent, path = _scene_file(tmp_path)
    r = CliRunner().invoke(cli, ["send", "beta", "-s", "scene", "-f", path])
    assert r.exit_code == 0, r.output
    (raw,) = mta.delivered()
    msg = email.message_from_bytes(raw)
    assert msg["Content-Transfer-Encoding"] == "quoted-printable"
    assert b"\r\n " not in raw and _longest(raw.partition(b"\n\n")[2]) <= 76
    assert msg.get_payload(decode=True) == sent and not msg.defects


def test_the_mcp_door_sends_the_same_long_line_body_byte_for_byte(mta, tmp_path):
    """`message_send` runs the real `message send` child (`_mcp.SEND_COMMAND`),
    so it reaches the same `build()`; through the folding MTA it arrives QP
    and decodes to the text given."""
    obj, sent, _ = _scene_file(tmp_path)
    result, _ = call({"to": ["beta"], "subject": "scene", "body": sent.decode("utf-8")})
    assert result.is_error is False, texts(result)
    (raw,) = mta.delivered()
    msg = email.message_from_bytes(raw)
    assert msg["Content-Transfer-Encoding"] == "quoted-printable"
    assert msg.get_payload(decode=True) == sent
    assert json.loads(msg.get_payload(decode=True)) == obj


# ── signing: verify GREEN on receipt, a tamper still RED ─────────────────────

def test_a_signed_long_line_body_verifies_after_the_mta(sender, keyed, tmp_path, fake_sendmail,
                                                        verifier):
    _, sent, _ = _scene_file(tmp_path)
    rc, _, err = send(sender, keyed, tmp_path, body=sent, to="beta")
    assert rc == 0, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    raw = _through_the_mta(fake_sendmail.wire, tmp_path)
    assert email.message_from_bytes(raw).get_payload(decode=True) == sent
    done = _verify(verifier, tmp_path, raw)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "part 1            : ✅ text/plain" in done.stdout, done.stdout


def test_a_tampered_long_line_body_still_fails_verify(sender, keyed, tmp_path, fake_sendmail,
                                                      verifier):
    _, sent, _ = _scene_file(tmp_path)
    rc, _, err = send(sender, keyed, tmp_path, body=sent, to="beta")
    assert rc == 0, err
    head, sep, body = _through_the_mta(fake_sendmail.wire, tmp_path).partition(b"\n\n")
    assert b'"scene"' in body
    done = _verify(verifier, tmp_path, head + sep + body.replace(b'"scene"', b'"scenf"', 1))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "❌ ALTERED text/plain" in done.stdout, done.stdout


def test_the_signature_is_the_same_whatever_the_encoding(sender, keyed, tmp_path):
    """p= is over the DECODED leaf: the 8bit build and the QP build of one
    body carry the same digest, so neither the format nor the signed bytes
    moved (rca-001's measurement, pinned)."""
    from sessionmail import _sig
    body = b"g" * (LIMIT + 1) + b"\n"
    plain = b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n" + body
    req = _sender.parse_argv(["beta", "--from", "eve", "--subject", "s"])
    wire = _sender.build(req, body, host=HOST, date="D", message_id="<m@h>", references=[],
                         session="", signature=None)
    assert b"Content-Transfer-Encoding: quoted-printable\n" in wire
    assert _sig.digests(wire) == _sig.digests(plain)
    assert "content-transfer-encoding" not in _sig.h_for(wire)


# ── the page path: per part, the top level stays 8bit ────────────────────────

def test_a_rendered_long_line_mail_encodes_each_part_and_verifies(sender, keyed, tmp_path,
                                                                  fake_sendmail, verifier):
    _, sent, _ = _scene_file(tmp_path)
    rc, _, err = send(sender, keyed, tmp_path, body=sent, to="judy")
    assert rc == 0, err
    raw = _through_the_mta(fake_sendmail.wire, tmp_path)
    msg = email.message_from_bytes(raw)
    assert msg.get_content_type() == "multipart/alternative"
    assert msg["Content-Transfer-Encoding"] == "8bit"            # RFC 2045 section 6.4
    text, html = msg.get_payload()
    assert text["Content-Transfer-Encoding"] == "quoted-printable"
    assert text.get_payload(decode=True) == sent
    from sessionmail._render import render_page
    page = render_page(sent.decode("utf-8"), "Build green").encode()
    assert html.get_payload(decode=True) == page
    assert html["Content-Transfer-Encoding"] == _sender._leaf_cte(page)[0]
    assert b"\r\n " not in raw
    done = _verify(verifier, tmp_path, raw)
    assert done.returncode == 0, done.stdout + done.stderr


# ── readers: the decoded text, never the QP ──────────────────────────────────

def test_message_read_and_the_pager_view_show_the_decoded_text(mta, tmp_path, monkeypatch):
    obj, sent, path = _scene_file(tmp_path)
    r = CliRunner().invoke(cli, ["send", "beta", "-s", "scene", "-f", path])
    assert r.exit_code == 0, r.output
    (raw,) = mta.delivered()
    assert b"=3D" in raw and b"=\n" in raw, "the wire is QP, or this test proves nothing"
    root = tmp_path / "mail"
    for sub in ("tmp", "new", "cur"):
        (root / "beta" / sub).mkdir(parents=True, exist_ok=True)
    delivered = root / "beta" / "new" / "1922716800.M1P1.test"
    delivered.write_bytes(raw)
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "00000000-0000-4000-8000-000000000818")

    read = CliRunner().invoke(cli, ["read", "beta", "1", "--full"], prog_name="message")
    assert read.exit_code == 0, read.output
    text = sent.decode("utf-8").rstrip("\n")
    assert text in read.output, read.output[-400:]
    assert "=3D" not in read.output and "=\n" not in read.output

    view = _views.view(_views.load(delivered), "1")      # the pager's page / scoop view
    shown = "\n".join(view.body)
    assert view.body[0][:300] == text[:300], view.body[0][:300]   # `=`, the em dash: decoded
    assert "=3D" not in shown and "=E2" not in shown     # (a cut may end on a literal `=`)


# ── notmuch: what SM-818 emits, GMime and Python decode alike ────────────────

def test_notmuch_and_python_decode_the_emitted_qp_to_the_same_bytes(mta, tmp_path):
    """An addition: the QP this sender emits is
    well-formed, so notmuch (GMime: `memory cat`, the tree) and Python (the
    verifier, `message read`) read the same bytes. A SCRATCH database, a
    NOTMUCH_CONFIG of its own: never ~/.mail, never the real config."""
    notmuch = NOTMUCH
    if notmuch is None:
        pytest.skip("notmuch is not installed here: the GMime side cannot be read")
    _, sent, path = _scene_file(tmp_path)
    r = CliRunner().invoke(cli, ["send", "beta", "-s", "scene", "-f", path])
    assert r.exit_code == 0, r.output
    (raw,) = mta.delivered()
    msg = email.message_from_bytes(raw)
    assert msg["Content-Transfer-Encoding"] == "quoted-printable"
    python = msg.get_payload(decode=True)

    db = tmp_path / "notmuch-db"
    for sub in ("tmp", "new", "cur"):
        (db / "box" / sub).mkdir(parents=True)
    (db / "box" / "new" / "1922716800.M1P1.test").write_bytes(raw)
    config = tmp_path / "notmuch-config"
    config.write_text(f"[database]\npath={db}\n[new]\ntags=\n[maildir]\nsynchronize_flags=false\n")
    nm_env = {"NOTMUCH_CONFIG": str(config), "HOME": str(tmp_path), "PATH": os.defpath}
    subprocess.run([notmuch, "new", "--quiet"], env=nm_env, check=True, capture_output=True)
    mid = msg["Message-ID"].strip().strip("<>")
    shown = subprocess.run([notmuch, "show", "--format=raw", "--part=1", f"id:{mid}"],
                           env=nm_env, check=True, capture_output=True).stdout
    assert shown == python == sent

    # and every awkward body, each as the leaf `build` would write
    for n, label in enumerate(sorted(AWKWARD)):
        data = AWKWARD[label]
        cte, out = _sender._leaf_cte(data)
        (db / "box" / "new" / f"1922716801.M{n}P1.test").write_bytes(
            f"From: a\nTo: b\nSubject: {n}\nMessage-ID: <awkward{n}@sm818>\n".encode()
            + b"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
            + f"Content-Transfer-Encoding: {cte}\n\n".encode() + out)
    subprocess.run([notmuch, "new", "--quiet"], env=nm_env, check=True, capture_output=True)
    for n, label in enumerate(sorted(AWKWARD)):
        shown = subprocess.run([notmuch, "show", "--format=raw", "--part=1", f"id:awkward{n}@sm818"],
                               env=nm_env, check=True, capture_output=True).stdout
        assert shown == AWKWARD[label], label
