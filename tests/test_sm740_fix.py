"""SM-740 fix-001: regression tests for F1, F2 and F4 (findings/rca.md
section 5 in the task store; the repro is tests/test_sm740_ride_lost.py).

F1  the batch hook's one `additionalContext` string is bounded under
    Claude Code's 10,000-character cap: what does not fit is HELD, un-given
    and unmarked, back in ride/new in order, and one line says so. A voice
    turn no hook can carry whole is never cut (N13): it is said by pointer,
    not given.
F2  a batch hook past its give-by takes nothing; the next batch gives.
F4  the `pager listen` banner NAMES each given id (capped), and names as
    UNSHOWN (a) a paged id never carried and (c) an id a scoop filed with
    no line.

Hermetic, as the repro: the `seat` fixture of test_pager_hook and the
`rig` fixture of test_pager_listen, both under tmp_path.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from sessionmail import _seat, pager
from sessionmail.pager import cli
from test_pager_hook import SESSION, seat  # noqa: F401  (fixture reused)
from test_pager_listen import EX_PAGE, EX_TIMEOUT, rig, run  # noqa: F401  (fixture reused)
from test_sm740_ride_lost import (HOOK_CAP, VOICE_HOST, _context, _queue, _reply,
                                  _seen_by_scoop, _voice_turn)

BATCH = {"session_id": SESSION, "hook_event_name": "PostToolBatch", "tool_calls": []}


def _batch(payload: dict | None = None):
    return CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(payload or BATCH))


def _mail(n: int, body_chars: int) -> str:
    """A reply with a body of BODY_CHARS characters, each to its own
    address, so no four of them coalesce into one count line."""
    body = (f"body {n} " * (body_chars // 7 + 1))[:body_chars]
    return _reply(n, f"addr{n}@hq").replace(f"ok - reply {n}, short.\n", body + "\n")


def _waiting(s) -> list[str]:
    return sorted(os.listdir(_seat.ride_dir(s, SESSION) / _seat.QUEUE_NEW))


def _done(s) -> list[str]:
    d = _seat.ride_dir(s, SESSION) / _seat.QUEUE_DONE
    return sorted(os.listdir(d)) if d.is_dir() else []


def _marks_for(s, mid: str) -> list[str]:
    return [e.mark for e in _seat.read_mark_events(s, SESSION) if e.message_id == mid]


# ── F1: the hook string is bounded ───────────────────────────────────────

def test_f1_an_over_room_scoop_gives_what_fits_and_holds_the_rest_in_order(
        seat, tmp_path, monkeypatch):
    """Six mails of 600 body characters against a room of 2,000: the first
    ones fit, the rest are held - not marked, back in ride/new, nothing
    packed behind the first held - and one line counts them. The next batch
    gives the held ones, oldest first."""
    monkeypatch.setattr(pager, "HOOK_CONTEXT_MAX", 2_000)
    s = _seat.resolve()
    mids = [_queue(s, tmp_path, n, _mail(n, 600)) for n in range(1, 7)]

    context = _context(_batch())
    assert len(context) <= pager.HOOK_CONTEXT_MAX
    given = _seen_by_scoop(s)
    fit = [m for m in mids if m in given]
    held = [m for m in mids if m not in given]
    assert fit and held, (len(context), context)
    assert fit == mids[: len(fit)], "a later mail was packed in ahead of a held one"
    assert f"{len(held)} more held for your next tool call" in context
    for m in held:
        assert _marks_for(s, m) == [], f"{m} was held and still marked"
    assert len(_waiting(s)) == len(held) and len(_done(s)) == len(fit)

    # The next batch gives what was held, from the first held one.
    context = _context(_batch())
    assert len(context) <= pager.HOOK_CONTEXT_MAX
    again = _seen_by_scoop(s)
    assert held[0] in again
    first = int(held[0].split("-")[1].split("@")[0])
    assert f"body {first}" in context


def test_f1_a_voice_turn_that_fits_a_hook_but_not_the_room_left_is_held_whole(
        seat, tmp_path, monkeypatch, keeper_signs):
    """Two 1,500-character mails, then an 8,000-character spoken turn: the
    turn fits a hook on its own but not after the mails, so it is HELD
    (not cut, not given, no mark) and the next batch gives it whole."""
    for k, v in VOICE_HOST.items():
        monkeypatch.setenv(k, v)
    s = _seat.resolve()
    mails = [_queue(s, tmp_path, n, _mail(n, 1_500)) for n in (1, 2)]
    turn = _queue(s, tmp_path, 3, _voice_turn(8_000))

    context = _context(_batch())
    assert len(context) <= pager.HOOK_CONTEXT_MAX
    assert set(mails) <= _seen_by_scoop(s)
    assert _marks_for(s, turn) == []
    assert "1 more held for your next tool call" in context
    assert "and the next thing" not in context            # no part of the turn
    assert len(_waiting(s)) == 1

    context = _context(_batch())
    assert len(context) <= pager.HOOK_CONTEXT_MAX
    assert turn in _seen_by_scoop(s)
    assert "done" in _marks_for(s, turn), "the turn was not given whole"
    assert _voice_turn(8_000).split("\n\n", 1)[1].strip() in context


def test_f1_a_voice_turn_no_hook_can_carry_is_said_by_pointer_never_cut_never_given(
        seat, tmp_path, monkeypatch, keeper_signs):
    """N13: a 12,000-character turn (under `_voice.CEILING`) can never be
    shown whole in a hook. It is not cut and not given: its header line
    and a `message read` pointer say it, with no mark, and its link is
    filed (never retaken, never re-said). What follows it is still given."""
    for k, v in VOICE_HOST.items():
        monkeypatch.setenv(k, v)
    s = _seat.resolve()
    turn = _queue(s, tmp_path, 1, _voice_turn(12_000))
    reply = _queue(s, tmp_path, 2, _reply(2, f"{SESSION}@hq"))

    context = _context(_batch())
    assert len(context) <= HOOK_CAP
    assert "too long to carry whole in a hook" in context
    assert "`message read " in context
    assert "and the next thing" not in context            # never cut
    assert _marks_for(s, turn) == []
    assert reply in _seen_by_scoop(s) and "reply 2" in context
    assert _waiting(s) == [] and len(_done(s)) == 2

    quiet = _batch()                                       # never said twice
    assert quiet.exit_code == 0
    assert "too long" not in quiet.stdout


def test_f1_an_unverified_turn_is_capped_like_mail_never_said_by_pointer(
        seat, tmp_path, monkeypatch):
    """SM-782: the same 12,000-character turn UNSIGNED is not voice to the
    hook: it is given as a mail, capped at the text cap, with no `done`."""
    for k, v in VOICE_HOST.items():
        monkeypatch.setenv(k, v)
    s = _seat.resolve()
    turn = _queue(s, tmp_path, 1, _voice_turn(12_000))
    context = _context(_batch())
    assert len(context) <= HOOK_CAP
    assert "too long to carry whole in a hook" not in context
    assert "[heard UNVERIFIED " in context and "more - message read " in context
    assert "done" not in _marks_for(s, turn)


def test_f1_the_bound_counts_the_lines_the_hook_adds_around_the_scoop(
        seat, tmp_path, monkeypatch):
    """The room is what the cap leaves after the hook's own lines: a long
    bad-rules line (said beside the mail) shrinks it, and the whole string
    still stays under HOOK_CONTEXT_MAX."""
    monkeypatch.setattr(pager, "HOOK_CONTEXT_MAX", 2_500)
    monkeypatch.setattr(pager, "_state_lines",
                        lambda *a, **k: ["[pager] " + "x" * 900])
    s = _seat.resolve()
    for n in range(1, 6):
        _queue(s, tmp_path, n, _mail(n, 500))
    context = _context(_batch())
    assert len(context) <= pager.HOOK_CONTEXT_MAX, len(context)
    assert "more held for your next tool call" in context


def test_f1_pager_status_new_is_not_bounded(seat, tmp_path, monkeypatch):
    """`pager status --new` prints to a terminal, not a hook: no room, so
    nothing is held there."""
    monkeypatch.setattr(pager, "HOOK_CONTEXT_MAX", 500)
    s = _seat.resolve()
    mids = [_queue(s, tmp_path, n, _mail(n, 600)) for n in range(1, 4)]
    out = CliRunner().invoke(cli, ["status", "--new"])
    assert "more held" not in out.output
    assert set(mids) <= _seen_by_scoop(s)


# ── F2: never give near the deadline ─────────────────────────────────────

def test_f2_a_batch_past_its_give_by_takes_nothing_and_the_next_gives(
        seat, tmp_path, monkeypatch):
    s = _seat.resolve()
    mid = _queue(s, tmp_path, 1, _reply(1, f"{SESSION}@hq"))
    monkeypatch.setattr(pager, "HOOK_GIVE_BY", -1.0)       # already past it
    late = _batch()
    assert late.exit_code == 0
    assert "reply 1" not in late.stdout
    assert _marks_for(s, mid) == []
    assert len(_waiting(s)) == 1 and _done(s) == []
    leftover = [p for p in _seat.ride_dir(s, SESSION).iterdir()
                if p.name.startswith(_seat.TAKING_PREFIX)]
    assert leftover == [], "a late batch claimed the queue"

    monkeypatch.setattr(pager, "HOOK_GIVE_BY", 2.5)
    assert "reply 1" in _context(_batch())
    assert mid in _seen_by_scoop(s)


def test_f2_the_hook_timeouts_are_unchanged():
    """F2 is in the hook, not the plugin's settings: hooks/hooks.json keeps
    its 5-second timeouts (a design call the slice did not make)."""
    from pathlib import Path
    hooks = json.loads((Path(__file__).parents[1] / "hooks" / "hooks.json").read_text())
    timeouts = {h.get("timeout") for event in hooks["hooks"].values()
                for group in event for h in group["hooks"] if "timeout" in h}
    assert timeouts <= {5}, timeouts


# ── F4: the banner names what was given, and what may never have shown ───

def test_f4_the_banner_names_each_given_id_capped(rig, monkeypatch):
    """Twelve ids given to this session: the banner names the ten newest -
    id, sender, subject - and counts the other two."""
    rig.box()
    mids = [f"sm740-given-{n:02d}@hq.session-mail" for n in range(12)]
    for n, mid in enumerate(mids):
        rig.deliver("sess-a", mid, f"subject number {n}")
        _seat.write_mark(rig.seat, mid, "seen", "sess-a",
                         pager.datetime.now().astimezone().isoformat(timespec="seconds"),
                         "ride-along")
    rig.stopped()
    out = run("--timeout", "0.3")
    named = [line for line in out.stderr.splitlines() if line.startswith("  given      :")]
    assert len(named) == 11, out.stderr
    assert named[-1].endswith("and 2 more - `message list` to see them")
    assert any("beta" in line and "subject number" in line for line in named[:-1])


def test_f4c_a_mail_marked_done_before_it_was_shown_is_named_unshown(rig, monkeypatch):
    """#1155 + #1156 (rca 2f): the seat marks a queued, unshown mail
    `done`; the scoop files it with no line. The re-arm names it UNSHOWN."""
    mid = "2000000000.11111.11111@hq.session-mail"
    rig.box()
    rig.deliver("sess-a", mid, "DECLINE: not this slice")
    rig.running()
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "600")
    assert run("--timeout", "0.4").exit_code == EX_TIMEOUT
    _seat.write_mark(rig.seat, mid, "done", "sess-a",
                     pager.datetime.now().astimezone().isoformat(timespec="seconds"), "bulk")
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    scoop = CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps({
        "session_id": "sess-a", "hook_event_name": "PostToolBatch", "tool_calls": []}))
    assert scoop.exit_code == 0 and mid not in scoop.stdout
    rig.stopped()
    monkeypatch.delenv(pager.PAGE_AGE_VAR)
    again = run("--timeout", "0.4")
    line = [x for x in again.stderr.splitlines() if x.startswith("  UNSHOWN    :")]
    assert len(line) == 1, again.stderr
    assert mid in line[0] and "DECLINE" in line[0] and "filed with no line" in line[0]


def test_f4a_a_paged_id_never_carried_is_named_unshown(rig, monkeypatch):
    """rca 2e: a page marks `seen` reason `pager wake`; no carry follows,
    so the link still waits in ride/new. Past the grace, the re-arm (which
    does not page it again: never twice) names it UNSHOWN."""
    mid = "sm740-woke@hq.session-mail"
    rig.box()
    rig.deliver("sess-a", mid, "GO on the merge")
    rig.stopped()
    assert run("--timeout", "2").exit_code == EX_PAGE
    queued = _seat.ride_dir(rig.seat, "sess-a") / _seat.QUEUE_NEW
    assert len(os.listdir(queued)) == 1
    monkeypatch.setattr(pager, "UNCARRIED_GRACE", 0)
    again = run("--timeout", "0.4")
    assert again.exit_code == EX_TIMEOUT                  # never twice: not paged
    line = [x for x in again.stderr.splitlines() if x.startswith("  UNSHOWN    :")]
    assert len(line) == 1, again.stderr
    assert mid in line[0] and "GO on the merge" in line[0] and "never carried" in line[0]
    named = [x for x in again.stderr.splitlines() if x.startswith("  given      :")]
    assert not any(mid in x for x in named), "named twice"


def test_f4a_inside_the_grace_a_paged_id_is_not_called_unshown(rig, monkeypatch):
    mid = "sm740-woke-now@hq.session-mail"
    rig.box()
    rig.deliver("sess-a", mid, "GO on the merge")
    rig.stopped()
    assert run("--timeout", "2").exit_code == EX_PAGE
    again = run("--timeout", "0.3")
    assert "UNSHOWN" not in again.stderr
    assert any(mid in x for x in again.stderr.splitlines() if x.startswith("  given      :"))


def test_f1_a_burst_count_line_that_does_not_fit_holds_the_whole_burst(
        seat, tmp_path, monkeypatch):
    """A burst coalesced to one count line is held whole when its line does
    not fit: none of its members is marked, all wait in ride/new."""
    s = _seat.resolve()
    _batch()                       # the state line said once: the room is the scoop's
    first = _queue(s, tmp_path, 1, _mail(1, 700))
    # Room for the first mail and its tail reserve, and not one burst line more.
    monkeypatch.setattr(pager, "HOOK_CONTEXT_MAX", 760 + pager.SCOOP_TAIL_RESERVE + 40)
    burst = [_queue(s, tmp_path, n, _reply(n, "burst@hq")) for n in range(2, 6)]
    context = _context(_batch())
    assert len(context) <= pager.HOOK_CONTEXT_MAX
    assert first in _seen_by_scoop(s)
    assert "4 more held for your next tool call" in context
    for m in burst:
        assert _marks_for(s, m) == []
    assert len(_waiting(s)) == 4
    context = _context(_batch())
    assert "4 for burst - " in context
    assert set(burst) <= _seen_by_scoop(s)
