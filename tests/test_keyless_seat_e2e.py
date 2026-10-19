"""SM-807: a seat with NO hardware key works end to end, unblessed.

This pins a condition of seat identity (SK-46): it works for someone who
has no hardware key, who then does not bless the seat.

Each piece is already tested on its own: seat init (tests/test_seat_init.py),
the seat agent (tests/test_seat_agent.py), rc 4 UNBLESSED
(tests/mhs/test_verify_registry.py) and REQUIRE_BLESSING (tests/test_memory_run_trust.py).
This file runs them as ONE chain, as a person with no hardware key would. The
chain runs in a scratch HOME. The only ssh-agent is the seat agent the test
starts, and the registry is a scratch git repo:

  1. `message seat init --registry <scratch> keyless.seat` exits 0. It prints
     the sid, the fingerprint and the bless command, and does not run the bless.
  2. `message seat agent keyless.seat` holds the key, and a mail sent as the
     seat is SIGNED and delivered into a scratch box.
  3. `message verify --trust` on the delivered file exits 4. Its last line is
     `TRUST: UNBLESSED reason=bless-absent`.
  4. A /BOOT/WAKE item that the seat signed through its agent RUNS under
     `memory run /BOOT/WAKE` (SESSIONMAIL_MEMORY_RUN=1, SESSION_REGISTRY=<scratch>):
     its marker file appears. The program is in the BODY with a shebang, so
     it runs however SM-771 is ruled.

"No hardware key" is enforced, not assumed. Seat init, the sends, verify and
memory run all run with SSH_SK_PROVIDER set to a library that does not exist,
so any FIDO/security-key operation fails. Seat init runs with no SSH_AUTH_SOCK
at all. The seat agent runs under Seat's env, which is built from nothing.
Every later child sees only that agent's socket, and the test checks that it
holds exactly the seat's one software `ssh-ed25519` key. These changes would each break a step, and the failure
message names the condition: seat init needing hardware or an agent (step 1),
"unblessed" becoming an error (step 3), REQUIRE_BLESSING flipping (step 4).

Hermetic. It uses no real key, agent, registry, `~/.mail` or MTA. Each send is
the sender `message send` runs (`_sender.run`, as test_seat_init and
test_seat_agent send; test_seat_acceptance drives the CLI itself). The send
goes to test_sender's fake sendmail and lands as postfix delivers it
(test_sender_v2's `delivered`). `memory run` reads a notmuch stub
(test_memory_run_trust's) that serves the delivered files. Skips only when a required binary (ssh-keygen, ssh-agent,
ssh-add, git) is absent: the `tools` fixture.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import TEST_ORIGIN
from test_memory_run_trust import NOTMUCH
from test_seat_acceptance import Seat
from test_seat_agent import short_home, ssh_dir  # noqa: F401
from test_seat_init import MESSAGE, git_env, registry, seat_init, tools  # noqa: F401
from test_sender import EX_OK, bin_dir, body_of, env, fake_sendmail, headers_of, root, sender  # noqa: F401
from test_sender_v2 import delivered

#: The installed `memory` verb, beside `message` in the test venv.
MEMORY = Path(sys.executable).parent / "memory"
#: A fixture seat (a dot, never a `+`) and a fixture box. No real seat or person.
ADDRESS = "keyless.seat"
INBOX = "scratch.inbox"
ROOT_HINT = "KEYLESS_SEAT_ROOT"          # mhs-thread's `$<AGENT>_ROOT`, for keyless.seat
#: A security-key provider that cannot load, so any hardware-key path fails.
NO_SK = "/nonexistent/sm-807-no-security-key-provider.dylib"
CONDITION = ("The owner's condition for seat identity (SK-46, SM-807): a seat with NO hardware "
             "key must work end to end, unblessed")


def broken(step: int, what: str, evidence: str = "") -> str:
    """Every failure names the condition first, then the step that broke it."""
    return f"{CONDITION}. BROKEN at step {step}: {what}\n{evidence}"


def no_agent_but(env: dict[str, str], sock: str | None) -> dict[str, str]:
    """ENV with no ssh-agent but SOCK (none at all if None) and no usable security-key provider."""
    out = {k: v for k, v in env.items() if k not in ("SSH_AUTH_SOCK", "SSH_AGENT_PID")}
    out["SSH_SK_PROVIDER"] = NO_SK
    if sock is not None:
        out["SSH_AUTH_SOCK"] = sock
    return out


@pytest.fixture
def seat(tools, short_home, git_env, registry, tmp_path):  # noqa: F811
    """test_seat_acceptance's Seat (it runs `message seat agent` with no
    SSH_AUTH_SOCK and stops it at teardown), over a git_env with no agent and
    no usable security-key provider. `short_home`'s teardown then kills any
    ssh-agent left on a socket under the scratch dir."""
    git_env.update(no_agent_but(git_env, None))
    (tmp_path / "seat").mkdir()
    s = Seat(ADDRESS, tmp_path / "seat", git_env, registry)
    yield s
    s.stop()


def send_as_seat(sender, env, tmp_path, fake_sendmail, to: str, subject: str, body: str,  # noqa: F811
                 box: Path, name: str) -> tuple[Path, bytes]:
    """One send as the seat, through the seat agent, must-sign and --raw. The wire
    lands in BOX/new/ as postfix delivers it (test_sender_v2's `delivered`): the
    sender writes bare addresses, and postfix qualifies them with its origin."""
    body_path = tmp_path / f"{name}.body"
    body_path.write_text(body)
    rc, _, err = sender.run([to, "--from", ADDRESS, "--subject", subject,
                             "--body-file", str(body_path), "--raw"], env)
    assert rc == EX_OK, broken(2, f"the send as {ADDRESS} to {to} was refused (rc {rc})", err)
    wire = fake_sendmail.wire
    assert "X-Session-Signature" in dict(headers_of(wire)), broken(
        2, f"the mail to {to} went out UNSIGNED: the seat agent's software key did not sign it", err)
    for sub in ("new", "cur", "tmp"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    path = box / "new" / f"{time.time_ns()}.sm807.{name}"
    o = TEST_ORIGIN.encode()
    landed = delivered(wire, {f"From: {ADDRESS}\n".encode(): f"From: {ADDRESS}@".encode() + o + b"\n",
                              f"To: {to}\n".encode(): f"To: {to}@".encode() + o + b"\n"})
    path.write_bytes(landed)
    return path, landed


def notmuch_message(path: Path, wire: bytes, ts: int) -> dict:
    """The delivered file as `notmuch show --format=json` gives it to mhs-thread."""
    h = dict(headers_of(wire))
    return {"id": h["Message-ID"].strip("<>"), "timestamp": ts, "filename": [str(path)],
            "headers": {k: h[k] for k in ("Subject", "From", "To", "Date")},
            "body": [{"id": 1, "content-type": "text/plain",
                      "content": body_of(wire).decode("utf-8")}]}


def test_a_seat_with_no_hardware_key_inits_sends_verifies_unblessed_and_runs_its_wake(
        seat, git_env, registry, sender, env, tmp_path, fake_sendmail, root):  # noqa: F811
    # ── 1. seat init: no agent at all, no security-key provider ─────────────
    assert "SSH_AUTH_SOCK" not in git_env and git_env["SSH_SK_PROVIDER"] == NO_SK
    rc, out, err = seat_init(git_env, "--registry", registry, ADDRESS)
    assert rc == 0, broken(1, f"`message seat init` exited {rc} with no hardware key and no "
                              "ssh-agent", out + err)
    sid_m = re.search(r"sid\s+: (\S+)", out)
    assert sid_m, broken(1, "seat init printed no sid", out)
    sid = sid_m.group(1)
    assert re.search(r"fingerprint : 256 SHA256:\S+", out), broken(1, "no fingerprint printed", out)
    assert "NOT blessed" in out and "LAMP_BLESS=1" in out and "session-bless" in out, broken(
        1, "seat init did not print the bless command for a person to run", out)
    assert not list(registry.glob("certs/*blessed-by*")), broken(
        1, "seat init RAN the bless: a keyless seat is never blessed by init", out)
    pub, key = seat.home / "seat.pub", seat.home / "id_ed25519"
    publine = " ".join(pub.read_text().split()[:2])
    assert publine.startswith("ssh-ed25519 ") and key.is_file(), broken(
        1, f"the seat key is not a SOFTWARE ed25519 key on disk: {publine[:40]!r}")
    assert (registry / "certs" / f"{sid}.cert").is_file(), broken(1, "no cert in the registry")

    # ── 2. the seat agent, and a signed mail into a scratch box ─────────────
    sock = seat.sock
    try:
        seat.run_agent()                  # `message seat agent keyless.seat`, no SSH_AUTH_SOCK
    except AssertionError as exc:
        pytest.fail(broken(2, "`message seat agent` did not come up holding the key", str(exc)))
    held = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True,
                          env=no_agent_but({"PATH": os.environ["PATH"]}, sock)).stdout.splitlines()
    assert [" ".join(line.split()[:2]) for line in held] == [publine], broken(
        2, "the only agent must hold exactly the seat's one software key", "\n".join(held))

    env.update(no_agent_but(env, sock))
    env.update({"CLAUDE_SESSION_AGENT_SOCK": sock, "CLAUDE_SESSION_PUB": str(pub),
                "SESSIONMAIL_REQUIRE_SIGNATURE": "1", "PATH": env["PATH"] + os.pathsep + ssh_dir()})
    hello, _ = send_as_seat(sender, env, tmp_path, fake_sendmail, INBOX, "keyless hello",
                            "from a seat with no hardware key\n", root / INBOX, "hello")

    # ── 3. message verify --trust: rc 4, UNBLESSED, never an error ──────────
    child = no_agent_but(dict(os.environ), sock)
    child.update({"HOME": str(tmp_path), "SESSION_REGISTRY": str(registry)})
    done = subprocess.run([str(MESSAGE), "verify", str(hello), "--trust"],
                          capture_output=True, text=True, env=child)
    said3 = done.stdout + done.stderr
    assert done.returncode == 4, broken(3, f"`message verify --trust` exited {done.returncode}, "
                                           "not 4 (UNBLESSED): an unblessed seat is not an error",
                                        said3)
    lines = done.stdout.splitlines()
    assert lines and lines[-1] == "TRUST: UNBLESSED reason=bless-absent", broken(
        3, "the last line is not `TRUST: UNBLESSED reason=bless-absent`", said3)
    assert f"sender-session-id : {sid}" in said3 and f"agent: {ADDRESS}" in said3, broken(
        3, "the verifier did not trace the mail to the cert seat init minted", said3)

    # ── 4. a /BOOT/WAKE item signed by the seat RUNS under memory run ────────
    marker = tmp_path / "keyless-wake-ran"
    script = ("#!/bin/sh\n"
              "# SM-807: a keyless, unblessed seat's wake item runs.\n"
              f"touch '{marker}'\n"
              "echo keyless-wake-ran\n")
    memory_box = root / ADDRESS
    to = f"{ADDRESS}+memory"
    sent = [send_as_seat(sender, env, tmp_path, fake_sendmail, to, subject, body, memory_box, name)
            for name, subject, body in (("root", "[ROOT]", "root\n"), ("boot", "[BOOT]", "boot\n"),
                                        ("wake", "[WAKE]", "wake\n"),
                                        ("item", "10 keyless wake marker", script))]
    root_m, boot_m, wake_m, item_m = (notmuch_message(p, w, 2000 + i) for i, (p, w) in enumerate(sent))
    stub = tmp_path / "nm"
    (stub / "bin").mkdir(parents=True)
    (stub / "bin" / "notmuch").write_text(NOTMUCH.format(T=stub))
    (stub / "bin" / "notmuch").chmod(0o755)
    (stub / "thread.json").write_text(json.dumps(
        [[[root_m, [[boot_m, [[wake_m, [[item_m, []]]]]]]]]]))
    (stub / "root.json").write_text(json.dumps([[[root_m, []]]]))

    run_env = {k: v for k, v in child.items() if k not in ("SESSION_MAIL_VERIFY", "SESSION_AGENT")}
    run_env.update({"PATH": f"{stub / 'bin'}{os.pathsep}{child['PATH']}",
                    "CLAUDE_CODE_AGENT": ADDRESS, ROOT_HINT: root_m["id"],
                    "SESSIONMAIL_MEMORY_RUN": "1", "SESSION_REGISTRY": str(registry)})
    done = subprocess.run([str(MEMORY), "run", "/BOOT/WAKE"], capture_output=True, text=True,
                          env=run_env, timeout=120)
    said4 = done.stdout + done.stderr
    assert marker.exists(), broken(4, "the seat's own signed /BOOT/WAKE item did NOT run "
                                      "(did REQUIRE_BLESSING flip?)", said4)
    assert done.returncode == 0, broken(4, f"`memory run /BOOT/WAKE` exited {done.returncode}",
                                        said4)
    header = next((ln for ln in done.stdout.splitlines() if ln.startswith("═══ 10 ")), "")
    assert "UNBLESSED" in header and "REFUSED" not in done.stdout, broken(
        4, "the item must run AND be named UNBLESSED", said4)
