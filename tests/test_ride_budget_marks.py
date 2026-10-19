"""pager-genie 3.2c, 3.2d and 3.4: the batch budget, the negative share, and
the marks a view earns.

openspec/changes/pager-genie, tasks.md:

  - 3.2c the batch budget: 8,000 characters of bodies per ride-along;
    entries past it get the header view plus one counting line; a voice
    turn that crosses it is given whole and the budget closes after it;
    coalescing unchanged. *(The ride-along carries a view of each message,
    by kind)*
  - 3.2d the negative share: the mail ride-along prints no mouth lines and
    never opens the heard log. *(The ride-along carries the other seats'
    mouth lines)*
  - 3.4 marks for views (D5): a mail given whole writes `read` as well as
    `seen` (reason `ride-along whole`); a cut view writes `seen` only; a
    voice turn given whole writes `done` (reason `ride-along whole
    (voice)`), and no `not done` line counts it. *(Delivered whole is read,
    and a turn given whole is done)*

    ⏸ The `read` half is HELD until SM-446 is deployed (a gate
    rule): `pager.READ_ON_WHOLE` is False, so a
    mail given whole is `seen` only and still lists under `--unread`. The
    tests below pin the hold; the ones named `..._when_released` pin the
    ruled O13 behaviour behind the flag, so flipping it is proven too.

SM-782: "a voice turn" above is a TRUSTED one, signed by the keeper
(`KEEPER_SIGNED`, or `keeper_signs` for a scoop). The same turn unsigned is
UNVERIFIED voice and is a MAIL to the budget and the marks: capped at the
text cap, refused past the budget, never `done` (pinned below).

Hermetic, like test_views.py, whose scratch seat and helpers this reuses:
SESSIONMAIL_HOME, SESSION_MAIL_ROOT and VOICEMODE_BASE_DIR all sit under
tmp_path, notmuch is faked, and no live box, index or listener is touched.
"""

from __future__ import annotations

import builtins
import inspect
import io
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat, _views, message, pager
# The module under its own name: `_voice` below is a test helper, and an
# import under that name would be rebound by it (SM-746).
from sessionmail import _voice as voice_mod
from conftest import KEEPER_SIGNED
from test_views import (  # noqa: F401  (scoop is a fixture)
    ENV, KEEPER_FROM, SESSION, _arrive, _given, _msg, _raw, _words, scoop,
)


def _marks() -> list[tuple[str, str, str]]:
    """(message-id, mark, reason) for every marks line this session wrote."""
    path = _seat.session_root(SESSION) / "marks"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        _stamp, mid, mark, _seat_name, _session, *reason = line.split()
        out.append((mid, mark, " ".join(reason)))
    return out


def _marks_of(mid: str) -> list[tuple[str, str]]:
    return [(m, r) for i, m, r in _marks() if i == mid]


def _mid(raw: str) -> str:
    return raw.split("Message-ID: <", 1)[1].split(">", 1)[0]


def _unread() -> str:
    """`message list --unread` in this session, over its own box."""
    r = CliRunner().invoke(message.cli, ["list", "--unread"], prog_name="message")
    assert r.exit_code == 0, r.output
    return r.output


def _voice(body: str, subject: str = "a turn", *more: str) -> str:
    return _raw(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods", *more,
                body=body, subject=subject)


@pytest.fixture
def ride(scoop, monkeypatch):
    """The scratch seat, with grouping raised out of the way: every `_raw`
    arrival shares one (absent) X-Original-To, and four would coalesce."""
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "100")
    return scoop


# ── the budget, pure (3.2c) ─────────────────────────────────────────────

def _v(chars: int, voice: bool = False, cut: int = 0, textless: bool = False):
    return _views.View("whole" if voice else "text", voice, (), ("x",), chars, cut,
                       textless)


def test_the_budget_is_8000_characters():
    assert _views.BATCH_BUDGET == 8_000
    assert _views.Budget().limit == 8_000


def test_views_that_fit_are_admitted_and_spent():
    b = _views.Budget()
    assert b.admit(_v(2_000)) and b.admit(_v(2_000)) and b.admit(_v(4_000))
    assert (b.spent, b.closed, b.past) == (8_000, False, 0)
    assert b.admit(_v(0))                     # an empty body costs nothing
    assert b.line("mail") is None


