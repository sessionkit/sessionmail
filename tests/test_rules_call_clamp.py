"""SM-782: an UNVERIFIED call may do nothing beyond what plain mail can already do.

The condition SM-782's design was approved on:
unverified voice (an unsigned mail From the keeper's address) still PAGES on its
calls, behind `_voice.UNVERIFIED_CALLS`, but a line keyed on `call:` whose verb
is `start`, `wake!` or `carry!` is CLAMPED to `carry` for it: no start, no
breakthrough, whatever a seat-written rule says. Those verbs on `call:` are the
trusted path's only. The built-in `carry call:$me` and `carry call:everyone`
keep working.

A breakthrough is what passes a focus mode and every layer's `ride *`, so a
clamped line is judged in the second pass, and never above the focus layer.

Pure: `Facts` are built by hand, the layers parsed from text. Nothing is read.
"""

from __future__ import annotations

import pytest

from sessionmail import _voice, rules
from sessionmail.rules import CLAMP_NOTE, Facts, Layer, Me, built_in, judge, parse

ME = Me.of(seat="eve", host="hq")
CALLED = ("eve@hq",)


def facts(voice: str = _voice.UNVERIFIED, calls: tuple[str, ...] = CALLED, **headers) -> Facts:
    hdrs = {"from": "Ears <heard+airpods@hq>", "subject": "hey eve", **headers}
    return Facts(hdrs, "normal", calls, voice=voice)


def layer(name: str, text: str) -> Layer:
    parsed, errors = parse(text, name)
    assert not errors, errors
    return Layer(name, parsed)


def verdict(f: Facts, *layers: Layer) -> tuple[str, str | None, str]:
    v = judge(f, [*layers, built_in()], ME)
    return v.action, v.rule, v.layer


def clamped(line: str) -> str:
    return f"{line} {CLAMP_NOTE}"


# == the built-in lines keep working ===========================================


@pytest.mark.parametrize("calls,rule", [(CALLED, "carry call:$me"),
                                        (("everyone",), "carry call:everyone")])
def test_an_unverified_call_pages_through_the_built_in_carry_lines(calls, rule):
    v = judge(facts(calls=calls), [built_in()], ME)
    assert (v.action, v.rule, v.layer, v.carry) == ("wake", rule, rules.BUILT_IN, True)


def test_an_unverified_mail_that_calls_no_one_rides():
    assert verdict(facts(calls=())) == ("ride", "ride *", rules.BUILT_IN)


# == start / wake! / carry! on call: are clamped to carry ======================


def test_a_seat_start_on_call_is_clamped_to_carry():
    seat = layer("seat", "start call:$me\n")
    assert verdict(facts(), seat) == ("wake", clamped("start call:$me"), "seat")
    v = judge(facts(), [seat, built_in()], ME)
    assert v.carry is True                      # a carry: the wake turn runs the ride-along


def test_on_trusted_voice_a_seat_start_on_call_starts():
    seat = layer("seat", "start call:$me\n")
    assert verdict(facts(_voice.TRUSTED), seat) == ("start", "start call:$me", "seat")


@pytest.mark.parametrize("verb", ["wake!", "carry!"])
def test_an_unverified_breakthrough_on_call_does_not_break_through(verb):
    """Trusted, the seat's `wake!`/`carry!` beats the session's `ride *` (the
    breakthrough pass). Unverified, it is a `carry` in the seat layer, BELOW
    the session layer, so the session's `ride *` decides, as for plain mail."""
    session = layer("session", "ride *\n")
    seat = layer("seat", f"{verb} call:$me\n")
    assert verdict(facts(_voice.TRUSTED), session, seat) == ("wake", f"{verb} call:$me", "seat")
    assert verdict(facts(), session, seat) == ("ride", "ride *", "session")


@pytest.mark.parametrize("verb", ["wake!", "carry!", "start"])
def test_with_nothing_above_it_a_clamped_line_carries_from_its_place(verb):
    seat = layer("seat", f"{verb} call:$me\n")
    assert verdict(facts(), seat) == ("wake", clamped(f"{verb} call:$me"), "seat")


def test_the_clamp_holds_for_a_call_on_everyone_too():
    seat = layer("seat", "start call:everyone\n")
    assert verdict(facts(calls=("everyone",)), seat) == (
        "wake", clamped("start call:everyone"), "seat")


@pytest.mark.parametrize("line", ["wake call:$me", "carry call:$me", "ride call:$me"])
def test_verbs_within_plain_mail_are_not_touched(line):
    seat = layer("seat", f"{line}\n")
    action = "ride" if line.startswith("ride") else "wake"
    assert verdict(facts(), seat) == (action, line, "seat")


def test_a_breakthrough_not_keyed_on_call_is_not_clamped():
    """`wake! subject:hey*` breaks through for ANY mail with that subject,
    plain mail included, so an unverified voice mail gets no more by it."""
    session = layer("session", "ride *\n")
    seat = layer("seat", "wake! subject:hey*\n")
    assert verdict(facts(), session, seat) == ("wake", "wake! subject:hey*", "seat")


def test_a_hand_built_facts_with_calls_is_clamped_fail_closed():
    f = Facts({"from": "x@hq"}, "normal", CALLED)        # no voice state: PLAIN
    assert f.voice == _voice.PLAIN
    seat = layer("seat", "start call:$me\n")
    assert verdict(f, seat) == ("wake", clamped("start call:$me"), "seat")


def test_a_clamped_condition_still_has_to_match():
    seat = layer("seat", "start call:$me\n")
    assert verdict(facts(calls=("ivan@hq",)), seat) == ("ride", "ride *", rules.BUILT_IN)


# == never above a focus mode ==================================================


def _asleep() -> Layer:
    return Layer("focus: sleep", parse("ride *\n", "focus")[0], focus="session")


def test_asleep_the_locked_voice_breakthrough_does_not_pass_for_an_unverified_call():
    locked = layer("locked", "wake! call:$me\nwake! call:everyone\n")
    assert verdict(facts(_voice.TRUSTED), locked, _asleep()) == (
        "wake", "wake! call:$me", "locked")
    assert verdict(facts(), locked, _asleep()) == ("ride", "ride *", "focus: sleep")


def test_asleep_even_a_locked_start_on_call_is_held_below_the_mode():
    locked = layer("locked", "start call:$me\n")
    assert verdict(facts(), locked, _asleep()) == ("ride", "ride *", "focus: sleep")


def test_asleep_a_locked_line_within_plain_mail_keeps_its_place():
    """The clamp moves only the lines it clamps: a locked `carry call:$me`
    is the operator's own line above the mode, for every mail it matches."""
    locked = layer("locked", "carry call:$me\n")
    assert verdict(facts(), locked, _asleep()) == ("wake", "carry call:$me", "locked")


def test_awake_a_locked_breakthrough_carries_from_the_locked_layer():
    locked = layer("locked", "wake! call:$me\n")
    session = layer("session", "ride *\n")
    assert verdict(facts(), locked, session) == ("wake", clamped("wake! call:$me"), "locked")


def test_the_layers_handed_in_are_not_changed():
    seat = layer("seat", "start call:$me\n")
    layers = [seat, built_in()]
    judge(facts(), layers, ME)
    assert layers[0].rules[0].verb == "start" and layers[0].rules[0].text == "start call:$me"
