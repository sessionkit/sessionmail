"""One model, two doors: the click commands ARE the MCP tools.

A stateless MCP server, very lightweight: the CLI and the MCP reuse the
same underlying model as far as possible, so the code is never duplicated
and nothing drifts out of sync.

So nothing here declares a tool - BUT ONE. `message_send` is hand-written
(openspec/changes/agents-send-door: the point of the MCP tool is that a
body can carry backticks, so there is a body box): an agent has WORDS, not
a file, so it takes `body` where the page takes `-f FILE`. It still RUNS the
click command, in a fresh process, so the model is one; see `_send_handler`.

Every other tool is read off a click command:
its name, its arguments and flags (types, defaults, per-flag help) and
the prose of its authored page. The help page IS the tool description,
under the same 40 lines / 400 tokens the reader gets. A verb built in
click is built in MCP the same instant; a stub stays a stub on both
doors and says so the same way (exit 69 becomes an MCP tool error).

Two transports from one server: `stdio` (a client starts it, as the
Claude Code plugin does through `.mcp.json`) and `http`, the Streamable
HTTP transport of the 2025-06-18 spec run STATELESS - no session id, no
resumption store, every request self-contained. http has NO auth
and binds loopback only: a client on another box reaches it
through an ssh tunnel, never through an off-loopback bind.

The MCP SDK is imported LAZILY, inside the functions that serve. Every
command imports this module to gain its `mcp` verb, and `pager status` is
the ride-along after every tool call: it must not pay for pydantic,
starlette and anyio it will never use. Measured: a
module-level import broke `pager -h` on the PATH outright (the editable
tool venv had no `mcp`), and the CLI must answer --help without the SDK.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

import click
from click.testing import CliRunner

from . import __version__
from ._help import CONTEXT_SETTINGS, PageCommand, unwrapped

#: sysexits.h EX_UNAVAILABLE: what `_stub.stub` exits. Not imported from
#: there so this module has no opinion on how a stub is written.
EX_UNIMPLEMENTED = 69

#: The verb that serves the tools is not itself a tool.
SERVING_VERB = "mcp"


# ── the one hand-written tool: message_send ───────────────────────────────

#: What a send's child starts with: THIS interpreter running the package's
#: OWN module, never whatever `message` is first on the PATH, so the child is
#: always the code that is installed beside this server. Tests replace it with
#: a fake; nothing in the ENVIRONMENT can, so no variable names the program a
#: send runs.
#:
#: `-P` IS A SECURITY LINE, NOT A TIDY-UP (code review, C1, measured):
#: `python -m` puts the CURRENT DIRECTORY first on the path, and an MCP
#: server's cwd is whatever project the agent is working in. Without `-P`, any
#: repository holding a `sessionmail/` package gets ITS code run on every send,
#: with the seat's identity and its signing key. Python >= 3.11, which is this
#: package's floor. The console scripts are not affected; only `-m` is.
SEND_COMMAND = [sys.executable, "-P", "-m", "sessionmail.message", "send"]

#: Once the child has EXITED, how long its output may take to finish arriving.
#: Anything it started that still holds the pipe keeps the drains from seeing
#: end-of-file; without a bound, a send that SUCCEEDED ran to the limit and
#: was reported `unconfirmed: timeout` (code review, C3, measured).
DRAIN_GRACE_SECONDS = 1.0

#: ONE number: 30 seconds is the default, and it can be changed. It is not
#: waiting on a finger.
#: Not a wait for a person: an agent's key is software. It guards a dead
#: signing agent or a wedged postconf, postmap, sendmail or renderer.
SEND_TIMEOUT_SECONDS = 30
TIMEOUT_VAR = "SESSIONMAIL_SEND_TIMEOUT"

#: A receipt is a Message-ID or it is not a receipt: angle brackets, one `@`,
#: no white space. The empty string a silent child prints does not match.
MESSAGE_ID_RE = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")

#: The page's parameters the tool does NOT take, and why. `body_file`: a tool
#: that takes a path raises "what path the tool should be allowed to access"
#: (decision 1); words go through the tool, files through the command
#: line. `renderer`: an agent's tool call does not choose a program to run
#: (decision 7). `as_json`: the tool always passes it.
NOT_ON_THE_TOOL = {"body_file", "renderer", "as_json"}

BODY_DESCRIPTION = (
    "The whole message, markdown. It travels byte for byte: backticks, quotes "
    "and $( ) need no escaping. Say only what is new; the subject carries the "
    "point. A file goes by the command line instead: message send -f FILE")

UNCONFIRMED = "it may or may not have gone out; look before you resend"

SEND_PROSE = f"""From is your seat, from the environment; there is no `from`. A
reply goes out From the address its parent was mailed at. `supersedes`
REPLACES an earlier message (pass its id) and threads to it. An
`in_reply_to` or `supersedes` id nobody here has seen still sends, with a
notice; a malformed one is refused. Pass ids plain, `<id@host>`, never
HTML-escaped.

