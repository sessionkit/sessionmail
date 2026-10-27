"""A08 fs-new-mail-detector: block while a maildir box's new/ is
empty; when it is not, return every Message-ID in it. Nothing else.

Something small that listens on the file system and returns the
Message-IDs when new messages come in. It blocks, and returns a list of all
the Message-IDs in new/: five in new/ returns all five; none in new/ waits
until one comes in. Pure Python, small, with no fswatch.

The contract, whole:

  - THE CONDITION IS "new/ IS NOT EMPTY". No arm-time snapshot: a file
    already resting in new/ is returned at once, with everything beside
    it. What to do about a file that keeps sitting there is the caller's
    (D07: the detector detects; marking, moving, claiming are not here),
    so a caller that never moves anything out of new/ gets the same list
    back on every call - by design, and said out loud.
  - BLOCKS on an in-process kqueue watch of the new/ directory: it
    rings ~1 ms after the file lands; a file descriptor in this
    process, so R16 holds by construction - there is no child to orphan).
  - A POLL LEG behind the watch: kqueue's own wait is bounded to POLL
    seconds and every return, event or not, rescans. That IS the 1 s
    fallback the design allows, behind the watch, not instead of it.
  - SETTLE (a batch window, opt-in, default 0): once new/ is non-empty
    wait SETTLE more seconds, then sweep and return everything. Argument
    or SESSIONMAIL_DETECT_SETTLE. Composes with the list: wait, sweep,
    return.
  - RETURNS oldest first (maildir names sort by delivery time). A file
    with no Message-ID is named by its maildir name, never dropped.
  - DIES WITH ITS CALLER: the parent pid is checked at every scan.

Two doors, one function (`detect()` is the wake; the loop is a wrapper):

    python -m sessionmail._detect BOX [--poll S] [--timeout S] [--settle S]
    python -m sessionmail._detect BOX --follow [--exec CMD]

BOX is a maildir path, or a box name under the agents root
($SESSIONMAIL_ROOT, else $SESSION_MAIL_ROOT, else ~/.mail/agents).
One-shot: print each Message-ID in new/ as `<id>` on its own line, exit
0; exit 124 on --timeout; exit 2 when the box cannot be watched.
--follow never exits: each wake reports each file ONCE for the life of
the run (the loop remembers what it has reported, not the
disk), then waits for the next; with --exec CMD the ids go to CMD as
arguments instead of stdout, one call per wake. This is the pipeline the
design describes: when a new message comes in, it calls something. With
--paths the ids become the files' paths under new/ - what a hook that
hardlinks a message onward needs, since a Message-ID is not an inode.

--link-into DIR (repeatable) is that hook built in, and it is the
detector's half of D08 (the detector hard links into a queue for
notification, and the ride-alongs scoop them out): every file returned is
hardlinked into each DIR under its own maildir name - same inode, a
second name, no copy, the seat's box untouched. EEXIST means it is
already there and is not an error (a re-run is idempotent). The scoop -
`mv DIR DIR.taking.$$ && mkdir DIR` - is the consumer's, never done
here. One DIR per consumer (an addition to D08): a
scoop empties a directory, so two consumers on one would steal from each
other; links are free, so give each its own.

--older-than T changes the condition from "new/ is not empty" to "new/
holds an entry at least T seconds old" - the AGE WATCHDOG (the pager
listens on the ride-along queue, and an entry past a certain age
triggers a page). A consumer
that keeps up empties its queue before T; an entry that crosses T means
nobody scooped it - the agent is idle or stuck, which is when a page is
worth its cost. The wake lands at the crossing itself, not on a poll
tick: the wait is shortened to the oldest entry's remaining time. Age is
the inode's mtime - for a hardlinked queue that is when the mail was
DELIVERED, not when it was linked, which is the honest age of the mail.
T may be a number or a zero-argument callable re-read at every scan,
so the CALLER can choose it per wake without this module knowing why
(a stopped agent pages immediately, a running one after about a minute -
the pager reads its stopped stamp and hands the value in; see
pager.page_age). There is no
default T here.

What this is NOT: counts and status are a poll and a
search; arrival is an event. This is the event leg only.
"""

from __future__ import annotations

import errno
import os
import select
import subprocess
import sys
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from pathlib import Path

from sessionmail import _maildir
from sessionmail._headers import as_written, safe_header

DEFAULT_POLL = 1.0
POLL_VAR = "SESSIONMAIL_DETECT_POLL"
SETTLE_VAR = "SESSIONMAIL_DETECT_SETTLE"
EX_ARRIVED, EX_COULD_NOT_WATCH, EX_TIMEOUT = 0, 2, 124


class NoSuchBox(Exception):
    """BOX has no new/ directory to watch."""


