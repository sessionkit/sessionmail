"""pager-genie 3.3: supersede in the pager (design D3, O6 RULED).

openspec/changes/pager-genie, tasks.md 3.3: a superseding mail stands alone
and is given whole; a superseded one not yet given is not given and is
marked `seen`, reason `superseded`. Both orders. *(A superseding mail stands
alone, and a superseded one is not given twice)*

Hermetic, like test_views.py, whose scratch seat and helpers this reuses:
SESSIONMAIL_HOME and SESSION_MAIL_ROOT sit under tmp_path, notmuch is faked,
and no live box, index or listener is touched.
"""

from __future__ import annotations

import pytest

from sessionmail import pager
from test_ride_budget_marks import _marks_of, _mid, _unread, _voice, ride  # noqa: F401
from test_views import _arrive, _given, _raw, _words, scoop  # noqa: F401

SEEN = ("seen", "ride-along")
SUPERSEDED = ("seen", "superseded")


def _pair(m_body: str = "the first word", c_body: str = "the corrected word"):
    """Mail M, and its correction C (`Supersedes:` M)."""
    m = _raw("eve@hq", body=m_body, subject="first")
    c = _raw("eve@hq", f"Supersedes: <{_mid(m)}>", body=c_body, subject="corrected")
    return m, c


# ── both waiting in one ride-along: only the superseding one is given ───

@pytest.mark.parametrize("order", ["M then C", "C then M"])
def test_the_correction_arrives_before_the_ride_along(ride, order):
    """Scenario *The correction arrives before the ride-along*, in both queue
    orders: C is given whole, M is not given, and M is `seen`, `superseded`."""
    m, c = _pair()
    for raw in ((m, c) if order == "M then C" else (c, m)):
        _arrive(ride, raw)
    lines = _given().output.splitlines()
    assert "the corrected word" in lines                 # C, whole
    assert "the first word" not in lines                 # M, not given
    assert not any(ln.endswith(" first") for ln in lines)  # not even its header line
    assert sum(1 for ln in lines if ln.startswith("[mail] ")) == 1
    assert _marks_of(_mid(m)) == [SUPERSEDED]
    assert _marks_of(_mid(c)) == [SEEN]


def test_a_superseded_mail_is_not_given_by_a_later_ride_along_either(ride):
    """It is filed with the rest of the taking: a second scoop has nothing."""
    m, c = _pair()
    _arrive(ride, m)
    _arrive(ride, c)
    _given()
    assert "the first word" not in _given().output


def test_a_long_correction_is_given_its_own_view(ride):
    """Standing alone: C's view is C's own. Over the cap it is the text view
    with its pointer, as any mail's is; M's having been superseded neither
    shortens nor lengthens it."""
    body = _words(5_412)
    m, c = _pair(c_body=body)
    _arrive(ride, m)
    _arrive(ride, c)
    out = _given().output
    assert body[:2_000] in out and "3,412 more - message read" in out
    assert "the first word" not in out


def test_a_chain_gives_only_the_last(ride):
    """M, C superseding M, D superseding C, all waiting: only D is given;
    M and C are both `superseded`."""
    m, c = _pair()
    d = _raw("eve@hq", f"Supersedes: <{_mid(c)}>", body="the last word", subject="last")
    for raw in (m, c, d):
        _arrive(ride, raw)
    lines = _given().output.splitlines()
    assert "the last word" in lines
    assert "the first word" not in lines and "the corrected word" not in lines
    assert _marks_of(_mid(m)) == [SUPERSEDED]
    assert _marks_of(_mid(c)) == [SUPERSEDED]
    assert _marks_of(_mid(d)) == [SEEN]


def test_a_superseded_mail_is_not_counted_into_a_burst(scoop):
    """Taken out before the grouping: GROUP_AFTER arrivals for one address,
    one of them superseded by another, leave GROUP_AFTER - 1 shown, which is
    under the threshold, so each still gets its own line."""
    n = pager.GROUP_AFTER
    head = "X-Original-To: alpha@hq"
    m = _raw("eve@hq", head, body="the first word", subject="first")
    c = _raw("eve@hq", head, f"Supersedes: <{_mid(m)}>", body="the corrected word",
             subject="corrected")
    others = [_raw("eve@hq", head, body=f"other {i}", subject=f"o{i}") for i in range(n - 2)]
    for raw in (m, c, *others):
        _arrive(scoop, raw)
    out = _given().output
    assert " for alpha" not in out                       # no burst line
    assert "the corrected word" in out and "the first word" not in out


def test_a_superseded_mail_does_not_list_under_unread(ride):
    """`message list` already hides it; the ride-along agrees, so the seat
    sees the correction and never the stale one, by either door."""
    m, c = _pair()
    _arrive(ride, m)
    _arrive(ride, c)
    _given()
    assert "first" not in _unread().replace("corrected", "")


# ── the correction arrives after ────────────────────────────────────────

def test_the_correction_arrives_after(ride):
    """Scenario *The correction arrives after*: M was given by an earlier
    ride-along; C, arriving later, is given whole, with nothing left out
    because M was seen."""
    m, c = _pair(c_body=_words(1_500))
    _arrive(ride, m)
    first = _given().output
    assert "the first word" in first
    _arrive(ride, c)
    lines = _given().output.splitlines()
    assert _words(1_500) in lines                        # C, every character
    assert sum(1 for ln in lines if ln.startswith("[mail] ")) == 1
    assert _marks_of(_mid(m)) == [SEEN]                  # never `superseded` after the fact
    assert _marks_of(_mid(c)) == [SEEN]


def test_a_correction_to_a_message_never_seen_here_is_given(ride):
    """Its target is in no queue of this session: C stands alone and is given."""
    c = _raw("eve@hq", "Supersedes: <elsewhere@example.invalid>", body="standing alone",
             subject="alone")
    _arrive(ride, c)
    assert "standing alone" in _given().output.splitlines()


# ── voice: a correction to a turn ───────────────────────────────────────

def test_a_voice_correction_is_given_whole_and_done(ride, keeper_signs):
    """A turn T and its correction T2 (a 2,500-character turn) both waiting:
    T2 is given whole, past the mail cap (a voice body is never cut by it),
    and is `done`; T is `seen`, `superseded`, and NOT `done` (it was never
    given). A TRUSTED turn (SM-782): the keeper signed both."""
    t = _voice("hey alpha, the first take", "a turn")
    body = _words(2_500)
    t2 = _voice(body, "a turn, corrected", f"Supersedes: <{_mid(t)}>")
    _arrive(ride, t)
    _arrive(ride, t2)
    lines = _given().output.splitlines()
    assert body in lines
    assert "hey alpha, the first take" not in lines
    assert _marks_of(_mid(t)) == [SUPERSEDED]
    assert _marks_of(_mid(t2)) == [SEEN, ("done", "ride-along whole (voice)")]
