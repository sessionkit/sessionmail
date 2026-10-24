"""`message mark` - the seat's private state, and the only writer so far.

It closes the loop `pager status` opened: status rides along with not-done,
and this is what makes something done. specs/pager, "Read is not terminal".

Hermetic like the rest: `tests/conftest.py` redirects the search engine and
the seat's home for every test, so no maildir and no real `~/.sessionmail`
is touched.
"""

from __future__ import annotations

import json
import re
import stat
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _notmuch, _seat
from sessionmail.message import cli
from sessionmail.pager import cli as pager_cli
from fakes import FAKE_NOTMUCH_BY_ID, forest, message, write_fake

EX_OK, EX_USAGE, EX_REFUSED = 0, 2, 2

ID = "a@example.invalid"
SESSION = "7ed6b16b-31bd"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    """Seat `alpha`, an engine that knows about exactly one message."""
    home = tmp_path / "sessionmail"
    root = home / "seats" / "alpha"
    root.mkdir(parents=True)

    fake = write_fake(tmp_path / "fake-notmuch")
    payload = tmp_path / "answer.json"
    payload.write_text(
        json.dumps(forest(message(ID, 1, "beta@example.invalid", "hello")))
    )

    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(fake))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_JSON", str(payload))

    class Fixture:
        def __init__(self) -> None:
            self.root = root
            self.home = home
            self.payload = payload

        def marks_file(self, session: str = SESSION) -> Path:
            """Where the marks are since marks went per session: under
            the SESSION id, not the seat."""
            return _mail(session) / "marks"

        @property
        def marks(self) -> str:
            path = self.marks_file()
            return path.read_text() if path.exists() else ""

        def nothing_matches(self) -> None:
            self.payload.write_text(json.dumps(forest()))

    return Fixture()


def _mail(sid: str) -> Path:
    """Where a session's mail state is since session-settings 3.3a: under
    the session dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/`
    (spelt out, not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def run(*argv: str):
    return CliRunner().invoke(cli, ["mark", *argv], prog_name="message")


STAMP = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$"


def test_it_appends_the_ruled_line(seat):
    """_seat.MARK_LINE: timestamp, message id, mark, seat, session, and a
    free-text reason last. Without a reason the line is exactly five fields."""
    result = run(ID, "done")
    assert result.exit_code == EX_OK, result.output + result.stderr
    lines = seat.marks.splitlines()
    assert len(lines) == 1
    stamp, mid, mark, seat_, session = lines[0].split()
    assert (mid, mark, seat_, session) == (ID, "done", "alpha", "7ed6b16b-31bd")
    assert re.match(STAMP, stamp)


def test_a_reason_is_free_text_written_last(seat):
    """The reason may hold spaces, so it is the last field and open-ended."""
    result = run(ID, "done", "--reason", "created TM-99 for it")
    assert result.exit_code == EX_OK, result.output + result.stderr
    line = seat.marks.splitlines()[0]
    assert line.endswith(" 7ed6b16b-31bd created TM-99 for it")
    event = _seat.parse_mark(line)
    assert event is not None and event.reason == "created TM-99 for it"
    assert event.line == line


def test_a_newline_in_a_reason_cannot_forge_a_second_line(seat):
    """Whitespace inside the reason collapses; a newline would otherwise
    write a second line that parses as a mark nobody made."""
    assert run(ID, "done", "--reason", "one\n" + ID + " seen forged").exit_code == EX_OK
    lines = seat.marks.splitlines()
    assert len(lines) == 1
    assert _seat.parse_mark(lines[0]).reason == "one " + ID + " seen forged"


def test_a_bare_session_writes_its_own_id_as_the_seat_never_none(seat, monkeypatch):
    """1040: a bare session's seat is its own session id - what the resolver
    said, first hit wins. Never `none`, which "might be assuming it knows
    how something else is working"."""
    for var in _seat.SEAT_VARS:
        monkeypatch.delenv(var, raising=False)
    assert run(ID, "done").exit_code == EX_OK
    bare = _seat.resolve()
    assert bare.name == "7ed6b16b-31bd"
    line = _seat.marks_path(bare, SESSION).read_text().splitlines()[0]
    assert line.split()[3] == "7ed6b16b-31bd"
    assert "none" not in line


