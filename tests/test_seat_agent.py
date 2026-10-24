"""SM-590 do-003: seat-agent - the seat's own ssh-agent, and its LaunchAgent template.

Design §8 Road 1 (UMA-6's seam), conditions 1-2; build sketch step 3. No launchd:
the wrapper runs as a plain child here, on a scratch key that seat-init
mints (do-002's layout) into a scratch $SESSIONMAIL_HOME and registry. Mail goes
through the sender's own `sign()` to a fake sendmail. No live key, agent,
registry or LaunchAgent is touched, and nothing here runs launchctl.

SM-715 do-001 moved the wrapper and the template verbatim into the package, and
do-002 made the wrapper a verb: these tests RUN it as `message seat agent`, through
the test venv's own `message`, which execs the packaged bash (design §1, §6), and
read the PACKAGED files under src/sessionmail/seat/. Never in-process: the verb
replaces the process. The old path, scripts/seat-agent/seat-agent, is a shim
(tests/test_seat_package.py).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

import pytest

from test_sender import EX_OK, EX_REFUSED, bin_dir, env, fake_sendmail, headers_of, root, sender, verifier  # noqa: F401
from test_seat_init import MESSAGE, git_env, registry, seat_init, tools  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / "src" / "sessionmail" / "seat"     # the packaged files (SM-715 do-001)
SHIMS = REPO / "scripts" / "seat-agent"            # the old path: the shim and the README
WRAPPER = HERE / "seat-agent"
TEMPLATE = HERE / "seat-agent.plist.template"
#: The verb every run goes through (SM-715 do-002): the venv's `message`, which execs WRAPPER.
AGENT = (str(MESSAGE), "seat", "agent")
ADDRESS = "desk.uma"


def ssh_dir() -> str:
    return os.path.dirname(shutil.which("ssh-agent"))


def answers(sock: str) -> int:
    """`ssh-add -l` on SOCK: 0 holds keys, 1 empty, 2 nothing answers."""
    return subprocess.run(["ssh-add", "-l"], capture_output=True,
                          env={**os.environ, "SSH_AUTH_SOCK": sock}).returncode


def agents_on(sock: str) -> list[str]:
    out = subprocess.run(["pgrep", "-f", f"ssh-agent -D -a {sock}"], capture_output=True, text=True)
    return out.stdout.split()


@pytest.fixture
def short_home(git_env):  # noqa: F811
    """A $SESSIONMAIL_HOME under /tmp, so the DEFAULT socket fits macOS's cap."""
    d = tempfile.mkdtemp(prefix="sm-sa-", dir="/tmp")
    git_env["SESSIONMAIL_HOME"] = os.path.join(d, "h")
    yield Path(d)
    for sock in Path(d).rglob("*.sock"):
        for pid in agents_on(str(sock)):
            os.kill(int(pid), signal.SIGKILL)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def minted(short_home, git_env, registry):  # noqa: F811
    rc, out, err = seat_init(git_env, "--registry", registry, ADDRESS)
    assert rc == 0, err
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / ADDRESS
    return dict(home=home, key=home / "id_ed25519", pub=home / "seat.pub",
                sock=str(home / "agent.sock"), reg=registry,
                env={"PATH": f"{ssh_dir()}:/usr/bin:/bin", "HOME": str(short_home),
                     "SESSIONMAIL_HOME": git_env["SESSIONMAIL_HOME"]})


def start(minted, *extra: str):
    log = open(minted["home"].parent.parent / f"wrapper-{time.monotonic_ns()}.log", "w+")
    proc = subprocess.Popen([*AGENT, ADDRESS, *extra], env=minted["env"],
                            stdout=log, stderr=log)
    return proc, log


def said(log) -> str:
    log.flush()
    log.seek(0)
    return log.read()


def run_wrapper(minted, *extra: str, timeout: float = 15):
    done = subprocess.run([*AGENT, ADDRESS, *extra], env=minted["env"],
                          capture_output=True, text=True, timeout=timeout)
    return done.returncode, done.stderr


@pytest.fixture
def seat_agent(minted, tools):  # noqa: F811
    """The wrapper, running, with the key loaded."""
    proc, log = start(minted)
    for _ in range(100):
        if answers(minted["sock"]) == 0 or proc.poll() is not None:
            break
        time.sleep(0.1)
    assert answers(minted["sock"]) == 0, said(log)
    # The key answers a beat before the wrapper prints its 'agent pid' line, and a
    # loaded host stretches that beat: wait on the line, as long as the socket wait.
    for _ in range(100):
        if re.search(r"agent pid (\d+)", said(log)) or proc.poll() is not None:
            break
        time.sleep(0.1)
    found = re.search(r"agent pid (\d+)", said(log))
    assert found, said(log)
    pid = int(found.group(1))
    yield dict(minted, proc=proc, log=log, agent=pid)
    if proc.poll() is None:
        proc.terminate()
        proc.wait(10)
    log.close()


