"""The verifier never crashes on a bare address when $SESSIONMAIL_ORIGIN is unset.

A v2 signature canonicalises a still-bare address (a captured wire, never
delivered mail) with an origin, so the verifier asks `_sig.origin_of`. The
verifier loads `_sig.py` BY PATH, as a module with no package, on purpose
(`session-mail-verify`'s `load_sig`). `origin_of` used to import the settings
layer relatively whenever the variable was unset, and under the verifier that
raised `ImportError: attempted relative import with no known parent package`:
exit 1, read by every caller as a BAD signature, and under --trust no
`TRUST:` line at all. A stranger's host has no $SESSIONMAIL_ORIGIN.

Two halves of the fix, each pinned here:

  - `origin_of` reads the settings file only when it is the package's
    module. Loaded by path it takes the variable, then postconf, then "".
  - `_mhs.child_env` pins $SESSIONMAIL_ORIGIN from the settings file's
    `postfix.origin` when the variable is unset. A mail the package signed
    with the file's origin then verifies under the bundled verifier, which
    cannot read the file itself.

Every verifier here is the SHIPPED one, started the way `message verify`
starts it: `_mhs.oracle("verify")`, under `_mhs.child_env`. A fake `postconf`
first on PATH stands for the host's postfix, so no test reads the real one.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import write_mail_settings

from sessionmail import _mhs, _operator, _settings, _sig, message

NS = "session-mail@example.com"          # _sender.SIG_NS_DEFAULT
SEAT = "bot.alice"
HOST_ORIGIN = "origin.example"           # what the fake postconf answers
FILE_ORIGIN = "file.example"             # what a settings file says
SYSTEM_PYTHON = "/usr/bin/python3"
#: A host layer that sets `postfix.origin`.
FILE_SETTINGS = json.dumps({"version": 1, "hooks": False, "postfix": {"origin": FILE_ORIGIN}})

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")


# ── fixtures ─────────────────────────────────────────────────────────────────

class World:
    """A key, a registry holding its cert (unblessed), and a fake postconf."""

    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        self.postconf(HOST_ORIGIN)
        self.key = root / "k"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", SEAT,
                        "-f", str(self.key)], check=True, stdin=subprocess.DEVNULL)
        self.registry = root / "registry"
        (self.registry / "certs").mkdir(parents=True)
        t, b64 = (root / "k.pub").read_text().split()[:2]
        h = hashlib.sha256(base64.b64decode(b64)).digest()[:16].hex()
        sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        (self.registry / "certs" / f"{sid}.cert").write_text(
            f"session-birth-v1\nsession-id: {sid}\npubkey: {t} {b64}\nagent: {SEAT}\n")

    def postconf(self, origin: str) -> None:
        """A `postconf` that answers `-xh myorigin` with ORIGIN, as postfix would."""
        fake = self.bin / "postconf"
        fake.write_text('#!/bin/sh\n[ "$1 $2" = "-xh myorigin" ] && '
                        f'echo {origin} && exit 0\nexit 1\n')
        fake.chmod(0o755)

    def mail(self, origin: str) -> Path:
        """A v2 mail with a BARE To:, signed for real with ORIGIN as the
        signer's `origin_of()` would have given it."""
        head = [f"From: {SEAT} <{SEAT}@host.example>", "To: eve",
                "Date: Tue, 01 Jan 2030 12:00:00 +0000", "Subject: origin unset",
                "Message-ID: <1.2.3@host.example>", "MIME-Version: 1.0",
                "Content-Type: text/plain; charset=utf-8",
                "Content-Transfer-Encoding: 8bit"]
        body = "hello\n"
        wire = ("\n".join(head) + "\n\n" + body).encode()
        h, p = _sig.h_for(wire), _sig.digests(wire)
        assert _sig.has_bare_address(wire, h), "the mail must make the verifier ask origin_of"
        signed = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(self.key), "-n", NS, "-"],
                                input=_sig.canonical(wire, NS, h, p, origin),
                                capture_output=True, check=True).stdout.decode()
        armor = "".join(line for line in signed.split("\n") if line and "SSH SIGNATURE" not in line)
        out = self.root / f"mail-{origin}.eml"
        out.write_text("\n".join(head + [_sig.format_header(NS, h, p, armor)]) + "\n\n" + body)
        return out

    def env(self, **extra: str) -> dict[str, str]:
        """What `message verify` hands the verifier (`_mhs.child_env`), with
        $SESSIONMAIL_ORIGIN removed and the fake postconf first on PATH."""
        base = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        base.pop("SESSION_REGISTRY", None)
        env = _mhs.child_env(base)
        env.pop("SESSIONMAIL_ORIGIN", None)
        env.update(extra)
        return env

    def verify(self, mail: Path, *flags: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([*_mhs.oracle("verify"), str(mail), *flags],
                              capture_output=True, text=True, env=self.env(**extra), timeout=60)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def no_settings_origin():
    """The suite's settings home (conftest) sets no `postfix.origin`."""
    write_mail_settings(_operator.settings_home(), None)
    assert _settings.file_text("postfix.origin", {}) is None


#: The package's interpreter, and the stranger's: the system python3, which
#: has none of the package's dependencies. Skipped where it is absent.
INTERPRETERS = [
    pytest.param(None, id="package-python"),
    pytest.param(SYSTEM_PYTHON, id="system-python", marks=pytest.mark.skipif(
        not os.access(SYSTEM_PYTHON, os.X_OK), reason=f"no {SYSTEM_PYTHON} here")),
]


# ── the shipped verifier, as message verify runs it ──────────────────────────

@pytest.mark.parametrize("python", INTERPRETERS)
def test_the_shipped_verifier_reads_a_bare_address_with_no_origin_set(world, python):
    no_settings_origin()
    mail = world.mail(HOST_ORIGIN)
    extra = {"SESSIONMAIL_PYTHON": python} if python else {}
    done = world.verify(mail, **extra)
    assert "Traceback" not in done.stderr, done.stderr
    assert "ImportError" not in done.stderr, done.stderr
    assert "signature-crypto  : ✅ good" in done.stdout, done.stdout + done.stderr
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("python", INTERPRETERS)
def test_trust_ends_on_one_trust_line_unblessed(world, python):
    no_settings_origin()
    mail = world.mail(HOST_ORIGIN)
    extra = {"SESSION_REGISTRY": str(world.registry)}
    if python:
        extra["SESSIONMAIL_PYTHON"] = python
    done = world.verify(mail, "--trust", **extra)
    assert "Traceback" not in done.stderr, done.stderr
    trust = [line for line in done.stdout.splitlines() if line.startswith("TRUST:")]
    assert len(trust) == 1, done.stdout + done.stderr
    assert done.stdout.rstrip("\n").splitlines()[-1] == trust[0]
    assert trust[0].startswith("TRUST: UNBLESSED"), trust[0]
    assert done.returncode == 4, done.stdout + done.stderr


def test_a_reader_with_another_origin_reads_bad_never_a_crash(world):
    """The verifier's origin is its own: a mail signed with another one is BAD
    (exit 1, the crypto line says so), named, not a traceback."""
    no_settings_origin()
    mail = world.mail("elsewhere.example")
    done = world.verify(mail)
    assert "Traceback" not in done.stderr, done.stderr
    assert "signature-crypto  : ❌" in done.stdout, done.stdout + done.stderr
    assert done.returncode == 1


# ── origin_of, loaded by path as the verifier loads it ───────────────────────

def _by_path():
    spec = importlib.util.spec_from_file_location("sig_by_path", _sig.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert not mod.__package__, "loaded by path, the module has no package"
    return mod


def test_origin_of_by_path_never_raises(world, monkeypatch):
    mod = _by_path()
    # No postconf anywhere: nothing on PATH, no fallback.
    monkeypatch.setattr(mod, "POSTCONF_FALLBACK", ())
    assert mod.origin_of({"PATH": ""}) == ""
    # The host's postconf, as the verifier would find it on PATH.
    assert mod.origin_of({"PATH": str(world.bin)}) == HOST_ORIGIN


def test_origin_of_by_path_still_takes_the_variable_first(world):
    mod = _by_path()
    env = {"PATH": str(world.bin), "SESSIONMAIL_ORIGIN": " var.example "}
    assert mod.origin_of(env) == "var.example"


def test_origin_of_in_the_package_still_reads_the_settings_file(monkeypatch):
    monkeypatch.setattr(_sig.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(_sig, "POSTCONF_FALLBACK", ())
    write_mail_settings(_operator.settings_home(), FILE_SETTINGS)
    assert _sig.origin_of({}) == FILE_ORIGIN


# ── child_env pins the file's origin for every bundled script ───────────────

def test_child_env_pins_the_settings_files_origin_when_the_variable_is_unset():
    write_mail_settings(_operator.settings_home(), FILE_SETTINGS)
    assert _mhs.child_env({})[_mhs.ORIGIN_VAR] == FILE_ORIGIN


def test_child_env_keeps_a_set_variable():
    write_mail_settings(_operator.settings_home(), FILE_SETTINGS)
    env = _mhs.child_env({_mhs.ORIGIN_VAR: "var.example"})
    assert env[_mhs.ORIGIN_VAR] == "var.example"


def test_child_env_adds_nothing_with_no_file_value():
    no_settings_origin()
    env = _mhs.child_env({"PATH": "/p"})
    assert _mhs.ORIGIN_VAR not in env
    assert env == {"PATH": "/p", _mhs.PYTHON_VAR: env[_mhs.PYTHON_VAR]}


def test_message_verify_reads_the_signers_file_origin(world, monkeypatch):
    """The whole path: a host whose settings file says `postfix.origin` signs
    with it (in the package), and `message verify`, variable unset, hands it
    to the bundled verifier, whose own postconf says something else."""
    write_mail_settings(_operator.settings_home(), FILE_SETTINGS)
    assert _sig.origin_of({"PATH": str(world.bin)}) == FILE_ORIGIN
    mail = world.mail(FILE_ORIGIN)
    monkeypatch.delenv("SESSIONMAIL_ORIGIN", raising=False)
    monkeypatch.setenv("PATH", f"{world.bin}{os.pathsep}{os.environ.get('PATH', '')}")
    res = CliRunner().invoke(message.cli, ["verify", str(mail)])
    assert "Traceback" not in res.output, res.output
    assert res.exit_code == 0, res.output
