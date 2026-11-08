"""SM-988: a glob with a class-shaped `[...]` is kept, and NAMED.

`[` is literal in a pager glob (build decision O-g): there are no `[...]`
classes, so `subject:[draft]*` matches a bracketed tag. The other face of
that is `subject:*[Aa]lice*`: it looks for the text `[Aa]lice`, brackets and
all, and never matches `Alice, can you...`. The parser took that line with
no word, the mail fell through to a catch-all `ride` below it, and the seat
was deaf. O-g is kept (reversing it is not this fix); what changes is that
the pager says so:

- `rules.lint(rules)` returns a `RuleWarning` for each class-shaped glob,
  naming the line and, for a case pair, the glob that was meant (`*alice*`:
  matching is already case-insensitive). Any other class gets no rewrite:
  `?` is broader than a set, and `[a-z]*` as `?*` is a catch-all. A warned
  line is still a rule, still judged.
- `load_layers` gathers them as `Layers.warnings`, beside `Layers.errors`.
- `pager rule list` (one layer and `--all`) and `pager rule add` print them;
  the arm banner names them, and the ride-along names a new one once.

A bracketed TAG (`[draft]`, `[URGENT]`, `[RFC-822]`, a date) is never
class-shaped, so the use O-g was built for draws no warning.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sessionmail import rules
from sessionmail.pager import cli
from sessionmail.rules import RuleWarning, lint, parse
from test_pager_listen_judges import (  # noqa: F401  (the rig is a fixture)
    EX_PAGE,
    batch,
    rig,
    run,
)
from test_rules_layers import Roots, roots  # noqa: F401  (a fixture)

SRC = "/home/x/.sessionmail/seats/alice/rules"


def warnings_for(text: str) -> tuple[RuleWarning, ...]:
    """`lint` over a text that must parse clean."""
    got, errors = parse(text, SRC)
    assert errors == (), errors
    return lint(got)


# ── what is class-shaped, and what is a tag ─────────────────────────────────

@pytest.mark.parametrize("glob, meant", [
    ("*[Aa]lice*", "*alice*"),              # the shape that missed
    ("*[aA]lice*", "*alice*"),              # either order
    ("?[Bb]ob*", "?bob*"),                   # a `?` already in the glob is kept
    ("*[Aa]lice*[Bb]ob*", "*alice*bob*"),    # every class in one glob
])
def test_a_case_pair_is_warned_with_the_glob_that_was_meant(glob, meant):
    (w,) = warnings_for(f"carry subject:{glob}\n")
    assert (w.source, w.line, w.text) == (SRC, 1, f"carry subject:{glob}")
    assert w.reason.endswith(
        f"matching is already case-insensitive, so write subject:{meant}"), w.reason
    assert "literal" in w.reason


#: A class no glob is as narrow as: `?` is any one character, so a rewrite
#: to it is BROADER than the intent (`subject:[a-z]*` as `subject:?*` is a
#: catch-all, the deafness this warning exists to stop). It is named, and
#: what it literally matches is said; no rewrite is offered.
NO_REWRITE = [
    "[a-z]*",              # a range
    "*[A-Za-z]x",
    "v[0-9]*",
    "[!a-z]*",             # a negated class
    "[AaBb]*",             # two case pairs: one of a set
    "[0-9]*",              # a digit range holding no number
    "*[a-f0-9]*",
    "*[0-9a-z]x",
    "*[Aa]lice[0-9]*",     # a case pair beside a range: no exact rewrite
]


@pytest.mark.parametrize("glob", NO_REWRITE)
def test_a_class_no_glob_can_match_is_warned_and_offered_no_rewrite(glob):
    (w,) = warnings_for(f"carry subject:{glob}\n")
    assert (w.source, w.line, w.text) == (SRC, 1, f"carry subject:{glob}")
    assert "literal" in w.reason and "looks for the text [" in w.reason
    assert w.reason.endswith("no glob is as narrow as a set, so no rewrite is offered"), w
    assert "so write" not in w.reason, "no rewrite, so no glob to write"
    assert "?" not in w.reason, "no glob in NO_REWRITE holds a ?, so none was offered"


def test_a_case_pair_says_matching_is_already_case_insensitive():
    (w,) = warnings_for("carry subject:*[Aa]lice*\n")
    assert w.reason.endswith("matching is already case-insensitive, so write subject:*alice*"), w
    assert "[Aa]" in w.reason


def test_a_range_says_what_it_matches_and_offers_no_catch_all():
    (w,) = warnings_for("wake subject:[a-z]*\n")
    assert "subject:[a-z]* looks for the text [a-z], brackets and all" in w.reason, w
    assert "subject:?*" not in w.reason, "the old advice was a catch-all"


@pytest.mark.parametrize("glob", [
    "[draft]*", "[URGENT]*", "*[RFC-822]*", "*[2030-06-09]*", "*[v2]*",
    "[!important]*", "*[Dad]*", "*]x[*", "*[*", "*alice*", "*",
])
def test_a_tag_or_a_plain_glob_is_not_warned(glob):
    assert warnings_for(f"wake subject:{glob}\n") == ()


@pytest.mark.parametrize("tag", ["[Oops]*", "*[Aaron]*", "[Llama]*", "*[Bob]*"])
def test_a_word_tag_holding_a_case_pair_is_not_warned(tag):
    """A word with an adjacent case pair (`Oo`, `Aa`, `Ll`) is a tag, not a
    class: warning it would tell its writer to swap a narrow rule for `?*`,
    a catch-all. A class is case pairs and nothing else (`[Cc]`, `[AaBb]`)."""
    assert warnings_for(f"carry subject:{tag}\n") == ()


@pytest.mark.parametrize("tag", ["*[10-20]*", "[v10-12]*", "*[1-22]*", "[2030-12-08]*"])
def test_a_tag_holding_a_number_is_not_warned(tag):
    """Two digits side by side are a number, so the brackets hold a tag. A
    date whose month and day digits happen to run in order (`0-1`, `2-2`)
    once read as ranges and was told to become `?*`, a catch-all. A class
    never writes a number: `[0-9]` and `[a-f0-9]` are still warned."""
    assert warnings_for(f"carry subject:{tag}\n") == ()


@pytest.mark.parametrize("tag", [
    "[non-urgent]*", "*[auto-reply]*", "[pre-release]*", "*[e-mail]*",
    "[re-run]*", "[co-op]*", "*[ci-skip]*", "[in-progress]*", "[read-only]*",
    "[sign-off]*", "[v1-2]*",
])
def test_a_hyphenated_word_tag_is_not_warned(tag):
    """A hyphenated word whose letters either side of the hyphen run in
    order (`n-u` in `non-urgent`) once read as a range and was told to
    become `?*`, a catch-all. A range class is ranges and nothing else
    (`[a-z]`, `[A-Za-z]`, `[a-f0-9]`); a letter standing alone is a word."""
    assert warnings_for(f"carry subject:{tag}\n") == ()


#: REAL bracketed tags, measured read-only on one host (SM-988 fix-001
#: round 3: every [...] run in 95,437 mail subjects and 158,339 commit
#: subjects, linted as `subject:<tag>*`). A representative sample of the
#: shapes, names and hosts left out. Every one is a tag, and none is warned;
#: the only two runs warned in that corpus are the classes at the end.
REAL_TAGS = [
    "[draft]", "[TEST]", "[BOX]", "[Title]", "[Unreleased]", "[attachment]",
    "[media]", "[bed]", "[0]", "[1]", "[10]", "[453]", "[100%]", "[1/3]",
    "[0.1.0]", "[0.2.1]", "[0.3.2]", "[...]", "[-]", "[+]", "[+device]",
    "[--json]", "[--dry-run]", "[--why]", "[DB|reset]", "[N|ID...]",
    "[{node,card}]", "[,NAME]", "[/<profile>]", "[1f066cc2]", "[3ae1fa02]",
    "[TASK-ID]", "[SRC-VERIFIED]", "[dependency-groups]", "[markdown-oxide]",
    "[openspec-format]", "[data-qa=channel_name]",
]
#: The two runs that WERE warned in that corpus, each an unmistakable
#: class: a regex lookbehind's `[0-9]`, and the case-pair glob this fix
#: is about.
REAL_CLASSES = [("[0-9]", None), ("[Cc]", "c*")]


@pytest.mark.parametrize("tag", REAL_TAGS)
def test_a_real_tag_is_not_warned(tag):
    assert warnings_for(f"carry subject:{tag}*\n") == ()


@pytest.mark.parametrize("tag, meant", REAL_CLASSES)
def test_a_real_class_is_warned_and_only_a_case_pair_is_rewritten(tag, meant):
    (w,) = warnings_for(f"carry subject:{tag}*\n")
    if meant is None:
        assert w.reason.endswith("so no rewrite is offered"), w
    else:
        assert w.reason.endswith(f"so write subject:{meant}"), w


@pytest.mark.parametrize("line", [
    "wake from:[Aa]lice@*",
    "ride to:*[0-9]@example.net",
    "wake x-project:[Aa]tomnote",
    "carry call:[Aa]lice",
])
def test_every_glob_kind_is_linted(line):
    (w,) = warnings_for(line + "\n")
    assert w.text == line


def test_a_variable_and_the_value_kinds_are_never_linted():
    assert warnings_for("wake to:$me\nwake priority:urgent\nride draft:yes\n") == ()


def test_lint_names_each_line_in_line_order_and_keeps_the_rule():
    text = "carry subject:*[Aa]lice*\n# note\nride *\nwake subject:[a-z]*\n"
    got, errors = parse(text, SRC)
    assert errors == () and len(got) == 3, "a warned line is still a rule"
    assert [w.line for w in lint(got)] == [1, 4]


def test_a_warning_prints_as_source_line_warning_reason_text():
    (w,) = warnings_for("carry subject:*[Aa]lice*\n")
    assert str(w) == f"{SRC}:1: warning: {w.reason}: carry subject:*[Aa]lice*"


def test_the_built_ins_carry_no_warnings():
    assert lint(rules.built_in().rules) == ()


# ── load_layers gathers them ────────────────────────────────────────────────

def test_load_layers_gathers_warnings_from_every_user_layer(roots: Roots):
    roots.write_locked("wake! subject:[Aa]lert*\n")
    path = roots.write(roots.session_file(), "ride *\ncarry subject:*[Aa]lice*\n")
    env = {rules.PAGE_RULES_VAR: "wake subject:[0-9]*"}
    layers = roots.load(env=env)
    assert layers.errors == ()
    assert [(w.source, w.line) for w in layers.warnings] == [
        (str(roots.locked), 1), (str(path), 2), (rules.PAGE_RULES_VAR, 1)]


def test_a_layers_with_no_class_shaped_glob_has_no_warnings(roots: Roots):
    roots.write(roots.session_file(), "wake subject:[draft]*\n")
    assert roots.load().warnings == ()


# ── where a writer sees it ──────────────────────────────────────────────────

def pager_cmd(*argv: str):
    return CliRunner().invoke(cli, list(argv), prog_name="pager")


def test_rule_add_writes_a_class_shaped_rule_and_warns(rig):
    result = pager_cmd("rule", "add", "carry", "subject:*[Aa]lice*")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "added to session" in result.stdout
    assert "warning (kept)" in result.stdout, result.stdout
    assert "write subject:*alice*" in result.stdout


def test_rule_add_of_a_tag_does_not_warn(rig):
    result = pager_cmd("rule", "add", "wake", "subject:[draft]*")
    assert result.exit_code == 0, result.stderr
    assert "warning" not in result.stdout


def test_rule_list_names_a_warned_line_and_still_lists_it(rig):
    path = rig.session_rules("carry subject:*[Aa]lice*\nride *\n")
    result = pager_cmd("rule", "list")
    assert result.exit_code == 0, result.stderr
    assert "1  carry subject:*[Aa]lice*" in result.stdout, "kept, so listed"
    assert f"warning (kept)  {path}:1: warning:" in result.stdout, result.stdout


def test_rule_list_all_names_a_warned_line(rig):
    path = rig.session_rules("carry subject:*[Aa]lice*\n")
    result = pager_cmd("rule", "list", "--all")
    assert result.exit_code == 0, result.stderr
    assert f"warning (kept)  {path}:1:" in result.stdout, result.stdout


def test_a_warned_line_present_at_arm_is_named_in_the_banner(rig):
    rig.box()
    path = rig.session_rules("carry subject:*[Aa]lice*\n")
    rig.deliver("w1@x.invalid")
    rig.stopped()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE
    assert f"{path}:1: warning:" in result.stderr, result.stderr
    assert "kept, as written" in result.stderr


def test_a_warned_line_that_appears_later_is_named_once_in_the_ride_along(rig):
    rig.box()
    path = rig.session_rules("ride *\n")
    assert not any("warning" in ln for ln in batch())
    path.write_text("ride *\ncarry subject:*[Aa]lice*\n", encoding="utf-8")
    first = [ln for ln in batch() if "warning" in ln]
    assert len(first) == 1 and f"{path}:2:" in first[0], first
    assert "kept as written" in first[0]
    assert not any("warning" in ln for ln in batch()), "once, not on every tool call"
