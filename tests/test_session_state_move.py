"""Session-settings 3.3a: a session's mail state (ride, stamps, marks, key,
address, subscriptions, rules) lives under the session dir sessiontools
keeps, `<session dir>/mail/`, and `pager listen` moves a live session's old
`<home>/sessions/<sid>/` there once, leaving a symlink (as ruled: split
yes; no fallback read; the gap folds, never overwrites, and the line reaches
the box; callers follow `sessiontools.settings.session_dir`)."""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from sessionmail import _reach, _seat

SID = "7f3a0c2e-1111-2222-3333-444455556666"


@pytest.fixture
def place(tmp_path, monkeypatch):
    """A sessionmail home and a user HOME, apart: the old root is under the
    first, the session dir under the second."""
    home = tmp_path / "sessionmail"
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("HOME", str(user))
    env = {"SESSIONMAIL_HOME": str(home), "HOME": str(user)}

    class Place:
        old = home / "sessions" / SID
        new = user / ".session" / "sessions" / SID / "mail"

        def seed(self, *names: str, under: Path | None = None) -> None:
            root = self.old if under is None else under
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{name} from {root.name}\n")

    p = Place()
    p.env = env
    return p


def private(path: Path) -> bool:
    return (path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o700
            and (path / ".gitignore").read_text() == "*\n")


def test_the_root_is_the_session_dirs_mail_and_the_old_root_is_named_apart(place):
    assert _seat.session_root(SID, place.env) == place.new
    assert _seat.old_session_root(SID, place.env) == place.old
    assert _seat.session_root("-", place.env) is None and _seat.old_session_root("", place.env) is None


def test_nothing_to_move_makes_a_private_mail_dir_and_points_the_old_path_at_it(place):
    moved = _seat.move_session_state(SID, place.env)
    assert private(place.new)
    assert place.old.is_symlink() and place.old.resolve() == place.new.resolve()
    assert not moved.noteworthy
    assert "nothing to move" in moved.lines[0] and moved.lines[1].startswith(f"{place.old} now")


def test_a_live_dir_moves_whole_in_one_rename_and_old_code_still_reaches_it(place):
    place.seed("stamps/listening", "ride/new/k1", "marks")
    moved = _seat.move_session_state(SID, place.env)
    assert sorted(p.name for p in place.new.iterdir()) == [".gitignore", "marks", "ride", "stamps"]
    assert (place.new / "ride" / "new" / "k1").is_file() and private(place.new)
    # The symlink: a writer on code from before the move lands in mail/.
    assert place.old.is_symlink()
    (place.old / "stamps" / "ride-along").write_text("1\n")
    assert (place.new / "stamps" / "ride-along").is_file()
    assert not moved.noteworthy and "moved 3 entries" in moved.lines[0]


def test_moved_earlier_is_a_no_op_that_says_so(place):
    _seat.move_session_state(SID, place.env)
    (place.new / "marks").write_text("kept\n")
    again = _seat.move_session_state(SID, place.env)
    assert not again.noteworthy and "moved earlier" in again.lines[0]
    assert (place.new / "marks").read_text() == "kept\n" and place.old.is_symlink()


def test_both_present_folds_in_what_is_missing_keeps_mails_copy_and_names_the_clash(place):
    place.seed("address", "rules")
    place.new.mkdir(parents=True)
    (place.new / "address").write_text("mail's own\n")
    moved = _seat.move_session_state(SID, place.env)
    assert (place.new / "address").read_text() == "mail's own\n"         # never overwritten
    assert (place.new / "rules").read_text() == "rules from " + SID + "\n"  # folded in
    assert moved.noteworthy
    assert any("address" in line and "NOT linked" in line for line in moved.lines)
    assert place.old.is_dir() and not place.old.is_symlink()            # keeps its copy
    assert (place.old / "address").is_file() and not (place.old / "rules").exists()
    assert private(place.new)


# ── the deep fold (the Rupert's (a), #9320): only a file can clash ─────────────
def test_both_present_merges_the_marks_and_verdicts_logs_old_lines_first(place):
    place.old.mkdir(parents=True)
    (place.old / "marks").write_text("old 1\nold 2\n")
    (place.old / "verdicts").write_text('{"old": 1}')                  # no last newline
    place.new.mkdir(parents=True)
    (place.new / "marks").write_text("new 1\n")
    (place.new / "verdicts").write_text('{"new": 1}\n')
    moved = _seat.move_session_state(SID, place.env)
    assert (place.new / "marks").read_text() == "old 1\nold 2\nnew 1\n"
    assert (place.new / "verdicts").read_text() == '{"old": 1}\n{"new": 1}\n'
    assert place.old.is_symlink() and not moved.noteworthy
    assert "marks merged (old lines first)" in moved.lines[0]
    assert "verdicts merged (old lines first)" in moved.lines[0]
    assert not list(place.new.glob(".*.fold.*"))                       # no tmp left


