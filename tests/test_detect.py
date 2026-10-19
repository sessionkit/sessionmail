"""A08 fs-new-mail-detector (SM-70): block while new/ is empty; return
everything in it.

Hermetic: a maildir under tmp_path, deliveries made the way an MDA makes
them (write tmp/, rename into new/), the detector run in-process with a
long poll so that a fast wake PROVES the kqueue fired and not the poll.

Each gate here was watched red before the module existed (SM-70 F16).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sessionmail import _detect

LONG_POLL = 5.0     # a wake faster than this came from the watch, not the poll
WANTED = [f"burst-{i}@example" for i in range(10)]


def maildir(root: Path) -> Path:
    box = root / "box"
    for d in ("tmp", "new", "cur"):
        (box / d).mkdir(parents=True)
    return box


_n = 0


def deliver(box: Path, mid: str, where: str = "new") -> str:
    """The MDA's move: write under tmp/, rename into WHERE (new/ by default)."""
    global _n
    _n += 1
    name = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.test"
    tmp = box / "tmp" / name
    tmp.write_bytes(
        f"From: alpha@example.com\r\nTo: beta@example.com\r\n"
        f"Subject: probe {_n}\r\nMessage-ID: <{mid}>\r\n\r\nbody\r\n".encode()
    )
    os.rename(tmp, box / where / name)
    return name


def later(seconds: float, fn, *args) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), fn(*args)))
    t.start()
    return t


def burst(box: Path, gap: float) -> None:
    for mid in WANTED:
        deliver(box, mid)
        if gap:
            time.sleep(gap)


def ids(arrivals) -> list[str]:
    return [a.message_id for a in arrivals]


# --- the contract -----------------------------------------------------------

def test_blocks_while_new_is_empty_and_times_out(tmp_path):
    box = maildir(tmp_path)
    t0 = time.monotonic()
    with pytest.raises(_detect.Timeout):
        _detect.detect(box, poll=0.05, timeout=0.3)
    assert 0.25 <= time.monotonic() - t0 < 2.0


def test_one_arrival_wakes_from_the_watch_not_the_poll(tmp_path):
    box = maildir(tmp_path)
    t = later(0.2, deliver, box, "one@example")
    t0 = time.monotonic()
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    took = time.monotonic() - t0
    t.join()
    assert ids(got) == ["one@example"]
    assert took < 1.0, f"woke after {took:.3f}s: that was the poll, not the watch"


def test_residents_are_returned_at_once_all_of_them(tmp_path):
    """Five in new: it returns all five, at once."""
    box = maildir(tmp_path)
    for i in range(5):
        deliver(box, f"resident-{i}@example")
    t0 = time.monotonic()
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    assert ids(got) == [f"resident-{i}@example" for i in range(5)]
    assert time.monotonic() - t0 < 0.1


def test_the_list_is_everything_in_new_not_just_what_woke_it(tmp_path):
    box = maildir(tmp_path)
    deliver(box, "already@example")
    later(0.2, deliver, box, "later@example")
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    # The resident wins the race by construction; the point is the SHAPE:
    # whatever is in new/ when it looks, oldest first.
    assert ids(got)[0] == "already@example"


# --- the burst, and F16 -----------------------------------------------------

def test_a_spaced_burst_with_no_settle_returns_what_has_landed(tmp_path):
    """F16: the watch rings ~1 ms after file 1. With no settle the sweep
    that follows sees what has landed by then - one, for a burst 20 ms
    apart. The other nine are not lost: they are residents, and the
    NEXT call returns them at once (the list contract's answer to the
    seam a first-sight one-shot had)."""
    box = maildir(tmp_path)
    t = later(0.2, burst, box, 0.02)
    first = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    t.join()
    rest = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    print(f"\nspaced burst, settle=0: first call {len(first)} of 10; "
          f"next call, at once, {len(rest)} of 10 (residents)")
    assert ids(first)[0] == WANTED[0]
    assert ids(rest) == WANTED                # residents = the whole burst


