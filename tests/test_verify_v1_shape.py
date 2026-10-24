"""SM-822: a v1 mail verifies only in the exact shape the v1 sender wrote.

A v1 signature covers five headers and ONE body string: the raw payload of a single part, or
of the text/plain first part of a multipart/alternative. It covers no MIME header and no MIME
tree, and the verifier reads a CR as LF. So a relay with no key could add a transfer encoding
(notmuch then decodes other text), re-wrap or append a part (`memory run` joins every
text/plain part into the command it runs), re-label the charset, or turn LF into CR, and v1
still said GOOD. Now such a mail is BAD, rc 1 (`_sig.v1_defects`, through the verifier's
`v1_refusal`):

- `reason=cte-defect`: a part (either leaf, or the container) has more than one CTE field, or
  one that is not a single plain token `7bit`, `8bit` or `binary`, read from the field's
  source bytes. SM-819 gives the token the same meaning for v2.
- `reason=v1-shape`: not exactly text/plain, or multipart/alternative of exactly [text/plain,
  text/html]; a charset other than utf-8 or us-ascii; a bare CR; a defect in the MIME parse.

Fixtures are neutral and built here: signed with `_sender.canonical` (the v1 sender's own
canonical form), `_sig.format_v1` and a key made for the test. The task's exhibits stay in the
task store. Every verifier case runs under PATH's python3 AND /usr/bin/python3 (macOS 3.9),
through a PATH shim, the two interpreters the verifier meets.

Run: uv run pytest -q tests/test_verify_v1_shape.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sessionmail import _mhs, _sig
from sessionmail._sender import canonical as v1_canonical

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
NS = "session-mail@example.com"
SYS_PY = "/usr/bin/python3"
BODY = b"Pay Alice 100 dollars.\nThanks, Bob.\n"

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

    def sign(self, frm: str, to: str, subject: str, date: str, mid: str, body: bytes) -> str:
        """The v1 header line, over the v1 sender's own canonical form."""
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n", NS],
                              input=v1_canonical(frm, to, subject, date, mid, body),
                              capture_output=True, check=True)
        b = "".join(x for x in done.stdout.decode().splitlines()
                    if x and "SSH SIGNATURE" not in x)
        return _sig.format_v1(NS, b)

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
    return Rig(tmp_path_factory.mktemp("sm822"))


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


def test_both_interpreters_are_really_used(tmp_path):
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


# ── honest v1 mail, in the shapes the v1 senders wrote ──────────────────────

FROM, TO = "ivan@example.com", "uma@example.com"
DATE = "Sun, 08 Dec 2030 08:00:00 +0000"
MID = "<sm822@example.com>"
BOUNDARY = "=_sesh890_0123456789abcdef0123456789abcdef"
PLAIN = b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n"


def single(rig, body=BODY, mime=PLAIN, subject="s sm822", extra=b"") -> bytes:
    """A single-part v1 mail, built as `_sender.build` @ 91f6d82 builds one. MIME is the
    Content-Type and CTE lines (b"" for none); the signature is the last header."""
    head = (f"From: {FROM}\nTo: {TO}\nDate: {DATE}\nSubject: {subject}\nMessage-ID: {MID}\n"
            ).encode() + (b"MIME-Version: 1.0\n" + mime if mime else b"") + extra
    sig = rig.sign(FROM, TO, subject, DATE, MID, body)
    return head + sig.encode() + b"\n\n" + body


def alternative(rig, body=BODY, page=b"<p>Pay Alice 100 dollars.</p>\n", plain=PLAIN,
                html=b"Content-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: 8bit\n",
                container=b"Content-Transfer-Encoding: 8bit\n", tail=b"") -> bytes:
    """SESH-890's multipart/alternative, as `_sender.build` @ 91f6d82 builds it: the
    markdown first (signed), the html second (not signed)."""
    sig = rig.sign(FROM, TO, "s sm822", DATE, MID, body)
    b = BOUNDARY.encode()
    return ((f"From: {FROM}\nTo: {TO}\nDate: {DATE}\nSubject: s sm822\nMessage-ID: {MID}\n"
             f'MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary="{BOUNDARY}"\n'
             ).encode() + container + sig.encode() + b"\n\n"
            + b"--" + b + b"\n" + plain + b"\n" + body
            + b"\n--" + b + b"\n" + html + b"\n" + page
            + tail + b"\n--" + b + b"--\n")


