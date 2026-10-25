"""SM-901: an UNEXPECTED exception inside `session-mail-verify` is "could not decide", never BAD.

The defect (SM-948 r01, measured at 306ddc4): a RuntimeError planted in `_sig.parse_header`
makes plain, `--trust` and `--json` all print a traceback and exit rc 1, BAD's code, so every
caller reads a verifier bug as a forgery. `--trust` prints no TRUST: line and `--json` prints
no object. The only wrapper round main() is `except SystemExit`.

The verifier already has a "could not decide" code, and these tests ask for it, unchanged:
plain rc 3 with leg 1 unverified/verifier-incomplete; under `--trust` rc 5 and
`TRUST: ERROR reason=verifier-incomplete` (the header comment's rc-5 row, and load_sig(), the
load-time path). Under `--json` the object is the one a broken install already gets
(tests/test_verify_json.py::test_a_broken_install_is_one_unverified_object).

The plant: the shipped verifier COPIED into a temp install, with a copy of `_sig.py` beside
its `mhs/` (load_sig() reads `../_sig.py` by path, from the script's own install) whose named
function is replaced by one that raises. Never the live ~/.mail, never the installed tree.

CONTROL: a genuine BAD signature (a signed header changed) still exits rc 1 in all three
modes, through the shipped verifier and through the planted install's verifier with an
unplanted `_sig.py` copy alike.

The planted cases were strict xfail until the fix landed (fix-001 removed the marker).

AFTER LEG 1 (fix-001): a fault planted after leg 1 has recorded "good" (and, under --json, the
signature; under --trust, the cert's agent) must not leak what was recorded into the object:
the verifier-incomplete object is the same one, result unverified, signature and agent null.
That plant edits the COPIED verifier's text (the faults there are the verifier's own code).

NO ssh-keygen ON PATH (fix-001): the real-world trigger rca-001 measured. A minimal host with
no openssh-client was told every signed mail is forged (rc 1, a FileNotFoundError traceback).

Run: uv run pytest -q tests/test_verify_unexpected_exception.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sessionmail import _mhs

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
SIG_PY = VERIFIER.parent.parent / "_sig.py"
D = Path(__file__).parent / "fixtures" / "sig_v2"
ORIGIN = json.loads((D / "vectors.json").read_text())["vectors"]["single"]["origin"]
PLANTED = "planted by SM-901 repro"

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")

TRUST_ERROR = "TRUST: ERROR reason=verifier-incomplete"


def install(tmp_path: Path, plant: str | None) -> Path:
    """A copy of the shipped install (mhs/session-mail-verify + _sig.py), `plant` (a function
    name in _sig) redefined to raise a RuntimeError. None: an honest copy (the control)."""
    root = tmp_path / "install"
    (root / "mhs").mkdir(parents=True)
    script = root / "mhs" / "session-mail-verify"
    shutil.copy2(VERIFIER, script)
    text = SIG_PY.read_text()
    if plant is not None:
        text += (f"\n\ndef {plant}(*a, **k):\n"
                 f"    raise RuntimeError({PLANTED!r} + ': ' + {plant!r})\n")
    (root / "_sig.py").write_text(text)
    return script


def run(tmp_path: Path, script: Path, raw: bytes, *flags: str, registry: bool = True):
    f = tmp_path / "mail.eml"
    f.write_bytes(raw)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SESSIONMAIL_ORIGIN", "SESSIONMAIL_PYTHON", "SESSIONMAIL_VERIFY_SELF",
                        "SESSION_REGISTRY")}
    env["SESSIONMAIL_ORIGIN"] = ORIGIN
    if registry:
        reg = tmp_path / "registry"
        (reg / "certs").mkdir(parents=True, exist_ok=True)
        env["SESSION_REGISTRY"] = str(reg)
    return subprocess.run([str(script), str(f), *flags], capture_output=True, text=True, env=env)


def good_mail() -> bytes:
    return (D / "single.eml").read_bytes()


def bad_mail() -> bytes:
    """A genuine BAD signature: a SIGNED header (Subject) changed after signing."""
    raw = good_mail()
    assert b"Subject: design done\n" in raw
    return raw.replace(b"Subject: design done\n", b"Subject: design undone\n", 1)


def no_traceback(done):
    # No debug switch exists in the verifier (measured: no "debug" in it), so a
    # traceback is never the verdict, on either stream.
    assert "Traceback (most recent call last)" not in done.stdout, done.stdout
    assert "Traceback (most recent call last)" not in done.stderr, done.stderr


# Where the exception is planted. parse_header: SM-948's site, reached by EVERY mail (the
# ONE byte parse reads v= for v1 too). below_floor: a v2-only site, later in leg 1, after
# the header parsed.
PLANTS = ["parse_header", "below_floor"]


# ── the planted exception: could not decide, never BAD ───────────────────────

@pytest.mark.parametrize("plant", PLANTS)
def test_plain_mode_is_rc3_verifier_incomplete_not_bad(tmp_path, plant):
    done = run(tmp_path, install(tmp_path, plant), good_mail())
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert "TRUST:" not in done.stdout
    # stderr says what broke: the exception the run could not decide past.
    assert "RuntimeError" in done.stderr and PLANTED in done.stderr, done.stderr


@pytest.mark.parametrize("plant", PLANTS)
@pytest.mark.parametrize("quiet", [(), ("--quiet",)], ids=["loud", "quiet"])
def test_trust_mode_is_rc5_error_verifier_incomplete(tmp_path, plant, quiet):
    done = run(tmp_path, install(tmp_path, plant), good_mail(), "--trust", *quiet)
    assert done.returncode == 5, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    lines = done.stdout.strip().splitlines()
    # the invariant: EXACTLY ONE TRUST: line, the last line of stdout
    assert lines and lines[-1] == TRUST_ERROR, done.stdout
    assert sum(ln.startswith("TRUST:") for ln in lines) == 1, done.stdout


@pytest.mark.parametrize("plant", PLANTS)
def test_json_mode_is_one_unverified_object(tmp_path, plant):
    script = install(tmp_path, plant)
    done = run(tmp_path, script, good_mail(), "--json")
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert done.stdout.count("\n") == 1, done.stdout
    assert json.loads(done.stdout) == {
        "schema": 1, "rc": 3, "result": "unverified", "reason": "verifier-incomplete",
        "signature": None, "trust": None}


@pytest.mark.parametrize("plant", PLANTS)
def test_json_trust_mode_is_one_error_object(tmp_path, plant):
    script = install(tmp_path, plant)
    done = run(tmp_path, script, good_mail(), "--json", "--trust")
    assert done.returncode == 5, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert done.stdout.count("\n") == 1, done.stdout
    assert not any(ln.startswith("TRUST:") for ln in done.stdout.splitlines())
    assert json.loads(done.stdout) == {
        "schema": 1, "rc": 5, "result": "unverified", "reason": "verifier-incomplete",
        "signature": None,
        "trust": {"verdict": "ERROR", "leg": None, "reason": "verifier-incomplete",
                  "line": TRUST_ERROR, "agent": None}}


# ── the CONTROL: a genuine BAD signature is still rc 1 ───────────────────────

@pytest.fixture(params=["shipped", "honest-copy"])
def verifier(request, tmp_path):
    """The shipped verifier, and the temp install with an UNPLANTED _sig.py: the rig itself
    is not what turns a verdict."""
    return VERIFIER if request.param == "shipped" else install(tmp_path, None)


def test_control_the_good_mail_verifies(tmp_path, verifier):
    done = run(tmp_path, verifier, good_mail())
    assert done.returncode == 0, (done.stdout, done.stderr)


def test_control_a_bad_signature_is_still_rc1_plain(tmp_path, verifier):
    done = run(tmp_path, verifier, bad_mail())
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert "❌ BAD" in done.stdout
    no_traceback(done)


def test_control_a_bad_signature_is_still_rc1_trust(tmp_path, verifier):
    done = run(tmp_path, verifier, bad_mail(), "--trust")
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert done.stdout.strip().splitlines()[-1] == "TRUST: FAIL leg=1-signature reason=bad-signature"


def test_control_a_bad_signature_is_still_rc1_json(tmp_path, verifier):
    done = run(tmp_path, verifier, bad_mail(), "--json")
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    obj = json.loads(done.stdout)
    assert (obj["rc"], obj["result"], obj["reason"], obj["signature"]) == (1, "bad", "bad-signature", None)
    done = run(tmp_path, verifier, bad_mail(), "--json", "--trust")
    obj = json.loads(done.stdout)
    assert (done.returncode, obj["rc"], obj["trust"]["verdict"], obj["trust"]["reason"]) == \
        (1, 1, "FAIL", "bad-signature")


# ── after leg 1: nothing the run recorded reaches the object ─────────────────

# Anchors in the COPIED verifier. A `raise` goes in before the anchor line, at its indent.
#   v2-good: verify_v2's hand-off to --trust, after leg1("good") and (--json) J["sig"].
#   agent:   run_trust, once leg 2 passed and J["agent"] was recorded.
AFTER = {
    "v2-good": "    if trust:\n        run_trust(sid, keyline, msg)\n",
    "agent": '    J["agent"] = agent\n',
}


def install_after(tmp_path: Path, where: str) -> Path:
    """The honest temp install, its verifier with a RuntimeError planted AFTER `where`'s
    state was recorded (the first occurrence: verify_v2 sits above v1's main)."""
    script = install(tmp_path, None)
    text = script.read_text()
    anchor = AFTER[where]
    assert anchor in text, where
    if where == "agent":
        assert text.count(anchor) == 1
        text = text.replace(anchor, anchor + f"    raise RuntimeError({PLANTED!r})\n", 1)
    else:
        text = text.replace(anchor, f"    raise RuntimeError({PLANTED!r})\n" + anchor, 1)
    script.write_text(text)
    return script


def register(tmp_path: Path) -> None:
    """A cert for the fixture's signer, so leg 2 passes (unblessed: no .sig beside it)."""
    import base64
    import hashlib
    kt, kb = (D / "sessionmail-sig-TEST-VECTORS-ONLY.pub").read_text().split()[:2]
    h = hashlib.sha256(base64.b64decode(kb)).digest()[:16].hex()
    sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    certs = tmp_path / "registry" / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    (certs / f"{sid}.cert").write_text(
        f"session-birth-v1\nsession-id: {sid}\npubkey: {kt} {kb}\nagent: acme+t-1.worker\n")


INCOMPLETE = {"schema": 1, "rc": 3, "result": "unverified", "reason": "verifier-incomplete",
              "signature": None, "trust": None}
INCOMPLETE_TRUST = {**INCOMPLETE, "rc": 5, "trust": {
    "verdict": "ERROR", "leg": None, "reason": "verifier-incomplete", "line": TRUST_ERROR,
    "agent": None}}


def test_the_registered_control_is_unblessed(tmp_path):
    # the rig: with the cert, the honest copy reaches leg 3 (so the agent plant is after leg 2)
    register(tmp_path)
    done = run(tmp_path, install(tmp_path, None), good_mail(), "--json", "--trust")
    obj = json.loads(done.stdout)
    assert (done.returncode, obj["trust"]["verdict"], obj["trust"]["agent"]) == \
        (4, "UNBLESSED", "acme+t-1.worker"), (done.stdout, done.stderr)
    assert obj["signature"] is not None


def test_after_leg1_good_plain_is_rc3(tmp_path):
    done = run(tmp_path, install_after(tmp_path, "v2-good"), good_mail())
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert "RuntimeError" in done.stderr and PLANTED in done.stderr, done.stderr


def test_after_leg1_good_json_drops_the_recorded_signature(tmp_path):
    done = run(tmp_path, install_after(tmp_path, "v2-good"), good_mail(), "--json")
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert done.stdout.count("\n") == 1, done.stdout
    assert json.loads(done.stdout) == INCOMPLETE


@pytest.mark.parametrize("where", ["v2-good", "agent"])
def test_after_leg1_good_trust_is_rc5_error(tmp_path, where):
    register(tmp_path)
    done = run(tmp_path, install_after(tmp_path, where), good_mail(), "--trust")
    assert done.returncode == 5, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    lines = done.stdout.strip().splitlines()
    assert lines[-1] == TRUST_ERROR and sum(ln.startswith("TRUST:") for ln in lines) == 1
    if where == "agent":
        # leg 2's own line printed before the fault: the per-leg lines stay, the verdict is ERROR
        assert any(ln.startswith("registry-cert") and "✅" in ln for ln in lines), done.stdout


@pytest.mark.parametrize("where", ["v2-good", "agent"])
def test_after_leg1_good_json_trust_drops_signature_and_agent(tmp_path, where):
    register(tmp_path)
    done = run(tmp_path, install_after(tmp_path, where), good_mail(), "--json", "--trust")
    assert done.returncode == 5, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert done.stdout.count("\n") == 1, done.stdout
    assert json.loads(done.stdout) == INCOMPLETE_TRUST


# ── a KeyboardInterrupt is not the verifier's to decide ──────────────────────

def test_a_keyboard_interrupt_is_not_turned_into_a_verdict(tmp_path):
    # `except Exception`, not BaseException: Ctrl-C keeps Python's own handling (not rc 3/5)
    script = install(tmp_path, None)
    text = script.read_text()
    anchor = AFTER["v2-good"]
    script.write_text(text.replace(anchor, "    raise KeyboardInterrupt\n" + anchor, 1))
    done = run(tmp_path, script, good_mail(), "--trust")
    assert done.returncode not in (0, 1, 3, 4, 5), (done.returncode, done.stdout, done.stderr)
    assert "TRUST:" not in done.stdout


# ── the real-world trigger: no ssh-keygen on PATH ────────────────────────────

def bare_path(tmp_path: Path) -> str:
    """A PATH holding python3, bash and env only: no ssh-keygen (rca-001 a2)."""
    import sys
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, target in (("python3", sys.executable), ("bash", shutil.which("bash")),
                         ("env", shutil.which("env"))):
        (bin_ / name).symlink_to(target)
    return str(bin_)


@pytest.mark.parametrize("flags, rc", [((), 3), (("--trust",), 5), (("--json",), 3),
                                       (("--json", "--trust"), 5)],
                         ids=["plain", "trust", "json", "json-trust"])
def test_no_ssh_keygen_on_path_is_could_not_decide(tmp_path, flags, rc):
    f = tmp_path / "mail.eml"
    f.write_bytes(good_mail())
    reg = tmp_path / "registry"
    (reg / "certs").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SESSIONMAIL_PYTHON", "SESSIONMAIL_VERIFY_SELF", "SESSION_REGISTRY")}
    env.update(PATH=bare_path(tmp_path), SESSIONMAIL_ORIGIN=ORIGIN, SESSION_REGISTRY=str(reg))
    done = subprocess.run([str(VERIFIER), str(f), *flags], capture_output=True, text=True,
                          env=env)
    assert done.returncode == rc, (done.returncode, done.stdout, done.stderr)
    no_traceback(done)
    assert "FileNotFoundError" in done.stderr and "ssh-keygen" in done.stderr, done.stderr
    if "--json" in flags:
        assert json.loads(done.stdout) == (INCOMPLETE_TRUST if "--trust" in flags else INCOMPLETE)
    elif "--trust" in flags:
        assert done.stdout.strip().splitlines()[-1] == TRUST_ERROR
    else:
        assert "TRUST:" not in done.stdout
