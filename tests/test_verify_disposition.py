"""SM-831: a mail with a Content-Disposition on any part is refused, BAD rc 1.

No signature covers a part's Content-Disposition. It is not in the sender's h= (`h_for` names
only what the sender wrote, and it writes none), and `leaf_canon` signs a leaf's type, charset
and bytes only. So a relay with no key could add `Content-Disposition: attachment` to the
signed text leaf: the mail verified GOOD, and every reader that uses the email package's
`get_body` (`message read`, the pager preview, `_say`, the wake shelf, the sessionvoice mouth)
showed no text. Under GOOD, a signed amend with its body hidden read as a RETRACT.

Now any field named Content-Disposition, whatever its value, on any part (a leaf, a container
or the top message), is refused once the mail is otherwise good (`_sig.dispo_defects`):

- v2: `reason=disposition`, with a `part N : ❌ DISPOSITION` line, after the crypto, the floor,
  `mime-ambiguous`, `cte-defect` and the digests, so each of those keeps its own token;
- v1: `reason=v1-shape`, through `_sig.v1_defects` and the verifier's `v1_refusal`.

The 25 rows are SM-831 rca-001's (findings/rca-001/RESULTS.md: 22 v2, 3 v1), rebuilt here from
an honest `_sender.build` mail and SM-822's v1 builders, so no exhibit file ships. Every
verifier case runs under PATH's python3 AND /usr/bin/python3 (macOS 3.9).

Run: uv run pytest -q tests/test_verify_disposition.py
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sessionmail import _sender, _sig
from test_verify_mime_ambiguous import (BODY, GOLDEN, HONEST, LEAF_HTML, LEAF_PLAIN, PAGE,
                                        SYS_PY, VERIFIER)
from test_verify_mime_ambiguous import Rig as V2Rig
from test_verify_mime_ambiguous import mixed, sent, sub
from test_verify_v1_shape import Rig as V1Rig
from test_verify_v1_shape import alternative as v1_alternative
from test_verify_v1_shape import honest as v1_honest
from test_verify_v1_shape import single as v1_single

FAIL = "TRUST: FAIL leg=1-signature reason=disposition"

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")


class Rig(V2Rig):
    """SM-823's rig, which signs as the sender does, plus names appended to h= (option c':
    `content-disposition` signed ABSENT, which v2's `canonical` already supports)."""

    def sign_h(self, raw: bytes, extra_h: tuple = ()) -> bytes:
        h = _sig.h_for(raw)
        h += [n for n in extra_h if n not in h]
        p = _sig.digests(raw)
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n",
                               "session-mail@example.com"],
                              input=_sig.canonical(raw, "session-mail@example.com", h, p, ""),
                              capture_output=True, check=True)
        b = "".join(x for x in done.stdout.decode().splitlines()
                    if x and "SSH SIGNATURE" not in x)
        return _sig.insert_header(raw, _sig.format_header("session-mail@example.com", h, p, b))


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return Rig(tmp_path_factory.mktemp("sm831"))


@pytest.fixture(scope="module")
def v1rig(tmp_path_factory):
    return V1Rig(tmp_path_factory.mktemp("sm831v1"))


def _pythons():
    out = [pytest.param("python3", id="path-python3")]
    if os.access(SYS_PY, os.X_OK):
        out.append(pytest.param(SYS_PY, id="usr-bin-python3"))
    return out


@pytest.fixture(params=_pythons())
def py(request):
    return request.param


# ── the keyless edits (SM-831 repro-001 measure.py's leaf/top, lifted) ──────

CDA = b"Content-Disposition: attachment\n"
CDA_FN = b'Content-Disposition: attachment; filename="note.txt"\n'
CDA_UP = b"content-disposition: ATTACHMENT\n"
CDI = b"Content-Disposition: inline\n"
CDI_FN = b'Content-Disposition: inline; filename="note.txt"\n'
CDX = b"Content-Disposition: x-hidden\n"
CD_DUP = b"Content-Disposition: inline\nContent-Disposition: attachment\n"
CD_FOLDED = b"Content-Disposition:\n attachment\n"
CD_FN_ONLY = b'Content-Disposition: ; filename="x.txt"\n'
OLD = "<111.sm831@sm831.invalid>"


