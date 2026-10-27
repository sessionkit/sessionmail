"""SM-815: the SM-446 subagent rule, through the MCP door.

The plugin's MCP servers (`.mcp.json`: `message mcp`, `pager mcp`) serve the
verbs as tools and run with the seat's environment, so a subagent's
`message_read` takes the mail AS THE SEAT just as its Bash `message read`
does. SM-815 measure-001 (Claude Code 2.1.301, store
`findings/measure-001.md`): a background subagent IS handed all 26 tools,
and its MCP PreToolUse carries `agent_id`; the main thread's never does.

hooks.json routes the message and pager servers' tools (not memory's) to the
same mail-guard. Inside a subagent it denies exactly what the Bash door
denies, judged by the SAME table (`subagent_verb`); a main thread's MCP call
is allowed at once, exit 0 and no output, as when the hook never ran.

Every cell runs the hook the way Claude Code does: the shim the plugin
registers, the payload on stdin in measure-001's shape, the decision on
stdout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_mail_guard import guard
from test_mail_guard_subagent import ALLOW as BASH_ALLOW
from test_mail_guard_subagent import DENY as BASH_DENY
from test_mail_guard_subagent import SHIM, _base_shim, decision
from test_mail_guard_subagent import payload as bash_payload

ROOT = Path(__file__).resolve().parents[1]
#: The source this fix was built on: the Bash door must decide as it did.
BASE = "1c4ee10"
#: The words the deny reason carried at BASE and no longer does (the shipped
#: text names no tracker id). Built from pieces so the line reads as data.
BASE_REF_DROPPED = " (S" "M-446)"

SESSION = "0000aaaa-1111-2222-3333-444455556666"
AGENT_ID = "a0123456789abcdef"
PREFIX = "mcp__plugin_sessionmail_"

#: The 26 tools measure-001 saw a main thread (and its subagent) handed,
#: in its order (store SM-815 findings/measure-001.md, "Main-thread MCP tool
#: names (26)"). `test_the_26_are_what_the_servers_serve` pins them to the code.
TOOLS_26 = {
    "memory": ["memory_cat", "memory_ls", "memory_tree", "memory_wake"],
    "message": ["message_box_list", "message_box_path", "message_list", "message_mark",
                "message_path", "message_read", "message_send", "message_subscription_add",
                "message_subscription_list", "message_subscription_remove", "message_tree",
                "message_verify"],
    "pager": ["pager_focus", "pager_hook", "pager_listen", "pager_mute", "pager_off",
              "pager_rule", "pager_status", "pager_stop", "pager_unmute", "pager_why"],
}
ALL_26 = [f"{PREFIX}{server}__{tool}" for server, tools in TOOLS_26.items() for tool in tools]

#: Tools the servers gained AFTER measure-001, so no live seat has been seen
#: handed them; each is placed here by hand. `memory_init` is SM-186's verb
#: (merged up into this branch by SM-815 do-002). SM-837 routes it, alone of
#: memory's tools, to the guard: a subagent may not mint a root.
ADDED_SINCE = {"memory": ["memory_init"]}
SERVED = {g: sorted(TOOLS_26.get(g, []) + ADDED_SINCE.get(g, []))
          for g in TOOLS_26.keys() | ADDED_SINCE.keys()}
ALL_SERVED = [f"{PREFIX}{server}__{tool}" for server, tools in SERVED.items() for tool in tools]


def full(tool: str) -> str:
    """`message_read` -> `mcp__plugin_sessionmail_message__message_read`."""
    return f"{PREFIX}{tool.split('_', 1)[0]}__{tool}"


def payload(tool: str, tool_input: dict | object, subagent: bool) -> str:
    """measure-001's PreToolUse (MCP) payload, KEY for key (findings/
    measure-001.md:102 main thread, :111 subagent), with placeholder values.
    MCP adds `mcp_server` (the Bash payload has none); a subagent's adds
    `agent_id` and `agent_type`; the session id is the PARENT's either way."""
    name = tool if tool.startswith("mcp__") else full(tool)
    p = {
        "session_id": SESSION,
        "transcript_path": f"/scratch/transcripts/{SESSION}.jsonl",
        "cwd": "/scratch/cwd",
        "prompt_id": "11112222-3333-4444-5555-666677778888",
        "permission_mode": "bypassPermissions",
    }
    if subagent:
        p.update({"agent_id": AGENT_ID, "agent_type": "general-purpose"})
    p.update({
        "effort": {"level": "medium"},
        "hook_event_name": "PreToolUse",
        "mcp_server": "plugin:sessionmail:" + name[len(PREFIX):].split("__", 1)[0],
        "tool_name": name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_test",
    })
    return json.dumps(p)


