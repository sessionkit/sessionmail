"""A09 imap-new-mail-detector: the A08 contract over IMAP IDLE,
read-only, against the Dovecot we already run.

A small thing that connects as the user, blocks, and returns when a new
mail comes in.

Same contract as sessionmail._detect (A08):

  - ARRIVALS ONLY: UIDNEXT at arm is the snapshot; a message with a
    lower UID is queue depth, not a wake. This differs from A08 on
    purpose. A08's condition is "new/ is not empty", and new/ is a
    maildir fact. IMAP's nearest fact is SEARCH UNSEEN, and that is a
    SEARCH over the whole mailbox (8k messages on one measured INBOX) - the
    other leg. Polling can tell how many are unread; for fast flow what
    matters is when a message goes into new, and that is an event. Counts
    and unread are the poll leg's question; this answers only "did one
    just arrive". Do not "fix" this into a counter.
  - BLOCKS in IMAP IDLE (RFC 2177) - a genuine push from the server.
  - A POLL LEG behind it: the IDLE wait is bounded to POLL seconds; on
    every return, event or not, the parent is checked; every REISSUE
    seconds IDLE is ended, a NOOP asked (which reports what a lost push
    would have), and IDLE re-issued - inside RFC 2177's 29 minutes.
  - READ-ONLY BY CONSTRUCTION: EXAMINE, never SELECT, and BODY.PEEK for
    the one header it reads. Read state cannot change under the owner.
  - RETURNS EVERY ARRIVAL, oldest UID first; SETTLE as in A08 (Q02).
  - DETECTS ONLY, and DIES WITH ITS CALLER.

Program door:

    python -m sessionmail._detect_imap --user U --password-file F
        [--host 127.0.0.1] [--port 1143] [--ssl] [--mailbox INBOX]
        [--poll S] [--timeout S] [--settle S] [--reissue S]

The password comes from a FILE, never argv or the environment (`ps`
publishes argv; the environment leaks to every child). Prints
`<Message-ID>` per arrival on stdout; exit 0 on arrival, 124 on timeout,
2 when it cannot connect, examine, or the password is offered on argv.
Stderr carries a banner and, after a wake, the FLAGS of what woke it -
the proof that watching did not mark.

imaplib has no IDLE before Python 3.14; the six lines that do it here
are the textbook ones (tag IDLE, read the continuation, wait, DONE,
read to the tagged reply).
"""

from __future__ import annotations

import os
import re
import select
import ssl
import sys
import time
from dataclasses import dataclass
import imaplib

DEFAULT_POLL = 1.0
DEFAULT_REISSUE = 300.0        # well inside RFC 2177's 29 minutes
EX_ARRIVED, EX_COULD_NOT_WATCH, EX_TIMEOUT = 0, 2, 124

_EXISTS = re.compile(rb"^\* (\d+) EXISTS")
_MSGID = re.compile(rb"Message-ID:\s*<?([^>\r\n]+)>?", re.I)
_UID = re.compile(rb"UID (\d+)")
_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")


class CouldNotWatch(Exception):
    pass


class Timeout(Exception):
    pass


class CallerGone(Exception):
    pass


@dataclass(frozen=True)
class Arrival:
    uid: int
    message_id: str
    flags: str


def connect(host: str, port: int, use_ssl: bool, user: str, password: str,
            timeout: float = 30.0) -> imaplib.IMAP4:
    try:
        if use_ssl:
            imap = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(),
                                     timeout=timeout)
        else:
            imap = imaplib.IMAP4(host, port, timeout=timeout)
        imap.login(user, password)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise CouldNotWatch(f"{user}@{host}:{port}: {exc}") from exc
    return imap


def examine(imap: imaplib.IMAP4, mailbox: str) -> tuple[int, int]:
    """EXAMINE the mailbox; return (EXISTS, UIDNEXT)."""
    typ, data = imap.select(mailbox, readonly=True)
    if typ != "OK":
        raise CouldNotWatch(f"EXAMINE {mailbox}: {typ} {data}")
    exists = int(data[0])
    _, uidnext = imap.response("UIDNEXT")
    if not uidnext or uidnext[0] is None:
        raise CouldNotWatch(f"EXAMINE {mailbox} gave no UIDNEXT")
    return exists, int(uidnext[0])


