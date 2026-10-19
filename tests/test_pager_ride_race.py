"""SM-102: the ride queue's `new/` goes missing and the listener dies of it.

A deaf re-arm that LOOKS like it fired: the banner prints, then a traceback,
then exit 1. Five crashes in the wild, most of them at boot.

The WRITER is the scoop, `_seat.take_queue` (`src/sessionmail/_seat.py:1272-
1273 @ 3c918c9`)::

    os.rename(new, taking)    # new/ is now GONE
    new.mkdir()               # new/ is back

Between those two syscalls `ride/<sid>/new` does not exist, and the scoop
runs in the ride-along hook - a DIFFERENT process from the listener. Every
reader and writer of the queue that assumes the directory is there can land
in that window. The READERS, at 3c918c9:

    src/sessionmail/pager.py:959   len(os.listdir(queue))            banner
    src/sessionmail/pager.py:1104  for name in os.listdir(queue)     watch loop
    src/sessionmail/pager.py:1148  _detect.link_into(fresh, [queue])
      src/sessionmail/_detect.py:247  `not q.is_dir()` -> NoSuchBox (:248)
      src/sessionmail/_detect.py:252  os.link(...)     -> FileNotFoundError
    src/sessionmail/_seat.py:1354  queue_names: iterdir, then os.listdir(d)

Both shapes the card names are here, and neither fix covers the other:

  * FIRST BOOT - a fresh seat on a backlogged box, its queue made by
    `queue_new` and swapped away before the listener's first look;
  * MID-LIFE   - the queue vanishes under a listener that has been armed
    and looping for a while.

Every window here is INJECTED, deterministically, on a throwaway root (the
`rig` fixture: tmp_path mail root and SESSIONMAIL_HOME) - never the live
~/.mail or ~/.sessionmail. The listener-side windows are made by hand, as an
OLDER installed scoop would make them (`rename new -> taking.*`), because
those scoops stay on disk after any fix lands: the listener has to survive
the window whether or not this repo's scoop still opens it. The scoop-side
test measures this repo's scoop directly.

These tests were written to FAIL at 3c918c9 (repro-001) and to pass once
fix-001 lands. None of them encodes HOW the fix is built beyond what the
card's success criteria demand: nothing crashes, nothing is lost, and the
scoop never removes new/.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import traceback
from pathlib import Path

import pytest

from sessionmail import _detect, _seat
from test_pager_listen import (  # noqa: F401  (fixture reused)
    EX_COULD_NOT_LOOK,
    EX_PAGE,
    EX_TIMEOUT,
    counted,
    rig,
    run,
)

SESSION = "sess-a"


# ── helpers ──────────────────────────────────────────────────────────────

def _queue(rig, session: str = SESSION) -> Path:
    return _seat.ride_dir(rig.seat, session) / _seat.QUEUE_NEW


def _open_window(queue: Path) -> Path:
    """What an OLD scoop does first: `mv new taking.<sid>.<pid>`. The pid is
    one that cannot be alive, so a later scoop treats it as a leftover and
    recovers it - the window is the only thing under test, not the loss."""
    taking = queue.parent / f"{_seat.TAKING_PREFIX}{SESSION}.999999999"
    os.rename(queue, taking)
    return taking


def _survived(result) -> str:
    """The failure message when a listener crashed: its own traceback, so the
    test output names the line that fell over."""
    exc = result.exception
    if exc is None or isinstance(exc, SystemExit):
        return ""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def assert_listener_survived(result, *codes: int) -> None:
    tb = _survived(result)
    assert not tb, f"the listener CRASHED (exit {result.exit_code}):\n{tb}\n{result.stderr}"
    assert result.exit_code in codes, (result.exit_code, result.stderr)


# ── 1. the scoop: the writer that opens the window ───────────────────────

def test_the_scoop_never_leaves_new_absent(rig, monkeypatch):
    """Card criterion 2, "the scoop is proven not to remove the directory
    (test, not inspection)". Every directory-moving syscall the scoop makes
    is watched, and after EACH ONE `new/` must still be a directory - the
    listener, in another process, can look at any instant.

    At 3c918c9 this fails on the scoop's own claim, `os.rename(new, taking)`
    (_seat.py:1272), which leaves no new/ until the `mkdir` on :1273.

    ⚠ SPEC TENSION, for the fix to resolve rather than route around: the
    two-syscall swap is RATIFIED - `openspec/changes/the-queue-chain/specs/
    pager/spec.md:20-23 @ 3c918c9` (D08: "renaming the whole directory ...
    and creating a fresh new/ ... two syscalls, O(1) in depth"), ratified at
    `openspec/RATIFICATION.md:301 @ 3c918c9`. Its own scenario (:46-50)
    promises "a link landing during the scoop is in the new new/, not lost"
    - which is exactly what the window breaks. This test pins the card's
    criterion; how the scoop meets it and D08 together is fix-001's call."""
    rig.box()
    queue = _seat.queue_new(rig.seat, SESSION)
    src = rig.root / SESSION / "new"
    for i in range(3):
        name = rig.deliver(SESSION, f"sc{i}@example.invalid", f"scoop {i}")
        os.link(src / name, queue / name)

    absent_after: list[str] = []
    real_rename, real_rmdir = os.rename, os.rmdir

    def watch(op: str, real):
        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            if not queue.is_dir():
                absent_after.append(f"{op}{tuple(str(a) for a in args)}")
            return out
        return spy

    monkeypatch.setattr(os, "rename", watch("os.rename", real_rename))
    monkeypatch.setattr(os, "rmdir", watch("os.rmdir", real_rmdir))

    taken = _seat.take_queue(rig.seat, SESSION, os.getpid())
    _seat.finish_queue(rig.seat, SESSION, taken)

    assert not absent_after, ("the scoop left ride/<sid>/new ABSENT after: "
                              + "; ".join(absent_after))
    assert queue.is_dir()
    # ...and it still did its job: every entry taken, once, into done/.
    assert sorted(e.name for e in taken.entries) == sorted(os.listdir(
        queue.parent / _seat.QUEUE_DONE))
    assert os.listdir(queue) == []


def test_a_scoop_racing_a_linker_loses_nothing_and_raises_nothing(rig):
    """The race itself, un-injected: one thread scoops in a loop (take +
    finish, as the ride-along does on every tool call) while another links
    arrivals in (as the listener does). Nothing may raise, and every arrival
    must end up given (done/) or waiting (new/) - a link that raised is a
    message the ride-along never shows.

    A stress loop, so it is the SECOND test of the race, not the proof: the
    deterministic ones below are. At 3c918c9 it fails with NoSuchBox
    (_detect.py:248) or FileNotFoundError (_detect.py:252) from the linker."""
    rig.box()
    queue = _seat.queue_new(rig.seat, SESSION)
    src = rig.root / SESSION / "new"
    names = [rig.deliver(SESSION, f"st{i}@example.invalid", f"stress {i}")
             for i in range(400)]
    arrivals = [_detect.Arrival(n, f"st{i}@example.invalid", src / n)
                for i, n in enumerate(names)]

    errors: list[str] = []
    stop = threading.Event()

    def scooper() -> None:
        # One pid per scoop, as one hook PROCESS per tool call has: above
        # any real pid, so a finished scoop's dir reads as a leftover. The
        # same pid every time would collide on `taking.<sid>.<pid>` (ENOTEMPTY)
        # - an artifact of threads, not the race under test.
        pid = 10_000_000
        try:
            while not stop.is_set():
                pid += 1
                taken = _seat.take_queue(rig.seat, SESSION, pid)
                _seat.finish_queue(rig.seat, SESSION, taken)
        except Exception:
            errors.append("scooper:\n" + traceback.format_exc())

    t = threading.Thread(target=scooper)
    t.start()
    try:
        for a in arrivals:
            try:
                _detect.link_into([a], [queue])
            except Exception:
                errors.append(f"linker ({a.name}):\n" + traceback.format_exc())
                break
    finally:
        stop.set()
        t.join(10)

    assert not errors, errors[0]
    # one last scoop gives whatever is still waiting
    taken = _seat.take_queue(rig.seat, SESSION, os.getpid())
    _seat.finish_queue(rig.seat, SESSION, taken)
    done = set(os.listdir(queue.parent / _seat.QUEUE_DONE))
    assert set(names) - done == set(), "arrivals LOST in the race"


# ── 2. FIRST BOOT: a fresh seat, a backlogged box, a queue swapped away ──

def _boot_into_a_window(rig, monkeypatch) -> list[str]:
    """A fresh seat (no ride/ at all) on a box with resting mail, as one
    was in the wild (102 in new/). The listener makes its queue at start
    (pager.py:916, `queue_new`), and the ride-along's scoop - fired by the
    seat's own boot tool calls, in another process - swaps it away before
    the listener's first look. Injected at the one point it can happen:
    straight after `queue_new` returns."""
    rig.box()
    rig.stopped()
    assert not _seat.ride_dir(rig.seat, SESSION).exists()     # never created
    names = [rig.deliver(SESSION, f"bk{i}@example.invalid", f"backlog {i}")
             for i in range(5)]
    real_queue_new = _seat.queue_new
    opened: list[Path] = []

    def queue_new_then_scooped(seat, session):
        q = real_queue_new(seat, session)
        if not opened:
            opened.append(_open_window(q))
        return q

    monkeypatch.setattr(_seat, "queue_new", queue_new_then_scooped)
    return names


def test_first_boot_a_swapped_queue_does_not_kill_the_banner(rig, monkeypatch):
    """At 3c918c9: FileNotFoundError at pager.py:959, the banner's
    `len(os.listdir(queue))` - one of the card's occurrences."""
    names = _boot_into_a_window(rig, monkeypatch)
    result = run("--timeout", "3")
    assert_listener_survived(result, EX_PAGE)
    assert counted(result) == len(names), result.stdout
    assert set(names) <= set(os.listdir(_queue(rig))), "backlog not linked into the queue"


def test_first_boot_a_swapped_queue_does_not_kill_a_quiet_listener(rig, monkeypatch):
    """--quiet skips the banner, so the first look is the watch loop's
    listing. At 3c918c9: FileNotFoundError at pager.py:1104 - the
    occurrences in the wild."""
    names = _boot_into_a_window(rig, monkeypatch)
    result = run("--quiet", "--timeout", "3")
    assert_listener_survived(result, EX_PAGE)
    assert set(names) <= set(os.listdir(_queue(rig))), "backlog not linked into the queue"


def test_first_boot_a_taking_dir_vanishing_mid_count_does_not_refuse_to_listen(
        rig, monkeypatch):
    """The listener's first act on the queue is `queue_names` (pager.py:917),
    which lists new/, every taking.* and done/ (_seat.py:1354). A finishing
    scoop `rmdir`s its taking dir (`finish_queue`, _seat.py:1293) between the
    iterdir and the listdir. At 3c918c9 the OSError is caught at pager.py:918
    and turned into `could not open the queue` - exit 2, a listener that
    REFUSED at boot: deaf, but by a different door."""
    rig.box()
    rig.stopped()
    name = rig.deliver(SESSION, "tk1@example.invalid", "while a scoop finishes")
    ride = _seat.ride_dir(rig.seat, SESSION)
    taking = ride / f"{_seat.TAKING_PREFIX}{SESSION}.{os.getpid()}"   # a LIVE scoop
    taking.mkdir(parents=True)
    real_listdir = os.listdir

    def listdir(path="."):
        if Path(path) == taking and taking.exists():
            shutil.rmtree(taking)            # the scoop finished first
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", listdir)
    result = run("--timeout", "3")
    assert_listener_survived(result, EX_PAGE)
    assert result.exit_code != EX_COULD_NOT_LOOK, result.stderr
    assert (_queue(rig) / name).exists()


# ── 3. MID-LIFE: the queue vanishes under a live, looping listener ───────

def _mid_life(rig, monkeypatch, on_second_detect) -> None:
    """Run ON_SECOND_DETECT at the start of the listener's SECOND wait: it
    has armed, bannered, listed its queue and waited once already - a live
    seat, not a booting one (one had given 22 ids when it fell over)."""
    real_detect = _detect.detect
    calls = {"n": 0}

    def detect(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            on_second_detect()
        return real_detect(*args, **kwargs)

    monkeypatch.setattr(_detect, "detect", detect)


def test_mid_life_the_queue_vanishing_does_not_kill_the_watch_loop(rig, monkeypatch):
    """The window opens while the listener waits; the next tick lists the
    queue. At 3c918c9: FileNotFoundError at pager.py:1104."""
    rig.box()
    rig.running()
    _mid_life(rig, monkeypatch, lambda: _open_window(_queue(rig)))
    result = run("--timeout", "1")
    assert_listener_survived(result, EX_TIMEOUT)


def test_mid_life_an_arrival_inside_the_window_is_linked_not_raised(rig, monkeypatch):
    """The window opens and mail lands before the scoop's mkdir. The listener
    wakes on the arrival and links it into a queue that is not there. At
    3c918c9: NoSuchBox at _detect.py:248 (after the is_dir check on :247). The arrival must
    be LINKED, not dropped - a link that did not happen is a message the
    ride-along never shows."""
    rig.box()
    rig.stopped()
    got: list[str] = []

    def window_then_mail() -> None:
        _open_window(_queue(rig))
        got.append(rig.deliver(SESSION, "mw1@example.invalid", "inside the window"))

    _mid_life(rig, monkeypatch, window_then_mail)
    result = run("--timeout", "3")
    assert_listener_survived(result, EX_PAGE)
    assert counted(result) == 1, result.stdout
    assert (_queue(rig) / got[0]).exists(), "the arrival was not linked"


def test_mid_life_the_queue_vanishing_between_the_check_and_the_link(rig, monkeypatch):
    """The traceback seen in the wild, exactly: `is_dir()` passes,
    the scoop's rename lands, then `os.link` finds no target directory.
    At 3c918c9: FileNotFoundError at _detect.py:252, from pager.py:1148 -
    `link_into` catches only FileExistsError."""
    rig.box()
    rig.stopped()
    queue = _queue(rig)
    real_link = os.link
    fired: list[Path] = []

    def link(src, dst, *args, **kwargs):
        if not fired and Path(dst).parent == queue and queue.is_dir():
            fired.append(_open_window(queue))       # lands between check and link
        return real_link(src, dst, *args, **kwargs)

    got: list[str] = []
    _mid_life(rig, monkeypatch,
              lambda: got.append(rig.deliver(SESSION, "tl1@example.invalid", "check-then-link")))
    monkeypatch.setattr(os, "link", link)
    result = run("--timeout", "3")
    assert fired, "the injection never fired - the test measured nothing"
    assert_listener_survived(result, EX_PAGE)
    assert (queue / got[0]).exists(), "the arrival was not linked"


# ── 4. the unit sites, for the fix to be pinned at each ──────────────────

def test_link_into_never_leaks_a_raw_oserror_when_the_queue_goes_mid_link(tmp_path, monkeypatch):
    """_detect.py:252 on its own. `link_into`'s contract is a queue that is
    not there is NoSuchBox (test_detect.py:326 pins that), never a bare
    OSError traceback. At 3c918c9 the check-then-link window leaks
    FileNotFoundError. Either outcome that keeps the contract passes here:
    the link lands, or NoSuchBox is raised for the caller to handle."""
    box = tmp_path / "box"
    for d in ("new", "cur", "tmp"):
        (box / d).mkdir(parents=True)
    (box / "new" / "m1").write_bytes(b"Message-ID: <l1@example.invalid>\r\n\r\nx\r\n")
    arrival = _detect.Arrival("m1", "l1@example.invalid", box / "new" / "m1")
    queue = tmp_path / "ride" / "new"
    queue.mkdir(parents=True)
    real_link = os.link

    def link(src, dst, *args, **kwargs):
        if queue.is_dir():
            os.rename(queue, tmp_path / "ride" / "taking.x.1")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    try:
        _detect.link_into([arrival], [queue])
    except _detect.NoSuchBox:
        return
    assert (queue / "m1").exists()


def test_queue_names_survives_a_directory_vanishing_between_listing_and_reading(rig, monkeypatch):
    """_seat.py:1354 on its own: `iterdir()` names a taking.* (or new/), and a
    concurrent scoop removes it before `os.listdir(d)`. At 3c918c9:
    FileNotFoundError. What is still there must still be counted - the
    names are the listener's never-relink key."""
    ride = _seat.ride_dir(rig.seat, SESSION)
    for d, entry in (("new", "n1"), ("done", "d1"), (f"{_seat.TAKING_PREFIX}{SESSION}.1", "t1")):
        (ride / d).mkdir(parents=True)
        (ride / d / entry).write_bytes(b"x")
    doomed = ride / f"{_seat.TAKING_PREFIX}{SESSION}.1"
    real_listdir = os.listdir

    def listdir(path="."):
        if Path(path) == doomed and doomed.exists():
            shutil.rmtree(doomed)
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", listdir)
    names = _seat.queue_names(rig.seat, SESSION)
    assert {"n1", "d1"} <= names


# ── 5. visibility: a recreated queue is SAID, not silent ─────────────────

def test_a_recreated_queue_is_announced(rig, monkeypatch):
    """Card criterion 1: "it is recreated AND the banner says so". A queue
    that had to be remade means a scoop's window (or worse) was hit; the
    seat must be able to see that from its own output. Wording is the fix's
    to choose; this pins only that stderr names the queue again after the
    banner, with the word `recreated`."""
    names = _boot_into_a_window(rig, monkeypatch)
    result = run("--timeout", "3")
    assert_listener_survived(result, EX_PAGE)
    assert "recreated" in result.stderr.lower(), result.stderr
    assert names