def test_a_spaced_burst_with_settle_is_one_list(tmp_path):
    box = maildir(tmp_path)
    t = later(0.2, burst, box, 0.02)
    t0 = time.monotonic()
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0, settle=0.3)
    took = time.monotonic() - t0
    t.join()
    print(f"\nspaced burst, settle=0.3: {len(got)} of 10 in one list, {took:.2f}s")
    assert ids(got) == WANTED


def test_a_tight_burst_first_sweep_count_is_printed(tmp_path):
    """10 renames back to back: how many does the first sweep see? A race
    between the delivering thread and the woken sweep; printed as the
    finding, bounded as the gate."""
    box = maildir(tmp_path)
    t = later(0.2, burst, box, 0.0)
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    t.join()
    print(f"\ntight burst, settle=0: first sweep saw {len(got)} of 10")
    assert 1 <= len(got) <= 10


def test_the_burst_gate_can_go_red(tmp_path):
    """Negative control: deliver nothing; the wait must NOT return a list."""
    box = maildir(tmp_path)
    with pytest.raises(_detect.Timeout):
        _detect.detect(box, poll=0.05, timeout=0.3)


# --- what does not wake it ----------------------------------------------------

def test_tmp_and_cur_do_not_wake(tmp_path):
    box = maildir(tmp_path)
    later(0.1, deliver, box, "t@example", "tmp")
    later(0.1, deliver, box, "c@example", "cur")
    with pytest.raises(_detect.Timeout):
        _detect.detect(box, poll=0.05, timeout=0.4)


def test_no_new_dir_is_refused(tmp_path):
    with pytest.raises(_detect.NoSuchBox):
        _detect.detect(tmp_path / "nowhere", poll=0.05, timeout=0.1)


def test_a_file_without_a_message_id_is_named_not_dropped(tmp_path):
    box = maildir(tmp_path)
    (box / "tmp" / "bare").write_bytes(b"From: a@example.com\r\n\r\nno id\r\n")
    os.rename(box / "tmp" / "bare", box / "new" / "bare")
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    assert len(got) == 1 and got[0].message_id == "" and got[0].name == "bare"


# --- follow: the pipeline ------------------------------------------------------

def test_follow_hands_each_file_to_the_callback_once(tmp_path):
    """The version that does not exit: 'when a new message comes in, it
    calls something.' Three deliveries over time, each given once even
    though nothing is ever moved out of new/."""
    box = maildir(tmp_path)
    wakes: list[list[str]] = []
    done = threading.Event()

    def on_wake(arrivals) -> None:
        wakes.append(ids(arrivals))
        if len(wakes) == 3:
            done.set()
            raise SystemExit            # the only way out of follow()

    def run() -> None:
        try:
            _detect.follow(box, on_wake, poll=LONG_POLL)
        except SystemExit:
            pass

    worker = threading.Thread(target=run)
    worker.start()
    for i in range(3):
        time.sleep(0.15)
        deliver(box, f"follow-{i}@example")
    assert done.wait(3.0), f"follow gave {len(wakes)} wakes, wanted 3"
    worker.join(3.0)
    assert wakes == [[f"follow-{i}@example"] for i in range(3)]


# --- the program door --------------------------------------------------------------

def run_detect(*args: str, **kw) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "sessionmail._detect", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def test_the_program_prints_one_id_per_line_and_exits_0(tmp_path):
    box = maildir(tmp_path)
    proc = run_detect(str(box), "--timeout", "5")
    time.sleep(0.3)
    deliver(box, "cli-1@example")
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err
    assert out.splitlines() == ["<cli-1@example>"]


def test_the_program_with_settle_prints_the_whole_burst(tmp_path):
    box = maildir(tmp_path)
    proc = run_detect(str(box), "--timeout", "5", "--settle", "0.3")
    time.sleep(0.3)
    burst(box, 0.02)
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err
    assert out.splitlines() == [f"<{m}>" for m in WANTED]


def test_settle_rides_the_environment_too(tmp_path, monkeypatch):
    box = maildir(tmp_path)
    env = dict(os.environ, **{_detect.SETTLE_VAR: "0.3"})
    proc = run_detect(str(box), "--timeout", "5", env=env)
    time.sleep(0.3)
    burst(box, 0.02)
    out, err = proc.communicate(timeout=10)
    assert out.splitlines() == [f"<{m}>" for m in WANTED]


