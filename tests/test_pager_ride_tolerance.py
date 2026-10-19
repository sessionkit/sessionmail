"""SM-102 fix-001: the edges of the TOLERANCE half, beyond repro-001's reds.

repro-001 (`tests/test_pager_ride_race.py`) pins that nothing crashes and
nothing is lost when the queue's new/ vanishes. These tests pin what the
tolerance does at its own boundaries:

  * `link_into` turns only the missing-queue errnos into NoSuchBox, and
    skips an arrival whose SOURCE left the box first;
  * the listener gives up LOUDLY, with exit 2 and a line, when the queue
    keeps vanishing, instead of spinning or leaking a traceback;
  * `--quiet` stays quiet about a recreation;
  * the per-entry claim keeps the ratified scenario's promise: a link
    landing mid-scoop waits in new/ for the next scoop, and two scoops
    racing one queue take each entry exactly once;
  * `_entries` survives an entry taken between the listing and the stat.

Every root is a throwaway (tmp_path / the `rig` fixture), never ~/.mail.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from sessionmail import _detect, _seat, pager
from test_pager_listen import (  # noqa: F401  (fixture reused)
    EX_COULD_NOT_LOOK,
    EX_PAGE,
    rig,
    run,
)
from test_pager_ride_race import SESSION, _boot_into_a_window, _queue, assert_listener_survived


def _one_box(tmp_path: Path) -> tuple[_detect.Arrival, Path]:
    box = tmp_path / "box"
    for d in ("new", "cur", "tmp"):
        (box / d).mkdir(parents=True)
    (box / "new" / "m1").write_bytes(b"Message-ID: <e1@example.invalid>\r\n\r\nx\r\n")
    queue = tmp_path / "ride" / "new"
    queue.mkdir(parents=True)
    return _detect.Arrival("m1", "e1@example.invalid", box / "new" / "m1"), queue


# ── link_into ────────────────────────────────────────────────────────────

def test_link_into_skips_an_arrival_whose_source_left_the_box(tmp_path, monkeypatch):
    """ENOENT with the queue still there and the SOURCE gone: the arrival was
    claimed out of the box (new/ -> cur/) before it could be linked. That is
    not the queue's fault, so it is no NoSuchBox, and nothing else is lost."""
    arrival, queue = _one_box(tmp_path)
    real_link = os.link

    def link(src, dst, *args, **kwargs):
        os.unlink(src)                       # claimed away first
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    assert _detect.link_into([arrival], [queue]) == []
    assert queue.is_dir() and os.listdir(queue) == []


