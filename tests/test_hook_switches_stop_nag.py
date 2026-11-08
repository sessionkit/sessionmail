"""The Stop nag honours its own switch, `stop_nag`.

The Stop hook's nag (`pager hook stop`, exit 2 "not listening") acts iff
`hooks` is on AND `stop_nag` is not the boolean `false`:

- `"stop_nag": false` with `"hooks": true` is OFF: exit 0, nothing said,
  nothing written, on a seat whose pager is not required;
- `"stop_nag": true` (or absent) with `"hooks": true` is ON: exit 2;
- `"hooks": false` holds it off whatever `stop_nag` says (the AND);
- a value that is not a boolean is ignored: the nag acts as if the key were
  absent. Stop never says the switch_ignored line (that is the ride-along's).

The deaf check is NOT a switch's. It sits above the gate, so a required seat
hears DEAF from Stop with the nag off, and from the batch with the
ride-along off. With all four switches off it still does, from both.

The pair: the ride-along on and the nag off takes the mail and does not
nag; the ride-along off and the nag on nags and takes no mail.

Hermetic, by the do-001 table's rig: HOME, the state home and the mail root
are tmp; the generator is a fake whose runs are asserted zero; the fake
SESSIONMAIL_SENDMAIL is asserted never run; the session id and the seat are
made up; no date is written. The table's sweep of the REAL home runs at the
end of this module too.
"""

from __future__ import annotations

import json

import pytest

from sessionmail import _seat, _settings, pager

from test_hook_switches_table import (  # noqa: F401 - real_home_untouched is autouse
    GIVEN, INSTALL, LOG, MAIL_SUBJECT, SAID, SAID_DEAF, SEATS, assert_mail_then, batch,
    deaf, make_rig, real_home_untouched, state_line, stop)

#: Every per-hook switch, off.
ALL_FOUR_OFF = {key: False for key in _settings.SWITCH_KEYS}
#: The hook log, written again: it was there before the run.
LOG_AGAIN = {"~" + name[1:] for name in LOG}


def _file(data: dict) -> bytes:
    return (json.dumps({"version": 1, **data}) + "\n").encode()


def _rig(make_rig, data: dict, required: bool):
    """A rig on the hooks-true row, its file then replaced by DATA."""
    rig = make_rig("hooks-true", required)
    _settings.path().write_bytes(_file(data))
    return rig


def _assert_silent(out) -> None:
    assert out.exit == 0 and out.stdout == "" and out.stderr == "", out
    assert out.files == set(), sorted(out.files)


def _assert_nags(out) -> None:
    assert out.exit == 2 and out.stdout == "", out
    assert out.stderr.strip() == pager.LINE_NOT_LISTENING, out
    assert out.files == LOG, sorted(out.files)


def _assert_deaf(out, files=LOG) -> None:
    assert out.exit == 2 and out.stdout == "", out
    assert out.stderr.strip() == deaf(), out
    assert out.files == files, sorted(out.files)


def test_the_switch_key_is_stop_nag():
    assert _settings.STOP_NAG == "stop_nag"
    assert _settings.STOP_NAG in _settings.SWITCH_KEYS


# ── off: "stop_nag": false ──────────────────────────────────────────────────

def test_stop_nag_false_is_silent_on_a_plain_seat(make_rig):
    """Nothing listens, `hooks` is on, and still: exit 0, nothing said or
    written. A second Stop is the same."""
    rig = _rig(make_rig, {"hooks": True, "stop_nag": False}, False)
    for _ in range(2):
        _assert_silent(stop(rig))
    assert rig.generator_runs() == 0


def test_stop_nag_false_silences_the_no_session_line(make_rig, monkeypatch):
    """The hook's own "nothing to do" line is said only when the nag is on,
    as it is said only when `hooks` is on."""
    rig = _rig(make_rig, {"hooks": True}, False)
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    on = stop(rig)
    assert on.exit == 0 and "no session id" in on.stderr, on
    _settings.path().write_bytes(_file({"hooks": True, "stop_nag": False}))
    _assert_silent(stop(rig))


def test_stop_nag_false_on_a_required_seat_still_says_deaf(make_rig):
    """The deaf check is above the gate: the nag's switch does not reach it."""
    rig = _rig(make_rig, {"hooks": True, "stop_nag": False}, True)
    _assert_deaf(stop(rig))


# ── on: "stop_nag": true, or absent ─────────────────────────────────────────

@pytest.mark.parametrize("data", [{"hooks": True, "stop_nag": True}, {"hooks": True}],
                         ids=["stop-nag-true", "stop-nag-absent"])
def test_stop_nag_on_nags(make_rig, data):
    rig = _rig(make_rig, data, False)
    _assert_nags(stop(rig))
    assert rig.generator_runs() == 0


@pytest.mark.parametrize("value", ["no", 0, None, [], {}],
                         ids=["string", "zero", "null", "list", "object"])
