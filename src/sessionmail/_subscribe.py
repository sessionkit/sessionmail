"""`message subscription add|remove`: who may write which folder, and the write.

`openspec/changes/message-subscriptions`, tasks 3.2, designs D1, D3.1 and D5,
specs/message "Only the verbs write a subscription, and every write
rebuilds" and "Seat subscriptions, and anyone else's, are for a privileged
seat" (`operator.privileged` in `~/.sessionkit/tools/settings/session.json`, read by
`_operator`).

- **The session by default.** `add BOX` sets `subscriptions.<box>` in
  `~/.session/sessions/<sid>/settings/mail.json`, the caller's own
  session's layer (session-settings 3.3b; it was a file in `<home>/
  sessions/<sid>/subscriptions/`), from the environment. It dies with the
  session (D2's liveness rule).
- **`--seat` and `--for` are a privileged seat's** (`operator.privileged`).
  Any other caller is refused with exit 2 and the route: a seat subscription
  is REQUESTED by mail, from a privileged seat (D5). `--for WHO --seat` names seat WHO;
  `--for WHO` alone names session WHO, which must be one this host knows
  (its session dir, `~/.session/sessions/WHO/`), so a seat name is never
  quietly taken for a session.
- **A seat's own address is that seat's to grant** (D5). For
  anyone but a privileged seat, a BOX that is a flat box (a seat's or a
  session's own, never a store's) or a declared plus seat
  (`<home>/seats/<box>/`, e.g. `sessionmail+web-12.foreman`) is refused
  unless it is the caller's own, or the BASE of the caller's own plus
  address (`sessionvoice+<role>` owns `sessionvoice`, wherever that
  box was born; RFC 5233 by `_seat.split_plus`, no role grammar). Project
  and store boxes, and any tag of them nobody sits at, are open to every
  session.
- **Every D5 refusal carries its repair**: the exact
  `message subscription` line a privileged seat runs, with the caller's
  session or seat filled in, and the host it must run ON. A grant is
  HOST-LOCAL: the map it writes is this host's, so one made on any other
  host changes nothing here.
- **Add refuses what the generator would skip**: the codec
  (`_subscription.file_name`), `_boxes.refusal` (the one mirror of
  `sub_address`), a `/` name whose store has no marker, and a box that does
  not resolve, tried as the generator tries it: the box, then its base.
  An owner folder postfix would not keep is refused too (the generator
  skips it, `vmailbox-gen` subscription_records).
- **An existing subscription is a no-op** and does not rebuild. Every write
  rebuilds the alias map (`--valias-only`) before the verb returns, and
  prints the row postfix now holds for the box.

All seats on a host run as one uid, so the file system enforces none of
this: the verb is the only gate (D5).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import sessiontools

from . import _boxes, _host, _layer, _maildir, _operator, _seat, _subscription
from ._voice import KEEPER

#: Who may write a seat subscription, or anyone's (D5), is config's to say:
#: `_operator.read()`, carried on `Caller.op`. The door for
#: everyone else is `Caller.route()`, i.e. `_operator.route`.
ROUTE_TOPIC = "subscription"

#: What `vmailbox-gen` keeps in an owner folder's name: `_boxes.OWNER_CHARS`,
#: the one copy `list` reads too. Anything else, it skips the whole folder.
_OWNER_CHARS = _boxes.OWNER_CHARS


class Denied(Exception):
    """A refusal: exit 2, and the message says why and what to do instead.

    Not `_sender.Refused`: its KINDS are a SEND's vocabulary (agents-send-
    door, with `sent=`), and no mail is sent here. The non-send verbs
    (`path`, `box path`) refuse the same way: one line, exit 2."""


@dataclass(frozen=True)
class Caller:
    """Who is asking: the seat (if any) and the session (if any), and the
    operator config read when it was asked (`caller()` reads it ONCE). A
    Caller built by hand defaults to nothing configured: unprivileged."""

    seat: _seat.Seat | None
    session: str
    op: _operator.Operator = _operator.NOTHING

    @property
    def privileged(self) -> bool:
        return self.seat is not None and self.op.is_privileged(self.seat.address)

    @property
    def may(self) -> str:
        """Who a privileged verb is for, in a refusal (`_operator.who_may`)."""
        return _operator.who_may(self.op)

    def route(self, topic: str = ROUTE_TOPIC) -> str:
        """The request-by-mail door, in a refusal (`_operator.route`)."""
        return _operator.route(self.op, topic)

    @property
    def who(self) -> str:
        if self.seat is not None:
            return self.seat.address
        return self.session[:8] or "no seat"

    def owns(self, address: str) -> bool:
        """True when ADDRESS is the caller's own: its session, its seat, or
        the BASE of its seat's plus address. A plus seat
        `<base>+<ext>` is a sub-address OF `<base>` (RFC 5233), and the base
        is where its mail lands when it has no box of its own, so owning it
        grants nothing new; D9 says a plus seat hears its base BY
        subscription. Grammar only (`_seat.split_plus`): every plus seat
        owns its base, whatever the extension means to whoever minted it."""
        address = address.lower()
        own = {self.session.lower()} if self.session else set()
        if self.seat is not None:
            own.add(self.seat.address)
            parts = _seat.split_plus(self.seat.address)
            if parts:
                own.add(parts[0])
        return address in own


@dataclass(frozen=True)
class Target:
    """Whose subscriptions: an agent's folder, or (session-settings 3.2 and
    3.3b) a seat's or a session's `mail.json` map, when `map` is True and
    FOLDER is that layer's `settings/` dir."""

    kind: str           # "seat" or "session"
    owner: str          # the seat's address, or the session id
    folder: Path        # .../subscriptions, or ~/.session/{seats,sessions}/<x>/settings
    map: bool = False   # a map in the new tree (a seat's or a session's; not an agent's)

    @property
    def file(self) -> str:
        """The one file of the map this target's verbs write, under FOLDER:
        a session's `mail.d/70-live.json` (task 3.4: it folds after the
        birth's `50-env.json`, so a verb's `false` wins), a seat's `mail.json`."""
        return _layer.LIVE if self.kind == "session" else _layer.FILE

    @property
    def path_for(self) -> str:
        """Where this target's subscriptions are written, for a message."""
        return str(self.folder / self.file) if self.map else str(self.folder)

    @property
    def label(self) -> str:
        return f"{self.kind} {self.owner if self.kind == 'seat' else self.owner[:8]}"