def test_both_present_folds_directories_entry_by_entry(place):
    place.seed("ride/new/k1", "ride/cur/c1", "pager-note.key")
    place.seed("ride/new/k2", under=place.new)
    moved = _seat.move_session_state(SID, place.env)
    assert sorted(p.name for p in (place.new / "ride" / "new").iterdir()) == ["k1", "k2"]
    assert (place.new / "ride" / "cur" / "c1").is_file() and (place.new / "pager-note.key").is_file()
    assert place.old.is_symlink() and not moved.noteworthy
    assert "moved 3 entries" in moved.lines[0]


@pytest.mark.parametrize("newer", ["old", "mail"])
def test_a_stamp_on_both_sides_keeps_the_newer(place, newer):
    place.seed("stamps/listening")
    place.seed("stamps/listening", under=place.new)
    early, late = 1_000_000_000, 2_000_000_000
    old_t, new_t = (late, early) if newer == "old" else (early, late)
    os.utime(place.old / "stamps" / "listening", (old_t, old_t))
    os.utime(place.new / "stamps" / "listening", (new_t, new_t))
    moved = _seat.move_session_state(SID, place.env)
    kept = place.new / "stamps" / "listening"
    assert kept.stat().st_mtime == late
    assert kept.read_text() == f"stamps/listening from {SID if newer == 'old' else 'mail'}\n"
    assert place.old.is_symlink() and not moved.noteworthy
    assert "stamps/listening kept the newer" in moved.lines[0]


def test_a_file_on_both_sides_deeper_down_is_a_clash_named_by_its_path(place):
    place.seed("ride/new/k1")
    place.seed("ride/new/k1", under=place.new)
    moved = _seat.move_session_state(SID, place.env)
    assert moved.noteworthy
    assert any("ride/new/k1" in line and "NOT linked" in line for line in moved.lines)
    assert (place.new / "ride" / "new" / "k1").read_text() == "ride/new/k1 from mail\n"
    assert (place.old / "ride" / "new" / "k1").is_file() and not place.old.is_symlink()


def test_after_a_fold_the_second_arm_is_moved_earlier(place):
    place.seed("marks", "stamps/listening")
    place.seed("marks", "stamps/ride-along", under=place.new)
    first = _seat.move_session_state(SID, place.env)
    assert place.old.is_symlink() and not first.noteworthy
    second = _seat.move_session_state(SID, place.env)
    assert not second.noteworthy and len(second.lines) == 1 and "moved earlier" in second.lines[0]
    assert (place.new / "marks").read_text() == f"marks from {SID}\nmarks from mail\n"


# ── the first new-code writer moves (the Rupert's (b), #9320) ─────────────────
def _old_mark(message_id: str, mark: str) -> str:
    return _seat.Mark("2030-06-09T21:00:00+10:00", message_id, mark, "eve", SID).line + "\n"


def test_a_hook_stamp_and_a_mark_before_the_first_listen_then_one_arm(place):
    """The Rupert's ask: after the merge the hook's ride-along stamp and a
    `message mark` both land before the session re-arms. The first of them
    moves the state; the arm finds it moved; both sets of marks read."""
    place.seed("stamps/listening")
    (place.old / "marks").write_text(_old_mark("old@test", "done"))
    _seat.touch(None, _seat.RIDE_ALONG_STAMP, SID)                     # the hook, new code
    assert place.old.is_symlink()                                       # the first writer moved it
    _seat.write_mark(SimpleNamespace(name="eve"), "new@test", "read", SID,
                     "2030-06-09T22:10:00+10:00")                       # `message mark`
    moved = _seat.move_session_state(SID, place.env)                    # the re-arm
    assert not moved.noteworthy and "moved earlier" in moved.lines[0]
    assert _seat.read_marks(None, SID) == {"old@test": {"done"}, "new@test": {"read"}}
    assert (place.new / "stamps" / "listening").is_file()
    assert (place.new / "stamps" / "ride-along").is_file() and private(place.new)


def test_the_same_with_no_first_writer_move_the_deep_fold_alone_keeps_both(place, monkeypatch):
    """(a) without (b): a writer that made mail/ before any move (a race,
    or code between the two) still ends linked, with every mark."""
    monkeypatch.setattr(_seat, "ensure_root",
                        lambda session, env=None: _seat.make_root(_seat.session_root(session, env)))
    place.seed("stamps/listening")
    (place.old / "marks").write_text(_old_mark("old@test", "done"))
    _seat.touch(None, _seat.RIDE_ALONG_STAMP, SID)
    _seat.write_mark(SimpleNamespace(name="eve"), "new@test", "read", SID,
                     "2030-06-09T22:10:00+10:00")
    assert place.old.is_dir() and not place.old.is_symlink()            # both present now
    moved = _seat.move_session_state(SID, place.env)
    assert place.old.is_symlink() and not moved.noteworthy
    assert "marks merged (old lines first)" in moved.lines[0]
    events = _seat.read_mark_events(None, SID)
    assert [e.message_id for e in events] == ["old@test", "new@test"]
    assert (place.new / "stamps" / "listening").is_file() and (place.new / "stamps" / "ride-along").is_file()


