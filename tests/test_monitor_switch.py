"""SM-726 do-004 (design.md (l1), as designed): the pager monitor's switch,
off only.

- Host: `"monitor": false` in `~/.sessionkit/mail/settings/mail.json`. A missing key is
  ON; the template does not carry it. It is in `mail.schema.json`.
- Seat: `SESSIONMAIL_MONITOR=off`. It can only turn the monitor OFF; any
  other value is ignored, and `pager status` says so.
- `SESSIONMAIL_PAGER=off` turns the monitor off too.
- Switched off, `pager listen --monitor` prints ONE stdout line naming the
  source and exits 0 (Claude Code posts a notice for any monitor exit, so a
  silent one is impossible).
- `pager status` has a `monitor:` line: armed (pid), not armed, or off, plus
  the source.

Hermetic: conftest clears every SESSIONMAIL_* variable and gives each test its
own HOME; the listener tests use test_pager_monitor.py's rig.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat, _settings, pager
from sessionmail.pager import cli
from test_pager_monitor import DEADLINE, rig, sid  # noqa: F401

SESSION = "sess-a"
CLAUDE = 7_000_001
MONITOR_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen --monitor"
LISTEN_CMD = "/x/.venv/bin/python /x/.venv/bin/pager listen"


def host_file(text: str | None) -> Path:
    """Write TEXT as this test's host settings file (None: no file)."""
    p = _settings.path()
    if text is None:
        if p.exists():
            p.unlink()
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ── the switch: each source, and precedence ──────────────────────────────

def test_nothing_set_means_on():
    host_file(None)
    s = _seat.monitor_switch()
    assert s.on and s.source == "the default: no switch is set" and s.ignored == ()


def test_a_host_file_without_the_key_means_on():
    host_file('{"version": 1, "hooks": true}\n')
    s = _seat.monitor_switch()
    assert s.on and s.source == "the default: no switch is set"


def test_the_seat_env_turns_it_off(monkeypatch):
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    s = _seat.monitor_switch()
    assert not s.on and s.source == "SESSIONMAIL_MONITOR=off"


@pytest.mark.parametrize("value", ["OFF", " off ", "Off"])
def test_the_seat_env_off_is_read_loosely(monkeypatch, value):
    monkeypatch.setenv(_seat.MONITOR_VAR, value)
    assert not _seat.monitor_switch().on


def test_the_pager_env_turns_it_off(monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    s = _seat.monitor_switch()
    assert not s.on and s.source == "SESSIONMAIL_PAGER=off"


def test_the_host_file_turns_it_off():
    p = host_file('{"version": 1, "hooks": true, "monitor": false}\n')
    s = _seat.monitor_switch()
    assert not s.on and s.source == f'"monitor": false in {p}'


def test_the_host_file_can_say_on_and_is_named():
    p = host_file('{"version": 1, "hooks": true, "monitor": true}\n')
    s = _seat.monitor_switch()
    assert s.on and s.source == f'"monitor": true in {p}'


def test_the_host_key_is_read_even_when_the_hooks_key_is_a_fault():
    """`hooks` and `monitor` are separate settings: a file with no `hooks`
    key is a fault for the hooks, and its `monitor: false` still holds."""
    host_file('{"monitor": false}\n')
    assert _settings.read().fault
    assert not _seat.monitor_switch().on


@pytest.mark.parametrize("value", ["on", "yes", "1", "true", "anything"])
def test_the_seat_env_cannot_turn_it_on_over_the_host(monkeypatch, value):
    """Off only: no value of SESSIONMAIL_MONITOR turns the monitor on over
    a host `"monitor": false`. The value is ignored, and said."""
    p = host_file('{"version": 1, "monitor": false}\n')
    monkeypatch.setenv(_seat.MONITOR_VAR, value)
    s = _seat.monitor_switch()
    assert not s.on
    assert s.source == f'"monitor": false in {p}'
    assert s.ignored == (f"SESSIONMAIL_MONITOR={value} is ignored: only `off` changes it, "
                         "and only to off",)


def test_an_other_value_is_ignored_and_said_with_the_monitor_on(monkeypatch):
    monkeypatch.setenv(_seat.MONITOR_VAR, "on")
    s = _seat.monitor_switch()
    assert s.on and s.ignored and "SESSIONMAIL_MONITOR=on is ignored" in s.ignored[0]


def test_an_empty_seat_env_is_unset_not_ignored(monkeypatch):
    monkeypatch.setenv(_seat.MONITOR_VAR, "  ")
    s = _seat.monitor_switch()
    assert s.on and s.ignored == ()


def test_the_pager_env_is_named_first_when_both_say_off(monkeypatch):
    host_file('{"monitor": false}\n')
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    assert _seat.monitor_switch().source == "SESSIONMAIL_PAGER=off"


def test_the_seat_env_is_named_over_the_host_when_both_say_off(monkeypatch):
    host_file('{"monitor": false}\n')
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    assert _seat.monitor_switch().source == "SESSIONMAIL_MONITOR=off"


@pytest.mark.parametrize("text,said", [
    ('{"monitor": "false"}\n', '"monitor": "false" in'),
    ('{"monitor": 0}\n', '"monitor": 0 in'),
    ("{not json\n", "is not JSON, so the monitor is on"),
])
def test_a_host_value_that_is_not_a_boolean_is_ignored_and_said(text, said):
    host_file(text)
    s = _seat.monitor_switch()
    assert s.on
    assert len(s.ignored) == 1 and said in s.ignored[0]


# ── the schema and the template ──────────────────────────────────────────

def test_the_schema_names_monitor_as_a_boolean_and_the_template_does_not_carry_it():
    schema = json.loads(_settings.schema())
    assert schema["properties"]["monitor"]["type"] == "boolean"
    # x-env would make the launcher fold the variable into a layer as a
    # STRING; the pager reads SESSIONMAIL_MONITOR itself.
    assert "x-env" not in schema["properties"]["monitor"]
    assert "monitor" not in json.loads(_settings.template())


@pytest.mark.parametrize("value", [True, False])
def test_the_schema_accepts_monitor(value):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_settings.schema())
    jsonschema.validate({"version": 1, "hooks": True, "monitor": value}, schema)


