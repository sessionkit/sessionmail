"""The pager's rules: the grammar and its parser.

`openspec/changes/pager-filters`, tasks 2.1 (the conditions) and
D3 (the grammar).

One rule per line, and the VERB is the rule::

    # ~/.sessionmail/seats/<seat>/rules
    ride   to:kith@*                  # rides along, never wakes
    ride   from:*@delta.chat
    wake   x-project:atomnote
    wake!  from:bob@* subject:URGENT*

- **The verb comes first**: `start`, `wake`, `ride`, or `wake!` (the
  breakthrough, P1); or `carry` / `carry!`.
  EVERY WAKE CARRIES: a wake's wake turn also runs the ride-along, so `wake` judges
  as `carry` and `wake!` as `carry!`, and the two carry verbs stay valid
  as synonyms. `carry *` is `wake *`, and `carry draft:yes` is `wake
  draft:yes`: a draft wakes when a line says so, and never carries (E11,
  kept in `judge`). `set carry off` turns every wake back into a plain
  wake. Lower case, as ruled; anything else is an unknown verb.
- **Then one or more conditions, `name:glob`**, split at the FIRST colon.
  All the conditions on one line must match (AND); two lines are OR.
  `to:`, `from:` and `subject:` are the named kinds (D2); any other name is
  a HEADER, matched case-insensitively by name. A header name is printable
  ASCII letters, digits and `-`.
- **Globs, never regexes** (spine 10.26): `*` and `?`; `[` is
  literal. There are no `[...]` classes, so a bracketed tag such as
  `subject:[draft]*` matches itself (build decision O-g; D2's
  reason for globs is that they "need no escaping"). This module only
  stores them; the judgement (tasks 2.2) matches them.
- **`*` alone** is the catch-all (`ride *`, D4 step 3): a rule with no
  conditions. It stands alone; `ride * to:x` is a bad line.
- **`$me`** is this session's and this seat's addresses (D2), expanded by
  the judgement, not here. A variable is the WHOLE glob: `to:$me` is one,
  `to:$me*` is a bad line, because a partial expansion would silently
  match nothing. A `$` that names no variable (`subject:$100*`) is literal.
  `$operator` is who the operator is, ONE value
  (`operator()`), read from this host's config, never a name in code; and
  `$urgent` is the urgent allow list (`urgent_senders()`
  judgement half); each stands wherever an address glob may. The
  operator's MAILBOX is an alias for `$operator` on that host only
  (`operator_aliases`: `$alice` where `operator.mailbox` is `alice`); the
  impure readers hand it to `parse`, which stays pure.
- **`priority:`, `call:` and `draft:`** (A1, A5, P-5) are
  named kinds that read what the parsers PARSE, never a raw header:
  `priority:` globs the parsed level (`non-urgent`, `normal`, `urgent`; a
  missing or unknown `Priority:` is `normal`), `call:` globs each ENTRY of
  the calls feed (`$me` compares as genie D14 says: the domain
  case-insensitively, the local part exactly), and `draft:` is `yes` or
  `no`. A `priority:` glob that matches no level, or a `draft:` that is not
  `yes`/`no`, can never match, so it is a bad line, named.
- **`#` starts a comment** at the start of a line or after white space, so
  `subject:#123*` keeps its `#`.
- **White space separates**, so a glob cannot hold a space: write `?` or
  `*` for one. There is no quoting (none was ruled).

- **`set` is a line of the LOCKED file only** (A2): `set operator <glob...>` (who the operator
  is, A3; the host's alias spells it too) and
  `set urgent <glob...>` (who may send urgent, N3), and the switches, one
  word `on` or `off`: `set subject-shorten`, `set carry` (default
  on) and `set carry-prompt` (default off); and the one COUNT,
  `set subject-shorten-max N` (a whole number above 0, default
  10). `0`, a negative or anything not a whole number is INVALID: the line
  is named, skipped, and the default 10 applies. To never shorten, write
  `set subject-shorten off`, not 0. A `set` line is DATA,
  never a rule: `parse_settings` reads it, `parse` never makes a rule of it.
  In any other rules text (session, seat, focus, `SESSIONMAIL_PAGE_RULES`)
  it is a bad line, named, so no agent can put itself on the host-wide list.

A bad line (an unknown verb, a bad condition, a verb with no condition) is
returned as a `RuleError` naming the source, the line number and the
reason, and ONLY that line is skipped (the spec's *A line the parser does
not know*). A line that half-parses is dropped whole: keeping `ride
from:x` out of `ride from:x kith` would silence more than its author meant.

`parse(text, source)` is PURE: no file, no environment, no clock, and it
never raises over its text. The caller names the source (a path,
`SESSIONMAIL_PAGE_RULES`, `built-in`) so the error can.

**The judgement** (tasks 2.2) is `judge(facts, layers, me, now)`, also
pure, in D4's order: every `wake!` line from EVERY layer first, and a match
wakes; then the FIRST matching line down the layers (the built-in defaults
are the last layer, so they decide last); and with no line matching at all,
the mail rides (`Verdict.rule` None, layer `fallback`). The layers come in
order from the caller (a focus mode is one of them), so the judgement knows
no file. See `judge()` for how each condition reads a mail.

**The built-in defaults and the layers** (tasks 2.3, 2.4) are
`built_in()` and `load_layers(seat, session, env)`: D3's locked file,
session file, seat file and `SESSIONMAIL_PAGE_RULES`, then D4 step 3's
lines as A1, A3 and A5 amend them (`BUILT_IN_TEXT`; a draft gets
`ride *` alone, P-5). The loader reads files; `parse()` and `judge()`
still do not. The parsers (the level, the calls, the rename table)
are read from `_parsers`, `_headers` and `_voice` (an earlier stand-in
module for them was deleted when they landed).
The locked file's path is never taken from the environment
(`default_locked_path()`).

**Focus modes** (tasks 3.1-3.3): a mode is a
named rules layer, `MODES`; only `sleep` ships, and it is `ride *`. A mode
is set by a `focus` file beside a session's or a seat's rules, or
fleet-wide by a `focus sleep [until ...]` line in the locked file
(`parse_focus`, `Focus`). `load_layers` puts each set mode at D3 layer 0.5,
fleet-wide first, then session, then seat (design O-a, a proposal,
built as proposed); `judge` reads the first one still on at the mail's
arrival, which is when an end time is checked, since the pager keeps no
clock. Nothing ends a mode but its time or a deliberate change: `judge`
writes nothing, so a `wake!` wakes through a mode and leaves it on.
"""

from __future__ import annotations

import os
import pwd
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.errors import InvalidHeaderDefect
from email.headerregistry import AddressHeader
from email.message import Message
from email.parser import BytesHeaderParser
from email.policy import compat32
from email.utils import getaddresses, parseaddr
from fnmatch import fnmatchcase
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from sessionmail import _headers, _operator, _parsers, _seat, _settings, _voice

Verb = Literal["start", "wake", "ride", "wake!", "carry", "carry!"]
Action = Literal["start", "wake", "ride"]

#: The verbs: the four of D3 (`wake!` is P1), then the two carry verbs.
VERBS: tuple[str, ...] = ("start", "wake", "ride", "wake!", "carry", "carry!")
BREAKTHROUGH = "wake!"
#: A CARRY is a wake whose wake turn also runs the ride-along:
#: its action is `wake`, so the page age, `at_once` and the burst window
#: read it exactly as a wake. `carry!` is a breakthrough, as `wake!` is.
CARRY, CARRY_BREAKTHROUGH = "carry", "carry!"
#: The verbs WRITTEN as a carry. Every wake carries now, so this set no longer says
#: which lines carry (`WAKE_VERBS` does); it is only the spelling.
CARRY_VERBS: frozenset[str] = frozenset({CARRY, CARRY_BREAKTHROUGH})
#: The verbs whose line carries when it pages: `wake` is
#: `carry`, `wake!` is `carry!`.
WAKE_VERBS: frozenset[str] = frozenset({"wake", BREAKTHROUGH, CARRY, CARRY_BREAKTHROUGH})
BREAKTHROUGHS: frozenset[str] = frozenset({BREAKTHROUGH, CARRY_BREAKTHROUGH})

#: The condition names D2 gives a meaning of their own, and the three that
#: read the parsed facts (A1, A5, P-5). Any other name
#: is a header, read by name.
NAMED_KINDS = frozenset({"to", "from", "subject", "priority", "call", "draft"})

#: The variables a glob may be (the WHOLE glob): `$me` (D2), `$operator`
#: (A3: who the operator is, from config) and `$urgent` (A2's list).
#: A host's alias for `$operator` (`operator_aliases`) is not one of them:
#: it is handed to `parse` by the impure readers, so code names no person.
VARIABLES: frozenset[str] = frozenset({"me", "operator", "urgent"})
#: The variable an operator alias stands for.
OPERATOR_VAR = "operator"


#: The levels a `priority:` condition matches (N1; the parsers' parse).
LEVELS: tuple[str, ...] = _parsers.LEVELS
#: The values a `draft:` condition takes.
DRAFT_VALUES = ("yes", "no")

#: The catch-all, standing alone as a rule's only condition.
MATCH_ALL = "*"

#: The word that starts a SETTING line, and the names it
#: may set: `operator`, `urgent` (N3) and the shortener switch
#: `subject-shorten`. Only the locked file may carry one.
SET_WORD = "set"
SETTING_NAMES: tuple[str, ...] = ("operator", "urgent", "subject-shorten",
                                   "carry", "carry-prompt", "subject-shorten-max")
#: The SWITCH names among them: exactly one word, `on` or `off`,
#: never an address glob; each is a `Settings` bool under its own field name.
#: `carry` is the host kill switch for carry (every wake), default ON;
#: `carry-prompt` (R8) lets a human-typed prompt carry too, default OFF.
SWITCHES: dict[str, str] = {"subject-shorten": "subject_shorten",
                            "carry": "carry", "carry-prompt": "carry_prompt"}
SWITCH_VALUES = ("on", "off")
#: The COUNT names among them: exactly one word, a whole number
#: above 0, never a glob; each is a `Settings` int under its own field name.
#: `subject-shorten-max` caps how far over the limit a subject the shortener
#: may take (the OVERAGE).
SHORTEN_MAX = "subject-shorten-max"
COUNTS: dict[str, str] = {SHORTEN_MAX: "subject_shorten_max"}
#: The cap when the locked file sets none.
DEFAULT_SHORTEN_MAX = 10
_COUNT = re.compile(r"[0-9]+\Z")
#: Why a `set` line anywhere but the locked file is skipped.
LOCKED_ONLY = ("'set' is a line of the locked file (~/.sessionmail/rules.locked) "
               "only: skipped here, it sets nothing")

