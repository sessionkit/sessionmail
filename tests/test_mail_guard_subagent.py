"""SM-446: inside a subagent, the guard refuses what would take or mark the
SEAT's mail, or change the seat's pager.

A subagent's Bash runs with its seat's environment and the same parent
process (measured on Claude Code 2.1.300), so `pager status --new`, `message
read` and `message mark` run there take or mark the mail AS THE SEAT, and the
seat's pager does not page (repro-001: rows `mon-cli-message-read` and
`cli-pager-status-new` RED at 8741348). The only discriminator is the hook
payload: a subagent's PreToolUse carries `agent_id`, the main thread's never
does (SM-626 on 2.1.289; SM-446 measure-001 on 2.1.300).

Every cell runs the hook the way Claude Code does: `hooks/mail-guard-hook`
(the shim the plugin registers), the payload on stdin in the measured shape,
the decision on stdout. MAIL_GUARD_HOOK points the suite at another copy of
the shim, which is how the red run against the pre-fix source is made:
`git archive 8741348 hooks | tar -x -C DIR`, then
`MAIL_GUARD_HOOK=DIR/hooks/mail-guard-hook pytest tests/test_mail_guard_subagent.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _export import in_checkout

ROOT = Path(__file__).resolve().parents[1]
SHIM = Path(os.environ.get("MAIL_GUARD_HOOK") or ROOT / "hooks" / "mail-guard-hook")
#: The source this fix was built on: main-thread decisions must equal its own.
BASE = "8741348"

SESSION = "0000aaaa-1111-2222-3333-444455556666"
AGENT_ID = "a0123456789abcdef"


def payload(command: str, subagent: bool) -> str:
    """measure-001's PreToolUse (Bash) payload, field for field, with
    placeholder values. A subagent's adds `agent_id` and `agent_type`; the
    session id is the PARENT's either way."""
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
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "a test cell"},
        "tool_use_id": "toolu_test",
    })
    return json.dumps(p)


def run(command: str, subagent: bool, shim: Path = SHIM):
    return subprocess.run(["/bin/sh", str(shim)], input=payload(command, subagent),
                          capture_output=True, text=True, timeout=20)


def decision(proc) -> str:
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow"
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out["permissionDecision"]


# ── the table: what a subagent may not run, in every shape a seat types ───

#: (cell id, command): each one DENIED inside a subagent.
DENY = [
    # the verbs, bare
    ("status-new", "pager status --new"),
    ("status-new-quiet", "pager status --quiet --new"),
    ("read", "message read 1"),
    ("read-id", "message read '<1.2.3@host.example.invalid>'"),
    ("read-box", "message read somebox 2"),
    ("mark-done", "message mark 1 done"),
    ("mark-seen", "message mark '<1.2.3@host.example.invalid>' seen"),
    # Q2: each pager verb that changes or shadows the seat's pager
    ("pager-stop", "pager stop"),
    ("pager-off", "pager off"),
    ("pager-mute", "pager mute"),
    ("pager-unmute", "pager unmute"),
    ("pager-listen", "pager listen --timeout 60"),
    ("pager-listen-monitor", "pager listen --monitor"),
    # `pager hook <event>` typed into Bash: no payload, so no agent_id
    ("pager-hook-batch", "pager hook batch"),
    ("pager-hook-batch-piped", "echo '{}' | pager hook batch"),
    ("pager-hook-stop", "pager hook stop"),
    ("pager-hook-start", "pager hook start"),
    # a full path
    ("path-home-message", "~/.local/bin/message read 1"),
    ("path-abs-pager", "/opt/tools/bin/pager status --new"),
    ("path-abs-message-mark", "/opt/tools/bin/message mark 1 read"),
    # an env / assignment prefix
    ("env-prefix", "env X=1 message read 1"),
    ("assign-prefix", "SESSIONMAIL_PAGER=on pager stop"),
    ("command-prefix", "command message mark 1 done"),
    # after an operator
    ("after-and", "cd /scratch && message read 1"),
    ("after-semicolon", "true; pager status --new"),
    ("after-pipe", "echo 1 | message read 1"),
    ("after-or", "false || pager mute"),
    ("newline", "message list --unread\nmessage read 1"),
    ("in-substitution", "echo $(message read 1)"),
    # inside a shell's -c script, one level down
    ("bash-c", "bash -c 'message read 1'"),
    ("sh-c-double", 'sh -c "pager status --new"'),
    ("bash-lc", "bash -lc 'cd /scratch && message mark 1 done'"),
    ("bash-login-c", "bash --login -c 'pager stop'"),
    # inside a compound statement: the reserved word opens a command position
    ("brace-group", "{ message read 1; }"),
    ("if-head", "if message read 1; then echo ok; fi"),
    ("if-then", "if true; then pager status --new; fi"),
    ("else", "if false; then true; else message mark 1 done; fi"),
    ("for-do", "for i in 1 2; do message read $i; done"),
    ("while-do", "while read -r i; do message mark $i seen; done < ids"),
    ("negated", "! pager stop"),
    ("bash-c-for", "bash -c 'for i in 1 2; do message read $i; done'"),
]

