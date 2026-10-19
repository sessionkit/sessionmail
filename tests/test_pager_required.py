"""SM-726 do-005 (design.md (l4), (l5) and (f)): the deaf check, and the
`pager_required` setting that lets it speak.

The deaf check always runs, and says nothing unless the seat has set
`pager_required`.

- The Stop and PostToolBatch hooks ALWAYS run the deaf check, `"hooks": false`
  included. In order: required? muted? grace (~10 s)? listening? else DEAF,
  with the reason - the monitor did not arm (`monitor_missing`, naming
  tengu_amber_sentinel), was ended, or none was expected (headless `-p`, or
  the switch off) - always ending with the two ways out.
- With `"hooks": false` the DEAF line is the ONLY thing the hooks say.
- `pager_required` is read through the layered reader (host, seat, session;
  the most specific wins); `SESSIONMAIL_PAGER_REQUIRED` (1/true/on, 0/false/
  off) wins over every layer in BOTH directions; other values are ignored and
  said. `SESSIONMAIL_PAGER=off` beats required.
- `pager status` has a `required:` line with its source, and the (l5)
  no-monitor sentence when nothing listens.

Hermetic: conftest gives each test its own HOME and clears SESSIONMAIL_*; `ps`
and claude's age are injected, so no real claude or listener is consulted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _layer, _seat, _settings, pager
from sessionmail.pager import cli

ROOT = Path(__file__).resolve().parents[1]
SESSION = "11111111-2222-3333-4444-555555555555"
SEAT = "proj+task.worker"
CLAUDE = 7_000_001           # a claude that exists only in the injected `ps` table
INTERACTIVE = f"claude --session-id {SESSION}"
HOOKS_ON = '{"version": 1, "hooks": true}\n'
HOOKS_OFF = '{"version": 1, "hooks": false}\n'
MONITOR_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor"


# ── the rig ──────────────────────────────────────────────────────────────

def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def host(data) -> Path:
    """The host file. A map gains `"version": 1, "hooks": false` unless it
    says otherwise, so a test about `pager_required` is never a settings
    fault by accident."""
    if isinstance(data, dict):
        data = {"version": 1, "hooks": False, **data}
    return write(_settings.path(), data)


def seat_layer(data, name: str = "mail.json") -> Path:
    return write(_layer.settings_dir(SEAT) / name, data)


def session_layer(data, name: str = "mail.json") -> Path:
    return write(_layer.session_settings_dir(SESSION) / name, data)


class Seat:
    """One seat, one session, one claude - each part set by the test."""

    def __init__(self, monkeypatch, tmp_path) -> None:
        self.mp = monkeypatch
        monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
        monkeypatch.setenv("TM_AGENT_ADDRESS", SEAT)
        monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("EMAIL", raising=False)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        # The plugin that runs the hooks is this repo: its root monitors/.
        monkeypatch.setenv(_seat.PLUGIN_ROOT_VAR, str(ROOT))
        self.claude(INTERACTIVE)
        self.age(600.0)
        monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
        self.gate(True)
        host(HOOKS_OFF)

    def claude(self, command: str | None, extra: dict[int, str] | None = None) -> None:
        """This session's claude runs COMMAND (None: claude is not running)."""
        cmd = {} if command is None else {CLAUDE: command}
        cmd.update(extra or {})
        ppid = {p: (CLAUDE if p != CLAUDE else 1) for p in cmd}
        self.mp.setattr(_seat, "_ps_table", lambda: (dict(ppid), dict(cmd)))
        self.mp.setattr(_seat, "claude_for",
                        lambda session, ps=None, me=None, live=None:
                        None if command is None else CLAUDE)

    def age(self, seconds: float | None) -> None:
        self.mp.setattr(_seat, "claude_age", lambda pid: seconds)

    def gate(self, value) -> None:
        path = Path(os.environ["HOME"]) / ".claude.json"
        if value is None:
            path.unlink(missing_ok=True)
        else:
            write(path, {"cachedGrowthBookFeatures": {"tengu_amber_sentinel": value}})

    def required(self, value: str | None = "1") -> None:
        if value is None:
            self.mp.delenv(_seat.REQUIRED_VAR, raising=False)
        else:
            self.mp.setenv(_seat.REQUIRED_VAR, value)

    @property
    def stamps(self) -> Path:
        return _seat.stamp_dir(None, SESSION)

    def arm(self, pid: int | None = None) -> None:
        write(self.stamps / _seat.LISTENING_STAMP, str(pid or os.getpid()))

    def mute(self) -> None:
        write(self.stamps / _seat.OFF_STAMP, "")

    def monitor_was_armed(self) -> None:
        write(self.stamps / _seat.MONITOR_ARMED_STAMP, "")


