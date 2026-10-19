"""The verifier judges a mail by its own code and the stdlib, whatever the caller's cwd (SM-749).

`mhs/session-mail-verify` is a bash wrapper around a Python heredoc. Run as
`python3 -`, Python puts the CALLER's cwd first on `sys.path` and honours
`PYTHONPATH`, so a `.py` file sitting where the caller happens to stand
replaces a stdlib module inside the trust check. Every caller (`message
verify`, the pager trust checks, mhs-thread `--boot`, the `_sig_ok`
fallback) inherits its cwd and its environment.

Each case runs the shipped verifier as a subprocess, with ONE planted module
in its own directory (a raising `textwrap.py` would otherwise mask a lying
`subprocess.py`), and asserts the exact verdict: the exit code AND the
`signature-crypto` line that tells GOOD from BAD. A BAD v1 mail still prints
`signer-key` and `covers:`, so those lines do not separate the verdicts.

The plants:

  - `subprocess.py` whose `run()` reports returncode 0 for every command,
    the SM-668 optimize-002 reviewer's form (finding F5): a mail whose
    signature does not verify must still read BAD;
  - `textwrap.py` that raises on import: a good mail must still read GOOD.

Two ways in: the cwd, and `PYTHONPATH` (the environment). Two signature
versions: v1 (the heredoc's own path) and v2 (`_sig.py`, loaded by path,
but the ssh-keygen calls still go through the heredoc's `subprocess`).
The v2 tamper edits a SIGNED HEADER, so the crypto is what must catch it;
a body edit would be caught by the part digests whatever `subprocess` says.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sessionmail import _mhs

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
V2 = Path(__file__).parent / "fixtures" / "sig_v2" / "single.eml"

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")

PLANTS = {
    # Every command "succeeds": the reviewer's six lines.
    "subprocess": (
        "class _Done:\n"
        "    returncode = 0\n"
        "    stdout = b''\n"
        "    stderr = b''\n"
        "def run(*args, **kwargs):\n"
        "    return _Done()\n"
    ),
    "textwrap": "raise ImportError('planted textwrap.py ran')\n",
}

GOOD_LINES = ("signature-crypto  : ✅ good", "principal-binding : ✅ good",
              "signer-key        : ", "sender-session-id : ", "covers            : ")


# ── fixtures ─────────────────────────────────────────────────────────────────

def _v1(tmp_path: Path) -> bytes:
    """A v1 mail as the sender writes it, signed with a key made for the test
    (as tests/test_sig_v2.py's v1_mail does)."""
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


def _swap(raw: bytes, old: bytes, new: bytes) -> bytes:
    assert old in raw, old
    return raw.replace(old, new, 1)


MAILS = {
    # name: (derive(v1, v2) -> bytes, the verdict it must get)
    "v1-good": (lambda v1, v2: v1, "GOOD"),
    "v1-tampered-body": (lambda v1, v2: _swap(v1, b"\n\nhello\n", b"\n\nhellO\n"), "BAD"),
    "v2-good": (lambda v1, v2: v2, "GOOD"),
    "v2-tampered-subject": (
        lambda v1, v2: _swap(v2, b"Subject: design done", b"Subject: design DONE"), "BAD"),
}


@pytest.fixture
def mails(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    v1, v2 = _v1(keys), V2.read_bytes()
    out = {}
    for name, (derive, want) in MAILS.items():
        f = tmp_path / f"{name}.eml"
        f.write_bytes(derive(v1, v2))
        out[name] = (f, want)
    return out


def _env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONSAFEPATH", "SESSIONMAIL_ORIGIN", "SESSIONMAIL_VERIFY_SELF",
                        "SESSIONMAIL_PYTHON")}
    env.update(extra)
    return env


def verify(mail: Path, cwd: Path, **env):
    return subprocess.run([str(VERIFIER), str(mail)], cwd=cwd, capture_output=True,
                          text=True, env=_env(**env))


def plant(where: Path, module: str) -> Path:
    where.mkdir()
    (where / f"{module}.py").write_text(PLANTS[module])
    return where


def assert_verdict(done, want: str):
    said = f"rc={done.returncode}\n--- stdout\n{done.stdout}--- stderr\n{done.stderr}"
    if want == "GOOD":
        assert done.returncode == 0, said
        for line in GOOD_LINES:
            assert line in done.stdout, said
        assert "❌" not in done.stdout, said
    else:
        assert done.returncode == 1, said
        assert "signature-crypto  : ❌ BAD" in done.stdout, said
        assert "signature-crypto  : ✅ good" not in done.stdout, said


# ── the control: a clean cwd and a clean environment ─────────────────────────

@pytest.mark.parametrize("mail", sorted(MAILS))
def test_control_each_mail_gets_its_verdict_from_a_clean_cwd(tmp_path, mails, mail):
    clean = tmp_path / "clean"
    clean.mkdir()
    f, want = mails[mail]
    assert_verdict(verify(f, clean), want)


