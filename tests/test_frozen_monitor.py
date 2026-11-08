"""SM-1004 do-003: the frozen-turn watch wired into the pager monitor
(design.md 2.4, 2.5 and 2.7, in the task store).

- The episode guard: one notice per frozen TURN (O2-5), a resolve recorded
  and never announced, and the stamp `<session dir>/mail/stamps/frozen.json`
  that keeps a re-`exec`ed monitor from noticing the same turn twice.
- The notice: ONE `MonitorOut.say` line, the `frozen:` gauge in `pager
  status`, and the stamp. NO MAIL (O2-6): `notify` is a no-op seam.
- The wiring: `Watcher.tick` right after `follow_session` in both monitor
  loops, at most once per CHECK_SECONDS, the switch read at every look,
  never raising, and never in a bare `pager listen`.

Monitor-level runs are hermetic, in test_pager_monitor.py's style and with
its rig: SC1 1a, 1b (with D-1's enqueues), 1d, 1f and the 2b-over-2a case;
SC4 4a-4e; and the No-children scenario. ZERO SENDS IS THE DETECTOR'S
(decompose-001 re-review, note 3): the arm's own `listening` confirm still
goes through `pager._mta_send`, so the asserts are that no send carries
`Pager-Note: frozen` and that the sends with the switch on equal the sends
with it off over the same fixture.

Every made-up moment is derived from the fixtures' BASE, the project's
earlier synthetic Sunday. The wall clock the watch judges at is injected
through `_frozen.wall_clock`, never by patching the time module.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from frozen_fixtures import Script, epoch, frozen_turn
from test_pager_monitor import DEADLINE, driven, lines, monitor, until
from test_pager_monitor import rig as monitor_rig  # noqa: F401 - a fixture, by this name

from sessionmail import _frozen, _settings, pager
from sessionmail._frozen import Episode, Verdict, gauge
from sessionmail._settings import CHAIN, SILENCE
from sessionmail.pager import cli

SESSION = "sess-a"          # the rig's session
FROZEN = "looked frozen mid-turn"   # in the say-line, and nowhere else


# ── the rig: a transcript, a busy sidecar, a host file, a fake wall clock ──

def host_file(data: dict) -> Path:
    """The host mail.json, with the rig's `hooks: true` kept."""
    p = Path(os.environ["HOME"]) / _settings.RELATIVE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "hooks": True, **data}) + "\n")
    return p


def sidecar(directory: Path, pid: int, status: str = "busy",
            session: str = SESSION) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{pid}.json.tmp"
    tmp.write_text(json.dumps({"pid": pid, "sessionId": session, "status": status,
                               "updatedAt": int(time.time() * 1000)}))
    os.replace(tmp, directory / f"{pid}.json")
    return directory / f"{pid}.json"


class Frozen:
    """What a monitor-level case drives and reads."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.config = tmp_path / "claude"
        self.sidecars = tmp_path / "sidecars"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.config))
        monkeypatch.setenv("SESSION_SIDECAR_DIR", str(self.sidecars))
        for var in (_settings.API_TIMEOUT_VAR, _settings.FIRST_BYTE_VAR):
            monkeypatch.delenv(var, raising=False)
        sidecar(self.sidecars, os.getppid())     # the monitor's caller, under CliRunner
        monkeypatch.setattr(_frozen, "CHECK_SECONDS", 0.05)
        self.clock = [epoch(0)]
        monkeypatch.setattr(_frozen, "wall_clock", lambda: self.clock[0])
        # Every wall value that reached judge, and every send the run made.
        self.judged_at: list[float] = []
        real = _frozen.judge_each

        def recording(state, status, now, settings, session=""):
            self.judged_at.append(now)
            return real(state, status, now, settings, session)

        monkeypatch.setattr(_frozen, "judge_each", recording)
        self.sent: list = []

        def mta(req, env, mint=None):
            self.sent.append(req)
            return f"<sent-{len(self.sent)}@test.invalid>"

        monkeypatch.setattr(pager, "_mta_send", mta)
        self.notified: list = []
        monkeypatch.setattr(_frozen, "notify", lambda n: self.notified.append(n))
        self.script: Script | None = None
        self.path: Path | None = None
        self.written = 0

    def transcript(self, script: Script) -> Path:
        self.script = script
        self.path = script.write(self.config, SESSION)
        self.written = len(script.entries)
        return self.path

    def append(self) -> None:
        """Append what the script gained since the last write."""
        assert self.script is not None and self.path is not None
        self.written = self.script.append(self.path, self.written)

    def at(self, offset: float) -> float:
        self.clock[0] = epoch(offset)
        return self.clock[0]

    def stamp(self) -> dict | None:
        return _frozen.read_stamp(SESSION)

    def stamp_file(self) -> Path:
        p = _frozen.stamp_path(SESSION)
        assert p is not None
        return p

    def frozen_sends(self) -> list:
        return [r for r in self.sent
                if any(name == pager.PAGER_NOTE and "frozen" in str(value).lower()
                       for name, value in r.headers)]


@pytest.fixture
def fz(request, tmp_path, monkeypatch) -> Frozen:
    request.getfixturevalue("monitor_rig")     # test_pager_monitor's rig, first
    return Frozen(tmp_path, monkeypatch)


def said(result) -> list[str]:
    return [ln for ln in lines(result) if FROZEN in ln]


def looks(n: int = 3) -> None:
    """Room for N more looks at the rig's 0.05 s check interval."""
    time.sleep(0.12 * n)


