"""`message receipt ID --session SID [--json]` (SM-748 do-002).

openspec/changes/message-receipt (PROPOSED): specs/message/spec.md there is
the contract, and design.md's "Test strategy" names the twenty-four cases
below by number. Two more groups come from the design review's notes for
the build (design-001, session b9e4ce99): M1, an unnamed failure exits 2,
never 1 ("no row"); M2, four spec'd branches with no case of their own.

Hermetic, as conftest makes it: `SESSIONMAIL_HOME` and `HOME` are under the
sandbox, `SESSIONS_DIR` and every session-id variable are cleared, so the
verb runs with NO seat and NO session id of its own. A session's state is
written by hand under `$HOME/.session/sessions/<sid>/mail/` (the one root
the verb reads) and `$SESSIONMAIL_HOME/sessions/<sid>` (the old root it only
looks at).

EVERY call goes through `receipt()`, which snapshots both trees (path, size,
mtime, inode, mode and ctime of every entry, by `lstat`) before and after,
and fails on any difference: case 14's read-only trap runs under every case,
not beside them. `test_14_the_trap_can_fail` shows the instrument is not
blind (POLICY 4b): `make_root` on an existing `mail/` only chmods, and the
snapshot sees it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, _seat
from sessionmail import message as message_mod
from sessionmail.message import cli as message_cli

S = "3d9d583c-44b9-7c6e-de8e-d5939a3a8d0f"     # the session asked about
T = "a29b85de-8921-b813-4c63-cc98dab885f0"     # another session
ID = "2000000000.11111.11111@hq.session-mail"
OTHER = "2000000100.22222.22222@hq.session-mail"
STAMP = "2030-01-01T12:00:00Z"


# ── the rig ──────────────────────────────────────────────────────────────

def sessions_root() -> Path:
    return Path(os.environ["HOME"]) / ".session" / "sessions"


def session_dir(sid: str = S) -> Path:
    return sessions_root() / sid


def mail(sid: str = S) -> Path:
    return session_dir(sid) / "mail"


def old_root(sid: str = S) -> Path:
    return Path(os.environ["SESSIONMAIL_HOME"]) / "sessions" / sid


def make_mail(sid: str = S) -> Path:
    """`<session dir>/mail/`, mode 0755 and no `.gitignore`: NOT what
    `make_root` leaves, so a verb that calls it (or `ensure_root`) changes
    the mode, and the snapshot in `receipt()` sees it."""
    d = mail(sid)
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o755)
    return d


def six(mid: str = ID, mark: str = "read", seat: str = "worker", session: str = S,
        reason: str = "", stamp: str = STAMP) -> str:
    """A marks line in the ruled six-field form (MARK_LINE)."""
    return f"{stamp} {mid} {mark} {seat} {session}" + (f" {reason}" if reason else "")


def four(mid: str = ID, mark: str = "done", session: str = S, stamp: str = STAMP) -> str:
    """A marks line in the four-field form written before Wed 2030-09-11."""
    return f"{mid} {mark} {session} {stamp}"


def verdict(mid: str = ID, **kw) -> dict:
    base = {"stamp": STAMP, "message_id": mid, "action": "wake", "rule": "wake from:ivan",
            "layer": "session", "line": 3, "at_once": False, "by": "pager listen"}
    base.update(kw)
    return base


def write_marks(root: Path, *lines: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "marks"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def write_verdicts(root: Path, *records: dict | str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "verdicts"
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n"
                            for r in records), encoding="utf-8")
    return path


def _entry(st: os.stat_result) -> tuple:
    return (st.st_size, st.st_mtime_ns, st.st_ino, st.st_mode, st.st_ctime_ns)


def snapshot(*roots: Path) -> dict:
    """Every entry under ROOTS, by `lstat`, never following a link: path,
    size, mtime, inode, mode, ctime. A directory that cannot be listed is
    kept as its own entry (its mode is in it)."""
    out: dict[str, tuple | None] = {}
    for root in roots:
        try:
            out[str(root)] = _entry(os.lstat(root))
        except FileNotFoundError:
            out[str(root)] = None
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                out[p] = _entry(os.lstat(p))
    return out


def trees() -> tuple[Path, Path]:
    """The two trees a call could touch: `$HOME` (session dirs, a retired
    root, settings) and `$SESSIONMAIL_HOME` (old roots, seats)."""
    return Path(os.environ["HOME"]), Path(os.environ["SESSIONMAIL_HOME"])


def receipt(*args: str):
    """`message receipt ARGS`, with the read-only trap (case 14) around it."""
    before = snapshot(*trees())
    result = CliRunner().invoke(message_cli, ["receipt", *args], prog_name="message")
    after = snapshot(*trees())
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"message receipt changed {changed}"
    return result


def as_json(result) -> dict:
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout          # exactly ONE object, one line
    return json.loads(lines[0])


def unmeasured(result) -> str:
    """Exit 2: nothing on stdout, ONE line on stderr; that line."""
    assert result.exit_code == 2, (result.exit_code, result.stdout, result.stderr)
    assert result.stdout == ""
    return result.stderr


@pytest.fixture
def locked_away():
    """Paths made mode 000 by a test, put back so tmp can be cleaned."""
    made: list[Path] = []

    def lock(path: Path) -> Path:
        path.chmod(0)
        made.append(path)
        return path
    yield lock
    for path in made:
        path.chmod(0o700)


# ── the spec's scenarios, and design.md's cases ──────────────────────────

def test_spec_a_sessions_read_is_reported_and_nothing_changes():
    root = make_mail()
    write_marks(root, six(mark="read"), six(mark="seen", reason="pager wake"))
    write_verdicts(root, verdict())
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("verdict  wake by `wake from:ivan` (session, line 3)")
    assert lines[1] == f"mark     {STAMP}  read  worker"
    assert lines[2] == f"mark     {STAMP}  seen  worker  pager wake"
    assert "RECORDED" in lines[3] and str(root) in lines[3] and "never proof" in lines[3]
    assert len(lines) == 4


def test_01_six_field_rows_every_field_in_text_and_json():
    root = make_mail()
    write_marks(root, six(mark="seen", seat="ivan", reason="ride-along line 2"))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    assert f"mark     {STAMP}  seen  ivan  ride-along line 2" in result.stdout
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert got["marks"] == [{"stamp": STAMP, "mark": "seen", "seat": "ivan",
                             "reason": "ride-along line 2"}]
    assert got["answer"] == "yes" and got["schema"] == 1
    assert got["message_id"] == ID and got["session"] == S
    assert got["looked_at"] == {"new": str(root), "old": str(old_root())}


def test_02_the_four_field_line_is_read_seat_dash():
    write_marks(make_mail(), four(mark="done", stamp=STAMP))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    assert f"mark     {STAMP}  done  -" in result.stdout
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert got["marks"] == [{"stamp": STAMP, "mark": "done", "seat": "-", "reason": ""}]


def test_03_a_pre_move_session_is_not_moved_and_never_read(locked_away):
    old = old_root()
    write_marks(old, six(mark="read"))
    locked_away(old)                                # a build that lists or opens it meets this
    for extra in ([], ["--json"]):
        err = unmeasured(receipt(ID, "--session", S, *extra))
        assert f"not moved; at {old}" in err, err
    assert old.is_dir() and not old.is_symlink()
    assert not session_dir().exists()               # no mail/ was made


def test_04_not_moved_beside_a_session_dir_with_no_mail(locked_away):
    old = old_root()
    write_marks(old, six())
    locked_away(old)
    session_dir().mkdir(parents=True)
    err = unmeasured(receipt(ID, "--session", S))
    assert f"not moved; at {old}" in err
    assert not mail().exists()


def test_05_a_half_done_move_is_not_answered():
    old = old_root()
    old.mkdir(parents=True)
    write_marks(make_mail(), six(mark="seen"))
    err = unmeasured(receipt(ID, "--session", S))
    assert f"not moved; at {old}" in err


def test_06_the_movers_symlink_each_row_once():
    root = make_mail()
    write_marks(root, six(mark="read"))
    write_verdicts(root, verdict())
    old_root().parent.mkdir(parents=True)
    old_root().symlink_to(root)
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    assert result.stdout.count("mark     ") == 1 and result.stdout.count("verdict  ") == 1


def test_07_a_session_dir_with_no_mail_is_1_not_3():
    session_dir().mkdir(parents=True)
    result = receipt(ID, "--session", S)
    assert result.exit_code == 1, result.output


def test_08_no_session_dir_no_old_root_is_3():
    result = receipt(ID, "--session", S)
    assert result.exit_code == 3
    assert str(session_dir()) in result.stdout and "no such session" in result.stdout
    assert "never" not in result.stdout.split("(")[0]         # claims nothing about the host
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert got["answer"] == "no-such-session"
    assert got["marks"] == [] and got["verdicts"] == []
    assert got["looked_at"] == {"new": str(mail()), "old": str(old_root())}


def test_09_rows_for_other_ids_only_is_1():
    root = make_mail()
    write_marks(root, six(mid=OTHER))
    write_verdicts(root, verdict(mid=OTHER))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 1
    assert as_json(receipt(ID, "--session", S, "--json"))["answer"] == "no"


def test_10_verdicts_and_their_defaults_a_verdict_alone_is_0():
    root = make_mail()
    write_verdicts(root,
                   verdict(action="wake"),
                   verdict(action="ride", rule=None, layer="built-in", line=None),
                   verdict(action="wake", rule="carry! from:$operator", layer="built-in",
                           carry=True, promoted=True, at_once=True, bad=["line 2: wkae"]))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    assert result.stdout.count("verdict  ") == 3
    # why_text's words, one wording for one fact (D8): a promoted carry.
    assert "verdict  wake, promoted from carry by `carry! from:$operator`" in result.stdout
    got = as_json(receipt(ID, "--session", S, "--json"))
    first, second, third = got["verdicts"]
    assert first == {"stamp": STAMP, "action": "wake", "carry": False, "promoted": False,
                     "at_once": False, "rule": "wake from:ivan", "layer": "session",
                     "line": 3, "by": "pager listen", "bad": []}
    assert second["rule"] is None and second["line"] is None and second["carry"] is False
    assert third["carry"] is True and third["promoted"] is True and third["at_once"] is True
    assert third["bad"] == ["line 2: wkae"]
    assert got["marks"] == []


def test_10b_only_documented_verdict_keys_reach_the_json():
    write_verdicts(make_mail(), verdict(something_new="x"))
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert set(got["verdicts"][0]) == {"stamp", "action", "carry", "promoted", "at_once",
                                       "rule", "layer", "line", "by", "bad"}
    assert set(got) == {"schema", "answer", "message_id", "session", "looked_at", "marks",
                        "verdicts", "passed_over"}


def test_11_id_normalisation_brackets_and_space_case_kept():
    write_marks(make_mail(), six())
    for given in (f"<{ID}>", f" <{ID}> ", ID, f">{ID}<"):
        assert receipt(given, "--session", S).exit_code == 0, given
    assert receipt(ID.upper(), "--session", S).exit_code == 1


def test_11b_a_stored_bracketed_id_matches_too():
    write_verdicts(make_mail(), verdict(mid=f"<{ID}>"))
    assert receipt(ID, "--session", S).exit_code == 0


@pytest.mark.parametrize("args", [
    ["", "--session", S],                         # empty id
    ["a b@c", "--session", S],                    # whitespace in the id
    ["42", "--session", S],                       # an index: no @
    [S, "--session", S],                          # a sid where the ID goes: no @
    [ID, "--session", S[:8]],                     # a short id
    [ID, "--session", "not-a-session"],
    [ID, "--session", "../x"],
    [ID, "--session", S + "\n"],                  # fullmatch, never match
    [ID, "--session", S + "0"],
    [ID],                                         # no --session
], ids=["empty", "space", "index", "swapped", "short", "word", "path", "newline", "long",
        "no-session"])
def test_12_usage_is_2_and_stdout_is_empty_even_with_json(args):
    write_marks(make_mail(), six())
    for extra in ([], ["--json"]):
        result = receipt(*args, *extra)
        assert result.exit_code == 2, (args, result.output)
        assert result.stdout == ""


def test_12b_the_callers_own_session_is_not_the_default(monkeypatch):
    """Only this input fails a build that defaults `--session` to the
    caller: conftest clears the session-id variables, so with none set a
    defaulting build exits 2 too. Here the caller HAS a session, and its
    own marks hold a `read` for ID."""
    write_marks(make_mail(), six(mark="read"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", S)
    assert _seat.session_id() == S
    result = receipt(ID)
    assert result.exit_code == 2 and result.stdout == ""
    assert "--session" in result.stderr


def test_12c_a_sid_in_upper_case_is_folded():
    write_marks(make_mail(), six())
    got = as_json(receipt(ID, "--session", S.upper(), "--json"))
    assert got["answer"] == "yes" and got["session"] == S


def test_13_an_unreadable_marks_file_is_2_even_beside_a_verdict(locked_away):
    root = make_mail()
    marks = write_marks(root, six())
    write_verdicts(root, verdict())
    locked_away(marks)
    for extra in ([], ["--json"]):
        err = unmeasured(receipt(ID, "--session", S, *extra))
        assert str(marks) in err and "could not look" in err


def test_13b_an_unreadable_mail_dir_is_2(locked_away):
    root = make_mail()
    write_marks(root, six())
    locked_away(root)
    err = unmeasured(receipt(ID, "--session", S))
    assert "could not look" in err


def test_13c_a_torn_line_is_passed_over_and_counted_never_fatal():
    root = make_mail()
    write_marks(root, six(), "torn", "")
    write_verdicts(root, verdict(), '{"message_id": "half', "[1, 2]")
    result = receipt(ID, "--session", S, "--json")
    assert result.exit_code == 0
    assert as_json(result)["passed_over"] == {"marks": 2, "verdicts": 2}


def test_14_the_trap_can_fail():
    """POLICY 4b: the snapshot every call runs under sees the wrong build.
    `make_root` on a `mail/` that already has its `.gitignore` only chmods
    (path, size, mtime and inode stay), so mode and ctime must be in it."""
    root = make_mail()
    write_marks(root, six())
    (root / ".gitignore").write_text("*\n")
    before = snapshot(*trees())
    _seat.make_root(root)
    after = snapshot(*trees())
    assert before != after
    assert before[str(root)][3] != after[str(root)][3]          # the mode


def test_15_the_environment_relocates_both_paths(monkeypatch, tmp_path):
    """Both paths come from the CALLER's environment, through the functions
    every writer uses (D6, D10): the session dir from sessiontools
    (`$HOME/.session/sessions` here), the old root from
    `$SESSIONMAIL_HOME`."""
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sm-elsewhere"))
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "sm-elsewhere").mkdir()
    write_marks(make_mail(), six())
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert got["answer"] == "yes"
    assert got["looked_at"] == {
        "new": str(_seat.session_root(S)),
        "old": str(tmp_path / "sm-elsewhere" / "sessions" / S)}
    assert got["looked_at"]["new"].startswith(str(tmp_path / "elsewhere"))


def test_16_hidden_but_it_answers():
    page = CliRunner().invoke(message_cli, ["--help"], prog_name="message").stdout
    assert "receipt" not in page
    assert message_cli.commands["receipt"].hidden
    names = [name for name, _, _ in _mcp.tools(message_cli, "message")]
    assert names and "message_receipt" not in names and not any("receipt" in n for n in names)
    result = CliRunner().invoke(message_cli, ["receipt", "--help"], prog_name="message")
    assert result.exit_code == 0
    head = result.stdout.splitlines()[:20]
    assert head[0] == "Usage: message receipt ID --session SID [--json]"
    assert any("RECORDED" in line for line in head)
    assert any("seats/*/marks" in line for line in head)
    assert any(line.lstrip().startswith("Exit:") and "2 usage" in line for line in head)


def test_17_an_old_root_linked_elsewhere_is_2_nothing_read_through_it(locked_away, tmp_path):
    elsewhere = tmp_path / "elsewhere-state"
    write_marks(elsewhere, six(mark="read"))
    locked_away(elsewhere)                          # list it or open in it: another error
    write_marks(make_mail(), six(mark="seen"))      # a row in mail/ too
    old = old_root()
    old.parent.mkdir(parents=True)
    old.symlink_to(elsewhere)
    for extra in ([], ["--json"]):
        err = unmeasured(receipt(ID, "--session", S, *extra))
        assert f"{old} is a symlink to {elsewhere}, not here" in err, err


def _hold_lock(path: Path) -> subprocess.Popen:
    child = subprocess.Popen(
        [sys.executable, "-c",
         ("import fcntl, os, sys\n"
          f"fd = os.open({str(path)!r}, os.O_RDONLY)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\n"
          "print('locked', flush=True)\n"
          "sys.stdin.read()\n")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "locked"
    return child


def test_18_a_held_movers_lock_is_2_within_the_bound_then_it_answers():
    write_marks(make_mail(), six())
    child = _hold_lock(session_dir())
    try:
        started = time.monotonic()
        err = unmeasured(receipt(ID, "--session", S))
        took = time.monotonic() - started
        assert "locked" in err and str(session_dir()) in err
        assert message_mod.RECEIPT_LOCK_SECONDS <= took < message_mod.RECEIPT_LOCK_SECONDS + 3
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    assert receipt(ID, "--session", S).exit_code == 0


def test_19_a_reason_that_is_not_utf8_is_read_never_2():
    root = make_mail()
    (root / "marks").write_bytes(six(reason="caf").encode() + b"\xe9 ok\n")
    result = receipt(ID, "--session", S, "--json")
    assert result.exit_code == 0
    assert as_json(result)["marks"][0]["reason"] == "caf� ok"


@pytest.mark.parametrize("state", ["no-mail", "empty-mail", "verdicts-only"])
def test_20_a_seat_still_booting_is_1_never_2(state):
    """taskmaster's pin 1: a missing file is no rows."""
    session_dir().mkdir(parents=True)
    if state != "no-mail":
        make_mail()
    if state == "verdicts-only":
        write_verdicts(mail(), verdict(mid=OTHER))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 1, result.output
    assert as_json(receipt(ID, "--session", S, "--json"))["answer"] == "no"


