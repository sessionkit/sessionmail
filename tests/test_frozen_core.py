"""SM-1004 do-002: the frozen-turn detector's pure core, `_frozen`
(design.md 2.2, 2.3, 2.7 and the 2.9 test plan, in the task store).

Unit tests at the `judge` / `Tail` level: SC1 1a, 1c and 1e, and SC2 2a-2o
except 2n (do-001's, in test_frozen_settings.py). Monitor-level cases (1b,
1d, 1f, SC4) are do-003's; they reuse `frozen_fixtures`.

Every made-up moment is derived from the fixtures' BASE, the project's
earlier synthetic Sunday; NOW is wall-clock epoch seconds, as on a seat.
"""

from __future__ import annotations

import builtins
import json
import os

import pytest
from frozen_fixtures import SID, Script, epoch, fallback_chain, frozen_turn, watch

from sessionmail import _frozen
from sessionmail._frozen import State, Tail, fold, judge, judge_each, transcript
from sessionmail._settings import CHAIN, SILENCE

BUSY = "busy"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def on(home):
    """The watch on, every other key its default: rules ["silence"], N 720."""
    w = watch(home)
    assert (w.on, w.rules, w.after_seconds, w.chain_seconds) == (True, (SILENCE,), 720, 900)
    return w


@pytest.fixture
def both(home):
    """The watch on with both rules, N 720 and R 900."""
    w = watch(home, {"frozen_watch": True, "frozen_rules": [SILENCE, CHAIN]})
    assert w.rules == (SILENCE, CHAIN)
    return w


# ── SC1: a fake frozen turn holds ──────────────────────────────────────────

def test_1a_a_frozen_turn_holds_rule_silence_from_n(on):
    """1a: sidecar busy; a real prompt, then an api-request; nothing after
    it; now = request + 721 s gives rule 2a held. Just under N does not."""
    s = frozen_turn()
    state = fold(s.entries)
    assert state.clock == epoch(1)

    v = judge(state, BUSY, epoch(1 + 721), on, session=SID)
    assert v.held is True
    assert v.rule == SILENCE
    assert v.key == (SID, SILENCE, epoch(1))
    assert v.turn == s.entries[0]["uuid"]       # the real prompt's uuid
    assert v.silent == pytest.approx(721)
    assert "721 s" in v.why and "N 720 s" in v.why

    assert judge(state, BUSY, epoch(1 + 720), on).held is True     # >= N
    under = judge(state, BUSY, epoch(1 + 719), on)
    assert under.held is False and under.key is None
    assert under.why.startswith("G4:") and "under N 720 s" in under.why


class AnyEntryState(State):
    """The brief's clock, rejected by D-1: the newest entry of ANY kind,
    queue-operations and queued_command attachments included."""

    def feed(self, entry):
        if isinstance(entry, dict) and entry.get("isSidechain") is not True:
            self._tick(_frozen.parse_ts(entry.get("timestamp")), str(entry.get("type")))
        super().feed(entry)


def _fold_any(entries):
    state = AnyEntryState()
    for e in entries:
        state.feed(e)
    return state


def _paged_frozen_turn():
    """1a, plus a queue-operation enqueue every 180 s after the request and a
    queued_command attachment: pages keep arriving at a frozen seat."""
    s = frozen_turn()
    for k in range(1, 5):                         # +181, +361, +541, +721
        s.enqueue(1 + 180 * k)
    s.queued_command(1 + 200)
    return s


def test_1c_pages_arriving_never_reset_the_clock(on):
    """1c (D-1): still held at request + 721 s, with the clock on the request."""
    s = _paged_frozen_turn()
    state = fold(s.entries)
    assert state.clock == epoch(1) and state.clock_kind == "api-request"
    v = judge(state, BUSY, epoch(1 + 721), on, session=SID)
    assert v.held is True and v.key == (SID, SILENCE, epoch(1))


def test_1c_fails_under_an_any_entry_clock(on):
    """1c, the proof: the same fixture under an any-entry clock is NOT held,
    because the last enqueue (request + 720 s) reset it. This is why the
    clock excludes queue-operations and queued_command attachments."""
    s = _paged_frozen_turn()
    state = _fold_any(s.entries)
    assert state.clock == epoch(1 + 720)
    v = judge(state, BUSY, epoch(1 + 721), on)
    assert v.held is False
    assert v.why.startswith("G4:") and "1 s since the clock" in v.why