def run(script, runner: "CliRunner | None" = None):
    t = driven(script)
    result = monitor(runner=runner)
    t.join(DEADLINE)
    assert result.exit_code == 0, (result.exit_code, result.stderr)
    return result


# ── SC1: a fake frozen turn gives exactly ONE notice ───────────────────────

def test_1a_one_say_line_one_stamp_the_gauge_and_zero_frozen_sends(fz):
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    now = fz.at(1 + 721)

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed")), "never noticed"
        looks(5)

    result = run(script)
    assert len(said(result)) == 1, result.stdout
    line = said(result)[0]
    assert "rule silence" in line and "12m" in line, line
    assert "no keys" in line and "no mail" in line, line
    assert len(line) <= pager.MONITOR_LINE_MAX
    stamp = fz.stamp()
    assert stamp is not None
    assert stamp["rule"] == SILENCE and stamp["message_id"] is None
    assert stamp["key"] == [SESSION, SILENCE, epoch(1)]
    assert stamp["onset"] == _frozen.iso(epoch(1))
    assert stamp["noticed"] == _frozen.iso(now) and stamp["episodes"] == 1
    assert stamp["resolved"] is None and list(stamp["active"]) == [SILENCE]
    assert stamp["checked"] == {"judged": True, "why": ""}
    # One stamp file, and nothing left half-written beside it.
    assert sorted(p.name for p in fz.stamp_file().parent.glob("*frozen*")) == ["frozen.json"]
    # The gauge reads `silent 12m`, from the stamp and the clock.
    status = pager.frozen_status_line(SESSION, now=now)
    assert status.startswith("frozen: silent 12m mid-turn since "), status
    assert f"noticed {_frozen._hhmm(now)}" in status, status
    # The notice seam ran once; the detector sent nothing.
    assert len(fz.notified) == 1 and fz.notified[0].rule == SILENCE
    assert fz.frozen_sends() == []
    assert len(fz.sent) == 1, "only the arm's listening confirm"
    # Note 6: the wall clock reaching judge is the injected one, never the
    # monotonic one (seconds since boot would never be frozen on a seat).
    assert fz.judged_at and set(fz.judged_at) == {now}, fz.judged_at[:3]
    assert abs(fz.judged_at[0] - time.monotonic()) > 1e8


def test_1b_frozen_for_another_three_n_with_pages_arriving_still_one_notice(fz):
    """1b, with 1c's D-1 at monitor level: enqueues and a queued_command
    keep landing while the clock runs on for 3 x N; one say-line, one
    notice, and the episode's anchor never moves."""
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed")), "never noticed"
        assert fz.script is not None
        for step in range(1, 13):           # 12 x 180 s = 3 x 720 s on
            offset = 1 + 721 + step * 180
            fz.script.enqueue(offset - 1)
            if step % 4 == 0:
                fz.script.queued_command(offset - 1)
            fz.append()
            fz.at(offset)
            looks(1)
        looks(3)

    result = run(script)
    assert len(said(result)) == 1, result.stdout
    assert len(fz.notified) == 1
    stamp = fz.stamp()
    assert stamp is not None and stamp["key"] == [SESSION, SILENCE, epoch(1)]
    assert stamp["episodes"] == 1 and stamp["resolved"] is None
    assert fz.frozen_sends() == []
    assert pager.frozen_status_line(SESSION, now=fz.clock[0]).startswith(
        "frozen: silent 48m mid-turn since ")


