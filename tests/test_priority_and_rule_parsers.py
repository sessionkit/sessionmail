"""tasks.md 3.13 (the `Priority:` parse) and 3.21 (the `Ears-Rule` parser and
the calls feed): the pure parsers SM-474's `pager-filters` consumes.

Proves, for the parse half only (the judgement is SM-474's):

  - *The sender states a Priority, and the receiver's rules pick the action*:
    one header, `Priority:`, values `non-urgent`, `normal`, `urgent`; a
    missing header or any other value is `normal`.
  - *Only "hey" wakes: a call wakes the session it calls*: entries compared
    whole, the key's domain case-insensitively and its local part exactly,
    never as a glob; and *A call on mail that is not trusted voice is no
    call*: the calls feed is empty unless 3.1 says trusted voice.

Pure: messages are built from bytes here; the host forms come from an
explicit env. Nothing reads ~/.mail, ~/.sessionmail or notmuch.
"""

from __future__ import annotations

from email.parser import BytesParser
from email.policy import default as default_policy

import pytest

from conftest import KEEPER_SIGNED
from sessionmail import _parsers, _voice
from sessionmail._parsers import (
    EVERYONE,
    LEVELS,
    Entry,
    calls,
    parse_rule,
    priority,
    read_rule,
    same_address,
)

ENV = {"SESSIONMAIL_HOST": "hq", "MAGICDNS_SUFFIX": "example.ts.net"}


def _msg(*fields: str, sender: str = "ivan@hq", body: str = "hello\n"):
    raw = "\r\n".join([f"From: {sender}", "To: eve@hq", "Subject: s"] + list(fields))
    raw += "\r\n\r\n" + body
    return BytesParser(policy=default_policy).parsebytes(raw.encode())


# == 3.13: the Priority parse ================================================


def test_the_three_levels_and_no_others():
    assert LEVELS == ("non-urgent", "normal", "urgent")


def test_priority_urgent_upper_case_is_urgent():
    p = priority(_msg("Priority: URGENT"))
    assert p.level == "urgent"
    assert p.stated is True
    assert p.reason is None
    assert p.value == "URGENT"


def test_priority_soon_is_normal_not_a_level():
    p = priority(_msg("Priority: soon"))
    assert p.level == "normal"
    assert p.stated is False
    assert p.reason == "not a level"
    assert p.value == "soon"


def test_no_priority_header_is_normal():
    p = priority(_msg())
    assert p.level == "normal"
    assert p.stated is False
    assert p.value is None
    # tasks.md 3.13 / design D11: a missing header is recorded "not a level",
    # as an unknown value is; `value` None is what tells them apart
    assert p.reason == "not a level"


def test_two_priority_headers_take_the_first_and_record_the_second():
    p = priority(_msg("Priority: non-urgent", "Priority: urgent"))
    assert p.level == "non-urgent"
    assert p.ignored == ("urgent",)


def test_non_urgent_with_spaces_is_trimmed():
    p = priority(_msg("Priority:  non-urgent  "))
    assert p.level == "non-urgent"
    assert p.stated is True


def test_an_explicit_normal_is_stated():
    p = priority(_msg("Priority: Normal"))
    assert p.level == "normal"
    assert p.stated is True
    assert p.reason is None


def test_an_empty_priority_is_not_a_level():
    p = priority(_msg("Priority:"))
    assert p.level == "normal"
    assert p.reason == "not a level"


def test_x_priority_is_a_different_header_and_never_read():
    # a mail client's own `X-Priority: 1 (Highest)` is not an old spelling
    p = priority(_msg("X-Priority: 1 (Highest)"))
    assert p.level == "normal"
    assert p.value is None


def test_the_header_name_is_case_insensitive():
    assert priority(_msg("priority: urgent")).level == "urgent"


def test_a_second_bad_value_does_not_rescue_the_first():
    p = priority(_msg("Priority: soon", "Priority: urgent"))
    assert p.level == "normal"
    assert p.reason == "not a level"
    assert p.ignored == ("urgent",)


# == 3.21: the Ears-Rule parser ==============================================


def test_eve_call_calls_eve_and_not_ivan():
    r = parse_rule("eve@hq=call")
    assert r.calls("eve@hq")
    assert not r.calls("ivan@hq")
    assert not r.everyone


def test_everyone_everyone():
    r = parse_rule("everyone=everyone")
    assert r.everyone
    assert not r.calls("eve@hq")  # everyone is its own answer, not a match


def test_a_mention_calls_nobody():
    r = parse_rule("eve@hq=mention")
    assert not r.calls("eve@hq")
    assert not r.everyone
    assert r.called() == ()


def test_evette_does_not_call_eve():
    r = parse_rule("evette@hq=call")
    assert not r.calls("eve@hq")
    assert r.calls("evette@hq")


def test_a_b_c_splits_on_the_first_equals():
    r = parse_rule("a=b=c")
    assert r.entries == (Entry("a", "b=c"),)


def test_spaces_around_entries_are_trimmed():
    r = parse_rule("  eve@hq=call ,  ivan@hq=mention,everyone=everyone  ")
    assert r.entries == (
        Entry("eve@hq", "call"),
        Entry("ivan@hq", "mention"),
        Entry("everyone", "everyone"),
    )
    assert r.calls("eve@hq")
    assert r.everyone


def test_the_domain_is_case_insensitive_and_the_local_part_exact():
    r = parse_rule("eve@HQ=call")
    assert r.calls("eve@hq")
    assert not r.calls("Eve@hq")
    assert not parse_rule("Eve@hq=call").calls("eve@hq")


