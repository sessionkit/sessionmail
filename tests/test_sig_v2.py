"""Signature v2: the verifier and the shared canonicaliser (SM-594 do-002).

What is pinned here, all from the committed golden vectors
(`tests/fixtures/sig_v2/`, design.md section 8):

  - `_sig` reproduces every vector's `h=`, `p=` and canonical bytes, from the
    wire and from the delivered file alike;
  - the shipped verifier (`mhs/session-mail-verify`) says GOOD on every
    delivered vector, with a `covers:` line and one line per part;
  - every tamper case of design.md 6.6 gives its verdict - including the four
    address-canon rows added in design-001's third review (a comment or an
    address inside `<...>`, a space in the addr-spec, a stray comma), whose
    fix this slice is the second eye on;
  - a tamper of EACH signed header, and h= below the floor for EACH floor name;
  - an unknown `v=` (or `a=`) is rc 3 `unsupported-version`, never BAD;
  - a missing `_sig.py` is a broken install (rc 3; --trust ERROR rc 5), and a
    v1 mail verifies there anyway, because v1 never loads it;
  - v1 mail verifies exactly as before, now with its own `covers:` line;
  - one vector verifies under /usr/bin/python3 (3.9 on macOS), proving the
    3.9-safe constraint on `_sig.py` (design 6.2).

The vectors are signed with a TEST key committed beside them (fixtures/sig_v2/README.md);
the verifier needs only the public key, which SSHSIG embeds. The
v1 mails here are signed with a key made for the test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs, _sig

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
D = Path(__file__).parent / "fixtures" / "sig_v2"
MANIFEST = json.loads((D / "vectors.json").read_text())["vectors"]
NAMES = sorted(MANIFEST)
ORIGIN = MANIFEST["atom-three-part"]["origin"]          # hq.example.net

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")


# ── helpers ──────────────────────────────────────────────────────────────────

def verify(tmp_path, raw: bytes, *flags, origin=ORIGIN, script=VERIFIER, env_extra=None):
    """Run the shipped verifier on these bytes. ORIGIN is set as tests must set it (design
    4.4): a captured, never-delivered wire verifies only with the sender's origin."""
    f = tmp_path / "mail.eml"
    f.write_bytes(raw)
    # SESSIONMAIL_PYTHON too: inherited, it would pick the verifier's
    # interpreter over the PATH a test sets up (SM-878).
    env = {k: v for k, v in os.environ.items()
           if k not in ("SESSIONMAIL_ORIGIN", "SESSIONMAIL_PYTHON")}
    if origin is not None:
        env["SESSIONMAIL_ORIGIN"] = origin
    env.update(env_extra or {})
    return subprocess.run([str(script), str(f), *flags], capture_output=True, text=True, env=env)


def eml(name: str) -> bytes:
    return (D / f"{name}.eml").read_bytes()


def sub(raw: bytes, old: bytes, new: bytes) -> bytes:
    assert old in raw, old
    return raw.replace(old, new, 1)


def sig_tags(raw: bytes):
    return _sig.parse_v2(_sig.parse_header(_sig.signature_value(raw)))


def resign_header(raw: bytes, ns: str, h: list, p: list, b: str) -> bytes:
    """`raw` with its X-Session-Signature field (and its folded lines) replaced, in place,
    by one formatted from these tags: how a tamperer rewrites h= or p=."""
    head, sep, body = raw.partition(b"\n\n")
    out, dropping = [], False
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                out.append(line)
            continue
        dropping = line.lower().startswith(b"x-session-signature:")
        if dropping:
            out.append(_sig.format_header(ns, h, p, b).encode("ascii"))
        else:
            out.append(line)
    return b"\n".join(out) + sep + body


def capture(name: str) -> bytes:
    """The pre-postfix wire with its signature: what a SESSIONMAIL_SENDMAIL capture holds."""
    value = [v for n, v in _sig.fields(eml(name)) if n == "x-session-signature"][0]
    return _sig.insert_header((D / f"{name}.wire").read_bytes(),
                              "X-Session-Signature:" + value.decode("ascii"))