def test_a_non_boolean_stop_nag_acts_as_absent(make_rig, value):
    """Ignored, so on; and Stop says the nag alone, never switch_ignored."""
    rig = _rig(make_rig, {"hooks": True, "stop_nag": value}, False)
    out = stop(rig)
    _assert_nags(out)
    assert "ignored" not in out.stderr


# ── the AND: "hooks" off holds the nag off ──────────────────────────────────

@pytest.mark.parametrize("data", [{"hooks": False, "stop_nag": True}, {"hooks": False}],
                         ids=["stop-nag-true", "stop-nag-absent"])
def test_hooks_false_holds_the_nag_off(make_rig, data):
    rig = _rig(make_rig, data, False)
    _assert_silent(stop(rig))


def test_a_hooks_fault_holds_the_nag_off(make_rig):
    """A file with no `hooks` key is a fault, and a fault never nags, even
    with `stop_nag` true."""
    rig = _rig(make_rig, {"stop_nag": True}, False)
    _assert_silent(stop(rig))


# ── SC2: the pair, each direction on one rig ────────────────────────────────

def test_pair_ride_along_on_and_nag_off_takes_the_mail_and_does_not_nag(make_rig):
    rig = _rig(make_rig, {"hooks": True, "ride_along": True, "stop_nag": False}, False)
    link = rig.queue_mail()
    took = batch(rig)
    assert took.exit == 0 and took.stderr == "", took
    assert_mail_then(took, state_line(False))
    assert MAIL_SUBJECT in "\n".join(took.said)
    assert GIVEN <= took.files, sorted(took.files)
    assert not link.exists(), "the queued mail was not taken"
    _assert_silent(stop(rig))
    assert rig.generator_runs() == 0


def test_pair_ride_along_off_and_nag_on_nags_and_takes_no_mail(make_rig):
    rig = _rig(make_rig, {"hooks": True, "ride_along": False, "stop_nag": True}, False)
    link = rig.queue_mail()
    took = batch(rig)
    assert took.exit == 0 and took.stderr == "" and took.said == [], took
    assert not took.files & GIVEN, sorted(took.files)
    assert link.exists(), "the queued mail was taken"
    _assert_nags(stop(rig))
    assert link.exists(), "the queued mail was taken"
    assert rig.generator_runs() == 0


# ── SC4: every switch off, and a required seat still hears DEAF ─────────────

@pytest.mark.parametrize("hooks", [True, False], ids=["hooks-true", "hooks-false"])
def test_all_four_off_on_a_required_seat_still_says_deaf(make_rig, hooks):
    """From the batch (DEAF, said once, by the ride-along's off path) and
    from Stop (exit 2, DEAF, every time). No mail is taken; a second batch
    is silent."""
    rig = _rig(make_rig, {"hooks": hooks, **ALL_FOUR_OFF}, True)
    link = rig.queue_mail()
    first, second = batch(rig), batch(rig)
    assert first.exit == 0 and first.stderr == "" and first.person is None, first
    assert first.said == [deaf()], first.said
    assert first.files == INSTALL | LOG | SAID_DEAF, sorted(first.files)
    assert not first.files & (GIVEN | SAID), "the mail was taken"
    assert second.said == [] and second.files == set(), second
    assert link.exists(), "the queued mail was taken"
    for _ in range(2):
        _assert_deaf(stop(rig), LOG_AGAIN)   # Stop says it every time, not once
    assert rig.generator_runs() == 0


@pytest.mark.parametrize("hooks", [True, False], ids=["hooks-true", "hooks-false"])
def test_all_four_off_on_a_plain_seat_is_silent(make_rig, hooks):
    """The other half of SC4: off is off where no pager is required."""
    rig = _rig(make_rig, {"hooks": hooks, **ALL_FOUR_OFF}, False)
    link = rig.queue_mail()
    _assert_silent(stop(rig))
    out = batch(rig)
    assert out.exit == 0 and out.said == [] and not out.files & GIVEN, out
    assert link.exists()


def test_pager_off_still_silences_deaf_with_all_four_off(make_rig, monkeypatch):
    """Only SESSIONMAIL_PAGER=off silences the deaf check, as before."""
    rig = _rig(make_rig, {"hooks": True, **ALL_FOUR_OFF}, True)
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    _assert_silent(stop(rig))


@pytest.mark.parametrize("required", SEATS)
def test_the_fixture_is_hermetic(make_rig, required):
    """The rig's HOME is the conftest's tmp HOME, never the real one, and
    the settings file it writes is under it."""
    import os
    import pwd
    _rig(make_rig, {"hooks": True, "stop_nag": False}, required)
    real = pwd.getpwuid(os.getuid()).pw_dir
    assert os.environ["HOME"] != real
    assert _settings.path().is_relative_to(os.environ["HOME"]), _settings.path()