def leaf(which: bytes, add: bytes):
    """ADD after the last field of the leaf whose header block (after its boundary) is WHICH."""
    def f(raw: bytes) -> bytes:
        b = re.search(rb'boundary="([^"]+)"', raw).group(1)
        block = b"--" + b + b"\n" + which
        return sub(raw, block, block + add)
    return f


def top(add: bytes):
    """ADD right after the TOP header block's Content-Type field."""
    def f(raw: bytes) -> bytes:
        head, sep, rest = raw.partition(b"\n\n")
        h = head + b"\n"
        m = re.search(rb"^Content-Type: [^\n]*\n", h, re.M)
        return (h[:m.end()] + add + h[m.end():])[:-1] + sep + rest
    return f


def obs_top(line: bytes):
    """LINE as the LAST field of the top header block."""
    def f(raw: bytes) -> bytes:
        head, sep, rest = raw.partition(b"\n\n")
        return head + b"\n" + line.rstrip(b"\n") + sep + rest
    return f


def both(*edits):
    def f(raw: bytes) -> bytes:
        for e in edits:
            raw = e(raw)
        return raw
    return f


@pytest.fixture(scope="module")
def bases(rig):
    """The honest wires the rows edit: the sender's own, signed with the rig's key."""
    req = _sender.Request(to="mouth@sm831.invalid", from_="alice@sm831.invalid",
                          subject="SM-831 amend", supersedes=OLD)

    def amend(page):
        w = _sender.build(req, BODY, host="sm831.invalid", date="Sun, 08 Dec 2030 14:00:00 +0000",
                          message_id="<amend@sm831.invalid>", references=[], session="",
                          signature=None, page=page,
                          boundary=("=_sesh890_" + "1" * 32) if page else "")
        return rig.sign(w)

    return {
        "single": rig.sign(sent(page=None, mid="<honest-single@sm831.invalid>")),
        "alt": rig.sign(sent(mid="<honest-alt@sm831.invalid>")),
        "sup-single": amend(None),
        "sup-alt": amend(PAGE),
        "cprime-single": rig.sign_h(sent(page=None, mid="<cps@sm831.invalid>"),
                                    ("content-disposition",)),
        "cprime-alt": rig.sign_h(sent(mid="<cpa@sm831.invalid>"), ("content-disposition",)),
    }