#: A header field name, as far as a rule may write one: RFC 5322 allows
#: any printable ASCII but `:`, and every header a mail here carries is
#: letters, digits and `-`. Narrower is the safe side: a typo is named.
_HEADER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\Z")
#: `$` then an identifier: the only spelling a variable has.
_VARIABLE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
#: A comment: `#` at the start of the line or after white space.
_COMMENT = re.compile(r"(?:^|\s)#.*\Z", re.DOTALL)


@dataclass(frozen=True)
class Condition:
    """One `name:glob`. `name` is folded to lower case; the glob is kept as
    written (the judgement decides how each kind compares)."""

    name: str
    glob: str

    @property
    def kind(self) -> str:
        """`to`, `from`, `subject`, `priority`, `call`, `draft`, or
        `header`."""
        return self.name if self.name in NAMED_KINDS else "header"

    @property
    def variable(self) -> str | None:
        """The variable this glob IS (`me` for `$me`), or None."""
        m = _VARIABLE.fullmatch(self.glob)
        return m.group(1) if m and m.group(1) in VARIABLES else None


@dataclass(frozen=True)
class Rule:
    """One parsed line. `text` is the line as written, less its comment and
    outer white space: what `pager why` and a verdict show."""

    verb: str
    conditions: tuple[Condition, ...]
    source: str
    line: int
    text: str

    @property
    def action(self) -> Action:
        """What the mail gets: `wake!` wakes (it is a breakthrough, D4
        step 1), and `carry`/`carry!` wake (a carry IS a wake);
        every other verb is its own action."""
        if self.verb in BREAKTHROUGHS or self.verb in CARRY_VERBS:
            return "wake"
        return self.verb  # type: ignore[return-value]

    @property
    def breakthrough(self) -> bool:
        """True for `wake!` and `carry!`: judged first, from every layer."""
        return self.verb in BREAKTHROUGHS

    @property
    def carries(self) -> bool:
        """True for every verb that wakes: `wake`, `wake!`, `carry` and
        `carry!` (every wake carries). When this line pages, the wake turn runs the ride-along.
        Whether it does is `Verdict.carry`, which the host switch can turn
        off (`carry_on`) and a draft never earns (E11). `start` and `ride`
        never carry."""
        return self.verb in WAKE_VERBS

    @property
    def matches_all(self) -> bool:
        """True for `<verb> *`: no condition, so every mail matches."""
        return not self.conditions

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class RuleError:
    """A bad line: named, with its source and line number, and skipped."""

    source: str
    line: int
    text: str
    reason: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: {self.reason}: {self.text}"


class _Bad(Exception):
    """Internal: why one line is bad. Never escapes `parse()`."""


def _known(aliases: frozenset[str]) -> str:
    """The variables a bad-variable message lists: the built-ins, and this
    host's alias for `$operator` when it has one."""
    return ", ".join(f"${v}" for v in sorted(VARIABLES | aliases))


def _condition(token: str, aliases: frozenset[str] = frozenset()) -> Condition:
    """One `name:glob`, or `_Bad`. A whole glob that is one of ALIASES
    (this host's alias for `$operator`) is stored as `$operator`; the rule's
    `text` keeps it as written."""
    name, colon, glob = token.partition(":")
    if not colon:
        raise _Bad(f"{token!r} is not a condition (want name:glob, or * alone)")
    if not name:
        raise _Bad(f"{token!r} has no header name before the colon")
    if not _HEADER_NAME.match(name):
        raise _Bad(f"{name!r} is not a header name (letters, digits and -)")
    if not glob:
        raise _Bad(f"{token!r} has an empty glob (write * to match any value)")
    whole = _VARIABLE.fullmatch(glob)
    written = glob
    if whole:
        if whole.group(1) in aliases:
            glob = f"${OPERATOR_VAR}"
        elif whole.group(1) not in VARIABLES:
            raise _Bad(f"unknown variable {glob!r} (known: {_known(aliases)})")
    else:
        for m in _VARIABLE.finditer(glob):
            if m.group(1) in VARIABLES or m.group(1) in aliases:
                raise _Bad(f"${m.group(1)} must be the whole glob, not part of {glob!r}")
    name = name.lower()
    if name in ("priority", "draft") and whole:   # a built-in or this host's alias
        raise _Bad(f"{name}: takes a value, not a variable ({written!r})")
    if name == "priority" and not any(fnmatchcase(lv, _glob(glob)) for lv in LEVELS):
        raise _Bad(f"priority:{glob} matches no level (want one of: {', '.join(LEVELS)})")
    if name == "draft" and glob.lower() not in DRAFT_VALUES:
        raise _Bad(f"draft:{glob} is not yes or no")
    return Condition(name, glob)


def _rule(body: str, source: str, number: int,
          aliases: frozenset[str] = frozenset()) -> Rule:
    verb, *tokens = body.split()
    if verb == SET_WORD:
        # The locked file's own `set` lines never reach here (`load_layers`
        # takes them out first), so a `set` here is in the wrong layer.
        raise _Bad(LOCKED_ONLY)
    if verb not in VERBS:
        raise _Bad(f"unknown verb {verb!r} (want one of: {', '.join(VERBS)})")
    if not tokens:
        raise _Bad(f"{verb!r} has no condition (write * to match every mail)")
    if MATCH_ALL in tokens:
        if tokens != [MATCH_ALL]:
            raise _Bad("'*' stands alone: it already matches every mail")
        # `carry *` is no longer refused. Every wake carries, so
        # `carry *` IS `wake *` (an alias), and the two can never disagree.
        conditions: tuple[Condition, ...] = ()
    else:
        conditions = tuple(_condition(t, aliases) for t in tokens)
    # E11: `carry draft:yes` is no longer refused either; it is
    # `wake draft:yes`, a line live seats' files may already hold. A draft
    # still never carries: `judge` keeps that, for every line.
    return Rule(verb, conditions, source, number, body)


def parse(text: str, source: str, *,
          aliases: frozenset[str] = frozenset()) -> tuple[tuple[Rule, ...], tuple[RuleError, ...]]:
    """Parse a rules text into `(rules, errors)`, both in line order.

    Pure: `source` is only carried into each `Rule` and `RuleError`, never
    opened. Blank and comment-only lines give neither. A bad line gives one
    `RuleError` and no rule; the lines around it are unaffected.

    ALIASES are this host's spellings of `$operator` (`operator_aliases`),
    passed in by the impure readers so this stays pure.
    A host's alias is no variable anywhere else: on a host whose operator
    is `alice`, `$bob` is an unknown variable, a named bad line.
    """
    rules: list[Rule] = []
    errors: list[RuleError] = []
    if text.startswith("﻿"):
        text = text[1:]
    # Lines end at LF only (a CR before it is dropped): `splitlines()` would
    # also break at form feeds and U+2028, and the line numbers would stop
    # being the ones an editor shows.
    for number, raw in enumerate(text.split("\n"), start=1):
        body = _COMMENT.sub("", raw.removesuffix("\r")).strip()
        if not body:
            continue
        try:
            rules.append(_rule(body, source, number, aliases))
        except _Bad as bad:
            errors.append(RuleError(source, number, body, str(bad)))
    return tuple(rules), tuple(errors)


# ── the judgement (tasks 2.2) ────────────────────────────────────────────────

#: The layer a verdict names when NO line matched: the mail rides, the least
#: action, because every mail rides along anyway (D1). With the built-in
#: `ride *` loaded this is never reached; it exists so that a
#: caller holding no layers still gets an answer, and an honest one.
FALLBACK = "fallback"

#: Line folding (RFC 5322 2.2.3): a CRLF (or LF) before white space.
_FOLD = re.compile(r"\r?\n(?=[ \t])")
#: A byte the parser could not decode, kept as a lone surrogate.
_ESCAPED = re.compile("[\udc80-\udcff]")


def _fold(value: str) -> str:
    """How every glob and value compare: case-insensitively. Neither D2 nor
    the rulings name a case rule, so this follows Sieve's default comparator
    (`i;ascii-casemap`, RFC 5228 2.7.3), the one mail filters already teach:
    `subject:URGENT*` catches `Urgent: ...`, and `from:Alice@*` catches
    `alice@host`. A build choice, named for review."""
    return value.casefold()


#: The headers the stdlib reads as addresses (From, To, Cc, Bcc, Reply-To,
#: Sender and their Resent- forms), taken once from its registry.
_ADDRESS_HEADERS = frozenset(
    n for n, c in policy.default.header_factory.registry.items()
    if issubclass(c, AddressHeader))


def _decode(name: str, raw: str) -> str:
    """One header value as a person reads it: unfolded, RFC 2047 words
    decoded, outer white space trimmed. A value that will not decode (a bad
    charset, a broken encoded word) is kept as it came, never dropped: a
    rule may still match its raw text, and the judgement never raises.

    The decoding is `email.policy.default`'s, not `decode_header`'s, for
    two reasons found in review. An ADDRESS header is decoded by its
    address class, which keeps a decoded display name QUOTED: RFC 2047 lets
    a name hide a comma (`=?utf-8?q?Smith=2C_Alice?= <alice@host>`), and
    decoded bare it read as two addresses, so the From address was lost
    and the operator's `wake!` missed. And raw 8-bit text beside an encoded word,
    which `decode_header` turned into literal escape text, decodes whole.
    Every other header is decoded as unstructured text (the class Subject
    uses), so a Content-Type or a Date is never re-rendered under a glob.
    """
    value = _FOLD.sub("", raw)
    try:
        header = policy.default.header_factory(
            name if name in _ADDRESS_HEADERS else "subject", value)
        if any(isinstance(d, InvalidHeaderDefect) for d in header.defects):
            # An address the class could not parse (`a@b@c` renders as `<>`)
            # is read as text instead, so its words are kept for a glob.
            header = policy.default.header_factory("subject", value)
        value = str(header)
    except Exception:  # noqa: BLE001 - try the plain decoder, then keep raw
        try:
            value = str(make_header(decode_header(value)))
        except Exception:  # noqa: BLE001 - any decode failure keeps the raw text
            pass
    if _ESCAPED.search(value):
        # An 8-bit header (raw UTF-8, as SMTPUTF8 sends it) comes out of the
        # parser as surrogate escapes; read those bytes as the UTF-8 they
        # almost always are. Bytes that are not UTF-8 become U+FFFD, and the
        # rest of the value is kept.
        value = value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return value.strip()


