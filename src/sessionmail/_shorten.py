"""The subject shortener: what an over-limit or non-ASCII subject
becomes, or that it declines. PURE: no environment, no file, no clock.

Whether it runs at all is the door's (`_send.shorten_on`): the tool's
default is OFF, the host's locked file turns it on, a seat or one mail turns
it off. With it off, `message send` refuses as it always did. The contract is
openspec/changes/subject-shorten.

A shortener that mechanically shortens a subject that is too long; the tool
then replies, saying what the subject was shortened to and why.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from ._send import counted      # ONE length rule: the door's (eom-not-counted)
from ._sender import collapse   # ONE white-space rule: the verifier's

#: Under this limit there is no room to shorten into. It was
#: one character plus "..." after a counted " EOM"; since eom-not-counted the
#: marker costs nothing and 4 would do, but the floor stays 8, unchanged.
MIN_LIMIT = 8
#: The model hook is asked only when the subject is MORE than this many
#: characters over. A later change makes it configurable.
MODEL_OVERAGE = 10
EOM = " EOM"
ELLIPSIS = "..."

#: Filler words, dropped from the END one at a time. Negations, numbers, IDs and is/are
#: are deliberately absent.
FILLER = frozenset(("the", "a", "an", "that", "just", "really", "very", "actually",
                    "basically", "simply", "quite", "currently", "please"))

#: What NFKD does not fold. Every other Unicode space
#: becomes a space by `str.isspace()`, below.
TABLE = {
    "—": "-", "–": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "…": "...",
    "→": "->", "⇒": "=>", "←": "<-",
    "≤": "<=", "≥": ">=", "≠": "!=",
    "×": "x", "•": "*", "·": ".",
}

#: The small-model seam (design section 8): `(subject, room) -> str | None`.
#: This ships it with NO caller; the model and its key come later.
Suggest = Callable[[str, int], "str | None"]


@dataclass(frozen=True)
class Fit:
    """What `shorten` decided. `subject` is what is sent; `original` is
    exactly what was typed. `declined` non-empty means NOT shortened, and
    why: `subject == original`, and the door refuses as today."""

    subject: str
    original: str
    over: bool = False                              # the original was over the limit
    folded: tuple[tuple[str, str], ...] = ()        # ("—", "-"), ...
    unfoldable: tuple[str, ...] = ()                # characters with no ASCII form, dropped
    collapsed: bool = False                         # white space collapsed while over
    dropped: tuple[str, ...] = ()                   # filler words, in the order dropped
    cut: int = 0                                    # chars removed by the word-boundary trim
    hard_cut: bool = False                          # one word was longer than the room
    hard_word: int = 0                              # ... and its length
    suggested: bool = False                         # the model hook's answer was taken
    declined: str = ""

    @property
    def changed(self) -> bool:
        return self.subject != self.original

    @property
    def shortened(self) -> bool:
        """Changed, and not by a fold alone: the original was over the limit,
        or anything past the fold happened. A fitting non-ASCII subject that
        a fold lengthens past the limit is then cut, and a cut that reads as
        a fold is a misstatement. The report's verb and the
        counter's reason both read this ONE rule."""
        cut = self.dropped or self.cut or self.hard_cut or self.suggested or self.collapsed
        return self.changed and bool(self.over or cut)

    def report(self) -> str:
        """The ONE notice line, or "" when nothing changed."""
        if not self.changed:
            return ""
        why = []
        if self.folded:
            why.append("folded " + ", ".join(f'"{a}"->"{b}"' for a, b in self.folded))
        if self.unfoldable:
            why.append("dropped " + ", ".join(f'"{c}"' for c in self.unfoldable)
                       + " (no ASCII form)")
        if self.collapsed:
            why.append("collapsed white space")
        if self.dropped:
            why.append("dropped " + ", ".join(f'"{w}"' for w in self.dropped))
        if self.suggested:
            why.append("suggested by the model hook")
        if self.hard_cut:
            why.append(f"hard cut: one {self.hard_word}-char word")
        elif self.cut:
            why.append(f"cut {self.cut} chars at a word boundary")
        verb = "shortened" if self.shortened else "folded"
        return (f'message send: {verb} {len(self.original)}->{len(self.subject)}: '
                f'"{self.subject}" ({"; ".join(why)}); original in X-Original-Subject')


def _fold(text: str) -> tuple[str, list[tuple[str, str]], list[str], int, int]:
    """(folded text with the unfoldable REMOVED, folds, unfoldable, letters,
    dropped letters). Letters are counted after the table and NFKD, so a CJK
    character is a letter and an emoji is not."""
    out, folds, gone = [], [], []
    letters = dropped_letters = 0
    for c in text:
        if c.isascii():
            rep = c
        elif c in TABLE:
            rep = TABLE[c]
        elif c.isspace():
            rep = " "
        else:
            n = "".join(x for x in unicodedata.normalize("NFKD", c)
                        if not unicodedata.combining(x))
            rep = n if n and n.isascii() else None
        if rep is None:
            gone.append(c)
            if c.isalpha():
                letters += 1
                dropped_letters += 1
            continue
        if rep != c and (c, rep) not in folds:
            folds.append((c, rep))
        letters += sum(ch.isalpha() for ch in rep)
        out.append(rep)
    return "".join(out), folds, gone, letters, dropped_letters


