"""`pager hook stop` / `pager hook batch` - the plugin's two hooks, as verbs.

SM-30: the hooks the pager depends on
install with the plugin. hooks/hooks.json registers Stop, PostToolBatch and
(SM-640) UserPromptSubmit, each running `pager hook <event>` through
hooks/pager-hook - UserPromptSubmit on PostToolBatch's own `batch` - so seat identity
has one resolver. The two lines they say live in hook_lines.toml.

Hermetic like test_pager_status: a seat under tmp_path, SESSIONMAIL_HOME
pointing at it, no notmuch, fixtures `alpha` at `example.invalid`.
"""

from __future__ import annotations

import json
import re
import subprocess
import os
import stat
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner


def context(result) -> list[str]:
    """The lines a batch hook hands the model: hookSpecificOutput.additionalContext."""
    if not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    out = data["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolBatch"
    return out["additionalContext"].splitlines()

from _export import export_absent
from sessionmail import _seat, pager
from sessionmail.pager import cli

ROOT = Path(__file__).resolve().parents[1]
SESSION = "11111111-2222-3333-4444-555555555555"

#: SM-450: the settings file that turns the hooks on - the ONE setting
#: (RULED). The `hermetic` fixture points HOME at a sandbox with no
#: file, which means OFF; every test that expects a hook to speak writes it.
HOOKS_ON = '{"version": 1, "hooks": true}\n'
HOOKS_OFF = '{"version": 1, "hooks": false}\n'


def settings_path() -> Path:
    """`$HOME/.sessionkit/mail/settings/mail.json`, in the sandbox HOME (conftest)."""
    return Path(os.environ["HOME"]) / _settings.RELATIVE


def write_settings(data: str | bytes) -> Path:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)
    return p


@pytest.fixture
def seat(tmp_path, monkeypatch):
    home = tmp_path / "sessionmail"
    root = home / "seats" / "alpha"
    (root / "subscriptions").mkdir(parents=True)     # a pre-4.2 seat's folder; the pager ignores it
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
    monkeypatch.delenv("EMAIL", raising=False)
    # Not listening unless a test says so: no stamp, and the ancestry
    # fallback stubbed empty so a real listener on the box cannot leak in.
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    # SM-706: and the one-pid `ps` the batch hook asks of a live listener
    # (is it the monitor?) knows nothing either: the plain listening line.
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    # SM-450 (tasks.md 3.3): the hooks ON, so every nag and ride-along test
    # below keeps its meaning. A test about the file itself rewrites it.
    write_settings(HOOKS_ON)
    return root


def stamps(root: Path) -> Path:
    # Where the code keeps SESSION's stamps - `<home>/sessions/<session>/stamps/`
    # since SM-122 (session-keyed paths), not the seat pair this helper wrote
    # before the merge; asked of _seat so the two cannot drift again.
    return _seat.stamp_dir(None, SESSION)


def arm(root: Path) -> None:
    """A listening stamp holding a live pid (our own) = armed."""
    d = stamps(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / _seat.LISTENING_STAMP).write_text(str(os.getpid()))


# ── the lines: one file, both hooks ──────────────────────────────────────

def test_the_lines_live_in_one_file():
    lines = tomllib.loads((ROOT / "src/sessionmail/hook_lines.toml").read_text())
    # SM-450: plus the settings file's two lines (design.md D5)
    # SM-706: plus the not-yet line (`pager status` before claude starts),
    # and the listening line for the pager monitor (amendment 3)
    # SM-726 do-003: plus the no-monitor sentence (design.md (l5)), kept once
    # SM-726 do-005: plus the two ways out (kept once) and the three DEAF lines
    # SM-726 do-008: plus the not-required state line after `pager stop --kill`
    # SM-498: plus the SessionStart hook's line about the box
    # SM-873: plus the stranded-host line (the old settings folder not moved yet),
    # and the copied line (both settings folders real)
    assert set(lines) == {"not_listening", "listening", "off", "settings_created", "settings_fault",
                          "settings_stranded", "settings_copied", "settings_drift", "not_yet", "listening_monitor", "no_monitor",
                          "ways_out", "deaf_monitor_missing", "deaf_monitor_ended",
                          "deaf_not_listening", "monitor_ended", "born"}
    assert pager.LINE_NOT_YET == lines["not_yet"].format(arm=pager.ARM_HOW)   # composed: state + arm.txt
    # SM-410: the off line is a state, never an order to arm
    # SM-726 do-003 (design.md (l3)): reworded to MUTED - the stamp mutes
    # whatever listens; `pager unmute` is the way back
    assert pager.LINE_OFF == lines["off"] and pager.LINE_OFF.startswith("pager is muted")
    assert "`pager unmute` resumes" in pager.LINE_OFF
    assert "pager listen" not in pager.LINE_OFF and "re-arm" not in pager.LINE_OFF
    assert pager.LINE_NOT_LISTENING == lines["not_listening"].format(arm=pager.ARM_HOW)   # composed: state + arm.txt
    assert pager.LINE_LISTENING == lines["listening"]
    # The words, and what to do next in each
    assert pager.LINE_NOT_LISTENING.startswith("pager is not listening") and "pager listen" in pager.LINE_NOT_LISTENING
    assert pager.LINE_LISTENING.startswith("pager is listening") and "pager stop" in pager.LINE_LISTENING
    # SM-706 amendment 3: the monitor's listening line names it and never
    # ADVISES `pager stop`. SM-726 do-003 (design.md (l3)): it teaches the
    # mute instead, and says the monitor keeps running
    assert pager.LINE_LISTENING_MONITOR == lines["listening_monitor"]
    assert pager.LINE_LISTENING_MONITOR.startswith("pager is listening")
    # SM-726: "the pager monitor" - it lives in the root plugin now
    assert "pager monitor hears for you" in pager.LINE_LISTENING_MONITOR
    assert "pager-monitor plugin" not in pager.LINE_LISTENING_MONITOR
    assert "pager stop" not in pager.LINE_LISTENING_MONITOR
    assert "`pager mute` silences it" in pager.LINE_LISTENING_MONITOR
    # SM-726 do-003 (design.md (l5)): the no-monitor sentence, plain, both ways out
    assert pager.LINE_NO_MONITOR == lines["no_monitor"].format(ways_out=lines["ways_out"])
    assert "can only start when the seat launches" in pager.LINE_NO_MONITOR
    assert "run `pager listen` in the background" in pager.LINE_NO_MONITOR
    assert "restart the seat" in pager.LINE_NO_MONITOR
    # the ride-along's nudge is the same fact in the same words
    assert pager.PAGE_NOT_ARMED == pager.LINE_NOT_LISTENING


def test_pager_stop_is_pager_off():
    r = CliRunner().invoke(cli, ["--help"])
    assert " off " in r.output and " stop " in r.output
    assert cli.commands["stop"] is cli.commands["off"]


# ── the plugin's registration ────────────────────────────────────────────

def test_hooks_json_registers_both_events_through_the_shim():
    spec = json.loads((ROOT / "hooks/hooks.json").read_text())
    events = spec["hooks"]
    # PreToolUse is mail-guard's, not the pager's: tests/test_mail_guard_wiring.py
    assert set(events) == {"SessionStart", "Stop", "PostToolBatch", "UserPromptSubmit",
                           "PreToolUse"}
    for event, verb in (("SessionStart", "start"), ("Stop", "stop"), ("PostToolBatch", "batch"),
                        ("UserPromptSubmit", "batch")):
        cmds = [h["command"] for group in events[event] for h in group["hooks"]]
        assert cmds == [f'"${{CLAUDE_PLUGIN_ROOT}}/hooks/pager-hook" {verb}'], cmds
    shim = ROOT / "hooks/pager-hook"
    assert shim.stat().st_mode & stat.S_IXUSR, "the shim must be executable"
    body = shim.read_text()
    assert 'exec pager hook "$@"' in body
    assert "CLAUDE_CODE_SESSION_ID" not in body, "no second copy of the seat rule in shell"


def test_user_prompt_submit_runs_the_very_same_command_as_post_tool_batch():
    """SM-640 R6: the same code, run by both hooks without duplication.
    The UserPromptSubmit registration is PostToolBatch's, byte for byte:
    command, type and timeout. No second script, no second verb, no matcher."""
    events = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
    assert events["UserPromptSubmit"] == events["PostToolBatch"]
    raw = (ROOT / "hooks/hooks.json").read_text()
    assert raw.count('/hooks/pager-hook\\" batch"') == 2, "batch is wired to exactly two events"


def test_the_hooks_json_description_says_what_user_prompt_submit_does():
    """SM-640 do-004: the description says what UPS does - it notifies and
    stamps only when it carries; a typed prompt only with carry-prompt on;
    a subagent never; the hooks off, silent."""
    text = json.loads((ROOT / "hooks/hooks.json").read_text())["description"]
    ups = text[text.index("UserPromptSubmit (wake carry)"):text.index("PreToolUse (Bash)")]
    for needle in ("SAME command as PostToolBatch", "<task-notification>", "carry stamp",
                   "set carry-prompt on", "default off", "set carry off",
                   "Inside a subagent, never", "hooks off, silent"):
        assert needle in ups, needle


def test_the_shim_says_the_package_is_missing_once_then_hushes(tmp_path):
    """Two installs, and the plugin is only one of them. With `pager` off
    PATH every skill, hook and MCP entry the plugin brought calls a command
    that is not there; exiting 0 in silence leaves a reader with a plugin
    that simply does nothing. So: still exit 0 - a hook is never the reason
    a session cannot run - but say it once, on stdout, where
    PostToolBatch's context goes. (A gap a new user meets.)"""
    shim = ROOT / "hooks/pager-hook"
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)}
    first = subprocess.run(["/bin/sh", str(shim), "batch"], input="{}",
                           capture_output=True, text=True, env=env)
    assert first.returncode == 0
    assert "uv tool install sessionmail" in first.stdout
    second = subprocess.run(["/bin/sh", str(shim), "batch"], input="{}",
                            capture_output=True, text=True, env=env)
    assert second.returncode == 0
    assert second.stdout == "", "said twice in one session"


# ── stop ─────────────────────────────────────────────────────────────────

def test_stop_says_not_listening_with_exit_2_and_stamps_nothing(seat):
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert not (stamps(seat) / _seat.STOPPED_STAMP).exists()
    assert r.exit_code == 2
    assert r.stderr.strip() == pager.LINE_NOT_LISTENING
    assert r.stdout == ""


def test_stop_is_silent_and_zero_when_listening(seat):
    arm(seat)
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""
    assert not (stamps(seat) / _seat.STOPPED_STAMP).exists()


def test_stop_never_loops_when_the_harness_is_already_continuing(seat):
    payload = json.dumps({"stop_hook_active": True})
    r = CliRunner().invoke(cli, ["hook", "stop"], input=payload)
    assert r.exit_code == 0 and r.stderr == ""
    assert not (stamps(seat) / _seat.STOPPED_STAMP).exists(), "no stamp, ever (stopped-means-idle)"


