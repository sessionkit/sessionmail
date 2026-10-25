"""An env subscription (`SESSIONMAIL_SUBSCRIBE`, written by the launcher
into `mail.d/50-env.json`) must be in the alias map, not only in the
settings, and `message subscription list` must say which.

Every generator run rebuilds the WHOLE alias map from what it can see. A run
that misses one live session drops all of that session's rows and still exits
0. Two ways a run misses one, each closed in the generator and pinned here:

1. An older snapshot written last. The alias text was built BEFORE the lock,
   and the lock is a mkdir spin, not a queue, so a run whose read was older
   could write after a newer one. It is now built UNDER the lock.
2. A sidecar caught mid-rewrite (Claude Code rewrites them in place). It was
   skipped silently; it is now read once more, and named if it still fails.

And because a writer not yet named could drop a row too, the RESULT is
measured, not only the order: the detached generator at birth measures the
session's subscription rows after its run and runs once more if one is
missing (the born record's line 3 says what it found); the first `pager
listen`, seated or seatless, measures them again, rebuilds once, and says
`subscribe  : <box> NOT in map` if a row is still missing. A SessionStart
hook cannot wait out a generator, so the contract is: live by the first
listener, or a loud NOT in map.

Every test runs on `test_message_subscription`'s fleet: a mail root, maps and
HOME under tmp_path, fake postconf/postmap/ps and the REAL generator writing
under tmp (`--out`, `--no-postmap`) - never the live map. A scratch HOME alone
would not isolate sessionmail (SM-923); the fleet sets every override.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from _seatmap import session_env
from sessionmail import _birth, _seat
from test_message_subscription import HOST, OTHER, as_seat, fleet, make_live, rows  # noqa: F401
from test_pager_subscribe_at_birth import EX_TIMEOUT, NEW, banner, fresh, listen  # noqa: F401

def row(fleet, box: str) -> str:
    """The alias map's row for BOX@HOST, or "" when there is none."""
    return rows(fleet.valias).get(f"{box}@{HOST}", "")


def not_live(tmp_path: Path, sid: str) -> None:
    """SID's Claude Code sidecar gone: the generator does not see it live."""
    for car in (tmp_path / "claude-sessions").glob("*.json"):
        if json.loads(car.read_text())["sessionId"] == sid:
            car.unlink()


def seatless(monkeypatch) -> None:
    def no_seat(env=None):
        raise _seat.NoIdentity()
    monkeypatch.setattr(_seat, "resolve", no_seat)


def hook_birth(sid: str) -> _birth.Birth:
    """`pager hook start`'s birth (`pager.py:5406-5408 @ c703f9c`), run by hand
    with the hook's own arguments: the generator DETACHED, its line left in the
    born record. Waits for that line, so the test reads the map after it."""
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity:
        seat = None
    birth = _birth.ensure_session_box(sid, seat, make_root=False, regen="detach",
                                      record_source="startup", via="pager hook start")
    assert birth.regen == "detached", birth
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        lines = birth.record.read_text().splitlines()
        if len(lines) > 1:
            return birth
        time.sleep(0.05)
    pytest.fail(f"the detached generator wrote no line 2 to {birth.record}")


# ── the plus shape is not the cause ──────────────────────────────────────

def test_a_plus_box_at_birth_routes_like_a_bare_one(fresh, monkeypatch):
    """A plus box (`voicemode+airpods`) at birth is routed exactly as a bare
    one is, through the hook's detached birth and the listen after it: the
    box's shape is not what drops a row."""
    session_env(NEW, "voicemode+airpods voicemode", fresh.userhome)
    birth = hook_birth(NEW)
    assert "generator ran" in birth.record.read_text().splitlines()[1]
    assert row(fresh, "voicemode+airpods") == f"voicemode+airpods@{HOST}, {NEW}@{HOST}"
    assert row(fresh, "voicemode").endswith(f", {NEW}@{HOST}")
    assert listen().exit_code == EX_TIMEOUT


# ── 1. the birth's one run missed the session, and nothing re-runs it ─────