@pytest.fixture
def s(monkeypatch, tmp_path) -> Seat:
    return Seat(monkeypatch, tmp_path)


def stop(payload: dict | None = None):
    return CliRunner().invoke(cli, ["hook", "stop"], input=json.dumps(payload or {}))


def batch(payload: dict | None = None):
    return CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(payload or {}))


def said(result) -> list[str]:
    """What a batch hook hands the model."""
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].splitlines()


def missing(gate: str = "true") -> str:
    return " ".join(pager.DEAF_MONITOR_MISSING.replace("{gate}", gate).split())


def not_listening(why: str) -> str:
    return " ".join(pager.DEAF_NOT_LISTENING.replace("{why}", why).split())


def assert_deaf_from_both(line: str) -> None:
    r = stop()
    assert r.exit_code == 2, r.output
    assert r.stderr.strip() == line
    assert said(batch()) == [line]


def assert_silent_from_both() -> None:
    r = stop()
    assert r.exit_code == 0 and r.stderr == "" and r.stdout == "", r.output
    r = batch()
    assert r.exit_code == 0 and r.stdout == "", r.output


# ── criterion 1: the matrix, with "hooks": false ─────────────────────────

def test_required_interactive_no_listener_past_grace_is_deaf_from_both_hooks(s):
    s.required()
    assert not _settings.read().hooks
    assert_deaf_from_both(missing("true"))


def test_the_missing_line_names_the_gate_and_monitor_missing_and_both_ways_out(s):
    s.required()
    line = stop().stderr.strip()
    assert line.startswith("pager: DEAF")
    assert "monitor_missing" in line and "tengu_amber_sentinel=true" in line
    assert line.endswith(pager.LINE_WAYS_OUT)
    assert "run `pager listen` in the background" in line and "restart the seat" in line


@pytest.mark.parametrize("value,shown", [(False, "false"), (None, "unknown"), ("x", "unknown")])
def test_the_gate_is_named_as_read_or_unknown(s, value, shown):
    s.required()
    s.gate(value)
    assert stop().stderr.strip() == missing(shown)


def test_claude_config_dir_moves_where_the_gate_is_read(s, monkeypatch, tmp_path):
    s.required()
    write(tmp_path / "cfg" / ".claude.json",
          {"cachedGrowthBookFeatures": {"tengu_amber_sentinel": False}})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert stop().stderr.strip() == missing("false")


def test_not_required_is_silent(s):
    s.required(None)
    assert_silent_from_both()


def test_muted_is_silent(s):
    s.required()
    s.mute()
    assert_silent_from_both()


def test_a_muted_monitor_that_still_runs_is_silent(s):
    s.required()
    s.monitor_was_armed()
    s.mute()
    s.arm()
    s.mp.setattr(_seat, "_ps_one", lambda pid: ({pid: CLAUDE}, {pid: MONITOR_CMD}))
    assert_silent_from_both()


def ended() -> str:
    return " ".join(pager.DEAF_MONITOR_ENDED.split())


def test_after_kill_the_monitor_was_ended_not_muted(s):
    """design.md (l4) step 5: "monitor expected but dead, after `--kill`:
    the pager monitor was ended". `pager stop --kill` writes the `off` stamp
    as it ends the monitor (do-003), so the stamp alone must not hide it:
    there is nothing left for a mute to mute. (Review of do-005.)"""
    s.required()
    s.monitor_was_armed()
    s.mute()
    assert_deaf_from_both(ended())


