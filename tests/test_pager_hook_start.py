"""SM-498 build-002: `pager hook start`, the SessionStart hook.

Every session has a session box from the start, workers included. So the
plugin's SessionStart hook births the box (design.md 2.4), and:

- the BIRTH is UNGATED (RULED): box, subscribe, declare and doorbell
  happen whatever `SESSIONMAIL_PAGER` or the settings file say;
- what the hook SAYS is GATED behind ONE `if` - pager off, or the settings
  file missing, off or faulty, is silent; the file is READ, never written;
- every R4 failure exits 0 with ONE line;
- the generator runs DETACHED: the hook returns before a slow one finishes,
  and the born record gets the generator's line afterwards;
- a box born at SessionStart reports the launcher's SESSIONMAIL_SUBSCRIBE
  birth file there (task 3.4, merged at land-prep: the launcher applies it,
  the birth reports it), and the listen that later finds the box neither
  reports it again nor regenerates a LIVE doorbell (success criterion 3);
- on a REQUIRED seat (SM-726) the state line is the deaf check's: silence in
  claude's grace, never an arm order at a fresh SessionStart.

The doorbell is faked in-process where postfix is not the question; the
detached child is a REAL process, so it runs a fake generator (GEN_VAR) that
logs each run - never the live one. The end-to-end criterion-3 test runs on
`test_message_subscription`'s fleet: the REAL generator, under tmp.
"""

from __future__ import annotations

import json
import os
import re
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _birth, _seat, _settings, _subscribe, pager
from sessionmail.pager import cli

from test_message_subscription import HOST, fleet, make_live, rows  # noqa: F401
from _seatmap import session_live_path
from test_pager_subscribe_at_birth import NEW, banner, born_with, fresh  # noqa: F401

SID = "77777777-2222-3333-4444-555555555555"
OTHER_SID = "88888888-2222-3333-4444-555555555555"
HOOKS_ON = '{"version": 1, "hooks": true}\n'
HOOKS_OFF = '{"version": 1, "hooks": false}\n'
FAULTY = '{"hook": true}\n'
NOT_COPIED = (False, "NOT COPIED: no alias map postfix reads copies alpha@hq into this box")
LIVE = (True, "alpha@hq -> this box, in /fake/valias")

#: A fake generator: one line per run in $GEN_RUNS, after $GEN_SLEEP seconds.
FAKE_GEN = """#!/bin/sh
sleep "${GEN_SLEEP:-0}"
echo "$*" >> "$GEN_RUNS"
"""


def settings_path() -> Path:
    """The host settings file in the sandbox HOME - `_settings.path()`, so it
    follows the file where SM-450's successors put it (land-prep: it moved to
    `~/.sessionkit/mail/settings/mail.json`)."""
    return _settings.path()


