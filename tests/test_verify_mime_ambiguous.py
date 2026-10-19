"""SM-823: a v2 mail two MIME readers may split differently is refused, `reason=mime-ambiguous`.

p= signs each leaf's type and charset as Python's email package reads them, and no h= covers a
multipart part's own header block. Two readings differ there with no key:

- R1: a part with two Content-Type fields. The email package reads the FIRST, GMime (notmuch)
  the LAST;
- R2p: a bare CR. The email package ends a line at one, GMime does not, so a CR at a delimiter
  or in a part's header block moves what one reader sees and not the other.

So an honest signed mail, edited with no key, could still verify while notmuch showed other
text. The verifier now refuses it: BAD, rc 1, `TRUST: FAIL leg=1-signature
reason=mime-ambiguous`, after a good crypto and before the cte-defect check.

The 13 LIVE rows are the task's relay measurement (SM-823 findings/repro-001/relay), rebuilt
here from an honest `_sender.build` mail, so no exhibit file is shipped. Each verifier case
runs under PATH's python3 AND /usr/bin/python3 (macOS 3.9), the two the verifier meets.

The HONEST set (68 files, the design's false-positive count): the 20 golden vector files, 19
honest controls and 21 other-MUA shapes under tests/fixtures/mime_honest/, and 8 honest v2
mails whose body holds a bare CR, built here by the sender.

Run: uv run pytest -q tests/test_verify_mime_ambiguous.py
"""
import base64
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sessionmail import _mhs, _sender, _sig

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
NS = "session-mail@example.com"
FAIL = "TRUST: FAIL leg=1-signature reason=mime-ambiguous"
SYS_PY = "/usr/bin/python3"
HERE = Path(__file__).parent
GOLDEN = HERE / "fixtures" / "sig_v2"
HONEST = HERE / "fixtures" / "mime_honest"

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
            f"session-birth-v1\nsession-id: {sid}\npubkey: {kt} {kb}\nagent: alice\n")
        self.n = 0

    def sign(self, raw: bytes) -> bytes:
        h, p = _sig.h_for(raw), _sig.digests(raw)
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n", NS],
                              input=_sig.canonical(raw, NS, h, p, ""), capture_output=True,
                              check=True)
        b = "".join(x for x in done.stdout.decode().splitlines()
                    if x and "SSH SIGNATURE" not in x)
        return _sig.insert_header(raw, _sig.format_header(NS, h, p, b))

    def env(self, python: str) -> dict:
        shim = self.d / ("py-" + hashlib.sha256(python.encode()).hexdigest()[:8])
        if not shim.exists():
            shim.mkdir()
            (shim / "python3").symlink_to(shutil.which(python) or python)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SESSIONMAIL_ORIGIN", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_VERIFY_SELF",
                        "SESSIONMAIL_PYTHON")}
        env["SESSION_REGISTRY"] = str(self.registry)
        env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
        return env

    def run(self, raw: bytes, python: str, *flags, verifier=VERIFIER):
        self.n += 1
        f = self.d / f"mail{self.n}.eml"
        f.write_bytes(raw)
        return subprocess.run([str(verifier), str(f), *flags], capture_output=True, text=True,
                              env=self.env(python))


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return Rig(tmp_path_factory.mktemp("sm823"))


def _pythons():
    out = [pytest.param("python3", id="path-python3")]
    if os.access(SYS_PY, os.X_OK):
        out.append(pytest.param(SYS_PY, id="usr-bin-python3"))
    return out


@pytest.fixture(params=_pythons())
def py(request):
    return request.param


def refused(rig, raw, python, where):
    plain = rig.run(raw, python)
    assert plain.returncode == 1, plain.stdout + plain.stderr
    assert "signature-crypto  : ✅ good" in plain.stdout, plain.stdout
    label = re.escape(f"{where:<18}") if where else r"[^\n]+?"
    assert re.search(rf"^{label}: ❌ MIME-AMBIGUOUS", plain.stdout, re.M), plain.stdout
    assert "BAD: a MIME structure readers may split differently" in plain.stderr
    assert "Traceback" not in plain.stderr
    trusted = rig.run(raw, python, "--trust")
    assert (trusted.returncode, trusted.stdout.strip().splitlines()[-1]) == (1, FAIL)
    j = json.loads(rig.run(raw, python, "--json").stdout)
    assert (j["rc"], j["result"], j["reason"], j["signature"]) == (1, "bad", "mime-ambiguous", None)


