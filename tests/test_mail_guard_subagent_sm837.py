"""SM-837: what a background subagent may do as its seat, decided once for
both mail-guard doors (Bash and MCP), beyond SM-446/SM-815's take-and-mark rows.

Two changes, both on the subagent path only (a payload that carries
`agent_id`), so a main thread's decision cannot move:

1. **The `--` bug.** `subagent_verb` scanned its words flat, but click ends
   option parsing at `--`: after it, `--peek`, `--list` and `--help` are
   positionals. So `message read 1 -- --peek` TOOK while the guard allowed
   it (measured at BEFORE, store SM-837 `notes/repro-001.md`), and so
   did `message -- read 1` (the group drops one leading `--`) and
   `message read 1 -- --help`. Now a word that would ALLOW (`--peek`,
   `--list`, `-h`, `--help`) counts only before the first `--`.
2. **More rows, deny direction only:** `memory init` (the whole verb),
   `memory run` (unless `--list`), `message subscription add|remove`,
   `pager rule add|remove` and `pager focus`. `pager status --stopped`
   measures as a read (it writes nothing) and stays allowed.

Every cell runs the hook as Claude Code does: the shim on stdin, the
decision on stdout. BEFORE is the master these were measured on.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import test_mail_guard_subagent as bash_suite
import test_mail_guard_subagent_mcp as mcp_suite
from test_mail_guard import guard
from test_mail_guard_subagent import SHIM, _base_shim, decision

ROOT = Path(__file__).resolve().parents[1]
#: The master SM-837 was built on (rca-001 measured every cited file there).
BEFORE = "d607ecf"


def bash(command: str, subagent: bool, shim: Path = SHIM):
    return bash_suite.run(command, subagent=subagent, shim=shim)


def mcp(tool: str, tool_input, subagent: bool, shim: Path = SHIM):
    return mcp_suite.run(tool, tool_input, subagent=subagent, shim=shim)


# ── the Bash door ─────────────────────────────────────────────────────────

#: (cell id, command): each DENIED inside a subagent, and each ALLOWED at BEFORE.
BASH_DENY = [
    # 1. the `--` bug: after `--` the allowing word is a positional
    ("read-dd-peek", "message read 1 -- --peek"),
    ("read-dd-peek-box", "message read somebox 2 -- --peek"),
    ("read-dd-help", "message read 1 -- --help"),
    ("read-dd-h", "message read 1 -- -h"),
    ("group-dd-read", "message -- read 1"),
    ("group-dd-read-peek-after", "message -- read 1 -- --peek"),
    ("group-dd-mark", "message -- mark 1 done"),
    ("group-dd-pager-mute", "pager -- mute"),
    ("group-dd-pager-listen", "pager -- listen --timeout 60"),
    ("group-dd-status-new", "pager -- status --new"),
    ("bash-c-dd-peek", "bash -c 'message read 1 -- --peek'"),
    # 2. memory init: the WHOLE verb, its print path included
    ("memory-init", "memory init somebox"),
    ("memory-init-labels", "memory init somebox --labels CHARTER,RULES"),
    ("memory-init-supersede", "memory init somebox --supersede"),
    ("memory-init-record", "memory init somebox --record PROJECT.md"),
    ("memory-init-bare", "memory init"),
    ("memory-init-dd-help", "memory init somebox -- --help"),
    ("memory-group-dd-init", "memory -- init somebox"),
    ("memory-init-path", "~/.local/bin/memory init somebox"),
    ("memory-init-after-and", "cd /scratch && memory init somebox"),
    ("memory-init-bash-c", "bash -c 'memory init somebox'"),
    ("memory-init-env", "env X=1 memory init somebox"),
    # memory run: runs a script-mail AS THE SEAT, unless --list (it runs nothing)
    ("memory-run", "memory run /BOOT/WAKE"),
    ("memory-run-only", "memory run /BOOT/WAKE --only 1"),
    ("memory-run-json", "memory run /BOOT/WAKE --json"),
    ("memory-run-dd-list", "memory run /BOOT/WAKE -- --list"),
    ("memory-group-dd-run", "memory -- run /BOOT/WAKE"),
    ("memory-run-assign", "SESSIONMAIL_MEMORY_RUN=1 memory run /BOOT/WAKE"),
    # seat state: subscriptions
    ("subscription-add", "message subscription add somebox"),
    ("subscription-add-seat", "message subscription add somebox --seat"),
    ("subscription-remove", "message subscription remove somebox"),
    ("subscription-dd-add", "message subscription -- add somebox"),
    ("group-dd-subscription-add", "message -- subscription add somebox"),
    # seat state: pager rules (writes only)
    ("rule-add", "pager rule add 'ride from:*@host.example.invalid'"),
    ("rule-add-seat", "pager rule add --seat 'ride from:*@host.example.invalid'"),
    ("rule-remove", "pager rule remove 'ride from:*@host.example.invalid'"),
    ("rule-dd-add", "pager rule -- add 'ride from:*@host.example.invalid'"),
    # seat state: focus, any mode
    ("focus-sleep", "pager focus sleep"),
    ("focus-off", "pager focus off"),
    ("focus-until", "pager focus sleep --until 12:00"),
    ("focus-fleet", "pager focus off --fleet"),
    ("focus-bare", "pager focus"),
]

#: (cell id, command): each ALLOWED inside a subagent, now and at BEFORE.
BASH_ALLOW = [
    # `--` controls: a real peek, a help before the cut
    ("read-peek-dd", "message read --peek -- 1"),
    ("read-peek-then-dd", "message read --peek 1 --"),
    ("read-help-dd", "message read --help -- 1"),
    ("group-dd-list", "message -- list --unread"),
    ("group-dd-bare", "message --"),
    ("group-dd-status", "pager -- status"),
    # memory: the read-only verbs
    ("memory-ls", "memory ls /"),
    ("memory-tree", "memory tree /"),
    ("memory-cat", "memory cat /BOOT"),
    ("memory-wake", "memory wake"),
    ("memory-run-list", "memory run /BOOT/WAKE --list"),
    ("memory-run-list-first", "memory run --list /BOOT/WAKE"),
    ("memory-init-help", "memory init --help"),
    ("memory-run-h", "memory run -h"),
    ("memory-bare", "memory"),
    # seat state: the reads
    ("subscription-list", "message subscription list"),
    ("subscription-bare", "message subscription"),
    ("subscription-add-help", "message subscription add --help"),
    ("rule-list", "pager rule list"),
    ("rule-bare", "pager rule"),
    ("focus-help", "pager focus --help"),
    ("status-stopped", "pager status --stopped"),     # a read: it writes nothing
    # the words as data, never a command
    ("memory-quoted-arg", "grep 'memory init' notes.md"),
    ("memory-echo", "echo memory run /BOOT/WAKE"),
    ("memory-heredoc",
     "message send someone -s 'a subject' -f - <<'EOF'\n"
     "memory init somebox\nmemory run /BOOT/WAKE\npager focus sleep\nEOF"),
]

#: Allowed at BEFORE, denied now: the cells that are failing-first.
CHANGED_BASH = {c for c, _ in BASH_DENY}


@pytest.mark.parametrize("cell,command", BASH_DENY, ids=[c for c, _ in BASH_DENY])
def test_bash_subagent_is_denied(cell, command):
    assert decision(bash(command, subagent=True)) == "deny", f"{cell}: {command!r} was allowed"


@pytest.mark.parametrize("cell,command", BASH_ALLOW, ids=[c for c, _ in BASH_ALLOW])
def test_bash_subagent_is_allowed(cell, command):
    proc = bash(command, subagent=True)
    assert decision(proc) == "allow", f"{cell}: {command!r} was denied: {proc.stdout}"


# ── the MCP door ──────────────────────────────────────────────────────────

#: (cell id, tool, tool_input): each DENIED inside a subagent.
MCP_DENY = [
    # 1. the `--` bug: `args` are passed through, so `--` cuts here too
    ("read-dd-peek", "message_read", {"args": ["1", "--", "--peek"]}),
    ("read-dd-help", "message_read", {"args": ["1", "--", "--help"]}),
    ("read-dd-h", "message_read", {"args": ["1", "--", "-h"]}),
    # 2. memory_init: the whole tool
    ("memory-init", "memory_init", {"box": "somebox"}),
    ("memory-init-empty", "memory_init", {}),
    ("memory-init-supersede", "memory_init", {"box": "somebox", "supersede": True}),
    ("memory-init-labels", "memory_init", {"box": "somebox", "labels": "CHARTER"}),
    # seat state (`IDENTITY_WRITES`, `_mcp.py`): the three MCP writers
    ("subscription-add", "message_subscription_add", {"box_": "somebox"}),
    ("subscription-add-seat", "message_subscription_add", {"box_": "somebox", "seat": True}),
    ("subscription-remove", "message_subscription_remove", {"box_": "somebox"}),
    ("focus-sleep", "pager_focus", {"mode": "sleep"}),
    ("focus-off", "pager_focus", {"mode": "off"}),
    ("focus-empty", "pager_focus", {}),
]

#: (cell id, tool, tool_input): each ALLOWED inside a subagent.
MCP_ALLOW = [
    # a real peek: the server runs `read --peek 1 --` (`_argv` puts options
    # FIRST), so the cut must not deny it (rca-001 test_B)
    ("read-peek-args-dd", "message_read", {"args": ["1", "--"], "peek": True}),
    ("read-peek-args-dd-peek", "message_read", {"args": ["1", "--", "--peek"], "peek": True}),
    ("memory-ls", "memory_ls", {"path": "/"}),
    ("memory-tree", "memory_tree", {"path": "/"}),
    ("memory-cat", "memory_cat", {"path": "/BOOT"}),
    ("memory-wake", "memory_wake", {}),
    ("subscription-list", "message_subscription_list", {}),
    ("box-list", "message_box_list", {}),
    ("pager-rule", "pager_rule", {}),
    ("status-stopped", "pager_status", {"stopped": True}),    # a read
    ("why", "pager_why", {"message_id": "<1.2.3@host.example.invalid>"}),
    ("verify", "message_verify", {"args": ["1"]}),
]

#: Tools whose subagent decision moves from allow to deny in this change.
CHANGED_TOOLS = {"memory_init", "message_subscription_add", "message_subscription_remove",
                 "pager_focus"}


@pytest.mark.parametrize("cell,tool,tool_input", MCP_DENY, ids=[c for c, *_ in MCP_DENY])
def test_mcp_subagent_is_denied(cell, tool, tool_input):
    proc = mcp(tool, tool_input, subagent=True)
    assert decision(proc) == "deny", f"{cell}: {tool} {tool_input!r} was allowed"


@pytest.mark.parametrize("cell,tool,tool_input", MCP_ALLOW, ids=[c for c, *_ in MCP_ALLOW])
def test_mcp_subagent_is_allowed(cell, tool, tool_input):
    proc = mcp(tool, tool_input, subagent=True)
    assert decision(proc) == "allow", f"{cell}: {tool} {tool_input!r} was denied: {proc.stdout}"


def test_memory_init_reaches_the_hook():
    """A deny the matcher never routes is no deny: hooks.json must send
    `memory_init` to the guard, and ONLY it of memory's tools (the narrow
    form), so a main thread's `memory_ls|cat|tree|wake` spawns nothing new."""
    pattern = mcp_suite._matcher()
    for name in mcp_suite.ALL_SERVED:
        tool = name.rsplit("__", 1)[1]
        want = not tool.startswith("memory_") or tool == "memory_init"
        assert bool(re.fullmatch(pattern, name)) is want, name


