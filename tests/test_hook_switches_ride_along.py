"""The ride-along honours its own switch, `ride_along`.

The ride-along (`pager hook batch`, as PostToolBatch and as UserPromptSubmit)
acts iff `hooks` is on AND `ride_along` is not the boolean `false`:

- `"ride_along": false` with `"hooks": true` is OFF, by both entry points: no
  take, mark or stamp, and silent but for the drift/copied lines and DEAF;
- `"ride_along": true` (or absent) with `"hooks": true` is ON: mail given;
- `"hooks": false` holds it off whatever `ride_along` says (the AND).

A per-hook switch whose value is not a boolean is ignored, so its hook acts
as if the key were absent (on), and the ride-along says ONE switch_ignored
line naming each such key, once per session, with its own said-record, as
the drift line has. The line quotes each value bounded, never whole.

The pair with Stop: `ride_along` off leaves the nag on, and `stop_nag` off
leaves the ride-along on and silences Stop. Both directions, each on one
rig, are also in the Stop nag's own module.

Hermetic, by the do-001 table's rig: HOME, the state home and the mail root
are tmp; the generator is a fake whose runs are asserted zero; the fake
SESSIONMAIL_SENDMAIL is asserted never run; the session id and the seat are
made up; no date is written. The table's sweep of the REAL home runs at the
end of this module too.
"""

from __future__ import annotations

import json
import os

import pytest

from sessionmail import _seat, _settings, pager

from test_hook_switches_table import (  # noqa: F401 - real_home_untouched is autouse
    GIVEN, INSTALL, LOG, MAIL_SUBJECT, S, SAID, SAID_DEAF, SEATS, SID,
    assert_mail_then, batch, deaf, make_rig, real_home_untouched, state_line, stop, ups)

#: The switch_ignored line's record, as the snapshot names it.
SAID_IGNORED = {f"+{S}/mail/stamps/{_seat.SAID_IGNORED_STAMP}"}
SAID_DRIFT = {f"+{S}/mail/stamps/{_seat.SAID_DRIFT_STAMP}"}


def _file(data: dict) -> bytes:
    return (json.dumps({"version": 1, **data}) + "\n").encode()


def _rig(make_rig, data: dict, required: bool):
    """A rig on the hooks-true row, its file then replaced by DATA."""
    rig = make_rig("hooks-true", required)
    _settings.path().write_bytes(_file(data))
    return rig


def _reason(key: str, shown: str) -> str:
    return (f'"{key}": {shown} in {_settings.SHOWN} is ignored: '
            "only the boolean false turns it off, so it is on")


def _ignored_line(*reasons: str) -> str:
    return pager.SWITCH_IGNORED.format(ignored="; ".join(reasons))


# ── off: "ride_along": false with "hooks": true ─────────────────────────────

@pytest.mark.parametrize("required", SEATS)
def test_ride_along_false_is_off_on_post_tool_batch(make_rig, required):
    """The `hooks: false` path: no take, no mark, no stamp; silent but for
    DEAF on a required seat, said once."""
    rig = _rig(make_rig, {"hooks": True, "ride_along": False}, required)
    link = rig.queue_mail()
    first, second = batch(rig), batch(rig)
    for out in (first, second):
        assert out.exit == 0 and out.stderr == "" and out.person is None, out
    assert first.said == ([deaf()] if required else []), first.said
    assert first.files == INSTALL | ((LOG | SAID_DEAF) if required else set()), \
        sorted(first.files)
    assert not first.files & GIVEN, "the mail was taken"
    assert second.said == [] and second.files == set(), second
    assert link.exists(), "the queued mail was taken"
    assert rig.generator_runs() == 0