def verified(rig, raw, python):
    plain = rig.run(raw, python)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    for word in ("MIME-AMBIGUOUS", "CTE-DEFECT", "ALTERED"):
        assert word not in plain.stdout
    trusted = rig.run(raw, python, "--trust")
    assert trusted.returncode == 4, trusted.stdout + trusted.stderr


# ── the honest mail a send writes, and the relay's keyless edits ────────────

BODY = ("Pay Alïce 100 €.\n\n---\n\nOnly after the audit <ref 7> passes.\n"
        "Thanks, Bob.\n").encode()
PAGE = ("<p>Pay Alïce 100 €.</p>\n<hr>\n<p>Only after the audit &lt;ref 7&gt; passes.<br>\n"
        "Thanks, Bob.</p>\n").encode()
BOUNDARY = "=_sesh890_" + "0" * 32
DELIM = b"--" + BOUNDARY.encode()
LEAF_PLAIN = b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n"
LEAF_HTML = b"Content-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: 8bit\n"


def sent(body: bytes = BODY, page=PAGE, mid="<honest-alt@sm823.invalid>") -> bytes:
    """What an honest send writes: the sender's own `build`, unchanged."""
    req = _sender.Request(to="bob@sm823.invalid", from_="alice@sm823.invalid",
                          subject="SM-823 relay")
    return _sender.build(req, body, host="sm823.invalid", date="Wed, 02 Jan 2030 11:00:00 +0000",
                         message_id=mid, references=[], session="", signature=None, page=page,
                         boundary=BOUNDARY if page else "")


@pytest.fixture(scope="module")
def honest(rig):
    return rig.sign(sent())


def sub(raw: bytes, old: bytes, new: bytes) -> bytes:
    assert raw.count(old) == 1, (old, raw.count(old))
    return raw.replace(old, new)


def leaf1_after(extra: bytes):
    return lambda r: sub(r, DELIM + b"\n" + LEAF_PLAIN, DELIM + b"\n" + LEAF_PLAIN + extra)


#: (name, edit, where the verifier names it). The 13 rows measured LIVE at origin/master
#: 1c4ee10, SM-819 85f721a and SM-822 30961a1, on py3.13 and py3.9 (repro-001 relay/RESULTS.md).
LIVE = [
    ("alt-ct-dup-charset", leaf1_after(b"Content-Type: text/plain; charset=iso-8859-1\n"), "part 1"),
    ("alt-ct-dup-html", leaf1_after(b"Content-Type: text/html; charset=utf-8\n"), "part 1"),
    ("alt-ct-dup-multipart", leaf1_after(b'Content-Type: multipart/mixed; boundary="-"\n'), "part 1"),
    ("alt-ct-dup-rfc822", leaf1_after(b"Content-Type: message/rfc822\n"), "part 1"),
    ("alt-ct-dup-octet", leaf1_after(b"Content-Type: application/octet-stream\n"), "part 1"),
    ("alt-ct-dup-utf16", leaf1_after(b"Content-Type: text/plain; charset=utf-16\n"), "part 1"),
    ("alt-leaf2-ct-dup-plain",
     lambda r: sub(r, LEAF_HTML, LEAF_HTML + b"Content-Type: text/plain; charset=utf-8\n"), "part 2"),
    ("alt-boundary-cr",
     lambda r: sub(r, DELIM + b"\n" + LEAF_PLAIN, DELIM + b"\r" + LEAF_PLAIN), "the mail"),
    ("alt-cr-before-leaf2",
     lambda r: sub(r, b"\n" + DELIM + b"\n" + LEAF_HTML, b"\r" + DELIM + b"\n" + LEAF_HTML), "the mail"),
    ("alt-cr-before-close", lambda r: sub(r, b"\n" + DELIM + b"--", b"\r" + DELIM + b"--"), "the mail"),
    ("alt-boundary-ws-cr",
     lambda r: sub(r, DELIM + b"\n" + LEAF_PLAIN, DELIM + b" \t\r" + LEAF_PLAIN), "the mail"),
    ("alt-leaf2-boundary-cr",
     lambda r: sub(r, DELIM + b"\n" + LEAF_HTML, DELIM + b"\r" + LEAF_HTML), "the mail"),
    ("alt-leaf2-cr-hide-ct",
     lambda r: sub(r, DELIM + b"\n" + LEAF_HTML, DELIM + b"\nX-Hide: y\r" + LEAF_HTML), "the mail"),
]


