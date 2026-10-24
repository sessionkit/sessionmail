"""SM-832: a v1 mail with one of the five headers v1 signs present twice is BAD, v1-shape.

v1 signs a VALUE for each of five header names (`from`, `to`, `subject`, `date`,
`message-id`): the verifier's `canonical` reads `msg.get(h)`, the FIRST instance. It never
signed how many fields carry the name. notmuch's show JSON, which the readers act on, takes
the LAST Subject and Date and joins every To. So a party with no key could add a second
Subject (renaming and renumbering a memory path) or a second To (flipping a non-member into a
member) after the signed one, and v1 still said GOOD (SM-832 repro-001, rca-001).

Now `_sig.v1_defects` counts those five names with `_sig.fields()` (the byte parse SM-787
counts X-Session-Signature with) and refuses any count above 1: rc 1, `reason=v1-shape`,
`the mail: <n> <name> fields: v1 signs the first only`. The verifier and mhs-thread's
in-process port both reach it through `v1_refusal`; the readers' body path through
`_sig.signed_text`. v2 never calls it (its h= signs every instance).

The rig, the honest fixtures and `_bad` are SM-822's (tests/test_verify_v1_shape.py),
imported, not copied. Every verifier case runs under PATH's python3 AND /usr/bin/python3.
The reader rows use SM-827's rig (tests/sm827_rig.py): a scratch home and a REAL notmuch.

Run: uv run pytest -q tests/test_verify_v1_dup_header.py
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import sm827_rig as R
import test_memory_v1_signed_to as S846
import test_verify_v1_shape as V
from sessionmail import _root, _sig
from sessionmail._sender import canonical as v1_canonical

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")

WHY = "fields: v1 signs the first only"

#: (header as the v1 sender spells it, a keyless value for its second instance)
COVERED = [
    ("From", "mallory@example.com"),
    ("To", "mallory@example.com"),
    ("Subject", "s EVIL"),
    ("Date", "Sun, 09 Jun 2030 12:34:56 +0000"),
    ("Message-ID", "<evil@example.com>"),
]


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return V.Rig(tmp_path_factory.mktemp("sm832"))


@pytest.fixture(params=V._pythons())
def py(request):
    return request.param


def dup(raw: bytes, name: str, line: bytes, where: str = "after") -> bytes:
    """RAW with LINE (a whole field, LF-ended) put right after (or before) the first field
    named NAME. No key is touched: this is what a relay could do."""
    head, sep, body = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    i = next(n for n, l in enumerate(lines) if l.lower().startswith(name.lower().encode() + b":"))
    lines.insert(i + 1 if where == "after" else i, line.rstrip(b"\n"))
    return b"\n".join(lines) + sep + body


def field(name: str, value: str) -> bytes:
    return f"{name}: {value}\n".encode()


def _refused(rig, raw, py, n: int, name: str):
    """SM-822's _bad (rc 1, the v1-shape line, the stderr, --trust, --json), and the why."""
    V._bad(rig, raw, py, "v1-shape")
    out = rig.run(raw, py).stdout
    assert "signature-crypto  : ✅ good" in out, out
    assert f"the mail: {n} {name.lower()} {WHY}" in out, out


# ── (1) each of the five, a keyless second instance AFTER the signed one ─────

@pytest.mark.parametrize("name, value", COVERED, ids=[n for n, _ in COVERED])
def test_each_covered_header_twice_is_v1_shape(rig, py, name, value):
    _refused(rig, dup(V.single(rig), name, field(name, value)), py, 2, name)


# ── (2) placement does not matter ──────────────────────────────────────────

