"""The boxes on this host, by kind, each with who else receives its mail.

`openspec/changes/message-subscriptions`, tasks 3.1: `message
box list` lists seat, project and store boxes, each row naming its
subscribers; session-id boxes only on request (they were 2,007 of 2,845
under `~/.mail/agents` on one measured host, and 2,202 of 2,993 later).

The walk is the mailbox map's (`postfix/vmailbox-gen`, `generate()`): a
directory under the root is a flat box; a directory holding the store
marker is a STORE directory whose children are project boxes, and is itself
a box only when it holds a maildir. Unlike the map, a directory is listed
only if it IS a maildir (`new/` and `cur/`, as `_maildir.resolve_box` asks),
so a stray folder is never called a box.

The subscribers come from `_subscription.scan`, the one reader, never a
copy of it: seat subscriptions always, session subscriptions only for the
sessions the caller counts live. This module reads directory names and
nothing else. It opens no message and writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable

import sessiontools

from . import _seat, _subscription

#: The store marker (nested-v1): `vmailbox-gen`'s STORE_MARKER, written by
#: taskmaster. Read here, never written.
STORE_MARKER = ".tm-store"

#: A session-id box: a full id, or the 8-hex short form older boxes carry
#: (56 of them on one measured host). A seat is never all hex.
_SESSION = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$",
                      re.IGNORECASE)

#: The maildir's own leaves: never a project, even under a store.
_LEAVES = {"new", "cur", "tmp"}

KINDS = ("seat", "project", "store", "session")


def is_session_id(name: str) -> bool:
    return bool(_SESSION.match(name))


@dataclass
class Box:
    """One box: its name as `message box path` takes it, its kind, where it
    is, when mail last moved in it, and who else receives its mail."""

    name: str                 # `alice`, `example/sessionmail`, `example`
    kind: str                 # one of KINDS
    path: Path
    mtime: float              # newest of new/ and cur/: the last delivery or read
    subscribers: list[str] = field(default_factory=list)   # the whole box
    tagged: dict[str, list[str]] = field(default_factory=dict)  # tag -> who

    @property
    def address(self) -> str:
        """The local part postfix delivers to this box by: a project is
        addressed by its own name, not `<store>/<project>`."""
        return self.name.rsplit("/", 1)[-1]


def _is_maildir(d: Path) -> bool:
    try:
        return (d / "new").is_dir() and (d / "cur").is_dir()
    except OSError:
        return False


def _mtime(d: Path) -> float:
    newest = 0.0
    for leaf in ("new", "cur"):
        try:
            newest = max(newest, (d / leaf).stat().st_mtime)
        except OSError:
            pass
    return newest


def _children(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir() if not p.name.startswith(".") and p.is_dir())
    except OSError:
        return []


def walk(root: Path) -> list[Box]:
    """Every box under ROOT, every kind, in name order."""
    out: list[Box] = []
    for d in _children(root):
        if (d / STORE_MARKER).exists():
            if _is_maildir(d):
                out.append(Box(d.name, "store", d, _mtime(d)))
            for p in _children(d):
                if p.name not in _LEAVES and _is_maildir(p):
                    out.append(Box(f"{d.name}/{p.name}", "project", p, _mtime(p)))
            continue
        if _is_maildir(d):
            kind = "session" if is_session_id(d.name) else "seat"
            out.append(Box(d.name, kind, d, _mtime(d)))
    return out


#: The characters `vmailbox-gen` lets through into a row (`SUB_CHARS`, plus
#: the `/`, `+` and `-` its `sub_address` also allows).
_ADDRESS_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./+-")


def refusal(box: str) -> str:
    """Why the generator would write NO row for a subscription to BOX, or ""
    when it would: `sub_address` in `postfix/vmailbox-gen`, every rule that
    needs no file system. A character postfix would not keep, more than one
    `/`, a `+` beside a `/`, an empty base or tag, or a second `+` is no
    address; and `<x>+memory` is claimed by the box alone (its identity row),
    never subscribed. What is left is RESOLUTION (a store holding the
    project, a maildir for the box or its base), which needs a root.

    The ONE Python copy of that bash rule: `message box list` names no
    subscriber the map would not copy, and `message subscription add`
    refuses what the map would skip (tasks 3.2). `tests/test_message_subscription.py` pins it to
    the generator, name by name."""
    if not set(box) <= _ADDRESS_CHARS:
        return "not an address: a character postfix would not keep"
    if box.count("/") > 1:
        return "no address: more than one '/'"
    if "+" not in box:
        return ""
    if "/" in box:
        return "no address: a '+' in a store or project"
    base, _, tag = box.partition("+")
    if not base or not tag or "+" in tag:
        return "not <base>+<tag>"
    if tag.lower() == "memory":
        return "a +memory address: claimed by the box alone, never subscribed"
    return ""


def _addressable(box: str) -> bool:
    """Whether the generator would write a row for a subscription to BOX
    (`refusal`). A listing that named any other would promise a copy the
    map never makes."""
    return not refusal(box)


def _label(entry: _subscription.Entry) -> str:
    """A seat by its name; a session by its short id (the fleet's 8)."""
    return entry.subscriber if entry.kind == "seat" else entry.subscriber[:8]


#: What `vmailbox-gen` keeps in an OWNER folder's name (`SUB_CHARS` plus
#: `-`; a seat may also hold `+`, `subscription_records`). Any other name, it
#: skips the whole folder: no row for any file in it.
OWNER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def owner_kept(entry: _subscription.Entry) -> bool:
    """Whether the generator reads ENTRY's owner folder at all: a seat name
    may hold `+`, a session id may not, and neither starts with `.`."""
    allowed = OWNER_CHARS | ({"+"} if entry.kind == "seat" else set())
    name = entry.subscriber
    return bool(name) and not name.startswith(".") and set(name) <= allowed


@dataclass(frozen=True)
class Routed:
    """One subscription the generator turns into a row: the file, the box
    it lands in, and the tag it claims ("" for the whole box)."""

    entry: _subscription.Entry
    box: Box
    tag: str

    @property
    def key(self) -> str:
        """The address postfix keys this row on, lowercase: a project by its
        own name, a plus form with its tag (`voicemode+inbox`)."""
        return f"{self.box.address}+{self.tag}".lower() if self.tag else self.box.address.lower()


def routed(boxes: list[Box], scan: _subscription.Scan) -> list[Routed]:
    """The subscriptions in SCAN that the map ROUTES, each on its box: the
    one predicate `box list` and `subscription list` share, so neither can
    name a copy the generator never makes. Dropped, as the generator drops
    them: an owner folder it would not keep, an address `refusal` refuses,
    and a box that does not resolve. `store/project` names the project; a
    bare name the box postfix delivers it to - a flat box first, as
    `resolve_box` tries `<root>/<name>` first, then a project or store
    addressed by that name; `<base>+<tag>` lands on its base, under the tag,
    because only mail to that tag reaches the subscriber."""
    by_name = {b.name.lower(): b for b in boxes}
    by_address: dict[str, Box] = {}
    for b in sorted(boxes, key=lambda b: (b.kind != "seat" and b.kind != "session", b.name)):
        by_address.setdefault(b.address.lower(), b)
    out: list[Routed] = []
    for entry in scan.entries:
        if not owner_kept(entry) or not _addressable(entry.box):
            continue                       # the generator refuses it: no row
        name = entry.box.lower()
        if "/" in name:
            box, tag = by_name.get(name), ""
        else:
            base, _, tag = name.partition("+")
            box = by_address.get(base)
        if box is None:
            continue                       # unresolvable: the generator skips it too
        out.append(Routed(entry, box, tag))
    return out


def attach(boxes: list[Box], scan: _subscription.Scan) -> None:
    """Hang each ROUTED subscription on its box (`routed`): the whole box,
    or under the one tag it claims."""
    for r in routed(boxes, scan):
        who = _label(r.entry)
        bucket = r.box.tagged.setdefault(r.tag, []) if r.tag else r.box.subscribers
        if who not in bucket:
            bucket.append(who)


def matches(box: Box, pattern: str | None) -> bool:
    """PATTERN is a glob if it holds `*`, `?` or `[`, else a substring;
    either way case-insensitive, against the name `path` takes."""
    if not pattern:
        return True
    name, pat = box.name.lower(), pattern.lower()
    if any(c in pat for c in "*?["):
        return fnmatchcase(name, pat)
    return pat in name


def row(box: Box) -> str:
    """`<kind> <name>` and, when anyone else receives it, `<- who, ...`."""
    heard = list(box.subscribers)
    heard += [f"+{tag}: {', '.join(who)}" for tag, who in sorted(box.tagged.items())]
    tail = f"  <- {'; '.join(heard)}" if heard else ""
    return f"{box.kind:<7} {box.name}{tail}"


def live_sessions() -> set[str]:
    """The sessions this host counts live: a harness sidecar
    (`sessiontools.sessions`) whose pid is alive - `session list`'s rule,
    which the generator applies itself, from the same sidecars and `ps`
    (`live_sessions_json`, since master 2030-12-08). Its procStart check is
    not repeated here: a recycled pid can at worst name one extra subscriber
    in a listing, and routes nothing. `sessiontools` also skips a sidecar
    with no `status`/`updatedAt`, which the generator does not need; every
    real sidecar carries both (17 of 17 measured), and a
    thin one could only hide a subscriber from a listing, never a route.
    No harness is no session."""
    try:
        found = sessiontools.sessions()
    except Exception:          # a listing never fails on the harness
        return set()
    return {s.id for s in found if s.id and s.pid and _seat._pid_alive(s.pid)}


def listing(root: Path, *, live: Iterable[str], home: Path | None = None) -> list[Box]:
    """The boxes under ROOT with their subscribers attached: seats always,
    and a session in LIVE only when its box exists under ROOT - the
    generator's own rule (`vmailbox-gen`, a live session WITH A BOX), so a
    listing never names a subscriber the map would not copy."""
    boxes = walk(root)
    live_boxed = {sid for sid in live if sid and "/" not in sid and (root / sid).is_dir()}
    attach(boxes, _subscription.scan(home, live=live_boxed))
    return boxes


__all__ = ["Box", "KINDS", "OWNER_CHARS", "Routed", "STORE_MARKER", "attach", "is_session_id",
           "listing", "live_sessions", "matches", "owner_kept", "refusal", "routed", "row",
           "walk"]