def test_stop_is_silent_when_the_pager_is_opted_out(seat, monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 0 and r.stderr == ""


def test_stop_without_a_session_id_says_so_and_exits_0(seat, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 0
    assert "nothing to do" in r.stderr
    assert not (seat / _seat.STAMPS_DIR).exists()


def test_stop_reads_the_kicked_seats_variable_too(seat, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.setenv("CLAUDE_SESSION_ID", SESSION)
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert not (stamps(seat) / _seat.STOPPED_STAMP).exists()
    assert r.exit_code == 2


def test_stop_at_a_plain_shell_has_no_seat_and_exits_0(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sm"))
    write_settings(HOOKS_ON)                               # SM-450: hooks off would be silent earlier still
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 0
    assert "could not look" in r.stderr


# ── batch ────────────────────────────────────────────────────────────────

def test_batch_says_not_listening_stamps_ride_along_and_exits_0(seat):
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert context(r) == [pager.LINE_NOT_LISTENING]
    assert (stamps(seat) / _seat.RIDE_ALONG_STAMP).exists()


def test_batch_names_the_event_the_harness_named(seat):
    r = CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps({"hook_event_name": "PostToolUse"}))
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_batch_says_listening_when_armed(seat):
    arm(seat)
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert context(r) == [pager.LINE_LISTENING]


# SM-706 amendment 3: "pager is listening - run `pager stop` to stop" on a
# monitor seat taught the monitor's kill on every batch (Claude Code does not
# restart a monitor). When the live listener is `pager listen --monitor`, the
# line names the monitor and drops the stop advice; anything else keeps it.

MONITOR_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor"


def test_batch_names_the_monitor_and_never_advises_stop_when_it_listens(seat, monkeypatch):
    arm(seat)
    me = os.getpid()
    asked: list[int] = []

    def one(pid):
        asked.append(pid)
        return {pid: 1}, {pid: MONITOR_CMD}

    monkeypatch.setattr(_seat, "_ps_one", one)
    monkeypatch.setattr(_seat, "_ps_table", lambda: pytest.fail("the full ps table was read"))
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert context(r) == [pager.LINE_LISTENING_MONITOR]
    assert "run `pager stop`" not in r.stdout
    assert asked == [me]                    # one pid's `ps`, the stamp's


def test_batch_keeps_the_plain_listening_line_for_a_hand_armed_listener(seat, monkeypatch):
    arm(seat)
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({pid: 1}, {pid: "/x/bin/pager listen"}))
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert context(r) == [pager.LINE_LISTENING]
    assert "run `pager stop` to stop" in r.stdout


def test_batch_keeps_the_plain_listening_line_with_no_ps_answer(seat):
    """The fixture's `_ps_one` knows no pid: nothing can be said about the
    monitor, so today's line, unchanged."""
    arm(seat)
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert context(r) == [pager.LINE_LISTENING]


def test_batch_finds_a_stampless_monitor_by_ancestry_from_the_same_table(seat, monkeypatch):
    """No stamp yet (the monitor has exec'd, not stamped): ancestry finds it
    under this session's claude, and the SAME table says it is the monitor -
    no second `ps`."""
    claude, monitor, me = 7_300_001, 7_300_002, os.getpid()
    monkeypatch.setattr(_seat, "_ps_table", lambda: (
        {monitor: claude, me: claude, claude: 1},
        {claude: "claude", monitor: MONITOR_CMD, me: "pager hook batch"}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: pytest.fail("a second ps was run"))
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert context(r) == [pager.LINE_LISTENING_MONITOR]


def test_batch_gives_queued_mail_once_and_the_state_line_once(seat, tmp_path):
    arm(seat)
    mail = tmp_path / "1907540800.1.hq:2,"
    mail.write_text("From: beta@example.invalid\nSubject: hello alpha\nMessage-ID: <1@example.invalid>\n\nbody\n")
    q = _seat.queue_new(_seat.resolve(), SESSION)
    os.link(mail, q / mail.name)
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    lines = context(r)
    assert lines[-1] == pager.LINE_LISTENING
    assert any("hello alpha" in ln for ln in lines[:-1]), lines
    # once: the mail was given, and (SM-450 D11, RULED) so was the
    # state line, which has not changed - the next batch says nothing
    r2 = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r2.exit_code == 0 and context(r2) == []


def test_the_first_hook_after_the_merge_moves_before_it_reads_the_marks(seat, tmp_path):
    """Session-settings 3.3a, a review note on dd3b037: a session whose
    state still sits at the old root (its listener armed before the merge).
    Its first new-code hook moves the state BEFORE it reads the marks, so a
    mail marked done at the old root does not ride once more."""
    old = _seat.old_session_root(SESSION)
    (old / "ride" / "new").mkdir(parents=True)
    mail = tmp_path / "1907540800.2.hq:2,"
    mail.write_text("From: beta@example.invalid\nSubject: handled already\n"
                    "Message-ID: <2@example.invalid>\n\nbody\n")
    os.link(mail, old / "ride" / "new" / mail.name)
    (old / "marks").write_text(_seat.Mark("2030-07-13T22:34:56+10:00", "2@example.invalid",
                                          "done", "alpha", SESSION).line + "\n")
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert old.is_symlink() and (_seat.session_root(SESSION) / "marks").is_file()
    assert not any("handled already" in ln for ln in context(r)), context(r)


def test_batch_is_silent_when_opted_out_or_at_a_plain_shell(seat, monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""
    monkeypatch.delenv(_seat.PAGER_VAR)
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""


def test_batch_on_a_seat_with_no_subscriptions_folder_still_scoops(seat):
    """It used to say only the state line until the first listen wrote the
    born files. They retired (message-subscriptions 4.2), so the folder
    means nothing to the pager: the scoop runs, quiet or not."""
    (seat / "subscriptions").rmdir()
    q = _seat.ride_dir(None, SESSION) / _seat.QUEUE_NEW
    q.mkdir(parents=True)
    (q / "1923171201.M1P1.host").write_text(
        "From: beta@example.invalid\nTo: alpha@example.invalid\n"
        "Subject: queued anyway\nMessage-ID: <q1@example.invalid>\n\nbody\n")
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    lines = context(r)
    assert any("queued anyway" in ln for ln in lines), lines
    assert lines[-1] == pager.LINE_NOT_LISTENING


def test_batch_with_no_session_id_says_only_the_state_line(seat, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0
    assert context(r) == [pager.LINE_NOT_LISTENING]


# ── SM-378: a subagent's batch is not the seat's desk ────────────────────
#
# Measured on a live seat: two background subagents (Agent tool, async) ran while the main thread
# sat stopped. Claude Code runs the plugin's PostToolBatch hook for a
# SUBAGENT's tool batches too, with the PARENT's session id in the env
# and in the payload, so `pager hook batch` scooped the parent's queue,
# marked each id `seen (ride-along)` and handed the text to the SUBAGENT's
# context - where no one reads the seat's mail. The listener then counts
# the ids as given and never pages them. Four mails, lost.
#
# The payload's ONLY discriminator is `agent_id` / `agent_type` (measured
# with a scratch `claude -p` on 2.1.280: the main thread's batch carries
# neither, the subagent's carries both; `session_id`, `transcript_path`
# and $CLAUDE_CODE_SESSION_ID are identical). The shape below is that
# measured payload, trimmed.

def _subagent_payload() -> str:
    return json.dumps({
        "session_id": SESSION,
        "transcript_path": f"/tmp/projects/x/{SESSION}.jsonl",
        "cwd": "/tmp",
        "permission_mode": "bypassPermissions",
        "agent_id": "a81b552b29e268608",
        "agent_type": "general-purpose",
        "hook_event_name": "PostToolBatch",
        "tool_calls": [],
    })


def _main_payload() -> str:
    data = json.loads(_subagent_payload())
    del data["agent_id"], data["agent_type"]
    return json.dumps(data)


def test_sm378_a_subagents_batch_never_marks_the_seats_mail_given(seat, tmp_path):
    """The invariant: nothing is marked given unless its text went to the
    context that reads this seat's mail. A subagent's batch is not that
    context, so the mail stays ungiven - for the main thread's next batch,
    and for the listener, which pages what is not given."""
    arm(seat)
    mail = tmp_path / "2000000000.M100000P11111.hq:2,"
    mail.write_text("From: judy@example.invalid\nSubject: please read the clipboard\n"
                    "Message-ID: <2000000000.11111.11111@example.invalid>\n\nEOM\n")
    s = _seat.resolve()
    os.link(mail, _seat.queue_new(s, SESSION) / mail.name)
    mid = "2000000000.11111.11111@example.invalid"

    sub = CliRunner().invoke(cli, ["hook", "batch"], input=_subagent_payload())
    assert sub.exit_code == 0
    assert "seen" not in _seat.read_marks(s, SESSION).get(mid, set()), (
        "a subagent's PostToolBatch marked the seat's mail given: "
        "its text went to the subagent's context, not the seat's")

    main = CliRunner().invoke(cli, ["hook", "batch"], input=_main_payload())
    assert main.exit_code == 0
    assert any("please read the clipboard" in ln for ln in context(main)), context(main)
    assert "seen" in _seat.read_marks(s, SESSION).get(mid, set())


def _queue_one(s, tmp_path) -> tuple[Path, str]:
    mail = tmp_path / "2000000060.M1P1.hq:2,"
    mail.write_text("From: judy@example.invalid\nSubject: speak\n"
                    "Message-ID: <2000000060.22222.22222@example.invalid>\n\nEOM\n")
    link = _seat.queue_new(s, SESSION) / mail.name
    os.link(mail, link)
    return link, "2000000060.22222.22222@example.invalid"


def test_sm378_a_subagents_batch_leaves_the_seats_queue_and_stamps_untouched(seat, tmp_path):
    """fix-001: a subagent's batch is not the seat's tool call. It takes
    nothing from the queue, touches no busy stamp (a stamp would defer
    the page to a ride-along that is not coming), and says nothing: both
    state lines are orders about the SEAT's listener, not the subagent's."""
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    busy = stamps(seat) / _seat.RIDE_ALONG_STAMP

    for listening in (False, True):
        if listening:
            arm(seat)
        r = CliRunner().invoke(cli, ["hook", "batch"], input=_subagent_payload())
        assert r.exit_code == 0
        assert r.stdout == "", "a subagent's batch must say nothing - no mail, no state line"
        assert link.exists(), "the seat's queued mail was taken by a subagent's batch"
        assert not busy.exists(), "a subagent's batch stamped the seat busy"
        assert _seat.read_marks(s, SESSION).get(mid, set()) == set()


def test_sm378_agent_type_without_agent_id_is_the_main_thread(seat, tmp_path):
    """`claude --agent X` puts agent_type on the MAIN thread's payload too,
    with no agent_id. That is the seat's own desk: it must ride along
    exactly as before - the guard keys on agent_id, never agent_type."""
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    data = json.loads(_subagent_payload())
    del data["agent_id"]
    r = CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(data))
    assert r.exit_code == 0
    lines = context(r)
    assert any("speak" in ln for ln in lines), lines
    assert lines[-1] == pager.LINE_NOT_LISTENING
    assert not link.exists()
    assert "seen" in _seat.read_marks(s, SESSION).get(mid, set())
    assert (stamps(seat) / _seat.RIDE_ALONG_STAMP).exists()


@pytest.mark.parametrize("payload", ["{}", "", "not json",
                                     json.dumps({"hook_event_name": "PostToolBatch", "agent_id": ""}),
                                     json.dumps({"hook_event_name": "PostToolBatch", "agent_id": None})])
def test_sm378_no_agent_id_behaves_as_before(seat, tmp_path, payload):
    """A payload with no agent_id - the main thread, an older Claude Code,
    a hand run with junk or nothing on stdin - scoops, marks and stamps as
    on e917c58."""
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    r = CliRunner().invoke(cli, ["hook", "batch"], input=payload)
    assert r.exit_code == 0
    lines = context(r)
    assert any("speak" in ln for ln in lines), lines
    assert lines[-1] == pager.LINE_NOT_LISTENING
    assert not link.exists()
    assert "seen" in _seat.read_marks(s, SESSION).get(mid, set())
    assert (stamps(seat) / _seat.RIDE_ALONG_STAMP).exists()


# ── SM-450: sessionmail knows no roles; SM-410: a stop on purpose ────────
#
# Review case 8: 53 of 57 refused stops said
# "re-arm", among them to seats that had just run `pager stop` to stand
# down. SM-410 answered with two things: the `off` stamp (kept, below) and a
# role exemption, which SM-450 removes (openspec/changes/pager-settings-file,
# tasks.md 4.1-4.2, design.md D8-D9: session mail knows nothing of workers
# and reviewers). With the hooks on, every seat with no
# listener is nagged, whatever it is called. `is_listening`'s parent-chain
# fallback is stubbed empty by the `seat` fixture, so a live listener on the
# box running this suite cannot hide the nag (the RCA's isolation).

#: Addresses that SM-410 read as a role that holds no listener. Each is now
#: only an address (spec: "A worker's address is only an address").
ROLE_SHAPED_ADDRESSES = [
    "sessionmail+sm-410.worker",          # plus grammar: the last dot-segment of the extension
    "worker.sm-410",                      # legacy role.scope
    "WORKER.SM-410",                      # any case
    "sessionmail+sm-410.reviewer",
    "reviewer.sm-410",
]


def _only_a_session_id(monkeypatch):
    """No seat variable and no EMAIL: the seat resolves by session id alone."""
    for var in _seat.SEAT_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("EMAIL", raising=False)


def test_success_criterion_2_a_worker_by_role_variable_is_nagged(seat, monkeypatch):
    """SM-450 Success Criterion 2 (spec: "A worker is nagged like any seat"):
    the hooks on, and an environment that carries only CLAUDE_CODE_SESSION_ID
    and TASKMASTER_ROLE=worker - the live worker's env the SM-410 RCA
    measured. The Stop hook exits 2 with the not-listening line."""
    _only_a_session_id(monkeypatch)
    monkeypatch.setenv("TASKMASTER_ROLE", "worker")
    r = stop()
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING
    assert r.stdout == ""
    assert context(batch()) == [pager.LINE_NOT_LISTENING]


@pytest.mark.parametrize("var", ["TM_AGENT_ADDRESS", "EMAIL"])
@pytest.mark.parametrize("address", ROLE_SHAPED_ADDRESSES + ["sessionmail+sm-410.foreman"])
def test_a_workers_address_is_only_an_address(seat, monkeypatch, var, address):
    """Spec: "A worker's address is only an address". Stop exits 2, and the
    session's first batch ends with the not-listening line, exactly as for
    `sessionmail+sm-410.foreman` (the last parameter, the control)."""
    _only_a_session_id(monkeypatch)
    if var == "EMAIL":
        address = f"{address}@{_seat.short_host()}"   # a declared address, on this host
    monkeypatch.setenv(var, address)
    r = stop()
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING
    lines = context(batch())
    assert lines and lines[-1] == pager.LINE_NOT_LISTENING, lines


def test_a_worker_gets_its_mail_and_the_state_line_on_its_first_batch(seat, monkeypatch, tmp_path):
    """Inverted from SM-410's "only the state line goes": mail AND the state
    line on the session's first batch (D11 says it once)."""
    monkeypatch.setenv("TASKMASTER_ROLE", "worker")
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    lines = context(batch())
    assert any("speak" in ln for ln in lines), lines
    assert lines[-1] == pager.LINE_NOT_LISTENING, lines
    assert not link.exists()
    assert "seen" in _seat.read_marks(s, SESSION).get(mid, set())


@pytest.mark.parametrize("address", ["sessionmail+sm-410.foreman", "sessionmail+super", "acmecorp+gc",
                                     "foreman.sm-410", "sessionmail+engineer"])
def test_a_listening_seat_with_no_listener_is_still_nagged(seat, monkeypatch, address):
    """(C) The nag stands for every seat: exit 2, the line."""
    monkeypatch.setenv("TM_AGENT_ADDRESS", address)
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 2
    assert r.stderr.strip() == pager.LINE_NOT_LISTENING


#: SM-450 Success Criterion 3 (spec: "No role logic in the source"). The
#: names SM-410's role code used; none may appear anywhere in the source.
ROLE_CODE_NAMES = ("TASKMASTER_ROLE", "NO_LISTEN_ROLES", "no_listen_role", "_address_role_no_listen")
ROLE_WORD = re.compile(r"worker|reviewer", re.IGNORECASE)


def _role_word_allowed(rel: str, line: str) -> bool:
    """The uses of `worker`/`reviewer` that are not roles, matched by FILE and
    CONTENT, never by line number (tasks.md 4.3, design.md D8)."""
    if rel == "_mcp.py":
        return "worker thread" in line                     # a thread, not a seat
    if rel == "mhs/mhs-thread":
        return "max_workers" in line                       # a thread pool
    if rel.startswith("mhs/") and rel.endswith(".lib.sh"):
        return line.lstrip().startswith("#")               # address-shape examples in comments
    return False


def test_success_criterion_3_no_role_logic_in_the_source():
    src = ROOT / "src" / "sessionmail"
    seen, bad = set(), []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue                                        # not source text
        rel = path.relative_to(src).as_posix()
        seen.add(rel)
        for n, line in enumerate(text.splitlines(), 1):
            if any(name in line for name in ROLE_CODE_NAMES):
                bad.append(f"{rel}:{n}: role code: {line.strip()}")
            elif ROLE_WORD.search(line) and not _role_word_allowed(rel, line):
                bad.append(f"{rel}:{n}: {line.strip()}")
    # the walk must have seen the source, or zero hits means nothing
    assert {"_seat.py", "pager.py", "_mcp.py", "mhs/mhs-thread", "mhs/mail-box.lib.sh"} <= seen, sorted(seen)
    assert bad == [], "\n".join(bad)


#: SM-450 tasks.md 5.5 (spec: "The docs describe the file, not roles").
SETTINGS_PATH = "~/.sessionkit/mail/settings/mail.json"
HOOKS_TRUE = '"hooks": true'


def _help(*argv: str) -> str:
    r = CliRunner().invoke(cli, [*argv, "-h"], prog_name="pager")
    assert r.exit_code == 0, r.output
    return r.output


#: The diagram is export-ignored (.gitattributes): it is in a checkout, and
#: not in the release archive.
DIAGRAM = "docs/architecture/pager.html"


def doc_surfaces() -> dict[str, str]:
    """The four surfaces the scenario names, read the way a reader meets them.
    Where the export left the diagram out (absent AND untracked: the tarball,
    or a clone of the export) it is left out here, so only its own param
    skips; in a checkout that tracks it, a missing diagram still fails."""
    surfaces = {
        "hooks/hooks.json description": json.loads((ROOT / "hooks/hooks.json").read_text())["description"],
        "skills/pager/SKILL.md": (ROOT / "skills/pager/SKILL.md").read_text(),
        "pager hook --help": _help("hook"),
    }
    if not export_absent(DIAGRAM):
        surfaces[DIAGRAM] = (ROOT / DIAGRAM).read_text()
    return surfaces


@pytest.mark.parametrize("name", ["hooks/hooks.json description", "skills/pager/SKILL.md",
                                  "pager hook --help", "docs/architecture/pager.html"])
def test_the_docs_describe_the_file_not_roles(name):
    surfaces = doc_surfaces()
    if name not in surfaces:
        pytest.skip(f"{name} is export-ignored: not in the release archive")
    text = surfaces[name]
    assert len(text) > 200, f"{name} read as almost nothing - the check would prove nothing"
    assert not re.search(r"worker|reviewer|TASKMASTER_ROLE", text, re.IGNORECASE), name
    assert SETTINGS_PATH in text, f"{name} does not name {SETTINGS_PATH}"
    if name != "docs/architecture/pager.html":           # the diagram names the file; the rest name the setting
        assert HOOKS_TRUE in text, f"{name} does not name {HOOKS_TRUE}"


def test_the_hooks_json_description_says_what_the_file_governs_and_what_it_does_not():
    """tasks.md 5.1: mail-guard does not read the file (Q1a, RULED);
    the state line is given once, then only on change (D11, RULED);
    the opt-out still wins; a missing file is written OFF, once."""
    text = doc_surfaces()["hooks/hooks.json description"]
    guard = text[text.index("mail-guard"):]
    assert "does not read the settings file" in guard
    assert "once, then only on change" in text
    assert "SESSIONMAIL_PAGER=off wins" in text
    assert "OFF" in text and "says so, once" in text


def test_the_hook_help_names_the_read_only_stop_and_the_state_line_once():
    """tasks.md 5.3: `pager hook --help` names the Stop hook's read-only rule
    (D3) and the batch's once-then-on-change state line (D11)."""
    text = _help("hook")
    assert "never writes it" in text
    assert "once, then only on change" in text


def test_the_off_help_scopes_the_stop_hook_to_hooks_on():
    """tasks.md 5.3: `pager off --help` stays accurate - the Stop hook it
    describes speaks only when the hooks are on."""
    assert "when the hooks are on" in _help("off")


def test_the_skill_is_where_the_note_sends_readers():
    """tasks.md 5.2: the creation note says "load the sessionmail:pager skill"
    for more, so the skill must hold the more: the path, the one setting, OFF
    on a fresh install, the env override, faults, and that the commands and
    mail-guard ignore the file."""
    text = doc_surfaces()["skills/pager/SKILL.md"]
    for needle in (SETTINGS_PATH, HOOKS_TRUE, "OFF", "SESSIONMAIL_PAGER=off",
                   "faulty file", "commands", "mail-guard", "once, then only on change"):
        assert needle in text, needle


def skill_description() -> str:
    """The SKILL.md front-matter `description`, unfolded - the text injected
    into every agent context that lists the skill. Read by hand (a `>-`
    block: the indented lines after `description:`), so the test needs no
    YAML library the project does not declare."""
    lines = (ROOT / "skills/pager/SKILL.md").read_text().splitlines()
    assert lines[0] == "---", lines[:1]
    front = lines[1:lines.index("---", 1)]
    at = next(i for i, ln in enumerate(front) if ln.startswith("description:"))
    body = []
    for ln in front[at + 1:]:
        if not ln.startswith("  "):
            break
        body.append(ln.strip())
    return " ".join(body)


def test_the_skill_description_says_the_hooks_act_only_when_the_setting_is_on():
    """SM-450 do-007 (tasks.md 5.7): the description is what every agent
    reads, and the hooks are OFF until the file says so. It must name the
    file and the one setting, and must not claim that loading the plugin is
    enough for a seat to be stamped or ridden along."""
    text = skill_description()
    assert len(text) > 200, f"the description read as almost nothing: {text!r}"
    assert SETTINGS_PATH in text, text
    assert HOOKS_TRUE in text, text
    assert "only when" in text, text
    assert not re.search(r"loaded sessionmail is|stamped and ridden along", text), text


def test_the_skill_listen_bullet_agrees_with_listen_help():
    """SM-450 do-007 (tasks.md 5.7): `pager listen --help` is the authority.
    The bullet used to say `listen` reads every subscription and takes no
    arguments. Since the merge of master (refresh-001), the help is
    message-subscriptions design D10's page (tests/test_pager_listen_help.py):
    BOX and --timeout are honoured but no longer named, and the internals
    moved to the README. So the bullet says what D10 says, and names neither."""
    skill = " ".join((ROOT / "skills/pager/SKILL.md").read_text().split())
    listen = " ".join(_help("listen").split())
    assert "Reads every subscription" not in skill
    assert "takes no arguments" not in skill
    for phrase in ("Wait until mail arrives in your box, print the page, and exit.",
                   "Run it again to keep listening.",
                   "`pager stop` stops it.",
                   "subscribe to it: message subscription add BOX."):
        assert phrase in listen, f"the help no longer says {phrase!r}; re-read it"
        assert phrase in skill, f"the skill's listen bullet does not say {phrase!r}"
    for retired in ("[BOX]", "--timeout S", "Reads no subscriptions: one box, given"):
        assert retired not in listen, f"the help names {retired!r} again; re-read it"
        assert retired not in skill, f"the skill's listen bullet still names {retired!r}"


def test_pager_stop_settles_the_seat_hook_stop_is_silent_batch_says_off(seat):
    """(B) A deliberate stop - here with nothing running, as a seat standing
    down after its listener already fired - leaves the off stamp."""
    off = CliRunner().invoke(cli, ["stop"], input="")
    assert off.exit_code == 3                      # nothing listening: unchanged
    assert (stamps(seat) / _seat.OFF_STAMP).exists()
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 0 and r.stderr == ""
    b = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert context(b) == [pager.LINE_OFF]


# ── the post-124 stand-down (SM-450 do-006, install gate #3) ─────────────
#: The design call: a seat whose bounded
#: `pager listen --timeout N` returned 124 stands down with `pager off`, the
#: off stamp SM-450 already keeps. NO new exemption. REJECTED, and pinned
#: here as NOT built: (a) keying on taskmaster's FREE idle_until (roles back
#: in sessionmail); (b) the 124 writing the off stamp itself (it would
#: silence every --timeout user; SM-374 D11).


@pytest.fixture
def bounded(seat, monkeypatch):
    """The seat with its OWN session box already there, and nothing real
    reachable from `pager listen`: no box birth, no doorbell measurement of
    the machine's postfix maps, no alias map generator, no `session get idle`."""
    from sessionmail import _maildir
    box = _maildir.root() / SESSION
    for d in ("tmp", "new", "cur"):
        (box / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_seat, "doorbell", lambda address, session, env=None: (None, "not measured here"))
    monkeypatch.setenv(_seat.GEN_VAR, str(box.parent / "no-such-generator"))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    return seat


def listen_bounded():
    """`pager listen --timeout N` on this session's own box, nothing in it."""
    return CliRunner().invoke(cli, ["listen", "--timeout", "0.2"], prog_name="pager")


def test_a_bounded_listen_that_returns_124_then_pager_off_stands_the_seat_down(bounded):
    """THE SEQUENCE: listen --timeout N exits 124 -> `pager off` -> the Stop
    hook exits 0 with no nag -> the ride-along's state line is LINE_OFF."""
    s = _seat.resolve()
    heard = listen_bounded()
    assert heard.exit_code == 124, heard.output
    assert _seat.is_listening(s, SESSION) is None           # the 124 left no listener behind

    off = CliRunner().invoke(cli, ["off"], input="")
    # THE TRAP: nothing was listening, so `pager off` says so and EXITS 3 -
    # and has written the stamp anyway (pager.py `off`: write_off before the
    # pid check). Exit 3 here is expected, not a failed stand-down.
    assert off.exit_code == 3, off.output
    assert "nothing listening" in off.output
    assert (stamps(bounded) / _seat.OFF_STAMP).exists()
    assert _seat.stopped_on_purpose(s, SESSION)

    stop = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert stop.exit_code == 0 and stop.stderr == "", stop.stderr
    batch = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert context(batch) == [pager.LINE_OFF]


def test_a_124_on_its_own_writes_no_off_stamp_so_the_seat_is_still_nagged(bounded):
    """The control, and rejected design (b) pinned as NOT built: the timeout
    does not stand the seat down by itself. Without `pager off` the Stop hook
    nags and the ride-along says not listening - which is what makes the
    `pager off` step, and only that step, the stand-down."""
    s = _seat.resolve()
    assert listen_bounded().exit_code == 124
    assert not _seat.stopped_on_purpose(s, SESSION)
    stop = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert stop.exit_code == 2 and stop.stderr.strip() == pager.LINE_NOT_LISTENING
    batch = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert context(batch) == [pager.LINE_NOT_LISTENING]


def test_the_skill_and_the_off_help_say_exit_3_after_a_124_still_writes_the_stamp():
    """The trap, documented where a standing-down seat will look: the skill
    and `pager off --help` both say that exit 3 after a 124 is expected and
    the stamp is written."""
    skill = (ROOT / "skills/pager/SKILL.md").read_text()
    for text in (skill, _help("off")):
        flat = " ".join(text.split())
        low = flat.lower()
        assert "124" in flat, flat
        assert "exit 3" in low or "exits 3" in low, flat
        assert "stamp" in low and "written" in low, flat


def test_a_listener_that_died_without_pager_stop_is_still_nagged(seat):
    """No off stamp, a stale listening stamp (dead pid): the nag, as today."""
    d = stamps(seat)
    d.mkdir(parents=True, exist_ok=True)
    (d / _seat.LISTENING_STAMP).write_text("999999\n")
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING


def test_arming_clears_the_off_stamp_so_the_nag_returns_when_that_listener_goes(seat):
    s = _seat.resolve()
    CliRunner().invoke(cli, ["stop"], input="")
    assert _seat.stopped_on_purpose(s, SESSION)
    _seat.write_listening(s, SESSION, os.getpid())     # what `pager listen` does when it arms
    assert not _seat.stopped_on_purpose(s, SESSION)
    _seat.clear_listening(s, SESSION, os.getpid())     # ... and when it exits (fired, timed out)
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING


def test_stop_hook_active_still_never_loops(seat):
    payload = json.dumps({"stop_hook_active": True})
    r = CliRunner().invoke(cli, ["hook", "stop"], input=payload)
    assert r.exit_code == 0 and r.stderr == ""


# ── SM-410 review ──────────────────────────────────────────────────────────

SUCCESSOR = "66666666-7777-8888-9999-000000000000"


def test_the_off_stamp_is_per_session_a_successor_on_the_same_seat_is_still_nagged(seat, monkeypatch):
    """Ruling item 1: `pager stop` in session A settles A only. Session B on
    the SAME seat, no listener, must not inherit A's "off"."""
    assert CliRunner().invoke(cli, ["stop"], input="").exit_code == 3
    off = _seat.stamp_dir(None, SESSION) / _seat.OFF_STAMP
    assert off.exists()
    assert SESSION in off.parts, "the stamp lives under the session's own dir"
    assert not (seat / _seat.STAMPS_DIR / _seat.OFF_STAMP).exists(), "never the seat's dir"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SUCCESSOR)
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING
    b = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert pager.LINE_OFF not in context(b)


def test_a_seat_known_only_by_its_session_id_is_still_nagged(seat, monkeypatch):
    """Ruling item 4, end to end: no address, no TASKMASTER_ROLE, a session
    id alone - the seat resolves, and it is nagged as a listening seat."""
    monkeypatch.delenv("TM_AGENT_ADDRESS")
    r = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING


# ── SM-450: a settings file decides whether the hooks speak ──────────────
#
# openspec/changes/pager-settings-file, tasks.md 3.1-3.3 (slice 2). One test
# per scenario of "A settings file decides whether the hooks speak" and "A
# missing settings file is written once, off, and said once" (the delta,
# specs/pager/spec.md; design.md Traceability). The `seat` fixture wrote
# the hooks ON; each test below rewrites or removes the file it is about.
#
# Slice 2b (tasks.md 3.4-3.6, D11) made a fault's line once, then on change;
# its own tests are the "once, then on change" section at the end.

import sys  # noqa: E402

from sessionmail import _settings  # noqa: E402

AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def note_for(p: Path) -> str:
    return pager.SETTINGS_CREATED.format(path=p)


def queued(root: Path, tmp_path: Path):
    """A born seat with one message in SESSION's ride-along queue."""
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    return s, link, mid


def untouched(s, link: Path, mid: str) -> None:
    """Off means off: nothing taken, nothing marked, no stamp written."""
    assert link.exists(), "the ride-along took the mail with the hooks off"
    assert _seat.read_marks(s, SESSION).get(mid, set()) == set()
    assert not (stamps(None) / _seat.RIDE_ALONG_STAMP).exists(), "a stamp was touched"


def batch(payload: str = "{}"):
    return CliRunner().invoke(cli, ["hook", "batch"], input=payload)


def stop(payload: str = "{}"):
    return CliRunner().invoke(cli, ["hook", "stop"], input=payload)


# -- "A settings file decides whether the hooks speak" --

def test_hooks_off_the_stop_hook_is_silent(seat):
    write_settings(HOOKS_OFF)
    r = stop()
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""


def test_hooks_off_the_ride_along_says_nothing_mail_included(seat, tmp_path):
    """Q1b, RULED: off means off."""
    write_settings(HOOKS_OFF)
    s, link, mid = queued(seat, tmp_path)
    for _ in range(2):
        r = batch()
        assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""
    untouched(s, link, mid)
    assert not stamps(None).exists(), "none of the session's stamps may be written"


def test_hooks_on_a_seat_with_no_listener_is_nagged(seat):
    write_settings(HOOKS_ON)
    r = stop()
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING
    assert pager.LINE_NOT_LISTENING.startswith("pager is not listening - ")
    b = batch()
    assert b.exit_code == 0 and context(b)[-1] == pager.LINE_NOT_LISTENING


def test_the_environment_opt_out_still_wins(seat, monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    for value in ("off", "0", "No", " FALSE "):
        monkeypatch.setenv(_seat.PAGER_VAR, value)
        for r in (stop(), batch()):
            assert r.exit_code == 0 and r.stdout == "" and r.stderr == "", value
    # and in a HOME with no settings file, neither hook creates one
    settings_path().unlink()
    stop(), batch()
    assert not settings_path().exists()
    assert not settings_path().parent.exists() or not os.listdir(settings_path().parent)


@pytest.mark.parametrize("value", ["on", "1", "yes", "true", ""])
def test_no_value_of_the_environment_variable_turns_the_hooks_on(seat, monkeypatch, value):
    """D7: the file is the one enabler."""
    write_settings(HOOKS_OFF)
    monkeypatch.setenv(_seat.PAGER_VAR, value)
    r = stop()
    assert r.exit_code == 0 and r.stderr == ""
    assert batch().stdout == ""


FAULTS = [
    pytest.param(b'{"hooks": true', "not JSON", id="not-json"),
    pytest.param(b'[{"hooks": true}]', "the top level is a list, not an object", id="a-list"),
    pytest.param(b'{"hooks": "true"}', '`hooks` is "true", not true or false', id="hooks-string"),
    pytest.param(b'{"hooks": 1}', "`hooks` is 1, not true or false", id="hooks-1"),
    pytest.param(b'{"version": 1}', "no `hooks` key", id="no-hooks"),
    pytest.param(b'{"hook": true}', "no `hooks` key", id="the-typo"),
]


def _fault_line(ctx_lines: list[str], why: str) -> None:
    """ONE line: the path, "treated as off", and the reason (D1, D5)."""
    assert len(ctx_lines) == 1, ctx_lines
    (line,) = ctx_lines
    assert str(settings_path()) in line
    assert "treated as off" in line
    assert why in line
    assert not line.startswith("pager is"), "a fault gives no state line"
    assert "wrote" not in line and "hooks\": true" not in line, "never the creation note"


@pytest.mark.parametrize("data, why", FAULTS)
def test_a_faulty_settings_file_is_off_and_the_ride_along_says_so(seat, tmp_path, data, why):
    """A RECOMMENDATION, not ruled (design.md D1)."""
    p = write_settings(data)
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    s, link, mid = queued(seat, tmp_path)
    for i in range(3):
        r = batch()
        assert r.exit_code == 0, r.output
        lines = context(r)       # parses as the hook JSON, additionalContext
        # The first batch MUST say it (a silent fault is the bug this line
        # exists to prevent); the same fault again says nothing (slice 2b,
        # D11: "a second `pager hook batch` with the same fault prints
        # nothing").
        if i == 0:
            assert lines, "the first batch with a faulty file says nothing"
            _fault_line(lines, why)
            assert not any("speak" in ln for ln in lines), "no mail with a fault"
        else:
            assert r.stdout == "", "the same fault is said once"
    untouched(s, link, mid)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before, "a faulty file is never rewritten"


@pytest.mark.parametrize("data, why", FAULTS)
def test_a_faulty_settings_file_never_makes_the_stop_hook_nag(seat, data, why):
    write_settings(data)
    r = stop()
    assert r.exit_code == 0 and r.stdout == ""
    assert len(r.stderr.splitlines()) <= 1


def test_a_stranded_host_is_said_once_and_nothing_is_written(seat, tmp_path):
    """SM-873, the stranded host: the new file is absent and the old
    `~/.settings/mail/` is still a real folder (an upgrade from 0.1.0 whose
    files are not moved). The ride-along writes NOTHING, under either tree,
    and says ONE line naming both folders and the move, then nothing while
    it holds.
    Off means off: the mail is not taken. Never a crash, always exit 0."""
    import shutil
    home = Path(os.environ["HOME"])
    shutil.rmtree(home / _settings.ROOT)
    old = home / ".settings" / "mail" / "mail.json"
    old.parent.mkdir(parents=True)
    old.write_text(HOOKS_ON)
    old.chmod(0o000)              # never read: a read would be a fault, not this line
    try:
        before = {p: (p.lstat().st_mode, p.lstat().st_mtime_ns) for p in (home / ".settings").rglob("*")}
        s, link, mid = queued(seat, tmp_path)
        for i in range(2):
            r = batch()
            assert r.exit_code == 0, r.output
            if i == 0:
                want = pager.SETTINGS_STRANDED.format(old="~/.settings/mail/",
                                                      new="~/.sessionkit/mail/settings/")
                assert want in context(r), context(r)
                assert "Move the files in ~/.settings/mail/ to ~/.sessionkit/mail/settings/" in want
            else:
                assert r.stdout == "", "the same stranded host is said once"
        untouched(s, link, mid)
        assert not (home / _settings.ROOT).exists(), "nothing under the new tree"
        after = {p: (p.lstat().st_mode, p.lstat().st_mtime_ns) for p in (home / ".settings").rglob("*")}
        assert after == before, "nothing under the old tree"
        assert stop().exit_code == 0
    finally:
        old.chmod(0o600)


def _both_trees(home: Path) -> dict:
    """Every path under the old and the new settings folder: mode and mtime."""
    out = {}
    for top in (home / _settings.LEGACY_DIR, home / _settings.DIR):
        for p in [top, *top.rglob("*")]:
            out[p] = (p.lstat().st_mode, p.lstat().st_mtime_ns)
    return out


def _old_folder(home: Path, *, mail_json: bool = True) -> Path:
    """The old settings folder, REAL, its mail.json mode 000: never read."""
    old = home / _settings.LEGACY_DIR / "mail.json"
    old.parent.mkdir(parents=True)
    if mail_json:
        old.write_text(HOOKS_ON)
        old.chmod(0o000)
    return old


@pytest.mark.parametrize("new", [HOOKS_OFF, HOOKS_ON, None], ids=["new-off", "new-on", "new-no-file"])
def test_both_folders_real_say_the_copied_line_once_and_write_nothing(seat, tmp_path, new):
    """Both folders real (the old files copied, not moved): ONE line, once
    per session, whatever the new file says, with exit 0. Nothing is written
    to either folder and nothing is read from the old one. A new folder with
    no mail.json yet is stranded: still the copied line, never the stranded
    line too."""
    home = Path(os.environ["HOME"])
    settings_path().unlink(missing_ok=True)
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    if new is not None:
        write_settings(new)
    old = _old_folder(home)
    try:
        want = pager.SETTINGS_COPIED.format(old="~/.settings/mail/", new="~/.sessionkit/mail/settings/")
        assert want == "sessionmail: Remove ~/.settings/mail/ once its files are in ~/.sessionkit/mail/settings/."
        before = _both_trees(home)
        said = []
        for i in range(3):
            r = batch()
            assert r.exit_code == 0, r.output
            said += context(r)
            if i == 0:
                assert want in context(r), context(r)
        assert said.count(want) == 1, said
        assert not any(ln == pager.SETTINGS_STRANDED.format(old=_settings.SHOWN_LEGACY_DIR,
                                                            new=_settings.SHOWN_DIR) for ln in said), said
        if new != HOOKS_ON:
            assert len([ln for ln in said if ln.startswith("sessionmail:")]) == 1, said
        assert _both_trees(home) == before, "nothing written to either folder"
        if new != HOOKS_ON:                    # ON, the stop hook asks to arm (its own exit)
            assert stop().exit_code == 0
    finally:
        old.chmod(0o600)


@pytest.mark.parametrize("new", [None, HOOKS_ON], ids=["new-no-file", "new-on"])
def test_an_unsearchable_old_settings_folder_is_a_fault_never_a_crash(seat, tmp_path, new):
    """SM-877 fix-002: `~/.settings` lists but cannot be searched, so
    `stranded()` and `copied()` cannot tell (they RAISE on every CPython).
    With no new file, the ride-along writes NOTHING and says the fault line
    once, naming the folder it could not check: never a fresh OFF file (what
    3.14 wrote), never a traceback (what 3.13 gave). With a new file there,
    it is read and used, and no copied line is said. Exit 0 every time."""
    import shutil
    home = Path(os.environ["HOME"])
    shutil.rmtree(home / _settings.ROOT)
    if new is not None:
        write_settings(new)
    old = _old_folder(home)
    locked = home / ".settings"
    locked.chmod(0o444)
    try:
        if os.access(locked, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        said = []
        for _ in range(2):
            r = batch()
            assert r.exit_code == 0, r.output
            assert r.exception is None or isinstance(r.exception, SystemExit), repr(r.exception)
            said += context(r)
        copied = pager.SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
        assert copied not in said, said
        if new is None:
            want = pager.SETTINGS_FAULT.format(
                path=settings_path(),
                error="~/.settings/mail/ could not be checked (Permission denied), so nothing was written")
            assert said == [want], said                     # once, and nothing else
            assert not os.path.lexists(settings_path()), "a fresh OFF file was written"
            assert not (home / _settings.ROOT).exists(), "nothing under the new tree"
        else:
            assert not any(ln.startswith("sessionmail:") for ln in said), said
            assert settings_path().read_text() == HOOKS_ON
            assert sorted(os.listdir(settings_path().parent)) == ["mail.json"], "nothing installed"
    finally:
        locked.chmod(0o755)
        old.chmod(0o600)


def test_the_copied_line_is_taken_out_of_the_scoops_room(seat, tmp_path, monkeypatch):
    """Hooks ON with both folders real: the scoop's room is what the cap
    leaves after the copied line, counted by its characters: the line
    itself, as text, is among what the room is costed from."""
    home = Path(os.environ["HOME"])
    write_settings(HOOKS_ON)
    old = _old_folder(home)
    costed: list[list] = []
    rooms = []
    real_cost = pager._cost

    def cost(lines):
        costed.append(list(lines))
        return real_cost(lines)

    def scoop(*args, room, **kwargs):
        rooms.append(room)

    monkeypatch.setattr(pager, "_cost", cost)
    monkeypatch.setattr(pager, "_scoop", scoop)
    try:
        assert batch().exit_code == 0
        line = pager.SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
        assert rooms, "the scoop ran"
        assert any(line in lines for lines in costed), costed
        assert all(isinstance(x, str) for lines in costed for x in lines), costed
    finally:
        old.chmod(0o600)


@pytest.mark.parametrize("how", ["gone", "a-link"])
def test_a_moved_host_gets_no_copied_line(seat, how):
    """Moved: the old folder gone, or a link to the new one. No line."""
    home = Path(os.environ["HOME"])
    write_settings(HOOKS_OFF)
    if how == "a-link":
        (home / _settings.LEGACY_DIR).parent.mkdir(parents=True)
        (home / _settings.LEGACY_DIR).symlink_to(home / _settings.DIR)
    r = batch()
    assert r.exit_code == 0, r.output
    assert r.stdout == "", r.stdout


def _os_fault(kind: str) -> str:
    """Make the path unreadable in one of 2.4's OS ways; return the reason."""
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if kind == "directory":
        p.unlink(missing_ok=True)
        p.mkdir()
    elif kind == "dangling-symlink":
        p.unlink(missing_ok=True)
        p.symlink_to(p.parent / "nowhere.json")
    elif kind == "unreadable":
        write_settings(HOOKS_ON)
        p.chmod(0)
    return _settings.read().why


@pytest.mark.parametrize("kind", [
    "directory", "dangling-symlink",
    pytest.param("unreadable", marks=pytest.mark.skipif(AS_ROOT, reason="root reads mode 000")),
])
def test_a_path_that_cannot_be_read_is_a_loud_off_at_both_hooks(seat, tmp_path, kind):
    why = _os_fault(kind)
    try:
        assert why, "a fault always carries its reason"
        s, link, mid = queued(seat, tmp_path)
        r = batch()
        assert r.exit_code == 0
        _fault_line(context(r), why)
        untouched(s, link, mid)
        st = stop()
        assert st.exit_code == 0 and st.stdout == ""
        # never replaced: whatever is at the path is still there
        assert os.path.lexists(settings_path())
        if kind == "dangling-symlink":
            assert settings_path().is_symlink()
    finally:
        if kind == "unreadable":
            settings_path().chmod(0o644)


def test_unknown_keys_change_nothing(seat):
    write_settings('{"version": 7, "hooks": true, "colour": "blue"}')
    r = stop()
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING


def test_the_file_is_config_not_state(seat, monkeypatch):
    """SESSIONMAIL_HOME elsewhere: the hook reads $HOME/.sessionkit/mail/settings, and
    writes nothing about the settings under SESSIONMAIL_HOME."""
    state = Path(os.environ["SESSIONMAIL_HOME"])
    assert not str(settings_path()).startswith(str(state))
    settings_path().unlink()
    r = batch()
    assert context(r) == [note_for(settings_path())]
    assert settings_path().is_file()
    assert [p for p in state.rglob("*") if "settings" in p.name] == []


def test_the_commands_are_not_the_hooks_status_new_still_gives_the_mail(seat, tmp_path):
    """Q1c, PROPOSED: `pager status --new` behaves the same whatever the file
    says. (`pager listen` paging with the hooks off: test_pager_listen.py.)"""
    write_settings(HOOKS_OFF)
    s, link, mid = queued(seat, tmp_path)
    assert batch().stdout == ""                  # the hook: off means off
    r = CliRunner().invoke(cli, ["status", "--new"])
    assert "speak" in r.output, r.output         # the command: asked, so it answers
    assert "seen" in _seat.read_marks(s, SESSION).get(mid, set())


# -- "A missing settings file is written once, off, and said once" --

def test_success_criterion_1_a_home_with_no_settings_file(seat):
    """README Success Criterion 1: a HOME with no settings file shows the
    file created with hooks off, the note emitted exactly once, and a second
    invocation silent."""
    import shutil
    shutil.rmtree(Path(os.environ["HOME"]) / _settings.ROOT)
    assert not (Path(os.environ["HOME"]) / _settings.ROOT).exists()

    first = batch()
    assert first.exit_code == 0
    p = settings_path()
    assert json.loads(p.read_text()) == {"version": 1, "hooks": False, "sendmail": "drop"}
    assert p.read_text() == _settings.template()
    lines = context(first)
    assert lines == [note_for(p)], "the note is the batch's ONLY output"
    assert str(p) in lines[0] and '"hooks": true' in lines[0]
    assert "sessionmail:pager" in lines[0]                 # "load the skill for more"
    assert not stamps(None).exists(), "the creating run takes, marks and stamps nothing"

    second = batch()
    assert second.exit_code == 0 and second.stdout == "" and second.stderr == ""
    assert stop().exit_code == 0 and stop().stderr == ""


RACE = """
import os, sys, time
from click.testing import CliRunner
from sessionmail.pager import cli
go = sys.argv[1]
while not os.path.exists(go):
    time.sleep(0.001)
r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
sys.stdout.write(r.stdout)
sys.exit(r.exit_code)
"""


def test_two_sessions_race_to_create_it_and_exactly_one_says_the_note(seat, tmp_path):
    settings_path().unlink()
    go = tmp_path / "go"
    env = dict(os.environ)
    procs = []
    for i in range(6):
        env_i = dict(env, CLAUDE_CODE_SESSION_ID=f"{i}0000000-0000-0000-0000-000000000000")
        procs.append(subprocess.Popen([sys.executable, "-c", RACE, str(go)], env=env_i,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    go.touch()
    notes = 0
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        if out.strip():
            assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == note_for(settings_path())
            notes += 1
    assert notes == 1, "exactly one racer hands its agent the note"
    assert settings_path().read_text() == _settings.template(), "created once, and whole"
    assert [n for n in os.listdir(settings_path().parent)
            if n not in ("mail.json", _settings.SCHEMA, _settings.INSTALLED_TEMPLATE)] == []


@pytest.mark.parametrize("data", [b'{"hooks": true}', b"{}", b"\x00 not json at all \xff"])
def test_an_existing_file_is_never_replaced(seat, data):
    p = write_settings(data)
    os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    before = (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
    for _ in range(3):
        for r in (batch(), stop()):
            assert r.exit_code in (0, 2)
            assert not any(ln.startswith("sessionmail: wrote") for ln in
                           (context(r) if r.stdout.strip() else [])), "no creation note"
    assert (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) == before


def test_a_malformed_file_is_not_a_fresh_install(seat):
    """The pin: creation is the ONLY trigger. An existing
    file that is not JSON never gets the note, however many batches run."""
    p = write_settings(b"this is not json\n")
    before = p.read_bytes()
    said = set()
    for _ in range(10):
        r = batch()
        assert r.exit_code == 0
        said.update(context(r))
    assert said, "a malformed file is a fault, never a silent off"
    assert all("treated as off" in ln for ln in said), said
    assert not any("sessionmail: wrote" in ln for ln in said)
    assert p.read_bytes() == before


def test_the_stop_hook_finds_no_file_and_never_writes_one(seat):
    """D3 (PROPOSED, open question 3): the Stop hook reads, never writes."""
    settings_path().unlink()
    r = stop()
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""
    assert not settings_path().exists()


def test_a_subagents_batch_creates_nothing(seat):
    settings_path().unlink()
    r = batch(_subagent_payload())
    assert r.exit_code == 0 and r.stdout == ""
    assert not settings_path().exists()


def test_a_plain_shell_creates_nothing(tmp_path, monkeypatch):
    """D6 step 3: NoIdentity returns before the file is looked at."""
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    r = batch()
    assert r.exit_code == 0 and r.stdout == ""
    assert not settings_path().exists()


def test_the_file_cannot_be_written(seat):
    """A RECOMMENDATION: a plain file where
    `.settings` goes. OFF, one loud line with the OS error, exit 0; once the
    path can be written, the next run creates the file and says the note."""
    import shutil
    dot = Path(os.environ["HOME"]) / _settings.ROOT
    shutil.rmtree(dot)
    dot.write_text("not a directory\n")
    r = batch()
    assert r.exit_code == 0
    (line,) = context(r)
    assert str(settings_path()) in line and "treated as off" in line
    assert "Not a directory" in line or "File exists" in line, line
    assert stop().exit_code == 0 and stop().stdout == ""
    assert dot.read_text() == "not a directory\n", "never replaced"

    dot.unlink()
    again = batch()
    assert context(again) == [note_for(settings_path())]
    assert settings_path().read_text() == _settings.template()


@pytest.mark.skipif(AS_ROOT, reason="root writes into a read-only HOME")
def test_a_read_only_home_is_a_loud_off_until_it_can_be_written(seat):
    import shutil
    home = Path(os.environ["HOME"])
    shutil.rmtree(home / _settings.ROOT)
    home.chmod(0o500)
    try:
        r = batch()
        assert r.exit_code == 0
        (line,) = context(r)
        assert "treated as off" in line and "Permission denied" in line, line
        assert stop().exit_code == 0
    finally:
        home.chmod(0o755)
    assert context(batch()) == [note_for(settings_path())]


def test_a_deleted_file_is_a_fresh_install(seat):
    settings_path().unlink()
    assert context(batch()) == [note_for(settings_path())]
    assert batch().stdout == ""
    settings_path().unlink()                      # a person deleted it
    assert context(batch()) == [note_for(settings_path())]
    assert batch().stdout == ""


# ── SM-450 slice 2b: the state line once, then on change ────────────────
#
# Review case 11, RULED: the line runs once, and says something only on a
# change. tasks.md 3.4-3.6; design.md D11; one test per scenario of
# "The ride-along says its state line once, then on change" (the delta,
# specs/pager/spec.md), plus the record's own edges.

def said_path() -> Path:
    return stamps(None) / _seat.SAID_STAMP


def test_listening_is_said_once(seat):
    arm(seat)
    runs = [batch() for _ in range(3)]
    assert all(r.exit_code == 0 for r in runs)
    assert context(runs[0]) == [pager.LINE_LISTENING]
    assert runs[1].stdout == "" and runs[2].stdout == ""
    assert said_path().read_text() == pager.LINE_LISTENING, "the record holds the text said"


@pytest.mark.parametrize("how", ["returned", "SIGTERM"])
def test_a_change_is_said_listening_then_gone(seat, how):
    """The listener returns (it paged, or timed out: `pager listen` clears its
    own stamp) or is sent SIGTERM (the stamp is left behind, its pid dead)."""
    s = _seat.resolve()
    proc = subprocess.Popen(["sleep", "60"])
    try:
        _seat.write_listening(s, SESSION, proc.pid)
        assert context(batch()) == [pager.LINE_LISTENING]
        assert batch().stdout == ""
        if how == "returned":
            _seat.clear_listening(s, SESSION, proc.pid)
        else:
            proc.terminate()
            proc.wait(timeout=10)
        assert context(batch()) == [pager.LINE_NOT_LISTENING]
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_off_then_on(seat):
    """The `off` line given after `pager stop`; `pager listen` arms; the next
    batch says listening."""
    s = _seat.resolve()
    assert CliRunner().invoke(cli, ["stop"], input="").exit_code == 3
    assert context(batch()) == [pager.LINE_OFF]
    assert batch().stdout == ""
    _seat.write_listening(s, SESSION, os.getpid())       # what `pager listen` does when it arms
    assert context(batch()) == [pager.LINE_LISTENING]


def test_mail_still_rides_every_batch(seat, tmp_path):
    arm(seat)
    assert context(batch()) == [pager.LINE_LISTENING]
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    lines = context(batch())
    assert lines and all(not ln.startswith("pager is") for ln in lines), lines
    assert any("speak" in ln for ln in lines), lines
    assert not link.exists() and "seen" in _seat.read_marks(s, SESSION).get(mid, set())


def test_not_listening_is_said_once_while_the_stop_hook_still_nags(seat):
    """A RECOMMENDATION, NOT ruled (design.md open question 9).
    Pinned so that a ruling the other way shows as a visible change: see the
    next test, which flips the one switch."""
    assert pager.NOT_LISTENING_ONCE is True
    assert context(batch()) == [pager.LINE_NOT_LISTENING]
    assert batch().stdout == ""
    r = stop()
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING
    assert batch().stdout == "", "the Stop hook's nag is not a change for the ride-along"


def test_if_every_batch_is_ruled_it_is_one_switch(seat, monkeypatch):
    """The other answer to open question 9: the not-listening line on every batch
    while it holds. Every other line stays once, then on change."""
    monkeypatch.setattr(pager, "NOT_LISTENING_ONCE", False)
    for _ in range(3):
        assert context(batch()) == [pager.LINE_NOT_LISTENING]
    arm(seat)
    assert context(batch()) == [pager.LINE_LISTENING]
    assert batch().stdout == ""


def test_each_session_keeps_its_own_record(seat, monkeypatch):
    arm(seat)
    assert context(batch()) == [pager.LINE_LISTENING]            # session A
    assert batch().stdout == ""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SUCCESSOR)      # session B, same seat
    _seat.write_listening(_seat.resolve(), SUCCESSOR, os.getpid())
    assert context(batch()) == [pager.LINE_LISTENING]
    assert _seat.stamp_dir(None, SUCCESSOR) != stamps(None)
    assert (_seat.stamp_dir(None, SUCCESSOR) / _seat.SAID_STAMP).exists()


def test_hooks_off_on_purpose_keep_no_record(seat):
    write_settings(HOOKS_OFF)
    for _ in range(2):
        assert batch().stdout == ""
    assert not said_path().exists()
    write_settings(HOOKS_ON)
    assert context(batch()) == [pager.LINE_NOT_LISTENING]


def test_off_and_the_opt_out_neither_read_nor_write_the_record(seat, monkeypatch):
    """D11: with the hooks off on purpose, or SESSIONMAIL_PAGER=off, the
    record is not touched at all - and a subagent's batch, and the Stop hook,
    never reach it."""
    calls = []
    real_read, real_write = _seat.read_said, _seat.write_said
    monkeypatch.setattr(_seat, "read_said", lambda *a: calls.append("read") or real_read(*a))
    monkeypatch.setattr(_seat, "write_said", lambda *a: calls.append("write") or real_write(*a))

    write_settings(HOOKS_OFF)
    batch(), stop()
    write_settings(HOOKS_ON)
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    batch(), stop()
    monkeypatch.delenv(_seat.PAGER_VAR)
    batch(_subagent_payload())
    stop()
    assert calls == [], calls
    assert batch().stdout != "" and calls == ["read", "write"], "the hooks on: read, then write"


def test_back_on_compares_with_what_was_said_while_on(seat):
    """D11: off keeps no record, so when the hooks come back on the next batch
    compares against the last line said while they were on."""
    arm(seat)
    assert context(batch()) == [pager.LINE_LISTENING]
    write_settings(HOOKS_OFF)
    assert batch().stdout == ""
    write_settings(HOOKS_ON)
    assert batch().stdout == "", "the same line as last said: nothing to say"


def test_a_faults_line_is_said_once_then_again_when_the_error_changes(seat):
    write_settings(b'{"version": 1}')
    first = context(batch())
    _fault_line(first, "no `hooks` key")
    assert batch().stdout == ""
    write_settings(b'{"hooks": 1}')
    second = context(batch())
    _fault_line(second, "`hooks` is 1, not true or false")
    assert batch().stdout == ""
    # and fixed: the state line differs from the fault's, so it is said
    write_settings(HOOKS_ON)
    assert context(batch()) == [pager.LINE_NOT_LISTENING]
    assert batch().stdout == ""



# -- session-settings 3.1: an old host file that drifted is said once --
# (a review of d50c9d0)

def old_settings_path() -> Path:
    return Path(os.environ["HOME"]) / ".session" / "mail" / "settings.json"


def write_old_settings(data: str) -> Path:
    p = old_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    return p


def drift_line() -> str:
    return pager.SETTINGS_DRIFT.format(old=old_settings_path(), path=settings_path())


def test_a_drifted_old_file_is_said_once_before_the_state_line(seat):
    write_settings(HOOKS_ON)
    write_old_settings(HOOKS_OFF)
    assert context(batch()) == [drift_line(), pager.LINE_NOT_LISTENING]
    assert batch().stdout == "", "both lines said: nothing new"
    assert not old_settings_path().is_symlink(), "a differing old file is kept"


def test_a_drifted_old_file_is_said_even_with_the_hooks_off(seat):
    """The off may BE the drift: the old file says true, the new one false."""
    write_settings(HOOKS_OFF)
    write_old_settings(HOOKS_ON)
    assert context(batch()) == [drift_line()]
    assert batch().stdout == ""


def test_the_drift_record_leaves_the_state_lines_record_alone(seat):
    write_settings(HOOKS_ON)
    assert context(batch()) == [pager.LINE_NOT_LISTENING]
    write_old_settings(HOOKS_OFF)
    assert context(batch()) == [drift_line()], "the state line is not said again"
    arm(seat)
    assert context(batch()) == [pager.LINE_LISTENING], "nor the drift line"


def test_old_bytes_made_equal_are_linked_and_nothing_is_said(seat):
    write_settings(HOOKS_ON)
    write_old_settings(HOOKS_OFF)
    assert drift_line() in context(batch())
    write_old_settings(HOOKS_ON)
    assert batch().stdout == ""
    assert old_settings_path().is_symlink()
    assert old_settings_path().resolve() == settings_path().resolve()


def test_an_unwritable_record_repeats_the_line_and_does_not_raise(seat):
    """A repeat is the safe failure (D11): a directory where `said` goes can
    be neither read nor replaced."""
    arm(seat)
    said_path().mkdir(parents=True)
    for _ in range(3):
        r = batch()
        assert r.exit_code == 0 and r.exception is None
        assert context(r) == [pager.LINE_LISTENING]
    assert said_path().is_dir()


@pytest.mark.skipif(AS_ROOT, reason="root writes into a read-only directory")
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "refresh-001 (merge of master 71b689a): SM-374 do-009 (c89420a) retired the "
    "unborn seat - hook batch no longer gates on <seat>/subscriptions/ - so every "
    "seat with a session now scoops and `_seat.touch`es the ride-along stamp, which "
    "raises PermissionError on a read-only stamps dir (exit 1). That touch was "
    "already unguarded for every born seat at 4a9bf17 and at master; this test's "
    "premise (the said record is the only write) is gone. The record's own failure "
    "stays pinned by test_an_unwritable_record_repeats_the_line_and_does_not_raise. "
    "FOLLOW-UP, not fixed on a merge commit: guard the touch so batch keeps its "
    "'Exit: 0 always' contract."))
def test_a_read_only_stamps_dir_repeats_the_line_and_does_not_raise(seat):
    """An unborn seat touches no ride-along stamp, so the record is the only
    write: with its directory read-only, the line repeats and nothing raises.
    (Unborn seats retired with SM-374 do-009: see the xfail reason.)"""
    (seat / "subscriptions").rmdir()
    stamps(None).mkdir(parents=True)
    stamps(None).chmod(0o500)
    try:
        for _ in range(2):
            r = batch()
            assert r.exit_code == 0 and r.exception is None
            assert context(r) == [pager.LINE_NOT_LISTENING]
    finally:
        stamps(None).chmod(0o755)
    assert [p.name for p in stamps(None).iterdir()] == [], "no temp file left behind"


def test_a_bare_seat_with_no_session_keeps_no_record(seat, monkeypatch):
    """Session `-`: stamp_dir would give it a bucket the whole seat shares, so
    it keeps no record and says its line every time, as before (tasks.md 3.4)."""
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    assert _seat.session_id() == "-"
    for _ in range(2):
        assert context(batch()) == [pager.LINE_NOT_LISTENING]
    assert not (seat / _seat.STAMPS_DIR / "-" / _seat.SAID_STAMP).exists()
    _seat.write_said(_seat.resolve(), "-", "x")
    assert _seat.read_said(_seat.resolve(), "-") is None
    assert not (seat / _seat.STAMPS_DIR / "-").exists()


def test_the_record_round_trips_and_never_raises(seat):
    s = _seat.resolve()
    assert _seat.read_said(s, SESSION) is None
    _seat.write_said(s, SESSION, pager.LINE_NOT_LISTENING)
    assert _seat.read_said(s, SESSION) == pager.LINE_NOT_LISTENING
    assert [p.name for p in stamps(None).iterdir()] == [_seat.SAID_STAMP], "whole, no temp left"
    said_path().write_bytes(b"\xff\xfe not utf-8")
    assert _seat.read_said(s, SESSION) is None          # unreadable: say it again


# -- session-settings 3.1: a host still on the OLD path keeps its hooks on --

def test_a_host_on_the_old_path_is_converted_and_its_mail_rides(seat, tmp_path):
    """The first ride-along after the move CONVERTS `~/.session/mail/
    settings.json`: the hooks stay ON, no first-run note is said, and the
    mail rides in that same batch. The old path is a symlink after it."""
    import shutil
    shutil.rmtree(Path(os.environ["HOME"]) / _settings.ROOT, ignore_errors=True)
    old = Path(os.environ["HOME"]) / ".session" / "mail" / "settings.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text('{"version": 1, "hooks": true}\n')
    queued(seat, tmp_path)
    r = batch()
    assert r.exit_code == 0, r.output
    lines = context(r)
    assert note_for(settings_path()) not in lines, "a conversion is not a creation"
    assert any("speak" in ln for ln in lines), lines
    assert settings_path().read_text() == '{"version": 1, "hooks": true}\n'
    assert old.is_symlink() and old.resolve() == settings_path().resolve()


# ── SM-640 do-003: wake carry - the UserPromptSubmit gate (R6-R9, E2, E3) ────
#
# SM-632 option D, Q3: the listener's page writes a CARRY stamp when a paged
# verdict carries (tests/test_pager_listen_judges.py pins that end); the
# notice turn's UserPromptSubmit runs this SAME hook, and scoops only if it
# unlinks the stamp. A typed prompt carries only with `set carry-prompt on`
# (E2, RULED: off by default), and `set carry off` kills both (O1, the kill
# switch wins). A turn start that does not carry is a strict no-op (O2).
# G1 (findings/2030-10-09-g1-midturn-notice.md, CC 2.1.291): a mid-turn
# notice's UPS fires AFTER that boundary's PostToolBatch - pinned below.

from sessionmail import rules as _rules   # noqa: E402

NOTICE = ("<task-notification>\n<task-id>b0ffo0l2b</task-id>\n<status>completed</status>\n"
          "<summary>Background command \"pager listen\" completed (exit code 0)</summary>\n"
          "</task-notification>")


def _ups(prompt: str, **extra) -> str:
    """A UserPromptSubmit payload as Claude Code 2.1.291 sends it (G1: the
    same keys for a typed prompt and a notice; only `prompt` differs)."""
    return json.dumps({"session_id": SESSION, "transcript_path": f"/tmp/x/{SESSION}.jsonl",
                       "cwd": "/tmp", "permission_mode": "bypassPermissions",
                       "prompt_id": "145c2c46-8c92-4f7f-8c73-933837841772",
                       "hook_event_name": "UserPromptSubmit", "prompt": prompt, **extra})


def _ptb() -> str:
    return json.dumps({"session_id": SESSION, "hook_event_name": "PostToolBatch", "tool_calls": []})


def _said(result, event: str) -> list[str]:
    """What a hook run handed the model, checking it named EVENT."""
    if not result.stdout.strip():
        return []
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == event
    return out["additionalContext"].splitlines()


def _carry_stamp(root: Path) -> Path:
    return stamps(root) / _seat.CARRY_STAMP


def _stamp_carry() -> Path:
    """What `page()` leaves when a paged verdict carries."""
    return _seat.write_carry(_seat.resolve(), SESSION)


@pytest.fixture
def locked(tmp_path, monkeypatch):
    """The host's locked file, in the sandbox: where `set carry` lives."""
    path = tmp_path / "etc" / "rules.locked"
    monkeypatch.setattr(_rules, "locked_path", lambda: path)

    def write(text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path
    return write


def _hook(payload: str):
    return CliRunner().invoke(cli, ["hook", "batch"], input=payload)


def test_the_stamp_helpers_claim_once(seat):
    s = _seat.resolve()
    assert _seat.claim_carry(s, SESSION) is False           # none written: lost, silent
    path = _seat.write_carry(s, SESSION)
    assert path == _carry_stamp(seat) and path.exists()
    assert _seat.claim_carry(s, SESSION) is True
    assert _seat.claim_carry(s, SESSION) is False           # the loser gets ENOENT
    assert _seat.claim_carry(None, "-") is False            # nothing to key on: never raises


def test_a_notice_with_the_stamp_carries_the_mail_and_takes_the_stamp(seat, tmp_path):
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    _stamp_carry()
    r = _hook(_ups(NOTICE))
    assert r.exit_code == 0
    lines = _said(r, "UserPromptSubmit")
    assert any("speak" in ln for ln in lines), lines
    assert lines[-1] == pager.LINE_NOT_LISTENING             # the re-arm prompt, as the batch says it
    assert not _carry_stamp(seat).exists()
    assert not link.exists() and "seen" in _seat.read_marks(s, SESSION).get(mid, set())
    assert (stamps(seat) / _seat.RIDE_ALONG_STAMP).exists()  # the ride-along did run


def test_a_notice_without_the_stamp_is_a_strict_no_op(seat, tmp_path):
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    r = _hook(_ups(NOTICE))
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""
    assert link.exists() and _seat.read_marks(s, SESSION).get(mid, set()) == set()
    assert not (stamps(seat) / _seat.RIDE_ALONG_STAMP).exists()
    assert _seat.read_said(s, SESSION) is None or _seat.read_said(s, SESSION) == ""


def test_two_notice_runs_and_one_stamp_give_the_mail_once(seat, tmp_path):
    """Exactly once: the unlink is the claim. The second run gets ENOENT
    and says nothing, mail and state line both."""
    s = _seat.resolve()
    _queue_one(s, tmp_path)
    _stamp_carry()
    first, second = _hook(_ups(NOTICE)), _hook(_ups(NOTICE))
    assert any("speak" in ln for ln in _said(first, "UserPromptSubmit"))
    assert second.stdout == ""


def test_the_re_arms_batch_after_a_carry_gives_nothing_twice(seat, tmp_path):
    """SC1's shape in miniature: the wake turn carries; the agent's re-arm
    is a tool call, and its PostToolBatch finds the queue empty."""
    s = _seat.resolve()
    _queue_one(s, tmp_path)
    _stamp_carry()
    assert any("speak" in ln for ln in _said(_hook(_ups(NOTICE)), "UserPromptSubmit"))
    rearm = _hook(_ptb())
    assert not any("speak" in ln for ln in _said(rearm, "PostToolBatch"))


def test_g1_order_post_tool_batch_then_ups_in_one_boundary_gives_once(seat, tmp_path):
    """G1, measured on CC 2.1.291: a notice that lands MID-turn fires its
    UserPromptSubmit ~70 ms AFTER that boundary's PostToolBatch. The batch
    drops the stamp (R9) and gives the mail; the UPS finds no stamp and is
    silent. Once, at the same boundary."""
    s = _seat.resolve()
    _queue_one(s, tmp_path)
    _stamp_carry()
    batch = _hook(_ptb())
    assert any("speak" in ln for ln in _said(batch, "PostToolBatch"))
    assert not _carry_stamp(seat).exists()
    ups = _hook(_ups(NOTICE))
    assert ups.exit_code == 0 and ups.stdout == ""


def test_a_batch_drops_a_stale_stamp_with_nothing_queued(seat):
    _stamp_carry()
    r = _hook(_ptb())
    assert r.exit_code == 0
    assert not _carry_stamp(seat).exists()


def test_a_subagents_batch_leaves_the_stamp_for_the_seat(seat):
    """E3: a subagent's batch is not the seat's; it touches no stamp of the
    seat's, the carry stamp included."""
    _stamp_carry()
    r = _hook(_subagent_payload())
    assert r.exit_code == 0 and r.stdout == ""
    assert _carry_stamp(seat).exists()


def test_a_subagents_ups_never_carries_and_keeps_the_stamp(seat, tmp_path):
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    _stamp_carry()
    r = _hook(_ups(NOTICE, agent_id="a81b552b29e268608", agent_type="general-purpose"))
    assert r.exit_code == 0 and r.stdout == ""
    assert _carry_stamp(seat).exists() and link.exists()
    assert _seat.read_marks(s, SESSION).get(mid, set()) == set()


@pytest.mark.parametrize("settings", [HOOKS_OFF, '{"version": 1, "hooks": "yes"}\n', None])
def test_hooks_not_on_a_notice_is_silent_takes_nothing_writes_nothing(seat, tmp_path, settings):
    """Hooks off, a faulty file, or no file at all: silent as today. The gate
    READS the settings file and never creates it (O2): only a batch does."""
    if settings is None:
        settings_path().unlink()
    else:
        write_settings(settings)
    s = _seat.resolve()
    link, _ = _queue_one(s, tmp_path)
    _stamp_carry()
    r = _hook(_ups(NOTICE))
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == ""
    assert link.exists() and _carry_stamp(seat).exists()
    assert settings_path().exists() is (settings is not None)


def test_the_pager_opted_out_a_notice_is_silent(seat, tmp_path, monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    _stamp_carry()
    r = _hook(_ups(NOTICE))
    assert r.exit_code == 0 and r.stdout == ""
    assert _carry_stamp(seat).exists()


def test_a_typed_prompt_does_not_carry_by_default(seat, tmp_path, locked):
    """E2, RULED: no carry on a human-typed prompt by default.
    It takes nothing, not even a waiting stamp."""
    s = _seat.resolve()
    link, _ = _queue_one(s, tmp_path)
    _stamp_carry()
    r = _hook(_ups("what is in my mail?"))
    assert r.exit_code == 0 and r.stdout == ""
    assert link.exists() and _carry_stamp(seat).exists()


def test_a_typed_prompt_carries_with_carry_prompt_on(seat, tmp_path, locked):
    """R8: with the setting on, a typed prompt runs the same scoop, and
    needs no stamp (the README's Design note)."""
    locked("set carry-prompt on\n")
    s = _seat.resolve()
    link, mid = _queue_one(s, tmp_path)
    r = _hook(_ups("what is in my mail?"))
    lines = _said(r, "UserPromptSubmit")
    assert any("speak" in ln for ln in lines), lines
    assert not link.exists() and "seen" in _seat.read_marks(s, SESSION).get(mid, set())


@pytest.mark.parametrize("text", ["set carry-prompt off\n", "set carry-prompt on\nset carry off\n"])
def test_a_typed_prompt_is_silent_with_the_switch_off_or_carry_killed(seat, tmp_path, locked, text):
    """O1: `set carry off` is the host kill switch, typed prompts included."""
    locked(text)
    link, _ = _queue_one(_seat.resolve(), tmp_path)
    r = _hook(_ups("hello"))
    assert r.exit_code == 0 and r.stdout == "" and link.exists()


def test_a_typed_prompt_that_mentions_a_notice_is_still_typed(seat, tmp_path, locked):
    """Only a prompt that STARTS with the notice tag is a notice."""
    _queue_one(_seat.resolve(), tmp_path)
    _stamp_carry()
    r = _hook(_ups("why did I get a <task-notification> just now?"))
    assert r.stdout == "" and _carry_stamp(seat).exists()


def test_ups_and_post_tool_batch_run_the_same_scoop(seat, tmp_path, monkeypatch):
    """R6, ONE code path: both events reach `_scoop`, the one
    renderer and the one claim. Spy on it and run each event once."""
    calls: list[str] = []
    real = pager._scoop

    def spy(*args, **kwargs):
        calls.append(json.loads(spy.payload)["hook_event_name"])
        return real(*args, **kwargs)
    monkeypatch.setattr(pager, "_scoop", spy)
    for spy.payload in (_ptb(), _ups(NOTICE)):
        _stamp_carry()
        _hook(spy.payload)
    assert calls == ["PostToolBatch", "UserPromptSubmit"]


def test_a_carried_verdict_says_carry_in_the_line_why_and_the_record(seat):
    """R10, the pager half: the action that applied reads `carry`; `.action`
    stays `wake`, which is what decides."""
    v = _rules.Verdict("wake", "carry! from:$operator", "built-in", 1, False, True)
    assert pager.action_text(v) == "carry"
    assert pager.why_lines([v]) == ["pager: carry by `carry! from:$operator` (built-in)"]
    s = _seat.resolve()
    assert pager.record_verdict(s, SESSION, "<c1@x.invalid>", v, pager.LISTEN_BY)
    (rec,) = pager.verdicts_for(s, SESSION, "c1@x.invalid")
    assert rec["carry"] is True and rec["action"] == "wake"
    assert pager.verdict_of(rec) == v
    assert pager.why_text(rec).startswith("carry by `carry! from:$operator` (the built-in default)")
    plain = _rules.Verdict("wake", "wake to:$me", "built-in", 6)
    assert pager.action_text(plain) == "wake"
    assert pager.record_verdict(s, SESSION, "<w1@x.invalid>", plain, pager.LISTEN_BY)
    (rec,) = pager.verdicts_for(s, SESSION, "w1@x.invalid")
    assert "carry" not in rec and pager.verdict_of(rec).carry is False