#: (name, base, edit, every place dispo_defects names). The 22 v2 rows rca-001 measured
#: GOOD before this check and BAD rc 1 `disposition` after it (RESULTS.md).
V2_ROWS = [
    ("alt-plain-cd-attach", "alt", leaf(LEAF_PLAIN, CDA), ["part 1"]),
    ("alt-plain-cd-attach-fn", "alt", leaf(LEAF_PLAIN, CDA_FN), ["part 1"]),
    ("alt-plain-cd-attach-upper", "alt", leaf(LEAF_PLAIN, CDA_UP), ["part 1"]),
    ("alt-plain-cd-inline", "alt", leaf(LEAF_PLAIN, CDI), ["part 1"]),
    ("alt-plain-cd-inline-fn", "alt", leaf(LEAF_PLAIN, CDI_FN), ["part 1"]),
    ("alt-plain-cd-unknown", "alt", leaf(LEAF_PLAIN, CDX), ["part 1"]),
    ("alt-plain-cd-dup", "alt", leaf(LEAF_PLAIN, CD_DUP), ["part 1"]),
    ("alt-plain-cd-folded", "alt", leaf(LEAF_PLAIN, CD_FOLDED), ["part 1"]),
    ("alt-plain-cd-fn-only", "alt", leaf(LEAF_PLAIN, CD_FN_ONLY), ["part 1"]),
    ("alt-html-cd-attach", "alt", leaf(LEAF_HTML, CDA), ["part 2"]),
    ("alt-html-cd-inline", "alt", leaf(LEAF_HTML, CDI), ["part 2"]),
    ("alt-html-cd-inline-fn", "alt", leaf(LEAF_HTML, CDI_FN), ["part 2"]),
    ("alt-plain-attach-html-inline", "alt",
     both(leaf(LEAF_PLAIN, CDA), leaf(LEAF_HTML, CDI)), ["part 1", "part 2"]),
    ("alt-top-cd-attach", "alt", top(CDA), ["container multipart/alternative"]),
    ("single-top-cd-attach", "single", top(CDA), ["part 1"]),
    ("single-top-cd-attach-fn", "single", top(CDA_FN), ["part 1"]),
    ("single-top-cd-inline", "single", top(CDI), ["part 1"]),
    ("single-top-cd-inline-fn", "single", top(CDI_FN), ["part 1"]),
    ("single-top-cd-unknown", "single", top(CDX), ["part 1"]),
    ("sup-single-cd-attach", "sup-single", top(CDA), ["part 1"]),
    ("sup-alt-plain-cd-attach", "sup-alt", leaf(LEAF_PLAIN, CDA), ["part 1"]),
    ("cprime-alt-plain-cd-attach", "cprime-alt", leaf(LEAF_PLAIN, CDA), ["part 1"]),
]
V2_IDS = [r[0] for r in V2_ROWS]


def refused(rig, raw, python, wheres):
    plain = rig.run(raw, python)
    assert plain.returncode == 1, plain.stdout + plain.stderr
    assert "signature-crypto  : ✅ good" in plain.stdout, plain.stdout
    for where in wheres:
        assert re.search(rf"^{re.escape(f'{where:<18}')}: ❌ DISPOSITION$", plain.stdout,
                         re.M), plain.stdout
    assert "BAD: a Content-Disposition no signature covers" in plain.stderr, plain.stderr
    assert "Traceback" not in plain.stderr
    trusted = rig.run(raw, python, "--trust")
    assert (trusted.returncode, trusted.stdout.strip().splitlines()[-1]) == (1, FAIL)
    j = json.loads(rig.run(raw, python, "--json").stdout)
    assert (j["rc"], j["result"], j["reason"], j["signature"]) == (1, "bad", "disposition", None)


def verified(rig, raw, python):
    plain = rig.run(raw, python)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    for word in ("DISPOSITION", "MIME-AMBIGUOUS", "CTE-DEFECT", "ALTERED"):
        assert word not in plain.stdout
    trusted = rig.run(raw, python, "--trust")
    assert trusted.returncode == 4, trusted.stdout + trusted.stderr


def reason(rig, raw, python) -> str:
    return json.loads(rig.run(raw, python, "--json").stdout)["reason"]


# ── 1. _sig.dispo_defects, pure ─────────────────────────────────────────────

@pytest.mark.parametrize("name,base,edit,wheres", V2_ROWS, ids=V2_IDS)
def test_every_row_was_a_hole_before_this_check(bases, name, base, edit, wheres):
    """Each edit leaves every digest (p=) and every earlier check as it was: it verified GOOD
    without dispo_defects (rca-001 measured rc 0 at 72f2805 and at SM-823's tip)."""
    raw = edit(bases[base])
    assert raw != bases[base]
    assert _sig.digests(raw) == _sig.digests(bases[base])
    assert _sig.mime_defects(raw) == [] and _sig.cte_defects(raw) == []


@pytest.mark.parametrize("name,base,edit,wheres", V2_ROWS, ids=V2_IDS)
def test_dispo_defects_names_every_part_carrying_one(bases, name, base, edit, wheres):
    assert [w for w, _ in _sig.dispo_defects(edit(bases[base]))] == wheres