def test_a_mail_that_would_cross_gets_the_header_and_closes_the_budget():
    b = _views.Budget()
    assert b.admit(_v(7_000))
    assert not b.admit(_v(1_001))
    assert (b.spent, b.closed, b.past) == (7_000, True, 1)


def test_a_voice_turn_that_would_cross_is_given_and_the_budget_closes_after_it():
    b = _views.Budget()
    assert b.admit(_v(7_000))
    assert b.admit(_v(2_500, voice=True))
    assert (b.spent, b.closed, b.past) == (9_500, True, 0)


def test_after_the_budget_closes_a_voice_turn_is_still_admitted_and_mail_is_not():
    b = _views.Budget()
    assert not b.admit(_v(9_000))                 # a mail crosses: closed
    assert b.admit(_v(300, voice=True))           # N13: a turn is whole anyway
    assert not b.admit(_v(0))                     # closed means closed for mail
    assert (b.spent, b.closed, b.past) == (300, True, 2)


def test_a_voice_turn_that_fits_does_not_close_the_budget():
    b = _views.Budget()
    assert b.admit(_v(8_000, voice=True))
    assert not b.closed
    assert b.admit(_v(0))


def test_after_the_budget_closes_every_entry_is_counted():
    b = _views.Budget()
    assert not b.admit(_v(9_000))
    b.skip()
    b.skip()
    assert b.past == 3
    assert b.line("mail") == ("[mail] 3 past the 8,000-character budget, header line"
                              " only - `message list --unread` to read them")


# ── what a view earns (3.4, pure) ───────────────────────────────────────

@pytest.mark.parametrize("shape, whole", [
    ({"chars": 900}, True),                               # nothing cut
    ({"chars": 0}, True),                                 # an EMPTY body: nothing left to read
    ({"chars": 2_000, "cut": 3_412}, False),              # capped
    ({"chars": 0, "textless": True}, False),              # NO text part: its words are elsewhere
    ({"chars": 16_000, "voice": True, "cut": 24_000}, False),  # voice past the 3.1 ceiling
])
def test_given_whole_is_every_character_and_a_text_part(shape, whole):
    assert _v(**shape).given_whole is whole


@pytest.fixture
def released(monkeypatch):
    """The `read` half of 3.4 released, as it will be once SM-446 is
    deployed: the ruled O13 behaviour behind `pager.READ_ON_WHOLE`."""
    monkeypatch.setattr(pager, "READ_ON_WHOLE", True)


def test_the_read_half_is_held_until_sm_446_is_deployed():
    """Gate rule: until SM-446 is
    DEPLOYED, 3.4 writes `seen` ONLY on a whole text view."""
    assert pager.READ_ON_WHOLE is False


def test_while_held_a_whole_mail_earns_nothing_and_a_whole_turn_earns_done():
    assert pager._earned(_v(900)) == ()
    assert pager._earned(_v(900, voice=True)) == (("done", "ride-along whole (voice)"),)
    assert pager._earned(_v(2_000, cut=1)) == ()
    assert pager._earned(_v(0, textless=True)) == ()


def test_a_whole_mail_earns_read_and_a_whole_turn_earns_done_when_released(released):
    assert pager._earned(_v(900)) == (("read", "ride-along whole"),)
    assert pager._earned(_v(900, voice=True)) == (("done", "ride-along whole (voice)"),)
    assert pager._earned(_v(2_000, cut=1)) == ()
    assert pager._earned(_v(0, textless=True)) == ()


@pytest.mark.parametrize("read_on_whole", [False, True])
def test_the_real_views_earn_by_their_kind(read_on_whole, monkeypatch):
    monkeypatch.setattr(pager, "READ_ON_WHOLE", read_on_whole)
    mail = _views.view(_msg("eve@hq", body=_words(900)), "1", ENV)
    turn = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(2_500)), "2", ENV,
                       KEEPER_SIGNED)
    cut = _views.view(_msg("eve@hq", body=_words(5_412)), "3", ENV)
    unverified = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(900)), "4", ENV)
    assert pager._earned(mail) == ((("read", "ride-along whole"),) if read_on_whole else ())
    assert pager._earned(turn) == (("done", "ride-along whole (voice)"),)
    assert pager._earned(cut) == ()
    # SM-782: unverified voice given whole earns what MAIL earns, never `done`
    assert pager._earned(unverified) == pager._earned(mail)