def test_21_another_sessions_mark_is_not_this_sessions():
    """taskmaster's pin 3: a Cc or a subscriber's mark on the same ID."""
    root = make_mail()
    write_marks(root, six(mark="read", session=T), four(mark="done", session=T))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 1, result.output
    assert "mark     " not in result.stdout
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert got["marks"] == [] and got["passed_over"]["marks"] == 0     # records, not torn
    write_marks(root, six(mark="read", session=T), four(mark="done", session=T),
                six(mark="seen", seat="mine", reason="ride-along"))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    assert result.stdout.count("mark     ") == 1 and "seen  mine  ride-along" in result.stdout
    assert [m["mark"] for m in as_json(receipt(ID, "--session", S, "--json"))["marks"]] == ["seen"]


def _long_answer() -> None:
    """45 rows for ID across the END of DST on hq (03:00 AEDT 2030-04-07,
    `+11:00` -> `+10:00`), placed so that three wrong builds each keep a
    different 40 from the right one:

    - newest by STRING: the four `02:5x+11:00` stamps sort after the forty
      `02:0x+10:00` ones, yet they are 15:5xZ, and those 16:0xZ;
    - the LAST 40 in the file: the five oldest are written last;
    - an unreadable stamp taken as new: it is among the five oldest.

    The newest forty by time: one verdict (02:30+10:00) and 39 marks
    (02:00:00-02:00:38+10:00). The five cut: four +11:00 marks and the
    unreadable one."""
    root = make_mail()
    newest = [six(mark="seen", reason=f"new{i}", stamp=f"2030-04-07T02:00:{i:02d}+10:00")
              for i in range(39)]
    oldest = [six(mark="read", reason=f"old{i}", stamp=f"2030-04-07T02:5{i}:00+11:00")
              for i in range(4)]
    write_marks(root, *newest, *oldest, six(mark="done", reason="old-unreadable",
                                            stamp="not-a-stamp"))
    write_verdicts(root, verdict(stamp="2030-04-07T02:30:00+10:00"))