def test_1d_a_monitor_that_starts_again_on_the_same_turn_says_nothing_more(fz):
    """The stamp is the guard across a re-`exec`: the exec replaces the
    process, so a second monitor run on the same session, the same turn,
    starts with a fresh Watcher and only the stamp to go on."""
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)

    def first() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed"))
        looks(2)

    assert len(said(run(first))) == 1
    before = fz.stamp()
    fz.at(1 + 721 + 300)                     # still frozen, later

    def second() -> None:
        assert until(lambda: len(fz.judged_at) >= 8)
        looks(3)

    again = run(second)
    assert said(again) == [], again.stdout
    assert len(fz.notified) == 1
    after = fz.stamp()
    assert after is not None and before is not None
    assert after["noticed"] == before["noticed"] and after["episodes"] == 1
    assert fz.frozen_sends() == []
    assert len(fz.sent) == 2, "one listening confirm per arm, nothing else"


def test_1f_a_second_episode_in_the_same_turn_updates_the_stamp_and_says_nothing(fz):
    """1f, with the resolve between: an assistant entry lands (the falling
    edge, recorded and never announced), then a fresh N of silence in the
    same turn (a second episode: stamp and gauge, no second say-line)."""
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    first = fz.at(1 + 721)
    resolved_at: list[dict] = []

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed"))
        assert fz.script is not None
        fz.script.assistant(800)
        fz.append()
        fz.at(801)
        assert until(lambda: (fz.stamp() or {}).get("resolved")), "resolve not recorded"
        resolved_at.append(dict(fz.stamp() or {}))
        fz.at(800 + 721)
        assert until(lambda: (fz.stamp() or {}).get("episodes") == 2), "no second episode"
        looks(3)

    result = run(script)
    assert len(said(result)) == 1, result.stdout
    assert len(fz.notified) == 1
    gap = resolved_at[0]
    assert gap["active"] == {} and gap["resolved"] == _frozen.iso(epoch(801))
    assert "G4" in gap["resolved_why"], gap["resolved_why"]
    stamp = fz.stamp()
    assert stamp is not None
    assert stamp["key"] == [SESSION, SILENCE, epoch(800)]
    assert stamp["onset"] == _frozen.iso(epoch(800)) and stamp["resolved"] is None
    assert stamp["noticed"] == _frozen.iso(first), "the turn's notice is the first's"
    status = pager.frozen_status_line(SESSION, now=fz.clock[0])
    assert status.startswith("frozen: silent 12m mid-turn since "), status
    assert fz.frozen_sends() == []


def test_a_chain_episode_overlapping_a_silence_one_says_nothing_more(fz):
    """Decompose-001 re-review, note 5 (design 2.4, O2-5): with both rules
    on, rule 2a holds at +722 s, then rule 2b joins it at +902 s in the
    same turn. The stamp and gauge show both; there is no second line."""
    host_file({"frozen_watch": True, "frozen_rules": [SILENCE, CHAIN]})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed"))
        fz.at(1 + 901)
        assert until(lambda: len((fz.stamp() or {}).get("active") or {}) == 2)
        looks(3)

    result = run(script)
    assert len(said(result)) == 1, result.stdout
    assert len(fz.notified) == 1 and fz.notified[0].rule == SILENCE
    stamp = fz.stamp()
    assert stamp is not None
    assert sorted(stamp["active"]) == [CHAIN, SILENCE] and stamp["episodes"] == 2
    assert stamp["rule"] == CHAIN and stamp["key"] == [SESSION, CHAIN, epoch(1)]
    status = pager.frozen_status_line(SESSION, now=fz.clock[0])
    assert "(rule chain, silence;" in status and "R 900 s" not in status, status
    assert fz.frozen_sends() == []