# ── the budget, through `pager status --new` (3.2c) ─────────────────────

def test_scoop_entries_past_the_budget_get_the_header_line_and_one_count(ride):
    """Four 1,900-character mails spend 7,600; the fifth would cross, so it
    and everything after it get the header line only, and ONE line counts
    them. The order of what is printed is the queue's."""
    bodies = [_words(1_900) for _ in range(5)]
    for i, body in enumerate(bodies):
        _arrive(ride, _raw("eve@hq", body=body, subject=f"m{i}"))
    _arrive(ride, _raw("eve@hq", body="tiny", subject="hq"))
    lines = _given().output.splitlines()
    heads = [ln for ln in lines if ln.startswith("[mail] ") and " past the " not in ln]
    assert [h.split()[-1] for h in heads] == ["m0", "m1", "m2", "m3", "m4", "hq"]
    assert lines.count(bodies[0]) == 4           # the four that fit, whole
    assert "tiny" not in lines                   # the budget stays closed
    assert lines[-1] == ("[mail] 2 past the 8,000-character budget, header line"
                         " only - `message list --unread` to read them")
    body_chars = sum(len(ln) for ln in lines if not ln.startswith("[mail] "))
    assert body_chars <= 8_000


def test_scoop_a_voice_turn_that_crosses_is_given_whole_then_the_budget_closes(ride, keeper_signs):
    mails = [_words(1_900) for _ in range(3)]            # 5,700
    for i, body in enumerate(mails):
        _arrive(ride, _raw("eve@hq", body=body, subject=f"m{i}"))
    turn = _words(2_500)                                 # 8,200: crosses
    _arrive(ride, _voice(turn, "hey alpha"))
    _arrive(ride, _raw("eve@hq", body="after", subject="late"))
    lines = _given().output.splitlines()
    assert turn in lines                                 # whole, never cut
    assert "after" not in lines
    late = [ln for ln in lines if ln.endswith("late")]
    assert len(late) == 1
    assert lines[-1].startswith("[mail] 1 past the 8,000-character budget")


def test_scoop_the_header_view_past_the_budget_keeps_its_additions(ride):
    """Past the budget a MAIL is the HEADER view: its level (3.13) still
    shows; only the body is gone."""
    _arrive(ride, _raw("eve@hq", body=_words(1_990), subject="a"))
    for i in range(3):
        _arrive(ride, _raw("eve@hq", body=_words(1_990), subject=f"b{i}"))  # 7,960
    _arrive(ride, _raw("eve@hq", "Priority: non-urgent", body=_words(500), subject="leveled"))
    lines = _given().output.splitlines()
    leveled = [ln for ln in lines if "leveled" in ln]
    assert leveled == [leveled[0]] and leveled[0].endswith("(priority: non-urgent)")
    assert _words(500) not in lines
    assert lines[-1].startswith("[mail] 1 past the 8,000-character budget")


def test_scoop_a_voice_turn_after_the_budget_closed_is_still_given_whole(ride, keeper_signs):
    """N13 (RULED): every turn reaches every subscriber WHOLE, and cutting a
    turn to its header view is owed M4 first (tasks.md 2.3). So a turn
    queued behind a closed budget is given whole, with its device, and is
    `done`; the mail around it still gets the header line only. This is the
    spec's *The page is still a nudge*: the re-arm's ride-along carries the
    turn's whole view, whatever arrived before it."""
    for i in range(4):
        _arrive(ride, _raw("eve@hq", body=_words(1_990), subject=f"b{i}"))  # 7,960
    _arrive(ride, _raw("eve@hq", body=_words(500), subject="crosses"))      # closes it
    turn_body = _words(300)
    turn = _voice(turn_body, "hey alpha")
    _arrive(ride, turn)
    _arrive(ride, _raw("eve@hq", body="tiny", subject="after"))
    lines = _given().output.splitlines()
    # SM-562: a spoken turn's line is the heard bracket, device inside it.
    head = [ln for ln in lines if ln.startswith("[heard ")]
    assert len(head) == 1 and head[0].endswith(" airpods ?]"), head
    assert not any("hey alpha" in ln for ln in lines)
    assert turn_body in lines                                # whole, not cut
    assert _words(500) not in lines and "tiny" not in lines  # mail stays shut out
    assert lines[-1].startswith("[mail] 2 past the 8,000-character budget")
    assert _marks_of(_mid(turn)) == [("seen", "ride-along"),
                                     ("done", "ride-along whole (voice)")]


