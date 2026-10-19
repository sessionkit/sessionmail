"""The judgement: `openspec/changes/pager-filters`, tasks 2.2.

Design D4, as ruled (P1-P15): every `wake!` line from
EVERY layer first (a match wakes), then the FIRST matching line down the
layers, then the built-in defaults. The conditions are D2's: `to:` reads
`X-Original-To` first and `To`/`Cc` only without it; `from:` the From
ADDRESS; `subject:` the decoded Subject; any other name a header's first
occurrence, decoded, by case-insensitive name. `$me` is this session's and
this seat's addresses with their `+tag` forms, and for a seat that is
itself a `+tag` on a shared base, the plus form only.

`judge()` is pure: the mail's facts, the layers, who "me" is and the time
go in; a `Verdict` (action, the deciding rule's text, its layer, its line,
and `at_once`, False until do-005) comes out.

The built-in layer below is a FIXTURE with the ruled three lines (D4 step
3); the real built-ins and the layer loader are do-003's.

Every table here was run against a `sessionmail.rules` with no `judge`
first and failed (the fail-first rule of the slice).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sessionmail.rules import Facts, Layer, Me, Verdict, judge, parse

SID = "5c802c34-f0da-2b67-c1f7-c062abd4433a"
HOST = "hq.tailnet.example"
ME = Me.of(session=SID, seat="ivan", host=HOST)
NOW = datetime(2030, 7, 11, 12, 34, 56, tzinfo=timezone.utc)

JUDY = f"Judy <judy@{HOST}>"


def layer(name: str, text: str) -> Layer:
    """A layer from rules text that must parse clean."""
    rules, errors = parse(text, name)
    assert errors == (), errors
    return Layer(name, rules)


#: D4 step 3, the ruled three (a fixture: do-003 owns the real built-ins).
BUILT_IN = layer("built-in", "wake! from:judy@*\nwake to:$me\nride *\n")


def mail(*headers: tuple[str, str], body: str = "") -> Facts:
    """Facts from a raw mail with HEADERS in the order given."""
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return Facts.from_bytes((head + "\n" + body).encode())


def verdict(facts: Facts, *layers: Layer, me: Me = ME) -> Verdict:
    return judge(facts, layers, me, NOW)


def action(facts: Facts, *layers: Layer, me: Me = ME) -> str:
    return verdict(facts, *layers, me=me).action


# ── D4: breakthroughs first ──────────────────────────────────────────────────

def test_wake_bang_beats_a_sessions_ride_star_at_the_top():
    """The spec's *A session cannot silence Judy*: the session's own file
    starts with `ride *`, and Judy's mail still wakes, by the built-in
    `wake!`, tried before any first match."""
    session = layer("session", "ride *\n")
    got = verdict(mail(("From", JUDY), ("X-Original-To", "ivan")), session, BUILT_IN)
    # carry=True: every wake carries (SM-656 R1).
    assert got == Verdict("wake", "wake! from:judy@*", "built-in", 1, at_once=False, carry=True)


def test_the_same_mail_without_the_breakthrough_rides():
    """The control for the test above: it is the `wake!` pass, not an
    accident of order, that woke it."""
    session = layer("session", "ride *\n")
    no_bang = layer("built-in", "wake to:$me\nride *\n")
    got = verdict(mail(("From", JUDY), ("X-Original-To", "ivan")), session, no_bang)
    assert (got.action, got.rule, got.layer, got.line) == ("ride", "ride *", "session", 1)


def test_a_seat_breakthrough_beats_a_session_ride_above_it():
    session = layer("session", "ride from:eve@*\n")
    seat = layer("seat", "# the seat's own\nwake! from:eve@* subject:URGENT*\n")
    got = verdict(mail(("From", "eve@hq"), ("Subject", "URGENT: db1 is down")),
                  session, seat, BUILT_IN)
    assert (got.action, got.rule, got.layer, got.line) == (
        "wake", "wake! from:eve@* subject:URGENT*", "seat", 2)


def test_a_breakthrough_wakes_through_a_focus_layer():
    """The spec's *Marked mail from Eve, at night*: `sleep`'s `ride *` sits
    above the seat, and the seat's `wake!` still wakes."""
    sleep = layer("focus: sleep", "ride *\n")
    seat = layer("seat", "wake! from:eve@* subject:URGENT*\n")
    urgent = mail(("From", "eve@hq"), ("Subject", "URGENT: db1 is down"))
    other = mail(("From", "eve@hq"), ("Subject", "lunch?"), ("X-Original-To", "ivan"))
    assert verdict(urgent, sleep, seat, BUILT_IN).layer == "seat"
    assert action(urgent, sleep, seat, BUILT_IN) == "wake"
    assert verdict(other, sleep, seat, BUILT_IN) == Verdict(
        "ride", "ride *", "focus: sleep", 1, at_once=False)