def wake(rig, body: bytes, supersedes: bool) -> bytes:
    """A real v1 /BOOT/WAKE item's shape (names and values only): delivered headers first,
    the signature LAST after X-Machine, single text/plain utf-8 8bit, LF line ends. Its
    names are the source text a held test rewrites (test_memory_honest_unchanged.py
    replaces them by string), so this module's names move with that file's pass."""
    frm, to = "ivan <ivan@nas.example>", "ivan+memory@nas.example"
    subject, date, mid = "[WAKE] 30 a synthetic wake step", DATE, "<wake-30@nas.example>"
    head = ("Return-Path: <ivan@nas.example>\nX-Original-To: ivan+memory@nas.example\n"
            "Delivered-To: ivan+memory@nas.example\n"
            "Received: by nas.example (Postfix, from userid 501)\n\tid 4F00A1B2C3; "
            "Sun, 08 Dec 2030 08:00:00 +0000 (UTC)\n"
            f"From: {frm}\nTo: {to}\nDate: {date}\nSubject: {subject}\nMessage-ID: {mid}\n"
            "In-Reply-To: <wake-label@nas.example>\n"
            "References: <root@nas.example> <boot@nas.example>\n <wake-label@nas.example>\n"
            "MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
            "Content-Transfer-Encoding: 8bit\n"
            + ("Supersedes: <wake-29@nas.example>\n" if supersedes else "")
            + "X-Session-From: 0123abcd\nX-Machine: nas\n")
    return head.encode() + rig.sign(frm, to, subject, date, mid, body).encode() + b"\n\n" + body


def honest(rig) -> dict[str, bytes]:
    return {
        "8bit": single(rig),
        "7bit": single(rig, mime=b"Content-Type: text/plain; charset=utf-8\n"
                                 b"Content-Transfer-Encoding: 7bit\n"),
        "binary": single(rig, mime=b"Content-Type: text/plain; charset=utf-8\n"
                                   b"Content-Transfer-Encoding: binary\n"),
        "no-cte": single(rig, mime=b"Content-Type: text/plain; charset=utf-8\n"),
        "no-mime-headers": single(rig, mime=b""),
        "us-ascii": single(rig, mime=b"Content-Type: text/plain; charset=us-ascii\n"),
        "charset-quoted-upper": single(rig, mime=b'Content-Type: text/plain; charset="UTF-8"\n'),
        "utf8-body": single(rig, body="café, naïve = résumé\n".encode()),
        "crlf-line-ends": single(rig).replace(b"\n", b"\r\n"),
        "trailing-lfs": single(rig, body=BODY + b"\n\n\n"),
        "no-trailing-lf": single(rig, body=BODY.rstrip(b"\n")),
        "empty-body": single(rig, body=b""),
        "sesh890-alternative": alternative(rig),
        "sesh890-alternative-utf8": alternative(rig, body="café = ok\n".encode()),
        "sesh890-alternative-crlf": alternative(rig).replace(b"\n", b"\r\n"),
        "wake-1lf-ascii-supersedes": wake(rig, b"memory run: echo step 30\n", True),
        "wake-2lf-utf8": wake(rig, "café: echo step 30\n\n".encode(), True),
        "wake-0lf-no-supersedes": wake(rig, b"echo step 30", False),
    }


def test_honest_v1_stays_good(rig, py):
    for name, raw in honest(rig).items():
        r = rig.run(raw, py)
        assert r.returncode == 0, (name, r.stdout, r.stderr)
        assert "v1-shape" not in r.stdout, name
        t = rig.run(raw, py, "--trust", "--quiet")
        assert (t.returncode, t.stdout.strip()) == (4, "TRUST: UNBLESSED reason=bless-absent"), \
            (name, t.stdout, t.stderr)


def test_honest_v1_good_output_is_unchanged(rig):
    """A GOOD v1 verdict prints what it printed before SM-822: no new line."""
    out = rig.run(single(rig), "python3").stdout.splitlines()
    assert [l.split(":")[0].strip() for l in out] == [
        "signature-crypto", "principal-binding", "signer-key", "sender-session-id", "covers"]


# ── what a relay with no key could do ───────────────────────────────────────

