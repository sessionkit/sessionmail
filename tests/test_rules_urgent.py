"""Priority, calls, `$operator` and the urgent list: SM-474 do-005.

Design A1 (the `Priority:` lines are built-in DEFAULT rules, N2), A3 (who the
operator is: ONE value, `$operator`), A5 (only a CALL wakes, N10; as resolved:
no `From:` test on the call lines), A8 (a header condition
on a name in the rename table also reads its `X-` spelling), the urgent-list
READER (A2's judgement half; the `set` grammar is do-013's), `at_once` (A4;
it depends on WHICH line decided, review R-A4), and the draft default
(P-5 RULED: a draft with no rule for it rides and never wakes).

SM-442 owns the parsers: `_parsers.priority`, `_parsers.calls`,
`_parsers.same_address`, `_headers.MIGRATING` / `UNPREFIXED_ONLY` /
`old_spelling` and `_voice.host_forms`. Until SM-442 merged, SM-474 carried
stand-ins for them; they were deleted at the merge (swap-001), and the
parser tests here pin the behaviour the engine relies on, so the swap was
checked when it happened.

Written to fail first: every test here was run against do-004's
`sessionmail.rules` (fdf520a) and failed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import RUNNER, RUNNER_SETTINGS

from sessionmail import _headers, _parsers, _voice, rules
from sessionmail.rules import (
    BUILT_IN_TEXT,
    Facts,
    Layer,
    Me,
    Verdict,
    built_in,
    judge,
    operator,
    parse,
    urgent_senders,
)

SID = "5006e0fb-ed67-7d14-3b9c-8764386a8d7f"
HOST = "hq.tailnet.example"
SHORT = "hq"
ME = Me.of(session=SID, seat="ivan", host=HOST)
NOW = datetime(2030, 6, 9, 12, 34, 56, tzinfo=timezone.utc)
#: The host SM-442's keeper test reads (`_voice.host_forms`).
ENV = {"SESSIONMAIL_HOST": SHORT, "MAGICDNS_SUFFIX": "tailnet.example"}

JUDY = f"Judy <judy@{HOST}>"
EVE = f"Eve <eve@{HOST}>"
#: The alert runner, From its configured local part (`conftest.RUNNER`, SM-798).
ALERTS = f"Alerts <{RUNNER}@{HOST}>"
KEEPER = f"Ears <heard+phone@{HOST}>"

DIRECT = ("X-Original-To", "ivan")
SUBSCRIBED = ("X-Original-To", "atomnote")


def mail(*headers: tuple[str, str], body: str = "", env=ENV) -> Facts:
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return Facts.from_bytes((head + "\n" + body).encode(), env=env)


def turn(rule: str, *more: tuple[str, str], sender: str = KEEPER,
         spelling: str = "Ears-Rule") -> Facts:
    """A heard turn, as the keeper writes one: marked `Ears-Kind`, From the
    keeper, through the `heard` subscription (so `to:$me` never decides)."""
    return mail(("From", sender), ("X-Original-To", "heard"),
                ("Ears-Kind", "spoken"), (spelling, rule), *more)


def layer(name: str, text: str) -> Layer:
    got, errors = parse(text, name)
    assert errors == (), errors
    return Layer(name, got)


def verdict(facts: Facts, *layers: Layer, me: Me = ME) -> Verdict:
    """Judged on the SC6 host with an alert runner configured: `$operator`
    is `judy@*` (SM-727) and the runner is `RUNNER` (SM-798). Plain layers
    read no config, so the settings that host fills are handed in."""
    return judge(facts, (*layers, built_in()), me, NOW, settings=RUNNER_SETTINGS)


def decided(facts: Facts, *layers: Layer) -> tuple[str, str | None, str, bool]:
    got = verdict(facts, *layers)
    return got.action, got.rule, got.layer, got.at_once


# ── the built-in lines, in order (A1, A3, A5) ────────────────────────────────

def test_the_built_in_lines_are_the_amended_seven_in_order():
    assert [r.text for r in built_in().rules] == [
        "carry! from:$operator",
        "carry call:$me",              # P-8 RULED (do-013): the call lines
        "carry call:everyone",         # sit ABOVE the Priority lines
        "ride priority:non-urgent",
        "carry priority:urgent from:$urgent",
        "wake to:$me",
        "ride *",
    ]
    got, errors = parse(BUILT_IN_TEXT, "built-in")
    assert errors == () and len(got) == 7


# ── the slice's criteria ─────────────────────────────────────────────────────

def test_direct_non_urgent_rides():
    """A1: `ride priority:non-urgent` sits ABOVE `wake to:$me`, so a direct
    mail marked non-urgent rides (first match)."""
    got = decided(mail(("From", EVE), DIRECT, ("Priority", "non-urgent")))
    assert got == ("ride", "ride priority:non-urgent", "built-in", False)


def test_the_same_direct_mail_without_a_priority_wakes():
    """The control: it is the Priority line, not the order, that rode it."""
    assert decided(mail(("From", EVE), DIRECT)) == ("wake", "wake to:$me", "built-in", False)


def test_judys_non_urgent_mail_wakes_n14():
    """N14: all Judy's mail wakes, whatever its Priority: the `wake!` pass."""
    got = decided(mail(("From", JUDY), SUBSCRIBED, ("Priority", "non-urgent")))
    assert got == ("wake", "carry! from:$operator", "built-in", False)


