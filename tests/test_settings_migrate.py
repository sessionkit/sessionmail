"""session-settings 3.2 (part 2): `pager settings migrate` carries a seat's
old box files into its `mail.json` map (`_migrate`). HOME and
SESSIONMAIL_HOME are conftest's sandbox."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sessionmail import _layer, _migrate, _subscription as sub

from _seatmap import seat_map_path, seat_subscribe

QUERY = "query: to:{seat}\n"


def smhome() -> Path:
    return Path(os.environ["SESSIONMAIL_HOME"])


def old(seat: str, name: str, body: str = "# added by judy 2030-06-09: why\n") -> Path:
    p = smhome() / "seats" / seat / "subscriptions" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def born(seat: str) -> None:
    old(seat, "seat", QUERY)
    old(seat, "session", QUERY)


def mapped(seat: str) -> dict:
    p = seat_map_path(seat)
    return json.loads(p.read_text()) if p.is_file() else {}


def log_lines(seat: str) -> list[dict]:
    p = seat_map_path(seat).parent.parent / _layer.LOG
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


def test_a_seat_with_only_saved_queries_gets_no_file_and_no_dir():
    born("voicemode+super")
    r = _migrate.migrate()
    assert r.seats == 1 and r.queries == 2 and not r.moved
    assert not seat_map_path("voicemode+super").parent.parent.exists()
    assert "nothing to move" in _migrate.lines(r)[-1]


def test_a_box_file_becomes_a_true_key_and_the_file_goes():
    born("ears+super")
    f = old("ears+super", "alerts+critical")
    g = old("ears+super", "work--demo")
    r = _migrate.migrate(by="judy", argv="pager settings migrate")
    assert {(s, b) for s, b, _ in r.moved} == {("ears+super", "alerts+critical"),
                                               ("ears+super", "work/demo")}
    assert mapped("ears+super") == {"subscriptions": {"alerts+critical": True,
                                                      "work/demo": True}}
    assert not f.exists() and not g.exists()
    assert (smhome() / "seats/ears+super/subscriptions/seat").is_file()   # queries stay
    logs = log_lines("ears+super")
    assert [x["key"] for x in logs] == ["subscriptions.alerts+critical",
                                        "subscriptions.work/demo"]
    assert logs[0]["by"] == "judy" and "added by judy 2030-06-09: why" in logs[0]["why"]
    assert str(f) in logs[0]["why"]


def test_after_the_move_the_reader_routes_it_and_names_nothing_unconverted():
    old("ears+super", "alerts+critical")
    before = sub.scan(live=set())
    assert ("ears+super", "alerts+critical") not in before.pairs
    assert any(why == sub.NOT_CONVERTED for _, why in before.skipped)
    _migrate.migrate()
    after = sub.scan(live=set())
    assert ("ears+super", "alerts+critical") in after.pairs
    assert not any(why == sub.NOT_CONVERTED for _, why in after.skipped)


def test_a_second_run_finds_nothing():
    old("ears+super", "alerts+critical")
    _migrate.migrate()
    r = _migrate.migrate()
    assert not r.moved and not r.already
    assert len(log_lines("ears+super")) == 1


def test_dry_run_writes_nothing():
    f = old("ears+super", "alerts+critical")
    r = _migrate.migrate(dry_run=True)
    assert [(s, b) for s, b, _ in r.moved] == [("ears+super", "alerts+critical")]
    assert f.is_file() and not seat_map_path("ears+super").exists()
    out = _migrate.lines(r)
    assert out[-1] == "dry run: nothing written"
    assert any(ln.startswith("  would move  ears+super  alerts+critical") for ln in out)
    assert not r.changed


def test_a_box_already_true_in_the_map_only_removes_the_file():
    seat_subscribe("ears+super", "alerts+critical")
    f = old("ears+super", "alerts+critical")
    r = _migrate.migrate()
    assert [(s, b) for s, b, _ in r.already] == [("ears+super", "alerts+critical")]
    assert not r.moved and not f.exists() and log_lines("ears+super") == []


def test_the_maps_false_wins_and_the_file_is_left_and_named():
    p = seat_map_path("ears+super")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"subscriptions": {"alerts+critical": False}}))
    f = old("ears+super", "alerts+critical")
    r = _migrate.migrate()
    assert f.is_file() and mapped("ears+super")["subscriptions"] == {"alerts+critical": False}
    assert [(s, b, v) for s, b, _, v in r.conflicts] == [("ears+super", "alerts+critical", False)]
    assert any("the map wins" in ln for ln in _migrate.lines(r))


def test_a_map_that_is_not_json_fails_that_box_and_not_the_run():
    p = seat_map_path("ears+super")
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    f = old("ears+super", "alerts+critical")
    g = old("uma+super", "events")
    r = _migrate.migrate()
    assert f.is_file() and [e[0] for e in r.errors] == [f]
    assert mapped("uma+super") == {"subscriptions": {"events": True}} and not g.exists()
    assert any(ln.startswith("FAILED") for ln in _migrate.lines(r))


def test_an_agents_bare_name_stays_and_prints_its_md_block():
    born("ivan")
    f = old("ivan", "alerts+critical")
    r = _migrate.migrate()
    assert f.is_file() and r.agents == {"ivan": ["alerts+critical"]}
    assert not (Path(os.environ["HOME"]) / ".session/seats/ivan").exists()
    out = "\n".join(_migrate.lines(r))
    assert "  ivan:\n    settings:\n      mail:\n        subscriptions:\n" \
           "          alerts+critical: true" in out


def test_a_session_ids_folder_is_listed_only():
    sid = "dd529b30-fdeb-4fdf-9fee-6f8a30a4ee62"
    f = old(sid, "atomnote")
    r = _migrate.migrate()
    assert r.session_ids == [sid] and f.is_file() and not r.agents


def test_a_seat_rules_file_is_left_and_named():
    born("ears+super")
    rules = smhome() / "seats/ears+super/rules"
    rules.write_text("wake from:judy@*\n")
    r = _migrate.migrate()
    assert r.rules == [rules] and rules.read_text() == "wake from:judy@*\n"
    assert any("seat rules move with the seat rules reader" in ln for ln in _migrate.lines(r))


def test_a_file_that_is_not_a_stored_box_is_skipped_and_kept():
    f = old("ears+super", "a---b")
    r = _migrate.migrate()
    assert f.is_file() and not r.moved and [p for p, _ in r.skipped] == [f]


# ── the verb: `pager settings migrate`, hidden, not an MCP tool ───────────

def pager_cmd(*argv: str):
    from click.testing import CliRunner
    from sessionmail.pager import cli
    return CliRunner().invoke(cli, list(argv), prog_name="pager")


def test_the_verb_is_hidden_from_the_page_and_is_no_mcp_tool():
    from sessionmail import _mcp
    from sessionmail.pager import cli
    assert "settings" in cli.commands and cli.commands["settings"].hidden
    assert "settings" not in pager_cmd("--help").output
    assert not [name for name, *_ in _mcp.tools(cli, "pager") if "settings" in name]


def test_dry_run_is_open_to_any_seat(monkeypatch):
    monkeypatch.setenv("TM_AGENT_ADDRESS", "ears+super")
    f = old("ears+super", "alerts+critical")
    r = pager_cmd("settings", "migrate", "--dry-run")
    assert r.exit_code == 0, r.output
    assert "would move  ears+super  alerts+critical" in r.output and f.is_file()


def test_the_run_is_refused_to_a_seat_that_is_not_ivan_or_judy(monkeypatch):
    monkeypatch.setenv("TM_AGENT_ADDRESS", "ears+super")
    f = old("ears+super", "alerts+critical")
    r = pager_cmd("settings", "migrate")
    assert r.exit_code == 2 and "refused" in r.output and f.is_file()
    assert not seat_map_path("ears+super").exists()


def test_the_run_with_no_seat_moves_and_rebuilds(monkeypatch):
    from sessionmail import _subscribe
    calls = []
    monkeypatch.setattr(_subscribe, "rebuild", lambda env=None: calls.append(1) or (True, "map: rebuilt"))
    f = old("ears+super", "alerts+critical")
    r = pager_cmd("settings", "migrate")
    assert r.exit_code == 0, r.output
    assert "moved  ears+super  alerts+critical" in r.output and "map: rebuilt" in r.output
    assert not f.exists() and calls == [1]
    again = pager_cmd("settings", "migrate")
    assert "nothing to move" in again.output and calls == [1]