Returns the Message-ID alone as the first block, then any notices. An
error's first words say whether it was sent: `not sent: KIND: ...` (fix
that, send again) or `unconfirmed: KIND: ...` ({UNCONFIRMED}). If this call
is cancelled or times out at your client, the send is unconfirmed too."""


def send_limit(env: dict[str, str] | None = None) -> tuple[int, str | None]:
    """(seconds, a notice when the variable was set and ignored). Whole
    seconds; a bad value is ignored WITH a notice and thirty stands (decision
    4), the way $SESSIONMAIL_PARENT_CHECK does it."""
    raw = (os.environ if env is None else env).get(TIMEOUT_VAR, "").strip()
    if not raw:
        return SEND_TIMEOUT_SECONDS, None
    if raw.isdigit() and int(raw) > 0:
        return int(raw), None
    return SEND_TIMEOUT_SECONDS, (f"message send: ignoring ${TIMEOUT_VAR}={raw!r} - expected "
                                  f"whole seconds; the limit stays {SEND_TIMEOUT_SECONDS} s")


def read_outcome(code: int | None, stdout: str, stderr: str) -> tuple[list[str], bool]:
    """(the text blocks, is it an error) from what the child did. PURE.

    Sent ONLY when the child exits 0 AND its receipt is a Message-ID. A
    refusal says "not sent" ONLY when the child said so itself, with a kind;
    everything else - exit 0 and no receipt, a death, the limit (code None) -
    is UNCONFIRMED, because an agent told "nothing was sent" RE-SENDS, and
    duplicate mail on the handoff channel is real harm. The kind is the first
    words of the TEXT: on an error an agent is handed the text and nothing
    else (measured, Claude Code 2.1.274)."""
    try:
        said = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        said = None
    said = said if isinstance(said, dict) else {}
    notices = [str(n) for n in said.get("notices") or []]
    if code == 0 and said.get("sent") == "yes" and MESSAGE_ID_RE.match(str(said.get("id", ""))):
        return [said["id"], *(["\n".join(notices)] if notices else [])], False
    if code not in (None, 0) and said.get("sent") == "no" and said.get("kind"):
        return ["\n".join([f"not sent: {said['kind']}: {said.get('message', '')}", *notices])], True
    if code not in (None, 0) and said.get("sent") == "unknown" and said.get("kind"):
        return ["\n".join([f"unconfirmed: {said['kind']}: {said.get('message', '')} - "
                           f"{UNCONFIRMED}", *notices])], True
    if code is None:
        first = f"unconfirmed: timeout: no answer inside the limit, so the send was stopped - {UNCONFIRMED}"
    elif code == 0:
        first = f"unconfirmed: no-receipt: the send exited 0 and printed no Message-ID - {UNCONFIRMED}"
    else:
        first = f"unconfirmed: crashed: the send exited {code} without saying what happened - {UNCONFIRMED}"
    evidence = [part for part in (stdout.strip(), stderr.strip()) if part]
    return ["\n".join([first, *evidence])], True


def _send_handler(group: click.Group, group_name: str, cmd: click.Command, transport: str):
    """`message_send`: the page's flags, but `body` where the page has
    `-f FILE`. Each call is a FRESH PROCESS running the door (D10): an
    upgrade takes effect on the next send, a stuck send is killed without
    freezing `list` and `read`, one crash cannot take the tool down - and both
    doors are on one core by construction, every door rule inherited.

    `async`, and the child held through anyio, because a plain `def` runs in
    a worker thread that a client's cancel cannot interrupt (measured:
    its child ran on for its whole 6 s). Here the limit and the cancel are one
    mechanism: the scope ends, `finally` kills and reaps. A cancelled call is
    never answered - the protocol's behaviour, not a bug to fix."""

    async def run(**kwargs: Any):
        import anyio                                    # lazy: see the module note
        from mcp.types import CallToolResult, TextContent

        def result(blocks: list[str], is_error: bool):
            return CallToolResult(content=[TextContent(type="text", text=b) for b in blocks],
                                  is_error=is_error)

        if transport != "stdio":
            # Over http the environment is the SERVER's seat, so every
            # caller would send as whoever started it. No child is started.
            return result([f"not sent: identity: over {transport} this server cannot know who is "
                           f"calling, and will not send as its own seat. Use the stdio server "
                           f"(`{group_name} mcp`), started by your own client."], True)

        recipients = len(kwargs.get("to") or [])
        if not recipients:
            # The schema requires `to`, and an empty list satisfies it. Click
            # would refuse it before the door ran, with no object, and the
            # agent would be told `unconfirmed: crashed` about mail that was
            # never sent (code review, C4).
            return result(["not sent: usage: `to` is empty - name at least one address"], True)

        limit, limit_notice = send_limit()
        body = kwargs.pop("body", None)
        out: list[bytes] = []
        err: list[bytes] = []
        process = None
        body_file = None
        code: int | None = None

        async def drain(stream, into: list[bytes], done) -> None:
            async for chunk in stream:
                into.append(chunk)
            done.set()

        try:
            if body is not None:
                # 0600 from the instant it exists; no newline translation:
                # these bytes ARE the message. Never argv, which `ps` shows to
                # everyone. Made INSIDE the try, so a failed write cannot leak it.
                fd, body_file = tempfile.mkstemp(prefix="message-send-", suffix=".body")
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(body)
            tail = _argv(cmd, {**kwargs, "body_file": body_file, "as_json": True})[1:]
            argv = [*SEND_COMMAND, *tail[:len(tail) - recipients], "--", *tail[len(tail) - recipients:]]
            # Its OWN session, so the limit can kill the GROUP: what wedges is
            # a postmap, a sendmail, a renderer - a GRANDCHILD, which a kill of
            # the child alone orphans, and it is the one that may still deliver
            # (code review, C2).
            process = await anyio.open_process(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, start_new_session=True)
            try:
                with anyio.fail_after(limit):
                    async with anyio.create_task_group() as group_:
                        ends = [anyio.Event(), anyio.Event()]
                        group_.start_soon(drain, process.stdout, out, ends[0])
                        group_.start_soon(drain, process.stderr, err, ends[1])
                        code = await process.wait()
                        # The child has SPOKEN. Its output gets a moment to
                        # finish arriving, never the rest of the limit (C3).
                        with anyio.move_on_after(DRAIN_GRACE_SECONDS):
                            for end in ends:
                                await end.wait()
                        group_.cancel_scope.cancel()
            except TimeoutError:
                code = process.returncode       # None only if it never exited
        finally:
            # On EVERY path, a cancel included: kill the group, REAP, and only
            # then let go of the body. Shielded, or a cancel would skip it.
            with anyio.CancelScope(shield=True):
                if process is not None:
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            process.kill()
                    await process.wait()
                    await process.aclose()
                if body_file is not None:
                    Path(body_file).unlink(missing_ok=True)

        blocks, is_error = read_outcome(code, b"".join(out).decode("utf-8", "replace"),
                                        b"".join(err).decode("utf-8", "replace"))
        if limit_notice:
            blocks = [*blocks, limit_notice] if len(blocks) == 1 else [blocks[0], f"{blocks[1]}\n{limit_notice}"]
        return result(blocks, is_error)

    from pydantic import Field  # lazy: see module note

    signature = []
    annotations: dict[str, Any] = {}
    for p in _params(cmd):
        if p.name == "body_file":
            # `str` with a None default, as every optional string here is: the
            # schema says "string", not an anyOf a model has to read round.
            name, ann, default = "body", Annotated[str, Field(description=BODY_DESCRIPTION)], None
        elif p.name in NOT_ON_THE_TOOL:
            continue
        else:
            name, ann, default = p.name or "", Annotated[_annotation(p), Field(description=_describe(p))], _default(p)
        signature.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=ann))
        annotations[name] = ann
    run.__signature__ = inspect.Signature(signature)  # type: ignore[attr-defined]
    run.__annotations__ = annotations
    run.__name__ = f"{group_name}_{cmd.name}"
    run.__doc__ = send_description(cmd)
    return run