def write_settings(text: str) -> Path:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.root = Path(os.environ["SESSION_MAIL_ROOT"])
        self.home = Path(os.environ["SESSIONMAIL_HOME"])
        self.runs = tmp_path / "gen-runs"
        self.rings = [NOT_COPIED]
        gen = tmp_path / "fake-gen"
        gen.write_text(FAKE_GEN)
        gen.chmod(0o755)
        monkeypatch.setenv(_seat.GEN_VAR, str(gen))
        monkeypatch.setenv("GEN_RUNS", str(self.runs))
        monkeypatch.setenv("GEN_SLEEP", "0")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
        monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
        monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
        monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
        monkeypatch.delenv(pager.SUBSCRIBE_VAR, raising=False)
        monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
        monkeypatch.setattr(_seat, "doorbell", self.doorbell)
        self.root.mkdir(parents=True, exist_ok=True)       # a machine WITH mail
        write_settings(HOOKS_ON)

    def doorbell(self, address, session, env=None):
        return self.rings.pop(0) if len(self.rings) > 1 else self.rings[0]

    def box(self, sid: str = SID) -> Path:
        return self.root / sid

    def record(self, sid: str = SID) -> Path:
        # `_birth.born_record`: under the session's mail dir, wherever
        # `_seat.session_root` puts it (land-prep: `<session dir>/mail/`).
        return _birth.born_record(sid)

    def generator_runs(self) -> int:
        return len(self.runs.read_text().splitlines()) if self.runs.exists() else 0

    def wait_for_record(self, sid: str = SID, lines: int = 2, within: float = 15.0) -> list[str]:
        """The detached child is a real process: wait for its line, bounded."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            if self.record(sid).exists():
                got = self.record(sid).read_text().splitlines()
                if len(got) >= lines:
                    return got
            time.sleep(0.05)
        raise AssertionError(f"born record never reached {lines} lines: "
                             f"{self.record(sid).read_text() if self.record(sid).exists() else None}")


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def start(payload: dict | None = None):
    data = {"hook_event_name": "SessionStart", "source": "startup", **(payload or {})}
    return CliRunner().invoke(cli, ["hook", "start"], input=json.dumps(data), prog_name="pager")


def said(result) -> list[str]:
    """The lines the model is handed: hookSpecificOutput.additionalContext."""
    if not result.stdout.strip():
        return []
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    return out["additionalContext"].splitlines()


def born_line(rig: Rig, sid: str = SID) -> str:
    return pager.BORN_LINE.format(box=rig.box(sid), session=sid)


# ── the registration and the line ───────────────────────────────────────────

def test_the_born_line_names_the_box_and_the_session_id():
    line = pager.BORN_LINE.format(box="/m/agents/x", session="x")
    assert line == "sessionmail: this session's box is /m/agents/x - mail to x lands there"


def test_hook_start_is_listed_after_batch():
    assert list(pager.hook.commands)[-1] == "start"


# ── a fresh session ─────────────────────────────────────────────────────────

def test_a_fresh_session_is_born_told_its_box_and_told_to_listen(rig):
    result = start()
    assert result.exit_code == 0, result.output
    assert all((rig.box() / d).is_dir() for d in ("cur", "new", "tmp"))
    assert said(result) == [born_line(rig), pager.LINE_NOT_LISTENING]
    record = rig.wait_for_record()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d startup regen=detached",
                        record[0]), record
    assert record[1].startswith("generator ran in "), record
    assert rig.generator_runs() == 1


def test_the_state_line_given_at_start_is_not_repeated_by_the_first_batch(rig):
    assert pager.LINE_NOT_LISTENING in said(start())
    rig.wait_for_record()
    batch = CliRunner().invoke(cli, ["hook", "batch"], input="{}", prog_name="pager")
    assert batch.exit_code == 0
    assert pager.LINE_NOT_LISTENING not in batch.stdout


def test_a_listening_session_is_told_it_is_listening(rig):
    d = _seat.stamp_dir(None, SID)
    d.mkdir(parents=True)
    (d / _seat.LISTENING_STAMP).write_text(str(os.getpid()))
    assert said(start())[-1] == pager.LINE_LISTENING
    rig.wait_for_record()


# ── land-prep: SM-726's required seat (the deaf check) ──────────────────────

def test_a_required_seat_at_a_fresh_start_is_told_its_box_and_no_arm_order(rig, monkeypatch):
    """SM-726 do-005, merged at land-prep: on a REQUIRED seat the state line
    is the deaf check's, as `hook batch` gives it. At a fresh SessionStart
    claude is not yet running for the session (or is in its grace), so it
    says nothing: no "not listening - arm" order racing the pager monitor."""
    monkeypatch.setenv(_seat.REQUIRED_VAR, "1")
    # The premise, pinned: claude is not running for this session yet. Left
    # to `ps`, a suite run from inside a claude seat finds that seat's claude
    # by ancestry - past its grace - and the test would measure the runner.
    monkeypatch.setattr(_seat, "claude_for", lambda session, ps=None, **kw: None)
    result = start()
    assert result.exit_code == 0, result.output
    assert said(result) == [born_line(rig)]
    rig.wait_for_record()


def test_a_required_seat_past_its_grace_with_nothing_listening_is_told_DEAF(rig, monkeypatch):
    """A `compact` or `clear` on a seat past its grace, required, nothing
    listening and no monitor expected: the hook gives the DEAF line the
    batch hook would, never the plain arm line."""
    monkeypatch.setenv(_seat.REQUIRED_VAR, "1")
    monkeypatch.setattr(_seat, "claude_for", lambda session, ps=None, **kw: 4242)
    monkeypatch.setattr(_seat, "claude_age", lambda pid: 999.0)
    monkeypatch.setattr(_seat, "monitor_expected", lambda session, ps=None, env=None, claude=None:
                        SimpleNamespace(expected=False, why="headless (-p): no monitor runs"))
    lines = said(start({"source": "compact"}))
    assert lines[0] == born_line(rig)
    assert lines[1:] == [" ".join(pager.DEAF_NOT_LISTENING.replace(
        "{why}", "headless (-p): no monitor runs").split())]
    assert pager.LINE_NOT_LISTENING not in lines
    rig.wait_for_record()


def test_a_seat_not_required_keeps_the_plain_state_line(rig, monkeypatch):
    monkeypatch.setenv(_seat.REQUIRED_VAR, "0")
    assert said(start()) == [born_line(rig), pager.LINE_NOT_LISTENING]
    rig.wait_for_record()


def test_the_payload_source_is_recorded(rig):
    start({"source": "clear"})
    assert rig.wait_for_record()[0].split()[1:] == ["clear", "regen=detached"]


def test_a_compact_finds_the_box_and_a_LIVE_doorbell_runs_no_generator_and_writes_no_record(rig):
    for d in ("cur", "new", "tmp"):
        (rig.box() / d).mkdir(parents=True)
    rig.rings = [LIVE]
    result = start({"source": "compact"})
    assert result.exit_code == 0
    assert said(result) == [born_line(rig), pager.LINE_NOT_LISTENING]
    time.sleep(0.3)
    assert rig.generator_runs() == 0 and not rig.record().exists()


def test_a_resume_whose_rows_went_with_the_old_process_regenerates_detached(rig):
    """resume: the box exists, the alias rows do not - NOT COPIED - so the
    generator runs, detached; no born record (this call did not birth)."""
    for d in ("cur", "new", "tmp"):
        (rig.box() / d).mkdir(parents=True)
    result = start({"source": "resume"})
    assert result.exit_code == 0
    deadline = time.monotonic() + 15
    while rig.generator_runs() == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert rig.generator_runs() == 1 and not rig.record().exists()


# ── the generator is detached ───────────────────────────────────────────────

def test_the_hook_returns_before_a_slow_generator_finishes(rig, monkeypatch):
    monkeypatch.setenv("GEN_SLEEP", "3")
    t0 = time.monotonic()
    result = start()
    took = time.monotonic() - t0
    assert result.exit_code == 0 and took < 2.0, took
    assert said(result)[0] == born_line(rig)
    assert len(rig.record().read_text().splitlines()) == 1       # line 1 now, line 2 later
    record = rig.wait_for_record()
    assert time.monotonic() - t0 >= 3.0
    assert record[1].startswith("generator ran in ")


def test_a_missing_generator_is_written_to_the_record_and_the_hook_still_says_the_box(rig,
                                                                                    monkeypatch,
                                                                                    tmp_path):
    monkeypatch.setenv(_seat.GEN_VAR, str(tmp_path / "no-such-generator"))
    result = start()
    assert result.exit_code == 0 and said(result)[0] == born_line(rig)
    assert rig.wait_for_record()[1].startswith("generator not found")


def test_a_generator_that_fails_is_written_to_the_record(rig, tmp_path, monkeypatch):
    gen = tmp_path / "bad-gen"
    gen.write_text("#!/bin/sh\necho 'lock held' >&2\nexit 3\n")
    gen.chmod(0o755)
    monkeypatch.setenv(_seat.GEN_VAR, str(gen))
    assert start().exit_code == 0
    assert rig.wait_for_record()[1].startswith("generator exit 3 in ")


def test_a_child_that_cannot_start_is_recorded_as_failed(rig, monkeypatch):
    def refuse(*a, **kw):
        raise OSError(2, "No such file or directory")
    monkeypatch.setattr(_birth.subprocess, "Popen", refuse)
    result = start()
    assert result.exit_code == 0 and said(result)[0] == born_line(rig)
    assert rig.record().read_text().split()[1:] == ["startup", "regen=failed"]


# ── THE ONE IF: the birth is ungated, the speech is gated ──────────────────

@pytest.mark.parametrize("settings", [None, HOOKS_OFF, FAULTY, "not json", ""])
def test_hooks_off_missing_or_faulty_births_the_box_and_says_nothing(rig, settings):
    p = settings_path()
    if settings is None:
        p.unlink()
    else:
        write_settings(settings)
    result = start()
    assert result.exit_code == 0
    assert result.stdout == "" and result.stderr == ""
    assert (rig.box() / "new").is_dir()                               # born regardless
    assert rig.wait_for_record()[1].startswith("generator ran in ")
    # READ, never written: missing stays missing, the rest byte-identical
    assert (not p.exists()) if settings is None else p.read_text() == settings


@pytest.mark.parametrize("value", ["off", "0", "no", "false"])
def test_the_pager_opted_out_still_births_and_says_nothing(rig, monkeypatch, value):
    monkeypatch.setenv(_seat.PAGER_VAR, value)
    result = start()
    assert result.exit_code == 0 and result.stdout == ""
    assert (rig.box() / "new").is_dir()
    rig.wait_for_record()


def test_a_subagent_payload_births_nothing_and_says_nothing(rig):
    result = start({"agent_id": "adf55fe4", "agent_type": "general-purpose", "session_id": SID})
    assert result.exit_code == 0 and result.stdout == "" and result.stderr == ""
    assert not rig.box().exists() and not rig.record().exists()
    assert rig.generator_runs() == 0


# ── R4: every failure exits 0 with ONE line ────────────────────────────────

def _no_session(rig, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    return pager.START_NO_SESSION


def _no_root(rig, monkeypatch):
    rig.root.rmdir()
    return (f"sessionmail: no mail root at {rig.root} - no box born; "
            "mail to this session will bounce")


def _birth_refused(rig, monkeypatch):
    def refuse(root, name):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(_seat, "birth_box", refuse)
    return (f"sessionmail: could not create the session box at {rig.box()}: "
            "[Errno 13] Permission denied")


def _crash(rig, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(_birth, "ensure_session_box", boom)
    return "sessionmail: hook start could not finish - unexpected"


R4 = {"no session id": _no_session, "no mail root": _no_root,
      "birth OSError": _birth_refused, "anything else raised": _crash}


@pytest.mark.parametrize("case", list(R4))
def test_each_R4_failure_exits_0_with_its_one_line(rig, monkeypatch, case):
    line = R4[case](rig, monkeypatch)
    result = start({} if case != "no session id" else {"session_id": ""})
    assert result.exit_code == 0, result.output
    assert said(result) == [line]
    assert not rig.record().exists()


@pytest.mark.parametrize("case", list(R4))
def test_with_the_hooks_off_an_R4_failure_is_unsaid_but_logged_on_stderr(rig, monkeypatch, case):
    line = R4[case](rig, monkeypatch)
    write_settings(HOOKS_OFF)
    result = start({} if case != "no session id" else {"session_id": ""})
    assert result.exit_code == 0 and result.stdout == ""
    assert result.stderr == line + "\n"


def test_no_mail_root_is_never_created_by_the_hook(rig, monkeypatch):
    _no_root(rig, monkeypatch)
    start()
    assert not rig.root.exists()
    assert rig.generator_runs() == 0


def test_no_postfix_to_measure_is_no_fault_and_the_birth_still_regenerates(rig):
    rig.rings = [(None, "no postconf/postmap on PATH to read the alias maps")]
    result = start()
    assert said(result) == [born_line(rig), pager.LINE_NOT_LISTENING]
    assert rig.wait_for_record()[1].startswith("generator ran in ")


# ── the session id: one resolver, the payload only as a fallback ───────────

def test_the_payloads_session_id_is_used_when_the_environment_has_none(rig, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    result = start({"session_id": OTHER_SID})
    assert said(result)[0] == born_line(rig, OTHER_SID)
    assert (rig.box(OTHER_SID) / "new").is_dir()
    rig.wait_for_record(OTHER_SID)


def test_when_they_disagree_the_environment_wins_and_the_hook_says_so(rig):
    result = start({"session_id": OTHER_SID})
    lines = said(result)
    assert lines[0] == (f"sessionmail: the harness said session {OTHER_SID}, the environment "
                        f"{SID} - the environment's box is the one born")
    assert lines[1] == born_line(rig)
    assert (rig.box() / "new").is_dir() and not rig.box(OTHER_SID).exists()
    rig.wait_for_record()


# ── declare at birth ────────────────────────────────────────────────────────

def test_a_declared_address_is_written_at_session_start(rig, monkeypatch):
    monkeypatch.setenv("EMAIL", "sessionmail+sm-498.foreman@hq")
    monkeypatch.setattr(_seat, "short_host", lambda: "hq")
    start()
    # Read through `_seat.read_address`: since session-settings the
    # declaration is the `address` key of the session's layer, not a file.
    assert _seat.read_address(SID) == "sessionmail+sm-498.foreman"
    rig.wait_for_record()


# ── SUBSCRIBE at the hook's birth, and success criterion 3 (the real generator) ──

@pytest.fixture
def unborn(fresh, monkeypatch):
    """`fresh`'s fleet and its unborn live session NEW; the hooks on, so the
    hook's line can be read."""
    write_settings(HOOKS_ON)
    return fresh