def seat_send(sender, env, tmp_path, agent):  # noqa: F811
    """A send under a bare env (test_sender's `env`: no session, no keys) with only
    UMA-6's mapping, must-sign and the fake sendmail added (probe 7's mirror)."""
    env.update({"CLAUDE_SESSION_AGENT_SOCK": agent["sock"], "CLAUDE_SESSION_PUB": str(agent["pub"]),
                "SESSIONMAIL_REQUIRE_SIGNATURE": "1",
                "PATH": env["PATH"] + os.pathsep + ssh_dir()})
    body = tmp_path / "body.md"
    body.write_text("from the desk\n")
    return sender.run(["eve", "--from", ADDRESS, "--subject", "desk", "--body-file", str(body)], env)


# ── the seat agent signs, and its death refuses ─────────────────────────────

def test_a_send_through_the_seat_agent_is_signed_and_names_the_seat(sender, env, tmp_path, fake_sendmail,
                                                                    verifier, seat_agent):
    rc, _, err = seat_send(sender, env, tmp_path, seat_agent)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    eml = tmp_path / "seat.eml"
    eml.write_bytes(fake_sendmail.wire)
    done = subprocess.run([str(verifier), str(eml), "--trust"], capture_output=True, text=True,
                          env={**os.environ, "SESSION_REGISTRY": str(seat_agent["reg"])})
    assert done.returncode == 4, done.stdout + done.stderr
    assert f"agent: {ADDRESS}" in done.stdout + done.stderr and "TRUST: UNBLESSED" in done.stdout


def test_the_agent_killed_the_wrapper_exits_1_and_the_send_is_refused(sender, env, tmp_path,
                                                                      fake_sendmail, seat_agent):
    os.kill(seat_agent["agent"], signal.SIGKILL)
    assert seat_agent["proc"].wait(10) == 1, "non-zero, so launchd restarts the job"
    assert "the agent exited" in said(seat_agent["log"])
    assert not os.path.exists(seat_agent["sock"])
    rc, _, err = seat_send(sender, env, tmp_path, seat_agent)
    assert rc == EX_REFUSED and "NOTHING WAS SENT" in err
    assert fake_sendmail.wire is None


def test_term_stops_the_agent_and_removes_its_socket(seat_agent):
    """Started as `message seat agent`, the pid launchd would hold is bash's own (an
    exec, never a child), so the TERM reaches the wrapper's trap: rc 0, not -15."""
    ps = subprocess.run(["ps", "-o", "command=", "-p", str(seat_agent["proc"].pid)],
                        capture_output=True, text=True, check=False)
    assert ps.stdout.split()[:2] == ["/bin/bash", str(WRAPPER)], ps.stdout
    seat_agent["proc"].terminate()
    assert seat_agent["proc"].wait(10) == 0
    assert not os.path.exists(seat_agent["sock"])
    assert agents_on(seat_agent["sock"]) == []


# ── a load that fails exits non-zero, with nothing left running ──────────────

def test_a_key_that_will_not_load_exits_1(minted, tools):  # noqa: F811
    minted["key"].write_text("not a key\n")
    rc, err = run_wrapper(minted)
    assert rc == 1 and "did not load" in err, err
    assert not os.path.exists(minted["sock"]) and agents_on(minted["sock"]) == []


def test_a_pub_that_names_another_key_exits_1(minted, tmp_path, tools):  # noqa: F811
    other = tmp_path / "other"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    minted["pub"].write_text(Path(f"{other}.pub").read_text())
    rc, err = run_wrapper(minted)
    assert rc == 1 and "does not hold seat.pub's key" in err, err
    assert not os.path.exists(minted["sock"]) and agents_on(minted["sock"]) == []


# ── refusals: exit 2, nothing started ───────────────────────────────────────

@pytest.mark.parametrize("case", ["unminted", "loose-key", "loose-dir", "private-beside-pub", "plus",
                                  "not-a-socket"])
def test_it_refuses_and_starts_nothing(minted, tools, case):  # noqa: F811
    args = [ADDRESS]
    if case == "unminted":
        args = ["other.seat"]
    elif case == "loose-key":
        os.chmod(minted["key"], 0o640)
    elif case == "loose-dir":
        os.chmod(minted["home"], 0o750)
    elif case == "private-beside-pub":
        (minted["home"] / "seat").write_text("a private key where probe 8c finds it\n")
    elif case == "plus":
        args = ["uma+desk+x"]      # two '+': not a seat address (one '+' is, test_seat_plus.py)
    elif case == "not-a-socket":
        Path(minted["sock"]).write_text("someone's file\n")
    done = subprocess.run([*AGENT, *args], env=minted["env"], capture_output=True, text=True,
                          timeout=15)
    assert done.returncode == 2 and "Nothing was started" in done.stderr, done.stderr
    assert agents_on(minted["sock"]) == []
    if case == "not-a-socket":
        assert Path(minted["sock"]).read_text() == "someone's file\n", "never removed"