def _addresses(*values: str) -> tuple[str, ...]:
    """The addresses in VALUES, as `getaddresses` reads them, empty ones
    dropped. An address it cannot read gives none, never an exception:
    RFC 5322 comments nest and `email.utils` parses them recursively, so
    an address behind ~500 levels of `(` raised RecursionError out of
    `judge` (found in the second review). The listener judges every mail
    it hears, so that was a mail any sender could write to deafen a seat."""
    try:
        return tuple(a for _, a in getaddresses(list(values)) if a)
    except RecursionError:
        return ()


def _address(value: str) -> str:
    """The one address in VALUE, as `parseaddr` reads it, or "" (the same
    guard as `_addresses`)."""
    try:
        return parseaddr(value)[1]
    except RecursionError:
        return ""


@dataclass(frozen=True)
class Facts:
    """Everything the judgement reads about ONE mail (design Part 3).

    `headers` holds each header's FIRST occurrence (D2: `<header>:` reads
    the first), decoded, under its name folded to lower case. Build it with
    `from_bytes()` or `from_message()`; the conditions read it through
    `recipients()`, `sender()` and `header()`.

    The parsed facts, each defaulted so `Facts(headers)` is still a
    mail with no level, no call and no draft:

    - `priority`: the PARSED level (`non-urgent`, `normal`, `urgent`).
      What `priority:` reads.
    - `calls`: the addresses the mail calls, and `everyone` (genie 3.21),
      `()` unless the mail is voice (trusted, or UNVERIFIED while
      `_voice.UNVERIFIED_CALLS` is on). What `call:` reads.
    - `draft`: True only when the draft watch (3.18c) judges a draft;
      every box arrival is False. A draft's built-in layer is `ride *`
      alone (P-5), and `draft:` reads it.
    - `voice`: the mail's voice state (`_voice.TRUSTED`, `UNVERIFIED` or
      `PLAIN`). Only TRUSTED voice's calls are unclamped: on any
      other state a line keyed on `call:` does at most `carry` (`judge`).
      Defaulted to PLAIN, so a hand-built `Facts` with calls is clamped
      (fail closed); `from_message` sets it from `_voice.recognise`.
    """

    headers: Mapping[str, str] = field(default_factory=dict)
    priority: str = _parsers.NORMAL
    calls: tuple[str, ...] = ()
    draft: bool = False
    voice: str = _voice.PLAIN

    @classmethod
    def from_message(cls, msg: Message,
                     env: Mapping[str, str] | None = None, *,
                     signed: "_voice.Signed | None" = None) -> "Facts":
        """Facts from a parsed mail, whatever policy parsed it: the RAW
        values are read and decoded here, so a compat32 message and a
        `policy.default` one give the same facts. The level and the calls
        are the parsers' (`_parsers`); ENV names this
        host for the keeper test (os.environ when None), as the parsers'
        `calls(msg, env)` takes it. SIGNED is the keeper-signature verdict
        seam (`_voice.signed_file` for a mail on disk); without it a voice
        mail is UNVERIFIED. Never a draft: only the draft watch makes one."""
        headers: dict[str, str] = {}
        for name, raw in msg.raw_items():
            key = name.strip().lower()
            if key not in headers:
                headers[key] = _decode(key, raw if isinstance(raw, str) else str(raw))
        rec = _voice.recognise(msg, env, signed)
        return cls(headers, _parsers.priority(msg).level, _parsers.calls(msg, env, voice=rec),
                   voice=rec.state)

    @classmethod
    def from_bytes(cls, raw: bytes, env: Mapping[str, str] | None = None, *,
                   signed: "_voice.Signed | None" = None) -> "Facts":
        """Facts from a mail's bytes (its headers are enough; SIGNED, when
        given, judges the signature over the whole file)."""
        return cls.from_message(BytesHeaderParser(policy=compat32).parsebytes(raw), env,
                                signed=signed)

    def header(self, name: str) -> str | None:
        """What a `<header>:` condition reads: NAME's first
        occurrence, decoded; for a name in the rename table
        (`_headers.MIGRATING`, as data) its `X-` spelling
        when NAME itself is absent. Presence decides: an empty unprefixed
        value still wins. Any other name is read exactly as before."""
        name = name.lower()
        if name in self.headers:
            return self.headers[name]
        old = _headers.old_spelling(name)
        return self.headers.get(old) if old is not None else None

    def recipients(self) -> tuple[str, ...]:
        """What `to:` reads (D2): the `X-Original-To` address when the mail
        carries a non-empty one, and ONLY that, since it names the address
        this copy came THROUGH, which is what tells "mailed to me" from
        "reached me through a subscription". Without it, every address in
        `To` and `Cc`."""
        original = self.headers.get("x-original-to", "")
        if original:
            return _addresses(original) or (original,)
        values = [self.headers[h] for h in ("to", "cc") if h in self.headers]
        return _addresses(*values)

    def sender(self) -> str | None:
        """What `from:` reads: the From ADDRESS, never its display name."""
        value = self.headers.get("from")
        if value is None:
            return None
        return _address(value) or None


@dataclass(frozen=True)
class Me:
    """Who `$me` is (D2): this seat's address and this session's, as
    folded local parts, with the host that makes a domain "ours".

    A seat that is itself a `+tag` on a shared base (`example+ops`) is
    stored WHOLE, so `$me` reaches `example+ops` and its own tags but never
    the bare `example`, which is the store's (D2, measured). `host` empty means no domain is checked.
    """

    addresses: tuple[str, ...]
    host: str = ""

    @classmethod
    def of(cls, *, session: str = "", seat: str = "", host: str = "") -> "Me":
        """`Me` from a session id, a seat address and this host's name
        (short or full). The seat comes first, as `_seat.own_addresses`
        orders them; a repeat or a `-` (no session) is dropped."""
        out: list[str] = []
        for value in (seat, session):
            local = value.strip().lower().split("@", 1)[0]
            if local and local != "-" and local not in out:
                out.append(local)
        return cls(tuple(out), host.strip().lower())

    @property
    def domains(self) -> tuple[str, ...]:
        """This host's names, full and short (`host.<...>`, `host`)."""
        short = self.host.split(".", 1)[0]
        return tuple(dict.fromkeys(d for d in (self.host, short) if d))

    def local(self, domain: str) -> bool:
        """True when DOMAIN is absent or names this host (`host`, `host.<any>`):
        the same test `_seat.local_domain` makes for the DM rule."""
        return not domain or not self.host or \
            domain.lower().split(".", 1)[0] == self.host.split(".", 1)[0]

    def called(self, key: str) -> bool:
        """True when a call entry KEY is exactly one of mine (R5.2, genie
        D14): the local part compared EXACTLY (no `+tag` reach, no
        case fold), the domain case-insensitively and whole, as one of this
        host's names; a bare key is an address on this host. With no host
        set, no domain is checked (as `local`)."""
        key = key.strip()
        if not self.host:
            return (key.rpartition("@")[0] if "@" in key else key) in self.addresses
        forms = (*self.addresses,
                 *(f"{own}@{d}" for own in self.addresses for d in self.domains))
        return any(_parsers.same_address(key, form) for form in forms)

    def owns(self, address: str) -> bool:
        """True when ADDRESS is one of mine or one of their `+tag` forms,
        on this host. The `+` is a boundary, never a bare prefix: `alice`
        does not own `alicea`."""
        local, at, domain = address.strip().lower().rpartition("@")
        if not at:
            local, domain = domain, ""
        if not local or not self.local(domain):
            return False
        return any(local == own or local.startswith(own + "+") for own in self.addresses)

    def forms(self, address: str) -> tuple[str, ...]:
        """ADDRESS as a glob may meet it. A bare local part (no domain) is an
        address on THIS host, so it is also offered qualified with each of
        this host's names: MEASURED, 460 of 500
        `X-Original-To` values in one seat's `cur/` carry no domain, and the
        spec's example `ride to:kith@*` must still reach them."""
        address = address.strip()
        if not address:
            return ()
        if "@" in address:
            return (address,)
        return (address, *(f"{address}@{d}" for d in self.domains))


@dataclass(frozen=True)
class Layer:
    """One layer of D3, named as a verdict names it (`locked`,
    `focus: sleep`, `session`, `seat`, `env`, `built-in`), with its rules
    in line order. The judgement reads the layers in the order given.

    A FOCUS layer (D3 0.5, D5; tasks 3.1) says where its mode was set in
    `focus` (`fleet`, `session` or `seat`) and may carry an end time,
    `until`. Every other layer leaves both None. The judgement reads ONE
    focus layer, the first still on at the mail's arrival (`ended`), and
    passes over the rest (design O-a: the caller orders them fleet-wide,
    session, seat)."""

    name: str
    rules: tuple[Rule, ...]
    focus: str | None = None
    until: datetime | None = None

    def ended(self, now: datetime | None) -> bool:
        """True once NOW is at or past `until` (tasks 3.3: the end is
        checked at arrival, since the pager keeps no clock). With no end
        time, or no NOW to compare, it has not ended: the mode holds, the
        side that keeps the quiet its author asked for."""
        return _ended(self.until, now)


@dataclass(frozen=True)
class Verdict:
    """What the judgement decided for one mail (design Part 3).

    `rule` is the deciding line as written (`Rule.text`), `layer` the
    layer's name and `line` its line number; with no line matching, `rule`
    and `line` are None and `layer` is `FALLBACK`. `at_once` is A4's
    "an honoured urgent pages a busy seat at once" (P-3; the listener
    reads it): the action is `wake`, the parsed level is
    `urgent`, and the urgent is HONOURED, by its From on the urgent list,
    or by the deciding line being a seat or session rule that names
    `priority:urgent` (A2 (b)). So it depends on WHICH line decided,
    not on the mail alone.

    `carry` is True when the deciding line wakes
    (`wake`, `wake!`, `carry` or `carry!`: every wake carries),
    the host switch (`set carry off`) has not turned carry
    off, and the mail is not a draft (E11): then a page this verdict fires
    lets the wake turn run the ride-along. Every other verdict is False, an
    UNJUDGED wake included (E10: it wakes, but it has earned no carry).
    """

    action: Action
    rule: str | None
    layer: str
    line: int | None
    at_once: bool = False
    carry: bool = False


def _glob(text: str) -> str:
    """A rule's glob as `fnmatchcase` must see it: folded (`_fold`), with
    every `[` escaped as `[[]` so it is literal, not a class opener (build
    decision O-g). A `]` with no opener is already literal to fnmatch, and
    `*` and `?` stay the only wildcards. Escaping, rather than a matcher of
    our own, keeps fnmatch's non-backtracking compile."""
    return _fold(text).replace("[", "[[]")