def test_the_noun_tools_are_the_servers():
    """hook.py is stdlib-only and cannot import `_mcp`, so it carries its
    own copy of the noun tools it splits (`message_subscription_add` is
    `subscription add`, as `_argv` runs it). This pins the copy."""
    from sessionmail import _mcp
    mine = {(server, noun) for server, nouns in guard.SUBAGENT_MCP_NOUNS.items() for noun in nouns}
    assert mine == set(_mcp.NOUN_TOOLS)


@pytest.mark.parametrize("tool,words", [
    ("message_subscription_add", ["subscription", "add"]),
    ("message_subscription_remove", ["subscription", "remove"]),
    ("message_subscription_list", ["subscription", "list"]),
    ("message_box_list", ["box", "list"]),
    ("message_read", ["read"]),
    ("pager_rule", ["rule"]),
])
def test_mcp_call_rebuilds_a_noun_tool_as_the_cli_words(tool, words):
    assert guard.mcp_call(mcp_suite.full(tool), {}) == (tool.split("_", 1)[0], words)


def test_mcp_call_puts_the_flags_first_as_argv_does():
    """`_mcp._argv` runs `[verb, *options, *arguments]`; so does the rebuild."""
    assert guard.mcp_call(mcp_suite.full("message_read"), {"args": ["1", "--"], "peek": True}) \
        == ("message", ["read", "--peek", "1", "--"])


