"""A `done` mark on mail that was never read: what the unread surfaces say.

`message mark N done` with no `read` before it is the quickest way a seat
says "dealt with". Done is terminal (specs/pager, "Done is terminal"), so
both surfaces that count what is still unread treat a `done` as ending it,
as a `read` does, through one predicate, `_maildir.is_unread`:

- `message list --unread` hides it, and the footer says why: `marked done
  (--unread)` when it was dealt with unopened, `marked read (--unread)`
  whenever a `read` is among its marks;
- the listener's page counts `N unread` per address
  (`_maildir.group_by_original_to`, through `pager.waiting_groups`) with
  the same test, so the count drops for it.

And `mark ... done` over mail that was unread says so on stderr, `marked
done: K, of which U were unread`, with U measured from the marks as they
stood before the call wrote anything. The exit code is unchanged: it is
said, not refused.

The defect cases were written first as STRICT xfails, each naming the
defect; the fix removed the markers. The controls beside them pin the
preconditions, and that a `read` or a `seen` mark behaves exactly as
before.

Hermetic: every maildir is a fixture under `tmp_path` (`SESSIONMAIL_ROOT`)
and the marks are written under the sandboxed `HOME` that `conftest.py`
sets; no real mail root, marks file or pager is touched.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sessionmail import _detect, _maildir, _seat, pager
from sessionmail.message import cli
from test_message_list import deliver

SESSION = "4e1c2a7d-done-unread"
MID = "never-read@example.invalid"
OTHER = "other@example.invalid"
KEY = "1923171201"

EX_OK = 0


@pytest.fixture
def box(tmp_path, monkeypatch):
    """This session's own box, born, holding one mail nobody has read.

    The session box is what a bare `list` and a bare `mark N` both number
    (`message._default_box`), so `1` names the same mail to each."""
    root = tmp_path / "mail" / "stores"
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root.parent))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    deliver(root, SESSION, KEY, subject="never read", mid=MID)
    return root / SESSION


def message(*argv: str):
    return CliRunner().invoke(cli, list(argv), prog_name="message")


def mark(*argv: str):
    result = message("mark", *argv)
    assert result.exit_code == EX_OK, result.output + result.stderr
    return result


def unread_listing() -> str:
    result = message("list", "--unread")
    assert result.exit_code == EX_OK, result.output + result.stderr
    return result.output


def unread_on_the_page(box_path) -> int:
    """The page's `N unread` for this box, built the way the listener
    builds it: the arrivals grouped with THIS session's marks."""
    arrivals = [_detect.Arrival(p.name, MID, p) for p in sorted((box_path / "new").iterdir())]
    groups = pager.waiting_groups(box_path, arrivals, _seat.resolve(), SESSION, _maildir)
    return sum(g.unread for g in groups)


# ── controls: true today, and must stay true ──────────────────────────────

def test_control_unread_mail_is_listed_and_counted_before_any_mark(box):
    """The precondition each defect case starts from."""
    assert "never read" in unread_listing()
    assert unread_on_the_page(box) == 1


def test_control_a_read_mark_hides_it_from_unread_and_drops_the_count(box):
    """`mark N read` is not what is being changed."""
    mark("1", "read")
    out = unread_listing()
    assert "never read" not in out
    assert "1 marked read (--unread)" in out
    assert unread_on_the_page(box) == 0


def test_control_a_seen_mark_leaves_it_unread(box):
    """A mark of another name keeps its meaning: `seen` is not `read`."""
    mark("1", "seen")
    assert "never read" in unread_listing()
    assert unread_on_the_page(box) == 1


def test_control_a_done_mark_is_written_and_exits_0(box):
    """The mark itself lands: the defect is in what reads it."""
    mark("1", "done")
    marks = _seat.read_marks(_seat.resolve(), SESSION)
    assert marks.get(MID) == {"done"}


# ── the defect, fixed: these were the strict xfails ───────────────────────

def test_done_without_a_read_hides_it_from_list_unread(box):
    mark("1", "done")
    assert "never read" not in unread_listing()


def test_done_without_a_read_drops_the_unread_count_on_the_page(box):
    mark("1", "done")
    assert unread_on_the_page(box) == 0


def test_done_over_unread_mail_says_how_many_were_unread_on_stderr(box, tmp_path):
    deliver(tmp_path / "mail" / "stores", SESSION, "1923171202", subject="read first", mid=OTHER)
    mark(OTHER, "read")
    result = mark("1", "2", "done")
    assert "marked done: 2, of which 1 were unread" in result.stderr