def test_a_live_agent_on_the_socket_is_refused_and_left_alone(minted, tools):  # noqa: F811
    other = subprocess.Popen(["ssh-agent", "-D", "-a", minted["sock"]], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if answers(minted["sock"]) == 1:
                break
            time.sleep(0.1)
        rc, err = run_wrapper(minted)
        assert rc == 2 and "a live agent already answers" in err, err
        assert other.poll() is None and answers(minted["sock"]) == 1, "the live agent is untouched"
    finally:
        other.kill()
        other.wait()


def test_a_stale_socket_is_cleared(minted, tools):  # noqa: F811
    s = socket.socket(socket.AF_UNIX)
    s.bind(minted["sock"])
    s.close()                                     # the file stays; nothing answers on it
    assert os.path.exists(minted["sock"]) and answers(minted["sock"]) == 2
    proc, log = start(minted)
    try:
        for _ in range(100):
            if answers(minted["sock"]) == 0 or proc.poll() is not None:
                break
            time.sleep(0.1)
        assert answers(minted["sock"]) == 0, said(log)
        assert "removed a stale socket" in said(log)
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()


@pytest.mark.parametrize("length, binds", [(103, True), (104, False)])
def test_the_socket_cap_is_103_bytes(minted, short_home, tools, length, binds):  # noqa: F811
    """macOS's sun_path is 104 bytes WITH its NUL: 103 binds, 104 is refused up front."""
    stem = str(short_home) + "/"
    sock = stem + "s" * (length - len(stem))
    assert len(sock.encode()) == length
    proc, log = start(minted, sock)
    try:
        if binds:
            for _ in range(100):
                if answers(sock) == 0 or proc.poll() is not None:
                    break
                time.sleep(0.1)
            assert answers(sock) == 0, said(log)
        else:
            assert proc.wait(10) == 2 and "macOS binds 103 at most" in said(log)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)
        log.close()


# ── the template is a template ──────────────────────────────────────────────

PLACEHOLDERS = ("ADDRESS", "MESSAGE", "SESSIONMAIL_HOME", "SOCKET", "LOG_DIR")


def test_the_template_renders_to_a_plist_that_lints(tmp_path):
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("plutil is macOS's; the template is for launchd")
    text = TEMPLATE.read_text()
    for name in PLACEHOLDERS:
        assert f"@{name}@" in text
        text = text.replace(f"@{name}@", {"ADDRESS": ADDRESS}.get(name, f"/scratch/{name.lower()}"))
    assert "@" not in text.split("<plist", 1)[1], "an unrendered placeholder"
    rendered = tmp_path / f"dev.sessionkit.mail.seat-agent.{ADDRESS}.plist"
    rendered.write_text(text)
    lint = subprocess.run([plutil, "-lint", str(rendered)], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    for var in ("TM_AGENT_ADDRESS", "DESK_SIGNER_SOCK", "DESK_SIGNER_PUB",
                "SESSIONMAIL_REQUIRE_SIGNATURE", "SESSION_REGISTRY"):
        assert var in text, f"the adapter's env documents {var}"
    assert "seat-keys/desk.uma/seat.pub" in text, "DESK_SIGNER_PUB names the pub apart from the key"
    args = re.search(r"<key>ProgramArguments</key>\s*<array>(.*?)</array>", text, re.DOTALL).group(1)
    assert re.findall(r"<string>(.*?)</string>", args) == [
        "/scratch/message", "seat", "agent", ADDRESS, "/scratch/socket"], "launchd runs `message seat agent`"
    assert "SEAT_AGENT" not in TEMPLATE.read_text(), "the old placeholder is gone, so an old render fails loudly"


def test_nothing_installs_or_bootstraps_it_and_message_seat_agent_reaches_the_packaged_files():
    """Nothing installs or bootstraps the LaunchAgent, and no fourth command: ruling
    (b) keeps [project.scripts] at exactly
    message, pager and memory, and the wrapper is reached as `message seat agent`
    (the verb's own paths are the packaged files)."""
    repo = REPO
    makefile = (repo / "Makefile").read_text()
    pyproject = (repo / "pyproject.toml").read_text()
    for word in ("launchctl", "LaunchAgents", "seat-agent"):
        assert word not in makefile, f"the Makefile mentions {word}"
    assert "seat-agent" not in pyproject and 'packages = ["src/sessionmail"]' in pyproject
    assert set(tomllib.loads(pyproject)["project"]["scripts"]) == {"message", "pager", "memory"}
    from sessionmail import message
    assert Path(message.SEAT_AGENT) == WRAPPER.resolve()
    assert Path(message.SEAT_TEMPLATE) == TEMPLATE.resolve()
    code = [line for line in WRAPPER.read_text().splitlines() if not line.lstrip().startswith("#")]
    assert not any("launchctl" in line or "LaunchAgents" in line for line in code)
    assert os.access(WRAPPER, os.X_OK) and (SHIMS / "README.md").is_file()