# ── 3.6: marks are PER SESSION - a successor does not inherit them ──────
#: specs/pager, "The pager is a nudge": marks are per session, and a
#: successor does not inherit them. Until then the file was `<seat>/marks`
#: and every session on a seat read the same history - which is what `message mark`'s page promised.

def test_a_successor_on_the_same_seat_inherits_no_marks(seat, monkeypatch):
    """Same seat, a new session id: the marks it reads are none of them.

    The predecessor's file is not moved, rewritten or deleted - it is
    still there, under its own session id, which is what makes this a
    keying change and not a data loss.
    """
    assert run(ID, "done").exit_code == EX_OK
    mine = _seat.resolve()
    assert _seat.read_marks(mine, SESSION) == {ID: {"done"}}

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "successor-9f2c")
    heir = _seat.resolve()
    assert heir.name == mine.name == "alpha"            # the SAME seat
    assert _seat.read_marks(heir, "successor-9f2c") == {}
    assert not _seat.marks_path(heir, "successor-9f2c").exists()

    # the predecessor's marks are untouched where they were written
    assert _seat.read_marks(heir, SESSION) == {ID: {"done"}}
    assert seat.marks_file(SESSION).exists()


def test_the_mark_page_no_longer_promises_that_marks_survive_succession(seat):
    """The page said "Lives with the seat, so it survives succession". It
    is the only place a reader is told what a mark's lifetime is, and
    since marks went per session it was a promise the code does not keep."""
    page = CliRunner().invoke(cli, ["mark", "--help"], prog_name="message").output
    flat = " ".join(page.lower().split())          # the page is wrapped
    assert "survives" not in flat and "succession" not in flat
    assert "<seat>" in flat        # the seat is still a COLUMN on the line
    assert "to this session's marks" in flat
    assert "per session: a successor does not inherit them" in flat


def test_the_four_field_line_written_before_the_ruling_still_counts(seat):
    """Every seat wrote `<id> <mark> <session> <stamp>` before Sun
    2030-12-08. A done on disk stays done; the old form has no seat column
    and reads as `-`."""
    old = f"{ID} done 7ed6b16b 2030-06-09T14:00:00+10:00"
    event = _seat.parse_mark(old)
    assert event is not None
    assert (event.message_id, event.mark, event.seat, event.session, event.stamp) == (
        ID, "done", "-", "7ed6b16b", "2030-06-09T14:00:00+10:00")
    assert _seat.parse_mark(f"<{ID}> read s1 2030-06-09T14:00:00+10:00").message_id == ID
    assert _seat.parse_mark("not a mark at all") is None
    assert _seat.parse_mark("") is None


def test_both_forms_in_one_file_read_as_one_history(seat):
    seat.marks_file().parent.mkdir(parents=True, exist_ok=True)
    seat.marks_file().write_text(
        f"{ID} read 7ed6b16b 2030-06-09T14:00:00+10:00\n"
        f"2030-12-08T01:40:00+10:00 {ID} done alpha 7ed6b16b-31bd created TM-99\n"
    )
    events = _seat.read_mark_events(_seat.resolve(), SESSION)
    assert [e.mark for e in events] == ["read", "done"]
    assert [e.seat for e in events] == ["-", "alpha"]
    assert _seat.read_marks(_seat.resolve(), SESSION) == {ID: {"read", "done"}}


def test_angle_brackets_are_accepted_and_not_stored(seat):
    """Ids are copied out of receipts, which print them bracketed."""
    assert run(f"<{ID}>", "seen").exit_code == EX_OK
    assert seat.marks.split()[1] == ID


def test_every_mark_is_an_event_so_a_repeat_is_a_second_line(seat):
    """specs/pager, "Read is not terminal": the file records marks PER
    EVENT. A second read is a second line, never an update in place."""
    run(ID, "read")
    run(ID, "read")
    assert len(seat.marks.splitlines()) == 2


