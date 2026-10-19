"""The header reader: one function for the headers this change mints.

openspec/changes/pager-genie, tasks.md 3.22 (the migration
decision: no header this package mints starts with `X-`). The spec requirement it proves
is *A header this change
mints carries no X-, and the reader takes both spellings*.

The rule, whole:

  1. A header in `MIGRATING` is read by its unprefixed name first, else by
     its `X-` form (`Ears-Rule`, else `X-Ears-Rule`).
  2. When a message carries both, the unprefixed one wins and the `X-` one
     is ignored, whatever either says, and wherever each sits on the wire.
     Presence decides, not truthiness: an empty `Ears-Rule:` still wins.
  3. Which spelling was read is recorded (`HeaderRead.spelling`,
     `.prefixed`), so `pager why` can say "read `X-Ears-Rule`" once it
     exists (X10), and a test can assert it now.
  4. This module writes nothing. No `X-` form of these names is ever minted.
  5. The end is named: tasks.md 3.23 deletes the `X-` fallback (and its
     tests) in one commit, once the keeper's conforming change (X1) lands.

Any other name (`Alert-Id`, `Priority`, ...) is read by that name only,
case-insensitively, with NO `X-` fallback. That is deliberate:

  - `Alert-Id`: D16 says no code mints `X-Alert-Id`, "so there is nothing
    to migrate and no reader to keep". `message send --header alert-id=`
    writes `Alert-ID`, which reads here because field names are
    case-insensitive (RFC 5322 1.2.2).
  - `Priority`: `X-Priority` is a mail client's own, different header
    (`1 (Highest)`..`5`), not an old spelling of `Priority:`; falling back
    to it would invent a level nobody sent.

The existing `X-Session-*`, `X-Project`, `X-Task`, `X-Machine` are NOT this
reader's: they are read and written exactly as today, and a later change
renames them. Asking for an `X-` name is refused: the caller names the header this
change mints, and the reader owns its old spelling.

Pure: no I/O, no wake logic. What a header MEANS (a call, a level, a note
that never pages) is judged elsewhere; this only reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.errors import HeaderParseError
from email.message import Message

#: The headers this change renames, each read in both
#: spellings until tasks.md 3.23. `Pager-Note` is the pager notes' marker,
#: read through here too (D16 item 1).
MIGRATING: tuple[str, ...] = (
    "Ears-Kind",
    "Ears-Rule",
    "Ears-Seq",
    "Ears-Start",
    "Ears-End",
    "Ears-Source",
    "Ears-Device",
    "Ears-Speaker",
    "Pager-Note",
)

#: Names this change mints that were never minted with `X-`: read here by
#: the one name only (D16, "`Alert-Id`: P-10 DECIDED, spec text only").
UNPREFIXED_ONLY: tuple[str, ...] = ("Alert-Id",)

_CANONICAL = {n.lower(): n for n in MIGRATING + UNPREFIXED_ONLY}
_MIGRATING_LOWER = frozenset(n.lower() for n in MIGRATING)
_FOLD = re.compile(r"\r?\n(?=[ \t])")


@dataclass(frozen=True)
class HeaderRead:
    """What the reader found for one header name.

    `name`      the canonical unprefixed name asked for (`Ears-Rule`)
    `value`     the first value of the spelling that won, unfolded; None if
                neither spelling is present ("" is present and empty)
    `spelling`  the field name exactly as it was on the wire
                (`X-Ears-Rule`, `ears-rule`); None if absent
    `prefixed`  True if the `X-` form was read, False if the unprefixed,
                None if absent
    `values`    every value of the winning spelling, in wire order (the
                first is `value`; a parser that takes the first only, like
                3.13's Priority, can record the rest)
    `ignored`   the wire spellings of the `X-` form that lost to the
                unprefixed one, in wire order; () if nothing was ignored
    """

    name: str
    value: str | None
    spelling: str | None
    prefixed: bool | None
    values: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.value is not None


# ── the safe accessor ──────────────────────────────────────────────
#
# A SENDER writes every header, and CPython's email parser does not always
# survive what one writes. Under `policy.default` a header is parsed LAZILY,
# when it is read (`msg.get`, `msg[...]`, `items()`, `values()`,
# `get_all()`), not when the file is parsed. A domain literal that starts
# with whitespace (`Message-ID: <x@[ `, hex 3c78405b20) reaches
# `_header_value_parser._get_ptext_to_endchars` with an empty first
# fragment, its `for` runs zero times, and its `else:` reads `pos` unbound:
# UnboundLocalError. On 3.13 `In-Reply-To` and `References` reach the same
# frame. Measured on 3.13.16 and the macOS system Python 3.9.6.
# Likely a CPython bug; not reported upstream (a public act, not ours).
#
# Every read of a header on a `policy.default` message goes through
# `safe_header` (or `read_header`, which shares its one `try`), so a header
# the parser cannot read is DATA - shown as `<unreadable header>` - and the
# message is still listed, read and paged. It never sinks its caller.

#: What an unreadable header reads as, wherever it is shown.
UNREADABLE = "<unreadable header>"

#: The parser faults that make a header unreadable. Deliberately not
#: `Exception`: anything else is a bug of ours and should be seen.
#:   UnboundLocalError - the fault measured above (MRO NameError ->
#:     Exception; NOT an `email.errors` class, so naming only those would
#:     miss it). It is raised while the parser handles a HeaderParseError.
#:   HeaderParseError - the parser's own "this does not parse" class. Its
#:     header classes are meant to turn it into a defect, so one that
#:     escapes is the same kind of fault: a header we cannot read, not a
#:     bug of ours. PREDICTION (not measured): an upstream fix to the
#:     unbound `pos` could let the HeaderParseError in flight escape instead.
PARSER_FAULTS: tuple[type[Exception], ...] = (UnboundLocalError, HeaderParseError)


class Unreadable(str):
    """A header the stdlib parser could not read. It IS the string
    `UNREADABLE`, so a caller that only shows it shows the mark; a caller
    that needs more reads the attributes:

    `name`   the field name as it was on the wire
    `raw`    the value exactly as the sender wrote it, never parsed (an id
             caller keys on this: it is what compat32, the rules and the
             index read for the same header)
    `fault`  the parser's exception class name
    """

    name: str
    raw: str
    fault: str

    def __new__(cls, name: str, raw: object, fault: str) -> "Unreadable":
        self = super().__new__(cls, UNREADABLE)
        self.name, self.raw, self.fault = name, str(raw), fault
        return self


def _fetch(msg: Message, field: str, raw: object) -> object:
    """The ONE place a header value is built from its raw form for this
    module's readers: what `msg.get` / `msg.items()` would hand back for
    this field, or an `Unreadable` when the parser faults on it."""
    try:
        return msg.policy.header_fetch_parse(field, raw)
    except PARSER_FAULTS as exc:
        return Unreadable(field, raw, type(exc).__name__)


def safe_header(msg: Message, name: str, default: object = None) -> object:
    """`msg.get(NAME, DEFAULT)`, total: the first NAME field's value, read
    exactly as `Message.get` reads it, else DEFAULT; an `Unreadable` (which
    shows as `<unreadable header>`) when the parser cannot read it. Only
    the field asked for is parsed, so a poison header elsewhere in MSG
    never touches it. Safe on any policy."""
    want = name.lower()
    for field, raw in msg.raw_items():
        if field.lower() == want:
            return _fetch(msg, field, raw)
    return default


def as_written(value: object) -> str:
    """VALUE (from `safe_header`) as text to key on: an `Unreadable`'s
    `raw`, the sender's own bytes; else `str(value)`, "" for None."""
    if isinstance(value, Unreadable):
        return value.raw
    return "" if value is None else str(value)


def _unfold(value: object) -> str:
    # the default policy hands back unfolded header objects; compat32 hands
    # back the raw folded string. Unfold both the same way (RFC 5322 2.2.3:
    # remove the line break, keep the whitespace).
    return _FOLD.sub("", str(value))


def old_spelling(name: str) -> str | None:
    """The `X-` field name NAME is also read by, lower case (`x-ears-rule`),
    or None for a name not in `MIGRATING`. The one place the rename is
    spelled: `read_header` uses it, and so does the rules' `<header>:`
    condition (`rules.Facts.header`), which reads raw fields."""
    low = name.lower()
    return "x-" + low if low in _MIGRATING_LOWER else None


def read_header(msg: Message, name: str) -> HeaderRead:
    """Read header NAME from MSG: unprefixed first, else its `X-` form if
    NAME is in `MIGRATING`; the unprefixed wins; the spelling is recorded."""
    if name.lower().startswith("x-"):
        raise ValueError(
            f"ask for the unprefixed name, not {name!r}: the reader owns the X- form"
        )
    canonical = _CANONICAL.get(name.lower(), name)
    want = canonical.lower()
    old = old_spelling(want)

    new_hits: list[tuple[str, str]] = []
    old_hits: list[tuple[str, str]] = []
    # raw_items, not items(): items() builds EVERY header object on a
    # policy.default message, so one poison header anywhere would
    # raise whatever NAME was asked for. Only the matching fields are
    # built, through the shared accessor, so the values are the ones
    # items() gave (`Message.items` is this same fetch per field).
    for field, raw in msg.raw_items():
        f = field.lower()
        if f == want:
            new_hits.append((field, _unfold(_fetch(msg, field, raw))))
        elif old is not None and f == old:
            old_hits.append((field, _unfold(_fetch(msg, field, raw))))

    if new_hits:
        return HeaderRead(
            name=canonical,
            value=new_hits[0][1],
            spelling=new_hits[0][0],
            prefixed=False,
            values=tuple(v for _, v in new_hits),
            ignored=tuple(f for f, _ in old_hits),
        )
    if old_hits:
        return HeaderRead(
            name=canonical,
            value=old_hits[0][1],
            spelling=old_hits[0][0],
            prefixed=True,
            values=tuple(v for _, v in old_hits),
        )
    return HeaderRead(name=canonical, value=None, spelling=None, prefixed=None)


def header(msg: Message, name: str) -> str | None:
    """The value of header NAME in MSG, read as `read_header` reads it
    (`header(msg, "Ears-Rule")`); None when absent."""
    return read_header(msg, name).value
