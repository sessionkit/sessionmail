"""SM-748 do-001 (openspec/changes/message-receipt, tasks 2.1; design D7, D8).

A refactor with no new verb, pinned three ways:

- D7, ONE parser: `_seat.read_marks_at(path)` and `_seat.read_verdicts_at(path)`
  take a path, return the records and how many lines they passed over, and
  `read_mark_events` / `read_verdicts` are built on them. A missing file (or a
  missing `mail/`) is no rows; a file that exists and cannot be read raises
  OSError for the caller to judge. Marks are decoded as UTF-8 with
  replacement, as verdicts already were, so one bad byte in a reason no
  longer raises `UnicodeDecodeError` out of `message list --unread` (case 19).
- D8, the words: `_mid` and `why_text` (and the two layers it names) live in
  `_why`, so `message` can say a verdict the way `pager why` says it without
  importing the pager. `pager why` prints byte for byte what it printed
  before: GOLDEN below was captured from `pager why` at 5b7ec49, before the
  move, and is pasted here unedited.
- `message` never imports the pager, in a fresh interpreter.

Hermetic: the rig of `test_pager_listen_judges` (a maildir root and a
sessionmail home under tmp_path, conftest's sandbox HOME).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_pager_listen_judges import SESSION, rig  # noqa: F401  (the rig is a fixture)

from sessionmail import _seat, _why, pager
from sessionmail.message import cli as message_cli

MID = "golden1@x.invalid"
ONE = "golden2@x.invalid"


def _mail(sid: str) -> Path:
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def _rec(**kw) -> dict:
    base = {"stamp": "2030-12-08T12:00:00+11:00", "message_id": MID, "action": "wake",
            "rule": None, "layer": "session", "line": None, "at_once": False,
            "by": "pager listen"}
    base.update(kw)
    return base


#: One record per branch of `why_text`, in the order `pager why` meets them.
RECORDS = [
    _rec(action="ride", layer="pager note", stamp="2030-12-08T12:00:01+11:00"),
    _rec(action="wake", layer="unjudged", stamp="2030-12-08T12:00:02+11:00"),
    _rec(action="ride", rule=None, layer="session", stamp="2030-12-08T12:00:03+11:00"),
    _rec(action="ride", rule="ride *", layer="built-in", line=7,
         stamp="2030-12-08T12:00:04+11:00"),
    _rec(action="wake", rule="wake from:ivan", layer="session", line=3,
         stamp="2030-12-08T12:00:05+11:00"),
    _rec(action="wake", rule="wake from:eve", layer="seat", line=0,
         stamp="2030-12-08T12:00:06+11:00"),
    _rec(action="ride", rule="ride kith", layer="focus sleep", line=4,
         stamp="2030-12-08T12:00:07+11:00"),
    _rec(action="ride", rule="ride *", layer="built-in", promoted=True,
         stamp="2030-12-08T12:00:08+11:00"),
    _rec(action="wake", rule="wake! to:$me", layer="env", line=2, at_once=True,
         stamp="2030-12-08T12:00:09+11:00", by="ride-along"),
    _rec(action="wake", rule="carry! from:$operator", layer="built-in", carry=True,
         stamp="2030-12-08T12:00:10+11:00"),
    {"message_id": MID, "action": "wake", "rule": "wake *", "layer": "locked", "line": 9},
    # The same decision again: `pager why` shows it once.
    _rec(action="wake", rule="wake from:ivan", layer="session", line=3,
         stamp="2030-12-08T12:00:11+11:00", bad=["line 2: no such verb `wkae`",
                                                 "line 5: a bad variable `$who`"]),
]

#: Captured from `pager why golden1@x.invalid` and `pager why <golden2@x.invalid>`
#: at 5b7ec49 (before the move), by this file's own setup. Never edit to pass.
GOLDEN_MID = """\
pager why <golden1@x.invalid>: ride - the pager's own confirmation (Pager-Note): the rules never saw it, and it never pages; judged by pager listen at 2030-12-08T12:00:01+11:00
pager why <golden1@x.invalid>: wake - it could not be judged, so it woke: an unknown wakes; judged by pager listen at 2030-12-08T12:00:02+11:00
pager why <golden1@x.invalid>: ride - no rule matched (session); judged by pager listen at 2030-12-08T12:00:03+11:00
pager why <golden1@x.invalid>: ride by `ride *` (the built-in default); judged by pager listen at 2030-12-08T12:00:04+11:00
pager why <golden1@x.invalid>: wake by `wake from:ivan` (session, line 3); judged by pager listen at 2030-12-08T12:00:05+11:00
pager why <golden1@x.invalid>: wake by `wake from:eve` (seat); judged by pager listen at 2030-12-08T12:00:06+11:00
pager why <golden1@x.invalid>: ride by `ride kith` (focus sleep); judged by pager listen at 2030-12-08T12:00:07+11:00
pager why <golden1@x.invalid>: wake, promoted from ride by `ride *` (the built-in default) - that listener had no ride-along to carry it; judged by pager listen at 2030-12-08T12:00:08+11:00
pager why <golden1@x.invalid>: wake by `wake! to:$me` (env, line 2) - an honoured urgent, at once; judged by ride-along at 2030-12-08T12:00:09+11:00
pager why <golden1@x.invalid>: carry by `carry! from:$operator` (the built-in default); judged by pager listen at 2030-12-08T12:00:10+11:00
pager why <golden1@x.invalid>: wake by `wake *` (locked, line 9); judged by ? at ?
pager why <golden1@x.invalid>: paged at 2030-12-08T12:01:00+11:00: pager wake (idle with children T=0s)
pager why: judged 12 times, and the rules changed between: the last line is the latest
pager why: a bad line was skipped then: line 2: no such verb `wkae`
pager why: a bad line was skipped then: line 5: a bad variable `$who`
"""

GOLDEN_ONE = """\
pager why <golden2@x.invalid>: ride by `ride *` (the built-in default); judged by pager listen at 2030-12-08T12:02:00+11:00
"""


def _plant(rig) -> Path:  # noqa: F811
    """The golden state: RECORDS (and ONE's record) in the session's
    verdicts, and two marks for MID: a page (shown) and a plain read (not)."""
    mail = _mail(SESSION)
    mail.mkdir(parents=True, exist_ok=True)
    for rec in RECORDS:
        _seat.write_verdict(rig.seat, SESSION, rec)
    _seat.write_verdict(rig.seat, SESSION, _rec(message_id=ONE, action="ride", rule="ride *",
                                                layer="built-in",
                                                stamp="2030-12-08T12:02:00+11:00"))
    with (mail / "marks").open("a", encoding="utf-8") as fh:
        fh.write(f"2030-12-08T12:01:00+11:00 {MID} seen eve {SESSION} "
                 "pager wake (idle with children T=0s)\n")
        fh.write(f"2030-12-08T12:01:30+11:00 {MID} read eve {SESSION} message read\n")
    return mail


def _why_cmd(*argv: str):
    return CliRunner().invoke(pager.cli, ["why", *argv], prog_name="pager")


# ── D8: pager why, byte for byte ─────────────────────────────────────────────

def test_pager_why_prints_exactly_what_it_printed_before_the_move(rig):  # noqa: F811
    _plant(rig)
    result = _why_cmd(MID)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert result.stdout == GOLDEN_MID


def test_pager_why_with_brackets_and_one_verdict_is_unchanged(rig):  # noqa: F811
    _plant(rig)
    result = _why_cmd(f"  <{ONE}> ")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert result.stdout == GOLDEN_ONE


def test_pager_why_with_no_verdict_still_says_so_and_exits_3(rig):  # noqa: F811
    mail = _plant(rig)
    result = _why_cmd("never@x.invalid")
    assert result.exit_code == 3
    assert result.stdout == (
        f"pager why <never@x.invalid>: no verdict recorded in session {SESSION} "
        f"({mail / 'verdicts'}). Nothing is re-judged: a verdict is recorded when "
        "`pager listen` or the ride-along judges a mail.\n")


def test_the_pager_names_are_the_moved_ones_not_copies():
    """One wording for one fact (D8): the pager re-exports, never re-defines."""
    assert pager.why_text is _why.why_text
    assert pager._mid is _why.mid
    assert pager.NOTE_LAYER == _why.NOTE_LAYER == "pager note"
    assert pager.UNJUDGED == _why.UNJUDGED == "unjudged"


@pytest.mark.parametrize("raw, want", [
    ("a@b", "a@b"), ("<a@b>", "a@b"), ("  <a@b>  ", "a@b"), ("< a@b >", "a@b"),
    ("<<a@b>>", "a@b"), ("", ""), ("A@B", "A@B"),
])
def test_mid_is_strip_then_brackets_then_strip_and_folds_no_case(raw, want):
    assert _why.mid(raw) == want


# ── D7: one parser, path-taking, counting what it passes over ───────────────

SIX = "2030-12-08T12:00:00+11:00 <a@x> read eve sid-1 message read"
FOUR = "b@x done sid-1 2030-06-09T22:34:56+10:00"


def test_read_marks_at_reads_both_forms_and_counts_the_rest(tmp_path):
    path = tmp_path / "marks"
    path.write_text(f"{SIX}\ntorn li\n{FOUR}\n\nx y z\n{SIX}\n", encoding="utf-8")
    marks, passed = _seat.read_marks_at(path)
    assert [(m.message_id, m.mark, m.seat, m.session) for m in marks] == [
        ("a@x", "read", "eve", "sid-1"), ("b@x", "done", "-", "sid-1"),
        ("a@x", "read", "eve", "sid-1")]
    assert marks[0].reason == "message read" and marks[1].stamp == "2030-06-09T22:34:56+10:00"
    assert passed == 3          # the torn line, the blank one and `x y z`


def test_read_verdicts_at_keeps_objects_with_an_id_and_counts_the_rest(tmp_path):
    path = tmp_path / "verdicts"
    good = {"message_id": "a@x", "action": "wake"}
    path.write_text("\n".join([json.dumps(good), '{"message_id": "a@x", "act',
                               json.dumps(["a list"]), json.dumps({"action": "ride"}),
                               json.dumps({"message_id": ""}), json.dumps(good)]) + "\n",
                    encoding="utf-8")
    records, passed = _seat.read_verdicts_at(path)
    assert records == [good, good]
    assert passed == 4          # torn, not an object, no id, an empty id


@pytest.mark.parametrize("reader", [_seat.read_marks_at, _seat.read_verdicts_at])
def test_a_missing_file_or_missing_dir_is_no_rows_and_nothing_passed_over(tmp_path, reader):
    assert reader(tmp_path / "marks") == ([], 0)
    assert reader(tmp_path / "no-mail-dir" / "marks") == ([], 0)
    assert not (tmp_path / "no-mail-dir").exists()      # read-only: nothing made


@pytest.mark.parametrize("reader", [_seat.read_marks_at, _seat.read_verdicts_at])
def test_a_file_that_exists_and_cannot_be_read_raises_oserror(tmp_path, reader):
    """Told apart from a missing one: the caller decides (the verb's exit 2)."""
    as_dir = tmp_path / "is-a-dir"
    as_dir.mkdir()
    with pytest.raises(OSError):
        reader(as_dir)
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    locked = tmp_path / "locked"
    locked.write_text(SIX + "\n")
    locked.chmod(0)
    try:
        with pytest.raises(OSError):
            reader(locked)
    finally:
        locked.chmod(0o600)


def test_an_empty_file_is_no_rows(tmp_path):
    (tmp_path / "marks").write_bytes(b"")
    (tmp_path / "verdicts").write_bytes(b"")
    assert _seat.read_marks_at(tmp_path / "marks") == ([], 0)
    assert _seat.read_verdicts_at(tmp_path / "verdicts") == ([], 0)


def test_the_session_readers_answer_what_the_path_readers_answer(rig):  # noqa: F811
    """read_mark_events / read_verdicts are the path readers on the session's
    own paths, with the count dropped: callers see what they always saw."""
    mail = _plant(rig)
    with (mail / "marks").open("a", encoding="utf-8") as fh:
        fh.write("torn\n" + FOUR + "\n")
    with (mail / "verdicts").open("a", encoding="utf-8") as fh:
        fh.write('{"torn\n')
    events = _seat.read_mark_events(rig.seat, SESSION)
    assert events == _seat.read_marks_at(_seat.marks_path(rig.seat, SESSION))[0]
    assert [m.mark for m in events] == ["seen", "read", "done"]
    verdicts = _seat.read_verdicts(rig.seat, SESSION)
    assert verdicts == _seat.read_verdicts_at(_seat.verdicts_path(rig.seat, SESSION))[0]
    assert len(verdicts) == len(RECORDS) + 1
    assert _seat.read_marks_at(mail / "marks")[1] == 1
    assert _seat.read_verdicts_at(mail / "verdicts")[1] == 1


def test_a_missing_marks_file_is_still_no_events(rig):  # noqa: F811
    assert _seat.read_mark_events(rig.seat, SESSION) == []
    assert _seat.read_verdicts(rig.seat, SESSION) == []
    assert _seat.read_marks(rig.seat, SESSION) == {}


# ── case 19: a byte that is not UTF-8 no longer raises ──────────────────────

BAD = (b"2030-12-08T12:00:00+11:00 bad1@x.invalid read eve " + SESSION.encode()
       + b" reason with \xff\xfe in it\n")


def test_a_bad_byte_in_a_reason_is_read_with_replacement(tmp_path):
    path = tmp_path / "marks"
    path.write_bytes(BAD)
    marks, passed = _seat.read_marks_at(path)
    assert passed == 0 and len(marks) == 1
    assert marks[0].message_id == "bad1@x.invalid" and marks[0].mark == "read"
    assert marks[0].reason == "reason with �� in it"


def test_a_bad_byte_no_longer_raises_out_of_message_list_unread(tmp_path, monkeypatch):
    """Before do-001 `read_mark_events` used `read_text()`: the locale's codec
    (UTF-8 on hq) with strict errors, so the 0xff raised UnicodeDecodeError,
    a ValueError, past `message list --unread`'s `except NoIdentity` (and
    every `except (_seat.Malformed, OSError)` in the pager). Measured at
    5b7ec49: `_seat.read_marks` on this file raised. Now the `read` mark is
    honoured and the message is hidden as read."""
    sid = "5e55a0de-0000-4000-8000-000000000019"
    mail = tmp_path / "mail"
    for d in ("tmp", "new", "cur"):
        (mail / sid / d).mkdir(parents=True)
    (mail / sid / "new" / "1923171201.M1P1.test").write_text(
        "From: beta@example.invalid\nTo: alpha@example.invalid\nSubject: hello\n"
        "Message-ID: <bad1@x.invalid>\n\nbody\n")
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(mail))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    marks = _mail(sid) / "marks"
    marks.parent.mkdir(parents=True)
    marks.write_bytes(BAD.replace(SESSION.encode(), sid.encode()))

    result = CliRunner().invoke(message_cli, ["list", "--unread"], prog_name="message")
    assert result.exception is None or isinstance(result.exception, SystemExit), \
        repr(result.exception)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "hello" not in result.stdout
    assert "marked read (--unread)" in result.stdout + result.stderr


# ── D8: message never imports the pager ─────────────────────────────────────

def test_message_and_why_import_without_the_pager():
    code = ("import sys, sessionmail.message, sessionmail._why\n"
            # why_text's lazy `rules` import, run: it must not bring the pager either
            "sessionmail._why.why_text({'action': 'ride', 'rule': 'ride *',"
            " 'layer': 'built-in'})\n"
            "bad = sorted(m for m in sys.modules if m == 'sessionmail.pager')\n"
            "print(bad)\n"
            "sys.exit(1 if bad else 0)\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60, check=False)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert done.stdout.strip() == "[]"


def test_why_does_not_import_the_pager_at_module_level():
    source = Path(_why.__file__).read_text(encoding="utf-8")
    assert "pager" not in "\n".join(line for line in source.splitlines()
                                    if line.startswith(("import ", "from ")))