def test_the_first_matching_breakthrough_down_the_layers_is_the_one_named():
    locked = layer("locked", "wake! from:judy@hq*\n")
    got = verdict(mail(("From", JUDY)), locked, BUILT_IN)
    assert (got.rule, got.layer) == ("wake! from:judy@hq*", "locked")


def test_a_breakthrough_that_does_not_match_is_passed_over():
    seat = layer("seat", "wake! from:eve@*\nride from:kith@*\n")
    got = verdict(mail(("From", "kith@hq")), seat, BUILT_IN)
    assert (got.action, got.rule, got.line) == ("ride", "ride from:kith@*", 2)


# ── D4: then the first match, down the layers ────────────────────────────────

def test_an_upper_layer_beats_a_lower_one():
    session = layer("session", "ride from:eve@*\n")
    seat = layer("seat", "wake from:eve@*\n")
    got = verdict(mail(("From", "eve@hq")), session, seat, BUILT_IN)
    assert (got.action, got.layer) == ("ride", "session")


def test_within_a_layer_the_first_line_wins():
    seat = layer("seat", "wake from:eve@*\nride from:eve@*\n")
    got = verdict(mail(("From", "eve@hq")), seat, BUILT_IN)
    assert (got.action, got.line) == ("wake", 1)


def test_a_lower_layer_decides_when_the_upper_ones_do_not_match():
    session = layer("session", "ride from:kith@*\n")
    seat = layer("seat", "\n\n# three lines down\nwake x-project:atomnote\n")
    got = verdict(mail(("From", "eve@hq"), ("X-Project", "atomnote")), session, seat, BUILT_IN)
    assert (got.action, got.rule, got.layer, got.line) == (
        "wake", "wake x-project:atomnote", "seat", 4)


def test_start_is_an_action_of_its_own():
    """D8: `start` parses, is stored and is judged like any verb; the
    listener, not the judgement, declines to act on it."""
    seat = layer("seat", "start from:judy@* subject:start*\n")
    got = verdict(mail(("From", "judy@hq"), ("Subject", "start please")), seat)
    assert got.action == "start"


@pytest.mark.parametrize("headers, expected", [
    # direct mail wakes, by `wake to:$me` (line 2)
    ((("X-Original-To", "ivan"),), ("wake", "wake to:$me", 2)),
    # a subscription copy rides, by `ride *` (line 3)
    ((("X-Original-To", "atomnote"), ("To", "atomnote@hq")), ("ride", "ride *", 3)),
    # Judy, by the breakthrough (line 1)
    ((("From", JUDY), ("X-Original-To", "atomnote")), ("wake", "wake! from:judy@*", 1)),
])
def test_the_built_in_layer_decides_last(headers, expected):
    got = verdict(mail(*headers), BUILT_IN)
    assert (got.action, got.rule, got.line) == expected
    assert got.layer == "built-in"


def test_with_no_line_matching_the_mail_rides_and_no_rule_is_named():
    """No layer at all (or none that matches): the fallback is `ride`, the
    least action, since every mail rides along anyway (D1)."""
    assert verdict(mail(("From", "eve@hq"))) == Verdict(
        "ride", None, "fallback", None, at_once=False)
    seat = layer("seat", "wake from:kith@*\n")
    assert verdict(mail(("From", "eve@hq")), seat).rule is None


def test_at_once_is_false_for_every_verdict_for_now():
    """`at_once` is do-005's (design A4); the field exists and is False."""
    for facts in (mail(("From", JUDY)), mail(("X-Original-To", "ivan")), mail()):
        assert verdict(facts, BUILT_IN).at_once is False


