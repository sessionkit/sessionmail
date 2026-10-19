"""The IMAP puller: a box on another host, mirrored into a LOCAL maildir.

SPIKE (spike/pager-imap-idle). PROPOSED, nothing here is ruled:
openspec/changes/pager-over-imap/.

The ask: a seat on another host should have the same mail. The first cut
put IMAP in `pager listen`, which made the page exit all the time; the
turn was a writer that only pulls: fetch it, drop it into new. So the pager
does not speak IMAP at all. This process sits in IMAP IDLE on ONE remote
mailbox (one box per session: a session should not be on several) and
delivers each arrival into a local maildir, tmp/ then new/.
Everything local - `pager listen`, the ride-along, `message read`,
`memory` - then works unchanged, because to them it IS a local box.

It builds on A09 (`_detect_imap`): IDLE with a poll leg behind it,
READ-ONLY by construction (EXAMINE, BODY.PEEK) - pulling never marks the
remote copy. What it adds:

  - A TARGET, one URL: `imaps://USER@HOST[:PORT][/MAILBOX]` (`imap://` is
    plain, for loopback), from `--imap` or `$SESSIONMAIL_IMAP`.
  - A PASSWORD FILE, never argv, URL or env: `$SESSIONMAIL_IMAP_PASSWORD_FILE`,
    else `<home>/imap/USER@HOST.pass`, refused unless owner-only.
  - A CURSOR, `<home>/imap/USER@HOST_PORT/MAILBOX.cursor` = "UIDVALIDITY
    UID", advanced after each delivery. A restart resumes from it, so
    nothing that landed while the puller was down is lost. First run ever:
    no backlog, only what arrives from now. A new UIDVALIDITY drops it.
  - EXACTLY ONCE: the file name carries `imapV<validity>U<uid>`; a UID
    already in new/ or cur/ is not delivered again (the crash window
    between the rename and the cursor write).

Program door (a spike's; where it lives is OPEN):

    python -m sessionmail._pull_imap --into MAILDIR [--imap URL] [--once]

Prints `pulled <Message-ID> -> new/NAME` per delivery. Exit: 0 done (or
stopped) · 1 usage · 2 could not look (URL, password, first connect).
"""

from __future__ import annotations

import os
import re
import socket
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import _detect_imap
from ._maildir import deliver_private, make_maildir, make_private_dirs, write_private

ENV_URL = "SESSIONMAIL_IMAP"
ENV_PASSWORD_FILE = "SESSIONMAIL_IMAP_PASSWORD_FILE"
EX_OK, EX_USAGE, EX_COULD_NOT_LOOK = 0, 1, 2
BACKOFF_MAX = 60.0