def caller(env: dict[str, str] | None = None) -> Caller:
    try:
        seat = _seat.resolve(env)
    except _seat.NoIdentity:
        seat = None
    return Caller(seat=seat, session=sessiontools.session_id(env) or "", op=_operator.read())


def _check_owner(owner: str, kind: str) -> None:
    allowed = _OWNER_CHARS | ({"+"} if kind == "seat" else set())
    if not owner or not set(owner) <= allowed or owner.startswith("."):
        raise Denied(f"{kind} {owner!r}: not a name postfix would keep, so the map "
                      "would skip its whole folder; nothing written")


def repair(who: Caller, box: str | None = None, *, verb: str | None = "add",
           seat: bool = False, for_: str | None = None) -> str:
    """The repair a D5 refusal carries:
    the exact line a privileged seat runs, with WHO filled in, and the host
    it must run ON, since a grant is HOST-LOCAL.

    FOR_ names whose subscriptions, as the caller asked; without it, the
    caller's own: its seat with SEAT, else its session (a session grant
    dies with the session, D2), else its seat. BOX or VERB unknown (the
    remove path's gate is asked before the box) reads `BOX` and
    `add|remove`: the shape, never a guess."""
    if for_ is None:
        if seat:
            for_ = who.seat.address if who.seat is not None else "SEAT"
        elif who.session:
            for_ = who.session
        elif who.seat is not None:
            seat, for_ = True, who.seat.address
        else:
            for_ = "SESSION-ID"
    line = " ".join(["message subscription", verb or "add|remove", box or "BOX"]
                    + (["--seat"] if seat else []) + ["--for", for_])
    host = _seat.short_host()
    return (f"the repair, which a privileged seat runs ON {host}: {line} (a grant is HOST-LOCAL: "
            f"it writes {host}'s map, and one made on any other host changes nothing here)")