def test_dispo_defects_counts_the_fields_and_reads_them_by_name(bases):
    alt = bases["alt"]
    assert _sig.dispo_defects(leaf(LEAF_PLAIN, CDA)(alt)) == [
        ("part 1", "1 Content-Disposition field, which no signature covers")]
    assert _sig.dispo_defects(leaf(LEAF_PLAIN, CD_DUP)(alt)) == [
        ("part 1", "2 Content-Disposition fields, which no signature covers")]
    for spelling in (b"CONTENT-DISPOSITION: inline\n", b"Content-disposition:inline\n",
                     b"Content-Disposition: \n", b"Content-Disposition: attachment (c)\n"):
        assert [w for w, _ in _sig.dispo_defects(leaf(LEAF_PLAIN, spelling)(alt))] == ["part 1"]


def test_a_container_inside_a_mixed_is_labelled_by_its_type():
    """SM-823's mixed() shape: the CD on its PDF leaf is part 3; a CD added to the inner
    alternative is that container."""
    raw = mixed()
    assert _sig.dispo_defects(raw) == [
        ("part 3", "1 Content-Disposition field, which no signature covers")]
    inner = re.search(rb"^Content-Type: multipart/alternative;[^\n]*\n", raw, re.M).group(0)
    two = sub(raw, inner, inner + CDI)
    assert [w for w, _ in _sig.dispo_defects(two)] == ["container multipart/alternative",
                                                       "part 3"]


def test_nothing_honest_carries_one():
    """The golden vectors, SM-823's honest controls and their v1 shapes: no CD at all."""
    files = (sorted(GOLDEN.glob("*.eml")) + sorted(GOLDEN.glob("*.wire"))
             + sorted((HONEST / "controls").glob("*.eml")))
    assert len(files) == 39
    assert [f.name for f in files if _sig.dispo_defects(f.read_bytes())] == []


def test_digests_never_call_the_check(monkeypatch):
    def boom(raw):
        raise AssertionError("digests() must not call dispo_defects()")

    monkeypatch.setattr(_sig, "dispo_defects", boom)
    manifest = json.loads((GOLDEN / "vectors.json").read_text())["vectors"]
    for name, v in manifest.items():
        assert _sig.digests((GOLDEN / f"{name}.eml").read_bytes()) == v["p"]


# ── 2. v2: the 22 rows are BAD, reason=disposition ──────────────────────────

@pytest.mark.parametrize("name,base,edit,wheres", V2_ROWS, ids=V2_IDS)
def test_every_v2_row_is_refused(rig, bases, py, name, base, edit, wheres):
    refused(rig, edit(bases[base]), py, wheres)


def test_the_v2_rows_are_the_22_measured():
    assert len(V2_ROWS) == 22 and len(set(V2_IDS)) == 22


def test_a_signer_who_writes_one_is_refused_too(rig, py):
    """The rule reads the mail, not who made it: a key holder's own CD is refused as well."""
    raw = rig.sign(leaf(LEAF_PLAIN, CDA)(sent(mid="<signer-cd@sm831.invalid>")))
    refused(rig, raw, py, ["part 1"])


# ── 3. v1: the 3 rows are BAD, reason=v1-shape ──────────────────────────────

V1_PLAIN = b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n"
V1_ROWS = [
    ("v1-alt-plain-cd-attach", "alt", leaf(V1_PLAIN, CDA), "part 1"),
    ("v1-alt-top-cd-attach", "alt", top(CDA), "container multipart/alternative"),
    ("v1-single-top-cd-attach", "single", top(CDA), "part 1"),
]


@pytest.mark.parametrize("name,base,edit,where", V1_ROWS, ids=[r[0] for r in V1_ROWS])
def test_every_v1_row_is_refused_as_v1_shape(v1rig, py, name, base, edit, where):
    honest = v1_alternative(v1rig) if base == "alt" else v1_single(v1rig)
    raw = edit(honest)
    assert _sig.v1_defects(honest) == []
    assert _sig.v1_defects(raw) == [
        ("v1-shape", where, "1 Content-Disposition field, which no signature covers")]
    r = v1rig.run(raw, py)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"v1-shape          : ❌ V1-SHAPE: {where}: 1 Content-Disposition" in r.stdout, r.stdout
    t = v1rig.run(raw, py, "--trust", "--quiet")
    assert (t.returncode, t.stdout.strip().splitlines()[-1]) == (
        1, "TRUST: FAIL leg=1-signature reason=v1-shape")
    j = json.loads(v1rig.run(raw, py, "--json").stdout)
    assert (j["rc"], j["result"], j["reason"]) == (1, "bad", "v1-shape")


