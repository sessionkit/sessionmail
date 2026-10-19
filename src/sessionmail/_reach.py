"""`message subscription list [BOX]`: what reaches me, and who hears this box.

`openspec/changes/message-subscriptions`, tasks 3.3, specs/message
"What reaches me, and who hears this box".

- **No BOX: a TREE rooted at the session, seat first** (the seat is always
  the first one: it is a tiering). Under the session: its seat (the row the generator writes for
  every live session with a box, D12), then each of its own subscriptions.
  Under every address: the addresses that FEED it, i.e. the subscriptions
  held in the folder named for that address (`seats/<address>/` or, for a
  live session, `sessions/<sid>/`). So the tree reads as the path mail takes
  to you.
- **A loop is printed once and marked** `(loop: already above)`, never
  walked twice; **depth is bounded** (4 by default, D6), and a node cut by
  the bound says how many feed it and how to see them.
- **"Subscribed" is what the generator ROUTES** (`_boxes.routed`, the same
  predicate `box list` uses and the same one `add` enforces): an old saved
  query, an unreadable file, a folder postfix would not keep, a refused or
  unresolvable address is never shown as reaching anyone; it is counted in
  the footer instead. A session's subscriptions count only while it is live
  with a box, the generator's own rule.
- **With BOX: who hears this box.** Every seat and live session subscribed
  to the whole box, then each tag claimed from it (a plus subscription
  claims its tag), the live sessions the seat row copies it to
  when BOX is a seat, and - during the release of grace (D9) - every live
  `pager listen` on BOX, marked as a LISTENER, not a subscription. A session
  whose own listener is live is marked `listening`.

Pure over its inputs: the mail root, the sessionmail home, the live set
(`_boxes.live_sessions`, never a second copy) and a `ps` table.
It opens no message and writes nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import sessiontools

from . import _boxes, _layer, _maildir, _seat, _subscription

#: "Depth stops at 4, with a line saying how to see more."
DEFAULT_DEPTH = 4

LOOP = "(loop: already above)"


# ── the graph: who feeds whom ──────────────────────────────────────────────

def _short(name: str) -> str:
    return name[:8] if _boxes.is_session_id(name) else name


def _when(path: Path) -> str:
    try:
        return time.strftime("%H:%M %a", time.localtime(path.stat().st_mtime))
    except OSError:
        return "?"


def _what(r: _boxes.Routed) -> str:
    """`project box, seat subscription <HH:MM> <weekday>`: what the address is, and
    which folder the file is in, and when it was written."""
    kind = (f"+{r.tag} of {r.box.kind} {r.box.name}" if r.tag
            else f"{r.box.kind} box")
    return f"{kind}, {r.entry.kind} subscription {_when(r.entry.path)}"


@dataclass
class Graph:
    """Every routed subscription, by the owner that holds it (lowercase):
    the owner's address is FED by each box in its list."""

    feeds: dict[str, list[_boxes.Routed]]
    boxes: list[_boxes.Box]
    #: files the generator skips, by owner (lowercase): shown only as a count
    dropped: dict[str, int] = field(default_factory=dict)

    def feeders(self, key: str) -> list[_boxes.Routed]:
        return self.feeds.get(key.lower(), [])


