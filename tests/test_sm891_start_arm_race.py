"""SM-891: SessionStart must not say "pager is not listening" before the
pager monitor arms (SK-104 F8; walk-145 on kit 0.2.1).

MEASURED (SK-104 pass 3, walk 145): with the hooks on and the pager NOT
required, `pager hook start` ran before the pager monitor had stamped
`listening`, so its additionalContext carried the not-listening line - the
arm instruction from `arm.txt` - and the agent armed a SECOND, backgrounded
`pager listen`. The monitor stamped within the same second; the next
PostToolBatch said the monitor hears.

The fix (README "Folded in: GC item 11") moves the state line out of
`hook start` into the per-turn `hook batch`. Its success criteria, pinned:

- a fresh session whose monitor arms after `start` and before the first
  `batch` never sees "not listening";
- a seat with genuinely no listener still hears it, on its first turn.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from sessionmail import _seat, pager
from sessionmail.pager import cli

from test_pager_hook_start import SID, born_line, rig, said, start  # noqa: F401


def _not_required(monkeypatch):
    """The F8 seat: hooks on (the rig writes them), pager NOT required - no
    SESSIONMAIL_PAGER_REQUIRED, so the plain state line, not the deaf check."""
    monkeypatch.delenv(_seat.REQUIRED_VAR, raising=False)


def _monitor_arms(monkeypatch) -> int:
    """The pager monitor arms: its `listening` stamp names a live pid, and
    `ps` says that pid is a `pager listen --monitor`. The pid is this test
    process, alive for as long as the test runs; the rig's `ps` table is
    empty, so `is_monitor` is told (as test_pager_mute's monitor fixture
    does)."""
    pid = os.getpid()
    d = _seat.stamp_dir(None, SID)
    d.mkdir(parents=True, exist_ok=True)
    (d / _seat.LISTENING_STAMP).write_text(str(pid))
    monkeypatch.setattr(_seat, "is_monitor", lambda p, ps=None: True if p == pid else None)
    return pid


def batch() -> list[str]:
    """`pager hook batch` (PostToolBatch, and UserPromptSubmit): the lines the
    model is handed, or none when it says nothing."""
    result = CliRunner().invoke(cli, ["hook", "batch"], input="{}", prog_name="pager")
    assert result.exit_code == 0, result.output
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].splitlines()


def test_a_monitor_that_arms_after_start_means_no_not_listening_is_ever_said(rig, monkeypatch):
    """README success: `start` runs before the monitor stamp exists, then
    `batch` runs after it. Neither says "not listening"; the batch says the
    monitor hears."""
    _not_required(monkeypatch)
    at_start = said(start())
    _monitor_arms(monkeypatch)
    at_batch = batch()
    rig.wait_for_record()
    assert pager.LINE_NOT_LISTENING not in at_start, at_start
    assert at_start == [born_line(rig)]
    assert pager.LINE_NOT_LISTENING not in at_batch, at_batch
    assert pager.LINE_LISTENING_MONITOR in at_batch, at_batch


def test_a_seat_with_genuinely_no_listener_hears_it_on_its_first_turn(rig, monkeypatch):
    """README success: nothing ever listens. `start` says only its box; the
    first `batch` says not listening (before the fix `start` said it and
    recorded it as said, so the first batch stayed silent)."""
    _not_required(monkeypatch)
    at_start = said(start())
    at_batch = batch()
    rig.wait_for_record()
    assert at_start == [born_line(rig)]
    assert pager.LINE_NOT_LISTENING in at_batch, at_batch


def test_a_typed_first_prompt_says_nothing_the_first_tool_batch_says_it(rig, monkeypatch):
    """Who tells a deaf seat on its first turn: a typed UserPromptSubmit does
    not carry the ride-along by default (`set carry-prompt` is off), so it
    says nothing; the first PostToolBatch does (and Stop ends the turn). The
    shipped text names those tellers, not "the first UserPromptSubmit"."""
    _not_required(monkeypatch)
    assert said(start()) == [born_line(rig)]
    ups = CliRunner().invoke(cli, ["hook", "batch"], prog_name="pager", input=json.dumps(
        {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}))
    assert ups.exit_code == 0, ups.output
    assert ups.stdout.strip() == "", ups.stdout
    rig.wait_for_record()
    assert pager.LINE_NOT_LISTENING in batch()
