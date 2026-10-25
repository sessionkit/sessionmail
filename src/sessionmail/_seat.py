"""The seat: who I am, where my own small files live, what I have marked.

A seat is the durable half of an agent, and outlives the sessions that
come and go. Its MARKS do not: they are per session. "Read is not terminal"
(`openspec/specs/pager/spec.md`) still holds inside a session - `pager
status` says "you read this and never finished it" - and a successor
starts with none of it.

Three things live here because three commands need them and none of them
owns them:

- **identity** - `openspec/specs/pager/spec.md`, "Identity comes from the
  environment": first hit wins, the source is printed, none of them set
  means refuse. Nothing is ever passed as an address.
- **the seat directory** - `<seat>/subscriptions/` is named on the
  authored pages (`help/pager/subscriptions.txt`); since
  message-subscriptions 4.2 it holds box-name files the pager never reads
  (see "subscriptions: retired from the pager" below). Where `<seat>`
  itself lives is named on NO page and in NO requirement, so the default
  below is INFERRED, not specified: see `SEAT_ROOT_IS_INFERRED`.
- **the session directory** - `<home>/sessions/<session-id>/` holds the
  queue, the stamps and the marks. Everything a page reads keys on the
  SESSION ID, never the seat. A seat is a NAME (the column in
  a marks line, the doorbell's address) and it MAY be absent; nothing a
  page reads depends on one. See `session_root`.
- **marks** - the line is MARK_LINE (it
  re-ordered the page's `help/message/mark.txt` form; both are read).
  `write_mark` is its only writer; `message read` and `message mark`
  are its only callers. Per session: a successor does NOT inherit them.

Of what `pager status` calls, only `write_mark`, `touch` and the queue
verbs write to disk (`write_born`, the seat's birth, retired with
message-subscriptions 4.2). `pager status` must be safe to run after
every tool call, and a ride-along that creates directories is a ride-along
that changes what it is measuring.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat

import sessiontools
import time
from datetime import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ._maildir import make_maildir

#: The seat-ish variables, in the order `openspec/specs/pager/spec.md`
#: fixes them, then the session id. MEASURED DIVERGENCE, reported not
#: silently patched: the retired `session-mail-arm` consults
#: `SESSION_SEAT` ahead of these two and `CLAUDE_CODE_SESSION_ID` ahead of
#: `CLAUDE_SESSION_ID`. The requirement names neither. We follow the
#: REQUIREMENT here and the divergence is filed, because widening an
#: identity rule to match an implementation is how a spec stops meaning
#: anything.
SEAT_VARS = ("TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT")
#: The session-id chain is sessiontools' tuple (>=0.0.3): one copy, not
#: three. The name stays,
#: because IDENTITY_VARS below and pager.py read it.
SESSION_VARS = sessiontools.SESSION_ID_VARS
IDENTITY_VARS = SEAT_VARS + SESSION_VARS

#: The variables a seat DECLARES its mail address in, first wins
#: (changes/task-address-doorbell D1). `EMAIL` is the declaration;
#: Taskmaster sets it. `TM_AGENT_ADDRESS` is the FALLBACK until Taskmaster
#: ships that half, and goes when it does. Only a PLUS address
#: (`<base>+<ext>`, RFC 5233) is a declaration: a bare `EMAIL=alice@...`
#: inherited from a human's shell declares nothing and moves no seat.
ADDRESS_VARS = ("EMAIL", "TM_AGENT_ADDRESS")

#: The HEARS variable, the extra addresses a seat heard, is RETIRED: it only
#: let a seat listen on its own base box, and a subscription does that job.
#: Nothing here reads it; an inherited one is inert, and Taskmaster stops
#: setting it.

#: `<home>/sessions/<sid>/address`: one line, the declared plus address.
#: The pager WRITES it (it holds the environment); the alias map generator
#: READS it and writes the row (the generator is the only writer of the
#: map).
ADDRESS_FILE = "address"

#: INFERRED: not specified, and not in any
#: requirement. The authored pages say `<seat>/subscriptions/` (and said
#: `<seat>/marks`, until marks moved under the session) without ever
#: saying where `<seat>` is. Two candidates
#: were live: inside the maildir (`~/.mail/agents/<name>/`) and beside it.
#: Beside it wins on one measured argument - `openspec/specs/pager/spec.md`
#: "Reading is harmless" promises a human may open the maildir in Mail.app,
#: neomutt or notmuch and change nothing, and a `subscriptions/` directory
#: sitting between `new/` and `cur/` is not a maildir. Override with
#: `SESSIONMAIL_HOME`; retract freely if a requirement says otherwise.
SEAT_ROOT_IS_INFERRED = True
DEFAULT_HOME = "~/.sessionmail"

#: A directory name, not an address: `alice@host` and `alice+role` are two
#: addresses of ONE seat (specs/pager, "The banner tells the truth"), so
#: the domain and the plus-extension are cut before the name is a path.
#: Also inferred; also cheap to change.
_UNSAFE = re.compile(r"[^a-z0-9._-]+")

#: An ADDRESS keeps its `+`: `seats/sessionmail+tm-123.foreman/` (one
#: folder rule). Kept, not mapped to `-`, so a plus seat's folder can
#: never collide with a real box named `<base>-<ext>`.
_UNSAFE_ADDRESS = re.compile(r"[^a-z0-9._+-]+")


class NoIdentity(Exception):
    """Nothing in the environment says who this seat is."""

    def __init__(self) -> None:
        super().__init__(
            "no identity in the environment - looked for "
            + ", ".join(IDENTITY_VARS)
            + ", in that order"
        )


@dataclass(frozen=True)
class Seat:
    """Who we are, where that was read from, and where the seat's files are."""

    name: str
    source: str
    root: Path
    #: The seat's full address, the `+` KEPT: what the doorbell
    #: asks postfix for, and the seat folder's name. `name` is unchanged -
    #: marks, the banner's seat line and message's seat-box fallback keep
    #: reading it. Defaults to `name` for a Seat built by hand.
    address: str = ""

    def __post_init__(self) -> None:
        if not self.address:
            object.__setattr__(self, "address", self.name)

    @property
    def subscriptions(self) -> Path:
        return self.root / "subscriptions"

    #: There is deliberately NO `marks` here any more. Marks key on the
    #: session (`marks_path`), and a property that still answered
    #: `<seat>/marks` would read an empty file in silence - the worst
    #: kind of wrong, because it looks like "nothing marked yet".


def seat_name(address: str) -> str:
    """The directory name for ADDRESS: no domain, no plus-extension, safe."""
    local = address.strip().lower().split("@", 1)[0].split("+", 1)[0]
    return _UNSAFE.sub("-", local).strip("-")


def fold_address(value: str) -> str:
    """The local part of VALUE, lowercase, safe, with the `+` KEPT."""
    local = value.strip().lower().split("@", 1)[0]
    return _UNSAFE_ADDRESS.sub("-", local).strip("-")


def split_plus(address: str) -> tuple[str, str] | None:
    """(base, ext) for a plus address `<base>+<ext>`, else None.

    RFC 5233 and nothing more: split at the FIRST `+`, both halves non-empty.
    Sessionmail knows no project, task or role grammar: naming belongs to
    the tool that mints the address, so `tm-123.foreman` is one opaque
    extension and nothing here ever splits on `.`."""
    base, plus, ext = address.partition("+")
    if not plus or not base or not ext:
        return None
    return base, ext


def local_domain(value: str) -> bool:
    """True when VALUE's domain is absent or names THIS host (`alpha`,
    `alpha.<anything>` on a host named alpha). A plus address at any other
    domain - a human's `name+tag@gmail.com` in an inherited EMAIL - is not a
    seat on this host and declares nothing."""
    _, at, domain = value.strip().lower().partition("@")
    if not at:
        return True
    host = short_host()
    return bool(host) and (domain == host or domain.startswith(host + "."))