def test_after_kill_in_the_grace_is_still_silent(s):
    s.required()
    s.monitor_was_armed()
    s.mute()
    s.age(1.0)
    assert_silent_from_both()


def test_a_muted_headless_seat_with_a_monitor_stamp_is_silent(s):
    """No monitor is expected on `-p`, so the stamp is a plain mute there."""
    s.required()
    s.claude(f"claude -p --session-id {SESSION}")
    s.monitor_was_armed()
    s.mute()
    assert_silent_from_both()


@pytest.mark.parametrize("age", [0.0, 3.0, 9.0])
def test_within_the_grace_is_silent(s, age):
    s.required()
    s.age(age)
    assert_silent_from_both()


def test_the_grace_ends_at_ten_seconds(s):
    s.required()
    s.age(_seat.GRACE_SECONDS)
    assert stop().exit_code == 2


def test_claude_not_running_yet_is_silent(s):
    s.required()
    s.claude(None)
    assert_silent_from_both()


def test_an_unknown_age_does_not_hide_deafness(s):
    s.required()
    s.age(None)
    assert stop().exit_code == 2


def test_a_live_hand_listener_is_silent(s):
    s.required()
    s.arm()
    assert_silent_from_both()


def test_a_live_monitor_is_silent(s):
    s.required()
    s.arm()
    s.mp.setattr(_seat, "_ps_one", lambda pid: ({pid: CLAUDE}, {pid: MONITOR_CMD}))
    assert_silent_from_both()


def test_a_stale_listening_stamp_is_not_a_listener(s):
    s.required()
    s.arm(pid=2_999_999)                 # no such process
    assert stop().stderr.strip() == missing()


def test_a_monitor_that_armed_and_is_gone_was_ended(s):
    s.required()
    s.monitor_was_armed()
    line = stop().stderr.strip()
    assert line == " ".join(pager.DEAF_MONITOR_ENDED.split())
    assert "was ended" in line and "monitor_missing" not in line
    assert line.endswith(pager.LINE_WAYS_OUT)


@pytest.mark.parametrize("switch_off,source", [
    (lambda mp: mp.setenv(_seat.MONITOR_VAR, "off"), "SESSIONMAIL_MONITOR=off"),
    (lambda mp: host('{"version": 1, "hooks": false, "monitor": false}\n'), '"monitor": false in'),
], ids=["seat", "host"])
def test_switched_off_is_the_plain_not_listening_reason(s, switch_off, source):
    s.required()
    switch_off(s.mp)
    line = stop().stderr.strip()
    assert line.startswith("pager: DEAF - pager is not listening")
    assert "the pager monitor is switched off" in line and source in line
    assert "monitor_missing" not in line and "tengu_amber_sentinel" not in line
    assert line.endswith(pager.LINE_WAYS_OUT)


def test_a_plugin_with_no_monitor_is_the_plain_reason(s, monkeypatch, tmp_path):
    s.required()
    write(tmp_path / "plug" / "monitors" / "monitors.json", "[]")
    monkeypatch.setenv(_seat.PLUGIN_ROOT_VAR, str(tmp_path / "plug"))
    assert stop().stderr.strip() == not_listening("this plugin runs no pager monitor")
    monkeypatch.delenv(_seat.PLUGIN_ROOT_VAR)
    assert stop().stderr.strip() == not_listening("this plugin runs no pager monitor")


def test_the_stop_hook_never_loops(s):
    s.required()
    r = stop({"stop_hook_active": True})
    assert r.exit_code == 0 and r.stderr == ""


def test_a_subagents_batch_says_nothing(s):
    s.required()
    assert batch({"agent_id": "a1"}).stdout == ""


def test_with_the_hooks_off_deaf_is_the_only_thing_said_and_nothing_is_taken(s):
    s.required()
    r = batch()
    assert said(r) == [missing()]
    assert not (s.stamps / _seat.RIDE_ALONG_STAMP).exists(), "off: no ride-along stamp"
    assert not (s.stamps / _seat.SAID_STAMP).exists(), "off: the state record is untouched"