def _cte(raw: bytes, new: bytes, n: int = 1) -> bytes:
    """Replace the n-th `Content-Transfer-Encoding: 8bit` line with NEW (b"" removes it)."""
    line = b"Content-Transfer-Encoding: 8bit\n"
    i = -1
    for _ in range(n):
        i = raw.index(line, i + 1)
    return raw[:i] + new + raw[i + len(line):]


#: Group 1 and 2: every spelling a relay could give the CTE. (name, replacement line).
RELABELS = [
    ("qp", b"Content-Transfer-Encoding: quoted-printable\n"),
    ("base64", b"Content-Transfer-Encoding: base64\n"),
    ("x-uuencode", b"Content-Transfer-Encoding: x-uuencode\n"),
    ("upper-BASE64", b"Content-Transfer-Encoding: BASE64\n"),
    ("comment", b"Content-Transfer-Encoding: base64 (c)\n"),
    ("folded", b"Content-Transfer-Encoding:\n base64\n"),
    ("quoted", b'Content-Transfer-Encoding: "base64"\n'),
    ("trailing-ws", b"Content-Transfer-Encoding: quoted-printable\t\n"),
    ("trailing-ws-8bit", b"Content-Transfer-Encoding: 8bit \n"),
    ("second-after", b"Content-Transfer-Encoding: 8bit\nContent-Transfer-Encoding: base64\n"),
    ("second-before", b"Content-Transfer-Encoding: base64\nContent-Transfer-Encoding: 8bit\n"),
    ("two-identity", b"Content-Transfer-Encoding: 8bit\nContent-Transfer-Encoding: 7bit\n"),
    ("space-colon", b"Content-Transfer-Encoding : base64\n"),
]


def _bad(rig, raw, py, reason):
    r = rig.run(raw, py)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert f"v1-shape          : ❌ {reason.upper()}:" in r.stdout, r.stdout
    assert "BAD: not the shape a v1 signature was made in" in r.stderr, r.stderr
    t = rig.run(raw, py, "--trust", "--quiet")
    assert (t.returncode, t.stdout.strip().splitlines()[-1]) == (
        1, f"TRUST: FAIL leg=1-signature reason={reason}"), (t.stdout, t.stderr)
    j = json.loads(rig.run(raw, py, "--json").stdout)
    assert (j["rc"], j["result"], j["reason"], j["signature"]) == (1, "bad", reason, None), j


@pytest.mark.parametrize("name, line", RELABELS, ids=[n for n, _ in RELABELS])
def test_group1_single_part_relabelled_is_cte_defect(rig, py, name, line):
    raw = single(rig)
    if name == "space-colon":
        # The email package ends the header block at a field with whitespace before its
        # colon and reads it as BODY, so the signed text changes and the crypto says BAD
        # first. (Group 2 carries it on the unsigned html leaf, where the crypto stays good.)
        raw = raw.replace(b"\n\n", b"\n" + line + b"\n", 1)
        t = rig.run(raw, py, "--trust", "--quiet")
        assert t.stdout.strip() == "TRUST: FAIL leg=1-signature reason=bad-signature", t.stdout
        return
    _bad(rig, _cte(raw, line), py, "cte-defect")


@pytest.mark.parametrize("where", ["leaf-plain", "leaf-html", "container"])
@pytest.mark.parametrize("name, line", RELABELS, ids=[n for n, _ in RELABELS])
def test_group2_alternative_relabelled_is_cte_defect(rig, py, where, name, line):
    raw = alternative(rig)
    n = {"container": 1, "leaf-plain": 2, "leaf-html": 3}[where]
    if name == "space-colon":
        # The email package ends a header block at a field with whitespace before its colon
        # and reads the field as body. Where that lands decides which rule refuses the mail.
        if where == "leaf-plain":
            # The signed leaf: the signed text changes, so the crypto says BAD first.
            raw = _cte(raw, b"Content-Transfer-Encoding: 8bit\n" + line, n)
            t = rig.run(raw, py, "--trust", "--quiet")
            assert (t.returncode, t.stdout.strip()) == (
                1, "TRUST: FAIL leg=1-signature reason=bad-signature"), (t.stdout, t.stderr)
            return
        if where == "container":
            # The container: the signed text is untouched and the crypto stays GOOD, but the
            # MIME parse reports the defect, so the v1-shape rule refuses it.
            raw = raw.replace(b"\n\n", b"\n" + line + b"\n", 1)
            _bad(rig, raw, py, "v1-shape")
            assert "signature-crypto  : ✅ good" in rig.run(raw, py).stdout
            assert "the container: the MIME parse reports MissingHeaderBodySeparatorDefect" \
                in rig.run(raw, py).stdout
            return
        # The unsigned html leaf: the crypto stays GOOD, and the CTE rule refuses it.
        raw = raw.replace(b"charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n<p>",
                          b"charset=utf-8\nContent-Transfer-Encoding: 8bit\n" + line + b"\n<p>")
    else:
        raw = _cte(raw, line, n)
    _bad(rig, raw, py, "cte-defect")


