"""How a recorded verdict is said, and the one rule for a Message-ID.

Moved out of `pager.py` (openspec/changes/message-receipt) so that `message` can say a
verdict the way `pager why` says
it - one wording for one fact - without importing the pager. The pager
re-exports every name here; `pager why` prints byte for byte what it
printed before the move (`tests/test_seat_readers.py` pins it).

Nothing here imports the pager, at module level or later.
"""

from __future__ import annotations

#: The layer a verdict names for a confirmation mail: it was
#: never judged by the rules, so no rules layer decided it.
NOTE_LAYER = "pager note"
#: The layer a verdict names for a mail that could not be judged (unreadable,
#: or the rules raised): it WAKES, since unknown fails toward waking.
UNJUDGED = "unjudged"

#: The layers whose line numbers are a reader's (see `why_text`).
WRITTEN_LAYERS = ("locked", "session", "seat", "env")


def mid(message_id: str) -> str:
    """MESSAGE_ID as it is keyed: `strip()`, then its `<>`, then `strip()`
    again. Case is kept: two ids that differ only in case are two ids."""
    return message_id.strip().strip("<>").strip()


def why_text(record: dict) -> str:
    """One recorded verdict as `pager why` says it (D7, specs/pager "Why a
    mail did what it did"): the action, the rule and its layer, or the
    built-in default that did; then who judged it, and when."""
    from . import rules
    action, rule, layer, line = (record.get("action"), record.get("rule"),
                                 record.get("layer"), record.get("line"))
    if record.get("carry"):
        action = "carry"                # a carry is a wake that carries
    # A line number means something only in a layer someone wrote: a file,
    # or SESSIONMAIL_PAGE_RULES. The built-in text and a mode's are ours.
    where = f", line {line}" if line and layer in WRITTEN_LAYERS else ""
    if layer == NOTE_LAYER:
        text = (f"{action} - the pager's own confirmation (Pager-Note): the rules never "
                "saw it, and it never pages")
    elif layer == UNJUDGED:
        text = f"{action} - it could not be judged, so it woke: an unknown wakes"
    elif rule is None:
        text = f"{action} - no rule matched ({layer})"
    elif layer == rules.BUILT_IN:
        text = f"{action} by `{rule}` (the built-in default)"
    else:
        text = f"{action} by `{rule}` ({layer}{where})"
    if record.get("promoted"):
        text = f"wake, promoted from {text} - that listener had no ride-along to carry it"
    if record.get("at_once"):
        text += " - an honoured urgent, at once"
    return f"{text}; judged by {record.get('by') or '?'} at {record.get('stamp') or '?'}"