def run(tool: str, tool_input, subagent: bool, shim: Path = SHIM):
    return subprocess.run(["/bin/sh", str(shim)], input=payload(tool, tool_input, subagent),
                          capture_output=True, text=True, timeout=20)


# ── the table, through the MCP door: parity with the Bash door ───────────

#: (cell id, tool, tool_input): each one DENIED inside a subagent.
DENY = [
    ("read", "message_read", {}),
    ("read-id", "message_read", {"args": ["1"]}),
    ("read-box-id", "message_read", {"args": ["somebox", "2"]}),
    ("read-str-arg", "message_read", {"args": "1"}),
    ("read-peek-false", "message_read", {"args": ["1"], "peek": False}),
    ("read-peek-str-false", "message_read", {"args": ["1"], "peek": "false"}),
    ("read-peek-zero", "message_read", {"args": ["1"], "peek": 0}),
    ("read-peek-null", "message_read", {"args": ["1"], "peek": None}),
    ("read-thread", "message_read", {"args": ["1"], "thread": True}),
    ("mark", "message_mark", {"ids": ["1"], "mark_": "done"}),
    ("mark-empty", "message_mark", {}),
    ("status-new", "pager_status", {"new_only": True}),
    ("status-new-str", "pager_status", {"new_only": "yes"}),
    ("status-new-one", "pager_status", {"new_only": 1}),
    ("status-new-quiet", "pager_status", {"new_only": True, "quiet": True}),
    ("pager-stop", "pager_stop", {}),
    ("pager-stop-kill", "pager_stop", {"kill": True}),
    ("pager-off", "pager_off", {}),
    ("pager-mute", "pager_mute", {}),
    ("pager-unmute", "pager_unmute", {}),
    ("pager-listen", "pager_listen", {}),
    ("pager-listen-box", "pager_listen", {"box": "somebox", "quiet": True}),
    # SM-837: a subscription is the SEAT's state (`IDENTITY_WRITES`, `_mcp.py`)
    ("subscription-add", "message_subscription_add", {"box_": "somebox"}),
    ("subscription-remove", "message_subscription_remove", {"box_": "somebox"}),
    # SM-837: focus is the SEAT's pager state, any mode
    ("pager-focus", "pager_focus", {"mode": "sleep"}),
]

#: (cell id, tool, tool_input): each one ALLOWED inside a subagent.
ALLOW = [
    ("send", "message_send", {"to": ["someone"], "subject": "a subject", "body": "text"}),
    ("list", "message_list", {}),
    ("list-unread", "message_list", {"unread": True}),
    ("read-peek", "message_read", {"args": ["1"], "peek": True}),
    ("read-peek-str", "message_read", {"args": ["1"], "peek": "true"}),
    ("read-peek-str-upper", "message_read", {"args": ["1"], "peek": "TRUE"}),
    ("read-peek-one", "message_read", {"args": ["1"], "peek": 1}),
    ("read-peek-in-args", "message_read", {"args": ["1", "--peek"]}),
    ("read-help-in-args", "message_read", {"args": ["--help"]}),
    ("status", "pager_status", {}),
    ("status-all", "pager_status", {"show_all": True}),
    ("status-new-false", "pager_status", {"new_only": False}),
    ("status-new-str-false", "pager_status", {"new_only": "false"}),
    ("why", "pager_why", {"message_id": "<1.2.3@host.example.invalid>"}),
    # parity: a bare `pager hook` is allowed in Bash; the tool has NO params,
    # so `_argv` drops any key and only the bare group can ever run
    ("pager-hook", "pager_hook", {}),
    ("pager-hook-extra-key", "pager_hook", {"args": ["batch"]}),
    ("tree", "message_tree", {"id": "<1.2.3@host.example.invalid>"}),
    ("path", "message_path", {}),
    ("verify", "message_verify", {"args": ["1"]}),
    ("box-list", "message_box_list", {}),
    ("box-path", "message_box_path", {}),
    # SM-837 ruled these reads: `--stopped` writes nothing (pager.py
    # `status`), and the `pager_rule` tool takes no params, so it can only
    # print (its writes, `pager rule add|remove`, are Bash-only)
    ("status-stopped", "pager_status", {"stopped": True}),
    ("subscription-list", "message_subscription_list", {}),
    ("pager-rule", "pager_rule", {}),
    # SM-837: memory IS judged now; its read-only tools stay allowed
    ("memory-cat", "memory_cat", {"path": "x"}),
    ("memory-wake", "memory_wake", {}),
]


