"""The rules grammar and its parser: `openspec/changes/pager-filters`, tasks 2.1.

Design D3 (the grammar) and D2 (the conditions), as ruled (P1-P15). One rule per line, the VERB first (`start`, `wake`,
`ride`, `wake!`), then conditions that must ALL match (AND on one line; two
lines are OR). Conditions are `to:` `from:` `subject:` and any header by
name, each a GLOB (fnmatch-style, never a regex). `$me` stands for this
session's and this seat's addresses; `#` starts a comment.

The spec's scenario *A line the parser does not know*: an unknown verb on
line 3 is named with the file and line 3, and ONLY that line is skipped.
So `parse()` never raises over the text it is given: every bad line comes
back as a named error beside the rules that did parse.

`parse(text, source)` is pure: no file, no environment, no clock. The
judgement (tasks 2.2, do-002) and the layers (2.4, do-003) build on it.

Every table here was run against a missing `sessionmail.rules` first and
failed (the fail-first rule of the slice).
"""

from __future__ import annotations

import pytest

from sessionmail import rules
from sessionmail.rules import Condition, Rule, RuleError, parse

SRC = "/home/x/.sessionmail/seats/ivan/rules"


def one(text: str) -> Rule:
    """Parse a text that must give exactly one rule and no error."""
    got, errors = parse(text, SRC)
    assert errors == (), errors
    assert len(got) == 1, got
    return got[0]


def one_error(text: str) -> RuleError:
    """Parse a text that must give no rule and exactly one error."""
    got, errors = parse(text, SRC)
    assert got == (), got
    assert len(errors) == 1, errors
    return errors[0]


# ── the verbs (four ruled, two carries) ──────────────────────────────────────

@pytest.mark.parametrize("line, verb, action, breakthrough", [
    ("start to:$me", "start", "start", False),
    ("wake to:$me", "wake", "wake", False),
    ("ride *", "ride", "ride", False),
    ("wake! from:judy@*", "wake!", "wake", True),
])
def test_each_verb_parses(line, verb, action, breakthrough):
    rule = one(line)
    assert rule.verb == verb
    # `wake!` is a breakthrough whose ACTION is wake (D4 step 1); the others
    # are their own action.
    assert rule.action == action
    assert rule.breakthrough is breakthrough


def test_the_verbs_are_exactly_the_four_ruled_ones_then_the_two_carries():
    """The four as ruled (D3, P1), then SM-640's `carry` and `carry!`
    (tests/test_rules_carry.py holds the rest of them)."""
    assert rules.VERBS == ("start", "wake", "ride", "wake!", "carry", "carry!")


@pytest.mark.parametrize("line, why", [
    ("override from:judy@*", "override"),    # an alternative to wake! (P1), not ruled
    ("locked from:judy@*", "locked"),        # the other alternative
    ("Wake to:$me", "Wake"),                 # verbs are lower case, as ruled
    ("WAKE! to:$me", "WAKE!"),
    ("wake!! to:$me", "wake!!"),
    ("ride-along *", "ride-along"),
    ("to:$me wake", "to:$me"),              # the verb comes FIRST
])
def test_an_unknown_verb_is_named(line, why):
    err = one_error(line)
    assert "unknown verb" in err.reason
    assert repr(why) in err.reason
    assert err.line == 1


