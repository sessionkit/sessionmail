"""The pager reads no subscription files (message-subscriptions 4.2, 4.3).

`openspec/changes/message-subscriptions`, tasks 4.2 and 4.3, and the two
REMOVED pager requirements *A subscription belongs to the seat* and *A
subscription is a query*: `pager subscriptions`, `BORN`, `write_born` and
`migrate_born` retire; `pager status` stops reading subscription files; and
`pager --help` stops saying listen "reads every file in <seat>/subscriptions/
on every run" (no longer true).

The resident view needs SOMETHING to read once the files are gone. The spec's
own migration note says why the born files were redundant: "a seat already
hears its own address through its generated alias row" and "the listener
watches the session box". Both land in THIS SESSION'S BOX, so that is what
`pager status` reads now - the box `pager listen` watches, not-done mail
only, from the files themselves, with no index.

Hermetic: a tmp mail root, a tmp sessionmail home, a fake notmuch that
records any argv it is handed (so "asked nothing" is measured, not assumed).
Fixtures are `alpha` and `beta` at `example.invalid`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _notmuch, _seat, pager
from sessionmail import _settings
from sessionmail.pager import cli

from fakes import forest, write_fake

EX_PAGE, EX_COULD_NOT_LOOK, EX_NOTHING = 0, 2, 3
SESSION = "sess-a"
_n = 0


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    root.mkdir()
    home = tmp_path / "sessionmail"
    fake = write_fake(tmp_path / "fake-notmuch")
    payload = tmp_path / "answer.json"
    payload.write_text(json.dumps(forest()))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(fake))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_JSON", str(payload))
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "is_listening", lambda seat, session, ps=None: os.getpid())
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)

    class Rig:
        def __init__(self) -> None:
            self.root = root
            self.home = home
            self.log = tmp_path / "argv.log"
            self.seat = _seat.resolve()

        def box(self, name: str = SESSION):
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def deliver(self, mid: str, subject: str, to: str = "alpha", name: str = SESSION) -> None:
            global _n
            _n += 1
            b = self.box(name)
            fname = f"{1923171200 + _n}.M{_n:06d}P{os.getpid()}.test"
            (b / "tmp" / fname).write_bytes(
                f"X-Original-To: {to}@example.invalid\r\n"
                f"From: beta@example.invalid\r\nTo: {to}@example.invalid\r\n"
                f"Subject: {subject}\r\nMessage-ID: <{mid}>\r\n"
                f"Date: Tue, 01 Jan 2030 12:00:{_n % 60:02d} +0000\r\n\r\nbody\r\n".encode())
            os.rename(b / "tmp" / fname, b / "new" / fname)

        def plant_subscriptions(self) -> None:
            """What 77 seats hold on hq today: the two born files, and one
            file the OLD reader refused outright (no `query:` line)."""
            d = self.seat.root / "subscriptions"
            d.mkdir(parents=True, exist_ok=True)
            (d / "seat").write_text('query: to:"$ADDRESS"\nroute: pager,ride-along\n')
            (d / "session").write_text("query: to:$SESSION\nroute: pager\n")
            (d / "atomnote").write_text("# a new-format file: one line naming a box\n")

        def asked_notmuch(self) -> bool:
            return self.log.exists() and bool(self.log.read_text().strip())

    return Rig()


def status(*argv: str):
    return CliRunner().invoke(cli, ["status", *argv], prog_name="pager")


def mark_done(rig, mid: str) -> None:
    _seat.write_mark(rig.seat, mid, "done", SESSION, "2030-07-07T22:34:56+10:00", "test")


# ── 4.2: the four are gone ───────────────────────────────────────────────

def test_pager_subscriptions_is_no_longer_a_verb():
    result = CliRunner().invoke(cli, ["subscriptions"], prog_name="pager")
    assert result.exit_code == 2 and "No such command" in result.output
    assert "subscriptions" not in cli.list_commands(None)


@pytest.mark.parametrize("name", ["BORN", "write_born", "migrate_born"])
def test_the_born_files_and_their_migration_are_gone(name):
    assert not hasattr(_seat, name), f"_seat.{name} should be retired"


def test_the_first_listen_on_a_bare_seat_writes_no_subscription_file(rig):
    rig.box()
    CliRunner().invoke(cli, ["listen", "--timeout", "0"], prog_name="pager")
    assert not (rig.seat.root / "subscriptions").exists()


# ── 4.2: `pager status` reads no subscription file ───────────────────────

def test_status_reads_no_subscription_file_and_asks_no_index(rig):
    """The old reader exited 2 on `atomnote` (no `query:` line) and handed
    `to:$SESSION` to notmuch. Neither may happen now."""
    rig.plant_subscriptions()
    rig.deliver("m1@example.invalid", "from the box")
    result = status()
    assert result.exit_code == EX_PAGE, result.output
    assert "from the box" in result.output
    assert not rig.asked_notmuch()


def test_status_needs_no_subscriptions_directory(rig):
    """A seat with no `subscriptions/` was "deaf, not quiet" (exit 2). The
    directory no longer means anything to the pager."""
    rig.deliver("m1@example.invalid", "hello")
    assert not (rig.seat.root / "subscriptions").exists()
    assert status().exit_code == EX_PAGE


def test_status_shows_this_sessions_box_not_another_one(rig):
    rig.deliver("mine@example.invalid", "for this session")
    rig.deliver("theirs@example.invalid", "for another session", name="sess-b")
    out = status().output
    assert "for this session" in out and "for another session" not in out


def test_the_line_names_the_address_that_delivered_it(rig):
    """With no subscription name to tag a line, the tag is the envelope
    recipient (`X-Original-To`): a subscribed box's mail says which box."""
    rig.deliver("m1@example.invalid", "a note", to="atomnote")
    assert "[atomnote]" in status().output