@pytest.mark.parametrize("cell,tool,tool_input", DENY, ids=[c for c, *_ in DENY])
def test_subagent_is_denied(cell, tool, tool_input):
    proc = run(tool, tool_input, subagent=True)
    assert decision(proc) == "deny", f"{cell}: {tool} {tool_input!r} was allowed in a subagent"


@pytest.mark.parametrize("cell,tool,tool_input", ALLOW, ids=[c for c, *_ in ALLOW])
def test_subagent_is_allowed(cell, tool, tool_input):
    proc = run(tool, tool_input, subagent=True)
    assert decision(proc) == "allow", f"{cell}: {tool} {tool_input!r} was denied: {proc.stdout}"


@pytest.mark.parametrize("cell,tool,tool_input", DENY + ALLOW, ids=[c for c, *_ in DENY + ALLOW])
def test_main_thread_mcp_is_exit_0_and_silent(cell, tool, tool_input):
    """No `agent_id`: exit 0, NO stdout, NO stderr - the same as today, when
    the Bash-only matcher never ran the hook on an MCP call at all."""
    proc = run(tool, tool_input, subagent=False)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", ""), cell


@pytest.mark.parametrize("name", ALL_SERVED)
def test_every_one_of_the_26_on_the_main_thread_is_exit_0_and_silent(name):
    proc = run(name, {}, subagent=False)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", ""), name


def test_the_main_thread_is_not_logged(tmp_path):
    """Allowed at once: nothing is read, nothing is written."""
    log = Path(os.environ["HOME"], ".session", "sessions", SESSION, "log", "mail", "pager.jsonl")
    before = log.read_text() if log.exists() else None
    for _, tool, tool_input in DENY:
        run(tool, tool_input, subagent=False)
    after = log.read_text() if log.exists() else None
    assert after == before


# ── one table, not two: each MCP denial IS a Bash denial ─────────────────

#: Each denied MCP cell's tool, and the Bash command the Bash door denies for it.
BASH_TWIN = {
    "message_read": "message read 1",
    "message_mark": "message mark 1 done",
    "pager_status": "pager status --new",
    "pager_stop": "pager stop",
    "pager_off": "pager off",
    "pager_mute": "pager mute",
    "pager_unmute": "pager unmute",
    "pager_listen": "pager listen",
    "message_subscription_add": "message subscription add somebox",
    "message_subscription_remove": "message subscription remove somebox",
    "pager_focus": "pager focus sleep",
}


def test_the_denied_tools_are_exactly_the_bash_doors_verbs():
    denied = {tool for _, tool, _ in DENY}
    assert denied == set(BASH_TWIN)
    assert {t.split("_", 1)[1] for t in denied if t.startswith("pager_")} \
        == set(guard.SUBAGENT_PAGER_VERBS) | {"status", "focus"}


@pytest.mark.parametrize("tool", sorted(BASH_TWIN))
def test_each_mcp_denial_names_what_the_bash_door_names(tool):
    """The `what` in the deny is the Bash door's, from the same table."""
    tool_input = {"new_only": True} if tool == "pager_status" else {}
    mcp = guard.decide_subagent_mcp(full(tool), tool_input)
    bash = guard.decide_subagent(BASH_TWIN[tool])
    what = re.search(r"`([^`]+)` here", bash).group(1)
    assert f"`{tool}` (`{what}`)" in mcp


def test_the_mcp_door_judges_through_the_bash_doors_table(monkeypatch):
    """No second list: swap the table and the MCP door follows it."""
    monkeypatch.setattr(guard, "subagent_verb", lambda base, rest: "x" if rest == ["list"] else None)
    assert guard.decide_subagent_mcp(full("message_list"), {})
    assert guard.decide_subagent_mcp(full("message_read"), {}) is None