# ── the deny text: the old rows byte for byte, the new rows true ──────────

#: The new rows' `what`, each with the words its line must carry.
NEW_ROWS = {
    "memory init": ("memory", "`memory ls /`"),
    "memory run": ("script-mail", "`memory run PATH --list`"),
    "message subscription add": ("subscriptions", "`message subscription list`"),
    "message subscription remove": ("subscriptions", "`message subscription list`"),
    "pager rule add": ("pager rules", "`pager rule list`"),
    "pager rule remove": ("pager rules", "`pager rule list`"),
    "pager focus": ("focus", "`pager status`"),
}


@pytest.mark.parametrize("what", sorted(NEW_ROWS))
def test_a_new_rows_deny_says_what_it_would_do(what):
    """`take or mark the SEAT's mail` is false for these; each gets its own clause."""
    reason = guard.subagent_reason(what)
    clause, read_only = NEW_ROWS[what]
    assert f"`{what}` here" in reason and clause in reason and read_only in reason
    assert "take or mark" not in reason and "\n" not in reason


DENY_NAMES = [
    ("init", "memory init somebox", "memory init"),
    ("run", "memory run /BOOT/WAKE", "memory run"),
    ("sub-add", "message subscription add somebox", "message subscription add"),
    ("sub-remove", "message subscription remove somebox", "message subscription remove"),
    ("rule-add", "pager rule add x", "pager rule add"),
    ("rule-remove", "pager rule -- remove x", "pager rule remove"),
    ("focus", "pager focus sleep", "pager focus"),
    ("read-dd", "message read 1 -- --peek", "message read"),
    ("group-dd", "message -- mark 1 done", "message mark"),
    ("hook-dd", "pager hook -- batch", "pager hook batch"),
]