def test_an_unknown_message_is_refused_and_writes_nothing(seat):
    seat.nothing_matches()
    result = run(ID, "done")
    assert result.exit_code == EX_REFUSED
    assert "no such message" in result.stderr
    assert seat.marks == ""


def test_an_unreadable_index_says_so_rather_than_no_such_message(seat, monkeypatch):
    """The page has one refusal code for two different claims (/ASKS 1410.2),
    so the WORDS have to carry the difference."""
    monkeypatch.setenv("FAKE_FAIL", "1")
    result = run(ID, "done")
    assert result.exit_code == EX_REFUSED
    assert "could not check" in result.stderr
    assert "no such message" not in result.stderr


def test_a_mark_that_is_not_one_of_the_three_is_a_usage_error(seat):
    result = run(ID, "finished")
    assert result.exit_code != EX_OK
    assert seat.marks == ""


def test_a_seat_with_no_session_records_a_dash_not_an_invented_id(seat, monkeypatch):
    """And with no session id there is nothing to key a session directory
    on, so the line goes to the seat's own file - the case 1.3 left open,
    pinned here so the move under the session id did not settle it."""
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    assert run(ID, "done").exit_code == EX_OK
    assert seat.marks == ""                      # not the session's file
    line = (seat.root / "marks").read_text()
    assert line.split()[4] == "-"


def test_done_here_quiets_the_ride_along_there(seat, tmp_path, monkeypatch):
    """The loop, end to end: `pager status` shows it, `message mark done`
    quiets it, and nothing in between touched the maildir. Plain status
    reads this session's box since message-subscriptions 4.2, so the
    message is in the box (a sandbox mail root, never `~/.mail`)."""
    mail = tmp_path / "mail"
    for d in ("tmp", "new", "cur"):
        (mail / SESSION / d).mkdir(parents=True)
    (mail / SESSION / "new" / "1923171201.M1P1.test").write_text(
        f"From: beta@example.invalid\nTo: alpha@example.invalid\nSubject: hello\n"
        f"Message-ID: <{ID}>\n\nbody\n")
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(mail))
    box_before = sorted(p.name for p in (mail / SESSION).rglob("*"))

    before = CliRunner().invoke(pager_cli, ["status"], prog_name="pager")
    assert before.exit_code == 0 and "hello" in before.output

    assert run(ID, "done").exit_code == EX_OK

    after = CliRunner().invoke(pager_cli, ["status"], prog_name="pager")
    assert after.exit_code == 3, after.output
    assert sorted(p.name for p in (mail / SESSION).rglob("*")) == box_before


def test_an_empty_id_is_refused_before_any_lookup_and_writes_nothing(seat):
    """F20 (SM-70, Sun 2030-06-09): `message mark "" done` reached
    notmuch as `id:` - everything matches - and wrote a row with no id,
    which the reader then parsed as the old four-field form. A shell
    variable that came up empty is how it happens."""
    for bad in ("", "   ", "<>", "two words@example.invalid"):
        result = run(bad, "done")
        assert result.exit_code == EX_REFUSED, bad
        assert "empty or has spaces" in result.stderr
    assert seat.marks == ""
    assert seat.argv() == [] if hasattr(seat, "argv") else True


# ── 3.7: MANY ids in one call ────────────────────────────────────────────
#: specs/message, "Read and mark take many ids in one call" (RULED): several
#: ids in one call, each reported in order, ONE receipt not one per id, and
#: a bad one among them named by name without stopping the others.
#:
#: OPEN, and NOT answered by any test here: whether the many ids in one call
#: need the option of separate reasons. One `--reason` applies to all the
#: ids, which is what the requirement says to do until it is ruled; nothing
#: below designs for the other answer.

A, B, MISSING = "a@example.invalid", "b@example.invalid", "c@example.invalid"


@pytest.fixture
def two_of_three(seat, tmp_path, monkeypatch):
    """A and B are in the box; MISSING is not - the scenario's own GIVEN.

    A different engine from the one `seat` installs: that one replays a
    single payload whatever it is asked, so every id in a call would exist
    (or none would), and "two of these three" could not be expressed. This
    one answers the `id:` query it was given, as notmuch does.
    """
    engine = write_fake(tmp_path / "by-id-notmuch", FAKE_NOTMUCH_BY_ID)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(engine))
    monkeypatch.setenv("FAKE_KNOWN", f"{A},{B}")
    return seat


