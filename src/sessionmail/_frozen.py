"""The frozen-turn detector's pure core: find the transcript, fold it into a
little state, and judge that state.

A seat is "frozen mid-turn" when Claude Code has a turn open and has stopped
writing anything to its transcript for a long time. Two rules say so:

- **2a, silence** (`_settings.SILENCE`). G1: the sidecar says
  exactly `busy`. G2: the main turn is open. G3: every main-thread
  `tool_use` has its `tool_result`, paired by id. G4: `now - clock >= N`,
  where the clock is the LARGEST main-thread timestamp, never the last
  line's, and never a `queue-operation` entry's or a `queued_command`
  attachment's (mail arriving mid-turn writes those at once, so an
  any-entry clock never runs out on a seat that keeps getting pages). The
  silence is never counted from before the open turn's opening entry: a
  dequeue opens a turn but is not the clock (`State.opened`).
- **2b, chain** (`_settings.CHAIN`). G1 and G2 as 2a. H3: the
  newest main-thread `api-request` has no `assistant` entry after it. H4:
  `now - first >= R`, where `first` is the timestamp of the first
  `api-request` of that unanswered run.

Nothing here reads `system/api_error`: a retry shows as a fresh
`api-request`, which resets G4.

The pure core is three pieces, and only `transcript` and `Tail.read` touch
the disk:

- `transcript(session, env)`: the ONE file
  `<CLAUDE_CONFIG_DIR or ~/.claude>/projects/*/<session>.jsonl`. Zero or two
  matches mean "not judged", with the reason. Claude Code's project-slug rule
  is never rebuilt.
- `Tail`: reads the transcript incrementally (a byte offset, a partial last
  line carried over, a reset on a new inode or a shorter file) and keeps a
  `State`, never the lines.
- `judge(state, sidecar_status, now, settings)`: a `Verdict`. No I/O and no
  clock inside: NOW is passed in.

**NOW is wall-clock epoch seconds** (`time.time()`), never
`time.monotonic()`. G4 and H4 subtract timestamps parsed from the
transcript's ISO UTC stamps, which are epoch seconds. A monotonic now
(seconds since boot) is far smaller than any real clock, so `judge` refuses
a now earlier than the clock it would subtract, with a reason: a backwards
clock is never "frozen".

The monitor's side sits below the core:
`sidecar_status` (G1's word, a plain file read), the stamp
(`<session dir>/mail/stamps/frozen.json`), `Episode` (the edge detector and
the one-notice-per-TURN guard), `gauge` (`pager status`'s `frozen:` line),
`notify` (the one no-op seam a notice sender would plug into) and `Watcher`
(the tick the monitor calls). It sends no mail and no keys.

No I/O at import, no child process, no tmux, no network.
"""

from __future__ import annotations

import glob
import json
import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from sessionmail._settings import CHAIN, SILENCE, FrozenWatch

#: Claude Code's config dir variable, and its default under HOME.
CONFIG_VAR = "CLAUDE_CONFIG_DIR"
CONFIG_DEFAULT = ".claude"

#: The status word G1 needs, exactly (`Session.raw_status`).
BUSY = "busy"

#: The text Claude Code writes when a human presses Escape. It is
#: matched in a user entry's own text, never inside a tool_result's output,
#: where a tool may have printed it.
INTERRUPT = "[Request interrupted by user"

#: The api-request source of the main thread (every main-transcript request
#: in the measured transcripts carries it).
MAIN_QUERY = "repl_main_thread"

#: How much one read takes at a time: bounds memory on a 27 MB first read.
CHUNK = 1 << 20


# ── where the transcript is ────────────────────────────────────────────────

@dataclass(frozen=True)
class Transcript:
    """The answer of `transcript`: PATH when exactly one file matched, else
    None and WHY says why it is not judged. PATTERN is the glob, as shown."""

    path: Path | None
    why: str = ""
    pattern: str = ""


def config_dir(env: Mapping[str, str] | None = None) -> Path:
    """`CLAUDE_CONFIG_DIR` when it is set and not empty, else `~/.claude`
    (HOME from ENV, default `os.environ`)."""
    env = os.environ if env is None else env
    configured = (env.get(CONFIG_VAR) or "").strip()
    if configured:
        return Path(configured).expanduser()
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(home) / CONFIG_DEFAULT