def test_a_v1_cte_defect_still_wins_over_a_disposition(v1rig, py):
    """v1_refusal's rule: cte-defect wins when both apply, and SM-831 adds only v1-shape."""
    raw = top(CDA)(v1_single(v1rig)).replace(b"Content-Transfer-Encoding: 8bit\n",
                                             b"Content-Transfer-Encoding: base64\n", 1)
    assert reason(v1rig, raw, py) == "cte-defect"


# ── 4. precedence: an earlier refusal keeps its own token ───────────────────

def test_a_failed_crypto_stays_bad_signature(rig, bases, py):
    """Option c': a sender that names content-disposition in h= (signed absent) makes a
    keyless TOP-block CD break the crypto, before this check is reached."""
    for base in ("cprime-single", "cprime-alt"):
        raw = top(CDA)(bases[base])
        assert reason(rig, raw, py) == "bad-signature"
        assert "DISPOSITION" not in rig.run(raw, py).stdout


def test_an_altered_part_stays_part_altered(rig, bases, py):
    raw = leaf(LEAF_PLAIN, CDA)(bases["alt"]).replace(b"Pay Al", b"Pay Ml", 1)
    assert _sig.dispo_defects(raw)
    assert reason(rig, raw, py) == "part-altered"


def test_a_loose_cte_stays_cte_defect(rig, bases, py):
    raw = leaf(LEAF_PLAIN, CDA + b"Content-Transfer-Encoding: 8bit\n")(bases["alt"])
    assert _sig.dispo_defects(raw) and _sig.cte_defects(raw)
    assert reason(rig, raw, py) == "cte-defect"


def test_a_split_mime_reading_stays_mime_ambiguous(rig, bases, py):
    raw = leaf(LEAF_PLAIN, CDA + b"Content-Type: text/html; charset=utf-8\n")(bases["alt"])
    assert _sig.dispo_defects(raw) and _sig.mime_defects(raw)
    assert reason(rig, raw, py) == "mime-ambiguous"


def test_the_obs_form_on_a_multipart_top_needs_sm823(rig, bases, py):
    """`Content-Disposition : attachment` (a space before the colon) is no field to the email
    package, so dispo_defects never sees it. On a multipart's top block it ended that block
    early and still verified at 72f2805; SM-823's R3 refuses it, mime-ambiguous. This pins
    the dependency."""
    raw = obs_top(b"Content-Disposition : attachment\n")(bases["alt"])
    assert _sig.dispo_defects(raw) == []
    assert reason(rig, raw, py) == "mime-ambiguous"


def test_a_traceback_in_the_check_fails_closed(rig, bases, tmp_path):
    lone = tmp_path / "mhs"
    lone.mkdir()
    shutil.copy(VERIFIER, lone / "session-mail-verify")
    sig = Path(inspect.getsourcefile(_sig)).read_text()
    (tmp_path / "_sig.py").write_text(
        sig + "\n\ndef dispo_defects(raw):\n    raise RuntimeError('broken')\n")
    done = rig.run(bases["alt"], "python3", "--trust", verifier=lone / "session-mail-verify")
    assert (done.returncode, done.stdout.strip().splitlines()[-1]) == (1, FAIL)
    assert "Traceback" not in done.stderr
    assert "cannot be checked (RuntimeError)" in done.stderr


# ── 5. honest mail is never refused ─────────────────────────────────────────

def _long_line_body() -> bytes:
    return b"x" * 1200 + b"\nshort\n"