def trust_line(done) -> str:
    return done.stdout.strip().splitlines()[-1]


@pytest.fixture
def registry(tmp_path):
    """An empty registry: a v2 mail whose leg 1 passes reaches leg 2 and fails there no-cert."""
    reg = tmp_path / "registry"
    (reg / "certs").mkdir(parents=True)
    return {"SESSION_REGISTRY": str(reg)}


# ── _sig against the vectors ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_sig_reproduces_every_vector_from_the_wire_and_the_delivered_file(name):
    v = MANIFEST[name]
    wire, delivered = (D / f"{name}.wire").read_bytes(), eml(name)
    canon = (D / f"{name}.canon").read_bytes()
    assert _sig.h_for(wire) == v["h"]
    assert _sig.digests(wire) == _sig.digests(delivered) == v["p"]
    assert _sig.canonical(wire, v["ns"], v["h"], v["p"], v["origin"]) == canon
    assert _sig.canonical(delivered, v["ns"], v["h"], v["p"], v["origin"]) == canon
    assert _sig.has_bare_address(wire, v["h"])              # the sender writes bare addresses
    assert not _sig.has_bare_address(delivered, v["h"])     # postfix qualified every one


@pytest.mark.parametrize("name", NAMES)
def test_the_header_round_trips_and_no_line_passes_78(name):
    ns, h, p, b = sig_tags(eml(name))
    text = _sig.format_header(ns, h, p, b)
    assert sig_tags(b"X-Session-Signature:" + text.split(":", 1)[1].encode() + b"\n\n") == (ns, h, p, b)
    assert max(len(line) for line in text.split("\n")) <= 78


def test_x_original_subject_is_never_in_h_and_claims_are_required_when_present():
    assert "x-original-subject" not in MANIFEST["atom-headers"]["h"]
    raw = sub(eml("atom-three-part"), b"Atom-Id: D2\n", b"Atom-Id: D2\nParent: A9\nID: X\n")
    assert _sig.below_floor(raw, MANIFEST["atom-three-part"]["h"]) == ["id", "parent"]
    assert _sig.below_floor(eml("single"), MANIFEST["single"]["h"]) == []


@pytest.mark.parametrize("a, b", [
    # Each pair reads differently under email.utils.getaddresses, so each must sign
    # differently (design 4.2; design-001 reviews 2 and 3, the F1 principle).
    (b"Judy <m@x>", b"Judy <m@x> mallory@evil"),        # F1: an address after the '>'
    (b"Judy <m@x>", b"Judy <m@x"),                      # F1: the '>' removed
    (b"Judy <m@x>", b"Judy <(EVIL)m@x>"),               # F2: a comment inside <...>
    (b"Judy <m@x>", b"Judy <m(, M <e@v>)@x>"),          # F3: an address in an in-angle comment
    (b"Judy <judy@x>", b"Judy <mi ke@x>"),              # F4: a space inside the addr-spec
    (b"Judy <m@x>", b"Judy <m@x>,"),                    # F5: an empty item
    (b"a@x, b@x", b"a@x,, b@x"),                        # F5: an empty item in the middle
    (b"x@y (abc", b"x@y (abd"),                         # F6: an unterminated comment's last byte
    (b"Judy <m@x> (abc", b"Judy <m@x> (abd"),           # F6, after an angle-addr
    # do-002 review: whitespace that getaddresses reads, beside a '\\' or a stray '.'.
    (b"m\\ x@y", b"m\\x@y"),                              # class 1: one address vs another
    (b"x\\ (c)", b"x\\(c)"),                              # class 1: a comment moved past it
    (b".x, a", b". x, a"),                              # class 2: '.x' vs '.'
    (b"..b", b".. b"),                                  # class 2: '..b' vs '..'
    (b"m .", b"m."),                                    # class 2: 'm' vs 'm.'
    (b"x (a \\ )", b"x (a \\)"),                          # a closed comment vs an unterminated one
    # do-003 review: a word glued to a quoted string, in an addr-spec.
    (b'<x"y"@z>', b'<x "y"@z>'),                        # 'x"y"@z' vs 'x "y"@z'
    (b'x"y"@z', b'x "y"@z'),                            # the same, bare
    (b'<"y"x@z>', b'<"y" x@z>'),                        # glued on the other side
])
def test_the_address_canon_keeps_every_byte_that_changes_the_reading(a, b):
    assert _sig.canon_address_list(a, "o.example") != _sig.canon_address_list(b, "o.example")


