"""pager-genie 3.28: `message` and drafts (design D13, "`message` and drafts").

openspec/changes/pager-genie, tasks.md 3.28: `message list` neither lists nor
counts a `D` file, and says in its disclosure line how many drafts it hid;
`message read` of a draft's Message-ID prints it and says it is a draft; no
`message` verb marks, claims, moves or removes a draft. *(specs/message: A
listing does not count a draft as mail, and says how many it hid)*

A draft, as D13 places it: ONE file per open turn in the `ears` box's
`cur/`, its maildir info carrying the `D` flag (`<name>:2,D`), in Internet
Message Format under the turn's Message-ID.

Hermetic: test_message_list's maildir helper under tmp_path
(SESSION_MAIL_ROOT); marks under the conftest sandbox; `mark`'s notmuch is
test_message_mark's fake, answering from a JSON file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from fakes import forest, message
from sessionmail import _maildir, _views
from sessionmail.message import DRAFT_NOTE, cli
from test_message_list import box, deliver  # noqa: F401  (fixture reused)
from test_message_mark import ID, seat  # noqa: F401  (fixture reused)
from test_message_read import marks_lines

EX_OK, EX_REFUSED = 0, 2
TURN = "turn-3@ears.invalid"


def draft(root: Path, key: str, *, mid: str = TURN, words: str = "so far I have said",
          flags: str = "D", box: str = "ears") -> Path:
    """An open turn's draft, as D13 places it: `cur/<name>:2,<flags>`."""
    for leaf in ("new", "cur", "tmp"):
        (root / box / leaf).mkdir(parents=True, exist_ok=True)
    path = root / box / "cur" / f"{key}.M1P1.host:2,{flags}"
    path.write_text("\r\n".join([
        "From: ears+airpods@hq", "To: ears+airpods@hq",
        "Date: Sun, 09 Jun 2030 12:00:00 +1000",
        "Subject: a turn, still open", f"Message-ID: <{mid}>",
        "Ears-Kind: spoken", "Ears-Device: airpods",
    ]) + "\r\n\r\n" + words + "\n")
    os.utime(path, (1_923_171_200, 1_923_171_200))
    return path


def listing(*args: str):
    return CliRunner().invoke(cli, ["list", *args])


def read(*args: str):
    return CliRunner().invoke(cli, ["read", *args])


def _open_turn(box_root: Path) -> Path:
    """Two finished turns and one draft between them, in key order."""
    deliver(box_root, "ears", "1923171201", subject="turn one", sender="ears+airpods@hq")
    path = draft(box_root, "1923171202")
    deliver(box_root, "ears", "1923171203", subject="turn two", sender="ears+airpods@hq",
            new=False)
    return path


def _snapshot(path: Path) -> tuple[str, int, int, bytes]:
    st = path.stat()
    return path.name, st.st_ino, st.st_mtime_ns, path.read_bytes()


# ── Scenario: The ears box with an open turn ───────────────────────────

def test_the_ears_box_with_an_open_turn(box):
    """GIVEN the `ears` box holding two finished turns and one draft, WHEN
    `message list ears` runs, THEN it lists the two turns, and says it hid
    one draft."""
    _open_turn(box)
    r = listing("ears")
    assert r.exit_code == EX_OK, r.output
    rows = r.output.splitlines()
    assert rows[0].startswith("   1 N ears+airpods turn one")
    assert rows[1].startswith("   2 . ears+airpods turn two")
    assert "still open" not in r.output
    assert rows[-1] == "-- ears: 2 shown, 1 hidden (1 draft); index spans all 2"


def test_two_drafts_are_counted_as_two(box):
    deliver(box, "ears", "1923171201", subject="turn one")
    draft(box, "1923171202", mid="a@ears.invalid")
    draft(box, "1923171203", mid="b@ears.invalid")
    r = listing("ears")
    assert r.output.splitlines()[-1] == "-- ears: 1 shown, 2 hidden (2 drafts); index spans all 1"


def test_a_draft_is_disclosed_alongside_every_other_reason(box):
    """The drafts are said in the same line as what the filters hid."""
    old = deliver(box, "ears", "1923171201", subject="v1")
    deliver(box, "ears", "1923171202", subject="v2", supersedes=old)
    draft(box, "1923171203")
    r = listing("ears")
    assert r.output.splitlines()[-1] == (
        "-- ears: 1 shown, 2 hidden (1 superseded, 1 draft); index spans all 2")


