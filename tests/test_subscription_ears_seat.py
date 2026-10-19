"""pager-genie 3.29: a SEAT subscription to `heard` refuses (the PROPOSED
reading of N8, design D13/D14).

openspec/changes/pager-genie, tasks.md 3.29: `message subscription add ears
--seat` and `add heard+<device> --seat` exit 2, name N8, and write nothing,
for every caller, Ivan and Judy included (although `--seat` is otherwise
theirs). The `heard` name is ONE named value, shared with 3.1. A SESSION
`add heard+airpods` still exits 0. *(A predecessor's turns never reach its
successor; A session may read a device's partials at the source)*

Hermetic: test_message_subscription.py's synthetic fleet (mail root,
sessionmail home, postfix maps and the real generator writing under
tmp_path, never postmap), with ONE `heard` box added to its store.
"""

from __future__ import annotations

import pytest

from sessionmail import _subscribe, _voice
from sessionmail._seat import regen_maps
from test_message_subscription import (  # noqa: F401  (fixture reused)
    IVAN_SID, HOST, JUDY_SID, SID, _maildir, as_seat, fleet, rows, sub,
)


@pytest.fixture
def ears(fleet):
    """The fleet, with the one `heard` box in its store, routed by the map as
    `acmecorp/heard` is on hq."""
    _maildir(fleet.agents / "st" / "heard")
    assert regen_maps(fleet.agents).startswith("generator ran"), fleet.log
    fleet.log.unlink()
    return fleet


def _refused_by_n8(done, fleet, before_files, before_valias) -> None:
    assert done.exit_code == 2, done.output
    assert "N8" in done.output and "per SESSION" in done.output, done.output
    assert "Nothing written" in done.output
    assert fleet.files() == before_files                 # no subscription file
    assert fleet.runs() == 0                             # no rebuild
    assert fleet.valias == before_valias                 # the map as it was


# ── Scenario: A seat subscription to a device refuses ──────────────────

@pytest.mark.parametrize("box", ["heard+airpods", "heard"])
@pytest.mark.parametrize("who,sid", [("ivan", IVAN_SID), ("judy", JUDY_SID)])
def test_a_seat_subscription_to_a_device_refuses_even_for_ivan_and_judy(
        ears, monkeypatch, who, sid, box):
    """GIVEN the `heard` box, WHEN `message subscription add heard+airpods
    --seat` runs, by Ivan (or Judy, or on the bare box), THEN it exits 2,
    names N8, and writes nothing."""
    as_seat(monkeypatch, who, sid)
    before, valias = ears.files(), ears.valias
    _refused_by_n8(sub("add", box, "--seat"), ears, before, valias)
    assert not ears.seat_file(who, box).exists()


def test_for_another_seat_refuses_the_same_way(ears, monkeypatch):
    """`--for WHO --seat` is still a SEAT subscription."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    before, valias = ears.files(), ears.valias
    _refused_by_n8(sub("add", "heard+airpods", "--seat", "--for", "worker"),
                   ears, before, valias)


def test_anyone_else_is_told_n8_not_the_route_to_ivan(ears):
    """A worker would be refused a seat subscription anyway (D5). It is told
    the reason that holds for everyone: Ivan could not grant this one."""
    before, valias = ears.files(), ears.valias
    done = sub("add", "heard+airpods", "--seat")
    _refused_by_n8(done, ears, before, valias)
    assert "request it by mail" not in done.output


@pytest.mark.parametrize("box", ["st/heard+airpods", "HEARD+AirPods", "heard+airpods@" + HOST])
def test_every_spelling_of_the_box_refuses(ears, monkeypatch, box):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    before, valias = ears.files(), ears.valias
    _refused_by_n8(sub("add", box, "--seat"), ears, before, valias)


def test_it_refuses_by_name_before_the_box_is_there(fleet, monkeypatch):
    """No `heard` box on this host yet: the answer is still N8, not "no such
    box" - the rule is about what the box IS, not whether it exists."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    before, valias = fleet.files(), fleet.valias
    _refused_by_n8(sub("add", "heard+airpods", "--seat"), fleet, before, valias)


# ── what is NOT refused ────────────────────────────────────────────────

def test_a_session_subscription_to_a_device_still_succeeds(ears):
    """A SESSION `add heard+airpods` exits 0, as MEASURED on `71b689a`: the
    device subscription N8 rules is exactly this one."""
    done = sub("add", "heard+airpods")
    assert done.exit_code == 0, done.output
    assert "heard+airpods" in ears.subscribed(SID)
    assert rows(ears.valias)[f"heard+airpods@{HOST}"].endswith(f", {SID}@{HOST}")


def test_ivan_may_still_add_a_seat_subscription_to_any_other_box(ears, monkeypatch):
    as_seat(monkeypatch, "ivan", IVAN_SID)
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == 0, done.output
    assert ears.seat_file("ivan", "atomnote").is_file()


def test_remove_of_a_seat_subscription_to_ears_is_not_refused(ears, monkeypatch):
    """Only ADD is ruled. One written before this rule (by hand, or by an
    older build) can still be taken away with the verb."""
    as_seat(monkeypatch, "ivan", IVAN_SID)
    path = ears.seat_file("ivan", "heard+airpods")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# added by ivan (message subscription add heard+airpods --seat)\n")
    done = sub("remove", "heard+airpods", "--seat")
    assert done.exit_code == 0, done.output
    assert not path.exists()


# ── the name, pure ─────────────────────────────────────────────────────

@pytest.mark.parametrize("box,is_device", [
    ("heard", True), ("heard+airpods", True), ("HEARD+airpods", True),
    ("st/heard", True), ("acmecorp/heard+desk", True), ("heard+airpods@hq", True),
    (" heard ", True),
    # SM-550: the keeper box is `heard`, so the
    # old `ears` name, now the ears ASK box, is not a device box at all.
    ("ears", False), ("ears+airpods", False), ("st/ears", False),
    ("earshot", False), ("ears-box", False), ("atomnote+ears", False),
    ("hearding", False), ("heard-box", False),
    ("st/atomnote", False), ("mears", False), ("", False),
])
def test_device_box_reads_the_name(box, is_device):
    assert _subscribe.device_box(box) is is_device


def test_the_name_is_the_one_3_1_trusts():
    """ONE named value: the box refused here is the sender 3.1 believes."""
    assert _subscribe.KEEPER is _voice.KEEPER == "heard"