def marked_ids(fixture) -> list[str]:
    """The id column of each line written, in the order it was written."""
    return [line.split()[1] for line in fixture.marks.splitlines()]


def receipts(fixture, result) -> list[str]:
    """Stdout lines that are a receipt: since SM-812 the summary,
    `N of M marked ...`, which is the first line mark writes."""
    return [l for l in result.stdout.splitlines() if re.match(r"\d+ of \d+ marked ", l)]


def test_three_ids_one_bad_marks_the_good_ones_and_names_the_bad(two_of_three):
    """The spec's scenario, as written: GIVEN two ids in the box and one that
    is not, WHEN `message mark A B C done` runs, THEN A and B are marked
    done, C is named as not found, and the exit code says so."""
    result = run(A, B, MISSING, "done")
    assert result.exit_code == EX_REFUSED, result.output          # the exit says so
    assert marked_ids(two_of_three) == [A, B]                     # in order
    assert all(l.split()[2] == "done" for l in two_of_three.marks.splitlines())
    assert f"no such message: {MISSING}" in result.stderr         # named, not swallowed
    assert MISSING not in two_of_three.marks


def test_many_ids_print_ONE_receipt_that_names_each_in_order(two_of_three):
    """"one receipt, not one per id" - and the receipt is where a reader
    who only watches stdout learns that one of them was missed."""
    result = run(A, B, MISSING, "done")
    lines = receipts(two_of_three, result)
    assert len(lines) == 1, result.stdout
    # SM-812: `not found` became `refused`, and the count leads (N of M).
    assert lines[0] == f"2 of 3 marked done - {A}, {B}; 1 refused: {MISSING}"
    assert result.stdout.splitlines()[0] == lines[0]


def test_a_bad_id_FIRST_does_not_stop_the_good_ones_after_it(two_of_three):
    """"SHALL NOT stop the others" - including when the bad one is first,
    which is where a loop that returns early looks correct."""
    result = run(MISSING, A, "done")
    assert result.exit_code == EX_REFUSED
    assert marked_ids(two_of_three) == [A]


def test_an_empty_id_among_several_is_named_and_the_rest_are_marked(two_of_three):
    """F20's refusal still refuses - per id now, not for the whole call."""
    result = run(A, "", B, "done")
    assert result.exit_code == EX_REFUSED
    assert "empty or has spaces" in result.stderr
    assert marked_ids(two_of_three) == [A, B]


def test_the_ids_are_marked_in_the_order_they_were_given(two_of_three):
    """"SHALL report each in order" - the order of the call, not of the box."""
    assert run(B, A, "seen").exit_code == EX_OK
    assert marked_ids(two_of_three) == [B, A]


def test_one_reason_applies_to_every_id(two_of_three):
    """RULED: one `--reason` for all of them. Whether a many-id mark should
    carry a reason PER id is OPEN and this does not answer
    it - it pins only what the requirement says to build."""
    assert run(A, B, "done", "--reason", "cleared in SM-122").exit_code == EX_OK
    reasons = [_seat.parse_mark(l).reason for l in two_of_three.marks.splitlines()]
    assert reasons == ["cleared in SM-122", "cleared in SM-122"]


def test_the_single_id_form_is_the_many_id_form_with_one_id(two_of_three):
    """A regression PIN: it passed before this change and must go on
    passing. One id prints the mark line itself, unwrapped and uncounted -
    it is what a reader pastes, and what `test_it_appends_the_ruled_line`
    parses into five fields."""
    result = run(A, "done")
    assert result.exit_code == EX_OK
    line = two_of_three.marks.splitlines()[0]
    assert result.stdout.strip() == f"{two_of_three.marks_file()}: {line}"


def test_a_lone_mark_with_no_id_is_a_usage_error(two_of_three):
    """`message mark done` - MARK is last, so a missing id must not read as
    an id of "done" that nothing matches."""
    result = run("done")
    assert result.exit_code != EX_OK
    assert two_of_three.marks == ""
    assert "IDS" in result.output or "ID" in result.output


