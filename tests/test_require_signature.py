"""SM-590 do-001: a send that cannot sign is REFUSED when the seat asks for it.

A send that cannot sign is refused, never sent unsigned. Design s8, F1: a per-send switch,
`SESSIONMAIL_REQUIRE_SIGNATURE`, read with `setting()` so the old
`SESSION_MAIL_` spelling works too. Unset, RULED 7(b) stands byte for byte.

The probes these mirror are the design's 8a (not keyed), 8b (keyed, agent
alive) and 8d (keyed, agent DEAD, the pub alone). Hermetic: a scratch
ssh-agent and key (`keyed`), a fake sendmail, the REAL `sign()`; nothing here
touches a live key, agent or box.
"""

from __future__ import annotations

import pytest

from test_sender import (EX_OK, EX_REFUSED, bin_dir, env, fake_sendmail, headers_of,  # noqa: F401
                         keyed, root, send, sender, verifier, fake_postfix)
from test_message_send_json import run as json_send, world  # noqa: F401

VAR = "SESSIONMAIL_REQUIRE_SIGNATURE"
#: The PATH before any fixture narrows it: `world` sets PATH to its fake bin
#: alone, and the scratch keyholder still has to find the ssh tools.
REAL_PATH = __import__("os").environ.get("PATH", "")


def kill_keyholder(keyed, tmp_path):
    """Probe 8d: the agent is gone and the private key is not beside the pub
    (with it there, `ssh-keygen -Y sign -f key.pub` signs from the file)."""
    keyed["CLAUDE_SESSION_AGENT_SOCK"] = str(tmp_path / "no-such-socket")
    (tmp_path / "session-key").unlink()


# ── 8a: not keyed ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [VAR, "SESSION_MAIL_REQUIRE_SIGNATURE"])
def test_not_keyed_and_must_sign_is_refused_and_the_mta_never_runs(sender, env, tmp_path, fake_sendmail, name):
    env[name] = "1"
    rc, out, err = send(sender, env, tmp_path)
    assert rc == EX_REFUSED and out == ""
    assert fake_sendmail.argv is None and fake_sendmail.wire is None, "nothing was handed to the MTA"
    assert f"${VAR} is set" in err and "no session key" in err and "NOTHING WAS SENT" in err
    assert "sending UNSIGNED" not in err, "no warning about a send that did not happen"


def test_not_keyed_is_refused_before_the_mta_check_and_preflight(sender, env, tmp_path, fake_sendmail, fake_postfix):
    """Knowable at once, so it comes first: neither a missing MTA nor an
    unmapped recipient gets to speak."""
    fake_postfix()                                   # `eve` unmapped: preflight would refuse
    env[VAR] = "1"
    with pytest.raises(sender.Refused) as refused:
        sender.send(sender.Request(to="eve", from_="ivan", subject="s"), b"x\n", env)
    assert refused.value.kind == "identity" and refused.value.sent == "no"
    assert refused.value.code == EX_REFUSED and refused.value.notices == []
    env["SESSIONMAIL_SENDMAIL"] = str(tmp_path / "no-such-sendmail")
    rc, _, err = send(sender, env, tmp_path)
    assert rc == EX_REFUSED and f"${VAR}" in err and "no MTA" not in err


# ── 8b: keyed, agent alive ───────────────────────────────────────────────────

def test_keyed_and_alive_with_must_sign_signs_and_verifies(sender, keyed, tmp_path, fake_sendmail, verifier):
    import subprocess

    keyed[VAR] = "1"
    rc, out, err = send(sender, keyed, tmp_path, body=b"must sign\n")
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    assert "UNSIGNED" not in err
    eml = tmp_path / "signed.eml"
    eml.write_bytes(fake_sendmail.wire)
    done = subprocess.run([str(verifier), str(eml)], capture_output=True, text=True)
    assert done.returncode == 0 and "good" in done.stdout + done.stderr, done.stdout + done.stderr


# ── 8d: keyed, agent DEAD, the pub alone ─────────────────────────────────────

def test_dead_keyholder_with_must_sign_is_refused_and_nothing_is_sent(sender, keyed, tmp_path, fake_sendmail):
    kill_keyholder(keyed, tmp_path)
    keyed[VAR] = "1"
    rc, out, err = send(sender, keyed, tmp_path)
    assert rc == EX_REFUSED and out == ""
    assert fake_sendmail.argv is None and fake_sendmail.wire is None
    assert f"${VAR} is set" in err and "signing FAILED" in err and "NOTHING WAS SENT" in err
    assert "sending UNSIGNED" not in err


def test_dead_keyholder_without_must_sign_still_sends_unsigned_and_warns(sender, keyed, tmp_path, fake_sendmail):
    """RULED 7(b) is unchanged when the switch is off."""
    kill_keyholder(keyed, tmp_path)
    rc, _, err = send(sender, keyed, tmp_path)
    assert rc == EX_OK
    assert "X-Session-Signature" not in dict(headers_of(fake_sendmail.wire))
    assert "keyed session but signing FAILED — sending UNSIGNED" in err


# ── unset (or off): today's behaviour, byte for byte ─────────────────────────

@pytest.mark.parametrize("value", ["", "0", "off", "no", "false", "OFF", " 0 "])
def test_off_is_byte_for_byte_what_unset_does(sender, env, tmp_path, fake_sendmail, value):
    mint = dict(now=1907536000, pid=4242, rand=777)
    rc0, out0, err0 = send(sender, env, tmp_path, **mint)
    wire0 = fake_sendmail.wire
    env[VAR] = value
    rc1, out1, err1 = send(sender, env, tmp_path, **mint)
    assert (rc1, out1, err1, fake_sendmail.wire) == (rc0, out0, err0, wire0)
    assert rc0 == EX_OK and "sending UNSIGNED" in err0