# ── AND on one line ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("frm, subject, matched", [
    ("eve@hq", "URGENT: down", True),
    ("eve@hq", "lunch", False),       # subject fails
    ("kith@hq", "URGENT: down", False),  # from fails
])
def test_every_condition_on_a_line_must_match(frm, subject, matched):
    seat = layer("seat", "wake from:eve@* subject:URGENT*\n")
    got = verdict(mail(("From", frm), ("Subject", subject)), seat)
    assert (got.rule is not None) is matched


# ── to: X-Original-To first, then To/Cc ──────────────────────────────────────

@pytest.mark.parametrize("headers, rule, matched", [
    # X-Original-To decides when present: To names me, but the copy came
    # THROUGH atomnote, so it is not "mailed to me" (D2).
    ((("X-Original-To", "atomnote@hq"), ("To", "ivan@hq")), "wake to:$me", False),
    ((("X-Original-To", "atomnote@hq"), ("To", "ivan@hq")), "wake to:atomnote@*", True),
    ((("X-Original-To", "atomnote@hq"), ("To", "ivan@hq")), "wake to:ivan@*", False),
    # without X-Original-To, To and Cc are read
    ((("To", "Ivan <ivan@hq>"),), "wake to:$me", True),
    ((("To", "eve@hq, Ivan <ivan@hq>"),), "wake to:$me", True),
    ((("To", "eve@hq"), ("Cc", "ivan@hq")), "wake to:$me", True),
    ((("To", "eve@hq"), ("Cc", "kith@hq")), "wake to:$me", False),
    ((("To", "eve@hq"), ("Cc", "kith@hq")), "wake to:kith@*", True),
    # an empty X-Original-To is no X-Original-To
    ((("X-Original-To", ""), ("To", "ivan@hq")), "wake to:$me", True),
    # no recipient at all: nothing to match, even `*`
    ((("From", "eve@hq"),), "wake to:*", False),
])
def test_to_reads_x_original_to_first(headers, rule, matched):
    got = verdict(mail(*headers), layer("seat", rule))
    assert (got.rule is not None) is matched


@pytest.mark.parametrize("original_to, rule, matched", [
    # MEASURED Thu 2030-10-03: 460 of 500 X-Original-To values in
    # agents/ivan/cur carry no domain at all. A bare local part is an address
    # on THIS host, so the ruled example `ride to:kith@*` must reach it.
    ("kith", "ride to:kith@*", True),
    ("kith", f"ride to:kith@{HOST}", True),
    ("kith", "ride to:kith@hq", True),
    ("kith", "ride to:kith", True),
    ("kith", "ride to:kith@example.com", False),
    ("kith@example.com", "ride to:kith@hq", False),
])
def test_a_bare_local_part_is_an_address_on_this_host(original_to, rule, matched):
    got = verdict(mail(("X-Original-To", original_to)), layer("seat", rule))
    assert (got.rule is not None) is matched


# ── from: the From address ───────────────────────────────────────────────────

@pytest.mark.parametrize("frm, rule, matched", [
    (JUDY, "wake from:judy@*", True),
    ("judy@ipad.local", "wake from:judy@*", True),      # measured, design.md
    ("admin <judy@hq>", "wake from:admin*", False),     # the display name is not the address
    ('"eve@hq" <kith@hq>', "wake from:eve@*", False),   # nor is a display name that looks like one
    ("JUDY@HQ.EXAMPLE.TS.NET", "wake from:judy@*", True),  # case-insensitive
    ("eve@hq", "wake from:judy@*", False),
    ("judy", "wake from:judy@*", True),                 # bare: this host
])
def test_from_matches_the_from_address(frm, rule, matched):
    got = verdict(mail(("From", frm)), layer("seat", rule))
    assert (got.rule is not None) is matched


def test_a_mail_with_no_from_matches_no_from_condition():
    assert verdict(mail(("Subject", "x")), layer("seat", "wake from:*\n")).rule is None


# ── subject: the decoded Subject ─────────────────────────────────────────────

@pytest.mark.parametrize("subject, rule, matched", [
    ("URGENT: db1 is down", "wake subject:URGENT*", True),
    ("=?utf-8?q?URGENT=3A_db1_is_down?=", "wake subject:URGENT:*", True),  # RFC 2047
    ("=?utf-8?b?4pqhIGRlcGxveQ==?=", "wake subject:⚡*", True),           # base64, non-ASCII
    ("urgent: lower case", "wake subject:URGENT*", True),   # case-insensitive
    ("not URGENT", "wake subject:URGENT*", False),          # the glob is anchored
    ("#123 fixed", "wake subject:#123*", True),
])
def test_subject_is_a_glob_over_the_decoded_subject(subject, rule, matched):
    got = verdict(mail(("Subject", subject)), layer("seat", rule))
    assert (got.rule is not None) is matched


