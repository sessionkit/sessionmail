"""message subscription add|remove (openspec/changes/message-subscriptions,
tasks 3.2, designs D1, D3.1 and D5; specs/message "Only the verbs write a
subscription, and every write rebuilds" and "Seat subscriptions, and anyone
else's, are for Ivan and Judy").

Every test runs over a synthetic fleet under tmp_path: the mail root, the
sessionmail home, the postfix maps and `session list` are all fakes, and the
generator is the REAL one held at arm's length - `--out` under tmp, never
postmap. The live map cannot be rebuilt from here: conftest points the
generator at a path that does not exist unless a test says otherwise
(`test_a_test_without_a_generator_cannot_reach_the_live_one`)."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from importlib.resources import as_file, files
from pathlib import Path

import pytest
from click.testing import CliRunner

from _seatmap import session_boxes, session_dir, session_live_path, session_log, session_map_path
from fakes import write_fake
from sessionmail import _boxes, _operator, _seat, _subscribe, _subscription
from sessionmail.message import EX_NOT_REBUILT, EX_REFUSED, cli
from test_task_address_doorbell import FAKE_POSTMAP

HOST = socket.gethostname().split(".")[0].lower()
SID = "5a1d0c3e-1111-2222-3333-444455556666"        # the caller's session (a worker)
IVAN_SID = "c0a4c0a4-1111-2222-3333-444455556666"
JUDY_SID = "3e3e3e3e-1111-2222-3333-444455556666"
OTHER = "0be70be7-1111-2222-3333-444455556666"      # someone else's live session

#: The doorbell suite's fake, plus `virtual_mailbox_base`: `box_via_map`
#: asks it to turn a map value (`agents/st/atomnote/`) into a path, which is
#: how a bare project name resolves, on hq and here.
FAKE_POSTCONF = """#!{python}
import os, sys
maps = os.environ["POSTFIX_MAPS"]
print({{"virtual_alias_maps": "hash:" + maps + "/valias",
       "virtual_mailbox_maps": "hash:" + maps + "/vmailbox",
       "virtual_mailbox_base": os.environ["MAILBOX_BASE"]}}[sys.argv[2]])
"""

#: The generator reads Claude Code's sidecars for LIVE (master 2030-12-08, no
#: `session` any more); `ps` answers from the fixture so the pid is "running".
#: Ported from the `session list` stub at the SM-374 merge (do-015), the way
#: master ported tests/test_task_address_doorbell.py.
FAKE_PS = """#!/bin/sh
cat "$FAKE_PS_TABLE"
"""
STARTED = "Sun Jun 09 12:00:00 2030"


def make_live(tmp_path, sid: str, agent: str) -> None:
    """SID live in the fleet as AGENT: a sidecar `<pid>.json` and a `ps` row
    whose start matches its procStart."""
    sidecars, table = tmp_path / "claude-sessions", tmp_path / "ps.txt"
    sidecars.mkdir(exist_ok=True)
    pid = 40000 + len(list(sidecars.glob("*.json")))
    (sidecars / f"{pid}.json").write_text(json.dumps(
        {"pid": pid, "sessionId": sid, "agent": agent, "procStart": STARTED}))
    with table.open("a") as fh:
        fh.write(f"{pid} {STARTED}\n")

#: The REAL generator: `--out` under tmp, no postmap, the host pinned. Each
#: run is logged, so a test can say "did not rebuild" by count, not by hope.
WRAPPED_GEN = """#!/bin/sh
echo "$*" >> "$GEN_LOG"
exec "$REAL_GEN" --out "$POSTFIX_MAPS/vmailbox" --no-postmap --host "$GEN_HOST" "$@"
"""


def _maildir(d: Path) -> Path:
    for leaf in ("new", "cur", "tmp"):
        (d / leaf).mkdir(parents=True, exist_ok=True)
    return d


class Fleet:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.agents = tmp / "mail" / "agents"
        self.home = tmp / "sessionmail"
        self.maps = tmp / "maps"
        self.log = tmp / "gen.log"
        self.userhome = tmp / "userhome"     # HOME: ~/.session lives here

    @property
    def valias(self) -> str:
        return (self.maps / "valias").read_text()

    def runs(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def session_file(self, sid: str, stored: str) -> Path:
        """A session's OLD box file (before 3.3b): routes nothing now."""
        return self.home / "sessions" / sid / "subscriptions" / stored

    def session_map(self, sid: str) -> Path:
        """SID's layer, `~/.session/sessions/<sid>/settings/mail.json` (3.3b)."""
        return session_map_path(sid, self.userhome)

    def session_live(self, sid: str) -> Path:
        """The file SID's verbs write since task 3.4: `mail.d/70-live.json`."""
        return session_live_path(sid, self.userhome)

    def subscribed(self, sid: str) -> list[str]:
        """The boxes SID's layer holds `true`, folded (`mail.json`, `mail.d/`)."""
        return session_boxes(sid, self.userhome)

    def session_log(self, sid: str) -> list[dict]:
        return session_log(sid, self.userhome)

    def seat_file(self, seat: str, stored: str) -> Path:
        return self.home / "seats" / seat / "subscriptions" / stored

    def files(self) -> list[str]:
        # Through symlinks: since session-settings 3.3a a live session's
        # `<home>/sessions/<sid>` is a symlink into its session dir's `mail/`,
        # and the owners walk (the generator's input) reads through it. Since
        # 3.3b a session's subscriptions are its map: `sessions/<sid>#<box>`.
        old = [str(p.relative_to(self.home))
               for p in self.home.rglob("subscriptions/*", recurse_symlinks=True)]
        root = self.userhome / ".session" / "sessions"
        maps = [f"sessions/{d.name}#{box}" for d in (root.iterdir() if root.is_dir() else [])
                for box in session_boxes(d.name, self.userhome)]
        return sorted(old + maps)


