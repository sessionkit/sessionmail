"""pager-genie tasks.md 3.9a and 3.9b: `pager say`'s two reads, as PURE
functions (`sessionmail._say`), no CLI.

openspec/changes/pager-genie @ d156f13; design D7 steps 1-2.

- 3.9a *The latest turn this session heard (N9)*, proving *A reply to a turn
  is a mail To the mouth, threaded on the latest turn heard*: scenario
  *Replying to the latest turn heard, not the latest call*; nothing heard
  -> no `In-Reply-To`; the tie order (one ride-along stamp: the later
  `Ears-Start`, then `Date:`); only THIS session's marks, only trusted voice.
- SM-782: a turn is TRUSTED voice, signed by the keeper: every read here is
  handed `KEEPER_SIGNED`, the verdict stand-in, and UNVERIFIED voice (the
  same turns unsigned) is never a turn and never holds a line.
- 3.9b *The operator's-words pre-check (N7)*, proving *the operator's unread
  words hold a line*: scenario *the operator said something the session has
  not seen*; a turn of Rupert's does not hold; the operator is SM-474's
  `$operator` (`rules.operator()`, and
  `set operator` overrides it), no stand-in.

NOT tested here, HELD with 3.18b (held-001): *Heard only as a draft* and *the
operator is still talking, and the session has heard it* both need the drafts snapshot. The
marks analogue of the second (a turn of Judy's this session WAS given does
not hold, and is the turn replied to) is tested instead, and named so.

Pure: messages are built in memory and the marks are `_seat.Mark` values.
No maildir, no `~/.mail`, no `~/.sessionmail`, no listener.
"""

from __future__ import annotations

from datetime import datetime
from email import message_from_bytes
from email.policy import default as default_policy

from conftest import KEEPER_SIGNED, SC6_SETTINGS

from sessionmail import _say, _seat, rules

HOST = "hq"
ENV = {"SESSIONMAIL_HOST": HOST, "MAGICDNS_SUFFIX": "example.ts.net"}  # SM-674: no default
EVE = "9f1e0000-1111-2222-3333-444455556666"
IVAN = "c0a40000-1111-2222-3333-444455556666"
JUDY = f"judy@{HOST}"
RUPERT = f"rupert@{HOST}"
KEEPER = f"heard+airpods@{HOST}"

#: One ride-along stamp, and later ones.
T0 = "2030-01-01T12:00:00+00:00"
T1 = "2030-01-01T12:01:00+00:00"
T2 = "2030-01-01T12:02:00+00:00"


def turn(mid: str, *, speaker: str = JUDY, rule: str | None = None, sender: str = KEEPER,
         start: str | None = "2030-01-01T11:59:52+00:00",
         date: str | None = "Tue, 01 Jan 2030 12:00:00 +0000",
         words: str | None = None, prefixed: bool = False, kind: bool = True):
    """A FINISHED turn as the keeper mails it (the spec's headers, as
    test_genie_finished_turn.turn writes them), parsed."""
    x = "X-" if prefixed else ""
    head = [f"From: Ears <{sender}>", f"To: {KEEPER}", f"Subject: heard {mid}",
            f"Message-ID: <{mid}>", f"{x}Ears-Device: airpods", f"{x}Ears-Speaker: {speaker}"]
    if kind:
        head.append(f"{x}Ears-Kind: spoken")
    if date is not None:
        head.append(f"Date: {date}")
    if start is not None:
        head.append(f"{x}Ears-Start: {start}")
    if rule is not None:
        head.append(f"{x}Ears-Rule: {rule}")
    body = words if words is not None else f"the words of {mid}"
    raw = ("\n".join(head) + "\n\n" + body + "\n").encode()
    return message_from_bytes(raw, policy=default_policy)


def mark(mid: str, stamp: str = T0, *, session: str = EVE, kind: str = "seen") -> _seat.Mark:
    return _seat.Mark(stamp, mid, kind, "eve", session, "ride-along")


def latest(box, events, session=EVE):
    heard = _say.latest_heard(box, events, session, ENV, signed=KEEPER_SIGNED)
    return None if heard is None else heard.turn.message_id


# ── 3.9a: the latest turn this session heard (N9) ───────────────────────────

def test_replying_to_the_latest_turn_heard_not_the_latest_call():
    """Spec: GIVEN turn T1 called eve, and a later turn T2, which called only
    ivan, was also given to Eve's session; THEN `In-Reply-To` is T2."""
    t1 = turn("t1@hq", rule=f"eve@{HOST}=eve", start="2030-01-01T11:59:00+00:00")
    t2 = turn("t2@hq", rule=f"ivan@{HOST}=ivan", start="2030-01-01T12:01:00+00:00")
    heard = _say.latest_heard([t1, t2], [mark("t1@hq", T0), mark("t2@hq", T1)], EVE, ENV, signed=KEEPER_SIGNED)
    assert heard is not None
    assert heard.turn.message_id == "t2@hq"
    assert heard.in_reply_to == "<t2@hq>"
    assert heard.given == datetime.fromisoformat(T1)