def _globs(cond: Condition, me: Me, settings: Settings | None) -> tuple[str, ...]:
    """The globs COND stands for: its own, or the list a `$operator` or
    `$urgent` expands to (read once, from the ONE reader of each, with the
    SETTINGS handed down: never None there, which would read a file)."""
    settings = NO_SETTINGS if settings is None else settings
    if cond.variable == OPERATOR_VAR:
        return operator(settings)
    if cond.variable == "urgent":
        return urgent_senders(me.domains, settings=settings)
    return (cond.glob,)


def _condition_matches(cond: Condition, facts: Facts, me: Me,
                       settings: Settings | None = None) -> bool:
    kind = cond.kind
    if kind == "priority":  # the PARSED level, never the raw header (A1)
        return fnmatchcase(facts.priority, _glob(cond.glob))
    if kind == "draft":
        return facts.draft is (cond.glob.lower() == "yes")
    if kind == "to":
        values: tuple[str, ...] = facts.recipients()
    elif kind == "from":
        sender = facts.sender()
        values = (sender,) if sender is not None else ()
    elif kind == "call":  # each ENTRY of the calls feed (A5)
        values = facts.calls
    else:  # `subject` is a header too: the decoded Subject
        value = facts.header(cond.name)
        values = (value,) if value is not None else ()
    if cond.variable is not None and kind not in ("to", "from", "call"):
        # a header naming an address: `Reply-To: Alice <alice@host>`
        values = tuple(_address(v) or v for v in values)
    if cond.variable == "me":
        if kind == "call":
            return any(me.called(v) for v in values)
        return any(me.owns(v) for v in values)
    globs = tuple(_glob(g) for g in _globs(cond, me, settings))
    if kind in ("to", "from"):
        return any(fnmatchcase(_fold(form), g)
                   for v in values for form in me.forms(v) for g in globs)
    return any(fnmatchcase(_fold(v), g) for v in values for g in globs)


def matches(rule: Rule, facts: Facts, me: Me, settings: Settings | None = None) -> bool:
    """True when every condition on RULE matches (AND); `*` matches all.
    `$operator` and `$urgent` read SETTINGS (the locked file's and the
    host config's, as `Layers` carries them); None is the defaults (no
    operator), never a file read."""
    return all(_condition_matches(c, facts, me, settings) for c in rule.conditions)


def _aware(when: datetime) -> datetime:
    """WHEN as an aware time: a naive one is LOCAL, as a person writes
    `until 07:00` and as `datetime.now()` gives it."""
    return when if when.tzinfo is not None else when.astimezone()


def _ended(until: datetime | None, now: datetime | None) -> bool:
    """True once NOW is at or past UNTIL; never with either one missing."""
    return until is not None and now is not None and _aware(now) >= _aware(until)


def _in_effect(layers: Sequence[Layer], now: datetime | None) -> tuple[Layer, ...]:
    """LAYERS less every focus layer that has ended at NOW and every focus
    layer after the first one still on: one mode is in effect at a time."""
    out: list[Layer] = []
    have_focus = False
    for layer in layers:
        if layer.focus is not None:
            if have_focus or layer.ended(now):
                continue
            have_focus = True
        out.append(layer)
    return tuple(out)


#: The layers whose rule may honour an `urgent` off the list, by naming
#: `priority:urgent` (A2 (b): "a seat widens the list for itself").
_WIDENING_LAYERS = frozenset({"session", "seat"})
#: `from:$urgent`, the list test, as a condition (built once).
_ON_THE_LIST = Condition("from", "$urgent")


def _names_urgent(rule: Rule) -> bool:
    """True when RULE has a `priority:` condition that picks out `urgent`
    alone: `priority:urgent` or `priority:urg*`, but not `priority:*`,
    which matches every level and names none."""
    return any(c.kind == "priority"
               and [lv for lv in LEVELS if fnmatchcase(lv, _glob(c.glob))] == ["urgent"]
               for c in rule.conditions)


def _at_once(facts: Facts, rule: Rule, layer: Layer, me: Me, settings: Settings) -> bool:
    """Design A4, as corrected in review (R-A4): an honoured `urgent`
    given `wake`. Honoured by From (on the urgent list) or by the deciding
    line (a session or seat rule naming `priority:urgent`)."""
    if rule.action != "wake" or facts.priority != "urgent":
        return False
    if layer.name in _WIDENING_LAYERS and _names_urgent(rule):
        return True
    return _condition_matches(_ON_THE_LIST, facts, me, settings)


#: What a line keyed on `call:` may do when the call is not
#: trusted voice. These verbs do more than plain mail can (start a seat, break
#: through a busy one or a focus mode), so on UNVERIFIED voice each is
#: clamped to `carry`: a wake that carries, in its place among the
#: non-breakthrough lines. `wake`, `carry` and `ride` are already within it.
CALL_CLAMPED: frozenset[str] = frozenset({"start", BREAKTHROUGH, CARRY_BREAKTHROUGH})

#: What a clamped line's verdict adds to the line as written, so `pager why`
#: says the verb did not apply.
CLAMP_NOTE = "[clamped to carry: unverified voice]"


def _clamped(rule: Rule, facts: Facts) -> Rule:
    """RULE as it may act on FACTS. Unchanged unless the mail's calls are
    not TRUSTED voice and RULE is a `start`, `wake!` or `carry!` line with a
    `call:` condition: then it is a `carry` line (an unverified call
    may do nothing beyond what plain mail can already do to the seat). The
    built-in `carry call:$me` and `carry call:everyone` are within it."""
    if facts.voice == _voice.TRUSTED or rule.verb not in CALL_CLAMPED \
            or not any(c.kind == "call" for c in rule.conditions):
        return rule
    return replace(rule, verb=CARRY, text=f"{rule.text} {CLAMP_NOTE}")


def _clamp_layers(layers: Sequence[Layer], facts: Facts) -> tuple[Layer, ...]:
    """LAYERS with every line `_clamped` for FACTS, and no clamped line
    above the focus mode in effect. A breakthrough is what passes a focus
    mode (D5: sleep's `ride *` catches every mail the `wake!` pass let by),
    so a clamped line in a layer ABOVE the focus layer (the locked file's
    `wake! call:$me`) is judged just BELOW it, under its own layer's name:
    asleep, an unverified call rides as plain mail To the seat would; awake,
    it carries from the place it was written. With no focus in effect, or a
    line in or below the focus layer, the line keeps its place."""
    focus_at = next((i for i, layer in enumerate(layers) if layer.focus is not None), None)
    out: list[Layer] = []
    below: list[Layer] = []
    for i, layer in enumerate(layers):
        kept: list[Rule] = []
        moved: list[Rule] = []
        for rule in layer.rules:
            judged = _clamped(rule, facts)
            above = focus_at is not None and i < focus_at
            (moved if judged is not rule and above else kept).append(judged)
        out.append(replace(layer, rules=tuple(kept)))
        if moved:
            below.append(Layer(layer.name, tuple(moved)))
        if i == focus_at:
            out.extend(below)
    return tuple(out)


def _decided(facts: Facts, rule: Rule, layer: Layer, me: Me, settings: Settings) -> Verdict:
    # E11: a draft is never carried, whatever line wakes it.
    return Verdict(rule.action, rule.text, layer.name, rule.line,
                   _at_once(facts, rule, layer, me, settings),
                   rule.carries and carry_on(settings) and not facts.draft)


def judge(facts: Facts, layers: Sequence[Layer], me: Me,
          now: datetime | None = None, *, settings: Settings | None = None) -> Verdict:
    """The action one mail gets, and the line that gave it (D4).

    1. Every `wake!` line, from EVERY layer, in layer order: the first that
       matches wakes. This pass comes first so that no layer's `ride *` can
       swallow a breakthrough (the spec's *A session cannot silence the operator*).
    2. Then the FIRST matching line of any other verb, down the layers;
       its verb is the action. The built-in defaults are the caller's last
       layer, so they decide last (D4 step 3).
    3. No line matched: `ride`, with no rule named (`FALLBACK`).

    How the conditions read the mail (D2):

    - `to:` the `X-Original-To` address when there is one, else every `To`
      and `Cc` address (`Facts.recipients`).
    - `from:` the From address, not its display name (`Facts.sender`).
    - `subject:` and any other name: that header's first occurrence,
      decoded; a header the mail does not carry matches nothing, not even
      `*`.
    - `$me` is `Me.owns`: mine, or one of my `+tag` forms, on this host.
    - Globs are `*` and `?` (a `[` is literal), anchored, and
      case-insensitive; a bare local part is an address on this host
      (`Me.forms`).

    A focus mode (D5) is a layer like the others, at D3 0.5, so `sleep`'s
    `ride *` catches every mail the `wake!` pass let by, and a `wake!`
    wakes THROUGH it. Before either pass, a focus layer whose end time NOW
    has reached is dropped (tasks 3.3), and of the focus layers still on
    only the FIRST is read (design O-a). Nothing here ends a mode: a
    `wake!` leaves it as it was, since the judgement writes nothing.

    A DRAFT (`Facts.draft`, set only by the draft watch) is judged
    with `ride *` in place of the built-in layer (P-5: a draft with
    no rule for it rides and never wakes); every other layer applies to it
    as to a mail, so a seat's own rule may still wake on one (N11/N12). A
    draft never CARRIES (E11): its verdict's `carry` is False, whatever
    line woke it.

    `Verdict.at_once` is computed here (A4; see `Verdict`).

    `$operator` and `$urgent` read the locked file's `set` lines
    and the host's operator config as SETTINGS carries
    them: given, else the `Layers.settings` that `load_layers` read, else
    the defaults (`NO_SETTINGS`: no operator, so `$operator` matches
    nothing). Plain layers get the defaults: the judgement never reads a
    file itself.

    Pure: nothing here reads a file, the environment or the clock. `now` is
    the mail's arrival time, the only clock a mode's end is checked by; with
    `now` None an end time cannot be checked and the mode holds.
    """
    if settings is None:
        settings = getattr(layers, "settings", None)
    if not isinstance(settings, Settings):
        settings = NO_SETTINGS
    layers = _in_effect(layers, now)
    if facts.draft:
        layers = tuple(draft_built_in() if layer.name == BUILT_IN else layer
                       for layer in layers)
    # A `call:` line on a call that is not trusted voice is judged
    # as the `carry` it is clamped to, in the second pass, never above a
    # focus mode.
    if facts.calls and facts.voice != _voice.TRUSTED:
        layers = _clamp_layers(layers, facts)
    for layer in layers:
        for rule in layer.rules:
            if rule.breakthrough and matches(rule, facts, me, settings):
                return _decided(facts, rule, layer, me, settings)
    for layer in layers:
        for rule in layer.rules:
            if not rule.breakthrough and matches(rule, facts, me, settings):
                return _decided(facts, rule, layer, me, settings)
    return Verdict("ride", None, FALLBACK, None)