def test_no_glob_over_the_value():
    # `*eve*` would match `evette@hq=call`; entries are compared whole
    r = parse_rule("evette@hq=call,xeve@hq=call")
    assert not r.calls("eve@hq")
    assert not r.calls("*eve*")


def test_the_whole_domain_is_compared():
    # the short host and the FQDN are different keys: SM-474's $me carries
    # the forms a session answers to; this answers for the address it is given
    assert not parse_rule("eve@hq=call").calls("eve@hq.example.ts.net")
    assert not parse_rule("eve=call").calls("eve@hq")
    assert parse_rule("eve=call").calls("eve")


def test_the_rule_word_is_exact():
    assert not parse_rule("eve@hq=CALL").calls("eve@hq")
    assert not parse_rule("eve@hq=calls").calls("eve@hq")
    assert not parse_rule("everyone=call").everyone


def test_empty_entries_and_an_entry_with_no_equals_are_kept_and_call_nobody():
    r = parse_rule("eve@hq, ,=call")
    assert r.entries == (Entry("eve@hq", ""), Entry("", "call"))
    assert r.called() == ()
    assert not r.calls("")


def test_an_empty_value_has_no_entries():
    assert parse_rule("").entries == ()
    assert parse_rule(None).entries == ()


def test_called_lists_the_call_keys_in_wire_order_once():
    r = parse_rule("eve@hq=call,ivan@hq=call,eve@hq=call,everyone=everyone")
    assert r.called() == ("eve@hq", "ivan@hq", EVERYONE)


def test_x_ears_rule_only_is_read_and_recorded():
    r = read_rule(_msg("X-Ears-Rule: eve@hq=call"))
    assert r.calls("eve@hq")
    assert r.spelling == "X-Ears-Rule"
    assert r.prefixed is True


def test_ears_rule_read_through_the_reader_the_unprefixed_wins():
    r = read_rule(_msg("X-Ears-Rule: eve@hq=call", "Ears-Rule: ivan@hq=call"))
    assert r.calls("ivan@hq")
    assert not r.calls("eve@hq")
    assert r.prefixed is False


def test_no_ears_rule_calls_nobody():
    r = read_rule(_msg())
    assert r.entries == ()
    assert r.spelling is None


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("eve@hq", "eve@hq", True),
        ("eve@hq", "eve@HQ", True),
        ("eve@hq", "Eve@hq", False),
        ("eve@hq", "eve@hq.example.ts.net", False),
        ("eve", "eve", True),
        ("eve", "eve@hq", False),
        ("eve+x@hq", "eve@hq", False),
    ],
)
def test_same_address(a, b, same):
    assert same_address(a, b) is same
    assert same_address(b, a) is same


# == 3.21: the calls feed (AGREED with SM-474) ====================


def test_a_call_on_mail_that_is_not_trusted_voice_is_no_call():
    # the spec's scenario, verbatim: From eve@hq, Ears-Kind, ivan@hq=call
    m = _msg("Ears-Kind: spoken", "Ears-Rule: ivan@hq=call", sender="eve@hq")
    assert calls(m, ENV) == ()


def test_a_trusted_turns_calls():
    m = _msg(
        "Ears-Kind: spoken",
        "Ears-Rule: eve@hq=call, ivan@hq=mention, everyone=everyone",
        sender="Ears <heard+airpods@hq>",
    )
    assert calls(m, ENV, KEEPER_SIGNED) == ("eve@hq", EVERYONE)


def test_a_trusted_turn_in_the_x_spelling_calls():
    m = _msg("X-Ears-Kind: spoken", "X-Ears-Rule: eve@hq=call", sender="heard@hq")
    assert calls(m, ENV, KEEPER_SIGNED) == ("eve@hq",)


def test_an_unverified_turn_calls_while_the_switch_is_on():
    # SM-782: unsigned keeper mail still pages on its calls ("hey <name>"), the
    # switch's default; the rules clamp what such a call may do.
    assert _voice.UNVERIFIED_CALLS is True
    m = _msg("Ears-Kind: spoken", "Ears-Rule: eve@hq=call, everyone=everyone",
             sender="Ears <heard+airpods@hq>")
    assert calls(m, ENV) == ("eve@hq", EVERYONE)


def test_strict_an_unverified_turn_calls_nobody(monkeypatch):
    # SM-782's ONE switch flipped: no calls from unverified voice until the
    # keeper signs; a trusted turn still calls.
    monkeypatch.setattr(_voice, "UNVERIFIED_CALLS", False)
    m = _msg("Ears-Kind: spoken", "Ears-Rule: eve@hq=call", sender="heard+airpods@hq")
    assert calls(m, ENV) == ()
    assert calls(m, ENV, KEEPER_SIGNED) == ("eve@hq",)


def test_a_recognition_already_made_is_used_as_given():
    m = _msg("Ears-Kind: spoken", "Ears-Rule: eve@hq=call", sender="eve@hq")
    plain = _voice.recognise(m, ENV)
    assert calls(m, ENV, voice=plain) == ()


def test_the_keeper_without_ears_kind_is_no_call():
    m = _msg("Ears-Rule: eve@hq=call", sender="heard+airpods@hq")
    assert calls(m, ENV) == ()


def test_the_ceiling_is_not_part_of_trust():
    # a long turn is cut when given (3.1), but its calls still count
    m = _msg("Ears-Kind: spoken", "Ears-Rule: eve@hq=call",
             sender="heard+airpods@hq", body="z" * 40_000)
    assert calls(m, ENV) == ("eve@hq",)


def test_the_parsers_write_no_wake_logic():
    import inspect

    assert "wakeable" not in inspect.getsource(_parsers)