@pytest.mark.parametrize("required", SEATS)
def test_ride_along_false_is_a_strict_no_op_on_user_prompt_submit(make_rig, required):
    """A task notice with the page's carry stamp does not carry: nothing
    read but the gate, nothing written, nothing said; the stamp and the mail
    left where they were."""
    rig = _rig(make_rig, {"hooks": True, "ride_along": False}, required)
    link = rig.queue_mail()
    carry = rig.carry_stamp()
    out = ups(rig)
    assert out.exit == 0 and out.stderr == "", out
    assert out.stdout == "" and out.files == set(), (out.stdout, sorted(out.files))
    assert link.exists() and carry.exists()
    assert rig.generator_runs() == 0


def test_ride_along_false_still_says_the_drift_line(make_rig):
    """Off is silent BUT for the drift line: it is said on or off."""
    rig = _rig(make_rig, {"hooks": True, "ride_along": False}, False)
    rig.old.parent.mkdir(parents=True, exist_ok=True)
    rig.old.write_bytes(b'{"version": 1, "hooks": true}\n')   # differs from the new file
    link = rig.queue_mail()
    first, second = batch(rig), batch(rig)
    drift = pager.SETTINGS_DRIFT.format(old=_settings.old_path(), path=_settings.path())
    assert first.exit == 0 and first.said == [drift], first.said
    assert first.files == LOG | SAID_DRIFT | INSTALL, sorted(first.files)
    assert second.said == [] and second.files == set(), second
    assert link.exists()


# ── on: "ride_along": true, or absent ───────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data", [{"hooks": True, "ride_along": True}, {"hooks": True}],
                         ids=["ride-along-true", "ride-along-absent"])
def test_ride_along_on_gives_the_mail_on_post_tool_batch(make_rig, data, required):
    rig = _rig(make_rig, data, required)
    rig.queue_mail()
    first, second = batch(rig), batch(rig)
    assert first.exit == 0 and first.stderr == "", first
    assert_mail_then(first, state_line(required))
    assert first.files == LOG | GIVEN | SAID | INSTALL, sorted(first.files)
    assert second.said == [] and second.files == {f"+{S}/log/mail/quiet"}, second
    assert rig.generator_runs() == 0


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data", [{"hooks": True, "ride_along": True}, {"hooks": True}],
                         ids=["ride-along-true", "ride-along-absent"])
def test_ride_along_on_carries_on_user_prompt_submit(make_rig, data, required):
    rig = _rig(make_rig, data, required)
    rig.queue_mail()
    rig.carry_stamp()
    out = ups(rig)
    assert out.exit == 0 and out.stderr == "", out
    assert_mail_then(out, state_line(required))
    assert out.files == LOG | GIVEN | SAID | INSTALL | {f"-{S}/mail/stamps/carry"}, \
        sorted(out.files)


# ── the AND: "hooks": false holds it off whatever ride_along says ───────────

@pytest.mark.parametrize("required", SEATS)
def test_hooks_false_with_ride_along_true_is_off(make_rig, required):
    rig = _rig(make_rig, {"hooks": False, "ride_along": True}, required)
    link = rig.queue_mail()
    out = batch(rig)
    assert out.exit == 0 and out.said == ([deaf()] if required else []), out.said
    assert out.files == INSTALL | ((LOG | SAID_DEAF) if required else set()), sorted(out.files)
    carry = rig.carry_stamp()
    out = ups(rig)
    assert out.stdout == "" and out.files == set(), (out.stdout, sorted(out.files))
    assert link.exists() and carry.exists()


# ── switch_ignored: a value that is not a boolean ───────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("value", ["no", 0, None, [False], {"on": False}],
                         ids=["string", "zero", "null", "list", "object"])
