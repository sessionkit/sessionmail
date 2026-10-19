"""The parent lookup: notmuch when it is here, the maildir walk as the fallback.

Amending send-in-python decision 5 ("notmuch out of the send path"): this is
mail, so notmuch is the lookup, with the walk as a fallback. The walk costs
1.2-3.6 s over 56,677 files on hq (friction.md item 1); notmuch answers in
milliseconds but may be missing (a bare Mac) or behind (a just-arrived
parent not yet indexed). Hermetic: a FAKE notmuch that prints what it is
told and logs what it was asked, and a walk that is watched, not assumed.
"""

from __future__ import annotations

import sys

import pytest

from fakes import write_fake
from sessionmail import _sender

FAKE_NOTMUCH = """#!{python}
import os, sys
open(os.environ["FAKE_NOTMUCH_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
sys.stdout.write(open(os.environ["FAKE_NOTMUCH_OUT"]).read())
raise SystemExit(int(os.environ.get("FAKE_NOTMUCH_RC", "0")))
"""

PARENT = "<1908540800.1.2@hq.session-mail>"


def message(path, msgid):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"From: a\nTo: b\nMessage-ID: {msgid}\nReferences: <root@x>\n\nbody\n".encode())
    return str(path)


@pytest.fixture
def world(tmp_path, monkeypatch):
    bin_dir, root, out, log = tmp_path / "bin", tmp_path / "mail", tmp_path / "nm.out", tmp_path / "nm.log"
    bin_dir.mkdir()
    out.write_text("")
    walked = []
    real_walk = _sender.find_messages

    def watched_walk(r, ids):
        walked.append(set(ids))
        return real_walk(r, ids)

    monkeypatch.setattr(_sender, "find_messages", watched_walk)

    class World:
        pass

    w = World()
    w.root, w.bin, w.walked = root, bin_dir, walked
    w.parent = message(root / "alpha" / "cur" / "1908540800.M1P1.hq:2,S", PARENT)
    w.says = lambda text: out.write_text(text)
    w.asked = lambda: log.read_text() if log.exists() else ""
    w.env = {"PATH": str(bin_dir), "HOME": str(tmp_path),
             "FAKE_NOTMUCH_OUT": str(out), "FAKE_NOTMUCH_LOG": str(log)}
    profile = tmp_path / ".config" / "notmuch" / "sessionkit" / "config"   # host mode's
    profile.parent.mkdir(parents=True)
    profile.write_text(f"[database]\npath={root}\n")
    w.with_notmuch = lambda: write_fake(bin_dir / "notmuch", FAKE_NOTMUCH.format(python=sys.executable))
    return w


def test_notmuch_answers_and_the_walk_never_runs(world):
    world.with_notmuch()
    world.says(world.parent + "\n")
    assert _sender.find_parents(world.root, {PARENT}, world.env) == {PARENT: world.parent}
    assert world.walked == [], "a hit from the index costs no walk"
    assert 'search --output=files id:"1908540800.1.2@hq.session-mail"' in world.asked()


def test_no_notmuch_on_the_path_the_walk_answers(world):
    assert _sender.find_parents(world.root, {PARENT}, world.env) == {PARENT: world.parent}
    assert world.walked == [{PARENT}]


def test_an_index_that_has_not_seen_the_parent_yet_falls_back_to_the_walk(world):
    world.with_notmuch()
    world.says("")
    assert _sender.find_parents(world.root, {PARENT}, world.env) == {PARENT: world.parent}
    assert world.asked() and world.walked == [{PARENT}]


def test_a_failing_notmuch_falls_back_to_the_walk(world):
    world.with_notmuch()
    world.says(world.parent + "\n")
    world.env["FAKE_NOTMUCH_RC"] = "1"
    assert _sender.find_parents(world.root, {PARENT}, world.env) == {PARENT: world.parent}
    assert world.walked == [{PARENT}]


def test_a_file_outside_the_mail_root_is_not_taken(world, tmp_path):
    world.with_notmuch()
    elsewhere = message(tmp_path / "other" / "cur" / "x:2,S", PARENT)
    world.says(elsewhere + "\n")
    assert _sender.find_parents(world.root, {PARENT}, world.env) == {PARENT: world.parent}
    assert world.walked == [{PARENT}], "notmuch's database may cover another root"


def test_a_file_whose_own_message_id_disagrees_is_not_taken(world):
    world.with_notmuch()
    wrong = message(world.root / "alpha" / "cur" / "y:2,S", "<someone-else@hq.session-mail>")
    world.says(wrong + "\n")
    assert _sender.find_parents(world.root, {PARENT}, world.env) == {PARENT: world.parent}


def test_two_targets_one_indexed_the_walk_looks_only_for_the_other(world):
    world.with_notmuch()
    other = "<1908540801.1.2@hq.session-mail>"
    second = message(world.root / "beta" / "new" / "1908540801.M1P1.hq", other)
    world.says(world.parent + "\n")               # the fake answers the same for both ids
    found = _sender.find_parents(world.root, {PARENT, other}, world.env)
    assert found == {PARENT: world.parent, other: second}
    assert world.walked == [{other}]


def test_the_id_is_asked_quoted_so_any_id_parses(world):
    """A review nit: unquoted, an id with a space, a paren
    or a quote is a query parse error. Quoted, any `"` doubled, notmuch reads
    it whole (checked on hq: `id:"a""b c(@x"` exits 0)."""
    assert _sender.id_term('a "b" (c)@x') == 'id:"a ""b"" (c)@x"'
    assert _sender.id_term("1908540800.1.2@hq.session-mail") == 'id:"1908540800.1.2@hq.session-mail"'