def test_with_the_hooks_off_the_batch_says_it_once_then_only_on_change(s):
    s.required()
    assert said(batch()) == [missing()]
    assert said(batch()) == [], "said once"
    assert stop().exit_code == 2, "the Stop hook still nags at every turn end"
    s.arm()
    assert said(batch()) == [], "listening now: nothing to say"
    (s.stamps / _seat.LISTENING_STAMP).unlink()
    assert said(batch()) == [missing()], "deaf again: said again"
    s.monitor_was_armed()
    assert said(batch()) == [" ".join(pager.DEAF_MONITOR_ENDED.split())], "a new reason is a change"


def test_a_faulty_settings_file_is_off_and_the_deaf_line_rides_with_its_note(s):
    s.required()
    host("not json")
    lines = said(batch())
    assert lines[-1] == missing() and "treated as off" in lines[0]


# ── with "hooks": true, DEAF replaces not-listening on a required seat ──

def test_hooks_on_required_the_deaf_line_replaces_not_listening(s):
    host(HOOKS_ON)
    s.required()
    assert_deaf_from_both(missing())


def test_hooks_on_not_required_keeps_todays_lines(s):
    host(HOOKS_ON)
    r = stop()
    assert r.exit_code == 2 and r.stderr.strip() == pager.LINE_NOT_LISTENING
    assert said(batch()) == [pager.LINE_NOT_LISTENING]


def test_hooks_on_required_in_the_grace_says_no_state_line(s):
    host(HOOKS_ON)
    s.required()
    s.age(1.0)
    assert_silent_from_both()


def test_hooks_on_required_and_listening_keeps_the_listening_line(s):
    host(HOOKS_ON)
    s.required()
    s.arm()
    assert stop().exit_code == 0
    assert said(batch()) == [pager.LINE_LISTENING]


def test_hooks_on_required_and_muted_keeps_the_muted_line(s):
    host(HOOKS_ON)
    s.required()
    s.mute()
    assert stop().exit_code == 0
    assert said(batch()) == [pager.LINE_OFF]


def test_hooks_on_required_after_kill_says_ended_not_muted(s):
    host(HOOKS_ON)
    s.required()
    s.monitor_was_armed()
    s.mute()
    assert_deaf_from_both(ended())


def test_hooks_on_not_required_after_kill_names_the_ended_monitor(s):
    """SM-726 do-008 (verify-001 F1): not required, a monitor armed, the
    `off` stamp written by `pager stop --kill`, and nothing listens. The line
    is NOT the muted one ("`pager unmute` resumes" is wrong advice: there is
    nothing to unmute); it names the ended monitor and the two ways out. A
    state line, not DEAF: the Stop hook stays silent, and the batch says it
    once, then only on change."""
    host(HOOKS_ON)
    s.monitor_was_armed()
    s.mute()
    r = stop()
    assert r.exit_code == 0 and r.stderr == "", r.output
    assert said(batch()) == [pager.LINE_MONITOR_ENDED]
    assert said(batch()) == [], "a state line: said once"
    line = pager.LINE_MONITOR_ENDED
    assert "pager monitor was ended" in line and line.endswith(pager.LINE_WAYS_OUT)
    assert "unmute" not in line and "DEAF" not in line
    s.arm()
    assert said(batch()) == [pager.LINE_OFF], "a hand listen, muted: a change, said"


def test_hooks_on_not_required_muted_live_monitor_keeps_the_muted_line(s):
    """The monitor armed AND still runs, muted: `pager unmute` does resume
    it, so the muted line stands (do-008 changes only the ended case)."""
    host(HOOKS_ON)
    s.monitor_was_armed()
    s.arm()
    s.mute()
    assert said(batch()) == [pager.LINE_OFF]


def test_hooks_on_not_required_muted_with_no_monitor_ever_keeps_the_muted_line(s):
    """A hand listener stopped on purpose, no monitor ever armed: today's
    muted line - nothing was ended that the seat did not end itself."""
    host(HOOKS_ON)
    s.mute()
    assert said(batch()) == [pager.LINE_OFF]