def test_placement_does_not_matter(rig, py):
    one = V.single(rig)
    head, sep, body = one.partition(b"\n\n")
    evil = field("Subject", "s EVIL")
    cases = {
        # after the CTE, before the signature
        "after-cte": dup(one, "Content-Transfer-Encoding", evil),
        # after X-Session-Signature: the last header line
        "after-the-signature": head + b"\n" + evil.rstrip(b"\n") + sep + body,
        # on SESH-890's multipart/alternative, after the signed Subject and after the container
        "alternative-after-subject": dup(V.alternative(rig), "Subject", evil),
        "alternative-last-header": dup(V.alternative(rig), "X-Session-Signature", evil),
    }
    for case, raw in cases.items():
        try:
            _refused(rig, raw, py, 2, "Subject")
        except AssertionError as e:
            raise AssertionError(case) from e


# ── (3) spelling does not matter: the rule counts fields, it compares no value ─

def test_spelling_does_not_matter(rig, py):
    one = V.single(rig)
    cases = {
        "lower-case": (dup(one, "Subject", b"subject: s EVIL\n"), 2),
        "upper-case": (dup(one, "Subject", b"SUBJECT: s EVIL\n"), 2),
        "folded": (dup(one, "Subject", b"Subject: s\n EVIL\n"), 2),
        "empty": (dup(one, "Subject", b"Subject:\n"), 2),
        "encoded-word": (dup(one, "Subject", b"Subject: =?utf-8?q?s_EVIL?=\n"), 2),
        "same-text-after": (dup(one, "Subject", b"Subject: s sm822\n"), 2),
        # the same text BEFORE the signed one: the first instance still holds the signed value,
        # so the crypto stays good, and the count still refuses it
        "same-text-before": (dup(one, "Subject", b"Subject: s sm822\n", "before"), 2),
        "three": (dup(dup(one, "Subject", b"Subject: s EVIL\n"), "Subject",
                      b"Subject: s EVIL too\n"), 3),
    }
    for case, (raw, n) in cases.items():
        try:
            _refused(rig, raw, py, n, "Subject")
        except AssertionError as e:
            raise AssertionError(case) from e


def test_two_covered_headers_twice_name_both(rig, py):
    raw = dup(dup(V.single(rig), "Subject", b"Subject: s EVIL\n"), "To", b"To: mallory@x\n")
    _refused(rig, raw, py, 2, "Subject")
    assert f"the mail: 2 to {WHY}; the mail: 2 subject {WHY}" in rig.run(raw, py).stdout


# ── (4) the crypto is still named first ────────────────────────────────────

@pytest.mark.parametrize("name, value", COVERED, ids=[n for n, _ in COVERED])
def test_a_different_value_before_the_signed_one_stays_bad_signature(rig, py, name, value):
    """Python reads the first instance, so the signed text changes: bad-signature, as at
    efdb9bb (repro-001's *-before rows). The shape is judged only after a good crypto."""
    raw = dup(V.single(rig), name, field(name, value), "before")
    t = rig.run(raw, py, "--trust", "--quiet")
    assert (t.returncode, t.stdout.strip()) == (
        1, "TRUST: FAIL leg=1-signature reason=bad-signature"), (t.stdout, t.stderr)


def test_an_obsolete_subject_after_stays_bad_signature(rig, py):
    """`Subject :` ends the email package's header block, so Date and Message-ID drop out of
    `canonical`: bad-signature, as at efdb9bb. `fields()` counts it (2) where `get_all` does
    not (1): the byte count is the superset, and it changes no existing verdict."""
    for line in (b"Subject : s EVIL\n", b"Subject\t: s EVIL\n"):
        raw = dup(V.single(rig), "Subject", line)
        t = rig.run(raw, py, "--trust", "--quiet")
        assert t.stdout.strip() == "TRUST: FAIL leg=1-signature reason=bad-signature", line


def test_a_cte_defect_still_wins(rig, py):
    """v1_refusal's precedence is unchanged: cte-defect wins when both apply."""
    raw = dup(V._cte(V.single(rig), b"Content-Transfer-Encoding: base64\n"), "Subject",
              b"Subject: s EVIL\n")
    V._bad(rig, raw, py, "cte-defect")