def test_a_seatless_env_subscription_missed_at_birth_is_put_in_the_map_by_the_listen(
        fresh, monkeypatch, tmp_path):
    """The production order on a seatless session: the SessionStart hook
    births the box and detaches the generator; the pager monitor's listen
    follows. The hook's run does not see the session live, so the generator
    refuses the alias map (exit 3). The detached child measures the
    subscription row after its run, runs ONCE more, and says in the born
    record that the row is NOT in the map. Once the session IS live, the
    monitor's listen finds the box born, measures the row (seatless too), rebuilds once, and the row is in the map."""
    seatless(monkeypatch)
    not_live(tmp_path, NEW)
    session_env(NEW, "atomnote", fresh.userhome)
    before = fresh.runs()
    birth = hook_birth(NEW)
    lines = birth.record.read_text().splitlines()
    assert "exit 3" in lines[1] and "do not show it live" in lines[1], lines
    # The child said so, and re-ran ONCE (bounded), never a loop.
    assert len(lines) == 3, lines
    assert lines[2].startswith("subscriptions: 0/1 in map; NOT in map: atomnote"), lines
    assert "ran again: generator exit 3" in lines[2], lines
    assert fresh.runs() == before + 2, "the child's run, and one re-run"
    assert NEW not in row(fresh, "atomnote")
    runs = fresh.runs()

    make_live(tmp_path, NEW, "worker")        # the session is live now
    result = listen()                          # the monitor's listen
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.runs() == runs + 1, "the listen rebuilt ONCE"
    assert any("atomnote NOT in map at this listen; rebuilt" in line
               for line in banner(result, "map")), result.stderr
    # THE ASK: the env subscription is in the map once the session is live.
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}", fresh.valias
    # And a listen that finds it in the map says nothing more, and runs nothing.
    again = listen()
    assert fresh.runs() == runs + 1, "a row in the map is not rebuilt"
    assert not any("atomnote" in line for line in banner(again, "map")), again.stderr


# ── 2. a run whose snapshot predates the birth writes after it ───────────

#: A `jq` in front of the real one. In the ONE run that carries SLOW_MARK,
#: it holds the live-session read's answer (the call with `--arg ps`,
#: `vmailbox-gen:828`) until the test says GO: that run's snapshot is taken,
#: and its write waits. Every other call is the real jq, unchanged.
HOLD_JQ = """#!/bin/sh
if [ -n "$SLOW_MARK" ] && [ ! -e "$SLOW_MARK" ]; then
  case " $* " in *" --arg ps "*)
    out="$("$REAL_JQ" "$@")"; rc=$?
    : > "$SLOW_MARK"
    while [ ! -e "$SLOW_GO" ]; do sleep 0.05; done
    printf '%s\\n' "$out"; exit $rc ;;
  esac
fi
exec "$REAL_JQ" "$@"
"""