@pytest.mark.parametrize("sender", [ALERTS, f"{RUNNER}@{SHORT}"])
def test_urgent_from_the_alert_runner_wakes_at_once(sender):
    """N3 + P-7: the urgent list's default is Judy and the configured
    `<runner>@<host>` (SM-798), in both host forms. Through a subscription, so only the Priority line can
    wake it."""
    got = decided(mail(("From", sender), SUBSCRIBED, ("Priority", "urgent")))
    assert got == ("wake", "carry priority:urgent from:$urgent", "built-in", True)


def test_urgent_from_eve_counts_as_normal():
    """N3: an `urgent` from anyone off the list meets neither Priority line
    and falls through exactly as `normal`: through a subscription it rides,
    direct it wakes by `wake to:$me`, and neither is at once."""
    assert decided(mail(("From", EVE), SUBSCRIBED, ("Priority", "urgent"))) == (
        "ride", "ride *", "built-in", False)
    assert decided(mail(("From", EVE), DIRECT, ("Priority", "urgent"))) == (
        "wake", "wake to:$me", "built-in", False)


def test_a_seat_rule_naming_priority_urgent_honours_eve_at_once():
    """A2 (b): a seat widens the list for itself."""
    seat = layer("seat", "wake priority:urgent from:eve@*\n")
    got = decided(mail(("From", EVE), SUBSCRIBED, ("Priority", "urgent")), seat)
    assert got == ("wake", "wake priority:urgent from:eve@*", "seat", True)


def test_a_session_rule_naming_priority_urgent_honours_too():
    session = layer("session", "wake priority:urgent from:eve@*\n")
    assert decided(mail(("From", EVE), SUBSCRIBED, ("Priority", "urgent")), session)[3] is True


def test_call_me_wakes_and_a_bare_turn_rides():
    """N10: "hey ivan" wakes ivan; a turn that calls nobody rides along."""
    called = turn(f"ivan@{HOST}=call")
    assert decided(called) == ("wake", "carry call:$me", "built-in", False)
    bare = turn(f"ivan@{HOST}=rule")
    assert decided(bare) == ("ride", "ride *", "built-in", False)


def test_x_ears_rule_is_read_when_ears_rule_is_absent():
    """A8: the old spelling is read when the new one is absent."""
    got = decided(turn(f"ivan@{HOST}=call", spelling="X-Ears-Rule"))
    assert got == ("wake", "carry call:$me", "built-in", False)


# ── priority: reads the PARSED level (A1; SM-442's 3.13 parse) ───────────────

@pytest.mark.parametrize("headers, level", [
    ((), "normal"),                                        # missing = normal
    ((("Priority", "urgent"),), "urgent"),
    ((("Priority", " Urgent "),), "urgent"),               # trimmed, any case
    ((("Priority", "NON-URGENT"),), "non-urgent"),
    ((("Priority", "normal"),), "normal"),
    ((("Priority", "high"),), "normal"),                   # not a level
    ((("Priority", ""),), "normal"),
    ((("X-Priority", "1 (Highest)"),), "normal"),          # a client's header, never read
    ((("Priority", "non-urgent"), ("Priority", "urgent")), "non-urgent"),  # first only
])
def test_the_parsed_level(headers, level):
    assert mail(*headers).priority == level