def graph(root: Path, *, live: Iterable[str], home: Path | None = None,
          env: dict[str, str] | None = None) -> Graph:
    """Read every seat's folder, and each session in LIVE whose box exists
    under ROOT (the generator's rule), into who-feeds-whom."""
    if isinstance(live, str):
        raise TypeError("live is a collection of session ids, not one str")
    boxes = _boxes.walk(root)
    live_boxed = {sid for sid in live if sid and "/" not in sid and (root / sid).is_dir()}
    scan = _subscription.scan(home, live=live_boxed, env=env)
    feeds: dict[str, list[_boxes.Routed]] = {}
    kept = _boxes.routed(boxes, scan)
    for r in kept:
        feeds.setdefault(r.entry.subscriber.lower(), []).append(r)
    for rs in feeds.values():
        rs.sort(key=lambda r: (r.entry.kind != "seat", r.entry.box.lower()))
    dropped: dict[str, int] = {}
    # By the entry, not its path: one seat's `mail.json` holds many (3.2).
    kept_entries = {r.entry for r in kept}
    for e in scan.entries:
        if e not in kept_entries:
            dropped[e.subscriber.lower()] = dropped.get(e.subscriber.lower(), 0) + 1
    home_ = _seat.home(env) if home is None else Path(home)
    try:
        tree = _layer.seats_root(env).parent    # ~/.session: seats' and sessions' maps
    except _layer.LayerError:
        tree = None
    for path, _why in scan.skipped:
        owner = _owner_of(path, home_) or (_owner_of(path, tree) if tree else "")
        if owner:
            dropped[owner] = dropped.get(owner, 0) + 1
    return Graph(feeds=feeds, boxes=boxes, dropped=dropped)


def _owner_of(path: Path, home: Path) -> str:
    """The owner folder a skipped PATH was found under, lowercase, or "".
    HOME is the sessionmail home (old folders) or `~/.session` (maps)."""
    try:
        parts = path.relative_to(home).parts
    except ValueError:
        return ""
    if len(parts) >= 2 and parts[0] in (_subscription.SEATS_DIR, _subscription.SESSIONS_DIR):
        return parts[1].lower()
    return ""


# ── the tree: what reaches me ──────────────────────────────────────────────

@dataclass
class Node:
    label: str
    note: str = ""
    children: list["Node"] = field(default_factory=list)
    loop: bool = False
    cut: int = 0          # feeders not walked because the depth bound stopped here


@dataclass
class Tree:
    root: Node
    addresses: int        # distinct addresses under the root
    levels: int           # the deepest level printed
    cut: int              # nodes the depth bound stopped at
    dropped: int          # subscription files on the tree that the map skips


def _walk(g: Graph, key: str, path: tuple[str, ...], depth: int, limit: int,
          seen: set[str], stats: dict[str, int], walked: set[str],
          skip: str = "") -> list[Node]:
    """The feeders of KEY as nodes: PATH holds every address above (the
    root included), so a feeder already on it is a loop. WALKED collects
    every owner whose folder was read, so its skipped files count once.
    A feeder labelled SKIP is not walked at all: it is drawn elsewhere, and
    a branch walked only to be thrown away still counts in the footer."""
    walked.add(key.lower())
    out: list[Node] = []
    for r in g.feeders(key):
        if skip and _short(r.entry.box).lower() == skip:
            continue
        k = r.key
        node = Node(label=_short(r.entry.box), note=_what(r))
        seen.add(k)
        stats["levels"] = max(stats["levels"], depth)
        if k in path:
            node.loop = True
        elif depth >= limit:
            node.cut = len(g.feeders(k))
            stats["cut"] += 1 if node.cut else 0
        else:
            node.children = _walk(g, k, path + (k,), depth + 1, limit, seen, stats, walked)
        out.append(node)
    return out


def tree(g: Graph, *, session: str = "", seat: str = "", limit: int = DEFAULT_DEPTH) -> Tree:
    """What reaches SESSION (or, with no session, SEAT) as a tree: the seat
    first, then the session's own subscriptions, each with what feeds it."""
    if limit < 1:
        raise ValueError("the depth bound is at least 1")
    seen: set[str] = set()
    stats = {"levels": 0, "cut": 0}
    walked: set[str] = set()
    seat_key = seat.lower()
    if session:
        top = Node(label=f"{_short(session)} (this session)", note=unmoved(session))
        path: tuple[str, ...] = (session.lower(),)
        if seat_key:
            s = Node(label=seat, note="your seat: its row copies seat mail here (D12)")
            seen.add(seat_key)
            stats["levels"] = 1
            if seat_key in path:
                s.loop = True
            elif limit <= 1:
                s.cut = len(g.feeders(seat_key))
                stats["cut"] += 1 if s.cut else 0
            else:
                s.children = _walk(g, seat_key, path + (seat_key,), 2, limit, seen, stats,
                                   walked)
            top.children.append(s)
            path = path + (seat_key,)
        # The session's own subscriptions; one naming the seat is the seat's
        # branch already, not a second one, so it is skipped BEFORE it is
        # walked: its cuts would otherwise count twice.
        top.children += _walk(g, session.lower(), (session.lower(),), 1, limit, seen, stats,
                              walked, skip=seat_key)
    else:
        top = Node(label=f"{seat} (seat)")
        seen.add(seat_key)
        top.children = _walk(g, seat_key, (seat_key,), 1, limit, seen, stats, walked)
    seen.discard(session.lower())
    if not session:
        seen.discard(seat_key)
    return Tree(root=top, addresses=len(seen), levels=stats["levels"],
                cut=stats["cut"], dropped=sum(g.dropped.get(k, 0) for k in walked))