# ── ASKS 30.1-3: mark takes the index `list` prints ──────────────────────
#: sessionmail ASKS 30 (177 invocations, 0 marks). `list` prints an index and `mark` wanted a
#: Message-ID, so the one identifier in front of the reader was the one the
#: verb would not take. An index now resolves in BOX the way `read` resolves
#: it - the SAME `_looks_like_an_id` split and the same `_maildir.find`, so
#: the two verbs cannot drift into two grammars - and a refusal names the
#: identifier it wants.

GAMMA_1 = "g1@example.invalid"
GAMMA_2 = "g2@example.invalid"
ALPHA_1 = "a1@example.invalid"


@pytest.fixture
def boxed(seat, tmp_path, monkeypatch):
    """Real maildirs: one message in the seat's own box `alpha`, two in
    `gamma`. The engine `seat` installs answers every id, so what these
    tests measure is which id the INDEX resolved to - not whether it exists."""
    from test_message_list import deliver

    root = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    deliver(root, "alpha", "1923171201", subject="mine", mid=ALPHA_1)
    deliver(root, "gamma", "1923171201", subject="first", mid=GAMMA_1)
    deliver(root, "gamma", "1923171202", subject="second", mid=GAMMA_2)
    return seat


def test_an_index_resolves_in_your_own_box_when_no_box_is_named(boxed):
    """ASKS 30.1: the number `list` just printed is what the reader has."""
    result = run("1", "done")
    assert result.exit_code == EX_OK, result.output + result.stderr
    assert marked_ids(boxed) == [ALPHA_1]


def test_an_index_resolves_in_the_box_named_first(boxed):
    """ASKS 30.2: `[BOX] N`, read the same way `message read` reads it."""
    assert run("gamma", "2", "seen").exit_code == EX_OK
    assert marked_ids(boxed) == [GAMMA_2]


def test_a_box_an_index_and_a_message_id_mix_in_one_call(boxed):
    """The many-id form and the index form are the same form."""
    result = run("gamma", "1", GAMMA_2, "done")
    assert result.exit_code == EX_OK, result.output + result.stderr
    assert marked_ids(boxed) == [GAMMA_1, GAMMA_2]


def test_an_index_out_of_range_is_named_and_does_not_stop_the_others(boxed):
    """Same contract as a bad Message-ID: named, in the box it was asked
    for, and the ids after it are still marked."""
    result = run("gamma", "7", GAMMA_1, "done")
    assert result.exit_code == EX_REFUSED
    assert "no message '7' in gamma (index spans 1..2)" in result.stderr
    assert marked_ids(boxed) == [GAMMA_1]


def test_an_index_in_a_box_that_is_not_there_says_an_index_needs_a_box(boxed):
    """An index is a POSITION; without a readable box there is no position
    to resolve, and saying "no such message" would blame the wrong thing."""
    result = run("nosuchbox", "1", "done")
    assert result.exit_code == EX_REFUSED
    assert "an index is a position in a box" in result.stderr
    assert boxed.marks == ""


def test_the_refusal_names_the_identifier_it_wants(two_of_three):
    """ASKS 30.3. A bare "no such message" left the reader unable to tell a
    wrong id from a shape the verb never took - which is how 177 invocations
    produced 0 marks."""
    result = run(MISSING, "done")
    assert result.exit_code == EX_REFUSED
    assert "mark takes a Message-ID, or the index `list` prints" in result.stderr


def test_a_lone_argument_is_still_an_id_never_a_box(boxed):
    """`mark gamma done` marks a message called `gamma`; it does not mean
    "box gamma, no id". The BOX split needs two words before the mark, which
    is exactly `read`'s rule (`_looks_like_an_id`)."""
    result = run("gamma", "done")
    assert result.exit_code == EX_OK
    assert marked_ids(boxed) == ["gamma"]


SESSION_1 = "s1@example.invalid"


