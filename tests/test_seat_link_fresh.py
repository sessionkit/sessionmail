"""A fresh host gets no old folder for the sake of a link.

A fresh install creates none of the old folders, only `~/.sessionkit`.
`_seat._link` used to `mkdir` the old root's parent before it placed the
symlink, so the first `pager listen` on a fresh host made
`~/.sessionkit/mail/state/sessions/` (case a), an unborn `$SESSIONMAIL_HOME`
(case b), or `~/.sessionmail/sessions/` in the released 0.3.2 (case c). Now
it links only an old root that is already there; a host that has one keeps
today's behaviour exactly.

Every case runs on a tmp HOME (the autouse sandbox in conftest.py, then a
HOME of its own here); none reaches the postfix generator, and the env
carries `SESSIONMAIL_SENDMAIL=drop` and `SESSIONMAIL_VMAILBOX_GEN=/usr/bin/true`
all the same, so nothing here can reach live mail."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from sessionmail import _seat

SID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A fresh HOME holding only `.sessionkit/`, with no `SESSIONMAIL_HOME`:
    the env a first `pager listen` on a new host sees."""
    home = tmp_path / "home"
    (home / ".sessionkit").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SESSIONMAIL_HOME", raising=False)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "drop")
    monkeypatch.setenv("SESSIONMAIL_VMAILBOX_GEN", "/usr/bin/true")
    env = {"HOME": str(home), "SESSIONMAIL_SENDMAIL": "drop",
           "SESSIONMAIL_VMAILBOX_GEN": "/usr/bin/true"}
    return home, env


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


# ── a fresh host: nothing is made at the old path ────────────────────────────
def test_a_fresh_host_links_nothing_and_makes_no_old_root(fresh):   # case (a)
    home, env = fresh
    old = _seat.old_session_root(SID, env)
    assert old == home / ".sessionkit" / "mail" / "state" / "sessions" / SID
    moved = _seat.move_session_state(SID, env)
    assert not old.parent.exists() and not old.is_symlink()
    assert not (home / ".sessionmail").exists()
    assert not (home / ".sessionkit" / "mail" / "state").exists()
    # The mail dir itself is still made, private, where the session dir is.
    new = _seat.session_root(SID, env)
    assert new.is_dir() and (new / ".gitignore").read_text() == "*\n"
    # The banner says why; a fresh host is not news for the session's box.
    assert moved.lines == [f"{new} - nothing to move at {old}", _seat._no_old_root(old)]
    assert "no old root" in moved.lines[1] and str(old.parent) in moved.lines[1]
    assert not moved.noteworthy


def test_an_unborn_override_root_is_not_created(fresh, tmp_path, monkeypatch):  # case (b)
    home, env = fresh
    smhome = tmp_path / "smhome"
    monkeypatch.setenv("SESSIONMAIL_HOME", str(smhome))
    env = {**env, "SESSIONMAIL_HOME": str(smhome)}
    moved = _seat.move_session_state(SID, env)
    assert not smhome.exists()
    assert not moved.noteworthy and "no old root" in moved.lines[-1]


def test_the_second_listen_on_a_fresh_host_says_the_same_and_still_makes_nothing(fresh):
    home, env = fresh
    first = _seat.move_session_state(SID, env)
    before = _tree(home)
    again = _seat.move_session_state(SID, env)
    assert _tree(home) == before and again.lines == first.lines and not again.noteworthy


def test_ensure_root_on_a_fresh_host_makes_no_old_root(fresh):
    """The writers' door (a hook's stamp, a mark before the first listen)."""
    home, env = fresh
    root = _seat.ensure_root(SID, env)
    assert root == _seat.session_root(SID, env) and root.is_dir()
    assert not (home / ".sessionkit" / "mail" / "state").exists()
    assert not (home / ".sessionmail").exists()


def test_link_alone_never_makes_the_parent(tmp_path):
    old = tmp_path / "absent" / "sessions" / SID
    new = tmp_path / "new"
    new.mkdir()
    assert _seat._link(old, new) == _seat._no_old_root(old)
    assert not (tmp_path / "absent").exists()


# ── an old root that IS there: today's behaviour, exactly ────────────────────
def test_an_existing_override_root_still_gets_its_link(fresh, tmp_path, monkeypatch):
    home, env = fresh
    smhome = tmp_path / "smhome"
    (smhome / "sessions").mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_HOME", str(smhome))
    env = {**env, "SESSIONMAIL_HOME": str(smhome)}
    moved = _seat.move_session_state(SID, env)
    old, new = smhome / "sessions" / SID, _seat.session_root(SID, env)
    assert old.is_symlink() and old.resolve() == new.resolve()
    assert moved.lines[-1] == f"{old} now points there" and not moved.noteworthy


def test_an_existing_default_old_root_still_gets_its_link(fresh):
    """`~/.sessionkit/mail/state/sessions/` already there (a host on the
    default root): linked as before."""
    home, env = fresh
    sessions = home / ".sessionkit" / "mail" / "state" / "sessions"
    sessions.mkdir(parents=True)
    moved = _seat.move_session_state(SID, env)
    old = sessions / SID
    assert old.is_symlink() and old.resolve() == _seat.session_root(SID, env).resolve()
    assert not moved.noteworthy


def test_an_existing_old_dir_is_folded_and_linked_as_before(fresh, tmp_path, monkeypatch):
    home, env = fresh
    smhome = tmp_path / "smhome"
    old = smhome / "sessions" / SID
    (old / "stamps").mkdir(parents=True)
    (old / "marks").write_text("old 1\n")
    (old / "stamps" / "listening").write_text("1\n")
    monkeypatch.setenv("SESSIONMAIL_HOME", str(smhome))
    env = {**env, "SESSIONMAIL_HOME": str(smhome)}
    new = _seat.session_root(SID, env)
    new.mkdir(parents=True)
    (new / "marks").write_text("new 1\n")
    moved = _seat.move_session_state(SID, env)
    assert (new / "marks").read_text() == "old 1\nnew 1\n"
    assert (new / "stamps" / "listening").is_file()
    assert old.is_symlink() and old.resolve() == new.resolve() and not moved.noteworthy


def test_an_existing_correct_symlink_is_left_unchanged(fresh, tmp_path, monkeypatch):
    home, env = fresh
    smhome = tmp_path / "smhome"
    (smhome / "sessions").mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_HOME", str(smhome))
    env = {**env, "SESSIONMAIL_HOME": str(smhome)}
    _seat.move_session_state(SID, env)
    old = smhome / "sessions" / SID
    target = os.readlink(old)
    again = _seat.move_session_state(SID, env)
    assert os.readlink(old) == target and "moved earlier" in again.lines[0]
    assert not again.noteworthy


def test_a_wrong_symlink_is_still_not_linked_and_named(fresh, tmp_path, monkeypatch):
    home, env = fresh
    smhome = tmp_path / "smhome"
    (smhome / "sessions").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    old = smhome / "sessions" / SID
    new = tmp_path / "new"
    new.mkdir()
    old.symlink_to(elsewhere)
    line = _seat._link(old, new)
    assert line.startswith(f"NOT LINKED - {old} is a symlink to {elsewhere}")