def test_a_non_boolean_ride_along_acts_as_absent_and_is_said_once(make_rig, value, required):
    """The ride-along stays ON (mail given), and the switch_ignored line
    leads the first run's lines, then is never said again this session."""
    rig = _rig(make_rig, {"hooks": True, "ride_along": value}, required)
    rig.queue_mail()
    first = batch(rig)
    line = _ignored_line(_reason("ride_along", json.dumps(value)))
    assert first.exit == 0 and first.said[0] == line, first.said
    assert_mail_then(first, state_line(required))
    assert first.files == LOG | GIVEN | SAID | SAID_IGNORED | INSTALL, sorted(first.files)
    assert _seat.read_said(None, SID, _seat.SAID_IGNORED_STAMP) == line
    # Two more runs, and a turn start that carries: never re-said.
    for _ in range(2):
        out = batch(rig)
        assert line not in out.said, out.said
    rig.carry_stamp()
    out = ups(rig)
    assert line not in out.said, out.said


def test_switch_ignored_is_said_on_the_off_path_too(make_rig):
    """`"hooks": false` with an ignored guard value: off, and the line is
    said, once, as the drift line is - a typo in a switch is said on or off."""
    rig = _rig(make_rig, {"hooks": False, "mail_guard": "off"}, False)
    link = rig.queue_mail()
    first, second = batch(rig), batch(rig)
    line = _ignored_line(_reason("mail_guard", '"off"'))
    assert first.said == [line], first.said
    assert first.files == LOG | SAID_IGNORED | INSTALL, sorted(first.files)
    assert second.said == [] and second.files == set(), second
    assert link.exists()


def test_switch_ignored_beside_ride_along_false(make_rig):
    """`ride_along` false and `stop_nag` a string: the ride-along is off
    (no mail), and says only the switch_ignored line, once."""
    rig = _rig(make_rig, {"hooks": True, "ride_along": False, "stop_nag": "x"}, False)
    link = rig.queue_mail()
    first, second = batch(rig), batch(rig)
    assert first.said == [_ignored_line(_reason("stop_nag", '"x"'))], first.said
    assert not first.files & GIVEN
    assert second.said == [], second.said
    assert link.exists()


def test_switch_ignored_beside_a_hooks_fault(make_rig):
    """No `hooks` key (a fault) and an ignored birth value: the fault line
    and the switch_ignored line, each once."""
    rig = _rig(make_rig, {"birth": 0}, False)
    _settings.path().write_bytes(b'{"version": 1, "birth": 0}\n')
    first, second = batch(rig), batch(rig)
    fault = pager.SETTINGS_FAULT.format(path=_settings.path(), error="no `hooks` key")
    assert first.said == [_ignored_line(_reason("birth", "0")), fault], first.said
    assert second.said == [], second.said


def test_every_ignored_key_is_named_in_one_line_in_key_order(make_rig):
    rig = _rig(make_rig, {"hooks": True, "birth": "no", "ride_along": 1, "stop_nag": "",
                          "mail_guard": None}, False)
    out = batch(rig)
    assert out.said[0] == _ignored_line(
        _reason("ride_along", "1"), _reason("stop_nag", '""'),
        _reason("mail_guard", "null"), _reason("birth", '"no"')), out.said
    assert sum(ln.startswith("sessionmail:") for ln in out.said) == 1, out.said


def test_switch_ignored_is_said_again_only_when_it_changes(make_rig):
    rig = _rig(make_rig, {"hooks": True, "stop_nag": "a"}, False)
    first = batch(rig)
    assert first.said[0] == _ignored_line(_reason("stop_nag", '"a"')), first.said
    assert batch(rig).said == []
    _settings.path().write_bytes(_file({"hooks": True, "stop_nag": "b"}))
    changed = batch(rig)
    assert changed.said == [_ignored_line(_reason("stop_nag", '"b"'))], changed.said
    _settings.path().write_bytes(_file({"hooks": True}))     # fixed: nothing to say
    assert batch(rig).said == []


def test_no_ignored_value_says_no_line_and_writes_no_record(make_rig):
    rig = _rig(make_rig, {"hooks": True, "ride_along": True, "stop_nag": False,
                          "mail_guard": True, "birth": True}, False)
    out = batch(rig)
    assert not any("is ignored" in ln for ln in out.said), out.said
    assert not out.files & SAID_IGNORED