def test_the_schema_refuses_a_monitor_that_is_not_a_boolean():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_settings.schema())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"monitor": "off"}, schema)


# ── `pager listen --monitor` with the switch off: ONE line, exit 0 ────────

SOURCES = [
    ("env-monitor", lambda mp: mp.setenv(_seat.MONITOR_VAR, "off"), "SESSIONMAIL_MONITOR=off"),
    ("env-pager", lambda mp: mp.setenv(_seat.PAGER_VAR, "off"), "SESSIONMAIL_PAGER=off"),
    ("host", lambda mp: host_file('{"version": 1, "hooks": true, "monitor": false}\n'),
     '"monitor": false in '),
]


@pytest.mark.parametrize("name,switch_off,source", SOURCES, ids=[s[0] for s in SOURCES])
def test_monitor_switched_off_says_one_line_naming_the_source_and_exits_0(
        rig, monkeypatch, name, switch_off, source):
    rig.box()
    switch_off(monkeypatch)
    r = CliRunner().invoke(cli, ["listen", "--monitor"], prog_name="pager")
    assert r.exit_code == pager.EX_MONITOR_OFF == 0, r.output
    lines = r.stdout.splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith("pager: the pager monitor did not start")
    assert source in lines[0]
    # nothing armed: no listening stamp, no confirm mail, no banner
    assert not rig.listening().exists()
    assert "box now" not in r.stderr