# ── the conditions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, want", [
    ("wake to:ivan@*", ("to", "ivan@*")),
    ("ride from:*@delta.chat", ("from", "*@delta.chat")),
    ("wake! subject:URGENT*", ("subject", "URGENT*")),
    ("wake x-project:atomnote", ("x-project", "atomnote")),
    ("wake x-store:acmecorp", ("x-store", "acmecorp")),
    ("wake x-alert-name:disk-*", ("x-alert-name", "disk-*")),
    ("wake Pager-Note:*", ("pager-note", "*")),              # header names fold case
    ("wake X-Project:AtomNote", ("x-project", "AtomNote")),  # ...the glob keeps it
    ("wake TO:ivan@*", ("to", "ivan@*")),                    # so do the named kinds
    ("wake to:ivan@hq:2525", ("to", "ivan@hq:2525")),        # split at the FIRST colon
    ("ride subject:[ops]*", ("subject", "[ops]*")),          # kept verbatim ([ is literal, O-g)
    ("ride subject:re?", ("subject", "re?")),
])
def test_each_condition_kind(line, want):
    rule = one(line)
    assert [(c.name, c.glob) for c in rule.conditions] == [want]


@pytest.mark.parametrize("name, kind", [
    ("to", "to"),
    ("from", "from"),
    ("subject", "subject"),
    ("x-project", "header"),
    ("list-id", "header"),
    ("cc", "header"),                 # only to: reads To/Cc; cc: is a plain header
    ("x-original-to", "header"),      # ...and to: reads X-Original-To, but this names it raw
])
def test_the_condition_kind_is_named(name, kind):
    rule = one(f"wake {name}:x")
    assert rule.conditions[0].kind == kind


def test_several_conditions_on_one_line_are_all_kept_in_order():
    """AND on one line: every condition must match (D3)."""
    rule = one("wake! from:eve@* subject:URGENT* x-project:ops")
    assert rule.conditions == (
        Condition("from", "eve@*"),
        Condition("subject", "URGENT*"),
        Condition("x-project", "ops"),
    )


def test_the_same_condition_twice_is_two_conditions():
    """Both must match; nothing is merged or dropped."""
    rule = one("ride from:*@delta.chat from:bot-*")
    assert rule.conditions == (Condition("from", "*@delta.chat"),
                               Condition("from", "bot-*"))


def test_two_lines_are_two_rules_in_file_order():
    """OR across lines; the ORDER is what first-match reads (D4 step 2)."""
    got, errors = parse("ride to:kith@*\nwake to:$me\n", SRC)
    assert errors == ()
    assert [(r.verb, r.line) for r in got] == [("ride", 1), ("wake", 2)]


# ── `*` ──────────────────────────────────────────────────────────────────────

def test_star_alone_matches_every_mail():
    """`ride *` is the built-in catch-all (D4 step 3): no conditions."""
    rule = one("ride *")
    assert rule.conditions == ()
    assert rule.matches_all is True


def test_a_rule_with_conditions_is_not_match_all():
    assert one("ride to:x").matches_all is False


@pytest.mark.parametrize("line", [
    "ride * to:kith@*",
    "ride to:kith@* *",
    "ride * *",
])
def test_star_stands_alone(line):
    err = one_error(line)
    assert "'*'" in err.reason


# ── `$me` ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "wake to:$me",
    "ride from:$me",
    "wake x-original-to:$me",
])
def test_me_is_a_variable(line):
    cond = one(line).conditions[0]
    assert cond.glob == "$me"
    assert cond.variable == "me"


def test_a_plain_glob_is_not_a_variable():
    assert one("wake to:ivan@*").conditions[0].variable is None


def test_the_variables_are_me_operator_and_urgent():
    """`$me` (D2); `$operator` (design A3; SM-727, which renamed `$judy`) and
    `$urgent` (A2's list), do-005. No person's name is a variable."""
    assert rules.VARIABLES == frozenset({"me", "operator", "urgent"})


@pytest.mark.parametrize("line, why", [
    ("wake to:$you", "$you"),            # unknown variable
    ("wake! from:$judyx", "$judyx"),     # a variable is its whole name
    ("wake to:$me*", "$me"),             # a variable is the WHOLE glob
    ("wake to:*$me", "$me"),
    ("wake to:$me@hq", "$me"),
])
def test_a_bad_variable_is_named(line, why):
    err = one_error(line)
    assert why in err.reason