def test_scoop_coalescing_is_unchanged_and_spends_no_budget(scoop):
    """A burst to one address is still ONE counting line (group_after), and a
    count carries no body, so it spends nothing: a mail after it is given."""
    for i in range(pager.GROUP_AFTER):
        _arrive(scoop, _raw("bridge@hq", "X-Original-To: bridge@hq",
                            body=_words(3_000), subject=f"chunk {i}"))
    body = _words(1_900)
    _arrive(scoop, _raw("eve@hq", "X-Original-To: alpha@hq", body=body, subject="own"))
    lines = _given().output.splitlines()
    assert lines[0] == (f"[mail] {pager.GROUP_AFTER} for bridge"
                        " - `message list` to see them")
    assert body in lines
    assert not any(" past the " in ln for ln in lines)


def test_scoop_under_the_budget_prints_no_count(ride):
    _arrive(ride, _raw("eve@hq", body=_words(900), subject="one"))
    assert " past the " not in _given().output


# ── the marks a view earns, through the scoop (3.4) ─────────────────────

def test_scoop_while_held_a_whole_mail_is_seen_only_and_stays_unread(ride):
    """The HOLD (until SM-446 is deployed): 900 characters given whole are
    `seen` only, so `message list --unread` still lists the mail, and the
    store's mitigation (`--unread` after a subagent) still finds it."""
    raw = _raw("eve@hq", body=_words(900), subject="whole one")
    _arrive(ride, raw)
    _given()
    assert _marks_of(_mid(raw)) == [("seen", "ride-along")]
    assert "whole one" in _unread()


def test_scoop_a_whole_mail_leaves_unread_when_released(ride, released):
    """Scenario *A whole mail leaves unread*, behind the flag: 900
    characters given whole are `seen` and `read`, so `message list
    --unread` does not list it."""
    raw = _raw("eve@hq", body=_words(900), subject="whole one")
    _arrive(ride, raw)
    _given()
    assert _marks_of(_mid(raw)) == [("seen", "ride-along"), ("read", "ride-along whole")]
    assert "whole one" not in _unread()


def test_scoop_a_cut_mail_stays_unread(ride):
    """Scenario *A cut mail stays unread*: 5,412 characters, capped, are
    `seen` only."""
    raw = _raw("eve@hq", body=_words(5_412), subject="capped one")
    _arrive(ride, raw)
    _given()
    assert _marks_of(_mid(raw)) == [("seen", "ride-along")]
    assert "capped one" in _unread()


def test_scoop_a_mail_past_the_budget_stays_unread(ride, released):
    """Released, so the contrast shows: the first mail (whole) is read, the
    late one (header line only, past the budget) is seen only."""
    first = _raw("eve@hq", body=_words(1_900), subject="first")
    _arrive(ride, first)
    for i in range(3):
        _arrive(ride, _raw("eve@hq", body=_words(1_900), subject=f"mid {i}"))
    late = _raw("eve@hq", body=_words(1_900), subject="late one")
    _arrive(ride, late)
    _given()
    assert _marks_of(_mid(first)) == [("seen", "ride-along"), ("read", "ride-along whole")]
    assert _marks_of(_mid(late)) == [("seen", "ride-along")]
    listed = _unread()
    assert "late one" in listed and "first" not in listed


def test_scoop_a_mail_with_no_text_part_stays_unread(ride):
    raw = ("From: eve@hq\nTo: alpha@hq\nSubject: html only\nMessage-ID: <h@example.invalid>\n"
           "Content-Type: text/html\n\n<p>hello</p>\n")
    _arrive(ride, raw)
    _given()
    assert _marks_of("h@example.invalid") == [("seen", "ride-along")]


