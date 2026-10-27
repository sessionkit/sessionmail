"""A plus seat (`heard+airpods`) mints a key and a cert, and its cert matches
exactly that seat: `message seat init` accepts a plus address.

What a `+` does is decided once and pinned here, in each place a seat
address lands. It is KEPT VERBATIM everywhere, never mapped:

- the seat-keys directory: `$SESSIONMAIL_HOME/seat-keys/heard+airpods/`;
- the birth cert's `agent:` line: `agent: heard+airpods`, exactly;
- the registry commit: the paths are sid-named (`certs/<sid>.cert`), so no
  `+` reaches a path; the author is `heard+airpods (seat)` and the subject
  `registry: birth of heard+airpods seat <sid>`;
- `message seat agent`: the launchd Label `dev.sessionkit.mail.seat-agent.
  heard+airpods`, the plist and log file names, and the default socket
  `seat-keys/heard+airpods/agent.sock`. A launchd Label is any string
  (launchd.plist(5)); launchctl splits a service target only on '/', and no
  seat address holds one. MEASURED on macOS 27 (launchctl print
  user/<uid>/dev.sessionkit.mail.seat-agent.heard+airpods): launchd reads
  the whole label as one service name, exactly as it reads the dotted form.

The grammar: a name (lowercase letters, digits, '.', '_', '-', starting
with a letter or digit), or ONE '+' between two such names. Three copies
hold it (seat_init.ADDRESS, _launchd.ADDRESS_RE, the wrapper's
SEAT_ADDRESS); the first test pins them equal.

The proof (leg 2 of the verifier keeps the +tag and matches it exactly): a
cert for heard+airpods passes From heard+airpods@host and fails From
heard@host and heard+other@host; a cert for heard fails From
heard+airpods@host.

Hermetic: scratch SESSIONMAIL_HOMEs and scratch registries (tmp git repos)
only, a scratch ssh-agent, a fake sendmail and the conftest's fake
launchctl. No live key, agent, registry or LaunchAgent is touched.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from sessionmail import _launchd, seat_init as minter
from test_sender import EX_OK, HOST, bin_dir, env, fake_sendmail, headers_of, root, sender, verifier  # noqa: F401
from test_seat_init import (MESSAGE, git_env, head, nothing_written, registry, seat_init,  # noqa: F401
                            tools)
from test_seat_install import message, pairs, scratch  # noqa: F401

PLUS = "heard+airpods"
WRAPPER = Path(__file__).resolve().parents[1] / "src" / "sessionmail" / "seat" / "seat-agent"
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS's")

#: Admitted by every copy of the grammar.
GOOD = ["heard+airpods", "desk.alice", "heard", "proj+tm-123.worker", "a+b", "h_1+d-2.x"]
#: Refused by every copy: each one breaks exactly one rule of the grammar.
BAD = ["heard+", "+airpods", "heard++airpods", "heard+air+pods", "heard+Airpods", "Heard+airpods",
       "heard+.x", "heard+-x", "heard+a/b", "heard+air pods", "heard+a@b", "", ".heard", "../heard",
       "heard+airpods\n", "desk.alice\n"]     # a trailing newline: `\Z`, never `$`


def wrapper_admits(address: str) -> bool:
    """The wrapper's own SEAT_ADDRESS test, run by bash, read from the file."""
    line = next(ln for ln in WRAPPER.read_text().splitlines() if ln.startswith("SEAT_ADDRESS="))
    script = f"{line}\n[[ \"$1\" =~ $SEAT_ADDRESS ]]"
    return subprocess.run(["/bin/bash", "-c", script, "-", address]).returncode == 0


@pytest.mark.parametrize("address", GOOD + BAD)
def test_the_three_copies_of_the_grammar_agree(address):
    want = address in GOOD
    assert bool(minter.ADDRESS.match(address)) is want
    assert bool(_launchd.ADDRESS_RE.match(address)) is want
    assert wrapper_admits(address) is want


@pytest.mark.parametrize("address", ["heard+airpods\n", "desk.alice\n"])
def test_launchd_refuses_a_seat_address_with_a_trailing_newline(address):
    """`$` under re.match also matches before a final newline; ADDRESS_RE ends
    in `\\Z`, so the newline is refused before it can reach a Label or a path."""
    assert _launchd.ADDRESS_RE.match(address) is None
    with pytest.raises(_launchd.Refused, match="is not a seat address"):
        _launchd._check_address(address)


