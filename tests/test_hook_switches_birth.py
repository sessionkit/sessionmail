"""The birth honours its own switch, `birth`.

`pager hook start` (SessionStart) births this session's box. The birth
needs only `birth`; the box line it says also needs `hooks` (D3):

- `"birth": false` is OFF: no box under the mail root, the generator is
  never started, nothing is said (stdout and stderr both empty, no inject
  row), nothing is written anywhere, exit 0. Whatever `hooks` says, a fault
  included, and on a required seat too;
- `"birth": true`, or absent, is ON: the box is born and the generator runs;
- `"hooks": false` with `birth` absent: born, silent. The speech gate is
  unchanged;
- a value that is not a boolean is ignored: the birth acts as if the key were
  absent. The birth never says the switch_ignored line (the ride-along's).

The file-level `Switches.why` (decided on purpose): a file that decides
nothing for the switches - not JSON, bytes that are not text, not an
object, unreadable, a symlink to nothing - leaves `birth` ON, and the birth
stays SILENT about it. Every such file is a fault for `hooks` too, so the speech
gate is shut whatever this hook might say, and the ride-along's fault line
names the file at the first batch. The tests below pin both halves: the box
is born and nothing is said, and each such file is a `hooks` fault.

Hermetic, by the do-001 table's rig: HOME, the state home and the mail root
are tmp; the generator is a fake whose runs are asserted; the fake
SESSIONMAIL_SENDMAIL is asserted never run; the session id and the seat are
made up; no date is written. The table's sweep of the REAL home runs at the
end of this module too.
"""

from __future__ import annotations

import json
import time

import pytest
from click.testing import CliRunner

from sessionmail import _seat, _settings, pager
from sessionmail.pager import cli

from test_hook_switches_table import (  # noqa: F401 - real_home_untouched is autouse
    AS_ROOT, BORN, INSTALL, LOG, SEATS, SID, Out, born_line, make_rig, real_home_untouched,
    snapshot, start, written)

#: How long a detached generator, were one started, is given to show itself.
GEN_SETTLE = 0.2


def _file(data: dict) -> bytes:
    return (json.dumps({"version": 1, **data}) + "\n").encode()


def _rig(make_rig, data: dict | bytes, required: bool = False):
    """A rig on the hooks-true row, its file then replaced by DATA."""
    rig = make_rig("hooks-true", required)
    _settings.path().write_bytes(data if isinstance(data, bytes) else _file(data))
    return rig


def _start_off(rig) -> Out:
    """`pager hook start` where nothing is expected to be born: the table's
    `start` waits for the generator's line in the born record, so this runs
    the hook alone and gives a detached generator, were one started, a
    moment to show itself before the snapshot."""
    before = snapshot(rig.roots())
    r = CliRunner().invoke(cli, ["hook", "start"], input=json.dumps({
        "hook_event_name": "SessionStart", "source": "startup", "session_id": SID}))
    time.sleep(GEN_SETTLE)
    return Out(r.exit_code, [], None, r.stdout, r.stderr, written(before, snapshot(rig.roots())))


def _assert_nothing(rig, out: Out) -> None:
    assert out.exit == 0 and out.stdout == "" and out.stderr == "", out
    assert out.files == set(), sorted(out.files)
    assert not (rig.root / SID).exists()
    assert rig.generator_runs() == 0


def _assert_born(rig, out: Out, said: bool) -> None:
    assert out.exit == 0 and out.stderr == "", out
    assert all((rig.root / SID / d).is_dir() for d in ("cur", "new", "tmp"))
    assert rig.generator_runs() == 1
    assert out.person is None
    if said:
        assert out.said == [born_line(rig)], out.said
        assert out.files == BORN | INSTALL | LOG, sorted(out.files)
    else:
        assert out.stdout == "", out.stdout
        assert out.files == BORN | INSTALL, sorted(out.files)


def test_the_switch_key_is_birth():
    assert _settings.BIRTH == "birth"
    assert _settings.BIRTH in _settings.SWITCH_KEYS


# ── off: "birth": false ─────────────────────────────────────────────────────

OFF_FILES = [
    pytest.param({"hooks": True, "birth": False}, id="hooks-true"),
    pytest.param({"hooks": False, "birth": False}, id="hooks-false"),
    pytest.param({"birth": False}, id="hooks-key-missing"),
    pytest.param({"hooks": "true", "birth": False}, id="hooks-not-a-boolean"),
    pytest.param({k: False for k in _settings.SWITCH_KEYS} | {"hooks": False}, id="all-off"),
]


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data", OFF_FILES)
def test_birth_false_births_nothing_and_says_nothing(make_rig, data, required):
    """No box, no generator, no line, no row, no file: exit 0. A second
    SessionStart (a resume) is the same."""
    rig = _rig(make_rig, data, required)
    for _ in range(2):
        _assert_nothing(rig, _start_off(rig))


