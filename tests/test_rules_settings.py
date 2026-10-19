"""`set` lines in the locked file, and the call lines above the Priority lines:
SM-474 do-013.

Design A2, RULED YES: the urgent allow list is a SETTING in
`~/.sessionmail/rules.locked`. Two names:

    set operator    <glob...>     # who the operator is (A3); default: host config (SM-727)
    set urgent  <glob...>     # who may send urgent (N3); default $operator <runner>@<host>

A `set` line is DATA, never tried as a rule. ONLY the locked file may carry
one: in any other layer (session, seat, focus, `SESSIONMAIL_PAGE_RULES`) it
is a bad line, named with its file and line, and skipped, so no agent can put
itself on the host-wide list. `operator()` and `urgent_senders()` read the locked
file's lines when present, else the defaults.

And P-8, RULED: when a Priority rule and
a call rule both match a voice turn, the CALL rule wins, so the built-in call
lines sit ABOVE the Priority lines.

Written to fail first: every test here was run against do-005's
`sessionmail.rules` (215d30c) and failed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

import pytest
from conftest import RUNNER, RUNNER_SETTINGS, SC6_SETTINGS

from sessionmail import rules
from sessionmail.rules import (
    BUILT_IN_TEXT,
    PAGE_RULES_VAR,
    Facts,
    Layer,
    Layers,
    Me,
    Settings,
    built_in,
    judge,
    load_layers,
    operator,
    parse,
    parse_settings,
    urgent_senders,
)

SID = "9be83ae9-58c6-0c79-9faf-d18fba5b63be"
HOST = "hq.tailnet.example"
SHORT = "hq"
ME = Me.of(session=SID, seat="ivan", host=HOST)
NOW = datetime(2030, 7, 11, 12, 34, 56, tzinfo=timezone.utc)
ENV = {"SESSIONMAIL_HOST": SHORT, "MAGICDNS_SUFFIX": "tailnet.example"}

JUDY = f"Judy <judy@{HOST}>"
EVE = f"Eve <eve@{HOST}>"
BOSS = f"Boss <boss@{HOST}>"
#: The alert runner, From its configured local part (`conftest.RUNNER`, SM-798).
ALERTS = f"Alerts <{RUNNER}@{HOST}>"
KEEPER = f"Ears <heard+phone@{HOST}>"
SUBSCRIBED = ("X-Original-To", "atomnote")


def _mail(sid: str) -> Path:
    """Where a session's mail state is since session-settings 3.3a: under
    the session dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/`
    (spelt out, not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def mail(*headers: tuple[str, str], env=ENV) -> Facts:
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return Facts.from_bytes((head + "\n").encode(), env=env)


def urgent_from(sender: str) -> Facts:
    """An urgent through a subscription: only a Priority line (or `wake!`)
    can wake it, so the list alone decides."""
    return mail(("From", sender), SUBSCRIBED, ("Priority", "urgent"))


def turn(rule: str, *more: tuple[str, str]) -> Facts:
    """A trusted voice turn, as the keeper writes one (see do-005's tests)."""
    return mail(("From", KEEPER), ("X-Original-To", "heard"),
                ("Ears-Kind", "spoken"), ("Ears-Rule", rule), *more)


class Roots:
    """A fake host: a sessionmail home and a locked file, both under tmp."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.locked = tmp / "etc" / "rules.locked"

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_locked(self, text: str) -> Path:
        return self.write(self.locked, text)

    def session_file(self, name: str = "rules") -> Path:
        return _mail(SID) / name

    def seat_file(self, name: str = "rules") -> Path:
        return self.home / "seats" / "ivan" / name

    def load(self, env: dict[str, str] | None = None) -> Layers:
        return load_layers("ivan", SID, env or {}, home=self.home, locked=self.locked)


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


def decide(facts: Facts, layers) -> tuple[str, str | None, str, bool]:
    got = judge(facts, layers, ME, NOW)
    return got.action, got.rule, got.layer, got.at_once


# ── P-8: the call lines sit ABOVE the Priority lines ─────────────────────────

def test_the_built_in_lines_put_the_call_lines_above_the_priority_lines():
    """P-8 RULED: the call rule wins over a Priority rule on a voice turn."""
    assert [r.text for r in built_in().rules] == [
        "carry! from:$operator",
        "carry call:$me",
        "carry call:everyone",
        "ride priority:non-urgent",
        "carry priority:urgent from:$urgent",
        "wake to:$me",
        "ride *",
    ]
    got, errors = parse(BUILT_IN_TEXT, "built-in")
    assert errors == () and len(got) == 7


def test_a_non_urgent_voice_turn_that_calls_me_wakes():
    """P-8's pin: `Priority: non-urgent` and a call both match; the call wins."""
    got = decide(turn(f"ivan@{HOST}=call", ("Priority", "non-urgent")), [built_in()])
    assert got == ("wake", "carry call:$me", "built-in", False)


def test_a_non_urgent_voice_turn_that_calls_everyone_wakes():
    got = decide(turn("everyone=everyone", ("Priority", "non-urgent")), [built_in()])
    assert got == ("wake", "carry call:everyone", "built-in", False)


def test_a_non_urgent_voice_turn_that_calls_nobody_rides():
    """P-8's other half: with no call, the Priority line decides."""
    got = decide(turn(f"ivan@{HOST}=rule", ("Priority", "non-urgent")), [built_in()])
    assert got == ("ride", "ride priority:non-urgent", "built-in", False)


def test_a_draft_is_still_ride_star_alone_whatever_calls_it():
    """P-5 is untouched by P-8: a draft's built-in layer is `ride *` alone."""
    facts = turn(f"ivan@{HOST}=call", ("Priority", "non-urgent"))
    draft = Facts(facts.headers, facts.priority, facts.calls, draft=True)
    assert decide(draft, [built_in()]) == ("ride", "ride *", "built-in", False)


# ── the `set` grammar: parse_settings is pure ───────────────────────────────

def test_parse_settings_reads_the_two_names():
    got, errors = parse_settings(
        "set operator boss@*\n"
        "set urgent $operator alerts@hq eve@*   # the list\n", "locked")
    assert errors == ()
    assert got == Settings(operator=("boss@*",), urgent=("$operator", "alerts@hq", "eve@*"))


def test_parse_settings_ignores_every_line_that_is_not_a_set_line():
    """Rules and `focus` lines are the rules parser's and the focus
    parser's: `parse_settings` neither reads nor names them."""
    got, errors = parse_settings(
        "wake! from:$operator\nfocus sleep\nnonsense here\nset urgent eve@*\n", "locked")
    assert errors == () and got == Settings(urgent=("eve@*",))


def test_no_set_lines_is_the_empty_settings():
    got, errors = parse_settings("wake! from:$operator\n", "locked")
    assert errors == () and got == Settings()
    assert (got.operator, got.urgent) == (None, None)


@pytest.mark.parametrize("line, why", [
    ("set", "name"),                                    # no name
    ("set eve eve@*", "unknown"),                       # an unknown set name
    ("set urgent", "at least one"),                     # nobody named
    ("set operator", "at least one"),
    ("set operator $operator", "$operator"),                        # circular
    ("set urgent $urgent", "$urgent"),                  # circular
    ("set urgent $me", "$me"),                          # a seat's, never host-wide
    ("set urgent $operator*", "whole"),                     # a partial variable
    ("set urgent <runner>@<host>", "<host>"),           # the design's placeholder
    ("set urgent from:eve@*", "condition"),             # rule syntax, not a glob
    ("set subject-shorten maybe", "on or off"),         # SM-538: a switch is on|off
    ("set subject-shorten", "on or off"),
    ("set subject-shorten on off", "on or off"),
    ("set subject-shorten ON", "on or off"),            # exact words: a typo never turns it on
    ("set subject-shorten-max ten", "whole number above 0"),   # SM-792: a count is digits
    ("set subject-shorten-max -5", "whole number above 0"),    # negative
    ("set subject-shorten-max 0", "whole number above 0"),     # zero
    ("set subject-shorten-max 1.5", "whole number above 0"),   # not whole
    ("set subject-shorten-max +5", "whole number above 0"),    # digits only
    ("set subject-shorten-max", "whole number above 0"),       # no value
    ("set subject-shorten-max 10 20", "whole number above 0"),  # one word
])
def test_a_bad_set_line_is_named_with_file_and_line_and_skipped(line, why):
    got, errors = parse_settings("# the locked file\n" + line + "\n", "/etc/rules.locked")
    assert got == Settings()
    assert len(errors) == 1
    err = errors[0]
    assert (err.source, err.line, err.text) == ("/etc/rules.locked", 2, line)
    assert why in err.reason
    assert str(err).startswith("/etc/rules.locked:2: ")


def test_a_bad_set_line_costs_only_that_line():
    got, errors = parse_settings("set eve x\nset urgent eve@*\n", "locked")
    assert got == Settings(urgent=("eve@*",))
    assert [e.line for e in errors] == [1]


def test_a_second_set_of_one_name_is_named_and_the_first_stands():
    """As a focus file's second mode line (O-c): one line a name, the first
    stands, so a later line cannot quietly undo the one above it."""
    got, errors = parse_settings("set urgent eve@*\nset urgent *\n", "locked")
    assert got == Settings(urgent=("eve@*",))
    assert [(e.line, "first" in e.reason) for e in errors] == [(2, True)]


# ── `set` is a line of the LOCKED file only ─────────────────────────────────

def test_parse_names_a_set_line_as_a_line_of_the_locked_file_only():
    """In any rules text `parse` is handed (session, seat, env), `set` is a
    bad line with its own reason, not a rule and not a setting."""
    got, errors = parse("wake to:$me\nset urgent eve@*\nride *\n", "/home/x/seats/ivan/rules")
    assert [r.text for r in got] == ["wake to:$me", "ride *"]
    assert len(errors) == 1
    assert (errors[0].source, errors[0].line) == ("/home/x/seats/ivan/rules", 2)
    assert "locked" in errors[0].reason


@pytest.mark.parametrize("where", ["session", "seat", "session focus", "seat focus", "env"])
def test_set_urgent_outside_the_locked_file_is_named_and_does_not_widen(roots, where):
    """The slice's criterion: no agent can put itself on the host-wide list."""
    line = "set urgent eve@*\n"
    env: dict[str, str] = {}
    if where == "session":
        path = roots.write(roots.session_file(), line)
    elif where == "seat":
        path = roots.write(roots.seat_file(), line)
    elif where == "session focus":
        path = roots.write(roots.session_file("focus"), line)
    elif where == "seat focus":
        path = roots.write(roots.seat_file("focus"), line)
    else:
        env = {PAGE_RULES_VAR: line}
        path = PAGE_RULES_VAR
    layers = roots.load(env)
    assert layers.settings == SC6_SETTINGS
    assert [(e.source, e.line) for e in layers.errors] == [(str(path), 1)]
    assert "locked" in layers.errors[0].reason
    assert decide(urgent_from(EVE), layers) == ("ride", "ride *", "built-in", False)


def test_set_judy_in_a_seat_file_does_not_make_the_seat_judy(roots):
    roots.write(roots.seat_file(), "set operator eve@*\n")
    layers = roots.load()
    assert layers.settings == SC6_SETTINGS and len(layers.errors) == 1
    assert decide(mail(("From", EVE), SUBSCRIBED), layers)[:2] == ("ride", "ride *")


# ── the locked file's settings, through load_layers and judge ───────────────

def test_absent_set_lines_give_the_defaults(roots, alert_runner):
    roots.write_locked("wake from:ops@*\n")
    layers = roots.load()
    assert layers.settings == RUNNER_SETTINGS and layers.errors == ()
    assert decide(urgent_from(ALERTS), layers) == (
        "wake", "carry priority:urgent from:$urgent", "built-in", True)
    assert decide(urgent_from(EVE), layers) == ("ride", "ride *", "built-in", False)
    assert decide(mail(("From", JUDY), SUBSCRIBED), layers)[:2] == ("wake", "carry! from:$operator")


def test_no_locked_file_gives_the_defaults(roots, alert_runner):
    layers = roots.load()
    assert layers.settings == RUNNER_SETTINGS
    assert urgent_senders((HOST, SHORT), settings=layers.settings) == (
        "judy@*", f"{RUNNER}@{HOST}", f"{RUNNER}@{SHORT}")


def test_set_urgent_in_the_locked_file_widens_who_is_honoured(roots):
    roots.write_locked(f"set urgent $operator {RUNNER}@* eve@*\n")
    layers = roots.load()
    assert layers.errors == ()
    assert decide(urgent_from(EVE), layers) == (
        "wake", "carry priority:urgent from:$urgent", "built-in", True)


def test_set_urgent_in_the_locked_file_narrows_who_is_honoured(roots, alert_runner):
    """`set urgent` REPLACES the default: the runner left off is off, though
    the host's mail settings name it (the key is not consulted)."""
    roots.write_locked("set urgent $operator\n")
    layers = roots.load()
    assert layers.settings.runner == RUNNER
    assert decide(urgent_from(ALERTS), layers) == ("ride", "ride *", "built-in", False)
    # Judy is still honoured, and her urgent is at once
    assert decide(urgent_from(JUDY), layers) == ("wake", "carry! from:$operator", "built-in", True)


def test_set_lines_are_data_never_rules(roots):
    """A `set` line is not tried in the `wake!` pass or the first-match
    pass: the locked layer holds the rules around it, at their own line
    numbers, and nothing else."""
    roots.write_locked("set urgent eve@*\nride from:ops@*\nset judy boss@*\n")
    layers = roots.load()
    locked = layers[0]
    assert locked.name == "locked"
    assert [(r.text, r.line) for r in locked.rules] == [("ride from:ops@*", 2)]
    assert layers.errors == ()


def test_set_judy_changes_who_the_built_in_wake_bang_matches(roots):
    roots.write_locked("set operator boss@*\n")
    layers = roots.load()
    assert decide(mail(("From", BOSS), SUBSCRIBED, ("Priority", "non-urgent")), layers) == (
        "wake", "carry! from:$operator", "built-in", False)
    # the old default is not Judy any more: through a subscription it rides
    assert decide(mail(("From", JUDY), SUBSCRIBED), layers) == ("ride", "ride *", "built-in", False)


def test_set_judy_is_what_dollar_judy_means_in_every_layer(roots):
    roots.write_locked("set operator boss@*\n")
    roots.write(roots.seat_file(), "ride from:$operator\n")  # a ride never beats wake!
    layers = roots.load()
    assert decide(mail(("From", BOSS), SUBSCRIBED), layers)[:3] == (
        "wake", "carry! from:$operator", "built-in")


def test_set_judy_flows_into_the_default_urgent_list(roots, alert_runner):
    """The urgent default is `$operator <runner>@<host>`: `$operator` is read from
    the ONE value, so `set operator` alone moves both."""
    roots.write_locked("set operator boss@*\n")
    settings = roots.load().settings
    assert urgent_senders((HOST,), settings=settings) == ("boss@*", f"{RUNNER}@{HOST}")


def test_dollar_judy_in_set_urgent_expands_to_set_judy(roots):
    roots.write_locked("set urgent $operator eve@*\nset judy boss@* chief@*\n")
    settings = roots.load().settings
    assert urgent_senders((HOST,), settings=settings) == ("boss@*", "chief@*", "eve@*")
    assert operator(settings) == ("boss@*", "chief@*")


def test_a_bad_set_line_in_the_locked_file_is_named_by_load_layers(roots):
    roots.write_locked("wake from:ops@*\nset eve x\nfocus nap\n")
    layers = roots.load()
    assert [(e.source, e.line) for e in layers.errors] == [
        (str(roots.locked), 2), (str(roots.locked), 3)]
    assert [r.text for r in layers[0].rules] == ["wake from:ops@*"]


def test_a_seat_rule_still_widens_for_itself_under_a_narrowed_list(roots):
    """A2 (b) is unchanged by `set`: a seat widens the list for ITSELF."""
    roots.write_locked("set urgent $operator\n")
    roots.write(roots.seat_file(), "wake priority:urgent from:eve@*\n")
    layers = roots.load()
    assert decide(urgent_from(EVE), layers) == (
        "wake", "wake priority:urgent from:eve@*", "seat", True)


# ── the readers: operator() and urgent_senders() ────────────────────────────────

def test_the_readers_with_no_settings_read_the_locked_file(roots, monkeypatch):
    """`operator()` and `urgent_senders()` bare are the ONE readers SM-442's
    `pager say` pre-check calls: they read the locked file (never the env)."""
    monkeypatch.setattr(rules, "locked_path", lambda: roots.locked)
    assert operator() == ("judy@*",)
    roots.write_locked("set operator boss@*\nset urgent $operator ops@*\n")
    assert operator() == ("boss@*",)
    assert urgent_senders((HOST,)) == ("boss@*", "ops@*")


def test_the_readers_ignore_a_home_named_by_the_environment(roots, monkeypatch, tmp_path):
    """The locked file is never read from the environment: a
    `$SESSIONMAIL_HOME` or `$HOME` holding a `rules.locked` is not read."""
    fake = tmp_path / "fake-home"
    for where in (fake / ".sessionmail", fake):
        roots.write(where / "rules.locked", "set operator eve@*\n")
    monkeypatch.setenv("SESSIONMAIL_HOME", str(fake / ".sessionmail"))
    monkeypatch.setenv("HOME", str(fake))
    assert operator() == ("judy@*",)
    assert load_layers("ivan", SID, {"SESSIONMAIL_HOME": str(fake / ".sessionmail"),
                                     "HOME": str(fake)}).settings == SC6_SETTINGS


def test_judge_reads_the_settings_the_layers_carry_and_is_pure(roots, monkeypatch):
    """`judge` does no I/O: it reads the `Layers.settings` it is handed (or
    an explicit `settings=`), and with plain layers it uses the defaults,
    never the locked file on disk."""
    monkeypatch.setattr(rules, "locked_path", lambda: roots.locked)
    roots.write_locked("set urgent eve@*\n")
    plain = [built_in()]
    assert decide(urgent_from(EVE), plain)[:2] == ("ride", "ride *")
    got = judge(urgent_from(EVE), plain, ME, NOW, settings=Settings(urgent=("eve@*",)))
    assert (got.action, got.at_once) == ("wake", True)


# ── SM-538: `set subject-shorten on|off`, the shortener's HOST switch ────────

@pytest.mark.parametrize("word, value", [("on", True), ("off", False)])
def test_set_subject_shorten_parses_to_a_bool(word, value):
    got, errors = parse_settings(f"set subject-shorten {word}   # the host\n", "locked")
    assert errors == () and got == Settings(subject_shorten=value)
    assert got.subject_shorten is value


def test_subject_shorten_sits_beside_the_glob_settings():
    got, errors = parse_settings("set urgent eve@*\nset subject-shorten on\nset operator boss@*\n",
                                 "locked")
    assert errors == ()
    assert got == Settings(operator=("boss@*",), urgent=("eve@*",), subject_shorten=True)


def test_no_subject_shorten_line_is_none_so_the_tools_default_stands():
    got, errors = parse_settings("set urgent eve@*\n", "locked")
    assert errors == () and got.subject_shorten is None


def test_a_second_subject_shorten_line_cannot_turn_it_on():
    got, errors = parse_settings("set subject-shorten off\nset subject-shorten on\n", "locked")
    assert got.subject_shorten is False
    assert [(e.line, "first" in e.reason) for e in errors] == [(2, True)]


def test_no_locked_file_reads_the_shortener_as_unset(roots, monkeypatch):
    monkeypatch.setattr(rules, "locked_path", lambda: roots.locked)
    assert not roots.locked.exists()
    assert rules.read_settings().subject_shorten is None
    roots.write_locked("set subject-shorten on\n")
    assert rules.read_settings().subject_shorten is True


def test_set_subject_shorten_in_a_seat_file_is_named_and_sets_nothing(roots):
    """Only the locked file can turn the shortener on: a seat's own rules
    file is a layer an agent can write, so its `set` line is skipped."""
    roots.write(roots.seat_file(), "set subject-shorten on\n")
    layers = roots.load()
    assert layers.settings == SC6_SETTINGS and len(layers.errors) == 1


# ── SM-792: `set subject-shorten-max N`, the shortener's HOST cap ───────────

@pytest.mark.parametrize("word, value", [("10", 10), ("1", 1), ("24", 24), ("010", 10)])
def test_set_subject_shorten_max_parses_to_an_int(word, value):
    got, errors = parse_settings(f"set subject-shorten-max {word}   # the host\n", "locked")
    assert errors == () and got == Settings(subject_shorten_max=value)
    assert rules.shorten_max(got) == value


def test_no_subject_shorten_max_line_is_the_default_10():
    got, errors = parse_settings("set subject-shorten on\n", "locked")
    assert errors == () and got.subject_shorten_max is None
    assert rules.shorten_max(got) == rules.DEFAULT_SHORTEN_MAX == 10


def test_a_bad_cap_line_sets_nothing_and_the_default_stands():
    got, errors = parse_settings("set subject-shorten on\nset subject-shorten-max -5\n", "locked")
    assert got == Settings(subject_shorten=True) and rules.shorten_max(got) == 10
    assert [str(e) for e in errors] == [
        "locked:2: 'set subject-shorten-max' takes one whole number above 0 (got '-5'; "
        "the default 10 applies; to never shorten, 'set subject-shorten off'): "
        "set subject-shorten-max -5"]


@pytest.mark.parametrize("word", ["0", "-5", "ten"])
def test_a_bad_cap_line_says_0_is_not_never_and_names_the_off_switch(word):
    """0 and negatives are INVALID, the
    default 10 applies, and the way to never shorten is the OFF switch."""
    _, errors = parse_settings(f"set subject-shorten-max {word}\n", "locked")
    assert len(errors) == 1
    assert "the default 10 applies" in errors[0].reason
    assert "to never shorten, 'set subject-shorten off'" in errors[0].reason


def test_a_second_cap_line_cannot_raise_it():
    got, errors = parse_settings(
        "set subject-shorten-max 5\nset subject-shorten-max 50\n", "locked")
    assert rules.shorten_max(got) == 5
    assert [(e.line, "first" in e.reason) for e in errors] == [(2, True)]


def test_the_cap_is_read_from_the_locked_file_and_named_when_bad(roots, monkeypatch):
    monkeypatch.setattr(rules, "locked_path", lambda: roots.locked)
    assert rules.shorten_max() == 10, "no locked file: the default"
    roots.write_locked("set subject-shorten-max 24\n")
    assert rules.shorten_max() == 24
    roots.write_locked("# host\nset subject-shorten-max 0\n")
    settings, errors = rules.read_settings_named()
    assert rules.shorten_max(settings) == 10 and rules.read_settings() == settings
    assert [(e.source, e.line) for e in errors] == [(str(roots.locked), 2)]


def test_set_subject_shorten_max_in_a_seat_file_is_named_and_sets_nothing(roots):
    """A seat can never raise the cap: its own rules file is a layer an agent
    can write, so its `set` line is skipped, named, and the host's stands."""
    roots.write(roots.seat_file(), "set subject-shorten-max 100\n")
    layers = roots.load()
    assert layers.settings == SC6_SETTINGS and len(layers.errors) == 1
    assert rules.shorten_max(layers.settings) == 10


def test_set_subject_shorten_max_in_the_page_rules_variable_sets_nothing(roots):
    layers = roots.load({rules.PAGE_RULES_VAR: "set subject-shorten-max 100"})
    assert rules.shorten_max(layers.settings) == 10 and len(layers.errors) == 1