@pytest.mark.parametrize("cell,command,what", DENY_NAMES, ids=[c for c, *_ in DENY_NAMES])
def test_the_bash_deny_names_the_verb(cell, command, what):
    assert guard.decide_subagent(command) == guard.subagent_reason(what), cell


@pytest.mark.parametrize("tool,tool_input,what", [
    ("memory_init", {"box": "somebox"}, "memory init"),
    ("message_subscription_add", {"box_": "x"}, "message subscription add"),
    ("message_subscription_remove", {"box_": "x"}, "message subscription remove"),
    ("pager_focus", {"mode": "sleep"}, "pager focus"),
    ("message_read", {"args": ["1", "--", "--peek"]}, "message read"),
])
def test_the_mcp_deny_names_the_tool_and_the_bash_verb(tool, tool_input, what):
    reason = guard.decide_subagent_mcp(mcp_suite.full(tool), tool_input)
    assert reason == guard.subagent_reason(what, tool)
    assert f"the MCP tool `{tool}` (`{what}`)" in reason and "\n" not in reason


def test_the_mcp_read_only_way_names_tools():
    """Through the MCP door the read-only way is a tool, never a shell line."""
    assert "`memory_ls`" in guard.subagent_reason("memory init", "memory_init")
    assert "`message_subscription_list`" in guard.subagent_reason(
        "message subscription add", "message_subscription_add")
    assert "`pager_status`" in guard.subagent_reason("pager focus", "pager_focus")


# ── against BEFORE: the main thread byte-identical, the old subagent rows too ──

@pytest.fixture(scope="module")
def before_shim(tmp_path_factory):
    """BEFORE's hooks/, by the SM-446 suite's own helper (it reads its module's BASE)."""
    old = bash_suite.BASE
    bash_suite.BASE = BEFORE
    try:
        return _base_shim(tmp_path_factory)
    finally:
        bash_suite.BASE = old