@pytest.mark.parametrize("line, glob", [
    ("ride subject:$100*", "$100*"),     # `$` then a digit: never a variable
    ("ride subject:*$*", "*$*"),
    ("ride subject:cost$", "cost$"),
    ("ride subject:$", "$"),
])
def test_a_dollar_that_names_no_variable_is_literal(line, glob):
    cond = one(line).conditions[0]
    assert cond.glob == glob
    assert cond.variable is None


# ── comments, blank lines, white space ───────────────────────────────────────

def test_comments_and_blank_lines_give_no_rule_and_no_error():
    text = "# ~/.sessionmail/seats/ivan/rules\n\n   \n\t# indented comment\n"
    assert parse(text, SRC) == ((), ())


def test_a_trailing_comment_is_dropped():
    rule = one("ride   to:kith@*                  # Kith rides along, never wakes")
    assert rule.conditions == (Condition("to", "kith@*"),)
    assert rule.text == "ride   to:kith@*"


def test_a_hash_inside_a_glob_is_not_a_comment():
    """`#` starts a comment at the start of a line or after white space
    only, so a subject glob can still hold one."""
    rule = one("ride subject:#123*  # a ticket")
    assert rule.conditions == (Condition("subject", "#123*"),)


def test_tabs_and_runs_of_spaces_separate_the_same_as_one_space():
    rule = one("\twake!\t\tfrom:eve@*   subject:URGENT*\t")
    assert rule.verb == "wake!"
    assert len(rule.conditions) == 2


def test_crlf_lines_parse_like_lf():
    got, errors = parse("ride to:kith@*\r\nwake to:$me\r\n", SRC)
    assert errors == ()
    assert [r.line for r in got] == [1, 2]
    assert got[0].text == "ride to:kith@*"


def test_an_empty_text_is_no_rules():
    assert parse("", SRC) == ((), ())


# ── the rule remembers where it came from ────────────────────────────────────

def test_a_rule_carries_its_source_line_and_text():
    """`pager why ID` names the rule and its layer (P10); the verdict
    (do-002) carries the rule text and line, so the rule must hold them."""
    got, _ = parse("# seat rules\n\nride   from:*@delta.chat   # a chat bridge\n", SRC)
    (rule,) = got
    assert rule.source == SRC
    assert rule.line == 3
    assert rule.text == "ride   from:*@delta.chat"
    assert str(rule) == "ride   from:*@delta.chat"


def test_the_design_d3_example_file_parses_whole():
    text = (
        "# ~/.sessionmail/seats/ivan/rules\n"
        "ride   to:kith@*                  # Kith rides along, never wakes\n"
        "ride   from:*@delta.chat          # a chat bridge\n"
        "wake   x-project:atomnote         # a project I'm building\n"
    )
    got, errors = parse(text, SRC)
    assert errors == ()
    assert [(r.verb, r.line, [(c.name, c.glob) for c in r.conditions]) for r in got] == [
        ("ride", 2, [("to", "kith@*")]),
        ("ride", 3, [("from", "*@delta.chat")]),
        ("wake", 4, [("x-project", "atomnote")]),
    ]


def test_the_built_in_defaults_parse():
    """D4 step 3's three lines, as ruled."""
    got, errors = parse("wake!  from:judy@*\nwake   to:$me\nride   *\n", "built-in")
    assert errors == ()
    assert [r.verb for r in got] == ["wake!", "wake", "ride"]


# ── a bad condition ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, fragment", [
    ("wake", "no condition"),                       # a verb alone
    ("wake!   # nothing after", "no condition"),
    ("ride kith", "'kith'"),                        # no colon
    ("ride to:", "empty glob"),                     # nothing to match
    ("ride :kith@*", "no header name"),
    ("ride to kith@*", "'to'"),                     # a space where the colon goes
    ("wake x_project:atomnote", "'x_project'"),     # not a header name
    ("wake x project:atomnote", "'x'"),
    ("wake héader:x", "'héader'"),
])
def test_a_bad_condition_is_named(line, fragment):
    err = one_error(line)
    assert fragment in err.reason