def test_a_draft_takes_no_row_so_no_number_moves(box):
    """The keeper writes a draft and removes it; the rows either side of it
    keep their numbers throughout, so a number pasted into `read` still
    names the same turn."""
    path = _open_turn(box)
    with_draft = listing("ears").output.splitlines()[:2]
    path.unlink()                                   # the turn closed: the keeper removes it
    assert listing("ears").output.splitlines()[:2] == with_draft
    r = read("ears", "2", "--peek")
    assert "#2  turn two" in r.output


def test_an_index_never_reaches_a_draft(box):
    _open_turn(box)
    r = read("ears", "3")
    assert r.exit_code == EX_REFUSED and "index spans 1..2" in r.output


def test_a_listing_moves_and_flags_no_draft(box):
    path = _open_turn(box)
    before = _snapshot(path)
    listing("ears")
    assert _snapshot(path) == before


@pytest.mark.parametrize("flags,is_draft", [
    ("D", True), ("DS", True), ("FD", True), ("DRS", True),
    ("S", False), ("", False), ("FRS", False),
])
def test_the_d_flag_is_what_makes_a_draft(tmp_path, flags, is_draft):
    assert _maildir.is_draft(tmp_path / f"1923171201.M1P1.host:2,{flags}") is is_draft


def test_a_name_with_no_info_is_not_a_draft(tmp_path):
    """A delivered file in new/ carries no info at all, and a `D` elsewhere
    in its name is not a flag."""
    assert not _maildir.is_draft(tmp_path / "1923171201.M1P1.hostD")
    assert not _maildir.is_draft(tmp_path / "D.M1P1.host")


# ── Scenario: Reading a draft does not claim it ────────────────────────

def test_reading_a_draft_does_not_claim_it(box, monkeypatch):
    """GIVEN a draft under Message-ID M in the `ears` box, WHEN `message
    read` is run on M, THEN it prints the draft and says it is a draft, AND
    the draft's file is where it was, with its flags unchanged."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    path = _open_turn(box)
    before = _snapshot(path)
    r = read("ears", TURN)
    assert r.exit_code == EX_OK, r.output
    assert "so far I have said" in r.output
    assert DRAFT_NOTE in r.output and "DRAFT" in r.output
    assert f"Message-ID: <{TURN}>" in r.output
    assert "draft: nothing marked, nothing moved - still cur/" + path.name in r.output
    assert "marked:" not in r.output
    assert marks_lines() == []                       # no mark written
    assert _snapshot(path) == before                 # same name (flags), inode, mtime, bytes
    assert sorted(p.name for p in (box / "ears" / "cur").iterdir()).count(path.name) == 1


@pytest.mark.parametrize("given", [TURN, f"<{TURN}>"])
def test_a_draft_is_found_by_its_id_in_either_form(box, given):
    _open_turn(box)
    assert DRAFT_NOTE in read("ears", given).output


def test_a_peek_at_a_draft_says_it_is_one_and_marks_nothing(box):
    path = _open_turn(box)
    r = read("ears", TURN, "--peek")
    assert r.exit_code == EX_OK, r.output
    assert DRAFT_NOTE in r.output
    assert marks_lines() == []
    assert path.exists()


def test_a_raw_read_keeps_the_file_verbatim_and_says_draft_aside(box):
    path = _open_turn(box)
    r = CliRunner().invoke(cli, ["read", "ears", TURN, "--raw"])
    assert r.exit_code == EX_OK, r.output
    assert r.stdout.startswith(path.read_text()[:40])
    assert DRAFT_NOTE not in r.stdout and DRAFT_NOTE in r.stderr
    assert marks_lines() == []


def test_a_draft_among_mail_in_one_call_marks_only_the_mail(box):
    _open_turn(box)
    r = read("ears", "1", TURN)
    assert r.exit_code == EX_OK, r.output
    assert "#1  turn one" in r.output and DRAFT_NOTE in r.output
    lines = marks_lines()
    assert len(lines) == 1 and "1923171201@example.invalid read" in lines[0]
    assert TURN not in "\n".join(lines)


def test_once_the_finished_turn_is_in_the_box_the_id_names_the_mail(box):
    """D13's gap: the keeper mails the finished turn under the SAME
    Message-ID and only then removes the draft. Between the two, the id
    names the MAIL: it is read, and marked, as mail."""
    deliver(box, "ears", "1923171201", subject="turn one")
    draft(box, "1923171202", mid=TURN)
    deliver(box, "ears", "1923171203", subject="the finished turn", mid=TURN)
    r = read("ears", TURN)
    assert r.exit_code == EX_OK, r.output
    assert "#2  the finished turn" in r.output
    assert DRAFT_NOTE not in r.output
    assert any(f"{TURN} read" in ln for ln in marks_lines())


def test_a_thread_read_of_a_draft_is_the_draft_alone(box):
    _open_turn(box)
    r = read("ears", TURN, "--thread", "--peek")
    assert r.exit_code == EX_OK, r.output
    assert "turn one" not in r.output and "turn two" not in r.output


def test_an_id_no_file_carries_is_still_no_message(box):
    _open_turn(box)
    r = read("ears", "nobody@ears.invalid")
    assert r.exit_code == EX_REFUSED


# ── no verb marks a draft ───────────────────────────────────────────────

def _answer(seat, *filenames: str) -> None:
    one = message(ID, 1, "ears+airpods@hq", "a turn")
    if filenames:
        one["filename"] = list(filenames)
    seat.payload.write_text(json.dumps(forest(one)))


def mark(*argv: str):
    return CliRunner().invoke(cli, ["mark", *argv], prog_name="message")


def test_marking_a_draft_is_refused_and_writes_nothing(seat):
    _answer(seat, "/m/ears/cur/1923171202.M1P1.host:2,D")
    r = mark(ID, "done", "--reason", "heard it")
    assert r.exit_code == EX_REFUSED, r.output
    assert "is a draft" in r.output and "no verb of message marks a draft" in r.output
    assert seat.marks == ""


def test_marking_a_turn_whose_draft_is_still_beside_it_marks_the_turn(seat):
    """The gap again: notmuch holds both files under the one id. Not every
    file is a draft, so the id is mail."""
    _answer(seat, "/m/ears/cur/1923171202.M1P1.host:2,D", "/m/ears/new/1923171203.M1P1.host")
    r = mark(ID, "seen")
    assert r.exit_code == EX_OK, r.output
    assert f"{ID} seen" in seat.marks


def test_an_answer_with_no_filename_marks_as_before(seat):
    """An older notmuch, or the suite's fakes: no `filename`, no change."""
    _answer(seat)
    assert mark(ID, "read").exit_code == EX_OK
    assert f"{ID} read" in seat.marks


