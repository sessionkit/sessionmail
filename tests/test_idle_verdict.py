"""The idle verdict (changes/stopped-means-idle): `session get idle` is the
one door, cached; unknown is not idle; the threshold is ours.

RULED: no hook, no stamp - Claude Code's
own status, read through session's door. 60 s."""
from __future__ import annotations

import json
import subprocess

import pytest

from sessionmail import _seat


class Res:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout = rc, out


@pytest.fixture(autouse=True)
def clean_cache():
    _seat._idle_cache.clear()
    yield
    _seat._idle_cache.clear()


def test_idle_seconds_asks_session_get_idle_json_and_parses_the_number(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return Res(0, '{"session_id": "sess-a", "name": "alpha", "idle": 75, "status": "shell"}\n')
    monkeypatch.setattr(subprocess, "run", run)
    assert _seat.idle_seconds("sess-a", now=1000.0) == 75.0
    assert calls == [["session", "get", "idle", "sess-a", "--json"]]


@pytest.mark.parametrize("res", [
    Res(2, ""),                                     # no such seat
    Res(0, "not json"),                             # garbage
    Res(0, '{"status": "busy"}'),                   # no idle key
])
def test_unknown_when_session_cannot_say(monkeypatch, res):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: res)
    assert _seat.idle_seconds("sess-a", now=1000.0) is None
    assert _seat.is_idle("sess-a", now=1000.0) is None


def test_unknown_when_session_is_not_on_path_or_times_out(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError("session")
    monkeypatch.setattr(subprocess, "run", missing)
    assert _seat.idle_seconds("sess-a", now=1000.0) is None

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 3)
    _seat._idle_cache.clear()
    monkeypatch.setattr(subprocess, "run", slow)
    assert _seat.idle_seconds("sess-a", now=2000.0) is None


def test_the_verdict_is_cached_per_session_for_idle_poll_seconds(monkeypatch):
    n = {"calls": 0}

    def run(cmd, **kw):
        n["calls"] += 1
        return Res(0, '{"idle": 10}')
    monkeypatch.setattr(subprocess, "run", run)
    assert _seat.idle_seconds("sess-a", now=1000.0) == 10.0
    assert _seat.idle_seconds("sess-a", now=1000.0 + _seat.IDLE_POLL_SECONDS - 0.1) == 10.0
    assert n["calls"] == 1
    assert _seat.idle_seconds("sess-a", now=1000.0 + _seat.IDLE_POLL_SECONDS + 0.1) == 10.0
    assert n["calls"] == 2
    assert _seat.idle_seconds("sess-b", now=1000.0) == 10.0       # another session: its own ask
    assert n["calls"] == 3


def test_is_idle_applies_our_threshold_not_sessions(monkeypatch):
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, "60")
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 59.0)
    assert _seat.is_idle("sess-a") is False
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 60.0)
    assert _seat.is_idle("sess-a") is True
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 0.0)   # busy
    assert _seat.is_idle("sess-a") is False
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, "nonsense")
    assert _seat.idle_threshold() == _seat.DEFAULT_IDLE_SECONDS == 1.0   # was 60


# ── idle-one-second ──────────────────────────────────────────────────────────

def _no_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("session get idle was shelled out to; the sidecar should have answered")
    monkeypatch.setattr(subprocess, "run", boom)


def _sidecar(tmp_path, monkeypatch, session, status, updated_ms, pid=4242):
    monkeypatch.setenv(_seat.SIDECAR_DIR_ENV, str(tmp_path))
    (tmp_path / f"{pid}.json").write_text(json.dumps(
        {"sessionId": session, "status": status, "updatedAt": updated_ms,
         "statusUpdatedAt": updated_ms, "pid": pid}))


def test_the_default_threshold_and_poll_are_one_second(monkeypatch):
    """One second: five added latency, most of all for voice. The 60 was an
    earlier match to the harness; the 5 was the cost of shelling out, which
    the sidecar read removes."""
    monkeypatch.delenv(_seat.IDLE_SECONDS_VAR, raising=False)
    assert _seat.DEFAULT_IDLE_SECONDS == 1.0
    assert _seat.idle_threshold() == 1.0
    assert _seat.IDLE_POLL_SECONDS == 1.0


def test_idle_seconds_reads_claude_codes_own_sidecar_and_never_shells_out(tmp_path, monkeypatch):
    """What it reads is Claude's own status: the
    per-session sidecar Claude Code writes under ~/.claude/sessions
    (<pid>.json, matched on sessionId), the same file `session list` reads.
    Rule unchanged from session-get: busy -> 0, else seconds since updatedAt."""
    _no_subprocess(monkeypatch)
    _seat._idle_cache.clear()
    now = 1_908_070_000.0
    _sidecar(tmp_path, monkeypatch, "s9", "idle", int((now - 7) * 1000))
    assert _seat.idle_seconds("s9", now) == pytest.approx(7.0)


def test_busy_in_the_sidecar_is_zero(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    _seat._idle_cache.clear()
    now = 1_908_070_000.0
    _sidecar(tmp_path, monkeypatch, "s9", "busy", int((now - 80) * 1000))
    assert _seat.idle_seconds("s9", now) == 0.0


def test_no_sidecar_for_the_session_falls_back_to_session_get_idle(tmp_path, monkeypatch):
    _seat._idle_cache.clear()
    _sidecar(tmp_path, monkeypatch, "someone-else", "idle", 1)
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"idle": 3}\n', stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert _seat.idle_seconds("s9", 1_908_070_000.0) == 3.0
    assert calls and calls[0][:3] == ["session", "get", "idle"]