def honest_v2(rig) -> dict[str, bytes]:
    """What the sender writes, signed: single and alt, a quoted-printable leaf (SM-818), an
    empty body, `x`, and a signed amend."""
    out = {"single": rig.sign(sent(page=None, mid="<h1@sm831.invalid>")),
           "alt": rig.sign(sent(mid="<h2@sm831.invalid>")),
           "qp-single": rig.sign(sent(_long_line_body(), None, mid="<h3@sm831.invalid>")),
           "qp-alt": rig.sign(sent(_long_line_body(), b"<p>" + b"x" * 1200 + b"</p>\n",
                                   mid="<h4@sm831.invalid>")),
           "empty": rig.sign(sent(b"", None, mid="<h5@sm831.invalid>")),
           "x": rig.sign(sent(b"x", None, mid="<h6@sm831.invalid>"))}
    assert b"quoted-printable" in out["qp-single"] and b"quoted-printable" in out["qp-alt"]
    return out


def test_honest_v2_is_good(rig, py):
    for name, raw in honest_v2(rig).items():
        assert _sig.dispo_defects(raw) == [], name
        verified(rig, raw, py)


def test_honest_amends_are_good(rig, bases, py):
    for base in ("sup-single", "sup-alt", "cprime-single", "cprime-alt"):
        verified(rig, bases[base], py)


def test_honest_v1_is_good(v1rig, py):
    """SM-822's 18 honest v1 shapes, Ivan's three wake shapes among them."""
    shapes = v1_honest(v1rig)
    assert len(shapes) == 18
    for name, raw in shapes.items():
        assert _sig.v1_defects(raw) == [], name
        r = v1rig.run(raw, py)
        assert r.returncode == 0, (name, r.stdout, r.stderr)


@pytest.mark.parametrize("f", sorted(GOLDEN.glob("*.eml")), ids=lambda f: f.name)
def test_every_golden_vector_still_verifies(f, py, tmp_path):
    rig = Rig.__new__(Rig)
    rig.d, rig.registry, rig.n = tmp_path, tmp_path / "registry", 0
    plain = rig.run(f.read_bytes(), py)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "DISPOSITION" not in plain.stdout


#: SM-823's 19 honest controls, split at COLLECTION by the signature header: 13 signed, and
#: SM-819's 6 transport controls (`sm819-ctl-*`), which ship unsigned. Each half has its own
#: test, so neither skips (SM-831 fix-002, ruled).
CONTROLS = sorted((HONEST / "controls").glob("*.eml"))
SIGNED_CONTROLS = [f for f in CONTROLS if _sig.signature_value(f.read_bytes()) is not None]
UNSIGNED_CONTROLS = [f for f in CONTROLS if _sig.signature_value(f.read_bytes()) is None]


def test_the_controls_are_13_signed_and_6_unsigned():
    """The pin: a filter that dropped every control would leave the two tests below with no
    parameters, and this count fails."""
    assert (len(CONTROLS), len(SIGNED_CONTROLS), len(UNSIGNED_CONTROLS)) == (19, 13, 6)
    assert [f.name for f in UNSIGNED_CONTROLS] == [
        f"sm819-ctl-{cte}--{shape}.eml"
        for cte in ("8bit-clean", "b64-clean", "qp-sm818") for shape in ("multi", "single")]


@pytest.mark.parametrize("f", SIGNED_CONTROLS, ids=lambda f: f.name)
def test_every_signed_honest_control_still_verifies(f, py, tmp_path):
    raw = f.read_bytes()
    rig = Rig.__new__(Rig)
    rig.d, rig.registry, rig.n = tmp_path, tmp_path / "registry", 0
    plain = rig.run(raw, py)
    assert plain.returncode == 0, plain.stdout + plain.stderr


