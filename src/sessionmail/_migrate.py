"""`pager settings migrate`: carry what the old tree still holds into the new.

sessiontools `openspec/changes/session-settings`, task 3.2 (part 2). Part 1
made a SEAT's subscriptions the `subscriptions` map of
`~/.session/seats/<seat>/settings/mail.json` (`_layer`), and since then a
box file left in a seat's OLD folder routes nothing: `_subscription.scan`
names it `NOT_CONVERTED`, and that message names this verb. Each move ships
convert + writer + symlink; this is the convert.

What it does, per folder under `~/.sessionmail/seats/`:

- **A seat** (`<project>+<role>`, `<role>.<project>`): each real box file
  becomes a `true` key of the seat's map, through `_layer.set_entry` (one
  writer, under its lock). The file's body - its note of who, when and why -
  goes to the seat's `log/settings.jsonl` as the change's `why`, and then
  the file is removed, so a second run finds nothing to do. A key the map
  already holds as `true` only removes the file; a key the map holds as
  anything else is the map's word: the file is left, and named.
- **An agent's bare name** (no `+`, no `.`): stays
  where it is until 3.8 reads the agent layer. Its real boxes are printed
  as the block for its agent MD; nothing is written.
- **A session id's folder**: listed only.
- **Saved queries** (a `query:` line): the retired ones, left for tasks 6.3,
  counted and not listed (176 on one measured host).
- **A seat's `rules` file**: left in place and named. It moves with the seat
  rules reader; moving it before that reader exists would turn it off.

Task 3.3b adds the LIVE SESSIONS (Open 2: live only), by the generator's
rule - `session list` counts it live and its box exists. For each,
`convert_session` carries `~/.sessionmail/sessions/<sid>/` into the
session's layer, `~/.session/sessions/<sid>/settings/mail.json`: each box
file becomes a `true` key of `subscriptions` (its body the log's `why`),
and the `address` file becomes the `address` key; each converted file is
removed. The layer's own value wins over a file, as for a seat. It is THE
one conversion: `pager listen` runs it for its own session right after the
3.3a move, which catches a session resumed after this verb ran. A dead
session's files are never read, so they are never converted.

No symlink at the old folder: it also holds the
saved queries. `--dry-run` says the same lines and writes nothing. After a
real run that moved a box, the alias map is rebuilt (`vmailbox-gen
--valias-only`), since the moved boxes route only from then.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from . import _layer, _seat, _subscription as sub

#: A folder named for a session, not an agent: a UUID.
_SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

#: The old seat rules file (`rules.RULES_FILE`; not imported: rules.py is heavy).
RULES = "rules"

#: A session's old address file, beside its `subscriptions/` folder (3.3b).
ADDRESS_FILE = "address"


@dataclass
class Report:
    """What one run found and did (or, dry, would do)."""

    dry_run: bool
    moved: list[tuple[str, str, Path]] = field(default_factory=list)    # seat, box, old file
    already: list[tuple[str, str, Path]] = field(default_factory=list)  # in the map: file removed
    conflicts: list[tuple[str, str, Path, object]] = field(default_factory=list)
    agents: dict[str, list[str]] = field(default_factory=dict)          # bare name -> boxes
    session_ids: list[str] = field(default_factory=list)
    rules: list[Path] = field(default_factory=list)
    queries: int = 0
    seats: int = 0
    # Live sessions (3.3b): the same three outcomes, keyed on the session id.
    sessions: int = 0
    session_moved: list[tuple[str, str, Path]] = field(default_factory=list)
    session_already: list[tuple[str, str, Path]] = field(default_factory=list)
    session_conflicts: list[tuple[str, str, Path, object]] = field(default_factory=list)
    addresses: list[tuple[str, str, Path]] = field(default_factory=list)       # sid, address, old file
    address_already: list[tuple[str, str, Path]] = field(default_factory=list)
    address_conflicts: list[tuple[str, str, Path, object]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """A real run wrote a map or an address: the alias map owes a rebuild."""
        return not self.dry_run and bool(self.moved or self.session_moved or self.addresses)

    @property
    def touched(self) -> bool:
        """Anything found to carry, written or already there."""
        return bool(self.moved or self.already or self.session_moved
                    or self.session_already or self.addresses or self.address_already)


def _note(path: Path) -> str | None:
    """The old file's body, as the move's `why`. None if empty or unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def _split(owner: Path, report: Report, kind: str = "seat") -> list[sub.Entry]:
    """OWNER's real box files; saved queries counted, other skips kept."""
    found: list[sub.Entry] = []
    skipped: list[tuple[Path, str]] = []
    sub._read_folder(kind, owner, found, skipped)
    for path, why in skipped:
        if why.startswith("an old saved query"):
            report.queries += 1
        else:
            report.skipped.append((path, why))
    return found


