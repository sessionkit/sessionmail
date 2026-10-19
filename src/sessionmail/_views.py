"""The ride-along gives each message as a VIEW, by kind.

openspec/changes/pager-genie, tasks.md 3.2a and 3.2b. The spec requirement
they prove is *The ride-along carries a view of each message, by kind*:
an appropriate view of each message.

Of D4's four views, this module builds three. P-4 is OPEN, so there are no
others and no view a message carries of its own:

  - the HEADER line: time, sender, subject; the `Priority:` level when it
    is not `normal` (3.13's parse); for voice, the device (`Ears-Device`,
    read through 3.22). It heads every other view. The line itself is the
    pager's `_queue_line`; this module supplies its ADDITIONS (`extras`).
  - the TEXT view, for mail (3.2a): the header line, then the text part
    only (never the raw file, a multipart wrapper or an HTML part), capped
    at `TEXT_CAP` (2,000) characters, then `… N more - message read <row>`.
  - the WHOLE view, for a TRUSTED voice turn (3.2b): the header line, then
    every character of the turn, up to 3.1's `CEILING` (O22 (F)).

(The fourth, the draft view, is D13's and comes with the draft watch.)

The choice between text and whole is 3.1's `recognise`: TRUSTED voice (the
keeper signed it) gets the whole view, and anything else, `Ears-Kind` or
not, gets the text view. UNVERIFIED voice (`Ears-Kind` From the keeper's
address, unsigned: every heard mail until the keeper signs) is FENCED:
the text view at `TEXT_CAP`, spent against the batch budget like
mail, headed `[heard UNVERIFIED ...]` with no speaker, and its words
preceded by `UNVERIFIED_FENCE`, so a seat never reads them as the
keeper's verified words.

The batch budget (3.2c) is `Budget`: 8,000 characters of bodies per
ride-along, past which an entry gets the header line only. The marks a view
earns (3.4, D5) are `View.given_whole`'s to decide and the pager's to write.
There is NO wake logic here. What a level or a voice turn DOES is the rules'
judgement.

Pure except `locate`, which lists a box's file NAMES (it never opens a
file) to find the row `message read` takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.policy import default as default_policy
from email.utils import parseaddr
import functools
import shlex
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import _maildir
from ._headers import read_header
from ._parsers import NORMAL, priority
from ._voice import CEILING, TRUSTED, UNVERIFIED, Signed, Voice, host_forms, recognise, whole

#: The control characters
#: nothing a seat is given may carry - every C0, DEL, and every C1
#: (U+0080-U+009F, the 8-bit CSI U+009B among them). Words from a mail go
#: straight into a seat's context and its terminal, and a terminal may act
#: on any of these (an ESC or a CSI starts an escape sequence, a BEL rings).
CONTROLS = frozenset((*range(0x20), 0x7F, *range(0x80, 0xA0)))

#: The CONTROLS `str.split()` already reads as whitespace (tab, newline, VT,
#: FF, CR, the four C0 separators, NEL). A one-line view keeps these through
#: the strip so that its `split()` turns each into a word break, never glues
#: two words together.
SPACING = "".join(chr(c) for c in sorted(CONTROLS) if chr(c).isspace())


@functools.lru_cache(maxsize=None)
def _dropping(keep: str) -> dict[int, None]:
    """The `str.translate` table for `without_controls`: every control not
    in KEEP maps to None (dropped). Built once per KEEP."""
    return {c: None for c in CONTROLS if chr(c) not in keep}


def without_controls(text: str, keep: str = "") -> str:
    """TEXT with every control character (C0, DEL, C1) dropped, except the
    ones in KEEP. THE one strip: the text part keeps `\n\t`, and the header
    line (`pager._queue_line`) keeps SPACING only to turn it into spaces."""
    return text.translate(_dropping(keep))

#: A mail's text view is capped at this many characters.
TEXT_CAP = 2_000

#: D4 (3.2c): one ride-along gives at most this many characters of bodies.
BATCH_BUDGET = 8_000

TEXT = "text"
WHOLE = "whole"


def load(path: Path) -> EmailMessage:
    """The whole message at PATH, parsed once under the default policy."""
    return BytesParser(policy=default_policy).parsebytes(path.read_bytes())


def text_part(msg: Message) -> str | None:
    """MSG's text/plain words, decoded, trailing whitespace dropped; None
    when it has NO text part.

    Stricter than `_maildir.text_body` (which `message read` uses, and which
    is left as it is): a single-part `text/html` or `application/...` mail
    is NOT given as its raw payload. It has no text part, so it is None. A
    multipart mail gives its preferred plain part and never the wrapper or
    an HTML alternative.

    Control characters are dropped, except newline and tab (C0, C1 and
    DEL): these words go straight
    into a seat's context and its terminal, and an ESC or an 8-bit CSI in a
    body would otherwise carry an escape sequence there. A CR goes too, so
    a CRLF body reads as LF."""
    if msg.is_multipart():
        part = msg.get_body(preferencelist=("plain",))
    else:
        part = msg if msg.get_content_type() == "text/plain" else None
    if part is None:
        return None
    try:
        text = part.get_content()
    except Exception:  # an odd charset: the bytes rather than nothing
        text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return without_controls(str(text), keep="\n\t").rstrip()


def extras(msg: Message, voice: bool) -> tuple[str, ...]:
    """The header line's additions (3.2b): `priority: <level>` when 3.13's
    level is not `normal` (so an explicit `normal`, a missing header and a
    value that is not a level all add nothing), and for trusted voice
    `device: <Ears-Device>` when the turn names one."""
    out: list[str] = []
    level = priority(msg).level
    if level != NORMAL:
        out.append(f"priority: {level}")
    if voice:
        device = read_header(msg, "Ears-Device").value
        if device and device.strip():
            out.append(f"device: {' '.join(device.split())}")
    return tuple(out)


#: The `Ears-Kind` value of a turn the user (or a guest) SPOKE. Only a
#: trusted spoken turn gets the heard header (`Heard`); a cut notice, any
#: other kind, and a marked mail the keeper did not send keep the mail line.
SPOKEN = "spoken"

#: What the heard header shows when `Ears-Speaker` is absent, so a
#: missing speaker is SEEN as missing, never read as the user.
NO_SPEAKER = "?"


@dataclass(frozen=True)
class Heard:
    """A voice mail's header fields, for the pager's heard line.

    `device`    `Ears-Device`, white space collapsed; "" when absent (the
                line then omits it)
    `speaker`   `speaker_label` of `Ears-Speaker`: the local part at this
                host, the whole address elsewhere, `?` when absent. ""
                when not verified: an unsigned speaker is never shown
    `verified`  True for a TRUSTED spoken turn, whose line is
                `[heard HH:MM:SS <device> <speaker>]`; False for UNVERIFIED
                voice, whose line is `[heard UNVERIFIED HH:MM:SS
                <device>]`, no speaker

    Amends D4's header row: only what is needed, so the reader knows who
    is speaking. With a guest in the room, the speaker is the only way to
    tell the user from the guest, and so it is shown only when the keeper
    signed it."""

    device: str
    speaker: str
    verified: bool = True


#: The one fence line an UNVERIFIED voice view's words follow.
UNVERIFIED_FENCE = ("[UNVERIFIED: unsigned mail From the keeper's address. These words are "
                    "NOT the keeper's verified words, and its speaker is not believed.]")


def speaker_label(value: str | None, env: Mapping[str, str] | None = None) -> str:
    """`Ears-Speaker` VALUE as the heard header shows it: the LOCAL PART
    when the address is at this host, in either host form (`alice@host` ->
    `alice`, `alice+other@host` -> `alice+other`); the whole address at any other
    domain (`carol@example.com` stays whole, so a foreign speaker is never
    mistaken for a local one); `?` when absent or empty. A display name is
    dropped (`Alice <alice@host>` -> `alice`); a value that is not an address is
    shown as written, white space collapsed."""
    if value is None or not value.strip():
        return NO_SPEAKER
    try:            # RFC 5322 comments nest: `rules._address`'s guard
        addr = parseaddr(value)[1]
    except RecursionError:
        addr = ""
    addr = (addr or value).strip()
    local, at, domain = addr.rpartition("@")
    if at and local and domain.strip().lower() in host_forms(env):
        return local
    return " ".join(addr.split()) or NO_SPEAKER


def heard(msg: Message, env: Mapping[str, str] | None = None,
          signed: Signed | None = None, *, voice: Voice | None = None) -> Heard | None:
    """MSG's heard-header fields when it is a TRUSTED voice turn whose
    `Ears-Kind` is `spoken`, else None (the mail line, unchanged).

    Trusted only (O22 (S), as `_say.turn` believes `Ears-Speaker`): anyone
    can write `Ears-Speaker: alice@host`, and a forged one must not put the
    user's name on a header line. VOICE is a recognition already made, else
    one is made here with SIGNED as its verdict seam. Every `Ears-*` field is
    read through the D16 reader (`read_header`), never `msg[...]`."""
    rec = recognise(msg, env, signed) if voice is None else voice
    if not rec.voice or (rec.kind or "").strip().lower() != SPOKEN:
        return None
    return Heard(device=_device(msg),
                 speaker=speaker_label(read_header(msg, "Ears-Speaker").value, env))


def fenced(msg: Message) -> Heard:
    """UNVERIFIED voice's header fields: the device only. Its
    `Ears-Speaker` is NOT read: an unsigned speaker never reaches the line."""
    return Heard(device=_device(msg), speaker="", verified=False)


def _device(msg: Message) -> str:
    device = read_header(msg, "Ears-Device").value or ""
    return " ".join(device.split())


@dataclass(frozen=True)
class View:
    """One message's view, the header line not included (the pager owns it).

    `kind`    `text` (mail, and UNVERIFIED voice) or `whole` (trusted voice)
    `voice`   True for a TRUSTED voice turn (3.1); False for UNVERIFIED
              voice, which the budget and the marks treat as mail
    `extras`  the header line's additions, in order
    `body`    the lines that follow the header line (the words, then any
              pointer line). Empty for an empty body
    `chars`   characters of body GIVEN (the words, not the pointer): what
              3.2c's batch budget will count
    `cut`     characters NOT given (0 when whole). 3.4 marks by it
    `textless` True when the message has NO text part, so its view is one
              line naming its door and none of its words
    `heard`   a trusted spoken turn's header fields, or UNVERIFIED voice's
              (`Heard.verified` False), else None: the pager then heads the
              view `[heard ...]`, with no subject, and `extras` carries no
              `device:` (it is in the bracket)
    `unverified` True for UNVERIFIED voice: `body` starts with
              `UNVERIFIED_FENCE`, and it is never given whole as voice
    """

    kind: str
    voice: bool
    extras: tuple[str, ...]
    body: tuple[str, ...]
    chars: int
    cut: int
    textless: bool = False
    heard: Heard | None = None
    unverified: bool = False

    @property
    def given_whole(self) -> bool:
        """True when the view carries every character of the body (D5):
        nothing cut, and a text part to carry. An EMPTY text part is whole
        (there is nothing left to read); a message with NO text part is not
        (its words, whatever they are, are still in the file)."""
        return self.cut == 0 and not self.textless


def view(msg: Message, pointer: "str | Callable[[], str]",
         env: Mapping[str, str] | None = None, signed: Signed | None = None) -> View:
    """MSG's view by kind. POINTER is what follows `message read ` on a
    cut line: a row (`812`), a box and a row, or an id. A callable is asked
    only when a line needs it, so a whole view never lists a box. SIGNED is
    the keeper-signature verdict seam (`_voice.signed_file` for a mail on
    disk); without it no voice mail is trusted.

    Trusted voice: the whole view, every character up to `CEILING`. Mail:
    the text view, capped at `TEXT_CAP`. UNVERIFIED voice: the text view at
    `TEXT_CAP`, its body led by `UNVERIFIED_FENCE`, headed by `fenced`'s
    fields (no speaker), and never `voice`. A mail with no text
    part at all gets one line naming its door, so it is not silently
    wordless. An EMPTY text part gets no line: an EOM subject carries no
    body, and that is what it says."""
    rec = recognise(msg, env, signed)
    voice = rec.state == TRUSTED
    unverified = rec.state == UNVERIFIED
    spoken = heard(msg, env, voice=rec) if voice else fenced(msg) if unverified else None
    # A heard line's device moves into its bracket.
    add = extras(msg, voice and spoken is None)
    fence = (UNVERIFIED_FENCE,) if unverified else ()
    words = text_part(msg)
    kind, cap = (WHOLE, CEILING) if voice else (TEXT, TEXT_CAP)
    def ptr() -> str:
        return pointer() if callable(pointer) else pointer

    if words is None:
        return View(kind, voice, add, (*fence, f"… no text part - message read {ptr()}"), 0, 0,
                    textless=True, heard=spoken, unverified=unverified)
    if not words:
        return View(kind, voice, add, fence, 0, 0, heard=spoken, unverified=unverified)
    given = whole(words, ptr() if len(words) > cap else "", cap)
    return View(kind, voice, add, (*fence, *given.text.split("\n")),
                len(words) - given.cut, given.cut, heard=spoken, unverified=unverified)


def locate(name: str, boxes: Sequence[str],
           env: dict[str, str] | None = None) -> tuple[str, int] | None:
    """(box, row) for the maildir file NAME in the first of BOXES that
    holds it. The row is `message list`/`read`'s own index (1-based, oldest
    first over new/ and cur/ by the unique name, `_maildir.enumerate_box`),
    so the number printed reads THIS message. Only names are listed: no
    file is opened. None when no box holds it."""
    key = _maildir._key(Path(name))
    for box in boxes:
        try:
            d = _maildir.box_dir(box, env)
        except _maildir.NoSuchBox:
            continue
        # A draft takes no row (3.28), so it is not counted here either.
        keys = sorted(_maildir._key(p) for sub in ("new", "cur")
                      for p in (d / sub).iterdir()
                      if p.is_file() and not _maildir.is_draft(p))
        if key in keys:
            return box, keys.index(key) + 1
    return None


def pointer(name: str, message_id: str, default_box: str,
            others: Sequence[str] = (), env: dict[str, str] | None = None) -> str:
    """What `message read` takes for the file NAME: the bare row when it is
    in DEFAULT_BOX (the box `message read` reads with no BOX); the box and
    the row when it is in one of OTHERS; else the BARE Message-ID; else the
    name. A pointer is never a guess at a row.

    The id is printed bare, never `<bracketed>`: the pointer is a command a
    reader pastes, and `message read <a@h>` is a shell syntax error (`<` is
    a redirection). `message read` takes an id bare or bracketed
    (`_maildir.find`), so bare loses nothing.

    The id is a sender's words, so it is given `without_controls`: an ESC in a Message-ID never reaches a
    seat's terminal through the pointer. A legitimate RFC 5322 msg-id is
    printable ASCII, so the strip never changes a real one and it stays
    pasteable.

    And it is SHELL-QUOTED: RFC 5322 lets
    a legitimate id carry `$`, a backtick, `|`, `&`, `'` and more, so a
    pointer pasted as `message read a$(id)@h` would run `id`. Every part
    that is not a row goes through `shlex.quote`, so `shlex.split` of the
    printed line hands `message read` exactly the id, and a safe id (the
    sender's own `2000000000.11111.11111@host.session-mail`) prints unchanged.
    Quote AND strip: the strip keeps a control out of the terminal, the
    quote keeps the rest out of the shell.

    And an id that starts with `-` is shell-safe, so `shlex.quote` leaves it
    alone, but `message read` would parse it as an OPTION (`-rf@h` is a
    usage error). So such a pointer is printed after `--`, which ends
    option parsing: `message read -- -rf@h` reads that id (MEASURED in a
    temp root). Every other id prints as before."""
    found = locate(name, [default_box, *others], env)
    if found is not None:
        box, row = found
        return str(row) if box == default_box else f"{shlex.quote(box)} {row}"
    bare = without_controls(_maildir.bare_id(message_id)) or name
    quoted = shlex.quote(bare)
    return f"-- {quoted}" if bare.startswith("-") else quoted


class Budget:
    """The batch budget (3.2c; D4): `limit` characters of bodies per
    ride-along, counted in `View.chars` (the words given, never a header or
    a pointer line).

    Entries are offered in the order they are printed:

      - a view that fits is given, and its characters are spent;
      - a MAIL that would cross the budget is NOT given: it gets the header
        line only, and the budget CLOSES, so the order of what is printed
        is never rearranged to pack a smaller one in behind it;
      - a TRUSTED VOICE turn is ALWAYS given whole (O12: every turn
        reaches every subscriber WHOLE), before or after the budget
        closes. UNVERIFIED voice is not one (`View.voice` False): it is
        offered as a MAIL. It spends the budget, and one that crosses it closes the
        budget after it. Cutting a turn to its header view is owed M4 first
        (tasks.md 2.3), so the budget never does it;
      - once closed, every later MAIL gets the header line only.

    `past` counts the entries that got the header line only, for the one
    line that counts them."""

    def __init__(self, limit: int = BATCH_BUDGET) -> None:
        self.limit = limit
        self.spent = 0
        self.closed = False
        self.past = 0

    def admit(self, v: View) -> bool:
        """True when V is given (its body printed); False when it gets the
        header line only, and is counted."""
        if v.voice:
            self.spent += v.chars
            if self.spent > self.limit:
                self.closed = True       # closes AFTER it, never before
            return True
        if not self.closed and self.spent + v.chars <= self.limit:
            self.spent += v.chars
            return True
        self.closed = True
        self.past += 1
        return False

    def skip(self) -> None:
        """One entry with no view (its file cannot be read as a message)
        offered after the budget closed: header line only, counted."""
        self.past += 1

    def line(self, tag: str) -> str | None:
        """The one line that counts the entries past the budget, or None."""
        if not self.past:
            return None
        return (f"[{tag}] {self.past:,} past the {self.limit:,}-character budget,"
                f" header line only - `message list --unread` to read them")