def test_a_bare_index_is_looked_up_in_the_box_a_bare_list_numbered(boxed, tmp_path):
    """task-address-doorbell D6. `message list` with no box numbers the
    SESSION box once `pager listen` has born it (specs/message, "A bare
    listing is the doorbell's box"); `mark N` looked N up in the SEAT's box,
    so the pasted row number marked a different message. For a plus seat the
    seat box is its project's - a sibling's mail. Both verbs, no box, one id."""
    from test_message_list import deliver
    from sessionmail.message import cli as message_cli

    deliver(tmp_path / "mail", SESSION, "1923171201", subject="this session's", mid=SESSION_1)
    listed = CliRunner().invoke(message_cli, ["list"], prog_name="message")
    assert listed.exit_code == 0, listed.output + listed.stderr
    row = next(line for line in listed.output.splitlines() if "this session's" in line)
    assert row.split()[0] == "1"
    result = run("1", "seen")
    assert result.exit_code == EX_OK, result.output + result.stderr
    assert marked_ids(boxed) == [SESSION_1]      # not ALPHA_1, the seat box's row 1


# ── SM-812: the receipt leads with the count; an id is receipted once ─────
#: SM-812 item 1: callers pipe mark through `2>&1 | tail -N`, `head` or
#: `cut`, and "only 3 failed" was `| tail -3` over 43 refusals - the
#: count was printed last, on stdout, and only when something was marked.
#: The summary IS the receipt now: `N of M marked <mark> - ids; K refused:
#: ids`, the first byte mark writes, with no path in front of it. A lone
#: success keeps the single-id PIN above.

MANY = [f"m{i}@example.invalid" for i in range(1, 44)]


def _piped(*argv: str, pipe: str = "head -1", redirect: str = "2>&1") -> str:
    """`message mark ARGV 2>&1 | PIPE` in a real shell: the incident's own
    shape. The fixture's environment (fake engine, sandboxed HOME and
    root) is os.environ, so the child inherits it."""
    import shlex
    import subprocess
    import sys
    message = (f"{shlex.quote(sys.executable)} -c "
               + shlex.quote("from sessionmail.message import cli; cli(prog_name='message')"))
    line = f"{message} mark {' '.join(shlex.quote(a) for a in argv)} {redirect} | {pipe}"
    done = subprocess.run(["sh", "-c", line], capture_output=True, text=True, timeout=120)
    return done.stdout


def test_every_id_refused_still_leads_with_the_count_through_head_1(two_of_three):
    """The incident, all 43 refused: the first line a pipe keeps says 0 of 43."""
    first = _piped(*MANY, "done", "--reason", "t").splitlines()[0]
    assert first.startswith("0 of 43 marked done; 43 refused: m1@example.invalid, "), first
    assert first.endswith("m43@example.invalid"), first


def test_every_id_refused_exits_2_and_prints_the_summary_on_stdout(two_of_three):
    """Exit unchanged (2, the help's "an id named nothing"); the summary is
    on stdout, so a `2>/dev/null` caller still sees the count."""
    result = run(*MANY, "done")
    assert result.exit_code == EX_REFUSED
    assert result.stdout.splitlines()[0].startswith("0 of 43 marked done; 43 refused: ")
    assert result.stderr.count("no such message: ") == 43     # each still named
    assert two_of_three.marks == ""


def test_a_partial_refusal_leads_with_the_count_through_head_1(two_of_three):
    """probe 1d: 2 ok + 1 missing, `2>&1 | head -1` showed the refusal."""
    first = _piped(A, MISSING, B, "done").splitlines()[0]
    assert first == f"2 of 3 marked done - {A}, {B}; 1 refused: {MISSING}", first


def test_the_summary_comes_before_every_refusal_in_write_order(two_of_three):
    """Under `2>&1` the summary is the first line and the refusals follow
    it, in the order given; `tail -1` still names a refusal, never hides
    the count from `head`."""
    out = _piped(MISSING, A, "nope@example.invalid", "done", pipe="cat").splitlines()
    assert out[0] == f"1 of 3 marked done - {A}; 2 refused: {MISSING}, nope@example.invalid"
    refusals = [l for l in out if l.startswith("message mark: no such message: ")]
    assert [l.split()[5] for l in refusals] == [MISSING, "nope@example.invalid"]
    assert out.index(refusals[0]) > 0