def test_a_folded_subject_is_unfolded_before_matching():
    facts = Facts.from_bytes(b"Subject: URGENT: a long\n subject line\n\n")
    assert verdict(facts, layer("seat", "wake subject:*long?subject*")).rule is not None


# ── <header>: first occurrence, decoded, name case-insensitive ───────────────

@pytest.mark.parametrize("headers, rule, matched", [
    ((("X-Project", "atomnote"),), "wake x-project:atomnote", True),
    ((("X-PROJECT", "atomnote"),), "wake x-project:atomnote", True),   # name case
    ((("x-project", "atomnote"),), "wake X-Project:atomnote", True),
    ((("X-Project", "AtomNote"),), "wake x-project:atomnote", True),   # value case
    ((("X-Project", "atomnote"), ("X-Project", "other")), "wake x-project:other", False),
    ((("X-Project", "atomnote"), ("X-Project", "other")), "wake x-project:atom*", True),
    ((("X-Project", "=?utf-8?q?atom_note?="),), "wake x-project:atom?note", True),
    ((("X-Store", "acmecorp"),), "wake x-project:*", False),   # absent: no value to match
    ((("X-Project", ""),), "wake x-project:*", True),          # present and empty
    ((("X-Project", "atomnote"),), "wake x-project:atom", False),  # globs are anchored
])
def test_a_header_condition_reads_the_first_occurrence(headers, rule, matched):
    got = verdict(mail(*headers), layer("seat", rule))
    assert (got.rule is not None) is matched


# ── the glob dialect: `*` and `?` only; `[` is LITERAL (build decision O-g) ──
#
# Full fnmatch reads `[...]` as a character class, so `wake subject:[URGENT]*`
# woke on every `Re:` and missed `[URGENT] deploy` (do-002 review 3, F4). D2's
# ruled reason for globs is that they "need no escaping", so a bracketed tag
# must match itself. Every glob condition kind, not only `subject:`.

@pytest.mark.parametrize("subject, rule, matched", [
    ("[URGENT] deploy", "wake subject:[URGENT]*", True),
    ("[urgent] deploy", "wake subject:[URGENT]*", True),    # still case-insensitive
    ("Re: hello", "wake subject:[URGENT]*", False),         # a class would take the `r`
    ("Test", "wake subject:[URGENT]*", False),              # ...or the `t`
    ("URGENT deploy", "wake subject:[URGENT]*", False),     # the brackets are required
    ("[SM-474] x", "wake subject:[SM-474]*", True),
    ("Re: x", "wake subject:[SM-474]*", False),             # `M-4` is not a range
    ("4", "wake subject:[SM-474]", False),
    ("[!x] y", "wake subject:[!x]*", True),                 # `!` is not a negation
    ("a", "wake subject:[!x]", False),
    ("x] y", "wake subject:x]*", True),                     # a lone `]` was always literal
    ("[[x]] y", "wake subject:[[x]]*", True),
    ("[a] y", "wake subject:[?]*", True),                   # `?` and `*` stay wildcards
    ("[abc] y", "wake subject:[*]*", True),
    ("[a] y", "wake! subject:[URGENT]*", False),            # the breakthrough is the worst case
])
def test_a_bracket_in_a_subject_glob_is_literal(subject, rule, matched):
    got = verdict(mail(("Subject", subject)), layer("seat", rule))
    assert (got.rule is not None) is matched


@pytest.mark.parametrize("headers, rule, matched", [
    ((("X-Alert-Name", "[prod] disk full"),), "wake x-alert-name:[prod]*", True),
    ((("X-Alert-Name", "prod disk full"),), "wake x-alert-name:[prod]*", False),
    ((("X-Alert-Name", "p"),), "wake x-alert-name:[prod]", False),
    ((("X-Original-To", "ops+[x]@hq"),), "wake to:ops+[x]@*", True),
    ((("X-Original-To", "ops+x@hq"),), "wake to:ops+[x]@*", False),
    ((("X-Original-To", "ops@hq"),), "wake to:[o]ps@*", False),
    ((("From", "judy@hq"),), "wake from:[jp]*@*", False),     # a class would take the `j`
    ((("From", "judy@hq"),), "wake from:[j]udy@*", False),
])
def test_a_bracket_is_literal_in_every_glob_condition_kind(headers, rule, matched):
    got = verdict(mail(*headers), layer("seat", rule))
    assert (got.rule is not None) is matched