# ── criterion 2: headless (`-p`) ─────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    f"claude -p --session-id {SESSION} do the thing",
    f"claude --print --session-id {SESSION}",
    f"claude --print=text --session-id {SESSION}",
    f"/Users/x/.local/share/claude/versions/2.1.296 -p --session-id {SESSION}",
])
def test_a_headless_argv_never_gives_monitor_missing(s, argv):
    s.required()
    s.claude(argv)
    line = stop().stderr.strip()
    assert line == not_listening("this is a headless seat (`claude -p`), which runs no monitor")
    assert "monitor_missing" not in line and "tengu_amber_sentinel" not in line
    assert said(batch()) == [line]


def test_a_headless_seat_that_had_a_monitor_stamp_is_still_not_called_ended(s):
    """The ended reason needs an EXPECTED monitor; `-p` never expects one."""
    s.required()
    s.claude(f"claude -p --session-id {SESSION}")
    s.monitor_was_armed()
    assert "was ended" not in stop().stderr


def test_a_hand_arm_on_a_headless_seat_silences_it(s):
    s.required()
    s.claude(f"claude -p --session-id {SESSION}")
    assert stop().exit_code == 2
    s.arm()
    assert_silent_from_both()


@pytest.mark.parametrize("command,headless", [
    ("claude", False),
    (f"claude --session-id {SESSION} --model opus", False),
    ("claude --permission-mode plan", False),
    ("claude -p", True),
    ("claude --print", True),
    ("claude --print=json", True),
    ("claude --printer", False),
    ("claude -pv", False),
])
def test_headless_reads_the_argv(command, headless):
    assert _seat.headless(command) is headless


@pytest.mark.parametrize("text,seconds", [
    ("00:05", 5.0), ("01:02", 62.0), ("1:00:00", 3600.0), ("2-03:04:05", 183845.0),
    ("  12:34  ", 754.0), ("", None), ("junk", None),
])
def test_etime_seconds(text, seconds):
    assert _seat.etime_seconds(text) == seconds


def test_claude_age_reads_a_real_pid():
    age = _seat.claude_age(os.getpid())
    assert age is not None and age >= 0


def test_plugin_monitors(monkeypatch, tmp_path):
    monkeypatch.delenv(_seat.PLUGIN_ROOT_VAR, raising=False)
    assert _seat.plugin_monitors() is None
    monkeypatch.setenv(_seat.PLUGIN_ROOT_VAR, str(ROOT))
    assert _seat.plugin_monitors() is True
    monkeypatch.setenv(_seat.PLUGIN_ROOT_VAR, str(ROOT / "plugins" / "pager-monitor"))
    assert _seat.plugin_monitors() is False, "the retired opt-in plugin carries []"
    monkeypatch.setenv(_seat.PLUGIN_ROOT_VAR, str(tmp_path))
    assert _seat.plugin_monitors() is False


# ── criterion 3: precedence ──────────────────────────────────────────────

def test_nothing_set_means_not_required(s):
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert not r.on and r.source == "the default: no setting says required" and r.ignored == ()


@pytest.mark.parametrize("value", ["1", "true", "on", "TRUE", " On "])
@pytest.mark.parametrize("layer", ["host", "seat", "session"])
def test_the_env_turns_it_on_over_every_layer(s, value, layer):
    {"host": host, "seat": seat_layer, "session": session_layer}[layer](
        {"pager_required": False})
    s.required(value)
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert r.on and r.source == f"{_seat.REQUIRED_VAR}={value.strip()}"
    assert_deaf_from_both(missing())


@pytest.mark.parametrize("value", ["0", "false", "off", "OFF"])
@pytest.mark.parametrize("layer", ["host", "seat", "session"])
def test_the_env_turns_it_off_over_every_layer(s, value, layer):
    {"host": host, "seat": seat_layer, "session": session_layer}[layer](
        {"pager_required": True})
    s.required(value)
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert not r.on and r.source == f"{_seat.REQUIRED_VAR}={value}"
    assert_silent_from_both()


@pytest.mark.parametrize("value", ["yes", "2", "required", "no"])
def test_another_env_value_is_ignored_and_said(s, value):
    p = seat_layer({"pager_required": True})
    s.required(value)
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert r.on and r.source == f'"pager_required": true in {p}'
    assert r.ignored == (f"{_seat.REQUIRED_VAR}={value} is ignored: only 1/true/on or "
                         "0/false/off decide it",)