def uidvalidity(imap: imaplib.IMAP4) -> int | None:
    """The UIDVALIDITY the last EXAMINE reported, or None if the server
    sent none. A cursor (a UID) means nothing across a change of it."""
    _, value = imap.response("UIDVALIDITY")
    try:
        return int(value[0]) if value and value[0] is not None else None
    except (TypeError, ValueError):
        return None


def _readable(imap: imaplib.IMAP4, seconds: float) -> bool:
    """Wait up to SECONDS for a server line. select() on the socket, plus
    TLS's own buffer (bytes decrypted but not yet read). imaplib's file
    object is NOT given a timeout: once one fires on it, CPython refuses
    every later read ("cannot read from timed out object")."""
    sock = imap.sock
    if getattr(sock, "pending", lambda: 0)():
        return True
    r, _, _ = select.select([sock], [], [], seconds)
    return bool(r)


def _idle(imap: imaplib.IMAP4, seconds: float) -> bool:
    """One IDLE of at most SECONDS; True when the server pushed an EXISTS.
    Ends the IDLE before returning. A push the server wrote in the same
    segment as the continuation reply sits in imaplib's buffer where
    select() cannot see it; the drain after DONE reads it, so such a push
    costs one wait, never a loss."""
    tag = imap._new_tag()
    imap.send(tag + b" IDLE\r\n")
    line = imap.readline()
    if not line.startswith(b"+"):
        raise CouldNotWatch(f"IDLE refused: {line.decode(errors='replace').strip()}")
    pushed = False
    deadline = time.monotonic() + seconds
    while True:
        left = deadline - time.monotonic()
        if left <= 0 or not _readable(imap, left):
            break
        line = imap.readline()
        if not line:
            raise CouldNotWatch("connection closed inside IDLE")
        if _EXISTS.match(line):
            pushed = True
            break
    imap.send(b"DONE\r\n")
    while True:
        line = imap.readline()
        if not line:
            raise CouldNotWatch("connection closed ending IDLE")
        if line.startswith(tag):
            break
        if _EXISTS.match(line):
            pushed = True
    return pushed


