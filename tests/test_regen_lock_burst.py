"""Writing regens in a burst: queued runs coalesce, and no run gives up.

The generator serialises writers with a mkdir lock. The alias text is built
UNDER that lock, so a no-older-snapshot-wins guarantee holds, and that read
is the slow part of a run. Measured over a scratch root of about 4700 boxes,
with N concurrent writing runs each doing its own read behind the last: at
N=40 a third of the runs gave up on the lock's 10 s spin, at N=80 two thirds.

So queued runs coalesce. Each writing run leaves a request beside the lock;
the run that takes the lock lists the requests BEFORE its read and, AFTER its
write, answers each with the exit code that run would have had. A waiter
whose request is answered exits with it and writes nothing. A request made
after a holder's listing is left for the next holder, whose read is later.
The spin's bound stays, as a clock (about 10 s by `$SECONDS`), for a holder
stuck past it.

This module checks that contract, scaled down so the suite stays fast:

- **The holder is real and slow.** One run takes the lock and is held at a
  chosen point by a fake tool on PATH: `ps` (the first thing the live-session
  reader asks, so the hold falls after the holder's listing and snapshot) or
  `find` (the request dirs' housekeeping, just before the listing, so the
  requests started meanwhile are the holder's to answer). The `find` hold
  waits on a gate file the test opens, so no step depends on a race.
- **The spin's naps are dropped.** `sleep` on PATH is `true`. The bound is a
  clock, so the shim makes the spin busier, not shorter: a queued run still
  gives up only past its 10 s.
- **A session is born while the holder holds**, after the holder's snapshot.
  The queued runs start after the birth, so the map they leave behind must
  carry the new session's row.

Hermetic: the root, the map dir (`--out`), HOME, TMPDIR and the sidecars are
all under `tmp_path`; `--no-postmap`; `SESSIONMAIL_POSTCONF` names a missing
file, so the host's postfix is never asked. Nothing live is read or written.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from importlib.resources import as_file, files
from pathlib import Path

import pytest

HOST = "testhost"
SUFFIX = "example.ts.net"

#: procStart as Claude Code writes it (UTC ctime), and `ps` in UTC agreeing.
STARTED = "Sun Jun 09 12:00:00 2030"

#: Seconds the holder keeps the lock: many times the shortened spin bound.
HOLD = 2.0

#: Queued runs started behind the holder.
WAITERS = 6

FAKE_PS = """#!/bin/sh
# `ps -axo pid=,lstart=`, answered from the fixture table. A run started with
# FAKE_PS_HOLD set is the HOLDER: it snapshots the table, marks that it holds,
# then waits before answering, so its read (and so the lock) is held long.
t="$(cat "$FAKE_PS_TABLE")"
if [ -n "${FAKE_PS_HOLD:-}" ]; then
  : > "$FAKE_PS_HOLDING"
  /bin/sleep "$FAKE_PS_HOLD"
fi
printf '%s\\n' "$t"
"""

#: `sleep` on the generator's PATH: `true`, so a spin step costs one fork.
TRUE = shutil.which("true") or "/usr/bin/true"

#: The real `find`, which the gate shim below runs once it is let through.
FIND = shutil.which("find") or "/usr/bin/find"

GATED_FIND = """#!/bin/sh
# `find`, except in a run started with FAKE_FIND_GATE set: that run marks that
# it holds (FAKE_FIND_HOLDING), then waits for the gate file to appear before
# it goes on. The generator's only `find` is the housekeeping it runs right
# after taking the lock and right before listing the queued requests.
if [ -n "${FAKE_FIND_GATE:-}" ]; then
  : > "$FAKE_FIND_HOLDING"
  n=0
  while [ ! -e "$FAKE_FIND_GATE" ] && [ "$n" -lt 600 ]; do
    /bin/sleep 0.05; n=$((n + 1))
  done