def test_latest_is_by_when_given_not_by_when_spoken():
    """N9's "latest heard" is the turn GIVEN most recently: an older turn
    given later (a catch-up) is the one replied to."""
    old = turn("old@hq", start="2030-01-01T10:50:00+00:00")
    new = turn("new@hq", start="2030-01-01T11:50:00+00:00")
    assert latest([old, new], [mark("new@hq", T0), mark("old@hq", T1)]) == "old@hq"


def test_nothing_heard_means_no_in_reply_to():
    """None heard -> no `In-Reply-To`: an empty box, a box of turns it holds
    no mark on, and marks on ids not in its box all answer None."""
    assert _say.latest_heard([], [], EVE, ENV, signed=KEEPER_SIGNED) is None
    assert latest([turn("a@hq")], []) is None
    assert latest([], [mark("a@hq")]) is None


def test_a_tie_in_one_ride_along_goes_to_the_later_ears_start():
    early = turn("early@hq", start="2030-01-01T11:59:00+00:00",
                 date="Tue, 01 Jan 2030 12:10:00 +0000")      # a later Date: loses to Start
    late = turn("late@hq", start="2030-01-01T11:59:30+00:00",
                date="Tue, 01 Jan 2030 12:00:00 +0000")
    events = [mark("late@hq", T0), mark("early@hq", T0)]       # the file order favours early
    assert latest([early, late], events) == "late@hq"


def test_a_tie_with_the_same_start_goes_to_the_later_date():
    a = turn("a@hq", date="Tue, 01 Jan 2030 12:00:05 +0000")
    b = turn("b@hq", date="Tue, 01 Jan 2030 12:00:01 +0000")
    assert latest([a, b], [mark("a@hq", T0), mark("b@hq", T0)]) == "a@hq"
    assert latest([a, b], [mark("b@hq", T0), mark("a@hq", T0)]) == "a@hq"


def test_a_turn_with_no_ears_start_loses_the_tie():
    bare = turn("bare@hq", start=None, date="Tue, 01 Jan 2030 12:50:00 +0000")
    timed = turn("timed@hq")
    assert latest([bare, timed], [mark("bare@hq", T0), mark("timed@hq", T0)]) == "timed@hq"


def test_given_is_the_first_mark_a_later_read_does_not_move_it():
    """A turn is given once: its later `read`/`done` marks are not a new
    giving, so re-reading an old turn does not make it the latest heard."""
    a, b = turn("a@hq"), turn("b@hq")
    events = [mark("a@hq", T0), mark("b@hq", T1), mark("a@hq", T2, kind="read")]
    assert latest([a, b], events) == "b@hq"


def test_a_done_mark_alone_counts_as_heard():
    """D8: "a turn with a done mark was heard"."""
    assert latest([turn("a@hq")], [mark("a@hq", kind="done")]) == "a@hq"


def test_only_this_sessions_marks_count():
    """Marks are per session (`_seat.marks_path`): another session's mark on
    a turn in this box is not this session hearing it."""
    a, b = turn("a@hq"), turn("b@hq")
    events = [mark("a@hq", T0), mark("b@hq", T1, session=IVAN)]
    assert latest([a, b], events) == "a@hq"
    assert latest([a, b], events, session=IVAN) == "b@hq"


def test_a_mark_on_plain_mail_is_not_a_turn_heard():
    """Only TRUSTED voice is a turn (O22 (F)): plain mail it was given later,
    and a forged `Ears-Kind` From another sender, are passed over."""
    voice = turn("voice@hq")
    plain = turn("plain@hq", kind=False)
    forged = turn("forged@hq", sender=f"ivan@{HOST}")
    events = [mark("voice@hq", T0), mark("plain@hq", T1), mark("forged@hq", T2)]
    assert latest([voice, plain, forged], events) == "voice@hq"


def test_unverified_voice_is_never_a_turn_heard():
    """SM-782: the same turns UNSIGNED (no verdict) are not turns: a reply
    threads on nothing, and nothing is believed of their speaker."""
    a, b = turn("a@hq"), turn("b@hq")
    events = [mark("a@hq", T0), mark("b@hq", T1)]
    assert _say.latest_heard([a, b], events, EVE, ENV) is None
    assert _say.turn(a, ENV) is None
    assert _say.turn(a, ENV, KEEPER_SIGNED) is not None


def test_an_unverified_turn_of_the_operators_holds_nothing():
    """SM-782: an unsigned `Ears-Speaker: judy@hq` From the keeper's address
    is not the operator speaking, so it holds no line."""
    t5 = turn("t5@hq", words="Hold on, don't merge that yet.")
    assert _say.operator_unread([t5], [], EVE, ENV, SC6_SETTINGS) is None


def test_the_x_spelling_is_read_too():
    """3.22's reader: the keeper as built writes `X-Ears-*` (D16)."""
    early = turn("early@hq", prefixed=True, start="2030-01-01T11:59:00+00:00")
    late = turn("late@hq", prefixed=True, start="2030-01-01T11:59:30+00:00")
    assert latest([early, late], [mark("early@hq", T0), mark("late@hq", T0)]) == "late@hq"