def test_priority_normal_matches_a_mail_with_no_priority_header():
    seat = layer("seat", "ride priority:normal\n")
    assert decided(mail(("From", EVE), DIRECT), seat)[:3] == (
        "ride", "ride priority:normal", "seat")


def test_priority_matches_the_level_not_the_raw_text():
    seat = layer("seat", "ride priority:urgent\n")
    assert verdict(mail(("From", EVE), DIRECT, ("Priority", "Urgent ")), seat).layer == "seat"
    assert verdict(mail(("From", EVE), DIRECT, ("Priority", "high")), seat).layer == "built-in"


@pytest.mark.parametrize("line", [
    "ride priority:urgnet",        # a typo matches no level: named, never silent
    "ride priority:high",
    "ride priority:$me",           # a level is not an address
    "ride priority:$operator",
    "ride draft:maybe",
    "ride draft:*",
    "ride draft:$me",
])
def test_a_condition_that_can_never_match_is_a_bad_line(line):
    got, errors = parse(line + "\n", "seat")
    assert got == () and len(errors) == 1
    assert errors[0].line == 1


@pytest.mark.parametrize("line", [
    "ride priority:non-urgent", "wake priority:URGENT", "ride priority:*urgent",
    "wake call:$me", "wake call:everyone", "wake call:eve@*", "wake call:$operator",
    "ride draft:yes", "wake draft:no from:eve@*", "wake! from:$operator",
    "wake priority:urgent from:$urgent", "wake to:$operator",
])
def test_the_new_conditions_and_variables_parse(line):
    got, errors = parse(line + "\n", "seat")
    assert errors == () and len(got) == 1


# ── $operator and the urgent list: ONE reader each (A3, A2's judgement half) ─────

def test_judy_is_one_value_and_its_default_is_the_ruled_glob():
    """On the SC6 host (conftest) the operator is the ruled glob, from config;
    with no config there is no default (SM-727: `test_rules_operator.py`)."""
    assert operator() == ("judy@*",)
    assert {"me", "operator", "urgent"} <= rules.VARIABLES


def test_urgent_senders_default_is_judy_and_the_runner_in_both_host_forms(alert_runner):
    assert urgent_senders((HOST, SHORT)) == (
        "judy@*", f"{RUNNER}@{HOST}", f"{RUNNER}@{SHORT}")


def test_urgent_senders_with_no_env_reads_the_host_forms_as_sm442_does(alert_runner):
    assert urgent_senders(env=ENV) == (
        "judy@*", f"{RUNNER}@{SHORT}", f"{RUNNER}@{SHORT}.tailnet.example")


def test_with_no_host_the_runner_is_not_on_the_list(alert_runner):
    """Fail closed: with no host there is no `<host>` to name, and an allow
    list does not widen to `<runner>@*` (a forger on any host)."""
    assert urgent_senders(()) == ("judy@*",)
    me = Me.of(session=SID, seat="ivan")
    got = judge(mail(("From", ALERTS), SUBSCRIBED, ("Priority", "urgent")),
                [built_in()], me, NOW, settings=RUNNER_SETTINGS)
    assert (got.action, got.at_once) == ("ride", False)


def test_the_runner_on_another_host_is_not_on_the_list():
    got = decided(mail(("From", f"{RUNNER}@elsewhere.example"), SUBSCRIBED,
                       ("Priority", "urgent")))
    assert got == ("ride", "ride *", "built-in", False)


@pytest.mark.parametrize("sender, woke", [
    (JUDY, True), ("judy@ipad.local", True), ("JUDY@HQ", True),
    (EVE, False), ("judyx@hq", False),
])
def test_from_judy_reads_the_one_value(sender, woke):
    seat = layer("seat", "wake from:$operator\nride *\n")
    assert (verdict(mail(("From", sender)), seat).action == "wake") is woke