# ── the deny text and the log row ─────────────────────────────────────────

def test_the_deny_is_one_line_naming_the_tool_sm815_and_the_read_only_way():
    out = json.loads(run("message_read", {"args": ["1"]}, subagent=True).stdout)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "\n" not in reason
    assert reason.startswith("mail-guard: ")
    assert "the seat would not be paged" in reason
    assert "the MCP tool `message_read`" in reason
    assert "`message_list`" in reason and "`message_read` with `peek: true`" in reason


def test_the_bash_deny_text_is_unchanged():
    """`tool` is optional: without it the line is SM-446's, byte for byte."""
    assert guard.subagent_reason("message read") == (
        "a subagent shares its seat's session, so `message read` here would take or mark the "
        "SEAT's mail or change its pager, and the seat would not be paged. "
        "Read-only instead: `message list`, `message read --peek N`; leave marking "
        "and the pager to the main thread.")


def test_the_deny_is_logged_like_any_other():
    """SM-518: an MCP deny gets the same row, naming the blocked tool."""
    run("pager_stop", {}, subagent=True)
    rows = Path(os.environ["HOME"], ".session", "sessions", SESSION,
                "log", "mail", "pager.jsonl").read_text().splitlines()
    row = json.loads(rows[-1])
    assert row["via"] == "pretooluse-deny" and row["sid"] == SESSION
    assert row["blocked_tool"] == full("pager_stop")
    assert row["tool_use_ids"] == ["toolu_test"]


# ── fails OPEN ────────────────────────────────────────────────────────────