def test_a_bracketed_tag_in_a_breakthrough_wakes_through_a_ride_star():
    facts = mail(("From", "ci@hq"), ("Subject", "[URGENT] db1 is down"))
    session = layer("session", "ride *\n")
    seat = layer("seat", "wake! subject:[URGENT]*\n")
    # carry=True: every wake carries (SM-656 R1).
    assert verdict(facts, session, seat) == Verdict("wake", "wake! subject:[URGENT]*", "seat", 1,
                                                    carry=True)
    reply = mail(("From", "ci@hq"), ("Subject", "Re: hello"))
    assert verdict(reply, session, seat).action == "ride"


def test_facts_keep_the_first_occurrence_under_a_folded_name():
    facts = mail(("X-Project", "atomnote"), ("x-project", "other"), ("Subject", "hi"))
    assert facts.headers["x-project"] == "atomnote"
    assert facts.headers["subject"] == "hi"
    assert "X-Project" not in facts.headers


# ── $me ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("to, matched", [
    ("ivan", True),
    ("ivan@hq", True),
    (f"ivan@{HOST}", True),
    ("IVAN@HQ", True),
    ("ivan+kaizen", True),                 # the seat's +tag forms
    ("ivan+kaizen@hq", True),
    (SID, True),                           # the session's own address
    (f"{SID}@hq", True),
    (f"{SID}+boot", True),                 # and its +tag forms
    ("ivana", False),                      # a prefix is not a +tag boundary
    ("ivan@gmail.com", False),             # another host's ivan is not me
    ("eve", False),
    ("", False),
])
def test_me_is_the_session_and_the_seat_with_their_tags(to, matched):
    got = verdict(mail(("X-Original-To", to)), layer("seat", "wake to:$me"))
    assert (got.rule is not None) is matched


@pytest.mark.parametrize("to, matched", [
    ("acmecorp+gc", True),
    ("acmecorp+gc@hq", True),
    ("acmecorp+gc+boot", True),
    ("acmecorp", False),          # the bare base belongs to the store (D2)
    ("acmecorp@hq", False),
    ("acmecorp+super", False),    # another seat on the same base
    ("acmecorp+gcx", False),
])
def test_a_plus_seat_is_me_in_its_plus_form_only(to, matched):
    """D2, measured Wed 2030-09-25: the store box's copy of a mail to
    the GC carries `X-Original-To: acmecorp+gc`, and the bare base is the
    store's. Otherwise `to:$me` would wake the GC on every store mail."""
    gc = Me.of(session=SID, seat="acmecorp+gc", host=HOST)
    got = verdict(mail(("X-Original-To", to)), layer("seat", "wake to:$me"), me=gc)
    assert (got.rule is not None) is matched


def test_me_as_from_is_my_own_mail():
    seat = layer("seat", "ride from:$me\n")
    assert verdict(mail(("From", "Ivan <ivan@hq>")), seat).rule == "ride from:$me"
    assert verdict(mail(("From", "eve@hq")), seat).rule is None


def test_a_session_with_no_seat_is_still_its_own_address():
    me = Me.of(session=SID, host=HOST)
    assert me.addresses == (SID,)
    seat = layer("seat", "wake to:$me\n")
    assert verdict(mail(("X-Original-To", SID)), seat, me=me).action == "wake"
    assert verdict(mail(("X-Original-To", "ivan")), seat, me=me).rule is None


def test_me_folds_its_addresses_and_drops_repeats_and_domains():
    me = Me.of(session="ABC", seat="Ivan@HQ.tailnet.example", host="HQ.Tailnet.Example")
    assert me.addresses == ("ivan", "abc")
    assert Me.of(session="ivan", seat="ivan", host=HOST).addresses == ("ivan",)


# ── pure ─────────────────────────────────────────────────────────────────────

