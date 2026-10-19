"""`pager say`'s two reads of this session's own record: the latest turn it
heard (N9) and the operator's unread words (N7). PURE functions, no CLI.

openspec/changes/pager-genie, tasks.md 3.9a and 3.9b.
The spec requirements they prove: *A reply to a turn is a mail To the
mouth, threaded on the latest turn heard* (3.9a) and *the operator's unread words
hold a line* (3.9b). `pager say` itself, the exec of `mouth say` (3.9c),
is NOT in this cut: it will call these two and turn their answers into an
`In-Reply-To` and an exit 3.

What both read, and nothing else:

- BOX: the mails in THIS session's own box, parsed (`email.message.Message`).
  A turn that reached the `heard` box before the session subscribed is not
  in its box, so it is never looked at (D7 step 2, PROPOSED scope).
- EVENTS: this session's marks (`_seat.read_mark_events`). Any mark of the
  session's own (`seen`, `read`, `done`) on a Message-ID means it was GIVEN
  that turn. A mark of another session's never counts.

A mail counts as a TURN only when `_voice.recognise` says TRUSTED voice
(marked by `Ears-Kind`, From the keeper, and SIGNED by the keeper, O22
(S)): a mark on plain mail, on a forged `Ears-Kind` from another sender,
or on UNVERIFIED voice (the keeper's address, unsigned: every heard mail
until the keeper signs) is not a turn heard, and a forged
`Ears-Speaker: <operator>@...` holds nothing. The signature verdict reaches
these reads through `signed`, `_voice`'s seam; without it no mail is a turn.

Neither reads the heard log, moves a cursor or writes anything (D7 step 8).

⚠️ HELD (held-001; tasks.md 3.18b is not built): the DRAFTS SNAPSHOT half.
D7 counts a turn as heard when this session was given any draft of it (the
snapshot of 3.18b), and 3.9b also looks at the open drafts of devices whose
partials the session reads. Neither exists yet, so this cut reads MARKS
ONLY. The seam is ONE function, `given()`: when 3.18b lands, the snapshot's
(Message-ID, given time) pairs join the marks there, and both checks see
them with no other change. The open-draft half of 3.9b joins `_turns()`'s
caller the same way. Until then a turn heard only as a draft is NOT heard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from fnmatch import fnmatchcase
from typing import Iterable, Mapping, Sequence

from . import _seat
from ._headers import Unreadable, as_written, read_header, safe_header
from ._maildir import bare_id
from ._voice import Signed, recognise
from .rules import Settings, _address, _glob, operator

#: `pager say`'s exit when a check holds the line (D7: "print its words,
#: send nothing, exit 3"). 3.9b returns a `Hold`; 3.9c exits with this.
HOLD_EXIT = 3


@dataclass(frozen=True)
class Turn:
    """One trusted voice turn in this session's box, as these checks read it.

    `message_id`  bare, no angle brackets
    `speaker`     the address in `Ears-Speaker` (either spelling), or None
    `start`       `Ears-Start` parsed as ISO 8601, or None when absent or
                  unreadable
    `date`        `Date:` parsed, or None
    `words`       the text body, as the keeper wrote it
    """

    message_id: str
    speaker: str | None
    start: datetime | None
    date: datetime | None
    words: str


@dataclass(frozen=True)
class Heard:
    """3.9a's answer: the latest turn this session heard, and when it was
    given (the stamp of the session's FIRST mark on it; None when that
    stamp cannot be read)."""

    turn: Turn
    given: datetime | None

    @property
    def in_reply_to(self) -> str:
        """The `In-Reply-To` a line carries: the turn's Message-ID, bracketed."""
        return f"<{self.turn.message_id}>"


@dataclass(frozen=True)
class Hold:
    """3.9b's answer when the operator's words hold the line: every turn of theirs in
    this session's box that it was given none of, oldest first. `pager say`
    prints `text`, sends nothing and exits `exit_code`."""

    turns: tuple[Turn, ...]
    exit_code: int = HOLD_EXIT

    @property
    def text(self) -> str:
        return "\n".join(t.words.rstrip("\n") for t in self.turns)


# ── reading one mail ────────────────────────────────────────────────────────

def _when(value: str | None) -> datetime | None:
    """An `Ears-Start` (ISO 8601) as an aware datetime, else None. A naive
    value is read as local time, as `_seat.given_since` reads a stamp."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.astimezone()


def _date(msg: Message) -> datetime | None:
    value = safe_header(msg, "Date")
    if not value or isinstance(value, Unreadable):
        return None
    try:
        when = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    return when if when.tzinfo is not None else when.astimezone()


def _words(msg: Message) -> str:
    """The text/plain body, decoded (`_maildir.text_body`'s reading, on a
    parsed message)."""
    part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    if part is None:
        return ""
    try:
        return part.get_content()
    except Exception:  # an odd charset, or a compat32 message: the bytes
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload or "")


def turn(msg: Message, env: Mapping[str, str] | None = None,
         signed: Signed | None = None) -> Turn | None:
    """MSG as a Turn when it is TRUSTED voice with a Message-ID, else None.
    SIGNED is `_voice.recognise`'s verdict seam: without it, None."""
    mid = bare_id(as_written(safe_header(msg, "Message-ID")))    # total
    if not mid or not recognise(msg, env, signed).voice:
        return None
    speaker = read_header(msg, "Ears-Speaker").value
    return Turn(
        message_id=mid,
        speaker=(_address(speaker) or speaker.strip() or None) if speaker is not None else None,
        start=_when(read_header(msg, "Ears-Start").value),
        date=_date(msg),
        words=_words(msg),
    )