def test_1e_a_fallback_chain_trips_chain_and_not_silence(both):
    """1e (D-2): api-requests 300 s apart, no assistant entry; now = first +
    901 s. Rule 2b holds; rule 2a does not (each attempt resets its clock)."""
    s = fallback_chain(attempts=4)                # requests at +1, +301, +601, +901
    state = fold(s.entries)
    assert (state.first, state.requests) == (epoch(1), 4)
    now = epoch(1 + 901)

    silence, chain = judge_each(state, BUSY, now, both, session=SID)
    assert silence.rule == SILENCE and silence.held is False
    assert silence.why.startswith("G4:")
    assert chain.rule == CHAIN and chain.held is True
    assert chain.key == (SID, CHAIN, epoch(1))
    assert "4 unanswered api-requests" in chain.why and "R 900 s" in chain.why
    # With no rule named, judge returns the one that holds.
    assert judge(state, BUSY, now, both, session=SID) == chain


def test_1e_chain_is_opt_in(on):
    """frozen_rules defaults to ["silence"]: the chain rule does not run."""
    state = fold(fallback_chain(attempts=4).entries)
    v = judge(state, BUSY, epoch(1 + 901), on, rule=CHAIN)
    assert v.held is False and "not in frozen_rules" in v.why
    assert [x.rule for x in judge_each(state, BUSY, epoch(1 + 901), on)] == [SILENCE]


# ── SC2: no notice ─────────────────────────────────────────────────────────

def test_2a_an_open_tool_use_for_30_minutes_is_not_frozen(on):
    s = frozen_turn().assistant(5, "toolu_1")
    state = fold(s.entries)
    assert state.open_tools == {"toolu_1"}
    v = judge(state, BUSY, epoch(5 + 1800), on)
    assert v.held is False and v.why.startswith("G3:")


def test_2b_the_moment_the_tool_result_lands_the_clock_is_its_timestamp(on):
    s = frozen_turn().assistant(5, "toolu_1").tool_result(5 + 1800, "toolu_1")
    state = fold(s.entries)
    assert state.open_tools == set()
    assert state.clock == epoch(5 + 1800)
    v = judge(state, BUSY, epoch(5 + 1800 + 1), on)
    assert v.held is False and v.why.startswith("G4:")


def test_2c_a_backdated_newest_line_is_not_the_clock(on):
    """The clock is the max timestamp, never the last line's (q3 6.2)."""
    s = frozen_turn().assistant(100).api_request(101)
    s.attachment(101 - 800)                       # newest LINE, 800 s backdated
    state = fold(s.entries)
    assert state.clock == epoch(101)
    v = judge(state, BUSY, epoch(101 + 300), on)
    assert v.held is False and "300 s since the clock" in v.why


def test_2c_a_backdated_tool_result_still_closes_its_tool(on):
    """Every entry updates the state, a backdated one too (opengaps2's fix)."""
    s = frozen_turn().assistant(10, "toolu_1").api_request(20)
    s.tool_result(15, "toolu_1")                  # written late, stamped early
    state = fold(s.entries)
    assert state.open_tools == set() and state.clock == epoch(20)


def test_2d_the_longest_measured_think_is_under_n(on):
    """An api-request, then a thinking entry at +444.7 s."""
    s = frozen_turn()
    before = fold(s.entries)
    assert judge(before, BUSY, epoch(1 + 444), on).held is False
    s.thinking(1 + 444.7)
    after = fold(s.entries)
    v = judge(after, BUSY, epoch(1 + 446), on)
    assert v.held is False and v.why.startswith("G4:")


def test_2e_a_retry_in_flight_resets_on_the_api_request_with_no_api_error(on):
    """Advisory 3: the clock keys on the fresh api-request; nothing reads
    api_error, and this fixture has none anywhere."""
    s = frozen_turn().api_request(1 + 400)        # a retry, 400 s on
    assert not any(e.get("subtype") == "api_error" for e in s.entries)
    v = judge(fold(s.entries), BUSY, epoch(1 + 721), on)
    assert v.held is False and "321 s since the clock" in v.why

    # The escalated retry: first byte aborted at 181 s, then 599 s of nothing.
    esc = frozen_turn().api_request(1 + 181)
    v = judge(fold(esc.entries), BUSY, epoch(1 + 181 + 599), on)
    assert v.held is False and "599 s" in v.why


