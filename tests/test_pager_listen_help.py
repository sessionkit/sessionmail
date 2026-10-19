"""`pager listen --help` is design D10's page (message-subscriptions 4.9).

Agents read `pager listen --help | head -20`, so what a caller must know sits
in those twenty lines: what it does, which box, how to hear another box,
when it pages, and the two flags. The internals (the queue path, the
hardlinks, the stamps, the reminder window) moved to the README, and from
there (SM-755) to docs/pager-listen-internals.md, which is where a person
reads how it works. D10 is a draft in
`openspec/changes/message-subscriptions/design.md`; its lines are pinned here
rather than read from there, because an archived change moves that file.
"""

from __future__ import annotations

import re
from pathlib import Path

from click.testing import CliRunner

from sessionmail.pager import cli

FOLD = 20
INTERNALS_DOC = Path(__file__).resolve().parent.parent / "docs" / "pager-listen-internals.md"

#: D10's lines, each as it reads there (whitespace aside). Every one of them
#: is on the page; the ones up to Options are inside the fold.
D10_IN_THE_FOLD = [
    "Usage: pager listen [--quiet] [--list]",
    "Wait until mail arrives in your box, print the page, and exit.",
    "Run it again to keep listening.",
    "Your box is this session's. With no session (a human at a shell),",
    "it's $MAIL. Your box is created when your session starts, and the",
    "banner says whether mail reaches it. To hear a project or store",
    "box too, subscribe to it: message subscription add BOX.",
    "It pages at once if you're idle. If you're busy, only when a mail",
    "has waited 60 s without riding along on a tool call.",
    "Options:",
]
D10_OPTIONS = {
    "--quiet": "exit code only",
    "--list / --no-list": "one line per message: box, sender, subject",
}
D10_BELOW_THE_FOLD = [
    "Environment:",
    "SESSIONMAIL_IDLE_SECONDS (1) · SESSIONMAIL_PAGE_AGE_SECONDS (60)",
    "SESSIONMAIL_REMIND_MINUTES (60) · SESSIONMAIL_PAGE_LIST",
    "SESSIONMAIL_SUBSCRIBE: boxes the launcher subscribes it to at birth",
    "MAIL: your box when there is no session",
    # pager-genie 3.8 (D10 of THAT change, O17 RULED) adds exit 3: a second
    # listener in one session refuses. The page says so.
    # SM-726 R2: exit 0 also when the pager monitor already hears for this
    # session (EX_MONITOR_HEARS); 3 stays for a hand-armed listener.
    "Exit: 0 paged · 1 usage · 2 could not look · 3 already listening",
    "0 too when the pager monitor already hears for this session",
]

#: What moved to docs/pager-listen-internals.md: none of it is on the page any more.
INTERNALS = ["ride/new", "hardlink", "stamp", "reminder window", "claims nothing"]


def page() -> list[str]:
    result = CliRunner().invoke(cli, ["listen", "--help"], prog_name="pager")
    assert result.exit_code == 0, result.output
    lines = [line.rstrip() for line in result.output.splitlines()]
    assert lines, "the help printed nothing"
    return lines


def test_the_critical_lines_are_in_the_first_twenty():
    head = [line.strip() for line in page()[:FOLD]]
    for want in D10_IN_THE_FOLD:
        assert want in head, (want, head)


def test_both_flags_are_in_the_fold_as_d10_words_them():
    head = page()[:FOLD]
    for flag, words in D10_OPTIONS.items():
        rows = [line for line in head if line.strip().startswith(flag)]
        assert rows, (flag, head)
        assert rows[0].split(flag, 1)[1].strip() == words, rows


def test_the_environment_and_exit_follow_in_d10s_order():
    lines = [line.strip() for line in page()]
    at = [lines.index(want) for want in D10_BELOW_THE_FOLD]
    assert at == sorted(at), list(zip(D10_BELOW_THE_FOLD, at))
    assert at[0] > lines.index("Options:")


def test_the_sections_sit_at_the_margin_like_options():
    """D10 prints Environment and Exit as headings, flush left, as click
    prints Options - not as indented prose under the options."""
    lines = page()
    for heading in ("Options:", "Environment:"):
        assert heading in lines, (heading, lines)
    assert any(line.startswith("Exit: ") for line in lines), lines


def test_it_says_how_to_stop():
    """D10's own rule for the page: "when it pages, what it prints, and
    how to stop"."""
    head = " ".join(page()[:FOLD])
    assert "pager stop" in head, head


def test_the_internals_are_gone_from_the_help():
    text = "\n".join(page()).lower()
    for internal in INTERNALS:
        assert internal not in text, internal


def test_the_help_says_nothing_of_hears():
    """message-subscriptions 4.8 retired SESSIONMAIL_HEARS (design D9)."""
    assert "HEARS" not in "\n".join(page())


def test_the_page_fits_the_width_and_the_cap():
    lines = page()
    assert len(lines) <= 40, len(lines)
    assert max(len(line) for line in lines) <= 78, lines


def test_the_internals_are_in_their_doc():
    # SM-755: the README's "How `pager listen` works" section is now its own
    # file, docs/pager-listen-internals.md; the whole file is the section.
    text = INTERNALS_DOC.read_text()
    m = re.search(r"^# How `pager listen` works\n(.*)", text, re.S | re.M)
    assert m, "no 'How `pager listen` works' heading in docs/pager-listen-internals.md"
    section = m.group(1)
    for internal in ("<session>/ride/new/", "hardlink", "<session>/stamps/listening",
                     "reminder window", "SESSIONMAIL_REMIND_MINUTES",
                     "pager wake", "claims nothing", "SESSIONMAIL_IDLE_SECONDS",
                     "SESSIONMAIL_PAGE_AGE_SECONDS", "SESSIONMAIL_SUBSCRIBE",
                     "doorbell", "$MAIL"):
        assert internal in section, internal
    assert "HEARS" not in section
