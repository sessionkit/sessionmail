"""mail-guard's wiring: the plugin registers it, and it never blocks by failing.

In session the guard was wired by `session hooks install` into
~/.claude/settings.json, and `make check-hooks` went red on an unwired box.
Here the PLUGIN registers it (hooks/hooks.json, SM-30: hooks we rely on
install with the plugin), so "is this seat behind the guard" is "did it load
sessionmail" - and what is left to pin is the registration and the shim.

The shim's one job is SM-30's rule: a hook must never be the reason a
session cannot run. Measured before the shim (Sun 2030-12-08): with
no python3 on PATH the bare hook exits 127, a hook error on every Bash call;
`python3 <missing file>` exits 2, and exit 2 from a PreToolUse hook BLOCKS
the call. Every one of those must come out as exit 0 and silence.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from sessionmail import _settings

from test_mail_guard import LAMP_1, guard

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "hooks" / "mail-guard-hook"
ENTRY = '"${CLAUDE_PLUGIN_ROOT}/hooks/mail-guard-hook"'


def payload(command: str) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": command}})


def run_shim(shim: Path, command: str, env: dict | None = None):
    return subprocess.run(["/bin/sh", str(shim)], input=payload(command),
                          capture_output=True, text=True, env=env)


def test_hooks_json_registers_the_guard_on_bash_through_the_shim():
    """Bash, and (SM-815) the message and pager servers' MCP tools, and
    (SM-837) `memory_init`: the same shim, so one guard judges both doors."""
    spec = json.loads((ROOT / "hooks/hooks.json").read_text())
    groups = spec["hooks"]["PreToolUse"]
    assert [g.get("matcher") for g in groups] == [
        "Bash", "mcp__plugin_sessionmail_(message|pager)__.*"
                "|mcp__plugin_sessionmail_memory__memory_init"]
    for group in groups:
        (hook,) = group["hooks"]
        assert hook["type"] == "command" and hook["command"] == ENTRY
        assert 0 < hook["timeout"] <= 10, "a guard that can stall a tool is worse than none"
    assert "mail-guard" in spec["description"] and "PreToolUse (MCP)" in spec["description"]


def test_the_shim_is_executable_and_never_execs():
    assert SHIM.stat().st_mode & stat.S_IXUSR
    assert (ROOT / "hooks/mail-guard/hook.py").stat().st_mode & stat.S_IXUSR
    body = SHIM.read_text()
    assert "|| exit 0" in body
    assert "exec " not in body.replace("# No `exec`", ""), "exec would skip the `|| exit 0`"


def test_through_the_shim_a_damaged_send_is_denied():
    proc = run_shim(SHIM, LAMP_1)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_through_the_shim_a_safe_command_is_silent():
    proc = run_shim(SHIM, "ls -la")
    assert proc.returncode == 0 and proc.stdout == ""


def test_no_python3_on_path_allows(tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    proc = run_shim(SHIM, LAMP_1, env={"PATH": str(empty)})
    assert proc.returncode == 0 and proc.stdout == "", proc.stderr


@pytest.mark.parametrize("hook_py", [
    None,                                   # the file is missing: python3 exits 2
    "raise SystemExit(2)\n",                # exit 2 would BLOCK the call
    "raise RuntimeError('boom')\n",         # an uncaught crash: exit 1
    "def broken(:\n",                       # a SyntaxError main's wrapper never sees
], ids=["missing", "exit-2", "crash", "syntax-error"])
def test_a_broken_guard_allows(tmp_path, hook_py):
    hooks = tmp_path / "hooks"
    (hooks / "mail-guard").mkdir(parents=True)
    shim = hooks / "mail-guard-hook"
    shutil.copy2(SHIM, shim)
    if hook_py is not None:
        (hooks / "mail-guard" / "hook.py").write_text(hook_py)
    proc = run_shim(shim, LAMP_1)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert proc.stdout == ""


def test_decide_itself_never_raises(monkeypatch):
    """Before the move, split_commands() and match_send() ran outside
    decide()'s try: the process still exited 0, but the importable function
    raised. It is the function the corpus scan and these tests call."""
    def boom(*_a, **_k):
        raise RuntimeError("a guard bug")
    for name in ("split_commands", "match_send", "judge"):
        with monkeypatch.context() as m:
            m.setattr(guard, name, boom)
            assert guard.decide(LAMP_1) is None, name


def test_sm450_mail_guard_does_not_read_the_settings_file():
    """SM-450, RULED (Q1a): mail-guard stays on always. With the
    pager hooks OFF in the settings file, a damaged send is still denied."""
    settings = Path(os.environ["HOME"]) / _settings.RELATIVE
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"version": 1, "hooks": false}\n')
    proc = run_shim(SHIM, LAMP_1)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "settings.json" not in (ROOT / "hooks/mail-guard/hook.py").read_text()