# ── at_once: honoured urgent AND wake, by WHICH line decided (A4, R-A4) ──────

def test_judys_urgent_is_at_once_and_his_normal_is_not():
    assert decided(mail(("From", JUDY), ("Priority", "urgent")))[3] is True
    assert decided(mail(("From", JUDY)))[3] is False
    assert decided(mail(("From", JUDY), ("Priority", "normal")))[3] is False


def test_an_honoured_urgent_that_rides_is_not_at_once():
    seat = layer("seat", f"ride from:{RUNNER}@*\n")
    got = decided(mail(("From", ALERTS), ("Priority", "urgent")), seat)
    assert got == ("ride", f"ride from:{RUNNER}@*", "seat", False)


def test_a_listed_urgent_woken_by_another_line_is_still_at_once():
    """"At once" is a property of the MAIL (A4): from the list, urgent, and
    given wake, by any line."""
    seat = layer("seat", f"wake from:{RUNNER}@*\n")
    assert decided(mail(("From", ALERTS), ("Priority", "urgent")), seat) == (
        "wake", f"wake from:{RUNNER}@*", "seat", True)


def test_a_rule_that_does_not_name_urgent_does_not_honour_eve():
    """Only a rule NAMING `priority:urgent` widens the list: `priority:*`
    matches every level and names none."""
    for text in ("wake from:eve@*\n", "wake priority:* from:eve@*\n"):
        got = decided(mail(("From", EVE), ("Priority", "urgent")), layer("seat", text))
        assert (got[0], got[3]) == ("wake", False), text


@pytest.mark.parametrize("name", ["env", "locked"])
def test_only_a_seat_or_session_rule_widens_the_list(name):
    """A2 (b) names SEAT and SESSION rules. The env layer and the locked
    file are not widened here (a locked rule is the operator's; do-013 gives the
    locked file `set urgent`, the host-wide list)."""
    other = layer(name, "wake priority:urgent from:eve@*\n")
    got = decided(mail(("From", EVE), ("Priority", "urgent")), other)
    assert (got[0], got[2], got[3]) == ("wake", name, False)


def test_a_non_urgent_mail_is_never_at_once():
    seat = layer("seat", f"wake from:{RUNNER}@*\n")
    assert decided(mail(("From", ALERTS), ("Priority", "normal")), seat)[3] is False


# ── call: (A5; genie D14; R5.2) ──────────────────────────────────────────────

@pytest.mark.parametrize("value, woke", [
    (f"ivan@{HOST}=call", True),
    (f"ivan@HQ.TAILNET.EXAMPLE=call", True),       # domain: case-insensitive
    (f"ivan@{SHORT}=call", True),                  # the short host form
    (f"Ivan@{HOST}=call", False),                  # local part: exact
    (f"ivan+x@{HOST}=call", False),                # whole, never a +tag reach
    (f"ivana@{HOST}=call", False),
    (f"ivan@elsewhere.example=call", False),
    (f"eve@{HOST}=call, ivan@{HOST}=call", True),  # ANY entry
    (f"{SID}@{HOST}=call", True),                  # the session is $me too
    ("ivan=call", True),                           # bare: this host
])
def test_call_me_compares_each_entry_as_d14_says(value, woke):
    assert (verdict(turn(value)).rule == "carry call:$me") is woke


def test_call_everyone_wakes():
    got = decided(turn("everyone=everyone"))
    assert got == ("wake", "carry call:everyone", "built-in", False)


def test_a_call_on_mail_that_is_not_trusted_voice_is_no_call():
    """SM-442's calls feed is `()` unless the mail is trusted voice: an
    `Ears-Rule` on Eve's mail, or on unmarked mail, calls nobody."""
    assert mail(("From", EVE), ("Ears-Kind", "spoken"),
                ("Ears-Rule", f"ivan@{HOST}=call")).calls == ()
    assert mail(("From", KEEPER), ("Ears-Rule", f"ivan@{HOST}=call")).calls == ()
    assert decided(turn(f"ivan@{HOST}=call", sender=EVE))[:2] == ("ride", "ride *")