def test_scoop_a_whole_turn_is_done_and_mail_is_never_done(ride, keeper_signs):
    turn = _voice(_words(2_500), "hey alpha")
    mail = _raw("eve@hq", body=_words(900), subject="plain")
    _arrive(ride, turn)
    _arrive(ride, mail)
    _given()
    assert _marks_of(_mid(turn)) == [("seen", "ride-along"),
                                     ("done", "ride-along whole (voice)")]
    assert ("done" not in {m for _i, m, _r in _marks() if _i == _mid(mail)})


def test_scoop_an_unverified_turn_that_crosses_gets_the_header_line_only(ride):
    """SM-782: the 3.2c case above, the turn UNSIGNED. It is spent like a
    mail: the budget refuses it (header line only, counted), never the
    always-admit voice branch."""
    for i in range(4):
        _arrive(ride, _raw("eve@hq", body=_words(1_900), subject=f"m{i}"))   # 7,600
    turn = _words(2_500)                     # capped at 2,000, it would still cross
    _arrive(ride, _voice(turn, "hey alpha"))
    lines = _given().output.splitlines()
    assert not any(turn[:2_000] in ln for ln in lines)
    assert _views.UNVERIFIED_FENCE not in lines
    head = [ln for ln in lines if ln.startswith("[heard ")]
    assert len(head) == 1 and head[0].startswith("[heard UNVERIFIED "), head
    assert lines[-1].startswith("[mail] 1 past the 8,000-character budget")


def test_scoop_an_unverified_turn_given_whole_is_never_done(ride):
    """SM-782: an unsigned turn short enough to be given whole is `seen`
    (and only what a mail earns), never `done`, so its `not done` reminder
    still counts it."""
    turn = _voice(_words(900), "hey alpha")
    _arrive(ride, turn)
    out = _given().output
    assert _views.UNVERIFIED_FENCE in out and _words(900) in out
    assert "done" not in {m for _i, m, _r in _marks() if _i == _mid(turn)}


def test_scoop_a_turn_past_the_ceiling_is_seen_only(ride):
    turn = _voice(_words(40_000), "a long turn")
    _arrive(ride, turn)
    out = _given().output
    assert "more - message read" in out
    assert _marks_of(_mid(turn)) == [("seen", "ride-along")]


def test_scoop_a_heard_turn_needs_no_mark(ride, monkeypatch, keeper_signs):
    """Scenario *A heard turn needs no mark*: when the reminder interval
    passes, the `not done` line counts the whole MAIL (read is not terminal)
    and not the whole voice turn (done). A negative interval makes every
    giving already due, so no clock is waited on."""
    _arrive(ride, _voice(_words(2_500), "hey alpha"))
    _arrive(ride, _raw("eve@hq", body=_words(900), subject="plain"))
    _given()
    monkeypatch.setenv(_seat.REMIND_VAR, "-1")
    out = _given().output.splitlines()
    assert len(out) == 1 and out[0].startswith("[mail] 1 not done, oldest "), out


def test_a_heard_turn_is_not_in_the_successors_boot_status(ride, monkeypatch, keeper_signs):
    """Scenario *A heard turn needs no mark*, its second half (do-004
    verdict N3): "the successor's boot status does not list it". R10 with N8
    (spec.md "A predecessor's turns never reach its successor"): marks, the
    queue and the box are all per SESSION, so a successor reads its own box
    and none of the predecessor's. The control: the PREDECESSOR's own boot
    status still lists its not-done mail (read is not terminal) and not the
    done turn, so the successor's quiet is not the view being blind.

    It pins what this package owns - a status that reads only its own
    session - and not delivery: whether a device copy reaches the successor
    at all is the subscription's (per session, 3.29), not the status's."""
    turn = _voice(_words(2_500), "hey alpha")
    _arrive(ride, turn)
    _arrive(ride, _raw("eve@hq", body=_words(900), subject="plain"))
    _given()
    before = CliRunner().invoke(pager.cli, ["status", "--all"], prog_name="pager")
    assert "plain" in before.output and "hey alpha" not in before.output, before.output

    successor = "sess-b"
    for d in ("tmp", "new", "cur"):
        (ride / successor / d).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", successor)
    monkeypatch.setenv(_seat.REMIND_VAR, "-1")
    boot = CliRunner().invoke(pager.cli, ["status", "--all"], prog_name="pager")
    assert boot.exit_code == pager.EX_NOTHING, boot.output
    assert "hey alpha" not in boot.output and "plain" not in boot.output
    new = CliRunner().invoke(pager.cli, ["status", "--new"], prog_name="pager")
    assert "hey alpha" not in new.output and "not done" not in new.output, new.output


