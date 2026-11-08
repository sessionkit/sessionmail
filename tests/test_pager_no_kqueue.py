"""The pager on a platform with no `select.kqueue`.

The watch under `pager listen` is a kqueue on `new/`, which only BSD and
macOS have. Elsewhere the pager must refuse ONCE, plainly - one line naming
the platform and what is missing, a documented exit code, no traceback -
and the hooks must not tell the agent to run a command that cannot work:

- `pager listen` (bare) says the line on stderr and exits 5, with no stamp
  left behind; `--monitor` says it once on stdout, the channel the seat
  hears; `python -m sessionmail._detect` refuses the same way.
- The Stop hook says nothing at all there, on a plain seat and on a
  required one.
- The PostToolBatch hook says the platform line in place of "not
  listening" (or DEAF), once, then only on change.
- `pager status` reads the platform line in place of the two ways out.

Hermetic, as tests/test_pager_listen.py and tests/test_pager_required.py
are: a maildir root under tmp_path, a seat under SESSIONMAIL_HOME, `ps` and
claude's age injected. The platform is simulated by hiding `select.kqueue`
for the test, so this runs on any host; the control cases (kqueue present)
run only where the host has it.
"""

from __future__ import annotations

import json
import os
import select
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _detect, _seat, _settings, pager
from sessionmail.pager import cli

SESSION = "11111111-2222-3333-4444-555555555555"
CLAUDE = 7_000_001           # a claude that exists only in the injected `ps` table
HOOKS_ON = '{"version": 1, "hooks": true}\n'
HOOKS_OFF = '{"version": 1, "hooks": false}\n'

has_kqueue = pytest.mark.skipif(not hasattr(select, "kqueue"),
                                reason="the control case needs a host with kqueue")