ALL_BASH = bash_suite.ALL + bash_suite.MAIN_EXTRA + BASH_DENY + BASH_ALLOW
ALL_MCP = mcp_suite.DENY + mcp_suite.ALLOW + MCP_DENY + MCP_ALLOW


@pytest.mark.parametrize("cell,command", ALL_BASH, ids=[c for c, _ in ALL_BASH])
def test_bash_main_thread_is_byte_identical_to_before(before_shim, cell, command):
    """Every Bash cell, SM-446's and SM-837's, on the main thread."""
    now, then = bash(command, False), bash(command, False, before_shim)
    assert (now.returncode, now.stdout, now.stderr) == (then.returncode, then.stdout,
                                                        then.stderr), cell


@pytest.mark.parametrize("cell,tool,tool_input", ALL_MCP, ids=[c for c, *_ in ALL_MCP])
def test_mcp_main_thread_is_byte_identical_to_before(before_shim, cell, tool, tool_input):
    """The MCP door: every cell on the main thread."""
    now, then = mcp(tool, tool_input, False), mcp(tool, tool_input, False, before_shim)
    assert (now.returncode, now.stdout, now.stderr) == (then.returncode, then.stdout,
                                                        then.stderr), cell
    assert (now.returncode, now.stdout, now.stderr) == (0, "", "")


@pytest.mark.parametrize("name", mcp_suite.ALL_SERVED)
def test_every_served_tool_on_the_main_thread_is_byte_identical_to_before(before_shim, name):
    """`memory_init` now reaches the hook on a main thread: exit 0, silent."""
    now, then = mcp(name, {}, False), mcp(name, {}, False, before_shim)
    assert (now.returncode, now.stdout, now.stderr) == (then.returncode, then.stdout,
                                                        then.stderr) == (0, "", ""), name


OLD_BASH = [(c, cmd) for c, cmd in bash_suite.ALL if c not in CHANGED_BASH]
OLD_MCP = [(c, t, i) for c, t, i in mcp_suite.DENY + mcp_suite.ALLOW if t not in CHANGED_TOOLS]


@pytest.mark.parametrize("cell,command", OLD_BASH, ids=[c for c, _ in OLD_BASH])
def test_an_old_bash_subagent_cell_decides_as_before(before_shim, cell, command):
    """Deny direction only: every SM-446 cell is decided, and worded, as at BEFORE."""
    now, then = bash(command, True), bash(command, True, before_shim)
    assert (now.returncode, now.stdout) == (then.returncode, then.stdout), cell


@pytest.mark.parametrize("cell,tool,tool_input", OLD_MCP, ids=[c for c, *_ in OLD_MCP])
def test_an_old_mcp_subagent_cell_decides_as_before(before_shim, cell, tool, tool_input):
    """Deny direction only: every SM-815 cell not named by SM-837, as at BEFORE."""
    now, then = mcp(tool, tool_input, True), mcp(tool, tool_input, True, before_shim)
    assert (now.returncode, now.stdout) == (then.returncode, then.stdout), cell


@pytest.mark.parametrize("cell,command", BASH_ALLOW, ids=[c for c, _ in BASH_ALLOW])
def test_a_bash_control_was_allowed_before_too(before_shim, cell, command):
    """The controls are not new allows: BEFORE allowed each one as well."""
    assert decision(bash(command, True, before_shim)) == "allow", cell


@pytest.mark.parametrize("cell,command", BASH_DENY, ids=[c for c, _ in BASH_DENY])
def test_a_bash_deny_cell_was_allowed_before(before_shim, cell, command):
    """Each new deny is failing-first: BEFORE let it through."""
    assert decision(bash(command, True, before_shim)) == "allow", cell


@pytest.mark.parametrize("cell,tool,tool_input", MCP_DENY, ids=[c for c, *_ in MCP_DENY])
def test_an_mcp_deny_cell_was_allowed_before(before_shim, cell, tool, tool_input):
    assert decision(mcp(tool, tool_input, True, before_shim)) == "allow", cell
