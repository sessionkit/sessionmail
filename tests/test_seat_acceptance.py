"""SM-590 do-004: the acceptance test. A seat with no Claude session sends signed mail.

design.md build sketch step 4, README requirements 1-4. All scratch, no launchd:

    `message seat init` (do-002)        mints the seat key + cert into a scratch git registry
    `message seat agent` (do-003)       holds the key in its own ssh-agent (it execs
                                        seat/seat-agent; SM-715 do-002 made both verbs)
    `message send` (the real CLI, a child process under an env built from nothing)
        with the adapter's env mapped the way UMA-6 maps it, and must-sign (do-001)
    session-mail-verify --trust         against the scratch registry, with a scratch
                                        bless principal (never judy@bless)

Every row of design.md §4 "What `message verify --trust` says" is reproduced, through
the sender's real `sign()`, plus the refusal when the seat agent dies. The seat's
address is a PARAMETER: nothing here knows about uma (README requirement 4).
No live key, agent, registry, LaunchAgent or mailbox is touched; the MTA is fake.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import TEST_ORIGIN
from test_sender import FAKE_SENDMAIL, HOST, write_fake
from test_seat_agent import AGENT, agents_on, answers, short_home, ssh_dir  # noqa: F401
from test_seat_init import git_env, registry, seat_init  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
BLESS_NS = "session-bless@example.com"              # lamp-check:37, session-bless:37
BLESS_PRINCIPAL = "scratch@bless"                   # never judy@bless (design §0)
TOOLS = ("ssh-keygen", "ssh-agent", "ssh-add", "git")


def other_local_parts(address: str) -> list[str]:
    """The design table's spoof row, derived from the address, not named: the seat's
    base box (`uma` for desk.uma) and that base with the seat as its tag (`uma+desk`)."""
    tag, _, base = address.rpartition(".")
    return [base, f"{base}+{tag}"]


def kin6(env: dict[str, str]) -> dict[str, str]:
    """UMA-6 design.md:55: strip the caller's CLAUDE_SESSION_AGENT_SOCK/PUB, set them
    ONLY from DESK_SIGNER_SOCK/PUB. The sender itself is unchanged (Road 1)."""
    out = {k: v for k, v in env.items() if k not in ("CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB")}
    out["CLAUDE_SESSION_AGENT_SOCK"] = env["DESK_SIGNER_SOCK"]
    out["CLAUDE_SESSION_PUB"] = env["DESK_SIGNER_PUB"]
    return out


class Seat:
    """One seat, end to end. Each method is one step a seat's operator, its adapter
    or a reader takes; the test reads as the design table."""

    def __init__(self, address, tmp_path, git_env, registry):  # noqa: F811
        self.address, self.tmp, self.git_env, self.reg = address, tmp_path, git_env, registry
        self.home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / address
        self.sock = str(self.home / "agent.sock")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        write_fake(self.bin / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
        self.wire = tmp_path / "sendmail.wire"
        (tmp_path / "mail").mkdir()
        self.unregistered = tmp_path / "registry-before-mint"
        self.agent = self.log = None

    # ── the operator: mint, then run the seat agent ──
    def mint(self):
        shutil.copytree(self.reg, self.unregistered)   # the registry as it was before
        rc, out, err = seat_init(self.git_env, "--registry", self.reg, self.address)
        assert rc == 0, err
        assert "NOT blessed" in out

    def run_agent(self):
        self.log = open(self.tmp / "seat-agent.log", "w+")
        self.agent = subprocess.Popen(
            [*AGENT, self.address], stdout=self.log, stderr=self.log,
            env={"PATH": f"{ssh_dir()}:/usr/bin:/bin", "HOME": str(self.tmp),
                 "SESSIONMAIL_HOME": self.git_env["SESSIONMAIL_HOME"]})
        for _ in range(100):
            if answers(self.sock) == 0 or self.agent.poll() is not None:
                break
            time.sleep(0.1)
        assert answers(self.sock) == 0, self.said()

    def said(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def kill_agent(self):
        pid = int(self.said().split("agent pid ", 1)[1].split()[0])
        os.kill(pid, signal.SIGKILL)
        assert self.agent.wait(10) == 1, "the wrapper exits non-zero, so launchd restarts it"

    def stop(self):
        if self.agent is not None and self.agent.poll() is None:
            self.agent.terminate()
            self.agent.wait(10)
        if self.log is not None:
            self.log.close()

    # ── the adapter: `message send`, a child under an env built from nothing ──
    def send(self, as_address: str, must_sign: bool = True) -> tuple[int, str, bytes | None]:
        self.wire.unlink(missing_ok=True)
        body = self.tmp / "body.md"
        body.write_text(f"from {as_address}, a seat with no Claude session\n")
        adapter = {
            "PATH": f"{self.bin}:{ssh_dir()}:/usr/bin:/bin",   # no postmap: no live maps read
            "HOME": str(self.tmp),
            "PYTHONPATH": str(REPO / "src"),
            "SESSIONMAIL_ROOT": str(self.tmp / "mail"),
            "SESSIONMAIL_HOST": HOST,
            "SESSIONMAIL_SENDMAIL": str(self.bin / "sendmail"),
            "FAKE_SENDMAIL_WIRE": str(self.wire),
            "FAKE_SENDMAIL_ARGV": str(self.tmp / "sendmail.argv"),
            # the seat-agent template's documented adapter env (do-003):
            "TM_AGENT_ADDRESS": as_address,
            "DESK_SIGNER_SOCK": self.sock,
            "DESK_SIGNER_PUB": str(self.home / "seat.pub"),
            "SESSIONMAIL_REQUIRE_SIGNATURE": "1" if must_sign else "",
            "SESSION_REGISTRY": str(self.reg),
            # SM-594: a v2 signature qualifies a bare address with the mail origin.
            # This env is built from nothing, so conftest's never reaches the child,
            # which would ask the host's postfix while the reader (os.environ) uses
            # TEST_ORIGIN: the two would sign and check different addresses.
            "SESSIONMAIL_ORIGIN": TEST_ORIGIN,
        }
        done = subprocess.run(
            [sys.executable, "-c", "from sessionmail.message import cli; cli(prog_name='message')",
             "send", "eve", "-s", "seat acceptance", "-f", str(body)],
            env=kin6(adapter), capture_output=True, text=True, timeout=60)
        wire = self.wire.read_bytes() if self.wire.exists() else None
        if wire is not None:
            (self.tmp / f"{as_address}.eml").write_bytes(wire)
        return done.returncode, done.stderr, wire

    # ── the reader: --trust ──
    def trust(self, as_address: str, registry: Path | None = None) -> tuple[int, str]:
        done = subprocess.run(
            [str(REPO / "src" / "sessionmail" / "mhs" / "session-mail-verify"),
             str(self.tmp / f"{as_address}.eml"), "--trust"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "SESSION_REGISTRY": str(registry or self.reg),
                 "SESSION_BLESS_PRINCIPAL": BLESS_PRINCIPAL, "SESSION_BLESS_NS": BLESS_NS})
        return done.returncode, done.stdout + done.stderr

    def sid(self) -> str:
        return next(p.name[:-len(".cert")] for p in (self.reg / "certs").glob("*.cert"))

    # ── the blesser: a SCRATCH principal, the ceremony's shape (session-bless:101-110) ──
    def bless(self):
        key = self.tmp / "bless-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", BLESS_PRINCIPAL,
                        "-f", str(key)], check=True)
        pub = " ".join(Path(f"{key}.pub").read_text().split()[:2])
        with open(self.reg / "allowed_signers", "a") as f:
            f.write(f'{BLESS_PRINCIPAL} namespaces="{BLESS_NS}" {pub}\n')
        cert = self.reg / "certs" / f"{self.sid()}.cert"
        copy = self.tmp / "bless-input"
        shutil.copyfile(cert, copy)                  # sign a byte copy, never the cert
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", BLESS_NS, str(copy)],
                       check=True, capture_output=True, env={**os.environ, "SSH_AUTH_SOCK": ""})
        shutil.copyfile(f"{copy}.sig", f"{cert}.blessed-by.{BLESS_PRINCIPAL}.sig")

    def alter_cert(self):
        cert = self.reg / "certs" / f"{self.sid()}.cert"
        text = cert.read_text()
        assert "\nmachine: " in text
        cert.write_text(text.replace("\nmachine: ", "\nmachine: elsewhere-"))


@pytest.fixture
def tools():
    missing = [t for t in TOOLS if shutil.which(t) is None]
    if missing:
        pytest.skip(f"{', '.join(missing)} not on this machine: a seat key cannot be minted, "
                    f"held or verified without them")


@pytest.mark.parametrize("address", ["desk.uma", "night.relay"])
def test_a_seat_walks_the_trust_table(address, tools, short_home, git_env, registry, tmp_path):  # noqa: F811
    seat = Seat(address, tmp_path, git_env, registry)
    try:
        seat.mint()
        seat.run_agent()

        # The adapter sends as the seat: signed by the seat agent, through the real sender.
        rc, err, wire = seat.send(address)
        assert rc == 0, err
        assert wire is not None and b"\nX-Session-Signature: " in wire
        head = wire.split(b"\n\n", 1)[0].decode()
        assert re.search(rf"^From: .*<{re.escape(address)}>$", head, re.M), head

        # Row 1. Key generated, nothing registered: FAIL leg=2-registry reason=no-cert (rc 1).
        rc, said = seat.trust(address, registry=seat.unregistered)
        assert rc == 1 and "TRUST: FAIL leg=2-registry reason=no-cert" in said, said

        # Row 2. Cert registered, not blessed: names the seat, UNBLESSED (rc 4), never FULL.
        rc, said = seat.trust(address)
        assert rc == 4, said
        assert f"→ agent: {address}" in said and "TRUST: UNBLESSED reason=bless-absent" in said

        # Row 5. The seat key speaking as another local-part. Under must-sign the
        # SENDER refuses it now (SM-802: the key's seat is the address, and
        # neither the base box nor base+tag is that seat) and nothing reaches the
        # MTA; sent without must-sign it signs as-is, warns, and the receiver
        # still says agent-mismatch (rc 1).
        for other in other_local_parts(address):
            rc, err, wire = seat.send(other)
            assert rc == 2 and wire is None and "NOTHING WAS SENT" in err, err
            assert f"names {address!r}" in err and f"From is {other!r}" in err, err
            rc, err, wire = seat.send(other, must_sign=False)
            assert rc == 0 and wire is not None and f"names {address!r}" in err, err
            rc, said = seat.trust(other)
            assert rc == 1 and "TRUST: FAIL leg=2-registry reason=agent-mismatch" in said, (other, said)

        # Row 3. Blessed (a scratch principal): FULL (rc 0).
        seat.bless()
        rc, said = seat.trust(address)
        assert rc == 0, said
        assert f"bless-sig         : ✅ {BLESS_PRINCIPAL}" in said and said.rstrip().endswith("TRUST: FULL")

        # Row 4. Cert altered after blessing: FAIL leg=3-bless reason=bless-invalid (rc 1).
        seat.alter_cert()
        rc, said = seat.trust(address)
        assert rc == 1 and "TRUST: FAIL leg=3-bless reason=bless-invalid" in said, said

        # The seat agent dies: the same send is REFUSED (rc 2) and nothing reaches the MTA.
        seat.kill_agent()
        assert agents_on(seat.sock) == []
        rc, err, wire = seat.send(address)
        assert rc == 2 and "NOTHING WAS SENT" in err, err
        assert wire is None, "nothing reached the fake MTA"
    finally:
        seat.stop()