# ── (5) only the five: a duplicate of any other header is not refused ──────

def test_other_headers_twice_stay_good_and_unchanged(rig, py):
    """v1 signs none of these, so a second one adds nothing a single keyless one could not.
    Delivery's own headers (Received, Delivered-To, Return-Path) are among them. Two Cc is
    GOOD: the Cc membership hole is a separate card (rca-001 (e)), not fixed here."""
    one = V.single(rig)
    base = rig.run(one, py)
    assert base.returncode == 0, base.stdout
    delivered = (b"Return-Path: <alice@example.com>\nDelivered-To: uma@example.com\n"
                 b"Received: by a.example (Postfix)\n\tid 1; Sun, 08 Dec 2030 08:00:00 +0000\n"
                 b"Received: from b.example by a.example\nReceived: by b.example\n")
    cases = {
        "two-cc": dup(dup(one, "Message-ID", b"Cc: a@x\n"), "Cc", b"Cc: b@x\n"),
        "postfix-delivery": delivered + one,
        "two-references": dup(dup(one, "Message-ID", b"References: <a@x>\n"), "References",
                              b"References: <b@x>\n"),
        "two-in-reply-to": dup(dup(one, "Message-ID", b"In-Reply-To: <a@x>\n"), "In-Reply-To",
                               b"In-Reply-To: <b@x>\n"),
        "two-x-foo": dup(dup(one, "Message-ID", b"X-Foo: 1\n"), "X-Foo", b"X-Foo: 2\n"),
        "two-mime-version": dup(one, "MIME-Version", b"MIME-Version: 1.0\n"),
    }
    for case, raw in cases.items():
        got = rig.run(raw, py)
        assert (got.returncode, got.stdout, got.stderr) == (
            0, base.stdout, base.stderr), (case, got.stdout, got.stderr)
        assert _sig.v1_defects(raw) == [], case


# ── (6) one list: three copies, pinned equal ───────────────────────────────

def _verifier_hdrs() -> list[str]:
    """HDRS as the verifier's python heredoc assigns it, read by AST (as aplus_dry.py and
    mhs-thread's port read the lifted names), never imported."""
    text = V.VERIFIER.read_text()
    head = "<<'PYEOF'\n"
    i = text.index(head) + len(head)
    src = text[i:text.index("\nPYEOF\n", i) + 1]
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "HDRS" for t in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError("no HDRS in the verifier")


def test_v1_hdrs_is_the_verifiers_hdrs_and_roots_signed():
    assert _sig.V1_HDRS == ("from", "to", "subject", "date", "message-id")
    assert list(_sig.V1_HDRS) == _verifier_hdrs()
    assert tuple(_root.SIGNED) == _sig.V1_HDRS
    assert ":".join(_sig.V1_HDRS) == _sig.V1_FIELDS


# ── (7) the function, directly ─────────────────────────────────────────────

def _vectors(rig) -> dict[str, tuple[bytes, list]]:
    """name -> (raw, exactly what v1_defects must return)."""
    one, alt = V.single(rig), V.alternative(rig)
    out = {}
    for name, value in COVERED:
        why = [["v1-shape", "the mail", f"2 {name.lower()} {WHY}"]]
        out[f"single-{name}"] = (dup(one, name, field(name, value)), why)
        out[f"alt-{name}"] = (dup(alt, name, field(name, value)), why)
        out[f"crlf-{name}"] = (dup(one, name, field(name, value)).replace(b"\n", b"\r\n"), why)
    return out


def test_v1_defects_names_exactly_the_duplicate(rig):
    for name, (raw, want) in _vectors(rig).items():
        assert [list(t) for t in _sig.v1_defects(raw)] == want, name
    for name, raw in V.honest(rig).items():
        assert _sig.v1_defects(raw) == [], name


