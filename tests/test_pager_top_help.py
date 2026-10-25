"""`pager -h`, the top page a stranger reads first on a fresh install.

It states the default (mail to this session's id wakes this session),
points at `pager rule` for what pages and what rides, names no mail
carrier, and its `Exit:` line says what `pager` really exits. The exit
codes are measured here by running the group, not copied from the page.
"""
from __future__ import annotations

import re

from click.testing import CliRunner

from sessionmail.pager import EX_COULD_NOT_LOOK, cli


def _page() -> str:
    return CliRunner().invoke(cli, ["-h"], prog_name="pager").output


def _exit_line(page: str) -> str:
    lines = [line.strip() for line in page.splitlines() if line.strip().startswith("Exit:")]
    assert len(lines) == 1, page
    return lines[0]


def _codes(exit_line: str) -> dict[str, int]:
    """'Exit: 0 ok · 2 usage, or could not look' -> {'ok': 0, 'usage': 2,
    'could not look': 2}. A code covers every meaning in its clause."""
    out: dict[str, int] = {}
    for clause in exit_line.removeprefix("Exit:").split("·"):
        m = re.match(r"\s*(\d+)\s+(.*)", clause)
        assert m, clause
        code, words = int(m.group(1)), re.sub(r"\(.*?\)", "", m.group(2))
        for meaning in re.split(r",\s*or\s+|,\s*", words):
            if meaning.strip():
                out[meaning.strip()] = code
    return out


def test_the_page_states_the_default():
    page = " ".join(_page().split())
    assert "mail to this session's id wakes this session" in page.lower(), page


def test_the_page_points_at_pager_rule():
    assert "`pager rule`" in _page()


def test_the_page_names_no_carrier():
    assert "postfix" not in _page().lower()


def test_the_exit_line_says_what_a_usage_error_exits():
    measured = {CliRunner().invoke(cli, argv, prog_name="pager").exit_code
                for argv in (["--no-such-flag"], ["no-such-verb"], ["on"])}
    assert measured == {2}, measured     # click's usage exit, every way in
    codes = _codes(_exit_line(_page()))
    assert codes.get("usage") == 2, codes
    assert 1 not in codes.values(), codes


def test_the_exit_line_says_what_could_not_look_exits():
    codes = _codes(_exit_line(_page()))
    assert codes.get("could not look") == EX_COULD_NOT_LOOK, codes


def test_could_not_look_is_measured_too(monkeypatch, tmp_path):
    """No session id is the plainest could-not-look: `pager status` has no
    box to read."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "TM_AGENT_ADDRESS",
                "CLAUDE_CODE_AGENT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(cli, ["status"], prog_name="pager")
    assert "could not look" in result.output, result.output
    assert result.exit_code == EX_COULD_NOT_LOOK == 2