#: (cell id, command): each one ALLOWED inside a subagent (read-only, or not ours).
ALLOW = [
    ("list", "message list"),
    ("list-unread", "message list --unread"),
    ("read-peek", "message read 1 --peek"),
    ("read-peek-first", "message read --peek 1"),
    ("status", "pager status"),
    ("status-all", "pager status --all"),
    ("why", "pager why '<1.2.3@host.example.invalid>'"),
    ("send-file", "message send someone -s 'a subject' -f body.md"),
    ("help-read", "message read --help"),
    ("help-pager-stop", "pager stop -h"),
    ("bare-message", "message"),
    ("bare-pager", "pager"),
    ("bare-pager-hook", "pager hook"),
    ("tree", "message tree '<1.2.3@host.example.invalid>'"),
    ("unrelated", "ls -la && git status"),
    # the false positives the GO names: the words as DATA, never a command
    ("quoted-arg-grep", "grep 'message read' notes.md"),
    ("quoted-arg-echo", 'echo "run pager status --new later"'),
    ("comment", "ls  # then message read 1"),
    ("heredoc-body-send",
     "message send someone -s 'a subject' -f - <<'EOF'\n"
     "please message read 1 and then message mark 1 done\n"
     "pager stop\npager status --new\nEOF"),
    ("heredoc-body-file",
     "cat > body.md <<'EOF'\nmessage read 1\npager listen\nEOF\n"
     "message send someone -s 'a subject' -f body.md"),
    ("quoted-head", "'message' read 1"),        # a quoted head is data (documented)
    ("bash-c-peek", "bash -c 'message read --peek 1'"),
    ("bash-script-file", "bash ./message-read.sh"),
    # a reserved word is skipped only when it LEADS: these words are data
    ("for-list-is-data", "for w in message read; do echo $w; done"),
    ("quoted-keyword", "'do' message read 1"),
    ("for-do-peek", "for i in 1 2; do message read --peek $i; done"),
]

ALL = DENY + ALLOW


@pytest.mark.parametrize("cell,command", DENY, ids=[c for c, _ in DENY])
def test_subagent_is_denied(cell, command):
    proc = run(command, subagent=True)
    assert decision(proc) == "deny", f"{cell}: {command!r} was allowed in a subagent"


@pytest.mark.parametrize("cell,command", ALLOW, ids=[c for c, _ in ALLOW])
def test_subagent_is_allowed(cell, command):
    proc = run(command, subagent=True)
    assert decision(proc) == "allow", f"{cell}: {command!r} was denied: {proc.stdout}"