@pytest.fixture
def fleet(tmp_path, monkeypatch) -> Fleet:
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("the generator's alias half needs jq")
    f = Fleet(tmp_path)
    for flat in ("worker", "ivan", "judy", SID, IVAN_SID, JUDY_SID, OTHER):
        _maildir(f.agents / flat)
    store = f.agents / "st"
    store.mkdir()
    (store / _boxes.STORE_MARKER).write_text("")
    _maildir(store / "atomnote")
    _maildir(store / "voicemode")
    _maildir(f.agents / "loose" / "pr")         # a project-shaped dir, no store marker
    for seat in ("ivan", "judy", "worker", "voicemode+super"):
        (f.home / "seats" / seat).mkdir(parents=True)
    for sid in (SID, IVAN_SID, JUDY_SID, OTHER):
        (f.home / "sessions" / sid).mkdir(parents=True)
        # The session dir sessiontools keeps (its launcher makes it): where
        # a session's layer lives since 3.3b, and what `--for SID` asks for.
        session_dir(sid, f.userhome).mkdir(parents=True)
    f.maps.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "postconf", FAKE_POSTCONF)
    write_fake(bin_dir / "postmap", FAKE_POSTMAP)
    for name, text in (("ps", FAKE_PS), ("vmailbox-gen-wrapped", WRAPPED_GEN)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    for sid, agent in ((SID, "worker"), (IVAN_SID, "ivan"), (JUDY_SID, "judy"), (OTHER, "eve")):
        make_live(tmp_path, sid, agent)
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as real:
        monkeypatch.setenv("REAL_GEN", str(real))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.path.dirname(jq)}:/usr/bin:/bin")
    monkeypatch.setenv(_seat.GEN_VAR, str(bin_dir / "vmailbox-gen-wrapped"))
    monkeypatch.setenv("GEN_HOST", HOST)
    monkeypatch.setenv("GEN_LOG", str(f.log))
    monkeypatch.setenv("POSTFIX_MAPS", str(f.maps))
    monkeypatch.setenv("MAILBOX_BASE", str(f.agents.parent))
    monkeypatch.setenv("FAKE_PS_TABLE", str(tmp_path / "ps.txt"))
    monkeypatch.setenv("SESSION_SIDECAR_DIR", str(tmp_path / "claude-sessions"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(f.agents))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(f.home))
    as_seat(monkeypatch, "worker", SID)
    # Both maps once, WHOLE, so a bare project name resolves by the map as it
    # does on hq (`sessionmail@hq -> agents/acmecorp/sessionmail/`).
    assert _seat.regen_maps(f.agents).startswith("generator ran"), f.log
    f.log.unlink()
    return f


def as_seat(monkeypatch, seat: str, sid: str | None) -> None:
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", seat)
    if sid:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    else:
        for var in _seat.SESSION_VARS:
            monkeypatch.delenv(var, raising=False)


def sub(*argv: str):
    return CliRunner().invoke(cli, ["subscription", *argv])


def rows(text: str) -> dict[str, str]:
    return dict(line.split("\t", 1) for line in text.splitlines() if "\t" in line)


# ── add ───────────────────────────────────────────────────────────────────

