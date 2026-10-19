"""message box list|path (openspec/changes/message-subscriptions, tasks 3.1,
design D6, specs/message "Boxes are listed by kind, and session boxes only on
request").

Every test runs over a synthetic root (`SESSION_MAIL_ROOT`) and a synthetic
sessionmail home (conftest's `SESSIONMAIL_HOME`); live sessions come from a
stub, never from this host's harness. The live `~/.mail` is never read: the
`root` fixture points the root at tmp, and `test_the_live_mail_root_is_never
_read` proves a listing reads only the root it was given."""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat, _tokens, message
from sessionmail.message import EX_REFUSED, cli

HOST = socket.gethostname().split(".")[0].lower()
SID = "5a1d0c3e-1111-2222-3333-444455556666"
DEAD = "dead0000-1111-2222-3333-444455556666"


def mkbox(d: Path, mtime: float | None = None) -> Path:
    for s in ("new", "cur", "tmp"):
        (d / s).mkdir(parents=True, exist_ok=True)
    if mtime is not None:
        for s in ("new", "cur"):
            os.utime(d / s, (mtime, mtime))
    return d


def subscribe(home: Path, folder: str, stored: str) -> None:
    """A subscription where it routes from: a SESSION's is a key of its map
    (session-settings 3.3b); an agent's, a file in its folder."""
    if folder.startswith("sessions/"):
        from _seatmap import session_subscribe
        from sessionmail import _subscription
        session_subscribe(folder.split("/", 1)[1], _subscription.box_name(stored))
        return
    f = home / folder / "subscriptions" / stored
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("added by a test\n")


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "mail"
    r.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(r))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)     # no postfix here
    monkeypatch.setattr(message, "_live_sessions", lambda: {SID})
    return r


@pytest.fixture
def home() -> Path:
    return _seat.home()          # conftest's hermetic SESSIONMAIL_HOME


def box_list(*args):
    return CliRunner().invoke(cli, ["box", "list", *args])


def rows(out: str) -> list[str]:
    return [line for line in out.splitlines() if not line.startswith("-- ")]


def footer(out: str) -> str:
    last = out.rstrip("\n").splitlines()[-1]
    assert last.startswith("-- "), out
    return last


# ── list: kinds, and the sessions only on request ──────────────────────────

@pytest.fixture
def fleet(root):
    """Seats, a store with two projects (one a stray folder), a store box,
    session boxes in both shapes, a stray folder and a hidden one."""
    mkbox(root / "ivan", 100)
    mkbox(root / "atomnote", 200)
    (root / "acmecorp").mkdir()
    (root / "acmecorp" / ".tm-store").write_text("")
    mkbox(root / "acmecorp", 300)                       # the store box itself
    mkbox(root / "acmecorp" / "sessionmail", 400)
    (root / "acmecorp" / "notaproject").mkdir()         # no maildir: no box
    mkbox(root / SID, 500)
    mkbox(root / "0477eedf", 600)                       # the 8-hex short form
    (root / "stray").mkdir()                            # no maildir: no box
    mkbox(root / ".notmuch", 700)                       # hidden: never a box
    return root


def test_a_bare_listing_hides_the_sessions_and_says_how_many_and_how(fleet):
    r = box_list()
    assert r.exit_code == 0, r.output
    shown = rows(r.stdout)
    assert not any(SID in line or "0477eedf" in line for line in shown), r.stdout
    last = footer(r.stdout)
    assert "2 session boxes hidden" in last and "--all" in last, last


def test_each_kind_is_named_and_a_folder_that_is_no_maildir_is_not_a_box(fleet):
    r = box_list()
    got = {tuple(line.split()[:2]) for line in rows(r.stdout)}
    assert got == {("seat", "ivan"), ("seat", "atomnote"), ("store", "acmecorp"),
                   ("project", "acmecorp/sessionmail")}, r.stdout


def test_all_shows_the_session_boxes_too(fleet):
    r = box_list("--all")
    assert r.exit_code == 0, r.output
    got = {tuple(line.split()[:2]) for line in rows(r.stdout)}
    assert ("session", SID) in got and ("session", "0477eedf") in got
    assert "session boxes hidden" not in footer(r.stdout)


def test_the_newest_come_first(fleet):
    names = [line.split()[1] for line in rows(box_list().stdout)]
    assert names == ["acmecorp/sessionmail", "acmecorp", "atomnote", "ivan"]