def test_scoop_coalesced_entries_are_seen_only(scoop):
    raws = [_raw("bridge@hq", "X-Original-To: bridge@hq", body="x", subject=f"c{i}")
            for i in range(pager.GROUP_AFTER)]
    for raw in raws:
        _arrive(scoop, raw)
    _given()
    for raw in raws:
        assert _marks_of(_mid(raw)) == [("seen", "ride-along")]


# ── the negative share (3.2d) ───────────────────────────────────────────

@pytest.fixture
def heard_log(tmp_path, monkeypatch):
    """A heard log in the scratch root, holding another seat's `said` row,
    and a recorder that notes any open or listing under the voicemode base.
    The recorder never raises: the scoop swallows a view's exceptions, so a
    raise would be hidden, and a record cannot be."""
    base = tmp_path / "voicemode"
    conv = base / "logs" / "conversations"
    conv.mkdir(parents=True)
    log = conv / "heard_2030-10-03.jsonl"
    log.write_text(json.dumps({"seq": 1, "type": "said", "session": "rupert-sess",
                               "who": "rupert", "text": "The build is green"}) + "\n")
    (base / "state" / "heard-cursor").mkdir(parents=True)
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(base))
    touched: list[str] = []

    def watch(real):
        def wrapped(path, *a, **k):
            if isinstance(path, (str, os.PathLike)) and str(base) in os.fsdecode(path):
                touched.append(os.fsdecode(path))
            return real(path, *a, **k)
        return wrapped

    monkeypatch.setattr(builtins, "open", watch(builtins.open))
    monkeypatch.setattr(io, "open", watch(io.open))
    monkeypatch.setattr(os, "open", watch(os.open))
    monkeypatch.setattr(os, "listdir", watch(os.listdir))
    monkeypatch.setattr(os, "scandir", watch(os.scandir))
    return touched


def test_the_mail_ride_along_never_opens_the_heard_log(ride, heard_log):
    _arrive(ride, _raw("eve@hq", body=_words(900), subject="mail only"))
    _arrive(ride, _voice(_words(300), "a turn"))
    out = _given().output
    assert "mail only" in out and "[heard " in out          # the scoop did run
    assert heard_log == []                                   # and read nothing there
    assert "The build is green" not in out
    assert "rupert" not in out and "said" not in out


def test_the_pager_holds_no_heard_log_reader():
    """The same guard at the source: nothing on the ride-along's path names
    the heard log, its directory or voicemode's base."""
    for mod, name in ((pager, "pager"), (_views, "_views"),
                      (voice_mod, "_voice"), (_seat, "_seat")):
        assert inspect.ismodule(mod), f"{name}: {mod!r} is not a module"
        assert Path(inspect.getsourcefile(mod)).name == f"{name}.py", mod
        src = inspect.getsource(mod)
        for needle in ("heard_", "VOICEMODE_BASE_DIR", "logs/conversations", "heard-cursor"):
            assert needle not in src, f"{mod.__name__} names {needle!r}"


def test_the_budget_and_marks_hold_no_wake_logic():
    src = inspect.getsource(_views)
    assert "wakeable" not in src.replace("NO wake logic", "")
    assert "def wakeable" not in src
    assert not hasattr(_views.Budget, "wake")
    assert Path(inspect.getsourcefile(_views)).name == "_views.py"


def test_an_empty_queue_makes_no_budget(ride, monkeypatch):
    """The scoop runs on every tool batch, in a fresh process. The budget
    (and the `_views` import behind it) is made only when there is
    something to give; an arrival proves the counter is live."""
    made: list[int] = []
    real = _views.Budget.__init__

    def counted(self, *a, **k):
        made.append(1)
        real(self, *a, **k)

    monkeypatch.setattr(_views.Budget, "__init__", counted)
    _given()                                              # nothing queued
    assert made == []
    _arrive(ride, _raw("eve@hq", body="hi", subject="one"))
    _given()
    assert made == [1]
