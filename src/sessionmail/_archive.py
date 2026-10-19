"""Archive dead session boxes, and bring them back.

`message box archive` lists the session boxes that are DEAD and, with
`--apply`, moves each one from `agents/<sid>` to `archive/<sid>`, so that
`notmuch new` stops walking it. `message box unarchive` moves one back.

A box is DEAD only when ALL of these hold, and only a session-id box can
be (seat, store and project boxes are always KEEP):

  (a) no live session owns it: no Claude Code sidecar whose pid is
      running names it (`$SESSION_SIDECAR_DIR`, the generator's source);
  (b) no alias row names it, as key or destination, in the alias map
      postfix reads now OR the one the next regenerate would write
      (`vmailbox-gen --print-valias`). Its MAILBOX row is removed by the
      archive itself: move, regenerate once, verify;
  (c) no new file for N days (newest of the files' and the leaves' mtimes).

`--apply` is ATOMIC per box: every box ends ARCHIVED, its
mailbox row verified gone in the WRITTEN map (and the .db, when postconf
answers), or BACK under `agents/`, with a non-zero exit. A box re-created
while its row still routed it (a mail in the window) is merged
back by link+unlink, never overwriting. A run that is interrupted finishes
its verify for what it moved; a run that dies leaves a `moving` ledger line
that the next run finds and finishes first.

Never: a git command, a `notmuch` run, a write to a notmuch config, or a
change to the generator.
"""

from __future__ import annotations

import configparser
import errno
import json
import os
import re
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from typing import Callable, Iterable

from . import _boxes, _drop, _generator, _seat
from ._maildir import make_private_dirs, strict_mode

DEFAULT_DAYS = 7.0
LEDGER = ".ledger"
LOCK = ".lock"
LEAVES = ("new", "cur", "tmp")
SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")

#: Test seams, by name: `before_move(rel)`, `after_move(rel)` and
#: `after_regen(round)`. Empty in use; a test fills it.
HOOKS: dict[str, Callable] = {}

#: How long a `--print-valias` answer is trusted while boxes are being
#: moved, before it is asked again (the per-box re-check of (b)).
NEXT_TTL = 10.0


class Unknown(Exception):
    """A criterion that could not be READ. Never a pass: it keeps every box."""


class Held(Exception):
    """The archive lock is held by another run."""


def _hook(name: str, *args) -> None:
    fn = HOOKS.get(name)
    if fn is not None:
        fn(*args)


def _dev(path: Path) -> int:
    """The device a path is on. A seam: a test moves the archive elsewhere."""
    return os.stat(path).st_dev


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


# ── (a) live sessions ──────────────────────────────────────────────────────

def sidecar_dir(env: dict[str, str]) -> Path:
    if env.get("SESSION_SIDECAR_DIR"):
        return Path(env["SESSION_SIDECAR_DIR"])
    return Path(env.get("HOME") or Path.home()) / ".claude" / "sessions"