def test_a_pattern_is_a_substring_or_a_glob(fleet):
    assert [l.split()[1] for l in rows(box_list("mail").stdout)] == \
        ["acmecorp/sessionmail"]
    assert {l.split()[1] for l in rows(box_list("acme*").stdout)} == \
        {"acmecorp", "acmecorp/sessionmail"}
    assert {l.split()[1] for l in rows(box_list("I*").stdout)} == {"ivan"}


# ── list: each row names its subscribers ───────────────────────────────────

def test_each_row_names_who_else_receives_its_mail(fleet, home):
    subscribe(home, "seats/ivan", "atomnote")                 # a seat, whole box
    subscribe(home, f"sessions/{SID}", "atomnote")            # a live session
    subscribe(home, f"sessions/{DEAD}", "atomnote")           # a DEAD session: no
    subscribe(home, "seats/eve", "sessionmail")               # a project, by name
    subscribe(home, "seats/ivan", "acmecorp--sessionmail")    # ... and by store/project
    subscribe(home, "seats/ivan", "ivan+kaizen")              # only the tag
    subscribe(home, "seats/eve", "nosuchbox")                 # resolves nowhere
    subscribe(home, "seats/eve", "old-query")                 # an old saved query
    (home / "seats/eve/subscriptions/old-query").write_text("query: tag:x\n")
    by_name = {line.split()[1]: line for line in rows(box_list().stdout)}
    assert by_name["atomnote"].endswith("<- ivan; 5a1d0c3e"), by_name
    assert DEAD[:8] not in by_name["atomnote"]
    # Subscribers in the reader's order: seats by name, then live sessions.
    assert by_name["acmecorp/sessionmail"].endswith("<- eve; ivan"), by_name
    assert by_name["ivan"].endswith("<- +kaizen: ivan"), by_name
    assert "<-" not in by_name["acmecorp"]
    assert not any("nosuchbox" in l or "old-query" in l for l in by_name.values())


# ── list: the fold - 40 rows, 400 tokens, then how to get more ─────────────

def test_the_newest_40_then_a_line_saying_how_many_were_hidden(root):
    for i in range(60):
        mkbox(root / f"seat{i:02d}", 1000 + i)
    r = box_list()
    shown = rows(r.stdout)
    assert len(shown) == 40
    assert shown[0].split()[1] == "seat59" and shown[-1].split()[1] == "seat20"
    last = footer(r.stdout)
    assert "40 shown" in last and "20 hidden" in last and "-n all" in last, last
    assert len(rows(box_list("-n", "all").stdout)) == 60
    assert len(rows(box_list("-n", "0").stdout)) == 60
    assert len(rows(box_list("-n", "5").stdout)) == 5


def test_rows_stop_at_400_tokens_even_under_40(root, home):
    for i in range(40):
        mkbox(root / f"box{i:02d}", 1000 + i)
        for j in range(8):
            subscribe(home, f"seats/subscriber-number-{j:02d}", f"box{i:02d}")
    r = box_list()
    assert r.exit_code == 0, r.output
    assert _tokens.estimate(r.stdout) <= 400
    assert len(r.stdout) / 3 <= 400, "the scaffold's own conservative estimate too"
    shown = rows(r.stdout)
    assert 0 < len(shown) < 40
    last = footer(r.stdout)
    assert f"{40 - len(shown)} hidden" in last and "400" in last, last


def test_an_empty_or_missing_root_is_an_empty_listing_not_an_error(root):
    r = box_list()
    assert r.exit_code == 0, r.output
    assert footer(r.stdout).startswith("-- 0 shown")


def test_the_live_mail_root_is_never_read(root, monkeypatch, tmp_path):
    """The listing reads the root it was given and nothing else: with HOME
    pointed at an empty tmp dir, the default root would be empty too, and
    the fixture's box is the only one printed."""
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    mkbox(root / "only", 1)
    assert [l.split()[1] for l in rows(box_list().stdout)] == ["only"]


# ── path: today's `message path`, and the alias stays ──────────────────────

@pytest.fixture
def nested(tmp_path, monkeypatch, root):
    base = tmp_path / "base"
    box = mkbox(base / "agents" / "store" / "proj")
    monkeypatch.setattr(_seat, "_postfix_maps",
                        lambda p: ["hash:/fake/vmailbox"] if p == "virtual_mailbox_maps" else None)
    monkeypatch.setattr(_seat, "_postmap_lookup",
                        lambda key, maps: ("/fake/vmailbox", "agents/store/proj/")
                        if key == f"proj@{HOST}" else None)
    monkeypatch.setattr(_seat, "_postconf_value",
                        lambda p: str(base) if p == "virtual_mailbox_base" else None)
    return box