def _drop_fillers(text: str, room: int) -> tuple[str, list[str]]:
    """From the END, one at a time, never the first word, until it fits."""
    words, dropped = text.split(" "), []
    i = len(words) - 1
    while len(" ".join(words)) > room and i >= 1:
        w = words[i]
        if w.isascii() and w.isalpha() and w.lower() in FILLER:
            dropped.append(w)
            del words[i]
        i -= 1
    return " ".join(words), dropped


def _trim(text: str, room: int) -> tuple[str, bool]:
    """(the longest run of whole words, stripped of trailing `,;:-`, plus
    "..." within ROOM; or a hard cut when no whole word fits). Never empty."""
    words = text.split(" ")
    for k in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:k]).rstrip(",;:-")
        if prefix.strip() and len(prefix) + len(ELLIPSIS) <= room:
            return prefix + ELLIPSIS, False
    return text[:room - len(ELLIPSIS)] + ELLIPSIS, True


def _accept(answer: object, room: int) -> str | None:
    """A model's answer is taken only if it is ASCII, one line, fits, and says
    something (design section 8); anything else and the mechanical trim runs."""
    if not isinstance(answer, str):
        return None
    if not answer.isascii() or "\n" in answer or "\r" in answer or len(answer) > room:
        return None
    return answer if any(c.isalnum() for c in answer) else None


def cap_measure(fit: Fit, limit: int) -> int:
    """The ONE number the cap (`set subject-shorten-max`) is held
    to: the OVERAGE, `counted(original) - limit`, so a trailing " EOM" is not
    counted. It is NOT the characters removed: a trim adds "..." and stops
    at a word, so a subject 10 over can lose ~10 + 3 + a partial word. The
    report and the counter keep the TRUE number removed. Should the literal
    reading be ruled, it is this function, `len(fit.original) -
    len(fit.subject)`, and the word "over" in `shorten`'s decline."""
    return counted(fit.original) - limit


def shorten(subject: str, limit: int, *, suggest: Suggest | None = None,
            cap: int | None = None) -> Fit:
    """Fit SUBJECT to LIMIT and ASCII, changing as little as it can; or decline.

    The entry condition: only a subject the rule
    would refuse - over the limit, or not ASCII - is touched. A fitting ASCII
    subject comes back as it is, double spaces and all.

    A trailing " EOM" is not counted against LIMIT (eom-not-counted, the
    rule `_send.counted` reads): it is held aside first, and LIMIT is the
    room for the rest.

    CAP (the host's `set subject-shorten-max`; None is no cap): a
    subject more than CAP over (`cap_measure`) is declined, so the door
    refuses as today and the sender rewrites. A subject that FITS and is
    only folded is never capped, whatever the fold does."""
    fit = _fit(subject, limit, suggest)
    if cap is None or fit.declined or not fit.changed or not fit.over:
        return fit
    k = cap_measure(fit, limit)
    if k > cap:
        return Fit(subject, subject, over=True,
                   declined=f"{k} over; the cap is {cap}: rewrite it, or front-load the point")
    return fit


def _fit(subject: str, limit: int, suggest: Suggest | None) -> Fit:
    """`shorten` with no cap: the original steps, as they were."""
    body, tail = (subject[:-len(EOM)], EOM) if subject.endswith(EOM) else (subject, "")
    over = len(body) > limit
    if subject.isascii() and not over:
        return Fit(subject, subject)
    if limit < MIN_LIMIT:
        return Fit(subject, subject, over=over,
                   declined=f"the limit {limit} leaves no room to shorten (under {MIN_LIMIT})")

    # The trailing " EOM" was held aside BEFORE anything, the gutted-subject
    # guard included, so `修复 EOM` can never go out as a bare `EOM` (3.6).
    room = limit

    # 3.1 fold; the guard (step 4); a drop tidies its own gap (step 5).
    text, folds, gone, letters, dropped_letters = _fold(body)
    if gone:
        if 2 * dropped_letters > letters:
            return Fit(subject, subject, over=over,
                       declined=f"folding would drop {dropped_letters} of {letters} letters")
        text = collapse(text)
        if not any(c.isalnum() for c in text):
            return Fit(subject, subject, over=over,
                       declined="folding would leave nothing alphanumeric")

    found = dict(over=over, folded=tuple(folds), unfoldable=tuple(gone))
    if len(text) <= room:
        return Fit(text + tail, subject, **found)

    # 3.2 collapse, only while still over.
    squeezed = collapse(text)
    found["collapsed"] = squeezed != text and not gone
    text = squeezed
    if len(text) <= room:
        return Fit(text + tail, subject, **found)

    # 3.3 fillers from the end.
    text, dropped = _drop_fillers(text, room)
    found["dropped"] = tuple(dropped)
    if len(text) <= room:
        return Fit(text + tail, subject, **found)

    # 3.4 the model seam: no caller in this card.
    if suggest is not None and len(text) - room > MODEL_OVERAGE:
        answer = _accept(suggest(text, room), room)
        if answer is not None:
            return Fit(answer + tail, subject, suggested=True, **found)

    # 3.5 trim at a word boundary with "...", or a hard cut.
    trimmed, hard = _trim(text, room)
    if hard:
        # The word the cut lands IN, not the first word: a punctuation-only
        # first word ("- ") strips to nothing and the cut falls in the next
        # one.
        at = room - len(ELLIPSIS)
        start, end = text.rfind(" ", 0, at) + 1, text.find(" ", at)
        return Fit(trimmed + tail, subject, hard_cut=True,
                   hard_word=len(text[start:end if end >= 0 else len(text)]), **found)
    return Fit(trimmed + tail, subject, cut=len(text) - (len(trimmed) - len(ELLIPSIS)), **found)