def _raw(text: str):
    return subprocess.run(["/bin/sh", str(SHIM)], input=text,
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("tool_input", [None, [], "message_read", 1, ["peek"]],
                         ids=["null", "list", "string", "int", "list-of-str"])
def test_a_non_dict_tool_input_is_allowed(tool_input):
    assert decision(run("message_read", tool_input, subagent=True)) == "allow"


@pytest.mark.parametrize("name", [
    f"{PREFIX}message__message_nonsense",       # a verb the table does not know
    f"{PREFIX}message__message_",               # no verb
    f"{PREFIX}message__pager_stop",             # server and tool disagree
    f"{PREFIX}pager__message_read",
    f"{PREFIX}memory__memory_nonsense",         # a memory verb the table does not know
    f"{PREFIX}message_message_read",            # no `__`
    "mcp__plugin_othermail_message__message_read",   # another plugin
    "mcp__message__message_read",               # a non-plugin server
    "message_read",                             # bare
    "",
], ids=["unknown-verb", "no-verb", "server-mismatch-1", "server-mismatch-2", "memory-unknown-verb",
        "no-separator", "other-plugin", "non-plugin", "bare", "empty"])
def test_an_unknown_tool_is_allowed(name):
    proc = _raw(json.dumps({"tool_name": name, "tool_input": {}, "agent_id": AGENT_ID}))
    assert decision(proc) == "allow", name


@pytest.mark.parametrize("text", ["not json", "[1, 2]", "null", '"x"',
                                  json.dumps({"tool_name": 7, "agent_id": AGENT_ID}),
                                  json.dumps({"agent_id": AGENT_ID})],
                         ids=["bad-json", "list", "null", "string", "int-tool-name", "no-tool-name"])
def test_a_malformed_payload_is_allowed(text):
    assert decision(_raw(text)) == "allow"


def test_an_empty_agent_id_is_the_main_thread():
    """`agent_id` is truthy-tested, as the Bash door and SM-378 test it."""
    p = json.loads(payload("message_read", {}, subagent=False))
    p["agent_id"] = ""
    proc = _raw(json.dumps(p))
    assert (proc.returncode, proc.stdout) == (0, "")


def test_decide_subagent_mcp_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("a guard bug")
    for name in ("mcp_call", "subagent_verb", "subagent_reason"):
        with monkeypatch.context() as m:
            m.setattr(guard, name, boom)
            assert guard.decide_subagent_mcp(full("message_read"), {}) is None, name


# ── the flag reading matches the server's ─────────────────────────────────

def test_mcp_true_agrees_with_the_servers_validator():
    """A flag counts as set exactly when the server would read it as True.
    The server's bool parameters are pydantic `bool` (`_mcp._annotation`)."""
    pydantic = pytest.importorskip("pydantic")
    ta = pydantic.TypeAdapter(bool)
    for value in [True, False, 1, 0, 1.0, 0.0, 2, -1, "true", "True", "TRUE", "1", "yes",
                  "Y", "on", "t", "false", "0", "no", "off", "f", "n", "", "maybe",
                  "1.0", " true", None, [], {}]:
        try:
            server = ta.validate_python(value)
        except pydantic.ValidationError:
            server = False      # refused before it runs: no flag, and no call either
        assert guard._mcp_true(value) is server, value


def test_the_flag_names_are_the_servers_parameters():
    """`peek` and `new_only` are the tools' own parameter names, and their
    flags the CLI's (`_mcp._argv` rebuilds `--peek`, `--new` from them)."""
    import click

    from sessionmail import message, pager
    read = message.cli.commands["read"]
    status = pager.cli.commands["status"]
    flags = dict(guard.SUBAGENT_MCP_FLAGS)
    by_name = {p.name: p for p in read.params}
    assert flags["peek"] in by_name["peek"].opts
    by_name = {p.name: p for p in status.params}
    assert flags["new_only"] in by_name["new_only"].opts
    assert isinstance(next(p for p in read.params if p.name == "args"), click.Argument)
    assert guard.SUBAGENT_MCP_WORDS == {"message_read": "args"}


# ── the matcher: every served tool is routed, or deliberately not ─────────

def _matcher() -> str:
    spec = json.loads((ROOT / "hooks/hooks.json").read_text())
    (group,) = [g for g in spec["hooks"]["PreToolUse"] if g.get("matcher") != "Bash"]
    return group["matcher"]


@pytest.mark.parametrize("name", ALL_SERVED)
def test_every_one_of_the_26_is_matched_or_deliberately_not(name):
    """message and pager tools are routed to the guard, and of memory's only
    `memory_init`, the one memory tool a subagent is denied (SM-837). The
    rest of memory's are not: routing them would buy a python3 run per
    main-thread call and deny nothing. Anchored or not, the matcher says
    the same."""
    pattern = _matcher()
    want = not name.startswith(PREFIX + "memory__") or name == PREFIX + "memory__memory_init"
    assert bool(re.fullmatch(pattern, name)) is want, name
    assert bool(re.search(pattern, name)) is want, name


def test_the_26_are_what_the_servers_serve():
    """measure-001's list, plus ADDED_SINCE, is the code's: if a tool is
    added, this goes red and the matcher test above has to place it."""
    from sessionmail import _mcp, memory, message, pager
    served = {g: sorted(n for n, _, _ in _mcp.tools(cli, g))
              for g, cli in (("memory", memory.cli), ("message", message.cli), ("pager", pager.cli))}
    assert served == SERVED


def test_the_matcher_routes_no_other_plugins_tools():
    pattern = _matcher()
    for name in ["mcp__plugin_voicemode_voicemode__converse", "Bash", "Read",
                 "mcp__plugin_sessionmail_memory__memory_cat", "mcp__claude_ai_Claude_Docs__x"]:
        assert not re.search(pattern, name), name


# ── the Bash door decides as it did at BASE, for a subagent too ───────────

@pytest.fixture(scope="module")
def base_shim(tmp_path_factory):
    """BASE's hooks/, by the SM-446 suite's own helper (it reads its module's BASE)."""
    import test_mail_guard_subagent as t
    old = t.BASE
    t.BASE = BASE
    try:
        return _base_shim(tmp_path_factory)
    finally:
        t.BASE = old


@pytest.mark.parametrize("cell,command", BASH_DENY + BASH_ALLOW,
                         ids=[c for c, _ in BASH_DENY + BASH_ALLOW])
def test_bash_subagent_decision_is_byte_identical_to_base(base_shim, cell, command):
    """The refactor (one table, `subagent_verb`) changes no Bash decision.

    The one difference allowed is BASE_REF_DROPPED: the deny text at BASE named
    a tracker id that the shipped text no longer carries. Nothing else in the
    output may differ, byte for byte."""
    def go(shim):
        return subprocess.run(["/bin/sh", str(shim)], input=bash_payload(command, True),
                              capture_output=True, text=True, timeout=20)
    now, then = go(SHIM), go(base_shim)
    assert (now.returncode, now.stdout) == (then.returncode,
                                            then.stdout.replace(BASE_REF_DROPPED, "")), cell
