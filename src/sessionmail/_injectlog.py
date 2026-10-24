"""The pager inject log: one JSONL row per string sessionmail puts into an
agent's context, sized on the exact string emitted.

openspec/changes/pager-inject-log (PROPOSED). The rows live in the SESSION dir:

    <session-dir>/log/mail/pager.jsonl     the rows (0600)
    <session-dir>/log/mail/pager.1.jsonl   the one rolled generation
    <session-dir>/log/mail/seq             the seq counter and its lock
    <session-dir>/log/mail/quiet           the empty-run counter (§16.5)
    <session-dir>/log/mail/capture/        the text, when capture is on (0700,
                                           `.gitignore` of `*`; <seq>.txt 0600)

`<session-dir>` is the parent of the session's `mail/` root, the dir
`_seat.session_root` already resolves. No session id: nothing is written,
and nothing is made.

Every writer of a session's log - the monitor, each hook process and (in
its own stdlib copy) the mail-guard - takes ONE exclusive `flock` on
`seq`, bumps the counter, rolls the file at ROLL_AT, appends its row with
one `O_APPEND` write, and lets go. So `seq` is distinct across writers and
never derived from the file's length, and two writers never both roll
.

A row costs the injection nothing: it is written AFTER the text was
emitted, and `write` never raises (a failure costs the row, never the
injection). Tokens are `_tokens.estimate`, in-process: no tokenizer, no
subprocess, no network.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import _tokens

#: Under the session dir: `log/mail/` (the path already starts with .session;
#: it replaces an earlier location).
LOG_DIR = ("log", "mail")
FILE = "pager.jsonl"
ROLLED = "pager.1.jsonl"
SEQ_FILE = "seq"
#: Past this, `pager.jsonl` becomes `pager.1.jsonl` (one generation).
ROLL_AT = 16 * 1024 * 1024
#: The token method, named on every row so nobody reads it as a count.
TOK = "chars/4"
#: The seq counter is written fixed-width at offset 0, so it is never
#: truncated first: a crash mid-write cannot leave it empty and restart seq.
SEQ_WIDTH = 20

#: `via` for each path (spec "One row per injection, on every path that injects").
VIA_PROMPT = "prompt"
VIA_BATCH = "batch"
VIA_START = "start"
VIA_STOP = "stop"
VIA_MONITOR = "monitor"
VIA_LISTEN = "listen"
#: The empty-run counter (spec "Empty runs are counted, not logged"): one
#: fixed-width line, `count total_ms max_ms`, rewritten in place under an
#: exclusive `flock` on the file itself. Cumulative, never reset (§16.5).
QUIET_FILE = "quiet"
QUIET_WIDTH = 20
#: Full-content capture (spec "Full content is captured only when switched
#: on"). The switch is the `capture` key, layered
#: host -> seat -> session through `_layer.read`; the env wins both ways.
CAPTURE_KEY = "capture"
CAPTURE_VAR = "SESSIONMAIL_CAPTURE"
CAPTURE_ON = ("on", "1", "yes", "true")          # the PAGE_LIST_ON vocabulary
CAPTURE_OFF = ("off", "0", "no", "false")
#: An EXIT never waits on its log. A listener's way out (`_listen`'s
#: `finally`, the followed exec, a hand listen's page) takes the `seq` and
#: `quiet` locks under ONE deadline, this many seconds from the exit: tried
#: with LOCK_NB every `LOCK_STEP` (the `message._receipt_lock` precedent),
#: then the row is dropped and said ONCE on stderr. Every other writer (the
#: hooks, the mail-guard, the in-loop rows) passes no deadline and waits as
#: before. The env knob is read at the exit; 0, a negative, a non-number or
#: a non-finite value reads as the default (the safe reading).
LOCK_SECONDS_VAR = "SESSIONMAIL_LOG_LOCK_SECONDS"
EXIT_LOCK_SECONDS = 1.0
LOCK_STEP = 0.05
CAPTURE_DIR = "capture"
#: Past this many bytes of capture per session, capture stops.
CAPTURE_CAP = 64 * 1024 * 1024
#: Under `capture/`, both written only under the seq lock: the bytes
#: captured so far (fixed width, as `seq`), and the mark that capture is
#: full - made O_EXCL, so exactly one writer says `capture full`.
CAPTURE_SIZE = ".size"
CAPTURE_FULL = ".full"

#: `capped`, the cap bit that applied, most binding first when several did.
CAPS = ("give_by", "room", "batch", "text")


@dataclass
class Ride:
    """What a ride-along scoop hands UP to the hook's emit:
    the items behind its lines, in emit order, and its counts. A path that
    never ran the scoop logs a fresh one: `items: []`, zero counts."""

    items: list[dict[str, Any]] = field(default_factory=list)
    superseded: int = 0
    held: int = 0
    past_budget: int = 0
    capped: str | None = None

    def cap(self, bit: str) -> None:
        """Record cap BIT, keeping the most binding of those that applied."""
        if self.capped is None or CAPS.index(bit) < CAPS.index(self.capped):
            self.capped = bit


def item(mid: str | None, *, kind: str, view: str, given: int, in_mail: int | None,
         cut: bool) -> dict[str, Any]:
    """One `items[]` object. MID loses its angle brackets, as marks write it;
    a mail with no Message-ID has `id: null`."""
    mid = (mid or "").strip().strip("<>")
    return {"id": mid or None, "kind": kind, "view": view,
            "given": given, "in_mail": in_mail, "cut": cut}


def tool_use_ids(payload: Mapping[str, Any]) -> list[str]:
    """The tool calls a hook run answers: `tool_calls[].tool_use_id`, then a
    top-level `tool_use_id` (PreToolUse), the missing ones dropped; `[]`
    when there are none. The inner key is Claude Code's hooks reference,
    "PostToolBatch input" (code.claude.com/docs/en/hooks, read 2030-06-09):
    each `tool_calls` element carries `tool_name`, `tool_input`,
    `tool_use_id` and `tool_response`. Never anything else of the call:
    its input and response are not ours to log."""
    out: list[str] = []
    calls = payload.get("tool_calls") if isinstance(payload, Mapping) else None
    if isinstance(calls, list):
        for call in calls:
            if isinstance(call, Mapping):
                tid = call.get("tool_use_id")
                if isinstance(tid, str) and tid:
                    out.append(tid)
    top = payload.get("tool_use_id") if isinstance(payload, Mapping) else None
    if isinstance(top, str) and top and top not in out:
        out.append(top)
    return out


def sizes(s: str) -> dict[str, Any]:
    """The four size fields, on the exact string S that was emitted (its
    trailing newline, if the writer adds one, not included)."""
    return {"chars": len(s), "lines": s.count("\n") + 1 if s else 0,
            "tokens_est": _tokens.estimate(s), "tok": TOK}


def log_dir(session: str, env: Mapping[str, str] | None = None) -> Path | None:
    """`<session-dir>/log/mail/`, or None when there is no session."""
    from . import _seat
    root = _seat.session_root(session, dict(env) if env is not None else None)
    if root is None:
        return None
    return root.parent.joinpath(*LOG_DIR)


def _make(d: Path) -> None:
    """D as a 0700 directory, its parents made plainly (`log/` is shared
    with other tools' logs; only ours is private)."""
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        d.mkdir(mode=0o700)
    except FileExistsError:
        return
    os.chmod(d, 0o700)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def lock_seconds(env: Mapping[str, str] | None = None) -> float:
    """The exit's lock budget in seconds: `SESSIONMAIL_LOG_LOCK_SECONDS`,
    or `EXIT_LOCK_SECONDS` when it is unset, 0, negative, not a number or not
    finite. Never raises."""
    env = os.environ if env is None else env
    try:
        value = float((env.get(LOCK_SECONDS_VAR) or "").strip())
    except ValueError:
        return EXIT_LOCK_SECONDS
    return value if math.isfinite(value) and value > 0 else EXIT_LOCK_SECONDS


@dataclass(frozen=True)
class Deadline:
    """When an exit stops waiting on its log's locks: `at` on
    `time.monotonic()`, and the `seconds` it was given, which the stderr
    line names. ONE per exit, shared by every lock that exit takes."""

    at: float
    seconds: float


def exit_deadline(env: Mapping[str, str] | None = None) -> Deadline:
    """A fresh deadline for one exit, `lock_seconds` from now."""
    seconds = lock_seconds(env)
    return Deadline(time.monotonic() + seconds, seconds)


class LockHeld(Exception):
    """A log lock (PATH) another process still held at an exit's DEADLINE."""

    def __init__(self, path: Path, deadline: Deadline) -> None:
        super().__init__(f"{path} still locked by another process")
        self.path = path
        self.deadline = deadline


def _flock(fd: int, op: int, path: Path, deadline: Deadline | None) -> None:
    """`flock(FD, OP)`. With no DEADLINE it waits, as every writer always
    has. With one: LOCK_NB, retried every `LOCK_STEP` until DEADLINE, then
    `LockHeld`. A signal handler that raises still ends the wait."""
    if deadline is None:
        fcntl.flock(fd, op)
        return
    while True:
        try:
            fcntl.flock(fd, op | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            left = deadline.at - time.monotonic()
            if left <= 0:
                raise LockHeld(path, deadline) from None
            time.sleep(min(LOCK_STEP, left))


def say_held(what: str, held: list[LockHeld]) -> None:
    """The ONE stderr line for a row an exit dropped (or cut) because HELD
    locks outlived its deadline: it names each lock and the budget. stdout
    is never touched (a monitor's stdout is its page channel). Never raises."""
    try:
        paths = " and ".join(str(h.path) for h in held)
        seconds = held[0].deadline.seconds
        sys.stderr.write(f"pager: {what}: {paths} still locked by another process after "
                         f"the {seconds:g} s exit budget ({LOCK_SECONDS_VAR}); "
                         f"an exit never waits on its log\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - saying it is never why an exit fails
        pass


def _next_seq(lock: int) -> int:
    """The next seq, under the lock LOCK holds: written back FIRST, so a row
    that then fails leaves a gap, never a repeat. Fixed width, no truncate
    (SEQ_WIDTH)."""
    raw = os.pread(lock, SEQ_WIDTH + 1, 0)
    try:
        seq = int(raw.decode("ascii").strip() or 0) + 1
    except ValueError:
        seq = 1
    os.pwrite(lock, f"{seq:0{SEQ_WIDTH}d}\n".encode("ascii"), 0)
    os.fchmod(lock, 0o600)
    return seq


def _put(d: Path, row: dict[str, Any], limit: int) -> None:
    """ROW as one line of D's `pager.jsonl`, rolled first when it is LIMIT
    bytes or more (Q8, inside the lock, §18.1). One O_APPEND write."""
    path = d / FILE
    try:
        if os.stat(path).st_size >= limit:
            os.replace(path, d / ROLLED)
    except FileNotFoundError:
        pass
    data = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def capture_name(seq: int) -> str:
    """`capture/<seq>.txt`, zero-padded (`000042.txt`): the row's `capture`
    field, relative to `log/mail/`."""
    return f"{CAPTURE_DIR}/{seq:06d}.txt"


def _capture(d: Path, seq: int, text: str, cap: int) -> tuple[str | None, bool]:
    """Under the seq lock: TEXT, as UTF-8, into `capture/<seq>.txt`.
    Returns (the name for the row, or None; whether THIS call found capture
    full and so owes the one `error` row). `capture/` is made as `mail/` is
    (0700, a `.gitignore` of `*`); the file is 0600 and made O_EXCL, so a
    seq seen twice (a torn `seq` file) never overwrites a capture. A write
    that fails costs the capture, never the row."""
    from . import _seat
    cd = d / CAPTURE_DIR
    try:
        _seat._settings().ensure_private_dir(cd)
        if (cd / CAPTURE_FULL).exists():
            return None, False
        data = text.encode("utf-8")
        size = os.open(cd / CAPTURE_SIZE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            raw = os.pread(size, SEQ_WIDTH + 1, 0)
            try:
                used = int(raw.decode("ascii").strip() or 0)
            except ValueError:
                used = 0
            if used + len(data) > cap:
                # Full. The mark is made once, O_EXCL: the writer that
                # makes it writes the one `capture full` row; no one after.
                try:
                    os.close(os.open(cd / CAPTURE_FULL,
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
                except FileExistsError:
                    return None, False
                return None, True
            name = capture_name(seq)
            fd = os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.fchmod(fd, 0o600)
                _write_all(fd, data)
            finally:
                os.close(fd)
            os.pwrite(size, f"{used + len(data):0{SEQ_WIDTH}d}\n".encode("ascii"), 0)
            os.fchmod(size, 0o600)
            return name, False
        finally:
            os.close(size)
    except OSError:
        return None, False


def append(d: Path, row: dict[str, Any], *, roll_at: int | None = None,
           capture: str | None = None, cap: int | None = None,
           deadline: Deadline | None = None) -> int:
    """Append ROW to D's `pager.jsonl` under the seq lock and return its
    seq. With CAPTURE (the exact emitted string), it is first written to
    `capture/<seq>.txt` under the same lock, and the row names it; when
    that finds capture full, one `error` row (`what: capture full`) follows
    the row, with its own seq. With a DEADLINE (an exit's) a lock
    still held then raises `LockHeld` and nothing is written; with none it
    waits. Raises on failure: `write` is the never-raises door."""
    limit = ROLL_AT if roll_at is None else roll_at
    lock = os.open(d / SEQ_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _flock(lock, fcntl.LOCK_EX, d / SEQ_FILE, deadline)
        seq = _next_seq(lock)
        row["seq"] = seq
        full = False
        if capture is not None:
            name, full = _capture(d, seq, capture,
                                  CAPTURE_CAP if cap is None else cap)
            if name is not None:
                row["capture"] = name
        _put(d, row, limit)
        if full:
            # The envelope, `via` the path whose capture found it full (spec:
            # an error row's `via` is the writer that wrote it).
            _put(d, {"ts": row["ts"], "tool": "mail", "event": "error", "via": row["via"],
                     "seq": _next_seq(lock), "sid": row["sid"], "seat": row["seat"],
                     "pid": row["pid"], "what": "capture full"}, limit)
        return seq
    finally:
        os.close(lock)                   # closing the fd lets the lock go


def capture_on(seat: Any, session: str, env: Mapping[str, str] | None = None) -> bool:
    """Is capture on for SEAT in SESSION? `SESSIONMAIL_CAPTURE` (on/1/yes/
    true, off/0/no/false) wins over every layer, both ways. Otherwise the
    most specific layer holding a boolean `capture` decides (session over
    seat over host), each read through `_layer.read`, as `pager_required`
    reads its key. With the layered reader missing, the host file alone.
    Off when nothing says on, or anything fails: never raises."""
    try:
        from . import _layer, _seat
        from . import _settings as host_settings
        env = os.environ if env is None else env
        raw = (env.get(CAPTURE_VAR) or "").strip().lower()
        if raw in CAPTURE_ON:
            return True
        if raw in CAPTURE_OFF:
            return False
        found = False
        reader_missing = False
        try:
            layers = _seat._required_layers(seat, session, dict(env))
        except _layer.LayerError as exc:
            layers = []
            reader_missing = _layer.NOT_INSTALLED in str(exc)
        for name, directory in layers:
            try:
                values, _origins = _layer.read(directory, name)
            except _layer.LayerError as exc:
                if _layer.NOT_INSTALLED in str(exc):
                    reader_missing = True
                    break
                continue                 # an unreadable layer decides nothing
            value = values.get(CAPTURE_KEY)
            if isinstance(value, bool):
                found = value
        if reader_missing:
            present, value, _why = host_settings.host_value(CAPTURE_KEY, env)
            found = value if present and isinstance(value, bool) else False
        return found
    except Exception:  # noqa: BLE001 - capture fails closed: off
        return False


def _seat_name(seat: Any) -> str | None:
    if seat is None:
        return None
    return getattr(seat, "address", None) or getattr(seat, "name", None)


def _envelope(session: str, *, event: str, via: str, seat: Any) -> dict[str, Any]:
    """The keys EVERY row carries, of every `event` (spec "The row names its
    source"): `seq` is filled in under the lock by `append`."""
    return {"ts": _now(), "tool": "mail", "event": event, "via": via, "seq": None,
            "sid": session, "seat": _seat_name(seat), "pid": os.getpid()}


def write(session: str, *, via: str, s: str, event: str = "inject",
          seat: Any = None, hook_event_name: str | None = None,
          tool_use_ids: list[str] | None = None, ride: Ride | None = None,
          ms: int | None = None, env: Mapping[str, str] | None = None,
          extra: Mapping[str, Any] | None = None,
          deadline: Deadline | None = None) -> int | None:
    """Log one injection of the string S by path VIA, for SESSION. Returns
    the row's seq, or None when no row was written: no session, an empty
    S, or ANY failure. Never raises (spec: "Writing a row SHALL NEVER
    raise into the injection"). EXTRA adds a path's own keys after the
    common ones (`listen`'s `stderr_chars`, spec "sized on the exact string").
    DEADLINE: only a page on its way out passes one; a `seq` lock
    still held then drops the row, said once on stderr."""
    try:
        if not s or not session or session == "-":
            return None
        d = log_dir(session, env)
        if d is None:
            return None
        ride = ride if ride is not None else Ride()
        row: dict[str, Any] = {
            **_envelope(session, event=event, via=via, seat=seat),
            "hook_event_name": hook_event_name,
            "tool_use_ids": list(tool_use_ids or []),
            **sizes(s),
            "items": list(ride.items),
            "superseded": ride.superseded, "held": ride.held,
            "past_budget": ride.past_budget, "capped": ride.capped,
            "ms": int(ms) if ms is not None else None,
        }
        if extra:
            row.update(extra)
        _make(d)
        # The text itself, only when capture is on (spec "Full content is
        # captured only when switched on"); the row names its file.
        text = s if capture_on(seat, session, env) else None
        return append(d, row, capture=text, deadline=deadline)
    except LockHeld as held:
        say_held(f"{event} row not logged", [held])
        return None
    except Exception:  # noqa: BLE001 - a row is never why an injection fails
        return None


def _lifecycle(session: str, event: str, via: str, seat: Any,
               env: Mapping[str, str] | None, deadline: Deadline | None,
               fields: Mapping[str, Any]) -> int | None:
    """`lifecycle`'s row, raising: `LockHeld` past DEADLINE, or anything."""
    if not session or session == "-":
        return None
    d = log_dir(session, env)
    if d is None:
        return None
    row = {**_envelope(session, event=event, via=via, seat=seat), **fields}
    _make(d)
    return append(d, row, deadline=deadline)


def lifecycle(session: str, *, event: str, via: str, seat: Any = None,
              env: Mapping[str, str] | None = None, deadline: Deadline | None = None,
              **fields: Any) -> int | None:
    """A listener's own `start`/`exit`/`error` row (spec "The listener logs
    its own life"): the envelope plus FIELDS, and none of the inject row's
    sizing, item or hook keys. Returns its seq, or None; never raises. No
    session: nothing. DEADLINE (an exit's only): a `seq` lock still
    held then drops the row, said once on stderr; with none it waits."""
    try:
        return _lifecycle(session, event, via, seat, env, deadline, fields)
    except LockHeld as held:
        say_held(f"{event} row not logged", [held])
        return None
    except Exception:  # noqa: BLE001 - a row is never why a listener fails
        return None


def error(session: str, *, via: str, what: str, seat: Any = None,
          env: Mapping[str, str] | None = None) -> int | None:
    """An `error` row: a write the listener depends on failed (WHAT names
    it). Best effort, as every row is."""
    return lifecycle(session, event="error", via=via, seat=seat, env=env, what=what)


def _quiet_parse(raw: bytes) -> tuple[int, int, int]:
    try:
        count, total, most = (int(x) for x in raw.decode("ascii").split())
        return count, total, most
    except ValueError:
        return 0, 0, 0


def bump_quiet(session: str, ms: int, env: Mapping[str, str] | None = None) -> bool:
    """Add one empty run of MS milliseconds to SESSION's counter. Only
    `pager hook batch`, past every gate, having run the scoop and printed
    nothing, calls this: no other path touches the file.
    True when it was counted; never raises."""
    try:
        if not session or session == "-":
            return False
        d = log_dir(session, env)
        if d is None:
            return False
        _make(d)
        fd = os.open(d / QUIET_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            count, total, most = _quiet_parse(os.pread(fd, 3 * (QUIET_WIDTH + 1), 0))
            ms = max(0, int(ms))
            w = QUIET_WIDTH
            line = f"{count + 1:0{w}d} {total + ms:0{w}d} {max(most, ms):0{w}d}\n"
            # Fixed width at offset 0, never truncated: a crash mid-write
            # cannot leave the counter empty (as SEQ_WIDTH).
            os.pwrite(fd, line.encode("ascii"), 0)
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 - a count is never why a hook fails
        return False


def _quiet(session: str, env: Mapping[str, str] | None,
           deadline: Deadline | None) -> dict[str, int]:
    """`read_quiet`'s counts, raising `LockHeld` (and only that) when the
    counter is still locked at DEADLINE."""
    count = total = most = 0
    try:
        d = log_dir(session, env) if session and session != "-" else None
        if d is not None:
            fd = os.open(d / QUIET_FILE, os.O_RDONLY)
            try:
                _flock(fd, fcntl.LOCK_SH, d / QUIET_FILE, deadline)
                count, total, most = _quiet_parse(os.pread(fd, 3 * (QUIET_WIDTH + 1), 0))
            finally:
                os.close(fd)
    except LockHeld:
        raise
    except Exception:  # noqa: BLE001 - no counter reads as none
        pass
    return {"quiet": count, "quiet_ms_total": total, "quiet_ms_max": most}


def read_quiet(session: str, env: Mapping[str, str] | None = None, *,
               deadline: Deadline | None = None) -> dict[str, int]:
    """SESSION's cumulative empty runs, as an `exit` row carries them:
    `quiet`, `quiet_ms_total`, `quiet_ms_max`. Zeros when there is no
    counter (or it cannot be read). Never raises; makes nothing. DEADLINE
    (an exit's only): a counter still locked then gives `{}`, no
    counts at all (zeros would be a false count), said once on stderr."""
    try:
        return _quiet(session, env, deadline)
    except LockHeld as held:
        say_held("quiet counts not read", [held])
        return {}


def exit_row(session: str, *, via: str, seat: Any = None,
             env: Mapping[str, str] | None = None, deadline: Deadline | None = None,
             **fields: Any) -> int | None:
    """The listener's `exit` row, carrying the quiet counts, as `lifecycle`
    plus `read_quiet` wrote it, with BOTH locks under the ONE DEADLINE
    (a fresh `exit_deadline()` when none is given): the way out
    never waits on its log. Past it: `quiet` still held, the row is written
    without its quiet counts; `seq` still held, the row is dropped. Either
    way it is said ONCE on stderr, naming the locks and the budget. Returns
    the row's seq, or None; never raises."""
    deadline = exit_deadline(env) if deadline is None else deadline
    held: list[LockHeld] = []
    try:
        quiet = _quiet(session, env, deadline)
    except LockHeld as exc:
        quiet = {}
        held.append(exc)
    try:
        seq = _lifecycle(session, "exit", via, seat, env, deadline, {**fields, **quiet})
    except LockHeld as exc:
        say_held("exit row not logged", held + [exc])
        return None
    except Exception:  # noqa: BLE001 - a row is never why a listener fails
        return None
    if held:
        say_held("exit row logged without its quiet counts", held)
    return seq


def read_rows(session: str, env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Every row of SESSION's log, oldest first: the rolled generation, then
    `pager.jsonl`. A line that is not a JSON object is skipped (a torn last
    line, a hand edit). Empty when there is no session or no log. Reads
    only; never raises."""
    out: list[dict[str, Any]] = []
    try:
        d = log_dir(session, env) if session and session != "-" else None
    except Exception:  # noqa: BLE001
        d = None
    if d is None:
        return out
    for name in (ROLLED, FILE):
        try:
            text = (d / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


#: The ride-along's paths: what `pager status` calls "rides".
RIDE_VIAS = (VIA_BATCH, VIA_PROMPT)


def today_totals(session: str, *, today: str | None = None,
                 env: Mapping[str, str] | None = None) -> dict[str, int]:
    """SESSION's `inject` rows dated TODAY (`YYYY-MM-DD`, local; the row's
    `ts` is local time with its offset, so its first ten characters are its
    local date): `injections`, `tokens` (the sum of `tokens_est`), `rides`
    (the tokens of the ride-along's rows, `batch` and `prompt`), and the
    chars given to `mail` and to `heard` items, as `mail`/`heard` tokens
    (chars // 4). README Requirement 8."""
    day = today or datetime.now().astimezone().date().isoformat()
    n = tokens = rides = mail = heard = 0
    for row in read_rows(session, env):
        if row.get("event") != "inject" or str(row.get("ts", ""))[:10] != day:
            continue
        est = row.get("tokens_est")
        est = est if isinstance(est, int) else 0
        n += 1
        tokens += est
        if row.get("via") in RIDE_VIAS:
            rides += est
        for it in row.get("items") or []:
            given = it.get("given") if isinstance(it, dict) else None
            if not isinstance(given, int):
                continue
            if it.get("kind") == "heard":
                heard += given
            elif it.get("kind") == "mail":
                mail += given
    return {"injections": n, "tokens": tokens, "rides": rides,
            "mail": mail // 4, "heard": heard // 4}


def elapsed_ms(started: float, now: float) -> int:
    """Whole milliseconds between two `time.monotonic()` readings."""
    return max(0, int((now - started) * 1000))