def _arrivals(imap: imaplib.IMAP4, uidnext: int) -> list[Arrival]:
    typ, data = imap.uid("SEARCH", None, f"UID {uidnext}:*")
    if typ != "OK":
        raise CouldNotWatch(f"UID SEARCH: {typ} {data}")
    uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) >= uidnext)
    if not uids:
        return []
    typ, data = imap.uid("FETCH", ",".join(map(str, uids)),
                         "(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    if typ != "OK":
        raise CouldNotWatch(f"UID FETCH: {typ} {data}")
    out: dict[int, Arrival] = {}
    for item in data:
        if not isinstance(item, tuple):
            continue
        head, body = item
        uid = _UID.search(head)
        if not uid:
            continue
        flags = _FLAGS.search(head)
        mid = _MSGID.search(body or b"")
        out[int(uid.group(1))] = Arrival(
            int(uid.group(1)),
            mid.group(1).decode(errors="replace").strip() if mid else "",
            flags.group(1).decode(errors="replace") if flags else "",
        )
    return [out[u] for u in uids if u in out]


def detect(imap: imaplib.IMAP4, mailbox: str, *, poll: float = DEFAULT_POLL,
           timeout: float | None = None, settle: float = 0.0,
           reissue: float = DEFAULT_REISSUE, parent: int | None = None,
           say=None, since: tuple[int, int] | None = None,
           on_base=None) -> list[Arrival]:
    """SINCE is a cursor, (UIDVALIDITY, UID), kept by a caller that re-arms
    (the pager, spike/pager-imap-idle): a message that landed between the
    last page and this arm has a UID below the server's UIDNEXT, and
    arrivals-only would never report it. With a cursor of the same
    UIDVALIDITY the base is the lower of the two, and anything already at
    or above it is returned at once, without an IDLE. A cursor of another
    UIDVALIDITY names other messages: it is dropped, and said. ON_BASE, if
    given, is called with (UIDVALIDITY, base) once the base is known, so
    the caller can store the cursor it will advance."""
    exists, uidnext = examine(imap, mailbox)
    validity = uidvalidity(imap)
    base = uidnext
    if since is not None:
        if validity is not None and since[0] == validity:
            base = min(since[1], uidnext)
        elif say:
            say(f"cursor     : UIDVALIDITY {since[0]} is not the server's {validity} "
                f"- cursor dropped, arrivals from UIDNEXT {uidnext}")
    if on_base:
        on_base(validity, base)
    if say:
        say(f"examine    : {mailbox} read-only, {exists} exist, UIDNEXT {uidnext} "
            f"- arrivals are UID >= {base}"
            + (" (the cursor from the last page)" if base < uidnext else ""))
    if base < uidnext:
        backlog = _arrivals(imap, base)
        if backlog:
            return backlog
    uidnext = base
    deadline = None if timeout is None else time.monotonic() + timeout
    next_reissue = time.monotonic() + reissue
    seen: list[Arrival] = []
    quiet_since: float | None = None
    while True:
        now = time.monotonic()
        if quiet_since is not None:
            wait = max(0.0, settle - (now - quiet_since))
            if wait == 0.0:
                break
        else:
            wait = poll
            if deadline is not None:
                wait = max(0.0, min(poll, deadline - now))
        pushed = _idle(imap, max(wait, 0.05))
        if now >= next_reissue:
            imap.noop()               # the poll leg proper: ask what a lost push would have said
            next_reissue = now + reissue
        found = _arrivals(imap, uidnext) if (pushed or quiet_since is not None
                                             or now >= next_reissue - reissue + poll) else []
        if len(found) > len(seen):
            seen = found
            quiet_since = time.monotonic()
            if settle <= 0:
                break
            continue
        if quiet_since is not None:
            continue
        if parent is not None and os.getppid() != parent:
            raise CallerGone(f"caller {parent} went; not waiting alone")
        if deadline is not None and time.monotonic() >= deadline:
            raise Timeout(f"nothing arrived in {mailbox} within {timeout:g}s")
    return seen


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m sessionmail._detect_imap",
        description="block in IMAP IDLE on one mailbox, read-only; print each "
                    "arrival's Message-ID; exit")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1143)
    ap.add_argument("--ssl", action="store_true")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password-file", required=True,
                    help="file holding the password; never argv, never env")
    ap.add_argument("--password", help=argparse.SUPPRESS)
    ap.add_argument("--mailbox", default="INBOX")
    ap.add_argument("--poll", type=float, default=DEFAULT_POLL)
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--settle", type=float, default=0.0)
    ap.add_argument("--reissue", type=float, default=DEFAULT_REISSUE)
    args = ap.parse_args(argv)

    def say(line: str) -> None:
        print(f"detect-imap: {line}", file=sys.stderr)

    if args.password is not None:
        say("a password never travels on argv - ps publishes argv to every process; "
            "use --password-file")
        return EX_COULD_NOT_WATCH
    try:
        password = open(os.path.expanduser(args.password_file)).read().strip()
    except OSError as exc:
        say(f"password file: {exc}")
        return EX_COULD_NOT_WATCH

    try:
        imap = connect(args.host, args.port, args.ssl, args.user, password)
    except CouldNotWatch as exc:
        say(str(exc))
        return EX_COULD_NOT_WATCH
    say(f"connected  : {args.user}@{args.host}:{args.port}"
        f"{' tls' if args.ssl else ' plain'}; idle, poll {args.poll:g}s, "
        f"reissue {args.reissue:g}s")
    try:
        arrivals = detect(imap, args.mailbox, poll=args.poll, timeout=args.timeout,
                          settle=args.settle, reissue=args.reissue,
                          parent=os.getppid(), say=say)
    except CouldNotWatch as exc:
        say(str(exc))
        return EX_COULD_NOT_WATCH
    except Timeout:
        return EX_TIMEOUT
    except CallerGone as exc:
        say(str(exc))
        return EX_ARRIVED
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    for a in arrivals:
        say(f"woke on    : UID {a.uid} flags ({a.flags}) - "
            f"{'still unseen' if 'Seen' not in a.flags else 'SEEN - watching marked it'}")
        print(f"<{a.message_id}>" if a.message_id else f"uid:{a.uid}")
    return EX_ARRIVED


if __name__ == "__main__":
    sys.exit(main())
