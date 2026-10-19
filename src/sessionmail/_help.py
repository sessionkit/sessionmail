"""Shared help machinery for the three commands.

Help-driven development (slipbox `help-driven-development`): the page is
written first, the command is scaffolded to match it, and the body is a
stub until the requirement is built. This module holds the two click
fixes already measured in the slipbox note `help-in-python`:

- click has no `-h` by default, only `--help`;
- click rewraps a multi-line docstring or epilog into one paragraph
  unless the paragraph starts with a line containing only `\b`.
"""

from __future__ import annotations

import click

#: `-h` as well as `--help`, and a width that matches the reader's budget.
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 78,
}


def unwrapped(text: str) -> str:
    """Return TEXT with every paragraph marked so click will not rewrap it."""
    paragraphs = text.strip("\n").split("\n\n")
    return "\n\n".join("\b\n" + p for p in paragraphs)


class PageCommand(click.Command):
    """A command whose usage line is the one written on its authored page.

    Click renders `Usage: message send [OPTIONS] TO...`. The authored page
    says `Usage: message send TO [TO...] -s SUBJECT (-f FILE | --no-body)
    [options]`, which tells the reader what is required and what is a
    choice. Help-driven development treats the page as the target, so the
    usage line is authored while every option below it stays real.
    """

    def __init__(self, *args, usage: str | None = None,
                 sections: str | None = None, **kwargs) -> None:
        self._authored_usage = usage
        self._sections = sections
        super().__init__(*args, **kwargs)

    def format_epilog(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """SECTIONS, when given, is printed as written, at the margin, after
        the epilog: headed blocks such as `Environment:` and a closing
        `Exit:` line that the authored page sets flush left, as click sets
        `Options:` (message-subscriptions' design). Click indents an
        epilog, so a heading written there would read as prose."""
        super().format_epilog(ctx, formatter)
        if self._sections:
            formatter.write_paragraph()
            formatter.write(self._sections.strip("\n") + "\n")

    def collect_usage_pieces(self, ctx: click.Context) -> list[str]:
        if self._authored_usage is None:
            return super().collect_usage_pieces(ctx)
        return [self._authored_usage]


class OrderedGroup(click.Group):
    """A group whose `--help` lists verbs in the order they were declared.

    Click sorts commands alphabetically. The authored help pages do not:
    `message` reads send, list, tree, read, mark, and `pager` puts the
    read-only verb first on purpose. That order is authorial intent, so
    it is preserved rather than sorted away.
    """

    def __init__(self, *args, name_column: int | None = None,
                 joined_commands: bool = False, joined_options: bool = False,
                 help_row: bool = True, **kwargs) -> None:
        self._name_column = name_column
        self._joined_commands = joined_commands
        self._joined_options = joined_options
        self._help_row = help_row
        super().__init__(*args, **kwargs)

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)

    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """With JOINED_OPTIONS, `Options:` follows the description with no
        blank line between them. Without HELP_ROW, the group page lists no
        `-h, --help` row: its reader is already on that page, and `-h` still
        answers. Each verb's own page keeps its row. Both save a line of
        click's own, never a verb's, so `message --help | head -20` still
        reaches `mcp` with `setup` listed."""
        if not self._joined_options and self._help_row:
            return super().format_options(ctx, formatter)
        help_option = self.get_help_option(ctx)
        opts = [record for param in self.get_params(ctx)
                if (self._help_row or param is not help_option)
                and (record := param.get_help_record(ctx)) is not None]
        if opts:
            if not self._joined_options:
                formatter.write_paragraph()
            formatter.write_heading("Options")
            with formatter.indentation():
                formatter.write_dl(opts)
        self.format_commands(ctx, formatter)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """With NAME_COLUMN, the verb names get that many characters and a
        longer name puts its line below itself. Without it click sizes the
        column to the longest name, and ONE long name (`subscription`, 12)
        pushes every row's text past the width: each wraps to two lines, and
        a page read with `--help | head -20` loses its last verbs (measured on
        `message --help`, message-subscriptions 3.4).

        With JOINED_COMMANDS, `Commands:` follows the Options block with no
        blank line between them. Both are click's own lists, so no prose loses
        its break, and the line saved is the one `message --help` needs to
        keep `mcp` inside `head -20` once master's `verify` joins it."""
        if self._name_column is None:
            return super().format_commands(ctx, formatter)
        rows = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is not None and not cmd.hidden:
                rows.append((name, cmd.get_short_help_str(formatter.width)))
        if not rows:
            return
        if not self._joined_commands:
            formatter.write_paragraph()
        formatter.write_heading("Commands")
        with formatter.indentation():
            formatter.write_dl(rows, col_max=self._name_column)