@pytest.mark.parametrize("sender", [f"heard@{HOST}", f"heard+phone@{SHORT}", KEEPER])
def test_the_keeper_is_ears_or_ears_plus_a_tag_at_this_host(sender):
    assert mail(("From", sender), ("Ears-Kind", "spoken"),
                ("Ears-Rule", f"ivan@{HOST}=call")).calls == (f"ivan@{HOST}",)


@pytest.mark.parametrize("sender", ["heard+@hq", "heard@elsewhere.example", "Ears@hq",
                                    "pears@hq", "heard@hq, eve@hq"])
def test_anything_else_is_not_the_keeper(sender):
    assert mail(("From", sender), ("Ears-Kind", "spoken"),
                ("Ears-Rule", f"ivan@{HOST}=call")).calls == ()


def test_the_calls_feed_is_the_called_keys_once_each_in_wire_order():
    facts = mail(("From", KEEPER), ("Ears-Kind", "spoken"), ("Ears-Rule",
                 f"eve@{HOST}=call, ivan@{HOST}=rule, everyone=everyone, "
                 f"eve@{HOST}=call, =call, nothing"))
    assert facts.calls == (f"eve@{HOST}", "everyone")


def test_call_takes_a_glob_over_each_entry():
    seat = layer("seat", "wake call:eve@*\n")
    assert verdict(turn(f"eve@{HOST}=call"), seat).layer == "seat"
    assert verdict(turn(f"ivan@{HOST}=call"), seat).layer == "built-in"


def test_a_non_urgent_call_wakes_the_call_rule_wins_p8():
    """R5.3 / genie P-8, RULED (do-013): when a
    Priority rule and a call rule both match a voice turn, the CALL rule
    wins. (This test read "rides" while P-8 was open.)"""
    got = decided(turn(f"ivan@{HOST}=call", ("Priority", "non-urgent")))
    assert got[:2] == ("wake", "carry call:$me")


# ── A8: the rename table reads the X- spelling when the new one is absent ────

def test_a_header_condition_on_a_renamed_name_reads_the_x_spelling():
    seat = layer("seat", "wake ears-kind:spoken\n")
    assert verdict(mail(("X-Ears-Kind", "spoken")), seat).layer == "seat"


def test_the_unprefixed_spelling_wins_when_both_are_present():
    """SM-442 D16 rule 2: presence decides, wherever each sits on the wire."""
    seat = layer("seat", "wake ears-kind:spoken\n")
    assert verdict(mail(("X-Ears-Kind", "spoken"), ("Ears-Kind", "cut")), seat).layer == "built-in"
    assert verdict(mail(("X-Ears-Kind", "cut"), ("Ears-Kind", "spoken")), seat).layer == "seat"


def test_alert_id_has_no_x_spelling_to_read():
    """SM-442's `UNPREFIXED_ONLY`: no code ever minted `X-Alert-Id`."""
    seat = layer("seat", "wake alert-id:*\n")
    assert verdict(mail(("X-Alert-Id", "a1")), seat).layer == "built-in"
    assert verdict(mail(("Alert-ID", "a1")), seat).layer == "seat"


def test_names_off_the_table_are_read_exactly_as_today():
    """P-9: X-Project and friends join the table later, as data (SM-478)."""
    seat = layer("seat", "wake project:atomnote\n")
    assert verdict(mail(("X-Project", "atomnote")), seat).layer == "built-in"
    seat = layer("seat", "wake x-project:atomnote\n")
    assert verdict(mail(("X-Project", "atomnote")), seat).layer == "seat"


def test_the_rename_table_is_data():
    assert "Ears-Rule" in rules.MIGRATING and "Pager-Note" in rules.MIGRATING
    assert rules.UNPREFIXED_ONLY == ("Alert-Id",)


# ── drafts (P-5 RULED; Facts.draft is SM-442's to set) ───────────────────────

def test_a_box_arrival_is_never_a_draft():
    assert mail(("From", EVE)).draft is False
    assert Facts({}).draft is False and Facts({}).calls == () and Facts({}).priority == "normal"


@pytest.mark.parametrize("headers", [
    (("From", JUDY),),
    (("From", EVE), DIRECT),
    (("From", ALERTS), ("Priority", "urgent")),
])
def test_a_draft_with_no_rule_for_it_rides_and_never_wakes(headers):
    facts = Facts(mail(*headers).headers, priority=mail(*headers).priority, draft=True)
    assert decided(facts) == ("ride", "ride *", "built-in", False)