def test_the_honest_mail_is_the_measured_shape(honest):
    assert DELIM + b"\n" + LEAF_PLAIN in honest and DELIM + b"\n" + LEAF_HTML in honest
    assert _sig.leaf_types(honest) == ["text/plain", "text/html"]
    assert _sig.mime_defects(honest) == [] and _sig.cte_defects(honest) == []


def test_the_honest_mail_verifies(rig, honest, py):
    verified(rig, honest, py)


@pytest.mark.parametrize("name,edit,where", LIVE, ids=[r[0] for r in LIVE])
def test_every_live_row_was_a_hole_before_this_check(honest, name, edit, where):
    """Without mime_defects each row verified GOOD: the edit leaves every digest (p=) and the
    signed header block as they were, and SM-819's check finds nothing."""
    raw = edit(honest)
    assert raw != honest
    assert _sig.digests(raw) == _sig.digests(honest)
    assert _sig.cte_defects(raw) == []
    assert _sig.header_block(raw) == _sig.header_block(honest)


@pytest.mark.parametrize("name,edit,where", LIVE, ids=[r[0] for r in LIVE])
def test_every_live_row_is_refused(rig, honest, py, name, edit, where):
    raw = edit(honest)
    assert where in dict(_sig.mime_defects(raw))
    refused(rig, raw, py, where)


def test_a_second_content_type_is_named_with_its_count(honest):
    raw = LIVE[1][1](honest)
    assert _sig.mime_defects(raw) == [("part 1", "2 Content-Type fields")]


def test_the_top_message_and_a_container_are_counted_too():
    """R1 covers every header block it parses (the top one is in h=, and already rc 1 when a
    relay adds a field; a signer who wrote two is refused by this rule)."""
    single = sent(page=None)
    top = sub(single, b"Content-Type: text/plain; charset=utf-8\n",
              b"Content-Type: text/plain; charset=utf-8\nContent-Type: text/html\n")
    assert _sig.mime_defects(top) == [("part 1", "2 Content-Type fields")]
    alt = sent()
    m = re.search(rb"^Content-Type: multipart/alternative;[^\n]*\n(?:[ \t][^\n]*\n)*", alt, re.M)
    two = alt[:m.end()] + b"Content-Type: text/plain\n" + alt[m.end():]
    assert ("container multipart/alternative", "2 Content-Type fields") in _sig.mime_defects(two)


def test_a_signer_crafted_duplicate_is_refused_too(rig, py):
    """A key holder who signs a two-Content-Type part gets the same refusal: the rule reads
    the mail, not who made it."""
    raw = rig.sign(LIVE[1][1](sent()))
    refused(rig, raw, py, "part 1")


def test_mime_ambiguous_wins_over_cte_defect(rig, honest, py):
    """A CR-shifted structure makes the encoding's reading ambiguous too: the structural
    reason is the true one, so it is the one reported."""
    raw = sub(honest, DELIM + b"\n" + LEAF_PLAIN,
              DELIM + b"\n" + LEAF_PLAIN.replace(b"8bit\n", b"8bit\nContent-Transfer-Encoding: base64\n")
              + b"Content-Type: text/html\n")
    assert _sig.cte_defects(raw) and _sig.mime_defects(raw)
    refused(rig, raw, py, "part 1")
    plain = rig.run(raw, py)
    assert "CTE-DEFECT" not in plain.stdout


# ── R3: a header block the email package ends early (the review's class B) ──
# DELETE the blank line that ends a header block, with no key and no CR. The email package
# meets a body line it cannot read as a field (MissingHeaderBodySeparatorDefect) and hashes it
# as body, so p= is unchanged; GMime skips that line and reads on to the next blank one, so it
# hides the leaf's first paragraph, or a whole page (SM-823 findings/fix-001-review, lfprobe).

MIX_B = "=_sm823_mixed_0000"
PDF = b"%PDF-1.4\n%sm823\n"