def test_the_judgement_is_the_same_every_time_and_touches_nothing(monkeypatch, tmp_path):
    """Pure: no environment, no file, no clock read. Poison the ways it
    could reach them and judge the same mail twice."""
    monkeypatch.setenv("HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("SESSIONMAIL_PAGE_RULES", "wake *")
    facts = mail(("X-Original-To", "atomnote"))
    first = verdict(facts, BUILT_IN)
    assert first == verdict(facts, BUILT_IN)
    assert first.action == "ride"
    assert not (tmp_path / "nowhere").exists()


def test_facts_come_from_a_message_or_from_bytes_alike():
    import email
    raw = b"From: eve@hq\nX-Original-To: ivan\nSubject: hi\n\nbody\n"
    assert Facts.from_bytes(raw) == Facts.from_message(email.message_from_bytes(raw))


# ── edges found in the build ─────────────────────────────────────────────────

def test_a_raw_utf8_header_is_read_as_utf8():
    """An 8-bit header (SMTPUTF8, or a sender that did not encode) comes
    through the parser as surrogate escapes; the judgement reads it as the
    UTF-8 it is, so `subject:⚡*` meets `⚡ deploy` either way it was sent."""
    facts = Facts.from_bytes("Subject: ⚡ deploy\nX-Project: café\n\n".encode())
    assert facts.headers["subject"] == "⚡ deploy"
    assert verdict(facts, layer("seat", "wake subject:⚡*")).rule is not None
    assert verdict(facts, layer("seat", "wake x-project:caf?")).rule is not None


def test_a_header_that_is_not_utf8_is_kept_not_dropped():
    facts = Facts.from_bytes(b"Subject: caf\xe9 latin-1\n\n")
    assert facts.headers["subject"].endswith(" latin-1")
    assert verdict(facts, layer("seat", "wake subject:*latin-1")).rule is not None


def test_me_on_a_header_reads_its_address_not_its_display_name():
    seat = layer("seat", "wake reply-to:$me\n")
    assert verdict(mail(("Reply-To", "Ivan <ivan@hq>")), seat).rule is not None
    assert verdict(mail(("Reply-To", "ivan-ish <eve@hq>")), seat).rule is None


def test_facts_from_a_default_policy_message_match_compat32():
    import email
    from email import policy
    raw = "Subject: =?utf-8?q?URGENT=3A_x?=\nFrom: Ivan <ivan@hq>\nX-Project: ⚡\n\n".encode()
    assert Facts.from_message(email.message_from_bytes(raw, policy=policy.default)) \
        == Facts.from_bytes(raw)


# ── edges found in review (do-002 peer review, session 2f07cdee) ─────────────

def test_an_encoded_display_name_with_a_comma_still_breaks_through():
    """RFC 2047 lets a display name hide a comma (`=2C`). Decoded before the
    address is parsed, `Hopper, Judy <judy@hq>` reads as TWO addresses and
    the From address is lost, so Judy's `wake!` missed. The breakthrough is
    the one line no layer may silence (D4 step 1)."""
    facts = Facts.from_bytes(b"From: =?utf-8?q?Hopper=2C_Judy?= <judy@hq>\n\n")
    assert facts.sender() == "judy@hq"
    got = verdict(facts, layer("session", "ride *\n"), BUILT_IN)
    assert (got.action, got.rule) == ("wake", "wake! from:judy@*")


def test_an_encoded_display_name_is_not_read_as_an_address():
    """The same comma on To: made the display name `ivan` an address, and
    `wake to:$me` woke Ivan on a mail to eve."""
    facts = Facts.from_bytes(b"To: =?utf-8?q?ivan=2C_x?= <eve@hq>\n\n")
    assert facts.recipients() == ("eve@hq",)
    assert verdict(facts, layer("seat", "wake to:$me\n")).rule is None


def test_an_address_header_still_reads_as_a_person_would():
    facts = Facts.from_bytes(b"From: =?utf-8?q?M=C3=BCller=2C_Hans?= <h@hq>\n\n")
    assert "Müller, Hans" in facts.headers["from"]
    assert facts.sender() == "h@hq"


def test_raw_utf8_beside_an_encoded_word_is_read_as_utf8():
    """8-bit text and an RFC 2047 word in ONE header: the plain part came
    out as literal `\\udcc3` escape text, not `é`."""
    facts = Facts.from_bytes("Subject: café =?utf-8?q?deploy?=\n\n".encode())
    assert facts.headers["subject"] == "café deploy"
    assert verdict(facts, layer("seat", "wake subject:café*")).rule is not None


def test_an_address_header_that_will_not_parse_keeps_its_text():
    """The address class renders `a@b@c` as `<>`; the value is kept as
    text instead, never dropped (`_decode`'s promise). Measured red in
    review against the address class with no defect guard."""
    facts = Facts.from_bytes(b"From: a@b@c\n\n")
    assert facts.headers["from"] == "a@b@c"
    assert facts.sender() is None


# ── re-submission (do-002, session 93288af2) ─────────────────────────────────

def test_the_judgement_never_raises_on_garbage_headers():
    """`judge` promises never to raise. Nothing pinned that promise, so this
    does: seeded garbage made of the bytes
    address parsing trips on (quotes, brackets, `@`, `,`, encoded-word
    marks, 8-bit), in the headers the conditions read. Measured 20,000
    rounds clean at the re-submission; 2,000 here keep the suite fast.

    It does NOT catch an error that `_decode`'s broad `except` swallows (the
    review met a NameError hidden that way). The encoded-comma tests above
    catch that: the fallback decoder fails them."""
    import random

    layers = (layer("seat", "start reply-to:$me\nwake subject:*x*\n"), BUILT_IN)
    alphabet = b'<>@,;:"\\()[]=?_ \tabcjudy\xc3\xa9\xff' + b"=?utf-8?q?=?b?=2C"
    names = (b"From", b"To", b"Cc", b"X-Original-To", b"Subject", b"Reply-To")
    rng = random.Random(7)
    for _ in range(2000):
        head = b"".join(
            rng.choice(names) + b": "
            + bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) + b"\n"
            for _ in range(3))
        got = judge(Facts.from_bytes(head + b"\n"), layers, ME, NOW)
        assert got.action in ("start", "wake", "ride")