def send_description(cmd: click.Command) -> str:
    """The page's own first line (the sameness gate reads it), then prose that
    speaks the TOOL's words: the page's speaks shell (-f, stdout, Exit:)."""
    head = (cmd.help or "").strip().splitlines()[0] if cmd.help else cmd.name or ""
    return f"{head}\n\n{SEND_PROSE}"


#: (command, verb) pairs whose tool is hand-written. One, on purpose.
HAND_WRITTEN = {("message", "send")}

#: (command, noun) pairs whose VERBS are the tools, each named
#: `<command>_<noun>_<verb>` and standing where the noun stands on the page
#: (message-subscriptions 3.5). A noun as a tool of its own takes no argument
#: and can only print its page. Named here, not every sub-group: `pager
#: hook`'s verbs are the plugin's hooks, not an agent's tools.
NOUN_TOOLS = {("message", "box"), ("message", "subscription")}

#: (command, verb) pairs that are on the PAGE but are NOT tools, by name.
#: An explicit list, never `hidden=True`: a hidden verb leaves the CLI page
#: too, and the page must stay true about what the CLI does.
#:
#: `memory run` EXECUTES script-mail as the owner. An item runs only if its
#: signer passes `--trust` (a registry cert for the From agent), but over MCP
#: running code is still one tool call from a model reading untrusted mail,
#: and lifting the interim gate is a decision of its own, not a side effect. So it stays
#: off the tool surface; the CLI door is opt-in
#: as well (`_mhs.RUN_GATE_VAR`).
#:
#: Each pair carries the REASON its serving page gives, so a verb withheld
#: for another cause never inherits `memory run`'s sentence. A
#: noun's verb is named `<noun> <verb>`: `message box archive` and `unarchive`
#: move boxes and change the postfix maps, a host owner's act. `message setup`
#: makes this host's mail root, boxes and notmuch profile and links its hooks:
#: a host owner's act too, never one tool call from a model reading mail.
RUN_REASON = """It executes script-mail as you,
so it is a CLI act on purpose, never a tool call."""
BOX_MOVE_REASON = "A host owner's act."
SETUP_REASON = """It makes this host's mail root, boxes and notmuch
profile, and links its hooks: a host owner's act."""
NOT_A_TOOL = {
    ("memory", "run"): RUN_REASON,
    ("message", "box archive"): BOX_MOVE_REASON,
    ("message", "box unarchive"): BOX_MOVE_REASON,
    ("message", "setup"): SETUP_REASON,
}