def render(node: Node, *, limit: int) -> list[str]:
    """The tree drawn with box characters, each note in one column."""
    rows: list[tuple[str, str]] = [(node.label, node.note)]

    def draw(n: Node, prefix: str) -> None:
        kids = list(n.children)
        for i, c in enumerate(kids):
            last = i == len(kids) - 1 and not n.cut
            note = f"{c.note}  {LOOP}" if c.loop else c.note
            rows.append((prefix + ("└── " if last else "├── ") + c.label, note))
            draw(c, prefix + ("    " if last else "│   "))
        if n.cut:
            rows.append((prefix + "└── …",
                         f"{n.cut} more feed{'s' if n.cut == 1 else ''} this, below depth "
                         f"{limit} (--depth {limit + 1} shows them)"))

    draw(node, "")
    width = max(len(label) for label, _ in rows) + 3
    return [f"{label:<{width}}{note}".rstrip() if note else label for label, note in rows]


def footer(t: Tree, *, limit: int, boxed: bool = True) -> str:
    n = t.addresses
    line = (f"-- {n} address{'es' if n != 1 else ''} reach{'' if n != 1 else 'es'} you, "
            f"{t.levels} level{'s' if t.levels != 1 else ''} deep")
    if t.cut:
        line += f"; cut at depth {limit} in {t.cut} place{'s' if t.cut != 1 else ''} (--depth N)"
    if t.dropped:
        line += (f"; {t.dropped} subscription file{'s' if t.dropped != 1 else ''} the map "
                 "skips (saved queries, or boxes that do not resolve): not shown")
    if not boxed:
        line += ("; this session has NO BOX yet, so none of this reaches it until "
                 "`pager listen` births it")
    return line


# ── who hears this box ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Listener:
    owner: str            # the session id that runs it, or "" when none is found
    pid: int
    token: str            # the BOX it was given, or "" for the session's own


def _listen_box(rest: str) -> str:
    """The BOX argument of a `pager listen` command line's tail, or ""."""
    words = rest.split()
    i = 0
    while i < len(words):
        w = words[i]
        if w in ("-t", "--timeout"):
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        return w
    return ""


def _owner_by_ancestry(pid: int, ppid: dict[int, int], cmd: dict[int, str]) -> str:
    """The session id on PID's NEAREST claude ancestor, or "" when there is
    none or it carries no `--session-id` (an interactive `claude`,
    `--resume`, `--continue`). Never a claude further up: a listener
    under a nested claude is that claude's session's, not the outer one's
   . The walk and the parse are `_seat`'s, one copy each, so the
    `--session-id=X` form is read too."""
    claude = _seat._nearest_claude(pid, ppid, cmd)
    if claude is None:
        return ""
    return _seat._claude_session(cmd.get(claude, "")) or ""


def unmoved(session: str, env: dict[str, str] | None = None) -> str | None:
    """"not moved; at <old>" when SESSION's state still sits at the root
    before session-settings 3.3a (`<home>/sessions/<sid>`: a listener armed
    on old code, or a dead id) - and None when it was moved, or there is
    nothing at either place. The reader reads NEW paths only, so the old
    place is named, never read (3.1: say when old != new)."""
    old = _seat.old_session_root(session, env)
    new = _seat.session_root(session, env)
    if old is None or new is None or old.is_symlink() or not old.is_dir() or new.exists():
        return None
    return f"not moved; at {old}"