def _read_sidecar(f: Path) -> dict | None:
    """One sidecar, read twice at most: one caught mid-write gets a second
    look 100 ms later. Gone (its session ended) is None; unparseable twice
    is Unknown."""
    for attempt in (1, 2):
        try:
            car = json.loads(f.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if attempt == 1:
                time.sleep(0.1)
                continue
            raise Unknown(f"sidecar {f.name} will not parse") from None
        if not isinstance(car, dict):
            raise Unknown(f"sidecar {f.name} is not an object")
        return car
    return None


def live_sessions(env: dict[str, str]) -> set[str]:
    """Session ids whose sidecar pid is running. A recycled pid can only
    make a dead session look LIVE, which keeps its box (the generator's
    procStart match is dropped on purpose). Fails closed: no sidecar dir,
    a torn sidecar, or a caller session missing from the set is Unknown."""
    d = sidecar_dir(env)
    if not d.is_dir():
        raise Unknown(f"no sidecar dir {d}")
    live: set[str] = set()
    for f in sorted(d.glob("*.json")):
        car = _read_sidecar(f)
        if not car:
            continue
        sid, pid = str(car.get("sessionId") or ""), car.get("pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if sid and pid > 0 and _seat._pid_alive(pid):
            live.add(sid.lower())
    me = (env.get("CLAUDE_CODE_SESSION_ID") or "").lower()
    if me and me not in live:
        raise Unknown(f"this session {me[:8]} is not in the live set: the sidecars cannot be trusted")
    return live


def owner(name: str, live: Iterable[str]) -> str | None:
    """The live session that owns box NAME: its own id, or for an 8-hex
    short box any live id starting with it (a prefix match keeps it)."""
    n = name.lower()
    if len(n) == 8:
        return next((s for s in sorted(live) if s.startswith(n)), None)
    return n if n in live else None


# ── (b) alias rows, and the mailbox map ────────────────────────────────────

def generator(env: dict[str, str], mail_root: Path) -> str | None:
    """The generator, found as `_seat._run_generator` finds it: env, PATH,
    `<mail root>/.postfix`, and never the packaged copy, because the archive
    regenerates the live maps through `_seat` (the packaged copy never writes a live map)."""
    found = _generator.find(env, mail_root, packaged=False)
    return None if found is None else found.path


def map_paths(maps: Path | None, name: str, parameter: str) -> list[Path] | None:
    """The text sources of a postfix map: `--maps DIR/<name>`, else every
    `hash:`/`lmdb:` path postconf names for PARAMETER. None: nobody said."""
    if maps is not None:
        return [maps / name]
    found = _seat._postfix_maps(parameter)
    if not found:
        return None
    return [Path(m.split(":", 1)[1]) for m in found]


def read_maps(paths: list[Path] | None, what: str) -> str:
    if not paths:
        raise Unknown(f"no {what} map: postconf could not say (give --maps DIR)")
    out = []
    for p in paths:
        try:
            out.append(p.read_text())
        except OSError as exc:
            raise Unknown(f"{what} map {p} unreadable: {exc.strerror or exc}") from None
    return "".join(out)


def routed_names(text: str) -> set[str]:
    """Every local part an alias map names, as key or destination, and the
    base of each plus form: `x+y@h` names `x+y` and `x`."""
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for addr in line.replace(",", " ").split():
            local = addr.split("@", 1)[0].lower()
            names.add(local)
            names.add(local.split("+", 1)[0])
    return names


def mailbox_keys(text: str, rel: str) -> list[str]:
    """The keys of REL's rows in a mailbox map's text: the rows whose
    SECOND field is exactly `agents/<rel>/` (BN1), so both host forms are
    found by one test and never by a substring."""
    want = f"agents/{rel.lower()}/"
    keys = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == want:
            keys.append(parts[0])
    return keys


@dataclass
class Ctx:
    """One run: where things are, and what it may ask."""

    root: Path                          # the agents dir (`_maildir.root()`)
    archive: Path
    days: float = DEFAULT_DAYS
    maps: Path | None = None            # --maps DIR; None: ask postconf
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    now: float = field(default_factory=time.time)
    out: Callable[[str], None] = print
    err: Callable[[str], None] = print

    @property
    def mail_root(self) -> Path:
        return self.root.parent

    @property
    def actor(self) -> str:
        return self.env.get("CLAUDE_CODE_SESSION_ID") or "-"

    @property
    def dropping(self) -> bool:
        """The carrier is the mail drop and no `--maps DIR` names maps to read.
        Such a host has no written map: every map this run reads is the one
        the drop prints per send (`_drop.print_map`), and no map is written."""
        return self.maps is None and _drop.selected(self.env)

    def _printed(self, which: str) -> str:
        try:
            return _drop.print_map(self.env, which)
        except _drop.NotDelivered as exc:
            raise Unknown(f"the mail drop's {which} map: {exc}") from None

    def mailbox_text(self) -> str | None:
        try:
            if self.dropping:
                return self._printed("mailbox")
            return read_maps(map_paths(self.maps, "vmailbox", "virtual_mailbox_maps"), "mailbox")
        except Unknown:
            return None

    def alias_now(self) -> str:
        if self.dropping:
            return self._printed("alias")
        return read_maps(map_paths(self.maps, "valias", "virtual_alias_maps"), "alias")

    def alias_next(self) -> str:
        if self.dropping:                   # nothing is regenerated: the next print is the map
            return self._printed("alias")
        gen = generator(self.env, self.mail_root)
        if gen is None:
            raise Unknown("no generator: cannot ask what the next regenerate would write")
        try:
            res = subprocess.run(_generator.argv(gen, ["--print-valias", "--root",
                                                       str(self.mail_root)], self.env),
                                 capture_output=True, text=True,
                                 env=_generator.spawn_env(self.env) or self.env, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unknown(f"--print-valias could not run: {exc}") from None
        if res.returncode != 0:
            tail = (res.stderr or "").strip().splitlines()[-1:] or ["no message"]
            raise Unknown(f"--print-valias exit {res.returncode}: {tail[0]}")
        return res.stdout

    def regen(self) -> str:
        """One whole regenerate. Its line is only shown: the effect is what
        is judged (`regen_maps` drops the generator's ok flag)."""
        return _seat.regen_maps(self.root, env=self.env)

    def use_db(self) -> bool:
        """Ask the .db too, only when postconf answers (never under --maps,
        and never under the drop, which has none)."""
        return self.maps is None and not self.dropping


# ── (c) activity ───────────────────────────────────────────────────────────

def activity(box: Path) -> tuple[float, int, int]:
    """(newest mtime, files, bytes) over the regular files in new/cur/tmp
    and those three dirs' own mtimes: any activity at all keeps a box."""
    newest, files, size = 0.0, 0, 0
    for leaf in LEAVES:
        d = box / leaf
        try:
            newest = max(newest, d.stat().st_mtime)
        except OSError:
            continue
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            files += 1
                            size += st.st_size
                            newest = max(newest, st.st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return newest, files, size


# ── the ledger and the lock ────────────────────────────────────────────────

def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _clean(s: str) -> str:
    return " ".join(str(s).split()) or "-"


def ledger_append(archive: Path, state: str, rel: str, *, actor: str, files: int = 0,
                  size: int = 0, newest: float = 0.0, reason: str = "",
                  keys: Iterable[str] = ()) -> None:
    """One TSV line, appended and fsynced (BN4): time, state, box, files,
    bytes, newest mtime, reason, actor session, the box's mailbox keys
    (BN6: what a recovering run asks the .db)."""
    line = "\t".join([_iso(time.time()), state, rel, str(files), str(size),
                      _iso(newest) if newest else "-", _clean(reason), actor,
                      ",".join(keys) or "-"]) + "\n"
    fd = os.open(archive / LEDGER, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def ledger_last(archive: Path) -> dict[str, list[str]]:
    """Each box's LAST ledger line, split: its state is the second field."""
    last: dict[str, list[str]] = {}
    try:
        text = (archive / LEDGER).read_text()
    except OSError:
        return last
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) >= 3:
            last[f[2]] = f
    return last


def interrupted(archive: Path) -> list[tuple[str, list[str]]]:
    """Boxes whose last ledger line is `moving`: a run stopped between the
    move and the verify."""
    return sorted((rel, f) for rel, f in ledger_last(archive).items() if f[1] == "moving")


def lock_owner(archive: Path) -> str | None:
    """Who holds the lock, and whether that pid still runs; None: free."""
    lock = archive / LOCK
    if not lock.is_dir():
        return None
    try:
        text = (lock / "owner").read_text().strip()
    except OSError:
        text = ""
    m = re.search(r"pid=(\d+)", text)
    alive = ("pid running" if m and _seat._pid_alive(int(m.group(1)))
             else "pid NOT running" if m else "no owner recorded")
    return f"{lock} held by {text or '?'} ({alive})"


def take_lock(archive: Path, actor: str) -> None:
    lock = archive / LOCK
    try:
        os.mkdir(lock)
    except FileExistsError:
        raise Held(f"{lock_owner(archive)}. Never removed by this tool: if no run is live, "
                   f"the host owner runs: rm -f {lock}/owner && rmdir {lock}") from None
    (lock / "owner").write_text(f"pid={os.getpid()} session={actor} since={_iso(time.time())}\n")


def release_lock(archive: Path) -> None:
    lock = archive / LOCK
    try:
        (lock / "owner").unlink()
    except OSError:
        pass
    try:
        lock.rmdir()
    except OSError:
        pass


# ── preflight ──────────────────────────────────────────────────────────────

def notmuch_configs(env: dict[str, str]) -> list[Path]:
    """Every notmuch config file this host could use: `$NOTMUCH_CONFIG`
    (else `~/.notmuch-config`), each `~/.notmuch-config.<profile>`, and
    each `$XDG_CONFIG_HOME/notmuch/<profile>/config`."""
    home = Path(env.get("HOME") or Path.home())
    found = [Path(env["NOTMUCH_CONFIG"]).expanduser()] if env.get("NOTMUCH_CONFIG") \
        else [home / ".notmuch-config"]
    found += sorted(home.glob(".notmuch-config.*"))
    xdg = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    found += sorted(xdg.glob("notmuch/*/config"))
    out: list[Path] = []
    for p in found:
        if p.is_file() and p not in out:
            out.append(p)
    return out


def _ignored(rel: Path, ignores: list[str]) -> bool:
    """notmuch-config(1) `new.ignore`: a plain entry is a file or directory
    NAME anywhere; a `/regex/` is searched in the path relative to the
    database. A directory ignored hides all below it."""
    parts = rel.parts
    for i, name in enumerate(parts):
        prefix = "/".join(parts[:i + 1])
        for ig in ignores:
            if len(ig) >= 2 and ig.startswith("/") and ig.endswith("/"):
                try:
                    if re.search(ig[1:-1], prefix):
                        return True
                except re.error:
                    continue
            elif ig == name:
                return True
    return False


def notmuch_problem(cfg: Path, archive: Path, env: dict[str, str]) -> str | None:
    """Why notmuch, under config CFG, would scan ARCHIVE; None when it
    would not (the database does not hold it, or an ignore entry hides it).
    Read with configparser only: notmuch itself is never run."""
    home = Path(env.get("HOME") or Path.home())
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(cfg.read_text())
    except (OSError, configparser.Error) as exc:
        return f"notmuch config {cfg} unreadable: {exc}"
    db = (cp.get("database", "mail_root", fallback="") or cp.get("database", "path", fallback="")
          or env.get("MAILDIR") or str(home / "mail"))
    db_path = Path(db).expanduser()
    if not db_path.is_absolute():
        db_path = home / db_path
    try:
        rel = archive.resolve().relative_to(db_path.resolve())
    except ValueError:
        return None
    if not rel.parts:
        return f"notmuch config {cfg}: the archive IS the database path {db_path}"
    ignores = [s.strip() for s in cp.get("new", "ignore", fallback="").split(";") if s.strip()]
    if _ignored(rel, ignores):
        return None
    return (f"notmuch {cfg} scans it: database {db_path}, new.ignore={';'.join(ignores) or '(none)'} "
            f"(the host owner adds /^{re.escape(rel.as_posix())}$/ before --apply)")


@dataclass
class Preflight:
    problems: list[str] = field(default_factory=list)   # each refuses NEW moves
    notes: list[str] = field(default_factory=list)      # information only
    generator: str | None = None


def preflight(ctx: Ctx, live_err: str | None, alias_err: str | None) -> Preflight:
    pf = Preflight()
    # P1: one device, so every move is a rename and never a copy.
    target = ctx.archive if ctx.archive.exists() else ctx.archive.parent
    state = "exists" if ctx.archive.exists() else "absent"
    try:
        if _dev(target) != _dev(ctx.root):
            pf.problems.append(f"P1 archive {ctx.archive} is on another device than {ctx.root}: "
                               "a move would be a copy")
        else:
            pf.notes.append(f"archive {ctx.archive} ({state}; same device as agents/)")
    except OSError as exc:
        pf.problems.append(f"P1 archive {ctx.archive}: {exc.strerror or exc}")
    # P2: notmuch will not scan it.
    configs = notmuch_configs(ctx.env)
    for cfg in configs:
        why = notmuch_problem(cfg, ctx.archive, ctx.env)
        if why:
            pf.problems.append(f"P2 {why}")
    if not configs:
        pf.notes.append("no notmuch config found: nothing scans the archive")
    # P3, P4: the criteria could be read.
    if live_err:
        pf.problems.append(f"P3 (a) liveness unreadable: {live_err}")
    if alias_err:
        pf.problems.append(f"P4 (b) alias maps unreadable: {alias_err}")
    # P5: a generator to regenerate with; P6: a mailbox map to verify against.
    # Under the drop nothing is regenerated, so P5 does not apply, and P6
    # reads the map the drop prints.
    if ctx.dropping:
        pf.notes.append("the carrier is the mail drop: no map is written, and each box is "
                        "verified against the map it prints")
    else:
        pf.generator = generator(ctx.env, ctx.mail_root)
        if pf.generator is None:
            pf.problems.append("P5 no vmailbox-gen (SESSIONMAIL_VMAILBOX_GEN, PATH, <mail root>/.postfix)")
    if ctx.mailbox_text() is None:
        pf.problems.append("P6 the mailbox map cannot be read: nothing could be verified")
    if (ctx.mail_root / ".git").exists():
        pf.notes.append("a git work tree: the moves will show as renames; committing them is the "
                        "host owner's")
    return pf


# ── the census ─────────────────────────────────────────────────────────────

@dataclass
class Row:
    box: _boxes.Box
    reasons: list[str]
    keep: str | None            # the first reason it is kept; None: a candidate
    newest: float = 0.0
    files: int = 0
    size: int = 0

    @property
    def candidate(self) -> bool:
        return self.keep is None

    def line(self) -> str:
        return f"{'CANDIDATE' if self.candidate else 'KEEP     '} {self.box.name}  " + "  ".join(self.reasons)


@dataclass
class Census:
    rows: list[Row]
    live: set[str] | None
    live_err: str | None
    alias: set[str] | None
    alias_err: str | None
    lines_now: int = 0
    lines_next: int = 0


def _alias_sets(ctx: Ctx) -> tuple[set[str], set[str], int, int]:
    now_text, next_text = ctx.alias_now(), ctx.alias_next()
    return (routed_names(now_text), routed_names(next_text),
            len(now_text.splitlines()), len(next_text.splitlines()))


def judge(ctx: Ctx, box: _boxes.Box, live: set[str] | None, live_err: str | None,
          now_names: set[str] | None, next_names: set[str] | None, alias_err: str | None) -> Row:
    """One box's verdict, every criterion with its reason."""
    if box.kind != "session":
        return Row(box, [f"kind {box.kind}, not archived in v1"], "kind")
    newest, files, size = activity(box.path)
    reasons: list[str] = []
    keep: str | None = None
    if (ctx.archive / box.name).exists():
        reasons.append("RE-CREATED: also under archive/")
        keep = "recreated"
    if live is None:
        reasons.append(f"(a) UNKNOWN: {live_err}")
        keep = keep or "unknown"
    else:
        who = owner(box.name, live)
        if who:
            reasons.append(f"(a) live session {who[:8]}")
            keep = keep or "live"
        else:
            reasons.append("(a) no live sidecar")
    if now_names is None or next_names is None:
        reasons.append(f"(b) UNKNOWN: {alias_err}")
        keep = keep or "unknown"
    else:
        n = box.name.lower()
        where = [w for w, names in (("now", now_names), ("next", next_names)) if n in names]
        if where:
            reasons.append(f"(b) alias row: {' and '.join(where)}")
            keep = keep or "alias"
        else:
            reasons.append("(b) no alias row, now or next")
    age = (ctx.now - newest) / 86400
    if newest >= ctx.now - ctx.days * 86400:
        reasons.append(f"(c) newer than {ctx.days:g}d: {age:.1f}d")
        keep = keep or "newer"
    else:
        reasons.append(f"(c) quiet {age:.1f}d ({files} files)")
    return Row(box, reasons, keep, newest, files, size)


def census(ctx: Ctx, only: set[str] | None = None) -> Census:
    try:
        live, live_err = live_sessions(ctx.env), None
    except Unknown as exc:
        live, live_err = None, str(exc)
    try:
        now_names, next_names, n_now, n_next = _alias_sets(ctx)
        alias_err = None
    except Unknown as exc:
        now_names = next_names = None
        n_now = n_next = 0
        alias_err = str(exc)
    rows = [judge(ctx, b, live, live_err, now_names, next_names, alias_err)
            for b in _boxes.walk(ctx.root) if not only or b.name.lower() in only]
    alias = None if now_names is None or next_names is None else now_names | next_names
    return Census(rows, live, live_err, alias, alias_err, n_now, n_next)


KEEP_WORDS = (("live", "live"), ("alias", "alias row"), ("newer", "newer than {d}d"),
              ("kind", "not a session box"), ("unknown", "unknown"), ("recreated", "re-created"))


def total(c: Census, days: float) -> str:
    cand = [r for r in c.rows if r.candidate]
    files = sum(r.files for r in cand)
    kept = []
    for key, word in KEEP_WORDS:
        n = sum(1 for r in c.rows if r.keep == key)
        if n or key in ("live", "alias", "newer", "kind"):
            kept.append(f"{n:,} {word.format(d=f'{days:g}')}")
    return (f"-- {len(cand):,} candidates of {len(c.rows):,} boxes at --days {days:g} "
            f"({len(cand) * 4:,} dirs, {files:,} files); kept: {', '.join(kept)}")


def listing(ctx: Ctx, only: set[str] | None, all_rows: bool) -> int:
    """The dry run: touches nothing. Exit 0."""
    c = census(ctx, only)
    pf = preflight(ctx, c.live_err, c.alias_err)
    for note in pf.notes:
        ctx.out(f"preflight: {note}")
    for problem in pf.problems:
        ctx.out(f"preflight: ✗ {problem}")
    if c.live is not None:
        ctx.out(f"preflight: live sessions {len(c.live)} (sidecars); "
                f"alias maps: now {c.lines_now} lines, next {c.lines_next} lines")
    held = lock_owner(ctx.archive)
    if held:
        ctx.out(f"LOCK {held}")
    for rel, f in interrupted(ctx.archive):
        ctx.out(f"INTERRUPTED {rel}: a run stopped between its move and its verify "
                f"(ledger {f[0]}); --apply finishes it first")
    for r in c.rows:
        if r.keep == "recreated":
            _n, files, _s = activity(r.box.path)
            ctx.out(f"RE-CREATED {r.box.name}: {files} file(s) in agents/ and a box under archive/; "
                    f"message box unarchive {r.box.name} --merge --apply joins them")
    for r in c.rows:
        if r.candidate or all_rows:
            ctx.out(r.line())
    _dangling(ctx, c)
    ctx.out(total(c, ctx.days))
    return 0


def _dangling(ctx: Ctx, c: Census) -> None:
    """Information only: a subscription file naming an archived box (the
    generator skips it and counts it on stderr). Never deleted here."""
    try:
        from . import _subscription
        scan = _subscription.scan(live=c.live or (), env=ctx.env)
    except Exception:              # information only: a listing never fails on it
        return
    for e in scan.entries:
        base = e.box.split("+", 1)[0].lower()
        if (ctx.archive / base).is_dir() and not (ctx.root / base).exists():
            ctx.out(f"info: subscription {e.path} names archived box {base} (the generator skips it)")


# ── moving back, and merging ───────────────────────────────────────────────

def _fresh_name(leaf: Path, name: str) -> Path:
    """A free maildir name beside NAME: its unique part gets `-arch<N>`,
    and any `:2,<flags>` info is kept."""
    base, sep, info = name.partition(":2,")
    n = 1
    while True:
        cand = leaf / (f"{base}-arch{n}{sep}{info}")
        if not cand.exists():
            return cand
        n += 1


def merge(src: Path, dst: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Join box SRC into box DST: each file of new/ and cur/ by link, then
    unlink. NEVER overwrites: on a clash both files are kept, the one from
    SRC under a fresh name. tmp/ files (unfinished deliveries) stay where
    they are. Returns (clashes as (was, now), what was left in SRC)."""
    clashes: list[tuple[str, str]] = []
    left: list[str] = []
    for leaf in ("new", "cur"):
        s, d = src / leaf, dst / leaf
        # Strict stats: an unsearchable SRC raises, so `_back` keeps
        # the box `moving` and says why. `s.is_dir()` raised there only up to
        # CPython 3.13; from 3.14 it says False, and SRC's mail, never
        # touched, would be reported as nothing left.
        if not stat.S_ISDIR(strict_mode(s)):
            continue
        make_private_dirs(d)            # 0700 if made here; an existing leaf keeps its mode
        for f in sorted(s.iterdir()):
            if not f.is_file() or f.is_symlink():
                left.append(f"{leaf}/{f.name}")
                continue
            target = d / f.name
            while True:
                try:
                    os.link(f, target)
                    break
                except FileExistsError:
                    target = _fresh_name(d, f.name)
            if target.name != f.name:
                clashes.append((f"{leaf}/{f.name}", f"{leaf}/{target.name}"))
            os.unlink(f)
        _fsync_dir(d)
        try:
            s.rmdir()
        except OSError:
            pass
    tmp = src / "tmp"
    if stat.S_ISDIR(strict_mode(tmp)):
        stuck = sorted(p.name for p in tmp.iterdir())
        left += [f"tmp/{n}" for n in stuck]
        if not stuck:
            tmp.rmdir()
    try:
        src.rmdir()
    except OSError:
        pass
    _fsync_dir(src.parent)
    return clashes, left


def move_back(ctx: Ctx, rel: str) -> tuple[list[tuple[str, str]], list[str]]:
    """archive/<rel> back to agents/<rel>: a rename when agents/<rel> is
    absent, else a merge into what is there."""
    src, dst = ctx.archive / rel, ctx.root / rel
    if not dst.exists():
        try:
            os.rename(src, dst)
            _fsync_dir(ctx.archive)
            _fsync_dir(ctx.root)
            return [], []
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
    return merge(src, dst)


def arrived(box: Path) -> list[str]:
    """Each file in BOX's new/ and cur/, by path, Message-ID and Subject,
    read from its header block only: the body is never read.

    TOTAL: it never raises. It runs in the
    verify step, before any box goes BACK, so one raise here would leave
    every moved box archived while its row may still route it. A leaf that
    cannot be listed, or a header block the stdlib parser chokes on (CPython
    3.13 raises UnboundLocalError on a Message-ID of `<x@[ `), is reported
    as unreadable and the box still goes BACK."""
    out = []
    for leaf in ("new", "cur"):
        d = box / leaf
        try:
            # A strict stat: when BOX itself is unsearchable (K7) it raises.
            # `d.is_dir()` raised there only up to CPython 3.13; from 3.14 it
            # says False and the box would read as holding no mail.
            if not stat.S_ISDIR(strict_mode(d)):
                continue
            names = sorted(d.iterdir())
        except OSError as exc:
            out.append(f"{leaf}/ <unreadable: {exc.strerror or exc}>")
            continue
        for f in names:
            try:
                with open(f, "rb") as fh:
                    head = fh.read(65536)
            except OSError:
                continue
            cut = min((i for i in (head.find(b"\n\n"), head.find(b"\r\n\r\n")) if i >= 0),
                      default=len(head))
            try:
                msg = BytesParser(policy=default_policy).parsebytes(head[:cut] + b"\n\n",
                                                                   headersonly=True)
                out.append(f"{leaf}/{f.name} {msg.get('Message-ID', '<no Message-ID>')} "
                           f"\"{msg.get('Subject', '')}\"")
            except Exception as exc:        # any parser fault: name it, never abort the run
                out.append(f"{leaf}/{f.name} <headers unreadable: {type(exc).__name__}>")
    return out


# ── --apply ────────────────────────────────────────────────────────────────

@dataclass
class Moved:
    rel: str
    keys: list[str]
    files: int = 0
    size: int = 0
    newest: float = 0.0
    recovered: bool = False
    state: str = "moved"        # moved -> archive | back
    reason: str = ""


class _Stop:
    """SIGINT/SIGTERM/SIGHUP only set a flag: the batch stops moving and
    still verifies what it moved (F4)."""

    def __init__(self, err: Callable[[str], None]) -> None:
        self.hit = 0
        self.err = err
        self.old: dict[int, object] = {}

    def __call__(self, signum, frame) -> None:
        self.hit += 1
        if self.hit > 1:
            self.err("signal again: ignored until the verify has written its ledger lines")

    def __enter__(self) -> "_Stop":
        for name in SIGNALS:
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                self.old[sig] = signal.signal(sig, self)
            except (ValueError, OSError):       # not the main thread: no handler
                pass
        return self

    def __exit__(self, *exc) -> None:
        for sig, old in self.old.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass


def verify_gone(ctx: Ctx, text: str | None, m: Moved) -> tuple[str, str]:
    """GONE, PRESENT or UNKNOWN, read from the WRITTEN map (never from
    `--print`, which walks the tree and could never list a moved box).
    Under the drop there is no written map: the print IS what every send
    resolves against, so TEXT is the print (`Ctx.mailbox_text`)."""
    if text is None:
        return "UNKNOWN", "map unreadable"
    if mailbox_keys(text, m.rel):
        return "PRESENT", "regenerate did not take"
    if not m.keys:
        # BN6: a `moving` line with no keys (or a box with no row) cannot ask
        # the .db anything; the text read stands alone, and says so.
        return "GONE", "text-only verify: no keys were recorded"
    if ctx.use_db():
        maps = _seat._postfix_maps("virtual_mailbox_maps")
        if not maps:
            return "UNKNOWN", "postconf could not say which .db postfix reads"
        for key in m.keys:
            if _seat._postmap_lookup(key, maps):
                return "PRESENT", f"the .db still routes {key}"
    return "GONE", ""


def routed_now(ctx: Ctx, text: str | None, rel: str) -> bool:
    """REL has its row back, in the text and (when postconf answers) the .db."""
    if text is None:
        return False
    keys = mailbox_keys(text, rel)
    if not keys:
        return False
    if ctx.use_db():
        maps = _seat._postfix_maps("virtual_mailbox_maps")
        return bool(maps) and any(_seat._postmap_lookup(k, maps) for k in keys)
    return True


def alias_db_hit(ctx: Ctx, rel: str) -> str | None:
    """By default, when postconf answers, ask the alias .db postfix READS
    for the box's own keys too (the keys on its mailbox rows, never
    recomputed). The text and the .db differ only after a failed
    postmap. A destination-only row cannot be asked by `postmap -q`; the
    text check covers that. The key that hits, or None."""
    if not ctx.use_db():
        return None
    maps = _seat._postfix_maps("virtual_alias_maps")
    if not maps:
        raise Unknown("postconf could not say which alias .db postfix reads")
    for key in mailbox_keys(ctx.mailbox_text() or "", rel):
        if _seat._postmap_lookup(key, maps):
            return key
    return None


def _recheck(ctx: Ctx, row: Row, next_names: list) -> str | None:
    """(a), (b) and (c) once more, immediately before the move. Returns why
    the box stays, or None. The next-regenerate map is asked again when the
    last answer is older than NEXT_TTL."""
    box = ctx.root / row.box.name
    live = live_sessions(ctx.env)                    # Unknown propagates: stop moving
    who = owner(row.box.name, live)
    if who:
        return f"(a) now live: {who[:8]}"
    if time.monotonic() - next_names[0] > NEXT_TTL:
        next_names[:] = [time.monotonic(), routed_names(ctx.alias_next())]
    n = row.box.name.lower()
    if n in routed_names(ctx.alias_now()) or n in next_names[1]:
        return "(b) now has an alias row"
    hit = alias_db_hit(ctx, row.box.name)               # Unknown propagates: stop moving
    if hit:
        return f"(b) the alias .db still names {hit}"
    newest, _f, _s = activity(box)
    if newest >= ctx.now - ctx.days * 86400 or newest > row.newest:
        return "(c) a new file since the listing"
    return None


def apply(ctx: Ctx, only: set[str] | None = None) -> int:
    """Move every candidate, atomically per box. Exit 0 all ARCHIVED; 3
    preflight refused new moves (after any recovery); 4 not clean."""
    c = census(ctx, only)
    pf = preflight(ctx, c.live_err, c.alias_err)
    for note in pf.notes:
        ctx.out(f"preflight: {note}")
    for problem in pf.problems:
        ctx.err(f"preflight: ✗ {problem}")
    pending = interrupted(ctx.archive) if ctx.archive.is_dir() else []
    # Recovery needs only the lock, a generator and a readable mailbox map:
    # never P2-P4, which guard NEW moves. Without P5 or P6 a dead
    # run's boxes go BACK by rename, with no regenerate: the safe state, and
    # exit 4.
    recover_blocked = [p for p in pf.problems if p.startswith(("P5", "P6"))]
    if pf.problems and not pending:
        return 3
    candidates = [] if pf.problems else [r for r in c.rows if r.candidate]
    if not candidates and not pending:
        ctx.out(total(c, ctx.days))
        return 0
    try:
        # 0700 when made here, never re-moded when it was there. Its
        # parent is the mail root's: preflight P1 refused before this if absent.
        make_private_dirs(ctx.archive)
        take_lock(ctx.archive, ctx.actor)
    except Held as exc:
        ctx.err(f"refused: the archive lock is held: {exc}")
        return 3
    try:
        # Read again under the lock: a run that held it may have finished
        # its boxes since the first read.
        pending = interrupted(ctx.archive)
        with _Stop(ctx.err) as stop:
            return _apply_locked(ctx, c, pf, candidates, pending, stop, recover_blocked)
    finally:
        release_lock(ctx.archive)


def _apply_locked(ctx: Ctx, c: Census, pf: Preflight, candidates: list[Row],
                  pending: list[tuple[str, list[str]]], stop: _Stop,
                  recover_blocked: list[str] | None = None) -> int:
    say, warn = ctx.out, ctx.err
    moved: list[Moved] = []
    unclean = False
    # 1. Recovery first: what a dead run moved and never verified.
    for rel, f in pending:
        keys = [] if len(f) < 9 or f[8] == "-" else f[8].split(",")
        if recover_blocked and (ctx.archive / rel).is_dir():
            # No generator, or no map to verify against: never leave it
            # archived while its old row may still route it. Back, by rename.
            n, files, size = activity(ctx.archive / rel)
            m = Moved(rel, keys, files, size, n, recovered=True, state="back",
                      reason=f"recovery blocked ({recover_blocked[0]}): back by rename, no regenerate")
            if _back(ctx, m) and not routed_now(ctx, ctx.mailbox_text(), rel):
                m.reason += "; BACK but UNROUTED until a regenerate"
            moved.append(m)
            continue
        if (ctx.archive / rel).is_dir():
            n, files, size = activity(ctx.archive / rel)
            moved.append(Moved(rel, keys, files, size, n, recovered=True))
            say(f"INTERRUPTED {rel}: finishing a stopped run's move")
        elif (ctx.root / rel).is_dir():
            ledger_append(ctx.archive, "back", rel, actor=ctx.actor, reason="never moved")
            say(f"BACK {rel}: never moved (a stopped run's intent line, the box still in agents/)")
        else:
            ledger_append(ctx.archive, "back", rel, actor=ctx.actor, reason="missing from both trees")
            warn(f"MISSING {rel}: a `moving` line, and the box is in neither agents/ nor archive/")
            unclean = True
    # 2. New moves, each re-checked first.
    done = 0
    next_names: list = []
    if candidates:
        try:
            next_names = [time.monotonic(), routed_names(ctx.alias_next())]
        except Unknown as exc:
            # Recovered boxes above still get their regenerate and verify.
            warn(f"stopped moving: {exc}")
            unclean = True
            candidates = []
    for row in candidates:
        if stop.hit:
            break
        rel = row.box.name
        _hook("before_move", rel)
        try:
            why = _recheck(ctx, row, next_names)
        except Unknown as exc:
            warn(f"stopped moving: {exc}")
            unclean = True
            break
        if why:
            say(f"KEEP      {rel}  {why}")
            continue
        if (ctx.archive / rel).exists():
            warn(f"refused {rel}: archive/{rel} already exists; message box unarchive {rel} --merge --apply")
            continue
        text = ctx.mailbox_text()
        keys = mailbox_keys(text or "", rel)
        if not keys:
            warn(f"not moved {rel}: no mailbox row for it before the move (BN2)")
            continue
        newest, files, size = activity(ctx.root / rel)
        try:
            ledger_append(ctx.archive, "moving", rel, actor=ctx.actor, files=files, size=size,
                          newest=newest, keys=keys)
            os.rename(ctx.root / rel, ctx.archive / rel)
        except OSError as exc:
            # One box's failure must never abandon the boxes already moved:
            # they still get the regenerate and the verify.
            warn(f"not moved {rel}: rename failed: {exc.strerror or exc}")
            unclean = True
            if not (ctx.archive / rel).exists():
                try:
                    ledger_append(ctx.archive, "back", rel, actor=ctx.actor,
                                  reason=f"rename failed: {exc.strerror or exc}")
                except OSError:
                    pass                # the next run reads `moving`, finds it in agents/
            continue
        _fsync_dir(ctx.root)
        _fsync_dir(ctx.archive)
        moved.append(Moved(rel, keys, files, size, newest))
        done += 1
        _hook("after_move", rel)
    stopped = bool(stop.hit) and done < len(candidates)
    if not moved or recover_blocked:
        return _finish(ctx, c, moved, unclean, stopped, done, len(candidates), pf)
    # 3. One whole regenerate, then the verify, box by box.
    say(f"regenerate: {ctx.regen()}")
    _hook("after_regen", 1)
    text = ctx.mailbox_text()
    for m in moved:
        # 4. W first: a box re-created in the window (before the regenerate,
        # so its row is back; or after it) goes back, merged, naming the mail.
        if (ctx.root / m.rel).exists():
            got = arrived(ctx.root / m.rel)
            m.state = "back"
            m.reason = f"RE-CREATED: {len(got)} mail arrived: " + "; ".join(got)
            continue
        state, why = verify_gone(ctx, text, m)
        if state != "GONE":
            m.state, m.reason = "back", why
        elif why:
            say(f"note {m.rel}: {why}")
    backs = [m for m in moved if m.state == "back" and _back(ctx, m)]
    if backs:
        say(f"regenerate (round 2): {ctx.regen()}")
        _hook("after_regen", 2)
        text = ctx.mailbox_text()
        for m in backs:
            if not routed_now(ctx, text, m.rel):
                m.reason += (f"; BACK but UNROUTED: agents/{m.rel} has no vmailbox row; mail to it "
                             "bounces until a regenerate (any birth, or running vmailbox-gen, "
                             "restores it)")
        for m in moved:
            if m.state != "moved":
                continue
            if (ctx.root / m.rel).exists():          # re-created again: merged, no third regenerate
                got = arrived(ctx.root / m.rel)
                m.state = "back"
                m.reason = (f"RE-CREATED after the second regenerate: {len(got)} mail arrived: "
                            + "; ".join(got))
                if not routed_now(ctx, text, m.rel):
                    m.reason += "; BACK but UNROUTED until a regenerate"
                _back(ctx, m)
            elif verify_gone(ctx, text, m)[0] != "GONE":
                m.state, m.reason = "back", "row back after the second regenerate"
                _back(ctx, m)
    for m in moved:
        if m.state == "moved":
            m.state = "archive"
    return _finish(ctx, c, moved, unclean, stopped, done, len(candidates), pf)


def _back(ctx: Ctx, m: Moved) -> bool:
    """Move M back (rename, or merge into a re-created box). One box's
    failure never abandons the others: an
    OSError leaves THIS box `moving` in the ledger, so the next --apply
    finds it INTERRUPTED and tries again, and says so. False: not back."""
    try:
        clashes, left = move_back(ctx, m.rel)
    except OSError as exc:
        m.state = "moving"
        m.reason = (f"{exc.strerror or exc}: archive/{m.rel} may still be routed "
                    f"({m.reason or 'going back'}); the next --apply finishes it first")
        return False
    for was, now in clashes:
        m.reason += f"; name clash {was}: both kept, archived copy at {now}"
    if left:
        m.reason += f"; left in archive/{m.rel}: {', '.join(left)}"
    return True


def _finish(ctx: Ctx, c: Census, moved: list[Moved], unclean: bool, stopped: bool,
            done: int, of: int, pf: Preflight) -> int:
    for m in moved:
        ledger_append(ctx.archive, m.state, m.rel, actor=ctx.actor, files=m.files, size=m.size,
                      newest=m.newest, reason=m.reason, keys=m.keys)
        if m.state == "archive":
            ctx.out(f"ARCHIVED {m.rel}  ({m.files} files; row verified gone)")
        elif m.state == "moving":
            ctx.err(f"NOT BACK {m.rel}: {m.reason}")
        else:
            ctx.err(f"BACK {m.rel}: {m.reason}")
    archived = sum(1 for m in moved if m.state == "archive")
    back = sum(1 for m in moved if m.state == "back")
    stuck = sum(1 for m in moved if m.state == "moving")
    tail = f"-- {archived:,} archived, {back:,} back"
    if stuck:
        tail += f", {stuck:,} NOT back (the next --apply finishes them)"
    if stopped:
        tail += f"; interrupted after {done} of {of}"
    ctx.out(tail)
    if back or stuck or unclean or stopped:
        return 4
    return 3 if pf.problems else 0


# ── unarchive ──────────────────────────────────────────────────────────────

def unarchive(ctx: Ctx, rel: str, merge_ok: bool, do: bool) -> int:
    """archive/<rel> back to agents/<rel>. Exit 0 ok; 2 no such archived
    box; 3 refused (a box with files there and no --merge, the lock held,
    no generator); 4 moved but its row did not come back."""
    if not rel or "/" in rel or rel.startswith(".") or not (ctx.archive / rel).is_dir():
        ctx.err(f"message box unarchive: no archived box {rel!r} under {ctx.archive}")
        return 2
    src, dst = ctx.archive / rel, ctx.root / rel
    there = dst.exists()
    _n, files_there, _s = activity(dst) if there else (0, 0, 0)
    if there and files_there and not merge_ok:
        ctx.err(f"refused: agents/{rel} holds {files_there} file(s); --merge joins the two, "
                "never overwriting")
        return 3
    plan = ("rename back" if not there else
            "remove the empty birth triad, then rename back" if not files_there else
            "merge into agents/ by link+unlink; a clash keeps both")
    if not do:
        ctx.out(f"would {plan}: {src} -> {dst}, then regenerate and verify its row (--apply does it)")
        return 0
    # Under the drop no map is written, so no map-WRITING generator is needed,
    # and the row is verified by the drop's own printed map (`Ctx.dropping`).
    # A `--maps DIR` names the maps to read, whatever the carrier.
    if not ctx.dropping and generator(ctx.env, ctx.mail_root) is None:
        ctx.err("refused: no vmailbox-gen to regenerate with")
        return 3
    try:
        take_lock(ctx.archive, ctx.actor)
    except Held as exc:
        ctx.err(f"refused: the archive lock is held: {exc}")
        return 3
    try:
        newest, files, size = activity(src)
        clashes: list[tuple[str, str]] = []
        left: list[str] = []
        if there and not files_there:
            try:
                for leaf in LEAVES:
                    if (dst / leaf).exists():
                        (dst / leaf).rmdir()
                dst.rmdir()
            except OSError:
                ctx.err(f"refused: a file arrived in agents/{rel} while emptying it; run with --merge")
                return 3
        if dst.exists():
            clashes, left = merge(src, dst)
        else:
            os.rename(src, dst)
            _fsync_dir(ctx.archive)
            _fsync_dir(ctx.root)
        ctx.out(f"regenerate: {ctx.regen()}")
        ok = routed_now(ctx, ctx.mailbox_text(), rel)
        reason = "merged" if clashes or left or (there and files_there) else "renamed"
        for was, now in clashes:
            reason += f"; name clash {was}: both kept, archived copy at {now}"
            ctx.out(f"clash {was}: both kept, the archived copy is {now}")
        if left:
            reason += f"; left in archive/{rel}: {', '.join(left)}"
            ctx.out(f"left in archive/{rel} (unfinished deliveries): {', '.join(left)}")
        if not ok:
            reason += "; UNROUTED"
        ledger_append(ctx.archive, "unarchive", rel, actor=ctx.actor, files=files, size=size,
                      newest=newest, reason=reason)
        if not ok:
            ctx.err(f"UNROUTED {rel}: back in agents/, but its vmailbox row did not come back; "
                    "mail to it bounces until a regenerate")
            return 4
        ctx.out(f"UNARCHIVED {rel}: back in agents/, its row verified")
        return 0
    finally:
        release_lock(ctx.archive)
