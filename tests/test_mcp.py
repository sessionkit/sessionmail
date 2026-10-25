"""The MCP door: the click commands ARE the tools (`sessionmail._mcp`).

A stateless MCP server, light, stubs only: one thing working and tested
first, and the CLI and the MCP on the same underlying model, so the code is
not duplicated.

So these gates measure sameness, not features:

- **Same surface** - every verb on the page is a tool, the serving verb
  is not, and the tool's schema is the page's flags with the page's help
  on each property.
- **Same budget** - a tool description is the verb's page prose and fits
  the same 400 tokens.
- **Same stub** - calling an unbuilt tool returns the CLI's own NOT
  IMPLEMENTED line and requirement, as an MCP error, never a crash.
- **Stateless** - the http door answers `tools/list` to a bare POST with no
  initialize and no session id.
"""

from __future__ import annotations

import json

import anyio
import pytest
from click.testing import CliRunner
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from starlette.testclient import TestClient

from sessionmail import _mcp, memory, message, pager
from test_scaffold import TOKEN_BUDGET, count_tokens
from tool_call import call
from test_message_subscription import fleet  # noqa: F401  (a fixture: a synthetic host)

#: Three servers, one per command.
GROUPS = {"message": message.cli, "pager": pager.cli, "memory": memory.cli}
GROUP, NAME = message.cli, "message"


#: The nouns whose verbs are tools (message-subscriptions 3.5), written here
#: and not read from `_mcp`, so the gate cannot agree with whatever it tests.
NOUNS = {("message", "box"), ("message", "subscription")}

#: Verbs on the page that are NOT tools (SM-780), written here for the same
#: reason: `memory run` executes self-signed mail, so it is never a tool. A
#: noun's verb is named `<noun> <verb>`: `message box archive|unarchive` move
#: boxes and rewrite the postfix maps, a host owner's act (SM-790).
#: `message setup` makes the host's mail root, boxes and notmuch profile and
#: links its hooks: a host owner's act too.
NOT_TOOLS = {("memory", "run"), ("message", "box archive"), ("message", "box unarchive"),
             ("message", "setup")}


def page_tools(group, name) -> list[tuple[str, list[str]]]:
    """(tool name, argv path) for every verb on NAME's page, in the page's
    order: a noun's verbs stand in for the noun (message-subscriptions 3.5),
    so `message box` is `message_box_list` and `message_box_path`."""
    out = []
    for verb in group.list_commands(None):
        cmd = group.commands[verb]
        if verb == _mcp.SERVING_VERB or cmd.hidden:
            continue                    # a hidden verb is on no page, so no tool
        if (name, verb) in NOT_TOOLS:
            continue                    # on the page, never a tool (SM-780)
        if (name, verb) in NOUNS:
            out += [(f"{name}_{verb}_{sub}", [verb, sub]) for sub in cmd.list_commands(None)
                    if (name, f"{verb} {sub}") not in NOT_TOOLS]
        else:
            out.append((f"{name}_{verb}", [verb]))
    return out


PAGE_TOOLS = page_tools(GROUP, NAME)


def tool_index(group=GROUP, name=NAME) -> dict[str, tuple[object, str]]:
    return {n: (fn, desc) for n, fn, desc in _mcp.tools(group, name)}


@pytest.mark.parametrize("name", list(GROUPS))
def test_each_command_serves_its_own_verbs_and_only_those(name):
    group = GROUPS[name]
    assert list(tool_index(group, name)) == [t for t, _ in page_tools(group, name)]
    assert _mcp.SERVING_VERB in group.commands, f"{name} has no mcp verb"
    page = CliRunner().invoke(group, ["mcp", "-h"], prog_name=name).output
    assert page.startswith(f"Usage: {name} mcp ")


# ── same surface ─────────────────────────────────────────────────────────

def test_every_page_verb_is_a_tool_and_the_serving_verb_is_not():
    names = list(tool_index())
    assert names == [t for t, _ in PAGE_TOOLS], names
    assert f"{NAME}_{_mcp.SERVING_VERB}" not in names