@pytest.mark.parametrize("a, b", [
    (b"J. Smith <x>", b"J.Smith <x@o.example>"),        # postfix's re-spacing and qualification
    (b"Judy<x+f>,   k", b"Judy <x+f@o.example>,\n\tk@o.example"),
    (b"x(c2)", b"x@o.example (c2)"),                    # a bare address's comment moves to its end
    (b"eve@hq", b"eve@hq"),                             # a short host is left alone
    (b"Judy <m. x@y>", b"Judy <m.x@y>"),                # inside <...> a gap beside '.' reads the same
    (b'"Long\n Name" <x@y>', b'"Long Name" <x@y>'),    # class 3: a folded quoted string, unfolded
    (b'A"b" <q@z>', b'A "b" <q@z>'),                    # a display is never glued: one reading
])
def test_the_address_canon_equates_what_postfix_does_in_transit(a, b):
    assert _sig.canon_address_list(a, "o.example") == _sig.canon_address_list(b, "o.example")


#: The property below, over the specials that make a gap matter. Bounded and seeded, so a
#: failure reproduces; 3000 cases run in well under a second.
_ALPHABET = list("ax.\\@,<>\"()m")


def _gap_pairs(seed: int, count: int, chars: frozenset = frozenset("\\.")):
    """Pairs of renderings of one no-whitespace string that differ ONLY in the interior gaps
    beside one of `chars`, by default a backslash or a '.' (every other gap, leading and trailing ones too, is shared:
    a trailing comma's own getaddresses quirk is another class)."""
    import random
    rnd = random.Random(seed)
    while count:
        base = [rnd.choice(_ALPHABET) for _ in range(rnd.randint(2, 8))]
        marks = [k for k in range(1, len(base)) if {base[k - 1], base[k]} & chars]
        if not marks:
            continue
        ga = [rnd.random() < 0.3 for _ in range(len(base) + 1)]
        gb = list(ga)
        for k in marks:
            if rnd.random() < 0.5:
                gb[k] = not gb[k]

        def render(g):
            return "".join((" " if g[k] else "") + base[k] for k in range(len(base))) + (
                " " if g[-1] else "")

        count -= 1
        yield render(ga), render(gb)


def test_no_gap_beside_a_backslash_or_dot_that_getaddresses_reads_is_unsigned():
    # Classes 1 and 2 of the do-002 review: before the fix, 1488 of 40000 such pairs read
    # differently under getaddresses yet signed the same. The canon must now tell apart
    # every pair getaddresses tells apart.
    from email.utils import getaddresses

    def reading(s):
        return [addr for _, addr in getaddresses([s])]

    same = [(a, b) for a, b in _gap_pairs(594, 3000)
            if reading(a) != reading(b)
            and _sig.canon_address_list(a.encode(), "") == _sig.canon_address_list(b.encode(), "")]
    assert same == []


def test_no_gap_beside_a_quoted_string_that_getaddresses_reads_is_unsigned():
    # do-003 review: a word glued to a quoted string in an addr-spec signed as the spaced
    # one. Before the glue rule this slice of the property found 42 of 40000 (seed 594).
    from email.utils import getaddresses

    def reading(s):
        return [addr for _, addr in getaddresses([s])]

    same = [(a, b) for a, b in _gap_pairs(594, 3000, frozenset('"'))
            if reading(a) != reading(b)
            and _sig.canon_address_list(a.encode(), "") == _sig.canon_address_list(b.encode(), "")]
    assert same == []