def test_a_seatless_monitor_ticks_the_watch_in_the_no_queue_loop(fz, monkeypatch):
    """The no-queue loop (no seat, so no queue) ticks the watch too, right
    after `follow_session`: the same one notice, and the stamp under the
    session, which needs no seat."""
    from sessionmail import _seat

    def no_seat(*a, **k):
        raise _seat.NoIdentity()

    monkeypatch.setattr(_seat, "resolve", no_seat)
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed")), "never noticed"
        looks(4)

    result = run(script)
    assert len(said(result)) == 1, result.stdout
    assert len(fz.notified) == 1 and fz.frozen_sends() == []


# ── SC4: the switch off means nothing ──────────────────────────────────────

@pytest.mark.parametrize("data,ignored", [
    ({}, False),                                   # 4a: no key
    ({"frozen_watch": False}, False),              # 4b
    ({"frozen_watch": "true"}, True),              # 4c
    ({"frozen_watch": 1}, True),                   # 4c
], ids=["4a-absent", "4b-false", "4c-string", "4c-one"])
def test_4abc_off_no_line_no_stamp_and_the_gauge_says_off(fz, data, ignored):
    host_file(data)
    fz.transcript(frozen_turn())
    now = fz.at(1 + 721)

    def script() -> None:
        looks(8)

    result = run(script)
    assert said(result) == [] and fz.notified == []
    assert not fz.stamp_file().exists(), "off writes no stamp"
    assert fz.judged_at == [], "off judges nothing"
    assert fz.frozen_sends() == [] and len(fz.sent) == 1
    status = pager.frozen_status_line(SESSION, now=now)
    assert status.startswith("frozen: off (frozen_watch is not true)"), status
    if ignored:
        assert "is ignored: only the boolean true turns it on" in status, status
    else:
        assert status == "frozen: off (frozen_watch is not true)", status


def test_sends_with_the_switch_on_equal_sends_with_it_off(fz):
    """The detector adds zero sends: same fixture, same run, on and off."""
    fz.transcript(frozen_turn())
    fz.at(1 + 721)
    counts = {}
    for on in (False, True):
        host_file({"frozen_watch": on})
        fz.sent.clear()

        def script() -> None:
            if on:
                assert until(lambda: (fz.stamp() or {}).get("noticed"))
            looks(6)

        result = run(script)
        assert len(said(result)) == (1 if on else 0), result.stdout
        counts[on] = len(fz.sent)
    assert counts[True] == counts[False] == 1, counts
    assert fz.frozen_sends() == []


def test_4d_switched_off_mid_episode_says_nothing_more_and_the_gauge_says_off(fz):
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)
    frozen_at: list[dict] = []

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed"))
        host_file({"frozen_watch": False})
        looks(2)                              # the next look reads it: off
        frozen_at.append(fz.stamp() or {})
        seen = len(fz.judged_at)
        assert fz.script is not None
        fz.script.assistant(800)             # what would resolve it, and
        fz.append()
        fz.at(800 + 721)                     # a fresh episode after it
        looks(6)
        assert len(fz.judged_at) == seen, "an off watch judged"

    result = run(script)
    assert len(said(result)) == 1, result.stdout
    assert fz.stamp() == frozen_at[0], "an off watch wrote the stamp"
    assert pager.frozen_status_line(SESSION, now=fz.clock[0]) == (
        "frozen: off (frozen_watch is not true)")
    assert fz.frozen_sends() == []


def test_4e_a_bare_listen_never_ticks_the_watch(fz, monkeypatch):
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)
    made: list = []
    real = _frozen.Watcher.__init__

    def init(self, *a, **k):
        made.append(a)
        real(self, *a, **k)

    monkeypatch.setattr(_frozen.Watcher, "__init__", init)
    ticks: list = []
    monkeypatch.setattr(_frozen.Watcher, "tick", lambda self, *a, **k: ticks.append(a))
    rigged = Path(os.environ["SESSIONMAIL_ROOT"], "stores") / SESSION
    for d in ("tmp", "new", "cur"):
        (rigged / d).mkdir(parents=True, exist_ok=True)
    result = CliRunner().invoke(cli, ["listen", "--timeout", "0.6"], prog_name="pager")
    assert result.exit_code == pager.EX_TIMEOUT, (result.exit_code, result.stderr)
    assert made == [] and ticks == []
    assert not fz.stamp_file().exists()