def test_a_draft_refusal_does_not_stop_the_other_ids(seat, monkeypatch, tmp_path):
    """Many ids in one call: the draft is named, the others are marked, and
    the exit says one named nothing it could mark."""
    other = "b@example.invalid"
    ones = [message(ID, 1, "ears+airpods@hq", "a turn"),
            message(other, 2, "beta@example.invalid", "mail")]
    ones[0]["filename"] = ["/m/ears/cur/1.M1P1.host:2,D"]
    ones[1]["filename"] = ["/m/beta/new/2.M1P1.host"]
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    for one in ones:
        (by_id / one["id"]).write_text(json.dumps(forest(one)))
    fake = tmp_path / "fake-by-id"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "wanted = sys.argv[-1].removeprefix('id:')\n"
        f"p = os.path.join({str(by_id)!r}, wanted)\n"
        "sys.stdout.write(open(p).read() if os.path.exists(p) else '[]')\n")
    fake.chmod(0o755)
    from sessionmail import _notmuch
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(fake))
    r = mark(ID, other, "done")
    assert r.exit_code == EX_REFUSED, r.output
    assert f"{other} done" in seat.marks and ID not in seat.marks


def test_the_search_carries_notmuchs_filenames(seat):
    from sessionmail import _notmuch
    _answer(seat, "/m/a:2,D", "/m/b")
    assert _notmuch.search(f"id:{ID}")[0].filenames == ("/m/a:2,D", "/m/b")
    one = message(ID, 1, "x", "y")
    one["filename"] = "/m/only"
    seat.payload.write_text(json.dumps(forest(one)))
    assert _notmuch.search(f"id:{ID}")[0].filenames == ("/m/only",)


# ── the ride-along's pointer rows agree with `list` ────────────────────

def test_a_pointer_row_skips_a_draft_as_list_does(box):
    """`_views.locate` numbers a box for a cut view's `message read BOX N`:
    it must number it as `list` does, or the pointer reads the wrong turn."""
    _open_turn(box)
    last = next((box / "ears" / "cur").glob("1923171203*"))
    assert _views.locate(last.name, ["ears"]) == ("ears", 2)
