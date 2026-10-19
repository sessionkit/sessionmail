"""`message mark`'s own-box fallback (SM-731 do-005, design §3, §13; host mode SM-714).

A store index holds no session box in phase 1 (OQ2, RULED), so in
store mode, when the index answers empty, refuses, or
holds only drafts under the id (widened on review c8d26d51), `mark` looks
for the id in the seat's OWN two boxes - its session box and its base box - and nowhere else:

- found there: marked, through the same draft check an indexed hit gets;
- not found: today's "no such message", exit 2;
- a refusal still prints its line, so the seat learns its index is missing;
- host mode (unset, or `host`): the SAME fallback, the same two boxes
  (SM-714 fix-001). This reverses SM-731 design §10, which pinned host mode
  as "exactly today, no fallback": a host index lags delivery, so a mail
  `list` just printed was "no such message" (SM-714 rca-001, verdict a). It
  holds to one scope: the seat's own two boxes, never another's.

The seat is `test_message_mark`'s (`alpha`, session `7ed6b16b-31bd`, a fake
engine answering from a JSON file). The boxes are real maildirs under the
conftest's sandboxed SESSION_MAIL_ROOT. The env the resolver reads is the
conftest's, which drops every SESSIONMAIL_* and NOTMUCH_DATABASE and
NOTMUCH_PROFILE; NOTMUCH_CONFIG is dropped here too (design §13 hygiene).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _index
from sessionmail.message import cli
from fakes import forest, message
from test_message_list import deliver
from test_message_mark import ID, SESSION, seat  # noqa: F401  (fixture reused)

EX_OK, EX_REFUSED = 0, 2
MINE = "mine@example.invalid"


@pytest.fixture
def store(seat, tmp_path, monkeypatch):  # noqa: F811
    """`seat`, in store mode with a resolvable profile, an index that holds
    nothing, and empty session and base boxes."""
    monkeypatch.delenv("NOTMUCH_CONFIG", raising=False)
    root = Path(os.environ["SESSION_MAIL_ROOT"])
    for box in (SESSION, "alpha", "gamma"):
        for sub in ("new", "cur", "tmp"):
            (root / box / sub).mkdir(parents=True, exist_ok=True)
    profile = Path(os.environ["HOME"]) / ".notmuch-config.alpha"
    profile.write_text("[database]\n")
    monkeypatch.setenv(_index.VAR, _index.STORE)
    monkeypatch.setenv(_index.PROFILE_VAR, "alpha")
    _index.clear_cache()
    seat.nothing_matches()
    seat.mail = root
    seat.profile = profile
    seat.log = tmp_path / "argv.log"
    yield seat
    _index.clear_cache()


def mark(*argv: str):
    return CliRunner().invoke(cli, ["mark", *argv], prog_name="message")


def draft(root: Path, box: str, key: str, mid: str) -> Path:
    """A draft: a `:2,D` file in cur/, as the keeper writes one."""
    deliver(root, box, key, mid=mid, new=False)
    path = root / box / "cur" / f"{key}.M1P1.host:2,S"
    return path.rename(path.with_name(f"{key}.M1P1.host:2,D"))


# --- store mode: the fallback ----------------------------------------------


@pytest.mark.parametrize("box", [SESSION, "alpha"], ids=["session-box", "base-box"])
def test_a_message_in_an_own_box_is_marked_when_the_index_is_empty(store, box):
    deliver(store.mail, box, "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} done alpha {SESSION}" in store.marks
    assert f"--config={store.profile}" in store.log.read_text(), "the store index was asked first"


def test_a_message_only_in_a_foreign_box_is_still_no_such_message(store):
    deliver(store.mail, "gamma", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert "no such message" in r.stderr
    assert store.marks == ""


def test_an_index_hit_is_marked_without_the_fallback(store):
    """The index answers: nothing changes, even for mail in no own box."""
    store.payload.write_text(json.dumps(forest(message(ID, 1, "b@x.invalid", "hi"))))
    r = mark(ID, "seen")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{ID} seen" in store.marks


def test_a_refused_index_says_so_and_the_own_box_still_answers(store, monkeypatch):
    """No profile: the refusal line names it, then the session box answers."""
    monkeypatch.setenv(_index.PROFILE_VAR, "nosuch")
    _index.clear_cache()
    deliver(store.mail, SESSION, "1923171201", mid=MINE)
    r = mark(MINE, "read")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert "no notmuch profile for this seat" in r.stderr and "'nosuch'" in r.stderr
    assert _index.REFUSES_HOST in r.stderr
    assert "no such message" not in r.stderr
    assert f"{MINE} read" in store.marks
    assert not store.log.exists(), "a refusal runs no notmuch"


def test_a_refused_index_and_a_foreign_id_say_both(store, monkeypatch):
    monkeypatch.setenv(_index.PROFILE_VAR, "nosuch")
    _index.clear_cache()
    deliver(store.mail, "gamma", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert "no notmuch profile" in r.stderr
    # SM-714: after a refusal the index never answered, so the miss is not
    # "no such message" (/ASKS 1410.2); it names where it looked instead.
    assert "no such message" not in r.stderr
    assert f"not marked: {MINE}" in r.stderr
    assert f"not in this seat's own boxes ({SESSION}, alpha" in r.stderr
    assert store.marks == ""


def test_an_index_that_fails_in_store_mode_still_falls_back(store, monkeypatch):
    """A notmuch error under the resolved profile is CouldNotLook too."""
    monkeypatch.setenv("FAKE_FAIL", "1")
    deliver(store.mail, "alpha", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert "could not check the index" in r.stderr
    assert f"{MINE} done" in store.marks


def test_many_ids_one_own_one_foreign(store):
    other = "theirs@example.invalid"
    deliver(store.mail, SESSION, "1923171201", mid=MINE)
    deliver(store.mail, "gamma", "1923171202", mid=other)
    r = mark(MINE, other, "done")
    assert r.exit_code == EX_REFUSED
    assert f"{MINE} done" in store.marks and other not in store.marks
    assert f"no such message: {other}" in r.stderr


def test_an_index_number_resolves_and_then_falls_back(store):
    """`mark 1 done`: the number names the session box's row (the doorbell's
    box), and the fallback finds it there."""
    deliver(store.mail, SESSION, "1923171201", mid=MINE)
    r = mark("1", "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} done" in store.marks


# --- the draft check, on own-box files (3.28) ------------------------------


def test_a_draft_alone_in_an_own_box_is_refused(store):
    draft(store.mail, SESSION, "1923171201", MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert "is a draft" in r.stderr
    assert store.marks == ""


@pytest.mark.parametrize("draft_key,mail_key", [("1923171201", "1923171202"),
                                                 ("1923171202", "1923171201")],
                         ids=["draft-sorts-first", "draft-sorts-last"])
@pytest.mark.parametrize("mail_in", ["new", "cur"])
def test_a_finished_copy_beside_its_draft_is_marked_whichever_is_found_first(
        store, draft_key, mail_key, mail_in):
    """The pin design §3 asks of do-005: `find_messages` keeps ONE file per
    id, so a draft found first must not decide. Either name order, the
    finished copy in new/ or in the same cur/: marked."""
    draft(store.mail, SESSION, draft_key, MINE)
    deliver(store.mail, SESSION, mail_key, mid=MINE, new=(mail_in == "new"))
    r = mark(MINE, "seen")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} seen" in store.marks


def test_a_draft_in_one_own_box_and_the_mail_in_the_other_is_marked(store):
    draft(store.mail, SESSION, "1923171201", MINE)
    deliver(store.mail, "alpha", "1923171202", mid=MINE)
    assert mark(MINE, "done").exit_code == EX_OK
    assert f"{MINE} done" in store.marks


# --- the walk stays inside the two boxes -----------------------------------


def test_a_box_nested_inside_the_base_box_is_not_searched(store):
    """A store-level seat's box IS the store directory, and its project boxes
    nest inside it (measured on a host at 12:34: a store holds six). A
    walk from the box would read them all; the fallback reads only the box's
    own new/ and cur/."""
    deliver(store.mail, "alpha/project", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert "no such message" in r.stderr
    assert store.marks == ""


def test_a_missing_session_box_leaves_the_base_box_to_answer(store):
    shutil.rmtree(store.mail / SESSION)
    deliver(store.mail, "alpha", "1923171201", mid=MINE)
    assert mark(MINE, "done").exit_code == EX_OK
    assert f"{MINE} done" in store.marks


# --- host mode: the same fallback, the same two boxes (SM-714) -------------
#
# SM-714 fix-001 changed `test_host_mode_has_no_fallback` (SM-731 §10's pin)
# into the test below: the pin asserted the bug.


def _host(monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv(_index.VAR)
    else:
        monkeypatch.setenv(_index.VAR, mode)
    _index.clear_cache()


HOST_MODES = pytest.mark.parametrize("mode", [None, "host", ""], ids=["unset", "host", "empty"])


@HOST_MODES
@pytest.mark.parametrize("box", [SESSION, "alpha"], ids=["session-box", "base-box"])
def test_host_mode_falls_back_to_the_own_boxes(store, monkeypatch, mode, box):
    _host(monkeypatch, mode)
    deliver(store.mail, box, "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} done alpha {SESSION}" in store.marks
    log = store.log.read_text()
    assert f"--config={_index.profile_config()}\x1f" in log, \
        "host mode asks the host's sessionkit profile, never a store's"
    assert f"id:{MINE}" in log, "the index was asked first"


@HOST_MODES
def test_host_mode_a_foreign_box_is_still_no_such_message(store, monkeypatch, mode):
    """The scope: never another seat's box."""
    _host(monkeypatch, mode)
    deliver(store.mail, "gamma", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert f"no such message: {MINE} - not in the index (notmuch)" in r.stderr
    assert f"not in this seat's own boxes ({SESSION}, alpha: new/ or cur/)" in r.stderr
    assert store.marks == ""


def test_host_mode_an_index_listed_in_a_foreign_box_carries_no_file(store, monkeypatch):
    """`mark gamma 1 done`: the index resolves in gamma, which is not an own
    box, so the file it found is not marked from disk, and the refusal says
    why."""
    _host(monkeypatch, None)
    deliver(store.mail, "gamma", "1923171201", mid=MINE)
    r = mark("gamma", "1", "done")
    assert r.exit_code == EX_REFUSED
    assert f"no such message: {MINE}" in r.stderr
    assert "1 was listed in gamma, which is not this seat's own box" in r.stderr
    assert store.marks == ""


def test_host_mode_a_nested_box_is_not_searched(store, monkeypatch):
    _host(monkeypatch, None)
    deliver(store.mail, "alpha/project", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert "no such message" in r.stderr
    assert store.marks == ""


def test_host_mode_an_unreadable_index_says_so_and_the_own_box_answers(store, monkeypatch):
    """SM-714 (5): CouldNotLook falls back too, and the refusal is still
    said in words first (/ASKS 1410.2)."""
    _host(monkeypatch, None)
    monkeypatch.setenv("FAKE_FAIL", "1")
    deliver(store.mail, SESSION, "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"could not check the index for {MINE}" in r.stderr
    assert "looking in this seat's own boxes" in r.stderr
    assert "no such message" not in r.stderr
    assert f"{MINE} done" in store.marks


def test_host_mode_an_unreadable_index_and_a_foreign_id_is_not_no_such_message(
        store, monkeypatch):
    _host(monkeypatch, None)
    monkeypatch.setenv("FAKE_FAIL", "1")
    deliver(store.mail, "gamma", "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_REFUSED
    assert "could not check the index" in r.stderr
    assert "no such message" not in r.stderr
    assert f"not marked: {MINE} - the index could not be read" in r.stderr
    assert store.marks == ""


def test_a_bad_mode_value_is_not_store_mode(store, monkeypatch):
    """`SESSIONMAIL_INDEX=stores` is refused by the resolver; it is not store
    mode, so it never asks a profile. The refusal is said, naming the value,
    and the own boxes answer (SM-714: both modes fall back)."""
    monkeypatch.setenv(_index.VAR, "stores")
    _index.clear_cache()
    deliver(store.mail, SESSION, "1923171201", mid=MINE)
    r = mark(MINE, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert "could not check the index" in r.stderr and "'stores'" in r.stderr
    assert f"{MINE} done" in store.marks


# --- the index answers with a draft only (review, do-005) ------------------


def _index_holds(store, *paths: Path) -> None:
    """The fake store index answers MINE with exactly PATHS as its files."""
    m = message(MINE, 1, "ears@example.invalid", "a turn")
    m["filename"] = [str(p) for p in paths]
    store.payload.write_text(json.dumps(forest(m)))


def test_a_draft_only_index_answer_does_not_hide_the_finished_copy_in_an_own_box(store):
    """The ears-box draft is in the store index; the finished turn, same id,
    was delivered to the seat's session box, which the store index does not
    hold. 3.28 refuses only when EVERY file under the id is a draft, and host
    mode (one index over both) marks it, so store mode must too."""
    held = draft(store.mail, "gamma", "1923171201", MINE)
    _index_holds(store, held)
    deliver(store.mail, SESSION, "1923171202", mid=MINE)
    r = mark(MINE, "seen")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} seen" in store.marks


def test_a_draft_only_index_answer_and_only_drafts_in_own_boxes_is_still_a_draft(store):
    held = draft(store.mail, "gamma", "1923171201", MINE)
    _index_holds(store, held)
    draft(store.mail, SESSION, "1923171202", MINE)
    r = mark(MINE, "seen")
    assert r.exit_code == EX_REFUSED
    assert "is a draft" in r.stderr
    assert store.marks == ""


def test_a_draft_only_index_answer_and_nothing_in_own_boxes_is_still_a_draft(store):
    held = draft(store.mail, "gamma", "1923171201", MINE)
    _index_holds(store, held)
    r = mark(MINE, "seen")
    assert r.exit_code == EX_REFUSED
    assert "is a draft" in r.stderr and "no such message" not in r.stderr
    assert store.marks == ""


def test_host_mode_a_draft_only_index_answer_does_not_hide_the_finished_copy(
        store, monkeypatch):
    """SM-714 (3): a host index that holds only the draft has not yet
    reached the finished copy delivered to the session box; 3.28 judges the
    on-disk files too, so the finished copy is marked."""
    _host(monkeypatch, None)
    held = draft(store.mail, "gamma", "1923171201", MINE)
    _index_holds(store, held)
    deliver(store.mail, SESSION, "1923171202", mid=MINE)
    r = mark(MINE, "seen")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} seen" in store.marks


def test_host_mode_a_draft_only_index_answer_and_only_a_draft_on_disk_is_refused(
        store, monkeypatch):
    _host(monkeypatch, None)
    held = draft(store.mail, "gamma", "1923171201", MINE)
    _index_holds(store, held)
    draft(store.mail, SESSION, "1923171202", MINE)
    r = mark(MINE, "seen")
    assert r.exit_code == EX_REFUSED
    assert "is a draft" in r.stderr
    assert store.marks == ""


@pytest.mark.parametrize("files", ["finished", "draft-and-finished"])
def test_an_index_answer_with_a_finished_file_never_walks_the_own_boxes(store, monkeypatch, files):
    """Only an answer that is empty, refused, or draft-ONLY falls back: one
    finished file under the id decides (3.28), so the own-box walk, which
    reads every header in both boxes on a miss, is not paid for."""
    from sessionmail import _sender

    def no_walk(*_a, **_k):
        raise AssertionError("the own boxes were walked")

    monkeypatch.setattr(_sender, "find_messages", no_walk)
    finished = store.mail / "gamma" / "new" / "1923171202.M1P1.host"
    paths = [finished] if files == "finished" else \
        [draft(store.mail, "gamma", "1923171201", MINE), finished]
    _index_holds(store, *paths)
    r = mark(MINE, "seen")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{MINE} seen" in store.marks