def test_22_a_long_answer_keeps_the_newest_40_by_time_and_says_so_last():
    _long_answer()
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    rows = [line for line in lines if line.startswith(("mark     ", "verdict  "))]
    assert len(rows) == 40
    assert sum(1 for r in rows if r.startswith("verdict  ")) == 1
    assert {r.split()[-1] for r in rows if r.startswith("mark")} == {f"new{i}" for i in range(39)}
    assert lines[-1] == "5 older rows not shown; --json has all"          # LAST (ruling F)
    assert "45 rows" in lines[-2] and "RECORDED" in lines[-2]
    got = as_json(receipt(ID, "--session", S, "--json"))
    assert len(got["marks"]) + len(got["verdicts"]) == 45                 # never capped


def test_22b_exactly_40_rows_has_no_cut_line():
    write_marks(make_mail(), *[six(reason=f"r{i}", stamp=f"2030-01-08T12:{i:02d}:00+11:00")
                               for i in range(40)])
    result = receipt(ID, "--session", S)
    assert result.exit_code == 0
    assert "older rows not shown" not in result.stdout
    assert result.stdout.count("mark     ") == 40


def test_22c_kept_rows_print_in_recorded_order():
    stamps = ["2030-01-08T12:05:00+11:00", "2030-01-08T12:01:00+11:00",
              "2030-01-08T12:03:00+11:00"]
    write_marks(make_mail(), *[six(reason=f"r{i}", stamp=s) for i, s in enumerate(stamps)])
    rows = [line for line in receipt(ID, "--session", S).stdout.splitlines()
            if line.startswith("mark")]
    assert [r.split()[-1] for r in rows] == ["r0", "r1", "r2"]