def test_v1_defects_is_the_same_on_both_interpreters(rig, tmp_path):
    """SM-822's cross-interpreter test, over the SM-832 vectors and SM-822's honest 18."""
    raws = {n: r for n, (r, _) in _vectors(rig).items()}
    raws.update(V.honest(rig))
    got = [V._defects_under(p.values[0], raws, tmp_path) for p in V._pythons()]
    assert all(g == got[0] for g in got[1:])
    for name, (_, want) in _vectors(rig).items():
        assert got[0][name] == want, name
    for name in V.honest(rig):
        assert got[0][name] == [], name


def test_signed_text_refuses_it(rig):
    """The readers' body path (`memory cat`, `tree -b`, `run`, the wake shelf) reaches
    v1_defects through `_sig.signed_text`."""
    for name, (raw, _) in _vectors(rig).items():
        assert _sig.signed_text(raw) == (None, "not in the v1 shape"), name
    assert _sig.signed_text(V.single(rig))[0] is not None


# ── (8) v2 is untouched ────────────────────────────────────────────────────

def test_v2_with_a_duplicate_subject_is_never_v1_shape(tmp_path):
    """v2's h= signs every instance, and v1_defects is never called on a v2 mail: a keyless
    second Subject after a signed v2 mail is bad-signature, as at efdb9bb."""
    key = R.Key(tmp_path / "key")
    raw = R.v2_mail(key, "1 a note", "<n@sm827.invalid>", None, b"echo hi\n")
    env = {**os.environ, "SESSION_REGISTRY": str(key.registry)}
    good = tmp_path / "good.eml"
    good.write_bytes(raw)
    assert subprocess.run([str(V.VERIFIER), str(good)], env=env,
                          capture_output=True).returncode == 0
    bad = tmp_path / "bad.eml"
    bad.write_bytes(dup(raw, "Subject", b"Subject: 9 EVIL\n"))
    j = json.loads(subprocess.run([str(V.VERIFIER), str(bad), "--json"], env=env,
                                  capture_output=True, text=True).stdout)
    assert (j["rc"], j["reason"]) == (1, "bad-signature"), j


# ── (10) the readers, end to end: a real notmuch, a scratch memory home ─────

ITEM_MID = "<item@sm827.invalid>"


def _v1_item(key: R.Key, extra: tuple[str, str] | None, to: str = R.A,
             subject: str = "1 honest note") -> bytes:
    """repro-001's v1 memory item under [NOTES], in the v1 sender's shape, with EXTRA (a
    keyless second instance of a covered header) right after the signed one."""
    hdr = [("From", R.A), ("To", to), ("Subject", subject), ("Date", R.DATE),
           ("Message-ID", ITEM_MID), ("In-Reply-To", "<notes@sm827.invalid>"),
           ("References", "<root@sm827.invalid> <notes@sm827.invalid>"),
           ("MIME-Version", "1.0"), ("Content-Type", "text/plain; charset=utf-8"),
           ("Content-Transfer-Encoding", "8bit")]
    body = b"echo honest\n"
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key.key), "-n", R.NS],
                          input=v1_canonical(R.A, to, subject, R.DATE, ITEM_MID, body),
                          capture_output=True, check=True)
    b = "".join(x for x in done.stdout.decode().splitlines() if x and "SSH SIGNATURE" not in x)
    if extra:
        i = next(n for n, (h, _) in enumerate(hdr) if h == extra[0])
        hdr.insert(i + 1, extra)
    return ("".join(f"{h}: {v}\n" for h, v in hdr) + _sig.format_v1(R.NS, b) + "\n").encode() \
        + b"\n" + body


SIG_WHY = "signature does not verify"
#: SM-846 (merged at fix-003): a v1 mail's "to" half reads the To the file carries FIRST,
#: the one v1 signs. to-flip-after's is bob, so Members refuses it on "to" BEFORE any
#: verify, and says so. The verifier still refuses it as v1-shape (asserted below).
TO_WHY = ("v1 signs only To's address ('bob'), never Cc or the rest of To: it is not "
          "'alice' (any +tag) (signature not checked)")