def test_a_generator_snapshot_older_than_the_birth_must_not_drop_its_rows(
        fresh, monkeypatch, tmp_path):
    """Another seat's regen G_old snapshots the live sessions BEFORE NEW is
    live, and is still running when NEW's birth runs its own regen G_new.
    The snapshot used to be taken BEFORE the lock (`vmailbox-gen:1136-1143
    @ c703f9c`): G_new read, won the lock, wrote NEW's rows, and G_old wrote its
    older text over them. Now the snapshot is taken UNDER the lock: G_new
    waits for G_old to write, then reads (NEW is live by then) and writes last.
    The same sequence discriminates both: with the old order G_new finishes
    while G_old is held, and G_old's write drops the row."""
    real_jq = shutil.which("jq")
    hold = tmp_path / "bin" / "jq"
    hold.write_text(HOLD_JQ)
    hold.chmod(0o755)
    mark, go = tmp_path / "g_old.snapshot", tmp_path / "g_old.go"
    monkeypatch.setenv("REAL_JQ", real_jq)          # every run's jq; only G_old is held

    not_live(tmp_path, NEW)
    session_env(NEW, "atomnote", fresh.userhome)
    _seat.birth_box(fresh.agents, NEW)               # NEW's box, born (no regen yet)
    gen = os.environ[_seat.GEN_VAR]
    root = str(fresh.agents.parent)
    env = dict(os.environ, SLOW_MARK=str(mark), SLOW_GO=str(go),
               CLAUDE_CODE_SESSION_ID=OTHER)        # G_old runs in another live session
    g_old = subprocess.Popen([gen, "--root", root], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    g_new = None
    try:
        deadline = time.monotonic() + 30
        while not mark.exists():
            assert g_old.poll() is None, g_old.stdout.read()
            assert time.monotonic() < deadline, "G_old never took its snapshot"
            time.sleep(0.05)
        runs = fresh.runs()
        make_live(tmp_path, NEW, "worker")          # NEW is live; G_old's read is older
        g_new = subprocess.Popen([gen, "--root", root],
                                 env=dict(os.environ, CLAUDE_CODE_SESSION_ID=NEW),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        while fresh.runs() == runs:                  # G_new started
            assert time.monotonic() < deadline, "G_new never started"
            time.sleep(0.05)
        try:                                         # the old order: G_new is done in this time
            g_new.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            pass                                     # now: G_new waits on G_old's lock
    finally:
        go.touch()
        out_old, _ = g_old.communicate(timeout=60)
        out_new, _ = g_new.communicate(timeout=60) if g_new is not None else ("", "")
    assert g_old.returncode == 0, out_old
    assert g_new.returncode == 0, out_new
    # THE ASK: the newer run's rows survive the older run's write.
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}", fresh.valias
    assert row(fresh, "worker").endswith(f", {NEW}@{HOST}"), fresh.valias
    result = listen()                                # NEW's listen: the row is there
    assert result.exit_code == EX_TIMEOUT, result.output
    assert "doorbell   : LIVE" in result.stderr, result.stderr


# ── the ordering, the bounded re-run, the loud refusal ───────────────────

#: A generator in front of the fleet's: it records whether the session's
#: birth file (`mail.d/50-env.json`) exists when the run STARTS, then runs.
ORDER_GEN = """#!/bin/sh
if [ -f "$ENV_FILE" ]; then echo present >> "$ORDER_LOG"; else echo absent >> "$ORDER_LOG"; fi
exec "$FLEET_GEN" "$@"
"""

#: A generator whose FIRST run (or every run, with MISS_ALWAYS) writes
#: nothing and exits 0: a run that missed the session still says
#: `generator ran`.
MISS_GEN = """#!/bin/sh
if [ -n "$MISS_ALWAYS" ] || [ ! -e "$MISS_MARK" ]; then
  : > "$MISS_MARK"; echo "$*" >> "$GEN_LOG"; exit 0
fi
exec "$FLEET_GEN" "$@"
"""


def front_gen(monkeypatch, tmp_path, name: str, text: str) -> Path:
    """Put TEXT in front of the fleet's generator wrapper."""
    path = tmp_path / "bin" / name
    path.write_text(text)
    path.chmod(0o755)
    monkeypatch.setenv("FLEET_GEN", os.environ[_seat.GEN_VAR])
    monkeypatch.setenv(_seat.GEN_VAR, str(path))
    return path


def drop_from_row(fleet, box: str, sid: str) -> None:
    """What a run that missed SID leaves: BOX's row without SID's copy."""
    key, dest = f"{box}@{HOST}", f", {sid}@{HOST}"
    text = "".join(line.replace(dest, "") + "\n" if line.startswith(key + "\t") else line + "\n"
                   for line in fleet.valias.splitlines())
    (fleet.maps / "valias").write_text(text)
    assert sid not in row(fleet, box)


def test_the_birth_file_is_written_before_the_births_generator_runs(fresh, monkeypatch, tmp_path):
    """The ordering: the launcher writes `50-env.json`,
    THEN the SessionStart hook births and runs the generator, which reads it.
    The run that routes the subscription found the file in place."""
    env_file = session_env(NEW, "atomnote", fresh.userhome)
    order = tmp_path / "order.log"
    front_gen(monkeypatch, tmp_path, "order-gen", ORDER_GEN)
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("ORDER_LOG", str(order))
    birth = hook_birth(NEW)
    assert order.read_text().split() == ["present"], "the birth's run found the file"
    lines = birth.record.read_text().splitlines()
    assert "generator ran" in lines[1], lines
    assert lines[2] == "subscriptions: 1/1 in map", lines
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}"