def test_the_first_writer_moves_and_the_listen_finds_it_moved_earlier(place):
    place.seed("stamps/listening", "rules")
    _seat.make_dir(place.new / "stamps", SID, place.env)
    assert place.old.is_symlink() and (place.new / "rules").is_file() and private(place.new)
    again = _seat.move_session_state(SID, place.env)
    assert not again.noteworthy and "moved earlier" in again.lines[0]


def test_a_writer_that_finds_mail_made_leaves_both_present_to_the_listen(place):
    place.seed("address", "rules")
    place.seed("address", under=place.new)
    d = _seat.make_dir(place.new / "stamps", SID, place.env)            # no fold, no link
    assert d.is_dir() and (place.old / "rules").is_file() and not place.old.is_symlink()
    moved = _seat.move_session_state(SID, place.env)
    assert moved.noteworthy and any("address" in line and "NOT linked" in line for line in moved.lines)


def test_a_clash_the_listen_named_is_not_refolded_by_the_next_writer(place):
    """The listen named the clash; then this listen's `clear_address`
    removed mail/'s address. A writer after it must not bring the old one
    back behind the banner's back."""
    place.seed("address")
    place.seed("address", under=place.new)
    assert _seat.move_session_state(SID, place.env).noteworthy
    (place.new / "address").unlink()                                    # clear_address
    _seat.touch(None, _seat.RIDE_ALONG_STAMP, SID)                      # the next tool call
    assert not (place.new / "address").exists() and (place.old / "address").is_file()


def test_an_oserror_in_a_writers_move_never_reaches_the_writer(place, monkeypatch):
    place.seed("marks")

    def refused(*a, **k):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(_seat, "move_session_state", refused)
    d = _seat.make_dir(place.new / "stamps", SID, place.env)
    assert d.is_dir() and private(place.new) and place.old.is_dir() and not place.old.is_symlink()


def test_a_rename_that_fails_moves_nothing_and_is_named(place, monkeypatch):
    place.seed("stamps/listening", "marks")
    real = os.rename

    def across_devices(src, dst, *a, **k):
        if Path(src) == place.old:
            raise OSError(errno.EXDEV, "Cross-device link")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "rename", across_devices)
    moved = _seat.move_session_state(SID, place.env)
    assert moved.noteworthy and moved.lines[0].startswith("NOT MOVED")
    assert "Cross-device link" in moved.lines[0] and "2 entries stay" in moved.lines[0]
    assert (place.old / "marks").is_file() and not place.old.is_symlink()
    assert not place.new.exists()


def test_the_gap_an_old_dir_back_before_the_link_is_folded_and_the_link_retried(place, monkeypatch):
    place.seed("marks")
    real = os.symlink
    calls = []

    def writer_got_in(src, dst, *a, **k):
        calls.append(dst)
        if len(calls) == 1:
            # A listener on old code recreates the dir in the window.
            (Path(dst) / "stamps").mkdir(parents=True)
            (Path(dst) / "stamps" / "ride-along").write_text("x\n")
            raise FileExistsError(errno.EEXIST, "File exists", str(dst))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "symlink", writer_got_in)
    moved = _seat.move_session_state(SID, place.env)
    assert len(calls) == 2
    assert (place.new / "marks").is_file() and (place.new / "stamps" / "ride-along").is_file()
    assert place.old.is_symlink() and not moved.noteworthy


def test_a_second_reappearance_is_named_not_fought(place, monkeypatch):
    def always_back(src, dst, *a, **k):
        Path(dst).mkdir(parents=True, exist_ok=True)
        raise FileExistsError(errno.EEXIST, "File exists", str(dst))

    monkeypatch.setattr(os, "symlink", always_back)
    moved = _seat.move_session_state(SID, place.env)
    assert moved.noteworthy and any(line.startswith("NOT LINKED") and "twice" in line
                                    for line in moved.lines)
    assert private(place.new)


def test_a_writer_before_the_first_listen_leaves_mail_private(place):
    d = _seat.make_dir(place.new / "stamps", SID, place.env)
    assert d.is_dir() and private(place.new)
    # A seat path is made plainly: no .gitignore dropped beside a seat's files.
    seat_dir = Path(place.env["SESSIONMAIL_HOME"]) / "seats" / "eve" / "stamps" / "-"
    _seat.make_dir(seat_dir, SID, place.env)
    assert seat_dir.is_dir() and not (seat_dir.parent / ".gitignore").exists()