def target(who: Caller, *, seat: bool, for_: str | None,
           env: dict[str, str] | None = None, box: str | None = None,
           verb: str | None = None) -> Target:
    """The folder a write goes to, after D5's gates on WHOSE it is. BOX and
    VERB, when the caller knows them, make the refusal's repair exact."""
    home = _seat.home(env)
    if for_ is not None and not who.privileged:
        raise Denied(f"--for is for {who.may} (you are {who.who}): "
                      f"to change another's subscriptions, {who.route()}; "
                      + repair(who, box, verb=verb, seat=seat, for_=for_))
    if seat and not who.privileged:
        raise Denied(f"a seat subscription is for {who.may} (you are {who.who}): "
                      "it outlives you and the next seat inherits it. "
                      f"Without --seat this changes only your session. {who.route()}; "
                      + repair(who, box, verb=verb, seat=True))
    if seat:
        owner = _seat.fold_address(for_) if for_ else (who.seat.address if who.seat else "")
        _check_owner(owner, "seat")
        if not _layer.is_agent_name(owner):
            # session-settings 3.2: a SEAT's subscriptions are its map in the
            # new tree. An agent's bare name keeps its old folder until 3.8.
            try:
                return Target("seat", owner, _layer.settings_dir(owner, env), map=True)
            except _layer.LayerError as exc:
                raise Denied(f"{exc}; nothing written") from None
        return Target("seat", owner, home / _subscription.SEATS_DIR / owner / _subscription.FOLDER)
    if for_ is not None:
        owner = for_.strip()
        try:
            known = (bool(owner) and "/" not in owner and owner not in (".", "..")
                     and _layer.session_settings_dir(owner, env).parent.is_dir())
            where = _layer.session_settings_dir("-", env).parent.parent
        except _layer.LayerError as exc:
            raise Denied(f"{exc}; nothing written") from None
        if not known:
            raise Denied(f"--for {for_!r} without --seat names a SESSION, and this host "
                          f"has no session {for_!r} ({where}); "
                          f"for seat {for_!r}, add --seat")
    else:
        owner = who.session
        if not owner:
            raise Denied("no session id in the environment (looked for "
                          + ", ".join(_seat.SESSION_VARS)
                          + "): add and remove change THIS session's subscriptions")
    _check_owner(owner, "session")
    # session-settings 3.3b: a SESSION's subscriptions are its layer's map.
    try:
        return Target("session", owner, _layer.session_settings_dir(owner, env), map=True)
    except _layer.LayerError as exc:
        raise Denied(f"{exc}; nothing written") from None


def _in_tree(name: str, env: dict[str, str] | None) -> Path | None:
    """The maildir the GENERATOR would map bare NAME to from the tree as it
    is now, never the installed map: `<root>/<store>/<name>/new` under a
    store dir carrying `_boxes.STORE_MARKER` (`generate()`'s `nested` rows,
    `vmailbox-gen:348-387`, kept by `sub_resolve`'s `new/` test, `:595`).
    Names compare lower-cased, as the generator folds them. The first store
    in sorted order wins; None when no store holds it."""
    root = _maildir.root(env)
    want = name.lower()
    try:
        stores = sorted(d for d in root.iterdir()
                        if not d.name.startswith(".") and (d / _boxes.STORE_MARKER).exists())
    except OSError:
        return None
    for store in stores:
        try:
            kids = sorted(store.iterdir())
        except OSError:
            continue
        for kid in kids:
            if kid.name.lower() == want and not kid.name.startswith(".") \
                    and (kid / "new").is_dir():
                return kid
    return None


def _resolve(box: str, env: dict[str, str] | None, *, tree: bool = False) -> Path | None:
    """The maildir BOX lands in, as the generator decides it: the box, else
    (a plus form) its base - `sub_resolve`'s `(box in ok) || (base in ok)`.
    A `/` name must name a project of a MARKED store, as `sub_address` asks.

    TREE (the birth's gate only): a bare name the
    installed map does not deliver yet falls back on the generator's own
    tree test (`_in_tree`), so a box made on disk before the next regen is
    not refused for a stale map. The birth always regenerates (or rebuilds)
    after its gate, so that generator routes it. A name with no maildir
    anywhere still resolves to None."""
    tries = [box]
    if "+" in box and "/" not in box:
        tries.append(box.partition("+")[0])
    for name in tries:
        try:
            found = _maildir.resolve_box(name, env)[0]
        except _maildir.NoSuchBox:
            # The generator's names never hold `+` or `/` (`san()` drops
            # them), so only a plain bare name can be found in the tree.
            if tree and "/" not in name and "+" not in name:
                found_in_tree = _in_tree(name, env)
                if found_in_tree is not None:
                    return found_in_tree
            continue
        if "/" in name and not (found.parent / _boxes.STORE_MARKER).exists():
            return None
        return found
    return None


