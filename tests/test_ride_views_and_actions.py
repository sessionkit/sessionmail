"""SM-442 x SM-474, on ONE ride-along line (refresh-001's merge of master).

SM-442 gives each queue entry as a VIEW: the header line, with its
additions (`extras`: a level that is not `normal`, a trusted voice turn's
device), and a body under a batch budget. SM-474 puts the ACTION that
applied (D7) on the same line, as `(ride)`, and a count of the actions on a
coalesced line. The merge keeps both: the extras, then the action LAST, so
neither a long subject nor the additions ever cost the line its action.

The view tests (test_views, test_ride_budget_marks) run with no rules
layers, so their lines carry no action; these run with the layers the
scoop really loads, which is where the two halves meet.
"""

from __future__ import annotations

import re

import pytest

from sessionmail import _seat, _views, pager

from test_views import (  # noqa: F401  (scoop is a fixture)
    KEEPER_FROM, _arrive, _given, _raw, _words, scoop,
)

#: The action, last on the line: `(ride)`, `(wake)`, ... - one word and,
#: on a coalesced line, a count.
ACTION_LAST = re.compile(r" \([a-z]+(?: \d+)?(?:, [a-z]+ \d+)*\)$")

_REAL_LAYERS = pager._layers_or_none


@pytest.fixture
def judged(scoop, monkeypatch):  # noqa: F811 - the imported fixture
    """The scratch seat, with the scoop's rules layers put back: every line
    carries the action that applied."""
    monkeypatch.setattr(pager, "_layers_or_none", _REAL_LAYERS)
    return scoop


class _Parsed:
    date = None
    sender_local = "eve"

    def __init__(self, subject: str = "a subject"):
        self.subject = subject


def _entry(recovered: str | None = None):
    return _seat.QueueEntry("1.M1P1.host", None, recovered=recovered)


# ── the line, pure ──────────────────────────────────────────────────────

def test_the_action_comes_after_the_extras():
    line = pager._queue_line(_entry(), _Parsed("hello"), ("priority: urgent",), action="wake")
    assert line == "[mail] --:-- eve hello  (priority: urgent) (wake)"


def test_a_long_subject_keeps_both_its_extras_and_its_action():
    line = pager._queue_line(_entry(), _Parsed("s" * 300), ("priority: urgent",),
                             action="ride")
    assert len(line) == pager.LINE_WIDTH
    assert line.endswith("…  (priority: urgent) (ride)")


def test_with_no_extras_the_line_is_master_s_fit():
    """No additions: the line is SM-474's `_fit` of SM-442's stripped line,
    byte for byte, with the action after the cut."""
    long = pager._queue_line(_entry(), _Parsed("s" * 300), action="ride")
    assert len(long) == pager.LINE_WIDTH and long.endswith("… (ride)")
    short = pager._queue_line(_entry(), _Parsed("hi"), action="ride")
    assert short == pager._fit("[mail] --:-- eve hi", "ride")


def test_no_action_leaves_the_line_as_the_view_made_it():
    with_none = pager._queue_line(_entry(), _Parsed("hi"), ("priority: urgent",))
    assert with_none == pager._queue_line(_entry(), _Parsed("hi"), ("priority: urgent",),
                                          action="")
    assert not ACTION_LAST.search(with_none)


def test_the_recovered_note_sits_between_the_extras_and_the_action():
    line = pager._queue_line(_entry(recovered="taking.1"), _Parsed("hi"),
                             ("priority: urgent",), action="ride")
    assert line.endswith("(priority: urgent)  (recovered from taking.1) (ride)")


def test_no_control_character_rides_in_on_the_action():
    line = pager._queue_line(_entry(), _Parsed("hi"), ("priority: urgent",),
                             action="ri\x1b[2Jde\n")
    assert "\x1b" not in line and "\n" not in line
    assert line.endswith("(ri[2Jde)")


# ── through `pager status --new`, judged ────────────────────────────────

def test_a_voice_turn_line_carries_its_device_then_its_action(judged, keeper_signs):
    body = _words(300)
    _arrive(judged, _raw(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
                         body=body, subject="hey alpha"))
    lines = _given().output.splitlines()
    head = lines[0]
    # SM-562: the heard bracket carries the device; no subject preview.
    assert head.startswith("[heard ") and ACTION_LAST.search(head), head
    assert ACTION_LAST.sub("", head).endswith(" airpods ?]"), head
    assert "hey alpha" not in head, head
    assert body in lines                                  # still given whole


def test_an_unverified_turn_line_carries_its_device_then_its_action(judged):
    """SM-782: the same turn unsigned: the UNVERIFIED bracket, no speaker,
    its action last, then the fence and the words."""
    body = _words(300)
    _arrive(judged, _raw(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
                         body=body, subject="hey alpha"))
    lines = _given().output.splitlines()
    head = lines[0]
    assert head.startswith("[heard UNVERIFIED ") and ACTION_LAST.search(head), head
    assert ACTION_LAST.sub("", head).endswith(" airpods]"), head
    assert lines[1] == _views.UNVERIFIED_FENCE and body in lines


def test_a_level_past_the_budget_keeps_its_level_and_its_action(judged, monkeypatch):
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "100")
    for i in range(4):
        _arrive(judged, _raw("eve@hq", body=_words(1_990), subject=f"b{i}"))  # 7,960
    _arrive(judged, _raw("eve@hq", "Priority: non-urgent", body=_words(500),
                         subject="leveled"))
    lines = _given().output.splitlines()
    leveled = [ln for ln in lines if "leveled" in ln]
    assert len(leveled) == 1 and ACTION_LAST.search(leveled[0]), leveled
    assert ACTION_LAST.sub("", leveled[0]).endswith("(priority: non-urgent)")
    assert _words(500) not in lines                       # the header view only
    assert lines[-1].startswith("[mail] 1 past the 8,000-character budget")


def test_a_coalesced_line_counts_its_actions_and_spends_no_budget(judged):
    for i in range(pager.GROUP_AFTER):
        _arrive(judged, _raw("bridge@hq", "X-Original-To: bridge@hq",
                             body=_words(3_000), subject=f"chunk {i}"))
    body = _words(1_900)
    _arrive(judged, _raw("eve@hq", "X-Original-To: alpha@hq", body=body, subject="own"))
    lines = _given().output.splitlines()
    assert lines[0].startswith(f"[mail] {pager.GROUP_AFTER} for bridge"
                               " - `message list` to see them (")
    assert lines[0].endswith(f" {pager.GROUP_AFTER})"), lines[0]
    assert body in lines
    assert not any(" past the " in ln for ln in lines)


def test_a_superseding_mail_is_given_judged_and_its_target_is_not(judged, monkeypatch):
    """The supersede step reads the scoop's entries, which carry the action
    since SM-474: it must still find the target, and give only the
    correction, with its action."""
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "100")
    old = _raw("eve@hq", body="the old text", subject="first")
    old_mid = re.search(r"Message-ID: (<[^>]+>)", old).group(1)
    _arrive(judged, old)
    _arrive(judged, _raw("eve@hq", f"Supersedes: {old_mid}", body="the new text",
                         subject="second"))
    lines = _given().output.splitlines()
    assert "the old text" not in lines and "the new text" in lines
    head = [ln for ln in lines if "second" in ln]
    assert len(head) == 1 and ACTION_LAST.search(head[0]), lines