def test_the_schema_is_the_pages_flags_with_the_pages_help():
    server = _mcp.server(GROUP, NAME)
    tool = server._tool_manager.get_tool(f"{NAME}_send")
    props = tool.parameters["properties"]
    assert set(tool.parameters["required"]) == {"to", "subject"}
    assert props["no_body"]["type"] == "boolean" and props["no_body"]["default"] is False
    assert props["cc"]["type"] == "array"
    # RULED, agents-send-door decision 5: `bcc` on the tool and `--bcc`
    # shown on the page. It was hidden, while
    # --cc's help said "(also --bcc)".
    assert props["bcc"]["type"] == "array", "on the page, so on the tool"
    assert "title" not in tool.parameters and not any("title" in p for p in props.values()), (
        "pydantic's titles are noise a model pays for (199 tokens per server, measured)"
    )
    # `send` is the ONE hand-written tool (agents-send-door): `body` stands
    # where the page has `-f FILE`, and the tool takes no path, no program to
    # run and no `--json` of its own. Every OTHER property is still the
    # page's flag with the page's help, word for word.
    send = GROUP.commands["send"]
    assert list(props) == [("body" if p.name == "body_file" else p.name) for p in send.params
                           if not getattr(p, "hidden", False) and
                           (p.name == "body_file" or p.name not in _mcp.NOT_ON_THE_TOOL)]
    assert props["body"]["description"] == _mcp.BODY_DESCRIPTION
    for param in send.params:
        if getattr(param, "hidden", False) or param.name in _mcp.NOT_ON_THE_TOOL:
            continue
        assert props[param.name]["description"], f"{param.name} has no description"
        if getattr(param, "help", None):
            assert props[param.name]["description"] == param.help


# ── the nouns: one tool per verb (message-subscriptions 3.5) ─────────────

NOUN_VERB_TOOLS = ["message_box_list", "message_box_path", "message_subscription_add",
                   "message_subscription_remove", "message_subscription_list"]


def test_a_noun_is_a_tool_per_verb_not_a_hollow_group():
    """Until 3.5, `message box` and `message subscription` were tools with
    no arguments: calling one could only print the group's page and exit 2.
    Each verb is now a tool of its own, where the noun stood, so every tool
    before them keeps its place (the order is the prompt cache's)."""
    names = list(tool_index())
    assert names[-len(NOUN_VERB_TOOLS):] == NOUN_VERB_TOOLS, names
    assert "message_box" not in names and "message_subscription" not in names
    assert names[:6] == ["message_send", "message_list", "message_tree", "message_read",
                         "message_mark", "message_path"]


def test_a_noun_verbs_schema_is_its_pages_flags_with_its_pages_help():
    server = _mcp.server(GROUP, NAME)
    add = GROUP.commands["subscription"].commands["add"]
    tool = server._tool_manager.get_tool("message_subscription_add")
    props = tool.parameters["properties"]
    assert list(props) == [p.name for p in add.params]
    assert tool.parameters["required"] == ["box_"]
    assert props["seat"]["type"] == "boolean" and props["seat"]["default"] is False
    for p in add.params:
        if getattr(p, "help", None):
            assert props[p.name]["description"] == p.help
    depth = server._tool_manager.get_tool("message_subscription_list").parameters["properties"]["depth"]
    assert depth["type"] == "integer" and depth["default"] == 4


def test_a_noun_verb_tool_runs_that_verb(tmp_path, monkeypatch):
    """The argv carries the noun: the tool runs `box list`, not `list`."""
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    # The seam, so the listing never reads this host's live sessions. The
    # verb runs in a CHILD (SM-926); the conftest carries this into it.
    monkeypatch.setattr(message, "_live_sessions", lambda: set())
    fn, _ = tool_index()["message_box_list"]
    assert call(fn).strip() == "-- 0 shown"
    fn, _ = tool_index()["message_box_path"]
    with pytest.raises(Exception) as caught:
        call(fn, box_="no-such-box-here")
    assert str(caught.value).startswith("exit 2: message box path: ")