# ── (a) (b) (d): a module planted in the caller's cwd ────────────────────────

@pytest.mark.parametrize("module", sorted(PLANTS))
@pytest.mark.parametrize("mail", sorted(MAILS))
def test_a_module_planted_in_the_cwd_never_changes_the_verdict(tmp_path, mails, module, mail):
    cwd = plant(tmp_path / f"cwd-{module}", module)
    f, want = mails[mail]
    assert_verdict(verify(f, cwd), want)


# ── (c) (d): the same module reached through PYTHONPATH ──────────────────────

@pytest.mark.parametrize("module", sorted(PLANTS))
@pytest.mark.parametrize("mail", sorted(MAILS))
def test_a_module_on_pythonpath_never_changes_the_verdict(tmp_path, mails, module, mail):
    where = plant(tmp_path / f"pp-{module}", module)
    clean = tmp_path / "clean"
    clean.mkdir()
    f, want = mails[mail]
    assert_verdict(verify(f, clean, PYTHONPATH=str(where)), want)


# ── -I drops PYTHON* only: the variables the script reads still take effect ──
#
# The verifier and the `_sig.py` it loads read exactly these (a444f69, sessionmail):
#
#   SESSION_MAIL_SIG_NS      mhs/session-mail-verify:117   v1 namespace when the header has no n=
#   SESSION_BLESS_NS         mhs/session-mail-verify:118   leg 3: the blessing's namespace
#   SESSION_BLESS_PRINCIPAL  mhs/session-mail-verify:119   leg 3: the blessing's principal
#   SESSION_REGISTRY         mhs/session-mail-verify:324   legs 2-3: the registry root
#   SESSIONMAIL_VERIFY_SELF  mhs/session-mail-verify:438   where _sig.py is (the wrapper sets it)
#   SESSIONMAIL_ORIGIN       _sig.py:167                   v2: the origin of a bare address
#
# Each test sets them to NON-default values that are the only way to the verdict
# it asserts, then takes one away and shows the verdict move: a variable that
# -I had dropped could not make that difference.

NS = "sm749-mail@example.test"            # not the shipped default
BLESS_NS = "sm749-bless@example.test"
BLESS_PRINCIPAL = "sm749@bless"


@pytest.fixture
def trusted(tmp_path):
    """A v1 mail signed under NS with NO n= in its header (so only SESSION_MAIL_SIG_NS
    names the namespace), and a registry where it is cert'd and blessed under
    BLESS_NS by BLESS_PRINCIPAL: --trust reads FULL only when all four variables land."""
    from sessionmail import _sender
    key, bkey = tmp_path / "k", tmp_path / "bless"
    for k in (key, bkey):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True)
    head = {"From": "eve", "To": "uma", "Subject": "env check",
            "Date": "Tue, 01 Jan 2030 12:00:00 +0000", "Message-ID": "<749.1@hq.session-mail>"}
    body = b"hello\n"
    data = _sender.canonical(head["From"], head["To"], head["Subject"], head["Date"],
                             head["Message-ID"], body)
    signed = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", NS],
                            input=data, capture_output=True, check=True).stdout.decode()
    b64 = "".join(line for line in signed.splitlines() if not line.startswith("-----"))
    lines = [f"{k}: {v}" for k, v in head.items()] + [
        "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8",
        f"X-Session-Signature: v=1; a=sshsig; h=from:to:subject:date:message-id; b={b64}"]
    mail = tmp_path / "trusted.eml"
    mail.write_bytes(("\n".join(lines) + "\n\n").encode() + body)

    keytype, blob = Path(f"{key}.pub").read_text().split()[:2]
    import base64, hashlib
    h = hashlib.sha256(base64.b64decode(blob)).digest()[:16].hex()
    sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    reg = tmp_path / "registry"
    (reg / "certs").mkdir(parents=True)
    cert = reg / "certs" / f"{sid}.cert"
    cert.write_text(f"session-birth-v1\nsession-id: {sid}\npubkey: {keytype} {blob}\nagent: eve\n")
    bpub = " ".join(Path(f"{bkey}.pub").read_text().split()[:2])
    (reg / "allowed_signers").write_text(f'{BLESS_PRINCIPAL} namespaces="{BLESS_NS}" {bpub}\n')
    copy = tmp_path / "bless-input"
    copy.write_bytes(cert.read_bytes())
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(bkey), "-n", BLESS_NS, str(copy)],
                   check=True, capture_output=True, env={**os.environ, "SSH_AUTH_SOCK": ""})
    Path(f"{cert}.blessed-by.{BLESS_PRINCIPAL}.sig").write_bytes(Path(f"{copy}.sig").read_bytes())
    clean = tmp_path / "clean"
    clean.mkdir()
    env = {"SESSION_MAIL_SIG_NS": NS, "SESSION_REGISTRY": str(reg),
           "SESSION_BLESS_NS": BLESS_NS, "SESSION_BLESS_PRINCIPAL": BLESS_PRINCIPAL}
    return mail, clean, env