def test_a_draft_of_a_call_rides_too():
    heard = turn(f"ivan@{HOST}=call")
    draft = Facts(heard.headers, priority=heard.priority, calls=heard.calls, draft=True)
    assert decided(draft)[:2] == ("ride", "ride *")


def test_the_seats_own_rules_still_decide_a_draft():
    """N11/N12: whether a draft wakes a seat is the seat's own rules' call."""
    heard = turn(f"ivan@{HOST}=call")
    draft = Facts(heard.headers, calls=heard.calls, draft=True)
    seat = layer("seat", "wake draft:yes call:$me\n")
    assert decided(draft, seat)[:3] == ("wake", "wake draft:yes call:$me", "seat")
    assert decided(heard, seat)[:3] == ("wake", "carry call:$me", "built-in")


def test_draft_no_matches_only_a_finished_mail():
    seat = layer("seat", "ride draft:no from:eve@*\n")
    assert verdict(mail(("From", EVE), DIRECT), seat).layer == "seat"
    assert verdict(Facts(mail(("From", EVE)).headers, draft=True), seat).rule == "ride *"


# ── the engine reads SM-442's parsers (swap-001: the stand-ins are gone) ───

def _msg(*headers: tuple[str, str]):
    """A parsed header block, as `Facts.from_bytes` parses one."""
    from email.parser import BytesHeaderParser
    from email.policy import compat32
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return BytesHeaderParser(policy=compat32).parsebytes((head + "\n").encode())


def test_the_engine_reads_sm442s_own_tables():
    """The swap is identity, not a copy: the level list, the rename table and
    the names never minted with `X-` ARE SM-442's objects."""
    assert rules.LEVELS is _parsers.LEVELS
    assert rules.MIGRATING is _headers.MIGRATING
    assert rules.UNPREFIXED_ONLY is _headers.UNPREFIXED_ONLY
    assert Facts().priority == _parsers.NORMAL


@pytest.mark.parametrize("name, old", [
    ("Ears-Rule", "x-ears-rule"), ("pager-note", "x-pager-note"),
    ("Alert-Id", None), ("Priority", None), ("subject", None),
])
def test_old_spelling_is_the_rename_table(name, old):
    assert _headers.old_spelling(name) == old


def test_a_header_condition_reads_the_old_spelling_through_sm442s_table():
    facts = Facts.from_message(_msg(("From", EVE), ("X-Ears-Device", "airpods")))
    assert facts.header("Ears-Device") == "airpods"
    assert facts.header("x-ears-device") == "airpods"
    assert Facts.from_message(_msg(("X-Alert-Id", "7"))).header("Alert-Id") is None


@pytest.mark.parametrize("a, b, same", [
    ("ivan@hq", "ivan@HQ", True), ("Ivan@hq", "ivan@hq", False),
    ("ivan@hq", "ivan@hq.tailnet.example", False), ("ivan", "ivan", True),
    ("ivan", "ivan@hq", False),
])
def test_same_address_is_sm442s(a, b, same):
    assert _parsers.same_address(a, b) is same


#: RFC 5322 comments nest and `email.utils` reads them recursively: past
#: ~500 levels, RecursionError. 1,000 is past it.
DEEP = "(" * 1000 + ")" * 1000


def test_a_from_nested_past_the_recursion_limit_is_not_the_keeper():
    """The stricter half of the swap: SM-474's stand-in failed CLOSED on a
    `From:` that `getaddresses` cannot read without raising, and SM-442's
    `_voice._sender` raised. Kept the stricter: no sender, not the keeper,
    no calls, and the facts are built - never a deaf seat."""
    msg = _msg(("From", f"{DEEP} heard@{HOST}"), ("Ears-Kind", "spoken"),
               ("Ears-Rule", f"ivan@{HOST}=call"))
    assert _voice.keeper_sent(msg, ENV) is False
    assert _voice.recognise(msg, ENV).voice is False
    assert _parsers.calls(msg, ENV) == ()
    assert Facts.from_message(msg, ENV).calls == ()