@pytest.mark.parametrize("tags, why", [
    ("v=2; a=sshsig; n=ns; h=from; p=" + "A" * 43 + "=", "lacks b="),
    ("v=2; a=sshsig; n=ns; h=from; p=short; b=AAAA", "44-character"),
    ("v=2; a=sshsig; n=ns; h=from:from; p=" + "A" * 43 + "=; b=AAAA", "twice"),
    ("v=2; a=sshsig; n=ns; h=from:x-session-signature; p=" + "A" * 43 + "=; b=AAAA", "x-session-signature"),
    ("v=2; a=sshsig; n=ns; h=fr om; p=" + "A" * 43 + "=; b=AAAA", None),   # whitespace is removed
    ("v=2; v=2; a=sshsig", "twice"),
    ("v=2; junk", "not a tag=value"),
])
def test_parse_refuses_a_malformed_v2_header(tags, why):
    if why is None:
        assert _sig.parse_v2(_sig.parse_header(tags))[1] == ["from"]
        return
    with pytest.raises(_sig.Malformed, match=re.escape(why)):
        _sig.parse_v2(_sig.parse_header(tags))


# ── the verifier: every vector is GOOD ───────────────────────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_every_delivered_vector_verifies_good_with_a_covers_line(tmp_path, name):
    v = MANIFEST[name]
    done = verify(tmp_path, eml(name))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "signature-crypto  : ✅ good" in done.stdout
    assert "principal-binding : ✅ good" in done.stdout
    n = len(v["p"])
    assert (f"covers            : v2 h={':'.join(v['h'])} ({len(v['h'])} headers) "
            f"+ {n} part{'s' if n != 1 else ''}: ") in done.stdout
    for i in range(n):
        assert f"part {i + 1}            : ✅ " in done.stdout


def test_delivered_mail_needs_no_origin_at_all(tmp_path):
    """Postfix qualified every address, so the verifier's own ORIGIN is never consulted
    (design 4.4): no SESSIONMAIL_ORIGIN, and a PATH with no postconf, still verifies."""
    done = verify(tmp_path, eml("cc"), origin=None,
                  env_extra={"PATH": os.path.dirname(shutil.which("ssh-keygen")) + ":/usr/bin:/bin"})
    assert done.returncode == 0, done.stdout + done.stderr


def test_quiet_prints_nothing_and_keeps_the_code(tmp_path):
    done = verify(tmp_path, eml("single"), "--quiet")
    assert (done.returncode, done.stdout) == (0, "")


def test_under_trust_a_good_v2_mail_goes_on_to_leg_two(tmp_path, registry):
    done = verify(tmp_path, eml("atom-three-part"), "--trust", env_extra=registry)
    assert done.returncode == 1
    assert trust_line(done) == "TRUST: FAIL leg=2-registry reason=no-cert"


# ── the tamper table, design.md 6.6 ──────────────────────────────────────────

A3 = "atom-three-part"
TO = b"To: Judy <judy@hq.example.net>"