# ── No children: the detector spawns nothing ───────────────────────────────

def test_the_watch_spawns_no_child(fz, monkeypatch):
    """The ratified *No children* scenario, for the detector: no process is
    started from inside a look, over the whole wait, with the switch on and
    a frozen turn noticed. (The arm's confirm, before the wait, is the
    arm's, and is not inside a look.)"""
    host_file({"frozen_watch": True, "frozen_rules": [SILENCE, CHAIN]})
    fz.transcript(frozen_turn())
    fz.at(1 + 721)
    inside = [False]
    spawned: list[str] = []
    real_look = _frozen.Watcher.look

    def look(self, wall):
        inside[0] = True
        try:
            return real_look(self, wall)
        finally:
            inside[0] = False

    monkeypatch.setattr(_frozen.Watcher, "look", look)
    real_popen = subprocess.Popen.__init__

    def popen(self, *a, **k):
        if inside[0]:
            spawned.append(f"Popen {a[:1]}")
        return real_popen(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "__init__", popen)
    for name in ("fork", "posix_spawn", "posix_spawnp", "system", "spawnv", "spawnvp"):
        real = getattr(os, name, None)
        if real is None:
            continue

        def guarded(*a, _name=name, _real=real, **k):
            if inside[0]:
                spawned.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(os, name, guarded)

    def script() -> None:
        assert until(lambda: (fz.stamp() or {}).get("noticed"))
        fz.at(1 + 901)
        assert until(lambda: len((fz.stamp() or {}).get("active") or {}) == 2)
        looks(3)

    result = run(script)
    assert len(said(result)) == 1
    assert len(fz.judged_at) >= 3
    assert spawned == [], spawned


# ── the episode guard, unit level ──────────────────────────────────────────

def held(rule: str, anchor: float, now: float, turn: str = "p1") -> Verdict:
    return Verdict(rule, True, (SESSION, rule, anchor), f"{rule}: held", turn, anchor,
                   now - anchor)


def unheld(rule: str = SILENCE, why: str = "G4: under N") -> Verdict:
    return Verdict(rule, False, None, why, "p1")


@pytest.fixture
def stamps(request):
    """The rig's session dir, for unit-level stamp writes."""
    return request.getfixturevalue("monitor_rig")


def test_episode_rising_edge_notices_once_and_writes_the_stamp(stamps):
    ep = Episode.load(SESSION)
    t0 = epoch(1)
    n = ep.step([held(SILENCE, t0, t0 + 721)], t0 + 721)
    assert n is not None and n.rule == SILENCE and n.key == (SESSION, SILENCE, t0)
    assert ep.step([held(SILENCE, t0, t0 + 751)], t0 + 751) is None, "same key: no edge"
    stamp = _frozen.read_stamp(SESSION)
    assert stamp is not None and stamp["episodes"] == 1 and stamp["noticed_turn"] == "p1"


def test_episode_first_check_with_nothing_held_writes_a_watching_stamp(stamps):
    ep = Episode.load(SESSION)
    assert ep.step([unheld()], epoch(5)) is None
    stamp = _frozen.read_stamp(SESSION)
    assert stamp is not None and stamp["active"] == {} and stamp["noticed"] is None
    mtime = _frozen.stamp_path(SESSION).stat().st_mtime_ns
    time.sleep(0.01)
    ep.step([unheld(why="G4: other words")], epoch(35))
    assert _frozen.stamp_path(SESSION).stat().st_mtime_ns == mtime, "no change, no write"


def test_episode_a_new_turn_is_noticed_again(stamps):
    ep = Episode.load(SESSION)
    t0 = epoch(1)
    assert ep.step([held(SILENCE, t0, t0 + 721)], t0 + 721) is not None
    ep.step([unheld()], t0 + 800)
    t1 = epoch(900)
    n = ep.step([held(SILENCE, t1, t1 + 721, turn="p2")], t1 + 721)
    assert n is not None and n.turn == "p2"
    assert _frozen.read_stamp(SESSION)["episodes"] == 1