class Timeout(Exception):
    """new/ stayed empty for the whole timeout."""


class CallerGone(Exception):
    """The parent process went away while we waited (R16)."""


@dataclass(frozen=True)
class Arrival:
    name: str           # the maildir unique name under new/
    message_id: str     # bare, no angle brackets; "" when the file has none
    path: Path


def _message_id(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            raw = fh.read(65536)
    except OSError:
        return ""
    headers = BytesHeaderParser(policy=default_policy).parsebytes(raw)
    # Through the shared accessor, so a Message-ID the parser cannot
    # read keys on the sender's raw text and never sinks `detect()`, which
    # builds an Arrival for every file before it returns any (one raise
    # here made the pager deaf to every mail after it).
    return _maildir.bare_id(as_written(safe_header(headers, "Message-ID")))


def _new_dir(box: Path) -> Path:
    new = box / "new"
    if not new.is_dir():
        raise NoSuchBox(f"no new/ under {box} - not a maildir I can watch")
    return new.resolve()


def _listing(new: Path, ignore: Collection[str]) -> list[str]:
    return sorted(n for n in os.listdir(new) if n not in ignore)


def _oldest_age(new: Path, names: list[str]) -> float | None:
    """Seconds since the oldest entry's mtime; None if they all vanished
    between the listing and the stat (scooped: not an error)."""
    now = time.time()
    ages = []
    for n in names:
        try:
            ages.append(now - os.stat(new / n).st_mtime)
        except FileNotFoundError:
            continue
    return max(ages) if ages else None


def detect(box: Path | str, *, poll: float = DEFAULT_POLL,
           timeout: float | None = None, settle: float = 0.0,
           parent: int | None = None,
           ignore: Collection[str] = (),
           older_than: float | Callable[[], float] | None = None) -> list[Arrival]:
    """Block while BOX/new/ (less IGNORE) is empty; once it is not, wait
    SETTLE seconds, then return everything in it, oldest first. With
    OLDER_THAN, block instead until an entry is at least that many
    seconds old (the age watchdog); the wake lands at the crossing.
    Raises Timeout, NoSuchBox, CallerGone."""
    new = _new_dir(Path(box))

    # O_EVTONLY (macOS): a watch-only descriptor that does not pin the
    # volume. The kqueue holds this fd for the life of the wait and both
    # close with the process - nothing is left behind.
    fd = os.open(new, os.O_RDONLY | getattr(os, "O_EVTONLY", 0))
    kq = select.kqueue()
    try:
        kq.control([select.kevent(
            fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
            fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND,
        )], 0, 0)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Look FIRST: a resident is returned without waiting at all.
            present = _listing(new, ignore)
            wait = poll
            if present:
                due = True
                if older_than is not None:
                    threshold = older_than() if callable(older_than) else older_than
                    age = _oldest_age(new, present)
                    due = age is not None and age >= threshold
                    if not due and age is not None:
                        # Wake at the crossing, not on the next poll tick -
                        # bounded by POLL so a threshold that changes under
                        # us (the agent stopped: T drops to 0) is seen soon.
                        wait = min(poll, threshold - age)
                if due:
                    if settle > 0:
                        time.sleep(settle)
                        present = _listing(new, ignore)
                    return [Arrival(n, _message_id(new / n), new / n) for n in present]
            if parent is not None and os.getppid() != parent:
                raise CallerGone(f"caller {parent} went; not waiting alone")
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                what = "held nothing old enough" if older_than is not None else "stayed empty"
                raise Timeout(f"new/ under {new.parent} {what} for {timeout:g}s")
            if deadline is not None:
                wait = min(wait, deadline - now)
            # An event OR the wait expiring: either way, look again. The
            # event only decides how soon.
            kq.control(None, 1, max(0.0, wait))
    finally:
        kq.close()
        os.close(fd)


def follow(box: Path | str, on_wake: Callable[[list[Arrival]], None], *,
           poll: float = DEFAULT_POLL, settle: float = 0.0,
           parent: int | None = None,
           older_than: float | Callable[[], float] | None = None) -> None:
    """detect() in a loop that never returns: each file is handed to
    ON_WAKE once for the life of this run, then forgotten again when it
    leaves new/ (so the memory is bounded by what is resting there)."""
    reported: set[str] = set()
    new = _new_dir(Path(box))
    while True:
        arrivals = detect(box, poll=poll, timeout=None, settle=settle,
                          parent=parent, ignore=reported, older_than=older_than)
        on_wake(arrivals)
        still = set(os.listdir(new))
        reported = (reported & still) | {a.name for a in arrivals}


def link_into(arrivals: list[Arrival], queues: list[Path]) -> list[Path]:
    """Hardlink each arrival into each queue under its maildir name.
    Returns the links made; an existing link is left alone (idempotent).
    A queue that is not a directory is an error - a queue that silently
    was not there is a deaf consumer.

    The queue can go between the `is_dir` check and the link: a
    scoop renames it away in another process (a traceback seen twice in one
    day). That is the same missing queue, so it is the same
    NoSuchBox, never a bare OSError. ENOENT and EINVAL both mean it here.
    EINVAL was seen once from `os.link` into a directory mid-rename on APFS
   . The caller decides whether to recreate the queue and link
    again. Links made before the raise are already in some queue, so the
    raise names only the one arrival that did not land."""
    made: list[Path] = []
    for q in queues:
        if not q.is_dir():
            raise NoSuchBox(f"queue {q} is not a directory")
        for a in arrivals:
            target = q / a.name
            try:
                os.link(a.path, target)
                made.append(target)
            except FileExistsError:
                pass
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.EINVAL):
                    raise
                if q.is_dir() and not os.path.lexists(a.path):
                    # The SOURCE went: the arrival was claimed out of the
                    # box first. There is nothing to link, and it is not the
                    # queue's fault.
                    continue
                raise NoSuchBox(f"queue {q} went away under the link of "
                                f"{a.name}: {exc}") from exc
    return made