def _wait_for_line_2(fleet_, sid: str) -> list[str]:
    record = _birth.born_record(sid)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if record.exists() and len(record.read_text().splitlines()) >= 2:
            return record.read_text().splitlines()
        time.sleep(0.05)
    raise AssertionError(f"no generator line: {record.read_text() if record.exists() else None}")


def test_criterion_3_hook_then_listen_with_a_LIVE_doorbell_runs_the_generator_once(unborn):
    hooked = start({"session_id": NEW})
    assert hooked.exit_code == 0, hooked.output
    assert (unborn.agents / NEW / "new").is_dir()                   # born at SessionStart
    assert _wait_for_line_2(unborn, NEW)[1].startswith("generator ran in ")
    assert unborn.runs() == 1                                       # the hook's detached run
    assert rows((unborn.maps / "vmailbox").read_text())[f"{NEW}@{HOST}"]   # deliverable at its id
    listened = CliRunner().invoke(cli, ["listen", "--timeout", "0"], prog_name="pager")
    assert listened.exit_code == 124, listened.output
    assert "doorbell   : LIVE" in listened.stderr, listened.stderr
    assert unborn.runs() == 1                                       # and the listen ran none


def test_the_hooks_birth_reports_and_routes_the_birth_file_and_the_listen_does_not(unborn,
                                                                                 monkeypatch):
    """Land-prep, against master's task 3.4: the LAUNCHER's birth file
    (`mail.d/50-env.json`) carries SESSIONMAIL_SUBSCRIBE, and the call that
    births the box - here the hook - reports it and its detached regen routes
    it. Nothing goes through the add path, and the later listen finds the box
    born, so it reports nothing and writes nothing."""
    born_with(unborn, monkeypatch, "atomnote")
    monkeypatch.setattr(_subscribe, "add", lambda *a, **k: pytest.fail("add called"))
    assert start({"session_id": NEW}).exit_code == 0
    assert _wait_for_line_2(unborn, NEW)[1].startswith("generator ran in ")
    assert unborn.subscribed(NEW) == ["atomnote"]
    assert rows(unborn.valias)[f"atomnote@{HOST}"].endswith(f", {NEW}@{HOST}")
    assert unborn.session_log(NEW) == []                            # the hook wrote nothing
    listened = CliRunner().invoke(cli, ["listen", "--timeout", "0"], prog_name="pager")
    assert listened.exit_code == 124, listened.output
    assert banner(listened) == []
    assert unborn.session_log(NEW) == []
    assert unborn.runs() == 1                                       # the hook's run, no other