def transcript(session: str, env: Mapping[str, str] | None = None) -> Transcript:
    """The ONE transcript of SESSION: `<config dir>/projects/*/<session>.jsonl`.

    Zero matches, two or more, or a session id that could not be a file
    name, give `path` None and a `why` the gauge can show.
    Never raises for the disk's sake."""
    root = config_dir(env) / "projects"
    pattern = str(root / "*" / f"{session}.jsonl")
    if not session or "/" in session or session in (".", ".."):
        return Transcript(None, f"no session id to look for ({session!r})", pattern)
    try:
        found = sorted(glob.glob(os.path.join(glob.escape(str(root)), "*",
                                              glob.escape(session) + ".jsonl")))
    except OSError as err:   # pragma: no cover - glob swallows most of these
        return Transcript(None, f"{pattern} could not be listed ({err.strerror or err})",
                          pattern)
    found = [f for f in found if os.path.isfile(f)]
    if not found:
        return Transcript(None, f"no transcript matches {pattern}", pattern)
    if len(found) > 1:
        return Transcript(None, f"{len(found)} transcripts match {pattern}, so none is "
                                "judged: " + ", ".join(found), pattern)
    return Transcript(Path(found[0]), "", pattern)


# ── the state a transcript folds into ──────────────────────────────────────