def test_a_birth_file_written_after_the_run_is_routed_by_the_first_listen(fresh, monkeypatch):
    """The order inverted (a late launcher): the hook's run found no birth
    file, so it routed nothing. The first listen measures the session's
    subscription rows, finds atomnote missing, and rebuilds ONCE."""
    birth = hook_birth(NEW)
    assert len(birth.record.read_text().splitlines()) == 2, "nothing subscribed: no line 3"
    session_env(NEW, "atomnote", fresh.userhome)        # written late
    assert NEW not in row(fresh, "atomnote")
    runs = fresh.runs()
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.runs() == runs + 1
    assert any("atomnote NOT in map at this listen" in line for line in banner(result, "map")), \
        result.stderr
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}"


def test_the_detached_child_reruns_once_when_its_run_missed_the_row(fresh, monkeypatch, tmp_path):
    """In the detached child: the birth's run says `generator ran` and wrote nothing (the
    session missed). The child measures, runs ONCE more, and the record's
    line 3 says what it found and what it did."""
    session_env(NEW, "atomnote", fresh.userhome)
    front_gen(monkeypatch, tmp_path, "miss-gen", MISS_GEN)
    monkeypatch.setenv("MISS_MARK", str(tmp_path / "missed"))
    before = fresh.runs()
    birth = hook_birth(NEW)
    lines = birth.record.read_text().splitlines()
    assert "generator ran" in lines[1], lines
    assert lines[2].startswith("subscriptions: 1/1 in map (NOT in map after the first run: "
                               "atomnote; ran again: generator ran"), lines
    assert fresh.runs() == before + 2
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}"


def test_a_row_still_missing_after_one_rebuild_is_said_loudly(fresh, monkeypatch, tmp_path):
    """At the listen, the loud refusal: a born, seated box whose subscription row is
    gone (the doorbell is LIVE: its seat row is there). The listen rebuilds
    ONCE; the row is still missing, so the banner says NOT in map, and no
    second rebuild runs."""
    session_env(NEW, "atomnote", fresh.userhome)
    assert listen().exit_code == EX_TIMEOUT                    # born, routed
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}"
    drop_from_row(fresh, "atomnote", NEW)
    front_gen(monkeypatch, tmp_path, "miss-gen", MISS_GEN)
    monkeypatch.setenv("MISS_MARK", str(tmp_path / "missed"))
    monkeypatch.setenv("MISS_ALWAYS", "1")
    runs = fresh.runs()
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert "doorbell   : LIVE" in result.stderr, result.stderr
    assert fresh.runs() == runs + 1, "ONE rebuild, never a loop"
    loud = [line for line in banner(result) if "NOT in map" in line]
    assert len(loud) == 1 and loud[0].startswith("  subscribe  : atomnote NOT in map"), \
        result.stderr
    assert "this session does NOT hear atomnote" in loud[0]


# ── a sidecar caught mid-rewrite is read again ───────────────────────────

#: A `jq` in front of the real one. The first read of the sidecar at
#: $HALF_CAR sees it HALF-written (Claude Code rewrites sidecars in place);
#: the file is whole again by the time the read returns, so a second read
#: sees it whole. With HALF_ALWAYS, every read is of the half file.
HALF_JQ = """#!/bin/sh
for a in "$@"; do last="$a"; done
if [ -n "$HALF_CAR" ] && [ "$last" = "$HALF_CAR" ] && [ ! -e "$HALF_MARK" -o -n "$HALF_ALWAYS" ]; then
  : > "$HALF_MARK"
  whole="$(cat "$HALF_CAR")"
  printf '%s' "$whole" | head -c 20 > "$HALF_CAR"
  "$REAL_JQ" "$@"; rc=$?
  [ -n "$HALF_ALWAYS" ] || printf '%s' "$whole" > "$HALF_CAR"
  exit $rc
fi
exec "$REAL_JQ" "$@"
"""


def sidecar_of(tmp_path: Path, sid: str) -> Path:
    for car in (tmp_path / "claude-sessions").glob("*.json"):
        if json.loads(car.read_text())["sessionId"] == sid:
            return car
    raise AssertionError(f"no sidecar for {sid}")