def test_birth_false_with_no_mail_root_says_nothing_either(make_rig):
    """The failed birth's one line (stderr, or the model's with the hooks
    on) is the birth's: with the birth off there is no birth to fail."""
    rig = _rig(make_rig, {"hooks": True, "birth": False})
    rig.root.rmdir()
    _assert_nothing(rig, _start_off(rig))
    assert not rig.root.exists()


def test_birth_false_does_not_touch_a_box_already_there(make_rig):
    """Off is off for this hook: a box born earlier (the switch flipped
    since) is left exactly as it is, and the generator is not run for it."""
    rig = _rig(make_rig, {"hooks": True})
    _assert_born(rig, start(rig), said=True)
    _settings.path().write_bytes(_file({"hooks": True, "birth": False}))
    out = _start_off(rig)
    assert out.exit == 0 and out.stdout == "" and out.stderr == "", out
    assert out.files == set(), sorted(out.files)
    assert rig.generator_runs() == 1          # the earlier birth's run, alone


def test_birth_false_beats_the_pager_switch_too(make_rig, monkeypatch):
    """SESSIONMAIL_PAGER=off silences the birth's line; `birth` false is what
    stops the birth itself. With both, nothing."""
    rig = _rig(make_rig, {"hooks": True, "birth": False})
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    _assert_nothing(rig, _start_off(rig))


# ── on: "birth": true, or absent ────────────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data", [{"hooks": True, "birth": True}, {"hooks": True}],
                         ids=["birth-true", "birth-absent"])
def test_birth_on_with_hooks_on_is_born_and_says_the_box_line(make_rig, data, required):
    rig = _rig(make_rig, data, required)
    _assert_born(rig, start(rig), said=True)


@pytest.mark.parametrize("data", [{"hooks": False, "birth": True}, {"hooks": False},
                                  {"birth": True}],
                         ids=["hooks-false-birth-true", "hooks-false-birth-absent",
                              "hooks-key-missing-birth-true"])
def test_birth_on_with_hooks_off_is_born_silent(make_rig, data):
    """The birth needs only `birth`; its line needs `hooks` too (the speech
    gate is unchanged): off or a fault, born and silent."""
    rig = _rig(make_rig, data)
    _assert_born(rig, start(rig), said=False)


@pytest.mark.parametrize("value", ["no", 0, None, [], {}],
                         ids=["string", "zero", "null", "list", "object"])
def test_a_non_boolean_birth_acts_as_absent(make_rig, value):
    """Ignored, so on: born, and the box line alone - the birth never says
    switch_ignored (the ride-along does, once per session)."""
    rig = _rig(make_rig, {"hooks": True, "birth": value})
    assert _settings.switches().birth.ignored
    out = start(rig)
    _assert_born(rig, out, said=True)
    assert not any("ignored" in line for line in out.said), out.said


# ── a file that decides nothing for the switches: on, and silent ────────────

def _dangling(rig) -> None:
    p = _settings.path()
    p.unlink()
    p.symlink_to(rig.tmp / "nothing-here.json")


def _unreadable(rig) -> None:
    _settings.path().chmod(0o000)


WHY_FILES = [
    pytest.param(b'{"birth": false', None, id="not-json"),
    pytest.param(b'\xff{"birth": false}', None, id="not-utf8"),
    pytest.param(b'[{"birth": false}]', None, id="not-an-object"),
    pytest.param(_file({"hooks": True, "birth": False}), _unreadable, id="unreadable"),
    pytest.param(b"", _dangling, id="dangling-symlink"),
]


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data,after", WHY_FILES)
def test_a_file_that_decides_nothing_leaves_the_birth_on_and_silent(
        make_rig, data, after, required):
    """The decision this slice pins: `Switches.why` is set, so every switch
    is ON and the box is born; and the birth says NOTHING about the file -
    no stdout, no stderr. Each such file is a `hooks` fault, so the speech
    gate is shut whatever the birth might say (and the ride-along's fault
    line names it). Even a `"birth": false` the file cannot be read for
    never turns the birth off."""
    if AS_ROOT and after is _unreadable:
        pytest.skip("root reads a mode-000 file")
    rig = _rig(make_rig, data, required)
    if after is not None:
        after(rig)
    sw = _settings.switches()
    assert sw.why and sw.birth.on and not sw.birth.set, sw
    settings = _settings.read()
    assert settings.fault and not settings.hooks, settings
    _assert_born(rig, start(rig), said=False)