def parse_ts(value: object) -> float | None:
    """An entry's ISO 8601 `timestamp` as epoch seconds, or None when it is
    missing or cannot be read. A stamp with no zone is read as UTC (Claude
    Code writes `...Z`)."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def iso(seconds: float | None) -> str:
    """Epoch SECONDS as an ISO UTC stamp to the millisecond, `...Z`; `-` for
    None or a value no clock can show."""
    if seconds is None or not math.isfinite(seconds):
        return "-"
    try:
        stamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "-"
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class State:
    """What a transcript says about its main thread, folded entry by entry.

    - `clock`: the largest main-thread timestamp, excluding `queue-operation`
      entries and `queued_command` attachments; `clock_kind` names the
      kind of entry that set it.
    - `open_tools`: the `tool_use` ids with no `tool_result` yet, by id.
    - `turn_open`, `turn`: whether a turn is open after the latest real
      prompt, and that prompt's uuid (`dequeue@<timestamp>` when a dequeued
      `queue-operation` opened it: it has no uuid). `closed_by` names what
      closed the last turn (`turn_duration` or `interrupt`).
    - `opened`: the timestamp of the entry that opened the open turn from a
      closed one (the rising edge only). G4 never counts silence from before
      it: a dequeue opens a turn but is never the clock, so without it
      a seat idle for more than N that is handed a queued prompt would read
      as frozen until the prompt's own entry lands (measured over 7 days of
      real transcripts: about 3,400 such openings, each leaving a window of
      up to 2.3 s).
    - `first`, `requests`: the first `api-request` of the unanswered run and
      how many it holds; `first` is None when no run is unanswered.
    - `entries`, `skipped`, `bad`: lines folded, sidechain lines skipped,
      lines that were not JSON objects.
    - `fault`: non-empty when the last read failed. `judge` then refuses to
      judge: a stale state must never be read as silence.
    """

    clock: float | None = None
    clock_kind: str = ""
    open_tools: set[str] = field(default_factory=set)
    turn_open: bool = False
    turn: str | None = None
    closed_by: str = ""
    opened: float | None = None
    first: float | None = None
    requests: int = 0
    entries: int = 0
    skipped: int = 0
    bad: int = 0
    fault: str = ""

    def snapshot(self) -> State:
        """A copy safe to keep while this state goes on folding."""
        return replace(self, open_tools=set(self.open_tools))

    # The fold. Every entry updates the state, a backdated one too (only the
    # clock is a max): a backdated tool_result must still close its tool_use
    # (a backdated tool_result skipped would leave its tool_use open, and
    # hide every later silence in the file).

    def feed(self, entry: object) -> None:
        """Fold one parsed transcript ENTRY into the state."""
        if not isinstance(entry, dict):
            self.bad += 1
            return
        if entry.get("isSidechain") is True:
            self.skipped += 1
            return
        self.entries += 1
        kind = entry.get("type")
        stamp = parse_ts(entry.get("timestamp"))

        if kind == "queue-operation":
            # Outside arrivals: never the clock. A dequeue is the queue
            # handing the seat its next prompt, so it opens a turn (G2).
            if entry.get("operation") == "dequeue":
                self._open(f"dequeue@{entry.get('timestamp')}", stamp)
            return
        if kind == "attachment":
            attachment = entry.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                return   # an arrival folded into the turn: never the clock
            self._tick(stamp, "attachment")
            return

        self._tick(stamp, str(kind))
        if kind == "user":
            self._user(entry)
        elif kind == "assistant":
            self._assistant(entry)
        elif kind == "api-request":
            if entry.get("querySource") == MAIN_QUERY and stamp is not None:
                if self.first is None:
                    self.first, self.requests = stamp, 0
                self.requests += 1
        elif kind == "system" and entry.get("subtype") == "turn_duration":
            self._close("turn_duration")

    def _tick(self, stamp: float | None, kind: str) -> None:
        if stamp is not None and (self.clock is None or stamp > self.clock):
            self.clock, self.clock_kind = stamp, kind

    def _open(self, turn: str, stamp: float | None) -> None:
        # Only the rising edge sets `opened`. A dequeue can be written while
        # a main request waits unanswered (measured over 7 days of real
        # transcripts: 30 of them), so letting one mid-turn move the anchor
        # would reset G4 on a frozen seat, and mail arriving mid-turn must
        # never keep a frozen seat looking alive.
        if not self.turn_open:
            self.opened = stamp
        self.turn_open, self.turn, self.closed_by = True, turn, ""

    def _close(self, by: str) -> None:
        # A closed turn answers everything: its tools cannot still be open,
        # and its requests are not unanswered. Clearing here (never at a new
        # prompt) keeps a stranded tool_use from blinding the next turn,
        # while a tool open mid-turn is never forgotten.
        self.turn_open, self.closed_by, self.opened = False, by, None
        self.open_tools.clear()
        self.first, self.requests = None, 0

    def _user(self, entry: dict) -> None:
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    ident = block.get("tool_use_id")
                    if isinstance(ident, str):
                        self.open_tools.discard(ident)
                elif block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            text = "\n".join(parts)
        if INTERRUPT in text:
            self._close("interrupt")
            return
        # A real prompt is Claude Code's own predicate, dzr(): a
        # user entry with string content, not isMeta, not isCompactSummary.
        if (isinstance(content, str) and entry.get("isMeta") is not True
                and entry.get("isCompactSummary") is not True):
            uuid = entry.get("uuid")
            self._open(uuid if isinstance(uuid, str) and uuid else
                       f"prompt@{entry.get('timestamp')}",
                       parse_ts(entry.get("timestamp")))

    def _assistant(self, entry: dict) -> None:
        # Any assistant entry answers the run (H3), a thinking block too.
        self.first, self.requests = None, 0
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    ident = block.get("id")
                    if isinstance(ident, str):
                        self.open_tools.add(ident)

    def feed_line(self, line: bytes | str) -> None:
        """Fold one raw transcript LINE (JSON text); a blank line is no entry."""
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line.strip():
            return
        try:
            entry = json.loads(line)
        except ValueError:
            self.bad += 1
            return
        self.feed(entry)


def fold(entries: Iterable[object]) -> State:
    """A fresh State with every one of ENTRIES folded in, in order."""
    state = State()
    for entry in entries:
        state.feed(entry)
    return state


class Tail:
    """An incremental reader of one transcript.

    `read()` folds the bytes appended since the last read into `state` and
    returns it. It keeps the byte offset and a partial last line (carried
    over to the next read, never folded half-written). A file with a new
    inode, or shorter than the offset, is a new file: the state starts
    again from byte 0. It keeps state only, never the lines.

    A read that fails sets `state.fault` (and keeps the offset), so `judge`
    refuses a state it can no longer trust; the next good read clears it."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.state = State()
        self.offset = 0
        self.inode: tuple[int, int] | None = None
        self._carry = b""
        self.resets = 0

    def _reset(self) -> None:
        self.state = State()
        self.offset = 0
        self._carry = b""

    def read(self) -> State:
        try:
            with open(self.path, "rb") as fh:
                st = os.fstat(fh.fileno())
                ident = (st.st_dev, st.st_ino)
                if self.inode is not None and (ident != self.inode or st.st_size < self.offset):
                    self._reset()
                    self.resets += 1
                self.inode = ident
                fh.seek(self.offset)
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    self.offset += len(chunk)
                    data = self._carry + chunk
                    lines = data.split(b"\n")
                    self._carry = lines.pop()
                    for line in lines:
                        self.state.feed_line(line)
        except OSError as err:
            self.state.fault = f"{self.path} could not be read ({err.strerror or err})"
            return self.state
        self.state.fault = ""
        return self.state