def _seat_owned(box: str, found: Path, env: dict[str, str] | None) -> str:
    """The seat (or session) whose OWN address BOX is, or "" for a project,
    a store, or a tag nobody sits at (D5)."""
    if "+" in box and "/" not in box:
        declared = _seat.fold_address(box)
        if (_seat.home(env) / _subscription.SEATS_DIR / declared).is_dir():
            return declared                 # a plus seat: sessionmail+web-12.foreman
    root = _maildir.root(env)
    try:
        flat = found.resolve().parent == root.resolve()
    except OSError:
        flat = found.parent == root
    if flat and not (found / _boxes.STORE_MARKER).exists():
        return found.name                   # a seat's box, or a session's
    return ""


def check_box(box: str, who: Caller, env: dict[str, str] | None = None, *,
              tree: bool = False) -> str:
    """The stored file name for BOX, or Denied: the map would never copy it,
    it does not resolve, or it is another seat's to grant. TREE is the
    birth's gate (`_birth.birth_subscriptions`): a box on disk that the
    installed map does not deliver YET resolves as the generator would
    (`_resolve`). `message subscription add` leaves it False."""
    try:
        stored = _subscription.file_name(box)
    except _subscription.BadBox as exc:
        raise Denied(f"not a box: {exc}") from None
    why = _boxes.refusal(box)
    if why:
        raise Denied(f"{box}: {why}; the map would never copy it, nothing written")
    found = _resolve(box, env, tree=tree)
    if found is None:
        raise Denied(f"no such box {box!r}: `message path {box}` finds no maildir for it "
                      "or its base; nothing written")
    owner = _seat_owned(box, found, env)
    if owner and not who.privileged and not who.owns(owner):
        raise Denied(f"{box} is {owner}'s own address, and a seat's mail is that seat's "
                      f"to grant (you are {who.who}); {who.route()}; {repair(who, box)}")
    return stored


def note(who: Caller, argv: str, why: str | None) -> str:
    """The file's body (D1): who, when, the command; and why, if given."""
    stamp = time.strftime("%H:%M %a %Y-%m-%d")
    by = who.seat.address if who.seat else (who.session[:8] or "unknown")
    text = f"# added by {by} {stamp} ({argv})\n"
    if why:
        text += f"# why: {' '.join(why.split())}\n"
    return text


