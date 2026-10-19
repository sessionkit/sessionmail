"""`message setup` is a listed verb: on `message --help`, not an MCP tool.

Its name is final. It is listed on the group page, beside the other verbs,
and `message setup --help` answers as before. It is NOT an MCP tool: it
makes this host's mail root, boxes and notmuch profile and links hooks, a
host owner's act, so it is named in `_mcp.NOT_A_TOOL` (on the page, never
a tool), the way `message box archive` is.
"""

from __future__ import annotations

from click.testing import CliRunner

from sessionmail import _mcp, _setup
from sessionmail.message import cli as message_cli


def _page() -> str:
    result = CliRunner().invoke(message_cli, ["--help"], prog_name="message")
    assert result.exit_code == 0, result.output
    return result.stdout


def test_group_help_lists_setup():
    """The group page lists `setup` in its verb list (one line per verb,
    the verb first)."""
    verbs = [line.split()[0] for line in _page().splitlines()
             if line.startswith("  ") and line.split()]
    assert _setup.VERB in verbs, _page()
    assert not message_cli.commands[_setup.VERB].hidden


def test_setup_is_not_an_mcp_tool():
    names = [name for name, _, _ in _mcp.tools(message_cli, "message")]
    assert names, "no tools at all: the check below would prove nothing"
    assert "message_setup" not in names
    assert ("message", _setup.VERB) in _mcp.NOT_A_TOOL
    assert "host owner" in _mcp.NOT_A_TOOL[("message", _setup.VERB)]


def test_its_own_page_still_answers_above_the_fold():
    result = CliRunner().invoke(message_cli, [_setup.VERB, "--help"], prog_name="message")
    assert result.exit_code == 0
    head = result.stdout.splitlines()[:20]
    assert head[0] == "Usage: message setup [--box NAME]... [--dry-run] [--write-maps]"
    assert any("--dry-run" in line for line in head)
    assert any(line.lstrip().startswith("Exit:") for line in head)


def test_the_name_is_final():
    """No page and no module doc calls the name provisional any more."""
    page = CliRunner().invoke(message_cli, [_setup.VERB, "--help"], prog_name="message").stdout
    assert "provisional" not in page.lower()
    doc = (_setup.__doc__ or "").lower()
    assert "provisional" not in doc and "hidden" not in doc


def test_the_mcp_page_says_why_setup_is_not_a_tool():
    page = CliRunner().invoke(message_cli, ["mcp", "-h"], prog_name="message").output
    flat = " ".join(page.split())
    assert ("Not a tool: `message setup`. It makes this host's mail root, boxes and notmuch "
            "profile, and links its hooks: a host owner's act.") in flat


# ── the group page makes room for `setup` from click's own lines ─────────────
#
# With `setup` listed, `message --help` needed two more lines to keep its last
# verb, `mcp`, inside `--help | head -20`. They come from click's chrome, not
# from any verb row: the group page drops its own `-h, --help` row (the
# reader is already on that page), and `Options:` follows the description
# with no blank line between them.

def test_the_last_verb_is_inside_head_20():
    head = _page().splitlines()[:20]
    rows = [line.split()[0] for line in head if line.startswith("  ") and line.split()]
    last = message_cli.list_commands(None)[-1]
    assert last == "mcp" and last in rows, "\n".join(head)
    assert _setup.VERB in rows


def test_the_group_page_still_answers_to_both_help_flags():
    for flag in ("-h", "--help"):
        result = CliRunner().invoke(message_cli, [flag], prog_name="message")
        assert result.exit_code == 0, (flag, result.output)
        assert result.stdout == _page()
        assert result.stdout.startswith("Usage: message <verb> [args]\n")


def test_the_group_page_drops_only_its_own_help_row():
    lines = _page().splitlines()
    opts = lines.index("Options:")
    assert lines[opts - 1].strip(), "a blank line before Options: again"
    assert not any(line.lstrip().startswith("-h, --help") for line in lines)
    assert any(line.lstrip().startswith("-V, --version") for line in lines)


def test_every_verb_page_keeps_its_help_row():
    verbs = [v for v in message_cli.list_commands(None) if not message_cli.commands[v].hidden]
    assert _setup.VERB in verbs and "mcp" in verbs
    for verb in verbs:
        page = CliRunner().invoke(message_cli, [verb, "-h"], prog_name="message").output
        assert any(line.lstrip().startswith("-h, --help") for line in page.splitlines()), verb


def test_the_version_flag_still_works():
    from sessionmail import __version__
    result = CliRunner().invoke(message_cli, ["-V"], prog_name="message")
    assert result.exit_code == 0, result.output
    assert __version__ in result.output