def listening_pid(session: str, env: dict[str, str] | None = None) -> int | None:
    """SESSION's live listener by its stamp (`<home>/sessions/<sid>/stamps/
    listening`, written by `pager listen` at arm), or None. Read-only: a
    stale stamp is left for the pager to clear."""
    root = _seat.session_root(session, env)
    if root is None:
        return None
    try:
        pid = int((root / _seat.STAMPS_DIR / _seat.LISTENING_STAMP).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _seat._pid_alive(pid) else None


def _where(name: str, boxes: list[_boxes.Box], env: dict[str, str] | None) -> Path | None:
    """The maildir a `pager listen` argument names: a path as given, a box on
    the walk (`find_box`'s rule, tag cut), else what `message path` finds."""
    if name.startswith(("/", "~", ".")):
        return Path(name).expanduser()
    hit = _find(boxes, name)
    if hit is not None:
        return hit[0].path
    try:
        return _maildir.resolve_box(name, env)[0]
    except _maildir.NoSuchBox:
        return None


def listeners(box: Path, *, live: Iterable[str], boxes: list[_boxes.Box] | None = None,
              env: dict[str, str] | None = None,
              ps: Callable[[], tuple[dict[int, int], dict[int, str]]] | None = None
              ) -> list[Listener]:
    """Every live `pager listen` whose box is BOX, one per owner. A process
    is a listener by its command line (`_seat._PAGER_LISTEN`, the pattern
    the ride-along's own check uses); its owner is the live session whose
    listening stamp names its pid, else the claude session above it."""
    ppid, cmd = (ps or _seat._ps_table)()
    stamped = {}
    for sid in live:
        pid = listening_pid(sid, env)
        if pid is not None:
            stamped[pid] = sid
    try:
        want = box.resolve()
    except OSError:
        want = box
    found: dict[str, Listener] = {}
    for pid, line in sorted(cmd.items()):
        m = _seat._PAGER_LISTEN.match(line)
        if not m:
            continue
        token = _listen_box(line[m.end():])
        owner = stamped.get(pid) or _owner_by_ancestry(pid, ppid, cmd)
        name = token or owner
        if not name:
            continue
        path = _where(name, boxes or [], env)
        if path is None:
            continue
        try:
            path = path.resolve()
        except OSError:
            pass
        if path != want:
            continue
        key = owner or f"pid {pid}"
        if key in found and found[key].pid in stamped and pid not in stamped:
            continue            # the stamped pid is the listener; the other its shell
        found[key] = Listener(owner=owner, pid=pid, token=token)
    return list(found.values())


def seat_sessions(address: str, live: Iterable[str], root: Path,
                  env: dict[str, str] | None = None) -> list[str]:
    """The live sessions WITH A BOX that seat ADDRESS's row copies to: those
    whose harness agent word is ADDRESS (the generator's `session list`
    rows), or that declared ADDRESS at `pager listen` (its plus rows)."""
    address = address.lower()
    live_boxed = {sid for sid in live if sid and "/" not in sid and (root / sid).is_dir()}
    out: set[str] = set()
    for sid in live_boxed:
        if _seat.read_address(sid, env) == address:     # its layer's `address` (3.3b)
            out.add(sid)
    try:
        for s in sessiontools.sessions(env):
            if s.id in live_boxed and (s.agent or "").lower() == address:
                out.add(s.id)
    except Exception:          # a listing never fails on the harness
        pass
    return sorted(out)


def find_box(g: Graph, name: str) -> tuple[_boxes.Box, str] | None:
    """BOX as `message box path` names it, on the walk the graph holds: the
    box and the tag (`voicemode+inbox` is `voicemode`, tag `inbox`)."""
    return _find(g.boxes, name)


def _find(boxes: list[_boxes.Box], name: str) -> tuple[_boxes.Box, str] | None:
    name = name.lower()
    by_name = {b.name.lower(): b for b in boxes}
    if name in by_name:
        return by_name[name], ""
    base, _, tag = name.partition("+")
    if "/" in name:
        return None
    for b in sorted(boxes, key=lambda b: (b.kind not in ("seat", "session"), b.name)):
        if b.address.lower() == base:
            return b, tag
    return None


def hearers(g: Graph, box: _boxes.Box, tag: str, *, live: Iterable[str], root: Path,
            env: dict[str, str] | None = None, ps=None) -> tuple[list[str], str]:
    """The lines of `list BOX` and its footer."""
    live = set(live)
    everyone = [r for rs in g.feeds.values() for r in rs if r.box is box]
    whole = [r for r in everyone if not r.tag]
    claims: dict[str, list[_boxes.Routed]] = {}
    for r in everyone:
        if r.tag:
            claims.setdefault(r.tag, []).append(r)

    def who(r: _boxes.Routed) -> tuple[str, str]:
        note = f"{r.entry.kind} subscription {_when(r.entry.path)}"
        if r.entry.kind == "session" and listening_pid(r.entry.subscriber, env):
            note += ", listening"
        return _short(r.entry.subscriber), note

    title = box.name + (f"+{tag}" if tag else "")
    body: list[tuple[str, str]] = []
    claimed = ""
    if tag and claims.get(tag):
        body += [who(r) for r in claims[tag]]
        claimed = f"; +{tag} is CLAIMED, so {box.name}'s own subscribers do not get it"
    elif tag:
        for r in whole:
            label, note = who(r)
            body.append((label, f"{note} to {box.name} (no one claims +{tag}: it falls through)"))
    else:
        body += [who(r) for r in whole]
    subs = len(body)
    if box.kind == "seat" and not tag:
        for sid in seat_sessions(box.name, live, root, env):
            note = "session in this seat: the seat row copies here (D12)"
            if listening_pid(sid, env):
                note += ", listening"
            body.append((_short(sid), note))
            subs += 1
    heard = listeners(box.path, live=live, boxes=g.boxes, env=env, ps=ps)
    for li in heard:
        label = _short(li.owner) if li.owner else f"pid {li.pid}"
        arg = f" {li.token}" if li.token else ""
        body.append((label, f"LISTENER, not a subscription: `pager listen{arg}` (pid {li.pid})"))
    lines: list[tuple[str, str]] = [(title, f"{box.kind} box" + (f", tag +{tag}" if tag else ""))]
    others = {} if tag else claims
    for i, (label, note) in enumerate(body):
        last = i == len(body) - 1 and not others
        lines.append((("└── " if last else "├── ") + label, note))
    for i, t in enumerate(sorted(others)):
        last = i == len(others) - 1
        lines.append((("└── " if last else "├── ") + f"+{t}",
                      "claimed: only these get +" + t + ", not the subscribers above"))
        for j, r in enumerate(others[t]):
            label, note = who(r)
            lines.append((("    " if last else "│   ")
                          + ("└── " if j == len(others[t]) - 1 else "├── ") + label, note))
    width = max(len(label) for label, _ in lines) + 3
    out = [f"{label:<{width}}{note}".rstrip() for label, note in lines]
    foot = f"-- {subs} hear{'s' if subs == 1 else ''} it{claimed}"
    n_claims = sum(len(v) for v in others.values())
    if n_claims:
        foot += f"; {n_claims} more on {len(others)} claimed tag{'s' if len(others) != 1 else ''}"
    foot += (f"; {len(heard)} live listener{'s' if len(heard) != 1 else ''}" if heard
             else "; no live listener on it")
    return out, foot


__all__ = ["DEFAULT_DEPTH", "Graph", "LOOP", "Listener", "Node", "Tree", "find_box", "footer",
           "graph", "hearers", "listeners", "listening_pid", "render", "seat_sessions", "tree"]