# ── the built-in defaults (tasks 2.3) and the layers (tasks 2.4) ─────────────

#: D4 step 3, as amended by A1, A3 and A5 and P-8, first match wins down
#: the lines:
#:
#: Carry turns four of them into CARRY lines,
#: the ones whose sender is already vouched for (E6): the operator, the two call
#: lines (empty unless the parser says the mail is trusted voice) and an urgent
#: from the urgent list. A carry is a wake, so every decision below stands;
#: only the wake turn's payload changes.
#:
#: Every wake carries, so `wake to:$me` carries too: E6 kept it a plain wake for
#: salience only (the same text, seconds earlier, by the re-arm's
#: ride-along), and found no new exposure. The lines keep their words: the
#: four `carry` lines say what the others now do as well.
#:
#: - `carry! from:$operator`: all the operator's mail wakes, whatever its
#:   level (N14). With no operator configured it matches nothing.
#: - `carry call:$me`, `carry call:everyone`: only a CALL wakes a heard turn
#:   (N10). No `From:` test: `Facts.calls` is empty unless the parser says
#:   the mail is trusted voice (R5.5). ABOVE the Priority lines: genie P-8,
#:   *when a Priority rule and a
#:   call rule both match a voice turn, the CALL rule wins*. So a
#:   non-urgent turn that calls me wakes, and one that calls nobody rides.
#: - `ride priority:non-urgent`: non-urgent rides, never wakes (N2); above
#:   `wake to:$me`, so a DIRECT non-urgent mail rides too.
#: - `carry priority:urgent from:$urgent`: an urgent wakes only From the
#:   urgent list (N3, P-7; `set urgent` in the locked file, A2); from anyone
#:   else it meets neither Priority line and falls through as `normal`.
#:   `normal` meets neither, unchanged (N2).
#: - `wake to:$me`: direct mail wakes, and carries; everything
#:   else, which reached the box through a subscription, rides along.
#: - `ride *`: everything else. START is never a default.
#:
#: One space between words: `Rule.text` keeps a line as written, and it is
#: what a verdict, `pager why` and the banner show.
BUILT_IN_TEXT = """\
carry! from:$operator
carry call:$me
carry call:everyone
ride priority:non-urgent
carry priority:urgent from:$urgent
wake to:$me
ride *
"""

#: The built-in layer a DRAFT gets in place of `BUILT_IN_TEXT` (genie P-5:
#: a draft with no rule for it rides and never
#: wakes). Only this layer changes for a draft; when P-5 moves, this does.
DRAFT_BUILT_IN_TEXT = "ride *\n"

#: Who the operator is has NO default in code: it is
#: this host's config (`_operator.read()`, `operator_globs`), and `set
#: operator` in the locked file overrides it. `operator()` is
#: the ONE reader.
#: The host's alert runner (N3, P-7) has NO default in code either (no
#: persona name is a behaviour default): its LOCAL
#: PART is `alert_runner` in the mail tool's host settings
#: (`_settings.SHOWN`), read by `alert_runner()`, the ONE reader,
#: from the TEMPLATE and HOST layers only (`_operator.host_values`, pwd's
#: home), as `_mhs.configured_registry` reads `registry`. Unset is no runner.
ALERT_RUNNER_KEY = "alert_runner"
#: The mail tool's settings name, whose host layers hold the key.
MAIL_TOOL = _settings.TOOL
#: What a runner's local part may hold (the fail-safe
#: table): letters, digits, `.`, `_`, `+` and `-`, nothing else. So never
#: `@` (a whole address), white space, `$` (a variable) or a glob character
#: (`*?[]!`): a configured runner can only name `<it>@<this host>`, never
#: widen the list. Mirrored by `mail.schema.json`'s pattern.
_RUNNER = re.compile(r"[A-Za-z0-9._+-]+\Z")


def runner_name(value: object) -> str | None:
    """VALUE as an alert runner's local part, lower-cased, or None when it
    is not one: not a string, empty, or holding anything outside `_RUNNER`
    (no `@`, white space, `$` or glob character, so it is never a glob,
    a whole address or a variable). A bad value is no runner: it can only
    REMOVE the runner from the urgent list, never widen it. Pure."""
    if not isinstance(value, str) or not _RUNNER.match(value):
        return None
    return value.lower()


def alert_runner() -> str | None:
    """This host's alert runner's local part, read now: `alert_runner` from
    the mail tool's TEMPLATE and HOST layers (`_operator.host_values`: pwd's
    home, never `$HOME`; never a seat or session layer, which agents write,
    so no agent can put a runner on the urgent list). Unset, a bad value
    (`runner_name`) or a file that cannot be read: None. Never raises."""
    try:
        values = _operator.host_values(MAIL_TOOL)
    except Exception:  # noqa: BLE001 - fail safe: a bad mail.json is no runner
        return None
    return runner_name(values.get(ALERT_RUNNER_KEY))