def declared_address(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(address, variable) - the first of ADDRESS_VARS holding a PLUS
    address on this host, folded - or None when no variable declares one."""
    env = os.environ if env is None else env
    for var in ADDRESS_VARS:
        value = env.get(var) or ""
        address = fold_address(value)
        if split_plus(address) and local_domain(value):
            return address, var
    return None


def own_addresses(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """Every address THIS SESSION answers to, folded, no repeats.

    The declared seat address and this session's id. (A base the retired
    HEARS variable named was a third kind until message-subscriptions 4.8;
    mail a SUBSCRIPTION copies in is answered from the seat address, and
    the send says it fell back.) Folded (`fold_address`) because the same address
    arrives in three spellings - bare, `@alpha`, FQDN - and a verbatim set
    would miss two of them; `specs/pager` already rules the comparison
    ("Addresses SHALL be grouped by their LOCAL PART, the host discarded,
    and the `+extension` KEPT", measured on the operator's box: 8,669 messages,
    five spellings, three readers).

    The seat is resolved leniently: a session with no seat identity still
    answers to its own id, and that is the case the DM rule most cares
    about, so NoIdentity is not fatal here.
    """
    env = os.environ if env is None else env
    out: list[str] = []
    try:
        out.append(resolve(env).address)
    except NoIdentity:
        pass
    sid = session_id(env)
    if sid not in ("", "-"):
        out.append(sid)
    seen: list[str] = []
    for value in out:
        folded = fold_address(value)
        if folded and folded not in seen:
            seen.append(folded)
    return tuple(seen)


def answers_to(address: str, env: dict[str, str] | None = None) -> str:
    """The address of OURS that ADDRESS reaches, or "" for none.

    Returns the address to answer AS, not a bool: a mention arrives at
    `<ours>+<suffix>` and is answered from `<ours>`, so the caller needs
    which one matched, not merely that one did.

    Both halves of the match are specified elsewhere: the DOMAIN must be absent
    or name this host (`local_domain` - a plus address at gmail is not a
    seat here), and the LOCAL PART is compared the way `specs/pager`
    already compares addresses.

    A SUFFIX BEYOND OUR OWN EXTENSION STILL REACHES US: `widget+lead+boot`,
    `widget+tm-123.lead+boot` and `notes+widget+session` (the boot shelf
    and its kin) are real, delivered traffic, and a seat named
    `widget+lead` plainly answers to the first.

    Matching on the `+` BOUNDARY, never a bare prefix: `widget+lead`
    must not swallow `widget+leader`.

    This is more than tidiness. Without it every reply to boot-shelf mail
    printed a fall-back note saying the session does not answer to an
    address postfix had just delivered to it - a warning firing on routine
    traffic, which is how a warning stops being read, and the notes it
    would be crowding out are the privacy one. For a SESSION ID it is the
    privacy rule itself: mail to `<sid>+<suffix>` is still a DM, and
    without this it would be answered from the seat.
    """
    if not address or not local_domain(address):
        return ""
    folded = fold_address(address)
    for own in own_addresses(env):
        if folded == own or folded.startswith(own + "+"):
            return own
    return ""


def refuses_base(seat: "Seat", box: str) -> str | None:
    """The base of a plus identity is not its box to listen on. The `<base>` a refusal names when BOTH
    hold - the identity is `<base>+<ext>`, and BOX is that `<base>` - else
    None. Its one way round, the base declared in the HEARS variable,
    is retired: a session that wants its base subscribes to it, and the
    bare listen hears it."""
    parts = split_plus(seat.address)
    if parts is None:
        return None
    base = parts[0]
    if fold_address(box) != base or "/" in box:
        return None
    return base


def home(env: dict[str, str] | None = None) -> Path:
    """The root under which every seat on this host keeps its own files."""
    env = os.environ if env is None else env
    return Path(env.get("SESSIONMAIL_HOME") or DEFAULT_HOME).expanduser()


#: The pager spec ("The pager is a nudge"): *"Everything a page reads - the queue, the stamps, the marks -
#: SHALL be keyed on the session id. A seat is a name and MAY be absent;
#: nothing in a page depends on one."*). So the three of them live under
#: `<home>/sessions/<session-id>/`, and a seated run and a seatless run of
#: the SAME session read exactly the same paths.
#:
#: That state lives under the SESSION DIR sessiontools keeps
#: for every session, `<session dir>/mail/`, found by ONE computation,
#: `sessiontools.settings.session_dir` - so a host whose sessions root is
#: `$SESSIONS_DIR` follows it the day sessiontools does, with no change
#: here. `<home>/sessions/<sid>` is the OLD root: `pager listen` moves a
#: LIVE session's dir there to `mail/` once and leaves a symlink for a
#: writer still running old code (`move_session_state`). Nothing reads the
#: old path: a dead session's state stays where it is, and a verb handed
#: its id says "not moved; at ..." rather than quietly reading there.
SESSIONS_DIR = "sessions"
MAIL_DIR = "mail"


def _settings():
    """`sessiontools.settings` (0.0.4+), named when it is missing: the tool
    venv behind the `pager` on PATH is upgraded apart from a merge."""
    try:
        from sessiontools import settings
    except ImportError as exc:
        raise RuntimeError(
            "sessiontools.settings is not installed (it ships in sessiontools "
            "0.0.4), so the session's mail dir cannot be found: "
            "`uv tool upgrade sessionmail`") from exc
    return settings


def old_session_root(session: str, env: dict[str, str] | None = None) -> Path | None:
    """`<home>/sessions/<SESSION>/`, where the state lived before 3.3a. Read
    by the mover, and by the one line that says a session was NOT moved."""
    if not session or session == "-":
        return None
    return home(env) / SESSIONS_DIR / session


def session_root(session: str, env: dict[str, str] | None = None) -> Path | None:
    """`<session dir>/mail/`, or None when there is no session id.

    None is not a failure: it is the case task 1.3 left OPEN (*"still open
    and no longer gating: a listener with no session id at all"*). The
    three callers below answer it the way they always have, under the
    seat - which is not a new claim about what a session is, and does not
    invent a shared `sessions/-` bucket that every seatless seat on the
    host would collide in.
    """
    if not session or session == "-":
        return None
    return _settings().session_dir(session, env) / MAIL_DIR


def make_root(root: Path) -> Path:
    """ROOT (a session's `mail/`) as a 0700 directory with a `.gitignore` of
    `*` (sessiontools' `ensure_private_dir`): `~/.session` is a git
    repo that tracks session dirs, and the pager-note key must never reach
    it. The session dir is made when the launcher has not."""
    root.parent.mkdir(parents=True, exist_ok=True)
    return _settings().ensure_private_dir(root)


def make_dir(d: Path, session: str, env: dict[str, str] | None = None) -> Path:
    """D made, parents too. When D is SESSION's mail root or under it, the
    root is made private FIRST, so no writer (a hook's stamp, a mark, a rule
    added before the first listen) leaves a 0755 `mail/` with no `.gitignore`
    for `~/.session`'s git to pick up. A seat path is made plainly."""
    root = session_root(session, env)
    if root is not None and (d == root or root in d.parents):
        ensure_root(session, env)
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_root(session: str, env: dict[str, str] | None = None) -> Path | None:
    """SESSION's `mail/`, made private - MOVED there first when the old root
    is still a real directory and `mail/` is not made yet. After the move
    every writer is new
    code at once: the hook stamps `ride-along` on every tool call and
    `message mark` appends, both before the session re-arms. So the FIRST
    new-code writer moves the state, under the flock `pager listen` takes,
    and the listen finds it "moved earlier"; the both-present fold is left
    to a race.

    Only the first: once `mail/` exists, a writer never folds. A both-
    present case (a race, a clash) is the LISTEN's to fold and to name -
    re-folded by every tool call, a clash the banner named would be
    undone behind it (an old `address` back in `mail/` the moment the
    listen's `clear_address` had removed the new one).

    A move that cannot finish (a refused rename) leaves the old root a
    directory, so the listen moves again and NAMES what stood in the way:
    nothing a writer's move met is lost for being said nowhere. An OSError
    here is the same case, and must not traceback a hook."""
    root = session_root(session, env)
    if root is None:
        return None
    old = old_session_root(session, env)
    if (old is not None and old.is_dir() and not old.is_symlink()
            and not (root.exists() or root.is_symlink())):
        try:
            move_session_state(session, env)
        except OSError:
            pass
    return make_root(root)


@dataclass
class Moved:
    """What `move_session_state` did: lines for the listen banner, and
    whether they belong in the session's box too (anything but a plain
    move, or nothing to move: an agent whose state did not move must learn
    it where it reads its mail)."""
    lines: list[str]
    noteworthy: bool = False


def move_session_state(session: str, env: dict[str, str] | None = None) -> Moved:
    """SESSION's state from the old root to `<session dir>/mail/`, ONCE, with
    a symlink left at the old path for a writer still running old code (the
    listener armed before the merge). Under an `flock` on the session dir
    itself - no lock file, the tree is a git repo. The cases:

    - the old path is a symlink already: moved earlier; nothing to do.
    - the old path is absent: nothing to move; `mail/` is made, the link placed.
    - a directory, and `mail/` absent: ONE rename, atomic on one disk. A
      rename that fails (another volume, say) moves nothing and is named;
      this run then writes under `mail/` and the old dir keeps what it had.
    - both present (a half-done move; a writer that got in first): the old
      entries are FOLDED into `mail/`, deep (`_fold`): directories entry by
      entry, the marks and verdicts logs merged, a stamp settled by mtime;
      any other name on both sides keeps the `mail/` copy and is named.
    - the gap: an old path that reappears before the symlink is folded once
      more and the link retried once; a second reappearance is named.
    """
    old = old_session_root(session, env)
    new = session_root(session, env)
    if old is None or new is None:
        return Moved([])
    new.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(new.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return _move_locked(old, new)
    finally:
        os.close(fd)


def _move_locked(old: Path, new: Path) -> Moved:
    if old.is_symlink():
        make_root(new)
        if old.resolve() == new.resolve():
            return Moved([f"{new} (moved earlier; {old} points here)"])
        return Moved([f"{new}; {old} is a symlink to {os.readlink(old)}, not here - "
                      "left alone"], True)
    fold = Fold()
    if old.is_dir():
        if new.exists():
            fold = _fold(old, new)
        else:
            moved = fold.moved = sum(1 for _ in old.iterdir())
            try:
                os.rename(old, new)
            except OSError as exc:
                return Moved([f"NOT MOVED - {old} could not be renamed to {new} "
                              f"({exc.strerror}); its {moved} entries stay there, and "
                              f"this run writes under {new}"], True)
    elif old.exists():
        make_root(new)
        return Moved([f"NOT MOVED - {old} is not a directory; left alone; this run "
                      f"writes under {new}"], True)
    make_root(new)
    if fold.merged or fold.stamps:
        done = ([f"{fold.moved} moved"]
                + [f"{name} merged (old lines first)" for name in fold.merged]
                + [f"{name} kept the newer" for name in fold.stamps])
        head = f"folded {old} in: " + ", ".join(done)
    elif fold.moved:
        head = f"moved {fold.moved} entries from {old}"
    else:
        head = f"nothing to move at {old}"
    lines = [f"{new} - {head}"]
    if fold.clashes:
        # The fold never overwrites: the old dir keeps the clashing entries,
        # so it stays a directory and the symlink is not placed over it.
        lines.append(f"{len(fold.clashes)} on both sides kept the {new.name}/ copy: "
                     + ", ".join(fold.clashes) + f"; the old copies stay at {old}, which is "
                     "NOT linked")
        return Moved(lines, True)
    link = _link(old, new)
    lines.append(link)
    return Moved(lines, not link.startswith(f"{old} now"))


#: The append-only logs at the top of a session's state (`marks_path`,
#: `verdicts_path`): on both sides they are both history, so a fold MERGES
#: them rather than keeping one (else mail marked done before the move
#: reads as not handled).
FOLD_LOGS = ("marks", "verdicts")


@dataclass
class Fold:
    """What `_fold` did. Names are relative to the old root (`stamps/x`)."""
    moved: int = 0
    merged: list[str] = field(default_factory=list)
    stamps: list[str] = field(default_factory=list)
    clashes: list[str] = field(default_factory=list)

    def add(self, other: "Fold") -> None:
        self.moved += other.moved
        self.merged += other.merged
        self.stamps += other.stamps
        self.clashes += other.clashes


def _real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _fold(old: Path, new: Path, rel: str = "") -> Fold:
    """OLD folded into NEW, DEEP (the shallow fold called a `stamps/` a hook had made before
    the re-arm a clash, so the old path was never linked). Never loses a
    line, never overwrites a choice it cannot make:

    - a name only OLD has: renamed in;
    - a directory on both sides: folded entry by entry, recursively;
    - `marks` / `verdicts` (FOLD_LOGS) at the top, a file on both sides:
      MERGED, OLD's lines first, then NEW's, one tmp + `os.replace`;
    - a stamp (a file under the top `stamps/`) on both sides: the newer
      mtime stays (NEW's on a tie) - a stamp's only fact is its time;
    - anything else on both sides: NEW's copy stays and the name is a clash.

    OLD (at each level) is removed when that empties it; a clash keeps it."""
    out = Fold()
    for entry in sorted(old.iterdir()):
        name = rel + entry.name
        dest = new / entry.name
        if not (dest.exists() or dest.is_symlink()):
            os.rename(entry, dest)
            out.moved += 1
        elif _real_dir(entry) and _real_dir(dest):
            out.add(_fold(entry, dest, name + "/"))
        elif _real_file(entry) and _real_file(dest) and not rel and entry.name in FOLD_LOGS:
            _merge_log(entry, dest)
            out.merged.append(name)
        elif _real_file(entry) and _real_file(dest) and rel == STAMPS_DIR + "/":
            if entry.stat().st_mtime_ns > dest.stat().st_mtime_ns:
                os.replace(entry, dest)
            else:
                entry.unlink()
            out.stamps.append(name)
        else:
            out.clashes.append(name)
    if not out.clashes:
        try:
            old.rmdir()
        except OSError:
            pass
    return out


def _merge_log(old: Path, new: Path) -> None:
    """NEW rewritten as OLD's lines then its own, atomically (a tmp beside it,
    `os.replace`), keeping NEW's mode; then OLD goes. Both are append-only
    in file order, and OLD's lines are the earlier ones."""
    head = old.read_bytes()
    if head and not head.endswith(b"\n"):
        head += b"\n"
    tmp = new.with_name(f".{new.name}.fold.{os.getpid()}")
    tmp.write_bytes(head + new.read_bytes())
    os.chmod(tmp, stat.S_IMODE(new.stat().st_mode))
    os.replace(tmp, new)
    old.unlink()


def _link(old: Path, new: Path) -> str:
    """A symlink NEW at OLD; the line that says so, or what stood in the way.
    The gap: OLD back as a directory before the link is
    folded into NEW once and the link retried once."""
    old.parent.mkdir(parents=True, exist_ok=True)   # a host (or test home) with no old root yet
    for attempt in (1, 2):
        try:
            os.symlink(new, old)
            return f"{old} now points there"
        except FileExistsError:
            pass
        if old.is_symlink():
            if old.resolve() == new.resolve():
                return f"{old} now points there (linked by another run)"
            return f"NOT LINKED - {old} is a symlink to {os.readlink(old)}, not {new}"
        if not old.is_dir():
            return f"NOT LINKED - {old} is in the way and is not a directory"
        if attempt == 2:
            break
        fold = _fold(old, new)
        if fold.clashes:
            return (f"NOT LINKED - {old} reappeared (a writer on old code) with "
                    f"{len(fold.clashes)} names {new} already had: {', '.join(fold.clashes)}; "
                    f"{fold.moved} folded in, the rest stays at {old}")
    return f"NOT LINKED - {old} reappeared twice (a writer on old code); left as it is"


def write_address(session: str, address: str, env: dict[str, str] | None = None) -> Path | None:
    """Declare ADDRESS for SESSION: the `address` key of its layer, in the
    file the session's verbs write, `<session dir>/settings/mail.d/70-live.json`
    (task 3.4; 3.3b wrote `mail.json`, which the conversion keeps and the fold
    still reads, first and lowest; it was `<home>/sessions/<sid>/address`).
    The file's path; None when there is no session id to key it on. Logged to the session's `log/settings.jsonl`
    when it changed. Raises `_layer.LayerError` when it cannot be written.

    The pager calls this BEFORE its first doorbell check, so a
    regen that check triggers already sees the key and writes the row."""
    if session_root(session, env) is None:
        return None
    from . import _layer
    directory = _layer.session_settings_dir(session, env)
    if _layer.set_value(directory, _layer.ADDRESS, address, file=_layer.LIVE):
        _layer.log(directory.parent, _layer.ADDRESS, address, by="pager listen",
                   argv="pager listen")
    return directory / _layer.LIVE


def clear_address(session: str, env: dict[str, str] | None = None) -> Path | None:
    """Remove SESSION's declaration when this listen declares nothing; the
    file it was removed from, or None when there was none. A RESUMED session
    keeps its id, so a key left from a declaring run would keep the
    generator writing a row this run no longer claims. Raises `_layer.LayerError` when it cannot be written."""
    if session_root(session, env) is None:
        return None
    from . import _layer
    directory = _layer.session_settings_dir(session, env)
    # From both files the session's verbs have written: the verbs' own (3.4)
    # and `mail.json` (3.3b's, and the conversion's). The verbs' file is
    # named when both held it: it is the one that was in force.
    removed = [name for name in (_layer.LIVE, _layer.FILE)
               if _layer.set_value(directory, _layer.ADDRESS, None, file=name)]
    if not removed:
        return None
    _layer.log(directory.parent, _layer.ADDRESS, None, by="pager listen", argv="pager listen")
    return directory / removed[0]


def read_address(session: str, env: dict[str, str] | None = None) -> str:
    """The plus address SESSION declares (its layer's `address`), lower case;
    "" when none, when there is no session id, or when the layer cannot be
    read - a reader that cannot look routes nothing, as the generator skips."""
    if session_root(session, env) is None:
        return ""
    from . import _layer
    try:
        values, _origins = _layer.read(_layer.session_settings_dir(session, env), "session")
    except _layer.LayerError:
        return ""
    return _layer.address(values)


def resolve(env: dict[str, str] | None = None) -> Seat:
    """The seat this process belongs to. Raises NoIdentity if nothing says."""
    env = os.environ if env is None else env
    for var in IDENTITY_VARS:
        value = (env.get(var) or "").strip()
        if value:
            name = seat_name(value)
            if name:
                declared = declared_address(env)
                address = (declared[0] if declared else fold_address(value)) or name
                return Seat(name=name, source=var, root=home(env) / "seats" / address,
                            address=address)
    raise NoIdentity()


# ── subscriptions: retired from the pager ────────────────────────────────
#
# message-subscriptions 4.2 (specs/pager, REMOVED "A subscription belongs to
# the seat" and "A subscription is a query"): the saved-query files, their
# born templates (`BORN`, `write_born`), the deaf-seat migration
# (`migrate_born`, `migrate_seat_file`, `migrate_all`) and the reader that
# ran them (`read_subscriptions`, `parse_subscription`, `expand_query`,
# routes and policy) are gone. The listener had long stopped reading
# them; `pager status` and `pager subscriptions` were the last readers.
# A subscription is now one file naming a box, compiled into the alias map
# (`_subscription`, `_subscribe`, the generator); the pager reads none.
# `Seat.subscriptions` still names the seat's folder, which now holds those.


class Malformed(Exception):
    """A seat file that cannot be honoured. Never skipped quietly."""


#: The SHORT host, and the reason the self-exclusion is not deaf.
#:
#: notmuch matches an address field by WORD, not by substring: `from:cor`
#: matches nothing, but `from:alice` matches `alice-chat@alpha` - because
#: `alice-chat` tokenises to the words `alice` and `chat`. So the bare
#: `not from:$SEAT` every seat was born with excluded the seat's SIBLINGS as
#: well as the seat: the Delta Chat bridge (`$SEAT-chat@`), and any
#: `$SEAT+label@`. Measured on a live index: `from:alice-chat and
#: to:alice` = 44, and `and not from:alice` = 0 - every message the chat
#: bridge carried dropped, silently, because THE ALARM IS THE QUERY.
#:
#: Qualifying the term with a host makes it a PHRASE, which no sibling
#: satisfies. It is deliberately the SHORT host and not the FQDN, and that is
#: a measurement, not a taste: an address term is a phrase PREFIX, so
#: `from:alice@alpha` matches the seat's own mail at BOTH `alice@alpha` and
#: `alice@alpha.<suffix>`, while `from:alice@alpha.<suffix>`
#: would MISS the bare-host form and re-open the hole on the other side.
def short_host(env: Mapping[str, str] | None = None) -> str:
    """This host's NAME, by the one rule (`_host.name`): the
    variable, else the settings `host`, else the short hostname, lowercased.
    ENV defaults to `os.environ`, so the old 0-argument call still works."""
    from . import _host
    return _host.name(env)


# ── marks ────────────────────────────────────────────────────────────────

#: The three marks, `help/message/mark.txt`. The LINE they sit on changed
#: after that page was written: MARK_LINE, below.
MARKS = ("seen", "read", "done")

#: The field order: timestamp, message id, mark, seat, session, and then
#: the free-text reason. Timestamp first because the
#: file is a log - if the tail were lost you would still know that at this
#: time this message was seen. Seat before session because the seat is the
#: name a human reads and the session is hex. Neither is ever blank: the
#: seat is what `resolve` said (a bare session's seat is its own id - never
#: `none`, which would assume it knows how something else is working), and
#: a seat with no session writes `-`.
MARK_LINE = "<stamp> <message-id> <mark> <seat> <session-id> [reason ...]"


@dataclass(frozen=True)
class Mark:
    """One event line of the session's marks file, every field kept."""

    stamp: str
    message_id: str
    mark: str
    seat: str
    session: str
    reason: str = ""

    @property
    def line(self) -> str:
        head = f"{self.stamp} {self.message_id} {self.mark} {self.seat} {self.session}"
        return f"{head} {self.reason}" if self.reason else head


def parse_mark(line: str) -> Mark | None:
    """The Mark on LINE, or None for a line that is not one.

    Two forms are read. The six-field line (MARK_LINE), and the older
    four-field line -
    `<message-id> <mark> <session-id> <stamp>` - which is still on disk and
    still counts: a `done` recorded last week is not undone by a
    re-ordering. The old form has no seat column and is read as `-`. The
    forms cannot collide: a six-field line's third word is a mark and its
    second is a message id; an old line's second word is the mark.
    """
    parts = line.split()
    if len(parts) >= 5 and parts[2] in MARKS:
        return Mark(parts[0], parts[1].strip("<>"), parts[2], parts[3], parts[4],
                    " ".join(parts[5:]))
    if len(parts) >= 2 and parts[1] in MARKS:
        session = parts[2] if len(parts) > 2 else "-"
        stamp = parts[3] if len(parts) > 3 else ""
        return Mark(stamp, parts[0].strip("<>"), parts[1], "-", session)
    return None


def marks_path(seat: Seat | None, session: str) -> Path:
    """SESSION's marks file. Per session - a successor does not inherit
    them. A run with no session id at all
    keeps the seat-wide file it has always written (`session_root`)."""
    root = session_root(session)
    if root is not None:
        return root / "marks"
    if seat is None:
        raise NoIdentity()
    return seat.root / "marks"


def read_mark_events(seat: Seat | None, session: str) -> list[Mark]:
    """Every mark THIS SESSION has recorded, in file order. Missing = [].

    Per session, not per seat. The seat
    column is still on every line - `given_since` and `overdue` filter on
    the session field as they always did, which is now belt and braces
    rather than the only guard.
    """
    return read_marks_at(marks_path(seat, session))[0]


def _read_lines(path: Path) -> list[str] | None:
    """PATH's lines, decoded as UTF-8 with replacement; None when PATH (or
    the directory it would sit in) does not exist. Any other OSError - a
    file that exists and cannot be read, a directory where a file should
    be - is raised: the caller tells "no rows" from "could not look".

    The ONE decode for the marks and verdicts files. Marks were once
    read with the locale's codec and strict errors, so one
    byte that is not UTF-8 in a reason raised `UnicodeDecodeError` (a
    ValueError) past every `except (Malformed, OSError)` that reads them:
    `message list --unread`, `pager status`, `pager listen`, the hook."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    return text.splitlines()


def read_marks_at(path: Path) -> tuple[list[Mark], int]:
    """Every Mark in the marks file at PATH, in file order, and how many
    lines it passed over (a line `parse_mark` does not read: a torn write,
    a blank line). Missing file = ([], 0). Unreadable = OSError, raised.

    The one parser under `read_mark_events`. It does NOT filter
    on the session column: that is each caller's guard."""
    lines = _read_lines(path)
    if lines is None:
        return [], 0
    out: list[Mark] = []
    for line in lines:
        mark = parse_mark(line)
        if mark is not None:
            out.append(mark)
    return out, len(lines) - len(out)


def read_marks(seat: Seat | None, session: str) -> dict[str, set[str]]:
    """Message-ID -> the marks THIS SESSION has recorded. Missing = {}.

    The view `pager status` and `message list --unread` want. The events
    themselves - with their stamps, seats and sessions, which specs/pager
    "Read is not terminal" needs to say "seen by A at its time" - are
    `read_mark_events`.
    """
    out: dict[str, set[str]] = {}
    for m in read_mark_events(seat, session):
        out.setdefault(m.message_id, set()).add(m.mark)
    return out


# ── verdicts: what the rules decided for each mail (pager-filters 5.3) ────
#: `pager why ID` answers from what was DECIDED, never from a re-judge (the
#: rules may have changed since). So each verdict is recorded where the
#: marks live, one JSON object a line, keyed by Message-ID: `message_id`,
#: `action`, `rule`, `layer`, `line`, `at_once`, `by`, `stamp`, and
#: `promoted`/`bad` when they apply. Append, never rewrite,
#: as the marks are.
VERDICTS_FILE = "verdicts"


def verdicts_path(seat: Seat | None, session: str) -> Path:
    """SESSION's verdicts file: beside its marks (`marks_path`), so a run
    with no session id keeps it under the seat, as it keeps the marks."""
    return marks_path(seat, session).with_name(VERDICTS_FILE)


def write_verdict(seat: Seat | None, session: str, record: dict) -> None:
    """Append ONE verdict RECORD (a JSON object, on one line)."""
    path = verdicts_path(seat, session)
    make_dir(path.parent, session)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_verdicts(seat: Seat | None, session: str) -> list[dict]:
    """Every verdict recorded for SESSION, in file order. Missing = []. A
    line that is not a JSON object (a torn write) is passed over."""
    return read_verdicts_at(verdicts_path(seat, session))[0]


def read_verdicts_at(path: Path) -> tuple[list[dict], int]:
    """Every verdict record in the verdicts file at PATH, in file order, and
    how many lines it passed over: a line that is not a JSON object with a
    non-empty `message_id` (a torn write, a blank line). Missing file =
    ([], 0). Unreadable = OSError, raised.

    The one verdict line rule, under `read_verdicts`."""
    lines = _read_lines(path)
    if lines is None:
        return [], 0
    out: list[dict] = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("message_id"):
            out.append(record)
    return out, len(lines) - len(out)


# ── who is awake ─────────────────────────────────────────────────────────
#: The page fires only when a seat has stopped doing tool use; while it
#: works, mail is left for the ride-along. Two stamps once tried to
#: say this - the ride-along's (PostToolBatch: busy) and the Stop hook's
#: (stopped) - but Stop fires at EVERY turn boundary, so "stopped" was
#: never idle (changes/stopped-means-idle). Idle is now Claude Code's own
#: status through `session get idle` (below, `idle_seconds`). The
#: ride-along stamp stays: the-quiet-page reads it for the banner.
RIDE_ALONG_STAMP = "ride-along"
#: Written by the listener's page when a paged
#: verdict CARRIES; claimed by the UserPromptSubmit hook by unlinking it, so
#: the wake turn runs the ride-along once. PostToolBatch drops a stale one.
CARRY_STAMP = "carry"
STOPPED_STAMP = "stopped"     # LEGACY: neither written nor read (stopped-means-idle)
STAMPS_DIR = "stamps"


def stamp_dir(seat: Seat | None, session: str) -> Path:
    """Where SESSION's stamps live: `<home>/sessions/<session>/stamps/`.

    A stamp was always per session: a seat-wide pair let session A's tool
    call defer session B's wake, and A's stop wake B mid-turn. The SESSION
    is also the root, so a seatless run of the same session reads the same
    stamps. A session of `-` (a bare seat) keeps the seat pair it has
    always had, and the banner says."""
    root = session_root(session)
    if root is not None:
        return root / STAMPS_DIR
    if seat is None:
        raise NoIdentity()
    return seat.root / STAMPS_DIR / "-"


def touch(seat: Seat, name: str, session: str) -> Path:
    """Set SESSION's <name> stamp's mtime to now (creating it). Returns it."""
    d = make_dir(stamp_dir(seat, session), session)
    path = d / name
    path.touch()
    os.utime(path, None)
    return path


def write_carry(seat: Seat, session: str) -> Path:
    """Write SESSION's carry stamp: the page fired for a
    carrying verdict, so the wake turn may run the ride-along."""
    return touch(seat, CARRY_STAMP, session)


def claim_carry(seat: Seat | None, session: str) -> bool:
    """Claim SESSION's carry stamp by UNLINKING it. True only for
    the run whose unlink removed it: of two runs, the loser gets ENOENT and
    False, as does a session with no stamp, or none it can look for. Never
    raises. PostToolBatch calls this too, to drop a stale stamp."""
    try:
        (stamp_dir(seat, session) / CARRY_STAMP).unlink()
    except (OSError, NoIdentity):
        return False
    return True




#: changes/stopped-means-idle: idle is Claude Code's OWN verdict, read
#: through `session get idle` - 0 while its status is busy, else seconds
#: since the status changed. No hook, no stamp. The threshold is OURS: not
#: busy for this many seconds is idle. Was 60 (to match the harness's
#: idle_prompt); now ONE, because a longer wait adds latency to every wake,
#: worst of all for voice (changes/idle-one-second).
IDLE_SECONDS_VAR = "SESSIONMAIL_IDLE_SECONDS"
DEFAULT_IDLE_SECONDS = 1.0
#: The verdict is cached per session for this long. Was 5 because
#: `session get idle` is a subprocess (0.37 s wall, measured) and ten
#: listeners polling it every second would be a core;
#: now the sidecar is read directly (microseconds) and the poll is ONE
#: second, the same word as the threshold.
IDLE_POLL_SECONDS = 1.0
_idle_cache: dict[str, tuple[float, float | None]] = {}

#: Where Claude Code writes its per-session sidecars, and the verdict rule
#: for reading them, now live in ONE place: the `sessiontools` library
#: (a library dependency, no command on the PATH). This was one of three
#: private copies
#: of that reader; taskmaster's and `session get idle`'s are the others.
#: Same directory rule (SESSION_SIDECAR_DIR, else ~/.claude/sessions),
#: same verdict: busy -> 0, anything else -> seconds since updatedAt, no
#: sidecar -> None: Claude's own status file, read through the library.
SIDECAR_DIR_ENV = sessiontools.SIDECAR_DIR_ENV
sidecar_dir = sessiontools.sidecar_dir


#: THE THREE STATES (they replace stopped-means-idle). sessiontools (a
#: release with `Session.state`) says `busy`, `idle with children` or `idle`
#: in `Session.state`: a seat whose turn ended while a background agent runs
#: keeps a busy sidecar, and the Stop hook's turn-ended mark says the turn
#: is over. Two rules, one word (openspec/changes/idle-with-children):
#:
#: - WAKE: `idle with children` is IDLE, idle since the turn end. The library
#:   already returns that number, so `idle_seconds` and `is_idle` need no
#:   new rule; the word only names it (banner, page, `pager why`).
#: - KILL: nothing that ends, stops or reaps a seat may treat it as idle.
#:   No path here does today (each reads pid liveness or an explicit ask);
#:   a future one reads `seat_state` and treats IDLE_WITH_CHILDREN as BUSY.
#:
#: DEFENSIVE: an older sessiontools has no `state` and
#: no constant. The constant falls back to its literal, and `seat_state`
#: falls back to today's two-state reading of `busy`, so nothing changes.
BUSY = "busy"
IDLE = "idle"
IDLE_WITH_CHILDREN = getattr(sessiontools, "IDLE_WITH_CHILDREN", "idle with children")
STATES = (BUSY, IDLE_WITH_CHILDREN, IDLE)
#: The state each `idle_seconds` read saw, beside the number (same clock).
_state_cache: dict[str, tuple[float, str | None]] = {}


def _state_of(found: object) -> str:
    """FOUND's state in our words: its own `state` when the library has one
    we know, else today's reading of `busy` (an older sessiontools)."""
    state = getattr(found, "state", None)
    if state in STATES:
        return state
    return BUSY if getattr(found, "busy", True) else IDLE


def _sidecar_idle(session: str, now: float) -> float | None:
    """Claude Code's own verdict, through sessiontools. None when no
    sidecar names SESSION (then `session get idle` is asked - one door,
    two readers of the same file, same rule). The number is exactly
    `sessiontools.idle_seconds(session, now)`'s (find, then the Session's
    own `idle_seconds`), read once so the state comes with it."""
    found = sessiontools.find(session)
    if found is None:
        return None
    _state_cache[session] = (now, _state_of(found))
    return found.idle_seconds(now)


def sidecar_session(pid: int, env: dict[str, str] | None = None) -> str | None:
    """The session id Claude Code's sidecar for PID names NOW, or None.

    A `/clear` keeps the claude process and its
    sidecar (`<sidecar dir>/<pid>.json`) but CHANGES the `sessionId` in it
    (measured, claude 2.1.295). The same reader as the
    idle verdict (sessiontools' `_read`, one copy), aimed at one file: an
    unreadable or missing sidecar, bad JSON, or no `sessionId` is None -
    nothing can be said, so nothing is done on it."""
    found = sessiontools.claude_code._read(sidecar_dir(env) / f"{pid}.json")
    if found is None:
        return None
    return found.id.strip() or None


def idle_threshold(env: dict[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(IDLE_SECONDS_VAR) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_IDLE_SECONDS
    except ValueError:
        return DEFAULT_IDLE_SECONDS


def idle_seconds(session: str, now: float | None = None) -> float | None:
    """Seconds SESSION has not been busy, by `session get idle SESSION --json`
    (Claude Code's status and clock, one door). None when nothing can be
    said: no `session` on PATH, no such seat, a refusal, or a timeout - the
    pager then never pages at once and the ride-along carries, which is
    the ratified fallback. Cached IDLE_POLL_SECONDS per session."""
    import json
    import subprocess
    now = time.time() if now is None else now
    hit = _idle_cache.get(session)
    if hit is not None and now - hit[0] < IDLE_POLL_SECONDS:
        return hit[1]
    value: float | None = _sidecar_idle(session, now)
    if value is not None:
        _idle_cache[session] = (now, value)
        return value
    state: str | None = None
    try:
        res = subprocess.run(["session", "get", "idle", session, "--json"],
                             capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            answer = json.loads(res.stdout)
            value = float(answer["idle"])
            # A newer `session get idle --json` also says `state`; an older
            # one does not, and then the number alone says busy or idle.
            word = answer.get("state") if isinstance(answer, dict) else None
            state = word if word in STATES else (BUSY if value == 0 else IDLE)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        value, state = None, None
    _idle_cache[session] = (now, value)
    _state_cache[session] = (now, state)
    return value


def seat_state(session: str, now: float | None = None) -> str | None:
    """SESSION's state: BUSY, IDLE_WITH_CHILDREN or IDLE; None when nothing
    can be said. Read with the idle number (one read, same cache), so it
    never disagrees with the verdict it names. A KILL reader (anything that
    ends, stops or reaps a seat) SHALL treat IDLE_WITH_CHILDREN as BUSY."""
    now = time.time() if now is None else now
    hit = _state_cache.get(session)
    if hit is None or now - hit[0] >= IDLE_POLL_SECONDS:
        idle_seconds(session, now)
        hit = _state_cache.get(session)
    return hit[1] if hit is not None else None


def is_idle(session: str, now: float | None = None) -> bool | None:
    """True: not busy for at least the threshold. False: busy, or not for
    long enough. None: unknown."""
    secs = idle_seconds(session, now)
    if secs is None:
        return None
    return secs >= idle_threshold()




#: The ride-along knows what it has given, so it does not keep giving it
#: to the next one. The record of a giving is the `seen` mark, by THIS
#: session; "Read is not terminal" still stands - a message not marked done is
#: presented again to the next session, and to this one after an interval.
#: The interval is INFERRED (60 min), not specified; env overrides it.
REMIND_VAR = "SESSIONMAIL_REMIND_MINUTES"
DEFAULT_REMIND_MINUTES = 60


def remind_minutes(env: dict[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(REMIND_VAR) or "").strip()
    try:
        return float(raw) if raw else float(DEFAULT_REMIND_MINUTES)
    except ValueError:
        return float(DEFAULT_REMIND_MINUTES)


def given_since(events: list[Mark], session: str, since: datetime) -> set[str]:
    """Message-IDs this SESSION was given (a seen or read mark of its own)
    at or after SINCE. A mark from another session never counts; a mark
    whose stamp cannot be read is treated as older than any SINCE."""
    out: set[str] = set()
    for m in events:
        if m.session != session or m.mark not in ("seen", "read"):
            continue
        try:
            when = datetime.fromisoformat(m.stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if when >= since:
            out.add(m.message_id)
    return out


# ── the one writer ───────────────────────────────────────────────────────

def session_id(env: dict[str, str] | None = None) -> str:
    """This session's id for a marks line, or `-` when there is no session.

    specs/pager, "Read is not terminal", wants the session on every event so
    a seat can tell "another session read this" from "I read this". A role
    box kicked without a session has neither; `-` says so rather than
    inventing an id that would silently merge two readers.

    The chain itself is sessiontools' (first non-blank of SESSION_VARS,
    stripped; None when there is none) - this keeps only the `-`.
    """
    return sessiontools.session_id(env) or "-"


def write_mark(
    seat: Seat, message_id: str, mark: str, session: str, stamp: str,
    reason: str = "",
) -> str:
    """Append ONE marks line (MARK_LINE) and return it. The only writer.

    Append, never rewrite: specs/pager "Read is not terminal" records marks
    PER EVENT, so a second `read` is a second line, not an update. Nothing
    here ever touches the maildir or its Seen flag. REASON is free text and
    goes last so it may hold spaces; its whitespace is collapsed to single
    spaces, because a newline inside it would write a second line that
    parses as a mark nobody made.
    """
    if mark not in MARKS:
        raise ValueError(f"{mark!r} is not one of {', '.join(MARKS)}")
    event = Mark(stamp, message_id.strip().strip("<>"), mark, seat.name,
                 session or "-", " ".join(reason.split()))
    path = marks_path(seat, session)
    make_dir(path.parent, session)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.line + "\n")
    return event.line


# ── is the pager armed? one stamp, with a fallback for listeners born before it ──
#: A Stop hook that reliably tells the agent to arm the pager if it is not
#: armed, and a ride-along line that says the same: both need one fact -
#: is THIS session's `pager listen` alive - and until now nothing recorded
#: it. The listener writes its pid here at arm and removes it on exit; a
#: stale file (SIGKILL) is one whose pid is dead, and is cleared on sight.
#: FALLBACK: a listener started before this stamp existed leaves none, so
#: a stampless seat is checked by process ancestry (a `pager listen` whose
#: parent chain reaches this session's claude) - the same test the ride-
#: along's `arm` line has always used. Without it, every live listener on
#: the box would have been called deaf the moment this landed: the false
#: positive that leaves agents suspecting they are deaf when they are not.
LISTENING_STAMP = "listening"

#: Opt-out, one key, default ON: a seat has the pager by default, and can
#: opt out of it.
#: THE COALESCING WINDOW - "at most one wake" (specs/pager, "Counts SHALL
#: coalesce per address across a burst").
#:
#: A page already carries EVERY message waiting at that instant and marks
#: them all given, so a burst that has already landed costs one wake. The
#: storm is the burst that lands WHILE the call is going on: on a Delta Chat
#: call the bridge mails the seat EVERY CHUNK, and one box held fourteen in
#: five minutes (one of them "[COUGH]"). Each new chunk is a message nobody
#: has been given, so each re-arm would page again - fourteen wakes.
#:
#: So a wake for an ADDRESS silences further wakes FOR THAT ADDRESS for this
#: long - WHILE THE SESSION IS RUNNING. An idle session bypasses the window
#: (pager.wakeable, changes/idle-rings-through-burst): it has no desk line to
#: carry a held chunk, and a stopped agent is paged immediately.
#: Nothing is dropped and nothing is filtered: the later chunks stay in
#: the queue and the ride-along shows them as ONE grouped desk line. That is
#: the difference between this and a sender or subject filter, which would
#: DISCARD the class, while a window only moves it from the doorbell to the
#: desk.
#:
#: 300s is five minutes, the span the gate is stated over. It is a threshold,
#: reversible, and deliberately not a filter.
BURST_SECONDS_VAR = "SESSIONMAIL_BURST_SECONDS"
DEFAULT_BURST_SECONDS = 300.0
WOKE_PREFIX = "woke."


def burst_seconds(env: dict[str, str] | None = None) -> float:
    """The coalescing window. A value that is not a non-negative number is
    IGNORED rather than obeyed (a typo must not silently turn coalescing
    off); an explicit 0 turns it off, which is a value, not a typo."""
    env = os.environ if env is None else env
    raw = (env.get(BURST_SECONDS_VAR) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BURST_SECONDS
    return value if value >= 0 else DEFAULT_BURST_SECONDS


def woke_stamp(key: str) -> str:
    """The stamp file name for one address key. The key is a local part and
    may carry a `+extension`; anything that is not safe in a filename is
    replaced, so a hostile or odd address cannot escape the stamp dir."""
    safe = "".join(c if (c.isalnum() or c in "+._-") else "_" for c in (key or ""))
    return WOKE_PREFIX + (safe or "_")


def woke_at(seat: Seat | None, session: str, key: str) -> float | None:
    """When this session last WOKE for KEY, or None."""
    try:
        path = stamp_dir(seat, session) / woke_stamp(key)
        return path.stat().st_mtime
    except (OSError, NoIdentity):
        return None


def in_burst(seat: Seat | None, session: str, key: str,
             window: float, now: float | None = None) -> bool:
    """True when a wake for KEY already happened inside the window, so this
    address must ride the desk line instead of ringing the doorbell."""
    if window <= 0:
        return False
    last = woke_at(seat, session, key)
    if last is None:
        return False
    return ((time.time() if now is None else now) - last) < window


def write_woke(seat: Seat, session: str, key: str) -> Path:
    """Record that this session woke for KEY, now."""
    d = make_dir(stamp_dir(seat, session), session)
    path = d / woke_stamp(key)
    path.touch()
    os.utime(path, None)
    return path


PAGER_VAR = "SESSIONMAIL_PAGER"


def pager_enabled(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(PAGER_VAR) or "on").strip().lower() not in ("off", "0", "no", "false")


#: The SEAT's switch for the pager monitor,
#: read from claude's launch environment. It can only turn the monitor OFF:
#: no value of it turns the monitor on over the host's `"monitor": false`.
#: Any value other than `off` is ignored, and `pager status` says so.
MONITOR_VAR = "SESSIONMAIL_MONITOR"


@dataclass(frozen=True)
class MonitorSwitch:
    """Whether this seat's pager monitor may run, and what decided it.

    `source` names the decider in words a person can act on: the variable
    and its value, or the host file's key and path, or the default.
    `ignored` holds each setting that was there and decided nothing."""

    on: bool
    source: str
    ignored: tuple[str, ...] = ()


def monitor_switch(env: dict[str, str] | None = None) -> MonitorSwitch:
    """Off only. Off wins, in this order of naming:
    `SESSIONMAIL_PAGER=off` (the whole pager is off, the monitor included),
    then `SESSIONMAIL_MONITOR=off` (the seat), then `"monitor": false` in the
    host file. A missing setting everywhere means ON."""
    from . import _settings as host_settings
    env = os.environ if env is None else env
    ignored: list[str] = []
    raw = (env.get(MONITOR_VAR) or "").strip()
    seat_off = raw.lower() == "off"
    if raw and not seat_off:
        ignored.append(f"{MONITOR_VAR}={raw} is ignored: only `off` changes it, and only "
                       "to off")
    host = host_settings.monitor_host(env)
    if host.ignored:
        ignored.append(host.ignored)
    host_file = host_settings.path(env)
    if not pager_enabled(env):
        return MonitorSwitch(False, f"{PAGER_VAR}={(env.get(PAGER_VAR) or '').strip()}",
                             tuple(ignored))
    if seat_off:
        return MonitorSwitch(False, f"{MONITOR_VAR}=off", tuple(ignored))
    if host.off:
        return MonitorSwitch(False, f'"{host_settings.MONITOR_KEY}": false in {host_file}',
                             tuple(ignored))
    if host.set_on:
        return MonitorSwitch(True, f'"{host_settings.MONITOR_KEY}": true in {host_file}',
                             tuple(ignored))
    return MonitorSwitch(True, "the default: no switch is set", tuple(ignored))


# ── pager_required and the deaf check ────────────────────────────────────
#: The deaf check always runs, but it says nothing to a seat unless that
#: seat is pager_required.
#: The key is read through the layered reader (host, seat, session); the
#: variable wins over every layer in BOTH directions - it only decides
#: whether a warning is spoken, so a launcher may turn it on or off.
REQUIRED_KEY = "pager_required"
REQUIRED_VAR = "SESSIONMAIL_PAGER_REQUIRED"
_REQUIRED_ON = ("1", "true", "on")
_REQUIRED_OFF = ("0", "false", "off")


@dataclass(frozen=True)
class Required:
    """Whether this seat's pager is REQUIRED to be listening (the deaf check
    speaks only then), and what decided it. `ignored` holds each setting
    that was there and decided nothing, in words a person can act on."""

    on: bool
    source: str
    ignored: tuple[str, ...] = ()


def _required_layers(seat: "Seat | None", session: str,
                     env: dict[str, str] | None) -> list[tuple[str, Path]]:
    """The layers `pager_required` is read from, lowest first: the host's
    `_settings.DIR`, the seat's settings dir, the session's. A seat
    named for an agent (no `+`, no `.`) has no seat layer yet (`_layer`)."""
    from . import _layer
    from . import _settings as host_settings
    out: list[tuple[str, Path]] = [("host", host_settings.path(env).parent)]
    if seat is not None and seat.address and not _layer.is_agent_name(seat.address):
        out.append(("seat", _layer.settings_dir(seat.address, env)))
    if session and session != "-":
        out.append(("session", _layer.session_settings_dir(session, env)))
    return out


def pager_required(seat: "Seat | None", session: str,
                   env: dict[str, str] | None = None) -> Required:
    """`SESSIONMAIL_PAGER_REQUIRED` (1/true/on, 0/false/off)
    wins over every layer, both ways; any other value is ignored and said.
    Otherwise the most specific layer that holds a boolean `pager_required`
    decides (session over seat over host), each read through `_layer.read`
    (`mail.json`, then `mail.d/*.json`). A layer that cannot be read is
    skipped and said. When the layered reader itself is missing (no
    sessiontools.settings), the host file alone is read and that is said.
    Missing everywhere means NOT required. Never raises."""
    from . import _layer
    from . import _settings as host_settings
    env = os.environ if env is None else env
    ignored: list[str] = []
    raw = (env.get(REQUIRED_VAR) or "").strip()
    if raw.lower() in _REQUIRED_ON:
        return Required(True, f"{REQUIRED_VAR}={raw}")
    if raw.lower() in _REQUIRED_OFF:
        return Required(False, f"{REQUIRED_VAR}={raw}")
    if raw:
        ignored.append(f"{REQUIRED_VAR}={raw} is ignored: only 1/true/on or 0/false/off "
                       "decide it")
    found: Required | None = None
    reader_missing = False
    try:
        layers = _required_layers(seat, session, dict(env))
    except _layer.LayerError as exc:
        layers = []
        if _layer.NOT_INSTALLED in str(exc):
            reader_missing = True
        else:
            ignored.append(f"the seat and session layers could not be found ({exc})")
    for name, directory in layers:
        try:
            values, origins = _layer.read(directory, name)
        except _layer.LayerError as exc:
            if _layer.NOT_INSTALLED in str(exc):
                reader_missing = True
                break
            ignored.append(f"the {name} layer is skipped: {exc}")
            continue
        if REQUIRED_KEY not in values:
            continue
        value = values[REQUIRED_KEY]
        where = origins.get(REQUIRED_KEY) or str(directory / _layer.FILE)
        if isinstance(value, bool):
            found = Required(value, f'"{REQUIRED_KEY}": {json.dumps(value)} in {where}')
        else:
            ignored.append(f'"{REQUIRED_KEY}": {json.dumps(value)} in {where} is ignored: '
                           "only a boolean decides it")
    if reader_missing:
        # The fallback: host file + env, and SAY SO.
        ignored.append("the layered reader (sessiontools.settings) is not installed, so only "
                       "the host file was read")
        present, value, why = host_settings.host_value(REQUIRED_KEY, env)
        if why:
            ignored.append(why)
        elif present and isinstance(value, bool):
            found = Required(value, f'"{REQUIRED_KEY}": {json.dumps(value)} in '
                                    f"{host_settings.path(env)}")
        elif present:
            ignored.append(f'"{REQUIRED_KEY}": {json.dumps(value)} in '
                           f"{host_settings.path(env)} is ignored: only a boolean decides it")
    if found is not None:
        return Required(found.on, found.source, tuple(ignored))
    return Required(False, "the default: no setting says required", tuple(ignored))


#: Touched by `pager listen --monitor` when it arms, beside
#: `listening`, and never cleared. A monitor that is gone while this stamp
#: stands was ENDED (`pager stop --kill`, or it died); with no stamp it never
#: armed (the gate). Claude Code does not restart a monitor, so either way
#: only a hand arm or a restart brings the pager back.
MONITOR_ARMED_STAMP = "monitor-armed"


def monitor_was_armed(seat: "Seat | None", session: str) -> bool:
    """Did a pager monitor ever arm for SESSION (its `monitor-armed` stamp)?
    The deaf check reads it to tell a monitor that was ENDED from one that
    never armed - and from a mute, which leaves the process running."""
    try:
        return (stamp_dir(seat, session) / MONITOR_ARMED_STAMP).exists()
    except NoIdentity:
        return False

#: The deaf check's grace: a claude younger than this is starting up, and
#: its monitor may not have armed yet (the listener's `not_yet` idea,
#: carried past claude's exec).
GRACE_SECONDS = 10.0

#: The plugin's root, as Claude Code gives it to a plugin's hooks.
PLUGIN_ROOT_VAR = "CLAUDE_PLUGIN_ROOT"


def plugin_monitors(env: dict[str, str] | None = None) -> bool | None:
    """Does the plugin that runs this hook carry a monitor: a non-empty list
    in `$CLAUDE_PLUGIN_ROOT/monitors/monitors.json`?
    None when no plugin root is known (a run from a plain shell): then
    nothing can be said, and no monitor is EXPECTED."""
    env = os.environ if env is None else env
    root = (env.get(PLUGIN_ROOT_VAR) or "").strip()
    if not root:
        return None
    try:
        data = json.loads((Path(root) / "monitors" / "monitors.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, list) and len(data) > 0


def headless(command: str) -> bool:
    """A claude command line run with `-p`/`--print` (F2: `!isInteractive()`
    never arms a monitor). Read from `ps`, whose argv is joined by spaces: a
    prompt that holds a bare ` -p ` reads as headless - the safe side, as it
    only gives the plain not-listening line instead of naming the monitor."""
    for token in command.split():
        if token in ("-p", "--print") or token.startswith("--print="):
            return True
    return False


@dataclass(frozen=True)
class MonitorExpected:
    """Whether this session SHOULD have a monitor, and,
    when not, why not (for the DEAF line)."""

    expected: bool
    why: str = ""


def monitor_expected(session: str, ps=None, env: dict[str, str] | None = None,
                     claude: int | None = None) -> MonitorExpected:
    """True when all three hold: the switch is on (`monitor_switch`), the
    plugin carries `monitors/monitors.json` (`plugin_monitors`), and this
    session's claude is interactive (no `-p`/`--print` on its argv). CLAUDE
    is this session's claude pid when the caller already has it."""
    switch = monitor_switch(env)
    if not switch.on:
        return MonitorExpected(False, f"the pager monitor is switched off ({switch.source})")
    if not plugin_monitors(env):
        return MonitorExpected(False, "this plugin runs no pager monitor")
    pid = claude_for(session, ps=ps) if claude is None else claude
    if pid is None:
        return MonitorExpected(False, "claude is not running for this session")
    _ppid, cmd = (ps or _ps_table)()
    command = cmd.get(pid)
    if command is None:
        command = _ps_one(pid)[1].get(pid)
    if command is None:
        return MonitorExpected(False, "`ps` cannot say how claude was launched")
    if headless(command):
        return MonitorExpected(False, "this is a headless seat (`claude -p`), which runs "
                                      "no monitor")
    return MonitorExpected(True)


def claude_age(pid: int) -> float | None:
    """Seconds since PID started, from `ps -o etime=` ([[dd-]hh:]mm:ss), or
    None when `ps` cannot say. The deaf check's grace reads it."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "etime="],
                             capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return etime_seconds(out)


def etime_seconds(text: str) -> float | None:
    """`ps`'s etime ([[dd-]hh:]mm:ss) in seconds, or None when it is not one."""
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text.strip())
    if not m:
        return None
    days, hours, mins, secs = (int(g) if g else 0 for g in m.groups())
    return float(((days * 24 + hours) * 60 + mins) * 60 + secs)


def amber_sentinel(env: dict[str, str] | None = None) -> str:
    """Claude Code's cached `tengu_amber_sentinel` gate, as `true`,
    `false` or `unknown`. Read only to NAME the cause in a DEAF line, never
    to decide (the cache can be stale). `~/.claude.json`, or
    `$CLAUDE_CONFIG_DIR/.claude.json` when that is set."""
    env = os.environ if env is None else env
    base = (env.get("CLAUDE_CONFIG_DIR") or "").strip() or (env.get("HOME") or "").strip()
    if not base:
        return "unknown"
    try:
        data = json.loads((Path(base) / ".claude.json").read_text(encoding="utf-8"))
        value = data["cachedGrowthBookFeatures"]["tengu_amber_sentinel"]
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return "unknown"
    return json.dumps(value) if isinstance(value, bool) else "unknown"


def write_listening(seat: Seat, session: str, pid: int) -> Path:
    """Stamp SESSION armed with PID. Arming ends a deliberate stop, so the
    `off` stamp goes first: a seat that listens again is one the
    hooks may nag again once that listener is gone.

    Whole or not at all: a temp file in the stamps dir, then
    `os.replace`, so a reader sees no stamp or a whole pid, never ''. It was
    touch() then write_text(), and a reader between them read '' - as did
    `clear_listening`, which then left the empty stamp for good. make_dir is
    touch()'s own first step (the private root); an OSError still raises."""
    clear_off(seat, session)
    d = make_dir(stamp_dir(seat, session), session)
    path = d / LISTENING_STAMP
    tmp = d / f".{LISTENING_STAMP}.{os.getpid()}"
    try:
        tmp.write_text(f"{pid}\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


# ── a seat that stopped on purpose ──────────────────────────────────────
#: Measured: of 57 stops the Stop hooks refused across 54 seats, 53 said
#: "re-arm" - among them to settled seats that had just run `pager stop` by
#: the book, so a Stop hook telling them to re-arm wasted their turns.
#: sessionmail reads no role to decide who is nagged
#: (openspec/changes/pager-settings-file D8): the settings file
#: decides whether the hooks speak, and this stamp says a stop was meant.

#: `pager stop` / `pager off` leaves this beside the listening stamp: the
#: listener was stopped ON PURPOSE, so the seat is settled, not deaf. The
#: Stop hook is silent while it stands and the ride-along says the pager is
#: off, not "re-arm" (a seat that stood down by the book was once told to
#: re-arm on its final turn). `pager listen` clears it when it arms (write_listening). A
#: listener that DIED or TIMED OUT leaves no such stamp and is nagged as
#: before.
OFF_STAMP = "off"


def write_off(seat: Seat, session: str) -> Path:
    return touch(seat, OFF_STAMP, session)


def clear_off(seat: Seat, session: str) -> bool:
    """Remove the `off` stamp; True when there was one to remove (`pager
    unmute` and the calm `pager listen` say so only then)."""
    try:
        (stamp_dir(seat, session) / OFF_STAMP).unlink()
    except (OSError, NoIdentity):
        return False
    return True


def stopped_on_purpose(seat: Seat, session: str) -> bool:
    try:
        return (stamp_dir(seat, session) / OFF_STAMP).exists()
    except NoIdentity:
        return False


# ── what the ride-along last said ────────────────────────────────────────
#: A state line should be said once, and again only when it changes: the
#: listening line was measured in 10,724 batches across 151 seats, 176 of
#: 177 batches in one. The ride-along keeps the TEXT of
#: the last state line (or a settings fault's line) it gave this session
#: here, beside `off` and `ride-along`, and gives the line again only when
#: it differs (openspec/changes/pager-settings-file). Text,
#: not a state name: a new fault reason, or a reworded arm.txt after an
#: upgrade, is a change too.
#:
#: Neither function raises. A record that cannot be read is None and one
#: that cannot be written is let go: the line is said again, and a repeat
#: is the safe failure. A session of `-` keeps no record at all, because
#: stamp_dir gives `-` a bucket the whole seat shares; such a run says its
#: line every time, as it did before.
SAID_STAMP = "said"

#: The settings drift line's own record: said once, beside the state line, which keeps
#: `SAID_STAMP`. One record each, so neither re-says the other.
SAID_DRIFT_STAMP = "said-drift"

#: The copied-settings line's own record (both settings folders real): said
#: once per session, on or off, the way the drift line is.
SAID_COPIED_STAMP = "said-copied"

#: The deaf check's own record with the hooks OFF, where its
#: DEAF line is the only thing said. Holds "" once a DEAF
#: has cleared, so the next one is said again.
SAID_DEAF_STAMP = "said-deaf"


def read_said(seat: Seat | None, session: str, name: str = SAID_STAMP) -> str | None:
    """The last line the ride-along gave SESSION, or None (nothing yet,
    session `-`, or the record could not be read)."""
    if not session or session == "-":
        return None
    try:
        return (stamp_dir(seat, session) / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, NoIdentity):
        return None


def write_said(seat: Seat | None, session: str, line: str,
               name: str = SAID_STAMP) -> None:
    """Record LINE as the last thing the ride-along gave SESSION. Whole or
    not at all (temp file + rename), so a torn write cannot equal a line."""
    if not session or session == "-":
        return
    try:
        d = make_dir(stamp_dir(seat, session), session)
        tmp = d / f".{name}.{os.getpid()}"
        try:
            tmp.write_text(line, encoding="utf-8")
            os.replace(tmp, d / name)
        finally:
            tmp.unlink(missing_ok=True)
    except (OSError, NoIdentity):
        pass


def clear_listening(seat: Seat, session: str, pid: int) -> None:
    """Remove the stamp only if it is still ours (a successor may have
    written its own pid over it)."""
    path = stamp_dir(seat, session) / LISTENING_STAMP
    try:
        if path.read_text().strip() == str(pid):
            path.unlink()
    except (OSError, ValueError):
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ps_table() -> tuple[dict[int, int], dict[int, str]]:
    """pid -> ppid and pid -> command, from one `ps`."""
    return _ps("-ax")


def _ps_one(pid: int) -> tuple[dict[int, int], dict[int, str]]:
    """`_ps_table` for PID alone (`ps -p`: ~4 ms, not ~60). The batch hook
    asks it whether a live listener is the monitor on every batch."""
    return _ps("-p", str(pid))


def _ps(*which: str) -> tuple[dict[int, int], dict[int, str]]:
    import subprocess
    ppid: dict[int, int] = {}
    cmd: dict[int, str] = {}
    try:
        out = subprocess.run(["ps", *which, "-o", "pid=,ppid=,command="],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ppid, cmd
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if m:
            ppid[int(m.group(1))] = int(m.group(2))
            cmd[int(m.group(1))] = m.group(3)
    return ppid, cmd


#: A claude process, known by its BINARY: argv[0]'s basename is
#: `claude` (on PATH, or any `/dir/claude`), or a `node` runs claude's npm
#: `claude-code/cli.js`. Never by a path that merely CONTAINS "claude": a
#: pytest under a Claude Code scratchpad (`/private/tmp/claude-502/...`)
#: or a `pager listen` under `~/.claude/plugins/` is not a claude (both
#: matched the old `\S*claude\b`). Applied with `.match`, to the whole line.
_CLAUDE = re.compile(
    r"(?:\S*/)?claude(?=\s|$)"
    r"|(?:\S*/)?node(?:\s+-\S*)*\s+\S*/claude-code/cli\.js(?=\s|$)")
_PAGER_LISTEN = re.compile(r"(?:\S+\s+)?\S*/?pager\s+listen\b")
#: The id a claude was launched with, in `--session-id X` or `--session-id=X`
#: form, read as one whole token.
_SESSION_ARG = re.compile(r"(?:^|\s)--session-id(?:=|\s+)(\S+)")


def _claude_session(command: str) -> str | None:
    """The `--session-id` a claude command line carries, or None."""
    m = _SESSION_ARG.search(command)
    return m.group(1) if m else None


#: A pager-monitor plugin's listener: `pager listen --monitor`,
#: the argv the plugin's `exec` gives it and `follow_session`'s `execv` keeps.
_PAGER_MONITOR = re.compile(r"(?:\S+\s+)?\S*/?pager\s+listen\b.*\s--monitor\b")


def _session_claudes(session: str, ppid: dict[int, int], cmd: dict[int, str],
                     me: int) -> set[int]:
    """THIS session's claude process(es) in a `ps` table: every claude with
    `--session-id SESSION` on its command line, else the NEAREST claude
    walking up from ME - and that one counts only when it carries no
    `--session-id` (an interactive `claude`, `--resume`, `--continue`) or
    SESSION itself. A nearest claude launched with any OTHER id is another
    session's: empty, and no further claude is tried (a generator for
    session B run under a live claude for session A). Empty
    when nothing is found. One copy, shared by `listening_by_ancestry` and
    `claude_for`, and the id is read only by `_claude_session`."""
    mine = {p for p, c in cmd.items()
            if session and _CLAUDE.match(c) and _claude_session(c) == session}
    if not mine:
        p = me
        while p in ppid and p > 1:
            if _CLAUDE.match(cmd.get(p, "")):
                other = _claude_session(cmd[p])
                if other is not None and other != session:
                    return set()
                return {p}
            p = ppid[p]
    return mine


def _live_sidecars() -> list:
    """The sessions whose claude is running now, by their sidecars
    (sessiontools, the one driver: pid AND procStart, so a reused pid is not
    a session). Honours SESSION_SIDECAR_DIR. The seam `claude_for` takes."""
    return sessiontools.claude_code.live()


def claude_for(session: str, ps=None, me: int | None = None, live=None) -> int | None:
    """The pid of a claude running for SESSION now, or None.

    None means claude has not started for this session (a `session start`
    context generator runs before claude is exec'd), so nothing CAN be
    listening yet: that is not deafness. It never says whether a monitor
    plugin will run - nothing before claude can know that.

    Three sources, cheapest first: (1, 2) the `ps` table - a claude with
    `--session-id SESSION` on its argv, else the nearest claude ancestor of
    ME unless it carries another session's id, the walk
    `listening_by_ancestry` does (`_session_claudes`); (3) only when those found nothing, a
    LIVE sidecar naming SESSION, which covers `claude --resume`, a hand
    launch, and a check from a plain terminal against a live session. PS
    and LIVE are injectable (tests); ME defaults to this process."""
    ppid, cmd = (ps or _ps_table)()
    me = os.getpid() if me is None else me
    found = _session_claudes(session, ppid, cmd, me)
    if found:
        return min(found)
    if not session or session == "-":
        return None
    for s in (live or _live_sidecars)():
        if s.id == session and s.pid:
            return s.pid
    return None


def _nearest_claude(pid: int, ppid: dict[int, int], cmd: dict[int, str]) -> int | None:
    """The NEAREST claude ancestor of PID in a `ps` table, or None: walking
    up from PID's parent (PID itself is a `pager listen`, never its own
    claude) at most 40 hops, the first pid whose command is a claude's.
    Leg (a): the one claude a listener belongs to, so a claude
    further up (one that launched a nested, headless claude) never owns it. Shared
    with `_reach._owner_by_ancestry`, which labels a listener by it."""
    q, hops = ppid.get(pid), 0
    while q is not None and hops < 40:
        if _CLAUDE.match(cmd.get(q, "")):
            return q
        q = ppid.get(q)
        hops += 1
    return None


def _stamped_elsewhere(session: str, env: dict[str, str] | None = None) -> dict[int, str]:
    """Live pids named by ANOTHER session's `listening` stamp, each mapped to
    that session's id (leg (b)). A listener another session has
    stamped is that session's, whatever its ancestry: never SESSION's.

    Read: every `<session dir>/mail/stamps/listening` (the sessions root is
    sessiontools', found as SESSION's dir's parent), and the older
    `<home>/sessions/<sid>/stamps/listening` a writer on old code may still
    keep. Only a pid that is alive counts, as `_reach.listening_pid` reads
    one session's stamp; nothing is cleared (read-only). A root that cannot
    be found adds nothing."""
    found: dict[int, str] = {}
    globs: list[tuple[Path, str]] = []
    try:
        probe = _settings().session_dir(session or "-", env)
        globs.append((probe.parent, f"*/{MAIL_DIR}/{STAMPS_DIR}/{LISTENING_STAMP}"))
    except Exception:  # noqa: BLE001 - no sessiontools root: nothing to read there
        pass
    try:
        globs.append((home(env) / SESSIONS_DIR, f"*/{STAMPS_DIR}/{LISTENING_STAMP}"))
    except Exception:  # noqa: BLE001 - no seat home: nothing to read there
        pass
    for root, pattern in globs:
        try:
            paths = list(root.glob(pattern))
        except OSError:
            continue
        for path in paths:
            sid = path.relative_to(root).parts[0]
            if sid == session:
                continue
            try:
                pid = int(path.read_text().strip())
            except (OSError, ValueError):
                continue
            if pid not in found and _pid_alive(pid):
                found[pid] = sid
    return found


def listening_by_ancestry(session: str, ppid: dict[int, int], cmd: dict[int, str],
                          me: int, exclude: frozenset[int] = frozenset()) -> int | None:
    """The pid of a stampless-for-SESSION `pager listen` that is this
    session's by its parent chain, or None.

    This session's claude is found by `--session-id SESSION` on its command
    line, else by walking up from ME to the NEAREST claude. That claude
    counts only when it carries no `--session-id` (an interactive `claude`,
    `--resume`, `--continue`) or carries SESSION itself; a nearest claude
    launched with any OTHER id is another session's, so the answer is None
    and no further claude is tried (else a test child under a live seat
    took the seat's listener as its own).

    A candidate listener is then this session's only on BOTH legs:
    (a) its OWN nearest claude ancestor is this session's claude - a claude
    anywhere higher up does not count, so a seat is never credited the
    listener of a claude nested under it; and (b) no other session's live `listening`
    stamp names it (`_stamped_elsewhere`) - so a probe under an id-less
    claude, asking for a made-up sid, is never credited a listener the real
    session stamped. A pid in EXCLUDE is never the answer (`other_listener`
    passes the asking listener and its own ancestors, so it cannot find
    itself)."""
    mine = _session_claudes(session, ppid, cmd, me)
    if not mine:
        return None
    foreign: dict[int, str] | None = None
    for p, c in cmd.items():
        if p in exclude or not _PAGER_LISTEN.match(c):
            continue
        if _nearest_claude(p, ppid, cmd) not in mine:
            continue
        if foreign is None:
            foreign = _stamped_elsewhere(session)
        if p in foreign:
            continue
        return p
    return None


def is_listening(seat: Seat, session: str, ps=None) -> int | None:
    """The pid of this session's live `pager listen`, or None. Stamp first;
    a stale stamp is cleared; no stamp falls back to ancestry, on the two
    legs `listening_by_ancestry` checks. PS is the table source,
    injectable for tests."""
    path = stamp_dir(seat, session) / LISTENING_STAMP
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None:
        if _pid_alive(pid):
            return pid
        try:
            path.unlink()
        except OSError:
            pass
    ppid, cmd = (ps or _ps_table)()
    return listening_by_ancestry(session, ppid, cmd, os.getpid())


def other_listener(seat: Seat | None, session: str, ps=None) -> int | None:
    """pager-genie 3.8 (D10, O17): the pid of ANOTHER live `pager
    listen` for SESSION, or None - found as the NOT ARMED check finds one
    (`is_listening`: the `listening` stamp, else process ancestry), but
    never this process or one of its ancestors, so a listener asking at its
    own start does not find itself (a `pager` shim that runs the Python
    listener as a child has the same command line).

    READ-ONLY, unlike `is_listening`: a stale stamp is left where it is (the
    listener that goes on to arm overwrites it), because a refusal SHALL
    change no stamp. A stamp whose pid is alive but, by `ps`, is no longer
    a `pager listen` (the pid was reused after a SIGKILL) is not a
    listener: refusing on it would leave the session unable to arm at all,
    deaf by a stranger's pid. With no `ps` answer the stamp is believed.
    Another session's listener is never found: the stamp is per session,
    and the ancestry walk counts a claude only when it carries THIS
    session's `--session-id` or none at all; the nearest claude launched
    with another id ends the walk with no answer. A candidate on
    the walk counts only when its OWN nearest claude is this session's and
    no other session's stamp names it (`listening_by_ancestry`)."""
    me = os.getpid()
    ppid, cmd = (ps or _ps_table)()
    mine = {me, os.getppid()}
    p = me
    while p in ppid and p > 1 and len(mine) < 64:
        p = ppid[p]
        mine.add(p)
    try:
        pid = int((stamp_dir(seat, session) / LISTENING_STAMP).read_text().strip())
    except (OSError, ValueError, NoIdentity):
        pid = None
    if (pid is not None and pid not in mine and _pid_alive(pid)
            and (pid not in cmd or _PAGER_LISTEN.match(cmd[pid]))):
        return pid
    return listening_by_ancestry(session, ppid, cmd, me, frozenset(mine))


def is_monitor(pid: int, ps=None) -> bool | None:
    """Whether PID is a pager-monitor plugin's listener (`pager listen
    --monitor`), by its command line in `ps`; None when `ps` does not know
    PID (then nothing can be said). A monitor that is told
    to stop is gone for good, because Claude Code does not restart one."""
    _ppid, cmd = (ps or _ps_table)()
    if pid not in cmd:
        return None
    return bool(_PAGER_MONITOR.match(cmd[pid]))


# ── the ride-along queue: links in, a rename per entry out ───────────────
#: openspec/changes/the-queue-chain, wiring D07/D08/D09: the detector hardlinks each arrival into the
#: session's own queue; the scoop CLAIMS by renaming each entry out of new/
#: into its taking directory (exactly once; new/ is never removed -
#: PROPOSED in openspec/changes/the-scoop-keeps-new, amending D08's whole-
#: directory swap, whose window killed listeners); a leftover `taking.*` is the
#: recovery record; `done/` keeps the link as the exactly-once memory. The
#: queue lives beside the session's stamps, OUTSIDE any maildir notmuch
#: indexes, so a link, a swap or a move here never bumps a message's
#: lastmod (F18's mechanism cannot recur). Per session, not per seat: the
#: scoop is per session (its marks and stamps are), so two sessions on one
#: seat cannot scoop each other's queue (D08 point 4, one queue per consumer).
RIDE_DIR = "ride"
QUEUE_NEW = "new"
QUEUE_DONE = "done"
TAKING_PREFIX = "taking."


def ride_dir(seat: Seat | None, session: str) -> Path:
    """This session's queue: `<home>/sessions/<session>/ride/` (new/,
    taking.*, done/). Keyed on the session id; a
    run with no session id at all keeps the seat's `-` queue."""
    root = session_root(session)
    if root is not None:
        return root / RIDE_DIR
    if seat is None:
        raise NoIdentity()
    return seat.root / RIDE_DIR / "-"


def queue_new(seat: Seat, session: str) -> Path:
    """The directory the detector links into. Created on demand so a
    listener can link before the first scoop and a scoop can swap before
    the first link (either order is fine; a missing new/ is an empty one)."""
    return make_dir(ride_dir(seat, session) / QUEUE_NEW, session)


@dataclass(frozen=True)
class QueueEntry:
    """One link taken from the queue. RECOVERED names the leftover taking
    directory it came out of, or "" when it came from this scoop's own swap."""
    name: str
    path: Path
    recovered: str = ""


@dataclass(frozen=True)
class Taken:
    """What one scoop claimed: the directories it now owns and their entries,
    oldest first. `finish` moves every entry to done/ and removes them."""
    dirs: tuple[Path, ...]
    entries: tuple[QueueEntry, ...]


def _taking_pid(path: Path) -> int | None:
    tail = path.name.rsplit(".", 1)[-1]
    return int(tail) if tail.isdigit() else None


def leftovers(seat: Seat, session: str) -> list[Path]:
    """Taking directories whose scoop is gone (its pid no longer alive). A
    live one is another scoop of this session mid-swap - left alone."""
    base = ride_dir(seat, session)
    if not base.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(base.iterdir()):
        if not p.is_dir() or not p.name.startswith(TAKING_PREFIX):
            continue
        pid = _taking_pid(p)
        if pid is not None and pid != os.getpid() and _pid_alive(pid):
            continue
        out.append(p)
    return out


def _entries(directory: Path, recovered: str = "") -> list[QueueEntry]:
    found = []
    for p in directory.iterdir():
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue                  # taken by a concurrent scoop between the listing and the stat
        if p.is_file():
            found.append((mtime, QueueEntry(p.name, p, recovered)))
    # Oldest first by the inode's mtime - a hardlink carries DELIVERY time.
    found.sort(key=lambda t: (t[0], t[1].name))
    return [e for _, e in found]


def take_queue(seat: Seat, session: str, pid: int) -> Taken:
    """The scoop's claim: leftovers first (recovered, named), then every
    entry listed in new/ renamed into `taking.<session>.<pid>/`, one
    `rename(2)` per entry. Nothing is read from the index and nothing is
    claimed from any maildir. Returns what is now this scoop's to give;
    nothing has been given or moved yet.

    A PROPOSED amendment of D08 (openspec/changes/the-scoop-keeps-new):
    new/ itself is NEVER renamed or removed. The claim used to be `mv new
    taking.* && mkdir new`, two syscalls, O(1) in depth. Between those two
    syscalls new/ did not exist, and the listener in another process died
    of it five times: at its banner, its watch loop and its link. The
    ratified scenario "a link landing during the scoop is in the new new/,
    not lost" is what that window broke. A per-entry rename is still
    exactly once: each rename is atomic, and the loser of a race on one
    entry gets ENOENT and skips it. The cost is O(depth), and the depth is
    what one tool call's scoop takes."""
    base = ride_dir(seat, session)
    dirs: list[Path] = []
    entries: list[QueueEntry] = []
    for left in leftovers(seat, session):
        dirs.append(left)
        entries.extend(_entries(left, recovered=left.name))
    new = queue_new(seat, session)
    taking = base / f"{TAKING_PREFIX}{session or '-'}.{pid}"
    taking.mkdir(exist_ok=True)      # the recovery record exists before the first claim
    for name in os.listdir(new):
        try:
            os.rename(new / name, taking / name)   # the claim: one entry, exactly once
        except FileNotFoundError:
            continue                  # another scoop of this session took it first
    if taking not in dirs:
        dirs.append(taking)
    # A taking dir of this very pid can also be a leftover (pid reuse); its
    # entries are already listed, under their recovered name.
    already = {e.name for e in entries}
    entries.extend(e for e in _entries(taking) if e.name not in already)
    return Taken(tuple(dirs), tuple(entries))


def finish_queue(seat: Seat, session: str, taken: Taken) -> Path:
    """After the giving: every entry into done/ (the exactly-once memory;
    a name already there means it was given before, so this link is
    dropped), then the emptied taking directories removed."""
    done = ride_dir(seat, session) / QUEUE_DONE
    done.mkdir(parents=True, exist_ok=True)
    for e in taken.entries:
        target = done / e.name
        try:
            os.link(e.path, target)
        except FileExistsError:
            pass
        e.path.unlink(missing_ok=True)
    for d in taken.dirs:
        try:
            d.rmdir()
        except OSError:
            pass                     # something landed in it after the listing: next scoop's
    return done


#: The not-done reminder, "Read is not terminal" for the SAME session: an
#: item this session was given and never marked done is re-offered after
#: an interval - as ONE depth line, once per interval, never the item
#: again (the queue gives each entry exactly once). Q05 on the-queue-chain,
#: still open; the interval is REMIND_VAR, still inferred.
REMINDED_STAMP = "reminded"


def overdue(events: list[Mark], marks: dict[str, set[str]], session: str,
            since: datetime) -> tuple[int, datetime | None]:
    """How many messages this SESSION was given before SINCE that nobody
    has marked done, and the oldest such giving."""
    due = overdue_ids(events, marks, session, since)
    if not due:
        return 0, None
    return len(due), min(due.values())


def overdue_ids(events: list[Mark], marks: dict[str, set[str]], session: str,
                since: datetime) -> dict[str, datetime]:
    """`overdue`'s ids: each Message-ID this SESSION was given (seen or
    read) before SINCE that nobody has marked done, with its oldest
    giving. `overdue` is the count and the oldest of these."""
    oldest_by_id: dict[str, datetime] = {}
    for m in events:
        if m.session != session or m.mark not in ("seen", "read"):
            continue
        if "done" in marks.get(m.message_id, set()):
            continue
        try:
            when = datetime.fromisoformat(m.stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if m.message_id not in oldest_by_id or when < oldest_by_id[m.message_id]:
            oldest_by_id[m.message_id] = when
    return {k: v for k, v in oldest_by_id.items() if v < since}


#: How much of a done/ file `overdue_boxes` reads: the headers are at the
#: top, and a mail whose headers run past this is counted as `other`.
_HEAD_BYTES = 65536


def overdue_boxes(seat: Seat | None, session: str, ids) -> dict[str, int]:
    """IDS (overdue Message-IDs) counted by the box each came for: the `X-Original-To` key (`_maildir.original_to_key`) of the
    copy the ride gave, read from this session's ride `done/`, where
    `finish_queue` hard-links every mail it gave. A mark row carries no box
    column (specs/pager's MARK_LINE), so done/ is the one place the box is
    kept. An id with no done/ copy - read by `message read <BOX>`, or marked
    by hand - or whose copy names no address, counts under "".

    Reads headers only, and stops as soon as every id is placed. The caller
    pays it once per reminder interval, never on every scoop."""
    from email.parser import BytesHeaderParser
    from email.policy import compat32
    from . import _maildir      # here, not at the top: _maildir reads this module
    left = set(ids)
    keys: dict[str, int] = {}
    try:
        done = ride_dir(seat, session) / QUEUE_DONE
        names = sorted(os.listdir(done)) if left else []
    except (OSError, NoIdentity):
        names = []
    parser = BytesHeaderParser(policy=compat32)
    for name in names:
        if not left:
            break
        try:
            with open(done / name, "rb") as f:
                raw = f.read(_HEAD_BYTES)
        except OSError:
            continue               # filed away or unlinked since the listing
        end = re.search(rb"\r?\n\r?\n", raw)
        try:
            headers = parser.parsebytes(raw[:end.start()] if end else raw)
            mid = _maildir.bare_id(str(headers.get("Message-ID") or ""))
            to = str(headers.get("X-Original-To") or "")
        except Exception:          # a header the parser cannot read: unplaced
            continue
        if mid in left:
            left.discard(mid)
            key = _maildir.original_to_key(to)
            keys[key] = keys.get(key, 0) + 1
    if left:
        keys[""] = keys.get("", 0) + len(left)
    return keys


def reminder_due(seat: Seat, session: str, interval_seconds: float,
                 now: float | None = None) -> bool:
    """True when no reminder line has been printed inside the interval."""
    path = stamp_dir(seat, session) / REMINDED_STAMP
    try:
        last = path.stat().st_mtime
    except FileNotFoundError:
        return True
    return ((time.time() if now is None else now) - last) >= interval_seconds


def queue_names(seat: Seat, session: str) -> set[str]:
    """Every maildir name already in this session's chain - waiting in
    new/, mid-scoop in a taking.*, or given in done/. The listener links a
    box arrival only when its name is in none of them, so a restart never
    re-links what a scoop has already given (the name is the key)."""
    base = ride_dir(seat, session)
    names: set[str] = set()
    if not base.is_dir():
        return names
    for d in base.iterdir():
        if d.is_dir() and (d.name in (QUEUE_NEW, QUEUE_DONE) or d.name.startswith(TAKING_PREFIX)):
            try:
                names.update(os.listdir(d))
            except FileNotFoundError:
                # It went between the iterdir and the listing. A
                # finished scoop rmdirs its taking.*, and an older scoop's
                # swap leaves new/ absent for a moment. Either way its names
                # are now in a directory this loop has seen or will see.
                continue
    return names


# ── the birth of a session box, and its doorbell ──────────────────────────
#: After `session mail arm` was retired, nothing else made a fresh session's
#: maildir or minted the postfix alias line that copies the seat's mail into
#: it (the arm did both; the generator writes a line only for a box that
#: exists). A successor obeying "never the arm" could not listen at all:
#: `no maildir ... nothing to listen on` (measured). So
#: the first `pager listen` of a session births the box, and every listen
#: MEASURES the doorbell (postmap, the map postfix reads) and mints the line
#: when it is missing - bounded, best-effort, announced, never silent.
#: Measured: the alias half alone is a
#: bounce. `--valias-only` minted `<seat>@<host> -> <seat>, <session>` and
#: nothing wrote the MAILBOX line `<session>@<host> -> agents/<session>/`
#: that nm-deliver resolves, so every doorbell copy to a cold seat came back
#: 5.1.1 "no mailbox mapping" under a banner that said LIVE. The generator
#: now runs WHOLE (0.2 s for 2,381 boxes, measured) and the doorbell asks
#: BOTH maps: LIVE means a mail would land.
GEN_VAR = "SESSIONMAIL_VMAILBOX_GEN"   # == _generator.VAR (kept: conftest and tests import it)
REGEN_BUDGET_VAR = "SESSIONMAIL_REGEN_BUDGET_SECONDS"
#: The generator is linear at ~4.8 ms/box, and a fixed 10 s budget was a
#: deadline a growing host outgrew. Floor 10 s, then
#: 12 ms per box (contended rate, with headroom).
REGEN_FLOOR = 10.0
REGEN_PER_BOX = 0.012


def birth_box(root: Path, name: str) -> bool:
    """Make ROOT/NAME/{cur,new,tmp} if NAME has no new/. True when born.
    Every directory made here, the mail root's missing parents included, is
    0700 under any umask; an existing level keeps its mode."""
    box = root / name
    if (box / "new").is_dir():
        return False
    make_maildir(box)
    return True


def _postfix_maps(parameter: str) -> list[str] | None:
    """The hash:/lmdb: maps postfix reads for PARAMETER, or None if postconf
    could not say."""
    import subprocess
    try:
        out = subprocess.run(["postconf", "-h", parameter], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [m for m in out.replace(",", " ").split()
            if m.startswith("hash:") or m.startswith("lmdb:")]


def _postconf_value(parameter: str) -> str | None:
    """`postconf -h PARAMETER`, stripped; None if postconf could not say."""
    import subprocess
    try:
        return subprocess.run(["postconf", "-h", parameter], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def box_via_map(name: str) -> tuple[Path, str] | None:
    """A bare box NAME resolved the way postfix DELIVERS to it: `<name>@<host>`
    in virtual_mailbox_maps -> virtual_mailbox_base / value. (path, why) when
    that path is a maildir; None when NAME has a slash, postfix cannot be
    asked, there is no row, or the row points at no maildir.

    Why: `<root>/<name>` is only where FLAT boxes live. A project box under
    nested-v1 lives at `<root>/<store>/<name>/` (widget@alpha ->
    agents/acme/widget/), so `pager listen widget` refused with "no maildir"
    while mail to that name landed fine - a seat started with that boot line
    was born deaf (measured). The map is the one place the answer already
    is; asking it is what a human typing the name expects too.
    """
    if not name or "/" in name or name.startswith("."):
        return None
    maps = _postfix_maps("virtual_mailbox_maps")
    if not maps:
        return None
    from . import _host
    host = _host.live()  # key as vmailbox-gen WRITES (hostname -s), not by NAME
    key = f"{name}@{host}"
    hit = _postmap_lookup(key, maps)
    tagged = ""
    if hit is None and "+" in name:
        # What postfix does next (virtual(5) TABLE SEARCH ORDER, with
        # recipient_delimiter `+`): strip the extension at the FIRST `+` and
        # deliver to the base box. `<box>[+<tag>]@<host>` - the tag is a slot
        # in the SAME maildir, never a new one (specs/addressing). This
        # once looked up the exact key only, so `message path widget+notes`
        # said "no row" while postfix delivered it with the plus removed
        # (measured).
        base, _, tag = name.partition("+")
        if base and not base.startswith("."):
            key = f"{base}@{host}"
            hit = _postmap_lookup(key, maps)
            tagged = f" (tag {tag!r} kept: recipient_delimiter +)"
    if hit is None:
        return None
    base = _postconf_value("virtual_mailbox_base")
    if not base:
        return None
    path = Path(base).expanduser() / hit[1]
    if not (path / "new").is_dir():
        return None
    return path, f"{key} -> {hit[1]}, in {hit[0]}{tagged}"


def _postmap_lookup(key: str, maps: list[str]) -> tuple[str, str] | None:
    """(map path, value) for the first map that resolves KEY, else None."""
    import subprocess
    for m in maps:
        try:
            res = subprocess.run(["postmap", "-q", key, m], capture_output=True, text=True,
                                 timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0 and res.stdout.strip():
            return m.split(":", 1)[1], res.stdout.strip()
    return None


def doorbell(address: str, session: str, env: dict[str, str] | None = None
             ) -> tuple[bool | None, str]:
    """Would a mail to <address>@<host> LAND in SESSION's box? ADDRESS is the
    seat's full address, `+` kept (task-address-doorbell D2): a plus seat's
    row is keyed on the plus form, never on its base. Two maps, both
    asked (F21): the alias map postfix READS must copy the seat's address
    into the session's, AND the mailbox map must resolve the session's
    address to a box - the alias half alone bounces 5.1.1.
    (True, why) / (False, "NOT COPIED: why" | "NOT DELIVERABLE: why") /
    (None, why) when it cannot be measured.

    The maps asked are the CARRIER's. An MTA carrier: postfix's compiled
    maps, as above. The mail drop: the drop's own rows (`_drop_doorbell`),
    because a drop host has no compiled maps and postfix's, where postfix is
    installed anyway, say nothing about where the drop delivers. A refused
    carrier delivers nothing, so it is not measured, and says why."""
    import shutil
    from . import _settings
    env = os.environ if env is None else env
    carrier = _settings.carrier(env)
    if carrier.kind == "drop":
        return _drop_doorbell(address, session, env)
    if carrier.kind == "refused":
        return None, f"the carrier is refused: {carrier.why}"
    if not shutil.which("postconf") or not shutil.which("postmap"):
        return None, "no postconf/postmap on PATH to read the alias maps"
    alias_maps = _postfix_maps("virtual_alias_maps")
    if alias_maps is None:
        return None, "postconf could not read virtual_alias_maps"
    from . import _host
    host = _host.live()  # key as vmailbox-gen WRITES (hostname -s), not by NAME
    key = f"{address}@{host}"
    copied = _postmap_lookup(key, alias_maps)
    if not (copied and session and session in copied[1]):
        return False, f"NOT COPIED: no alias map postfix reads copies {key} into this box"
    box_key = f"{session}@{host}"
    mailbox_maps = _postfix_maps("virtual_mailbox_maps")
    if mailbox_maps is None:
        return None, f"{key} -> this box in {copied[0]}, but postconf could not read virtual_mailbox_maps"
    landed = _postmap_lookup(box_key, mailbox_maps)
    if landed is None:
        return False, (f"NOT DELIVERABLE: {key} -> this box in {copied[0]}, but no mailbox line for "
                       f"{box_key} in virtual_mailbox_maps - the copy would bounce 5.1.1 (F21)")
    return True, f"{key} -> this box, in {copied[0]}; {box_key} -> {landed[1]}, in {landed[0]}"


def rows_in_map(pairs: list[tuple[str, str]], env: dict[str, str] | None = None
                ) -> tuple[list[bool] | None, str]:
    """Does the alias map the carrier reads copy each KEY into its DEST? PAIRS
    are local parts, `(key, dest)`: a subscription's row key (`atomnote`,
    `heard+airpods`) and its subscriber (a session id or a seat), as the
    generator writes them (`<key>@<host> -> <key>@<host>, <dest>@<host>`).
    ([True | False per pair], "") when measured, (None, why) when not - never
    a guess. Read-only: it asks the maps and runs no generator.

    The maps asked are the doorbell's: postfix's compiled maps on an MTA
    carrier (`_postmap_lookup`, the first map that resolves the key, as
    virtual(8) reads them), the drop's own alias rows on the mail drop."""
    import shutil, socket
    from . import _settings
    env = os.environ if env is None else env
    if not pairs:
        return [], ""
    carrier = _settings.carrier(env)
    if carrier.kind == "refused":
        return None, f"the carrier is refused: {carrier.why}"
    if carrier.kind == "drop":
        from . import _drop
        try:
            maps = _drop.prepare(env)
        except _drop.NotDelivered as exc:
            return None, f"the mail drop could not read its maps: {exc}"
        if maps.alias_refused:
            return [False] * len(pairs), ""
        out = []
        for key, dest in pairs:
            k, d = _drop.qualify(key, maps.host), _drop.qualify(dest, maps.host)
            out.append(k is not None and d is not None and d in maps.alias.get(k, []))
        return out, ""
    if not shutil.which("postconf") or not shutil.which("postmap"):
        return None, "no postconf/postmap on PATH to read the alias maps"
    alias_maps = _postfix_maps("virtual_alias_maps")
    if not alias_maps:
        return None, "postconf named no hash: or lmdb: alias map to read"
    host = socket.gethostname().split(".")[0].lower()
    out = []
    for key, dest in pairs:
        hit = _postmap_lookup(f"{key.lower()}@{host}", alias_maps)
        dests = [d.strip().lower() for d in hit[1].split(",")] if hit else []
        out.append(f"{dest.lower()}@{host}" in dests)
    return out, ""


def _drop_doorbell(address: str, session: str, env: dict[str, str]) -> tuple[bool | None, str]:
    """`doorbell` under the mail drop: the two maps a drop send resolves with
    (`_drop.prepare`, the generator's print modes), read the way the drop
    reads them (`qualify`, `expand`, `box_for`). LIVE only when the seat's
    address expands to include the session's AND the session's address
    resolves to its existing maildir: the two halves of F21, asked of the
    drop. And every other address it expands to resolves too, because the
    drop delivers a send whole or not at all. Never asks postfix, and never
    names it."""
    from . import _drop
    try:
        maps = _drop.prepare(env)
    except _drop.NotDelivered as exc:
        return None, f"the mail drop could not read its maps: {exc}"
    key = _drop.qualify(address, maps.host, maps.names)
    box_key = _drop.qualify(session, maps.host, maps.names) if session else None
    if key is None or box_key is None:
        return None, (f"the mail drop delivers on this host only, and {address!r} or the "
                      f"session {session!r} is not an address here")
    if maps.alias_refused:
        return False, (f"NOT COPIED: the mail drop has no alias map ({maps.alias_refused}), "
                       f"so it copies {key} into no session's box")
    try:
        finals = _drop.expand(key, maps)
    except _drop.NotDelivered as exc:
        return False, f"NOT DELIVERABLE: the mail drop refuses {key}: {exc}"
    if box_key not in finals:
        return False, (f"NOT COPIED: no row in the mail drop's alias map (the map generator's "
                       f"--print-valias) copies {key} into this box")
    box = _drop.box_for(box_key, maps)
    if box is None:
        return False, (f"NOT DELIVERABLE: {key} -> this box in the mail drop's alias map, but "
                       f"no row in its mailbox map (the map generator's --print) delivers "
                       f"{box_key} to an existing maildir under {maps.root} - the drop never "
                       f"makes a box")
    # The drop writes a send to all of its boxes or to none (`_drop.plan`),
    # so one final with no box stops the session's copy too. A seat row's
    # first destination is a box by the generator's rule; a plus row's base
    # is not asked to be one.
    missing = [final for final in dict.fromkeys(finals) if _drop.box_for(final, maps) is None]
    if missing:
        return False, (f"NOT DELIVERABLE: {key} -> this box in the mail drop's alias map, but "
                       f"the drop delivers to all of a send's boxes or none, and no row in its "
                       f"mailbox map delivers {', '.join(missing)} to an existing maildir under "
                       f"{maps.root} - the drop never makes a box")
    value = maps.mailbox.get(box_key) or str(box)
    return True, (f"{key} -> this box, in the mail drop's alias map; {box_key} -> {value}, "
                  f"in its mailbox map")


def regen_maps(root: Path, env: dict[str, str] | None = None) -> str:
    """Run the generator once, WHOLE (both maps - F21), bounded, TERM never
    KILL; return one line saying what happened. Never raises."""
    return _run_generator(root, env, ())[1]


def regen_valias(root: Path, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Rebuild the ALIAS map only (`--valias-only`): what `message
    subscription add|remove` runs before it returns (message-subscriptions
    D3.1, "rebuild on the spot"). (ok, one line). Never raises."""
    return _run_generator(root, env, ("--valias-only",))


def _run_generator(root: Path, env: dict[str, str] | None,
                   extra: tuple[str, ...]) -> tuple[bool, str]:
    """The generator at GEN_VAR (else PATH, else the mail root's), once,
    bounded, TERM never KILL: (ok, one line saying what happened).

    This caller WRITES the live maps, so it stops at those three steps and
    never runs the packaged copy (`_generator`).

    It writes only when the carrier is an MTA. A carrier that is not one
    (`_settings.carrier` refused it) runs nothing and says why. On a
    mail-drop host it runs nothing either: such a
    host has no compiled maps to keep, because the drop asks the generator's
    print modes on every send. Without this skip, every box birth there
    would report "generator not found" (or the live-map guard's exit 5)."""
    import subprocess, time as _time
    from . import _generator, _settings
    env = os.environ if env is None else env
    carrier = _settings.carrier(env)
    if carrier.kind == "drop":
        return True, ("generator not run: the carrier is the mail drop; "
                      "it reads the maps per send")
    if carrier.kind == "refused":           # never a map on a carrier that is not one
        return False, f"generator not run: {carrier.why}"
    mailroot = root.parent
    found = _generator.find(env, mailroot, packaged=False)
    if found is None:
        return False, (f"generator not found ({_generator.expected(env, mailroot)}) - "
                       "this box's alias and mailbox lines NOT written")
    gen = found.path
    try:
        boxes = sum(1 for _ in root.iterdir())
    except OSError:
        boxes = 0
    raw = (env.get(REGEN_BUDGET_VAR) or "").strip()
    try:
        budget = float(raw) if raw else max(REGEN_FLOOR, REGEN_PER_BOX * boxes)
    except ValueError:
        budget = max(REGEN_FLOOR, REGEN_PER_BOX * boxes)
    t0 = _time.monotonic()
    try:
        proc = subprocess.Popen(_generator.argv(gen, ["--root", str(mailroot), *extra], env),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                env=_generator.spawn_env(env))
    except OSError as exc:
        return False, f"generator could not start ({gen}): {exc}"
    try:
        _, err = proc.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return False, (f"generator TIMED OUT after {budget:g}s ({boxes} boxes) - sent TERM, "
                       "this box's lines may be missing; see mail-regen.lib.sh")
    took = _time.monotonic() - t0
    if proc.returncode != 0:
        tail = (err or "").strip().splitlines()[-1:] or ["no message"]
        return False, f"generator exit {proc.returncode} in {took:.1f}s - {tail[0]}"
    return True, f"generator ran in {took:.1f}s ({boxes} boxes)"