# ── a bad line is named with file and line, and skipped ALONE ────────────────

def test_an_unknown_verb_on_line_3_is_named_and_only_that_line_skipped():
    """The spec's scenario *A line the parser does not know*."""
    text = "ride to:kith@*\nwake to:$me\nsnooze from:*@delta.chat\nride *\n"
    got, errors = parse(text, SRC)
    assert [r.line for r in got] == [1, 2, 4]
    (err,) = errors
    assert err.source == SRC
    assert err.line == 3
    assert err.text == "snooze from:*@delta.chat"
    assert "'snooze'" in err.reason


def test_the_error_reads_as_file_colon_line():
    err = one_error("snooze from:*@delta.chat")
    assert str(err).startswith(f"{SRC}:1: ")
    assert "unknown verb 'snooze'" in str(err)
    assert "snooze from:*@delta.chat" in str(err)


def test_one_bad_condition_skips_the_whole_line_not_just_the_condition():
    """A line that half-parses is not kept half: `ride from:x kith` would
    otherwise widen to `ride from:x`, silencing more than its author meant."""
    got, errors = parse("ride from:*@delta.chat kith\nwake to:$me\n", SRC)
    assert [r.line for r in got] == [2]
    assert [e.line for e in errors] == [1]


def test_every_bad_line_is_named_not_only_the_first():
    text = "snooze *\nride kith\nwake to:$me\nwake to:\n"
    got, errors = parse(text, SRC)
    assert [r.line for r in got] == [3]
    assert [e.line for e in errors] == [1, 2, 4]
    assert all(e.source == SRC for e in errors)


def test_parse_never_raises_over_its_text():
    """Nothing a file can hold makes parse() throw: a bad line is DATA."""
    nasty = "\x00\x01 wake\n:::\n!!!\nwake! :\n﻿ride *\n" + "x" * 10000
    got, errors = parse(nasty, SRC)
    assert isinstance(got, tuple) and isinstance(errors, tuple)
    assert len(errors) >= 4


def test_the_source_is_carried_as_given():
    """The caller names the source: a path, `SESSIONMAIL_PAGE_RULES`, or
    `built-in`. The parser reads no file itself."""
    err = one_error_from("snooze *", "SESSIONMAIL_PAGE_RULES")
    assert err.source == "SESSIONMAIL_PAGE_RULES"
    assert str(err).startswith("SESSIONMAIL_PAGE_RULES:1: ")


def one_error_from(text: str, source: str) -> RuleError:
    got, errors = parse(text, source)
    assert got == ()
    (err,) = errors
    return err


# ── pure, and immutable ──────────────────────────────────────────────────────

def test_rules_and_errors_are_frozen():
    rule = one("wake to:$me")
    with pytest.raises(AttributeError):
        rule.verb = "ride"  # type: ignore[misc]
    err = one_error("snooze *")
    with pytest.raises(AttributeError):
        err.line = 9  # type: ignore[misc]


def test_parse_is_repeatable():
    text = "ride to:kith@*\nsnooze *\nwake to:$me\n"
    assert parse(text, SRC) == parse(text, SRC)


def test_a_byte_order_mark_at_the_start_of_the_text_is_not_part_of_the_verb():
    """An editor may save one; it is not a character of line 1."""
    rule = one("﻿ride *")
    assert rule.verb == "ride"


def test_only_lf_ends_a_line_so_numbers_match_an_editor():
    """A form feed inside a line does not start a new one (`splitlines()`
    would break there): the comment swallows the rest of line 1, and the
    error names line 2, the line an editor shows."""
    got, errors = parse("# a comment\x0cride *\nsnooze *\n", SRC)
    assert got == ()
    assert [e.line for e in errors] == [2]
