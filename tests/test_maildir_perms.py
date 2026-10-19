"""SM-783: what sessionmail CREATES in a maildir is the owner's alone.

0700 for every directory a call makes (the mail root's missing parents
included), 0600 for every mail file it writes, under a PERMISSIVE umask - and
never a mode change on anything that was already there (the never-widen half:
an existing box or parent keeps whatever its owner gave it).

The sites pinned are the four of findings/rca.md plus the cursor (A1):
`_seat.birth_box`, `_drop.write`, `_pull_imap.ensure_maildir`,
`_pull_imap.deliver`, `_pull_imap.write_cursor`, and SM-790's `_archive.merge`
(findings/rca.md A3; `apply`'s archive dir is pinned in test_box_archive.py,
beside the fleet it needs). Each runs under umask 0o022
(the common default: 0755/0644 before the fix) and 0o000 (0777/0666 before).

Fixtures are `alpha` and `testhost` (convention 9).
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from sessionmail import _archive, _drop, _maildir, _pull_imap, _seat

UMASKS = [0o022, 0o000]


def mode(p: Path) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


@pytest.fixture(params=UMASKS, ids=lambda u: f"umask{u:03o}")
def umask(request):
    old = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(old)


def every_level(top: Path, leaf: Path) -> list[Path]:
    """LEAF and each directory between it and TOP, TOP excluded."""
    out, p = [], leaf
    while p != top:
        out.append(p)
        p = p.parent
    return out


# -- birth_box (#1) ------------------------------------------------------------

def test_a_box_born_under_a_missing_root_is_0700_at_every_level(tmp_path, umask):
    root = tmp_path / ".mail" / "agents"          # neither level exists yet
    assert _seat.birth_box(root, "alpha") is True
    box = root / "alpha"
    made = {tmp_path / ".mail", root, box} | {box / d for d in ("cur", "new", "tmp")}
    assert {p: oct(mode(p)) for p in made} == {p: "0o700" for p in made}


def test_birth_box_never_touches_a_parent_that_was_there(tmp_path, umask):
    root = tmp_path / "agents"
    root.mkdir()
    os.chmod(root, 0o755)                         # the host owner's choice
    os.chmod(tmp_path, 0o751)
    _seat.birth_box(root, "alpha")
    assert mode(root) == 0o755
    assert mode(tmp_path) == 0o751
    assert mode(root / "alpha") == 0o700


def test_birth_box_leaves_an_existing_box_exactly_as_it_was(tmp_path, umask):
    box = tmp_path / "alpha"
    for d in ("cur", "new", "tmp"):
        (box / d).mkdir(parents=True)
        os.chmod(box / d, 0o755)
    os.chmod(box, 0o755)
    assert _seat.birth_box(tmp_path, "alpha") is False
    assert {mode(p) for p in (box, box / "cur", box / "new", box / "tmp")} == {0o755}


def test_a_half_box_gets_only_its_missing_dirs_made_private(tmp_path, umask):
    """A box with cur/ but no new/ is born again: new/ and tmp/ are made 0700,
    the cur/ and box dir that were there keep their mode."""
    box = tmp_path / "alpha"
    (box / "cur").mkdir(parents=True)
    os.chmod(box, 0o755)
    os.chmod(box / "cur", 0o755)
    assert _seat.birth_box(tmp_path, "alpha") is True
    assert (mode(box), mode(box / "cur")) == (0o755, 0o755)
    assert (mode(box / "new"), mode(box / "tmp")) == (0o700, 0o700)


# -- the drop (#2) -------------------------------------------------------------

def test_the_drop_writes_mail_0600_into_new(tmp_path, umask):
    _seat.birth_box(tmp_path, "alpha")
    box = tmp_path / "alpha"
    dest = _drop.write(box, b"Subject: hi\n\nbody\n", "testhost", 0)
    assert dest.parent == box / "new"
    assert mode(dest) == 0o600
    assert dest.read_bytes() == b"Subject: hi\n\nbody\n"
    assert list((box / "tmp").iterdir()) == []


def test_the_drop_never_changes_the_mode_of_a_box_it_writes_into(tmp_path, umask):
    box = tmp_path / "alpha"
    for d in ("cur", "new", "tmp"):
        (box / d).mkdir(parents=True)
        os.chmod(box / d, 0o755)
    dest = _drop.write(box, b"x\n", "testhost", 0)
    assert mode(dest) == 0o600
    assert {mode(box / d) for d in ("cur", "new", "tmp")} == {0o755}


def test_a_drop_onto_a_planted_tmp_name_does_not_follow_it(tmp_path, monkeypatch):
    """O_EXCL: a name already in tmp/ (here a symlink) is a FileExistsError,
    never a write through it. Since SM-838 that collision retries under a
    fresh name, so the copy lands and the symlink and its target are left
    exactly as they were."""
    _seat.birth_box(tmp_path, "alpha")
    box = tmp_path / "alpha"
    monkeypatch.setattr(_drop.time, "time", lambda: 1000.5)
    target = tmp_path / "elsewhere"
    target.write_bytes(b"untouched")
    name = f"1000.M500000P{os.getpid()}Q0.testhost"
    link = box / "tmp" / name
    link.symlink_to(target)
    dest = _drop.write(box, b"x\n", "testhost", 0)
    assert target.read_bytes() == b"untouched"
    assert link.is_symlink() and os.readlink(link) == str(target)
    assert dest.parent == box / "new" and dest.name != name and dest.read_bytes() == b"x\n"
    assert not dest.is_symlink() and mode(dest) == 0o600


# -- the IMAP puller (#3, #4, A1) ----------------------------------------------

def test_ensure_maildir_makes_every_missing_level_0700(tmp_path, umask):
    into = tmp_path / "deep" / "imap-inbox"
    _pull_imap.ensure_maildir(into)
    made = every_level(tmp_path, into) + [into / d for d in ("tmp", "new", "cur")]
    assert {p: oct(mode(p)) for p in made} == {p: "0o700" for p in made}


def test_ensure_maildir_never_touches_an_existing_parent(tmp_path, umask):
    parent = tmp_path / "mail"
    parent.mkdir()
    os.chmod(parent, 0o755)
    _pull_imap.ensure_maildir(parent / "imap-inbox")
    assert mode(parent) == 0o755
    assert mode(parent / "imap-inbox") == 0o700


def test_a_pulled_message_is_0600(tmp_path, umask):
    into = tmp_path / "imap-inbox"
    _pull_imap.ensure_maildir(into)
    dest = _pull_imap.deliver(into, b"Subject: pulled\n\nx\n", 7, 42)
    assert dest.parent == into / "new"
    assert mode(dest) == 0o600
    assert _pull_imap.already(into, 7, 42) == dest


def test_the_cursor_and_its_dirs_are_owner_only(tmp_path, umask):
    home = tmp_path / "home"                      # missing: made here
    target = _pull_imap.parse("imaps://alpha@testhost/INBOX")
    path = _pull_imap.cursor_path(target, home)
    _pull_imap.write_cursor(path, 7, 42)
    assert _pull_imap.read_cursor(path) == (7, 42)
    assert mode(path) == 0o600
    made = every_level(tmp_path, path.parent)
    assert {p: oct(mode(p)) for p in made} == {p: "0o700" for p in made}
    _pull_imap.write_cursor(path, 7, 43)          # a rewrite stays 0600
    assert (_pull_imap.read_cursor(path), mode(path)) == ((7, 43), 0o600)


def test_the_cursor_never_touches_a_home_that_was_there(tmp_path, umask):
    home = tmp_path / "home"
    home.mkdir()
    os.chmod(home, 0o755)
    target = _pull_imap.parse("imaps://alpha@testhost/INBOX")
    _pull_imap.write_cursor(_pull_imap.cursor_path(target, home), 7, 42)
    assert mode(home) == 0o755
    assert mode(home / "imap") == 0o700


def test_a_stale_cursor_tmp_from_a_crash_does_not_wedge_the_cursor(tmp_path):
    target = _pull_imap.parse("imaps://alpha@testhost/INBOX")
    path = _pull_imap.cursor_path(target, tmp_path)
    path.parent.mkdir(parents=True)
    path.with_name(f"{path.name}.{os.getpid()}.tmp").write_text("junk")
    _pull_imap.write_cursor(path, 7, 42)
    assert _pull_imap.read_cursor(path) == (7, 42)
    assert mode(path) == 0o600


# -- the archive's merge (A3, SM-790) -------------------------------------------

def test_merge_makes_a_missing_leaf_of_the_destination_0700(tmp_path, umask):
    """`merge` joins an archived box into one that is there: a new/ or cur/ the
    destination lacks is made 0700; the destination and the leaf it had keep
    their mode, and the mail it links keeps its own."""
    src, dst = tmp_path / "archive" / "alpha", tmp_path / "agents" / "alpha"
    for leaf in ("new", "cur"):
        (src / leaf).mkdir(parents=True)
        (src / leaf / f"1.M1P1.testhost{':2,S' if leaf == 'cur' else ''}").write_bytes(b"x\n")
    os.chmod(src / "new" / "1.M1P1.testhost", 0o600)
    (dst / "cur").mkdir(parents=True)
    os.chmod(dst, 0o755)                          # the host owner's choice
    os.chmod(dst / "cur", 0o755)
    clashes, left = _archive.merge(src, dst)
    assert (clashes, left) == ([], [])
    assert mode(dst / "new") == 0o700
    assert (mode(dst), mode(dst / "cur")) == (0o755, 0o755)
    assert mode(dst / "new" / "1.M1P1.testhost") == 0o600


def test_merge_never_touches_leaves_that_were_there(tmp_path, umask):
    src, dst = tmp_path / "archive" / "alpha", tmp_path / "agents" / "alpha"
    for box in (src, dst):
        for leaf in ("new", "cur"):
            (box / leaf).mkdir(parents=True)
            os.chmod(box / leaf, 0o750)
    (src / "new" / "1.M1P1.testhost").write_bytes(b"x\n")
    _archive.merge(src, dst)
    assert {mode(dst / leaf) for leaf in ("new", "cur")} == {0o750}


# -- the helpers ---------------------------------------------------------------

def test_make_private_dirs_reports_only_what_it_made(tmp_path, umask):
    (tmp_path / "a").mkdir()
    made = _maildir.make_private_dirs(tmp_path / "a" / "b" / "c")
    assert made == [tmp_path / "a" / "b", tmp_path / "a" / "b" / "c"]
    assert _maildir.make_private_dirs(tmp_path / "a" / "b" / "c") == []


def test_make_private_dirs_leaves_a_dir_a_racing_maker_made(tmp_path, monkeypatch):
    """A level that appears between the look and the mkdir is not ours: no chmod."""
    real_mkdir = os.mkdir
    target = tmp_path / "raced"

    def racing(path, mode=0o777, *a, **kw):
        if Path(path) == target:
            real_mkdir(path, 0o755)
            os.chmod(path, 0o755)
        return real_mkdir(path, mode, *a, **kw)

    monkeypatch.setattr(_maildir.os, "mkdir", racing)
    made = _maildir.make_private_dirs(target / "box")
    assert made == [target / "box"]
    assert mode(target) == 0o755


def test_make_private_dirs_refuses_a_file_in_the_way(tmp_path):
    (tmp_path / "f").write_text("")
    with pytest.raises(NotADirectoryError):
        _maildir.make_private_dirs(tmp_path / "f")


def test_concurrent_births_of_one_box_all_succeed(tmp_path):
    root = tmp_path / ".mail" / "agents"
    errors: list[BaseException] = []

    def born():
        try:
            _seat.birth_box(root, "alpha")
        except BaseException as exc:  # noqa: BLE001 - collected and asserted
            errors.append(exc)

    threads = [threading.Thread(target=born) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all((root / "alpha" / d).is_dir() for d in ("cur", "new", "tmp"))