def test_2e_an_api_error_changes_no_gate(on):
    """api_error is read by no gate: with or without it, the same verdict."""
    plain = frozen_turn()
    noisy = frozen_turn().api_error(1 - 0.5)      # an api_error before the request
    for s in (plain, noisy):
        assert judge(fold(s.entries), BUSY, epoch(1 + 721), on).held is True


def test_2f_the_streaming_worst_case_is_under_r(both):
    """2e's escalated retry for rule 2b: the run has been unanswered 780 s
    (181 s first byte + 599 s), under R = 900 s."""
    s = frozen_turn().api_request(1 + 181)
    state = fold(s.entries)
    assert (state.first, state.requests) == (epoch(1), 2)
    v = judge(state, BUSY, epoch(1 + 780), both, rule=CHAIN)
    assert v.held is False and v.why.startswith("H4:") and "under R 900 s" in v.why


@pytest.mark.parametrize("status", [None, "idle", "waiting", "shell", "", "Busy"])
def test_2g_a_sidecar_that_is_not_exactly_busy_is_never_judged(on, status):
    """Missing or unreadable (None), idle, waiting, shell, or anything else."""
    state = fold(frozen_turn().entries)
    v = judge(state, status, epoch(1 + 3600), on)
    assert v.held is False and v.why.startswith("G1:")


def test_2h_a_background_agent_keeps_busy_after_turn_duration(on):
    """The sidecar says busy, but the main turn closed more than N ago."""
    s = frozen_turn().assistant(2).turn_duration(3)
    v = judge(fold(s.entries), BUSY, epoch(3 + 721), on)
    assert v.held is False and v.why == "G2: no main turn is open (closed by turn_duration)"


def test_2i_a_compact_summary_is_not_a_real_prompt(on):
    s = frozen_turn().assistant(2).turn_duration(3).compact_summary(10)
    state = fold(s.entries)
    assert state.turn_open is False
    assert judge(state, BUSY, epoch(10 + 721), on).why.startswith("G2:")


def test_2i_a_compact_summary_mid_turn_leaves_the_turn_open(on):
    """Auto-compaction mid-turn: still the same open turn, same prompt."""
    s = frozen_turn()
    s.compact_summary(5)
    state = fold(s.entries)
    assert state.turn_open is True and state.turn == s.entries[0]["uuid"]


def test_2j_an_is_meta_entry_after_a_closed_turn_is_not_a_real_prompt(on):
    s = frozen_turn().assistant(2).turn_duration(3).meta(10)
    state = fold(s.entries)
    assert state.turn_open is False
    assert judge(state, BUSY, epoch(10 + 721), on).why.startswith("G2:")


def test_2k_parallel_tools_pair_by_id(on):
    s = frozen_turn().assistant(5, "toolu_a", "toolu_b").tool_result(6, "toolu_a")
    state = fold(s.entries)
    assert state.open_tools == {"toolu_b"}
    v = judge(state, BUSY, epoch(6 + 1800), on)
    assert v.held is False and v.why.startswith("G3: 1 tool_use ")
    s.tool_result(7, "toolu_b")
    assert fold(s.entries).open_tools == set()


def test_2l_an_interrupt_ends_the_turn(on):
    s = frozen_turn().interrupt(1 + 175.8)
    state = fold(s.entries)
    assert (state.turn_open, state.closed_by, state.first) == (False, "interrupt", None)
    v = judge(state, BUSY, epoch(1 + 3600), on)
    assert v.why == "G2: no main turn is open (closed by interrupt)"


def test_2l_an_interrupt_for_tool_use_ends_the_turn_and_its_tools(on):
    s = frozen_turn().assistant(5, "toolu_1").interrupt(9, for_tool=True)
    state = fold(s.entries)
    assert (state.turn_open, state.open_tools) == (False, set())


def test_2l_interrupt_text_inside_a_tool_s_output_is_not_an_interrupt(on):
    """Only the user entry's own text counts; a tool may print anything."""
    s = frozen_turn().assistant(5, "toolu_1")
    s.tool_result(6, "toolu_1", output="... [Request interrupted by user] ...")
    state = fold(s.entries)
    assert state.turn_open is True and state.open_tools == set()
    assert judge(state, BUSY, epoch(6 + 721), on).held is True