# ── seat init: a plus seat mints; a malformed one is refused ────────────────

def mint(git_env, registry, address, machine="hq"):  # noqa: F811
    rc, out, err = seat_init(git_env, "--registry", registry, "--machine", machine, address)
    assert rc == 0, err
    sid = re.search(r"sid\s+: (\S+)", out).group(1)
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / address
    return dict(out=out, sid=sid, home=home, reg=registry, address=address,
                pub=home / "seat.pub", key=home / "id_ed25519")


@pytest.fixture
def plus_minted(git_env, registry):  # noqa: F811
    before = head(registry, git_env)
    return dict(mint(git_env, registry, PLUS), before=before)


def test_the_key_dir_is_named_by_the_address_plus_and_all(plus_minted, git_env):  # noqa: F811
    keys = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys"
    assert sorted(p.name for p in keys.iterdir()) == [PLUS], "no mapped or extra directory"
    assert sorted(p.name for p in plus_minted["home"].iterdir()) == [
        "birth-cert.txt", "birth-cert.txt.sig", "id_ed25519", "seat.pub"]
    assert f"private key : {plus_minted['key']}" in plus_minted["out"]


def test_the_cert_agent_line_is_the_address_exactly(plus_minted):
    cert = (plus_minted["reg"] / "certs" / f"{plus_minted['sid']}.cert").read_text().splitlines()
    assert "agent: heard+airpods" in cert
    assert [ln for ln in cert if ln.startswith("agent:")] == ["agent: heard+airpods"]
    assert (plus_minted["home"] / "birth-cert.txt").read_text().splitlines() == cert


def test_the_registry_commit_names_the_plus_seat_and_its_paths_are_sid_named(plus_minted, git_env):  # noqa: F811
    reg, sid = plus_minted["reg"], plus_minted["sid"]
    log = subprocess.run(["git", "-C", str(reg), "rev-list", f"{plus_minted['before']}..HEAD"],
                         capture_output=True, text=True, env=git_env).stdout.split()
    assert len(log) == 1, "ONE commit"
    files = subprocess.run(["git", "-C", str(reg), "show", "--name-only", "--format=", "HEAD"],
                           capture_output=True, text=True, env=git_env).stdout.split()
    assert sorted(files) == sorted(["registry/allowed_signers", f"registry/certs/{sid}.cert",
                                    f"registry/certs/{sid}.cert.sig"])
    assert not any("+" in f for f in files), "the address never reaches a registry path"
    meta = subprocess.run(["git", "-C", str(reg), "log", "-1", "--format=%an%n%ae%n%s"],
                          capture_output=True, text=True, env=git_env).stdout.splitlines()
    assert meta == [f"{PLUS} (seat)", f"{sid}@session", f"registry: birth of {PLUS} seat {sid}"]
    ok = subprocess.run(["git", "-C", str(reg), "-c",
                         f"gpg.ssh.allowedSignersFile={reg / 'allowed_signers'}",
                         "verify-commit", "HEAD"], capture_output=True, text=True, env=git_env)
    assert ok.returncode == 0 and f"{sid}@session" in ok.stderr, ok.stderr
    assert f"registry: bless {PLUS} seat {sid}" in plus_minted["out"], "the bless command names it"


@pytest.mark.parametrize("address", [a for a in BAD if a])
def test_a_malformed_address_is_still_refused_and_nothing_is_written(git_env, registry, address):  # noqa: F811
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, address)
    assert rc == 2 and "not a seat address" in err and "Nothing was written" in err, err
    assert "strips the tag" not in err and "with a dot" not in err, "the stale reason is gone"
    nothing_written(git_env, registry, before)


def test_a_second_plus_mint_is_refused_and_leaves_the_registry_alone(plus_minted, git_env):  # noqa: F811
    after = head(plus_minted["reg"], git_env)
    rc, _, err = seat_init(git_env, "--registry", plus_minted["reg"], PLUS)
    assert rc == 2 and "never overwrites" in err
    assert head(plus_minted["reg"], git_env) == after


# ── the proof: leg 2 keeps the +tag and matches it exactly ──────────────────