@pytest.mark.parametrize("value", ["1", "on", "yes", "true", "TRUE", "please"])
def test_any_other_value_turns_it_on_it_fails_closed(sender, env, value):
    env[VAR] = value
    assert sender.must_sign(env)


def test_unset_is_off(sender, env):
    assert not sender.must_sign(env)


# ── one code path: the --json door (and the MCP tool, whose child it is) ─────

def test_the_json_door_refuses_with_kind_identity_and_sent_no(world, monkeypatch):
    monkeypatch.setenv(VAR, "1")
    r, said = json_send("beta", "-s", "the point", "-f", str(world.body), "--json")
    assert r.exit_code == EX_REFUSED
    assert said["sent"] == "no" and said["kind"] == "identity" and VAR in said["message"]
    assert world.wire() is None and not world.tripped()


def test_the_plain_door_refuses_too(world, monkeypatch):
    monkeypatch.setenv(VAR, "1")
    r, _ = json_send("beta", "-s", "the point", "-f", str(world.body))
    assert r.exit_code == EX_REFUSED and VAR in r.stderr
    assert world.wire() is None


# ── do-006: a named bash oracle is never taken when the send must sign ───────
#
# The bash `session-mail-send` does not read $SESSIONMAIL_REQUIRE_SIGNATURE, so
# taking it would fail OPEN (do-001 review F1). Ruled: the oracle is
# NOT taken; the package's sender signs or refuses. `world`'s oracle is a
# tripwire that records it was started.

@pytest.fixture
def scratch_keyholder(monkeypatch):
    """A throwaway ed25519 key in a throwaway ssh-agent under /tmp (a short
    socket path), exported the way a keyed seat is. Never a live key."""
    import os
    import re
    import shutil
    import subprocess
    import tempfile

    tools = {t: shutil.which(t, path=REAL_PATH) for t in ("ssh-keygen", "ssh-agent", "ssh-add")}
    for tool, where in tools.items():
        if where is None:
            pytest.skip(f"{tool} not on this machine - the keyed path cannot be exercised")
    d = tempfile.mkdtemp(prefix="sm-agent-", dir="/tmp")
    key = os.path.join(d, "k")
    subprocess.run([tools["ssh-keygen"], "-t", "ed25519", "-N", "", "-f", key, "-C", "test-seat"],
                   check=True, capture_output=True)
    out = subprocess.run([tools["ssh-agent"], "-s", "-a", os.path.join(d, "s")],
                         check=True, capture_output=True, text=True).stdout
    sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", out).group(1)
    pid = re.search(r"SSH_AGENT_PID=(\d+);", out).group(1)
    subprocess.run([tools["ssh-add"], key], check=True, capture_output=True,
                   env={**os.environ, "SSH_AUTH_SOCK": sock})
    os.unlink(key)                                  # the key lives only in its agent
    monkeypatch.setenv("CLAUDE_SESSION_AGENT_SOCK", sock)
    monkeypatch.setenv("CLAUDE_SESSION_PUB", key + ".pub")
    monkeypatch.setenv("PATH", os.environ["PATH"] + os.pathsep + os.path.dirname(tools["ssh-keygen"]))
    yield
    try:
        os.kill(int(pid), 15)                       # no `kill` on world's narrowed PATH
    except ProcessLookupError:
        pass
    shutil.rmtree(d, ignore_errors=True)


def oracle_named(world, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))


def passed_over(stderr):
    """The notice lines that say the named oracle was passed over."""
    return [line for line in stderr.splitlines() if "is not consulted" in line]


def assert_one_loud_skip(stderr):
    """ONE notice, naming the oracle and why it was not taken (do-006 criterion 3)."""
    said = passed_over(stderr)
    assert len(said) == 1, f"one notice, not {len(said)}: {said}"
    assert "$SESSIONMAIL_SEND_ORACLE" in said[0] and f"${VAR}" in said[0], said[0]


def test_must_sign_with_an_oracle_and_no_key_is_refused_and_the_oracle_never_runs(world, monkeypatch):
    oracle_named(world, monkeypatch)
    monkeypatch.setenv(VAR, "1")
    r, _ = json_send("beta", "-s", "the point", "-f", str(world.body))
    assert r.exit_code == EX_REFUSED and f"${VAR} is set" in r.stderr and "no session key" in r.stderr
    assert not world.tripped(), "the bash oracle was started"
    assert world.wire() is None, "nothing was handed to the MTA"
    assert_one_loud_skip(r.stderr)


def test_must_sign_with_an_oracle_and_a_live_key_signs_through_the_package(world, monkeypatch, scratch_keyholder):
    oracle_named(world, monkeypatch)
    monkeypatch.setenv(VAR, "1")
    r, _ = json_send("beta", "-s", "the point", "-f", str(world.body))
    assert r.exit_code == 0, r.stderr
    assert not world.tripped(), "the bash oracle was started"
    assert "X-Session-Signature" in dict(headers_of(world.wire()))
    assert_one_loud_skip(r.stderr)


@pytest.mark.parametrize("value", [None, "0", "off"])
def test_must_sign_unset_or_off_still_takes_the_named_oracle(world, monkeypatch, value):
    oracle_named(world, monkeypatch)
    if value is None:
        monkeypatch.delenv(VAR, raising=False)
        monkeypatch.delenv("SESSION_MAIL_REQUIRE_SIGNATURE", raising=False)
    else:
        monkeypatch.setenv(VAR, value)
    r, _ = json_send("beta", "-s", "the point", "-f", str(world.body))
    assert world.tripped() and world.wire() is None, "unset/off = the bash, as before"
    assert passed_over(r.stderr) == [], "no skip notice when the oracle is taken"
