"""SM-926: two tools/call that OVERLAP on one MCP server each get their OWN reply.

Root cause (SM-926 rca-001, measured at 7cbbcea): every sync tool ran its verb
in-process with `CliRunner().invoke` on an anyio worker thread, and
`CliRunner.isolation` swaps the process-global `sys.stdout`/`sys.stderr`. Two
invokes that overlap in time cross their swaps: one reply comes back blank, the
other carries both verbs' output, and a dead capture is left installed.

The fix is D10 extended from `message_send` to every tool: each call runs its
verb in a CHILD PROCESS, through the one runner send already uses (D09: one
underlying model, both doors). These gates hold it:

- the barrier shape: two verbs of a probe group that cannot start writing until
  BOTH have started, so they overlap every time. Deterministic: before the fix
  it failed on every run.
- the stdio shape, as repro-001's `drive.py` measured it: the real `message mcp`
  server, `message_list` and `message_mark` written in ONE write, each reply
  compared with the reply the same call gets when it is awaited alone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

import tool_call
from sessionmail import _mcp

# ── the barrier shape: a probe group whose two verbs MUST overlap ────────────

#: A click group of its own, a FILE so the server's child can run it exactly
#: as the in-process door did: `alpha` and `beta` each say they started, wait
#: for the other (a file each, so threads and processes both see it), then
#: write their own name five times on stdout and once on stderr, with naps.
PROBE = '''
import os, sys, time
from pathlib import Path
import click

LINES = 5

@click.group()
def cli():
    """probe - two verbs that cannot run apart"""

def _speak(name, other):
    here = Path(os.environ["PROBE_DIR"])
    (here / name).write_text("")
    end = time.monotonic() + 10
    while not (here / other).exists() and time.monotonic() < end:
        time.sleep(0.005)
    for i in range(LINES):
        click.echo(f"{name}{i}")
        time.sleep(0.01)
    click.echo(f"{name} done", err=True)

@cli.command()
def alpha():
    """probe alpha - waits for beta, then says alpha"""
    _speak("alpha", "beta")

@cli.command()
def beta():
    """probe beta - waits for alpha, then says beta"""
    _speak("beta", "alpha")

@cli.command()
def fail():
    """probe fail - exits 3, a reason on stderr and a verdict on stdout"""
    click.echo("verdict: no")
    click.echo("reason: probe", err=True)
    raise SystemExit(3)

@cli.command()
def whoami():
    """probe whoami - what the child it runs in is"""
    import json
    print(json.dumps({"pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0),
                      "stdin": sys.stdin.read(), "safe_path": bool(sys.flags.safe_path),
                      "env": os.environ.get("PROBE_INHERITED")}))

@cli.command()
def sleepy():
    """probe sleepy - says its pid, then blocks, as a pager listen does"""
    (Path(os.environ["PROBE_DIR"]) / "sleepy.pid").write_text(str(os.getpid()))
    time.sleep(60)

@cli.command()
def crash():
    """probe crash - raises, as a verb with a bug does"""
    raise RuntimeError("probe boom")

if __name__ == "__main__":
    cli(prog_name="probe")
'''


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """The probe group, loaded in-process (its tools are built from it) and
    registered as the program the `probe` server's child runs."""
    source = tmp_path / "probe_cli.py"
    source.write_text(PROBE)
    spec = importlib.util.spec_from_file_location("sm926_probe_cli", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendezvous = tmp_path / "rendezvous"
    rendezvous.mkdir()
    monkeypatch.setenv("PROBE_DIR", str(rendezvous))
    # `raising=False`: the red run predates the table, and must fail on the
    # SWAP, not on a missing attribute.
    commands = {**getattr(_mcp, "COMMANDS", {}), "probe": [sys.executable, "-P", str(source)]}
    monkeypatch.setattr(_mcp, "COMMANDS", commands, raising=False)
    return module.cli


def _calls(group, name, calls):
    """Every (tool, args) in CALLS, started together on ONE server; their replies, in order."""
    server = _mcp.server(group, name)

    async def main():
        replies: list = [None] * len(calls)
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def one(k, tool, args):
                    replies[k] = await session.call_tool(tool, args)

                async with anyio.create_task_group() as tg:
                    for k, (tool, args) in enumerate(calls):
                        tg.start_soon(one, k, tool, args)
        return replies

    return anyio.run(main)


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def test_two_overlapping_calls_each_get_their_own_output(probe):
    alpha, beta = _calls(probe, "probe", [("probe_alpha", {}), ("probe_beta", {})])
    assert (alpha.is_error, _text(alpha)) == (False, "".join(f"alpha{i}\n" for i in range(5)))
    assert (beta.is_error, _text(beta)) == (False, "".join(f"beta{i}\n" for i in range(5)))


def test_overlapping_calls_leave_the_servers_own_streams_alone(probe):
    """The swap's other half: a crossed restore left a DEAD capture installed
    as `sys.stdout` for the rest of the server's life."""
    before = (sys.stdout, sys.stderr)
    _calls(probe, "probe", [("probe_alpha", {}), ("probe_beta", {})])
    assert (sys.stdout, sys.stderr) == before


def test_a_failing_verb_keeps_the_error_shape(probe):
    """Byte-identical to the in-process door: `exit N: ` + stderr, newline, stdout."""
    (failed,) = _calls(probe, "probe", [("probe_fail", {})])
    assert failed.is_error is True
    assert _text(failed).endswith("exit 3: reason: probe\nverdict: no")


def test_a_verb_that_crashes_is_an_error_that_says_so(probe):
    """In-process, the exception was re-raised. In a child it is a crash exit
    with its traceback: an honest error, never a silent pass."""
    (crashed,) = _calls(probe, "probe", [("probe_crash", {})])
    assert crashed.is_error is True
    text = _text(crashed)
    assert "exit 1: " in text and "RuntimeError: probe boom" in text


# ── the child: what the brief holds it to ──────────────────────────────────

def test_every_command_runs_its_own_module_under_this_interpreter_with_safe_path():
    """The child is the SAME code as the server (an editable checkout stays
    one), and `-P` keeps a `sessionmail/` in the agent's cwd out (C1)."""
    for name in ("message", "pager", "memory"):
        assert _mcp.COMMANDS[name] == [sys.executable, "-P", "-c", _mcp.CHILD_BOOT,
                                       f"sessionmail.{name}"]


#: One reply per group that click WRAPS at its help width: message's usage
#: error with no seat (wraps at 76, so red even with COLUMNS unset), and
#: pager's group page for a bare `hook` (wraps only below 80 columns). The
#: boot is shared, so these stand for every tool; each was red on f220705.
WRAPPING = [("message", "message_read", {"args": ["1"]}, ["read", "1"], "Usage: message read"),
            ("pager", "pager_hook", {}, ["hook"], "Usage: pager hook")]


@pytest.mark.parametrize("columns", [None, "40", "200"])
@pytest.mark.parametrize("group_name, tool_name, kwargs, argv, usage", WRAPPING,
                         ids=[case[1] for case in WRAPPING])
def test_a_usage_error_wraps_as_it_did_in_process_whatever_the_seats_columns(
        monkeypatch, columns, group_name, tool_name, kwargs, argv, usage):
    """CliRunner pinned click's help width at 80; a child left alone wraps at
    its terminal's, which COLUMNS moves. The reply is still the in-process
    one, byte for byte: the 7cbbcea mapping, applied to CliRunner's result."""
    from click.testing import CliRunner
    from mcp.server.mcpserver.exceptions import ToolError
    from tool_call import call

    group = importlib.import_module(f"sessionmail.{group_name}").cli
    if columns is None:
        monkeypatch.delenv("COLUMNS", raising=False)
    else:
        monkeypatch.setenv("COLUMNS", columns)
    for var in ("TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    then = CliRunner().invoke(group, argv, prog_name=group_name)
    assert then.exit_code == 2 and usage in then.output, then.output
    detail = "\n".join(s for s in (then.stderr.strip(), then.stdout.strip()) if s)
    tool = {n: fn for n, fn, _ in _mcp.tools(group, group_name)}[tool_name]
    with pytest.raises(ToolError) as now:
        call(tool, **kwargs)
    assert str(now.value) == f"exit 2: {detail}"


def test_the_child_reads_no_wire_runs_in_its_own_session_and_inherits_the_env(probe, monkeypatch):
    """stdin is /dev/null, never fd 0 (over stdio that is the JSON-RPC wire);
    its own session, so a kill takes the group; the server's environment,
    which over stdio is the caller's seat."""
    monkeypatch.setenv("PROBE_INHERITED", "the seat's")
    (who,) = _calls(probe, "probe", [("probe_whoami", {})])
    seen = json.loads(_text(who))
    assert seen["pid"] != os.getpid()
    assert seen["pgid"] == seen["pid"] == seen["sid"], seen
    assert seen["stdin"] == "" and seen["safe_path"] is True
    assert seen["env"] == "the seat's"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_clients_cancel_kills_a_blocking_tools_child_and_the_server_still_answers(probe, tmp_path):
    """No new time limit on a generic tool (a `pager_listen` blocks by
    design), so the cancel is what ends it: raw JSON-RPC over stdio, as
    test_mcp_send's gate for send does it."""
    source = tmp_path / "probe_cli.py"
    boot = ("import importlib.util, sys; from sessionmail import _mcp; "
            f"spec = importlib.util.spec_from_file_location('sm926_probe_cli', {str(source)!r}); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            f"_mcp.COMMANDS['probe'] = [sys.executable, '-P', {str(source)!r}]; "
            "_mcp.serve(m.cli, 'probe', 'stdio', '', 0)")
    proc = subprocess.Popen([sys.executable, "-c", boot], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    buf = bytearray()
    pidfile = Path(os.environ["PROBE_DIR"]) / "sleepy.pid"
    try:
        proc.stdin.write(_req(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "sm926", "version": "0"}}))
        _replies(proc, {1}, time.monotonic() + 15, buf)
        proc.stdin.write(_req(None, "notifications/initialized"))
        proc.stdin.write(_req(2, "tools/call", {"name": "probe_sleepy", "arguments": {}}))
        end = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert pidfile.exists(), "the child never started"
        pid = int(pidfile.read_text())
        proc.stdin.write(_req(None, "notifications/cancelled", {"requestId": 2, "reason": "gate"}))
        end = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < end:
            time.sleep(0.05)
        assert not _alive(pid), "the child outlived the cancel"
        proc.stdin.write(_req(3, "tools/list"))
        got = _replies(proc, {3}, time.monotonic() + 10, buf)
        assert 2 not in got, "a cancelled request is never answered"
    finally:
        proc.stdin.close()
        proc.kill()
        proc.wait(timeout=10)


# ── the stdio shape: the real `message mcp`, as repro-001 drove it ─────────

SID = "5a1d0000-0926-4000-8000-000000000001"
#: A Message-ID no box holds: `mark` answers with a refusal, the same text
#: every time, and writes nothing, so the control and the overlap compare.
ABSENT = "absent.0926@testhost.session-mail"
#: Each run is a FRESH server; the gate fails if ANY run swaps. The swap hit
#: about 95 runs in 100 at 7cbbcea, so five runs all clean was a ~1e-6 chance.
RUNS = 5


def _mail(root: Path) -> None:
    box = root / SID
    for sub in ("new", "cur", "tmp"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    for k, subject in enumerate(("overlap probe one", "overlap probe two", "overlap probe three")):
        (box / "new" / f"1907238896.M{k}P926Q0.testhost").write_text(
            f"From: alpha@testhost\nTo: {SID}@testhost\nSubject: {subject}\n"
            f"Date: Sun, 09 Jun 2030 12:34:5{k} +0000\n"
            f"Message-ID: <{k}.0926@testhost.session-mail>\n\nbody {k}\n")


def _req(i, method, params=None) -> bytes:
    d: dict = {"jsonrpc": "2.0", "method": method}
    if i is not None:
        d["id"] = i
    if params is not None:
        d["params"] = params
    return (json.dumps(d) + "\n").encode()


LIST = _req(3, "tools/call", {"name": "message_list", "arguments": {"box": SID}})
MARK = _req(4, "tools/call", {"name": "message_mark", "arguments": {"ids": [ABSENT], "mark_": "read"}})


def _replies(proc, want: set, deadline: float, buf: bytearray) -> dict:
    got: dict = {}
    while not want <= got.keys():
        while b"\n" in buf:
            line, _, rest = bytes(buf).partition(b"\n")
            buf[:] = rest
            if line.strip():
                d = json.loads(line)
                if "id" in d:
                    got[d["id"]] = d
        if want <= got.keys():
            break
        left = deadline - time.monotonic()
        assert left > 0, f"no reply to {want - got.keys()} in time"
        if select.select([proc.stdout], [], [], left)[0]:
            chunk = os.read(proc.stdout.fileno(), 65536)
            assert chunk, "the server closed its stdout"
            buf.extend(chunk)
    return got


def _serve(tmp_path, overlap: bool, monkeypatch) -> dict:
    """One fresh `message mcp`; LIST and MARK in one write (OVERLAP) or each awaited.
    The server and its children carry the suite's seams (`tool_call.server_argv`),
    so `mark` reads the sandbox's operator file, never this host's."""
    root = Path(os.environ["SESSION_MAIL_ROOT"])
    shutil.rmtree(root, ignore_errors=True)
    _mail(root)
    env = {**os.environ, "CLAUDE_CODE_SESSION_ID": SID}
    argv = (tool_call.server_argv("message", monkeypatch) if hasattr(_mcp, "run_child")
            else [sys.executable, "-P", "-m", "sessionmail.message", "mcp"])   # the red tree
    proc = subprocess.Popen(argv, cwd=tmp_path,
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    buf = bytearray()
    deadline = time.monotonic() + 30
    try:
        proc.stdin.write(_req(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "sm926", "version": "0"}}))
        _replies(proc, {1}, deadline, buf)
        proc.stdin.write(_req(None, "notifications/initialized"))
        if overlap:
            proc.stdin.write(LIST + MARK)
            got = _replies(proc, {3, 4}, deadline, buf)
        else:
            proc.stdin.write(LIST)
            got = _replies(proc, {3}, deadline, buf)
            proc.stdin.write(MARK)
            got |= _replies(proc, {4}, deadline, buf)
    finally:
        proc.stdin.close()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    out = {}
    for i in (3, 4):
        r = got[i].get("result") or {}
        out[i] = (bool(r.get("isError")), "".join(c.get("text", "") for c in r.get("content") or []))
    return out


def test_list_and_mark_written_together_answer_as_they_do_apart(tmp_path, monkeypatch):
    control = _serve(tmp_path, overlap=False, monkeypatch=monkeypatch)
    assert control[3][0] is False and "overlap probe one" in control[3][1], control
    assert control[4][0] is True and ABSENT in control[4][1], control
    for run in range(RUNS):
        assert _serve(tmp_path, overlap=True, monkeypatch=monkeypatch) == control, f"run {run} swapped"