def test_episode_unjudged_ends_the_episode_and_says_why(stamps):
    ep = Episode.load(SESSION)
    t0 = epoch(1)
    ep.step([held(SILENCE, t0, t0 + 721)], t0 + 721)
    assert ep.step([held(SILENCE, t0, t0 + 751)], t0 + 751, unjudged="2 transcripts match") is None
    stamp = _frozen.read_stamp(SESSION)
    assert stamp["active"] == {} and stamp["resolved"] == _frozen.iso(t0 + 751)
    assert stamp["checked"] == {"judged": False, "why": "2 transcripts match"}
    assert "2 transcripts match" in stamp["resolved_why"]


def test_episode_a_moved_anchor_is_a_new_episode_in_the_same_turn(stamps):
    ep = Episode.load(SESSION)
    t0, t1 = epoch(1), epoch(100)
    assert ep.step([held(SILENCE, t0, t0 + 721)], t0 + 721) is not None
    assert ep.step([held(SILENCE, t1, t1 + 721)], t1 + 721) is None
    stamp = _frozen.read_stamp(SESSION)
    assert stamp["episodes"] == 2 and stamp["key"] == [SESSION, SILENCE, t1]


def test_episode_reloads_from_the_stamp(stamps):
    t0 = epoch(1)
    Episode.load(SESSION).step([held(SILENCE, t0, t0 + 721)], t0 + 721)
    again = Episode.load(SESSION)
    assert again.active == {SILENCE: t0}
    assert again.step([held(SILENCE, t0, t0 + 999)], t0 + 999) is None


def test_read_stamp_refuses_another_version_or_session(stamps):
    _frozen.write_stamp(SESSION, {"version": 99, "session": SESSION})
    assert _frozen.read_stamp(SESSION) is None
    _frozen.write_stamp(SESSION, {"version": _frozen.STAMP_VERSION, "session": "other"})
    assert _frozen.read_stamp(SESSION) is None
    _frozen.stamp_path(SESSION).write_text("{not json")
    assert _frozen.read_stamp(SESSION) is None
    assert _frozen.stamp_path("") is None and _frozen.read_stamp("-") is None


def test_the_stamp_lives_beside_the_other_stamps(stamps):
    from sessionmail import _seat
    p = _frozen.stamp_path(SESSION)
    assert p == _seat.session_root(SESSION) / _seat.STAMPS_DIR / "frozen.json"


# ── the gauge, unit level ──────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def resolved(home, data=None, **env):
    from frozen_fixtures import watch
    return watch(home, data, **env)


def test_gauge_watching_with_no_stamp_yet(home):
    w = resolved(home)
    assert gauge(w, None, epoch(0)) == "frozen: watching (silence; N 720 s); no check recorded yet"


def test_gauge_watching_names_r_only_when_chain_runs(home):
    w = resolved(home, {"frozen_watch": True, "frozen_rules": ["silence", "chain"]})
    stamp = {"active": {}, "checked": {"judged": True, "why": ""}}
    assert gauge(w, stamp, epoch(0)) == "frozen: watching (silence, chain; N 720 s, R 900 s)"


def test_gauge_not_judged_says_why(home):
    w = resolved(home)
    stamp = {"active": {}, "checked": {"judged": False, "why": "no transcript matches X"}}
    assert gauge(w, stamp, epoch(0)) == "frozen: not judged (no transcript matches X)"


def test_gauge_names_the_floor(home):
    """2n end to end: N asked 300 s under API_TIMEOUT_MS=300000 is floored
    to 600 s, and the gauge says so."""
    w = resolved(home, {"frozen_watch": True, "frozen_after_seconds": 300},
                 API_TIMEOUT_MS="300000")
    line = gauge(w, {"active": {}, "checked": {"judged": True, "why": ""}}, epoch(0))
    assert line.startswith("frozen: watching (silence; N 600 s); "), line
    assert "frozen_after_seconds 300 s is raised to 600 s, the floor" in line, line


def test_gauge_off_with_an_unreadable_file_says_why(home):
    from sessionmail import _settings as s
    p = s.path({"HOME": str(home)})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    w = s.frozen({"HOME": str(home)})
    want = f"frozen: off (frozen_watch is not true); {s.SHOWN} is not JSON"
    assert gauge(w, None, epoch(0)) == want