def test_2m_zero_or_two_transcripts_are_not_judged(home, tmp_path):
    config = tmp_path / "claude"
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config)}

    none = transcript(SID, env)
    assert none.path is None
    assert none.why == f"no transcript matches {config}/projects/*/{SID}.jsonl"

    one = frozen_turn().write(config, project="a")
    assert transcript(SID, env).path == one

    frozen_turn().write(config, project="b")
    two = transcript(SID, env)
    assert two.path is None
    assert two.why.startswith(f"2 transcripts match {config}/projects/*/{SID}.jsonl")


def test_2m_the_config_dir_defaults_to_home_dot_claude(home):
    env = {"HOME": str(home)}
    path = frozen_turn().write(home / ".claude")
    assert transcript(SID, env).path == path
    assert transcript(SID, {"HOME": str(home), "CLAUDE_CONFIG_DIR": ""}).path == path


@pytest.mark.parametrize("bad", ["", "../x", "a/b", ".", "..", "*"])
def test_2m_a_session_id_that_names_no_file_is_not_judged(home, bad):
    frozen_turn().write(home / ".claude", session="other")
    assert transcript(bad, {"HOME": str(home)}).path is None


def test_2o_sidechain_lines_are_skipped(on):
    """isSidechain lines silent for hours while the main thread answers: a
    sidechain prompt after the main turn closed would open a turn if it
    counted, and judge would hold three hours later. It is skipped."""
    s = frozen_turn().assistant(2).turn_duration(3)
    s.sidechain("prompt", 4).sidechain("assistant", 5, "toolu_side")
    state = fold(s.entries)
    assert state.skipped == 2
    assert (state.turn_open, state.open_tools, state.clock) == (False, set(), epoch(3))
    assert judge(state, BUSY, epoch(5 + 3 * 3600), on).why.startswith("G2:")


def test_2o_a_fresh_sidechain_entry_never_resets_the_main_clock(on):
    s = frozen_turn().sidechain("assistant", 700)
    state = fold(s.entries)
    assert state.clock == epoch(1)
    assert judge(state, BUSY, epoch(1 + 721), on).held is True


# ── the turn, the clock and the state ──────────────────────────────────────

def test_a_dequeue_opens_a_turn_and_is_never_the_clock(on):
    s = Script().assistant(0).turn_duration(1).dequeue(10).api_request(11)
    state = fold(s.entries)
    assert state.turn_open is True
    assert state.turn == f"dequeue@{s.entries[2]['timestamp']}"
    assert state.clock == epoch(11)
    assert judge(state, BUSY, epoch(11 + 721), on).held is True


def test_a_dequeue_after_a_long_idle_is_not_frozen_at_once(on):
    """Review of do-002: an idle seat (turn closed at +3) is handed a queued
    prompt 800 s later. The dequeue opens the turn but is never the clock, so
    the clock is still the old turn's end. Until the prompt's own entry lands
    (up to 2.3 s in real transcripts), silence counts from the turn's opening, not from
    the previous turn: 1 s, not 801 s."""
    s = Script().prompt(0).assistant(2).turn_duration(3).dequeue(3 + 800)
    state = fold(s.entries)
    assert state.turn_open is True and state.clock == epoch(3)
    assert state.opened == epoch(3 + 800)
    v = judge(state, BUSY, epoch(3 + 801), on)
    assert v.held is False
    assert v.why.startswith("G4: 1 s since the turn's opening")

    # A turn opened by a dequeue that then truly goes silent still holds.
    v = judge(state, BUSY, epoch(3 + 800 + 721), on, session=SID)
    assert v.held is True and v.key == (SID, SILENCE, epoch(3 + 800))


def test_a_dequeue_mid_turn_never_moves_the_anchor(on):
    """Only the rising edge sets `opened`: a dequeue written while the turn
    is open (30 of them inside a request's wait, over 7 days measured) must not
    reset G4 on a frozen seat (D-1)."""
    s = frozen_turn().dequeue(1 + 600)
    state = fold(s.entries)
    assert state.opened == epoch(0) and state.clock == epoch(1)
    v = judge(state, BUSY, epoch(1 + 721), on, session=SID)
    assert v.held is True and v.key == (SID, SILENCE, epoch(1))


def test_a_closed_turn_forgets_its_opening(on):
    s = frozen_turn().assistant(2).turn_duration(3)
    assert fold(s.entries).opened is None