#: Tools that WRITE as the caller. Over http the environment is the SERVER's
#: seat, so they would write as whoever started it, and D5's gate would ask
#: that seat, not the caller's: refused before anything runs, as
#: message_send is.
#: `pager_focus` (pager-filters 5.2) too: its gate asks whose seat is calling.
IDENTITY_WRITES = {"message_subscription_add", "message_subscription_remove", "pager_focus"}


# ── reading a click command as a tool ─────────────────────────────────────

def _long_opt(param: click.Option) -> str:
    """The `--flag` form the argv will carry (the page shows it; `-s` is a synonym)."""
    longs = [o for o in param.opts if o.startswith("--")]
    return longs[0] if longs else param.opts[0]


def _annotation(param: click.Parameter) -> Any:
    """A click parameter type as the Python annotation the schema is drawn from."""
    if isinstance(param, click.Option) and param.is_flag:
        base: Any = bool
    elif isinstance(param.type, click.Choice):
        base = Literal[tuple(param.type.choices)]  # type: ignore[valid-type]
    elif isinstance(param.type, click.types.IntParamType):
        base = int
    else:
        base = str
    many = param.nargs == -1 or bool(getattr(param, "multiple", False))
    if not many and _default(param) is None:
        return base | None  # an optional with no default: the schema says anyOf [T, null], so a client that sends null is not refused
    return list[base] if many else base  # type: ignore[valid-type]