def test_the_program_times_out_124_printing_nothing(tmp_path):
    box = maildir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "sessionmail._detect", str(box),
         "--timeout", "0.3", "--poll", "0.05"],
        capture_output=True, text=True, timeout=10)
    assert proc.returncode == 124 and proc.stdout == ""


def test_follow_exec_runs_the_command_per_wake(tmp_path):
    """--follow --exec CMD: the ids reach CMD as arguments, one call per
    wake; the detector itself prints nothing."""
    box = maildir(tmp_path)
    log = tmp_path / "calls.log"
    proc = run_detect(str(box), "--follow", "--exec", "sh", "-c",
                      f'echo "$@" >> {log}', "_")
    time.sleep(0.3)
    deliver(box, "x-1@example")
    time.sleep(0.3)
    deliver(box, "x-2@example")
    time.sleep(0.3)
    proc.kill()
    out, _ = proc.communicate(timeout=5)
    assert out == ""
    assert log.read_text().splitlines() == ["<x-1@example>", "<x-2@example>"]


def test_paths_hands_the_hook_a_file_it_can_hardlink(tmp_path):
    """The pipeline: hook -> hardlink into ride/new/. A hook
    needs the path, not the Message-ID; --paths gives it."""
    box = maildir(tmp_path)
    ride = tmp_path / "ride" / "new"
    ride.mkdir(parents=True)
    proc = run_detect(str(box), "--timeout", "5", "--paths", "--follow",
                      "--exec", "sh", "-c", f'for f; do ln "$f" {ride}/; done', "_")
    time.sleep(0.3)
    name = deliver(box, "linked@example")
    time.sleep(0.4)
    proc.kill()
    proc.communicate(timeout=5)
    linked = ride / name
    assert linked.exists()
    assert os.stat(linked).st_ino == os.stat(box / "new" / name).st_ino   # same inode, no copy
    assert (box / "new" / name).exists()                                   # the box is untouched


def test_link_into_is_the_detectors_half_of_d08(tmp_path):
    """The detector hardlinks each arrival into a queue for
    notification; consumers scoop by renaming the directory. Two queues
    (one per consumer), same inode in each, the seat's box untouched,
    and a re-run over the same files is idempotent (EEXIST is not an
    error)."""
    box = maildir(tmp_path)
    q1, q2 = tmp_path / "ride" / "new", tmp_path / "pager" / "new"
    q1.mkdir(parents=True)
    q2.mkdir(parents=True)
    names = [deliver(box, f"queued-{i}@example") for i in range(3)]
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    made = _detect.link_into(got, [q1, q2])
    assert len(made) == 6
    for n in names:
        src = os.stat(box / "new" / n)
        assert os.stat(q1 / n).st_ino == src.st_ino == os.stat(q2 / n).st_ino
        assert src.st_nlink == 3
    assert sorted(os.listdir(box / "new")) == sorted(names)     # untouched
    assert _detect.link_into(got, [q1, q2]) == []               # idempotent
    # The consumer's scoop: swap the directory; the seat and the other
    # queue see nothing of it.
    os.rename(q1, tmp_path / "ride" / "taking.1")
    q1.mkdir()
    assert os.listdir(q1) == [] and sorted(os.listdir(q2)) == sorted(names)


def test_link_into_refuses_a_missing_queue(tmp_path):
    """A queue that was not there is a deaf consumer: refuse, never skip."""
    box = maildir(tmp_path)
    deliver(box, "x@example")
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0)
    with pytest.raises(_detect.NoSuchBox):
        _detect.link_into(got, [tmp_path / "nowhere"])


def test_the_program_links_into_each_queue_and_still_prints(tmp_path):
    box = maildir(tmp_path)
    q = tmp_path / "q"
    q.mkdir()
    proc = run_detect(str(box), "--timeout", "5", "--link-into", str(q))
    time.sleep(0.3)
    name = deliver(box, "prog@example")
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err
    assert out.splitlines() == ["<prog@example>"]
    assert os.stat(q / name).st_ino == os.stat(box / "new" / name).st_ino


