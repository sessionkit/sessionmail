"""session-settings 3.2: a SEAT's subscriptions in the new tree.

sessiontools `openspec/changes/session-settings`, task 3.2. A seat's
subscriptions are the `subscriptions` map of
`~/.session/seats/<seat>/settings/mail.json` (then `mail.d/*.json`), read
PER OWNER (call 1 (A)): `true` is a box,
anything else routes nothing. An agent's bare name keeps its old folder until
3.8 (call 3 (b)). HOME is conftest's sandbox.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sessionmail import _layer, _subscription as sub

from _seatmap import seat_map_path, seat_subscribe


def home() -> Path:
    return Path(os.environ["HOME"])


def write(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


def old_file(smhome: Path, seat: str, name: str, body: str = "# added by a test\n") -> Path:
    return write(smhome / "seats" / seat / "subscriptions" / name, body)


# ── the name: an agent's bare name is not a seat ──────────────────────────

@pytest.mark.parametrize("name, agent", [
    ("ivan", True), ("eve", True), ("dd529b30-fdeb-4fdf-9fee-6f8a30a4ee62", True),
    ("sessionmail+super", False), ("super.uma", False), ("cutroom+cut-634.foreman", False),
])
def test_an_agents_bare_name_has_no_plus_and_no_dot(name, agent):
    assert _layer.is_agent_name(name) is agent


# ── one layer: mail.json then mail.d/, per owner ──────────────────────────

def test_a_seat_layer_folds_its_d_files_in_name_order():
    d = _layer.settings_dir("voicemode+super")
    assert d == home() / ".session/seats/voicemode+super/settings"
    write(d / "mail.json", {"subscriptions": {"a": True, "b": True, "c": False}})
    write(d / "mail.d" / "90-judy.json", {"subscriptions": {"b": False}})
    write(d / "mail.d" / "50-x.json", {"subscriptions": {"c": True, "b": True}})
    values, origins = _layer.read(d)
    assert _layer.boxes(values) == ["a", "c"]
    assert origins["subscriptions.b"] == str(d / "mail.d" / "90-judy.json")


@pytest.mark.parametrize("value", [False, "true", 1, None, {"x": True}, [True]])
def test_only_exactly_true_is_a_box(value):
    assert _layer.boxes({"subscriptions": {"box": value}}) == []


def test_a_layer_with_no_file_is_empty():
    assert _layer.read(home() / "nowhere") == ({}, {})


def test_a_layer_file_that_is_not_an_object_is_a_layer_error():
    d = _layer.settings_dir("a+b")
    write(d / "mail.json", "[1]")
    with pytest.raises(_layer.LayerError, match="not a JSON object"):
        _layer.read(d)


def test_without_sessiontools_settings_a_read_is_a_layer_error(monkeypatch):
    """sessiontools 0.0.3 has no `settings`: one LayerError, never an
    ImportError out of the pager."""
    import builtins
    real = builtins.__import__

    def no_settings(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sessiontools" and fromlist and "settings" in fromlist:
            raise ImportError("cannot import name 'settings'")
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_settings)
    with pytest.raises(_layer.LayerError, match="sessiontools 0.0.4"):
        _layer.settings_dir("a+b")


@pytest.mark.parametrize("answer", [
    ({}, {}, {}),                       # 414bad3's three values
    ({}, {}, {}, {}, {}),               # a fifth, some day
    ([], {}, {}, {}),                   # four, but not maps
    None,
])
def test_a_merge_of_another_shape_is_a_layer_error_naming_it(monkeypatch, answer):
    """A review: `merge` went from three values to
    four within the hour. Another change must name itself, not crash
    `message subscription list` or the migrate with a ValueError."""
    from sessiontools import settings
    d = _layer.settings_dir("voicemode+super")
    write(d / "mail.json", {"subscriptions": {"a": True}})
    monkeypatch.setattr(settings, "merge", lambda srcs: answer)
    with pytest.raises(_layer.LayerError, match="out of step"):
        _layer.read(d)


def test_a_merge_that_wants_another_argument_is_a_layer_error(monkeypatch):
    """A review: the guard covered merge's ANSWER, not its CALL. A
    merge that grows a required argument raises TypeError at the call."""
    from sessiontools import settings
    d = _layer.settings_dir("voicemode+super")
    write(d / "mail.json", {"subscriptions": {"a": True}})
    monkeypatch.setattr(settings, "merge", lambda srcs, extra: ({}, {}, {}, {}))
    with pytest.raises(_layer.LayerError, match="out of step"):
        _layer.read(d)


# ── the writer: mail.json only, under a lock ──────────────────────────────

def test_set_entry_adds_removes_and_never_writes_an_empty_map(tmp_path):
    d = tmp_path / "settings"
    assert _layer.set_entry(d, "subscriptions", "x", None) is False
    assert not d.exists(), "nothing to remove makes no dir"
    assert _layer.set_entry(d, "subscriptions", "x", True) is True
    assert _layer.set_entry(d, "subscriptions", "x", True) is False
    assert json.loads((d / "mail.json").read_text()) == {"subscriptions": {"x": True}}
    assert _layer.set_entry(d, "subscriptions", "x", None) is True
    assert not (d / "mail.json").exists()
    assert [p.name for p in d.iterdir() if not p.name.startswith(".")] == []


def test_set_entry_keeps_what_it_does_not_touch(tmp_path):
    d = tmp_path / "settings"
    write(d / "mail.json", {"$schema": "s", "rules": {"10": "ride from:x"}})
    _layer.set_entry(d, "subscriptions", "work/demo", True)
    assert json.loads((d / "mail.json").read_text()) == {
        "$schema": "s", "rules": {"10": "ride from:x"},
        "subscriptions": {"work/demo": True}}


def test_set_entry_refuses_a_section_that_is_not_a_map(tmp_path):
    d = tmp_path / "settings"
    write(d / "mail.json", {"subscriptions": ["x"]})
    with pytest.raises(_layer.LayerError, match="not a map"):
        _layer.set_entry(d, "subscriptions", "y", True)
    assert json.loads((d / "mail.json").read_text()) == {"subscriptions": ["x"]}


def test_true_and_one_are_different_values(tmp_path):
    d = tmp_path / "settings"
    write(d / "mail.json", {"subscriptions": {"x": 1}})
    assert _layer.set_entry(d, "subscriptions", "x", True) is True
    assert json.loads((d / "mail.json").read_text()) == {"subscriptions": {"x": True}}


def test_the_log_is_one_json_line_per_change(tmp_path):
    _layer.log(tmp_path, "subscriptions.x", True, by="ivan", argv="message subscription add x",
               why="  for\nthe  test ")
    _layer.log(tmp_path, "subscriptions.x", None, by="ivan", argv="message subscription remove x")
    a, b = [json.loads(line) for line in (tmp_path / "log/settings.jsonl").read_text().splitlines()]
    assert (a["key"], a["value"], a["why"]) == ("subscriptions.x", True, "for the test")
    assert b["value"] is None and "why" not in b


# ── the reader: seats from their maps, agents from their folders ──────────

def test_scan_reads_a_seats_map_and_an_agents_old_folder(tmp_path):
    smhome = tmp_path / "sessionmail"
    seat_subscribe("voicemode+super", "voicemode", "work/demo")
    old_file(smhome, "ivan", "atomnote")
    assert sub.pairs(smhome, live=set()) == {
        ("voicemode+super", "voicemode"), ("voicemode+super", "work/demo"),
        ("ivan", "atomnote")}
    [e] = [e for e in sub.scan(smhome, live=set()).entries if e.box == "voicemode"]
    assert e.path == seat_map_path("voicemode+super") and e.kind == "seat"


def test_a_seats_old_box_file_is_skipped_as_never_converted(tmp_path):
    smhome = tmp_path / "sessionmail"
    f = old_file(smhome, "voicemode+super", "atomnote")
    q = old_file(smhome, "voicemode+super", "seat", "query: to:$SEAT\n")
    scan = sub.scan(smhome, live=set())
    assert scan.pairs == set()
    why = dict(scan.skipped)
    assert why[f] == sub.NOT_CONVERTED and "pager settings migrate" in why[f]
    assert "saved query" in why[q]


def test_an_agents_name_under_the_new_tree_is_not_read(tmp_path):
    """Until 3.8 the agent layer is the MD, never `~/.session/seats/<agent>`."""
    seat_subscribe("ivan", "atomnote")
    assert sub.pairs(tmp_path / "sessionmail", live=set()) == set()


def test_a_seat_map_that_cannot_be_read_is_skipped_whole(tmp_path):
    write(seat_map_path("a+b"), "{")
    seat_subscribe("c+d", "x")
    scan = sub.scan(tmp_path / "sessionmail", live=set())
    assert scan.pairs == {("c+d", "x")}
    assert any(p == seat_map_path("a+b") and "not JSON" in why for p, why in scan.skipped)


@pytest.mark.parametrize("key", ["a--b", "a b", ".hidden", "x/-y", "é"])
def test_a_key_that_is_not_a_box_is_skipped(tmp_path, key):
    seat_subscribe("a+b", key)
    scan = sub.scan(tmp_path / "sessionmail", live=set())
    assert scan.pairs == set()
    assert any(f"subscriptions.{key}" in why for _p, why in scan.skipped)
