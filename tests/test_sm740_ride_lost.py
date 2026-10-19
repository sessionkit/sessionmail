"""SM-740 repro-001: carried mail recorded GIVEN that never reaches the model,
and that the next `pager listen` then never pages (kaizen #1124 #1129 #1155).

These tests were written to FAIL at 5dc20f8 (repro-001) and to pass once
fix-001 lands. Every `file:N` cite below is in this repo @ 5dc20f8. Each
pins only what the card's success criteria ask: a carried message is
either SHOWN or it still PAGES, and the ride-along never drops one in
silence. None of them says how the fix is built.

THE SHAPE, at 5dc20f8 (`src/sessionmail/pager.py`):

    _scoop                                  pager.py:708
      lines = header line + view, per entry
      emit(lines)                           pager.py:901   one hook JSON
      write_mark(... "seen" ...)            pager.py:909   GIVEN, unconditionally
      finish_queue(...)                     pager.py:914   link -> ride/done

The scoop records the giving on having PRINTED, with no evidence that what
it printed reached the model. Two documented Claude Code behaviours throw
printed hook output away (code.claude.com/docs/en/hooks, read 2030-10-16):

  * the 10,000-character cap: over it, "Claude Code saves the output to a
    file in the session directory and replaces it with the file path and a
    preview of up to the first 2,000 characters"; "Claude Code doesn't ask
    Claude to read the file". Test 1 REPRODUCES it: nothing in the scoop bounds its total.
  * the hook timeout (`"timeout": 5`, hooks/hooks.json:21,32): a hook that
    reaches it is cancelled, "discarding the hook's output". Not reproduced
    here (it needs Claude Code); see findings/repro.md.

Once such a giving is lost, nothing ever brings the mail back: its link is
in ride/done, so the queue has nothing to page (the queue loop lists only
`ride/new`, pager.py:2255), and its `seen` mark puts it in `given_now()`
(pager.py:1808-1818) for the 60 min reminder window
(`_seat.DEFAULT_REMIND_MINUTES`, _seat.py:1135). Test 2 pins that half:
the re-arm neither pages it nor names it.

Hermetic: the `seat` fixture of test_pager_hook (test 1) and the `rig`
fixture of test_pager_listen (test 2), both under tmp_path; no live box,
index, listener or Claude Code is touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from sessionmail import _seat, pager
from sessionmail.pager import cli
from test_pager_hook import SESSION, seat  # noqa: F401  (fixture reused)
from test_pager_listen import EX_PAGE, EX_TIMEOUT, rig, run  # noqa: F401  (fixture reused)

#: Claude Code's cap on one hook's `additionalContext` string, and the
#: preview it passes the model instead when a string is over it
#: (code.claude.com/docs/en/hooks, "Hook output"; no setting raises it).
HOOK_CAP = 10_000
HOOK_PREVIEW = 2_000
#: The host the keeper (`heard+airpods@hq`) is trusted on, as test_views pins it.
VOICE_HOST = {"SESSIONMAIL_HOST": "hq", "MAGICDNS_SUFFIX": "example.ts.net"}


def what_the_model_sees(context: str) -> str:
    """The part of an `additionalContext` string the model is handed: all of
    it at or under the cap; past it, only the preview (the rest is in a
    file the model is not asked to read)."""
    return context if len(context) <= HOOK_CAP else context[:HOOK_PREVIEW]


def _context(result, event: str = "PostToolBatch") -> str:
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == event
    return out["additionalContext"]


def _seen_by_scoop(s) -> set[str]:
    """Message-IDs this session's ride-along recorded as given."""
    return {e.message_id for e in _seat.read_mark_events(s, SESSION)
            if e.mark == "seen" and e.reason.startswith("ride-along")}


def _queue(s, tmp_path: Path, n: int, raw: str) -> str:
    """One mail in the session's ride-along queue, linked as the listener
    links it. Returns its Message-ID."""
    mid = f"sm740-{n}@hq.session-mail"
    path = tmp_path / f"17918{n:05d}.M{n}P1.hq:2,"
    path.write_text(raw.replace("<MID>", f"<{mid}>"))
    os.link(path, _seat.queue_new(s, SESSION) / path.name)
    return mid


def _reply(n: int, to: str) -> str:
    return (f"From: seat{n}@hq\nTo: {to}\nX-Original-To: {to}\n"
            f"Subject: Re: GO on the merge, reply {n}\nMessage-ID: <MID>\n"
            f"Date: Tue, 01 Jan 2030 12:00:0{n % 10} +0000\n\nok - reply {n}, short.\n")