def mixed() -> bytes:
    """A 3-leaf multipart/mixed { multipart/alternative { text/plain, text/html }, an
    application/pdf attachment }, made from the sender's own alternative (the review's base)."""
    w = sent(mid="<honest-mixed@sm823.invalid>")
    head, _, body = w.partition(b"\n\n")
    alt_ct = re.search(rb"^Content-Type: [^\n]*\n", head + b"\n", re.M).group(0)
    head = (head + b"\n").replace(
        alt_ct, f'Content-Type: multipart/mixed; boundary="{MIX_B}"\n'.encode())[:-1]
    return (head + b"\n\n" + f"--{MIX_B}\n".encode() + alt_ct + b"\n" + body
            + f"\n--{MIX_B}\n".encode()
            + b'Content-Type: application/pdf; name="audit.pdf"\n'
            + b'Content-Disposition: attachment; filename="audit.pdf"\n'
            + b"Content-Transfer-Encoding: base64\n\n" + base64.encodebytes(PDF)
            + f"\n--{MIX_B}--\n".encode())


def separators(raw: bytes) -> list:
    """The index of the LF that makes the blank line after each header block: the top
    block's, then each part's (the first blank line after each delimiter line)."""
    out = [raw.index(b"\n\n") + 1]
    for m in re.finditer(rb"^--[^\n]*\n", raw, re.M):
        if not m.group(0).rstrip(b"\n").endswith(b"--"):
            out.append(raw.index(b"\n\n", m.start()) + 1)
    return sorted(set(out))


@pytest.fixture(scope="module")
def bases(rig):
    return {"single": rig.sign(sent(page=None, mid="<honest-single@sm823.invalid>")),
            "alt": rig.sign(sent()), "mixed": rig.sign(mixed())}


#: (base, separator): every deletion that leaves p= AND the signed header text unchanged, so
#: it verified GOOD before R3: the top block of a single part, and every part's block.
CLASS_B = [("single", 0), ("alt", 1), ("alt", 2),
           ("mixed", 1), ("mixed", 2), ("mixed", 3), ("mixed", 4)]
#: A multipart's TOP separator: _sig's own header block then runs on into leaf 1's fields, so
#: h= reads a second Content-Type and the crypto fails (rc 1 already). R3 flags it as well.
TOP_MULTI = [("alt", 0), ("mixed", 0)]


def test_the_bases_have_the_separators_named(bases):
    assert [len(separators(bases[b])) for b in ("single", "alt", "mixed")] == [1, 3, 5]
    assert _sig.leaf_types(bases["mixed"]) == ["text/plain", "text/html", "application/pdf"]
    for raw in bases.values():
        assert _sig.mime_defects(raw) == [] and _sig.cte_defects(raw) == []


def _deleted(raw: bytes, k: int) -> bytes:
    i = separators(raw)[k]
    assert raw[i:i + 1] == b"\n" and raw[i - 1:i] == b"\n"
    return raw[:i] + raw[i + 1:]


@pytest.mark.parametrize("base,k", CLASS_B, ids=[f"{b}-sep{k}" for b, k in CLASS_B])
def test_a_deleted_header_separator_was_a_hole_before_this_rule(bases, base, k):
    """No key, no CR: what p= covers is unchanged, and so is every field h= signs."""
    raw = _deleted(bases[base], k)
    assert b"\r" not in raw
    assert _sig.digests(raw) == _sig.digests(bases[base])
    h = _sig.parse_v2(_sig.parse_header(_sig.signature_value(raw)))[1]
    assert _sig.canonical(raw, NS, h, [], "") == _sig.canonical(bases[base], NS, h, [], "")


@pytest.mark.parametrize("base,k", CLASS_B, ids=[f"{b}-sep{k}" for b, k in CLASS_B])
def test_a_deleted_header_separator_is_refused(rig, bases, py, base, k):
    raw = _deleted(bases[base], k)
    got = _sig.mime_defects(raw)
    assert any(why == "a header block this parser ends at a line it cannot read"
               for _, why in got), got
    refused(rig, raw, py, None)


@pytest.mark.parametrize("base,k", CLASS_B, ids=[f"{b}-sep{k}" for b, k in CLASS_B])
def test_a_header_separator_made_a_bare_cr_is_refused(rig, bases, py, base, k):
    """The review's x-crsub form of the same edit: the LF before the blank line becomes a CR.
    The pair is then CRLF, which lf() reads as one line end, so the blank line is gone."""
    i = separators(bases[base])[k] - 1
    raw = bases[base][:i] + b"\r" + bases[base][i + 1:]
    assert _sig.lf(raw) == _deleted(bases[base], k)
    got = _sig.mime_defects(raw)
    assert any(why == "a header block this parser ends at a line it cannot read"
               for _, why in got), got
    refused(rig, raw, py, None)