class BadTarget(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    user: str
    host: str
    port: int
    tls: bool
    mailbox: str

    def __str__(self) -> str:
        return f"{'imaps' if self.tls else 'imap'}://{self.user}@{self.host}:{self.port}/{self.mailbox}"

    @property
    def key(self) -> str:
        return f"{self.user}@{self.host}_{self.port}"


def parse(url: str) -> Target:
    """`imaps://USER@HOST[:PORT][/MAILBOX]`; a password in the URL is refused."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("imap", "imaps"):
        raise BadTarget(f"{url!r}: the scheme is imap:// or imaps://")
    if parts.password is not None:
        raise BadTarget("a password never rides in the URL - it is argv or env, and ps "
                        f"publishes both; put it in a file (${ENV_PASSWORD_FILE})")
    if not parts.username or not parts.hostname:
        raise BadTarget(f"{url!r}: USER@HOST are both needed")
    tls = parts.scheme == "imaps"
    try:
        port = parts.port or (993 if tls else 143)
    except ValueError as exc:
        raise BadTarget(f"{url!r}: {exc}") from exc
    return Target(unquote(parts.username), parts.hostname, port, tls,
                  unquote(parts.path.lstrip("/")) or "INBOX")


def password_path(target: Target, home: Path, env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    given = env.get(ENV_PASSWORD_FILE)
    return Path(given).expanduser() if given else home / "imap" / f"{target.user}@{target.host}.pass"


def read_password(path: Path) -> str:
    """Owner-only or refused: a file the group can read is a password the group has."""
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise BadTarget(f"password file {path}: {exc.strerror or exc}") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise BadTarget(f"password file {path} is mode {stat.S_IMODE(mode):o} - "
                        "only its owner may read it (chmod 600)")
    secret = path.read_text().strip()
    if not secret:
        raise BadTarget(f"password file {path} is empty")
    return secret


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]", "_", name) or "_"


def cursor_path(target: Target, home: Path) -> Path:
    return home / "imap" / _safe(target.key) / f"{_safe(target.mailbox)}.cursor"


def read_cursor(path: Path) -> tuple[int, int] | None:
    try:
        validity, uid = path.read_text().split()[:2]
        return int(validity), int(uid)
    except (OSError, ValueError):
        return None


def write_cursor(path: Path, validity: int | None, uid: int) -> None:
    if validity is None:
        return            # a UID without its UIDVALIDITY names nothing
    make_private_dirs(path.parent)            # 0700: it sits beside the password
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)                        # this pid's own leftover from a crash
    except FileNotFoundError:
        pass
    write_private(tmp, f"{validity} {uid}\n".encode(), fsync=False)   # 0600
    os.replace(tmp, path)


# -- the local maildir --------------------------------------------------------

def ensure_maildir(root: Path) -> None:
    """ROOT/{tmp,new,cur}: each level made here 0700, existing ones untouched
    (the same maker as `_seat.birth_box`)."""
    make_maildir(root)


def _tag(validity: int | None, uid: int) -> str:
    return f"imapV{validity if validity is not None else 0}U{uid}"


def already(root: Path, validity: int | None, uid: int) -> Path | None:
    """The copy of (validity, uid) already delivered, in new/ or cur/, if any."""
    tag = _tag(validity, uid)
    for sub in ("new", "cur"):
        for p in (root / sub).glob(f"*_{tag}.*"):
            return p
    return None


def deliver(root: Path, raw: bytes, validity: int | None, uid: int) -> Path:
    """Maildir delivery: write in tmp/, fsync, link into new/ - atomic, so a
    local reader never sees half a message, and a file already in new/ is
    never replaced. 0600 under any umask.

    A name collision retries under a fresh name (`_maildir.deliver_private`):
    the retry's `R<hex>` field goes BEFORE the `_imapV<v>U<uid>` tag, so
    `already()` still finds the copy. Three names failing is a broken box,
    not a collision: the FileExistsError escapes as before, with nothing
    written and the cursor not advanced (no loss, no double)."""
    host = re.sub(r"[/:]", "_", socket.gethostname()) or "localhost"
    now = time.time()
    stem = f"{int(now)}.M{int((now % 1) * 1e6)}P{os.getpid()}"
    tag = _tag(validity, uid)
    return deliver_private(root, raw, lambda fresh: f"{stem}{fresh}_{tag}.{host}")


def fetch(imap, uid: int) -> bytes:
    """The whole message, read with BODY.PEEK[] - never sets \\Seen."""
    typ, data = imap.uid("FETCH", str(uid), "(UID BODY.PEEK[])")
    if typ != "OK":
        raise _detect_imap.CouldNotWatch(f"UID FETCH {uid}: {typ} {data}")
    for item in data:
        if isinstance(item, tuple) and _detect_imap._UID.search(item[0]):
            return item[1]
    raise _detect_imap.CouldNotWatch(f"UID FETCH {uid}: no body came back")


def _message_id(raw: bytes) -> str:
    m = _detect_imap._MSGID.search(raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0])
    return f"<{m.group(1).decode(errors='replace').strip()}>" if m else "(no Message-ID)"


def pull_once(imap, target: Target, root: Path, cpath: Path, *, poll: float,
              reissue: float, timeout: float | None, say, out) -> int:
    """One wait: block in IDLE (from the cursor) until arrivals, deliver each,
    advance the cursor after each. Returns how many were delivered; raises
    _detect_imap.Timeout when nothing came within TIMEOUT."""
    state: dict[str, int | None] = {}

    def on_base(validity, base) -> None:
        state["validity"] = validity
        write_cursor(cpath, validity, base)

    arrivals = _detect_imap.detect(imap, target.mailbox, poll=poll, timeout=timeout,
                                   reissue=reissue, say=say, since=read_cursor(cpath),
                                   on_base=on_base)
    validity = state.get("validity")
    delivered = 0
    for a in arrivals:
        have = already(root, validity, a.uid)
        if have is None:
            raw = fetch(imap, a.uid)
            dest = deliver(root, raw, validity, a.uid)
            out(f"pulled {_message_id(raw)} -> {dest.parent.name}/{dest.name}")
            delivered += 1
        else:
            say(f"skip       : UID {a.uid} is already here ({have.parent.name}/{have.name})")
        write_cursor(cpath, validity, a.uid + 1)
    return delivered


def run(url: str, into: Path, *, home: Path, once: bool = False,
        poll: float = _detect_imap.DEFAULT_POLL,
        reissue: float = _detect_imap.DEFAULT_REISSUE,
        timeout: float | None = None, env: dict[str, str] | None = None,
        out=print, err=lambda s: print(s, file=sys.stderr)) -> int:
    """Resident unless ONCE: pull until stopped, reconnecting with backoff.
    ONCE: deliver what is waiting past the cursor (or wait up to TIMEOUT for
    the first arrival) and exit."""
    try:
        target = parse(url)
        secret = read_password(password_path(target, home, env))
    except BadTarget as exc:
        err(f"pull-imap: {exc}")
        return EX_COULD_NOT_LOOK
    into = into.expanduser()
    ensure_maildir(into)
    cpath = cursor_path(target, home)
    say = lambda s: err(f"  {s}")
    err(f"pull-imap: {target} -> {into}   [read-only, spike]")
    cur = read_cursor(cpath)
    err(f"  cursor     : {cpath}"
        + (f" (UIDVALIDITY {cur[0]}, UID {cur[1]})" if cur else
           " (none yet - hears only what arrives from now)"))
    backoff, first = 1.0, True
    while True:
        try:
            imap = _detect_imap.connect(target.host, target.port, target.tls,
                                        target.user, secret)
        except _detect_imap.CouldNotWatch as exc:
            if first or once:
                err(f"pull-imap: could not connect: {exc}")
                return EX_COULD_NOT_LOOK
            err(f"pull-imap: reconnect failed ({exc}); again in {backoff:g}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue
        first, backoff = False, 1.0
        try:
            while True:
                try:
                    pull_once(imap, target, into, cpath, poll=poll, reissue=reissue,
                              timeout=(timeout if once else reissue), say=say, out=out)
                except _detect_imap.Timeout:
                    if once:
                        return EX_OK
                    continue          # a quiet cycle: re-EXAMINE, re-IDLE
                if once:
                    return EX_OK
        except _detect_imap.CouldNotWatch as exc:
            if once:
                err(f"pull-imap: {exc}")
                return EX_COULD_NOT_LOOK
            err(f"pull-imap: lost the server ({exc}); reconnecting")
        finally:
            try:
                imap.logout()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    import argparse
    from . import _seat
    ap = argparse.ArgumentParser(
        prog="python -m sessionmail._pull_imap",
        description="mirror ONE remote mailbox into a local maildir over IMAP IDLE, "
                    "read-only; the local pager then hears it unchanged (spike)")
    ap.add_argument("--into", required=True, metavar="MAILDIR",
                    help="the local maildir to deliver into (made if missing)")
    ap.add_argument("--imap", metavar="URL", help=f"imaps://USER@HOST/MAILBOX (or ${ENV_URL})")
    ap.add_argument("--once", action="store_true",
                    help="deliver what is waiting, or the first arrival within --timeout; exit")
    ap.add_argument("--timeout", type=float, default=None, metavar="S")
    ap.add_argument("--poll", type=float, default=_detect_imap.DEFAULT_POLL, metavar="S")
    ap.add_argument("--reissue", type=float, default=_detect_imap.DEFAULT_REISSUE, metavar="S")
    args = ap.parse_args(argv)
    url = args.imap or os.environ.get(ENV_URL, "").strip()
    if not url:
        print(f"pull-imap: no target - give --imap URL or set ${ENV_URL}", file=sys.stderr)
        return EX_USAGE
    try:
        return run(url, Path(args.into), home=_seat.home(), once=args.once, poll=args.poll,
                   reissue=args.reissue, timeout=args.timeout,
                   out=lambda s: print(s, flush=True))
    except KeyboardInterrupt:
        return EX_OK


if __name__ == "__main__":
    sys.exit(main())