def _move(owner: Path, entry: sub.Entry, report: Report, env: Mapping[str, str] | None,
          *, by: str, argv: str) -> None:
    seat = owner.name
    try:
        directory = _layer.settings_dir(seat, env)
    except _layer.LayerError as exc:
        report.errors.append((entry.path, str(exc)))
        return
    _carry(seat, directory, entry, report, layer="seat", by=by, argv=argv)


def _carry(owner: str, directory: Path, entry: sub.Entry, report: Report, *,
           layer: str, by: str, argv: str) -> None:
    """ENTRY's box into the `subscriptions` map of the LAYER at DIRECTORY."""
    if layer == "seat":
        moved, already, conflicts = report.moved, report.already, report.conflicts
    else:
        moved, already = report.session_moved, report.session_already
        conflicts = report.session_conflicts
    try:
        values, _origins = _layer.read(directory, layer)
    except _layer.LayerError as exc:
        report.errors.append((entry.path, str(exc)))
        return
    current = (values.get(_layer.SUBSCRIPTIONS) or {})
    current = current.get(entry.box, None) if isinstance(current, dict) else None
    if current is not None and current is not True:
        conflicts.append((owner, entry.box, entry.path, current))
        return
    if current is True:
        already.append((owner, entry.box, entry.path))
    else:
        moved.append((owner, entry.box, entry.path))
    if report.dry_run:
        return
    try:
        if current is None:
            _layer.set_entry(directory, _layer.SUBSCRIPTIONS, entry.box, True)
            _layer.log(directory.parent, f"{_layer.SUBSCRIPTIONS}.{entry.box}", True,
                       by=by, argv=argv,
                       why=f"migrated from {entry.path}" + (f": {n}" if (n := _note(entry.path)) else ""))
        entry.path.unlink()
    except (_layer.LayerError, OSError) as exc:
        report.errors.append((entry.path, str(exc)))


def _old_address(path: Path) -> str | None:
    """The old address file's first line, trimmed and lower-cased as the
    generator read it; None when there is no file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return None
    lines_ = text.splitlines()
    return lines_[0].strip().lower() if lines_ else ""


def _carry_address(sid: str, directory: Path, path: Path, report: Report, *,
                   by: str, argv: str) -> None:
    """The old `address` file at PATH into the session layer's `address` key."""
    try:
        old = _old_address(path)
    except OSError as exc:
        report.errors.append((path, f"cannot read the file: {exc.strerror or exc}"))
        return
    if old is None:
        return
    try:
        values, _origins = _layer.read(directory, "session")
    except _layer.LayerError as exc:
        report.errors.append((path, str(exc)))
        return
    current = values.get(_layer.ADDRESS)
    if current is None or not old:
        # Nothing in the layer; or an empty file, which declared nothing.
        (report.addresses if old else report.address_already).append((sid, old, path))
    elif _layer.address(values) == old:
        report.address_already.append((sid, old, path))
    else:
        report.address_conflicts.append((sid, old, path, current))
        return
    if report.dry_run:
        return
    try:
        if current is None and old:
            _layer.set_value(directory, _layer.ADDRESS, old)
            _layer.log(directory.parent, _layer.ADDRESS, old, by=by, argv=argv,
                       why=f"migrated from {path}")
        path.unlink()
    except (_layer.LayerError, OSError) as exc:
        report.errors.append((path, str(exc)))


def convert_session(sid: str, report: Report, env: Mapping[str, str] | None = None, *,
                    home: Path | None = None, by: str = "", argv: str = "") -> None:
    """SID's old files into its session layer (3.3b): THE one conversion,
    run by `pager settings migrate` for each live session and by `pager
    listen` for its own. Box files become `true` keys of `subscriptions`,
    the `address` file the `address` key; each converted file is removed.
    Never raises: what fails is in `report.errors`."""
    if not sid or "/" in sid or sid.startswith("."):
        return
    home = _seat.home(env) if home is None else Path(home)
    owner = home / sub.SESSIONS_DIR / sid
    report.sessions += 1
    if not (owner.is_dir()):
        return                          # nothing old: a session born after 3.3b
    try:
        directory = _layer.session_settings_dir(sid, env)
    except _layer.LayerError as exc:
        report.errors.append((owner, str(exc)))
        return
    for entry in _split(owner, report, kind="session"):
        _carry(sid, directory, entry, report, layer="session", by=by, argv=argv)
    _carry_address(sid, directory, owner / ADDRESS_FILE, report, by=by, argv=argv)


def live_boxed() -> set[str]:
    """The generator's liveness rule: `session list` counts it live
    (`_boxes.live_sessions`) and its box exists under the agents root."""
    from . import _boxes, _maildir
    root = _maildir.root()
    return {sid for sid in _boxes.live_sessions() if (root / sid).is_dir()}