def test_entries_with_no_timestamp_never_move_the_clock(on):
    s = frozen_turn().untimed("cost-state").untimed("last-prompt")
    assert fold(s.entries).clock == epoch(1)


def test_a_new_turn_after_a_closed_one_is_a_new_turn_key(on):
    s = frozen_turn().assistant(2).turn_duration(3).prompt(10).api_request(11)
    state = fold(s.entries)
    assert state.turn == s.entries[4]["uuid"]
    v = judge(state, BUSY, epoch(11 + 721), on, session=SID)
    assert v.turn == s.entries[4]["uuid"] and v.key == (SID, SILENCE, epoch(11))


def test_an_assistant_entry_ends_the_unanswered_run(both):
    s = frozen_turn().assistant(2).api_request(3)
    state = fold(s.entries)
    assert (state.first, state.requests) == (epoch(3), 1)
    s.thinking(4)
    assert fold(s.entries).first is None
    v = judge(fold(s.entries), BUSY, epoch(4 + 3600), both, rule=CHAIN)
    assert v.held is False and v.why.startswith("H3:")


def test_a_non_main_api_request_moves_the_clock_but_starts_no_run(both):
    s = Script().prompt(0).api_request(1, source="compact")
    state = fold(s.entries)
    assert (state.clock, state.first) == (epoch(1), None)


def test_bad_lines_are_counted_and_skipped():
    state = State()
    for line in [b"not json", b"[1, 2]", b"", b"  ", json.dumps({"type": "user"}).encode()]:
        state.feed_line(line)
    assert (state.bad, state.entries) == (2, 1)


# ── judge: the wall clock, the switch, no I/O ──────────────────────────────

def test_judge_refuses_a_monotonic_now(on):
    """Note 6: NOW is wall-clock epoch seconds. A monotonic-sized now (1e5,
    seconds since boot) against a 2030 clock is never frozen, and the reason
    names the clock."""
    state = fold(frozen_turn().entries)
    v = judge(state, BUSY, 1e5, on)
    assert v.held is False
    assert "before the clock " + _frozen.iso(epoch(1)) in v.why
    assert "backwards clock is never frozen" in v.why and "monotonic" in v.why


def test_judge_refuses_a_backwards_now_for_the_chain_too(both):
    state = fold(fallback_chain(attempts=2).entries)
    v = judge(state, BUSY, epoch(0), both, rule=CHAIN)
    assert v.held is False and "before the run's first api-request" in v.why


@pytest.mark.parametrize("now", [float("nan"), float("inf"), True, "1e9", None])
def test_judge_refuses_a_now_that_is_no_clock(on, now):
    v = judge(fold(frozen_turn().entries), BUSY, now, on)
    assert v.held is False and v.why.startswith("not judged: now")


def test_judge_with_the_switch_off_judges_nothing(home):
    for data in ({}, {"frozen_watch": False}, {"frozen_watch": "true"}):
        w = watch(home, data)
        v = judge(fold(frozen_turn().entries), BUSY, epoch(1 + 3600), w)
        assert v.held is False and "frozen_watch is not true" in v.why


def test_judge_follows_n_from_the_settings(home):
    w = watch(home, {"frozen_watch": True, "frozen_after_seconds": 1000})
    state = fold(frozen_turn().entries)
    assert judge(state, BUSY, epoch(1 + 999), w).held is False
    assert judge(state, BUSY, epoch(1 + 1000), w).held is True


def test_judge_names_an_unknown_rule(on):
    v = judge(fold(frozen_turn().entries), BUSY, epoch(1 + 721), on, rule="nap")
    assert v.held is False and "'nap' is not a rule" in v.why


def test_judge_does_no_io(on, monkeypatch):
    state = fold(frozen_turn().entries)

    def no_open(*a, **k):
        raise AssertionError("judge opened a file")

    with monkeypatch.context() as m:   # undone before the fixtures' teardown
        m.setattr(builtins, "open", no_open)
        m.setattr(os, "stat", no_open)
        m.setattr(os, "fstat", no_open)
        held = judge(state, BUSY, epoch(1 + 721), on).held
    assert held is True


def test_judge_refuses_a_state_whose_last_read_failed(on):
    state = fold(frozen_turn().entries)
    state.fault = "the transcript could not be read"
    v = judge(state, BUSY, epoch(1 + 3600), on)
    assert v.held is False and v.why == "not judged: the transcript could not be read"