#: RFC 5322 comments nest, and `email.utils` reads them recursively, so an
#: address behind ~500 levels of `(` raised RecursionError out of `judge`.
#: Found in the second review, Thu 2030-10-03; 1,000 is past it.
DEEP = "(" * 1000 + ")" * 1000


@pytest.mark.parametrize("name", ["From", "To", "Cc", "X-Original-To"])
def test_an_address_nested_past_the_recursion_limit_never_raises(name):
    """A sender can nest comments in any address header, and the listener
    judges every mail it hears: a RecursionError here is a deaf seat. The
    address is read as unparseable instead, and the mail falls through to
    the lower lines like any other mail with no usable address."""
    head = [] if name == "From" else [("From", "z@x")]
    facts = mail(*head, (name, f"{DEEP} ivan@{HOST}"))
    assert verdict(facts, BUILT_IN).action in ("wake", "ride")


def test_a_nested_from_reads_as_no_sender_and_judys_rule_does_not_match():
    """The price, named: a From nested that deep has no address we can read,
    so `from:judy@*` does not match it. No mail client writes one."""
    facts = mail(("From", f"{DEEP} judy@{HOST}"))
    assert facts.sender() is None
    assert verdict(facts, layer("session", "ride *\n"), BUILT_IN) == Verdict(
        "ride", "ride *", "session", 1)


def test_me_on_a_nested_address_header_never_raises():
    """`$me` on any other header parses its address too (`reply-to:$me`)."""
    facts = mail(("From", "z@x"), ("Reply-To", f"{DEEP} ivan@{HOST}"))
    got = verdict(facts, layer("seat", "start reply-to:$me\n"), BUILT_IN)
    assert got.action in ("start", "ride")


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN GAP, not fixed in do-002: Python's strict getaddresses reads a "
    "list holding ONE malformed address as no addresses at all, so a good "
    "To/Cc beside it is lost and `wake to:$me` misses. MEASURED Thu "
    "2030-10-03 over all 129,062 files under ~/.mail/agents: 0 recipient "
    "lists lost this way (2 From values lost, both one multi-address test "
    "mail). Strict so that whoever fixes it sees this flip."))
def test_a_good_recipient_beside_a_malformed_one_is_still_read():
    facts = mail(("To", f"a@b@c, ivan@{HOST}"))
    assert facts.recipients() == (f"ivan@{HOST}",)