@pytest.fixture
def two_seats(git_env, registry):  # noqa: F811
    """heard+airpods and heard, minted into ONE scratch registry, and one scratch
    ssh-agent holding both keys (the sender picks by CLAUDE_SESSION_PUB)."""
    seats = {a: mint(git_env, registry, a) for a in (PLUS, "heard")}
    d = tempfile.mkdtemp(prefix="sm-plus-", dir="/tmp")           # a short socket path
    out = subprocess.run(["ssh-agent", "-s", "-a", os.path.join(d, "s")], check=True,
                         capture_output=True, text=True).stdout
    sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", out).group(1)
    pid = re.search(r"SSH_AGENT_PID=(\d+);", out).group(1)
    for s in seats.values():
        subprocess.run(["ssh-add", str(s["key"])], check=True, capture_output=True,
                       env={**os.environ, "SSH_AUTH_SOCK": sock})
    yield dict(seats=seats, sock=sock, reg=registry)
    subprocess.run(["kill", pid], capture_output=True)
    shutil.rmtree(d, ignore_errors=True)


def signed_as(sender, env, tmp_path, fake_sendmail, two_seats, cert_for: str,  # noqa: F811
              from_: str) -> Path:
    """A mail signed by CERT_FOR's key, From FROM_, as the wire carries it."""
    env = dict(env, CLAUDE_SESSION_AGENT_SOCK=two_seats["sock"],
               CLAUDE_SESSION_PUB=str(two_seats["seats"][cert_for]["pub"]),
               PATH=env["PATH"] + os.pathsep + os.path.dirname(shutil.which("ssh-keygen")))
    body = tmp_path / "body.md"
    body.write_text("heard a turn\n")
    rc, _, err = sender.run(["eve", "--from", from_, "--subject", "heard", "--body-file",
                             str(body)], env)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    eml = tmp_path / f"{cert_for}-as-{from_}.eml"
    eml.write_bytes(fake_sendmail.wire)
    return eml


def trust(verifier, eml: Path, reg: Path):  # noqa: F811
    done = subprocess.run([str(verifier), str(eml), "--trust"], capture_output=True, text=True,
                          env={**os.environ, "SESSION_REGISTRY": str(reg)})
    return done.returncode, done.stdout + done.stderr


def test_a_plus_cert_passes_leg_2_for_its_own_plus_from(sender, env, tmp_path, fake_sendmail,  # noqa: F811
                                                        verifier, two_seats):
    eml = signed_as(sender, env, tmp_path, fake_sendmail, two_seats, PLUS, f"{PLUS}@{HOST}")
    rc, said = trust(verifier, eml, two_seats["reg"])
    sid = two_seats["seats"][PLUS]["sid"]
    assert rc == 4, said                       # leg 2 passed; only the blessing is absent
    assert f"sender-session-id : {sid}" in said
    assert f"agent: {PLUS}" in said and "TRUST: UNBLESSED reason=bless-absent" in said
    assert "agent-mismatch" not in said


@pytest.mark.parametrize("from_", [f"heard@{HOST}", f"heard+other@{HOST}"])
def test_a_plus_cert_fails_leg_2_for_the_base_and_for_another_tag(sender, env, tmp_path,  # noqa: F811
                                                                  fake_sendmail, verifier,
                                                                  two_seats, from_):
    eml = signed_as(sender, env, tmp_path, fake_sendmail, two_seats, PLUS, from_)
    rc, said = trust(verifier, eml, two_seats["reg"])
    assert rc == 1 and "TRUST: FAIL leg=2-registry reason=agent-mismatch" in said, said
    assert f"cert agent '{PLUS}' != From local-part '{from_.split('@')[0]}'" in said


def test_a_base_cert_fails_leg_2_for_a_plus_from(sender, env, tmp_path, fake_sendmail,  # noqa: F811
                                                 verifier, two_seats):
    eml = signed_as(sender, env, tmp_path, fake_sendmail, two_seats, "heard", f"{PLUS}@{HOST}")
    rc, said = trust(verifier, eml, two_seats["reg"])
    assert rc == 1 and "TRUST: FAIL leg=2-registry reason=agent-mismatch" in said, said
    assert f"cert agent 'heard' != From local-part '{PLUS}'" in said


# ── message seat agent: label, plist, log and socket keep the '+' ───────────

@pytest.fixture
def plus_seat(scratch, registry, tools):  # noqa: F811
    """heard+airpods, minted with an explicit --registry into the scratch home."""
    rc, _, err = seat_init(scratch["env"], "--registry", registry, PLUS)
    assert rc == 0, err
    home = Path(scratch["env"]["SESSIONMAIL_HOME"]) / "seat-keys" / PLUS
    return dict(scratch, home=home, pub=home / "seat.pub", sock=str(home / "agent.sock"))