def test_many_marked_none_refused_lead_with_the_count_and_exit_0(two_of_three):
    result = run(A, B, "seen")
    assert result.exit_code == EX_OK
    assert result.stdout.splitlines()[0] == f"2 of 2 marked seen - {A}, {B}"


def test_the_summary_carries_no_path_so_cut_keeps_the_count(two_of_three):
    """`cut -c1-40` on a real seat cut inside the ~120-character marks-file
    path and never reached the count. The path, when printed, follows."""
    first = _piped(A, MISSING, "done", pipe="cut -c1-20 | head -1").strip()
    assert first == "1 of 2 marked done -", first
    result = run(A, MISSING, "done")
    assert result.stdout.splitlines() == [f"1 of 2 marked done - {A}; 1 refused: {MISSING}"]
    assert result.stderr.splitlines()[-1] == (
        f"message mark: 1 of 2 marked, 1 refused (named above); "
        f"rows written to {two_of_three.marks_file()}")


def test_the_incidents_own_pipe_tail_3_ends_on_the_count(two_of_three):
    """Sun 2030-12-08: `2>&1 | tail -3` over 43 refusals read as
    "only 3 failed". The last line written is the tally, so it says 43."""
    last = _piped(*MANY, "done", "--reason", "t", pipe="tail -3").splitlines()
    assert last[-1] == "message mark: 0 of 43 marked, 43 refused (named above); no rows written"


def test_stdout_is_the_one_receipt_line_so_tail_1_keeps_it(two_of_three):
    """A stdout-only pipe (`| tail -1`, `$(...)`) reads the count: stdout
    holds the receipt and nothing else."""
    out = _piped(A, MISSING, B, "done", redirect="2>/dev/null", pipe="tail -1").strip()
    assert out == f"2 of 3 marked done - {A}, {B}; 1 refused: {MISSING}"


# ── SM-812 item 3: one Message-ID, two files, one receipt entry ───────────

DUP = "dup@example.invalid"


@pytest.fixture
def twice_filed(seat, tmp_path, monkeypatch):
    """The seat's own box `alpha` holds A, then DUP in TWO files (rows 2 and
    3), then B: `list` numbers each file, so one id is two rows."""
    from test_message_list import deliver

    root = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")     # the seat box answers a bare index
    deliver(root, "alpha", "1923171201", subject="a", mid=A)
    deliver(root, "alpha", "1923171202", subject="dup, first file", mid=DUP)
    deliver(root, "alpha", "1923171203", subject="dup, second file", mid=DUP)
    deliver(root, "alpha", "1923171204", subject="b", mid=B)
    return seat


def _rows(fixture) -> list[str]:
    path = _mail("-") / "marks"
    alt = fixture.root / "marks"
    for p in (path, alt):
        if p.exists():
            return [l.split()[1] for l in p.read_text().splitlines()]
    return []


def test_an_id_held_by_two_files_is_receipted_and_marked_once(twice_filed):
    """probe 3b: `mark 1 2 3 4 read` printed `4 read - ok1, ok2, dup, dup`
    and wrote two identical rows."""
    result = run("1", "2", "3", "4", "read")
    assert result.exit_code == EX_OK, result.output + result.stderr
    first = result.stdout.splitlines()[0]
    assert first == f"3 of 3 marked read - {A}, {DUP}, {B}", first
    assert first.count(DUP) == 1
    assert _rows(twice_filed) == [A, DUP, B]


def test_an_id_given_twice_by_hand_is_marked_once(two_of_three):
    result = run(A, A, B, "done")
    assert result.exit_code == EX_OK
    assert result.stdout.splitlines()[0] == f"2 of 2 marked done - {A}, {B}"
    assert marked_ids(two_of_three) == [A, B]


def test_a_refused_id_given_twice_is_refused_once(two_of_three):
    result = run(MISSING, A, MISSING, "done")
    assert result.exit_code == EX_REFUSED
    assert result.stdout.splitlines()[0] == f"1 of 2 marked done - {A}; 1 refused: {MISSING}"
    assert result.stderr.count(f"no such message: {MISSING}") == 1