# ── the judgement ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Verdict:
    """One rule's judgement of one state.

    RULE is `silence` (2a) or `chain` (2b). HELD is True only when every gate
    holds. KEY is `(session, rule, anchor)` when it holds, the episode key:
    the anchor is the clock, or the turn's opening when that is
    later (2a), or `first` (2b), so a new
    episode needs new progress first. WHY says, in words, the gate that
    failed or what held. TURN is the open turn's prompt key (the one-notice-
    per-turn guard reads it), ANCHOR the anchor, and SILENT the seconds the
    rule measured (None when it measured nothing)."""

    rule: str
    held: bool
    key: tuple[str, str, float] | None
    why: str
    turn: str | None = None
    anchor: float | None = None
    silent: float | None = None


def _span(seconds: float) -> str:
    return f"{seconds:.1f} s" if not float(seconds).is_integer() else f"{int(seconds)} s"


def judge(state: State, sidecar_status: str | None, now: float,
          settings: FrozenWatch, rule: str | None = None,
          session: str = "") -> Verdict:
    """Judge STATE by one rule, with no I/O and no clock.

    SIDECAR_STATUS is the sidecar's raw `status` word, or None when the
    sidecar is missing or cannot be read. NOW is wall-clock epoch seconds
    (`time.time()`), never `time.monotonic()` (see the module docstring).
    SETTINGS is `_settings.frozen()`: its `rules`, `after_seconds` (N) and
    `chain_seconds` (R). RULE picks the rule; None judges each rule in
    `settings.rules` and returns the first that holds, else the first's
    verdict. SESSION goes into the episode key.

    The gates are checked in a fixed order, and the first that fails is the
    `why`: the switch, the state's own fault, G1, G2, then the rule's own."""
    if rule is None:
        verdicts = judge_each(state, sidecar_status, now, settings, session)
        for v in verdicts:
            if v.held:
                return v
        return verdicts[0] if verdicts else Verdict(SILENCE, False, None, "no rule runs")

    def no(why: str, silent: float | None = None, anchor: float | None = None) -> Verdict:
        return Verdict(rule, False, None, why, state.turn if state.turn_open else None,
                       anchor, silent)

    if rule not in (SILENCE, CHAIN):
        return no(f"{rule!r} is not a rule (the rules are {SILENCE!r} and {CHAIN!r})")
    if not settings.on:
        return no("frozen_watch is not true, so nothing is judged")
    if rule not in settings.rules:
        return no(f"rule {rule} is not in frozen_rules ({', '.join(settings.rules)})")
    if state.fault:
        return no(f"not judged: {state.fault}")
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
        return no(f"not judged: now {now!r} is not a wall clock")

    # G1: the seat is busy. (Its pid is alive: the monitor that runs this
    # returns when its seat dies.)
    if sidecar_status is None:
        return no("G1: no sidecar status (missing or unreadable), so not judged")
    if sidecar_status != BUSY:
        return no(f"G1: the sidecar says {sidecar_status!r}, not {BUSY!r}")
    # G2: the main turn is open.
    if not state.turn_open:
        closed = f" (closed by {state.closed_by})" if state.closed_by else ""
        return no(f"G2: no main turn is open{closed}")

    if rule == SILENCE:
        # G3: the model owns the turn; a tool that does is never frozen.
        if state.open_tools:
            n = len(state.open_tools)
            return no(f"G3: {n} tool_use{'s' if n != 1 else ''} without a tool_result, "
                      "so a tool owns the turn")
        # G4: silent for N or more, by the clock, and never counted
        # from before the turn opened (`State.opened`).
        if state.clock is None and state.opened is None:
            return no("G4: no timestamped main-thread entry yet")
        limit, anchor, what = settings.after_seconds, state.clock, "the clock"
        if state.opened is not None and (anchor is None or state.opened > anchor):
            anchor, what = state.opened, "the turn's opening"
    else:
        # H3: an unanswered run of main-thread requests.
        if state.first is None:
            return no("H3: no unanswered main-thread api-request")
        limit, anchor, what = settings.chain_seconds, state.first, "the run's first api-request"

    if now < anchor:
        return no(f"now {iso(now)} is before {what} {iso(anchor)}: a backwards clock is "
                  "never frozen (now must be wall-clock epoch seconds, not monotonic)",
                  anchor=anchor)
    silent = now - anchor
    gate = "G4" if rule == SILENCE else "H4"
    name = "N" if rule == SILENCE else "R"
    if silent < limit:
        return no(f"{gate}: {_span(silent)} since {what} {iso(anchor)}, under "
                  f"{name} {_span(limit)}", silent, anchor)
    detail = (f"{state.requests} unanswered api-request{'s' if state.requests != 1 else ''}, "
              if rule == CHAIN else "")
    return Verdict(rule, True, (session, rule, anchor),
                   f"{rule}: {detail}{_span(silent)} since {what} {iso(anchor)}, "
                   f"at or over {name} {_span(limit)}",
                   state.turn, anchor, silent)