# ── the bound: a long value is never said whole ─────────────────────────────

def test_a_long_value_is_quoted_bounded(make_rig):
    """The reader's reason embeds the value's JSON; past SHOWN_VALUE_MAX it
    is cut and ends in "...", so a long string never lands in the model's
    context whole."""
    long = "x" * 5000
    rig = _rig(make_rig, {"hooks": True, "stop_nag": long, "birth": "y" * 600}, False)
    out = batch(rig)
    line = out.said[0]
    cut = '"' + "x" * (_settings.SHOWN_VALUE_MAX - 4) + "..."
    assert len(cut) == _settings.SHOWN_VALUE_MAX
    assert line == _ignored_line(
        _reason("stop_nag", cut),
        _reason("birth", '"' + "y" * (_settings.SHOWN_VALUE_MAX - 4) + "...")), line
    assert long not in line and "x" * _settings.SHOWN_VALUE_MAX not in line
    # The whole line is bounded: its words, plus at most the max per value.
    assert len(line) <= (len(_ignored_line(_reason("stop_nag", ""), _reason("birth", "")))
                         + 2 * _settings.SHOWN_VALUE_MAX)


def test_the_reader_bounds_every_ignored_value():
    """At the reader, for every caller (this line, the verb's listing): a
    value whose JSON fits is quoted whole; one past the bound is cut."""
    fits = "z" * (_settings.SHOWN_VALUE_MAX - 2)                  # its JSON is exactly the max
    assert len(json.dumps(fits)) == _settings.SHOWN_VALUE_MAX
    assert _settings._shown_value(fits) == json.dumps(fits)
    over = fits + "z"
    assert _settings._shown_value(over) == json.dumps(over)[:_settings.SHOWN_VALUE_MAX - 3] + "..."
    assert len(_settings._shown_value(over)) == _settings.SHOWN_VALUE_MAX
    big = list(range(1000))
    assert len(_settings._shown_value(big)) == _settings.SHOWN_VALUE_MAX
    # Escapes are kept: the JSON is ASCII, so nothing raw reaches a terminal.
    assert _settings._shown_value("\x1b[31m\n") == json.dumps("\x1b[31m\n")


def test_the_line_lives_in_hook_lines_toml():
    import tomllib
    from importlib import resources
    raw = tomllib.loads(resources.files("sessionmail").joinpath("hook_lines.toml")
                        .read_text(encoding="utf-8"))
    assert raw["switch_ignored"] == pager.SWITCH_IGNORED
    assert "{ignored}" in pager.SWITCH_IGNORED


# ── the pair with Stop ──────────────────────────────────────────────────────

def test_pair_ride_along_off_leaves_the_nag_on(make_rig):
    """`ride_along` false: the ride-along is off, and Stop still nags."""
    rig = _rig(make_rig, {"hooks": True, "ride_along": False}, False)
    link = rig.queue_mail()
    assert batch(rig).said == []
    out = stop(rig)
    assert out.exit == 2 and out.stderr.strip() == pager.LINE_NOT_LISTENING, out
    assert link.exists()


def test_pair_nag_off_leaves_the_ride_along_on(make_rig):
    """`stop_nag` false: the ride-along still gives the mail."""
    rig = _rig(make_rig, {"hooks": True, "stop_nag": False}, False)
    rig.queue_mail()
    out = batch(rig)
    assert_mail_then(out, state_line(False))
    assert MAIL_SUBJECT in "\n".join(out.said)


def test_pair_nag_off_silences_stop(make_rig):
    rig = _rig(make_rig, {"hooks": True, "stop_nag": False}, False)
    out = stop(rig)
    assert out.exit == 0 and out.stderr == "", out


def test_the_fixture_home_is_the_sandbox():
    """The rig's HOME is the conftest's tmp HOME, never the real one."""
    import pwd
    assert os.environ["HOME"] != pwd.getpwuid(os.getuid()).pw_dir
