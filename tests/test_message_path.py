"""message path - the maildir a box name resolves to (changes/message-path).

One resolution for every reader: `<root>/<name>` for a flat box, else the
box postfix delivers `<name>@<host>` to - where a project box lives, nested
under its store. Measured Sun 2030-06-09: `message list delta-chat`
refused while `delta-chat@hq` delivered; `path` and `list` now agree."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat
from sessionmail.message import EX_REFUSED, cli

HOST = socket.gethostname().split(".")[0].lower()


def mkbox(d: Path) -> Path:
    for s in ("new", "cur", "tmp"):
        (d / s).mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(r))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)     # no postfix here
    return r


@pytest.fixture
def nested(tmp_path, monkeypatch, root):
    """proj@HOST -> agents/store/proj/ under a base; nothing under <root>/proj."""
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


def run(*args):
    return CliRunner().invoke(cli, ["path", *args])


def test_a_flat_box_prints_its_path_and_nothing_else(root):
    box = mkbox(root / "alpha")
    r = run("alpha")
    assert r.exit_code == 0, r.output
    assert r.stdout == f"{box}\n"


def test_box_defaults_to_the_seat(root):
    box = mkbox(root / "alpha")
    r = run()
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(box)


def test_a_project_box_only_the_map_knows_resolves_and_why_says_via_map(nested):
    r = run("proj", "--why")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(nested)
    assert f"via map proj@{HOST} -> agents/store/proj/" in r.stderr


def test_a_name_nothing_resolves_is_refused_naming_what_was_tried(root):
    r = run("ghost")
    assert r.exit_code == EX_REFUSED
    assert r.stdout == ""
    assert f"no box 'ghost' under {root}" in r.stderr
    assert "ghost@<host>" in r.stderr


def test_list_and_read_open_the_same_project_box_path_found(nested):
    r = CliRunner().invoke(cli, ["list", "proj"])
    assert r.exit_code == 0, r.output
    assert "-- proj: 0 shown" in r.stdout
    r = CliRunner().invoke(cli, ["read", "proj", "1", "--peek"])
    assert r.exit_code == EX_REFUSED
    assert "no message '1' in proj" in r.stderr         # the box opened; it is empty


def test_a_plus_address_resolves_to_its_base_box_and_why_keeps_the_tag(nested):
    """`<box>+<tag>@<host>`: postfix strips the extension at the first `+`
    (recipient_delimiter) and delivers to the base box; the tag is a slot in
    the SAME maildir (specs/addressing). `path` must say what postfix does.
    Measured Sun 2030-06-09: `message path taskmaster+kaizen` refused
    while postfix delivered it to agents/taskmaster/ (SM-242)."""
    r = run("proj+kaizen", "--why")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(nested)
    assert f"via map proj@{HOST} -> agents/store/proj/" in r.stderr
    assert "tag 'kaizen' kept" in r.stderr
    r = CliRunner().invoke(cli, ["list", "proj+kaizen"])   # same resolver, same box
    assert r.exit_code == 0, r.output
    assert "-- proj+kaizen: 0 shown" in r.stdout


def test_a_plus_address_whose_base_is_unknown_is_still_refused(nested):
    r = run("ghost+kaizen")
    assert r.exit_code == EX_REFUSED
    assert r.stdout == ""


def test_a_maildir_path_is_itself_the_answer_and_why_says_path(root, tmp_path):
    box = mkbox(tmp_path / "elsewhere" / "box")
    r = run(str(box), "--why")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(box)
    assert f"path {box}" in r.stderr                      # not "under <root>"


def test_store_slash_project_is_the_nested_layout_under_the_root(root):
    """The form SessionView calls with: it is IN a store and knows the project."""
    box = mkbox(root / "store" / "proj")
    r = run("store/proj", "--why")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(box)
    assert f"under {root}" in r.stderr


def test_a_slashed_name_never_asks_the_map(root, monkeypatch):
    monkeypatch.setattr(_seat, "box_via_map", lambda name: (_ for _ in ()).throw(AssertionError(name)))
    r = run("store/ghost")
    assert r.exit_code == EX_REFUSED
    assert "no box 'store/ghost' under" in r.stderr
