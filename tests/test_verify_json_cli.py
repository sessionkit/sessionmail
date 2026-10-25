"""SM-788 do-002: `message verify --json`, the CLI door and the MCP door.

The verifier writes the object (tests/test_verify_json.py pins its bytes). This file
pins what sits in front of it:

- the CLI passes `--json` on, as it passes `--quiet` and `--trust`, and adds no word
  of its own to stdout;
- the CLI's ONE object of its own, when the verifier is absent, has the verifier's
  key set (a parity test against the real verifier's objects, not a copy of them);
- the MCP tool `message_verify` (MEASURED here, `_mcp.py` unchanged by ruling):
  rc 0 hands back the bare object; any other rc, UNBLESSED's 4 included, raises a
  ToolError whose text is `exit N: ` + stderr + a newline + the object, so the
  object is the last line, and under --quiet it shares its line with the prefix.

New tests go in new files (the do-001 criterion: no existing test file is edited).

Run: uv run pytest -q tests/test_verify_json_cli.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, _mhs
from sessionmail.message import cli
from test_message_list import box, deliver  # noqa: F401  (fixture reused)
from test_message_verify import oracle  # noqa: F401  (fixture reused)
from test_verify_json import SCRUB, VERIFIER, Rig, base, wire
from tool_call import call

TOP = {"schema", "rc", "result", "reason", "signature", "trust"}
TRUST = {"verdict", "leg", "reason", "line", "agent"}
ABSENT_LINE = "TRUST: ERROR reason=verifier-absent"

needs_ssh = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                               reason="ssh-keygen not on this machine")


def run(*args):
    return CliRunner().invoke(cli, ["verify", *args])


@pytest.fixture
def clean_env(monkeypatch):
    """The verifier reads these; a test machine's own values must not reach it."""
    for var in (*SCRUB, "SESSIONMAIL_MHS_ORACLE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not on this machine")
    return Rig(tmp_path_factory.mktemp("sm788cli"))


# ── the CLI passes the flag on ───────────────────────────────────────────────

def test_json_is_passed_on_to_the_verifier_and_its_stdout_comes_back(box, oracle):
    deliver(box, "alpha", "1923171201")
    r = run("1", "--json")
    assert r.exit_code == 0, r.output
    (call,) = oracle.calls
    assert call[2:] == ["--json"]
    assert r.stdout == f"verifier says: {call[1]} --json\n"   # its stdout, nothing added


def test_json_rides_after_the_other_flags_in_one_order(box, oracle):
    deliver(box, "alpha", "1923171201")
    run("1", "--json", "--quiet", "--trust", "--require-blessing")
    assert oracle.calls[0][2:] == ["--trust", "--require-blessing", "--quiet", "--json"]


@pytest.mark.parametrize("rc", [0, 1, 2, 3, 4, 5])
def test_under_json_the_verifiers_exit_code_still_comes_back_untouched(box, oracle, monkeypatch, rc):
    deliver(box, "alpha", "1923171201")
    monkeypatch.setenv("FAKE_RC", str(rc))
    assert run("1", "--json", "--trust").exit_code == rc


def test_no_such_message_under_json_prints_no_object(box, oracle):
    """Design 3.4: no object when the mail was never found. A missing object is "no verdict"."""
    deliver(box, "alpha", "1923171201")
    r = run("7", "--json")
    assert r.exit_code == 3 and r.stdout == "" and oracle.calls == []


# ── the verifier-absent object: the CLI's one writer ─────────────────────────

def absent(box, monkeypatch, tmp_path, *flags):
    deliver(box, "alpha", "1923171201")
    monkeypatch.delenv("SESSIONMAIL_MHS_ORACLE", raising=False)
    monkeypatch.setattr(_mhs, "MHS_DIR", str(tmp_path / "gone"))
    return run("1", "--json", *flags)


def one_object(stdout: str) -> dict:
    lines = stdout.splitlines()
    assert len(lines) == 1, stdout
    assert not any(ln.startswith("TRUST:") for ln in lines)
    obj = json.loads(lines[0])
    assert lines[0] == json.dumps(obj, sort_keys=True, ensure_ascii=True)   # the verifier's form
    return obj