def test_add_defaults_to_the_session_rebuilds_and_prints_the_row(fleet):
    """Scenario "Add is heard at once" and "Any session may hear a project
    box": a worker's add lands in ITS session's folder, the map is rebuilt
    before the verb returns, and the row names the session's box, no seat."""
    done = sub("add", "atomnote")
    assert done.exit_code == 0, done.output
    assert fleet.subscribed(SID) == ["atomnote"]
    assert fleet.session_log(SID)[-1]["argv"] == "message subscription add atomnote"
    assert str(fleet.session_live(SID)) in done.output                # the verbs' file (3.4)
    assert fleet.runs() == 1
    assert "--valias-only" in fleet.log.read_text()
    assert rows(fleet.valias)[f"atomnote@{HOST}"] == f"atomnote@{HOST}, {SID}@{HOST}"
    assert f"row: atomnote@{HOST} -> atomnote@{HOST}, {SID}@{HOST}" in done.output
    assert fleet.files() == [f"sessions/{SID}#atomnote"]


def test_a_why_is_kept_in_the_note(fleet):
    done = sub("add", "voicemode", "--why", "watching the  build\nfor Judy")
    assert done.exit_code == 0, done.output
    assert fleet.session_log(SID)[-1]["why"] == "watching the build for Judy"


@pytest.mark.parametrize("box,stored", [("st/atomnote", "st--atomnote"),
                                        ("voicemode+vm-2266", "voicemode+vm-2266"),
                                        ("atomnote+kaizen", "atomnote+kaizen")])
def test_a_store_project_or_a_plus_form_is_stored_as_the_codec_writes_it(fleet, box, stored):
    """Since 3.3b a session's map keys the box itself (`st/atomnote`); the
    codec's `--` form is a file name's, and only the generator's sub_address
    applies it, to a map key as to a file."""
    done = sub("add", box)
    assert done.exit_code == 0, done.output
    assert fleet.subscribed(SID) == [box]
    assert not fleet.session_file(SID, stored).exists()
    key = _subscribe.address_key(box)
    assert rows(fleet.valias)[f"{key}@{HOST}"].endswith(f", {SID}@{HOST}")


def test_adding_it_twice_changes_nothing_and_does_not_rebuild(fleet):
    """Scenario "Adding it twice"."""
    assert sub("add", "atomnote").exit_code == 0
    path = fleet.session_live(SID)
    before = path.read_text()
    done = sub("add", "atomnote")
    assert done.exit_code == 0, done.output
    assert "already subscribed" in done.output
    assert fleet.runs() == 1
    assert path.read_text() == before
    assert len(fleet.session_log(SID)) == 1