@pytest.mark.parametrize("args", [
    ["alpha"], [], ["proj", "--why"], ["proj+kaizen", "--why"], ["ghost"],
    ["ghost+kaizen"],
])
def test_box_path_and_path_print_the_same_maildir_and_exit_the_same_way(nested, root, args):
    mkbox(root / "alpha")
    old = CliRunner().invoke(cli, ["path", *args])
    new = CliRunner().invoke(cli, ["box", "path", *args])
    assert (new.exit_code, new.stdout) == (old.exit_code, old.stdout)
    assert new.stderr.replace("message box path:", "message path:") == old.stderr
    if args and args[0].startswith("ghost"):
        assert new.exit_code == EX_REFUSED and new.stdout == ""
    else:
        assert new.exit_code == 0 and new.stdout.strip()


def test_the_box_page_names_both_verbs(root):
    page = CliRunner().invoke(cli, ["box", "-h"]).output
    assert "list" in page and "path" in page
    assert "box" in CliRunner().invoke(cli, ["-h"]).output


# ── who counts as live: the generator's rule, never this host's harness ────

def test_a_live_session_without_a_box_is_not_named_as_a_subscriber(fleet, home, monkeypatch):
    """vmailbox-gen copies a live session's subscriptions only when its box
    exists under agents/ (a live session WITH A BOX), so a listing that named
    one without a box would promise a copy the map never makes."""
    boxless = "b0c5e55e-1111-2222-3333-444455556666"
    monkeypatch.setattr(message, "_live_sessions", lambda: {SID, boxless})
    subscribe(home, f"sessions/{boxless}", "atomnote")
    subscribe(home, f"sessions/{SID}", "atomnote")
    by_name = {line.split()[1]: line for line in rows(box_list().stdout)}
    assert by_name["atomnote"].endswith("<- 5a1d0c3e"), by_name


def test_live_sessions_are_the_sidecars_whose_pid_is_alive(monkeypatch):
    from types import SimpleNamespace as S
    from sessionmail import _boxes
    monkeypatch.setattr(_boxes.sessiontools, "sessions", lambda: [
        S(id="alive", pid=101), S(id="dead", pid=202), S(id="nopid", pid=None), S(id="", pid=101),
    ])
    monkeypatch.setattr(_seat, "_pid_alive", lambda pid: pid == 101)
    assert _boxes.live_sessions() == {"alive"}


def test_no_harness_is_no_live_session_not_an_error(monkeypatch):
    from sessionmail import _boxes

    def broken():
        raise OSError("no sidecar dir")
    monkeypatch.setattr(_boxes.sessiontools, "sessions", broken)
    assert _boxes.live_sessions() == set()


def test_the_hidden_session_count_is_of_the_boxes_the_pattern_matched(fleet):
    last = footer(box_list("0477").stdout)
    assert "1 session boxes hidden" in last, last
    assert "session boxes hidden" not in footer(box_list("ivan").stdout)


def test_an_explicit_n_lifts_the_token_cap(root, home):
    for i in range(40):
        mkbox(root / f"box{i:02d}", 1000 + i)
        for j in range(8):
            subscribe(home, f"seats/subscriber-number-{j:02d}", f"box{i:02d}")
    assert len(rows(box_list("-n", "40").stdout)) == 40
    assert len(rows(box_list("-n", "all").stdout)) == 40


def test_a_subscription_the_generator_refuses_is_named_nowhere(fleet, home):
    """The generator's `sub_address` refuses these, so no row copies them:
    `+memory` is claimed by the box alone (its identity row), and an empty
    tag, a second `+`, a `+` beside a `/`, or a character postfix would not
    keep is no address. A listing that named them would promise a copy the
    map never makes - and for `+memory`, someone else reading an owner's
    memory writes. (Review of do-005, 2030-09-29.)"""
    for stored in ("ivan+memory", "ivan+MEMORY", "atomnote+", "atomnote+a+b",
                   "acmecorp--sessionmail+kaizen", "ivan+a:b"):
        subscribe(home, "seats/eve", stored)
    subscribe(home, "seats/eve", "ivan+kaizen")               # a good one still shows
    by_name = {line.split()[1]: line for line in rows(box_list().stdout)}
    assert by_name["ivan"].endswith("<- +kaizen: eve"), by_name
    assert "<-" not in by_name["atomnote"], by_name
    assert "<-" not in by_name["acmecorp/sessionmail"], by_name