READER_ROWS = {
    # name: (extra, signed To, what notmuch shows as the item's subject, why not a member)
    "subject-after": (("Subject", "1 EVIL subject"), R.A, "1 EVIL subject", SIG_WHY),
    "subject-renumber-after": (("Subject", "9 EVIL renumbered"), R.A, "9 EVIL renumbered",
                               SIG_WHY),
    "to-flip-after": (("To", R.A), "bob@sm827.invalid", "1 honest note", TO_WHY),
}


@pytest.mark.skipif(R.NOTMUCH is None, reason="notmuch not on this machine")
@pytest.mark.parametrize("row", sorted(READER_ROWS))
def test_the_live_exhibits_are_refused_by_the_readers(tmp_path, row):
    """repro-001's three LIVE rows, rebuilt here. At efdb9bb each was a member: `memory ls`
    listed it, and `run --list` would run it (subject-renumber-after at /NOTES/9)."""
    extra, to, shown, why = READER_ROWS[row]
    key = R.Key(tmp_path / "key")
    item = _v1_item(key, extra, to)
    assert _sig.signed_text(item) == (None, "not in the v1 shape")
    home = tmp_path / "home"
    R.make_home(home, {**R.labels(key), "item": item})
    env = R.env_for(home, shutil.which("python3"), SESSION_REGISTRY=str(key.registry))
    # Whatever layer of Members names it first, the verifier refuses the item as v1-shape.
    fn = next((home / ".mail" / "agents" / "alice").glob("*/item*"))
    done = subprocess.run([str(S846.VERIFY), str(fn), "--json"], env=env,
                          capture_output=True, text=True, timeout=120)
    assert (done.returncode, json.loads(done.stdout.strip().splitlines()[-1])["reason"]) \
        == (1, "v1-shape"), done.stdout
    tree = R.Tree(tmp_path / "tree")
    ls = tree.run(env, "session-mail-ls", "/NOTES")
    assert f"⊘ NOT A MEMBER: {shown}" in ls["out"], ls
    # ls truncates the reason; the head of it is exact.
    assert f"— {why[:40]}" in ls["out"], ls
    for n in ("1", "9"):
        got = tree.run(env, "session-mail-ls", f"/NOTES/{n}")
        assert got["rc"] == 1 and f"has no child '{n}'" in got["err"], (n, got)
    run = tree.run(env, "session-mail-run", "/NOTES", "--list")
    assert f"would be skipped: is NOT A MEMBER (MHS 170): {why}" in run["out"], run
    assert "runs:" not in run["out"], run


@pytest.mark.skipif(R.NOTMUCH is None, reason="notmuch not on this machine")
def test_the_honest_v1_item_is_still_a_member(tmp_path):
    """The control: the same item with no duplicate is listed and would run, as at efdb9bb."""
    key = R.Key(tmp_path / "key")
    home = tmp_path / "home"
    R.make_home(home, {**R.labels(key), "item": _v1_item(key, None)})
    env = R.env_for(home, shutil.which("python3"), SESSION_REGISTRY=str(key.registry))
    tree = R.Tree(tmp_path / "tree")
    ls = tree.run(env, "session-mail-ls", "/NOTES")
    assert "└ 1 honest note" in ls["out"] and "NOT A MEMBER" not in ls["out"], ls
    run = tree.run(env, "session-mail-run", "/NOTES", "--list")
    assert "# 1  budget=600   # UNBLESSED, runs" in run["out"], run


# ── SM-846 x SM-832: a duplicate To or From never makes a member ─────────────
# SM-846 (6dbf645) made Members.header_faults read, for a v1 mail, the To the file carries
# FIRST (email's .get("to"), the one v1 signs), where it had searched notmuch's To + Cc.
# notmuch joins every To, so on a mail with two To fields the two readers see different
# bytes. These rows pin, on the merged tree, that a v1 mail with a keyless second To or
# From after the signed one is (1) BAD v1-shape from the subprocess verifier, (2) never
# GOOD from mhs-thread's in-process port, which defers it (None) because the lifted
# v1_refusal refuses it, and (3) never a member, with the port ON and with it OFF (every
# verdict from the subprocess). Built with SM-846's own v1 builder, imported, not copied.