def half_jq(monkeypatch, tmp_path, sid: str) -> Path:
    real_jq = shutil.which("jq")                  # BEFORE the wrapper is on PATH
    hold = tmp_path / "bin" / "jq"
    hold.write_text(HALF_JQ)
    hold.chmod(0o755)
    monkeypatch.setenv("REAL_JQ", real_jq)
    monkeypatch.setenv("HALF_CAR", str(sidecar_of(tmp_path, sid)))
    mark = tmp_path / "half.read"
    monkeypatch.setenv("HALF_MARK", str(mark))
    return mark


def run_gen(fleet) -> subprocess.CompletedProcess:
    return subprocess.run([os.environ[_seat.GEN_VAR], "--root", str(fleet.agents.parent)],
                          capture_output=True, text=True, timeout=30)


def test_a_sidecar_half_written_on_the_first_read_keeps_its_row(fleet, monkeypatch, tmp_path):
    """IVAN_SID's sidecar is caught mid-rewrite on the first read
    and whole on the second. Its session stays live in the run, and its seat
    row keeps its copy (it used to be skipped silently)."""
    from test_message_subscription import IVAN_SID
    assert row(fleet, "ivan").endswith(f", {IVAN_SID}@{HOST}")
    mark = half_jq(monkeypatch, tmp_path, IVAN_SID)
    done = run_gen(fleet)
    assert done.returncode == 0, done.stderr
    assert mark.exists(), "the half read happened"
    assert "could not be read twice" not in done.stderr, done.stderr
    assert row(fleet, "ivan") == f"ivan@{HOST}, {IVAN_SID}@{HOST}", fleet.valias


def test_a_sidecar_unreadable_twice_is_named_not_dropped_silently(fleet, monkeypatch, tmp_path):
    """A sidecar that is half-written on BOTH reads: the session is not
    counted live in this run (its sid is inside the unreadable file, so no
    row can be kept for it without a guess), and stderr NAMES the file."""
    from test_message_subscription import IVAN_SID
    car = sidecar_of(tmp_path, IVAN_SID)
    half_jq(monkeypatch, tmp_path, IVAN_SID)
    monkeypatch.setenv("HALF_ALWAYS", "1")
    done = run_gen(fleet)
    assert done.returncode == 0, done.stderr
    assert f"sidecar {car} could not be read twice" in done.stderr, done.stderr
    assert IVAN_SID not in row(fleet, "ivan")


# ── `subscription list` says what the MAP routes ─────────────────────────

def sub_list():
    from click.testing import CliRunner
    from sessionmail.message import cli
    return CliRunner().invoke(cli, ["subscription", "list"])


def line_for(out: str, label: str) -> str:
    found = [line for line in out.splitlines() if line.lstrip("│├└─ ").split()[:1] == [label]]
    assert len(found) == 1, out
    return found[0]


def test_subscription_list_says_in_map_and_not_in_map(fleet):
    """Both ways, on the fleet's fake postmap: a row the map copies
    into this session says `in map`; once a run drops it, `NOT in map`,
    while the settings still say subscribed."""
    from test_message_subscription import SID, sub
    assert sub("add", "atomnote").exit_code == 0
    done = sub_list()
    assert done.exit_code == 0, done.output
    assert line_for(done.stdout, "atomnote").endswith("; in map"), done.stdout
    assert line_for(done.stdout, "worker").endswith("; in map"), done.stdout   # the seat row
    assert "map not measured" not in done.stdout

    drop_from_row(fleet, "atomnote", SID)
    done = sub_list()
    assert done.exit_code == 0, done.output
    assert line_for(done.stdout, "atomnote").endswith("; NOT in map"), done.stdout
    assert line_for(done.stdout, "worker").endswith("; in map"), done.stdout
    assert fleet.subscribed(SID) == ["atomnote"], "the settings still say subscribed"


def test_subscription_list_never_guesses_without_a_map(fleet, monkeypatch, tmp_path):
    """No postconf/postmap to ask: every row says `map not measured`, and the
    footer says why once. Never `in map` by assumption."""
    from test_message_subscription import sub
    assert sub("add", "atomnote").exit_code == 0
    monkeypatch.setenv("PATH", f"{os.path.dirname(shutil.which('jq'))}:/usr/bin:/bin")
    done = sub_list()
    assert done.exit_code == 0, done.output
    assert line_for(done.stdout, "atomnote").endswith("; map not measured"), done.stdout
    assert "in map" not in done.stdout.replace("map not measured", "")
    assert done.stdout.rstrip().splitlines()[-1].endswith(
        "; map not measured: no postconf/postmap on PATH to read the alias maps"), done.stdout