# ── Tail: the incremental reader ───────────────────────────────────────────

def test_tail_reads_only_what_was_appended(tmp_path, on):
    s = frozen_turn()
    path = s.write(tmp_path)
    tail = Tail(path)
    assert tail.read().clock == epoch(1)
    first_offset = tail.offset
    assert first_offset == path.stat().st_size

    n = len(s.entries)
    s.assistant(2, "toolu_1")
    s.append(path, n)
    state = tail.read()
    assert state.open_tools == {"toolu_1"} and state.clock == epoch(2)
    assert tail.offset == path.stat().st_size > first_offset
    assert tail.read() is state and state.entries == 3   # nothing new, nothing re-read


def test_tail_carries_a_partial_line_over(tmp_path):
    s = frozen_turn()
    path = s.write(tmp_path)
    tail = Tail(path)
    tail.read()
    line = json.dumps(Script().assistant(5, "toolu_1").entries[0]) + "\n"
    with open(path, "a") as fh:
        fh.write(line[:20])                        # half-written by Claude Code
    state = tail.read()
    assert state.open_tools == set() and state.bad == 0
    with open(path, "a") as fh:
        fh.write(line[20:])
    state = tail.read()
    assert state.open_tools == {"toolu_1"} and state.bad == 0


def test_tail_resets_on_a_shorter_file(tmp_path):
    s = frozen_turn().assistant(2, "toolu_1")
    path = s.write(tmp_path)
    tail = Tail(path)
    assert tail.read().open_tools == {"toolu_1"}
    path.write_text(Script().prompt(100).text())   # truncated and rewritten
    state = tail.read()
    assert tail.resets == 1
    assert (state.open_tools, state.clock, state.entries) == (set(), epoch(100), 1)


def test_tail_resets_on_a_new_inode(tmp_path):
    s = frozen_turn().assistant(2, "toolu_1")
    path = s.write(tmp_path)
    tail = Tail(path)
    tail.read()
    bigger = Script().prompt(100).api_request(101).api_request(102).api_request(103)
    new = tmp_path / "new.jsonl"
    new.write_text(bigger.text() + s.text())      # longer than the old offset
    os.replace(new, path)
    state = tail.read()
    assert tail.resets == 1
    assert state.open_tools == {"toolu_1"} and state.entries == len(bigger.entries) + 3


def test_tail_matches_a_whole_fold(tmp_path):
    s = _paged_frozen_turn().assistant(800, "toolu_a", "toolu_b").tool_result(801, "toolu_a")
    path = tmp_path / "t.jsonl"
    path.write_text("")
    tail = Tail(path)
    for i in range(len(s.entries)):
        with open(path, "a") as fh:
            fh.write(json.dumps(s.entries[i]) + "\n")
        tail.read()
    whole = fold(s.entries)
    assert tail.state == whole


def test_tail_marks_a_vanished_file_and_judge_refuses_it(tmp_path, on):
    s = frozen_turn()
    path = s.write(tmp_path)
    tail = Tail(path)
    assert judge(tail.read(), BUSY, epoch(1 + 721), on).held is True
    path.unlink()
    state = tail.read()
    assert state.fault and str(path) in state.fault
    assert judge(state, BUSY, epoch(1 + 721), on).why.startswith("not judged:")
    s.write(tmp_path)                              # back: a new inode, read afresh
    state = tail.read()
    assert state.fault == "" and judge(state, BUSY, epoch(1 + 721), on).held is True


def test_tail_reads_a_file_bigger_than_one_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(_frozen, "CHUNK", 64)       # every line spans chunks
    s = _paged_frozen_turn().assistant(800, "toolu_a")
    path = s.write(tmp_path)
    assert Tail(path).read() == fold(s.entries)


def test_iso_shows_epoch_seconds_and_refuses_nonsense():
    assert _frozen.iso(epoch(1.5)).endswith("00:00:01.500Z")
    assert _frozen.iso(None) == _frozen.iso(float("nan")) == _frozen.iso(1e300) == "-"


def test_parse_ts():
    assert _frozen.parse_ts(Script().api_request(1).entries[0]["timestamp"]) == epoch(1)
    for bad in (None, "", 5, "not a time"):
        assert _frozen.parse_ts(bad) is None