def migrate(env: Mapping[str, str] | None = None, *, dry_run: bool = False,
            home: Path | None = None, by: str = "", argv: str = "",
            live: Iterable[str] | None = None) -> Report:
    """Walk `<home>/seats/` and carry each seat's box files into its map;
    then each LIVE session's old files into its layer (3.3b; LIVE defaults
    to `live_boxed()`). HOME is the sessionmail home (`_seat.home(env)`);
    the maps follow ENV's HOME. Never raises over one folder: what fails is
    in `Report.errors`."""
    home = _seat.home(env) if home is None else Path(home)
    report = Report(dry_run=dry_run)
    owners = sub._owners(home / sub.SEATS_DIR, report.skipped)
    for owner in owners:
        name = owner.name
        if _layer.is_agent_name(name):
            if _SESSION_ID.match(name):
                report.session_ids.append(name)
                continue
            boxes = [e.box for e in _split(owner, report)]
            if boxes:
                report.agents[name] = boxes
            continue
        report.seats += 1
        if (owner / RULES).is_file():
            report.rules.append(owner / RULES)
        for entry in _split(owner, report):
            _move(owner, entry, report, env, by=by, argv=argv)
    if isinstance(live, str):
        raise TypeError("live is a collection of session ids, not one str")
    for sid in sorted(live_boxed() if live is None else set(live)):
        convert_session(sid, report, env, home=home, by=by, argv=argv)
    return report


def md_block(boxes: list[str]) -> str:
    """The block an agent MD carries for BOXES (3.8 reads it)."""
    lines = ["settings:", "  mail:", "    subscriptions:"]
    lines += [f"      {b}: true" for b in boxes]
    return "\n".join(lines)


def lines(report: Report) -> list[str]:
    """The report, one fact a line, for a person."""
    verb = "would move" if report.dry_run else "moved"
    out: list[str] = []
    out.append(f"seats: {report.seats} read; {len(report.moved)} box(es) {verb} into their maps")
    for seat, box, path in report.moved:
        out.append(f"  {verb}  {seat}  {box}  (was {path})")
    for seat, box, path in report.already:
        out.append(f"  already in the map  {seat}  {box}: "
                   f"{'would remove' if report.dry_run else 'removed'} {path}")
    for seat, box, path, value in report.conflicts:
        out.append(f"  left  {path}: {seat}'s map sets {box} to {value!r}, and the map wins")
    for path in report.rules:
        out.append(f"  left  {path}: seat rules move with the seat rules reader")
    if report.agents:
        out.append(f"agents: {len(report.agents)} bare name(s) hold boxes; they stay until "
                   "3.8 reads the agent layer. For each agent's MD:")
        for name, boxes in sorted(report.agents.items()):
            out.append(f"  {name}:")
            out += [f"    {ln}" for ln in md_block(boxes).splitlines()]
    out += session_lines(report)
    if report.session_ids:
        out.append(f"session ids: {len(report.session_ids)} folder(s), listed only: "
                   + ", ".join(report.session_ids))
    if report.queries:
        out.append(f"saved queries: {report.queries} left in the old folders "
                   "(retired; tasks 6.3 moves them)")
    for path, why in report.skipped:
        out.append(f"skipped  {path}: {why}")
    for path, why in report.errors:
        out.append(f"FAILED  {path}: {why}")
    if report.dry_run:
        out.append("dry run: nothing written")
    elif not report.touched:
        out.append("nothing to move")
    return out


def session_lines(report: Report, *, header: bool = True) -> list[str]:
    """The live sessions' half of the report (3.3b): what was carried into
    each session's layer, and what was left because the layer wins."""
    verb = "would move" if report.dry_run else "moved"
    gone = "would remove" if report.dry_run else "removed"
    out: list[str] = []
    if header and report.sessions:
        out.append(f"live sessions: {report.sessions} read; {len(report.session_moved)} "
                   f"box(es) and {len(report.addresses)} address(es) {verb} into their maps")
    for sid, box, path in report.session_moved:
        out.append(f"  {verb}  {sid}  {box}  (was {path})")
    for sid, addr, path in report.addresses:
        out.append(f"  {verb}  {sid}  address {addr}  (was {path})")
    for sid, box, path in report.session_already:
        out.append(f"  already in the map  {sid}  {box}: {gone} {path}")
    for sid, addr, path in report.address_already:
        out.append(f"  already in the map  {sid}  address {addr or '(empty)'}: {gone} {path}")
    for sid, box, path, value in report.session_conflicts:
        out.append(f"  left  {path}: {sid}'s map sets {box} to {value!r}, and the map wins")
    for sid, addr, path, value in report.address_conflicts:
        out.append(f"  left  {path}: {sid}'s map sets address to {value!r}, not {addr!r}, "
                   "and the map wins")
    return out


__all__ = ["Report", "migrate", "convert_session", "live_boxed", "md_block", "lines",
           "session_lines", "RULES", "ADDRESS_FILE"]