def test_unmoved_names_the_old_place_only_while_the_state_sits_there(place):
    assert _reach.unmoved(SID, place.env) is None            # nothing anywhere
    place.seed("stamps/listening")
    assert _reach.unmoved(SID, place.env) == f"not moved; at {place.old}"
    _seat.move_session_state(SID, place.env)
    assert _reach.unmoved(SID, place.env) is None            # moved: a symlink now
    assert _reach.unmoved("-", place.env) is None


def test_the_rules_loader_reads_the_session_layer_from_mail(place):
    from sessionmail import rules
    place.new.mkdir(parents=True)
    (place.new / rules.RULES_FILE).write_text("ride from:*@delta.chat\n")
    layers = rules.load_layers("eve", SID, place.env)
    session = [layer for layer in layers if layer.name == rules.SESSION]
    assert session and any("delta.chat" in str(rule) for rule in session[0].rules)


def test_pager_rule_add_refuses_for_a_session_whose_state_sits_at_the_old_root(place, monkeypatch):
    """The mixed window after the merge: this session's listener runs code
    from before the move and reads `<home>/sessions/<sid>/rules`; a rule
    written to mail/rules would be law nowhere. Refuse, name the place, say
    what to do (RULINGS 4520: the old path is named, never read or written)."""
    from click.testing import CliRunner
    from sessionmail.pager import cli
    place.seed("stamps/listening", "rules")
    monkeypatch.setenv("CLAUDE_SESSION_ID", SID)
    monkeypatch.setenv("TM_AGENT_ADDRESS", "eve")
    result = CliRunner().invoke(cli, ["rule", "add", "ride", "from:*@delta.chat"], prog_name="pager")
    assert result.exit_code == 2, result.output + result.stderr
    assert f"not moved; at {place.old}" in result.stderr and "pager listen" in result.stderr
    assert not place.new.exists()                                     # nothing written
    assert (place.old / "rules").read_text() == f"rules from {SID}\n"  # and the old file untouched
    # Moved (as the next `pager listen` does), the same add is taken.
    _seat.move_session_state(SID, place.env)
    result = CliRunner().invoke(cli, ["rule", "add", "ride", "from:*@delta.chat"], prog_name="pager")
    assert result.exit_code == 0, result.output + result.stderr
    assert "from:*@delta.chat" in (place.new / "rules").read_text()


# ── at `pager listen`: the move happens first, and the banner says so ─────────
from test_pager_listen_judges import SESSION as RIG_SESSION, rig, run  # noqa: E402,F401  (the rig is a fixture)
from sessionmail import pager  # noqa: E402


def _rig_places() -> tuple[Path, Path]:
    old = Path(os.environ["SESSIONMAIL_HOME"]) / "sessions" / RIG_SESSION
    new = Path(os.environ["HOME"]) / ".session" / "sessions" / RIG_SESSION / "mail"
    return old, new


def test_pager_listen_moves_the_state_first_and_its_banner_says_what_it_did(rig):
    old, new = _rig_places()
    (old / "stamps").mkdir(parents=True)
    (old / "stamps" / "ride-along").write_text("1\n")
    rig.box()
    result = run("--timeout", "0.1")
    assert f"  state      : {new} - moved 1 entries from {old}" in result.stderr, result.stderr
    assert f"  state      : {old} now points there" in result.stderr
    assert "state mail" not in result.stderr            # a plain move is banner-only
    assert old.is_symlink() and (new / "stamps" / "ride-along").is_file() and private(new)


def test_a_clash_at_listen_is_named_in_the_banner_and_sent_to_the_sessions_box(rig, monkeypatch):
    old, new = _rig_places()
    old.mkdir(parents=True)
    (old / "rules").write_text("# old\n")
    new.mkdir(parents=True)
    (new / "rules").write_text("# new\n")
    sent = []
    monkeypatch.setattr(pager, "_mta_send",
                        lambda req, env, mint=None: (sent.append(req), "<note@test.invalid>")[1])
    rig.box()
    result = run("--timeout", "0.1")
    assert "kept the mail/ copy: rules" in result.stderr and "NOT linked" in result.stderr
    assert "  state mail : state mail sent <note@test.invalid>" in result.stderr
    notes = [r for r in sent if (pager.PAGER_NOTE, pager.NOTE_MOVED) in r.headers]
    assert len(notes) == 1 and notes[0].no_body
    assert notes[0].subject.startswith("pager: mail state - ") and "rules" in notes[0].subject
    assert (new / "rules").read_text() == "# new\n" and (old / "rules").read_text() == "# old\n"
    assert old.is_dir() and not old.is_symlink()