#: (name, how to build the tampered bytes, expected plain rc, expected --trust reason or None
#: for good). A part-altered row also names the part that must be flagged.
TAMPER = {
    "md part byte": (lambda: sub(eml(A3), b"**go**", b"**no**"), "part-altered 1"),
    "json part byte": (lambda: sub(eml(A3), b'"authority": "judy"', b'"authority": "uma!"'), "part-altered 2"),
    "html part byte": (lambda: sub(eml(A3), b"<strong>go</strong>", b"<strong>no</strong>"), "part-altered 3"),
    "json part relabelled text/plain": (
        lambda: sub(eml(A3), b"Content-Type: application/json", b"Content-Type: text/plain"), "part-altered 2"),
    "b64 json part byte": (
        lambda: _b64_json_tamper(), "part-altered 2"),
    "Cc added": (lambda: sub(eml(A3), b"Date: ", b"Cc: mallory@hq.example.net\nDate: "), "bad-signature"),
    "Atom-Authority changed": (lambda: sub(eml(A3), b"Atom-Authority: judy", b"Atom-Authority: uma"), "bad-signature"),
    "a new Atom-* added": (lambda: sub(eml(A3), b"Atom-Id: D2\n", b"Atom-Id: D2\nAtom-Scope: fleet\n"), "below-floor"),
    "Parent: added": (lambda: sub(eml(A3), b"Atom-Id: D2\n", b"Atom-Id: D2\nParent: A9\n"), "below-floor"),
    "Type: added": (lambda: sub(eml(A3), b"Atom-Id: D2\n", b"Atom-Id: D2\nType: decision\n"), "below-floor"),
    "atom-authority removed from h=": (lambda: sub(eml(A3), b"atom-type:atom-authority:", b"atom-type:"), "below-floor"),
    "From domain changed": (lambda: sub(eml(A3), b"From: Uma <uma@hq.example.net>", b"From: Uma <uma@elsewhere.example>"), "bad-signature"),
    "From display name changed": (lambda: sub(eml(A3), b"From: Uma <", b"From: Mallory <"), "bad-signature"),
    "X-Session-From changed": (lambda: sub(eml(A3), b"X-Session-From: 0a1b2c3d", b"X-Session-From: deadbeef"), "bad-signature"),
    "X-Project changed": (lambda: sub(eml("atom-headers"), b"X-Project: acme", b"X-Project: other"), "bad-signature"),
    "v=3": (lambda: sub(eml(A3), b"v=2;", b"v=3;"), "unsupported-version"),
    "whole file to CRLF, extra spaces in Subject": (
        lambda: eml(A3).replace(b"\n", b"\r\n").replace(b"Subject: D2:", b"Subject:   D2:"), None),
    "X-Original-Subject changed (Q5)": (
        lambda: sub(eml("atom-headers"), b"X-Original-Subject:\n ", b"X-Original-Subject: XX\n "), None),
    "To turned bare again": (lambda: sub(eml(A3), TO, b"To: Judy <judy>"), None),
    "To domain changed": (lambda: sub(eml(A3), TO, b"To: Judy <judy@elsewhere.example>"), "bad-signature"),
    "a display name re-spaced, postfix-style": (lambda: sub(eml(A3), b"From: Uma <uma@", b"From:   Uma   <uma@"), None),
    "J.Smith arrives J. Smith": (
        lambda: sub(eml("postfix-reformatted"), b"To: J.Smith <", b"To: J. Smith <"), None),
    "a quoted display name's text changed": (
        lambda: sub(eml("postfix-reformatted"), b'"Q\\"uote"', b'"Q\\"uotf"'), "bad-signature"),
    "an address comment changed": (
        lambda: sub(eml("postfix-reformatted"), b"(a comment)", b"(b comment)"), "bad-signature"),
    "an address appended after >": (
        lambda: sub(eml(A3), TO, TO + b" mallory@evil.example"), "bad-signature"),
    "the closing > removed": (lambda: sub(eml(A3), TO, TO[:-1]), "bad-signature"),
    # design-001 third review (45e747f4): F2-F5. This slice is that fix's second eye.
    "F2 a comment inside <...>": (lambda: sub(eml(A3), b"To: Judy <judy@", b"To: Judy <(EVIL)judy@"), "bad-signature"),
    "F3 an address hidden in a comment inside <...>": (
        lambda: sub(eml(A3), TO, b"To: Judy <judy(, Mallory <mallory@evil.example>)@hq.example.net>"),
        "bad-signature"),
    "F4 a space inside the addr-spec": (lambda: sub(eml(A3), b"To: Judy <judy@", b"To: Judy <ju dy@"), "bad-signature"),
    "F5 a stray trailing comma": (lambda: sub(eml(A3), TO, TO + b","), "bad-signature"),
    "F6 an unterminated comment's last byte": (
        lambda: sub(eml("postfix-reformatted"), b"(a comment)", b"(a comment"), "bad-signature"),
}


def _b64_json_tamper() -> bytes:
    """One decoded byte of the base64 JSON part changed, re-encoded: the CTE layer is not
    signed, the decoded bytes are."""
    import base64
    raw = eml("atom-three-part-b64")
    m = re.search(rb"Content-Type: application/json\nContent-Transfer-Encoding: base64\n\n(.*?)\n--",
                  raw, re.S)
    assert m, "the b64 vector's JSON part"
    data = base64.b64decode(m.group(1))
    assert b'"judy"' in data
    new = base64.encodebytes(data.replace(b'"judy"', b'"uma!"', 1)).rstrip(b"\n")
    return raw[:m.start(1)] + new + raw[m.end(1):]