@pytest.mark.parametrize("base,k", TOP_MULTI, ids=[f"{b}-sep{k}" for b, k in TOP_MULTI])
def test_a_deleted_top_separator_of_a_multipart_is_bad_and_flagged(rig, bases, py, base, k):
    raw = _deleted(bases[base], k)
    assert _sig.digests(raw) == _sig.digests(bases[base])
    assert (f"container {_sig.email.message_from_bytes(raw).get_content_type()}",
            "a header block this parser ends at a line it cannot read") in _sig.mime_defects(raw)
    plain = rig.run(raw, py)
    assert plain.returncode == 1 and "signature-crypto  : ❌ BAD" in plain.stdout, plain.stdout


def test_the_top_block_is_named_as_the_mail_part_or_container(bases):
    assert ("part 1", "a header block this parser ends at a line it cannot read") in \
        _sig.mime_defects(_deleted(bases["single"], 0))
    assert ("container multipart/alternative",
            "a header block this parser ends at a line it cannot read") in \
        _sig.mime_defects(_deleted(bases["alt"], 0))


def test_an_inserted_unreadable_line_is_refused_too(rig, py):
    """The signer-crafted form (the task's `garbage-then-plain-field`), signed as it stands."""
    raw = sub(sent(), DELIM + b"\n" + LEAF_PLAIN,
              DELIM + b"\n" + LEAF_PLAIN + b"X garbage line\nX-Foo: bar\n")
    refused(rig, rig.sign(raw), py, "part 1")


# ── honest mail still verifies ──────────────────────────────────────────────

CR_BODIES = {
    "honest-cr-progress": b"progress 10%\rprogress 100%\ndone\n",
    "honest-cr-linestart": b"line one\n\rline two\nend\n",
    "honest-cr-dashes": b"table\r--- not a boundary ---\r\nend\n",
    "honest-cr-header-like": b"note\rContent-Type: text/html\nend\n",
}
CR_MAILS = {f"{name}--{shape}": (body, page)
            for name, body in CR_BODIES.items()
            for shape, page in (("single", None), ("alt", b"<p>x</p>\n"))}


@pytest.mark.parametrize("name", CR_MAILS, ids=list(CR_MAILS))
def test_an_honest_body_with_a_bare_cr_verifies(rig, py, name):
    """The honest sender refuses only NUL, so an 8bit leaf may carry a bare CR: such a CR is
    body text to both readers and changes neither parse."""
    body, page = CR_MAILS[name]
    _sender.check_body(body)
    wire = sent(body, page, mid=f"<{name}@sm823.invalid>")
    assert b"\r" in _sig.lf(wire)
    assert _sig.leaves(wire)[0].get_payload(decode=True) == _sig.lf(body)
    assert _sig.mime_defects(wire) == []
    verified(rig, rig.sign(wire), py)


def test_an_honest_crlf_mail_verifies(rig, honest, py):
    verified(rig, honest.replace(b"\n", b"\r\n"), py)


#: sha256 over (relative name, sha256(bytes)) of every file under tests/fixtures/mime_honest/:
#: the corpus repro-001 counted, copied byte for byte (CRLF files included).
#: Re-pinned at SM-855's merge of origin/master 88a6e10: SM-862 re-minted the 13 signed
#: controls (sm822 x7, sm823 x6) and SM-855 fix-005 moved the Dates of 24 unsigned files
#: (18 mua, 6 sm819) to synthetic values; the sets are disjoint. Re-pinned by SM-869: javamail-folded's boundary epoch.
HONEST_SHA256 = "394382a3f2567a386d11922dca9862705a1a6023d519e625e54c50b55cdb6dae"


def honest_files() -> list:
    golden = sorted(GOLDEN.glob("*.eml")) + sorted(GOLDEN.glob("*.wire"))
    return golden + sorted((HONEST / "controls").glob("*.eml")) + sorted((HONEST / "mua").glob("*.eml"))


def test_the_honest_corpus_is_the_one_measured():
    files = honest_files()
    assert len(files) + len(CR_MAILS) == 68
    assert (len(list(GOLDEN.glob("*.eml"))), len(list(GOLDEN.glob("*.wire")))) == (10, 10)
    assert len(list((HONEST / "controls").glob("*.eml"))) == 19
    assert len(list((HONEST / "mua").glob("*.eml"))) == 21
    h = hashlib.sha256()
    for f in sorted(HONEST.rglob("*.eml")):
        h.update(str(f.relative_to(HONEST)).encode() + b"\0" + hashlib.sha256(f.read_bytes()).digest())
    assert h.hexdigest() == HONEST_SHA256