def test_each_layer_can_say_required(s):
    for write_layer in (host, seat_layer, session_layer):
        p = write_layer({"pager_required": True})
        r = _seat.pager_required(_seat.resolve(), SESSION)
        assert r.on and r.source == f'"pager_required": true in {p}'
        p.unlink()


def test_the_most_specific_layer_wins(s):
    host({"version": 1, "hooks": False, "pager_required": True})
    p = seat_layer({"pager_required": False})
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert not r.on and r.source == f'"pager_required": false in {p}'
    q = session_layer({"pager_required": True})
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert r.on and r.source == f'"pager_required": true in {q}'


def test_the_launchers_birth_drop_in_is_read(s):
    """`x-env`: `session start` writes SESSIONMAIL_PAGER_REQUIRED into the
    session layer's `mail.d/50-env.json` as a boolean (sessiontools reads it
    by the key's schema type)."""
    p = session_layer({"pager_required": True}, name="mail.d/50-env.json")
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert r.on and r.source == f'"pager_required": true in {p}'


def test_a_value_that_is_not_a_boolean_is_ignored_and_said(s):
    p = session_layer({"pager_required": "yes"})
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert not r.on
    assert r.ignored == (f'"pager_required": "yes" in {p} is ignored: only a boolean decides it',)


def test_a_layer_that_cannot_be_read_is_skipped_and_said(s):
    host({"version": 1, "hooks": False, "pager_required": True})
    session_layer("{not json")
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert r.on, "the host layer still stands"
    assert len(r.ignored) == 1 and r.ignored[0].startswith("the session layer is skipped:")


def test_without_the_layered_reader_the_host_file_is_read_and_that_is_said(s, monkeypatch):
    p = host({"version": 1, "hooks": False, "pager_required": True})
    session_layer({"pager_required": False})

    def gone():
        raise _layer.LayerError("sessiontools.settings is not installed (it ships in "
                                "sessiontools 0.0.4), so the seat and session files under "
                                "~/.session cannot be read")

    monkeypatch.setattr(_layer, "_st", gone)
    r = _seat.pager_required(_seat.resolve(), SESSION)
    assert r.on and r.source == f'"pager_required": true in {p}'
    assert any("only the host file was read" in why for why in r.ignored)


def test_the_pager_off_beats_required(s):
    s.required()
    host({"version": 1, "hooks": True, "pager_required": True})
    s.mp.setenv(_seat.PAGER_VAR, "off")
    assert_silent_from_both()
    assert pager.deaf_check(_seat.resolve(), SESSION) is None


# ── `pager status`: the required: line and the no-monitor sentence ──────

def status_lines(prefix: str) -> list[str]:
    box = Path(os.environ["SESSION_MAIL_ROOT"]) / SESSION
    for d in ("tmp", "new", "cur"):
        (box / d).mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    return [ln for ln in r.stderr.splitlines() if ln.startswith(prefix)]


def test_status_says_not_required_and_the_default(s):
    assert status_lines("required:") == ["required: no (the default: no setting says required)"]


def test_status_names_the_env_as_the_source(s):
    s.required("on")
    assert status_lines("required:") == [
        f"required: yes ({_seat.REQUIRED_VAR}=on) - the Stop and PostToolBatch hooks say "
        "DEAF when nothing listens"]


def test_status_names_the_file_as_the_source(s):
    p = seat_layer({"pager_required": True})
    (line,) = status_lines("required:")
    assert line.startswith(f'required: yes ("pager_required": true in {p})')


def test_status_names_what_was_ignored(s):
    s.required("maybe")
    (line,) = status_lines("required:")
    assert line.startswith("required: no (the default")
    assert f"{_seat.REQUIRED_VAR}=maybe is ignored" in line


def test_status_names_the_contradiction_with_the_pager_off(s):
    host({"version": 1, "hooks": True, "pager_required": True})
    s.mp.setenv(_seat.PAGER_VAR, "off")
    (line,) = status_lines("required:")
    assert "but SESSIONMAIL_PAGER=off turns the pager off, which beats it" in line


