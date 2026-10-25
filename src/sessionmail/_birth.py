"""A session's box: born, subscribed, declared and rung - ONE path.

The birth that
`pager listen` did inline (`pager.py:931-1009 @ e740a9b`) is moved here
WHOLE and in its order, so the listen and the SessionStart hook
cannot drift into two copies of the birth/declare/doorbell/regen
order. build-001 is a PURE MOVE: what `pager listen` prints and does is
byte-for-byte what it was, and its tests pass unedited.

The order is the design, and each step's reason is kept beside it below:

1. birth     - `<root>/<session>/{cur,new,tmp}` (D9)
2. subscribe - the launcher's SESSIONMAIL_SUBSCRIBE birth file, reported
               by the call that births (D4, task 3.4)
3. declare   - the seat's plus address, BEFORE the doorbell (D1)
4. doorbell  - both maps measured; the generator run WHOLE when the box was
               just born or a row is missing, then measured again (F21, D9)
5. rebuild   - a seatless birth that subscribed owes one `--valias-only`

Every `_seat` call goes through the module attribute (`_seat.regen_maps`,
never `from ._seat import regen_maps`): `tests/test_task_address_doorbell.py`
monkeypatches `_seat.regen_maps` to prove step 3 comes before step 4, and a
direct import would let that test pass vacuously.

This module lives apart from `pager.py` and `_seat.py` on purpose: other
changes edit those two files, and a new file conflicts with none of them.

build-002 adds the SessionStart caller's two needs, and nothing else:

- `regen="detach"`: the generator is started DETACHED (`python -m
  sessionmail._birth --regen ...`, its own session, stdio on /dev/null) and
  the call returns at once - a SessionStart hook never waits out the
  generator's budget. `pager listen` keeps `"wait"`.
- `record_source`: a birth by the hook writes the BORN RECORD,
  `<session dir>/mail/born` (`_seat.session_root`) - `<iso time> <source> regen=<what>` - and
  the detached generator appends its one line to it when it finishes. It
  is the receipt that the box was born at session start, and what `pager listen`'s banner reads.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping

# `_maildir` is imported where it is used, not here: pager.py imports this
# module at its top, and every pager hook would otherwise load `_maildir` and
# `email.*` before deciding anything (measured at b727752).
from . import _seat

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._seat import Seat

#: Design D4's variable: box names an agent MD sets with `variables:`. Since task 3.4
#: the LAUNCHER's birth applies it, not the listen: `mail.schema.json` names
#: it with `x-env`, and sessiontools writes `<session dir>/settings/mail.d/
#: 50-env.json` once, before the session runs. The birth only reports it.
#: (Moved from pager.py; `pager.SUBSCRIBE_VAR` re-exports it.)
SUBSCRIBE_VAR = "SESSIONMAIL_SUBSCRIBE"

#: The born record's file name, under the session's mail dir
#: (`_seat.session_root`). Line 1: `<iso time> <source>
#: regen=<detached|skipped|...>`, written by the birth; line 2, when there is
#: one: the detached generator's line.
BORN_FILE = "born"

#: What a generator line says on a mail-drop host, suffix or none: the drop
#: builds no maps, so no generator runs there (`_seat._run_generator`'s own
#: words for it; `tests/test_birth.py` holds the two equal).
DROP_REGEN = "generator not run: the carrier is the mail drop; it reads the maps per send"


def _on_drop(env: Mapping[str, str] | None) -> bool:
    """Whether the carrier is the mail drop. Decides what a generator line
    SAYS, never what runs."""
    from . import _settings
    return _settings.carrier(env).kind == "drop"


#: Who births when the caller does not say: the subscription journal's `argv`.
VIA_LISTEN = "pager listen"


def birth_subscriptions(session: str, env: Mapping[str, str] | None = None,
                        via: str = VIA_LISTEN) -> tuple[list[str], bool]:
    """What the launcher's birth subscribed SESSION to (task 3.4): one banner
    line per box its `mail.d/50-env.json` sets `true`, and whether one is
    routed - the caller owes one rebuild when no doorbell runs the generator.

    The birth file is written by the launcher, not through the add path, so
    each box meets the add path's gates HERE (`_subscribe.check_box`: the map
    would copy it, it resolves, and a seat's own address is that seat's to
    grant, D5). The generator does not hold D5 (it is gated at the verb), so a
    refused box is turned OFF in the verbs' `mail.d/70-live.json`, which folds
    after the birth file and wins; the birth file still says what the
    environment said. Nothing else is written: a box removed stays removed.

    When SUBSCRIBE_VAR is set and there is NO birth file - a session not
    launched by `session start`, or an installed schema without the `x-env`
    - one line says the variable was NOT applied, never silence.

    Moved here from pager.py (it replaced D4's
    `subscribe_at_birth` on master, task 3.4), so the listen and the
    SessionStart hook keep ONE birth path (R5). VIA names the command that
    births, for the journal of a refused box turned off (`pager listen`, or
    `pager hook start`)."""
    from . import _layer, _subscribe
    env = os.environ if env is None else env
    try:
        path = _layer.session_settings_dir(session, env) / _layer.ENV
    except _layer.LayerError as exc:
        return [f"  subscribe  : the session's layer cannot be found - {exc}"], False
    if not path.is_file():
        if (env.get(SUBSCRIBE_VAR) or "").strip():
            return [f"  subscribe  : ${SUBSCRIBE_VAR} is set and NOT applied - the launcher "
                    f"wrote no birth file ({path}); `session start` writes it, or "
                    "`message subscription add BOX`"], False
        return [], False
    try:
        boxes = _layer.boxes(_layer._load(path))
    except _layer.LayerError as exc:
        return [f"  subscribe  : the birth file cannot be read - {exc}"], False
    who = _subscribe.caller(env)
    lines: list[str] = []
    routed = False
    for box in boxes:
        try:
            _subscribe.check_box(box, who, env, tree=True)
        except _subscribe.Denied as exc:
            try:
                tgt = _subscribe.target(who, seat=False, for_=None, env=env)
                _layer.set_entry(tgt.folder, _layer.SUBSCRIPTIONS, box, False, file=tgt.file)
                _layer.log(tgt.folder.parent, f"{_layer.SUBSCRIPTIONS}.{box}", False,
                           by=via, argv=f"{via}, ${SUBSCRIBE_VAR}",
                           why=f"refused: {exc}")
            except (_subscribe.Denied, _layer.LayerError, OSError) as off:
                lines.append(f"  subscribe  : {box} REFUSED (from ${SUBSCRIBE_VAR}) - {exc}; "
                             f"and NOT turned off - {off}: the generator may still route it")
                continue
            lines.append(f"  subscribe  : {box} REFUSED (from ${SUBSCRIBE_VAR}) - {exc}; "
                         f"turned off in {tgt.path_for}")
            continue
        routed = True
        lines.append(f"  subscribe  : {box} (from ${SUBSCRIBE_VAR}, at the launcher's birth) "
                     f"- {path}")
    return lines, routed


@dataclass
class Birth:
    """What `ensure_session_box` did, for its caller to print or say.

    The banner lines are the ones `pager listen` printed before the move,
    unchanged; the caller decides where they go."""

    box: Path | None = None        # <root>/<session>; None with no session id
    born: bool = False             # this call made cur/new/tmp
    error: str | None = None       # the birth was refused - one line; nothing after it ran
    sub_lines: list[str] = field(default_factory=list)   # "  subscribe  : ..." / "  map        : ..."
    bell_lines: list[str] = field(default_factory=list)  # "  address    : ..." / "  doorbell   : ..."
    live: bool | None = None       # the doorbell's LAST measurement (None: not measured / no seat)
    regen: str = ""                # "" (did not run) | "ran" | "detached" (started, not waited for)
    regen_line: str = ""           # the generator's one line when it ran / was started
    record: Path | None = None     # the born record, when this call wrote one
    suffix_unknown: bool = False   # no MagicDNS suffix, so no regen ran (asked by "wait" only)


def born_record(session: str, env: Mapping[str, str] | None = None) -> Path | None:
    """`<session dir>/mail/born` (`_seat.session_root`), or None with no session id. Whether
    it exists is the question "was this box born at session start?"."""
    root = _seat.session_root(session, env)
    return None if root is None else root / BORN_FILE


@dataclass
class BornRecord:
    """The born record as `pager listen`'s banner reads it."""
    when: str          # "HH:MM:SS" local time of the birth, or the raw stamp if unparseable
    source: str        # SessionStart's source: startup | resume | compact | clear | "-"
    regen: str         # what the hook did about the generator: ran | detached | skipped | failed
    line: str = ""     # the detached generator's own line, once it has finished ("" until then)