BOB = S846.BOB

#: name -> (keyless lines put right after the signed To, the signed To, header named,
#:          the reason Members gives)
DUP_ROWS = {
    # the signed To IS alice; a second To names bob: notmuch joins both
    "dup-to-after": ([("To", BOB)], R.A, "to", "signature"),
    # the same To twice: the value a reader shows is honest, the count is not
    "dup-to-same": ([("To", R.A)], R.A, "to", "signature"),
    # repro-001's flip: signed To bob, a keyless To alice. SM-846 reads the signed To
    # (bob) and refuses on "to" before any verify; the verifier refuses it anyway.
    "dup-to-flip": ([("To", R.A)], BOB, "to", "to"),
    # a second From after the signed one (alice): bob, then alice again. notmuch shows
    # the FIRST From (alice, measured at fix-003), so the header half passes and the
    # verify refuses it.
    "dup-from-after": ([("From", BOB)], R.A, "from", "signature"),
    "dup-from-same": ([("From", R.A)], R.A, "from", "signature"),
}


@pytest.fixture(scope="module")
def dup_home(tmp_path_factory):
    import sys
    if not (R.NOTMUCH and shutil.which("ssh-keygen")):
        pytest.skip("needs notmuch and ssh-keygen")
    out = tmp_path_factory.mktemp("sm832x846")
    key = R.Key(out / "key")
    mails = {"honest": S846.v1_item(key, "<honest@sm827.invalid>", R.A)}
    for name, (keyless, to, _, _) in DUP_ROWS.items():
        mails[name] = S846.v1_item(key, f"<{name}@sm827.invalid>", to, keyless)
    h = out / "home"
    files = R.make_home(h, {**R.labels(key), **mails})
    # python3 on the readers' PATH is THIS interpreter, so the in-process port can load
    # (_port_load refuses a python3 that is not sys.executable).
    env = R.env_for(h, sys.executable, SESSION_REGISTRY=str(key.registry))
    p = subprocess.run([R.NOTMUCH, "show", "--format=json", "--body=false",
                        "--entire-thread=false", "subject:item"],
                       env=env, capture_output=True, text=True, check=True)
    msgs: dict = {}

    def walk(x):
        if isinstance(x, dict) and "headers" in x:
            msgs[x["id"]] = x
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(json.loads(p.stdout))
    return {"env": env, "files": files, "raw": mails,
            "msg": {n: msgs[f"{n}@sm827.invalid"] for n in mails}, "key": key}


def _thread(dup_home, monkeypatch, port: bool):
    for k in list(os.environ):
        monkeypatch.delenv(k)
    for k, v in dup_home["env"].items():
        monkeypatch.setenv(k, v)
    pt = S846.load_thread()
    if not port:
        monkeypatch.setattr(pt, "_port", lambda: None)
    return pt


def test_dup_rows_were_built(dup_home):
    assert set(dup_home["raw"]) == {"honest", *DUP_ROWS}
    for name, (keyless, _, header, _) in DUP_ROWS.items():
        names = [n for n, _ in _sig.fields(dup_home["raw"][name])]
        assert names.count(header) == 2, (name, names)