@pytest.mark.parametrize("f", UNSIGNED_CONTROLS, ids=lambda f: f.name)
def test_every_unsigned_honest_control_is_still_unsigned(f, py, tmp_path):
    """SM-819's 6 transport controls carry no signature: rc 2 under the rule, as at 00ed0f4
    (measured: SM-831 findings/fix-002/probe.out), and no DISPOSITION line."""
    rig = Rig.__new__(Rig)
    rig.d, rig.registry, rig.n = tmp_path, tmp_path / "registry", 0
    plain = rig.run(f.read_bytes(), py)
    assert plain.returncode == 2, plain.stdout + plain.stderr
    assert "DISPOSITION" not in plain.stdout


def test_other_mua_verdicts_do_not_change(py, tmp_path):
    """SM-823's 21 other-MUA samples are unsigned: rc 2 with the rule as without it, though
    the rule alone fires on 11 of them (rca-001). That is why it runs only after a good
    crypto."""
    rig = Rig.__new__(Rig)
    rig.d, rig.registry, rig.n = tmp_path, tmp_path / "registry", 0
    files = sorted((HONEST / "mua").glob("*.eml"))
    assert len(files) == 21
    assert sum(1 for f in files if _sig.dispo_defects(f.read_bytes())) == 11
    for f in files:
        done = rig.run(f.read_bytes(), py)
        assert done.returncode == 2, (f.name, done.stdout, done.stderr)
        assert "DISPOSITION" not in done.stdout


# ── 6. the sender writes none: the tripwire SM-784 (`--attach`) trips on purpose ──

def test_sender_writes_no_disposition():
    for body in (BODY, b"", b"x", _long_line_body()):
        for page in (None, PAGE):
            wire = sent(body, page)
            assert _sig.dispo_defects(wire) == [], (body[:10], page is None)
            assert b"content-disposition" not in wire.lower()


# ── 8. the mouth's side: a FAIL it reads as unsigned (sessionvoice parse_verdict) ──

def test_the_mouth_gets_a_fail_for_a_hidden_amend(rig, bases, py):
    """sessionvoice's mouth keys on trust.verdict (FULL or UNBLESSED is signed; anything else
    is not). Under GOOD a keyless CD on a signed amend made it a RETRACT of another sender's
    line (rca-001 MOUTH.md); now the verdict is FAIL, so the amend reads as unsigned."""
    raw = leaf(LEAF_PLAIN, CDA)(bases["sup-alt"])
    done = rig.run(raw, py, "--json", "--trust")
    j = json.loads(done.stdout)
    assert done.returncode == 1
    assert (j["reason"], j["trust"]["verdict"], j["trust"]["leg"]) == (
        "disposition", "FAIL", "1-signature")
    honest = json.loads(rig.run(bases["sup-alt"], py, "--json", "--trust").stdout)
    assert honest["trust"]["verdict"] == "UNBLESSED"


# ── the same answers under the system python (3.9), loaded BY PATH ──────────

def test_dispo_defects_is_the_same_under_the_system_python(rig, bases, v1rig):
    if not os.access(SYS_PY, os.X_OK):
        pytest.skip("no /usr/bin/python3")
    cases = [edit(bases[base]) for _, base, edit, _ in V2_ROWS]
    cases += [edit(v1_alternative(v1rig) if b == "alt" else v1_single(v1rig))
              for _, b, edit, _ in V1_ROWS]
    cases += [f.read_bytes() for f in sorted((HONEST / "mua").glob("*.eml"))]
    cases += list(honest_v2(rig).values()) + list(v1_honest(v1rig).values())
    prog = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('s',sys.argv[1]);m=importlib.util.module_from_spec(s)\n"
            "s.loader.exec_module(m)\n"
            "print(json.dumps([m.dispo_defects(bytes.fromhex(x)) for x in json.load(sys.stdin)]))\n")
    done = subprocess.run([SYS_PY, "-I", "-c", prog, inspect.getsourcefile(_sig)],
                          input=json.dumps([c.hex() for c in cases]), capture_output=True,
                          text=True, check=True)
    assert json.loads(done.stdout) == [[list(x) for x in _sig.dispo_defects(c)] for c in cases]
