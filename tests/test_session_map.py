"""session-settings 3.3b: a SESSION's address and subscriptions live in its
layer, `~/.session/sessions/<sid>/settings/mail.json`.

- The conversion is ONE function, `_migrate.convert_session`: box files in
  the old `~/.sessionmail/sessions/<sid>/subscriptions/` become `true` keys,
  the `address` file the `address` key, each converted file is removed, and
  the layer's own value wins over a file. `pager settings migrate` runs it
  for every LIVE session (Open 2: live only); `pager listen` for its own.
- The generator reads the layer, never the old files: an old box file is
  skipped as never converted, an old `address` file is counted on stderr,
  and a layer that cannot be read writes no plus row and is named.

HOME and SESSIONMAIL_HOME are conftest's sandbox unless a test sets them."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _seatmap import (session_declare, session_log, session_map_path, session_subscribe,
                      session_values)
from sessionmail import _migrate, _subscription as sub
from test_pager_subscribe_at_birth import NEW, banner, fresh, listen  # noqa: F401
from test_message_subscription import HOST as FLEET_HOST, fleet, rows  # noqa: F401
from test_postfix_vmailbox_gen import HOST, _rows, _valias, generator, needs_jq  # noqa: F401

LIVE = "5e550000-1111-2222-3333-444455556666"
GONE = "9a9e0000-1111-2222-3333-444455556666"
NOTE = "# added by worker 2030-12-08 (message subscription add)\n# why: the build\n"


def smhome() -> Path:
    return Path(os.environ["SESSIONMAIL_HOME"])


def old(sid: str, name: str, body: str = NOTE) -> Path:
    """A file in SID's OLD folder, as a session before 3.3b wrote it."""
    p = smhome() / "sessions" / sid / ("address" if name == "address" else f"subscriptions/{name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ── the conversion ────────────────────────────────────────────────────────

def test_box_files_and_the_address_become_keys_and_the_files_go():
    a = old(LIVE, "atomnote")
    b = old(LIVE, "work--demo")
    q = old(LIVE, "seat", "query: to:worker\n")                 # a saved query: left
    addr = old(LIVE, "address", "SessionMail+SM-1.Foreman\n")
    r = _migrate.migrate(live={LIVE}, by="judy", argv="pager settings migrate")
    assert r.sessions == 1 and r.changed
    assert {(s, x) for s, x, _ in r.session_moved} == {(LIVE, "atomnote"),
                                                       (LIVE, "work/demo")}
    assert [(s, x) for s, x, _ in r.addresses] == [(LIVE, "sessionmail+sm-1.foreman")]
    assert session_values(LIVE) == {"subscriptions": {"atomnote": True,
                                                      "work/demo": True},
                                    "address": "sessionmail+sm-1.foreman"}
    assert not a.exists() and not b.exists() and not addr.exists() and q.is_file()
    log = {x["key"]: x for x in session_log(LIVE)}
    assert log["subscriptions.atomnote"]["why"].startswith(f"migrated from {a}")
    assert "why: the build" in log["subscriptions.atomnote"]["why"]
    assert log["address"]["value"] == "sessionmail+sm-1.foreman"
    out = "\n".join(_migrate.lines(r))
    assert "live sessions: 1 read; 2 box(es) and 1 address(es) moved into their maps" in out
    assert f"moved  {LIVE}  address sessionmail+sm-1.foreman" in out
    again = _migrate.migrate(live={LIVE})
    assert not again.touched and "nothing to move" in _migrate.lines(again)[-1]


def test_a_session_that_is_not_live_is_never_read():
    f = old(GONE, "atomnote")
    g = old(GONE, "address", "a+b\n")
    r = _migrate.migrate(live={LIVE})
    assert not r.touched and f.is_file() and g.is_file()
    assert not session_map_path(GONE).exists()


def test_the_layer_wins_over_a_file_and_the_file_is_left_and_named():
    session_declare(LIVE, "a+kept")
    p = session_map_path(LIVE)
    data = json.loads(p.read_text())
    data["subscriptions"] = {"atomnote": False}
    p.write_text(json.dumps(data))
    f = old(LIVE, "atomnote")
    g = old(LIVE, "address", "a+other\n")
    r = _migrate.migrate(live={LIVE})
    assert not r.changed and f.is_file() and g.is_file()
    out = "\n".join(_migrate.lines(r))
    assert f"{LIVE}'s map sets atomnote to False, and the map wins" in out
    assert f"{LIVE}'s map sets address to 'a+kept', not 'a+other', and the map wins" in out


def test_what_the_layer_already_holds_only_removes_the_file():
    session_subscribe(LIVE, "atomnote")
    session_declare(LIVE, "a+b")
    f = old(LIVE, "atomnote")
    g = old(LIVE, "address", "A+B\n")
    r = _migrate.migrate(live={LIVE})
    assert not r.changed and r.touched
    assert not f.exists() and not g.exists()
    assert session_log(LIVE) == []                     # nothing changed, nothing logged


def test_a_dry_run_says_the_same_and_writes_nothing():
    f = old(LIVE, "atomnote")
    r = _migrate.migrate(live={LIVE}, dry_run=True)
    assert [x for _s, x, _p in r.session_moved] == ["atomnote"] and not r.changed
    assert f.is_file() and not session_map_path(LIVE).exists()
    assert "would move" in "\n".join(_migrate.lines(r))


# ── the reader ────────────────────────────────────────────────────────────

def test_the_reader_routes_the_map_and_names_an_old_box_file_as_not_converted():
    session_subscribe(LIVE, "kith")
    f = old(LIVE, "atomnote")
    scan = sub.scan(live={LIVE})
    assert scan.pairs == {(LIVE, "kith")}
    assert dict(scan.skipped)[f] == sub.NOT_CONVERTED_SESSION
    assert "pager settings migrate" in sub.NOT_CONVERTED_SESSION
    assert "; " not in sub.NOT_CONVERTED_SESSION     # the generator's stderr list splits on it


# ── the generator ─────────────────────────────────────────────────────────

@needs_jq
def test_an_old_address_file_writes_no_row_and_is_counted(generator, tmp_path):
    home = tmp_path / "sessionmail"
    (home / "sessions" / "s1").mkdir(parents=True)
    (home / "sessions" / "s1" / "address").write_text("sessionmail+sm-1.foreman\n")
    done = _valias(generator, tmp_path, [{"session_id": "s1", "agent": "foreman"}], {}, ["s1"])
    assert done.returncode == 0, done.stderr
    assert "sessionmail+sm-1.foreman@" not in done.stdout
    assert "1 session(s) with an old address file, never converted" in done.stderr
    assert "pager settings migrate" in done.stderr and " s1" in done.stderr


@needs_jq
def test_the_layer_address_is_trimmed_folded_and_routed(generator, tmp_path):
    done = _valias(generator, tmp_path, [{"session_id": "s1", "agent": "foreman"}],
                   {"s1": "  SessionMail+SM-1.Foreman "}, ["s1"])
    assert done.returncode == 0, done.stderr
    rows_ = _rows(done.stdout)
    assert rows_[f"sessionmail+sm-1.foreman@{HOST}"] == f"sessionmail@{HOST}, s1@{HOST}"


@needs_jq
def test_a_layer_that_cannot_be_read_writes_no_row_and_is_named(generator, tmp_path):
    p = session_map_path("s1", tmp_path / "home")
    p.parent.mkdir(parents=True)
    p.write_text('{"address": "a+b",')                     # torn
    done = _valias(generator, tmp_path, [{"session_id": "s1", "agent": "foreman"}], {}, ["s1"])
    assert done.returncode == 0, done.stderr
    assert "a+b@" not in done.stdout
    assert "1 session layer(s) could not be read" in done.stderr and " s1" in done.stderr


@needs_jq
def test_a_dead_sessions_layer_is_never_read(generator, tmp_path):
    session_declare("s2", "a+b", tmp_path / "home")
    session_subscribe("s2", "s1", home=tmp_path / "home")
    done = _valias(generator, tmp_path, [{"session_id": "s1", "agent": "foreman"},
                                         {"session_id": "s2", "agent": "foreman",
                                          "alive": False}], {}, ["s1", "s2"])
    assert done.returncode == 0, done.stderr
    assert "a+b@" not in done.stdout and "s2@" not in done.stdout


# ── pager listen converts its own session ─────────────────────────────────

def test_the_listen_converts_its_own_sessions_old_files_and_routes_them(fresh):
    f = fresh.session_file(NEW, "atomnote")
    f.parent.mkdir(parents=True)
    f.write_text(NOTE)
    result = listen()
    assert result.exit_code == 124, result.output
    assert fresh.subscribed(NEW) == ["atomnote"]
    assert not f.exists()
    assert any("3.3b moved" in line and "atomnote" in line
               for line in banner(result, "state")), result.stderr
    assert rows(fresh.valias)[f"atomnote@{FLEET_HOST}"].endswith(f", {NEW}@{FLEET_HOST}")
    again = listen()
    assert not any("3.3b" in line for line in banner(again, "state")), again.stderr