@pytest.fixture
def no_kqueue(monkeypatch):
    """This host, as one without `select.kqueue`."""
    monkeypatch.delattr(select, "kqueue", raising=False)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """One seat, one session, one claude past its grace, nothing listening,
    the hooks ON."""
    root = tmp_path / "mail" / "stores"
    root.mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root.parent))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("EMAIL", raising=False)
    monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
    monkeypatch.delenv(_seat.REQUIRED_VAR, raising=False)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(pager.MONITOR_SPACING_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({CLAUDE: 1}, {CLAUDE: "claude"}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    monkeypatch.setattr(_seat, "claude_for",
                        lambda session, ps=None, me=None, live=None: CLAUDE)
    monkeypatch.setattr(_seat, "claude_age", lambda pid: 600.0)
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    write(Path(os.environ["HOME"]) / _settings.RELATIVE, HOOKS_ON)

    class Rig:
        def __init__(self) -> None:
            self.root = root
            self.seat = _seat.resolve()

        def box(self, name: str = SESSION) -> Path:
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def stamp(self, name: str) -> Path:
            return _seat.stamp_dir(self.seat, SESSION) / name

        def hooks(self, text: str) -> None:
            write(Path(os.environ["HOME"]) / _settings.RELATIVE, text)

        def required(self) -> None:
            monkeypatch.setenv(_seat.REQUIRED_VAR, "1")

    return Rig()


def maildir(root: Path) -> Path:
    box = root / "box"
    for d in ("tmp", "new", "cur"):
        (box / d).mkdir(parents=True)
    return box


def stop():
    return CliRunner().invoke(cli, ["hook", "stop"], input="{}")


def batch():
    return CliRunner().invoke(cli, ["hook", "batch"], input="{}")


def said(result) -> list[str]:
    """What a batch hook hands the model."""
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].splitlines()


def no_traceback(result) -> None:
    assert result.exception is None or isinstance(result.exception, SystemExit), \
        repr(result.exception)
    assert "Traceback" not in result.output


# -- the one capability check ------------------------------------------------

def test_can_watch_is_false_without_kqueue(no_kqueue):
    assert _detect.can_watch() is False
    assert pager.can_listen() is False


@has_kqueue
def test_can_watch_is_true_with_kqueue():
    assert _detect.can_watch() is True
    assert pager.can_listen() is True


def test_nothing_else_spells_the_check_out():
    """`_detect.can_watch` is the one home of the test; nobody else asks
    `select` for kqueue by name."""
    src = Path(pager.__file__).resolve().parent
    for path in src.rglob("*.py"):
        if path.name == "_detect.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert 'hasattr(select, "kqueue")' not in text, path
        assert "select.kqueue(" not in text, path


def test_the_line_names_the_platform_and_what_is_missing():
    line = pager.cannot_listen_line("plan9")
    assert line.startswith("pager cannot listen on plan9:")
    assert "select.kqueue" in line
    assert "\n" not in line
    # It names no arm instruction: arming cannot work here.
    assert "re-arm" not in line and pager.ARM_HOW not in line
    assert "run_in_background" not in line
    assert pager.cannot_listen_line() == pager.cannot_listen_line(sys.platform)


# -- the detector ------------------------------------------------------------

def test_detect_refuses_plainly_where_there_is_no_kqueue(tmp_path, no_kqueue):
    box = maildir(tmp_path)
    with pytest.raises(Exception) as info:
        _detect.detect(box, poll=0.05, timeout=0.2)
    assert type(info.value).__name__ == "Unsupported", repr(info.value)
    assert sys.platform in str(info.value) and "select.kqueue" in str(info.value)


def test_detect_refuses_before_it_looks_at_the_box(tmp_path, no_kqueue):
    """The platform is asked first: even a path that is no maildir gets the
    platform's refusal, not NoSuchBox."""
    with pytest.raises(_detect.Unsupported):
        _detect.detect(tmp_path / "no-such-box", poll=0.05, timeout=0.2)


def test_follow_refuses_too(tmp_path, no_kqueue):
    box = maildir(tmp_path)
    with pytest.raises(_detect.Unsupported):
        _detect.follow(box, lambda arrivals: None, poll=0.05)


def test_detect_module_main_exits_5_with_one_line(tmp_path, no_kqueue, capsys):
    box = maildir(tmp_path)
    assert _detect.main([str(box), "--timeout", "0.2"]) == _detect.EX_UNSUPPORTED == 5
    out, err = capsys.readouterr()
    assert out == ""
    lines = err.splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("detect: cannot watch on ") and "select.kqueue" in lines[0]


# -- pager listen ------------------------------------------------------------

def test_listen_refuses_once_where_there_is_no_kqueue(rig, no_kqueue):
    rig.box()
    result = CliRunner().invoke(cli, ["listen", "--timeout", "0.3"], prog_name="pager")
    no_traceback(result)
    assert result.exit_code == pager.EX_UNSUPPORTED == 5, (result.exit_code, result.stderr)
    assert result.stdout == ""
    assert result.stderr.splitlines() == [pager.cannot_listen_line()]


def test_a_refused_listen_leaves_no_stamp_claiming_it_listens(rig, no_kqueue):
    rig.box()
    CliRunner().invoke(cli, ["listen", "--timeout", "0.3"], prog_name="pager")
    assert not rig.stamp(_seat.LISTENING_STAMP).exists()
    assert not rig.stamp(_seat.MONITOR_ARMED_STAMP).exists()
    assert _seat.is_listening(rig.seat, SESSION) is None


def test_a_refused_listen_still_refuses_under_quiet(rig, no_kqueue):
    """`--quiet` is "exit code only" for a page; a refusal is never quiet."""
    rig.box()
    result = CliRunner().invoke(cli, ["listen", "--quiet", "--timeout", "0.3"],
                                prog_name="pager")
    assert result.exit_code == 5
    assert result.stderr.splitlines() == [pager.cannot_listen_line()]


def test_the_monitor_says_it_once_on_stdout_and_exits_5(rig, no_kqueue):
    rig.box()
    result = CliRunner().invoke(cli, ["listen", "--monitor"], prog_name="pager")
    no_traceback(result)
    assert result.exit_code == 5, (result.exit_code, result.output)
    assert result.stdout.splitlines() == [pager.cannot_listen_line()]
    assert "DEAF" not in result.output
    assert not rig.stamp(_seat.LISTENING_STAMP).exists()
    assert not rig.stamp(_seat.MONITOR_ARMED_STAMP).exists()


def test_the_listen_help_documents_exit_5():
    result = CliRunner().invoke(cli, ["listen", "--help"], prog_name="pager")
    assert "5 this platform has no kqueue, so the pager cannot listen here" in result.output


# -- the Stop hook: silent where the pager cannot listen ----------------------

@has_kqueue
def test_control_stop_still_nags_where_there_is_kqueue(rig):
    r = stop()
    assert r.exit_code == 2
    assert r.stderr.strip() == pager.LINE_NOT_LISTENING


def test_stop_is_silent_on_a_plain_seat(rig, no_kqueue):
    for _ in range(3):                    # every turn end, not just the first
        r = stop()
        assert r.exit_code == 0 and r.stdout == "" and r.stderr == "", r.output


@has_kqueue
def test_control_stop_says_deaf_on_a_required_seat_where_there_is_kqueue(rig):
    rig.required()
    r = stop()
    assert r.exit_code == 2 and r.stderr.startswith("pager: DEAF - "), r.output


def test_stop_is_silent_on_a_required_seat(rig, no_kqueue):
    rig.required()
    r = stop()
    assert r.exit_code == 0 and r.stdout == "" and r.stderr == "", r.output


def test_stop_is_silent_on_a_required_seat_whose_monitor_stamp_was_left(rig, no_kqueue):
    """A `monitor-armed` stamp left by an earlier build: still nothing."""
    rig.required()
    write(rig.stamp(_seat.MONITOR_ARMED_STAMP), "")
    r = stop()
    assert r.exit_code == 0 and r.stderr == "", r.output


# -- the PostToolBatch hook: the platform line, once ----------------------

def test_batch_says_the_platform_line_once_not_twice(rig, no_kqueue):
    first = batch()
    assert first.exit_code == 0, first.output
    assert said(first) == [pager.cannot_listen_line()]
    assert pager.LINE_NOT_LISTENING not in first.stdout
    second = batch()
    assert second.exit_code == 0
    assert said(second) == [], "said twice"


@has_kqueue
def test_control_batch_says_not_listening_where_there_is_kqueue(rig):
    assert said(batch()) == [pager.LINE_NOT_LISTENING]


def test_batch_on_a_required_seat_says_the_platform_line_not_deaf(rig, no_kqueue):
    rig.required()
    first = said(batch())
    assert first == [pager.cannot_listen_line()]
    assert said(batch()) == []


def test_batch_with_the_hooks_off_on_a_required_seat_says_it_once(rig, no_kqueue):
    rig.hooks(HOOKS_OFF)
    rig.required()
    assert said(batch()) == [pager.cannot_listen_line()]
    assert said(batch()) == []


def test_batch_with_the_hooks_off_on_a_plain_seat_is_silent(rig, no_kqueue):
    rig.hooks(HOOKS_OFF)
    assert said(batch()) == []


def test_batch_muted_is_still_muted(rig, no_kqueue):
    """A seat that muted on purpose keeps the muted line."""
    write(rig.stamp(_seat.OFF_STAMP), "")
    assert said(batch()) == [pager.LINE_OFF]


def test_deaf_check_names_the_platform_after_the_grace(rig, no_kqueue, monkeypatch):
    rig.required()
    assert pager.deaf_check(rig.seat, SESSION) == pager.cannot_listen_line()
    monkeypatch.setattr(_seat, "claude_age", lambda pid: 1.0)     # in the grace
    assert pager.deaf_check(rig.seat, SESSION) is None


# -- pager status ------------------------------------------------------------

def test_status_reads_the_platform_line_not_the_ways_out(rig, no_kqueue):
    readings = pager.status_readings(rig.seat, SESSION)
    assert pager.cannot_listen_line() in readings
    assert f"pager: {pager.LINE_NO_MONITOR}" not in readings


@has_kqueue
def test_control_status_reads_the_ways_out_where_there_is_kqueue(rig):
    assert f"pager: {pager.LINE_NO_MONITOR}" in pager.status_readings(rig.seat, SESSION)


def test_status_stopped_does_not_nag(rig, no_kqueue):
    rig.box()
    r = CliRunner().invoke(cli, ["status", "--stopped"], prog_name="pager")
    assert pager.LINE_NOT_LISTENING not in r.output
    assert r.exit_code == pager.EX_NOTHING, r.output


def test_status_prints_the_platform_line(rig, no_kqueue):
    rig.box()
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    no_traceback(r)
    assert pager.cannot_listen_line() in r.stderr


# -- pager mute / unmute: no way out to name ----------------------------------

def test_mute_with_nothing_listening_names_the_platform_not_the_ways_out(rig, no_kqueue):
    r = CliRunner().invoke(cli, ["mute"], prog_name="pager")
    no_traceback(r)
    assert r.exit_code == pager.EX_NOTHING, r.output
    assert r.output.strip() == ("pager mute: muted session " + SESSION
                                + ", but nothing is listening: " + pager.cannot_listen_line())
    assert pager.LINE_NO_MONITOR not in r.output


def test_unmute_with_nothing_listening_names_the_platform_not_the_ways_out(rig, no_kqueue):
    r = CliRunner().invoke(cli, ["unmute"], prog_name="pager")
    no_traceback(r)
    assert r.exit_code == pager.EX_NOTHING, r.output
    assert r.output.strip() == f"pager unmute: {pager.cannot_listen_line()}"


@has_kqueue
def test_control_mute_and_unmute_name_the_ways_out_where_there_is_kqueue(rig):
    for verb in ("mute", "unmute"):
        r = CliRunner().invoke(cli, [verb], prog_name="pager")
        assert r.exit_code == pager.EX_NOTHING, r.output
        assert r.output.strip().endswith(pager.LINE_NO_MONITOR), r.output


# -- the MCP tool rides the same command ---------------------------------------

#: The tool's verb runs in a CHILD, a fresh interpreter, where the rig's
#: `_seat` patches never land (tests/tool_call.py). So this program hides
#: `select.kqueue` there, sets the rig's seams to what the rig sets, and the
#: test names each seam in `tool_child.covered`. It runs `pager.cli` itself,
#: not as `__main__`, so the patches land on the modules the verb runs in.
#: The test process keeps its kqueue (no `no_kqueue` here): the event loop
#: that awaits the tool picks its selector by it.
CHILD_NO_KQUEUE = f"""
import select
from sessionmail import _seat, pager
vars(select).pop("kqueue", None)
_seat._ps_table = lambda: ({{{CLAUDE}: 1}}, {{{CLAUDE}: "claude"}})
_seat._ps_one = lambda pid: ({{}}, {{}})
_seat.claude_for = lambda session, ps=None, me=None, live=None: {CLAUDE}
_seat.claude_age = lambda pid: 600.0
_seat.idle_seconds = lambda session, now=None: None
pager.cli(prog_name="pager")
"""

#: What CHILD_NO_KQUEUE sets, as `tool_child.covered` names a seam.
CHILD_COVERS = ("sessionmail._seat._ps_table", "sessionmail._seat._ps_one",
                "sessionmail._seat.claude_for", "sessionmail._seat.claude_age",
                "sessionmail._seat.idle_seconds")


def test_the_mcp_listen_tool_refuses_with_exit_5_and_the_line(rig, tool_child, monkeypatch):
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError
    from tool_call import call

    from sessionmail import _mcp
    tool_child.covered.update(CHILD_COVERS)
    monkeypatch.setitem(_mcp.COMMANDS, "pager", [sys.executable, "-P", "-c", CHILD_NO_KQUEUE])
    tool = {n: fn for n, fn, _ in _mcp.tools(cli, "pager")}["pager_listen"]
    with pytest.raises(ToolError) as caught:
        call(tool)
    assert str(caught.value) == f"exit {pager.EX_UNSUPPORTED}: {pager.cannot_listen_line()}"
    assert not rig.stamp("listening").exists()
