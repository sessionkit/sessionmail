"""The readers of the `seats` and `sessions` leaves follow the ONE state
resolver (`_settings.state_path`), so they read where the writers write.

- `_seat.sessions_root` / `old_session_root`: the ONE function for an old
  session folder. The listen mover renames OUT of the folder it resolved,
  `_reach.unmoved` names it, and a receipt's `old` place is it.
- `_seat._stamped_elsewhere` globs an old `listening` stamp there.
- `_subscription.roots` / `scan`: both leaves, each resolved on its own,
  unless `home=` is given (honoured as-is).
- `_migrate.migrate` / `convert_session`: read the resolved leaf, and remove
  each converted file IN the folder it was read from, never in the other root.
- `_reach.graph`: a skipped path is owned under EITHER the resolved leaf or
  the old root.
- `message._list_whose`: its session-folder probe is the resolved leaf.

Every case of test_state_resolver's `CASES` runs (old and new both real,
old linked to new, new only, old only, neither, the override, a state folder
made for another leaf), in a tmp HOME: no test here reads or writes the real
`~/.sessionmail` or `~/.sessionkit`. Each case also lays a DECOY in the root
the resolver did not choose, and asserts nothing there is read or removed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import json

import _leaves
from sessionmail import _reach, _seat, _settings, _subscription as sub, message
from sessionmail import _migrate
from test_state_resolver import CASES, Host, at, host, lay  # noqa: F401

SID = "5e55-0000-4000-8000-000000000969"
OTHER = "5e55-0000-4000-8000-000000000970"
SEAT = "sessionmail+sm-969.worker"
NOTE = "# added by worker 2030-06-09 (message subscription add)\n"


@pytest.fixture(autouse=True)
def _pinned(request):
    """`host` makes its own HOME: pin sessiontools' leaves for it before the
    test runs, so the seat and session maps read below are
    where sessiontools said they are, not where the code under test wrote."""
    if "host" in request.fixturenames:
        _leaves.pin(request.getfixturevalue("host").home)


def seat_map_path(seat: str) -> Path:
    """SEAT's mail layer under sessiontools' pinned seats leaf."""
    return _leaves.seat_settings(seat)


def session_values(sid: str) -> dict:
    """SID's `mail.json` as written, under sessiontools' pinned session
    dir; {} when absent."""
    p = _leaves.session_settings(sid)
    return json.loads(p.read_text()) if p.is_file() else {}


def chosen(host: Host, case: str, leaf: str) -> Path:
    """Where CASE resolves LEAF to, as the resolver's own pins say."""
    return host.expected(case, leaf)


def decoy_root(host: Host, case: str, leaf: str) -> Path | None:
    """The root the resolver did NOT choose, when it is a real, separate
    folder a reader could wrongly read: new when old wins over it (both,
    stranded), old under the override. None otherwise: linked is one
    folder, and in "new" a decoy written at old would MAKE old real, and so
    the winner (rule 2), so there is no other root to lay one in."""
    if case == "both":
        return host.new(leaf)
    if case == "override":
        return host.old(leaf)
    return None


def write(p: Path, body: str = NOTE) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def laid(host: Host, case: str, leaf: str, monkeypatch) -> Path:
    """CASE's tree for LEAF, the override set when it is CASE; returns the
    folder the readers must read."""
    lay(host.new_root, host.old_root, case, leaf)
    at(host, case, monkeypatch)
    return chosen(host, case, leaf)


# ── the ONE function for an old session folder ───────────────────────────

