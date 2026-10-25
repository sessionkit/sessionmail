"""message subscription list [BOX] (openspec/changes/message-subscriptions,
tasks 3.3, design D6, specs/message "What reaches me, and who hears this
box").

Every test runs over a synthetic mail root (`SESSION_MAIL_ROOT`) and
conftest's hermetic `SESSIONMAIL_HOME`. Live sessions come from a stub, the
`ps` table is a stub, and the harness (`sessiontools.sessions`) is a stub:
nothing reads this host's `~/.mail`, its processes or its sessions."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _boxes, _reach, _seat, message

from _seatmap import seat_subscribe, session_declare, session_subscribe
from sessionmail.message import EX_REFUSED, cli

SID = "5a1d0c3e-1111-2222-3333-444455556666"
OTHER = "0f0e0d0c-1111-2222-3333-444455556666"
SUPER = "5e5e5e5e-1111-2222-3333-444455556666"
DEAD = "dead0000-1111-2222-3333-444455556666"


def _mail(sid: str) -> Path:
    """Where a session's mail state is since session-settings 3.3a: under
    the session dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/`
    (spelt out, not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def mkbox(d: Path) -> Path:
    for s in ("new", "cur", "tmp"):
        (d / s).mkdir(parents=True, exist_ok=True)
    return d


def subscribe(home: Path, folder: str, stored: str, body: str = "# added by a test\n") -> Path:
    """A subscription where it routes from: a SESSION's is a key of its map
    (session-settings 3.3b); an agent's, a file in its folder. A saved query
    (a `query:` body) has no map form: it stays a file in the old folder,
    where it is skipped as ever."""
    if folder.startswith("sessions/") and "query:" not in body:
        from sessionmail import _subscription
        return session_subscribe(folder.split("/", 1)[1], _subscription.box_name(stored))
    f = home / folder / "subscriptions" / stored
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


NO_PS = lambda: ({}, {})  # noqa: E731

#: The footer's clause when no alias map can be asked (SM-938): the `root`
#: fixture has no postfix, so every row says `map not measured` and the
#: footer says why, once.
NO_MAP = "; map not measured: no postfix here"


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "mail"
    r.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(r))
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)     # no postfix here
    monkeypatch.setattr(_seat, "rows_in_map",                        # nor its map (SM-938)
                        lambda pairs, env=None: (None, "no postfix here"))
    monkeypatch.setattr(message, "_live_sessions", lambda: {SID, OTHER, SUPER})
    monkeypatch.setattr(_seat, "_ps_table", NO_PS)
    monkeypatch.setattr(_reach.sessiontools, "sessions", lambda env=None: [])
    return r


@pytest.fixture
def home() -> Path:
    return _seat.home()          # conftest's hermetic SESSIONMAIL_HOME


def as_caller(monkeypatch, seat: str | None, sid: str | None) -> None:
    if seat:
        monkeypatch.setenv("TM_AGENT_ADDRESS", seat)
    if sid:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)


def sub_list(*args):
    return CliRunner().invoke(cli, ["subscription", "list", *args])


def body(out: str) -> list[str]:
    return [line for line in out.splitlines() if not line.startswith("-- ")]


def footer(out: str) -> str:
    last = out.rstrip("\n").splitlines()[-1]
    assert last.startswith("-- "), out
    return last


def label(line: str) -> str:
    """The address on a tree line: the branch drawing and the note cut."""
    return line.lstrip("│├└─ ").split()[0]


def indent(line: str) -> int:
    return len(line) - len(line.lstrip("│├└─ "))


# ── the tree: what reaches me ──────────────────────────────────────────────

@pytest.fixture
def gc(root, home, monkeypatch):
    """specs/message, "The tree shows what feeds what": a GC session in seat
    `acmecorp+gc`, the seat subscribed to `acmecorp` (a store box), the
    session subscribed to `voicemode+super`, which `voicemode+kaizen` feeds."""
    store = mkbox(root / "acmecorp")
    (store / _boxes.STORE_MARKER).write_text("")
    mkbox(store / "voicemode")
    mkbox(root / SID)
    seat_subscribe("acmecorp+gc", "acmecorp")      # a SEAT: its map (3.2)
    subscribe(home, f"sessions/{SID}", "voicemode+super")
    seat_subscribe("voicemode+super", "voicemode+kaizen")      # a SEAT: its map (3.2)
    as_caller(monkeypatch, "acmecorp+gc", SID)


def test_the_tree_shows_what_feeds_what_seat_first(gc):
    done = sub_list()
    assert done.exit_code == 0, done.output
    lines = body(done.stdout)
    assert [label(line) for line in lines] == [
        SID[:8], "acmecorp+gc", "acmecorp", "voicemode+super", "voicemode+kaizen"], done.stdout
    assert "(this session)" in lines[0]
    # the seat is the FIRST branch, with `acmecorp` under it
    assert lines[1].startswith("├── acmecorp+gc") and "your seat" in lines[1]
    assert lines[2].startswith("│   └── acmecorp") and "store box, seat subscription" in lines[2]
    # `voicemode+super` is the second branch, with `voicemode+kaizen` under it
    assert lines[3].startswith("└── voicemode+super")
    assert "+super of project acmecorp/voicemode, session subscription" in lines[3]
    assert lines[4].startswith("    └── voicemode+kaizen") and "seat subscription" in lines[4]
    assert footer(done.stdout) == "-- 4 addresses reach you, 2 levels deep" + NO_MAP


def test_notes_line_up_in_one_column(gc):
    lines = body(sub_list().stdout)[1:]
    starts = {line.index(note) for line, note in
              ((line, line.split("   ")[-1].strip()) for line in lines)}
    assert len(starts) == 1, lines


@pytest.fixture
def ring(root, home, monkeypatch):
    """specs/message, "A loop is printed once": `a` feeds `b` and `b` feeds
    `a`, and the session is subscribed to `a`."""
    for name in ("a", "b", SID):
        mkbox(root / name)
    subscribe(home, "seats/a", "b")
    subscribe(home, "seats/b", "a")
    subscribe(home, f"sessions/{SID}", "a")
    as_caller(monkeypatch, None, SID)


def test_a_loop_is_printed_once_and_marked(ring):
    done = sub_list()
    assert done.exit_code == 0, done.output
    lines = body(done.stdout)
    assert [label(line) for line in lines] == [SID[:8], "a", "b", "a"], done.stdout
    # `b` appears under `a`, and `a` under `b` is marked and not walked again
    assert indent(lines[2]) > indent(lines[1]) and indent(lines[3]) > indent(lines[2])
    assert lines[3].endswith(_reach.LOOP), lines[3]
    assert _reach.LOOP not in "".join(lines[:3])
    assert footer(done.stdout) == "-- 2 addresses reach you, 3 levels deep" + NO_MAP


def test_a_loop_back_to_the_seat_is_marked_not_walked(root, home, monkeypatch):
    mkbox(root / "x")
    mkbox(root / "ivan")
    subscribe(home, "seats/ivan", "x")
    subscribe(home, "seats/x", "ivan")
    as_caller(monkeypatch, "ivan", SID)
    lines = body(sub_list().stdout)
    assert [label(line) for line in lines] == [SID[:8], "ivan", "x", "ivan"]
    assert lines[-1].endswith(_reach.LOOP)


@pytest.fixture
def chain(root, home, monkeypatch):
    """c1 <- c2 <- ... <- c6: six levels, and the session subscribed to c1."""
    for n in range(1, 7):
        mkbox(root / f"c{n}")
    for n in range(1, 6):
        subscribe(home, f"seats/c{n}", f"c{n + 1}")
    subscribe(home, f"sessions/{SID}", "c1")
    mkbox(root / SID)
    as_caller(monkeypatch, None, SID)


def test_depth_stops_at_four_and_says_how_to_see_more(chain):
    done = sub_list()
    assert done.exit_code == 0, done.output
    lines = body(done.stdout)
    assert [label(line) for line in lines[:5]] == [SID[:8], "c1", "c2", "c3", "c4"]
    assert "c5" not in done.stdout and "c6" not in done.stdout
    assert "1 more feeds this, below depth 4 (--depth 5 shows them)" in lines[5], lines
    assert len(lines) == 6
    assert "cut at depth 4" in footer(done.stdout) and "4 levels deep" in footer(done.stdout)


def test_depth_is_the_readers_to_raise(chain):
    done = sub_list("--depth", "6")
    lines = body(done.stdout)
    assert [label(line) for line in lines] == [SID[:8]] + [f"c{n}" for n in range(1, 7)]
    assert "more feed" not in done.stdout and "cut at" not in done.stdout
    assert footer(done.stdout) == "-- 6 addresses reach you, 6 levels deep" + NO_MAP


def test_depth_bound_also_cuts_the_seat_branch(root, home, monkeypatch):
    mkbox(root / "ivan")
    mkbox(root / "atomnote")
    subscribe(home, "seats/ivan", "atomnote")
    as_caller(monkeypatch, "ivan", SID)
    lines = body(sub_list("--depth", "1").stdout)
    assert [label(line) for line in lines[:2]] == [SID[:8], "ivan"]
    assert "atomnote" not in "".join(lines)
    assert "--depth 2 shows them" in lines[2]


def test_depth_below_one_is_a_usage_error(chain):
    """click's usage exit, as for every verb here (`box list -n -1` too)."""
    done = sub_list("--depth", "0")
    assert done.exit_code == 2 and not done.stdout.startswith(SID[:8])
    assert "Invalid value for '--depth'" in done.output


def test_the_tree_is_never_looser_than_the_map(root, home, monkeypatch):
    """'Subscribed' is what the generator ROUTES (do-006 settled it at
    cb87f73): an old saved query, a box that does not resolve and a refused
    address are counted, never drawn. (A link to a plain file IS routed was
    pinned here on the session's folder; since 3.3b a session's boxes are
    map keys, and its old folder routes nothing.)"""
    mkbox(root / "atomnote")
    mkbox(root / "kith")
    mkbox(root / SID)
    subscribe(home, f"sessions/{SID}", "atomnote", body="query: tag:unread\n")
    subscribe(home, f"sessions/{SID}", "nowhere")
    subscribe(home, f"sessions/{SID}", "ivan+memory")
    subscribe(home, f"sessions/{SID}", "kith")
    as_caller(monkeypatch, None, SID)
    done = sub_list()
    lines = body(done.stdout)
    assert [label(line) for line in lines] == [SID[:8], "kith"], done.stdout
    assert "3 subscription files the map skips" in footer(done.stdout)


def test_a_skipped_file_is_counted_once_however_often_its_owner_is_reached(
        root, home, monkeypatch):
    """The footer counts FILES the map skips, not visits: an owner reached on
    two branches (the session subscribed to its own seat; a diamond through
    `x`) still holds each skipped file once. Red at 8ab0080: 2 and 2."""
    for n in ("ivan", "atomnote", "x", "y", SID):
        mkbox(root / n)
    subscribe(home, "seats/ivan", "atomnote", body="query: tag:unread\n")
    subscribe(home, f"sessions/{SID}", "ivan")
    as_caller(monkeypatch, "ivan", SID)
    assert "; 1 subscription file the map skips" in footer(sub_list().stdout)

    subscribe(home, "seats/ivan", "x")
    subscribe(home, f"sessions/{SID}", "y")
    subscribe(home, "seats/y", "x")
    subscribe(home, "seats/x", "nowhere")
    done = sub_list()
    assert [label(line) for line in body(done.stdout)] == [SID[:8], "ivan", "x", "y", "x"]
    assert "; 2 subscription files the map skips" in footer(done.stdout), done.stdout


def test_the_seat_branch_is_walked_once_so_the_cut_count_matches_the_tree(
        root, home, monkeypatch):
    """A session subscribed to its own seat (the spec's GC scenario) has the
    seat as its first branch, not a second: the footer's cut count is the
    number of `...` lines printed. At a341c77 the session's own walk walked the
    seat's subtree again and threw it away by label, so each cut counted
    twice: "in 2 places" beside one `...`, at --depth 3 and at --depth 1."""
    for n in ("ivan", "a", "b", "c", SID):
        mkbox(root / n)
    subscribe(home, "seats/ivan", "a")
    subscribe(home, "seats/a", "b")
    subscribe(home, "seats/b", "c")
    subscribe(home, f"sessions/{SID}", "ivan")
    as_caller(monkeypatch, "ivan", SID)
    for depth in ("3", "1"):
        done = sub_list("--depth", depth)
        assert done.exit_code == 0, done.output
        cuts = sum(1 for line in body(done.stdout) if line.lstrip("│├└─ ").startswith("…"))
        assert cuts == 1, done.stdout
        assert f"; cut at depth {depth} in 1 place (--depth N)" in footer(done.stdout), done.stdout


def test_a_dead_session_feeds_nobody(root, home, monkeypatch):
    """A session's subscriptions count only while it is live WITH a box: a
    subscription held by a dead session is not a feed."""
    mkbox(root / "ivan")
    mkbox(root / "atomnote")
    mkbox(root / DEAD)
    subscribe(home, f"sessions/{DEAD}", "ivan")         # DEAD would feed... nothing
    subscribe(home, "seats/ivan", DEAD)                 # ivan hears DEAD's box
    subscribe(home, f"sessions/{DEAD}", "atomnote")
    as_caller(monkeypatch, "ivan", SID)
    lines = body(sub_list().stdout)
    assert [label(line) for line in lines] == [SID[:8], "ivan", DEAD[:8]]


def test_a_session_subscription_to_its_own_seat_is_not_a_second_branch(root, home, monkeypatch):
    mkbox(root / "ivan")
    subscribe(home, f"sessions/{SID}", "ivan")
    as_caller(monkeypatch, "ivan", SID)
    lines = body(sub_list().stdout)
    assert [label(line) for line in lines] == [SID[:8], "ivan"]


def test_a_session_without_a_box_is_told_nothing_reaches_it_yet(root, home, monkeypatch):
    mkbox(root / "ivan")
    as_caller(monkeypatch, "ivan", SID)                 # no <root>/<SID>
    assert "NO BOX" in footer(sub_list().stdout)


def test_no_session_and_no_seat_is_refused_with_the_way_out(root):
    done = sub_list()
    assert done.exit_code == EX_REFUSED
    assert "--for WHO" in done.stderr and "CLAUDE_CODE_SESSION_ID" in done.stderr


def test_nothing_subscribed_is_an_empty_tree_not_an_error(root, monkeypatch):
    mkbox(root / SID)
    as_caller(monkeypatch, None, SID)
    done = sub_list()
    assert done.exit_code == 0
    assert body(done.stdout) == [f"{SID[:8]} (this session)"]
    assert footer(done.stdout) == "-- 0 addresses reach you, 0 levels deep"


def test_for_a_seat_roots_the_tree_at_the_seat(gc, monkeypatch):
    """Listing is open to all (D5): --for needs no privilege."""
    done = sub_list("--for", "voicemode+super")
    assert done.exit_code == 0, done.output
    assert [label(line) for line in body(done.stdout)] == ["voicemode+super", "voicemode+kaizen"]
    assert "(seat)" in body(done.stdout)[0]
    assert footer(done.stdout).startswith("-- 1 address reaches you")


def test_for_a_session_takes_its_declared_seat(root, home, monkeypatch):
    mkbox(root / "eve")
    mkbox(root / "kith")
    mkbox(root / OTHER)
    subscribe(home, "seats/eve", "kith")
    session_declare(OTHER, "eve")              # its layer's `address` (3.3b)
    as_caller(monkeypatch, "ivan", SID)
    lines = body(sub_list("--for", OTHER).stdout)
    assert [label(line) for line in lines] == [OTHER[:8], "eve", "kith"]


def test_for_a_session_falls_back_to_the_harness_agent(root, home, monkeypatch):
    from types import SimpleNamespace as S
    mkbox(root / "eve")
    mkbox(root / OTHER)
    monkeypatch.setattr(_reach.sessiontools, "sessions",
                        lambda env=None: [S(id=OTHER, agent="Eve", pid=1)])
    lines = body(sub_list("--for", OTHER).stdout)
    assert [label(line) for line in lines] == [OTHER[:8], "eve"]


# ── BOX: who hears this box ────────────────────────────────────────────────

def ps_with(*procs: tuple[int, int, str]):
    """A `ps` table: (pid, ppid, command) rows."""
    def table():
        return ({p: pp for p, pp, _ in procs}, {p: c for p, _, c in procs})
    return table


@pytest.fixture
def atomnote(root, home, monkeypatch):
    """specs/message, "A super's listener shows beside a subscription, in the
    release of grace": ivan subscribed to `atomnote`, and a super still
    running `pager listen atomnote`."""
    mkbox(root / "atomnote")
    mkbox(root / "ivan")
    for sid in (SID, SUPER, DEAD):
        mkbox(root / sid)
    subscribe(home, "seats/ivan", "atomnote")
    subscribe(home, f"sessions/{SID}", "atomnote")
    subscribe(home, f"sessions/{DEAD}", "atomnote")      # not live: hears nothing
    monkeypatch.setattr(_seat, "_ps_table", ps_with(
        (900, 1, f"claude --session-id {SUPER} --model opus"),
        (901, 900, "/bin/zsh -c eval 'pager listen atomnote'"),
        (902, 901, "/usr/bin/python3 /opt/bin/pager listen atomnote"),
        (903, 1, "/usr/bin/python3 /opt/bin/pager listen ivan"),      # another box
        (904, 1, "vim notes-about-pager-listen-atomnote"),
    ))


def test_box_lists_subscribers_and_a_live_listener_marked_as_such(atomnote):
    done = sub_list("atomnote")
    assert done.exit_code == 0, done.output
    lines = body(done.stdout)
    assert label(lines[0]) == "atomnote" and "seat box" in lines[0]
    by = {label(line): line for line in lines[1:]}
    assert set(by) == {"ivan", SID[:8], SUPER[:8]}, done.stdout
    assert "seat subscription" in by["ivan"] and "LISTENER" not in by["ivan"]
    assert "session subscription" in by[SID[:8]]
    assert "LISTENER, not a subscription" in by[SUPER[:8]]
    assert "`pager listen atomnote` (pid 902)" in by[SUPER[:8]]
    assert DEAD[:8] not in done.stdout
    assert footer(done.stdout) == "-- 2 hear it; 1 live listener"


def test_a_listener_is_named_by_its_stamp_before_its_ancestry(atomnote, home, monkeypatch):
    stamps = _mail(OTHER) / _seat.STAMPS_DIR
    stamps.mkdir(parents=True)
    (stamps / _seat.LISTENING_STAMP).write_text("777\n")
    monkeypatch.setattr(_seat, "_ps_table", ps_with(
        (777, 1, "/usr/bin/python3 /opt/bin/pager listen --quiet -t 30 atomnote")))
    monkeypatch.setattr(_seat, "_pid_alive", lambda pid: pid == 777)
    by = {label(line): line for line in body(sub_list("atomnote").stdout)[1:]}
    assert "LISTENER" in by[OTHER[:8]] and "(pid 777)" in by[OTHER[:8]]


def test_a_listener_with_no_owner_is_named_by_pid(atomnote, monkeypatch):
    monkeypatch.setattr(_seat, "_ps_table", ps_with(
        (555, 1, "/opt/bin/pager listen atomnote")))
    lines = body(sub_list("atomnote").stdout)
    assert any(line.startswith("└── pid 555") and "LISTENER" in line for line in lines), lines


def test_no_listener_says_so(atomnote, monkeypatch):
    monkeypatch.setattr(_seat, "_ps_table", NO_PS)
    done = sub_list("atomnote")
    assert "LISTENER" not in done.stdout
    assert footer(done.stdout) == "-- 2 hear it; no live listener on it"


def test_a_session_whose_own_listener_is_live_is_marked_listening(atomnote, home, monkeypatch):
    stamps = _mail(SID) / _seat.STAMPS_DIR
    stamps.mkdir(parents=True)
    (stamps / _seat.LISTENING_STAMP).write_text("4242\n")
    monkeypatch.setattr(_seat, "_pid_alive", lambda pid: pid == 4242)
    by = {label(line): line for line in body(sub_list("atomnote").stdout)[1:]}
    assert by[SID[:8]].endswith(", listening")
    assert not by["ivan"].endswith(", listening")


def test_a_bare_listen_is_a_listener_on_the_sessions_own_box(atomnote, monkeypatch):
    monkeypatch.setattr(_seat, "_ps_table", ps_with(
        (900, 1, f"claude --session-id {SUPER}"),
        (902, 900, "/opt/bin/pager listen"),
    ))
    lines = body(sub_list(SUPER).stdout)
    assert any("LISTENER" in line and "`pager listen` (pid 902)" in line for line in lines), lines
    assert "LISTENER" not in sub_list("atomnote").stdout


def test_a_seat_box_is_heard_by_its_live_sessions(root, home, monkeypatch):
    from types import SimpleNamespace as S
    mkbox(root / "eve")
    for sid in (SID, OTHER):
        mkbox(root / sid)
    session_declare(SID, "eve")                # its layer's `address` (3.3b)
    monkeypatch.setattr(_reach.sessiontools, "sessions",
                        lambda env=None: [S(id=OTHER, agent="eve", pid=1),
                                          S(id=DEAD, agent="eve", pid=2)])
    lines = body(sub_list("eve").stdout)
    by = {label(line): line for line in lines[1:]}
    assert set(by) == {SID[:8], OTHER[:8]}, lines
    assert all("the seat row copies here" in v for v in by.values())


def test_a_claimed_tag_is_shown_under_the_box_and_answers_for_itself(root, home, monkeypatch):
    """A plus subscription CLAIMS its tag (RULED): the bare box's
    subscribers do not get it; an unclaimed tag falls through."""
    store = mkbox(root / "acmecorp")
    (store / _boxes.STORE_MARKER).write_text("")
    mkbox(store / "voicemode")
    seat_subscribe("voicemode+super", "voicemode")      # a SEAT: its map (3.2)
    subscribe(home, "seats/ivan", "voicemode+kaizen")
    whole = sub_list("voicemode")
    lines = body(whole.stdout)
    assert label(lines[0]) == "acmecorp/voicemode"
    assert [label(line) for line in lines[1:]] == ["voicemode+super", "+kaizen", "ivan"], lines
    assert "claimed" in lines[2]
    assert "1 more on 1 claimed tag" in footer(whole.stdout)

    claimed = sub_list("voicemode+kaizen")
    assert [label(line) for line in body(claimed.stdout)[1:]] == ["ivan"]
    assert "+kaizen is CLAIMED" in footer(claimed.stdout)

    falls = sub_list("voicemode+vm-1")
    by = body(falls.stdout)[1:]
    assert [label(line) for line in by] == ["voicemode+super"]
    assert "no one claims +vm-1: it falls through" in by[0]


def test_an_unknown_box_is_refused(root):
    done = sub_list("nowhere")
    assert done.exit_code == EX_REFUSED
    assert "no such box 'nowhere'" in done.stderr


# ── one predicate: box list and subscription list agree ────────────────────

def test_an_owner_folder_postfix_would_not_keep_is_named_by_neither(root, home, monkeypatch):
    """The generator skips a whole owner folder whose name postfix would not
    keep (`subscription_records`). `box list` named it at cb87f73 (red); it
    and `subscription list` share `_boxes.routed` now."""
    mkbox(root / "atomnote")
    subscribe(home, "seats/we!rd", "atomnote")
    subscribe(home, "seats/ivan", "atomnote")
    listed = CliRunner().invoke(cli, ["box", "list"]).stdout
    assert "we!rd" not in listed and "<- ivan" in listed
    by = {label(line) for line in body(sub_list("atomnote").stdout)[1:]}
    assert by == {"ivan"}


def test_the_live_mail_root_is_never_read(root, home, monkeypatch):
    """Only the root it is given: a HOME full of real-looking boxes stays
    unread (the synthetic root is empty of `atomnote`)."""
    fake_home = root.parent / "userhome"
    mkbox(fake_home / ".mail" / "agents" / "atomnote")
    monkeypatch.setenv("HOME", str(fake_home))
    assert sub_list("atomnote").exit_code == EX_REFUSED
    assert os.environ["SESSION_MAIL_ROOT"] == str(root)
