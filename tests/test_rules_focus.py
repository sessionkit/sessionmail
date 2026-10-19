"""Focus modes: `openspec/changes/pager-filters`, tasks 3.1-3.3; design D5.

RULED (P4, P5, P14):

- A mode IS a named rules layer, read at D3 layer 0.5 while it is on. The
  one mode that ships is `sleep`, whose one line is `ride *`. Focus off,
  the normal state, adds no layer (3.1).
- A mode is set per session, per seat, or fleet-wide; the fleet-wide mode
  ("everyone off") is set in the locked file (3.2, P14).
- An end time is checked when a mail arrives: the pager keeps no clock. A
  `wake!` wakes THROUGH a mode and leaves it on (3.3).

Built on the proposal, not ruled (design.md Part 2, named in the READY):
O-a precedence (fleet-wide, then session, then seat), O-b the layer's place
(0.5), O-c the file form (`<mode> [until <HH:MM | ISO time>]`, a file
`focus` beside the rules; fleet-wide as a `focus sleep [until ...]` line in
`rules.locked`).

Every root is under `tmp_path`; the conftest keeps the real locked file out
of reach. Times are LOCAL and naive here (as a person writes `until
07:00`), and a file's mtime is set from the same local clock, so the tests
hold in any time zone. Written to fail first (do-004's rule).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sessionmail import rules
from sessionmail.rules import (
    MODES,
    PAGE_RULES_VAR,
    Facts,
    Focus,
    Layer,
    Layers,
    Me,
    judge,
    load_layers,
    parse,
    parse_focus,
)

SID = "100ce475-f89b-4d88-c545-cb9438b4115f"
OTHER = "2f07cdee-0000-0000-0000-000000000000"
HOST = "hq.tailnet.example"
ME = Me.of(session=SID, seat="ivan", host=HOST)
JUDY = f"Judy <judy@{HOST}>"

#: The evening the mode is set, and the times the tests' mails arrive.
SET_AT = datetime(2030, 6, 12, 23, 0)
NIGHT = datetime(2030, 6, 13, 2, 0)
BEFORE_SEVEN = datetime(2030, 6, 13, 6, 55)
AFTER_SEVEN = datetime(2030, 6, 13, 7, 5)


def _mail(sid: str) -> Path:
    """A session's mail state since session-settings 3.3a: under the session
    dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/` (spelt out,
    not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def mail(*headers: tuple[str, str]) -> Facts:
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return Facts.from_bytes((head + "\n").encode())


KITH = mail(("From", "kith@hq"), ("To", f"kith@{HOST}"), ("X-Original-To", "kith"))
DIRECT = mail(("From", "eve@hq"), ("To", f"ivan@{HOST}"), ("X-Original-To", f"ivan@{HOST}"))
FROM_JUDY = mail(("From", JUDY), ("To", f"ivan@{HOST}"), ("X-Original-To", "ivan"))


class Roots:
    """A fake host: a sessionmail home and a locked file, both under tmp."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.locked = tmp / "etc" / "rules.locked"

    def write(self, path: Path, text: str, at: datetime = SET_AT) -> Path:
        """Write PATH and stamp its mtime AT (local): the time it was set."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        stamp = at.timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def session_focus(self, sid: str = SID) -> Path:
        return _mail(sid) / "focus"

    def seat_focus(self, seat: str = "ivan") -> Path:
        return self.home / "seats" / seat / "focus"

    def session_rules(self, sid: str = SID) -> Path:
        return _mail(sid) / "rules"

    def seat_rules(self, seat: str = "ivan") -> Path:
        return self.home / "seats" / seat / "rules"

    def load(self, seat: str | None = "ivan", session: str | None = SID,
             env: dict[str, str] | None = None) -> Layers:
        return load_layers(seat, session, env or {}, home=self.home, locked=self.locked)


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


def names(layers: Layers) -> list[str]:
    return [layer.name for layer in layers]


def decided(facts: Facts, layers: Layers, now: datetime) -> tuple:
    got = judge(facts, layers, ME, now)
    return got.action, got.rule, got.layer


# ── 3.1 a mode is a named rules layer; sleep is `ride *`; off adds none ──────

def test_the_one_mode_that_ships_is_sleep_and_it_is_ride_star():
    """P5: only `sleep` ships, and it is one line, `ride *`."""
    assert dict(MODES) == {"sleep": "ride *\n"}
    got, errors = parse(MODES["sleep"], "focus: sleep")
    assert errors == ()
    assert [r.text for r in got] == ["ride *"]


def test_focus_off_adds_no_layer(roots):
    """No focus file, or a focus file saying `off`, adds no layer: the
    normal state (focus off is just normal)."""
    assert names(roots.load()) == ["built-in"]
    roots.write(roots.session_focus(), "off\n")
    roots.write(roots.seat_focus(), "off\n")
    roots.write(roots.locked, "focus off\n")
    layers = roots.load()
    # The locked file is there, so it is a layer (empty, as do-003 rules an
    # empty file); its `focus off` line is no rule and no mode.
    assert names(layers) == ["locked", "built-in"]
    assert layers[0].rules == ()
    assert layers.errors == ()
    assert layers.modes == ()
    assert layers.focus(NIGHT) is None


def test_a_mode_is_a_layer_at_zero_point_five(roots):
    """D3 0.5 (O-b): below the locked file, above the session's own rules
    and everything else an agent or its seat wrote."""
    roots.write(roots.locked, "wake! from:alerts@*\n")
    roots.write(roots.session_rules(), "wake from:kith@*\n")
    roots.write(roots.seat_rules(), "wake from:*@delta.chat\n")
    roots.write(roots.session_focus(), "sleep\n")
    layers = roots.load(env={PAGE_RULES_VAR: "wake x-project:atomnote\n"})
    assert names(layers) == ["locked", "focus: sleep", "session", "seat", "env", "built-in"]
    (rule,) = layers[1].rules
    assert (rule.text, rule.line, rule.source) == ("ride *", 1, "focus: sleep")


def test_the_mode_caps_the_session_and_seat_rules_below_it(roots):
    """`sleep`'s `ride *` sits above the session's `wake`: a Kith mail the
    session asked to be woken by rides while the session sleeps."""
    roots.write(roots.session_rules(), "wake from:kith@*\n")
    roots.write(roots.session_focus(), "sleep\n")
    assert decided(KITH, roots.load(), NIGHT) == ("ride", "ride *", "focus: sleep")


# ── the slice's first criterion: asleep, Kith rides, Judy wakes, still asleep ─

def test_asleep_kith_rides_and_judys_mail_wakes_and_she_is_still_asleep(roots):
    """The spec's *Asleep, Kith rides along and Judy's mail wakes without
    ending sleep*: Ivan in `sleep`, no end time. The Kith mail rides by the
    mode; Judy's wakes by the built-in `wake!`; and the mode is still on
    after, on disk and in the next verdict (a `wake!` never ends a mode)."""
    path = roots.write(roots.seat_focus(), "sleep\n")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    layers = roots.load()

    assert decided(KITH, layers, NIGHT) == ("ride", "ride *", "focus: sleep")
    assert decided(FROM_JUDY, layers, NIGHT) == ("wake", "carry! from:$operator", "built-in")

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    again = roots.load()
    assert "focus: sleep" in names(again)
    assert again.focus(NIGHT).mode == "sleep"
    assert decided(KITH, again, NIGHT) == ("ride", "ride *", "focus: sleep")
    assert decided(DIRECT, again, NIGHT) == ("ride", "ride *", "focus: sleep")


def test_a_seat_breakthrough_wakes_through_sleep_and_leaves_it_on(roots):
    """The spec's *Marked mail from Eve, at night*: `sleep until 07:00` and
    a seat rule `wake! from:eve@* subject:URGENT*`; Eve's `URGENT: db1 is
    down` at 02:00 wakes, and everything else still rides."""
    roots.write(roots.seat_rules(), "wake! from:eve@* subject:URGENT*\n")
    roots.write(roots.seat_focus(), "sleep until 07:00\n")
    layers = roots.load()
    urgent = mail(("From", "eve@hq"), ("Subject", "URGENT: db1 is down"),
                  ("X-Original-To", "ivan"))
    got = judge(urgent, layers, ME, NIGHT)
    assert (got.action, got.layer) == ("wake", "seat")
    assert decided(DIRECT, layers, NIGHT) == ("ride", "ride *", "focus: sleep")
    assert layers.focus(NIGHT).mode == "sleep"


# ── 3.3 the end time, checked at arrival ─────────────────────────────────────

def test_sleep_until_seven_and_a_direct_mail_at_seven_oh_five_wakes(roots):
    """The slice's second criterion, and the spec's *Asleep until seven,
    checked at arrival*: set at 23:00, a direct mail at 06:55 rides by the
    mode, and one at 07:05 wakes by the default `wake to:$me`. The layers
    are loaded ONCE, as a listener holds them: the end is judged at the
    mail, not when the files were read."""
    roots.write(roots.session_focus(), "sleep until 07:00\n")
    layers = roots.load()
    assert decided(DIRECT, layers, BEFORE_SEVEN) == ("ride", "ride *", "focus: sleep")
    assert decided(DIRECT, layers, AFTER_SEVEN) == ("wake", "wake to:$me", "built-in")
    assert layers.focus(BEFORE_SEVEN).mode == "sleep"
    assert layers.focus(AFTER_SEVEN) is None


def test_at_the_end_time_exactly_the_mode_has_ended(roots):
    roots.write(roots.session_focus(), "sleep until 07:00\n")
    layers = roots.load()
    assert judge(DIRECT, layers, ME, datetime(2030, 6, 13, 7, 0)).action == "wake"


def test_hh_mm_is_the_first_such_time_after_the_mode_was_set(roots):
    """`until 07:00` has no date. It is read as the FIRST 07:00 after the
    file was written (its mtime): set at 23:00 it runs overnight; set at
    05:00 it ends the same morning; set at 08:00 it runs to tomorrow."""
    path = roots.session_focus()
    roots.write(path, "sleep until 07:00\n", at=datetime(2030, 6, 13, 5, 0))
    assert roots.load().focus(BEFORE_SEVEN).until == datetime(2030, 6, 13, 7, 0).astimezone()
    roots.write(path, "sleep until 07:00\n", at=datetime(2030, 6, 13, 8, 0))
    layers = roots.load()
    assert layers.focus(datetime(2030, 6, 13, 23, 0)).until == \
        datetime(2030, 6, 14, 7, 0).astimezone()
    # An old file's end is long gone: the mode is not on, whatever the hour.
    roots.write(path, "sleep until 07:00\n", at=datetime(2030, 5, 14, 23, 0))
    assert roots.load().focus(NIGHT) is None


@pytest.mark.parametrize("until, ended_at_705", [
    ("2030-06-13T07:00", True),
    ("2030-06-13 07:00", True),
    ("2030-06-13T07:10", False),
    ("2030-06-14T07:00", False),
])
def test_an_iso_end_time_is_that_moment_local_when_no_zone_is_written(
        roots, until, ended_at_705):
    roots.write(roots.session_focus(), f"sleep until {until}\n")
    layers = roots.load()
    assert (judge(DIRECT, layers, ME, AFTER_SEVEN).action == "wake") is ended_at_705


def test_an_iso_end_time_with_a_zone_is_that_instant():
    at = datetime(2030, 6, 13, 7, 0, tzinfo=timezone.utc)
    focus, errors = parse_focus("sleep until 2030-06-13T07:00+00:00\n", "f", SET_AT)
    assert errors == ()
    assert focus.until == at
    assert focus.ended(at + timedelta(minutes=5))
    assert not focus.ended(at - timedelta(minutes=5))


def test_an_aware_now_and_a_naive_now_judge_the_same(roots):
    """`now` may come aware (the listener's clock) or naive (local); both
    are the same instant to the end-time check."""
    roots.write(roots.session_focus(), "sleep until 07:00\n")
    layers = roots.load()
    for now in (BEFORE_SEVEN, BEFORE_SEVEN.astimezone(),
                BEFORE_SEVEN.astimezone(timezone.utc)):
        assert judge(DIRECT, layers, ME, now).layer == "focus: sleep"
    for now in (AFTER_SEVEN, AFTER_SEVEN.astimezone(), AFTER_SEVEN.astimezone(timezone.utc)):
        assert judge(DIRECT, layers, ME, now).layer == "built-in"


def test_a_mode_with_no_end_time_never_ends_by_the_clock(roots):
    roots.write(roots.session_focus(), "sleep\n")
    layers = roots.load()
    far = datetime(2031, 1, 1, 12, 0)
    assert decided(DIRECT, layers, far) == ("ride", "ride *", "focus: sleep")


def test_with_no_now_an_end_time_cannot_be_checked_so_the_mode_holds(roots):
    """`judge(..., now=None)` has no time to compare: the mode stays on,
    the side that keeps the author's quiet (the listener always passes
    its arrival time; do-006)."""
    roots.write(roots.session_focus(), "sleep until 07:00\n")
    assert judge(DIRECT, roots.load(), ME, None).layer == "focus: sleep"


# ── 3.2 where a mode is set; the fleet-wide mode in the locked file ─────────

def test_fleet_wide_sleep_in_the_locked_file(roots):
    """The slice's third criterion, and the spec's *Everyone off*: `focus
    sleep` in the locked file. A direct mail to ANY idle session on the host
    does not wake it; a `wake!` still does. The `focus` line is not a bad
    rule, and the locked file's own rules still load, with their line
    numbers."""
    roots.write(roots.locked, "# everyone off\nfocus sleep\nwake! from:alerts@*\n")
    for seat, sid in (("ivan", SID), ("sessionmail+sm-474.worker", OTHER), (None, OTHER)):
        me = Me.of(session=sid, seat=seat or "", host=HOST)
        layers = roots.load(seat=seat, session=sid)
        assert layers.errors == ()
        assert names(layers) == ["locked", "focus: sleep", "built-in"]
        assert [(r.text, r.line) for r in layers[0].rules] == [("wake! from:alerts@*", 3)]
        to_it = mail(("From", "eve@hq"), ("X-Original-To", f"{sid}@{HOST}"))
        got = judge(to_it, layers, me, NIGHT)
        assert (got.action, got.layer) == ("ride", "focus: sleep")
        assert judge(mail(("From", "alerts@hq")), layers, me, NIGHT).layer == "locked"
        assert judge(mail(("From", JUDY)), layers, me, NIGHT).action == "wake"
        (mode,) = layers.modes
        assert (mode.mode, mode.where, mode.source, mode.line) == (
            "sleep", "fleet", str(roots.locked), 2)


def test_fleet_wide_sleep_until_is_anchored_at_the_locked_files_mtime(roots):
    roots.write(roots.locked, "focus sleep until 07:00\n")
    layers = roots.load()
    assert judge(DIRECT, layers, ME, BEFORE_SEVEN).layer == "focus: sleep"
    assert judge(DIRECT, layers, ME, AFTER_SEVEN).action == "wake"


def test_a_focus_line_is_the_locked_files_only(roots):
    """In the session's or seat's RULES file a `focus` line is an unknown
    verb, named: an agent sets its mode in its focus file, and the
    fleet-wide mode is Ivan's and Judy's (P14)."""
    path = roots.write(roots.session_rules(), "focus sleep\n")
    layers = roots.load()
    assert "focus: sleep" not in names(layers)
    (err,) = layers.errors
    assert (err.source, err.line) == (str(path), 1)
    assert "unknown verb" in err.reason


def test_the_focus_line_is_never_taken_from_the_environment(roots):
    """`SESSIONMAIL_PAGE_RULES` is the env layer; a `focus sleep` in it is
    a bad line there, never a fleet-wide mode."""
    layers = roots.load(env={PAGE_RULES_VAR: "focus sleep\n"})
    assert layers.modes == ()
    assert [e.source for e in layers.errors] == [PAGE_RULES_VAR]


@pytest.mark.parametrize("where", ["session", "seat"])
def test_a_session_or_seat_focus_file_sets_the_mode(roots, where):
    path = roots.session_focus() if where == "session" else roots.seat_focus()
    roots.write(path, "sleep until 07:00\n")
    layers = roots.load()
    focus = layers.focus(NIGHT)
    assert (focus.mode, focus.where, focus.source, focus.line) == ("sleep", where, str(path), 1)
    assert focus.until == datetime(2030, 6, 13, 7, 0).astimezone()


def test_the_seat_focus_file_follows_a_seat_object_root(roots, tmp_path):
    from sessionmail._seat import Seat

    root = tmp_path / "elsewhere" / "ivan"
    roots.write(root / "focus", "sleep\n")
    layers = load_layers(Seat(name="ivan", source="TM_AGENT_ADDRESS", root=root), SID, {},
                         home=roots.home, locked=roots.locked)
    assert layers.focus(NIGHT).where == "seat"


def test_another_sessions_focus_is_not_mine(roots):
    roots.write(roots.session_focus(OTHER), "sleep\n")
    assert names(roots.load()) == ["built-in"]


# ── precedence, when several are set (O-a: fleet-wide, session, seat) ───────

def test_fleet_wide_then_session_then_seat(roots):
    roots.write(roots.locked, "focus sleep until 03:00\n")
    roots.write(roots.session_focus(), "sleep until 05:00\n")
    roots.write(roots.seat_focus(), "sleep until 09:00\n")
    layers = roots.load()
    assert [m.where for m in layers.modes] == ["fleet", "session", "seat"]
    assert layers.focus(NIGHT).where == "fleet"
    assert layers.focus(datetime(2030, 6, 13, 4, 0)).where == "session"
    assert layers.focus(datetime(2030, 6, 13, 6, 0)).where == "seat"
    assert layers.focus(datetime(2030, 6, 13, 9, 30)) is None


def test_one_focus_layer_decides_the_rest_are_not_read():
    """Only ONE mode is in effect: the judgement reads the first focus layer
    still on and passes over the others. Two made-up modes show it (only
    `sleep` ships, and two sleeps look alike): the fleet's catches only
    Kith, so a direct mail goes past it; the session's `ride *` would have
    caught it, and must not."""
    fleet = Layer("focus: kith-off", parse("ride from:kith@*", "f")[0], focus="fleet")
    session = Layer("focus: sleep", parse("ride *", "s")[0], focus="session")
    builtin = rules.built_in()
    got = judge(DIRECT, [fleet, session, builtin], ME, NIGHT)
    assert (got.action, got.layer) == ("wake", "built-in")
    # With the fleet's mode over, the session's is the one in effect.
    ended = Layer("focus: kith-off", fleet.rules, focus="fleet",
                  until=datetime(2030, 6, 13, 1, 0).astimezone())
    got = judge(DIRECT, [ended, session, builtin], ME, NIGHT)
    assert (got.action, got.layer) == ("ride", "focus: sleep")


def test_an_ended_focus_layer_is_passed_over_in_the_breakthrough_pass_too():
    """An ended mode is not there at all: not even a `wake!` of its own."""
    gone = Layer("focus: odd", parse("wake! from:eve@*", "f")[0], focus="session",
                 until=datetime(2030, 6, 13, 1, 0).astimezone())
    builtin = rules.built_in()
    got = judge(mail(("From", "eve@hq"), ("X-Original-To", "atomnote")),
                [gone, builtin], ME, NIGHT)
    assert (got.action, got.layer) == ("ride", "built-in")


def test_a_layer_that_is_not_a_focus_layer_never_ends():
    """`until` belongs to a mode. The other layers carry none."""
    layer = Layer("session", parse("ride *", "s")[0])
    assert layer.focus is None and layer.until is None
    assert not layer.ended(datetime(2099, 1, 1))


# ── the focus file: its form, and a bad one named, never guessed ────────────

@pytest.mark.parametrize("text, mode, until", [
    ("sleep\n", "sleep", None),
    ("sleep until 07:00\n", "sleep", datetime(2030, 6, 13, 7, 0)),
    ("  sleep   until   7:00  # back at seven\n", "sleep", datetime(2030, 6, 13, 7, 0)),
    ("# set by ivan\n\nsleep until 23:30\n", "sleep", datetime(2030, 6, 12, 23, 30)),
    ("sleep until 2030-06-15T06:00\r\n", "sleep", datetime(2030, 6, 15, 6, 0)),
])
def test_the_focus_file_form(text, mode, until):
    focus, errors = parse_focus(text, "focus", SET_AT)
    assert errors == ()
    assert focus.mode == mode
    assert focus.until == (until.astimezone() if until else None)


@pytest.mark.parametrize("text", ["", "\n", "# nothing\n", "off\n", "off  # awake\n"])
def test_an_empty_or_off_focus_file_is_no_mode(text):
    assert parse_focus(text, "focus", SET_AT) == (None, ())


@pytest.mark.parametrize("text, reason", [
    ("slep\n", "unknown mode"),
    ("Sleep\n", "unknown mode"),
    ("airplane\n", "unknown mode"),
    ("sleep until\n", "until"),
    ("sleep until 7\n", "time"),
    ("sleep until 25:00\n", "time"),
    ("sleep until tomorrow\n", "time"),
    ("sleep at 07:00\n", "until"),
    ("sleep until 07:00 please\n", "until"),
    ("off until 07:00\n", "off"),
])
def test_a_bad_focus_line_is_named_and_sets_no_mode(text, reason):
    """A line that half-parses is dropped whole (as a rules line is): a
    typo'd end time does not put a session to sleep forever, and an unknown
    mode is not guessed at. The error names the file and the line."""
    focus, errors = parse_focus(text, "/x/focus", SET_AT)
    assert focus is None
    (err,) = errors
    assert (err.source, err.line) == ("/x/focus", 1)
    assert reason in err.reason


def test_a_second_mode_line_is_named_and_the_first_stands():
    focus, errors = parse_focus("sleep\nsleep until 07:00\n", "focus", SET_AT)
    assert focus.mode == "sleep" and focus.until is None
    (err,) = errors
    assert err.line == 2
    assert "one" in err.reason


def test_a_bad_focus_file_is_named_in_the_layers_errors(roots):
    path = roots.write(roots.session_focus(), "slep\n")
    layers = roots.load()
    assert names(layers) == ["built-in"]
    (err,) = layers.errors
    assert (err.source, err.line) == (str(path), 1)


def test_a_bad_fleet_focus_line_is_named_with_its_locked_line(roots):
    roots.write(roots.locked, "wake! from:alerts@*\nfocus slep\n")
    layers = roots.load()
    assert names(layers) == ["locked", "built-in"]
    (err,) = layers.errors
    assert (err.source, err.line) == (str(roots.locked), 2)
    assert "unknown mode" in err.reason


def test_a_focus_file_that_cannot_be_read_is_named_not_raised(roots):
    roots.session_focus().mkdir(parents=True)
    layers = roots.load()
    assert names(layers) == ["built-in"]
    (err,) = layers.errors
    assert (err.source, err.line) == (str(roots.session_focus()), 0)


def test_focus_is_frozen_and_parse_focus_is_pure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    focus, _ = parse_focus("sleep until 07:00\n", "focus", SET_AT)
    with pytest.raises(AttributeError):
        focus.mode = "off"  # type: ignore[misc]
    assert parse_focus("sleep until 07:00\n", "focus", SET_AT) == (focus, ())
    assert list(tmp_path.iterdir()) == []
    assert isinstance(focus, Focus)


def test_the_fleet_focus_line_splits_at_any_white_space(roots):
    roots.write(roots.locked, "focus\tsleep\tuntil 07:00\n")
    layers = roots.load()
    assert layers.errors == ()
    assert layers.focus(NIGHT).where == "fleet"


def test_a_bare_focus_line_in_the_locked_file_is_named(roots):
    roots.write(roots.locked, "focus\n")
    layers = roots.load()
    assert layers.modes == ()
    (err,) = layers.errors
    assert (err.line, err.text) == (1, "focus")
    assert "needs a mode" in err.reason