def test_a_refused_box_at_the_hooks_birth_is_turned_off_and_journaled_as_the_hook(unborn,
                                                                                monkeypatch):
    """The birth file names a box that does not resolve: the hook's birth
    turns it OFF in the verbs' file, as the listen's would, and the journal
    names `pager hook start` as the command that did (VIA)."""
    born_with(unborn, monkeypatch, "nosuchbox atomnote")
    assert start({"session_id": NEW}).exit_code == 0
    _wait_for_line_2(unborn, NEW)
    live = session_live_path(NEW, unborn.userhome)
    assert json.loads(live.read_text()) == {"subscriptions": {"nosuchbox": False}}
    assert unborn.subscribed(NEW) == ["atomnote"]
    (entry,) = unborn.session_log(NEW)
    assert entry["value"] is False
    assert "pager hook start" in json.dumps(entry), entry


# ── build-003: the listen banner reads the born record ───────────────────

def _listen():
    return CliRunner().invoke(cli, ["listen", "--timeout", "0"], prog_name="pager")


def _born_box_lines(result) -> list[str]:
    return [ln for ln in result.stderr.splitlines() if ln.startswith("  born box")]


def test_listen_after_a_hook_birth_says_the_box_was_born_at_session_start(rig):
    """The hook made the box, so the listen did not: its banner says so and
    names the generator's line (line 2 of the born record)."""
    assert start({"source": "resume"}).exit_code == 0
    rig.wait_for_record()
    result = _listen()
    assert result.exit_code == 124, result.output
    (line,) = _born_box_lines(result)
    assert f"{rig.box()} (cur/new/tmp)" in line
    assert "born at SessionStart" in line and "(resume)" in line, line
    assert "generator ran in" in line, line
    assert "made here" not in line
    assert re.search(r"at SessionStart \d\d:\d\d:\d\d \(resume\)", line), line


def test_listen_born_box_still_says_made_here_and_names_no_session_start(rig):
    """No hook ran, so no born record: the listen's own birth line, unchanged."""
    assert not rig.record().exists()
    result = _listen()
    (line,) = _born_box_lines(result)
    assert "made here since the arm retired (F19)" in line
    assert "SessionStart" not in line
    assert "born box" not in _listen().stderr                       # a re-arm: no record, no line


def test_a_born_record_without_a_generator_line_yet_says_what_the_hook_did(rig, monkeypatch):
    """The detached generator has not finished (or never ran): line 1 alone."""
    assert start().exit_code == 0
    rig.wait_for_record()
    rig.record().write_text(rig.record().read_text().splitlines()[0] + "\n")
    (line,) = _born_box_lines(_listen())
    assert re.search(r"born at SessionStart \d\d:\d\d:\d\d \(startup\); generator (detached|skipped|ran)",
                     line), line


def test_an_unreadable_born_record_is_no_reason_to_fail_the_listen(rig):
    assert start().exit_code == 0
    rig.wait_for_record()
    rig.record().write_text("garbage\n")
    result = _listen()
    assert result.exit_code == 124, result.output
    assert _born_box_lines(result) == []
    assert _birth.read_born("no-such-session") is None