def test_status_says_the_monitor_is_not_running_and_the_ways_out(s):
    assert status_lines("pager: the pager monitor") == [f"pager: {pager.LINE_NO_MONITOR}"]


def test_status_says_no_no_monitor_sentence_while_something_listens(s):
    s.arm()
    assert status_lines("pager: the pager monitor") == []


def test_status_quiet_says_none_of_it(s):
    s.required()
    box = Path(os.environ["SESSION_MAIL_ROOT"]) / SESSION
    for d in ("tmp", "new", "cur"):
        (box / d).mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(cli, ["status", "--quiet"], prog_name="pager")
    assert "required:" not in r.stderr


# ── the schema ───────────────────────────────────────────────────────────

def test_the_schema_names_pager_required_as_a_boolean_with_its_variable():
    schema = json.loads(_settings.schema())
    prop = schema["properties"]["pager_required"]
    assert prop["type"] == "boolean"
    assert prop["x-env"] == "SESSIONMAIL_PAGER_REQUIRED"
    assert "pager_required" not in json.loads(_settings.template()), "missing means not required"


@pytest.mark.parametrize("value", [True, False])
def test_the_schema_accepts_pager_required(value):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_settings.schema())
    jsonschema.validate({"version": 1, "hooks": False, "pager_required": value}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"pager_required": "yes"}, schema)


# ── the monitor leaves a trace when it arms ──────────────────────────────

def test_the_monitor_touches_monitor_armed_when_it_arms(rig, monkeypatch):
    from test_pager_monitor import DEADLINE, driven, monitor, until
    monkeypatch.setattr(_seat, "claude_for", lambda session, ps=None, me=None, live=None: None)
    rig.box()
    armed = _seat.stamp_dir(rig.seat, "sess-a") / _seat.MONITOR_ARMED_STAMP
    seen: list[bool] = []

    def script() -> None:
        # Seen WHILE the monitor runs (the thread ends it after), within the
        # rig's own DEADLINE: a slow start is not a missing stamp.
        seen.append(until(armed.exists, DEADLINE))

    t = driven(script)
    monitor()
    t.join(DEADLINE)
    assert seen == [True]
    assert armed.exists(), "never cleared: it records that a monitor ran"


def test_a_hand_listen_leaves_no_monitor_armed_stamp(rig):
    from test_pager_listen import run
    rig.box()
    assert run("--timeout", "0").exit_code == 124
    assert not (_seat.stamp_dir(rig.seat, "sess-a") / _seat.MONITOR_ARMED_STAMP).exists()


def test_a_real_kill_leaves_a_required_seat_deaf_with_ended(rig, sid, child, monkeypatch):
    """End to end: a REAL monitor arms (monitor-armed), `pager stop --kill`
    ends it (and writes `off`), and the deaf check then says ended - not
    silence, as if the seat were only muted."""
    from test_pager_mute import invoke
    armed = _seat.stamp_dir(rig.seat, sid) / _seat.MONITOR_ARMED_STAMP
    assert until(armed.exists, DEADLINE)
    r = invoke("stop", "--kill")
    assert r.exit_code == 0, r.output
    assert until(lambda: child.returncode is not None, DEADLINE)
    assert _seat.stopped_on_purpose(rig.seat, sid), "the kill writes the off stamp"
    monkeypatch.setenv(_seat.REQUIRED_VAR, "1")
    monkeypatch.setenv(_seat.PLUGIN_ROOT_VAR, str(ROOT))
    monkeypatch.setattr(_seat, "_ps_table",
                        lambda: ({CLAUDE: 1}, {CLAUDE: f"claude --session-id {sid}"}))
    monkeypatch.setattr(_seat, "claude_for", lambda session, ps=None, me=None, live=None: CLAUDE)
    monkeypatch.setattr(_seat, "claude_age", lambda pid: 600.0)
    assert pager.deaf_check(rig.seat, sid) == ended()


from test_pager_monitor import DEADLINE, rig, sid, until  # noqa: E402,F401
from test_pager_mute import child  # noqa: E402,F401  (a real monitor child)
