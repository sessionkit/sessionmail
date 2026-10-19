"""The arm instruction - ONE file, read by every teller.

specs/pager, "The arm instruction has one source": the instructions for
running `pager listen` live in one file, because several places say them and
they must be the same, never duplicated. The page, `pager status`, the
help, a CLAUDE.md line and a skill read it from there and NONE restates it.

The file is `arm.txt`, beside this module and INSIDE the package, so an
installed copy reads the same way a checkout does (it ships with the
package data; `tests/test_arm_instruction.py` pins that it is importable
from the installed location, not just findable on disk).

`arm.txt` holds the instruction and nothing else - no header, no comment,
no markup of its own - so a teller that is not Python can `cat` it and get
exactly the words the page says.

A variant per harness MAY be added later (another coding agent, say a
Python one); one file is the whole of it now.
"""

from __future__ import annotations

from importlib.resources import files

#: The one file. Beside this module, inside the package.
FILENAME = "arm.txt"


def path():
    """The instruction's file as a traversable - works from a checkout and
    from an installed wheel. Tellers outside Python are pointed here."""
    return files(__package__).joinpath(FILENAME)


def instruction() -> str:
    """The instruction, one line, no trailing newline.

    Read at every call. The CLI is a fresh process per invocation, so a
    teller that composes its text from this at import time still carries a
    change to the file on the very next run.
    """
    return path().read_text(encoding="utf-8").strip()


def without_rider() -> str:
    """The instruction with its trailing rider dropped: the last clause, when
    the line ends in one in parentheses ("(not needed when the pager monitor
    is listening)").

    A teller that speaks where the rider
    cannot apply - the line that says this seat's monitor is switched off -
    reads this, so the rider is never restated and never contradicted. A
    line with no trailing rider is returned whole.
    """
    text = instruction()
    if text.endswith(")"):
        start = text.rfind(" (")
        if start != -1 and ")" not in text[start + 2:-1]:
            return text[:start].rstrip()
    return text