def _describe(param: click.Parameter) -> str:
    """Per-argument guidance lives ON the property (skillbox `mcp`, the one rule)."""
    if isinstance(param, click.Option) and param.help:
        return param.help
    return param.metavar or param.name or ""


#: click >= 8.3 marks "no default given" with a sentinel, not None.
_UNSET = getattr(click.core, "UNSET", None)


def _default(param: click.Parameter) -> Any:
    if isinstance(param, click.Option) and param.is_flag:
        return False
    if param.required:
        return inspect.Parameter.empty
    if param.nargs == -1 or getattr(param, "multiple", False):
        return []
    if param.default is None or param.default is _UNSET:
        return None
    return param.default


def _params(cmd: click.Command) -> list[click.Parameter]:
    """The page's parameters: hidden ones are not on the page, so not on the tool."""
    return [p for p in cmd.params if not getattr(p, "hidden", False)]


def _description(cmd: click.Command) -> str:
    """The authored page's prose: first line of the docstring, then the epilog."""
    head = (cmd.help or "").strip().splitlines()[0] if cmd.help else cmd.name or ""
    body = (cmd.epilog or "").replace("\b\n", "").replace("\b", "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return f"{head}\n\n{body}" if body else head


def _argv(cmd: click.Command, kwargs: dict[str, Any]) -> list[str]:
    """Rebuild the command line the click command would have read."""
    options: list[str] = []
    arguments: list[str] = []
    for p in _params(cmd):
        value = kwargs.get(p.name or "")
        if isinstance(p, click.Argument):
            if value is None:
                continue
            arguments.extend(map(str, value)) if isinstance(value, list) else arguments.append(str(value))
        else:
            opt = _long_opt(p)
            if p.is_flag:
                if value:
                    options.append(opt)
            elif isinstance(value, list):
                for v in value:
                    options.extend([opt, str(v)])
            elif value is not None:
                options.extend([opt, str(value)])
    return [cmd.name or "", *options, *arguments]


def _handler(group: click.Group, group_name: str, cmd: click.Command,
             via: tuple[str, ...] = (), transport: str = "stdio"):
    """A function whose signature is the command's parameters and whose body
    runs the command. The SDK draws the input schema from the signature.
    VIA is the noun a verb sits under (`("box",)` for `box list`)."""
    name = "_".join([group_name, *via, cmd.name or ""])

    def run(**kwargs: Any) -> str:
        from mcp.server.mcpserver.exceptions import ToolError  # lazy: see module note

        if transport != "stdio" and name in IDENTITY_WRITES:
            raise ToolError(f"not done: identity: over {transport} this server cannot know who "
                            f"is calling, and will not write as its own seat. Use the stdio "
                            f"server (`{group_name} mcp`), started by your own client.")
        result = CliRunner().invoke(group, [*via, *_argv(cmd, kwargs)], prog_name=group_name)
        if result.exit_code == 0:
            return result.stdout
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        # Both streams: a verdict can sit on stdout while its reason is on
        # stderr (`message verify`: signer, session and the TRUST line on
        # stdout, "incorrect signature" on stderr), so either one alone
        # loses half of it.
        detail = "\n".join(s for s in (result.stderr.strip(), result.stdout.strip()) if s)
        if result.exit_code == EX_UNIMPLEMENTED:
            raise ToolError(detail)
        raise ToolError(f"exit {result.exit_code}: {detail}")

    from pydantic import Field  # lazy: see module note

    signature = []
    annotations: dict[str, Any] = {}
    for p in _params(cmd):
        ann = Annotated[_annotation(p), Field(description=_describe(p))]
        signature.append(
            inspect.Parameter(p.name or "", inspect.Parameter.KEYWORD_ONLY, default=_default(p), annotation=ann)
        )
        annotations[p.name or ""] = ann
    run.__signature__ = inspect.Signature(signature)  # type: ignore[attr-defined]
    run.__annotations__ = annotations
    run.__name__ = name
    run.__doc__ = _description(cmd)
    return run


def tools(group: click.Group, group_name: str, transport: str = "stdio") -> list[tuple[str, Any, str]]:
    """(tool name, handler, description) for every verb on GROUP's page, in
    the page's order: same names, same places, because the order of a tool
    list is the prompt cache's. `send` alone is hand-written; a noun in
    NOUN_TOOLS is its verbs, in its place."""
    out = []
    for verb, cmd in group.commands.items():
        if verb == SERVING_VERB or cmd.hidden:
            continue                    # a hidden verb is not on the page, so not a tool
        if (group_name, verb) in NOT_A_TOOL:
            continue                    # on the page, never a tool
        if (group_name, verb) in HAND_WRITTEN:
            out.append((f"{group_name}_{verb}", _send_handler(group, group_name, cmd, transport),
                        send_description(cmd)))
            continue
        if (group_name, verb) in NOUN_TOOLS and isinstance(cmd, click.Group):
            for sub, sub_cmd in cmd.commands.items():
                if (group_name, f"{verb} {sub}") in NOT_A_TOOL:
                    continue            # on the page, never a tool
                out.append((f"{group_name}_{verb}_{sub}",
                            _handler(group, group_name, sub_cmd, (verb,), transport),
                            _description(sub_cmd)))
            continue
        out.append((f"{group_name}_{verb}", _handler(group, group_name, cmd, (), transport),
                    _description(cmd)))
    return out


# ── the server ────────────────────────────────────────────────────────────

def server(group: click.Group, group_name: str, transport: str = "stdio"):
    """An MCPServer whose tools are GROUP's verbs and whose instructions are
    GROUP's own help page.

    EVERY tool registers with `structured_output=False`. An agent is handed a
    result's text OR its structured content, never both (measured,
    Claude Code 2.1.274), and no tool here advertised an output schema only by
    ACCIDENT: `_handler` overwrites `__signature__` and so drops the `-> str`.
    One tidy-up that kept the annotation, and every listing would reach agents
    as `{"result": "..."}`. Now it is on purpose."""
    from mcp.server.mcpserver import MCPServer

    page = CliRunner().invoke(group, ["-h"], prog_name=group_name).output
    srv = MCPServer(name=f"sessionmail-{group_name}", version=__version__, instructions=page)
    for name, fn, description in tools(group, group_name, transport):
        srv.tool(name=name, description=description, structured_output=False)(fn)
        _strip_titles(srv._tool_manager.get_tool(name).parameters)
    return srv


def _strip_titles(schema: dict[str, Any]) -> None:
    """pydantic adds a `title` to the schema and to every property ("To",
    "No Body"). A model reads none of it and pays for all of it: measured
    199 of message's 1609 tools/list tokens (12%)."""
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)


