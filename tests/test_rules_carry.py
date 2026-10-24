"""`carry` and `carry!`, the rules half of wake carry: SM-640 do-002.

SM-632 option D (findings/2030-12-08-wake-carry-design.md, Q1, E6, E10, E11),
built on a branch as ruled:

- R1 `carry` and `carry!` are verbs. A carry IS a wake (`Rule.action` is
  `wake`), `carry!` is a breakthrough as `wake!` is, and `Verdict.carry` says
  the wake turn may run the ride-along.
- R2 `carry *` (E6) and `carry draft:yes` (E11) are bad lines, named.
- R3 the built-in lines: `carry! from:$operator`, `carry call:$me`,
  `carry call:everyone`, `carry priority:urgent from:$urgent`; `wake to:$me`
  stays a wake.
- R4 `set carry off` in the locked file only: every carry judges as a plain
  wake. Default on. R8's `set carry-prompt on|off` parses beside it, default
  off; the hook reads it (do-003).
- E10: a mail that could not be judged wakes but never carries.

SM-656 (a wake carries) changes R1-R3 to the ruling, and the tests that pinned the old
reading say so where they stand:

- R1 `wake` judges as `carry` and `wake!` as `carry!`; the carry verbs stay
  valid, as synonyms.
- R2 (E11) a draft never carries, whatever line wakes it: kept in `judge`.
- R3 `set carry off` turns every wake back into a plain wake.
- R4 `carry *` is no longer refused: it is `wake *`. `carry draft:yes` is no
  longer refused either: it is `wake draft:yes` (valid today, so a refusal
  of the wake form would break live seats' rules files).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import RUNNER, RUNNER_SETTINGS

from sessionmail import pager, rules
from sessionmail.rules import (
    BUILT_IN_TEXT,
    LOCKED_ONLY,
    PAGE_RULES_VAR,
    Facts,
    Layer,
    Layers,
    Me,
    Settings,
    built_in,
    carry_on,
    carry_prompt_on,
    judge,
    load_layers,
    parse,
    parse_settings,
)

SID = "9d0c6c51-74a4-97fc-f6a6-c3c350e58ad9"
HOST = "hq.tailnet.example"
ME = Me.of(session=SID, seat="ivan", host=HOST)
NOW = datetime(2030, 12, 8, 12, 34, 56, tzinfo=timezone.utc)
ENV = {"SESSIONMAIL_HOST": "hq", "MAGICDNS_SUFFIX": "tailnet.example"}

JUDY = f"Judy <judy@{HOST}>"
EVE = f"Eve <eve@{HOST}>"
#: The alert runner, From its configured local part (`conftest.RUNNER`, SM-798).
ALERTS = f"Alerts <{RUNNER}@{HOST}>"
KEEPER = f"Ears <heard+phone@{HOST}>"
SUBSCRIBED = ("X-Original-To", "atomnote")


def mail(*headers: tuple[str, str]) -> Facts:
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return Facts.from_bytes((head + "\n").encode(), env=ENV)


def turn(rule: str) -> Facts:
    """A trusted voice turn, as the keeper writes one (as in do-013's tests)."""
    return mail(("From", KEEPER), ("X-Original-To", "heard"),
                ("Ears-Kind", "spoken"), ("Ears-Rule", rule))


def layer(name: str, text: str) -> Layer:
    got, errors = parse(text, name)
    assert errors == ()
    return Layer(name, got)


def decide(facts: Facts, layers, settings: Settings | None = None):
    """Judged on the SC6 host (`$operator` is `judy@*`) with an alert runner
    (`RUNNER`, SM-798) unless SETTINGS says otherwise (SM-727: plain layers
    read no config)."""
    got = judge(facts, layers, ME, NOW, settings=RUNNER_SETTINGS if settings is None else settings)
    return got.action, got.rule, got.layer, got.at_once, got.carry


# ── R1: the verbs ─────────────────────────────────────────────────────────────

def test_the_verbs_are_the_four_ruled_then_the_two_carries():
    assert rules.VERBS == ("start", "wake", "ride", "wake!", "carry", "carry!")


@pytest.mark.parametrize("line, verb, breakthrough", [
    ("carry from:eve@*", "carry", False),
    ("carry! from:eve@*", "carry!", True),
])
def test_a_carry_is_a_wake_that_carries(line, verb, breakthrough):
    (rule,), errors = parse(line, "session")
    assert errors == ()
    assert (rule.verb, rule.action, rule.breakthrough, rule.carries) == \
        (verb, "wake", breakthrough, True)


@pytest.mark.parametrize("line, verb, breakthrough", [
    ("wake from:eve@*", "wake", False),
    ("wake! from:eve@*", "wake!", True),
])
def test_a_wake_carries_as_a_carry_does(line, verb, breakthrough):
    """SM-656 R1: a wake carries. Was
    `test_no_other_verb_carries`, which pinned `wake` and `wake!` False."""
    (rule,), errors = parse(line, "session")
    assert errors == ()
    assert (rule.verb, rule.action, rule.breakthrough, rule.carries) == \
        (verb, "wake", breakthrough, True)


@pytest.mark.parametrize("line", ["ride from:eve@*", "start from:eve@*"])
def test_no_verb_but_a_wake_carries(line):
    (rule,), _ = parse(line, "session")
    assert rule.carries is False


@pytest.mark.parametrize("line", ["Carry from:eve@*", "CARRY! from:eve@*", "carry!! from:x"])
def test_the_verb_is_lower_case_and_exact(line):
    got, (error,) = parse(line, "session")
    assert got == () and "unknown verb" in error.reason


def test_a_carry_line_judges_wake_and_carries():
    got = decide(mail(("From", EVE), SUBSCRIBED), [layer("session", "carry from:eve@*\n")])
    assert got == ("wake", "carry from:eve@*", "session", False, True)


def test_a_wake_line_carries():
    """SM-656 R1: the carry flag was False here."""
    got = decide(mail(("From", EVE), SUBSCRIBED), [layer("session", "wake from:eve@*\n")])
    assert got == ("wake", "wake from:eve@*", "session", False, True)


@pytest.mark.parametrize("wake, carry", [("wake", "carry"), ("wake!", "carry!")])
def test_wake_and_carry_judge_the_same_mail_the_same(wake, carry):
    """SM-656 R1: synonyms, so the verdicts differ only in the line named."""
    facts = mail(("From", EVE), SUBSCRIBED, ("Priority", "urgent"))
    a = decide(facts, [layer("session", f"{wake} priority:urgent from:eve@*\n")])
    b = decide(facts, [layer("session", f"{carry} priority:urgent from:eve@*\n")])
    assert (a[0], *a[2:]) == (b[0], *b[2:]) == ("wake", "session", True, True)


def test_a_ride_and_the_fallback_never_carry():
    assert decide(mail(("From", EVE)), [layer("session", "ride *\n")])[-1] is False
    assert decide(mail(("From", EVE)), [])[-1] is False


def test_carry_bang_breaks_through_a_ride_star_above_it():
    """D4 step 1 reads every breakthrough first, `carry!` with `wake!`."""
    layers = [layer("session", "ride *\n"), layer("seat", "carry! from:eve@*\n")]
    got = decide(mail(("From", EVE), SUBSCRIBED), layers)
    assert got == ("wake", "carry! from:eve@*", "seat", False, True)


def test_plain_carry_does_not_break_through_a_ride_star_above_it():
    layers = [layer("session", "ride *\n"), layer("seat", "carry from:eve@*\n")]
    got = decide(mail(("From", EVE), SUBSCRIBED), layers)
    assert got == ("ride", "ride *", "session", False, False)


def test_carry_bang_wakes_through_focus_sleep():
    sleep = Layer(rules.focus_name("sleep"), parse(rules.MODES["sleep"], "focus")[0],
                  focus=rules.FLEET)
    got = decide(mail(("From", EVE), SUBSCRIBED), [sleep, layer("seat", "carry! from:eve@*\n")])
    assert got[0] == "wake" and got[-1] is True


def test_a_wake_bang_above_a_carry_decides_and_carries():
    """The first matching breakthrough decides, so a `wake!` above a `carry!`
    for the same mail is the line named. It carries too: SM-656 R1 (a wake
    carries); before, it did not."""
    layers = [layer("session", "wake! from:eve@*\n"), layer("seat", "carry! from:eve@*\n")]
    got = decide(mail(("From", EVE), SUBSCRIBED), layers)
    assert got == ("wake", "wake! from:eve@*", "session", False, True)


def test_an_honoured_urgent_carry_is_at_once_too():
    """A4 reads `Rule.action`, which a carry keeps as `wake`."""
    session = layer("session", "carry priority:urgent from:eve@*\n")
    got = decide(mail(("From", EVE), SUBSCRIBED, ("Priority", "urgent")), [session])
    assert got == ("wake", "carry priority:urgent from:eve@*", "session", True, True)


# ── SM-656 R4: the two SM-640 refusals are gone; each line is its wake ───────

@pytest.mark.parametrize("line, wake", [("carry *", "wake *"), ("carry! *", "wake! *"),
                                        ("carry   *   # every mail", "wake *")])
def test_carry_star_is_wake_star(line, wake):
    """SM-656 R4: was refused by name (E6). Every
    wake carries, so `carry *` IS `wake *`, and the two never disagree."""
    (rule,), errors = parse(line, "session")
    (twin,), _ = parse(wake, "session")
    assert errors == () and rule.matches_all and twin.matches_all
    assert (rule.action, rule.breakthrough, rule.carries) == \
        (twin.action, twin.breakthrough, twin.carries) == ("wake", wake.startswith("wake!"), True)
    facts = mail(("From", EVE), SUBSCRIBED)
    assert decide(facts, [Layer("session", (rule,))])[::2] == \
        decide(facts, [Layer("session", (twin,))])[::2] == ("wake", "session", True)


@pytest.mark.parametrize("line", ["carry draft:yes", "carry! from:judy@* draft:yes",
                                  "carry draft:YES call:$me"])
def test_carry_draft_yes_is_a_good_line_that_never_carries(line):
    """SM-656 (E11): was refused by name. It is `wake draft:yes` now, and a
    draft still never carries: the judgement keeps that, for every line."""
    (rule,), errors = parse(line, "session")
    assert errors == () and rule.carries
    facts = mail(("From", JUDY), SUBSCRIBED)
    draft = Facts(facts.headers, facts.priority, (f"ivan@{HOST}",), draft=True)
    got = decide(draft, [Layer("session", (rule,))])
    assert got[0] == "wake" and got[-1] is False


def test_carry_draft_no_is_a_good_line():
    (rule,), errors = parse("carry draft:no from:eve@*", "session")
    assert errors == () and rule.carries


@pytest.mark.parametrize("line", ["wake *", "wake! *", "ride *", "wake draft:yes",
                                  "wake! draft:yes call:$me"])
def test_the_wake_forms_were_always_good_lines(line):
    got, errors = parse(line, "session")
    assert errors == () and len(got) == 1


def test_a_carry_star_line_is_no_longer_skipped():
    """SM-656 R4: before, line 2 was a bad line, named and skipped."""
    got, errors = parse("wake from:a@*\ncarry *\nride from:b@*\n", "seat")
    assert [r.text for r in got] == ["wake from:a@*", "carry *", "ride from:b@*"]
    assert errors == ()


# ── R3: the built-in lines ───────────────────────────────────────────────────

def test_the_built_in_lines_carry_the_vouched_for_senders():
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


def test_judys_mail_carries_whatever_its_level():
    for level in ("non-urgent", "normal", "urgent"):
        got = decide(mail(("From", JUDY), SUBSCRIBED, ("Priority", level)), [built_in()])
        assert got[:3] == ("wake", "carry! from:$operator", "built-in") and got[-1] is True


def test_a_call_carries():
    assert decide(turn(f"ivan@{HOST}=call"), [built_in()])[1:] == \
        ("carry call:$me", "built-in", False, True)
    assert decide(turn("everyone=everyone"), [built_in()])[1:] == \
        ("carry call:everyone", "built-in", False, True)


def test_an_urgent_from_the_list_carries_at_once():
    got = decide(mail(("From", ALERTS), SUBSCRIBED, ("Priority", "urgent")), [built_in()])
    assert got == ("wake", "carry priority:urgent from:$urgent", "built-in", True, True)


def test_direct_mail_wakes_and_now_carries():
    """SM-640 E6 kept `wake to:$me` a plain wake. SM-656 R1 (a wake
    carries): the line is unchanged, and it carries."""
    got = decide(mail(("From", EVE), ("To", f"ivan@{HOST}")), [built_in()])
    assert got == ("wake", "wake to:$me", "built-in", False, True)


def test_a_draft_still_gets_ride_star_alone():
    facts = turn(f"ivan@{HOST}=call")
    draft = Facts(facts.headers, facts.priority, facts.calls, draft=True)
    assert decide(draft, [built_in()]) == ("ride", "ride *", "built-in", False, False)


# ── R4 and R8: the two switches, locked file only ────────────────────────────

@pytest.mark.parametrize("name, field", [("carry", "carry"), ("carry-prompt", "carry_prompt")])
@pytest.mark.parametrize("word, value", [("on", True), ("off", False)])
def test_the_carry_switches_parse_to_bools(name, field, word, value):
    got, errors = parse_settings(f"set {name} {word}   # the host\n", "locked")
    assert errors == () and getattr(got, field) is value


@pytest.mark.parametrize("line", ["set carry maybe", "set carry", "set carry on off",
                                  "set carry OFF", "set carry-prompt yes",
                                  "set carry-prompt judy@*"])
def test_a_carry_switch_takes_exactly_on_or_off(line):
    got, (error,) = parse_settings(line + "\n", "locked")
    assert got == Settings() and "on or off" in error.reason


def test_no_line_leaves_carry_on_and_typed_prompt_carry_off():
    got, errors = parse_settings("set urgent eve@*\n", "locked")
    assert errors == () and (got.carry, got.carry_prompt) == (None, None)
    assert carry_on(got) is True
    assert carry_prompt_on(got) is False


def test_the_readers_follow_the_lines():
    assert carry_on(Settings(carry=False)) is False
    assert carry_on(Settings(carry=True)) is True
    assert carry_prompt_on(Settings(carry_prompt=True)) is True
    assert carry_prompt_on(Settings(carry_prompt=False)) is False


def test_a_second_carry_line_cannot_undo_the_first():
    got, errors = parse_settings("set carry off\nset carry on\n", "locked")
    assert got.carry is False
    assert [(e.line, "first" in e.reason) for e in errors] == [(2, True)]


def test_the_switches_sit_beside_the_other_settings():
    got, errors = parse_settings(
        "set carry off\nset urgent eve@*\nset carry-prompt on\nset subject-shorten on\n", "locked")
    assert errors == ()
    assert got == Settings(urgent=("eve@*",), subject_shorten=True, carry=False,
                           carry_prompt=True)


@pytest.mark.parametrize("line", ["set carry off", "set carry-prompt on"])
def test_a_carry_switch_outside_the_locked_file_is_a_bad_line(line):
    got, (error,) = parse(line + "\n", "session")
    assert got == () and error.reason == LOCKED_ONLY


def test_set_carry_off_judges_every_wake_as_a_plain_wake():
    """SM-656 R3: the kill switch turns a `wake` line back too."""
    off = Settings(carry=False)
    direct = mail(("From", EVE), ("To", f"ivan@{HOST}"))
    assert decide(direct, [built_in()], settings=off) == \
        ("wake", "wake to:$me", "built-in", False, False)
    bang = layer("session", "wake! from:eve@*\n")
    assert decide(mail(("From", EVE), SUBSCRIBED), [bang], settings=off)[-1] is False


def test_set_carry_off_judges_every_carry_as_a_plain_wake():
    off = replace(RUNNER_SETTINGS, carry=False)
    got = decide(mail(("From", JUDY), SUBSCRIBED), [built_in()], settings=off)
    assert got == ("wake", "carry! from:$operator", "built-in", False, False)
    urgent = mail(("From", ALERTS), SUBSCRIBED, ("Priority", "urgent"))
    assert decide(urgent, [built_in()], settings=off) == \
        ("wake", "carry priority:urgent from:$urgent", "built-in", True, False)


def test_carry_prompt_does_not_change_the_judgement():
    """R8's switch is the hook's to read; the verdict is the same either way."""
    facts = mail(("From", JUDY), SUBSCRIBED)
    assert decide(facts, [built_in()], settings=Settings(carry_prompt=True)) == \
        decide(facts, [built_in()], settings=Settings(carry_prompt=False))


class Roots:
    """A fake host: a sessionmail home and a locked file, both under tmp."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.locked = tmp / "etc" / "rules.locked"

    def write_locked(self, text: str) -> None:
        self.locked.parent.mkdir(parents=True, exist_ok=True)
        self.locked.write_text(text, encoding="utf-8")

    def load(self, env: dict[str, str] | None = None) -> Layers:
        return load_layers("ivan", SID, env or {}, home=self.home, locked=self.locked)


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


def test_the_locked_files_set_carry_off_reaches_the_judgement(roots):
    roots.write_locked("set carry off\n")
    layers = roots.load()
    assert layers.errors == () and layers.settings.carry is False
    got = judge(mail(("From", JUDY), SUBSCRIBED), layers, ME, NOW)
    assert (got.action, got.rule, got.carry) == ("wake", "carry! from:$operator", False)


def test_with_no_locked_file_carry_is_on(roots):
    got = judge(mail(("From", JUDY), SUBSCRIBED), roots.load(), ME, NOW)
    assert (got.action, got.carry) == ("wake", True)


def test_a_set_carry_line_in_the_env_layer_sets_nothing(roots):
    layers = roots.load({PAGE_RULES_VAR: "set carry off"})
    assert [e.reason for e in layers.errors] == [LOCKED_ONLY]
    got = judge(mail(("From", JUDY), SUBSCRIBED), layers, ME, NOW)
    assert got.carry is True


def test_read_settings_reads_both_switches(roots, monkeypatch):
    monkeypatch.setattr(rules, "locked_path", lambda: roots.locked)
    assert (carry_on(), carry_prompt_on()) == (True, False)
    roots.write_locked("set carry off\nset carry-prompt on\n")
    assert (carry_on(), carry_prompt_on()) == (False, True)


# ── E10: a mail that could not be judged wakes, never carries ────────────────

def test_an_unjudged_mail_wakes_but_does_not_carry():
    got = pager._unjudged()
    assert (got.action, got.layer, got.carry) == ("wake", pager.UNJUDGED, False)


def test_a_confirmation_note_never_carries():
    assert pager._note_verdict().carry is False


# ── SM-656 E11: a draft never carries, whatever line wakes it ────────────────

def _draft(facts: Facts) -> Facts:
    return Facts(facts.headers, facts.priority, facts.calls, draft=True)


@pytest.mark.parametrize("line", ["wake draft:yes", "wake! draft:yes", "wake from:eve@*",
                                  "wake! from:eve@*", "carry from:eve@*", "carry! *",
                                  "wake *"])
def test_a_draft_a_line_wakes_never_carries(line):
    """`wake draft:yes` was a good line before SM-656 and stays one (a parse
    error would break live seats' files); every wake carries, but not a
    draft's. The same mail, not a draft, carries by the same line."""
    session = layer("session", line + "\n")
    facts = mail(("From", EVE), SUBSCRIBED)
    if "draft:yes" not in line:
        assert decide(facts, [session, built_in()])[::4] == ("wake", True)
    got = decide(_draft(facts), [session, built_in()])
    assert (got[0], got[2], got[-1]) == ("wake", "session", False)


def test_a_draft_from_judy_wakes_by_a_seat_line_and_does_not_carry():
    """A seat's own line may wake on a draft (N11/N12); the built-in is
    `ride *` alone for one (P-5); and no line carries it (E11)."""
    facts = _draft(mail(("From", JUDY), SUBSCRIBED))
    assert decide(facts, [built_in()]) == ("ride", "ride *", "built-in", False, False)
    seat = layer("seat", "wake! from:$operator\n")
    assert decide(facts, [seat, built_in()]) == \
        ("wake", "wake! from:$operator", "seat", False, False)
