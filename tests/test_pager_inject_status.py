"""SM-518 do-006: the read side - `pager status`'s `today:` line, and the
documented `jq` one-liner that sums `tokens_est` per seat per day.

README Requirement 8 and design §7: "`pager status` shows today's totals for
the session; a documented `jq` one-liner sums `tokens_est`". The one-liner is
read OUT OF docs/pager-inject-log.md and run, so the doc and the test cannot
drift. Hermetic: conftest's sandbox HOME, a fake session dir under it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _injectlog, _seat, _tokens, pager
from sessionmail.pager import cli

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "pager-inject-log.md"
SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    home = tmp_path / "sessionmail"
    (home / "seats" / "alpha").mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    return _seat.resolve()


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def ride(*items) -> _injectlog.Ride:
    return _injectlog.Ride(items=[_injectlog.item(f"m{i}@x", kind=k, view="text", given=g,
                                                  in_mail=g, cut=False)
                                  for i, (k, g) in enumerate(items)])


def raw_row(session: str, ts: str, **keys) -> None:
    """A row with a chosen `ts` (another day), appended as the writer would."""
    d = _injectlog.log_dir(session)
    d.mkdir(parents=True, exist_ok=True)
    row = {"ts": ts, "tool": "mail", "event": "inject", "via": "batch", "seq": 0,
           "sid": session, "seat": "alpha", "pid": 1, "tokens_est": 999, "items": [], **keys}
    with open(d / _injectlog.FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# ── today_totals: what the line sums ─────────────────────────────────────

def test_today_sums_this_sessions_inject_rows_and_splits_the_rides(seat):
    s1 = "x" * 400                                   # 100 tok
    s2 = "y" * 80                                    # 20 tok
    s3 = "z" * 12                                    # 3 tok
    _injectlog.write(SESSION, via="batch", s=s1, seat=seat, ride=ride(("mail", 200), ("heard", 120)))
    _injectlog.write(SESSION, via="prompt", s=s2, seat=seat, ride=ride(("mail", 40)))
    _injectlog.write(SESSION, via="monitor", s=s3, seat=seat)
    _injectlog.lifecycle(SESSION, event="start", via="monitor", seat=seat, box="b", timeout=None)
    t = _injectlog.today_totals(SESSION)
    assert t == {"injections": 3, "tokens": 123, "rides": 120,
                 "mail": (200 + 40) // 4, "heard": 120 // 4}
    assert t["tokens"] == sum(_tokens.estimate(s) for s in (s1, s2, s3))


def test_another_day_and_another_session_are_not_today(seat):
    raw_row(SESSION, "2030-12-12T09:00:00.000+11:00")
    _injectlog.write(OTHER, via="batch", s="x" * 40, seat=seat)
    _injectlog.write(SESSION, via="batch", s="x" * 40, seat=seat)
    assert _injectlog.today_totals(SESSION)["injections"] == 1
    assert _injectlog.today_totals(SESSION, today="2030-12-12")["tokens"] == 999


def test_the_rolled_generation_is_read_too(seat, monkeypatch):
    monkeypatch.setattr(_injectlog, "ROLL_AT", 1)
    for _ in range(3):
        _injectlog.write(SESSION, via="batch", s="x" * 40, seat=seat)
    d = _injectlog.log_dir(SESSION)
    assert (d / _injectlog.ROLLED).exists()
    assert _injectlog.today_totals(SESSION)["injections"] == 2, "pager.1.jsonl and pager.jsonl"


def test_a_torn_line_is_skipped_not_fatal(seat):
    _injectlog.write(SESSION, via="batch", s="x" * 40, seat=seat)
    with open(_injectlog.log_dir(SESSION) / _injectlog.FILE, "a") as fh:
        fh.write('{"ts": "torn\n[1, 2]\n')
    assert _injectlog.today_totals(SESSION)["injections"] == 1


def test_no_session_and_no_log_read_as_nothing(seat):
    assert _injectlog.read_rows("-") == [] and _injectlog.read_rows("") == []
    assert _injectlog.today_totals(SESSION)["injections"] == 0
    assert not _injectlog.log_dir(SESSION).exists(), "reading makes nothing"


# ── the line ─────────────────────────────────────────────────────────────

def test_the_line_says_counts_tokens_split_capture_and_path(seat, monkeypatch):
    _injectlog.write(SESSION, via="batch", s="x" * 4000, seat=seat,
                     ride=ride(("mail", 3000), ("heard", 800)))
    _injectlog.write(SESSION, via="stop", s="pager: not listening", seat=seat)
    line = pager.today_status_line(seat, SESSION)
    path = _injectlog.log_dir(SESSION) / _injectlog.FILE
    assert line == (f"today: 2 injections, ≈1,005 tok (rides ≈1,000: mail ≈750, heard ≈200); "
                    f"capture off - {path}")
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    assert "; capture on - " in pager.today_status_line(seat, SESSION)


def test_the_line_with_nothing_yet(seat):
    line = pager.today_status_line(seat, SESSION)
    assert line.startswith("today: nothing injected yet; capture off - ")
    assert not _injectlog.log_dir(SESSION).exists()


def test_no_session_no_line(seat):
    assert pager.today_status_line(seat, "-") is None


def test_pager_status_prints_the_today_line_on_stderr(seat, tmp_path, monkeypatch):
    root = tmp_path / "mail"
    for d in ("new", "cur", "tmp"):
        (root / SESSION / d).mkdir(parents=True)
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    _injectlog.write(SESSION, via="batch", s="x" * 400, seat=seat)
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    assert r.exit_code == pager.EX_NOTHING, r.output
    [line] = [ln for ln in r.stderr.splitlines() if ln.startswith("today: ")]
    assert line.startswith("today: 1 injection, ≈100 tok (rides ≈100: mail ≈0, heard ≈0)")
    assert "today:" not in r.stdout, "stdout stays one line per message"
    quiet = CliRunner().invoke(cli, ["status", "--quiet"], prog_name="pager")
    assert "today:" not in quiet.stderr


# ── the documented jq one-liner, run as written ──────────────────────────

def documented_jq() -> str:
    """The program in docs/pager-inject-log.md, under its `jq-sum` marker."""
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- jq-sum:", 1)[1].split("```sh\n", 1)[1].split("```", 1)[0]
    m = re.search(r"jq -rs '(.*?)'", block, re.S)
    assert m, block
    assert "~/.session/sessions/*/log/mail/pager*.jsonl" in block
    return m.group(1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_the_documented_jq_sums_tokens_per_seat_per_day(seat, monkeypatch):
    # Two sessions of one seat, a deny (seat null) in each, a lifecycle row,
    # and a row from another day.
    _injectlog.write(SESSION, via="batch", s="x" * 400, seat=seat)          # 100
    _injectlog.write(SESSION, via="monitor", s="x" * 40, seat=seat)         # 10
    _injectlog.lifecycle(SESSION, event="start", via="monitor", seat=seat, box="b", timeout=None)
    raw_row(SESSION, "2030-12-12T09:00:00.000+11:00", tokens_est=7)
    _injectlog.write(OTHER, via="prompt", s="x" * 80, seat=seat)            # 20
    hook = ROOT / "hooks" / "mail-guard" / "hook.py"
    import importlib.util
    import time
    spec = importlib.util.spec_from_file_location("mail_guard_jq", hook)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.log_deny({"session_id": SESSION, "tool_name": "Bash"}, "d" * 120, time.monotonic())  # 30
    guard.log_deny({"session_id": "lonely-session", "tool_name": "Bash"}, "e" * 8, time.monotonic())  # 2
    files = sorted(str(p) for p in Path(os.environ["HOME"]).glob(
        ".session/sessions/*/log/mail/pager*.jsonl"))
    assert len(files) == 3
    out = subprocess.run(["jq", "-rs", documented_jq(), *files], capture_output=True,
                         text=True, check=True).stdout
    got = sorted(tuple(line.split("\t")) for line in out.splitlines())
    assert got == sorted([
        ("(no seat)", today(), "1", "2"),            # a deny in a session with no seat row
        ("alpha", "2030-12-12", "1", "7"),
        ("alpha", today(), "4", str(100 + 10 + 20 + 30)),   # the deny joins its session's seat
    ])


def test_an_item_with_no_message_id_has_a_null_id():
    """do-003 review N5: an arrival with no Message-ID is `id: null`."""
    for mid in (None, "", "  ", "<>"):
        assert _injectlog.item(mid, kind="mail", view="count", given=0, in_mail=None,
                               cut=True)["id"] is None
    assert _injectlog.item("<a@b>", kind="mail", view="count", given=0, in_mail=None,
                           cut=True)["id"] == "a@b"