@pytest.mark.parametrize("body", ["# the old pager's\nquery: to:atomnote and date:1d..\n", None])
def test_a_saved_query_or_unreadable_file_is_not_already_subscribed(fleet, monkeypatch, body):
    """hq today: `seats/ivan/subscriptions/atomnote` is an OLD SAVED QUERY, and
    tasks 6.1 is `message subscription add atomnote --seat` as ivan. The
    generator skips such a file (and an unreadable one), so there is no row:
    add must not answer "already subscribed", and must not overwrite it
    either (6.3: no agent deletes those). Refused, exit 2, the file named,
    the file untouched, no rebuild."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    old = fleet.seat_file("ivan", "atomnote")
    old.parent.mkdir(parents=True)
    if body is None:
        old.mkdir()                               # a name add cannot read as a file
    else:
        old.write_text(body)
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == EX_REFUSED, done.output
    assert "already subscribed" not in done.output
    assert str(old) in done.stderr
    assert fleet.runs() == 0
    if body is not None:
        assert old.read_text() == body
    assert f"atomnote@{HOST}" not in rows(fleet.valias)


def _shape(path: Path, shape: str, outside: Path) -> None:
    """Put a thing of SHAPE at PATH: what a subscription folder may hold."""
    path.parent.mkdir(parents=True, exist_ok=True)
    real, query = outside / "real", outside / "query"
    if shape == "file":
        path.write_text("# a subscription\n")
    elif shape == "empty":
        path.write_text("")
    elif shape == "query":
        path.write_text("query: to:atomnote and date:1d..\n")
    elif shape == "folder":
        path.mkdir()
    elif shape == "fifo":
        os.mkfifo(path)
    elif shape == "dangling":
        path.symlink_to(outside / "nothing-here")
    elif shape == "link-to-file":
        path.symlink_to(real)
    elif shape == "link-to-query":
        path.symlink_to(query)
    elif shape == "unreadable":
        path.write_text("# a subscription\n")
        path.chmod(0)
    else:
        raise AssertionError(shape)


SHAPES = ["file", "empty", "query", "folder", "fifo", "dangling", "link-to-file",
          "link-to-query", "unreadable"]


def test_add_counts_as_there_exactly_what_the_generator_routes(fleet, tmp_path):
    """"Already subscribed" is a claim about the MAP, so add's test for a
    name already held must be the generator's, shape for shape: a symlink to
    a plain file IS routed (bash `-f` and Python `is_file` both follow it),
    so add must call it subscribed, not refuse it as "not a subscription";
    a query, a folder, a fifo, a dangling or unreadable name is skipped, so
    add must refuse (8db3b2d refused the routed link: SM-374 do-006 rework)."""
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-0 file")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "real").write_text("# a subscription, kept elsewhere\n")
    (outside / "query").write_text("query: to:atomnote\n")
    paths = []
    for i, shape in enumerate(SHAPES):
        f = fleet.seat_file(f"z{i:02d}", "atomnote")
        _shape(f, shape, outside)
        paths.append(f)
    done = subprocess.run([os.environ["REAL_GEN"], "--print-valias", "--host", HOST,
                           "--root", str(fleet.agents.parent)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    dest = rows(done.stdout).get(f"atomnote@{HOST}", "")
    table = []
    for i, (shape, path) in enumerate(zip(SHAPES, paths)):
        generator = f"z{i:02d}@{HOST}" in dest
        try:
            _subscribe.existing(path)
            python = True
        except _subscribe.Denied:
            python = False
        table.append((shape, generator, python))
    paths[SHAPES.index("unreadable")].chmod(0o644)
    assert [t for t in table if t[1] != t[2]] == [], table
    assert {t[1] for t in table} == {True, False}


def test_a_link_to_a_subscription_is_already_subscribed(fleet, monkeypatch, tmp_path):
    """The verb, end to end, on the one shape 8db3b2d got wrong: the map
    copies a link to a plain file, so add says "already subscribed", exit 0,
    leaves the link alone and does not rebuild."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    real = tmp_path / "kept-elsewhere"
    real.write_text("# a subscription\n")
    link = fleet.seat_file("ivan", "atomnote")
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == 0, done.output
    assert "already subscribed" in done.output
    assert fleet.runs() == 0
    assert link.is_symlink() and os.readlink(link) == str(real)


@pytest.mark.parametrize("box", ["nosuch", "nosuch+tag", "st/nosuch", "loose/pr"])
def test_a_box_that_does_not_exist_is_refused(fleet, box):
    """Scenario "A box that does not exist is refused": exit 2, the box
    named, no file, no rebuild. `loose/pr` is a maildir, but under no store
    marker: the generator would never route it, so add must not either."""
    done = sub("add", box)
    assert done.exit_code == EX_REFUSED
    assert box in done.stderr
    assert fleet.files() == []
    assert fleet.runs() == 0


@pytest.mark.parametrize("box", ["atomnote+memory", "ivan+MEMORY", "atomnote+", "+x",
                                 "atomnote+a+b", "atomnote+a:b", "st/atomnote+x", "a/b/c"])
def test_an_address_the_generator_refuses_is_refused(fleet, box):
    done = sub("add", box)
    assert done.exit_code == EX_REFUSED, done.output
    assert fleet.files() == []
    assert fleet.runs() == 0


def test_no_session_id_is_refused_naming_where_it_looked(fleet, monkeypatch):
    as_seat(monkeypatch, "worker", None)
    done = sub("add", "atomnote")
    assert done.exit_code == EX_REFUSED
    assert "CLAUDE_CODE_SESSION_ID" in done.stderr
    assert fleet.files() == []


def test_a_session_id_postfix_would_not_keep_is_refused(fleet, monkeypatch):
    """The generator skips a session folder whose id holds such a character
    (`vmailbox-gen` subscription_records): a file there would never route."""
    as_seat(monkeypatch, "worker", "s1, judy")
    done = sub("add", "atomnote")
    assert done.exit_code == EX_REFUSED
    assert "postfix would keep" in done.stderr
    assert fleet.files() == []


# ── D5: who may change whose ──────────────────────────────────────────────