def write(path: Path, body: str) -> None:
    """Atomically: a hidden temp name (the reader skips `.` files), then
    rename. Never over something that is not a file. A failure at either
    step takes the temp file with it, so a refusal leaves the folder as it
    found it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def removable(path: Path) -> None:
    """Refused unless PATH is a subscription these verbs could have written:
    a regular file, not an old saved query (the pager's, which share the
    folder until tasks 6.3), never a line of `valias.static`."""
    if path.is_symlink() or not path.is_file():
        raise Denied(f"{path}: not a regular file, not a subscription; left alone")
    why = _subscription._saved_query(path)
    if why:
        raise Denied(f"{path}: {why}; left alone (tasks 6.3 retires those)")


def existing(path: Path) -> None:
    """`add` found PATH already there: Denied unless the generator ROUTES it,
    since "already subscribed" is a claim about the map. So the generator's
    own test, not `removable`'s: a link to a plain file follows through (bash
    `-f`, Python `is_file`) and IS routed; a folder, a fifo, a dangling link,
    an old saved query or an unreadable file holds the name but is skipped,
    and overwriting it is tasks 6.3's (no agent deletes those). On one
    measured host, `seats/alice/subscriptions/atomnote` is a saved query, and
    tasks 6.1 adds exactly that name."""
    tail = ("; it holds the name, but the map does not copy it, so add neither "
            "counts it nor overwrites it (tasks 6.3 retires those)")
    if not path.is_file():
        raise Denied(f"{path}: not a regular file (a folder, a fifo, a dangling link?){tail}")
    why = _subscription._saved_query(path)
    if why:
        raise Denied(f"{path}: {why}{tail}")


@dataclass(frozen=True)
class Added:
    """What `add` did: the folder it wrote to, the file, and whether it wrote
    (False: already subscribed, nothing changed, so no rebuild is owed)."""

    target: Target
    path: Path
    written: bool


def device_box(box: str) -> bool:
    """pager-genie 3.29: BOX names the keeper box (`heard`) or a device on
    it, a plus address `heard+<device>` (ONE keeper box per host, a device
    is its plus address). By NAME, so the answer does not depend on whether the box is
    there yet: the bare name, a `<store>/` form, any case, any tag, and an
    `@host` left on are all the same box. The `heard` name is 3.1's ONE
    named value, `_voice.KEEPER`, never a second spelling of it."""
    name = box.strip().lower().rsplit("/", 1)[-1].split("@", 1)[0]
    return name.partition("+")[0] == KEEPER


def add(box: str, who: Caller, *, seat: bool = False, for_: str | None = None,
        why: str | None = None, argv: str | None = None,
        env: dict[str, str] | None = None) -> Added:
    """THE add path: D5's gates on whose folder, the box gates, the no-op, and
    the write. It does NOT rebuild: the caller owes the rebuild when
    `written`. `message subscription add` calls it (the pager's
    SESSIONMAIL_SUBSCRIBE did until task 3.4 moved it to the launcher's birth).
    Denied for every refusal, before anything is written."""
    if seat and device_box(box):
        # pager-genie 3.29 (the PROPOSED reading of N8: a device subscription
        # is per SESSION and dies at handover). Asked FIRST, before D5's gate
        # on who may write a seat folder, so the answer is the same for every
        # caller - privileged seats included, although `--seat` is otherwise
        # theirs - and it names N8 rather than the route to a privileged
        # seat, which could not grant it either.
        raise Denied(f"{box} is the {KEEPER} box or a device on it, and a device "
                     "subscription is per SESSION (N8): it ends with the session, so a "
                     "successor never inherits its predecessor's turns. A seat "
                     "subscription would outlive every session; without --seat this "
                     "session subscribes. Nothing written")
    tgt = target(who, seat=seat, for_=for_, env=env, box=box, verb="add")
    stored = check_box(box, who, env)
    if argv is None:
        argv = " ".join(["message subscription add", box]
                        + (["--seat"] if seat else []) + ([f"--for {for_}"] if for_ else []))
    if tgt.map:
        return _add_to_map(tgt, box, who, argv=argv, why=why)
    path = tgt.folder / stored
    if path.exists() or path.is_symlink():
        existing(path)
        return Added(tgt, path, False)
    write(path, note(who, argv, why))
    return Added(tgt, path, True)



# ── a seat's or a session's map (session-settings 3.2, 3.3b) ────────────────

def _map_read(tgt: Target) -> tuple[dict, dict[str, str]]:
    try:
        return _layer.read(tgt.folder, tgt.kind)
    except _layer.LayerError as exc:
        raise Denied(f"{exc}; nothing changed") from None


def _rank(tgt: Target, path: Path) -> tuple:
    """Where PATH folds in TGT's layer, as sessiontools' `layer_files` orders
    it: `mail.json` first, then `mail.d/*.json` by name; any other path last."""
    if path == tgt.folder / _layer.FILE:
        return (0, "")
    if path.parent == tgt.folder / f"{_layer.TOOL}.d":
        return (1, path.name)
    return (2, str(path))


def _not_ours(tgt: Target, box: str, origins: dict[str, str]) -> str | None:
    """The file that sets BOX when it folds AFTER the file these verbs write
    (a hand-made `mail.d/90-*`, which wins over them), else None. A file that
    folds before it - `mail.json`, or a session's birth `mail.d/50-env.json`
    (task 3.4) - is outranked by what the verbs write, so it is theirs to
    override, never to edit (the birth's file keeps saying what the
    environment said)."""
    origin = origins.get(f"{_layer.SUBSCRIPTIONS}.{box}")
    if origin and _rank(tgt, Path(origin)) > _rank(tgt, tgt.folder / tgt.file):
        return origin
    return None


def _add_to_map(tgt: Target, box: str, who: Caller, *, argv: str,
                why: str | None) -> Added:
    path = tgt.folder / tgt.file
    values, origins = _map_read(tgt)
    if box in _layer.boxes(values):
        return Added(tgt, Path(origins.get(f"{_layer.SUBSCRIPTIONS}.{box}") or path), False)
    other = _not_ours(tgt, box, origins)
    if other:
        raise Denied(f"{box} is set to {values[_layer.SUBSCRIPTIONS][box]!r} in {other}, "
                     "a file these verbs do not write; nothing written")
    try:
        _layer.set_entry(tgt.folder, _layer.SUBSCRIPTIONS, box, True, file=tgt.file)
    except _layer.LayerError as exc:
        # Name the box, NOT written, the path and the OS's reason.
        raise Denied(f"{box} NOT written - {exc}") from None
    _layer.log(tgt.folder.parent, f"{_layer.SUBSCRIPTIONS}.{box}", True,
               by=who.who, argv=argv, why=why)
    return Added(tgt, path, True)


def remove_from_map(tgt: Target, box: str, who: Caller, *, argv: str) -> bool:
    """Take BOX out of a seat's or a session's map. True if a file changed;
    False if the map does not subscribe to it. Denied when a file that wins
    over the verbs' sets it, or the write fails.

    The key goes from `mail.json` (the conversion's, and 3.3b's verbs') and
    from the verbs' own file. If a lower file still says `true` - a session's
    birth `mail.d/50-env.json`, what SESSIONMAIL_SUBSCRIBE said (task 3.4) -
    the verbs' file says `false`, which folds after it and wins: a
    subscription removed stays removed (specs/settings, lines 125-132)."""
    values, origins = _map_read(tgt)
    if box not in _layer.boxes(values):
        return False
    other = _not_ours(tgt, box, origins)
    if other:
        raise Denied(f"{box} is set in {other}, a file these verbs do not write; left alone")
    changed, wrote = False, None
    try:
        for name in dict.fromkeys((_layer.FILE, tgt.file)):
            changed = _layer.set_entry(tgt.folder, _layer.SUBSCRIPTIONS, box, None,
                                       file=name) or changed
        if box in _layer.boxes(_map_read(tgt)[0]):
            changed = _layer.set_entry(tgt.folder, _layer.SUBSCRIPTIONS, box, False,
                                       file=tgt.file) or changed
            wrote = False
    except _layer.LayerError as exc:
        raise Denied(f"{box} NOT removed - {exc}") from None
    if changed:
        _layer.log(tgt.folder.parent, f"{_layer.SUBSCRIPTIONS}.{box}", wrote,
                   by=who.who, argv=argv)
    return changed


def address_key(box: str) -> str:
    """The local part postfix keys BOX's row on: a project by its own name."""
    return box.rsplit("/", 1)[-1].lower()


def row(box: str) -> str:
    """The row postfix holds NOW for BOX, asked of `virtual_alias_maps`."""
    host = _host.live()  # key as vmailbox-gen WRITES (hostname -s), not by NAME
    key = f"{address_key(box)}@{host}"
    maps = _seat._postfix_maps("virtual_alias_maps")
    if not maps:
        return f"row: postfix could not be asked for {key} (no virtual_alias_maps)"
    hit = _seat._postmap_lookup(key, maps)
    if hit is None:
        return f"row: none for {key}: its mail reaches its own box only"
    return f"row: {key} -> {hit[1]}"


def rebuild(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """`vmailbox-gen --valias-only`, before the verb returns (D3.1)."""
    return _seat.regen_valias(_maildir.root(env), env)


__all__ = ["Added", "Caller", "ROUTE_TOPIC", "Denied", "Target", "add", "address_key",
           "caller", "check_box", "existing", "note", "rebuild", "removable", "repair", "row",
           "target", "write"]