def _resolve_box(token: str) -> Path:
    p = Path(token).expanduser()
    if p.is_dir():
        return p
    return _maildir.root() / token


def _line(a: Arrival, paths: bool = False) -> str:
    if paths:
        return str(a.path)
    return f"<{a.message_id}>" if a.message_id else a.name


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m sessionmail._detect",
        description="block while a maildir's new/ is empty; print every "
                    "Message-ID in it; exit")
    ap.add_argument("box", help="maildir path, or a box name under "
                    f"${_maildir.ROOT_VAR} ({_maildir.DEFAULT_ROOT})")
    ap.add_argument("--poll", type=float,
                    default=float(os.environ.get(POLL_VAR, DEFAULT_POLL)),
                    help=f"safety-poll interval, seconds (default {DEFAULT_POLL:g}; ${POLL_VAR})")
    ap.add_argument("--timeout", type=float, default=None,
                    help="give up after S seconds of empty new/ (exit 124); default never")
    ap.add_argument("--settle", type=float,
                    default=float(os.environ.get(SETTLE_VAR, 0.0)),
                    help="once new/ is non-empty wait S more seconds, then sweep "
                         f"and return all (batch window; default 0; ${SETTLE_VAR})")
    ap.add_argument("--older-than", metavar="T", type=float, default=None,
                    help="wake only when an entry in new/ is at least T seconds old "
                         "(the age watchdog: nobody scooped it); default: any entry")
    ap.add_argument("--paths", action="store_true",
                    help="print (or pass to --exec) each file's path under new/ "
                         "instead of its Message-ID")
    ap.add_argument("--link-into", metavar="DIR", action="append", default=[],
                    type=Path, help="hardlink every returned file into DIR under "
                    "its maildir name (repeatable: one queue per consumer; D08)")
    ap.add_argument("--follow", action="store_true",
                    help="never exit: report each file once per wake, then wait again")
    ap.add_argument("--exec", dest="command", nargs=argparse.REMAINDER,
                    help="with --follow: run CMD with the ids as arguments on each "
                         "wake instead of printing them (everything after --exec)")
    args = ap.parse_args(argv)
    if args.command and not args.follow:
        print("detect: --exec needs --follow", file=sys.stderr)
        return EX_COULD_NOT_WATCH

    box = _resolve_box(args.box)
    parent = os.getppid()

    def report(arrivals: list[Arrival]) -> None:
        if args.link_into:
            link_into(arrivals, args.link_into)
        if args.command:
            subprocess.run([*args.command, *(_line(a, args.paths) for a in arrivals)])
        else:
            for a in arrivals:
                print(_line(a, args.paths), flush=True)

    try:
        if args.follow:
            follow(box, report, poll=args.poll, settle=args.settle, parent=parent,
                   older_than=args.older_than)
            return EX_ARRIVED
        report(detect(box, poll=args.poll, timeout=args.timeout,
                      settle=args.settle, parent=parent, older_than=args.older_than))
        return EX_ARRIVED
    except NoSuchBox as exc:
        print(f"detect: {exc}", file=sys.stderr)
        return EX_COULD_NOT_WATCH
    except Timeout:
        return EX_TIMEOUT
    except CallerGone as exc:
        print(f"detect: {exc}", file=sys.stderr)
        return EX_ARRIVED


if __name__ == "__main__":
    sys.exit(main())
