"""The agents' send door: `message_send`, the one hand-written MCP tool.

openspec/changes/agents-send-door. RED ON PURPOSE, written before the
handler exists (tasks 3, before 4 and 8). What each test ASSERTS rests only
on text that is RULED, ALREADY TRUE of the live `mail_send`, or MEASURED.
Not asserted anywhere here, because they are not ruled: that `from` leaves
the tool, the vocabulary of kinds, the two openers, `bcc`.

The child is a FAKE `message send`. The JSON it speaks to the tool is the
door's internal wire and lives in ONE place below (FAKE_CHILD): if the
read-back changes that object, this is the only thing that moves. The tool
finds its child through two module attributes and nothing in the
environment, so no variable can name the program a send runs:

    _mcp.SEND_COMMAND            the argv the child starts with
    _mcp.SEND_TIMEOUT_SECONDS    the one number (30)

Fixtures are `alpha` at `testhost`, never a seat (convention 9).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

import anyio
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from fakes import write_fake
from sessionmail import _mcp, memory, message, pager

GROUPS = {"message": message.cli, "pager": pager.cli, "memory": memory.cli}
THE_ID = "<1907238896.4242.7@testhost.session-mail>"
NOTICE = "session-mail-send: sent via smtp (postfix) → alpha@testhost"
TRILOGY = "a `backtick`, it's an apostrophe, $(date), ${HOME}, a \\ backslash\nand a second line\n"

FAKE_CHILD = """#!{python}
import json, os, stat, sys, time
args = sys.argv[1:]
out = os.environ["FAKE_CHILD_OUT"]
path = args[args.index("--body-file") + 1] if "--body-file" in args else None
import subprocess
mode = os.environ.get("FAKE_CHILD_MODE", "ok")
SLEEPER = [sys.executable, "-c", "import time; time.sleep(40)"]
grandchild = None
if mode == "wedged":      # what the limit is FOR: a stuck postmap, sendmail, renderer
    grandchild = subprocess.Popen(SLEEPER, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
elif mode == "held":      # something the child started still holds its stdout
    grandchild = subprocess.Popen(SLEEPER)
seen = {{"argv": args, "pid": os.getpid(), "body_file": path,
        "grandchild": grandchild.pid if grandchild else None,
        "mode": stat.S_IMODE(os.stat(path).st_mode) if path else None,
        "body_hex": open(path, "rb").read().hex() if path else None}}
with open(out, "w") as f:
    json.dump(seen, f)
if mode == "slow":
    time.sleep(60)
elif mode == "wedged":
    grandchild.wait()
elif mode == "silent":
    sys.exit(0)
elif mode == "crash":
    sys.stderr.write("Traceback (most recent call last):\\nRuntimeError: boom\\n")
    sys.exit(1)
elif mode == "refused":
    print(json.dumps({{"sent": "no", "kind": "subject", "notices": [],
                      "message": "refused - the subject is 72 characters; the limit is 69"}}))
    sys.exit(2)
else:
    print(json.dumps({{"sent": "yes", "id": "@ID@", "notices": ["@NOTICE@"]}}, ensure_ascii=False))
"""


@pytest.fixture
def child(tmp_path, monkeypatch):
    """The fake child, and what it saw. `child.seen` is None until it runs."""
    # `write_fake` formats {python} itself, so the two values go in by token.
    script = write_fake(tmp_path / "fake-message-send",
                        FAKE_CHILD.replace("@ID@", THE_ID).replace("@NOTICE@", NOTICE))
    out = tmp_path / "child.json"
    monkeypatch.setenv("FAKE_CHILD_OUT", str(out))
    monkeypatch.delenv("FAKE_CHILD_MODE", raising=False)
    monkeypatch.setattr(_mcp, "SEND_COMMAND", [sys.executable, str(script)], raising=False)

    class Child:
        command = [sys.executable, str(script)]

        @property
        def seen(self):
            return json.loads(out.read_text()) if out.exists() else None

        def mode(self, name):
            monkeypatch.setenv("FAKE_CHILD_MODE", name)

    return Child()


def call(args, *, transport="stdio", then_list=False):
    """One `message_send` over a real client session (in-memory transport)."""
    try:
        server = _mcp.server(message.cli, "message", transport=transport)
    except TypeError:
        pytest.fail("_mcp.server() does not take transport= yet (agents-send-door task 8)")

    async def main():
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("message_send", args)
                listed = await session.list_tools() if then_list else None
                return result, listed

    return anyio.run(main)


def texts(result) -> list[str]:
    return [getattr(c, "text", "") for c in result.content]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


SEND = {"to": ["alpha"], "subject": "the point", "body": TRILOGY}


# ── the body is text, and it arrives byte for byte (RULED Q01, Q02) ──────────

def test_the_quoting_trilogy_survives_byte_exact(child):
    result, _ = call(SEND)
    assert result.is_error is False, texts(result)
    assert bytes.fromhex(child.seen["body_hex"]) == TRILOGY.encode("utf-8")


def test_only_send_is_hand_written_and_it_keeps_its_name_and_place():
    import inspect
    handlers = {n: fn for n, fn, _ in _mcp.tools(message.cli, "message")}
    from test_mcp import PAGE_TOOLS      # a noun's verbs stand in for it (3.5)
    server = _mcp.server(message.cli, "message")
    listed = [t.name for t in server._tool_manager.list_tools()]
    assert listed == [t for t, _ in PAGE_TOOLS], "same names, same order: the prompt cache's"
    assert listed[0] == "message_send"
    hand_written = [n for n, fn in handlers.items() if inspect.iscoroutinefunction(fn)]
    assert hand_written == ["message_send"], "one hand-written tool, on purpose"
    assert _mcp.HAND_WRITTEN == {("message", "send")}
    props = server._tool_manager.get_tool("message_send").parameters["properties"]
    assert props["body"]["type"] == "string" and props["body"]["description"]
    assert "title" not in props["body"]
    # RULED, decision 1: no path parameter at all, since a path would need a
    # rule for which paths the tool may read. Files go by the command line.
    assert "body_file" not in props
    assert "message send -f" in props["body"]["description"]


# ── the body travels as a private file, never in argv (ALREADY TRUE) ─────────

def test_the_body_is_a_0600_file_and_no_byte_of_it_is_in_argv(child):
    call(SEND)
    seen = child.seen
    assert seen["mode"] == 0o600
    assert not any("backtick" in a or "apostrophe" in a for a in seen["argv"]), seen["argv"]
    assert seen["argv"].count("--body-file") == 1


@pytest.mark.parametrize("mode", ["ok", "refused", "silent", "crash", "slow"])
def test_the_body_file_is_gone_on_every_path(child, monkeypatch, mode):
    child.mode(mode)
    monkeypatch.setattr(_mcp, "SEND_TIMEOUT_SECONDS", 1, raising=False)
    call(SEND)
    assert child.seen is not None, "the child never ran"
    assert not os.path.exists(child.seen["body_file"])


# ── a fresh process, the environment unchanged (RULED D10) ───────────────────

def test_the_child_is_the_packages_own_interpreter_running_the_module():
    command = getattr(_mcp, "SEND_COMMAND", None)
    assert command == [sys.executable, "-P", "-m", "sessionmail.message", "send"], (
        "never whatever `message` is first on the PATH, and never the current directory")


def test_a_package_in_the_current_directory_is_never_the_one_that_sends(tmp_path, monkeypatch):
    """Code review C1, MEASURED: `python -m` puts the cwd first on the
    path, and an MCP server's cwd is whatever project the agent is working
    in. So a repository holding a `sessionmail/` package would have ITS code
    run on every send, with the seat's identity and its signing key. `-P`
    closes it. The REAL child runs here, from a directory holding a decoy."""
    from test_sender import FAKE_SENDMAIL, HOST
    decoy = tmp_path / "project" / "sessionmail"
    decoy.mkdir(parents=True)
    marker = tmp_path / "decoy-ran"
    (decoy / "__init__.py").write_text("")
    (decoy / "message.py").write_text(f"open({str(marker)!r}, 'w').write('shadowed')\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    wire = tmp_path / "wire"
    for var in ("SESSIONMAIL_SEND_ORACLE", "CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK",
                "CLAUDE_SESSION_PUB", "FAKE_SENDMAIL_RC", "PYTHONPATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(tmp_path / "argv"))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(tmp_path / "mail"))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.chdir(decoy.parent)
    result, _ = call({"to": ["beta"], "subject": "the point", "body": "words\n"})
    assert not marker.exists(), "the decoy in the current directory was imported and RUN"
    assert result.is_error is False and wire.read_bytes().endswith(b"\n\nwords\n"), texts(result)


def test_every_call_is_a_new_process(child):
    call(SEND)
    first = child.seen["pid"]
    call(SEND)
    assert child.seen["pid"] != first and first != os.getpid()


# ── the result: the id alone, then the notices; TEXT ONLY (RULED) ───────────

def test_block_one_is_the_message_id_alone_and_block_two_the_notices(child):
    result, _ = call(SEND)
    blocks = texts(result)
    assert result.is_error is False
    assert blocks[0] == THE_ID, "the two-phase receipt rule parses content[0].text"
    assert len(blocks) == 2 and NOTICE in blocks[1]
    assert getattr(result, "structured_content", None) is None


@pytest.mark.parametrize("name", list(GROUPS))
def test_no_tool_on_any_server_advertises_an_output_schema(name):
    """Review R4: true today only by accident (`_handler` overwrites
    `__signature__` and drops the `-> str`). An agent is handed a result's
    text OR its structured content, never both (MEASURED, Claude Code
    2.1.274), so one kept annotation turns every listing into JSON."""
    server = _mcp.server(GROUPS[name], name)

    async def main():
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.list_tools()

    for tool in anyio.run(main).tools:
        assert getattr(tool, "output_schema", None) is None, tool.name


# ── never "sent" unconfirmed, never "not sent" unconfirmed (ALREADY TRUE) ────

@pytest.mark.parametrize("mode", ["silent", "crash"])
def test_a_send_that_cannot_be_confirmed_is_an_error_that_says_unconfirmed(child, mode):
    """`silent` is HUGO's fourth: exit 0 and NOTHING. It must say so."""
    child.mode(mode)
    result, _ = call(SEND)
    text = "\n".join(texts(result))
    assert result.is_error is True
    assert "unconfirmed" in text.lower() and THE_ID not in text


def test_a_refusal_is_returned_not_raised_and_says_its_kind_first(child):
    """The kind rides in the TEXT (RULED); a raised ToolError gets the
    SDK's `Error executing tool ...` prefix and the kind is no longer first
    (MEASURED). The exact opener is decision 9, so not pinned here."""
    child.mode("refused")
    result, _ = call(SEND)
    first = texts(result)[0].splitlines()[0]
    assert result.is_error is True
    assert not first.startswith("Error executing tool"), first
    assert "subject" in first and "72 characters" in "\n".join(texts(result))
    assert "unconfirmed" not in first.lower(), "a refusal before the MTA is not unconfirmed"


# ── one time limit, and it kills (RULED Q04, D10) ────────────────────────────

def test_a_wedged_send_is_killed_at_the_limit_and_the_server_still_answers(child, monkeypatch):
    child.mode("slow")
    monkeypatch.setattr(_mcp, "SEND_TIMEOUT_SECONDS", 1, raising=False)
    began = time.monotonic()
    result, listed = call(SEND, then_list=True)
    assert time.monotonic() - began < 10
    assert result.is_error is True and "unconfirmed" in "\n".join(texts(result)).lower()
    assert child.seen is not None and not alive(child.seen["pid"]), "killed AND reaped"
    assert "message_list" in [t.name for t in listed.tools]


def test_the_limit_kills_what_was_actually_wedged_not_only_the_child(child, monkeypatch):
    """Code review C2, MEASURED: the limit guards a wedged postconf,
    postmap, sendmail or renderer, and those are GRANDCHILDREN. `kill()`
    reached only the child and left the stuck one behind - the one that may
    still deliver. After the limit, NO process from that send is alive."""
    child.mode("wedged")
    monkeypatch.setattr(_mcp, "SEND_TIMEOUT_SECONDS", 1, raising=False)
    result, _ = call(SEND)
    assert result.is_error is True and texts(result)[0].startswith("unconfirmed: timeout:")
    time.sleep(0.3)
    assert not alive(child.seen["pid"]) and not alive(child.seen["grandchild"])


def test_a_send_that_succeeded_is_never_reported_as_a_timeout(child, monkeypatch):
    """Code review C3, MEASURED: the scope waited for the two drains
    as well as the child, so anything still holding the pipe ran it to the
    limit, and an exit code the child had ALREADY given was thrown away: the
    receipt sat in the evidence of an error saying it may not have gone."""
    child.mode("held")
    monkeypatch.setattr(_mcp, "SEND_TIMEOUT_SECONDS", 8, raising=False)
    began = time.monotonic()
    result, _ = call(SEND)
    took = time.monotonic() - began
    os.kill(child.seen["grandchild"], 9)
    assert result.is_error is False and texts(result)[0] == THE_ID, texts(result)
    assert took < 5, f"a finished child is not waited on to the limit ({took:.1f} s)"


def test_an_empty_to_is_refused_by_the_tool_before_any_child(child):
    """Code review C4: the schema requires `to`, and an empty list
    satisfies it. Click refused it before the door ran, so it came back
    `unconfirmed: crashed` - and an agent was told to go looking for mail
    that was never sent."""
    result, _ = call({**SEND, "to": []})
    assert result.is_error is True and child.seen is None
    assert texts(result)[0].startswith("not sent: usage:")


def test_the_limit_is_thirty_seconds_and_one_number():
    assert getattr(_mcp, "SEND_TIMEOUT_SECONDS", None) == 30


def test_a_clients_cancel_kills_the_child_and_is_never_answered(child, tmp_path):
    """Raw JSON-RPC over stdio, as the review's probe did it: the in-memory client
    does not promise to send `notifications/cancelled`, and this gate is about
    what is on the wire. A cancelled request gets NO response - the protocol's
    behaviour, not a bug to fix."""
    boot = ("import sys; from sessionmail import _mcp, message; "
            f"_mcp.SEND_COMMAND = {child.command!r}; "
            "_mcp.serve(message.cli, 'message', 'stdio', '', 0)")
    env = {**os.environ, "FAKE_CHILD_MODE": "slow"}
    srv = subprocess.Popen([sys.executable, "-c", boot], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)

    def tx(obj):
        srv.stdin.write(json.dumps(obj) + "\n")
        srv.stdin.flush()

    def rx(want, seconds):
        import select
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([srv.stdout], [], [], 0.2)[0]:
                line = srv.stdout.readline()
                if line and json.loads(line).get("id") == want:
                    return json.loads(line)
        return None

    try:
        tx({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "gate", "version": "0"}}})
        assert rx(1, 10) is not None
        tx({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tx({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "message_send", "arguments": SEND}})
        end = time.monotonic() + 5
        while child.seen is None and time.monotonic() < end:
            time.sleep(0.05)
        assert child.seen is not None, "the child never started"
        pid = child.seen["pid"]
        tx({"jsonrpc": "2.0", "method": "notifications/cancelled",
            "params": {"requestId": 2, "reason": "gate"}})
        time.sleep(1.0)
        assert not alive(pid), "the child outlived the cancel"
        assert rx(2, 1) is None, "a cancelled request is never answered"
        tx({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert rx(3, 5) is not None, "the server stopped answering"
    finally:
        srv.stdin.close()
        srv.kill()
        srv.wait(timeout=10)


# ── stdio only, for the first cut (RULED) ────────────────────────────────────

def test_the_http_door_refuses_send_says_why_and_starts_no_child(child):
    result, _ = call(SEND, transport="http")
    text = "\n".join(texts(result))
    assert result.is_error is True
    assert child.seen is None, "over http the server's seat is not the caller's: no child may start"
    assert "stdio" in text and "unconfirmed" not in text.lower()


def test_send_stays_listed_on_the_http_door():
    server = _mcp.server(message.cli, "message", transport="http")
    assert "message_send" in [t.name for t in server._tool_manager.list_tools()]


# ── the module is the command, or the fresh process does NOTHING (Q03, 4th) ──

@pytest.mark.parametrize("name", list(GROUPS))
def test_running_the_module_prints_the_commands_own_page(name):
    """MEASURED Sun 2030-06-09: `python -m sessionmail.message send -h`
    printed nothing and exited 0 - no `__main__` entry - so the spawn line
    above would have been a silent no-op on every send."""
    def run(*argv):
        return subprocess.run([sys.executable, *argv, "-h"], capture_output=True, text=True, timeout=30)

    # The console script is `cli()` under the program's name. Compared process
    # to process, because click wraps a page to the terminal it finds and
    # CliRunner finds a different one (measured: 1027 bytes against 1003).
    script = run("-c", f"from sessionmail.{name} import cli; cli(prog_name={name!r})")
    done = run("-m", f"sessionmail.{name}")
    assert script.returncode == 0 and script.stdout.startswith(f"Usage: {name} ")
    assert done.returncode == 0 and done.stdout == script.stdout, (
        f"python -m sessionmail.{name} -h printed {len(done.stdout)} bytes, "
        f"the command prints {len(script.stdout)}")


# ── SM-538: `no_shorten`, the one mail's OFF switch, derived from the page ───

def test_no_shorten_is_a_boolean_on_the_tool_defaulting_off():
    server = _mcp.server(message.cli, "message")
    props = server._tool_manager.get_tool("message_send").parameters["properties"]
    assert props["no_shorten"]["type"] == "boolean" and props["no_shorten"]["default"] is False


def test_no_shorten_true_reaches_the_child_as_the_flag(child):
    result, _ = call({**SEND, "no_shorten": True})
    assert result.is_error is False, texts(result)
    assert "--no-shorten" in child.seen["argv"], child.seen["argv"]


def test_no_shorten_unset_or_false_passes_no_flag(child):
    for args in (SEND, {**SEND, "no_shorten": False}):
        call(args)
        assert "--no-shorten" not in child.seen["argv"], child.seen["argv"]


# ── SM-538 do-006: `-c` is a short form of --cc; the tool's schema is unchanged ──

def test_dash_c_adds_no_parameter_the_tool_keeps_cc():
    server = _mcp.server(message.cli, "message")
    props = server._tool_manager.get_tool("message_send").parameters["properties"]
    assert "cc" in props and "c" not in props, sorted(props)


def test_cc_reaches_the_child_as_the_long_flag(child):
    result, _ = call({**SEND, "cc": ["gamma"]})
    assert result.is_error is False, texts(result)
    argv = child.seen["argv"]
    assert argv[argv.index("--cc") + 1] == "gamma" and "-c" not in argv, argv


# ── SM-716 do-004: `message_id`, derived from the page; no `_mcp.py` edit ────
# Design: store projects/sessionmail/tasks/SM-716_feat_send-msgid/design.md @
# 8a4f26595, §6 (the MCP row was a PREDICTION, asserted here) and §10 do-004;
# README SC6: the tool lists `message_id` and answers SC4 with `not sent: ...`.

def test_message_id_is_an_optional_string_on_the_tool():
    server = _mcp.server(message.cli, "message")
    params = server._tool_manager.get_tool("message_send").parameters
    prop = params["properties"]["message_id"]
    kinds = {b.get("type") for b in prop.get("anyOf", [prop])}
    assert kinds == {"string", "null"} and prop.get("default") is None, prop
    assert "message_id" not in params.get("required", []), params.get("required")


def test_message_id_reaches_the_child_as_the_flag(child):
    result, _ = call({**SEND, "message_id": "<x.1@testhost>"})
    assert result.is_error is False, texts(result)
    argv = child.seen["argv"]
    assert argv[argv.index("--message-id") + 1] == "<x.1@testhost>", argv


def test_message_id_unset_or_null_passes_no_flag(child):
    for args in (SEND, {**SEND, "message_id": None}):
        call(args)
        assert "--message-id" not in child.seen["argv"], child.seen["argv"]


@pytest.fixture
def real_child(tmp_path, monkeypatch):
    """The REAL `message send` child, hermetic as the decoy test's: a fake
    sendmail recording the wire, a temp root, `alpha` at test_sender's HOST."""
    from test_sender import FAKE_SENDMAIL, HOST
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    wire = tmp_path / "wire"
    for var in ("SESSIONMAIL_SEND_ORACLE", "CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK",
                "CLAUDE_SESSION_PUB", "FAKE_SENDMAIL_RC", "PYTHONPATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(tmp_path / "argv"))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(tmp_path / "mail"))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")

    class Real:
        host = HOST
        wire = staticmethod(lambda: wire.read_bytes() if wire.exists() else None)

    return Real()


@pytest.mark.parametrize("given", ["x@other.example", "x@{host}.session-mail"],
                         ids=["other-host", "minted-suffix"])
def test_message_id_a_foreign_id_answers_not_sent_message_id(real_child, given):
    """SC6 / SC4 through the tool: the real child refuses, and the tool's
    first words are `not sent: message-id: ...`, naming the bare host it
    wants; nothing reached the MTA."""
    given = given.format(host=real_child.host)
    result, _ = call({**SEND, "to": ["beta"], "message_id": given})
    text = "\n".join(texts(result))
    assert result.is_error is True, text
    assert text.startswith("not sent: message-id: --message-id "), text
    assert f"use the bare host {real_child.host!r}" in text
    assert real_child.wire() is None, "the MTA was handed a mail"


def test_message_id_an_own_host_id_is_sent_under_it(real_child):
    """The positive control: through the tool, the chosen id is block one
    and the one Message-ID on the wire."""
    chosen = f"<t.1@{real_child.host}>"
    result, _ = call({**SEND, "to": ["beta"], "message_id": chosen})
    assert result.is_error is False, texts(result)
    assert texts(result)[0] == chosen
    head = real_child.wire().split(b"\n\n", 1)[0].split(b"\n")
    assert [l for l in head if l.lower().startswith(b"message-id:")] == [
        f"Message-ID: {chosen}".encode()]