def test_a_subscription_tool_is_refused_by_the_same_gate_as_the_page(fleet):
    """One door rule, both doors: a worker's `--seat` is refused by the tool
    with the page's own words, and nothing is written or rebuilt."""
    from sessionmail._mcp import tools
    fn = {n: f for n, f, _ in tools(GROUP, NAME)}["message_subscription_add"]
    with pytest.raises(Exception) as caught:
        call(fn, box_="atomnote", seat=True)
    said = str(caught.value)
    assert said.startswith("exit 2: ") and "request it by mail, from a privileged seat" in said
    # The sandbox's operator (conftest: privileged ivan and judy), never the
    # host's real file: the verb runs in a child, and `call` hands it the
    # suite's seams (SM-926 review r2; a bare child named the host's seats).
    assert "message send ivan " in said, said
    assert fleet.files() == [] and fleet.runs() == 0


@pytest.mark.parametrize("verb", ["add", "remove"])
def test_over_http_a_subscription_write_is_refused_before_it_runs(fleet, verb):
    """Over http the environment is the SERVER's, so `add` would write as
    whoever started it - and the D5 gate would ask the server's seat, not
    the caller's. The same ruling as message_send's: no
    identity, no write. `list` only reads, and still runs."""
    by_name = {n: f for n, f, _ in _mcp.tools(GROUP, NAME, "http")}
    with pytest.raises(Exception) as caught:
        call(by_name[f"message_subscription_{verb}"], box_="atomnote")
    said = str(caught.value)
    assert said.startswith("not done: identity: over http"), said
    assert f"{NAME} mcp" in said
    assert fleet.files() == [] and fleet.runs() == 0


def test_over_stdio_a_subscription_write_runs(fleet):
    fn = {n: f for n, f, _ in _mcp.tools(GROUP, NAME, "stdio")}["message_subscription_add"]
    out = call(fn, box_="atomnote")
    assert "atomnote" in out and fleet.runs() == 1


# ── same budget ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,argv", PAGE_TOOLS)
def test_a_tool_description_is_the_page_prose_within_budget(name, argv):
    _, description = tool_index()[name]
    cmd = GROUP
    for word in argv:
        cmd = cmd.commands[word]
    assert description.startswith((cmd.help or "").strip().splitlines()[0])
    assert "\b" not in description
    tokens, how = count_tokens(description)
    if tokens is None:  # no count is a skip, never an estimate (SM-799)
        pytest.skip(f"{name}: the {TOKEN_BUDGET}-token budget was not measured ({how})")
    assert tokens <= TOKEN_BUDGET, f"{name}: {tokens} tokens by {how}"


# ── same stub, over a real client session (in-memory transport) ──────────

#: Every shipped verb has a body since Sun 2030-06-09, so the gate keeps a
#: stub of its own: one command that names a real requirement. A FILE, so the
#: tool's child (SM-926) runs the same group the server was built from.
STUB_GROUP = '''
import click
from sessionmail._help import CONTEXT_SETTINGS, OrderedGroup
from sessionmail._stub import stub

@click.group(cls=OrderedGroup, context_settings=CONTEXT_SETTINGS)
def probe() -> None:
    """probe - a group with one unbuilt verb, for this gate"""

@probe.command(context_settings=CONTEXT_SETTINGS)
def hollow() -> None:
    """probe hollow - not built; names a requirement that exists"""
    stub("memory", "The root holds only labels")

if __name__ == "__main__":
    probe(prog_name="probe")
'''