HIDDEN_CTE = [
    ("after-a-line-with-no-colon", b"X-Junk line\nContent-Transfer-Encoding: base64\n"),
    ("after-a-space-in-a-name", b"X Junk: y\nContent-Transfer-Encoding: base64\n"),
    ("after-a-blank-looking-line", b"X-Junk line\n \t\nContent-Transfer-Encoding: base64\n"),
    ("space-colon-after-a-junk-line", b"X-Junk line\nContent-Transfer-Encoding : base64\n"),
]


@pytest.mark.parametrize("name, lines", HIDDEN_CTE, ids=[n for n, _ in HIDDEN_CTE])
def test_group2_a_cte_hidden_after_an_unreadable_line_is_cte_defect(rig, py, name, lines):
    """SM-819 fix-003, SM-822 review F1: v1 reads a part's CTE field by SM-819's rule. The
    email package ends the html leaf's header block at a line it cannot read and keeps the
    rest as body; a GMime reader skips that line and reads the CTE field after it (SM-819,
    measured). A line of spaces or tabs is not the end of a header block to GMime (it reads
    it as a fold), so it does not end the search either. The crypto stays GOOD (the html
    leaf is unsigned) and the reason is the CTE's, as in v2: `cte-defect`, not `v1-shape`.
    Before the fold V1_OBS_CTE matched only whitespace before the colon, and every case
    here said `v1-shape`."""
    raw = alternative(rig).replace(
        b"charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n<p>",
        b"charset=utf-8\nContent-Transfer-Encoding: 8bit\n" + lines + b"\n<p>")
    assert b"\n" + lines in raw
    assert "signature-crypto  : ✅ good" in rig.run(raw, py).stdout
    _bad(rig, raw, py, "cte-defect")


def test_v1_and_v2_read_a_cte_field_by_one_rule():
    """SM-819 fix-003: there is ONE obs-CTE rule, SM-819's, and v1 calls it."""
    assert not hasattr(_sig, "V1_OBS_CTE") and not hasattr(_sig, "_v1_obs_cte")
    assert _sig.V1_CTE_FIELD is _sig.CTE_FIELD


def test_group3_a_bare_cr_in_the_signed_body_is_refused(rig, py):
    raw = single(rig)
    head, body = raw.split(b"\n\n", 1)
    _bad(rig, head + b"\n\n" + body.replace(b"\n", b"\r"), py, "v1-shape")
    # under a QP relabel too: both rules fire, and the CTE is named
    qp = _cte(raw, b"Content-Transfer-Encoding: quoted-printable\n")
    head, body = qp.split(b"\n\n", 1)
    _bad(rig, head + b"\n\n" + body.replace(b"\n", b"\r"), py, "cte-defect")
    # one CR, in the alternative's signed leaf
    alt = alternative(rig)
    _bad(rig, alt.replace(b"dollars.\nThanks", b"dollars.\rThanks", 1), py, "v1-shape")


