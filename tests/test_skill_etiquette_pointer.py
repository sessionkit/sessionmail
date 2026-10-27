"""SM-979: each of the three skills points at the family's ONE etiquette card.

The kit's skill holds the card, "Several agents in one seat". Every other
family skill carries one pointer line to it, word for word, after the
skill's lead paragraph, and keeps no copy of the card. The text is
SK-230's design.md, the line that superseded the long one (no URL, so it
fits the skills' token folds: the installed skill's name finds it).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SKILLS = ["skills/memory/SKILL.md", "skills/message/SKILL.md", "skills/pager/SKILL.md"]

#: The pointer line, byte for byte. Changing it is the kit's call, not ours.
POINTER = "Several agents in one seat? See the sessionkit skill's card, \"Several agents in one seat\"."

#: The card's six rules, as the kit words them. None of them belongs here.
CARD_RULES = [
    "Only the holder speaks",
    "The others speak when named",
    "A different voice",
    "never `--now`",
    "Reply to all of them",
    "needs the user",
]


def paragraphs_after_h1(rel: str) -> list[str]:
    """The body after the H1, split on blank lines."""
    text = (ROOT / rel).read_text()
    body = text.split("\n# ", 1)[1].split("\n", 1)[1]
    return [p.strip("\n") for p in re.split(r"\n[ \t]*\n", body) if p.strip()]


def test_the_pointer_is_one_short_line_with_no_url():
    assert "\n" not in POINTER
    assert "://" not in POINTER and "KIT_SKILL_URL" not in POINTER


@pytest.mark.parametrize("rel", SKILLS)
def test_the_skill_carries_the_pointer_once_as_its_own_paragraph(rel):
    text = (ROOT / rel).read_text()
    assert text.count(POINTER) == 1, f"{rel}: the pointer line is not there exactly once"
    assert f"\n\n{POINTER}\n\n" in text, f"{rel}: the pointer line is not its own paragraph"


@pytest.mark.parametrize("rel", SKILLS)
def test_the_pointer_follows_the_lead_paragraph(rel):
    """Directly after the lead paragraph. A lead that ends in a colon
    introduces the list under it, so the pointer follows that list."""
    paras = paragraphs_after_h1(rel)
    lead = 1
    if paras[0].rstrip().endswith(":"):
        lead = 2
    assert paras[0].startswith("`" + rel.split("/")[1] + " --help`"), paras[0]
    assert paras[lead] == POINTER, f"{rel}: paragraph {lead} is {paras[lead][:60]!r}"


@pytest.mark.parametrize("path", sorted(str(p.relative_to(ROOT)) for p in (ROOT / "skills").rglob("*.md")))
def test_no_skill_file_copies_the_card(path):
    text = " ".join((ROOT / path).read_text().split())
    copied = [rule for rule in CARD_RULES if rule.lower() in text.lower()]
    assert not copied, f"{path} restates the card: {copied}"


def test_the_send_bullet_does_not_say_every_mail_is_signed():
    """The card: on a kit install mail is unsigned by default. `send` signs
    only when the session holds a key (`_sender.keyed`), so the message
    skill's `send` bullet must not call every send signed."""
    text = (ROOT / "skills/message/SKILL.md").read_text()
    body = text.split("\n# ", 1)[1]
    send = body.split("\n- `send`", 1)[1].split("\n- `", 1)[0]
    assert "signed" not in " ".join(send.split()), send