def test_the_pager_off_line_does_not_tell_an_opted_out_seat_to_arm(rig, monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    r = CliRunner().invoke(cli, ["listen", "--monitor"], prog_name="pager")
    assert "pager listen" not in r.stdout
    assert "the pager is off for this session (SESSIONMAIL_PAGER=off)" in r.stdout


def test_the_monitor_off_line_names_the_hand_arm_from_its_one_source(rig, monkeypatch):
    """SM-726 do-008 (verify-001 F3): the arm instruction, read from its one
    source, WITHOUT the monitor rider - "(not needed when the pager monitor
    is listening)" cannot apply to a seat whose monitor is switched off."""
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    r = CliRunner().invoke(cli, ["listen", "--monitor"], prog_name="pager")
    assert pager.ARM_HOW_NO_RIDER in r.stdout
    assert pager.ARM_HOW_NO_RIDER != pager.ARM_HOW
    assert "not needed when the pager monitor is listening" not in r.stdout
    assert r.stdout.rstrip().endswith("never a trailing `&`."), r.stdout


def test_a_hand_listen_is_not_switched_off(rig, monkeypatch):
    """The switch is the MONITOR's: a hand `pager listen` still arms (and
    times out here, nothing delivered)."""
    rig.box()
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    r = CliRunner().invoke(cli, ["listen", "--timeout", "0.3"], prog_name="pager")
    assert r.exit_code == pager.EX_TIMEOUT, r.output


def test_a_real_monitor_child_switched_off_prints_one_line_and_exits_0(rig, sid, monkeypatch):
    """End to end on a pipe, as Claude Code runs it: one stdout line, exit 0,
    nothing else on stdout."""
    rig.box(sid)
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    proc = subprocess.run(
        [sys.executable, "-c", "from sessionmail.pager import cli; cli()", "listen", "--monitor"],
        capture_output=True, timeout=DEADLINE, env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.decode().splitlines()
    assert len(out) == 1 and "SESSIONMAIL_MONITOR=off" in out[0], out
    assert not rig.listening(sid).exists()


# ── `pager status`: the `monitor:` line ──────────────────────────────────

def _hold(rig, monkeypatch, pid: int, cmd: str) -> None:
    path = rig.listening()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({pid: CLAUDE}, {pid: cmd}))
    monkeypatch.setattr(_seat, "_ps_one", lambda p: ({pid: CLAUDE}, {pid: cmd}))


@pytest.fixture
def holder():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        reaper.join(DEADLINE)


def _status(rig) -> tuple[int, list[str]]:
    rig.box()
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    return r.exit_code, [ln for ln in r.stderr.splitlines() if ln.startswith("monitor:")]


def test_status_says_armed_with_the_pid_and_the_source(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    code, said = _status(rig)
    assert said == [f"monitor: armed (pid {holder.pid}); switch: on "
                    "(the default: no switch is set)"], said


def test_status_says_armed_and_muted(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    _seat.write_off(rig.seat, SESSION)
    _code, said = _status(rig)
    assert said[0].startswith(f"monitor: armed (pid {holder.pid}), muted;"), said


def test_status_says_not_armed_with_the_source(rig):
    p = host_file('{"version": 1, "hooks": true, "monitor": true}\n')
    _code, said = _status(rig)
    assert said == [f'monitor: not armed; switch: on ("monitor": true in {p})'], said


def test_status_names_a_hand_listener_as_not_the_monitor(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, LISTEN_CMD)
    _code, said = _status(rig)
    assert said[0].startswith(f"monitor: not armed (a hand `pager listen`, pid {holder.pid}, "
                              "hears for this session); switch: on"), said


@pytest.mark.parametrize("name,switch_off,source", SOURCES, ids=[s[0] for s in SOURCES])
def test_status_says_off_with_the_source(rig, monkeypatch, name, switch_off, source):
    switch_off(monkeypatch)
    _code, said = _status(rig)
    assert len(said) == 1 and said[0].startswith(f"monitor: off; switch: off ({source}"), said


def test_status_says_an_ignored_seat_value(rig, monkeypatch):
    p = host_file('{"monitor": false}\n')
    monkeypatch.setenv(_seat.MONITOR_VAR, "on")
    _code, said = _status(rig)
    assert said == [f'monitor: off; switch: off ("monitor": false in {p}); '
                    "SESSIONMAIL_MONITOR=on is ignored: only `off` changes it, and only to off"]


def test_status_says_a_running_monitor_outlives_a_switch_turned_off(rig, holder, monkeypatch):
    _hold(rig, monkeypatch, holder.pid, MONITOR_CMD)
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    _code, said = _status(rig)
    assert said[0].startswith(f"monitor: armed (pid {holder.pid}); switch: off "
                              "(SESSIONMAIL_MONITOR=off) - read at launch"), said


def test_status_keeps_stdout_one_line_per_message_and_quiet_says_nothing(rig, monkeypatch):
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    rig.box()
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    assert "monitor:" not in r.stdout
    r = CliRunner().invoke(cli, ["status", "--quiet"], prog_name="pager")
    assert r.stdout == "" and "monitor:" not in r.stderr


def test_status_with_no_box_yet_still_says_the_monitor_line(rig, monkeypatch):
    """A switched-off monitor never births the box, so `no box yet` is where
    it shows first: the `monitor:` line says why."""
    monkeypatch.setenv(_seat.MONITOR_VAR, "off")
    monkeypatch.setattr(_seat, "claude_for", lambda session, ps=None: CLAUDE)
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    assert r.exit_code == pager.EX_COULD_NOT_LOOK
    assert "monitor: off; switch: off (SESSIONMAIL_MONITOR=off)" in r.stderr