@pytest.mark.parametrize("case", sorted(TAMPER))
def test_every_tamper_case_gives_its_verdict(tmp_path, registry, case):
    build, want = TAMPER[case]
    raw = build()
    plain = verify(tmp_path, raw)
    trusted = verify(tmp_path, raw, "--trust", env_extra=registry)
    if want is None:                                   # GOOD: transit-benign or unsigned on purpose
        assert plain.returncode == 0, plain.stdout + plain.stderr
        assert trust_line(trusted) == "TRUST: FAIL leg=2-registry reason=no-cert"
        return
    reason, _, part = want.partition(" ")
    if reason == "unsupported-version":
        assert plain.returncode == 3
        assert "unsupported signature version v=3" in plain.stderr
        assert "BAD" not in plain.stdout + plain.stderr
    else:
        assert plain.returncode == 1, plain.stdout + plain.stderr
    if reason == "below-floor":
        assert "covers            : ❌ BELOW FLOOR: h= lacks " in plain.stdout
        assert "signature-crypto" not in plain.stdout   # refused BEFORE the crypto
    if reason == "bad-signature":
        assert "signature-crypto  : ❌ BAD" in plain.stdout
        assert "parts             : not checked (the signature does not verify)" in plain.stdout
    if reason == "part-altered":
        assert "signature-crypto  : ✅ good" in plain.stdout
        assert f"part {part}            : ❌ ALTERED " in plain.stdout
        others = [n for n in ("1", "2", "3") if n != part]
        for n in others:
            assert f"part {n}            : ✅ " in plain.stdout
        assert f"part {part} (" in plain.stderr
    assert trusted.returncode == 1
    assert trust_line(trusted) == f"TRUST: FAIL leg=1-signature reason={reason}"


def test_the_below_floor_line_names_what_is_missing(tmp_path):
    done = verify(tmp_path, TAMPER["Parent: added"][0]())
    assert "h= lacks parent" in done.stdout


def test_captured_wires_and_another_hosts_origin(tmp_path):
    """Design 4.4: a bare (captured) wire qualifies with the VERIFIER's origin, so it verifies
    only with the sender's; a delivered mail never consults it."""
    assert verify(tmp_path, capture(A3)).returncode == 0
    other = verify(tmp_path, capture(A3), origin="other.example")
    assert other.returncode == 1 and "signature-crypto  : ❌ BAD" in other.stdout
    assert verify(tmp_path, eml(A3), origin="other.example").returncode == 0


def test_a_part_added_is_named_as_a_count(tmp_path):
    raw = eml("two-part")
    boundary = re.search(rb'boundary="([^"]+)"', raw).group(1)
    extra = b"--" + boundary + b"\nContent-Type: text/plain\n\nsmuggled\n"
    raw = sub(raw, b"--" + boundary + b"--", extra + b"--" + boundary + b"--")
    done = verify(tmp_path, raw)
    assert done.returncode == 1
    assert "parts             : ❌ 3 parts, the signature covers 2" in done.stdout


# ── a tamper of EACH signed header, and the floor name by name ───────────────

def _header_cases():
    for name in ("atom-three-part", "atom-headers", "reply", "cc"):
        present = {n for n, _ in _sig.fields(eml(name))}
        for h in MANIFEST[name]["h"]:
            yield name, h, h in present


@pytest.mark.parametrize("vector, header, present", list(_header_cases()),
                         ids=lambda x: str(x))