fi
exec %s "$@"
""" % FIND


@pytest.fixture(scope="module")
def generator() -> Path:
    """The generator as the package ships it."""
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as path:
        yield Path(path)


def _maildir(path: Path) -> None:
    for leaf in ("new", "cur", "tmp"):
        (path / leaf).mkdir(parents=True, exist_ok=True)


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Fleet:
    """A scratch mail root, a map dir, sidecars and the fakes on PATH."""

    def __init__(self, tmp: Path, generator: Path) -> None:
        self.tmp = tmp
        self.generator = generator
        self.root = tmp / "root"
        self.out = tmp / "maps" / "vmailbox"
        self.sidecars = tmp / "claude-sessions"
        self.sidecars.mkdir()
        self.table = tmp / "ps.txt"
        self.table.write_text("")
        self.holding = tmp / "holding"
        self.bin = tmp / "bin"
        self.bin.mkdir()
        _exe(self.bin / "ps", FAKE_PS)
        _exe(self.bin / "find", GATED_FIND)
        (self.bin / "sleep").symlink_to(TRUE)
        self.gate = tmp / "gate"
        self.find_holding = tmp / "find-holding"
        (tmp / "home").mkdir()
        _maildir(self.root / "agents" / "worker")      # the seat word's box
        self.logs = tmp / "logs"
        self.logs.mkdir()

    def birth(self, pid: int, sid: str) -> None:
        """A live session with a box: its sidecar, then its `ps` row."""
        _maildir(self.root / "agents" / sid)
        car = {"pid": pid, "sessionId": sid, "procStart": STARTED, "agent": "worker"}
        (self.sidecars / f"{pid}.json").write_text(json.dumps(car))
        with self.table.open("a") as table:
            table.write(f"{pid:>5} {STARTED}    \n")

    def start(self, name: str, hold: float | None = None, *, args: tuple[str, ...] = (),
              gated: bool = False, sid: str | None = None, root: Path | None = None,
              path: str | None = None, new_session: bool = False) -> subprocess.Popen:
        """One writing run. `hold` holds it in `ps` (after its listing),
        `gated` holds it in `find` until `open_gate` (before its listing)."""
        env = {
            "PATH": path or f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.tmp / "home"),
            "TMPDIR": str(self.tmp),
            "MAGICDNS_SUFFIX": SUFFIX,
            "LC_ALL": "C",
            "SESSION_SIDECAR_DIR": str(self.sidecars),
            "SESSIONMAIL_HOME": str(self.tmp / "sessionmail"),
            "SESSIONMAIL_POSTCONF": str(self.tmp / "no-postconf"),
            "FAKE_PS_TABLE": str(self.table),
            "FAKE_PS_HOLDING": str(self.holding),
        }
        if hold is not None:
            env["FAKE_PS_HOLD"] = str(hold)
        if gated:
            env["FAKE_FIND_GATE"] = str(self.gate)
            env["FAKE_FIND_HOLDING"] = str(self.find_holding)
        if sid is not None:
            env["CLAUDE_CODE_SESSION_ID"] = sid
        return subprocess.Popen(
            [str(self.generator), "--host", HOST, "--root", str(root or self.root),
             "--out", str(self.out), "--no-postmap", *args],
            stdout=(self.logs / f"{name}.out").open("w"),
            stderr=(self.logs / f"{name}.err").open("w"),
            env=env,
            start_new_session=new_session,
        )

    def open_gate(self) -> None:
        self.gate.write_text("")

    def stdout(self, name: str) -> str:
        return (self.logs / f"{name}.out").read_text()

    def stderr(self, name: str) -> str:
        return (self.logs / f"{name}.err").read_text()

    def last_err(self, name: str) -> str:
        lines = self.stderr(name).strip().splitlines()
        return lines[-1] if lines else ""

    @property
    def reqdir(self) -> Path:
        return self.out.parent / ".vmailbox-gen.req"

    @property
    def donedir(self) -> Path:
        return self.out.parent / ".vmailbox-gen.done"

    def requests(self) -> list[str]:
        return sorted(p.name for p in self.reqdir.iterdir()) if self.reqdir.is_dir() else []

    def wait_for_requests(self, n: int, seconds: float = 20) -> None:
        deadline = time.monotonic() + seconds
        while len(self.requests()) < n:
            assert time.monotonic() < deadline, f"only {self.requests()} of {n} requests appeared"
            time.sleep(0.02)

    def leftovers(self) -> list[str]:
        """Anything the protocol left behind: the lock, a request, an answer."""
        left = [p.name for d in (self.reqdir, self.donedir) if d.is_dir() for p in d.iterdir()]
        if (self.out.parent / ".vmailbox-gen.lock").exists():
            left.append(".vmailbox-gen.lock")
        return left


@pytest.fixture
def fleet(tmp_path: Path, generator: Path) -> Fleet:
    return Fleet(tmp_path, generator)


def _wait_for(path: Path, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.02)


def test_the_rig_alone_writes_both_maps(fleet: Fleet) -> None:
    """With the fakes in place and no one holding the lock, one run goes
    through: exit 0, both maps written, the live session's row present. So
    the burst below fails on the lock, never on a broken rig."""
    fleet.birth(40000, "s-one")
    lone = fleet.start("lone")
    assert lone.wait(timeout=30) == 0, fleet.stderr("lone")
    assert fleet.out.is_file()
    assert "s-one@" in (fleet.out.parent / "valias").read_text()


def test_a_burst_behind_a_slow_holder_loses_no_run_and_keeps_the_newest_birth(
        fleet: Fleet) -> None:
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", hold=HOLD)
    _wait_for(fleet.holding, 15)               # it holds the lock, snapshot taken

    fleet.birth(40001, "s-born")               # after the holder's snapshot
    waiters = [fleet.start(f"waiter-{i}") for i in range(WAITERS)]

    rcs = {"holder": holder.wait(timeout=60)}
    for i, w in enumerate(waiters):
        rcs[f"waiter-{i}"] = w.wait(timeout=60)

    gave_up = sorted(n for n in rcs if "could not acquire regen lock" in fleet.stderr(n))
    assert gave_up == [], f"runs that gave up on the lock: {gave_up}; rcs {rcs}"
    assert set(rcs.values()) == {0}, rcs

    valias = (fleet.out.parent / "valias").read_text()
    assert "s-born@" in valias, "the burst left a map without the session born under the holder"

    # The waiters coalesced: fewer runs wrote than ran, and those that did not
    # write said whose answer they took.
    wrote = [n for n in rcs if "wrote " in fleet.stdout(n) and "coalesced" not in fleet.stdout(n)]
    coalesced = [n for n in rcs if "coalesced: " in fleet.stdout(n)]
    assert len(wrote) < len(rcs), f"every run wrote its own maps: {wrote}"
    assert coalesced, "no waiter was answered by a holder"
    assert fleet.leftovers() == []


def _path_without(tmp: Path, fleet: Fleet, name: str) -> str:
    """A PATH with every tool on today's PATH except `name`: the fakes first,
    then one dir of links to the rest."""
    farm = tmp / f"path-without-{name}"
    farm.mkdir()
    for d in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        try:
            entries = list(Path(d).iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name == name or (farm / e.name).exists() or (farm / e.name).is_symlink():
                continue
            if e.is_file() and os.access(e, os.X_OK):
                (farm / e.name).symlink_to(e)
    return f"{fleet.bin}:{farm}"


def _queue_behind_gate(fleet: Fleet, holder: subprocess.Popen,
                       waiters: dict[str, subprocess.Popen]) -> None:
    """The holder holds the lock before its listing; every waiter's request is
    in; then the holder is let through, so it lists them all."""
    fleet.wait_for_requests(1 + len(waiters))
    assert holder.poll() is None, "the holder finished before its gate opened"
    fleet.open_gate()


def test_a_coalesced_waiter_exits_with_the_holders_outcome_and_reason(
        tmp_path: Path, fleet: Fleet) -> None:
    """A holder whose valias is refused (no jq) answers a whole and an
    alias-only request with its exit 3, the refusal's reason as their LAST
    stderr line (the line a caller reports), and a mailbox-only request
    with 0, since the mailbox map was written."""
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", gated=True, path=_path_without(tmp_path, fleet, "jq"))
    _wait_for(fleet.find_holding, 15)
    waiters = {
        "whole": fleet.start("whole"),
        "alias": fleet.start("alias", args=("--valias-only",)),
        "mailbox": fleet.start("mailbox", args=("--no-valias",)),
    }
    _queue_behind_gate(fleet, holder, waiters)

    assert holder.wait(timeout=60) == 3, fleet.stderr("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert rcs == {"whole": 3, "alias": 3, "mailbox": 0}, {n: fleet.stderr(n) for n in rcs}
    for name in ("whole", "alias"):
        assert "no 'jq' on PATH" in fleet.last_err(name), fleet.stderr(name)
        assert "valias NOT regenerated" in fleet.last_err(name)
    for name in rcs:
        assert "coalesced" in fleet.stdout(name) + fleet.stderr(name), name
    assert "wrote " not in fleet.stdout("whole") + fleet.stdout("alias")
    assert fleet.out.is_file(), "the holder wrote the mailbox map"
    assert not (fleet.out.parent / "valias").exists(), "a refused valias was written"
    assert fleet.leftovers() == []


def test_a_holder_terminated_mid_run_answers_nothing(fleet: Fleet) -> None:
    """A holder that has listed the queued requests and is then TERMed (the
    Python caller's budget) answers none of them. It releases the lock, a
    waiter takes it and runs for itself, and every waiter exits 0 with a map
    that a completed run wrote.

    The hold is short because a bash 3.2 holder acts on the TERM only once
    the child it is waiting on (the fake `ps`) exits; bash 5 acts at once.
    Either way it runs its EXIT trap and dies before it writes anything."""
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", hold=3, gated=True, new_session=True)
    _wait_for(fleet.find_holding, 15)
    waiters = {f"waiter-{i}": fleet.start(f"waiter-{i}") for i in range(3)}
    _queue_behind_gate(fleet, holder, waiters)
    _wait_for(fleet.holding, 15)                       # listed them, now in its read
    try:
        holder.send_signal(signal.SIGTERM)             # bash only, as the caller does
        assert holder.wait(timeout=30) in (-signal.SIGTERM, 128 + signal.SIGTERM)
    finally:
        try:
            os.killpg(holder.pid, signal.SIGKILL)      # its fake `ps`, still napping
        except ProcessLookupError:
            pass

    assert "wrote " not in fleet.stdout("holder"), fleet.stdout("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert set(rcs.values()) == {0}, {n: fleet.stderr(n) for n in rcs}
    holder_pid = f"run {holder.pid} "
    for name in rcs:
        assert holder_pid not in fleet.stdout(name), f"{name} took the dead holder's answer"
    ran = [n for n in rcs if "coalesced" not in fleet.stdout(n)]
    assert ran, "no waiter ran for itself after the holder died"
    assert "s-one@" in (fleet.out.parent / "valias").read_text()
    assert fleet.leftovers() == []


def test_an_alias_only_holder_does_not_answer_a_whole_request(fleet: Fleet) -> None:
    """A `--valias-only` holder writes no mailbox map, so it answers the
    alias-only request it listed and leaves the whole one to its waiter,
    which then takes the lock and writes both maps itself."""
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", gated=True, args=("--valias-only",))
    _wait_for(fleet.find_holding, 15)
    waiters = {"whole": fleet.start("whole"), "alias": fleet.start("alias", args=("--valias-only",))}
    _queue_behind_gate(fleet, holder, waiters)

    assert holder.wait(timeout=60) == 0, fleet.stderr("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert rcs == {"whole": 0, "alias": 0}, {n: fleet.stderr(n) for n in rcs}
    assert f"coalesced: run {holder.pid} wrote" in fleet.stdout("alias")
    assert "coalesced" not in fleet.stdout("whole")
    assert f"wrote {fleet.out} " in fleet.stdout("whole")
    assert fleet.out.is_file()
    assert fleet.leftovers() == []


@pytest.mark.parametrize("differs", ["root", "valias-static"])
def test_runs_whose_maps_would_differ_are_never_coalesced(
        tmp_path: Path, fleet: Fleet, differs: str) -> None:
    """Two runs into one map dir with a different root, or a different static
    alias file, would write different maps: neither answers the other. A run
    with the holder's inputs is answered, as the control."""
    fleet.birth(40000, "s-one")
    other: dict = {}
    if differs == "root":
        other_root = tmp_path / "other-root"
        _maildir(other_root / "agents" / "elsewhere")
        other["root"] = other_root
    else:
        static = tmp_path / "valias.static"
        static.write_text("standing@testhost\tworker@testhost\n")
        other["args"] = ("--valias-static", str(static))
    holder = fleet.start("holder", gated=True)
    _wait_for(fleet.find_holding, 15)
    waiters = {"same": fleet.start("same"), "other": fleet.start("other", **other)}
    _queue_behind_gate(fleet, holder, waiters)

    assert holder.wait(timeout=60) == 0, fleet.stderr("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert rcs == {"same": 0, "other": 0}, {n: fleet.stderr(n) for n in rcs}
    assert f"coalesced: run {holder.pid} wrote" in fleet.stdout("same")
    assert "coalesced" not in fleet.stdout("other")
    assert f"wrote {fleet.out} " in fleet.stdout("other")
    if differs == "root":
        assert "elsewhere@" in fleet.out.read_text()
    else:
        assert "standing@testhost" in (fleet.out.parent / "valias").read_text()
    assert fleet.leftovers() == []


def test_a_request_whose_session_is_not_live_is_answered_3(fleet: Fleet) -> None:
    """A request made inside a session the holder's live list does not show
    is answered as that run's own self-check would answer it: exit 3, the
    self-check's words as its last stderr line. A request from a live
    session is answered 0. The holder's own map is written."""
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", gated=True)
    _wait_for(fleet.find_holding, 15)
    waiters = {"ghost": fleet.start("ghost", sid="s-ghost"), "live": fleet.start("live", sid="s-one")}
    _queue_behind_gate(fleet, holder, waiters)

    assert holder.wait(timeout=60) == 0, fleet.stderr("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert rcs == {"ghost": 3, "live": 0}, {n: fleet.stderr(n) for n in rcs}
    tail = fleet.last_err("ghost")
    assert "run inside session s-ghost" in tail and "do not show it live" in tail, tail
    assert "coalesced" in fleet.stdout("live")
    assert "s-one@" in (fleet.out.parent / "valias").read_text()
    assert fleet.leftovers() == []


def test_a_stale_request_or_answer_is_pruned(fleet: Fleet) -> None:
    """Requests and answers left by killed runs (minutes old) are removed by
    the next run that takes the lock; a fresh run is never answered by one."""
    fleet.birth(40000, "s-one")
    fleet.reqdir.mkdir(parents=True)
    fleet.donedir.mkdir(parents=True)
    old = time.time() - 600
    for path in (fleet.reqdir / "1-2.ma.-.1.1", fleet.donedir / "1-2.ma.-.1.1",
                 fleet.donedir / ".1-2.a.-.2.2"):
        path.write_text("0 stale\n")
        os.utime(path, (old, old))
    lone = fleet.start("lone")
    assert lone.wait(timeout=30) == 0, fleet.stderr("lone")
    assert "coalesced" not in fleet.stdout("lone")
    assert fleet.leftovers() == []


def test_a_holder_refused_by_its_own_session_check_does_not_answer_for_others(
        fleet: Fleet) -> None:
    """A holder run inside a session its live list does not show refuses
    valias for ITSELF: the check is "is my own session in the list". That
    says nothing about a waiter whose session IS in the list, or one run
    outside any session. Each would have written valias on its own run, so
    the holder leaves those requests to their waiters, which take the lock
    and write it. A waiter whose own session is missing too still exits 3,
    told about its own session, never about the holder's."""
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", gated=True, sid="s-gone")
    _wait_for(fleet.find_holding, 15)
    waiters = {
        "live": fleet.start("live", sid="s-one"),
        "nosid": fleet.start("nosid"),
        "ghost": fleet.start("ghost", sid="s-ghost"),
        "mailbox": fleet.start("mailbox", args=("--no-valias",), sid="s-one"),
    }
    _queue_behind_gate(fleet, holder, waiters)

    assert holder.wait(timeout=60) == 3, fleet.stderr("holder")
    assert "run inside session s-gone" in fleet.last_err("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert rcs == {"live": 0, "nosid": 0, "ghost": 3, "mailbox": 0}, \
        {n: fleet.stderr(n) for n in rcs}
    for name in rcs:
        assert "s-gone" not in fleet.stderr(name), f"{name} was told the holder's reason"
    assert "run inside session s-ghost" in fleet.last_err("ghost"), fleet.stderr("ghost")
    assert f"coalesced: run {holder.pid} wrote" in fleet.stdout("mailbox")
    for name in ("live", "nosid"):
        assert f"run {holder.pid} " not in fleet.stdout(name), name
    assert "s-one@" in (fleet.out.parent / "valias").read_text()
    assert fleet.leftovers() == []


def test_a_holder_refused_before_its_list_answers_waiters_with_a_session_its_reason(
        tmp_path: Path, fleet: Fleet) -> None:
    """A holder refused before it has a live list (here: no jq) cannot tell a
    live session from a gone one, so it never answers a request as "not
    live". Every alias request, with a session id or without, is answered
    with the holder's own refusal as its LAST stderr line: the reason that
    applies to it, and the one its caller reports."""
    fleet.birth(40000, "s-one")
    holder = fleet.start("holder", gated=True, sid="s-one",
                         path=_path_without(tmp_path, fleet, "jq"))
    _wait_for(fleet.find_holding, 15)
    waiters = {
        "live": fleet.start("live", sid="s-one"),
        "ghost": fleet.start("ghost", args=("--valias-only",), sid="s-ghost"),
    }
    _queue_behind_gate(fleet, holder, waiters)

    assert holder.wait(timeout=60) == 3, fleet.stderr("holder")
    rcs = {n: w.wait(timeout=60) for n, w in waiters.items()}
    assert rcs == {"live": 3, "ghost": 3}, {n: fleet.stderr(n) for n in rcs}
    for name in rcs:
        assert "no 'jq' on PATH" in fleet.last_err(name), fleet.stderr(name)
        assert "run inside session" not in fleet.stderr(name), fleet.stderr(name)
        assert "coalesced" in fleet.stderr(name), name
    assert not (fleet.out.parent / "valias").exists(), "a refused valias was written"
    assert fleet.leftovers() == []