# ── the locked file's settings (A2) ──────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    """The locked file's `set` lines (A2): each name's address globs AS WRITTEN (`$operator` unexpanded;
    a host alias is stored as `$operator`), or None when the locked file
    does not set it and the default stands.

    - `operator`: `set operator <glob...>`, who the operator is.
      Default: `configured`.
    - `urgent`: `set urgent <glob...>`, who may send `urgent` and be
      honoured (N3). It REPLACES the default, `$operator` and the
      configured alert runner at this host (`runner`), so it can narrow the
      list as well as widen it. `$operator` (or this host's alias for it)
      may stand in it.

    - `subject_shorten`: `set subject-shorten on|off`, the HOST switch of
      `message send`'s subject shortener. None (no line) is OFF,
      the tool's default; only this line can turn it on, and an agent cannot
      move the file (`locked_path`, never the environment).
    - `carry`: `set carry on|off`, the HOST kill switch for carry.
      None (no line) is ON; `off` makes every wake, `carry` lines and
      (as every wake carries) `wake` lines alike, judge as a plain wake.
    - `carry_prompt`: `set carry-prompt on|off`:
      may a human-typed prompt carry? None is OFF.
      The rules only parse it; the pager hook reads it.
    - `subject_shorten_max`: `set subject-shorten-max N`: the
      shortener takes a subject at most N characters OVER the limit and
      declines one further over, so the send refuses as today. None (no
      line) is the default, `DEFAULT_SHORTEN_MAX`. Only this line sets it:
      no seat variable, seat file or flag can raise it.

    - `configured`: NOT a `set` line. The operator's globs from this host's
      config (`operator_globs(_operator.read())`),
      filled by the impure readers (`read_settings`, `load_layers`) on
      every path, so `judge` stays pure. `()` is no operator: `$operator`
      matches nothing.
    - `runner`: NOT a `set` line either. The configured alert runner's local
      part (`alert_runner` in `_settings.SHOWN`, `alert_runner()`),
      filled by the same readers on every path. None is no runner:
      the default urgent list is the operator alone.

    Read them through `operator()`, `urgent_senders()`, `carry_on()`,
    `carry_prompt_on()` and `shorten_max()`, the ONE readers."""

    operator: tuple[str, ...] | None = None
    urgent: tuple[str, ...] | None = None
    subject_shorten: bool | None = None
    carry: bool | None = None
    carry_prompt: bool | None = None
    subject_shorten_max: int | None = None
    configured: tuple[str, ...] = ()
    runner: str | None = None


#: No `set` lines and no operator config: every name at its default.
NO_SETTINGS = Settings()


def operator_globs(op: _operator.Operator) -> tuple[str, ...]:
    """The address globs config makes the operator (design d.2): its
    mailbox on any host (`<mailbox>@*`) and its full address. Pure. Nothing
    configured is `()`, so `$operator` matches nothing: there is no
    default in code."""
    out = []
    if op.mailbox:
        out.append(f"{op.mailbox.casefold()}@*")
    if op.operator:
        out.append(op.operator.casefold())
    return tuple(dict.fromkeys(out))


def operator_aliases(op: _operator.Operator) -> frozenset[str]:
    """This host's alias for `$operator` (design d.3, Q2): the operator's
    MAILBOX, case-folded, when it is an identifier and not a built-in
    variable or a setting name. A host whose `operator.mailbox` is `alice`
    gets `$alice` (and `set alice`); none else does. An address alone gives
    no alias: its local part is not a box here. A mailbox named
    after a setting (`carry`) gets none either: `set carry off` must stay
    the switch, never quietly become `set operator off`. Pure."""
    name = (op.mailbox or "").casefold()
    if (name and _VARIABLE.fullmatch(f"${name}") and name not in VARIABLES
            and name not in SETTING_NAMES):
        return frozenset({name})
    return frozenset()


#: Why `$operator` in a `set urgent` line stands for nobody here (design
#: d.3): named on that line by `load_layers`, and by `pager rule add` as a
#: note. Config may land after the line, so it is not refused.
OPERATOR_EMPTY = (f"$operator is empty: no operator is configured on this host "
                  f"(set {_operator.OPERATOR_KEY} in {_operator.KEY_FILE})")


def _setting(values: Sequence[str],
             aliases: frozenset[str] = frozenset()) -> tuple[str, tuple[str, ...]]:
    """One `set` line's words after `set`, as (name, globs), or `_Bad`.
    A SWITCH name (`SWITCHES`) takes one word instead, `on` or `off`.

    A value is an ADDRESS glob, as a `from:` glob is: `*` and `?`, `[`
    literal. What it may not be, each named: a condition (`from:x`, the
    rule grammar written in the wrong place); a `<...>` placeholder copied
    from the design text (`<runner>@<host>`: no address holds `<`, so it
    would match nothing, silently); a variable that is not `$operator` in
    `set urgent` (`$me` is a seat's own address, never host-wide; `$urgent`
    and `$operator` in their own line would name themselves); or a partial
    one. ALIASES (this host's alias for `$operator`, `operator_aliases`)
    spell `operator` as a name and `$operator` as a value; both are stored
    as `operator`/`$operator`."""
    if not values:
        raise _Bad(f"'{SET_WORD}' needs a name (want one of: {', '.join(SETTING_NAMES)})")
    name, *globs = values
    if name in aliases:
        name = OPERATOR_VAR
    if name not in SETTING_NAMES:
        raise _Bad(f"unknown setting {name!r} (want one of: {', '.join(SETTING_NAMES)})")
    if name in SWITCHES:
        # A switch is ONE word, `on` or `off`: a per-name
        # branch, not a new grammar. Anything else is named and sets nothing,
        # so a typo can never turn the shortener on.
        if len(globs) != 1 or globs[0] not in SWITCH_VALUES:
            raise _Bad(f"'{SET_WORD} {name}' takes one word, "
                       f"{' or '.join(SWITCH_VALUES)} (got {' '.join(globs) or 'nothing'!r})")
        return name, tuple(globs)
    if name in COUNTS:
        # A count is ONE whole number above 0: digits only, so `-5`,
        # `0`, `1.5`, `ten` and `+5` are each named and set nothing, and the
        # default stands. A typo can never raise the cap past the default.
        if len(globs) != 1 or not _COUNT.match(globs[0]) or int(globs[0]) < 1:
            raise _Bad(f"'{SET_WORD} {name}' takes one whole number above 0 "
                       f"(got {' '.join(globs) or 'nothing'!r}; the default "
                       f"{DEFAULT_SHORTEN_MAX} applies; to never shorten, "
                       f"'{SET_WORD} subject-shorten off')")
        return name, tuple(globs)
    if not globs:
        raise _Bad(f"'{SET_WORD} {name}' needs at least one address glob")
    out: list[str] = []
    for glob in globs:
        out.append(glob)
        if ":" in glob:
            raise _Bad(f"{glob!r} is a condition; a '{SET_WORD}' value is an address glob")
        if "<" in glob or ">" in glob:
            raise _Bad(f"{glob!r}: an address glob holds no '<' or '>' "
                       "(write the host's name where the design writes <host>)")
        whole = _VARIABLE.fullmatch(glob)
        if whole:
            var = OPERATOR_VAR if whole.group(1) in aliases else whole.group(1)
            if var not in VARIABLES:
                raise _Bad(f"unknown variable {glob!r} (known: {_known(aliases)})")
            if var == name:
                raise _Bad(f"'{SET_WORD} {name}' cannot name {glob}: it is the value being set")
            if not (name == "urgent" and var == OPERATOR_VAR):
                raise _Bad(f"{glob} cannot stand in '{SET_WORD} {name}' (only $operator, or "
                           "this host's alias for it, and only in 'set urgent')")
            out[-1] = f"${OPERATOR_VAR}"
            continue
        for m in _VARIABLE.finditer(glob):
            if m.group(1) in VARIABLES or m.group(1) in aliases:
                raise _Bad(f"${m.group(1)} must be the whole glob, not part of {glob!r}")
    return name, tuple(out)


def parse_settings(text: str, source: str, *,
                   aliases: frozenset[str] = frozenset()) -> tuple[Settings, tuple[RuleError, ...]]:
    """The `set` lines of the locked file's TEXT as `(settings, errors)`.

    Every other line (a rule, a `focus` line) is not this parser's and is
    passed over without a word. One line a name: the FIRST good one
    stands, and a later one is named and ignored (as a focus file's second
    mode line is), so a line lower down cannot quietly undo the one above.
    A bad line is named (`source:line: reason: text`) and sets nothing.

    Pure, like `parse`: SOURCE is only carried into each `RuleError`, and
    ALIASES (this host's alias for `$operator`) come from the caller.
    `configured` is NOT read here: the impure readers fill it."""
    found: dict[str, tuple[str, ...]] = {}
    errors: list[RuleError] = []
    for number, _raw, body in _lines(text):
        word, *values = body.split()
        if word != SET_WORD:
            continue
        try:
            name, globs = _setting(values, aliases)
            if name in found:
                raise _Bad(f"one '{SET_WORD} {name}' line only; the first one stands")
        except _Bad as bad:
            errors.append(RuleError(source, number, body, str(bad)))
            continue
        found[name] = globs
    # A switch's one word becomes its bool (the glob TUPLES are
    # what `Settings(**found)` takes, so a switch needs its own conversion),
    # and a count's one word its int.
    fields = {}
    for name, value in found.items():
        if name in SWITCHES:
            fields[SWITCHES[name]] = value[0] == "on"
        elif name in COUNTS:
            fields[COUNTS[name]] = int(value[0])
        else:
            fields[name] = value
    return Settings(**fields), tuple(errors)


def read_settings(locked: Path | None = None) -> Settings:
    """The locked file's settings, read now: `locked_path()` unless LOCKED
    is given, NEVER a path from the environment (D3, 10.28). No file, or a
    file that cannot be read, is `NO_SETTINGS`: every default stands. Bad
    lines are skipped here; `load_layers` is what names them.

    The host's operator config (`_operator.read()`) fills
    `configured` and gives the alias, and its mail settings fill `runner`
    (`alert_runner()`), on EVERY path: with no locked file too,
    which is a common state."""
    return read_settings_named(locked)[0]


def read_settings_named(locked: Path | None = None) -> tuple[Settings, tuple[RuleError, ...]]:
    """`read_settings`, and the bad `set` lines it skipped, each named
    (`source:line: reason: text`), for a reader that must say so out loud:
    `message send` names a bad `set subject-shorten-max` line."""
    path = locked_path() if locked is None else locked
    op = _operator.read()
    text, _, _ = _read(path)
    settings, errors = (NO_SETTINGS, ()) if text is None else \
        parse_settings(text, str(path), aliases=operator_aliases(op))
    return replace(settings, configured=operator_globs(op), runner=alert_runner()), errors


def operator(settings: Settings | None = None) -> tuple[str, ...]:
    """Who the operator is, as address globs: the ONE reader
    that the built-in `carry!`, `$operator`, the urgent list's
    default and the `pager say` pre-check all read. The locked file's
    `set operator` when it has one, else this host's config
    (`Settings.configured`). There is no default in code: nothing
    configured is `()`.

    SETTINGS is what the readers filled (the judgement passes the ones
    `load_layers` read); None reads them now (`read_settings`)."""
    if settings is None:
        settings = read_settings()
    return settings.configured if settings.operator is None else settings.operator


def carry_on(settings: Settings | None = None) -> bool:
    """Is carry on for this host? The ONE reader of `set carry`:
    True unless the locked file says `set carry off`. SETTINGS as
    `operator()` takes it; None reads the locked file now."""
    if settings is None:
        settings = read_settings()
    return settings.carry is not False


def carry_prompt_on(settings: Settings | None = None) -> bool:
    """May a human-typed prompt carry? The ONE reader of `set
    carry-prompt`: False unless the locked file says `set carry-prompt on`.
    SETTINGS as `operator()` takes it; None reads the locked file now."""
    if settings is None:
        settings = read_settings()
    return settings.carry_prompt is True


def shorten_max(settings: Settings | None = None) -> int:
    """How far over the limit, in characters, a subject the shortener may
    take: the ONE reader of `set subject-shorten-max`. The locked
    file's N, else `DEFAULT_SHORTEN_MAX`. Nothing else is read: there is no
    seat variable and no flag, so a seat can never raise it. SETTINGS as
    `operator()` takes it; None reads the locked file now."""
    if settings is None:
        settings = read_settings()
    got = settings.subject_shorten_max
    return DEFAULT_SHORTEN_MAX if got is None else got


def urgent_senders(hosts: Sequence[str] | None = None, *,
                   env: Mapping[str, str] | None = None,
                   settings: Settings | None = None) -> tuple[str, ...]:
    """Who may send `urgent` and be honoured (N3): the ONE reader of the
    list. The locked file's `set urgent` when it has one, with
    `$operator` read from `operator()` (A2); else the
    default (P-7), the operator (`operator()`, empty with none
    configured) and the configured alert runner (`alert_runner` in
    `_settings.SHOWN`, `Settings.runner`) at each of HOSTS,
    this host's names (the judgement passes `Me.domains`, full and short).
    With HOSTS None they are `_voice.host_forms(env)` (short, then full).
    SETTINGS as `operator()` takes it; None reads the locked file now.

    No runner configured (or a bad one, `runner_name`), no runner: the
    default is the operator alone. No hosts, no runner either: the default
    list does not widen to `<runner>@*`, which any host could send as. It
    fails closed."""
    if settings is None:
        settings = read_settings()
    boss = operator(settings)
    if settings.urgent is not None:
        return tuple(dict.fromkeys(
            g for glob in settings.urgent
            for g in (boss if glob == f"${OPERATOR_VAR}" else (glob,))))
    if hosts is None:
        hosts = _voice.host_forms(env)
    name = runner_name(settings.runner)   # again: a Settings handed in is checked too
    runner = (f"{name}@{h.strip().lower()}" for h in hosts if h.strip()) if name else ()
    return tuple(dict.fromkeys((*boss, *runner)))

#: The layer names a verdict carries (design Part 3). A focus layer is
#: named `focus: <mode>` (`focus_name`).
LOCKED, SESSION, SEAT, ENV, BUILT_IN = "locked", "session", "seat", "env", "built-in"

#: Design A8's rename table, as DATA (P-9: `X-Project` and
#: friends join it later, by a later change, not by code). A `<header>:` condition
#: on a MIGRATING name also reads its `X-` spelling; an UNPREFIXED_ONLY
#: name never does.
MIGRATING: tuple[str, ...] = _headers.MIGRATING
UNPREFIXED_ONLY: tuple[str, ...] = _headers.UNPREFIXED_ONLY

#: D3 layer 3, P11: the agent MD's lines, one rule per line.
PAGE_RULES_VAR = "SESSIONMAIL_PAGE_RULES"

#: D3 layer 0, P2: `~/.sessionmail/rules.locked`.
LOCKED_FILE = "rules.locked"
#: The one file of each of the other two file layers (P3: one file a layer).
RULES_FILE = "rules"


# ── focus modes (tasks 3.1-3.3) ───────────────────────────────────

#: The modes that ship, each a named rules layer (P4): only `sleep`
#: (P5), whose one line is `ride *`, so that only a `wake!` gets through.
#: Focus `off` is the normal state and is no mode: it adds no layer.
MODES: Mapping[str, str] = MappingProxyType({"sleep": "ride *\n"})
OFF = "off"

#: Where a mode is set (3.2). A verdict names the layer `focus: <mode>`; the
#: `Focus` names where it came from, for the banner and `pager why`.
FLEET = "fleet"

#: The focus file, beside each rules file (design O-c, a proposal).
FOCUS_FILE = "focus"
#: The word that starts the fleet-wide mode's line in the locked file (P14).
FOCUS_WORD = "focus"

#: `HH:MM`, 24-hour, one or two digits for the hour.
_HHMM = re.compile(r"([01]?[0-9]|2[0-3]):([0-5][0-9])\Z")


def focus_name(mode: str) -> str:
    """The layer name a verdict carries for MODE: `focus: sleep`."""
    return f"{FOCUS_WORD}: {mode}"


@dataclass(frozen=True)
class Focus:
    """A focus mode that is set: its `mode`, its end time `until` (aware,
    or None for "until changed"), `where` it was set (`fleet`, `session`,
    `seat`), and the `source` file and `line` that set it, for the banner
    (D7: `focus: sleep until 07:00`) and for `pager why`."""

    mode: str
    until: datetime | None
    where: str
    source: str
    line: int
    text: str

    @property
    def name(self) -> str:
        return focus_name(self.mode)

    def ended(self, now: datetime | None) -> bool:
        """The same test as `Layer.ended`: at or past `until`."""
        return _ended(self.until, now)

    def layer(self) -> Layer:
        """The mode as a rules layer (P4), for D3 0.5. Its rules name the
        mode as their source (`focus: sleep`), since they are the mode's
        own text, not the focus file's."""
        got, _ = parse(MODES[self.mode], self.name)
        return Layer(self.name, got, focus=self.where, until=self.until)


def _until(text: str, set_at: datetime) -> datetime:
    """An end time: `HH:MM` is the FIRST such local time after SET_AT (when
    the mode was set: the file's mtime), so `until 07:00` set at 23:00 runs
    overnight and one left over from last week has long ended. Anything else
    is an ISO date and time (`2030-06-09T07:00`, `2030-06-09 07:00`), local
    when it names no zone. The sum is done on the local wall clock, so a DST
    change overnight still ends at 07:00 as the clock reads it."""
    hhmm = _HHMM.match(text)
    if hhmm:
        start = _aware(set_at).astimezone().replace(tzinfo=None)
        end = start.replace(hour=int(hhmm.group(1)), minute=int(hhmm.group(2)),
                            second=0, microsecond=0)
        if end <= start:
            end += timedelta(days=1)
        return end.astimezone()
    try:
        return _aware(datetime.fromisoformat(text))
    except ValueError:
        raise _Bad(f"{text!r} after 'until' is not a time "
                   "(want HH:MM, or an ISO date and time)") from None


def _focus_line(body: str, set_at: datetime) -> tuple[str, datetime | None] | None:
    """One mode line, `<mode> [until <time>]`, as (mode, until); None for
    `off`. Lower case, as the verbs are."""
    mode, *rest = body.split()
    if mode == OFF:
        if rest:
            raise _Bad("'off' takes nothing after it: off lasts until it is changed")
        return None
    if mode not in MODES:
        known = ", ".join((*MODES, OFF))
        raise _Bad(f"unknown mode {mode!r} (want one of: {known})")
    if not rest:
        return mode, None
    if rest[0] != "until":
        raise _Bad(f"want 'until <time>' after the mode, not {' '.join(rest)!r}")
    if len(rest) == 1:
        raise _Bad("'until' has no time (want HH:MM, or an ISO date and time)")
    return mode, _until(" ".join(rest[1:]), set_at)


def _lines(text: str):
    """(number, body) for each line of TEXT with something on it: the same
    line rules as `parse` (a BOM dropped, LF ends a line, `#` comments)."""
    if text.startswith("\ufeff"):
        text = text[1:]
    for number, raw in enumerate(text.split("\n"), start=1):
        body = _COMMENT.sub("", raw.removesuffix("\r")).strip()
        if body:
            yield number, raw, body


def _focus_from(entries, source: str, set_at: datetime,
                where: str) -> tuple[Focus | None, tuple[RuleError, ...]]:
    """The mode set by ENTRIES, `(number, body, text)` mode lines in order.
    The FIRST line is the one that counts (a focus holds one mode line,
    O-c); any after it is named and ignored. A bad line sets no mode: it is
    dropped whole, as a half-parsed rules line is, so a typo in the end time
    cannot put a seat to sleep for good."""
    focus: Focus | None = None
    errors: list[RuleError] = []
    first = True
    for number, body, text in entries:
        if not first:
            errors.append(RuleError(source, number, text,
                                    "one mode line only; the first one stands"))
            continue
        first = False
        try:
            if not body:  # a bare `focus` line in the locked file
                raise _Bad(f"'{FOCUS_WORD}' needs a mode (want one of: "
                           f"{', '.join((*MODES, OFF))})")
            got = _focus_line(body, set_at)
        except _Bad as bad:
            errors.append(RuleError(source, number, text, str(bad)))
            continue
        if got is not None:
            focus = Focus(got[0], got[1], where, source, number, text)
    return focus, tuple(errors)


def parse_focus(text: str, source: str, set_at: datetime, *,
                where: str = SESSION) -> tuple[Focus | None, tuple[RuleError, ...]]:
    """A focus file's text as `(focus, errors)` (tasks 3.2, design O-c).

    The file holds one line, `<mode> [until <time>]`: `sleep`, `sleep until
    07:00`, `sleep until 2030-06-09T07:00`, or `off`. An empty file, or
    `off`, is no mode (focus is None). SET_AT is when the mode was set (the
    file's mtime): an `HH:MM` end is the first such time after it. `where`
    is carried into the `Focus` (`session` or `seat`).

    Pure, like `parse`: nothing is opened; `source` is only carried into
    the `Focus` and each `RuleError`. A `set` line is named
    and skipped: it belongs to the locked file only."""
    entries: list[tuple[int, str, str]] = []
    wrong: list[RuleError] = []
    for number, _, body in _lines(text):
        if body.split()[0] == SET_WORD:
            wrong.append(RuleError(source, number, body, LOCKED_ONLY))
        else:
            entries.append((number, body, body))
    focus, bad = _focus_from(entries, source, set_at, where)
    return focus, tuple(sorted((*wrong, *bad), key=lambda e: e.line))


def _split_fleet(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """The locked file's text less its `focus ...` and `set ...` lines
    (blanked, so every other line keeps its number), and the focus lines as
    `(number, mode line, whole line)` for `_focus_from`. `focus` (P14) and
    `set` (A2) are words of the LOCKED file only: in any other rules text
    `focus` is an unknown verb and `set` is named as the locked file's.
    The `set` lines are `parse_settings`'s, read from the whole text."""
    kept = text.removeprefix("\ufeff").split("\n")
    entries: list[tuple[int, str, str]] = []
    for number, _raw, body in _lines(text):
        word, *rest = body.split(None, 1)
        if word == FOCUS_WORD:
            entries.append((number, rest[0] if rest else "", body))
            kept[number - 1] = ""
        elif word == SET_WORD:
            kept[number - 1] = ""
    return "\n".join(kept), entries


def built_in() -> Layer:
    """The built-in layer (D4 step 3), always the last. Its text is ours, so
    it parses clean; a test holds it to that."""
    got, _ = parse(BUILT_IN_TEXT, BUILT_IN)
    return Layer(BUILT_IN, got)


def draft_built_in() -> Layer:
    """The built-in layer a draft is judged with (P-5): `ride *` alone,
    still named `built-in`, so a verdict says which layer decided."""
    got, _ = parse(DRAFT_BUILT_IN_TEXT, BUILT_IN)
    return Layer(BUILT_IN, got)


def account_home() -> Path:
    """This uid's home, from the password database: NOT `$HOME`.

    D3 layer 0 is *"never read from an environment variable, because an
    agent could change that at load"* (10.28), and `$HOME` is
    an environment variable too. So the locked file's root is the one an
    agent's environment cannot move."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def default_locked_path() -> Path:
    """`~/.sessionmail/rules.locked`, where `~` is `account_home()`. Not
    `_seat.home()`: that honours `$SESSIONMAIL_HOME`, which an agent sets."""
    return account_home() / ".sessionmail" / LOCKED_FILE


def locked_path() -> Path:
    """Where the locked file is read from when the caller names none. The
    test suite's conftest points this into its sandbox (the one seam a test
    has, since no variable may); production reads `default_locked_path()`."""
    return default_locked_path()


@dataclass(frozen=True)
class Layers(Sequence[Layer]):
    """The layers `load_layers()` found, in D3 order, and every bad line in
    them: a Sequence of `Layer`, so it goes straight to `judge()`.

    `errors` holds each skipped line as a `RuleError` naming its file (or
    `SESSIONMAIL_PAGE_RULES`) and line number, in layer order, for the
    banner and `pager why` to show (the spec's *A line the parser does not
    know*). A file that could not be read at all is one error on line 0,
    and its layer is left out."""

    layers: tuple[Layer, ...]
    errors: tuple[RuleError, ...] = ()
    #: Every focus mode that is set, in the order that decides (design O-a):
    #: fleet-wide, then session, then seat. Each is also a layer at D3 0.5.
    modes: tuple[Focus, ...] = ()
    #: The locked file's `set` lines and the host's
    #: operator config, the settings `judge` reads `$operator` and
    #: `$urgent` through. No other layer sets any.
    settings: Settings = NO_SETTINGS

    def focus(self, now: datetime | None) -> Focus | None:
        """The mode in effect at NOW: the first set mode whose end NOW has
        not reached, or None (focus off). The one the judgement reads."""
        return next((m for m in self.modes if not m.ended(now)), None)

    def __getitem__(self, index):  # type: ignore[override]
        return self.layers[index]

    def __len__(self) -> int:
        return len(self.layers)


#: The mtime `_read` gives when there is no file (never used as an anchor).
_EPOCH = datetime.fromtimestamp(0, timezone.utc)


def _read(path: Path, what: str = "rules") -> tuple[str | None, datetime, RuleError | None]:
    """PATH's text and its mtime (local, aware: when a mode in it was set),
    or None when there is no file. Bytes that are not UTF-8 become U+FFFD,
    so one bad byte costs a line at most, never the file. The mtime is the
    open file's (`fstat`), so it belongs to the bytes read."""
    try:
        with path.open("rb") as handle:
            data = handle.read()
            stamp = os.fstat(handle.fileno()).st_mtime
        return data.decode("utf-8", "replace"), datetime.fromtimestamp(stamp).astimezone(), None
    except FileNotFoundError:
        return None, _EPOCH, None
    except OSError as exc:
        why = exc.strerror or type(exc).__name__
        return None, _EPOCH, RuleError(str(path), 0, "", f"cannot read the {what} file ({why})")



def _session_name(session: str | None) -> tuple[str | None, RuleError | None]:
    """SESSION as a folder name under `sessions/`, or None. `-` and empty
    mean no session (as `_seat.session_root` reads them). An id that is not
    a plain name (`..`, `a/b`, an absolute path) would read a file outside
    `sessions/`, so it reads nothing and is named."""
    if not session or session == "-":
        return None, None
    if session in (".", "..") or "/" in session or "\\" in session or "\0" in session:
        return None, RuleError(SESSION, 0, session, "not a session id (a folder name)")
    return session, None


def _seat_rules(seat: object, home: Path, name: str = RULES_FILE) -> Path | None:
    """The seat file NAME (`rules`, or `focus`): `<seat.root>/<name>` for a
    `_seat.Seat`, else `<home>/seats/<address>/<name>`, the address folded
    as `_seat.resolve` folds it (the `+` kept, the domain cut)."""
    if seat is None:
        return None
    root = getattr(seat, "root", None)
    if root is not None:
        return Path(root) / name
    folder = _seat.fold_address(str(seat))
    return home / "seats" / folder / name if folder else None


def load_layers(seat: object, session: str | None,
                env: Mapping[str, str] | None = None, *,
                home: Path | None = None, locked: Path | None = None) -> Layers:
    """The layers of D3, read now, in order; first match wins down them.

    0. `locked`: `~/.sessionmail/rules.locked` (P2). Its path is NEVER
       taken from ENV (D3, 10.28): not from `$SESSIONMAIL_HOME`, not from
       `$HOME`, and no variable holds its text. `locked` is for tests.
       Its `set` lines are DATA, not rules: they go to
       `Layers.settings`, never into the layer. A `set` line in any other
       layer is named and skipped.
    0.5 `focus: <mode>`: each focus mode that is set (tasks 3.1-3.2, D5),
       as a layer of its mode's rules, in the order that decides (design
       O-a): the fleet-wide mode, a `focus <mode> [until ...]` line in the
       locked file (P14); then the session's `<home>/sessions/<SESSION>/focus`;
       then the seat's `focus` beside its rules. `judge` reads the first one
       still on at the mail's arrival, so an end time is checked then (3.3).
       Focus off (no file, an empty one, or `off`) adds no layer.
    1. `session`: `<session dir>/mail/rules` (`_seat.session_root`: under
       the session dir sessiontools keeps, since session-settings 3.3a; it
       was `<home>/sessions/<SESSION>/rules`).
    2. `seat`: SEAT's folder, `<home>/seats/<address>/rules` (or a
       `_seat.Seat`'s own root).
    3. `env`: `$SESSIONMAIL_PAGE_RULES` from ENV, one rule per line (P11).
    4. `built-in`: D4 step 3, always present, always last.

    HOME is the sessionmail home, `_seat.home(env)` unless given, so the
    session and seat files follow `$SESSIONMAIL_HOME` as every other seat
    file does. ENV defaults to the process environment. A layer with no
    file (or an empty variable) is left out; an empty file is an empty
    layer. A focus file's `HH:MM` end is anchored at its mtime (the fleet
    line's at the locked file's). This reads every file on every call;
    re-reading only on a change is `LiveLayers`.

    The host's operator config (`_operator.read()`, never from ENV)
    fills `Layers.settings.configured` on EVERY path, a host with no locked
    file included (design d.2), and its alias for `$operator` is honoured in
    every layer. A `set urgent $operator` line when no operator is
    configured is kept and named (`OPERATOR_EMPTY`). Its mail settings fill
    `Layers.settings.runner` the same way (`alert_runner()`).
    """
    env = os.environ if env is None else env
    home = _seat.home(dict(env)) if home is None else home
    locked = locked_path() if locked is None else locked
    op = _operator.read()
    aliases = operator_aliases(op)
    layers: list[Layer] = []
    errors: list[RuleError] = []
    modes: list[Focus] = []

    def add(name: str, text: str | None, source: str) -> None:
        if text is None:
            return
        got, bad = parse(text, source, aliases=aliases)
        layers.append(Layer(name, got))
        errors.extend(bad)

    def add_file(name: str, path: Path | None) -> None:
        if path is None:
            return
        text, _, err = _read(path)
        if err is not None:
            errors.append(err)
        add(name, text, str(path))

    def add_focus(where: str, path: Path | None) -> None:
        if path is None:
            return
        text, set_at, err = _read(path, FOCUS_FILE)
        if err is not None:
            errors.append(err)
        if text is None:
            return
        focus, bad = parse_focus(text, str(path), set_at, where=where)
        errors.extend(bad)
        if focus is not None:
            modes.append(focus)

    # 0: the locked file, less its fleet-wide `focus` line (P14).
    text, set_at, err = _read(locked)
    if err is not None:
        errors.append(err)
    settings = NO_SETTINGS
    if text is not None:
        rules_text, entries = _split_fleet(text)
        got, bad = parse(rules_text, str(locked), aliases=aliases)
        layers.append(Layer(LOCKED, got))
        fleet, fleet_bad = _focus_from(entries, str(locked), set_at, FLEET)
        settings, set_bad = parse_settings(text, str(locked), aliases=aliases)
        empty = _operator_empty(text, str(locked), aliases, settings, operator_globs(op))
        errors.extend(sorted((*bad, *fleet_bad, *set_bad, *empty), key=lambda e: e.line))
        if fleet is not None:
            modes.append(fleet)

    # 0.5: the focus modes, fleet-wide, session, seat (O-a).
    sid, err = _session_name(session)
    if err is not None:
        errors.append(err)
    session_dir = _seat.session_root(sid, env) if sid else None
    add_focus(SESSION, session_dir / FOCUS_FILE if session_dir else None)
    add_focus(SEAT, _seat_rules(seat, home, FOCUS_FILE))
    layers.extend(mode.layer() for mode in modes)

    # 1-4.
    add_file(SESSION, session_dir / RULES_FILE if session_dir else None)
    add_file(SEAT, _seat_rules(seat, home))
    page_rules = env.get(PAGE_RULES_VAR) or ""
    add(ENV, page_rules if page_rules.strip() else None, PAGE_RULES_VAR)
    layers.append(built_in())
    return Layers(tuple(layers), tuple(errors), tuple(modes),
                  replace(settings, configured=operator_globs(op), runner=alert_runner()))


def _operator_empty(text: str, source: str, aliases: frozenset[str], settings: Settings,
                    configured: tuple[str, ...]) -> tuple[RuleError, ...]:
    """Each good `set urgent` line of the locked file's TEXT that names
    `$operator` (or the alias) while the operator is nobody: no `set
    operator` and nothing configured. Named, not refused (design d.3)."""
    if settings.operator is not None or configured:
        return ()
    for number, _raw, body in _lines(text):
        word, *values = body.split()
        if word != SET_WORD:
            continue
        try:
            name, globs = _setting(values, aliases)
        except _Bad:
            continue
        if name == "urgent":  # the FIRST good one stands (`parse_settings`)
            if f"${OPERATOR_VAR}" in globs:
                return (RuleError(source, number, body, OPERATOR_EMPTY),)
            return ()
    return ()


# ── the live reader (A6) ─────────────────────────────────────────────────────

def layer_files(seat: object, session: str | None,
                env: Mapping[str, str] | None = None, *,
                home: Path | None = None, locked: Path | None = None) -> tuple[Path, ...]:
    """Every file `load_layers` reads for SEAT and SESSION, in its order: the
    locked file, the session's and the seat's `focus`, the session's and the
    seat's `rules`. The paths are the loader's own (same HOME, same session
    check, same seat folder), so what is watched is what is read."""
    env = os.environ if env is None else env
    home = _seat.home(dict(env)) if home is None else home
    locked = locked_path() if locked is None else locked
    sid, _ = _session_name(session)
    session_dir = _seat.session_root(sid, env) if sid else None
    files = (locked,
             session_dir / FOCUS_FILE if session_dir else None,
             _seat_rules(seat, home, FOCUS_FILE),
             session_dir / RULES_FILE if session_dir else None,
             _seat_rules(seat, home))
    return tuple(f for f in files if f is not None)


def _stamp(path: Path) -> tuple | None:
    """What says PATH changed: device, inode, size, mtime and ctime (ns).
    None when there is no file, so a file that appears or goes is a change;
    an error that is not "no file" is its own stamp, so it is retried when
    it clears."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("error", exc.errno)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class LiveLayers:
    """The layers a listener judges by, re-read when a file CHANGES (A6,
    N5: *"re-read at the next mail after a change"*).

    `current()` stats every file `load_layers` reads (`layer_files`, and
    the operator's config, `_operator.files`, and the mail tool's,
    for the alert runner) and,
    when any stamp DIFFERS from the one taken before the last read
    (different, not newer: size, mtime, ctime or the inode), reads the
    layers again. An edit therefore applies from the next arrival, with no
    re-arm. The stat comes BEFORE the read, so a write that lands between
    the two is seen as a change at the next call, never lost.

    All the files are read again together (one `load_layers`), which gives
    exactly what re-reading only the changed one would: an unchanged file
    parses to the same rules. `$SESSIONMAIL_PAGE_RULES` is this process's
    environment and cannot change under it. `loads` counts the reads."""

    def __init__(self, seat: object, session: str | None,
                 env: Mapping[str, str] | None = None, *,
                 home: Path | None = None, locked: Path | None = None) -> None:
        self._seat = seat
        self._session = session
        self._env = os.environ if env is None else env
        self._home = home
        self._locked = locked
        self._stamps: tuple | None = None
        self._layers: Layers | None = None
        self.loads = 0

    def files(self) -> tuple[Path, ...]:
        """`layer_files`, the operator's config files (`_operator.files`) and the
        mail tool's (`_operator.files(MAIL_TOOL)`):
        `$operator`, its alias and the alert runner are read with the
        layers, so a change to who the operator or the runner is applies
        from the next arrival too."""
        return (layer_files(self._seat, self._session, self._env,
                            home=self._home, locked=self._locked)
                + _operator.files() + _operator.files(MAIL_TOOL))

    def current(self) -> Layers:
        """The layers as the files are NOW: the last read if no file changed."""
        stamps = tuple((f, _stamp(f)) for f in self.files())
        if self._layers is None or stamps != self._stamps:
            self._stamps = stamps
            self._layers = load_layers(self._seat, self._session, self._env,
                                       home=self._home, locked=self._locked)
            self.loads += 1
        return self._layers

    @property
    def last(self) -> Layers | None:
        """The layers the last `current()` returned, with NO stat and no
        read: what a verdict just made was judged by (`pager why` records
        its bad lines). None before the first `current()`."""
        return self._layers