def trust(mail, cwd, env, drop=None):
    full = _env(**env)
    for name in ("SESSION_MAIL_SIG_NS", "SESSION_REGISTRY", "SESSION_BLESS_NS",
                 "SESSION_BLESS_PRINCIPAL"):
        if name not in env:
            full.pop(name, None)
    if drop:
        full.pop(drop)
    done = subprocess.run([str(VERIFIER), str(mail), "--trust"], cwd=cwd,
                          capture_output=True, text=True, env=full)
    return done.returncode, done.stdout.strip().splitlines()[-1], done


def test_every_trust_variable_still_takes_effect(trusted):
    mail, clean, env = trusted
    rc, line, done = trust(mail, clean, env)
    assert (rc, line) == (0, "TRUST: FULL"), done.stdout + done.stderr


@pytest.mark.parametrize("drop, want", [
    ("SESSION_MAIL_SIG_NS", (1, "TRUST: FAIL leg=1-signature reason=bad-signature")),
    ("SESSION_REGISTRY", (5, "TRUST: ERROR reason=registry-absent")),
    ("SESSION_BLESS_PRINCIPAL", (4, "TRUST: UNBLESSED reason=bless-absent")),
    ("SESSION_BLESS_NS", (1, "TRUST: FAIL leg=3-bless reason=bless-invalid")),
])
def test_taking_one_trust_variable_away_moves_the_verdict(trusted, drop, want):
    mail, clean, env = trusted
    rc, line, done = trust(mail, clean, env, drop=drop)
    assert (rc, line) == want, done.stdout + done.stderr


def test_the_trust_verdict_holds_with_a_plant_in_the_cwd(trusted, tmp_path):
    """The same FULL run from a cwd holding the lying subprocess.py, and the same
    mail tampered: FAIL, not FULL."""
    mail, _, env = trusted
    cwd = plant(tmp_path / "cwd-subprocess", "subprocess")
    assert trust(mail, cwd, env)[:2] == (0, "TRUST: FULL")
    bad = tmp_path / "tampered.eml"
    bad.write_bytes(_swap(mail.read_bytes(), b"\n\nhello\n", b"\n\nhellO\n"))
    assert trust(bad, cwd, env)[:2] == (1, "TRUST: FAIL leg=1-signature reason=bad-signature")


def test_an_inherited_verify_self_is_overwritten_by_the_wrapper(tmp_path, mails):
    """SESSIONMAIL_VERIFY_SELF: the wrapper names itself every time, so v2 still
    finds its own _sig.py under -I whatever the caller exported."""
    f, want = mails["v2-good"]
    clean = tmp_path / "clean"
    clean.mkdir()
    assert_verdict(verify(f, clean, SESSIONMAIL_VERIFY_SELF=str(tmp_path / "nowhere" / "x")), want)


def test_sessionmail_origin_still_qualifies_a_captured_wire(tmp_path):
    """SESSIONMAIL_ORIGIN (_sig.py:167): a never-delivered v2 wire verifies only with the
    sender's origin, so the right one is GOOD and a wrong one is BAD."""
    import json
    from sessionmail import _sig
    d = Path(__file__).parent / "fixtures" / "sig_v2"
    origin = json.loads((d / "vectors.json").read_text())["vectors"]["single"]["origin"]
    value = [v for n, v in _sig.fields((d / "single.eml").read_bytes()) if n == "x-session-signature"][0]
    wire = tmp_path / "single.wire.eml"
    wire.write_bytes(_sig.insert_header((d / "single.wire").read_bytes(),
                                        "X-Session-Signature:" + value.decode("ascii")))
    clean = tmp_path / "clean"
    clean.mkdir()
    assert_verdict(verify(wire, clean, SESSIONMAIL_ORIGIN=origin), "GOOD")
    assert_verdict(verify(wire, clean, SESSIONMAIL_ORIGIN="elsewhere.example.test"), "BAD")


# ── a side effect of -I: PYTHON* encoding variables no longer crash the verdict ──

@pytest.mark.parametrize("env", [{"PYTHONIOENCODING": "ascii"},
                                 {"LC_ALL": "C", "PYTHONUTF8": "0"}])
def test_a_python_encoding_variable_no_longer_breaks_a_good_mail(tmp_path, mails, env):
    """At 2bd8a2b a good mail was rc 1 here (UnicodeEncodeError printing the check
    mark). -I ignores PYTHON* variables, so the verdict stands (SM-668 F3's case)."""
    f, want = mails["v1-good"]
    clean = tmp_path / "clean"
    clean.mkdir()
    assert_verdict(verify(f, clean, **env), want)