def test_a_worker_cannot_change_ivans_subscriptions(fleet):
    """Scenario "A worker cannot change Ivan's subscriptions"."""
    for argv in (["add", "atomnote", "--for", "ivan"],
                 ["add", "atomnote", "--for", "ivan", "--seat"],
                 ["add", "atomnote", "--for", IVAN_SID]):
        done = sub(*argv)
        assert done.exit_code == EX_REFUSED, argv
        # SM-727: the gate's words come from config (here: the SC6 host's)
        assert "a privileged seat (here: ivan, judy" in done.stderr
    assert fleet.files() == []
    assert fleet.runs() == 0


def test_a_worker_cannot_subscribe_its_seat(fleet):
    """Scenario "A worker cannot subscribe its seat": the refusal names the
    route, a request by mail to a privileged seat (SM-727: ivan, from config)."""
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == EX_REFUSED
    assert "request it by mail, from a privileged seat" in done.stderr
    assert "message send ivan" in done.stderr
    assert fleet.files() == []


@pytest.mark.parametrize("box", ["ivan", "ivan+kaizen", "voicemode+super", OTHER])
def test_a_worker_cannot_copy_another_seats_mail(fleet, box):
    """Scenario "A worker cannot copy Ivan's mail" (RULED S6), and its kin:
    a tag of Ivan's box, a declared plus seat (`<home>/seats/<box>/`), and
    another session's own box."""
    done = sub("add", box)
    assert done.exit_code == EX_REFUSED, done.output
    assert "that seat's to grant" in done.stderr
    assert fleet.files() == []
    assert fleet.runs() == 0


@pytest.mark.parametrize("box", ["worker", "worker+notes", SID, "st", "voicemode+vm-2274"])
def test_a_callers_own_boxes_and_open_boxes_are_allowed(fleet, box):
    """Its own seat's box (and a tag of it), its own session's, and project
    or store boxes: none is another seat's to grant. `st` is a store box
    here only if it resolves; it has no maildir, so it is refused as
    unresolvable, never as someone's."""
    done = sub("add", box)
    if box == "st":
        assert done.exit_code == EX_REFUSED and "no such box" in done.stderr
        return
    assert done.exit_code == 0, done.output


def test_judy_subscribes_her_seat(fleet, monkeypatch):
    """Scenario "Judy subscribes her seat": the seat's folder, and the row
    copies to `judy@`, which reaches every session she has."""
    as_seat(monkeypatch, "judy", JUDY_SID)
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == 0, done.output
    assert fleet.seat_file("judy", "atomnote").is_file()
    assert rows(fleet.valias)[f"atomnote@{HOST}"] == f"atomnote@{HOST}, judy@{HOST}"
    assert rows(fleet.valias)[f"judy@{HOST}"] == f"judy@{HOST}, {JUDY_SID}@{HOST}"