#: `mcp --transport http --host <not loopback>`: refused before anything is
#: built or bound. Not 2: click's own usage error is 2.
EX_OFF_LOOPBACK = 1


def loopback(host: str) -> bool:
    """True only for `localhost` or an IP literal on loopback (127.0.0.0/8,
    ::1). Any other name is NOT resolved: a name is whatever DNS says it is
    today, and the server has no auth to fall back on."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def rebind_guard(host: str):
    """The SDK's DNS-rebinding guard (Host and Origin checked), switched on for
    EVERY loopback host. The SDK turns it on by itself only when the host is
    spelled exactly `127.0.0.1`, `localhost` or `::1`. Bound to `127.0.0.2` or
    `LOCALHOST`, it served any Host, so a web page that rebinds its own name
    to loopback could drive the read verbs from a browser."""
    from mcp.server.transport_security import TransportSecuritySettings  # lazy

    names = ["127.0.0.1", "localhost", "[::1]"]
    bound = f"[{host}]" if ":" in host else host
    if bound not in names:
        names.append(bound)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{n}:*" for n in names],
        allowed_origins=[f"http://{n}:*" for n in names])


def serve(group: click.Group, group_name: str, transport: str, host: str, port: int) -> None:
    """Block, serving GROUP's verbs over TRANSPORT. `http` is stateless, and
    binds loopback only: the server has NO auth, and every read verb runs as
    this seat, so an off-box bind would hand out its mail. The check
    comes first, before the server is built, so nothing ever binds."""
    if transport != "stdio" and not loopback(host):
        click.echo(
            f"{group_name} {SERVING_VERB}: refused --host {host!r}: http binds loopback only "
            "(127.0.0.1, ::1, localhost); it has no auth. For a client on another box, "
            f"tunnel to this one: ssh -L {port}:127.0.0.1:{port} <this-host>", err=True)
        raise SystemExit(EX_OFF_LOOPBACK)
    srv = server(group, group_name, transport)
    if transport == "stdio":
        srv.run("stdio")
        return
    try:
        srv.run("streamable-http", host=host, port=port, stateless_http=True, json_response=True,
                transport_security=rebind_guard(host))
    except SystemExit as exc:  # uvicorn exits 3 on a startup failure; the page says 2
        if exc.code == 3:
            click.echo(f"{group_name} {SERVING_VERB}: could not bind {host}:{port}", err=True)
            raise SystemExit(2) from None
        raise




# ── the verb, declared once for the three commands ────────────────────────

def attach(group: click.Group, group_name: str) -> None:
    """Add the `mcp` verb to GROUP. One declaration for message, pager and
    memory (THREE servers, one per command, so someone can use just one);
    the authored page is the same words with the command's name
    (help/<command>/mcp.txt, 19 lines / 250 tokens each)."""
    nouns = ", ".join(sorted(n for g, n in NOUN_TOOLS if g == group_name))
    named = (f"""One tool per verb, named {group_name}_<verb>, or {group_name}_<noun>_<verb> under a