# --- the age watchdog (not yet ruled) ----------------------------------------

def test_older_than_wakes_at_the_crossing_not_the_poll(tmp_path):
    """An entry present but young: the wake lands when it crosses T,
    with a 5 s poll - so the crossing was computed, not polled for."""
    box = maildir(tmp_path)
    deliver(box, "young@example")
    t0 = time.monotonic()
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0, older_than=0.5)
    took = time.monotonic() - t0
    assert ids(got) == ["young@example"]
    assert 0.45 <= took < 1.0, f"woke after {took:.2f}s"


def test_older_than_on_an_empty_queue_waits_for_arrival_then_age(tmp_path):
    box = maildir(tmp_path)
    later(0.2, deliver, box, "late@example")
    t0 = time.monotonic()
    got = _detect.detect(box, poll=LONG_POLL, timeout=3.0, older_than=0.3)
    took = time.monotonic() - t0
    assert ids(got) == ["late@example"]
    assert 0.45 <= took < 1.2, f"woke after {took:.2f}s"


def test_a_consumer_that_keeps_up_never_pages(tmp_path):
    """The whole point: scooped before T = nothing to do."""
    box = maildir(tmp_path)
    name = deliver(box, "scooped@example")
    later(0.2, os.rename, box / "new" / name, box / "cur" / name)
    with pytest.raises(_detect.Timeout):
        _detect.detect(box, poll=0.05, timeout=0.8, older_than=0.5)


def test_older_than_age_is_the_inode_mtime_so_a_hardlink_carries_delivery_time(tmp_path):
    """A message delivered long ago and linked just now is OLD: the
    watchdog pages at once. Said in the module; pinned here."""
    box = maildir(tmp_path)
    name = deliver(box, "stale@example")
    old = time.time() - 100
    os.utime(box / "new" / name, (old, old))
    q = maildir(tmp_path / "ride")
    os.link(box / "new" / name, q / "new" / name)
    t0 = time.monotonic()
    got = _detect.detect(q, poll=LONG_POLL, timeout=3.0, older_than=5.0)
    assert ids(got) == ["stale@example"] and time.monotonic() - t0 < 0.1


def test_older_than_may_be_a_callable_re_read_at_every_scan(tmp_path):
    """Rulings 1330: T is chosen per wake by the caller (stopped -> 0,
    running -> ~60 s). The primitive re-reads it each scan, so a stop
    mid-wait pages at once, bounded by the poll."""
    box = maildir(tmp_path)
    deliver(box, "t@example")
    threshold = {"t": 5.0}
    later(0.3, threshold.__setitem__, "t", 0.0)       # "the agent stopped"
    t0 = time.monotonic()
    got = _detect.detect(box, poll=0.1, timeout=3.0, older_than=lambda: threshold["t"])
    took = time.monotonic() - t0
    assert ids(got) == ["t@example"]
    assert 0.25 <= took < 0.8, f"woke after {took:.2f}s"


def test_the_program_older_than(tmp_path):
    box = maildir(tmp_path)
    deliver(box, "cli-old@example")
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "sessionmail._detect", str(box),
         "--timeout", "5", "--older-than", "0.4"],
        capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0 and proc.stdout.splitlines() == ["<cli-old@example>"]
    assert time.monotonic() - t0 >= 0.4


def test_dies_with_its_caller(tmp_path):
    """R16: no child outlives its parent. A shell parent that exits takes
    the detector with it - measured, not inferred."""
    box = maildir(tmp_path)
    pidfile = tmp_path / "pid"
    parent = subprocess.Popen(
        ["sh", "-c",
         f"{sys.executable} -m sessionmail._detect {box} --poll 0.05 & "
         f"echo $! > {pidfile}; sleep 0.3; exit 0"])
    parent.wait(timeout=10)
    pid = int(pidfile.read_text())
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pid, 9)
    pytest.fail(f"detector {pid} outlived its caller by 2 s")
