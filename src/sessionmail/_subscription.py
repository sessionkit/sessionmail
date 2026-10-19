"""The subscription file: its name, and the reader that turns files into pairs.

`openspec/changes/message-subscriptions`, tasks 2.1 and D4:

- **Name = the box**, exactly as `message path` takes it: `atomnote`,
  `voicemode+inbox`, or `work/demo`, which is STORED as
  `work--demo` because a file name cannot hold `/`. `file_name` and
  `box_name` are the one codec, so the writer (the verbs, tasks 3.2) and the
  reader (the generator's rows, tasks 2.2) cannot drift apart.
- **Body = a note** of who, when and why: *"No query, route or policy. The
  generator reads only the name."* (D1). The ONE exception: a body with a
  `query:` line is an OLD saved query from the pager's retired subscriptions,
  which share this folder until tasks 6.3 moves them (192 on one measured
  host). It is skipped, never read as a box, and so is a file whose
  body cannot be read, since it cannot be ruled out. The generator
  (`postfix/vmailbox-gen`, `sub_folder`) skips the same files; the parity
  test pins it.
- **Seat or session is the FOLDER, never a field** (D1):

      <home>/seats/<seat>/subscriptions/<box>     durable, until removed
      <home>/sessions/<sid>/subscriptions/<box>   only while <sid> is live

  **A SEAT's moved** (sessiontools session-settings 3.2): its subscriptions
  are the `subscriptions` map of `~/.session/seats/<seat>/settings/mail.json`
  (`_layer`), each `true` key a box, read per owner. The old folder is read
  only for an AGENT's bare name (`alice`, `bob`: no `+`, no `.`) until 3.8
  reads the agent layer. In a seat's old folder, a file that is not a saved
  query was never converted: it is skipped, with `pager settings migrate`
  named as the way across.

  **A SESSION's moved too** (3.3b): a live session's subscriptions are the
  map of `~/.session/sessions/<sid>/settings/mail.json`, read the same way;
  a box file left in its old folder is skipped as never converted.

  (`specs/message`, "A subscription is a file that names a box").

The reader is PURE over the sessionmail home. It does not ask who is live:
the caller passes the session ids it counts live (the generator's rule is
"`session list` reports it LIVE and its box exists", D2), so a dead
session's files are simply never read and no reaper is needed. It writes
nothing, and it never raises over one bad file: a name that cannot be a
stored box comes back in `Scan.skipped` with its reason, to be counted
(tasks 2.4 gives an unresolvable box the same treatment at build time).

The codec refuses what would not ROUND-TRIP. `--` inside a box would read
back as `/`; a segment that starts or ends with `-` beside a `/` makes a run
of three dashes that reads back wrongly (`a-/b` and `a/-b` both store as
`a---b`), so a segment may do neither. A segment starting with `.` is a
hidden file (or `..`), and white space or a control character in a file
name is a trap for every shell that lists the folder. Case is KEPT, as the
box is kept: `foreman.Web-12` is a real box name.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import _layer, _seat
from ._maildir import strict_mode

#: The folder a subscription file lives in, under a seat or a session.
FOLDER = "subscriptions"
SEATS_DIR = "seats"
SESSIONS_DIR = _seat.SESSIONS_DIR

#: How `/` is stored in a file name (D1).
SLASH = "--"

_SPACE = re.compile(r"[\s\x00-\x1f\x7f]")


class BadBox(ValueError):
    """A box that cannot be stored as a file name, or a file name that is
    not a stored box. The message says which rule it broke."""


def _check_segment(box: str, segment: str) -> None:
    if not segment:
        raise BadBox(f"{box!r}: an empty part between slashes")
    if segment.startswith("."):
        raise BadBox(f"{box!r}: a part starts with '.' (a hidden file, or '..')")
    if SLASH in segment:
        raise BadBox(f"{box!r}: holds '--', which would read back as '/'")
    if segment.startswith("-") or segment.endswith("-"):
        raise BadBox(f"{box!r}: a part starts or ends with '-', "
                     "so the stored name would not read back")


def file_name(box: str) -> str:
    """The file name that stores BOX: `/` becomes `--`. BadBox if the name
    could not be read back as exactly BOX."""
    if not box:
        raise BadBox("an empty box name")
    if not box.isascii():
        # A box is an address, and sessionmail's addresses are ASCII. Refusing
        # it HERE keeps the bash codec (vmailbox-gen sub_decode) equal in every
        # locale: under LC_ALL=C bash cannot see NBSP or U+2028 as white space
        # (330 of 3,000 fuzz names disagreed).
        raise BadBox(f"{box!r}: not ASCII")
    if _SPACE.search(box):
        raise BadBox(f"{box!r}: white space or a control character")
    for segment in box.split("/"):
        _check_segment(box, segment)
    return box.replace("/", SLASH)


def box_name(stored: str) -> str:
    """The box a file named STORED subscribes to: `--` becomes `/`. BadBox
    if STORED is not a name `file_name` would have written."""
    if not stored:
        raise BadBox("an empty file name")
    box = stored.replace(SLASH, "/")
    try:
        again = file_name(box)
    except BadBox as exc:
        raise BadBox(f"file {stored!r} is not a stored box: {exc}") from None
    if again != stored:
        raise BadBox(f"file {stored!r} is not a stored box: it reads back as "
                     f"{box!r}, which is stored as {again!r}")
    return box


@dataclass(frozen=True)
class Entry:
    """One subscription file, read."""

    kind: str           # "seat" or "session": the folder it was found in
    subscriber: str     # the seat's folder name (its address), or the session id
    box: str            # decoded: `work/demo`, never `work--demo`
    path: Path


@dataclass(frozen=True)
class Scan:
    """What `scan` found: the subscriptions, and every file it would not read."""

    entries: tuple[Entry, ...]
    skipped: tuple[tuple[Path, str], ...]

    @property
    def pairs(self) -> set[tuple[str, str]]:
        return {(e.subscriber, e.box) for e in self.entries}


def _owners(parent: Path, skipped: list[tuple[Path, str]]) -> list[Path]:
    """The owner folders under PARENT (seats/ or sessions/), hidden ones cut.
    An unreadable PARENT is skipped with its reason, like an unreadable
    subscriptions/ folder: it must not throw away the other half's pairs."""
    try:                                # a strict stat: PARENT r-- lists, and denies it
        found = [p for p in parent.iterdir()
                 if not p.name.startswith(".") and stat.S_ISDIR(strict_mode(p))]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except PermissionError as exc:
        skipped.append((parent, f"cannot read the folder: {exc.strerror}"))
        return []
    return sorted(found)