@pytest.mark.parametrize("cell,command", DENY, ids=[c for c, _ in DENY])
def test_main_thread_is_not_denied_by_the_subagent_rule(cell, command):
    """No `agent_id`: none of the SM-446 cells is refused on the main thread."""
    assert decision(run(command, subagent=False)) == "allow"


def test_the_deny_is_one_line_naming_sm446_and_the_read_only_way():
    out = json.loads(run("message read 1", subagent=True).stdout)["hookSpecificOutput"]
    reason = out["permissionDecisionReason"]
    assert "\n" not in reason
    assert reason.startswith("mail-guard: ")
    assert "the seat would not be paged" in reason
    assert "`message list`" in reason and "`message read --peek N`" in reason
    assert "`message read`" in reason             # names what it refused


def test_the_deny_is_logged_like_any_other():
    """SM-518: a deny is an injection; the SM-446 one gets the same row."""
    run("pager stop", subagent=True)
    rows = Path(os.environ["HOME"], ".session", "sessions", SESSION,
                "log", "mail", "pager.jsonl").read_text().splitlines()
    row = json.loads(rows[-1])
    assert row["via"] == "pretooluse-deny" and row["sid"] == SESSION
    assert row["blocked_tool"] == "Bash" and row["tool_use_ids"] == ["toolu_test"]


def test_a_main_thread_pager_hook_batch_is_allowed():
    """Pinned so nobody reads the rule as "hooks are blocked": with no
    `agent_id`, `pager hook batch` passes (and is byte-identical to BASE, in
    the proof below). The harness runs real hooks as hook commands, never
    through the Bash tool, so this guard does not even see them."""
    assert decision(run("pager hook batch", subagent=False)) == "allow"
    assert decision(run("pager hook stop", subagent=False)) == "allow"


def test_an_empty_agent_id_is_the_main_thread():
    """`agent_id` is truthy-tested, as the pager's SM-378 guard tests it."""
    p = json.loads(payload("message read 1", subagent=False))
    p["agent_id"] = ""
    proc = subprocess.run(["/bin/sh", str(SHIM)], input=json.dumps(p),
                          capture_output=True, text=True, timeout=20)
    assert decision(proc) == "allow"


def test_a_damaged_send_is_still_denied_inside_a_subagent():
    """The subagent rule comes first, and it does not stand in for the send
    rules: a send it allows is still judged by them."""
    proc = run("message send someone -s \"see `date`\" -f body.md", subagent=True)
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "the seat would not be paged" not in out["permissionDecisionReason"]


# ── byte-identical on the main thread: every command, against BASE ────────

def _base_shim(tmp_path_factory) -> Path:
    if not in_checkout():
        pytest.skip(f"not a git checkout (the git-archive release export): base {BASE} cannot be read")
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{BASE}^{{commit}}"],
                      capture_output=True).returncode != 0:
        pytest.skip(f"base {BASE} not in this clone (shallow?): fetch it to run the proof")
    dest = tmp_path_factory.mktemp("base")
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", BASE, "hooks"],
                             capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
    return dest / "hooks" / "mail-guard-hook"


@pytest.fixture(scope="module")
def base_shim(tmp_path_factory):
    return _base_shim(tmp_path_factory)


#: The main-thread proof also covers sends the BASE guard judges, so
#: "identical" is tested on denies as well as allows.
MAIN_EXTRA = [
    ("send-damaged", "message send someone -s \"see `date`\" -f body.md"),
    ("send-inline-retired", "session mail send someone 'an inline body'"),
    ("send-clean", "message send someone -s 'plain' -f body.md"),
]


@pytest.mark.parametrize("cell,command", ALL + MAIN_EXTRA,
                         ids=[c for c, _ in ALL + MAIN_EXTRA])
def test_main_thread_is_byte_identical_to_base(base_shim, cell, command):
    now = run(command, subagent=False)
    then = run(command, subagent=False, shim=base_shim)
    assert (now.returncode, now.stdout) == (then.returncode, then.stdout), cell