def test_bracketed_ids_and_duplicate_copies_are_one_turn():
    a = turn("a@hq")
    events = [_seat.Mark(T0, "<a@hq>", "seen", "eve", EVE)]
    assert latest([a, turn("a@hq")], events) == "a@hq"


# ── 3.9b: the operator's-words pre-check (N7) ───────────────────────────────

def test_the_operator_said_something_the_session_has_not_seen():
    """Spec: GIVEN Judy's turn T5, delivered to Eve's session's box from
    `heard+airpods`, of which the session has been given neither the mail
    nor any draft; THEN nothing is sent, T5's words are printed, exit 3."""
    t5 = turn("t5@hq", words="Hold on, don't merge that yet.")
    hold = _say.operator_unread([t5], [], EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED)
    assert hold is not None
    assert hold.exit_code == _say.HOLD_EXIT == 3
    assert [t.message_id for t in hold.turns] == ["t5@hq"]
    assert hold.text == "Hold on, don't merge that yet."


def test_a_turn_of_judys_this_session_was_given_does_not_hold():
    """The MARKS analogue of *the operator is still talking, and the session has heard it*
    (whose draft half is HELD with 3.18b): Judy's turn T4, given to Eve's
    session as mail, holds nothing, and is the turn the line replies to."""
    t4 = turn("t4@hq")
    events = [mark("t4@hq")]
    assert _say.operator_unread([t4], events, EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED) is None
    assert latest([t4], events) == "t4@hq"


def test_a_turn_of_the_ruperts_does_not_hold():
    doc = turn("doc@hq", speaker=RUPERT, words="It's red on master")
    assert _say.operator_unread([doc], [], EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED) is None


def test_every_unread_turn_of_the_operators_is_printed_oldest_first():
    late = turn("late@hq", start="2030-01-01T12:02:00+00:00", words="second")
    early = turn("early@hq", start="2030-01-01T12:01:00+00:00", words="first")
    seen = turn("seen@hq", words="already heard")
    doc = turn("doc@hq", speaker=RUPERT, words="not the operator's")
    hold = _say.operator_unread([late, seen, doc, early], [mark("seen@hq")], EVE, ENV,
                             SC6_SETTINGS, signed=KEEPER_SIGNED)
    assert hold is not None
    assert hold.text == "first\nsecond"


def test_another_sessions_mark_does_not_count_as_this_one_hearing_the_operator():
    t5 = turn("t5@hq")
    hold = _say.operator_unread([t5], [mark("t5@hq", session=IVAN)], EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED)
    assert hold is not None and hold.turns[0].message_id == "t5@hq"


def test_judy_is_sm474s_judy_setting_not_a_stand_in():
    """Who the operator is: `rules.operator()` (on the SC6 host, from config, `judy@*`:
    any host, any case; SM-727: a stranger's host has nobody); a locked `set
    operator` replaces it, so the config no longer holds."""
    assert rules.operator(SC6_SETTINGS) == ("judy@*",)
    assert rules.operator(rules.NO_SETTINGS) == ()
    assert not _say.is_operator("Judy@hq.example.ts.net", rules.NO_SETTINGS)
    assert _say.is_operator("Judy@hq.example.ts.net", SC6_SETTINGS)
    assert not _say.is_operator(RUPERT, SC6_SETTINGS)
    assert not _say.is_operator(None, SC6_SETTINGS)
    boss = rules.Settings(operator=("boss@*",))
    assert _say.operator_unread([turn("t@hq")], [], EVE, ENV, boss, signed=KEEPER_SIGNED) is None
    held = _say.operator_unread([turn("b@hq", speaker=f"Boss <boss@{HOST}>")], [], EVE, ENV, boss, signed=KEEPER_SIGNED)
    assert held is not None and held.turns[0].speaker == f"boss@{HOST}"


def test_a_forged_speaker_from_another_sender_holds_nothing():
    """`Ears-Speaker: judy@...` is believed only on trusted voice (O22 (F)):
    anyone can write the header, and must not be able to hold every line."""
    forged = turn("forged@hq", sender=f"ivan@{HOST}")
    assert _say.operator_unread([forged], [], EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED) is None


def test_the_x_ears_speaker_spelling_holds_too():
    t = turn("x@hq", prefixed=True)
    assert _say.operator_unread([t], [], EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED) is not None


def test_the_checks_read_no_heard_log_and_write_nothing(tmp_path, monkeypatch):
    """D7 step 8: pure. With HOME and the sessionmail home pointed at an
    empty tmp dir, both checks leave it empty."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sm"))
    box = [turn("a@hq")]
    _say.latest_heard(box, [mark("a@hq")], EVE, ENV, signed=KEEPER_SIGNED)
    _say.operator_unread(box, [], EVE, ENV, SC6_SETTINGS, signed=KEEPER_SIGNED)
    assert list(tmp_path.iterdir()) == []