def judge_each(state: State, sidecar_status: str | None, now: float,
               settings: FrozenWatch, session: str = "") -> tuple[Verdict, ...]:
    """One verdict per rule in `settings.rules`, in that order, so a caller
    can see a 2b episode overlapping a 2a one."""
    return tuple(judge(state, sidecar_status, now, settings, rule, session)
                 for rule in settings.rules)


# ── the monitor's side: the sidecar, the stamp, the guard, the tick ────────
#
# The pager monitor calls `Watcher.tick` right after `follow_session`, in
# both of its wait loops, and only in monitor mode. A tick looks at most
# once per CHECK_SECONDS, reads the switch at every look, and never raises.
#
# DETECT ONLY. It sends no keys, captures no pane, runs no tmux and no
# nettop, and starts no child process: every read is a file read (the pager
# spec's *No children*). It sends NO MAIL either: the notice is one
# `MonitorOut.say` line, the `frozen:` gauge in `pager status`, and the
# stamp file. `notify` is the one seam a notice sender would plug into.

#: The least gap between two looks, in seconds. Read at each tick, so a
#: test may lower it.
CHECK_SECONDS = 30.0

#: The stamp's name, beside the ride-along stamp in `<session dir>/mail/stamps/`.
STAMP = "frozen.json"
#: The stamp's layout; a reader refuses a stamp of another version.
STAMP_VERSION = 1


def wall_clock() -> float:
    """Wall-clock epoch seconds: the NOW `judge` takes. The one place the
    watcher reads the wall clock, so a test can drive it without patching
    the time module."""
    import time
    return time.time()


