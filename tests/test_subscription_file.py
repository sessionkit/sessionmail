"""The subscription file (changes/message-subscriptions, tasks 2.1; design D1, D4).

A subscription is ONE file. Its NAME is the box, with `/` stored as `--`
because a file name cannot hold `/`. Its body is only a note, and nothing
reads the body. Which folder it is in decides seat or session:

    <home>/seats/<seat>/subscriptions/<box>     durable
    <home>/sessions/<sid>/subscriptions/<box>   only while <sid> is live

The reader returns the set of (subscriber, box) pairs: every seat file, and
the files of the sessions the caller counts live. Everything here runs over
a synthetic root under `tmp_path`, never the real `~/.sessionmail`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from sessionmail import _subscription as sub

from _seatmap import seat_subscribe, session_subscribe

NOTE = "# added by test (message subscription add)\n# why: a fixture\n"


def _write(home: Path, kind: str, owner: str, name: str, body: str = NOTE) -> Path:
    """A subscription where it routes from. A SESSION's is a `true` key of
    its map (session-settings 3.3b; the path returned is the map), unless the
    body is a saved query, which has no map form and stays an old file."""
    if kind == "sessions" and "query:" not in body:
        return session_subscribe(owner, sub.box_name(name))
    folder = home / kind / owner / "subscriptions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body, encoding="utf-8")
    return path


# ── the name: '/' <-> '--' ──────────────────────────────────────────────

@pytest.mark.parametrize("box, stored", [
    ("atomnote", "atomnote"),
    ("work/demo", "work--demo"),
    ("acmecorp/delta-chat", "acmecorp--delta-chat"),
    ("voicemode+vm-2266", "voicemode+vm-2266"),          # the `+` as typed (D1)
    ("sessionmail+sm-122.foreman", "sessionmail+sm-122.foreman"),
    ("foreman.VM-1802", "foreman.VM-1802"),               # case kept: a real box
])
def test_a_box_is_stored_with_slash_as_double_dash_and_read_back(box, stored):
    assert sub.file_name(box) == stored
    assert sub.box_name(stored) == box


@pytest.mark.parametrize("box", [
    "atomnote", "work/demo", "a/b/c", "voicemode+vm-2266",
    "delta-chat", "a-b/c-d", "x.y/z_w",
])
def test_the_name_round_trips_both_ways(box):
    assert sub.box_name(sub.file_name(box)) == box
    stored = sub.file_name(box)
    assert sub.file_name(sub.box_name(stored)) == stored


@pytest.mark.parametrize("box, why", [
    ("", "empty"),
    ("foo--bar", "--"),         # would read back as foo/bar
    ("a-/b", "-"),              # a---b reads back as a/-b
    ("a/-b", "-"),
    ("-lead", "-"),
    ("trail-", "-"),
    ("/abs", "empty"),
    ("a//b", "empty"),
    ("a/", "empty"),
    (".hidden", "."),
    ("a/..", "."),
    ("has space", "space"),
    ("tab\there", "space"),
])
def test_a_box_that_cannot_round_trip_is_refused_with_a_reason(box, why):
    with pytest.raises(sub.BadBox) as exc:
        sub.file_name(box)
    assert why in str(exc.value)


@pytest.mark.parametrize("stored", [
    "--help",        # a real accidental box on hq; not a subscription name
    "a----b",
    "a---b",
    "trail--",
    ".tmp.atomnote.123",
    "",
])
def test_a_file_name_that_is_not_a_stored_box_is_refused(stored):
    with pytest.raises(sub.BadBox):
        sub.box_name(stored)


# ── the reader ──────────────────────────────────────────────────────────

def test_the_reader_returns_seat_and_live_session_pairs(tmp_path):
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    _write(home, "seats", "ivan", "work--demo")
    seat_subscribe("sessionmail+sm-122.foreman", "voicemode+kaizen")   # a SEAT: its map (3.2)
    live = "55c9ceda-0000-4000-8000-000000000001"
    dead = "deadbeef-0000-4000-8000-000000000002"
    _write(home, "sessions", live, "voicemode+vm-2266")
    _write(home, "sessions", live, "kith")
    _write(home, "sessions", dead, "atomnote")

    assert sub.pairs(home, live={live}) == {
        ("ivan", "atomnote"),
        ("ivan", "work/demo"),
        ("sessionmail+sm-122.foreman", "voicemode+kaizen"),
        (live, "voicemode+vm-2266"),
        (live, "kith"),
    }


def test_a_dead_sessions_subscriptions_are_not_read(tmp_path):
    home = tmp_path / "sessionmail"
    dead = "deadbeef-0000-4000-8000-000000000002"
    _write(home, "sessions", dead, "atomnote")
    assert sub.pairs(home, live=set()) == set()
    assert sub.pairs(home, live={dead}) == {(dead, "atomnote")}


def test_two_subscribers_of_one_box_are_two_pairs(tmp_path):
    home = tmp_path / "sessionmail"
    sid = "55c9ceda-0000-4000-8000-000000000001"
    _write(home, "seats", "ivan", "atomnote")
    _write(home, "seats", "judy", "atomnote")
    _write(home, "sessions", sid, "atomnote")
    assert sub.pairs(home, live={sid}) == {
        ("ivan", "atomnote"), ("judy", "atomnote"), (sid, "atomnote")}


def test_the_body_is_only_a_note_and_is_never_read(tmp_path):
    """D1: 'No query, route or policy. The generator reads only the name.'
    A route or policy line in a body changes nothing. (This body once also
    carried `query:`. The flag-4 ruling made a `query:` body an old saved
    query, which is SKIPPED: see
    test_an_old_saved_query_file_is_skipped_with_its_reason_not_read_as_a_box.)"""
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote", body="route: pager\npolicy: 3\n")
    _write(home, "seats", "ivan", "kith", body="")
    assert sub.pairs(home, live=set()) == {("ivan", "atomnote"), ("ivan", "kith")}


def test_entries_say_seat_or_session_by_folder_and_keep_the_path(tmp_path):
    home = tmp_path / "sessionmail"
    sid = "55c9ceda-0000-4000-8000-000000000001"
    seat_file = _write(home, "seats", "ivan", "work--demo")
    sess_file = _write(home, "sessions", sid, "atomnote")
    scan = sub.scan(home, live={sid})
    got = {(e.kind, e.subscriber, e.box, e.path) for e in scan.entries}
    assert got == {
        ("seat", "ivan", "work/demo", seat_file),
        ("session", sid, "atomnote", sess_file),
    }
    assert scan.skipped == ()


def test_folders_hidden_files_and_bad_names_are_skipped_and_counted(tmp_path):
    """Nothing is skipped quietly: each skip comes back with its reason, so
    the generator can count it on stderr (tasks 2.4's treatment)."""
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    folder = home / "seats" / "ivan" / "subscriptions"
    (folder / "retired-2030-09-29").mkdir()             # tasks 6.3's dated folder
    (folder / "retired-2030-09-29" / "old-query").write_text("query: x\n")
    (folder / ".atomnote.tmp.123").write_text(NOTE)     # an atomic write in flight
    (folder / "a----b").write_text(NOTE)                # not a stored box
    scan = sub.scan(home, live=set())
    assert {(e.subscriber, e.box) for e in scan.entries} == {("ivan", "atomnote")}
    skipped = {p.name: why for p, why in scan.skipped}
    assert set(skipped) == {"retired-2030-09-29", "a----b"}
    assert "folder" in skipped["retired-2030-09-29"]
    assert "--" in skipped["a----b"]


def test_a_missing_home_or_folder_reads_as_no_subscriptions(tmp_path):
    home = tmp_path / "nowhere"
    assert sub.pairs(home, live={"x"}) == set()
    (tmp_path / "bare" / "seats" / "ivan").mkdir(parents=True)   # no subscriptions/
    (tmp_path / "bare" / "sessions" / "sid1").mkdir(parents=True)
    assert sub.pairs(tmp_path / "bare", live={"sid1"}) == set()


def test_the_reader_writes_nothing(tmp_path):
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    before = sorted(p for p in tmp_path.rglob("*"))
    sub.pairs(home, live={"sid1"})
    sub.pairs(tmp_path / "absent", live={"sid1"})
    assert sorted(p for p in tmp_path.rglob("*")) == before


def test_home_defaults_to_the_sessionmail_home(tmp_path, monkeypatch):
    home = tmp_path / "from-env"
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    _write(home, "seats", "ivan", "atomnote")
    assert sub.pairs(live=set()) == {("ivan", "atomnote")}


def test_a_written_name_is_read_back_as_the_same_pair(tmp_path):
    """The writer (the verbs, tasks 3.2) and the reader agree through the
    one codec: whatever file_name makes, scan reads back as the box."""
    home = tmp_path / "sessionmail"
    for box in ("work/demo", "voicemode+vm-2266", "a/b/c"):
        _write(home, "seats", "ivan", sub.file_name(box))
    assert sub.pairs(home, live=set()) == {
        ("ivan", "work/demo"), ("ivan", "voicemode+vm-2266"), ("ivan", "a/b/c")}


# ── review fixes (reviewer aa9f338f): the two edges the contract promised ──

def test_an_unreadable_folder_is_skipped_with_a_reason_not_raised(tmp_path):
    """The module promises it 'never raises over one bad file'. One seat's
    folder the reader cannot open must not abort the whole rebuild: it is
    skipped and counted, and every other seat is still read."""
    import os
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    locked = _write(home, "seats", "judy", "kith").parent
    os.chmod(locked, 0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running as a user who can read a mode-0 folder")
        scan = sub.scan(home, live=set())
    finally:
        os.chmod(locked, 0o755)
    assert scan.pairs == {("ivan", "atomnote")}
    assert [(p, "cannot" in why) for p, why in scan.skipped] == [(locked, True)]


@pytest.mark.parametrize("fn", [sub.scan, sub.pairs])
def test_live_as_one_bare_string_is_refused(tmp_path, fn):
    """A str is an Iterable[str] of its characters: `live=sid` would silently
    read no session at all. Refuse it loudly instead."""
    with pytest.raises(TypeError):
        fn(tmp_path, live="55c9ceda-0000-4000-8000-000000000001")


# ── review fixes (reviewer d177a9bb): the parent folders, and the reason ──

@pytest.mark.parametrize("parent", ["seats", "sessions"])
def test_an_unreadable_seats_or_sessions_folder_is_skipped_not_raised(tmp_path, parent):
    """96ce395 made one seat's folder skip, not raise; the folder ABOVE the
    seats (or the sessions) is the same promise one level up. An unreadable
    sessions/ must not throw away every seat subscription already read.
    (Since 3.3b a session's map is under ~/.session, not under the locked
    folder: it is read either way; the old sessions/ still holds folders.)"""
    import os
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    _write(home, "sessions", "sid1", "kith")
    (home / "sessions" / "sid1").mkdir(parents=True)      # an old folder, as 3.3a leaves one
    locked = home / parent
    os.chmod(locked, 0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running as a user who can read a mode-0 folder")
        scan = sub.scan(home, live={"sid1"})
    finally:
        os.chmod(locked, 0o755)
    kept = {("sid1", "kith")} | ({("ivan", "atomnote")} if parent == "sessions" else set())
    assert scan.pairs == kept
    assert [(p, "cannot" in why) for p, why in scan.skipped] == [(locked, True)]


def test_a_dangling_link_is_skipped_as_not_a_file_not_as_a_folder(tmp_path):
    """The reason is what the generator counts on stderr; a dangling link
    (or a fifo) is not 'a folder', and saying so sends the reader looking
    for a directory that is not there."""
    home = tmp_path / "sessionmail"
    folder = _write(home, "seats", "ivan", "atomnote").parent
    (folder / "kith").symlink_to(tmp_path / "nowhere")
    scan = sub.scan(home, live=set())
    assert scan.pairs == {("ivan", "atomnote")}
    skipped = {p.name: why for p, why in scan.skipped}
    assert set(skipped) == {"kith"}
    assert "folder" not in skipped["kith"]
    assert "not a regular file" in skipped["kith"]


# ── re-submit (worker 197c12bd): a folder that lists but cannot be searched ──

def test_a_folder_that_lists_but_cannot_be_searched_is_skipped_not_raised(tmp_path):
    """Mode r-- (0o444) on one seat's subscriptions/: `iterdir` succeeds, but
    telling a file from a folder needs a stat through it, and that is denied.
    The promise is "never raises over one bad file", so the other seat's (and
    the live session's) pairs must survive, and the unread file is counted
    as unreadable, not as "not a regular file" (SM-877: the reader stats
    strictly, since from CPython 3.14 `Path.is_file()` says False there)."""
    import os
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    _write(home, "seats", "eve", "kith")
    _write(home, "sessions", "sid1", "seat-list")
    locked = home / "seats" / "ivan" / "subscriptions"
    os.chmod(locked, 0o444)
    try:
        if os.access(locked, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        scan = sub.scan(home, live={"sid1"})
    finally:
        os.chmod(locked, 0o755)
    assert scan.pairs == {("eve", "kith"), ("sid1", "seat-list")}
    assert [(p, "cannot" in why) for p, why in scan.skipped] == [
        (locked / "atomnote", True)]


@pytest.mark.parametrize("parent", ["seats", "sessions"])
def test_a_seats_or_sessions_folder_that_lists_but_cannot_be_searched_is_skipped(
        tmp_path, parent):
    """SM-877: mode r-- on seats/ (or sessions/) itself. It lists, so the
    owner folders are named, but telling each one is a folder needs a stat
    through it, and that is denied. The folder is skipped WITH its reason,
    as a mode-0 one is; it is never read as holding no owners at all, which
    is what `Path.is_dir()` answered there from CPython 3.14."""
    import os
    home = tmp_path / "sessionmail"
    _write(home, "seats", "ivan", "atomnote")
    _write(home, "sessions", "sid1", "kith")
    (home / "sessions" / "sid1").mkdir(parents=True)      # an old folder, as 3.3a leaves one
    locked = home / parent
    os.chmod(locked, 0o444)
    try:
        if os.access(locked, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        scan = sub.scan(home, live={"sid1"})
    finally:
        os.chmod(locked, 0o755)
    kept = {("sid1", "kith")} | ({("ivan", "atomnote")} if parent == "sessions" else set())
    assert scan.pairs == kept
    assert [(p, "cannot read the folder" in why) for p, why in scan.skipped] == [
        (locked, True)]


# ── flag 4: skip what the generator skips ────────────────────────────────

OLD_QUERY = (
    "# The seat's own address, born with the seat.\n"
    "query: to:$SEAT and not from:$SEAT@$HOST and date:1d..\n"
    "route: pager,ride-along\n"
)


def test_an_old_saved_query_file_is_skipped_with_its_reason_not_read_as_a_box(tmp_path):
    """The old saved-query files share the folder until tasks 6.3 (192 on hq).
    The generator never routes them; the reader must not list
    them either. A `query:` line counts with leading space; a comment that
    only mentions query: does not."""
    folder = tmp_path / "seats" / "ivan" / "subscriptions"
    folder.mkdir(parents=True)
    (folder / "seat").write_text(OLD_QUERY)
    (folder / "session").write_text("  query: to:$SEAT\n")
    (folder / "atomnote").write_text("# why: replaces the old query: file\n")
    scan = sub.scan(tmp_path, live=())
    assert scan.pairs == {("ivan", "atomnote")}
    skipped = {p.name: why for p, why in scan.skipped}
    assert set(skipped) == {"seat", "session"}
    assert all("saved query" in why for why in skipped.values())


def test_an_unreadable_file_is_skipped_since_it_could_be_a_saved_query(tmp_path):
    """In an AGENT's folder (since 3.3b a session's boxes are map keys)."""
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    folder = tmp_path / "seats" / "ivan" / "subscriptions"
    folder.mkdir(parents=True)
    locked = folder / "kith"
    locked.write_text("")
    locked.chmod(0o000)
    (folder / "atomnote").write_text("")
    try:
        scan = sub.scan(tmp_path, live=())
    finally:
        locked.chmod(0o644)
    assert scan.pairs == {("ivan", "atomnote")}
    assert [(p, "cannot read the file" in why) for p, why in scan.skipped] == [(locked, True)]