@pytest.mark.parametrize("f", honest_files(), ids=lambda f: f"{f.parent.name}/{f.name}")
def test_no_honest_file_is_ambiguous(f):
    assert _sig.mime_defects(f.read_bytes()) == []


@pytest.mark.parametrize("f", sorted(GOLDEN.glob("*.eml")), ids=lambda f: f.name)
def test_every_golden_vector_still_verifies(f, py, tmp_path):
    rig = Rig.__new__(Rig)
    rig.d, rig.registry, rig.n = tmp_path, tmp_path / "registry", 0
    plain = rig.run(f.read_bytes(), py)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "MIME-AMBIGUOUS" not in plain.stdout


# ── the same answers under the system python (3.9), loaded BY PATH ──────────

def _by_path_under_sys_py(cases: list) -> list:
    prog = ("import importlib.util,json,sys\n"
            "s=importlib.util.spec_from_file_location('s',sys.argv[1]);m=importlib.util.module_from_spec(s)\n"
            "s.loader.exec_module(m)\n"
            "print(json.dumps([m.mime_defects(bytes.fromhex(x)) for x in json.load(sys.stdin)]))\n")
    done = subprocess.run([SYS_PY, "-I", "-c", prog, inspect.getsourcefile(_sig)],
                          input=json.dumps([c.hex() for c in cases]), capture_output=True,
                          text=True, check=True)
    return json.loads(done.stdout)


def test_mime_defects_is_the_same_under_the_system_python():
    """The 13 live rows and the 9 separator deletions are refused, and the 68 honest files are
    not, by `_sig` loaded under /usr/bin/python3 exactly as in-process."""
    if not os.access(SYS_PY, os.X_OK):
        pytest.skip("no /usr/bin/python3")
    base = sent()
    live = [edit(base) for _, edit, _ in LIVE]
    honest_bases = {"single": sent(page=None), "alt": base, "mixed": mixed()}
    live += [_deleted(honest_bases[b], k) for b, k in CLASS_B + TOP_MULTI]
    honest = ([f.read_bytes() for f in honest_files()]
              + [sent(b, p, mid=f"<{n}@sm823.invalid>") for n, (b, p) in CR_MAILS.items()])
    theirs = _by_path_under_sys_py(live + honest)
    assert theirs == [[list(x) for x in _sig.mime_defects(c)] for c in live + honest]
    assert [bool(t) for t in theirs] == [True] * len(live) + [False] * len(honest)
    assert len(honest) == 68


# ── what p= covers did not change ───────────────────────────────────────────

#: sha256 of the source of lf, leaves, leaf_canon and digests, at 0539fd2 (SM-819's pin).
CANON_AT_BASE = "ba66f8a35a3e3e5cbf76213da6631a7d71ec33d94fa2ef5e99b7145321bc5674"


def test_digests_and_leaf_canon_are_byte_identical_and_never_call_the_check(monkeypatch):
    src = "".join(inspect.getsource(getattr(_sig, n)) for n in ("lf", "leaves", "leaf_canon",
                                                                 "digests"))
    assert hashlib.sha256(src.encode()).hexdigest() == CANON_AT_BASE

    def boom(raw):
        raise AssertionError("digests() must not call mime_defects()")

    monkeypatch.setattr(_sig, "mime_defects", boom)
    manifest = json.loads((GOLDEN / "vectors.json").read_text())["vectors"]
    for name, v in manifest.items():
        assert _sig.digests((GOLDEN / f"{name}.eml").read_bytes()) == v["p"]


def test_a_traceback_in_the_check_fails_closed(rig, tmp_path):
    """A broken check is a refusal with a TRUST line, never a traceback."""
    lone = tmp_path / "mhs"
    lone.mkdir()
    shutil.copy(VERIFIER, lone / "session-mail-verify")
    sig = Path(inspect.getsourcefile(_sig)).read_text()
    (tmp_path / "_sig.py").write_text(
        sig + "\n\ndef mime_defects(raw):\n    raise RuntimeError('broken')\n")
    raw = rig.sign(sent())
    done = rig.run(raw, "python3", "--trust", verifier=lone / "session-mail-verify")
    assert (done.returncode, done.stdout.strip().splitlines()[-1]) == (1, FAIL)
    assert "Traceback" not in done.stderr and "cannot be checked (RuntimeError)" in done.stderr
