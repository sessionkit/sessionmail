"""The pages say which box a bare call reads, and the README's self-test
lists the box it sent to.

specs/message, "A bare listing is the doorbell's box": with no BOX,
`message list`, `read` and `path` read THIS SESSION's box once it exists,
else the seat's box, and say so on stderr when the session has none yet.
That default stays. What was missing was the page saying it: a reader who
sent to a box and then ran a bare `message list` saw their session's box,
not the one they sent to, and nothing on `-h` told them why.

Pinned here:
  - each of the four pages says, in its first 20 lines (what an agent reads
    with `--help | head -20`), which box a bare call reads and that a BOX
    names another;
  - no page still says the old, untrue "BOX defaults to your seat";
  - `message path` does what its page now says (`list` and `read` are
    pinned in test_message_list.py);
  - the README's quick-start self-test makes, sends to and lists ONE box.

Fixtures are `alpha` and `sess-a` (convention 9).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_scaffold import as_an_agent_reads_it

from sessionmail.message import cli

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
HEAD = 20

#: The four pages a bare BOX reaches, and the verb each names a BOX for.
PAGES = {
    "message list": "list another",
    "message read": "read another",
    "message path": "resolve another",
    "message box path": "resolve another",
}

#: What the line must carry, whitespace folded: the default, its fallback,
#: when stderr speaks, and the way out.
SAYS = [
    "With no BOX:",
    "this session's box once it exists",
    "the session-start hook or `pager listen` makes it",
    "else the seat's box",
    "stderr says so if the session has none yet",
]


def folded(text: str) -> str:
    """TEXT with every run of whitespace one space: the page wraps at 76
    columns, and a phrase may cross a line."""
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return {label: as_an_agent_reads_it(label) for label in PAGES}


@pytest.mark.parametrize("label", list(PAGES))
def test_the_page_says_which_box_a_bare_call_reads_in_its_first_20(label, pages):
    head = folded("\n".join(pages[label].splitlines()[:HEAD]))
    missing = [want for want in [*SAYS, f"Name a BOX to {PAGES[label]}"] if want not in head]
    assert not missing, f"`{label} --help | head -{HEAD}` lacks {missing}:\n{pages[label]}"


@pytest.mark.parametrize("label", list(PAGES))
def test_no_page_still_says_box_defaults_to_your_seat(label, pages):
    """It was untrue once a session box exists: the doorbell's box is read."""
    assert "defaults to your seat" not in folded(pages[label])


def test_read_says_it_reads_the_box_a_bare_list_reads(pages):
    """`read` takes the index `list` prints, so its page ties the two."""
    assert "the box a bare `list` reads" in folded(pages["message read"])


# ── `message path` does what its page says ─────────────────────────────────

@pytest.fixture
def root(tmp_path, monkeypatch):
    stores = tmp_path / "mail" / "stores"
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(stores.parent))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    for name in ("alpha", "sess-a"):
        for sub in ("cur", "new", "tmp"):
            (stores / name / sub).mkdir(parents=True, exist_ok=True)
    return stores


@pytest.mark.parametrize("argv", [["path"], ["box", "path"]])
def test_a_bare_path_is_the_session_box_once_it_exists(root, monkeypatch, argv):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    r = CliRunner().invoke(cli, argv)
    assert r.exit_code == 0, r.output
    assert r.output.strip().splitlines()[-1] == str(root / "sess-a")
    assert "instead of this session's" not in r.output


@pytest.mark.parametrize("argv", [["path"], ["box", "path"]])
def test_a_bare_path_is_the_seat_box_and_says_so_while_the_session_has_none(
        root, monkeypatch, argv):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-unborn")
    r = CliRunner().invoke(cli, argv)
    assert r.exit_code == 0, r.output
    assert r.output.strip().splitlines()[-1] == str(root / "alpha")
    assert "sess-unborn" in r.output and "instead of this session's" in r.output


@pytest.mark.parametrize("argv", [["path"], ["box", "path"]])
def test_a_named_box_wins_over_the_session_box(root, monkeypatch, argv):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    r = CliRunner().invoke(cli, [*argv, "alpha"])
    assert r.exit_code == 0, r.output
    assert r.output.strip().splitlines()[-1] == str(root / "alpha")


# ── the README's self-test ─────────────────────────────────────────────────

def quick_start() -> str:
    text = README.read_text()
    m = re.search(r"^## Quick start\n(.*?)^## ", text, re.DOTALL | re.MULTILINE)
    assert m, "README has no Quick start section"
    return m.group(1)


def command_lines(section: str) -> list[str]:
    """Every line inside a fenced block of SECTION, its comment cut off."""
    lines, inside = [], False
    for line in section.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside:
            lines.append(line.split("#", 1)[0].strip())
    return lines


def test_the_quick_start_self_test_sends_to_and_lists_one_box():
    lines = command_lines(quick_start())
    made = [m.group(1) for ln in lines if (m := re.fullmatch(r"message setup --box (\S+)", ln))]
    sent = [m.group(1) for ln in lines
            if (m := re.fullmatch(r"message send (\S+) -s \S+ --no-body", ln))]
    listed = [m.group(1) for ln in lines if (m := re.fullmatch(r"message list (\S+)", ln))]
    assert made and sent and listed, f"no setup/send/list self-test in:\n{lines}"
    assert made[0] == sent[0] == listed[0], (made, sent, listed)


def test_the_quick_start_says_why_the_list_names_the_box():
    prose = folded(quick_start())
    assert "Name the box in `message list` too." in prose
    assert "this session's box once it exists, else the seat's box" in prose