noun ({nouns}), read off the verb's own page: its flags are
the tool's arguments, its prose the description.""" if nouns else
             f"""One tool per verb, named {group_name}_<verb>, read off the verb's own
page: its flags are the tool's arguments, its prose the description.""")
    by_reason: dict[str, list[str]] = {}
    for (g, v), reason in sorted(NOT_A_TOOL.items()):
        if g == group_name:
            by_reason.setdefault(reason, []).append(v)
    for reason, verbs in by_reason.items():
        named += "\n" + f"""Not a tool: {", ".join(f"`{group_name} {v}`" for v in verbs)}. {reason}"""
    # The one-line summary names a withheld TOP-LEVEL verb only: a noun's
    # verb (`box archive`) is not one of "the verbs above" on that page.
    withheld = sorted(v for g, v in NOT_A_TOOL if g == group_name and " " not in v)

    @group.command(
        name=SERVING_VERB,
        cls=PageCommand,
        context_settings=CONTEXT_SETTINGS,
        usage="[--transport stdio|http] [--host H] [--port N]",
        short_help=("serve the verbs above as MCP tools; blocks" if not withheld else
                    f"serve the verbs above but {', '.join(withheld)} as MCP tools; blocks"),
        help=f"{group_name} mcp - serve {group_name}'s verbs as MCP tools; blocks until the client goes",
        epilog=unwrapped(
            f"""{named}
Nothing is declared twice, but for message_send, which takes `body`
where its page takes -f FILE (an agent has words, not a file).
A stub is a stub on both doors: the same NOT IMPLEMENTED line.
The server's instructions are `{group_name} --help`. http is
Streamable HTTP, run stateless (no session id).
http binds loopback only (no auth); from another box, tunnel:
ssh -L 8790:127.0.0.1:8790 <this-host>

Exit: 0 client closed · 1 --host not loopback · 2 usage or no bind"""
        ),
    )
    @click.option("--transport", type=click.Choice(["stdio", "http"]), default="stdio",
                  show_default=True,
                  help="stdio: the client starts and owns this process (the "
                       "plugin's .mcp.json does); http: stateless, loopback "
                       "only")
    @click.option("--host", metavar="H", default="127.0.0.1", show_default=True,
                  help="http only; loopback")
    @click.option("--port", metavar="N", type=int, default=8790, show_default=True,
                  help="http only")
    def mcp(transport: str, host: str, port: int) -> None:
        serve(group, group_name, transport, host, port)