def _turns(box: Iterable[Message], env: Mapping[str, str] | None,
           signed: Signed | None = None) -> dict[str, Turn]:
    """The trusted voice turns in BOX by Message-ID; a second copy of one id
    (two deliveries of the same turn) is the same turn, the first kept."""
    out: dict[str, Turn] = {}
    for msg in box:
        t = turn(msg, env, signed)
        if t is not None and t.message_id not in out:
            out[t.message_id] = t
    return out


# ── what this session was given: THE SEAM ───────────────────────────────────

def given(events: Sequence[_seat.Mark], session: str) -> dict[str, tuple[datetime | None, int]]:
    """Message-ID -> (when this SESSION was first given it, the file order of
    that first mark), from its own marks. Any mark of its own counts
    (`seen`, `read`, `done`: D8, "a turn with a done mark was heard"); a mark
    from another session never does. A stamp that cannot be read is kept as
    None: given, at an unknown time, older than any known one.

    SEAM (held-001, 3.18b): the drafts snapshot's (Message-ID, given time)
    pairs join here when 3.18b is built; until then this is marks only."""
    out: dict[str, tuple[datetime | None, int]] = {}
    for order, m in enumerate(events):
        if m.session != session or m.mark not in _seat.MARKS:
            continue
        mid = bare_id(m.message_id)
        if mid and mid not in out:
            out[mid] = (_when(m.stamp), order)
    return out


# ── 3.9a: the latest turn this session heard (N9) ───────────────────────────

#: Older than any real time: what a missing stamp, `Ears-Start` or `Date:`
#: sorts as. Aware (UTC), so it compares with every aware time read here.
_EPOCH = datetime(1, 1, 2, tzinfo=timezone.utc)


def _key(t: Turn, when: datetime | None, order: int) -> tuple:
    """Newest given first; a tie (one ride-along stamps its whole batch with
    one time) to the later `Ears-Start`, then the later `Date:` (D7 step 1,
    the ordering PROPOSED); a turn with neither loses the tie. Last, the
    later mark in the file, so the answer is always one turn."""
    return (when or _EPOCH, t.start or _EPOCH, t.date or _EPOCH, order)


def latest_heard(box: Iterable[Message], events: Sequence[_seat.Mark], session: str,
                 env: Mapping[str, str] | None = None,
                 signed: Signed | None = None) -> Heard | None:
    """The latest trusted voice turn this SESSION heard (N9), or None when it
    heard none: then the line carries no `In-Reply-To`.

    "Heard" is a turn in its own BOX that it holds a mark on (EVENTS); the
    latest is the one GIVEN most recently, by the mark's stamp, never the one
    that called the seat (an earlier rule, since overturned)."""
    turns = _turns(box, env, signed)
    best: tuple[tuple, Heard] | None = None
    for mid, (when, order) in given(events, session).items():
        t = turns.get(mid)
        if t is None:
            continue
        key = _key(t, when, order)
        if best is None or key > best[0]:
            best = (key, Heard(turn=t, given=when))
    return None if best is None else best[1]


# ── 3.9b: the operator's-words pre-check (N7) ───────────────────────────────

def is_operator(address: str | None, settings: Settings | None = None) -> bool:
    """ADDRESS is the operator: it matches one of `rules.operator()`'s
    globs (`$operator`; from this host's config, no default in code; `set
    operator` overrides it), compared as the rules compare a
    `from:` glob (`rules._glob`: case-folded, `[` literal). SETTINGS None
    reads them now, as `operator()` does. No operator configured: False."""
    if not address:
        return False
    value = address.casefold()
    return any(fnmatchcase(value, _glob(g)) for g in operator(settings))


def operator_unread(box: Iterable[Message], events: Sequence[_seat.Mark], session: str,
                 env: Mapping[str, str] | None = None,
                 settings: Settings | None = None,
                 signed: Signed | None = None) -> Hold | None:
    """N7's pre-check: a `Hold` when this SESSION's own BOX holds a trusted
    voice turn whose `Ears-Speaker` is the operator and whose Message-ID it was
    given NOTHING of (no mark of its own), else None (the line may go).

    Reads only the session's box and its own marks, never the heard log.
    The held turns are given oldest first (`Ears-Start`, then `Date:`), so
    the printed words read in the order they were said."""
    heard = given(events, session)
    unread = [t for mid, t in _turns(box, env, signed).items()
              if mid not in heard and is_operator(t.speaker, settings)]
    if not unread:
        return None
    unread.sort(key=lambda t: (t.start or _EPOCH, t.date or _EPOCH))
    return Hold(turns=tuple(unread))