def sidecar_status(pid: int, session: str, env: Mapping[str, str] | None = None) -> str | None:
    """The raw `status` word in Claude Code's sidecar for PID (the monitor's
    caller), G1's input; None when the file is missing, cannot be read, is
    not a JSON object, has no string `status`, or names a session other
    than SESSION (a `/clear` the follow has not caught up with yet).

    A plain file read: never sessiontools' `_read`, which may ask `ps`
    about a busy sidecar's process, a child this detector must not start."""
    from sessionmail import _seat  # lazy: no I/O, and no import cost, at import
    try:
        data = json.loads((_seat.sidecar_dir(dict(env) if env is not None else None)
                           / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    named = data.get("sessionId")
    if not isinstance(named, str) or named.strip() != session:
        return None
    status = data.get("status")
    return status if isinstance(status, str) else None


def stamp_path(session: str, env: Mapping[str, str] | None = None) -> Path | None:
    """`<session dir>/mail/stamps/frozen.json`; None with no session id."""
    from sessionmail import _seat
    root = _seat.session_root(session, dict(env) if env is not None else None)
    return None if root is None else root / _seat.STAMPS_DIR / STAMP


def read_stamp(session: str, env: Mapping[str, str] | None = None) -> dict | None:
    """SESSION's stamp as a dict, or None when there is none, or it cannot
    be read, is not a JSON object, is another version or names another
    session. Never raises."""
    p = stamp_path(session, env)
    if p is None:
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("version") != STAMP_VERSION
            or data.get("session") != session):
        return None
    return data


def write_stamp(session: str, data: dict, env: Mapping[str, str] | None = None) -> Path:
    """Write DATA as SESSION's stamp, atomically: a temporary file beside it,
    then a rename, so a reader sees the old stamp or the new, never half.
    Raises OSError (the watcher logs it once and goes on)."""
    from sessionmail import _seat
    p = stamp_path(session, env)
    if p is None:
        raise OSError(f"no session id ({session!r}), so no stamp")
    _seat.make_dir(p.parent, session, dict(env) if env is not None else None)
    tmp = p.with_name(f".{STAMP}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, p)
    return p


def _hhmm(seconds: float | None) -> str:
    """Epoch SECONDS as a local `HH:MM`, the way the gauge and the line say
    a time; `-` for None."""
    if seconds is None or not math.isfinite(seconds):
        return "-"
    try:
        return datetime.fromtimestamp(seconds).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "-"


def _minutes(seconds: float) -> str:
    return f"{int(max(0.0, seconds) // 60)}m"


#: What each rule's episode is, in the line and the gauge.
_WHAT = {SILENCE: "silent", CHAIN: "requests unanswered"}


@dataclass(frozen=True)
class Notice:
    """A rising edge that earned the turn's one notice: what `notify` is
    handed, and what the say-line is made from."""

    session: str
    rule: str
    key: tuple[str, str, float]
    turn: str | None
    anchor: float
    silent: float
    why: str
    at: float

    def line(self) -> str:
        """The ONE stdout line to the seat. A frozen turn cannot read it: it
        lands when the turn ends, by Claude Code's own abort or a human's
        Escape, so it speaks in the past."""
        what = ("wrote nothing" if self.rule == SILENCE
                else "left its model requests unanswered")
        return (f"pager: this seat looked frozen mid-turn: its open turn {what} for "
                f"{_minutes(self.silent)} from {_hhmm(self.anchor)} (rule {self.rule}, "
                "frozen_watch). Detect only: no keys were sent and no mail. If you can "
                "read this, the turn has ended. `pager status` shows the frozen: gauge.")


def notify(notice: Notice) -> None:
    """THE SEAM where a notice sender would plug in: called once per frozen
    TURN, right after the say-line. A no-op: the watch sends no mail and
    has no notice address. A sender needs a chosen channel, and the pager
    spec's *No children* to allow the child a send starts."""
    return None


class Episode:
    """The edge detector and the one-notice-per-TURN guard.

    An episode starts at a rising edge, the first check at which a rule
    holds with a new anchor, and ends at the falling edge, the first check
    at which it does not (or its anchor moved). Its key is `(session, rule,
    anchor)`. A rising edge earns a notice only when no notice was given in
    the same turn (the uuid of the prompt that opened it): a second episode
    in that turn, or a chain episode overlapping a silence one, updates the
    stamp and the gauge only. A falling edge
    is RECORDED in the stamp, never announced.

    The state lives here and in the stamp, which is what keeps a monitor
    that re-`exec`s itself (a `/clear` follow) or takes over from another
    from noticing the same turn twice. The stamp is written only when
    something a reader would see changes: the first check, a rising or
    falling edge, or the check turning judged or not judged (and why)."""

    def __init__(self, session: str, data: dict | None = None,
                 env: Mapping[str, str] | None = None):
        self.session = session
        self.env = env
        self.data: dict | None = data
        #: rule -> its open episode's anchor (epoch seconds).
        self.active: dict[str, float] = {}
        #: The OSError of the last stamp write, when it failed: the step
        #: still returns its notice, and the write is owed to the next step.
        self.unwritten: OSError | None = None
        self._owed = False
        if data is not None and not data.get("resolved"):
            for rule, open_ in (data.get("active") or {}).items():
                anchor = open_.get("anchor") if isinstance(open_, dict) else None
                if rule in (SILENCE, CHAIN) and isinstance(anchor, (int, float)):
                    self.active[rule] = float(anchor)

    @classmethod
    def load(cls, session: str, env: Mapping[str, str] | None = None) -> Episode:
        """The guard as SESSION's stamp left it (none: a fresh guard)."""
        return cls(session, read_stamp(session, env), env)

    def _fresh(self) -> dict:
        return {"version": STAMP_VERSION, "session": self.session, "active": {},
                "rule": None, "key": None, "turn": None, "onset": None,
                "anchor": None, "detected": None, "silent": None, "why": None,
                "noticed": None, "noticed_turn": None, "episodes": 0,
                "message_id": None, "resolved": None, "resolved_why": None,
                "checked": None}

    def step(self, verdicts: Iterable[Verdict], now: float,
             unjudged: str | None = None) -> Notice | None:
        """Fold one check into the guard: VERDICTS (one per rule that runs),
        at wall-clock NOW. UNJUDGED, when set, says why nothing could be
        judged (no transcript, two of them, a read that failed): every open
        episode then ends. Returns the Notice the check earned, else None;
        writes the stamp when a reader would see a change. A write that
        fails never costs the notice: its error is left in `unwritten`
        for the caller to log, and the write is tried again next step."""
        data = self.data if self.data is not None else self._fresh()
        changed = self.data is None
        verdicts = () if unjudged is not None else tuple(verdicts)
        by_rule = {v.rule: v for v in verdicts}
        held = {v.rule: v for v in verdicts if v.held and v.anchor is not None}

        checked = {"judged": unjudged is None, "why": unjudged or ""}
        if data.get("checked") != checked:
            data["checked"], changed = checked, True

        fell: list[str] = []
        for rule, anchor in list(self.active.items()):
            v = held.get(rule)
            if v is None or v.anchor != anchor:
                del self.active[rule]
                data["active"].pop(rule, None)
                gone = by_rule.get(rule)
                fell.append(f"{rule}: {gone.why if gone is not None else unjudged or 'not run'}")
        if fell:
            changed = True
            if not self.active:
                data["resolved"], data["resolved_why"] = iso(now), "; ".join(fell)

        notice: Notice | None = None
        for rule, v in held.items():
            if rule in self.active or v.key is None or v.anchor is None:
                continue
            self.active[rule] = v.anchor
            data["active"][rule] = {"anchor": v.anchor, "onset": iso(v.anchor),
                                    "turn": v.turn, "why": v.why}
            silent = v.silent if v.silent is not None else now - v.anchor
            data.update(rule=rule, key=list(v.key), turn=v.turn, onset=iso(v.anchor),
                        anchor=v.anchor, detected=iso(now), silent=round(silent, 3),
                        why=v.why, resolved=None, resolved_why=None)
            changed = True
            if v.turn is not None and v.turn == data.get("noticed_turn"):
                data["episodes"] = int(data.get("episodes") or 0) + 1
                continue
            data.update(noticed=iso(now), noticed_turn=v.turn, episodes=1, message_id=None)
            notice = Notice(self.session, rule, v.key, v.turn, v.anchor, silent, v.why, now)

        self.data = data
        if changed or self._owed:
            try:
                write_stamp(self.session, data, self.env)
                self._owed = False
            except OSError as exc:
                self._owed, self.unwritten = True, exc
        return notice


def gauge(watch: FrozenWatch, stamp: dict | None, now: float) -> str:
    """`pager status`'s `frozen:` line, from the settings read now and the
    stamp the monitor wrote:

    - `off (...)`: the switch is not the boolean true, with any ignored
      value and why;
    - `silent 12m mid-turn since 18:26 (rule silence; noticed 18:38)`: an
      episode is open (a chain episode says `requests unanswered`);
    - `not judged (<why>)`: the last check found zero or two transcripts,
      or could not read one;
    - `watching (<rules>; N 720 s[, R 900 s])`: on, nothing frozen, with the
      last resolved episode when there was one, or `no check recorded yet`.

    Then every floor applied and every value ignored, each said once."""
    notes = list(watch.reasons)
    if not watch.on:
        head = "off (frozen_watch is not true)"
        return "frozen: " + "; ".join([head, *notes])
    numbers = f"N {_span(watch.after_seconds)}"
    if CHAIN in watch.rules:
        numbers += f", R {_span(watch.chain_seconds)}"
    watching = f"watching ({', '.join(watch.rules)}; {numbers})"
    active = (stamp or {}).get("active") or {}
    opened = [(o.get("anchor"), rule) for rule, o in active.items()
              if isinstance(o, dict) and isinstance(o.get("anchor"), (int, float))]
    checked = (stamp or {}).get("checked") or {}
    if stamp is None:
        head = f"{watching}; no check recorded yet"
    elif opened and not stamp.get("resolved"):
        anchor, rule = min(opened)
        head = (f"{_WHAT.get(rule, rule)} {_minutes(now - anchor)} mid-turn since "
                f"{_hhmm(anchor)} (rule {', '.join(r for _a, r in sorted(opened))}; "
                f"noticed {_hhmm(parse_ts(stamp.get('noticed')))})")
    elif checked.get("judged") is False:
        head = f"not judged ({checked.get('why') or 'no reason recorded'})"
    else:
        head = watching
        if stamp.get("resolved"):
            head += (f"; last episode {stamp.get('rule')} from "
                     f"{_hhmm(parse_ts(stamp.get('onset')))}, resolved "
                     f"{_hhmm(parse_ts(stamp.get('resolved')))}")
    return "frozen: " + "; ".join([head, *notes])


class Watcher:
    """The monitor's frozen-turn watch: `tick` is called once per monitor
    tick, right after `follow_session`, and looks at most once per
    CHECK_SECONDS (monotonic). A look reads the switch (`_settings.frozen`),
    finds the transcript, folds what was appended, reads the caller's
    sidecar, judges at the WALL clock, and steps the episode guard. A notice
    is SAY (the monitor's `MonitorOut.say`), then `notify`.

    It never raises: an error is logged once per kind through LOG (the
    monitor's `_injectlog.error`) and the look is skipped."""

    def __init__(self, session: str, parent: int, say, *, log=None,
                 env: Mapping[str, str] | None = None, wall=None):
        self.session = session
        self.parent = parent
        self.say = say
        self.log = log
        self.env = env
        self.wall = wall
        self.last: float | None = None
        self.tail: Tail | None = None
        self.episode: Episode | None = None
        self.logged: set[str] = set()
        self.looks = 0

    def tick(self, now: float | None = None, wall=None) -> Notice | None:
        """One monitor tick. NOW is the MONOTONIC clock, for the throttle
        only; WALL (else the watcher's, else `wall_clock`) is the wall clock
        `judge` takes. Returns the notice the look gave, if any."""
        try:
            import time
            now = time.monotonic() if now is None else now
            if self.last is not None and now - self.last < CHECK_SECONDS:
                return None
            self.last = now
            return self.look(wall or self.wall or wall_clock)
        except Exception as exc:  # noqa: BLE001 - the watch never costs the monitor
            self._error(exc)
            return None

    def look(self, wall) -> Notice | None:
        watch = _frozen_settings(self.env)
        if not watch.on:
            return None   # off: no stamp, no line, nothing sent
        self.looks += 1
        moment = wall()
        if self.episode is None:
            self.episode = Episode.load(self.session, self.env)
        found = transcript(self.session, self.env)
        unjudged: str | None = None
        verdicts: tuple[Verdict, ...] = ()
        if found.path is None:
            unjudged, self.tail = found.why, None
        else:
            if self.tail is None or self.tail.path != found.path:
                self.tail = Tail(found.path)
            state = self.tail.read()
            if state.fault:
                unjudged = state.fault
            else:
                status = sidecar_status(self.parent, self.session, self.env)
                verdicts = judge_each(state, status, moment, watch, self.session)
        notice = self.episode.step(verdicts, moment, unjudged)
        if notice is not None:
            self.say(notice.line())
            notify(notice)
        fault, self.episode.unwritten = self.episode.unwritten, None
        if fault is not None:
            self._error(fault)   # logged once per kind; the notice stands
        return notice

    def _error(self, exc: BaseException) -> None:
        kind = type(exc).__name__
        if kind in self.logged:
            return
        self.logged.add(kind)
        if self.log is None:
            return
        try:
            self.log(f"frozen watch: {kind}: {exc}")
        except Exception:  # noqa: BLE001 - a log row is never why a look fails
            pass


def _frozen_settings(env: Mapping[str, str] | None) -> FrozenWatch:
    """`_settings.frozen`, read now: the switch is read at every look."""
    from sessionmail import _settings
    return _settings.frozen(dict(env) if env is not None else None)
