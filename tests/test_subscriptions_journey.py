"""One journey through the whole of message-subscriptions (SM-374 verify-001).

Each piece has its own tests (tasks 2.1-4.9: tests/test_subscription_file.py,
test_postfix_vmailbox_gen.py, test_message_box.py, test_message_subscription*.py,
test_pager_*.py). This file chains them in the order a real session meets
them, so the pieces are shown to agree with EACH OTHER: the file the verb
writes is the row the generator builds, and that row is what the listings
report.

    birth (SESSIONMAIL_SUBSCRIBE) -> files -> rows -> tree / hearers / box list
    -> the gates -> add twice -> Ivan's seat subscription -> remove
    -> the session dies -> the next rebuild drops its rows -> BOX's grace line

Over the do-006 synthetic fleet: a tmp mail root and sessionmail home, fake
postconf/postmap/ps, sidecars under tmp, and the REAL generator writing its
maps under tmp. Liveness for the Python readers comes from the same fake
`ps` table the generator reads (`_seat._pid_alive` is pointed at it), so the
two readers are shown to agree rather than each being stubbed separately.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from _seatmap import session_env
from sessionmail import _seat, message, pager
from test_message_subscription import IVAN_SID, HOST, as_seat, fleet, rows  # noqa: F401
from test_pager_subscribe_at_birth import NEW, fresh  # noqa: F401

EX_TIMEOUT = 124


@pytest.fixture
def alive(fresh, tmp_path, monkeypatch):
    """Liveness the two readers agree on. The generator needs a sidecar's
    `sessionId`, `pid` and `procStart` plus a matching `ps` row;
    `sessiontools.sessions()` (the Python readers' source) also needs
    `status` and `updatedAt`, which every real sidecar carries (17 of 17 on
    hq, measured Tue 2030-10-01) and the fleet's thin fixture does
    not. So the sidecars are given the real shape, and a pid is alive for
    the Python readers exactly when the fake `ps` table lists it."""
    for car in (tmp_path / "claude-sessions").glob("*.json"):
        data = json.loads(car.read_text())
        data.setdefault("status", "busy")
        data.setdefault("updatedAt", 1908540800000)
        car.write_text(json.dumps(data))
    table = tmp_path / "ps.txt"

    def pid_alive(pid) -> bool:
        return any(line.split()[0] == str(pid) for line in table.read_text().splitlines())

    monkeypatch.setattr(_seat, "_pid_alive", pid_alive)
    return fresh


def msg(*argv: str):
    return CliRunner().invoke(message.cli, list(argv), prog_name="message")


def listen(*argv: str):
    return CliRunner().invoke(pager.cli, ["listen", "--timeout", "0", *argv], prog_name="pager")


def row(fleet, key: str) -> str | None:
    return rows(fleet.valias).get(f"{key}@{HOST}")


def test_the_whole_journey(alive, tmp_path, monkeypatch):
    f = alive
    new8 = NEW[:8]

    # 4.1, 4.6; 3.4: the launcher's birth wrote SESSIONMAIL_SUBSCRIBE into the
    # session's layer (`mail.d/50-env.json`, sessiontools 4.4); the first listen
    # births the box, reports those boxes, and its regen routes them
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote voicemode+vm-2266")
    session_env(NEW, "atomnote voicemode+vm-2266", f.userhome)
    born = listen()
    assert born.exit_code == EX_TIMEOUT, born.output
    assert (f.agents / NEW / "new").is_dir()
    assert [line.split(":")[1].split()[0] for line in born.stderr.splitlines()
            if line.startswith("  subscribe")] == ["atomnote", "voicemode+vm-2266"]

    # 2.1, 2.2: the files are the source; the rows are built from them, box first
    assert f.files() == [f"sessions/{NEW}#atomnote",                 # its map (3.3b)
                         f"sessions/{NEW}#voicemode+vm-2266"]
    assert row(f, "atomnote") == f"atomnote@{HOST}, {NEW}@{HOST}"
    assert row(f, "voicemode+vm-2266") == f"voicemode+vm-2266@{HOST}, {NEW}@{HOST}"
    assert NEW not in (row(f, "voicemode") or "")          # narrow: the bare box is not claimed

    # 3.3: what reaches me, as a tree, seat first
    tree = msg("subscription", "list")
    assert tree.exit_code == 0, tree.output
    lines = tree.output.splitlines()
    assert lines[0].startswith(f"{new8} (this session)")
    assert "worker" in lines[1] and "your seat" in lines[1]
    assert [line.split()[1] for line in lines[2:4]] == ["atomnote", "voicemode+vm-2266"]
    assert lines[-1].startswith("-- 3 addresses reach you")

    # 3.3: who hears this box - the live session, as the row says
    hears = msg("subscription", "list", "atomnote")
    assert hears.exit_code == 0 and new8 in hears.output, hears.output
    assert "-- 1 hears it" in hears.output

    # 3.1: box list names the subscriber and hides the session boxes
    boxes = msg("box", "list")
    assert boxes.exit_code == 0, boxes.output
    atom = next(line for line in boxes.output.splitlines() if "st/atomnote" in line)
    assert "<-" in atom and new8 in atom, boxes.output
    assert not any(line.startswith("session") for line in boxes.output.splitlines())
    assert "session boxes hidden (--all shows them)" in boxes.output.splitlines()[-1]
    assert msg("box", "path", "atomnote").output == msg("path", "atomnote").output

    # 3.2: the gates (D5), and add twice is a no-op with no rebuild
    runs = f.runs()
    own = msg("subscription", "add", "ivan")
    assert own.exit_code == 2 and "that seat's to grant" in own.output
    seat = msg("subscription", "add", "atomnote", "--seat")
    assert seat.exit_code == 2 and "request it by mail, from a privileged seat" in seat.output
    again = msg("subscription", "add", "atomnote")
    assert again.exit_code == 0, again.output
    assert f.runs() == runs, "a refusal or a no-op rebuilt the map"

    # D5: Ivan may write a seat subscription; the row is the union, box first
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert msg("subscription", "add", "atomnote", "--seat").exit_code == 0
    assert row(f, "atomnote") == f"atomnote@{HOST}, ivan@{HOST}, {NEW}@{HOST}"

    # 3.2: remove rebuilds, and prints the row it changed
    as_seat(monkeypatch, "worker", NEW)
    gone = msg("subscription", "remove", "atomnote")
    assert gone.exit_code == 0, gone.output
    assert f"row: atomnote@{HOST} -> atomnote@{HOST}, ivan@{HOST}" in gone.output
    assert row(f, "atomnote") == f"atomnote@{HOST}, ivan@{HOST}"

    # "A session subscription dies with its session": no reaper, just the next rebuild
    table = tmp_path / "ps.txt"
    pid = next(p.stem for p in (tmp_path / "claude-sessions").glob("*.json")
               if NEW in p.read_text())
    table.write_text("".join(line + "\n" for line in table.read_text().splitlines()
                             if line.split()[0] != pid))
    # the next rebuild comes from some other live session: the generator
    # refuses to run inside a session it cannot see live (its own guard)
    as_seat(monkeypatch, "ivan", IVAN_SID)
    assert _seat.regen_maps(f.agents).startswith("generator ran")
    assert row(f, "voicemode+vm-2266") is None
    assert "voicemode+vm-2266" in f.subscribed(NEW)                  # its map (3.3b)
    assert "-- 0 hear it" in msg("subscription", "list", "voicemode+vm-2266").output

    # 4.7: BOX, in its release of grace, still listens and names the replacement
    grace = listen("atomnote")
    assert grace.exit_code == EX_TIMEOUT, grace.output
    assert ("pager listen: BOX is going away; to hear atomnote in your own box, "
            "run message subscription add atomnote") in grace.stderr