# ── a listen that cannot measure the map says so ─────────────────────────

NO_POSTFIX_WHY = "no postconf/postmap on PATH to read the alias maps"


def test_a_listen_whose_map_cannot_be_measured_says_so_per_box(fresh, monkeypatch):
    """No postconf/postmap to ask: the listen cannot measure the
    subscription rows, so it says so, once per box, in `subscription list`'s
    words. Never silence (a silently deaf seat is this task), and no rebuild
    on a guess."""
    session_env(NEW, "atomnote voicemode", fresh.userhome)
    assert listen().exit_code == EX_TIMEOUT                    # born, routed
    monkeypatch.setenv("PATH", f"{os.path.dirname(shutil.which('jq'))}:/usr/bin:/bin")
    runs = fresh.runs()
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.runs() == runs, "nothing measured, nothing rebuilt"
    said = [line for line in banner(result) if "map not measured" in line]
    assert said == [f"  subscribe  : atomnote map not measured ({NO_POSTFIX_WHY})",
                    f"  subscribe  : voicemode map not measured ({NO_POSTFIX_WHY})"], \
        result.stderr


def test_a_listen_whose_rows_are_all_in_the_map_says_nothing_new(fresh, monkeypatch):
    """The other way: every row measured in the map, so the banner gains no
    `subscribe` line and no `map` line at the listen after the birth."""
    session_env(NEW, "atomnote", fresh.userhome)
    assert listen().exit_code == EX_TIMEOUT                    # born, routed
    assert row(fresh, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}"
    runs = fresh.runs()
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.runs() == runs
    assert banner(result) == [] and banner(result, "map") == [], result.stderr
    assert "map not measured" not in result.stderr


def settle(monkeypatch, checks: list[_birth.SubCheck], rebuilt: list) -> _birth.Birth:
    """`_settle_listen` alone, its measures answered by CHECKS in order."""
    from sessionmail import _subscribe
    answers = iter(checks)
    monkeypatch.setattr(_birth, "subscription_check", lambda session, env=None: next(answers))
    monkeypatch.setattr(_subscribe, "rebuild",
                        lambda env=None: rebuilt.append(1) or (True, "rebuilt"))
    birth = _birth.Birth()
    _birth._settle_listen(birth, NEW, {})
    return birth


def test_unmeasured_after_the_rebuild_names_the_missing_boxes(monkeypatch):
    """Missing at the first measure; the rebuild runs, and the second measure
    cannot ask the map: the boxes found missing say `map not measured`, never
    a `now in map` nor a `NOT in map` on a guess."""
    monkeypatch.setattr(_birth, "_on_drop", lambda env: False)
    rebuilt: list = []
    birth = settle(monkeypatch, [_birth.SubCheck(boxes=["a", "b"], missing=["b"]),
                                 _birth.SubCheck(boxes=["a", "b"], why="gone")], rebuilt)
    assert rebuilt == [1]
    assert birth.sub_lines == ["  subscribe  : b map not measured (gone)"]


def test_unreadable_subscriptions_say_one_line_with_no_box(monkeypatch):
    """The subscriptions themselves could not be read: no box list to name,
    so one line says the map was not measured, and why."""
    monkeypatch.setattr(_birth, "_on_drop", lambda env: False)
    rebuilt: list = []
    birth = settle(monkeypatch, [_birth.SubCheck(why="the session's subscriptions could "
                                                     "not be read: boom")], rebuilt)
    assert rebuilt == []
    assert birth.sub_lines == ["  subscribe  : map not measured (the session's "
                               "subscriptions could not be read: boom)"]


def test_the_mail_drop_listen_stays_silent_by_design(monkeypatch):
    """The mail drop keeps no compiled map (it reads the generator's rows per
    send), so its silence is not an unmeasured map: nothing is measured or
    said there, even where a measure would have failed."""
    monkeypatch.setattr(_birth, "_on_drop", lambda env: True)
    rebuilt: list = []
    birth = settle(monkeypatch, [_birth.SubCheck(boxes=["a"], why="gone")], rebuilt)
    assert rebuilt == [] and birth.sub_lines == []
