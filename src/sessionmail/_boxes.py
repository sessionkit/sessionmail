"""The boxes on this host, by kind, each with who else receives its mail.

`openspec/changes/message-subscriptions`, tasks 3.1: `message
box list` lists seat, project and store boxes, each row naming its
subscribers; session-id boxes only on request (they were 2,007 of 2,845
under the boxes root on one measured host, and 2,202 of 2,993 later).

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

import os
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


# ── the short session id: ONE resolver for every verb ───────────────────────
#
# A pane title shows a session's first 8 hex, so a seat types that. Every
# verb used to look the token up as an exact NAME, and the send relayed a
# miss off-host. This is the one place a short form is expanded: at the
# EDGE, by each verb's door, never inside `_maildir.resolve_box` (whose
# internal callers pass names taken from mail headers and the environment).
# The bash `mail_box_resolve` step 4 keeps its own rule (rule 1 there:
# "Expansion happens once, here, at the edge").

#: The shortest prefix expanded: what a pane title shows. Below it, a short
#: all-hex seat name (`faded`, `decade`) could be captured, and 4 hex collide
#: by the birthday bound on a host with thousands of boxes.
SHORT_MIN = 8

#: The shape a full session id has; a short form is a STRICT prefix of it.
UUID_TEMPLATE = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

#: A full session id, and nothing else: the only names a short form expands to.
FULL_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)

#: How many candidates an ambiguous refusal names before `…, N in all`
#: (sessiontools' AMBIGUOUS_SHOW).
AMBIGUOUS_SHOW = 10

SOURCES = ("box", "session")


def short_form(token: str) -> str | None:
    """TOKEN lowercased when it is a STRICT prefix, SHORT_MIN to 35
    characters, of UUID_TEMPLATE (hex where it has `x`, `-` where it has
    `-`); else None. A full id is not a short form: it is exact or nothing."""
    if not SHORT_MIN <= len(token) < len(UUID_TEMPLATE):
        return None
    low = token.lower()
    for ch, want in zip(low, UUID_TEMPLATE):
        if want == "-" and ch != "-":
            return None
        if want == "x" and ch not in "0123456789abcdef":
            return None
    return low


@dataclass(frozen=True)
class Resolved:
    """What a door uses from here on. VALUE is the input, or the input with
    the full id spliced in for the short form (display name, `+tag` and any
    `@domain` kept). FULL is the full id when one was expanded, else "".
    NOTE is the one stderr line when expanded, else ""; it carries no
    program name, so each door prefixes its own."""

    value: str
    full: str = ""
    note: str = ""


class Unresolved(Exception):
    """A short form this host cannot expand: WHY is "ambiguous" (CANDIDATES,
    sorted, are every full id it starts) or "none" (no candidate). TOKEN is
    the short form as typed."""

    def __init__(self, token: str, candidates: list[str], why: str, source: str = "box"):
        self.token, self.candidates, self.why, self.source = token, sorted(candidates), why, source
        super().__init__(refusal_text(self))


def _address_parts(value: str) -> tuple[int, int, str]:
    """(start, end, addr): where the address sits in VALUE, the innermost
    `<...>` when there is one (`_sender.addr_spec`'s rule), else all of it."""
    i = value.rfind("<")
    j = value.find(">", i + 1) if i != -1 else -1
    if i != -1 and j != -1:
        return i + 1, j, value[i + 1:j]
    return 0, len(value), value


def short_of(value: str) -> str | None:
    """The short form (`short_form`) VALUE's address carries in the base of
    its local part, or None: `Gc <c0ffee01+ext@host>` -> `c0ffee01`."""
    local = _address_parts(value)[2].partition("@")[0]
    return short_form(local.partition("+")[0])


def _sessions_root(env) -> Path | None:
    """sessiontools' sessions leaf (`$SESSIONS_DIR`, else its old or new
    folder by its leaf rule), or None when it cannot say. The ONE session-dir
    resolver every sessionmail reader uses, `_layer.sessions_root`:
    never mail's own state leaf, never a spelled `~/.session` or
    `~/.sessionmail`. Imported here: `_layer` loads sessiontools' settings
    submodule only when asked."""
    try:
        from . import _layer
        return _layer.sessions_root(env)
    except Exception:          # a resolver never fails on the harness
        return None


def _exact(base: str, source: str, env) -> bool:
    """Whether BASE already names something exactly: an exact name wins
    before any expansion (a legacy 8-hex box, a session dir by that name)."""
    if source == "session":
        where = _sessions_root(env)
        return where is not None and (where / base).is_dir()
    from . import _maildir
    try:
        _maildir.resolve_box(base, None if env is None else dict(env))
    except _maildir.NoSuchBox:
        return False
    return True


def starting(short: str, where: Path, *, maildirs: bool = True) -> list[str]:
    """Every name directly under WHERE that is a full id starting with SHORT
    (a `short_form`), sorted: a MAILDIR, or with MAILDIRS false any
    directory. Reads only; a WHERE that cannot be read has none."""
    check = _is_maildir if maildirs else Path.is_dir
    out: list[str] = []
    try:
        with os.scandir(where) as it:
            for e in it:
                low = e.name.lower()
                if low.startswith(short) and FULL_ID.fullmatch(e.name) and check(Path(e.path)):
                    out.append(e.name)
    except OSError:
        return []
    return sorted(out)


def _candidates(short: str, source: str, env) -> list[str]:
    """Every full id under SOURCE's root that starts with SHORT: a flat
    MAILDIR named by a full id (`source="box"`; session boxes are always
    flat, see `walk`), or a session directory (`source="session"`)."""
    if source == "session":
        where = _sessions_root(env)
        return [] if where is None else starting(short, where, maildirs=False)
    from . import _maildir
    return starting(short, _maildir.root(env))


def resolve_session(value: str, *, source: str = "box", env=None) -> Resolved:
    """VALUE with a short session id expanded to the one full id it starts,
    on THIS host. Pure: no stderr, no writes.

    VALUE is a box name or an address: `c0ffee01`, `c0ffee01+notes`,
    `Gc <c0ffee01+ext>`, `c0ffee01@host`. Only the base of the local part
    is expanded; the rest is kept. Whether an `@domain` is this host's is
    the CALLER's call (the send asks `_sender.is_local` first): a box on
    another host cannot be seen from here.

    In order: a base that is not a short form (`short_form`) comes back
    unchanged; so does one that already names something exactly; one
    candidate is spliced in, with a note; more than one, or none, raises
    Unresolved. SOURCE picks the candidates: "box", the flat full-id
    maildirs under the mail root; "session", sessiontools' session dirs."""
    if source not in SOURCES:
        raise ValueError(f"not a source: {source!r}")
    start, end, addr = _address_parts(value)
    local, at, domain = addr.partition("@")
    base, plus, tag = local.partition("+")
    short = short_form(base)
    if short is None:
        return Resolved(value)
    if _exact(base, source, env) or (short != base and _exact(short, source, env)):
        return Resolved(value)
    found = _candidates(short, source, env)
    if len(found) != 1:
        raise Unresolved(base, found, "ambiguous" if found else "none", source)
    full = found[0]
    new = f"{full}{plus}{tag}{at}{domain}"
    kind = "session box" if source == "box" else "session"
    note = (f"{base!r} is session {full} (the only {kind} on this host that starts "
            f"with it)")
    return Resolved(value[:start] + new + value[end:], full, note)


def refusal_text(u: Unresolved, prog: str = "") -> str:
    """The ONE wording of a short id that does not resolve, for every door.
    PROG, when given, leads the line (`message list: ...`)."""
    lead = f"{prog}: " if prog else ""
    kind, kinds = (("session box", "session boxes") if u.source == "box"
                   else ("session", "sessions"))
    if u.why == "ambiguous":
        shown = ", ".join(u.candidates[:AMBIGUOUS_SHOW])
        more = (f", … {len(u.candidates)} in all" if len(u.candidates) > AMBIGUOUS_SHOW else "")
        return (f"{lead}{u.token!r} is ambiguous: it starts {len(u.candidates)} {kinds} "
                f"on this host ({shown}{more}); name one in full")
    return (f"{lead}{u.token!r} looks like a short session id, and no {kind} on this host "
            f"starts with it. A short id is resolved on THIS host only and never relayed: "
            f"for a session on another host, use its full id")


__all__ = ["AMBIGUOUS_SHOW", "Box", "FULL_ID", "KINDS", "OWNER_CHARS", "Resolved", "Routed",
           "SHORT_MIN", "SOURCES", "STORE_MARKER", "UUID_TEMPLATE", "Unresolved", "attach",
           "is_session_id", "listing", "live_sessions", "matches", "owner_kept", "refusal",
           "refusal_text", "resolve_session", "routed", "row", "short_form", "short_of", "starting",
           "walk"]