def _stub_group(tmp_path, monkeypatch):
    import importlib.util
    import sys

    source = tmp_path / "stub_probe.py"
    source.write_text(STUB_GROUP)
    spec = importlib.util.spec_from_file_location("sm_stub_probe", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(_mcp.COMMANDS, "probe", [sys.executable, "-P", str(source)])
    return module.probe


def test_calling_a_stub_tool_returns_the_clis_own_stub_line_as_an_error(tmp_path, monkeypatch):
    group = _stub_group(tmp_path, monkeypatch)
    server = _mcp.server(group, "probe")
    cli_text = CliRunner().invoke(group, ["hollow"], prog_name="probe").stderr

    async def main():
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                listed = await session.list_tools()
                result = await session.call_tool("probe_hollow", {})
                return init, listed, result

    init, listed, result = anyio.run(main)
    page = CliRunner().invoke(group, ["-h"], prog_name="probe").output
    assert init.instructions == page, "instructions are the group page, verbatim"
    assert [t.name for t in listed.tools] == list(tool_index(group, "probe"))
    assert result.is_error is True
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "NOT IMPLEMENTED" in text and "requirement:" in text


# ── stateless http ───────────────────────────────────────────────────────

def test_the_http_door_is_stateless():
    app = _mcp.server(GROUP, NAME).streamable_http_app(stateless_http=True, json_response=True)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    # The SDK allows Host 127.0.0.1:*, localhost:*, [::1]:* only (DNS-rebinding guard); a bare host is refused 421.
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r = client.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 200, r.text
    assert "mcp-session-id" not in {k.lower() for k in r.headers}
    tools = json.loads(r.text)["result"]["tools"]
    assert [t["name"] for t in tools] == list(tool_index())


# ── the CLI never pays for the SDK it is not using ───────────────────────

@pytest.mark.parametrize("name", list(GROUPS))
def test_importing_a_command_does_not_import_the_mcp_sdk(name):
    """`pager status` is the ride-along after every tool call; `X --help`
    must work on a host without the SDK. Measured Sun 2030-06-09:
    a module-level import broke all three commands on the PATH."""
    import subprocess, sys
    code = (f"import sys, sessionmail.{name}; "
            "sys.exit(1 if {'mcp', 'pydantic', 'starlette', 'anyio'} & set(sys.modules) else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0, (
        f"importing sessionmail.{name} imported the SDK or its stack")


# ── http binds loopback only (SM-781) ────────────────────────────────────
#
# The server has no auth and every read verb runs as the serving seat, so a
# bind off loopback hands that seat's mail to anyone who can reach the port.
# Nothing here binds: `_mcp.server` is replaced by a fake that records `run`.

#: Off loopback, every one: the wildcards, empty, a LAN address, a public
#: one, and names - which are never resolved, whatever they would resolve to.
OFF_LOOPBACK = ["0.0.0.0", "::", "", "192.168.1.20", "10.0.0.5", "8.8.8.8",
                "fe80::1", "example.com", "hq", "localhost.example.com", "[::1]"]
LOOPBACK = ["127.0.0.1", "127.0.0.2", "::1", "localhost", "LocalHost"]


class _FakeServer:
    def __init__(self):
        self.runs: list[tuple[tuple, dict]] = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))


@pytest.fixture
def built(monkeypatch):
    """Every server `serve` builds, in order; none of them binds."""
    made: list[_FakeServer] = []

    def fake_server(group, group_name, transport="stdio"):
        made.append(_FakeServer())
        return made[-1]

    monkeypatch.setattr(_mcp, "server", fake_server)
    return made


def _serve(name, *argv):
    return CliRunner().invoke(GROUPS[name], ["mcp", *argv], prog_name=name)


@pytest.mark.parametrize("name", list(GROUPS))
@pytest.mark.parametrize("host", OFF_LOOPBACK)
def test_an_http_bind_off_loopback_is_refused_before_a_server_is_built(built, name, host):
    done = _serve(name, "--transport", "http", "--host", host, "--port", "8799")
    assert done.exit_code == _mcp.EX_OFF_LOOPBACK == 1, done.output
    assert built == [], "a server was built for a refused host"
    out = done.output
    assert f"{name} mcp: refused --host {host!r}" in out
    assert "loopback only" in out and "no auth" in out
    assert "ssh -L 8799:127.0.0.1:8799" in out, out


