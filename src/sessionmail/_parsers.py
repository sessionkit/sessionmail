"""The parsers `pager-filters` consumes: parse here, judge there.

openspec/changes/pager-genie, tasks.md 3.13 and 3.21 (design: "Order:
the parse here, the judgement in the rules"), D14 ("Only hey wakes") and
"Trust" (the calls/trust interface agreed with the rules).

Three pure functions, each reading its header through 3.22's reader:

  - `priority(msg)`: the `Priority:` level (3.13; N1). The FIRST header
    only, a second ignored and recorded; the value trimmed and compared
    case-insensitively against `non-urgent`, `normal`, `urgent`; a missing
    header or any other value is `normal`, recorded "not a level".
    `X-Priority` is a mail client's own header and never read.
  - `read_rule(msg)` / `parse_rule(value)`: the `Ears-Rule` entries (3.21;
    N10). Split on `,`, trim each entry, split it on its FIRST `=`; answer,
    per entry and never by glob, "does it call address X" (the key's domain
    case-insensitively, its local part exactly) and "does it call
    everyone" (the entry `everyone=everyone`, as the keeper writes it).
  - `calls(msg)`: the calls feed, a mail -> the addresses it calls and
    `everyone`, and `()` unless 3.1 says voice (*A call on mail that is
    not voice is no call*). Trusted voice always calls; UNVERIFIED voice
    calls only while `_voice.UNVERIFIED_CALLS` is on, and the
    rules clamp each such call to at most `carry`.

What a level or a call DOES (the built-in lines, the urgent list, the
at-once page, the voice default) is the rules' judgement, and nothing here
decides it.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
from typing import Mapping

from . import _voice
from ._headers import read_header
from ._voice import Voice, recognise

# == 3.13: the Priority parse (D11; N1) =======================================

#: RFC 2156's values, and no new levels.
LEVELS: tuple[str, ...] = ("non-urgent", "normal", "urgent")
NORMAL = "normal"


@dataclass(frozen=True)
class Priority:
    """One mail's level, as parsed.

    `level`    `non-urgent`, `normal` or `urgent`: never anything else
    `stated`   True when the first `Priority:` named a level (an explicit
               `normal` is stated; a missing or unknown one is not)
    `value`    the first header's value as sent, None when absent
    `reason`   None when stated; `not a level` when the header is absent
               or its value is none of the three (tasks.md 3.13, design
               "a missing header or an unknown value is `normal`,
               recorded 'not a level'"; `value` None tells them apart)
    `ignored`  the values of every later `Priority:`, in wire order
    """

    level: str
    stated: bool
    value: str | None
    reason: str | None
    ignored: tuple[str, ...] = ()


def priority(msg: Message) -> Priority:
    """MSG's level: the first `Priority:`, trimmed, case-insensitive."""
    read = read_header(msg, "Priority")
    if not read.present:
        return Priority(level=NORMAL, stated=False, value=None, reason="not a level")
    ignored = read.values[1:]
    level = read.value.strip().lower()
    if level in LEVELS:
        return Priority(level=level, stated=True, value=read.value, reason=None,
                        ignored=ignored)
    return Priority(level=NORMAL, stated=False, value=read.value,
                    reason="not a level", ignored=ignored)


# == 3.21: the Ears-Rule parser (D14; N10) ====================================

#: The keeper's rule for a "hey <name>" call, and its entry for "hey
#: everyone" (`everyone=everyone`; ears-mail.py:149-170).
CALL = "call"
EVERYONE = "everyone"


def same_address(a: str, b: str) -> bool:
    """A and B are one address: the local part compared EXACTLY (`+ext`
    kept), the domain case-insensitively and WHOLE (`host` is not
    `host.<suffix>`). No domain on one side is not a match for a
    domain on the other."""
    la, at_a, da = a.rpartition("@") if "@" in a else (a, "", "")
    lb, at_b, db = b.rpartition("@") if "@" in b else (b, "", "")
    return la == lb and bool(at_a) == bool(at_b) and da.lower() == db.lower()


@dataclass(frozen=True)
class Entry:
    """One `key=rule` entry, split on its first `=`. An entry with no `=`
    has rule `""` and calls nobody."""

    key: str
    rule: str

    def calls(self, address: str) -> bool:
        return self.rule == CALL and bool(self.key) and same_address(self.key, address)

    @property
    def everyone(self) -> bool:
        return self.key == EVERYONE and self.rule == EVERYONE


@dataclass(frozen=True)
class Rule:
    """The parsed `Ears-Rule`, and which spelling it was read from.

    `spelling` / `prefixed` are the reader's record (3.22): `X-Ears-Rule`
    and True when only the old form was on the wire; None when absent."""

    entries: tuple[Entry, ...] = ()
    spelling: str | None = None
    prefixed: bool | None = None

    def calls(self, address: str) -> bool:
        """Does any entry call ADDRESS? Per entry, never a glob."""
        return any(e.calls(address) for e in self.entries)

    @property
    def everyone(self) -> bool:
        """Does it call everyone (the entry `everyone=everyone`)?"""
        return any(e.everyone for e in self.entries)

    def called(self) -> tuple[str, ...]:
        """Every key it calls, in wire order, once each; `everyone` for the
        everyone entry."""
        out: list[str] = []
        for e in self.entries:
            name = EVERYONE if e.everyone else (e.key if e.rule == CALL and e.key else None)
            if name is not None and name not in out:
                out.append(name)
        return tuple(out)


def parse_rule(value: str | None) -> Rule:
    """VALUE split on `,`, each entry trimmed, then split on its first `=`
    (key and rule trimmed too). Blank entries are dropped."""
    entries = []
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        key, _, rule = item.partition("=")
        entries.append(Entry(key.strip(), rule.strip()))
    return Rule(entries=tuple(entries))


def read_rule(msg: Message) -> Rule:
    """MSG's `Ears-Rule`, read through 3.22 (so `X-Ears-Rule` too), parsed."""
    read = read_header(msg, "Ears-Rule")
    parsed = parse_rule(read.value)
    return Rule(entries=parsed.entries, spelling=read.spelling, prefixed=read.prefixed)


# == 3.21: the calls feed (agreed with the rules) ==============================


def calls(msg: Message, env: Mapping[str, str] | None = None,
          signed: "_voice.Signed | None" = None, *, voice: Voice | None = None) -> tuple[str, ...]:
    """The addresses MSG calls, and `everyone`, for the rules' `Facts.calls`:
    `()` unless 3.1 says MSG is voice, whatever its `Ears-Rule` says.
    TRUSTED voice calls; UNVERIFIED voice calls only while
    `_voice.UNVERIFIED_CALLS` is on (the one switch; strict is no
    calls), and what such a call may do is clamped by the rules
    (`rules.judge`), never here. VOICE is a recognition already made for
    MSG (`Facts.from_message` makes one); else it is made here, with SIGNED
    as its verdict seam. Keys are returned as written; compare them with
    `same_address`."""
    rec = recognise(msg, env, signed) if voice is None else voice
    if not (rec.voice or (rec.unverified and _voice.UNVERIFIED_CALLS)):
        return ()
    return read_rule(msg).called()