@pytest.mark.parametrize("case", CASES)
def test_old_session_root_is_the_sessions_leaf(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    assert _seat.sessions_root() == _settings.state_path("sessions") == where
    assert _seat.old_session_root(SID) == where / SID
    assert sub.roots()[1] == where                  # writer (mover) == reader (scan)


@pytest.mark.parametrize("case", CASES)
def test_the_mover_renames_out_of_the_folder_it_read(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    write(where / SID / "marks", "a mark\n")
    decoy = decoy_root(host, case, "sessions")
    if decoy is not None:
        write(decoy / SID / "marks", "a decoy\n")
    _seat.move_session_state(SID)
    new = _seat.session_root(SID)
    assert (new / "marks").read_text() == "a mark\n"
    assert (where / SID).is_symlink() and (where / SID).resolve() == new.resolve()
    if decoy is not None:                           # the other root is never touched
        assert (decoy / SID / "marks").read_text() == "a decoy\n"
        assert not (decoy / SID).is_symlink()


@pytest.mark.parametrize("case", CASES)
def test_unmoved_names_the_resolved_old_folder(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    write(where / SID / "marks", "a mark\n")
    assert _reach.unmoved(SID) == f"not moved; at {where / SID}"


@pytest.mark.parametrize("case", CASES)
def test_a_receipt_looks_at_the_resolved_old_folder(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    got = message._receipt_answer("<x@example.org>", SID)
    assert got["looked_at"]["old"] == str(where / SID)


@pytest.mark.parametrize("case", CASES)
def test_an_old_listening_stamp_is_read_in_the_resolved_folder(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    me, decoy_pid = os.getpid(), os.getppid()      # two pids that are alive
    write(where / OTHER / _seat.STAMPS_DIR / _seat.LISTENING_STAMP, f"{me}\n")
    decoy = decoy_root(host, case, "sessions")
    if decoy is not None:
        write(decoy / "decoy-sid" / _seat.STAMPS_DIR / _seat.LISTENING_STAMP, f"{decoy_pid}\n")
    got = _seat._stamped_elsewhere(SID)
    assert got.get(me) == OTHER
    assert decoy_pid not in got


def test_no_old_stamp_anywhere_finds_nothing(host):
    assert _seat._stamped_elsewhere(SID) == {}


# ── _subscription.scan: both leaves, each on its own ─────────────────────

@pytest.mark.parametrize("leaf", ["seats", "sessions"])
@pytest.mark.parametrize("case", CASES)
def test_roots_resolve_each_leaf_on_its_own(host, monkeypatch, case, leaf):
    where = laid(host, case, leaf, monkeypatch)
    other = "sessions" if leaf == "seats" else "seats"
    seats, sessions = sub.roots()
    assert (seats if leaf == "seats" else sessions) == where
    # The leaf NOT laid resolves alone: the fresh default (new), or the override.
    expect_other = (host.override / other if case == "override" else host.new(other))
    assert (sessions if leaf == "seats" else seats) == expect_other


@pytest.mark.parametrize("case", CASES)
def test_an_explicit_home_is_honoured_as_is_by_roots(host, tmp_path, monkeypatch, case):
    laid(host, case, "sessions", monkeypatch)
    given = tmp_path / "given"
    assert sub.roots(given) == (given / "seats", given / "sessions")


@pytest.mark.parametrize("case", CASES)
def test_scan_reads_an_agents_old_folder_in_the_resolved_seats_leaf(host, monkeypatch, case):
    where = laid(host, case, "seats", monkeypatch)
    write(where / "worker" / sub.FOLDER / "atomnote")
    decoy = decoy_root(host, case, "seats")
    if decoy is not None:
        write(decoy / "reviewer" / sub.FOLDER / "kaizen")
    got = sub.scan(live=set())
    assert {(e.subscriber, e.box) for e in got.entries} == {("worker", "atomnote")}


@pytest.mark.parametrize("case", CASES)
def test_scan_names_a_live_sessions_old_file_in_the_resolved_sessions_leaf(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    f = write(where / SID / sub.FOLDER / "atomnote")
    decoy = decoy_root(host, case, "sessions")
    if decoy is not None:
        write(decoy / SID / sub.FOLDER / "kaizen")
    got = sub.scan(live={SID})
    assert [p for p, why in got.skipped if why == sub.NOT_CONVERTED_SESSION] == [f]


def test_scan_with_neither_leaf_reads_nothing(host):
    got = sub.scan(live={SID})
    assert got.entries == () and got.skipped == ()


# ── _migrate: read the resolved leaf, remove IN the folder read ──────────

@pytest.mark.parametrize("case", CASES)
def test_migrate_moves_a_seats_box_file_and_removes_it_in_the_folder_it_read(host, monkeypatch, case):
    where = laid(host, case, "seats", monkeypatch)
    f = write(where / SEAT / sub.FOLDER / "atomnote")
    decoy = decoy_root(host, case, "seats")
    g = write(decoy / SEAT / sub.FOLDER / "kaizen") if decoy is not None else None
    r = _migrate.migrate(live=set())
    assert [(s, b) for s, b, _ in r.moved] == [(SEAT, "atomnote")]
    assert [p for _, _, p in r.moved] == [f]
    assert not f.exists()
    assert json.loads(seat_map_path(SEAT).read_text())["subscriptions"] == {"atomnote": True}
    if g is not None:
        assert g.read_text() == NOTE                # never read, never removed


@pytest.mark.parametrize("case", CASES)
def test_convert_session_removes_only_in_the_folder_it_read(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    f = write(where / SID / sub.FOLDER / "atomnote")
    a = write(where / SID / "address", "SessionMail+SM-969.Worker\n")
    decoy = decoy_root(host, case, "sessions")
    if decoy is not None:
        g = write(decoy / SID / sub.FOLDER / "kaizen")
        b = write(decoy / SID / "address", "a+decoy\n")
    r = _migrate.Report(dry_run=False)
    _migrate.convert_session(SID, r)
    assert not r.errors
    assert not f.exists() and not a.exists()
    assert session_values(SID) == {"subscriptions": {"atomnote": True},
                                   "address": "sessionmail+sm-969.worker"}
    if decoy is not None:
        assert g.read_text() == NOTE and b.read_text() == "a+decoy\n"


@pytest.mark.parametrize("case", CASES)
def test_migrate_passes_its_resolution_to_convert_session(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    f = write(where / SID / sub.FOLDER / "atomnote")
    r = _migrate.migrate(live={SID})
    assert r.sessions == 1 and [p for _, _, p in r.session_moved] == [f]
    assert not f.exists()


def test_convert_session_with_an_explicit_home_reads_only_there(host, tmp_path):
    lay(host.new_root, host.old_root, "both", "sessions")
    given = tmp_path / "given"
    f = write(given / "sessions" / SID / sub.FOLDER / "atomnote")
    g = write(host.old("sessions") / SID / sub.FOLDER / "kaizen")
    h = write(host.new("sessions") / SID / sub.FOLDER / "notes")
    r = _migrate.Report(dry_run=False)
    _migrate.convert_session(SID, r, home=given)
    assert not f.exists() and g.is_file() and h.is_file()
    assert session_values(SID)["subscriptions"] == {"atomnote": True}


# ── _reach: a skipped path is owned under either root ────────────────────

@pytest.mark.parametrize("case", CASES)
def test_graph_counts_a_dropped_old_seat_file_where_it_was_read(host, tmp_path, monkeypatch, case):
    where = laid(host, case, "seats", monkeypatch)
    write(where / SEAT / sub.FOLDER / "atomnote")   # a seat's unconverted file: skipped
    root = tmp_path / "agents"
    root.mkdir()
    g = _reach.graph(root, live=set())
    assert g.dropped == {SEAT: 1}


def test_owner_in_reads_the_owner_under_a_leaf():
    leaf = Path("/x/.sessionkit/mail/state/seats")
    assert _reach._owner_in(leaf / "A+B" / "subscriptions" / "box", leaf) == "a+b"
    assert _reach._owner_in(leaf / "lonely", leaf) == ""
    assert _reach._owner_in(Path("/elsewhere/seats/a+b/x"), leaf) == ""


def test_a_path_under_the_old_root_is_owned_when_the_leaf_resolved_to_new(host, tmp_path, monkeypatch):
    """The old root still attributes (old OR resolved): a skipped path a
    reader met under `~/.sessionmail/` is counted against its owner."""
    lay(host.new_root, host.old_root, "new", "seats")
    old_file = host.old("sessions") / SID / sub.FOLDER / "atomnote"
    assert _reach._owner_of(old_file, _seat.home()) == SID.lower()
    assert _reach._owner_in(old_file, sub.roots()[0]) == ""     # not under new seats


# ── message._list_whose ──────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES)
def test_list_whose_finds_a_session_folder_in_the_resolved_leaf(host, monkeypatch, case):
    where = laid(host, case, "sessions", monkeypatch)
    (where / "oldsess").mkdir(parents=True)
    assert message._list_whose("oldsess")[0] == "oldsess"
    decoy = decoy_root(host, case, "sessions")
    if decoy is not None:
        (decoy / "decoysess").mkdir(parents=True)
        assert message._list_whose("decoysess") == ("", "decoysess")


def test_list_whose_with_no_sessions_leaf_is_a_seat(host):
    assert message._list_whose("oldsess") == ("", "oldsess")