@pytest.mark.parametrize("name", list(GROUPS))
@pytest.mark.parametrize("host", LOOPBACK)
def test_an_http_bind_on_loopback_still_runs(built, name, host):
    done = _serve(name, "--transport", "http", "--host", host, "--port", "8799")
    assert done.exit_code == 0, done.output
    assert [srv.runs for srv in built] == [[(("streamable-http",), dict(
        host=host, port=8799, stateless_http=True, json_response=True,
        transport_security=_mcp.rebind_guard(host)))]]


@pytest.mark.parametrize("name", list(GROUPS))
def test_the_default_host_is_loopback_and_runs(built, name):
    done = _serve(name, "--transport", "http")
    assert done.exit_code == 0, done.output
    assert built[0].runs[0][1]["host"] == "127.0.0.1"


@pytest.mark.parametrize("name", list(GROUPS))
def test_stdio_is_untouched_by_the_host(built, name):
    """`--host` is http only; stdio binds nothing, so it is not checked."""
    done = _serve(name, "--host", "0.0.0.0")
    assert done.exit_code == 0, done.output
    assert built[0].runs == [(("stdio",), {})]


@pytest.mark.parametrize("name", list(GROUPS))
def test_the_help_says_loopback_only_and_gives_the_tunnel(name):
    page = CliRunner().invoke(GROUPS[name], ["mcp", "-h"], prog_name=name).output
    flat = " ".join(page.split())
    assert "for a client on another box" not in flat   # the off-box steer, gone
    assert "http: stateless, loopback only" in flat
    assert "http binds loopback only (no auth)" in flat
    assert "ssh -L 8790:127.0.0.1:8790" in flat
    assert "0.0.0.0" not in flat
    exit_line = next(line for line in page.splitlines() if "Exit:" in line)
    assert "1 --host not loopback" in exit_line and "2 usage" in exit_line, exit_line
    assert "1 usage" not in exit_line, exit_line


@pytest.mark.parametrize("name", list(GROUPS))
def test_the_pages_usage_exit_is_the_codes(name):
    """The Exit line says `2 usage`; click's own usage error is what says so."""
    done = CliRunner().invoke(GROUPS[name], ["mcp", "--no-such-flag"], prog_name=name)
    assert done.exit_code == 2, done.output


def test_memory_run_is_not_a_tool_over_http():
    """SM-780, pinned at the http door: the tool list an http client is
    handed has no memory_run (built as `serve` builds it; no socket)."""
    app = _mcp.server(memory.cli, "memory", "http").streamable_http_app(
        stateless_http=True, json_response=True)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        r = client.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 200, r.text
    names = [t["name"] for t in json.loads(r.text)["result"]["tools"]]
    assert names, "tools/list over http came back empty - the gate measured nothing"
    assert "memory_run" not in names, names


def _tools_list(host, host_header, origin=None):
    """POST tools/list with HOST_HEADER to memory's http app, guarded as
    `serve` guards a bind to HOST. No socket."""
    app = _mcp.server(memory.cli, "memory", "http").streamable_http_app(
        stateless_http=True, json_response=True, host=host,
        transport_security=_mcp.rebind_guard(host))
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream", "Host": host_header}
    if origin:
        headers["Origin"] = origin
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        return client.post("/mcp", headers=headers,
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})


@pytest.mark.parametrize("host", LOOPBACK + ["0:0:0:0:0:0:0:1"])
def test_every_loopback_bind_keeps_the_dns_rebinding_guard(host):
    """The SDK guards Host/Origin by itself only for `127.0.0.1`, `localhost`
    and `::1` spelled exactly; any other loopback spelling served any Host,
    so a rebinding web page could drive the read verbs (SM-781)."""
    assert _tools_list(host, "attacker.example:8790").status_code == 421
    assert _tools_list(host, "127.0.0.1:8790", origin="http://attacker.example").status_code == 403
    bound = f"[{host}]" if ":" in host else host
    assert _tools_list(host, f"{bound}:8790").status_code == 200
    assert _tools_list(host, "127.0.0.1:8790").status_code == 200