def test_23_a_seats_file_is_never_read(locked_away):
    """Ruling H: a session that predates per-session marks may hold
    marks only in its seat's file, and the verb never reads it."""
    seat_marks = write_marks(Path(os.environ["SESSIONMAIL_HOME"]) / "seats" / "worker",
                             six(mark="read"))
    locked_away(seat_marks)
    write_marks(make_mail(), six(mid=OTHER))
    result = receipt(ID, "--session", S)
    assert result.exit_code == 1, result.output
    last = result.stdout.splitlines()[-1]
    assert str(mail()) in last and "that root alone" in last


def test_24_a_session_dir_under_a_retired_root_only_is_3():
    """Ruling G. The retired dir holds a `read` for ID, so a
    build that reads it answers 0, and one that counts it as known, 1."""
    write_marks(Path(os.environ["HOME"]) / ".taskmaster" / "sessions" / S / "mail",
                six(mark="read"))
    result = receipt(ID, "--session", S, "--json")
    assert result.exit_code == 3
    got = as_json(result)
    assert got["answer"] == "no-such-session"
    assert got["looked_at"] == {"new": str(mail()), "old": str(old_root())}


# ── M1: an unnamed failure is 2, never 1 ("no row") ─────────────────────

def test_m1_a_reader_that_raises_is_2_never_1(monkeypatch):
    write_marks(make_mail(), six())

    def boom(path):
        raise ValueError("not a marks file")
    monkeypatch.setattr(_seat, "read_marks_at", boom)
    for extra in ([], ["--json"]):
        err = unmeasured(receipt(ID, "--session", S, *extra))
        assert "could not look" in err and "not a marks file" in err