def test_done_quiets_a_line_and_read_is_not_terminal(rig):
    rig.deliver("m1@example.invalid", "one")
    _seat.write_mark(rig.seat, "m1@example.invalid", "read", SESSION,
                     "2030-07-07T22:34:56+10:00", "test")
    assert "one" in status().output                 # read is not terminal
    mark_done(rig, "m1@example.invalid")
    result = status()
    assert result.exit_code == EX_NOTHING and "all quiet" in result.output


def test_three_oldest_then_a_count_and_all_shows_every_one(rig):
    for i in range(5):
        rig.deliver(f"m{i}@example.invalid", f"subject {i}")
    lines = status().output.splitlines()
    assert [ln for ln in lines if "subject" in ln] == [
        ln for ln in lines if any(f"subject {i}" in ln for i in range(3))]
    assert any("+2 more" in ln for ln in lines)
    every = status("--all").output
    assert all(f"subject {i}" in every for i in range(5)) and "more" not in every


def test_an_empty_box_is_nothing(rig):
    rig.box()
    result = status()
    assert result.exit_code == EX_NOTHING and "all quiet" in result.output


def test_a_session_with_no_box_yet_is_could_not_look_and_names_listen(rig):
    """A ride-along never creates what it measures: the box is born by the
    session's first `pager listen`, so status says so, and makes nothing."""
    result = status()
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert "pager listen" in result.output
    assert not (rig.root / SESSION).exists()


def test_no_session_id_is_could_not_look(rig, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    result = status()
    assert result.exit_code == EX_COULD_NOT_LOOK and "session" in result.output


def test_status_writes_nothing(rig):
    rig.deliver("m1@example.invalid", "hello")
    before = sorted(p for p in rig.home.rglob("*")) if rig.home.exists() else []
    box_before = sorted(p.name for p in (rig.root / SESSION).rglob("*"))
    assert status().exit_code == EX_PAGE
    after = sorted(p for p in rig.home.rglob("*")) if rig.home.exists() else []
    assert after == before
    assert sorted(p.name for p in (rig.root / SESSION).rglob("*")) == box_before


# ── the scoop and the hook no longer gate on the born files ──────────────

def _queued(mid: str, subject: str) -> None:
    q = _seat.ride_dir(None, SESSION) / _seat.QUEUE_NEW
    q.mkdir(parents=True, exist_ok=True)
    (q / f"{int(time.time())}.{mid}.host").write_text(
        f"From: beta@example.invalid\nTo: alpha@example.invalid\nSubject: {subject}\n"
        f"Message-ID: <{mid}>\n\nbody\n")


def test_the_scoop_gives_a_queued_entry_with_no_subscriptions_directory(rig):
    _queued("q1@example.invalid", "queued")
    result = status("--new")
    assert result.exit_code == EX_PAGE and "queued" in result.output
    assert not (rig.seat.root / "subscriptions").exists()


def test_the_hook_scoops_with_no_subscriptions_directory(rig):
    # SM-450 (pager-settings-file): the hooks are OFF until the settings file
    # says "hooks": true, and an off hook scoops nothing. This test is about
    # the born-files gate, so it turns the hooks on first (merge of master
    # into feat/SM-450-pager-settings, refresh-001).
    settings = Path(os.environ["HOME"]) / _settings.RELATIVE
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"version": 1, "hooks": true}\n')
    _queued("q1@example.invalid", "queued for the hook")
    result = CliRunner().invoke(cli, ["hook", "batch"], prog_name="pager",
                                input="{}")
    assert result.exit_code == 0
    assert "queued for the hook" in result.output


def test_a_fresh_session_with_no_subscriptions_directory_is_quiet_not_deaf(rig):
    """It used to exit 2 ("no subscriptions directory") on every tool call
    until the first listen wrote the born files. Now nothing is queued, so
    it is simply quiet; the queue is made on demand (`_seat.queue_new`)."""
    result = status("--new")
    assert result.exit_code == EX_NOTHING, result.output
    assert not (rig.seat.root / "subscriptions").exists()


# ── 4.3: the group help stops making the false claim ─────────────────────

def test_the_group_help_no_longer_says_listen_reads_the_subscriptions():
    page = CliRunner().invoke(cli, ["--help"], prog_name="pager").output
    assert "reads every file in" not in page
    assert "<seat>/subscriptions/" not in page
    assert "born" not in page


@pytest.mark.parametrize("verb", ["status", "listen"])
def test_no_verb_page_names_the_seats_subscription_files(verb):
    page = CliRunner().invoke(cli, [verb, "--help"], prog_name="pager").output
    assert "<seat>/subscriptions/" not in page