# ── the fix's edges ───────────────────────────────────────────────────────

def test_is_unread_is_ended_by_read_or_done_and_not_by_seen():
    assert _maildir.is_unread(())
    assert _maildir.is_unread({"seen"})
    assert not _maildir.is_unread({"read"})
    assert not _maildir.is_unread({"done"})
    assert not _maildir.is_unread({"seen", "done"})
    assert not _maildir.is_unread(frozenset({"read", "done"}))


def test_a_done_only_mail_is_hidden_with_its_own_reason(box):
    """Dealt with unopened: the footer says `done`, not `read`."""
    mark("1", "done")
    out = unread_listing()
    assert "1 marked done (--unread)" in out
    assert "marked read (--unread)" not in out


def test_read_and_done_still_says_marked_read(box):
    """A `read` among the marks keeps the reason readers already rely on."""
    mark("1", "read")
    mark("1", "done")
    out = unread_listing()
    assert "never read" not in out
    assert "1 marked read (--unread)" in out
    assert "marked done (--unread)" not in out


def test_done_but_never_seen_counts_unseen_and_not_unread_on_the_page(box):
    """`unseen` keeps its meaning: only a `seen` ends it."""
    mark("1", "done")
    arrivals = [_detect.Arrival(p.name, MID, p) for p in sorted((box / "new").iterdir())]
    groups = pager.waiting_groups(box, arrivals, _seat.resolve(), SESSION, _maildir)
    assert (sum(g.unseen for g in groups), sum(g.unread for g in groups),
            sum(g.total for g in groups)) == (1, 0, 1)


def test_mark_read_is_unchanged_and_says_nothing_about_done(box):
    """`mark N read`: the one stdout line naming the marks file, no stderr."""
    result = mark("1", "read")
    marks_file = _seat.marks_path(_seat.resolve(), SESSION)
    assert result.stdout.startswith(f"{marks_file}: ")
    assert result.stdout.count("\n") == 1
    assert f" {MID} read " in result.stdout
    assert result.stderr == ""


def test_mark_seen_says_nothing_about_done(box):
    result = mark("1", "seen")
    assert result.stderr == ""


def test_done_over_one_unread_mail_says_so_after_the_mark_line(box):
    """The single-id form keeps its stdout line; the count is on stderr."""
    result = mark("1", "done")
    assert result.stdout.startswith(f"{_seat.marks_path(_seat.resolve(), SESSION)}: ")
    assert result.stderr == "message mark: marked done: 1, of which 1 were unread\n"


def test_done_over_mail_already_read_says_nothing(box):
    mark("1", "read")
    result = mark("1", "done")
    assert "marked done:" not in result.stderr


def test_done_over_mail_already_done_says_nothing(box):
    """U is measured before this call writes: a second done covers nothing unread."""
    mark("1", "done")
    result = mark("1", "done")
    assert "marked done:" not in result.stderr


def test_the_count_line_comes_before_the_closing_tally(box, tmp_path):
    """Seats read `2>&1 | tail -3`: the tally stays the last line."""
    deliver(tmp_path / "mail" / "stores", SESSION, "1923171202", subject="second", mid=OTHER)
    result = mark("1", "2", "done")
    lines = result.stderr.splitlines()
    assert lines[-2] == "message mark: marked done: 2, of which 2 were unread"
    assert lines[-1].startswith("message mark: 2 of 2 marked; rows written to ")


def test_an_id_given_twice_is_counted_once(box):
    result = mark("1", MID, "done")
    assert "marked done: 1, of which 1 were unread" in result.stderr


def test_marks_that_cannot_be_read_still_mark_and_say_the_count_is_unknown(box, monkeypatch):
    """A count we could not take is not claimed; the mark still lands, exit 0."""
    real = _seat.read_marks

    def unreadable(seat, session):
        raise OSError("marks file unreadable")

    monkeypatch.setattr(_seat, "read_marks", unreadable)
    result = mark("1", "done")
    assert ("message mark: marked done: 1; could not tell how many were unread "
            "(marks file unreadable)") in result.stderr
    monkeypatch.setattr(_seat, "read_marks", real)
    assert _seat.read_marks(_seat.resolve(), SESSION).get(MID) == {"done"}