def test_gauge_after_a_resolve_says_watching_and_the_last_episode(home):
    w = resolved(home)
    stamp = {"active": {}, "checked": {"judged": True, "why": ""}, "rule": SILENCE,
             "onset": _frozen.iso(epoch(1)), "resolved": _frozen.iso(epoch(800))}
    line = gauge(w, stamp, epoch(900))
    assert line == (f"frozen: watching (silence; N 720 s); last episode silence from "
                    f"{_frozen._hhmm(epoch(1))}, resolved {_frozen._hhmm(epoch(800))}")


def test_gauge_a_chain_episode_says_requests_unanswered(home):
    w = resolved(home, {"frozen_watch": True, "frozen_rules": ["chain"]})
    stamp = {"active": {CHAIN: {"anchor": epoch(1)}}, "noticed": _frozen.iso(epoch(902)),
             "checked": {"judged": True, "why": ""}, "resolved": None}
    line = gauge(w, stamp, epoch(1 + 960))
    assert line.startswith("frozen: requests unanswered 16m mid-turn since "), line
    assert "(rule chain; noticed" in line


@pytest.mark.usefixtures("monitor_rig")
def test_frozen_status_line_never_raises(monkeypatch):
    def broken(env=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(_settings, "frozen", broken)
    assert pager.frozen_status_line(SESSION) == "frozen: could not be read - RuntimeError('boom')"


@pytest.mark.usefixtures("monitor_rig")
def test_frozen_status_line_with_no_session():
    host_file({"frozen_watch": True})
    assert pager.frozen_status_line("-").startswith("frozen: not judged (no session id")


def test_pager_status_prints_the_frozen_line(fz):
    host_file({"frozen_watch": True})
    rigged = Path(os.environ["SESSIONMAIL_ROOT"], "stores") / SESSION
    for d in ("tmp", "new", "cur"):
        (rigged / d).mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(cli, ["status"], prog_name="pager")
    got = [ln for ln in r.stderr.splitlines() if ln.startswith("frozen:")]
    assert got == ["frozen: watching (silence; N 720 s); no check recorded yet"], r.stderr
    assert "frozen:" not in r.stdout


# ── the sidecar word, and the watcher's own guarantees ─────────────────────

@pytest.mark.parametrize("body,want", [
    ({"sessionId": SESSION, "status": "busy"}, "busy"),
    ({"sessionId": SESSION, "status": "idle"}, "idle"),
    ({"sessionId": "other", "status": "busy"}, None),    # a /clear not yet followed
    ({"status": "busy"}, None),
    ({"sessionId": SESSION, "status": 3}, None),
    ("{not json", None),
    ("[1]", None),
])
def test_sidecar_status_reads_the_raw_word(tmp_path, body, want):
    d = tmp_path / "sc"
    d.mkdir()
    (d / "77.json").write_text(body if isinstance(body, str) else json.dumps(body))
    assert _frozen.sidecar_status(77, SESSION, {"SESSION_SIDECAR_DIR": str(d)}) == want
    assert _frozen.sidecar_status(78, SESSION, {"SESSION_SIDECAR_DIR": str(d)}) is None


def test_the_watcher_looks_at_most_once_per_check_interval(fz, monkeypatch):
    monkeypatch.setattr(_frozen, "CHECK_SECONDS", 30.0)
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    fz.at(10)
    w = _frozen.Watcher(SESSION, os.getppid(), lambda line: None)
    for now in (100.0, 110.0, 129.9, 130.0, 131.0, 160.0):
        w.tick(now)
    assert w.looks == 3, "looks at 100, 130 and 160 only"


def test_the_watcher_never_raises_and_logs_once_per_kind(fz, monkeypatch):
    host_file({"frozen_watch": True})
    logged: list[str] = []

    def boom(*a, **k):
        raise ValueError("bad")

    monkeypatch.setattr(_frozen, "transcript", boom)
    w = _frozen.Watcher(SESSION, os.getppid(), lambda line: None, log=logged.append)
    for i in range(4):
        assert w.tick(float(i)) is None
    assert logged == ["frozen watch: ValueError: bad"]


def test_the_watcher_takes_an_injected_wall_clock(fz):
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    lines_said: list[str] = []
    w = _frozen.Watcher(SESSION, os.getppid(), lines_said.append,
                        wall=lambda: epoch(1 + 721))
    n = w.tick(0.0)
    assert n is not None and len(lines_said) == 1
    assert fz.judged_at == [epoch(1 + 721)]


def test_a_watcher_with_two_transcripts_is_not_judged(fz):
    host_file({"frozen_watch": True})
    frozen_turn().write(fz.config, SESSION, project="a")
    frozen_turn().write(fz.config, SESSION, project="b")
    w = _frozen.Watcher(SESSION, os.getppid(), lambda line: None, wall=lambda: epoch(722))
    assert w.tick(0.0) is None
    stamp = fz.stamp()
    assert stamp is not None and stamp["checked"]["judged"] is False
    assert "2 transcripts match" in stamp["checked"]["why"]
    assert pager.frozen_status_line(SESSION, now=epoch(722)).startswith(
        "frozen: not judged (2 transcripts match ")


def test_notify_is_a_no_op_seam():
    assert _frozen.notify.__module__ == "sessionmail._frozen"
    n = _frozen.Notice(SESSION, SILENCE, (SESSION, SILENCE, 1.0), "p", 1.0, 721.0, "w", 722.0)
    assert _frozen.notify(n) is None


def test_a_stamp_that_cannot_be_written_still_says_the_line_once(fz, monkeypatch):
    """A failed stamp write (a full disk, a mode) is logged once and costs
    the seat nothing it is owed: the turn's one line is still said, never
    twice, and the stamp is written at the first look that can."""
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    real = _frozen.write_stamp
    broken = [True]

    def write(session, data, env=None):
        if broken[0]:
            raise PermissionError("stamps dir is read-only")
        return real(session, data, env)

    monkeypatch.setattr(_frozen, "write_stamp", write)
    lines_said: list[str] = []
    logged: list[str] = []
    w = _frozen.Watcher(SESSION, os.getppid(), lines_said.append, log=logged.append,
                        wall=lambda: epoch(1 + 721))
    assert w.tick(0.0) is not None, "the notice survives the failed write"
    w.tick(100.0)
    assert len(lines_said) == 1 and len(fz.notified) == 1
    assert logged == ["frozen watch: PermissionError: stamps dir is read-only"]
    assert fz.stamp() is None
    broken[0] = False
    w.tick(200.0)                     # nothing new to say, but the write is owed
    stamp = fz.stamp()
    assert stamp is not None and stamp["noticed_turn"] is not None
    assert list(stamp["active"]) == [SILENCE] and len(lines_said) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a 0500 dir")
def test_a_read_only_stamps_dir_on_disk_still_says_the_line_once(fz):
    """The same fault as above with no stand-in for write_stamp: the stamps
    dir itself refuses the write, so the real path (make_dir, the temporary
    file, the rename) fails as it would on a seat. The line is said once,
    the error is logged once, nothing half-written is left behind, and the
    stamp lands once the dir can be written."""
    from sessionmail import _seat
    host_file({"frozen_watch": True})
    fz.transcript(frozen_turn())
    stamps = fz.stamp_file().parent
    _seat.make_dir(stamps, SESSION)
    stamps.chmod(0o500)
    try:
        lines_said: list[str] = []
        logged: list[str] = []
        w = _frozen.Watcher(SESSION, os.getppid(), lines_said.append, log=logged.append,
                            wall=lambda: epoch(1 + 721))
        assert w.tick(0.0) is not None, "the notice survives the refused write"
        w.tick(100.0)                 # still refused: owed, and not logged twice
        assert len(lines_said) == 1 and len(fz.notified) == 1
        assert len(logged) == 1 and logged[0].startswith("frozen watch: PermissionError:")
        assert list(stamps.iterdir()) == [], "no stamp and no temporary file"
    finally:
        stamps.chmod(0o700)
    w.tick(200.0)
    stamp = fz.stamp()
    assert stamp is not None and list(stamp["active"]) == [SILENCE]
    assert [p.name for p in stamps.iterdir()] == [_frozen.STAMP]
    assert len(lines_said) == 1 and len(logged) == 1
