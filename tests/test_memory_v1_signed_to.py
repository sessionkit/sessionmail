"""SM-846: a v1 mail's membership reads the To v1 SIGNS, never an unsigned Cc or the rest of To.

v1 signs five headers (session-mail-verify HDRS), and of To only local_part(To): the
innermost <...> (else the whole field) cut at the first '@'. It never signs Cc. But
`Members.header_faults` searched notmuch's To + " " + Cc, so a v1 mail alice signed To
bob became a member of alice's tree when a relay with no key added ONE `Cc: alice`, or
rewrote To to '"alice@h" <bob@h>', 'bob@h (alice@h)' or 'bob@h, alice@h': each still
verifies GOOD (repro-001; rca-001 s2 in the SM-846 task).

THE CONTRACT (MODEL B): for a mail
read as v1, the "to" half holds only if today's search hits on To (not Cc) AND the
file's To, reduced as the verifier signs it, is the recipient (any +tag). v2 signs cc
(_sig.FLOOR), so its To+Cc search is unchanged. A pure narrowing: no mail gains
membership, and no honest v1 mail loses it (the v1 sender signs a To with one address).

Every row of rca-001 s2's model-B column, plus SM-764's bare `To: alice` and an honest
+tag. Real keys, real notmuch, the real verifier (plain mode), in scratch homes.

Run:    uv run pytest -q tests/test_memory_v1_signed_to.py
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import sm827_rig as R
from sessionmail import _mhs, _sender, _sig
from sessionmail._sender import canonical as v1_canonical

MHS = Path(_mhs.MHS_DIR)
VERIFY = MHS / "session-mail-verify"
A, BOB = R.A, "bob@sm827.invalid"
NOTES = "<notes@sm827.invalid>"
BODY = b"echo honest\n"
OLD_TO = "To/Cc does not name 'alice' (any +tag) (signature not checked)"


def load_thread():
    loader = importlib.machinery.SourceFileLoader("mhs_thread_sm846", str(MHS / "mhs-thread"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def verifier_local_part():
    """session-mail-verify runs at import (no __main__ guard): lift local_part() from
    its source text, as tests/mhs/test_named_from_readers.py does."""
    src = VERIFY.read_text()
    start = src.index("def local_part(")
    end = src.index("\ndef ", start + 1)
    ns: dict = {}
    exec(src[start:end], ns)
    return ns["local_part"]


# ── the reduction: mhs-thread's copy IS the verifier's ───────────────────────

FORMS = ["alice", "alice@h", "ALICE@H", "Alice <alice@h>", '"alice@h" <bob@h>',
         "bob@h (alice@h)", "bob@h, alice@h", "alice+memory@h", "<alice+x@h>",
         "  bob @ h ", "a <b <c@h>>", "<unclosed@h", "x> <y@h", "", None,
         "<bob@h> =?utf-8?q?=3Calice=40h=3E?=", "Eve <eve+repro-oracle@hq>",
         "alice\n\t<bob@h>", "élève@h"]


@pytest.mark.parametrize("form", FORMS)
def test_the_reduction_is_the_verifiers(form):
    assert load_thread()._v1_local_part(form) == verifier_local_part()(form)


# ── the version read: fail closed to v1 ──────────────────────────────────────

def _file(tmp_path, name, raw: bytes):
    p = tmp_path / name
    p.write_bytes(raw)
    return {"filename": [str(p)], "headers": {"To": BOB}}


HEAD = b"From: alice@h\nTo: Bob <bob@h>\nSubject: s\n"


@pytest.mark.parametrize("sig, want", [
    (None, ("unsigned", None)),                         # rc 2: today's reading stands
    (b"X-Session-Signature:\n", ("unsigned", None)),    # empty: the verifier's unsigned too
    (b"X-Session-Signature: v=2; a=sshsig; b=x\n", ("2", None)),
    (b"X-Session-Signature: v = 2 ;b=x\n", ("2", None)),
    (b"X-Session-Signature: v=1; b=x\n", ("1", "Bob <bob@h>")),
    (b"X-Session-Signature: a=sshsig; b=x\n", ("1", "Bob <bob@h>")),   # no v=: v1
    (b"X-Session-Signature: v=2; v=2; b=x\n", ("1", "Bob <bob@h>")),   # Malformed
    (b"X-Session-Signature: junk\n", ("1", "Bob <bob@h>")),            # Malformed
    (b"X-Session-Signature: v=3; b=x\n", ("1", "Bob <bob@h>")),        # not a positive 2
])
def test_version_read_fails_closed_to_v1(tmp_path, sig, want):
    pt = load_thread()
    m = _file(tmp_path, "m", HEAD + (sig or b"") + b"\nbody\n")
    assert pt._signed_to(m) == want


def test_no_file_and_unreadable_file(tmp_path):
    pt = load_thread()
    assert pt._signed_to({"filename": [str(tmp_path / "gone")]}) == ("unsigned", None)
    assert pt._signed_to({}) == ("unsigned", None)
    bad = HEAD + b"X-Session-Signature: v=1; b=x\nX-Bad: \xff\xfe\n\nbody\n"
    m = _file(tmp_path, "undecodable", bad)
    import locale
    if (locale.getpreferredencoding(False) or "").lower().replace("-", "") != "utf8":
        pytest.skip("the verifier's mail_text decodes in the locale; this pins UTF-8")
    assert pt._signed_to(m) == ("1", None)   # the verifier's v1 BAD (undecodable) too


def test_unloadable_sig_reads_v1(tmp_path, monkeypatch):
    pt = load_thread()
    monkeypatch.setattr(pt, "_sig_mod", lambda: None)
    m = _file(tmp_path, "m", HEAD + b"X-Session-Signature: v=2; b=x\n\nbody\n")
    assert pt._signed_to(m) == ("1", None)
    assert pt.Members("alice").header_faults(
        {**m, "headers": {"From": "alice@h", "To": "alice@h"}}) == ["to"]


@pytest.mark.parametrize("recip, to, cc, fault", [
    ("alice", "alice@h", "", False),
    ("alice", "Alice <ALICE+memory@h>", "", False),
    ("alice", "bob@h", "alice@h", True),                 # the keyless Cc
    ("alice", '"alice@h" <bob@h>', "", True),
    ("alice", "bob@h (alice@h)", "", True),
    ("alice", "bob@h, alice@h", "", True),
    ("alice", "alicex@h", "", True),                    # the search misses: today's fault
    ("alice", "alice", "", True),                       # SM-764: no '@', the search misses
    ("box+memory", "box+memory@h", "", False),          # a plus-named box keeps its '+'
    ("box+memory", "box+memory+x@h", "", False),
    ("box+memory", "box@h", "box+memory@h", True),
])
def test_v1_to_half_against_the_signed_reduction(tmp_path, recip, to, cc, fault):
    """The 'to' half for a v1 file, the file's To and notmuch's To being the same bytes.
    For a recip with no '+', 'r or r+tag' is the ruled local_part(To), cut at its first
    '+', == r."""
    pt = load_thread()
    raw = (f"From: {recip}@h\nTo: {to}\n" + (f"Cc: {cc}\n" if cc else "")
           + "X-Session-Signature: v=1; b=x\n\nbody\n").encode()
    m = _file(tmp_path, "m", raw)
    m["headers"] = {"From": f"{recip}@h", "To": to, **({"Cc": cc} if cc else {})}
    assert ("to" in pt.Members(recip, shared=True).header_faults(m)) is fault
    vlp = verifier_local_part()
    if "+" not in recip and not fault:
        assert vlp(to).split("+", 1)[0] == recip


# ── the rows, end to end ─────────────────────────────────────────────────────

def sign_v1(key, frm, to, subject, date, mid, body) -> str:
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key.key), "-n", R.NS],
                          input=v1_canonical(frm, to, subject, date, mid, body),
                          capture_output=True, check=True)
    b = "".join(x for x in done.stdout.decode().splitlines() if x and "SSH SIGNATURE" not in x)
    return _sig.format_v1(R.NS, b)


def v1_item(key, mid, to, keyless=(), subject=None) -> bytes:
    """A v1 item under [NOTES] in the v1 sender's shape. KEYLESS: (name, value) lines put
    right after the signed To:, AFTER signing (no key)."""
    subj = subject or "item " + mid
    hdr = [("From", A), ("To", to), ("Subject", subj), ("Date", R.DATE),
           ("Message-ID", mid), ("In-Reply-To", NOTES),
           ("References", "<root@sm827.invalid> " + NOTES),
           ("MIME-Version", "1.0"), ("Content-Type", "text/plain; charset=utf-8"),
           ("Content-Transfer-Encoding", "8bit")]
    sig = sign_v1(key, A, to, subj, R.DATE, mid, BODY)
    i = next(n for n, (h, _) in enumerate(hdr) if h == "To") + 1
    hdr[i:i] = list(keyless)
    return "".join(f"{h}: {v}\n" for h, v in hdr).encode() + sig.encode() + b"\n\n" + BODY


def v2_item(key, mid, to=BOB, cc=(), keyless_cc=None) -> bytes:
    req = _sender.Request(to=to, from_=A, subject="item " + mid, in_reply_to=NOTES, cc=list(cc))
    w = _sender.build(req, BODY, host="sm827.invalid", date=R.DATE, message_id=mid,
                      references=[NOTES], session=R.SESSION, signature=None, page=None,
                      boundary="")
    raw = key.sign(w)
    if keyless_cc:
        raw = keyless_after_to(raw, f"Cc: {keyless_cc}")
    return raw


def keyless_after_to(raw: bytes, line: str) -> bytes:
    head, sep, rest = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    i = next(n for n, x in enumerate(lines) if x.lower().startswith(b"to:")) + 1
    lines.insert(i, line.encode())
    return b"\n".join(lines) + sep + rest


def edit_header(raw: bytes, prefix: bytes, edit) -> bytes:
    """Rewrite the header line starting PREFIX, AFTER signing (no key)."""
    head, sep, rest = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    i = next(n for n, x in enumerate(lines) if x.lower().startswith(prefix))
    lines[i] = edit(lines[i])
    return b"\n".join(lines) + sep + rest


def drop_sig(raw: bytes) -> bytes:
    head, sep, rest = raw.partition(b"\n\n")
    keep = [x for x in head.split(b"\n") if not x.lower().startswith(b"x-session-signature:")]
    return b"\n".join(keep) + sep + rest


NEW_TO = "v1 signs only To's address ('bob'), never Cc or the rest of To"

#: name -> (verify rc, member, why, why_text prefix). rc pins that each refused row is
#: refused by MEMBERSHIP, not by a signature that stopped verifying.
ROWS = {
    "honest-v1": (0, True, None, ""),
    "honest-v1-plus-tag": (0, True, None, ""),
    "signed-to-bob": (0, False, "to", OLD_TO),                          # the CONTROL
    "cc-alice-on-to-bob": (0, False, "to", NEW_TO),                     # the task's bug
    "cc-alice-on-to-bob-no-v": (0, False, "to", NEW_TO),
    "v1-to-display-name-alice": (0, False, "to", NEW_TO),
    "v1-to-comment-alice": (0, False, "to", NEW_TO),
    "v1-to-list-alice-second": (0, False, "to", NEW_TO),
    "v1-to-encoded-word-alice": (0, False, "to", OLD_TO),               # notmuch keeps it encoded
    "v1-bare-to-alice": (0, False, "to", OLD_TO),                       # SM-764: no '@'
    "v2-signed-cc-alice": (0, True, None, ""),                          # KEEP
    "v2-keyless-cc-alice": (1, False, "signature", "signature does not verify"),
    "cc-alice-relabel-v2": (3, False, "signature", "signature does not verify"),
    "unsigned-cc-alice": (2, False, "unsigned", "unsigned (no X-Session-Signature)"),
    "v2-to-display-name-alice": (1, False, "signature", "signature does not verify"),
}


def build(key) -> dict[str, bytes]:
    def mid(n):
        return f"<{n}@sm827.invalid>"
    bug = lambda n: v1_item(key, mid(n), BOB, [("Cc", A)])            # noqa: E731
    to_bob = lambda n, new: edit_header(v1_item(key, mid(n), BOB), b"to:",  # noqa: E731
                                        lambda _: f"To: {new}".encode())
    return {
        "honest-v1": v1_item(key, mid("honest-v1"), A),
        "honest-v1-plus-tag": v1_item(key, mid("honest-v1-plus-tag"), "alice+memory@sm827.invalid"),
        "signed-to-bob": v1_item(key, mid("signed-to-bob"), BOB),
        "cc-alice-on-to-bob": bug("cc-alice-on-to-bob"),
        "cc-alice-on-to-bob-no-v": edit_header(bug("cc-alice-on-to-bob-no-v"),
                                               b"x-session-signature:",
                                               lambda x: x.replace(b"v=1; ", b"", 1)),
        "v1-to-display-name-alice": to_bob("v1-to-display-name-alice", f'"{A}" <{BOB}>'),
        "v1-to-comment-alice": to_bob("v1-to-comment-alice", f"{BOB} ({A})"),
        "v1-to-list-alice-second": to_bob("v1-to-list-alice-second", f"{BOB}, {A}"),
        "v1-to-encoded-word-alice": to_bob("v1-to-encoded-word-alice",
                                           f"<{BOB}> =?utf-8?q?=3C{A.replace('@', '=40')}=3E?="),
        "v1-bare-to-alice": v1_item(key, mid("v1-bare-to-alice"), "alice"),
        "v2-signed-cc-alice": v2_item(key, mid("v2-signed-cc-alice"), cc=[A]),
        "v2-keyless-cc-alice": v2_item(key, mid("v2-keyless-cc-alice"), keyless_cc=A),
        "cc-alice-relabel-v2": edit_header(bug("cc-alice-relabel-v2"), b"x-session-signature:",
                                           lambda x: x.replace(b"v=1;", b"v=2;", 1)),
        "unsigned-cc-alice": drop_sig(bug("unsigned-cc-alice")),
        "v2-to-display-name-alice": edit_header(v2_item(key, mid("v2-to-display-name-alice")),
                                                b"to:", lambda _: f'To: "{A}" <{BOB}>'.encode()),
    }


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    if not (R.NOTMUCH and shutil.which("ssh-keygen")):
        pytest.skip("needs notmuch and ssh-keygen")
    out = tmp_path_factory.mktemp("sm846")
    key = R.Key(out / "key")
    mails = build(key)
    h = out / "home"
    files = R.make_home(h, {**R.labels(key), **mails})
    env = R.env_for(h, shutil.which("python3"), SESSION_REGISTRY=str(key.registry))
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
            "msg": {n: msgs[f"{n}@sm827.invalid"] for n in mails}}


@pytest.fixture
def pt(home, monkeypatch):
    for k in list(os.environ):
        monkeypatch.delenv(k)
    for k, v in home["env"].items():
        monkeypatch.setenv(k, v)
    return load_thread()


def verify(home, name, *flags):
    return subprocess.run([str(VERIFY), str(home["files"][name]), *flags], env=home["env"],
                          capture_output=True, text=True, timeout=120)


def test_every_row_was_built(home):
    assert set(home["raw"]) == set(ROWS)


@pytest.mark.parametrize("name", list(ROWS))
def test_row(home, pt, name):
    rc, member, why, text = ROWS[name]
    assert verify(home, name).returncode == rc, name
    m = home["msg"][name]
    mem = pt.Members("alice")
    assert mem(m) is member
    assert mem.why(m) == why
    assert mem.why_text(m).startswith(text) if text else mem.why_text(m) == ""


def test_the_bug_names_alice_on_cc_and_to_bob(home):
    """The row is what the task says it is: notmuch shows alice on Cc only."""
    h = home["msg"]["cc-alice-on-to-bob"]["headers"]
    assert h["Cc"] == A and h["To"] == BOB


@pytest.mark.parametrize("name", [n for n, r in ROWS.items() if r[0] == 0
                                  and not n.startswith("v2-")])
def test_the_to_read_is_what_the_verifier_signed(home, pt, name):
    """For every GOOD v1 row: the reader's version is the verifier's, and its reduction
    of the file's To is the `to` the verifier's --json says it covered."""
    j = json.loads(verify(home, name, "--json").stdout.strip().splitlines()[-1])
    version, to = pt._signed_to(home["msg"][name])
    assert version == j["signature"]["version"] == "1"
    covered = {e["name"]: e["values"] for e in j["signature"]["covered"]}
    assert [pt._v1_local_part(to)] == covered["to"]


def test_v2_rows_read_v2(home, pt):
    assert pt._signed_to(home["msg"]["v2-signed-cc-alice"]) == ("2", None)
    j = json.loads(verify(home, "v2-signed-cc-alice", "--json").stdout.strip().splitlines()[-1])
    assert j["signature"]["version"] == "2"


def test_memory_ls_and_run_list_hide_the_bug_row(home):
    """The consumers: `memory ls` and `run --list` no longer show the keyless-Cc item;
    the honest v1 item is still listed."""
    env = home["env"]
    ls = subprocess.run([str(MHS / "session-mail-ls"), "/NOTES"],
                        env={**env, "SESSIONMAIL_PYTHON": sys.executable},   # as memory (SM-878)
                        capture_output=True, text=True, timeout=300)
    assert ls.returncode == 0, ls.stderr
    shown = [x for x in ls.stdout.splitlines() if "item <" in x and "NOT A MEMBER" not in x]
    hidden = [x for x in ls.stdout.splitlines() if "NOT A MEMBER" in x]
    assert any("item <honest-v1@" in x for x in shown), ls.stdout
    for gone in ("cc-alice-on-to-bob", "v1-to-display-name-alice", "v1-to-comment-alice",
                 "v1-to-list-alice-second"):
        assert not any(f"item <{gone}@" in x for x in shown), (gone, ls.stdout)
        assert any(f"item <{gone}@" in x and NEW_TO in x for x in hidden), (gone, ls.stdout)



def test_run_list_does_not_run_the_bug_row(tmp_path):
    """`memory run --list` judges with the same Members: the keyless-Cc item no longer
    reads "UNBLESSED, runs" (repro-001); the honest v1 item beside it still runs."""
    if not (R.NOTMUCH and shutil.which("ssh-keygen")):
        pytest.skip("needs notmuch and ssh-keygen")
    key = R.Key(tmp_path / "key")
    items = {"one": v1_item(key, "<one@sm827.invalid>", A, subject="1 honest note"),
             "two": v1_item(key, "<two@sm827.invalid>", BOB, [("Cc", A)],
                            subject="2 keyless cc")}
    h = tmp_path / "home"
    R.make_home(h, {**R.labels(key), **items})
    env = R.env_for(h, shutil.which("python3"), SESSION_REGISTRY=str(key.registry))
    p = subprocess.run([sys.executable, str(MHS / "session-mail-run"), "/NOTES", "--list"],
                       env=env,   # on the package's Python, as memory starts it (SM-878)
                       capture_output=True, text=True, timeout=300)
    head = {x.split()[1]: x for x in p.stdout.splitlines() if x.startswith("# ") and "budget=" in x}
    assert "UNBLESSED, runs" in head["1"], p.stdout
    assert "would be skipped: is NOT A MEMBER" in head["2"] and NEW_TO in head["2"], p.stdout