def test_verifier_absent_plain_is_one_unverified_object_at_3(box, monkeypatch, tmp_path):
    r = absent(box, monkeypatch, tmp_path)
    assert r.exit_code == 3, r.output
    assert one_object(r.stdout) == {"schema": 1, "rc": 3, "result": "unverified",
                                    "reason": "verifier-absent", "signature": None, "trust": None}
    assert "message verify:" in r.stderr             # the reason a human reads stays on stderr


def test_verifier_absent_under_trust_is_error_at_5_and_the_trust_line_rides_in_it(
        box, monkeypatch, tmp_path):
    r = absent(box, monkeypatch, tmp_path, "--trust")
    assert r.exit_code == 5, r.output
    obj = one_object(r.stdout)
    assert obj["rc"] == 5 and obj["signature"] is None and obj["result"] == "unverified"
    assert obj["trust"] == {"verdict": "ERROR", "leg": None, "reason": "verifier-absent",
                            "line": ABSENT_LINE, "agent": None}
    assert obj["reason"] == obj["trust"]["reason"]   # design 2: under --trust, the TRUST: token


def test_verifier_absent_without_json_is_unchanged(box, monkeypatch, tmp_path):
    deliver(box, "alpha", "1923171201")
    monkeypatch.delenv("SESSIONMAIL_MHS_ORACLE", raising=False)
    monkeypatch.setattr(_mhs, "MHS_DIR", str(tmp_path / "gone"))
    r = run("1", "--trust")
    assert r.exit_code == 5 and r.stdout == ABSENT_LINE + "\n"
    r = run("1")
    assert r.exit_code == 3 and r.stdout == ""


@needs_ssh
@pytest.mark.parametrize("flags,registry", [
    ((), "unblessed"),                       # plain, rc 2: unsigned
    (("--trust",), "absent"),                # the verifier's own ERROR, rc 5
    (("--trust",), "unblessed"),             # FAIL 1-signature, rc 1
])
def test_the_absent_object_has_the_real_verifiers_key_set(box, monkeypatch, tmp_path, rig, flags,
                                                          registry):
    """Design 1: the CLI's object is a second writer, so its keys are pinned to the
    verifier's error object, run for real on an unsigned mail. Top level and trust both."""
    for var in SCRUB:
        monkeypatch.delenv(var, raising=False)
    real = json.loads(rig.run(base("parity", "hi\n").encode(), "--json", *flags,
                              registry=registry).stdout)
    cli_obj = one_object(absent(box, monkeypatch, tmp_path, *flags).stdout)
    assert set(real) == set(cli_obj) == TOP
    assert real["signature"] is None and cli_obj["signature"] is None
    if "--trust" in flags:
        assert set(real["trust"]) == set(cli_obj["trust"]) == TRUST
        assert type(real["trust"]["line"]) is type(cli_obj["trust"]["line"]) is str
    else:
        assert real["trust"] is None and cli_obj["trust"] is None


# ── the MCP door, MEASURED (design 1: _mcp.py unchanged) ──

def tool():
    return {n: fn for n, fn, _ in _mcp.tools(cli, "message")}["message_verify"]


@needs_ssh
def test_mcp_rc_0_hands_back_the_bare_object_byte_for_byte(clean_env, rig):
    f = rig.d / "mcp-good.eml"
    f.write_bytes(rig.v2(wire("mcpgood")))
    direct = subprocess.run([str(VERIFIER), str(f), "--json"], capture_output=True, text=True,
                            env={k: v for k, v in os.environ.items() if k not in SCRUB})
    got = call(tool(), args=[str(f)], as_json=True)
    assert got == direct.stdout                      # the verifier's stdout, unchanged
    obj = json.loads(got)
    assert obj["rc"] == 0 and obj["signature"] is not None


