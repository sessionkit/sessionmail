"""`message read` - open ONE message; the read is an event on the seat's
marks, never a maildir flag; a peek proves it moved nothing.

Hermetic: the maildir helper from test_message_list writes real RFC 5322
files under tmp_path; SESSION_MAIL_ROOT and SESSIONMAIL_HOME point there.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sessionmail import _seat
from sessionmail.message import cli
from test_message_list import deliver, box  # noqa: F401  (fixture reused)

EX_OK, EX_NO_MESSAGE = 0, 2


def run(*args):
    return CliRunner().invoke(cli, ["read", *args])


def marks_lines():
    p = _seat.marks_path(_seat.resolve(), _seat.session_id())
    return p.read_text().splitlines() if p.exists() else []


def test_read_by_index_prints_headers_and_body_and_marks_read(box, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    deliver(box, "alpha", "1923171201", subject="first", body="line one\nline two\n")
    r = run("1")
    assert r.exit_code == EX_OK, r.output
    assert "#1  first" in r.output and "From: beta@example.invalid" in r.output
    assert "line one\nline two" in r.output
    # _seat.MARK_LINE: stamp first, then id, mark, seat, session (1040).
    marked = [l for l in r.output.splitlines() if l.startswith("marked: ")]
    assert len(marked) == 1, r.output
    stamp, mid, mark, seat_, session = marked[0].removeprefix("marked: ").split()
    assert (mid, mark, seat_, session) == ("1923171201@example.invalid", "read", "alpha", "sess-1")
    assert len(marks_lines()) == 1 and marks_lines()[0] == marked[0].removeprefix("marked: ")


def test_read_by_id_and_a_second_read_is_a_second_line(box):
    mid = deliver(box, "alpha", "1923171201", subject="by id")
    assert run(mid).exit_code == EX_OK
    assert run(f"<{mid}>").exit_code == EX_OK
    assert len(marks_lines()) == 2


def test_peek_marks_nothing_and_proves_it(box):
    deliver(box, "alpha", "1923171201", subject="look only")
    r = run("1", "--peek")
    assert r.exit_code == EX_OK
    assert "peek: nothing marked, nothing moved - still new/1923171201.M1P1.host" in r.output
    assert marks_lines() == []


def test_reading_never_renames_or_flags(box):
    deliver(box, "alpha", "1923171201")
    before = sorted(p.name for p in (box / "alpha" / "new").iterdir())
    run("1")
    assert sorted(p.name for p in (box / "alpha" / "new").iterdir()) == before


def test_no_such_index_or_id_is_exit_2(box):
    deliver(box, "alpha", "1923171201")
    r = run("7")
    assert r.exit_code == EX_NO_MESSAGE and "index spans 1..1" in r.output
    assert run("nobody@example.invalid").exit_code == EX_NO_MESSAGE
    assert run("gamma", "1").exit_code == EX_NO_MESSAGE


def test_another_box_needs_peek_when_there_is_no_seat(box, monkeypatch):
    deliver(box, "beta", "1923171201", subject="beta's")
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    assert run("beta", "1").exit_code == 2  # usage: no seat for the mark
    r = run("beta", "1", "--peek")
    assert r.exit_code == EX_OK and "beta's" in r.output


def test_no_body_says_so_and_raw_is_verbatim(box):
    deliver(box, "alpha", "1923171201", subject="lunch EOM", body="")
    r = run("1", "--peek")
    assert "(no body)" in r.output
    r = run("1", "--raw", "--peek")
    # CliRunner folds \r\n to \n in .output; the bytes on disk are verbatim
    assert r.output.replace("\r\n", "\n").startswith("X-Original-To: alpha\nFrom: beta@example.invalid")


def test_long_body_is_cut_unless_full(box):
    deliver(box, "alpha", "1923171201", body="".join(f"l{i}\n" for i in range(60)))
    r = run("1", "--peek")
    assert "l39" in r.output and "l40" not in r.output and "cut here: 20 more lines" in r.output
    r = run("1", "--peek", "--full")
    assert "l59" in r.output and "cut here" not in r.output


def test_thread_prints_the_whole_thread_oldest_first(box):
    root = deliver(box, "alpha", "1923171201", subject="root")
    deliver(box, "alpha", "1923171203", subject="reply two", to="alpha")
    p = box / "alpha" / "new" / "1923171203.M1P1.host"
    p.write_text(p.read_text().replace("Subject:", f"In-Reply-To: <{root}>\r\nSubject:", 1))
    deliver(box, "alpha", "1923171202", subject="unrelated")
    r = run("3", "--thread", "--peek")
    assert r.exit_code == EX_OK
    assert r.output.index("#1  root") < r.output.index("#3  reply two")
    assert "unrelated" not in r.output


# ── 3.7: MANY ids in one call ────────────────────────────────────────────
#: specs/message, "Read and mark take many ids in one call" (RULED): `read` takes
#: several ids, prints each in order, writes ONE receipt not one per id, and
#: names a bad one among them without stopping the others. No maildir file is
#: renamed or flagged by any of it - a read is still only an event on this
#: session's marks.


def receipts(result) -> list[str]:
    return [l for l in result.stdout.splitlines() if l.startswith("marked: ")]


def test_many_ids_are_printed_in_order_with_one_receipt(box):
    deliver(box, "alpha", "1923171201", subject="first")
    deliver(box, "alpha", "1923171202", subject="second")
    deliver(box, "alpha", "1923171203", subject="third")
    r = run("3", "1", "2")
    assert r.exit_code == EX_OK, r.output
    # each one, in the order ASKED for - not the box's order
    assert r.output.index("#3  third") < r.output.index("#1  first") < r.output.index("#2  second")
    # ONE receipt, naming each in the same order; one mark line per message
    assert len(receipts(r)) == 1, r.stdout
    body = receipts(r)[0].removeprefix("marked: ")
    assert body == ("3 read - 1923171203@example.invalid, "
                    "1923171201@example.invalid, 1923171202@example.invalid")
    assert [l.split()[1] for l in marks_lines()] == [
        "1923171203@example.invalid", "1923171201@example.invalid",
        "1923171202@example.invalid"]


def test_a_bad_id_among_them_is_named_and_the_others_are_still_read(box):
    deliver(box, "alpha", "1923171201", subject="first")
    deliver(box, "alpha", "1923171202", subject="second")
    r = run("1", "9", "2")
    assert r.exit_code == EX_NO_MESSAGE                       # the exit says so
    assert "#1  first" in r.output and "#2  second" in r.output
    assert "no message '9' in alpha (index spans 1..2)" in r.stderr
    assert len(receipts(r)) == 1
    assert receipts(r)[0].endswith("; 1 not found: 9")
    assert len(marks_lines()) == 2                            # the good ones, marked


def test_every_id_bad_reads_nothing_and_writes_no_receipt(box):
    deliver(box, "alpha", "1923171201")
    r = run("8", "9")
    assert r.exit_code == EX_NO_MESSAGE
    assert receipts(r) == [] and marks_lines() == []
    assert "no message '8'" in r.stderr and "no message '9'" in r.stderr


def test_a_leading_box_is_a_box_and_a_number_is_an_id(box):
    """`[BOX] ID...` is split on SHAPE: digits or an `@` make an id, so
    `read 1 2` is two messages in my own box and not box `1`, message `2` -
    which is what two arguments used to mean."""
    deliver(box, "alpha", "1923171201", subject="mine one")
    deliver(box, "alpha", "1923171202", subject="mine two")
    deliver(box, "beta", "1923171201", subject="beta one")
    deliver(box, "beta", "1923171202", subject="beta two")

    mine = run("1", "2", "--peek")
    assert mine.exit_code == EX_OK
    assert "mine one" in mine.output and "mine two" in mine.output
    assert "beta" not in mine.output.replace("beta@example.invalid", "")

    theirs = run("beta", "1", "2", "--peek")
    assert theirs.exit_code == EX_OK
    assert "beta one" in theirs.output and "beta two" in theirs.output


def test_a_lone_argument_is_still_an_id_not_a_box(box):
    """`read gamma` looked for a message `gamma` before many ids, and still
    does - a BOX needs ids after it."""
    deliver(box, "alpha", "1923171201")
    assert run("gamma").exit_code == EX_NO_MESSAGE
    assert "no message 'gamma' in alpha" in run("gamma").stderr


def test_peek_with_many_ids_names_each_file_and_marks_nothing(box):
    deliver(box, "alpha", "1923171201")
    deliver(box, "alpha", "1923171202")
    r = run("1", "2", "--peek")
    assert r.exit_code == EX_OK
    assert ("peek: nothing marked, nothing moved - still "
            "new/1923171201.M1P1.host, new/1923171202.M1P1.host") in r.output
    assert marks_lines() == []


def test_many_ids_rename_and_flag_nothing_in_the_maildir(box):
    deliver(box, "alpha", "1923171201")
    deliver(box, "alpha", "1923171202")
    before = sorted(p.name for p in (box / "alpha" / "new").iterdir())
    assert run("1", "2").exit_code == EX_OK
    assert sorted(p.name for p in (box / "alpha" / "new").iterdir()) == before


def test_the_single_id_form_is_the_many_id_form_with_one_id(box):
    """A regression PIN - true before this change and kept true. One id
    prints the mark line itself, with no count in front of it."""
    deliver(box, "alpha", "1923171201", subject="alone")
    r = run("1")
    assert r.exit_code == EX_OK
    assert len(receipts(r)) == 1
    assert receipts(r)[0].removeprefix("marked: ") == marks_lines()[0]
    assert not receipts(r)[0].startswith("marked: 1 read")


def test_to_and_cc_are_the_headers_and_the_box_is_named_apart(box):
    """F37: a mail was `To: judy`, `Cc: ivan` in the file; `message read`
    on ivan's box printed `To: ivan` and no Cc, so its reader corrected the
    wrong party and forwarded to nobody. The line said `To:` and printed
    X-Original-To. Now `To:` and `Cc:` are the headers, byte for byte, and the box the MTA delivered to
    is its own field, `Box:`."""
    deliver(box, "alpha", "1923171201", subject="triage")
    p = next((box / "alpha" / "new").iterdir())
    p.write_text(p.read_text().replace(
        "To: alpha@example.invalid",
        "To: judy@example.invalid\r\nCc: alpha@example.invalid", 1))
    r = run("1")
    assert r.exit_code == EX_OK, r.output
    assert "To: judy@example.invalid    Cc: alpha@example.invalid" in r.output, r.output
    assert "Box: alpha" in r.output, r.output
    assert "To: alpha" not in r.output, r.output


def test_no_cc_prints_no_cc(box):
    deliver(box, "alpha", "1923171201", subject="plain")
    r = run("1")
    assert "To: alpha@example.invalid\n" in r.output and "Cc:" not in r.output, r.output
    assert "Box: alpha" in r.output, r.output


def test_bcc_only_prints_no_to_not_the_box(box):
    """A review nit on 4c1ba36: a Bcc-only mail
    has no To header, and `e.to or e.delivered_to or box` printed the box
    under `To:` again - the F37 conflation in a narrower case. The two facts
    stay apart: `To: -`, and Box carries the address."""
    deliver(box, "alpha", "1923171201", subject="bcc only")
    p = next((box / "alpha" / "new").iterdir())
    # bytes, not text: read_text() folds the fixture's \r\n to \n and the strip misses
    p.write_bytes(p.read_bytes().replace(b"To: alpha@example.invalid\r\n", b"", 1))
    r = run("1")
    assert r.exit_code == EX_OK, r.output
    assert "To: -\n" in r.output and "To: alpha" not in r.output, r.output
    assert "Box: alpha" in r.output, r.output


# ── SM-812 item 3: one Message-ID in two files is receipted once ──────────
#: probe 3c: `message read sid-0001 3 4` over two files of one id printed
#: `marked: 2 read - dup, dup` and wrote two rows. Both files are still
#: SHOWN (they are two files); the receipt and the marks name the id once.

def test_two_files_of_one_id_are_both_shown_and_marked_once(box):
    deliver(box, "alpha", "1923171201", subject="dup, first file", mid="dup@example.invalid")
    deliver(box, "alpha", "1923171202", subject="dup, second file", mid="dup@example.invalid")
    deliver(box, "alpha", "1923171203", subject="other", mid="o@example.invalid")
    r = run("1", "2", "3")
    assert r.exit_code == EX_OK, r.output
    assert "dup, first file" in r.output and "dup, second file" in r.output
    assert receipts(r) == ["marked: 2 read - dup@example.invalid, o@example.invalid"]
    assert [l.split()[1] for l in marks_lines()] == ["dup@example.invalid", "o@example.invalid"]