def test_each_signed_header_changed_or_added_is_bad(tmp_path, vector, header, present):
    raw = eml(vector)
    if present:
        # Append a word to the header's (first) value. For an address header that is a
        # token after the address, which the canon keeps.
        m = re.search(rb"(?im)^" + re.escape(header.encode()) + rb":[^\n]*(\n[ \t][^\n]*)*", raw)
        assert m, header
        raw = raw[:m.end()] + b" X" + raw[m.end():]
    else:
        # A header the sender signed as empty, added in transit.
        value = b"<forged@evil.example>" if header in ("in-reply-to", "references", "supersedes") \
            else b"mallory@evil.example"
        raw = sub(raw, b"X-Session-Signature:", header.encode() + b": " + value + b"\nX-Session-Signature:")
    done = verify(tmp_path, raw)
    assert done.returncode == 1, (header, done.stdout, done.stderr)
    assert "signature-crypto  : ❌ BAD" in done.stdout


@pytest.mark.parametrize("floor_name", _sig.FLOOR)
def test_h_without_a_floor_name_is_below_floor(tmp_path, registry, floor_name):
    raw = eml("atom-three-part")
    ns, h, p, b = sig_tags(raw)
    raw = resign_header(raw, ns, [n for n in h if n != floor_name], p, b)
    done = verify(tmp_path, raw)
    assert done.returncode == 1
    assert f"covers            : ❌ BELOW FLOOR: h= lacks {floor_name}" in done.stdout
    assert trust_line(verify(tmp_path, raw, "--trust", env_extra=registry)) == \
        "TRUST: FAIL leg=1-signature reason=below-floor"


# ── unknown versions, malformed headers, a broken install ────────────────────

def test_an_unknown_algorithm_is_unsupported_not_bad(tmp_path, registry):
    raw = sub(eml("single"), b"a=sshsig;", b"a=rsa-sha9;")
    done = verify(tmp_path, raw)
    assert done.returncode == 3
    assert "unsupported signature algorithm a=rsa-sha9" in done.stderr
    assert "BAD" not in done.stdout + done.stderr
    assert trust_line(verify(tmp_path, raw, "--trust", env_extra=registry)) == \
        "TRUST: FAIL leg=1-signature reason=unsupported-version"


def test_v3_prints_no_trust_line_in_plain_mode_and_one_under_trust(tmp_path, registry):
    raw = TAMPER["v=3"][0]()
    plain = verify(tmp_path, raw)
    assert "TRUST:" not in plain.stdout
    trusted = verify(tmp_path, raw, "--trust", "--quiet", env_extra=registry)
    assert trusted.stdout == "TRUST: FAIL leg=1-signature reason=unsupported-version\n"


def test_a_malformed_v2_header_is_a_parse_failure(tmp_path, registry):
    ns, h, p, b = sig_tags(eml("single"))
    raw = resign_header(eml("single"), ns, h, ["short"], b)
    done = verify(tmp_path, raw)
    assert done.returncode == 3
    assert "signature parse failed:" in done.stderr and "44-character" in done.stderr
    assert trust_line(verify(tmp_path, raw, "--trust", env_extra=registry)) == \
        "TRUST: FAIL leg=1-signature reason=bad-signature"


@pytest.fixture
def lone_verifier(tmp_path):
    """The verifier copied into an install with no `_sig.py` beside its `mhs/`."""
    mhs = tmp_path / "broken" / "mhs"
    mhs.mkdir(parents=True)
    copy = mhs / "session-mail-verify"
    shutil.copy2(VERIFIER, copy)
    return copy


def test_a_missing_sig_module_is_a_broken_install(tmp_path, registry, lone_verifier):
    done = verify(tmp_path, eml("single"), script=lone_verifier)
    assert done.returncode == 3
    assert "the verifier's _sig.py is missing (broken install)" in done.stderr
    trusted = verify(tmp_path, eml("single"), "--trust", script=lone_verifier, env_extra=registry)
    assert (trusted.returncode, trust_line(trusted)) == (5, "TRUST: ERROR reason=verifier-incomplete")


def test_the_sig_module_is_found_through_a_symlinked_verifier(tmp_path):
    link = tmp_path / "bin" / "session-mail-verify"
    link.parent.mkdir()
    link.symlink_to(VERIFIER)
    assert verify(tmp_path, eml("single"), script=link).returncode == 0