@pytest.mark.parametrize("name", sorted(DUP_ROWS))
def test_dup_to_from_is_v1_shape_from_the_subprocess_verifier(dup_home, name):
    header = DUP_ROWS[name][2]
    fn, env = str(dup_home["files"][name]), dup_home["env"]
    done = subprocess.run([str(S846.VERIFY), fn, "--json"], env=env,
                          capture_output=True, text=True, timeout=120)
    j = json.loads(done.stdout.strip().splitlines()[-1])
    assert (done.returncode, j["reason"]) == (1, "v1-shape"), (name, done.stdout)
    plain = subprocess.run([str(S846.VERIFY), fn], env=env,
                           capture_output=True, text=True, timeout=120)
    assert plain.returncode == 1, (name, plain.stdout)
    assert "signature-crypto  : ✅ good" in plain.stdout, (name, plain.stdout)
    assert f"the mail: 2 {header} fields: v1 signs the first only" in plain.stdout, name


def test_the_port_is_live_in_this_rig(dup_home, monkeypatch):
    """Without this the port rows below would prove nothing: the port must load here and
    say GOOD (True, no fork) for the honest control."""
    pt = _thread(dup_home, monkeypatch, port=True)
    if not S846.R.NOTMUCH:
        pytest.skip("needs notmuch")
    if pt._port() is None:
        pytest.skip("the in-process port does not load on this host (no cryptography, "
                    "a locale that is not UTF-8, or a moved pin: test_inproc_verify says)")
    assert pt.VERIFY_PORT_PIN == __import__("hashlib").sha256(
        S846.VERIFY.read_bytes()).hexdigest()
    assert pt._sig_inproc(str(dup_home["files"]["honest"])) is True


@pytest.mark.parametrize("name", sorted(DUP_ROWS))
def test_dup_to_from_is_refused_by_the_in_process_port(dup_home, monkeypatch, name):
    """The port's own verdict is None (defer), never True; the reason is the lifted
    v1_refusal's v1-shape, naming the header. The subprocess it defers to says rc 1."""
    pt = _thread(dup_home, monkeypatch, port=True)
    P = pt._port()
    if P is None:
        pytest.skip("the in-process port does not load on this host")
    header, fn = DUP_ROWS[name][2], str(dup_home["files"][name])
    assert pt._sig_inproc(fn) is None, name
    reason, why = P["v1_refusal"](P["_S"], dup_home["raw"][name])
    assert reason == "v1-shape" and f"2 {header} fields" in why, (name, why)
    assert pt._sig_ok({"filename": [fn]}) is False, name


@pytest.mark.parametrize("port", [True, False], ids=["port-on", "port-off"])
@pytest.mark.parametrize("name", sorted(DUP_ROWS))
def test_dup_to_from_never_reaches_membership_as_a_member(dup_home, monkeypatch, name, port):
    """Members (SM-846's header_faults, then the verify) refuses every row, with the port
    and without it. The honest control is a member both ways."""
    pt = _thread(dup_home, monkeypatch, port=port)
    m = dup_home["msg"][name]
    mem = pt.Members("alice")
    assert mem(m) is False, (name, m["headers"])
    want = DUP_ROWS[name][3]
    assert mem.why(m) == want, (name, mem.why(m), m["headers"])
    if want == "signature":
        assert mem.why_text(m) == "signature does not verify", name
    # Whatever the header reading, the signature itself never passes.
    assert pt._sig_ok(m) is False, name
    honest = dup_home["msg"]["honest"]
    assert mem(honest) is True and mem.why(honest) is None


def test_dup_to_from_rows_are_not_listed_as_members(dup_home):
    """The consumer: `memory ls /NOTES` lists the honest item and no dup row as a member."""
    ls = subprocess.run([str(S846.MHS / "session-mail-ls"), "/NOTES"],
                        env={**dup_home["env"], "SESSIONMAIL_PYTHON": sys.executable},   # as memory (SM-878)
                        capture_output=True, text=True, timeout=300)
    assert ls.returncode == 0, ls.stderr
    shown = [x for x in ls.stdout.splitlines() if "item <" in x and "NOT A MEMBER" not in x]
    assert any("item <honest@" in x for x in shown), ls.stdout
    for name in DUP_ROWS:
        assert not any(f"item <{name}@" in x for x in shown), (name, ls.stdout)