def test_m1_no_sessiontools_is_2(monkeypatch):
    """`_seat._settings` raises RuntimeError when sessiontools.settings is
    missing: the session dir cannot be found, so the verb could not look."""
    def missing():
        raise RuntimeError("sessiontools.settings is not installed")
    monkeypatch.setattr(_seat, "_settings", missing)
    err = unmeasured(receipt(ID, "--session", S, "--json"))
    assert "sessiontools.settings is not installed" in err


# ── M2: spec'd branches with no case of their own ────────────────────────

def test_m2_an_old_root_that_is_a_regular_file_is_2():
    old = old_root()
    old.parent.mkdir(parents=True)
    old.write_text("not a dir\n")
    write_marks(make_mail(), six())
    err = unmeasured(receipt(ID, "--session", S))
    assert f"not moved; at {old}" in err and "not a directory" in err


def test_m2_the_movers_dangling_link_with_no_session_dir_is_3():
    """D6 row 3: the link resolves where `mail/` would be, so it is the
    mover's own; with no session dir, there is no such session."""
    old = old_root()
    old.parent.mkdir(parents=True)
    old.symlink_to(mail())
    assert not mail().exists()
    result = receipt(ID, "--session", S)
    assert result.exit_code == 3, result.output
    assert "no such session" in result.stdout


def test_m2_unreadable_verdicts_beside_a_mark_row_is_2(locked_away):
    root = make_mail()
    write_marks(root, six())
    locked_away(write_verdicts(root, verdict()))
    err = unmeasured(receipt(ID, "--session", S, "--json"))
    assert str(root / "verdicts") in err


# ── the spec's JSON scenarios ────────────────────────────────────────────

def test_spec_a_program_reads_the_answer():
    root = make_mail()
    write_verdicts(root, verdict(action="ride", rule="ride *", layer="built-in", line=None))
    write_marks(root, six(mark="seen", reason="ride-along"))
    result = receipt(ID, "--session", S, "--json")
    assert result.exit_code == 0
    got = as_json(result)
    assert got["answer"] == "yes"
    assert len(got["marks"]) == 1 and len(got["verdicts"]) == 1
    assert got["verdicts"][0]["carry"] is False
    assert set(got["looked_at"]) == {"new", "old"}


def test_spec_not_moved_prints_no_object():
    old_root().mkdir(parents=True)
    err = unmeasured(receipt(ID, "--session", S, "--json"))
    assert "not moved; at" in err and str(old_root()) in err