def test_link_into_still_raises_any_other_oserror(tmp_path, monkeypatch):
    """Only the missing-queue errnos become NoSuchBox. A disk that is full or
    a permission that is wrong is a different fault and must not be recast
    as a vanished queue, where the listener would recreate and retry it."""
    arrival, queue = _one_box(tmp_path)

    def link(src, dst, *args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied", str(dst))

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(PermissionError):
        _detect.link_into([arrival], [queue])


def test_link_into_einval_mid_rename_is_the_missing_queue(tmp_path, monkeypatch):
    """EINVAL from os.link into a directory mid-rename (APFS, seen once in
    repro-001) is the same window. It is NoSuchBox, never a raw OSError."""
    arrival, queue = _one_box(tmp_path)

    def link(src, dst, *args, **kwargs):
        raise OSError(errno.EINVAL, "Invalid argument", str(dst))

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(_detect.NoSuchBox):
        _detect.link_into([arrival], [queue])


# ── the listener ─────────────────────────────────────────────────────────

def test_a_queue_that_keeps_vanishing_is_reported_not_spun_on(rig, monkeypatch):
    """Every link finds the queue gone. The listener retries a bounded number
    of times, then stops with exit 2 and says why. It must not spin, and it
    must not leak a traceback after a healthy banner, which is the deaf
    re-arm this card is about."""
    rig.box()
    rig.stopped()
    rig.deliver(SESSION, "kv1@example.invalid", "never lands")
    calls = {"n": 0}

    def link_into(arrivals, queues):
        calls["n"] += 1
        raise _detect.NoSuchBox(f"queue {queues[0]} is not a directory")

    monkeypatch.setattr(_detect, "link_into", link_into)
    result = run("--timeout", "3")
    assert_listener_survived(result, EX_COULD_NOT_LOOK)
    assert calls["n"] == pager.QUEUE_LINK_TRIES
    assert "vanished" in result.stderr and "could not link" in result.stderr, result.stderr


def test_quiet_says_nothing_about_a_recreation_but_still_survives(rig, monkeypatch):
    """--quiet is exit code only. The recreation is still done, and the exit
    is still the honest page."""
    names = _boot_into_a_window(rig, monkeypatch)
    result = run("--quiet", "--timeout", "3")
    assert_listener_survived(result, EX_PAGE)
    assert "recreated" not in result.stderr.lower(), result.stderr
    assert set(names) <= set(os.listdir(_queue(rig)))


# ── the scoop and its entries ────────────────────────────────────────────

def test_a_link_landing_during_the_scoop_is_kept_not_lost_not_given_twice(rig, monkeypatch):
    """The ratified scenario "The swap is the claim" (the-queue-chain,
    spec.md:46-50 @ 3c918c9) promises that a link landing during the scoop is
    in new/, not lost and not given twice. With the per-entry claim, new/ is
    there throughout, so the link lands in it and waits for the NEXT scoop.
    It is not in this scoop's taking dir, and the next scoop gives it
    once."""
    queue = _seat.queue_new(rig.seat, SESSION)
    for n in ("a1", "a2", "a3"):
        (queue / n).write_bytes(n.encode())
    real_rename = os.rename
    landed: list[str] = []

    def rename(src, dst, *args, **kwargs):
        if not landed:                                   # the listener, mid-scoop
            assert queue.is_dir()
            (queue / "late").write_bytes(b"late")
            landed.append("late")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    first = _seat.take_queue(rig.seat, SESSION, os.getpid())
    _seat.finish_queue(rig.seat, SESSION, first)
    monkeypatch.setattr(os, "rename", real_rename)
    assert {e.name for e in first.entries} == {"a1", "a2", "a3"}
    assert os.listdir(queue) == ["late"]                 # waiting, not lost
    second = _seat.take_queue(rig.seat, SESSION, os.getpid())
    _seat.finish_queue(rig.seat, SESSION, second)
    assert [e.name for e in second.entries] == ["late"]  # given once, by the next scoop
    assert sorted(os.listdir(queue.parent / _seat.QUEUE_DONE)) == ["a1", "a2", "a3", "late"]


def test_two_scoops_racing_one_queue_take_each_entry_exactly_once(rig, monkeypatch):
    """Two scoops of one session, one inside the other's claim loop (two
    tool calls' hooks at once). The per-entry rename is the claim: the loser
    of each entry gets ENOENT and skips it, so the union is the whole queue
    and the intersection is empty. The whole-directory swap got this
    property from one rename. The per-entry claim must keep it."""
    queue = _seat.queue_new(rig.seat, SESSION)
    names = [f"r{i}" for i in range(20)]
    for n in names:
        (queue / n).write_bytes(n.encode())
    real_rename = os.rename
    inner: list = []

    def rename(src, dst, *args, **kwargs):
        if not inner and Path(src).parent == queue:
            inner.append(None)
            monkeypatch.setattr(os, "rename", real_rename)
            inner[0] = _seat.take_queue(rig.seat, SESSION, 20_000_001)   # the other hook
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    try:
        outer = _seat.take_queue(rig.seat, SESSION, 20_000_000)
    except FileNotFoundError as exc:                     # the outer's first rename lost
        pytest.fail(f"a scoop that lost an entry to another scoop raised: {exc}")
    a = {e.name for e in outer.entries}
    b = {e.name for e in inner[0].entries}
    assert a | b == set(names)
    assert not (a & b), f"given twice: {sorted(a & b)}"
    assert queue.is_dir() and os.listdir(queue) == []


def test_entries_survive_an_entry_taken_between_listing_and_stat(tmp_path, monkeypatch):
    """`_entries` lists a directory, then stats each entry to sort it. A
    concurrent scoop can move one in between. The rest are still returned,
    oldest first, and there is no FileNotFoundError."""
    d = tmp_path / "taking.s.1"
    d.mkdir()
    for n in ("a", "b", "c"):
        (d / n).write_bytes(n.encode())
    doomed = d / "b"
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == doomed and os.path.exists(doomed):
            os.unlink(doomed)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    got = [e.name for e in _seat._entries(d)]
    assert set(got) == {"a", "c"}