@needs_ssh
@pytest.mark.parametrize("quiet", [False, True])
def test_mcp_nonzero_rc_is_a_tool_error_with_the_object_last(clean_env, rig, quiet):
    """MEASURED: a non-zero rc (here unsigned, 2) raises ToolError(`exit N: ` + stderr +
    "\\n" + stdout). The object is the LAST line. Under --quiet, stderr is empty, so the
    object shares its one line with the prefix: json.loads on the whole text fails."""
    from mcp.server.mcpserver.exceptions import ToolError
    f = rig.d / "mcp-unsigned.eml"
    f.write_bytes(base("mcpuns", "hi\n").encode())
    with pytest.raises(ToolError) as err:
        call(tool(), args=[str(f)], as_json=True, quiet=quiet)
    text = str(err.value)
    assert text.startswith("exit 2: ")
    last = text.splitlines()[-1]
    if quiet:
        assert len(text.splitlines()) == 1
        last = last.removeprefix("exit 2: ")
    else:
        assert "no X-Session-Signature header" in text.splitlines()[0]   # a per-leg line
    obj = json.loads(last)
    assert obj["rc"] == 2 and obj["result"] == "unsigned" and obj["signature"] is None
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


@needs_ssh
def test_mcp_unblessed_is_a_success_yet_arrives_as_a_tool_error(clean_env, rig, monkeypatch):
    """MEASURED: --trust UNBLESSED exits 4, which design 3.1 counts a success (signature set),
    but the MCP door sees a non-zero rc and raises. A gating consumer uses the CLI."""
    from mcp.server.mcpserver.exceptions import ToolError
    monkeypatch.setenv("SESSION_REGISTRY", str(rig.reg["unblessed"]))
    f = rig.d / "mcp-unblessed.eml"
    f.write_bytes(rig.v2(wire("mcpunbl")))
    with pytest.raises(ToolError) as err:
        call(tool(), args=[str(f)], as_json=True, trust=True)
    text = str(err.value)
    assert text.startswith("exit 4: ")
    obj = json.loads(text.splitlines()[-1])
    assert obj["trust"]["verdict"] == "UNBLESSED" and obj["signature"] is not None


def test_the_mcp_tool_takes_the_flag_as_as_json():
    """The tool's property is the click parameter's name. `as_json`, as `send` names it."""
    import inspect
    assert "as_json" in inspect.signature(tool()).parameters


# ── the funnel keeps its object on a TRUST: line it cannot parse (do-001 review N1) ──

@needs_ssh
def test_a_trust_line_with_a_bare_word_keeps_the_object_and_gates_nothing(rig, tmp_path):
    """No TRUST: line carries a bare word today. If one ever does, emit() must not raise
    inside the exit handler (that loses the object): it writes the object with
    trust.verdict null and signature null, so a gate on either fails closed. Pinned on a
    copy of the verifier whose UNBLESSED line gains a bare word, installed beside _sig.py."""
    mhs = tmp_path / "sessionmail" / "mhs"
    mhs.mkdir(parents=True)
    shutil.copy2(VERIFIER.parent.parent / "_sig.py", mhs.parent / "_sig.py")
    text = VERIFIER.read_text()
    good = 'verdict("TRUST: UNBLESSED reason=bless-absent", 4)'
    assert text.count(good) == 1
    patched = mhs / "session-mail-verify"
    patched.write_text(text.replace(good, 'verdict("TRUST: UNBLESSED reason=bless-absent oops", 4)'))
    patched.chmod(0o755)
    raw = rig.v2(wire("n1bare"))
    done = rig.run(raw, "--json", "--trust", registry="unblessed", verifier=patched)
    assert done.returncode == 4, done.stderr
    assert "Traceback" not in done.stderr
    obj = one_object(done.stdout)
    assert obj["signature"] is None
    assert obj["trust"] == {"verdict": None, "leg": None, "reason": None,
                            "line": "TRUST: UNBLESSED reason=bless-absent oops", "agent": "ann"}
    # the shipped verifier, same mail: the line parses and the signature is set
    shipped = json.loads(rig.run(raw, "--json", "--trust", registry="unblessed").stdout)
    assert shipped["trust"]["verdict"] == "UNBLESSED" and shipped["signature"] is not None