def test_ivan_may_act_for_a_seat_or_a_session_and_copy_any_box(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert sub("add", "atomnote", "--for", "worker", "--seat").exit_code == 0
    assert fleet.seat_file("worker", "atomnote").is_file()
    assert sub("add", "voicemode", "--for", OTHER).exit_code == 0
    assert fleet.subscribed(OTHER) == ["voicemode"]
    assert sub("add", "worker").exit_code == 0                 # another seat's own: hers to do
    assert fleet.subscribed(IVAN_SID) == ["worker"]


def test_for_a_name_that_is_not_a_session_says_to_add_seat(fleet, monkeypatch):
    """`--for ivan` without --seat names a SESSION; a seat name is never
    quietly taken for one."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    done = sub("add", "atomnote", "--for", "worker")
    assert done.exit_code == EX_REFUSED
    assert "add --seat" in done.stderr
    assert fleet.files() == []


# ── remove ────────────────────────────────────────────────────────────────

def test_remove_deletes_the_file_rebuilds_and_the_row_goes(fleet):
    assert sub("add", "atomnote").exit_code == 0
    done = sub("remove", "atomnote")
    assert done.exit_code == 0, done.output
    assert fleet.files() == []
    assert fleet.runs() == 2
    assert f"atomnote@{HOST}" not in rows(fleet.valias)
    assert f"row: none for atomnote@{HOST}" in done.output


def test_remove_keeps_the_other_subscribers_row(fleet, monkeypatch):
    assert sub("add", "atomnote").exit_code == 0
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert sub("add", "atomnote").exit_code == 0
    as_seat(monkeypatch, "worker", SID)
    done = sub("remove", "atomnote")
    assert done.exit_code == 0, done.output
    assert f"row: atomnote@{HOST} -> atomnote@{HOST}, {IVAN_SID}@{HOST}" in done.output


def test_removing_what_is_not_there_changes_nothing_and_does_not_rebuild(fleet):
    done = sub("remove", "atomnote")
    assert done.exit_code == 0, done.output
    assert "not subscribed" in done.output
    assert fleet.runs() == 0


def test_remove_never_touches_an_old_saved_query(fleet, monkeypatch):
    """The pager's old saved queries share the seat folder until tasks 6.3;
    `remove` removes only what `add` could have written."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    old = fleet.seat_file("ivan", "seat")
    old.parent.mkdir(parents=True)
    old.write_text("query: to:$SEAT\n")
    done = sub("remove", "seat", "--seat")
    assert done.exit_code == EX_REFUSED
    assert "saved query" in done.stderr
    assert old.is_file()
    assert fleet.runs() == 0


def test_a_worker_cannot_remove_a_seat_subscription(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert sub("add", "atomnote", "--for", "worker", "--seat").exit_code == 0
    as_seat(monkeypatch, "worker", SID)
    done = sub("remove", "atomnote", "--seat")
    assert done.exit_code == EX_REFUSED
    assert "request it by mail, from a privileged seat" in done.stderr
    assert fleet.seat_file("worker", "atomnote").is_file()


# ── the rebuild ───────────────────────────────────────────────────────────

def test_a_failed_rebuild_keeps_the_file_and_exits_3(fleet, monkeypatch, tmp_path):
    monkeypatch.setenv(_seat.GEN_VAR, str(tmp_path / "gone"))
    done = sub("add", "atomnote")
    assert done.exit_code == EX_NOT_REBUILT
    assert "NOT rebuilt" in done.stderr
    assert fleet.subscribed(SID) == ["atomnote"]


# ── a home that will not take the write (do-011 rework) ─────────────────

def _refused_cleanly(done, *words: str) -> None:
    """Exit 2 per the help's table, ONE stderr line naming what was not
    written, where, and the OS's reason; no traceback, nothing on stdout."""
    assert done.exit_code == EX_REFUSED, (done.output, done.exception)
    assert isinstance(done.exception, SystemExit), done.exception
    assert "Traceback" not in done.output
    lines = done.stderr.splitlines()
    assert len(lines) == 1, lines
    for word in words:
        assert word in lines[0], lines[0]
    assert done.stdout == ""


def test_add_into_a_file_squatting_the_folder_is_refused_cleanly(fleet):
    """A file holds the session layer's `settings` name: `mkdir` raises
    FileExistsError. The verb names the box, the path and the error and
    exits 2, rather than dying with a traceback (do-011 review, observation
    2; the folder is the layer's since 3.3b)."""
    squat = fleet.session_map(SID).parent
    squat.write_text("not a folder")
    done = sub("add", "atomnote")
    _refused_cleanly(done, "atomnote", "NOT written", str(squat), "File exists")
    assert squat.read_text() == "not a folder"
    assert fleet.runs() == 0


def test_add_into_a_read_only_home_is_refused_cleanly(fleet):
    if os.geteuid() == 0:
        pytest.skip("root writes into a mode-0500 folder")
    folder = session_dir(SID, fleet.userhome)       # the layer's parent (3.3b)
    folder.chmod(0o500)
    try:
        done = sub("add", "atomnote")
    finally:
        folder.chmod(0o755)
    _refused_cleanly(done, "atomnote", "NOT written", str(folder), "Permission denied")
    assert fleet.files() == []
    assert fleet.runs() == 0


def test_remove_from_a_read_only_folder_is_refused_cleanly(fleet):
    """remove shares the verb: an unlink the home refuses is named the same
    way, and the file and the map stay as they were."""
    if os.geteuid() == 0:
        pytest.skip("root unlinks from a mode-0500 folder")
    assert sub("add", "atomnote").exit_code == 0
    path = fleet.session_live(SID)                       # mail.d/, the verbs' (3.4)
    path.parent.chmod(0o500)
    try:
        done = sub("remove", "atomnote")
    finally:
        path.parent.chmod(0o755)
    _refused_cleanly(done, "atomnote", "NOT removed", str(path.parent), "Permission denied")
    assert fleet.subscribed(SID) == ["atomnote"]
    assert fleet.runs() == 1


@pytest.mark.parametrize("fails", ["write_text", "replace"])
def test_a_failed_write_leaves_no_hidden_temp_file(tmp_path, monkeypatch, fails):
    """`write` stages under a hidden `.BOX.<pid>` name, then renames. A
    failure at either step (a full disk mid-write; a rename the folder
    refuses) must take the temp file with it: "nothing changed" is the
    refusal's promise (SM-374 do-011 review 2, observation 2; closed at
    verify-001)."""
    target = tmp_path / "subscriptions" / "atomnote"

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    if fails == "write_text":
        real = Path.write_text

        def partial(self, data, *args, **kwargs):
            real(self, data[:3], *args, **kwargs)   # some bytes land, then it fails
            boom()

        monkeypatch.setattr(Path, "write_text", partial)
    else:
        monkeypatch.setattr(_subscribe.os, "replace", boom)
    with pytest.raises(OSError):
        _subscribe.write(target, "# added by a test\n")
    assert sorted(p.name for p in target.parent.iterdir()) == []


def test_a_test_without_a_generator_cannot_reach_the_live_one(tmp_path, monkeypatch):
    """conftest's default: the generator a verb would run is one that does
    not exist, never `vmailbox-gen` on PATH, which writes the LIVE map."""
    gen = os.environ[_seat.GEN_VAR]
    assert not os.path.exists(gen)
    ok, line = _seat.regen_valias(tmp_path)
    assert not ok and "generator not found" in line and gen in line


# ── parity: add refuses exactly what the generator refuses ─────────────────
#
# An added criterion: `_boxes.refusal` is
# the ONE Python copy of `sub_address`, and add's gate is it plus the
# generator's resolution rule. One table, both implementations: the name is
# written as a SEAT subscription of its own seat `zNN`, the real generator
# prints the alias map, and a row naming `zNN@` is the generator's "yes".
# Ivan asks, so D5's ownership gate (not the generator's business) stays out.

PARITY = ["worker", "ivan", "atomnote", "voicemode", "st/atomnote", "st/voicemode",
          "atomnote+kaizen", "ivan+kaizen", "voicemode+vm-2266", "voicemode+super", SID,
          "st", "loose/pr", "nosuch", "nosuch+tag", "st/nosuch", "a/b/c",
          "ivan+memory", "ivan+MeMoRy", "atomnote+", "+x", "ivan+a+b", "ivan+a:b",
          "st/atomnote+x", "st+x/atomnote", "ivan=x", "x.y_z", "st/deep/x"]


def test_add_refuses_exactly_what_the_generator_refuses(fleet, monkeypatch):
    # `st/deep/x` IS a maildir whose parent carries a marker, so only the
    # "more than one '/'" rule can refuse it: resolution alone would not.
    deep = fleet.agents / "st" / "deep"
    _maildir(deep / "x")
    (deep / _boxes.STORE_MARKER).write_text("")
    for i, box in enumerate(PARITY):
        f = fleet.seat_file(f"z{i:02d}", _subscription.file_name(box))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# parity\n")
    done = subprocess.run([os.environ["REAL_GEN"], "--print-valias", "--host", HOST,
                           "--root", str(fleet.agents.parent)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    dests = " ".join(rows(done.stdout).values())
    ivan = _subscribe.Caller(seat=_seat.Seat("ivan", "test", fleet.home / "seats" / "ivan"),
                             session=IVAN_SID, op=_operator.read())   # SM-727: config's ivan
    table = []
    for i, box in enumerate(PARITY):
        generator = f"z{i:02d}@{HOST}" in dests
        try:
            _subscribe.check_box(box, ivan)
            python = True
        except _subscribe.Denied:
            python = False
        table.append((box, generator, python))
    assert [t for t in table if t[1] != t[2]] == [], table
    # And the table is not trivially one-sided: both answers occur.
    assert {t[1] for t in table} == {True, False}


@pytest.mark.parametrize("box", ["ivan+memory", "atomnote+", "a/b/c", "ivan=x", "st+x/pr"])
def test_the_refusal_says_why(box):
    assert _boxes.refusal(box)


@pytest.mark.parametrize("box", ["ivan", "st/atomnote", "voicemode+vm-2266", "x.y_z"])
def test_an_address_has_no_refusal(box):
    assert _boxes.refusal(box) == ""


# ── session-settings 3.2: a SEAT's subscriptions are its map ───────────────
# sessiontools openspec/changes/session-settings, task 3.2; read per owner
# (call 1 (A)); an agent's bare name keeps its old
# folder until 3.8 (call 3 (b)).

def seat_map(seat: str) -> Path:
    return Path(os.environ["HOME"]) / ".session" / "seats" / seat / "settings" / "mail.json"


def seat_log(seat: str) -> list[dict]:
    p = Path(os.environ["HOME"]) / ".session" / "seats" / seat / "log" / "settings.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_a_seat_subscription_is_a_key_in_the_seats_map_and_the_row_follows(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    done = sub("add", "atomnote", "--for", "voicemode+super", "--seat")
    assert done.exit_code == 0, done.output
    assert json.loads(seat_map("voicemode+super").read_text()) == {"subscriptions": {"atomnote": True}}
    assert not fleet.seat_file("voicemode+super", "atomnote").exists(), "never the old folder"
    assert rows(fleet.valias)[f"atomnote@{HOST}"] == f"atomnote@{HOST}, voicemode+super@{HOST}"
    [rec] = seat_log("voicemode+super")
    assert (rec["key"], rec["value"], rec["by"], rec["tool"]) == ("subscriptions.atomnote", True, "ivan", "mail")
    assert "message subscription add atomnote --seat --for voicemode+super" == rec["argv"]


def test_a_repeat_add_to_a_seat_map_changes_nothing(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert sub("add", "atomnote", "--for", "voicemode+super", "--seat").exit_code == 0
    runs, before = fleet.runs(), seat_map("voicemode+super").read_bytes()
    again = sub("add", "atomnote", "--for", "voicemode+super", "--seat")
    assert again.exit_code == 0 and "already subscribed" in again.output
    assert fleet.runs() == runs, "nothing changed, so no rebuild"
    assert seat_map("voicemode+super").read_bytes() == before
    assert len(seat_log("voicemode+super")) == 1


def test_removing_a_seats_last_box_removes_its_file_and_its_row(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert sub("add", "atomnote", "--for", "voicemode+super", "--seat").exit_code == 0
    done = sub("remove", "atomnote", "--for", "voicemode+super", "--seat")
    assert done.exit_code == 0, done.output
    assert f"unsubscribed seat voicemode+super from atomnote: {seat_map('voicemode+super')}" in done.output
    assert not seat_map("voicemode+super").exists(), "an empty map is never written"
    assert f"atomnote@{HOST}" not in rows(fleet.valias)
    assert [r["value"] for r in seat_log("voicemode+super")] == [True, None]
    again = sub("remove", "atomnote", "--for", "voicemode+super", "--seat")
    assert again.exit_code == 0 and "is not subscribed" in again.output


def test_removing_one_box_keeps_the_rest_of_the_seats_file(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    p = seat_map("voicemode+super")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"rules": {"10": "ride from:x"}, "subscriptions": {"atomnote": True}}))
    assert sub("remove", "atomnote", "--for", "voicemode+super", "--seat").exit_code == 0
    assert json.loads(p.read_text()) == {"rules": {"10": "ride from:x"}}


def test_an_agents_bare_name_keeps_its_old_folder(fleet, monkeypatch):
    """`judy` is an agent's bare name, not a seat: until 3.8
    reads the agent layer, its subscriptions stay in the old folder."""
    as_seat(monkeypatch, "judy", JUDY_SID)
    assert sub("add", "atomnote", "--seat").exit_code == 0
    assert fleet.seat_file("judy", "atomnote").is_file()
    assert not seat_map("judy").exists()


def test_a_box_set_by_a_file_these_verbs_do_not_write_is_refused(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    d = seat_map("voicemode+super").parent / "mail.d"
    d.mkdir(parents=True)
    (d / "90-judy.json").write_text(json.dumps({"subscriptions": {"atomnote": False}}))
    done = sub("add", "atomnote", "--for", "voicemode+super", "--seat")
    assert done.exit_code == 2
    assert "90-judy.json" in done.output and "nothing written" in done.output
    assert not seat_map("voicemode+super").exists()
    assert fleet.runs() == 0


def test_a_seat_map_that_is_not_json_is_refused_and_left_alone(fleet, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    p = seat_map("voicemode+super")
    p.parent.mkdir(parents=True)
    p.write_text("{ not json")
    done = sub("add", "atomnote", "--for", "voicemode+super", "--seat")
    assert done.exit_code == 2 and "not JSON" in done.output
    assert p.read_text() == "{ not json"


def test_a_seat_box_file_left_in_the_old_folder_routes_nothing(fleet, monkeypatch):
    """No symlink at the old path (call 2): a box file in a SEAT's old folder
    is never converted by the readers. It routes nothing, and the generator
    names `pager settings migrate`."""
    f = fleet.seat_file("voicemode+super", "atomnote")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# added by hand\n")
    assert _seat.regen_maps(fleet.agents).startswith("generator ran")
    assert f"atomnote@{HOST}" not in rows(fleet.valias)