def test_an_inherited_self_variable_is_ignored(tmp_path, lone_verifier):
    """The wrapper names itself every time: pointing the variable at a good install does not
    rescue a broken one, and pointing it elsewhere does not break a good one."""
    done = verify(tmp_path, eml("single"), script=lone_verifier,
                  env_extra={"SESSIONMAIL_VERIFY_SELF": str(VERIFIER)})
    assert done.returncode == 3
    good = verify(tmp_path, eml("single"), env_extra={"SESSIONMAIL_VERIFY_SELF": str(lone_verifier)})
    assert good.returncode == 0


# ── v1: unchanged, plus its covers: line ─────────────────────────────────────

@pytest.fixture
def v1_mail(tmp_path):
    """A v1 mail as today's sender writes it, signed with a key made for the test."""
    from sessionmail import _sender
    key = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    head = {"From": "eve", "To": "uma", "Subject": "Build green",
            "Date": "Tue, 01 Jan 2030 12:00:00 +0000", "Message-ID": "<1.2.3@hq.session-mail>"}
    body = b"hello\n"
    data = _sender.canonical(head["From"], head["To"], head["Subject"], head["Date"],
                             head["Message-ID"], body)
    signed = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "session-mail@example.com"],
                            input=data, capture_output=True, check=True).stdout.decode()
    b64 = "".join(line for line in signed.splitlines() if not line.startswith("-----"))
    lines = [f"{k}: {v}" for k, v in head.items()] + [
        "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8",
        f"X-Session-Signature: v=1; a=sshsig; n=session-mail@example.com; "
        f"h=from:to:subject:date:message-id; b={b64}"]
    return ("\n".join(lines) + "\n\n").encode() + body


def test_v1_mail_verifies_as_before_with_its_covers_line(tmp_path, v1_mail):
    done = verify(tmp_path, v1_mail)
    assert done.returncode == 0, done.stdout + done.stderr
    assert ("covers            : v1 from:to:subject:date:message-id "
            "(address local parts only) + the first text part") in done.stdout
    assert verify(tmp_path, v1_mail.replace(b"hello", b"hellO")).returncode == 1


def test_without_sig_py_a_v1_mail_fails_closed_too(tmp_path, v1_mail, lone_verifier, registry):
    """SM-787, ruled (option A): the version is read
    by _sig's byte parse for EVERY mail, so an install with no _sig.py is broken for v1 as
    it already was for v2: rc 3, and under --trust ERROR verifier-incomplete (rc 5). This
    replaces test_v1_never_loads_the_v2_module, which pinned the opposite."""
    done = verify(tmp_path, v1_mail, script=lone_verifier)
    assert done.returncode == 3, done.stdout + done.stderr
    assert "the verifier's _sig.py is missing (broken install)" in done.stderr
    trusted = verify(tmp_path, v1_mail, "--trust", script=lone_verifier, env_extra=registry)
    assert (trusted.returncode, trust_line(trusted)) == (5, "TRUST: ERROR reason=verifier-incomplete")


def test_a_header_with_no_version_is_v1(tmp_path, v1_mail):
    assert verify(tmp_path, v1_mail.replace(b"v=1; ", b"")).returncode == 0


# ── Python 3.9 (design 6.2) ──────────────────────────────────────────────────

def test_a_v2_vector_verifies_under_the_system_python(tmp_path):
    py = Path("/usr/bin/python3")
    if not py.exists():
        pytest.skip("no /usr/bin/python3 here")
    version = subprocess.run([str(py), "-c", "import sys; print(sys.version_info[:2])"],
                             capture_output=True, text=True).stdout.strip()
    pin = tmp_path / "pin"
    pin.mkdir()
    (pin / "python3").symlink_to(py)
    path = os.pathsep.join([str(pin), os.path.dirname(shutil.which("ssh-keygen")), "/usr/bin", "/bin"])
    done = verify(tmp_path, eml("atom-three-part"), env_extra={"PATH": path})
    assert done.returncode == 0, (version, done.stdout, done.stderr)
    assert "part 3            : ✅ text/html" in done.stdout
    tampered = verify(tmp_path, TAMPER["F5 a stray trailing comma"][0](), env_extra={"PATH": path})
    assert tampered.returncode == 1, version