def test_group4_shapes_the_v1_sender_never_wrote(rig, py):
    one = single(rig)
    head, body = one.split(b"\n\n", 1)
    head = head.replace(b"Content-Type: text/plain; charset=utf-8\n", b"")
    rewrap = (head.replace(b"MIME-Version: 1.0\n",
                           b'MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary="R"\n')
              + b"\n\n--R\nContent-Type: text/plain; charset=utf-8\n\n" + body
              + b"\n--R\nContent-Type: text/plain; charset=utf-8\n\nPay Mallory too.\n--R--\n")
    cases = {
        "rewrap-second-text-plain": rewrap,
        "append-third-part": alternative(
            rig, tail=b"\n--" + BOUNDARY.encode()
            + b"\nContent-Type: text/plain; charset=utf-8\n\nPay Mallory too."),
        "charset-iso-8859-1": one.replace(b"charset=utf-8", b"charset=iso-8859-1"),
        "charset-twice": one.replace(b"charset=utf-8", b"charset=utf-8; charset=iso-8859-1"),
        "charset-rfc2231": one.replace(b"charset=utf-8", b"charset*=iso-8859-1''"),
        "extra-param": one.replace(b"charset=utf-8", b"charset=utf-8; format=flowed"),
        "type-comment": one.replace(b"text/plain;", b"text/plain (x);"),
        "single-text-html": one.replace(b"text/plain", b"text/html"),
        "two-content-type": one.replace(b"MIME-Version: 1.0\n", b"MIME-Version: 1.0\n"
                                        b"Content-Type: text/plain; charset=iso-8859-1\n"),
        "alt-html-latin1": alternative(rig, html=b"Content-Type: text/html; "
                                                 b"charset=iso-8859-1\n"),
        "alt-html-to-plain": alternative(rig, html=b"Content-Type: text/plain\n"),
        "alt-container-param": alternative(rig).replace(
            f'boundary="{BOUNDARY}"'.encode(), f'boundary="{BOUNDARY}"; x=y'.encode()),
    }
    for name, raw in cases.items():
        try:
            _bad(rig, raw, py, "v1-shape")
        except AssertionError as e:
            raise AssertionError(name) from e


def test_relabels_to_another_identity_token_stay_good(rig, py):
    """7bit, 8bit and binary are all identity: a relay that swaps one for another changes
    no reader's text, and v1 never signed the CTE. So it is not refused."""
    for line in (b"Content-Transfer-Encoding: 7bit\n", b"Content-Transfer-Encoding: BINARY\n",
                 b"content-transfer-encoding:8bit\n"):
        assert rig.run(_cte(single(rig), line), py).returncode == 0, line
        assert rig.run(_cte(alternative(rig), line, 2), py).returncode == 0, line


def test_the_html_part_may_still_change(rig, py):
    """The html part is unsigned by design (SESH-890 D2): a new html page in the same shape
    stays GOOD, as before."""
    raw = alternative(rig).replace(b"<p>Pay Alice 100 dollars.</p>", b"<p>other</p>")
    assert rig.run(raw, py).returncode == 0


def test_a_bad_signature_is_still_named_first(rig, py):
    """The shape is judged only once the crypto is good: a tampered AND relabelled mail is
    `bad-signature`, as before."""
    raw = _cte(single(rig), b"Content-Transfer-Encoding: base64\n").replace(b"Alice", b"Alicf")
    t = rig.run(raw, py, "--trust", "--quiet")
    assert t.stdout.strip() == "TRUST: FAIL leg=1-signature reason=bad-signature"


# ── the function, directly (both interpreters agree on every vector) ─────────

def _defects_under(python: str, raws: dict[str, bytes], tmp: Path) -> dict:
    d = tmp / ("v-" + hashlib.sha256(python.encode()).hexdigest()[:8])
    d.mkdir(exist_ok=True)
    for name, raw in raws.items():
        (d / name).write_bytes(raw)
    code = ("import importlib.util, json, os, sys\n"
            "s = importlib.util.spec_from_file_location('s', sys.argv[1])\n"
            "S = importlib.util.module_from_spec(s); s.loader.exec_module(S)\n"
            "print(json.dumps({n: S.v1_defects(open(os.path.join(sys.argv[2], n), 'rb').read())"
            " for n in sorted(os.listdir(sys.argv[2]))}))\n")
    out = subprocess.run([shutil.which(python) or python, "-I", "-c", code,
                          str(Path(_sig.__file__)), str(d)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_v1_defects_is_the_same_on_both_interpreters(rig, tmp_path):
    raws = dict(honest(rig))
    for name, line in RELABELS:
        if name != "space-colon":
            raws["relabel-" + name] = _cte(single(rig), line)
            raws["alt-leaf-" + name] = _cte(alternative(rig), line, 2)
    got = [_defects_under(p.values[0], raws, tmp_path) for p in _pythons()]
    assert all(g == got[0] for g in got[1:])
    for name in honest(rig):
        assert got[0][name] == [], name
    for name in raws:
        if name.startswith(("relabel-", "alt-leaf-")):
            assert got[0][name] and got[0][name][0][0] == "cte-defect", (name, got[0][name])