def _voice_turn(chars: int) -> str:
    """A trusted spoken turn, as Ears writes one (test_ride_budget_marks's
    `_voice`): given WHOLE, up to `_voice.CEILING` (N13, ruled)."""
    words = ("and the next thing we need is " * (chars // 30 + 1))[:chars]
    return ("From: Ears <heard+airpods@hq>\nTo: alpha@hq\nSubject: a turn\n"
            "Ears-Kind: spoken\nEars-Device: airpods\nMessage-ID: <MID>\n"
            "Date: Tue, 01 Jan 2030 12:00:00 +0000\n\n" + words + "\n")


# ── 1. the scoop gives more than Claude Code hands the model ─────────────

def test_sm740_every_mail_a_scoop_marks_given_is_in_what_the_model_is_handed(
        seat, tmp_path, monkeypatch):
    """Candidate B (#1124/#1129: several mails at once while busy; some
    given, absent from what the seat saw), in the one form this repo can
    reproduce without Claude Code: ONE scoop whose `additionalContext`
    crosses Claude Code's 10,000-character cap.

    The batch budget (`_views.BATCH_BUDGET`, 8,000, _views.py:84) bounds
    the BODIES of MAIL only. A trusted voice turn is given whole past it, up
    to `_voice.CEILING` (_views.py:258), and header lines, pointers, the
    budget line and the state line are not counted at all. So nothing bounds
    the string the hook hands Claude Code (pager.py:4426-4427). Over the cap,
    the model gets the first 2,000 characters; every mail after them is
    marked `seen` (pager.py:909) and filed in ride/done (pager.py:914) all
    the same: given, never shown.

    Here: a 10,000-character spoken turn (under the 16,000 `CEILING`), then
    three short replies to the seat. The turn spends the budget, so each
    reply gets its header line only (the budget closed), and each is marked
    given. The string comes to ~10,550 characters; none of the three
    replies is in what the model is handed. (Measured at 5dc20f8: a
    9,000-character turn makes 9,551, under the cap, and all three show.
    Mail alone rarely crosses it: nine 3,000-character replies over three
    addresses made 8,992. See findings/repro.md.)

    ⚠ For the fix: the voice turn's wholeness is RULED (N13, *"every turn
    reaches every subscriber WHOLE"*; `_views.Budget` docstring,
    _views.py:346-350). The bound has to be met without cutting a turn the
    ruling says is whole, or the ruling has to change first."""
    for k, v in VOICE_HOST.items():                  # the keeper's From is this host's
        monkeypatch.setenv(k, v)
    s = _seat.resolve()
    _queue(s, tmp_path, 1, _voice_turn(10_000))
    replies = [_queue(s, tmp_path, n, _reply(n, f"{SESSION}@hq")) for n in (2, 3, 4)]

    context = _context(CliRunner().invoke(cli, ["hook", "batch"],
                                          input=json.dumps({"session_id": SESSION,
                                                            "hook_event_name": "PostToolBatch",
                                                            "tool_calls": []})))
    given = _seen_by_scoop(s)
    assert set(replies) <= given, "the scoop did not give the replies at all"
    done = _seat.ride_dir(s, SESSION) / _seat.QUEUE_DONE
    assert len(os.listdir(done)) == 4, "the scoop did not file all four in ride/done"
    seen = what_the_model_sees(context)
    lost = [n for n in (2, 3, 4) if f"reply {n}" not in seen]
    assert not lost, (
        f"the scoop handed Claude Code {len(context):,} characters (cap {HOOK_CAP:,}); "
        f"the model is shown the first {HOOK_PREVIEW:,}. Replies {lost} are marked "
        f"given and filed in ride/done, and the model never saw them.")


# ── 2. a giving that never reached the model is never paged again ─────────

def test_sm740_a_carry_whose_scoop_never_reached_the_model_is_paged_or_named_on_re_arm(
        rig, tmp_path, monkeypatch):
    """Candidate A (#1155: a `wake` that carries, filed in ride/done as the
    turn ended, never seen; the re-armed listener does not page it, and the
    seat is deaf 38 min) at the code level.

    1. The seat is BUSY. A reply arrives; the listener judges it `wake`
       (SM-656: every wake carries), links it into the queue, and does not
       page it yet (running page age).
    2. A ride-along scoops it: marks it `seen` and files it in ride/done.
       What that hook printed never reaches the model. Which loss it was
       (the cap of test 1, a timeout's discarded output, or a Claude Code
       path not measured here) does not matter to this half: the scoop
       cannot tell, and records the giving either way. The transcript the
       hook was handed has no trace of the reply.
    3. The turn ends; the seat is idle; it re-arms `pager listen`.

    The card's expected behaviour: *"A carried message is either shown in
    the transcript or it still pages"*; minimum, *"ids that were carried but
    never shown are surfaced on the next re-arm banner"*. At 5dc20f8 the
    re-arm has nothing to page (the link is in ride/done; the queue loop
    lists only `ride/new`, pager.py:2255) and its banner counts the id
    among "already given to this session, not paged again" (pager.py:1914)
    without naming it. It times out, deaf, for the reminder window."""
    mid = "2000000000.11111.11111@hq.session-mail"        # #1155's DECLINE, by shape
    rig.box()
    rig.deliver("sess-a", mid, "DECLINE: not this slice")

    # 1. busy: judged and linked, not paged (a page age the test never reaches).
    rig.running()
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "600")
    first = run("--timeout", "0.4")
    assert first.exit_code == EX_TIMEOUT, first.output
    queued = _seat.ride_dir(rig.seat, "sess-a") / _seat.QUEUE_NEW
    assert len(os.listdir(queued)) == 1, "the listener did not link the reply"

    # 2. a ride-along takes it; its output is lost on the way to the model.
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "carry on"}}) + "\n")
    scoop = CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps({
        "session_id": "sess-a", "transcript_path": str(transcript),
        "hook_event_name": "PostToolBatch", "tool_calls": []}))
    assert scoop.exit_code == 0 and "DECLINE" in scoop.stdout, scoop.output
    assert os.listdir(queued) == [], "the scoop did not take the reply"
    assert mid not in transcript.read_text()            # never reached the model

    # 3. the turn is over: idle, and the seat re-arms.
    rig.stopped()
    monkeypatch.delenv(pager.PAGE_AGE_VAR)
    again = run("--timeout", "0.5")
    assert again.exit_code == EX_PAGE or mid in again.stderr, (
        f"carried, never shown, and the re-arm neither paged it nor named it "
        f"(exit {again.exit_code}):\n{again.stderr}")