def read_born(session: str, env: Mapping[str, str] | None = None) -> BornRecord | None:
    """The born record of SESSION, or None when there is none (the box was
    born by a `pager listen`, or by nobody) or it cannot be read. Line 1 is
    `<iso time> <source> regen=<what>`; line 2, when the detached generator
    has finished, is its one line. Never raises: a banner is not a reason
    for a listen to fail."""
    path = born_record(session, env)
    if path is None:
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    parts = lines[0].split()
    if len(parts) < 3 or not parts[2].startswith("regen="):
        return None
    try:
        when = datetime.fromisoformat(parts[0]).strftime("%H:%M:%S")
    except ValueError:
        when = parts[0]
    return BornRecord(when=when, source=parts[1], regen=parts[2][len("regen="):],
                      line=lines[1].strip() if len(lines) > 1 else "")


def _write_born(session: str, env: Mapping[str, str] | None, source: str, regen: str) -> Path | None:
    """Line 1 of the born record, whole (temp + rename), BEFORE any detached
    generator can append line 2. None when it could not be written: a record
    is a receipt, never a reason for a birth to fail."""
    path = born_record(session, env)
    if path is None:
        return None
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{BORN_FILE}.{os.getpid()}")
        tmp.write_text(f"{stamp} {source or '-'} regen={regen or 'skipped'}\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return None
    return path


@dataclass
class SubCheck:
    """SESSION's own subscriptions, measured in the alias map."""
    boxes: list[str] = field(default_factory=list)     # each row key the generator routes
    missing: list[str] = field(default_factory=list)   # the keys whose row does not copy here
    why: str = ""                                      # not "" when the map was not measured

    def line(self) -> str:
        """`subscriptions: N/N in map`, `NOT in map: <box> ...`, or why not measured."""
        n = len(self.boxes)
        if self.why:
            return f"subscriptions: map not measured ({self.why})"
        if self.missing:
            return (f"subscriptions: {n - len(self.missing)}/{n} in map; "
                    f"NOT in map: {', '.join(self.missing)}")
        return f"subscriptions: {n}/{n} in map"


def subscription_check(session: str, env: Mapping[str, str] | None = None) -> SubCheck:
    """Is each of SESSION's own subscriptions in the alias map postfix reads?
    The rows asked are the ones the generator ROUTES for the session
    (`_boxes.routed`, the predicate `subscription list` shares), so a box that
    does not resolve is never reported missing. Read-only: no generator runs.

    Why: every generator run rebuilds the whole alias map, and a run that
    missed this session dropped its rows while exiting 0. Nothing measured a
    subscription row after the birth, so a seat stayed deaf while
    `subscription list` said subscribed. This is that measure."""
    from . import _boxes, _maildir, _subscription
    env_ = os.environ if env is None else env
    check = SubCheck()
    if session in ("", "-"):
        return check
    root = _maildir.root(env_)
    try:
        scan = _subscription.scan(live={session}, env=dict(env_))
        routed = _boxes.routed(_boxes.walk(root), scan)
    except Exception as exc:  # noqa: BLE001 - a measure never fails a birth
        check.why = f"the session's subscriptions could not be read: {exc}"
        return check
    keys = sorted({r.key for r in routed
                   if r.entry.kind == "session" and r.entry.subscriber.lower() == session.lower()})
    check.boxes = keys
    if not keys:
        return check
    states, why = _seat.rows_in_map([(k, session) for k in keys], dict(env_))
    if states is None:
        check.why = why
        return check
    check.missing = [k for k, ok in zip(keys, states) if not ok]
    return check


def detach_regen(root: Path, env: Mapping[str, str] | None = None,
                 record: Path | None = None, session: str = "") -> tuple[bool, str]:
    """Start the generator in a DETACHED child and return at once: (started,
    one line). The child is `python -m sessionmail._birth --regen ROOT
    [--record PATH]`: its own session (`start_new_session`), stdio on
    /dev/null and no inherited fds, so a SessionStart hook's caller never
    waits for it (measured: a detached grandchild did not
    delay `claude -p`). It runs `_seat.regen_maps` - bounded, TERM never
    KILL - and appends its line to RECORD. With SESSION, the child then
    measures the session's subscription rows (`subscription_check`), runs the
    generator once more if one is missing, and appends what it found.
    Never raises."""
    argv = [sys.executable, "-m", "sessionmail._birth", "--regen", str(root)]
    if record is not None:
        argv += ["--record", str(record)]
    if session not in ("", "-"):
        argv += ["--session", session]
    try:
        child = subprocess.Popen(argv, env=None if env is None else dict(env),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True,
                                 start_new_session=True)
    except OSError as exc:
        return False, f"generator could not start in the background: {exc}"
    return True, f"generator started in the background (pid {child.pid})"


def ensure_session_box(session: str, seat: "Seat | None", *,
                       env: Mapping[str, str] | None = None,
                       make_root: bool = True,
                       box_label: str | None = None,
                       regen: Literal["wait", "detach"] = "wait",
                       record_source: str | None = None,
                       via: str = VIA_LISTEN,
                       converted: bool = False) -> Birth:
    """Birth SESSION's box if it has none, apply SESSIONMAIL_SUBSCRIBE when
    born, declare the seat's address, ring the doorbell and regenerate the
    maps when the box was born or a row is missing. Never raises for a
    refused birth: `Birth.error` says why, and nothing after it ran.

    SESSION "" or "-" is no session: no box, nothing declared, nothing rung
    (a human at a shell; `pager listen` falls back to $MAIL). SEAT None is a
    seatless session: born and declared, but no doorbell to ring.

    MAKE_ROOT False refuses to create the mail root itself (a
    hook never makes `~/.mail/agents` on a machine without mail).
    BOX_LABEL, when given, replaces "this box" in the doorbell line - the
    listen's BOX argument names a box that is not the session's, so the
    line must say which box it measured.

    REGEN "detach" starts the generator in a detached child instead of
    waiting for it, and does not measure again (the first `pager listen`
    does, and regenerates itself if the rows are still missing). A seatless
    birth that subscribed detaches the whole generator too, instead of the
    `--valias-only` rebuild, so nothing in a detaching call can block.
    RECORD_SOURCE, when given, writes the born record if THIS call births
    the box (the hook passes the SessionStart `source`). VIA labels the
    journal of a birth-file box turned off (`birth_subscriptions`).

    CONVERTED says the caller's 3.3b conversion wrote this session's layer
    (`pager listen`): like a subscription at birth, it owes the map one
    rebuild when no doorbell ran the generator (session-settings 3.3b).

    With no MagicDNS suffix the
    maps' FQDN rows cannot be written, so no regen and no rebuild runs.
    REGEN "wait" asks for the suffix where the listen always asked, and says
    so in its lines (`Birth.suffix_unknown`; the listen adds its banner line).
    REGEN "detach" never asks: the lookup can take a third of a second (0.335
    s measured with MAGICDNS_SUFFIX unset), which is a SessionStart
    hook's whole budget, so the detached child asks instead and writes what
    it found to the born record (`_main`)."""
    from . import _layer, _maildir
    detach = regen == "detach"
    has_session = session not in ("", "-")
    root = _maildir.root(env)
    birth = Birth(box=root / session if has_session else None)
    session_box = birth.box
    if session_box is not None and not (session_box / "new").is_dir():
        if not make_root and not root.is_dir():
            birth.error = f"no mail root at {root} - no box born"
            return birth
        try:
            birth.born = _seat.birth_box(root, session)
        except OSError as exc:
            birth.error = f"could not create the session box at {session_box}: {exc}"
            return birth
    # D4, since task 3.4: the LAUNCHER's birth wrote SESSIONMAIL_SUBSCRIBE
    # into the session's layer before the session ran; the call that births
    # the box reports it. The birth's whole regen below routes it; a seatless
    # birth has no doorbell and rebuilds after it.
    # The mail schema (and template) installed under HOME before
    # anything reads it, so the NEXT seat's launcher finds SESSIONMAIL_SUBSCRIBE
    # named with `x-env` without waiting on this seat's first tool batch.
    # Idempotent (a byte compare), best effort, and never `mail.json`: only
    # `hook_batch`'s `ensure()` creates that and says the first-run note.
    if session_box is not None:
        from . import _settings
        _settings.install(env, make=True)
    sub_wrote = False
    if birth.born:
        birth.sub_lines, sub_wrote = birth_subscriptions(session, env, via=via)
    # Asked once, here, where the listen asked - after the birth and
    # before the declaration. A detaching caller leaves it to its child.
    if not detach:
        from . import _magicdns
        suffix, _no_suffix = _magicdns.lookup(env)
        birth.suffix_unknown = suffix is None
    # The doorbell: would the seat's mail LAND in the session's box? Both maps
    # postfix reads are asked (F21: the alias half alone bounces). Measured,
    # and the generator run WHOLE when a line is missing or the box was just
    # born (D9: "a listen that births the box runs the generator in the same
    # step") - bounded, announced.
    bell_lines = birth.bell_lines
    # D1, and its ORDER is the design: the declaration is written BEFORE the
    # first doorbell check, so the regen that check may trigger already sees
    # it and writes the row. Written after, the first listen of a fresh plus
    # seat would regenerate without its row and read NOT COPIED.
    declared = _seat.declared_address(env) if has_session else None
    if declared is not None:
        address, var = declared
        try:
            where = _seat.write_address(session, address, env)
            bell_lines.append(f"  address    : {address} (from {var}) declared in {where} - "
                              f"the alias map generator writes its row")
        except (OSError, _layer.LayerError) as exc:
            bell_lines.append(f"  address    : {address} (from {var}) NOT declared - {exc}; "
                              f"no generator will write its row")
    elif has_session:
        try:
            stale = _seat.clear_address(session, env)
        except (OSError, _layer.LayerError) as exc:
            bell_lines.append(f"  address    : none declared, and the old declaration could "
                              f"not be removed - {exc}")
        else:
            if stale is not None:
                bell_lines.append(f"  address    : none declared - removed the address from "
                                  f"{stale}, left by an earlier run of this session; its row "
                                  f"goes at the next regen")
    if has_session and seat is not None:
        if detach and _settings.carrier(env).kind == "drop":
            # The drop's doorbell runs the generator (`_drop.prepare`), and a
            # detaching call never waits on it; the listen asks instead.
            live, why = None, "not measured at session start; the first pager listen measures it"
        else:
            live, why = _seat.doorbell(seat.address, session, env)
        regen_said = ""
        if (birth.born or live is False) and detach:
            _detach(birth, session, env, root, record_source)
            regen_said = birth.regen_line
        elif (birth.born or live is False) and birth.suffix_unknown:
            regen_said = (DROP_REGEN if _on_drop(env)
                          else "generator NOT run (MagicDNS suffix unknown)")
        elif birth.born or live is False:
            regen_said = _seat.regen_maps(root, env=env)
            birth.regen, birth.regen_line = "ran", regen_said
            if live is False:
                live, why = _seat.doorbell(seat.address, session, env)
        birth.live = live
        if live is True:
            state = "LIVE"
        elif live is None:
            state = "not measured"
        else:
            state, _, why = why.partition(": ")
            state += " - the seat's mail will not reach this box"
        bell = f"  doorbell   : {state}: {why}" + (f"; {regen_said}" if regen_said else "")
        if box_label is not None:
            # The doorbell (and the generator's clause) measure the SESSION's
            # box. Under a BOX in its release of grace that is not the box the
            # header line names, so EVERY branch says which box it means
            # (the not-measured branch too).
            bell = bell.replace("this box", box_label)
        bell_lines.append(bell)
    owed = (sub_wrote or converted) and birth.regen not in ("ran", "detached")
    if owed and detach:
        # A detaching caller never waits for a generator, the rebuild's
        # included: the whole generator, detached, routes the boxes too.
        _detach(birth, session, env, root, record_source)
    elif owed and birth.suffix_unknown:
        birth.sub_lines.append(f"  map        : {DROP_REGEN}" if _on_drop(env) else
                               "  map        : NOT rebuilt - MagicDNS suffix unknown; the "
                               "subscriptions stand, and the next rebuild with a suffix "
                               "routes them")
    elif owed:
        # No doorbell ran the generator (a seatless session): the add path's
        # own rebuild, once for every box written (D3.1, `--valias-only`).
        from . import _subscribe
        ok, line = _subscribe.rebuild(env)
        birth.sub_lines.append(f"  map        : {line} (--valias-only)" if ok else
                               f"  map        : NOT rebuilt - {line}; the subscriptions stand, "
                               "and the next rebuild (any add, remove or birth) routes them")
    if not detach and session_box is not None and not birth.suffix_unknown:
        _settle_listen(birth, session, env)
    if birth.born and record_source is not None and birth.record is None:
        # Born, and no generator was started (a seatless birth with nothing
        # to route): the record still says when, with regen=skipped.
        birth.record = _write_born(session, env, record_source, birth.regen)
    return birth


def _settle_listen(birth: Birth, session: str, env: Mapping[str, str] | None) -> None:
    """The WAIT path (`pager listen`, the monitor's included), seated
    or seatless, a box born now or long ago: measure the session's own
    subscription rows. A row missing rebuilds the alias map ONCE
    (`--valias-only`, the add path's rebuild), and is measured again. A row
    still missing is the loud refusal: a `subscribe  : <box> NOT in map`
    line in the banner. Rows all in the map say nothing: the banner is
    unchanged for a seat that hears. A map that cannot be measured (no
    postconf/postmap, no alias map named, a refused carrier, unreadable
    subscriptions) is never silence: one `subscribe  : <box> map not
    measured (<why>)` line per box, in `subscription list`'s words.

    The mail drop returns before measuring, and that silence is deliberate,
    not an unmeasured map: the drop keeps no compiled map to fall behind
    the settings. Its alias rows are the generator's, read per send
    (`DROP_REGEN`), so a row the settings route is routed at the next send,
    and no rebuild here could change that."""
    from . import _subscribe
    if _on_drop(env):
        return
    check = subscription_check(session, env)
    if check.why:
        _say_unmeasured(birth, check.boxes, check.why)
        return
    if not check.missing:
        return
    first = list(check.missing)
    ok, line = _subscribe.rebuild(dict(env) if env is not None else None)
    check = subscription_check(session, env)
    if check.why:
        _say_unmeasured(birth, first, check.why)
        return
    healed = [box for box in first if box not in check.missing]
    if healed:
        birth.sub_lines.append(f"  map        : {', '.join(healed)} NOT in map at this listen; "
                               f"rebuilt: {line} (--valias-only) - now in map")
    for box in check.missing:
        birth.sub_lines.append(f"  subscribe  : {box} NOT in map - the alias map does not copy "
                               f"{box} into this box, after one rebuild "
                               f"({line if ok else 'NOT rebuilt - ' + line}); this session does "
                               f"NOT hear {box}")


def _say_unmeasured(birth: Birth, boxes: list[str], why: str) -> None:
    """The banner's `subscribe  : <box> map not measured (<why>)` lines, one
    per box, in `subscription list`'s words (`_reach.NOT_MEASURED`); one line
    with no box when the subscriptions themselves could not be read (there is
    no box list to name)."""
    from ._reach import NOT_MEASURED
    for box in boxes or [""]:
        birth.sub_lines.append(f"  subscribe  : {box + ' ' if box else ''}{NOT_MEASURED} ({why})")


def _detach(birth: Birth, session: str, env: Mapping[str, str] | None, root: Path,
            record_source: str | None) -> None:
    """Start the detached generator for BIRTH. Line 1 of the born record is
    written FIRST, so the child's line 2 always has a line 1 to follow."""
    if birth.born and record_source is not None:
        birth.record = _write_born(session, env, record_source, "detached")
    started, birth.regen_line = detach_regen(root, env, birth.record, session=session)
    birth.regen = "detached" if started else ""
    if not started and birth.record is not None:
        # Nothing will append line 2: say so in line 1 instead.
        birth.record = _write_born(session, env, record_source or "-", "failed")


def _main(argv: list[str] | None = None) -> int:
    """`python -m sessionmail._birth --regen ROOT [--record PATH]`: the
    detached child. Runs the generator once and appends its one line to the
    born record. Always exits 0: nobody is waiting for its code."""
    import argparse
    parser = argparse.ArgumentParser(prog="python -m sessionmail._birth")
    parser.add_argument("--regen", required=True, metavar="ROOT")
    parser.add_argument("--record", metavar="PATH")
    parser.add_argument("--session", metavar="SID", default="")
    args = parser.parse_args(argv)
    from . import _magicdns
    lines: list[str] = []
    try:
        # The hook did not ask for the suffix (it costs the hook's
        # budget), so the child asks, and never runs the generator without
        # one: the record's line 2 says so instead.
        # On a mail-drop host no generator runs either way, so the line
        # names the carrier, not the suffix.
        suffix, why = _magicdns.lookup()
        ran = suffix is not None and not _on_drop(None)
        lines.append(_seat.regen_maps(Path(args.regen)) if suffix is not None
                     else DROP_REGEN if _on_drop(None)
                     else f"generator NOT run (MagicDNS suffix unknown: {why})")
        settled = _settle(args.session, Path(args.regen)) if ran and args.session else ""
        if settled:
            lines.append(settled)
    except Exception as exc:  # noqa: BLE001 - regen_maps never raises; a child that dies says why
        lines.append(f"generator failed in the background: {exc}")
    if args.record:
        try:
            # ONE write, line 2 and the measure's line 3 together, so a reader
            # that waits for line 2 never reads the record mid-settle.
            with open(args.record, "a", encoding="utf-8") as fh:
                fh.write("".join(" ".join(line.splitlines()) + "\n" for line in lines))
        except OSError:
            pass
    return 0


def _settle(session: str, root: Path) -> str:
    """In the detached child: after its run, measure SESSION's
    subscription rows. A row missing runs the generator ONCE more (bounded:
    one run, never a loop), then measures again. One line for the record, or
    "" when the session subscribes to nothing."""
    check = subscription_check(session)
    if not check.boxes and not check.why:
        return ""
    if check.why or not check.missing:
        return check.line()
    missing = ", ".join(check.missing)
    again = _seat.regen_maps(root)
    check = subscription_check(session)
    return f"{check.line()} (NOT in map after the first run: {missing}; ran again: {again})"


__all__ = ["BORN_FILE", "DROP_REGEN", "SUBSCRIBE_VAR", "Birth", "BornRecord", "SubCheck",
           "birth_subscriptions", "born_record", "detach_regen", "ensure_session_box", "read_born",
           "subscription_check"]


if __name__ == "__main__":
    sys.exit(_main())