@darwin
def test_the_plan_keeps_the_plus_in_label_plist_log_and_socket(plus_seat):
    job = _launchd.plan(PLUS, None, plus_seat["env"])
    home = plus_seat["env"]["HOME"]
    label = f"dev.sessionkit.mail.seat-agent.{PLUS}"
    assert job.label == label and job.target == f"gui/{os.getuid()}/{label}"
    assert job.plist == os.path.join(home, "Library", "LaunchAgents", f"{label}.plist")
    assert job.log == os.path.join(home, ".sessionkit", "mail", "log", f"seat-agent.{PLUS}.log")
    assert job.old_log == os.path.join(home, "Library", "Logs", f"seat-agent.{PLUS}.log")
    assert job.socket == plus_seat["sock"] and job.socket.endswith(f"/seat-keys/{PLUS}/agent.sock")
    rendered = plistlib.loads(_launchd.render(Path(_launchd.TEMPLATE).read_bytes(), job))
    assert rendered["Label"] == label
    assert rendered["ProgramArguments"][3:] == [PLUS, plus_seat["sock"]]
    assert rendered["StandardErrorPath"] == job.log


@darwin
def test_install_and_uninstall_a_plus_seat(plus_seat, launchctl_fake):
    env, label = plus_seat["env"], f"dev.sessionkit.mail.seat-agent.{PLUS}"
    done = message(env, "seat", "agent", "--install", PLUS)
    assert done.returncode == 0, done.stderr
    report = pairs(done.stdout)
    assert report["SEAT_LABEL"] == label and report["SEAT_SOCKET"] == plus_seat["sock"]
    assert report["SEAT_PLIST"].endswith(f"/Library/LaunchAgents/{label}.plist")
    assert report["SEAT_LOG"].endswith(f"/.sessionkit/mail/log/seat-agent.{PLUS}.log")
    assert plistlib.loads(Path(report["SEAT_PLIST"]).read_bytes())["Label"] == label
    assert launchctl_fake.calls()[-1] == ["bootstrap", f"gui/{os.getuid()}", report["SEAT_PLIST"]]
    held = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "SSH_AUTH_SOCK": plus_seat["sock"]})
    assert plus_seat["pub"].read_text().split()[1] in held.stdout, "the wrapper ran the plus seat"
    gone = message(env, "seat", "agent", "--uninstall", PLUS)
    assert gone.returncode == 0, gone.stderr
    assert not Path(report["SEAT_PLIST"]).exists() and not os.path.exists(plus_seat["sock"])


@darwin
def test_install_still_refuses_a_malformed_plus_address(plus_seat, launchctl_fake):
    done = message(plus_seat["env"], "seat", "agent", "--install", "heard+air+pods")
    assert done.returncode == 2 and "is not a seat address" in done.stderr
    assert "with a dot" not in done.stderr
    assert launchctl_fake.calls() == []


def test_the_wrapper_runs_a_plus_seat_on_its_default_socket(plus_seat):
    """No launchd: `message seat agent heard+airpods` as a plain child, the
    default socket under seat-keys/heard+airpods/, then a TERM."""
    env = dict(plus_seat["env"], PATH=os.path.dirname(shutil.which("ssh-agent")) + ":/usr/bin:/bin")
    log = Path(plus_seat["env"]["HOME"]) / "seat-agent.log"
    started = f"{PLUS}: agent pid"
    with log.open("w") as sink:
        proc = subprocess.Popen([str(MESSAGE), "seat", "agent", PLUS], env=env,
                                stdout=subprocess.DEVNULL, stderr=sink)
    try:
        # Wait for the wrapper's own started line, not just for the agent to
        # answer: the key answers ssh-add -L a beat before the wrapper says so,
        # and a TERM in that beat (a loaded host) would race the line away.
        for _ in range(300):
            if started in log.read_text() or proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is None, log.read_text()
        assert started in log.read_text(), log.read_text()
        held = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "SSH_AUTH_SOCK": plus_seat["sock"]})
        assert plus_seat["pub"].read_text().split()[1] in held.stdout
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    err = log.read_text()
    assert proc.returncode == 0, err
    assert "stopped by a signal" in err and not os.path.exists(plus_seat["sock"])