def _saved_query(path: Path) -> str:
    """Why PATH is not a subscription because of its BODY, or "": an old
    saved query (a line that starts `query:`, leading white space allowed; a
    `# ... query:` comment is a note), or a body that cannot be read."""
    try:
        with path.open("rb") as fh:
            for raw in fh:
                if raw.lstrip().startswith(b"query:"):
                    return "an old saved query (its body has a query: line), not a subscription"
    except OSError as exc:
        return f"cannot read the file, so cannot rule out a saved query: {exc.strerror}"
    return ""


def _read_folder(kind: str, owner: Path, entries: list[Entry],
                 skipped: list[tuple[Path, str]]) -> None:
    folder = owner / FOLDER
    try:
        children = sorted(folder.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return
    except PermissionError as exc:          # one seat's folder must not stop the rest
        skipped.append((folder, f"cannot read the folder: {exc.strerror}"))
        return
    for path in children:
        if path.name.startswith("."):
            continue                    # an atomic write in flight, or an editor's
        try:                            # a folder that lists but cannot be
            mode = strict_mode(path)    # searched (r--) denies this stat; a
        except OSError as exc:          # strict one, since 3.14's is_file() says False
            skipped.append((path, f"cannot read the file: {exc.strerror}"))
            continue
        regular, folder_too = stat.S_ISREG(mode), stat.S_ISDIR(mode)
        if not regular:
            why = ("a folder, not a subscription file" if folder_too
                   else "not a regular file (a dangling link, a fifo?)")
            skipped.append((path, why))
            continue
        why = _saved_query(path)
        if why:
            skipped.append((path, why))
            continue
        try:
            box = box_name(path.name)
        except BadBox as exc:
            skipped.append((path, str(exc)))
            continue
        entries.append(Entry(kind=kind, subscriber=owner.name, box=box, path=path))


#: Why a box file in a SEAT's old folder is skipped (session-settings 3.2).
NOT_CONVERTED = ("a seat's old folder, never converted: its subscriptions are read from "
                 "~/.session/seats/<seat>/settings/mail.json now, and `pager settings migrate` "
                 "moves this one")
#: ... and in a live SESSION's old folder (3.3b). The generator says the same.
NOT_CONVERTED_SESSION = (
    "a session's old folder, never converted: its subscriptions are read from "
    "~/.session/sessions/<sid>/settings/mail.json now, and `pager settings migrate` "
    "(or the session's next `pager listen`) moves this one")


def _read_map(kind: str, owner: str, directory: Path, entries: list[Entry],
              skipped: list[tuple[Path, str]]) -> None:
    """One layer's `subscriptions` map (3.2, 3.3b): a `true` key is a box,
    per owner. The entry's path is the file that set it. A layer that cannot
    be read is skipped whole, with its reason, like a folder."""
    try:
        values, origins = _layer.read(directory, kind)
    except _layer.LayerError as exc:
        skipped.append((directory / _layer.FILE, str(exc)))
        return
    for box in _layer.boxes(values):
        origin = Path(origins.get(f"{_layer.SUBSCRIPTIONS}.{box}") or directory / _layer.FILE)
        try:
            file_name(box)              # the codec's gates: one box, or skipped
        except BadBox as exc:
            skipped.append((origin, f"{_layer.SUBSCRIPTIONS}.{box}: {exc}"))
            continue
        entries.append(Entry(kind=kind, subscriber=owner, box=box, path=origin))


def _read_seat_maps(env: dict[str, str] | None, entries: list[Entry],
                    skipped: list[tuple[Path, str]]) -> None:
    """Each seat's `subscriptions` map in the new tree, per owner (3.2): a
    `true` key is a box. The entry's path is the file that set it. A layer
    that cannot be read is skipped whole, with its reason, like a folder."""
    try:
        root = _layer.seats_root(env)
    except _layer.LayerError as exc:
        skipped.append((Path("~/.session/seats"), str(exc)))
        return
    for owner in _owners(root, skipped):
        if _layer.is_agent_name(owner.name):
            continue                    # an agent's bare name: the old folder, until 3.8
        _read_map("seat", owner.name, owner / "settings", entries, skipped)


def _read_session_maps(live_ids: set[str], env: dict[str, str] | None,
                       entries: list[Entry], skipped: list[tuple[Path, str]]) -> None:
    """Each LIVE session's map (3.3b), in the session dir sessiontools keeps.
    Only listed sessions are read, so a dead session's map routes nothing."""
    for sid in sorted(live_ids):
        if "/" in sid or sid.startswith("."):
            continue                    # never a path out of the sessions root
        try:
            directory = _layer.session_settings_dir(sid, env)
        except _layer.LayerError as exc:
            skipped.append((Path("~/.session/sessions"), str(exc)))
            return
        _read_map("session", sid, directory, entries, skipped)


def _unconverted(owner: Path, skipped: list[tuple[Path, str]],
                 kind: str = "seat", why: str = NOT_CONVERTED) -> None:
    """An old folder (a SEAT's, 3.2; a live SESSION's, 3.3b): nothing in it
    routes now. A saved query is skipped as ever; a box file is named as not
    converted."""
    found: list[Entry] = []
    _read_folder(kind, owner, found, skipped)
    for e in found:
        skipped.append((e.path, why))


def scan(home: Path | None = None, *, live: Iterable[str],
         env: dict[str, str] | None = None) -> Scan:
    """Every seat subscription, and the subscriptions of each session in
    LIVE. HOME defaults to the sessionmail home (`_seat.home`): agents' and
    sessions' old folders. A seat's map is under `~/.session/seats/` (3.2),
    a session's under `~/.session/sessions/` (3.3b), from ENV's HOME. Reads,
    writes nothing."""
    if isinstance(live, str):
        # A str is an Iterable[str] of its characters, so `live=sid` would
        # silently read no session at all. Refuse it; pass `{sid}`.
        raise TypeError("live is a collection of session ids, not one str")
    home = _seat.home(env) if home is None else Path(home)
    live_ids = {sid for sid in live if sid}
    entries: list[Entry] = []
    skipped: list[tuple[Path, str]] = []
    _read_seat_maps(env, entries, skipped)
    for owner in _owners(home / SEATS_DIR, skipped):
        if _layer.is_agent_name(owner.name):
            _read_folder("seat", owner, entries, skipped)
        else:
            _unconverted(owner, skipped)
    _read_session_maps(live_ids, env, entries, skipped)
    for owner in _owners(home / SESSIONS_DIR, skipped):
        if owner.name in live_ids:
            _unconverted(owner, skipped, "session", NOT_CONVERTED_SESSION)
    return Scan(entries=tuple(entries), skipped=tuple(skipped))


def pairs(home: Path | None = None, *, live: Iterable[str],
          env: dict[str, str] | None = None) -> set[tuple[str, str]]:
    """The set of (subscriber, box) pairs for seats and live sessions (tasks
    2.1): what the generator turns into `<box> -> <box>, <subscriber>` rows."""
    return scan(home, live=live, env=env).pairs


__all__ = ["BadBox", "Entry", "Scan", "file_name", "box_name", "scan", "pairs",
           "FOLDER", "SEATS_DIR", "SESSIONS_DIR", "SLASH", "NOT_CONVERTED",
           "NOT_CONVERTED_SESSION"]
