"""The per-hook switches' reader: `_settings.switches`.

One rule for all four keys (`ride_along`, `stop_nag`, `mail_guard`, `birth`):
a key that is absent means ON, only the boolean `false` turns its hook off,
and any other value is ignored (ON) with a reason that names it. A file that
is missing, cannot be read, is not JSON or is not a JSON object gives four
switches that are ON.

The reader reads the host file alone and raw, and never folds in `hooks`:
`"hooks": false`, or a `hooks` fault, with no per-hook key, gives four
switches ON. The AND with `hooks` lives at each hook, not here.

Hermetic: HOME is a tmp dir and the reader is handed it; nothing sends, so
no carrier is set; the reader is asserted never to write under HOME; a sweep
of the REAL home's kit settings folder (read from the password database) at
the end of the module asserts that nothing was written there.
"""

from __future__ import annotations

import errno
import json
import os
import pwd
import subprocess
import time
from pathlib import Path

import pytest

from sessionmail import _settings

KEYS = _settings.SWITCH_KEYS
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
NOT_AS_ROOT = pytest.mark.skipif(AS_ROOT, reason="root reads a mode-000 file")

ALL_ON = _settings.Switches()


def _home(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    home.mkdir()
    return home, {"HOME": str(home)}


def _write(home: Path, data: bytes) -> Path:
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _tree(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    """Every path under ROOT with its mode, size and (for a readable file)
    bytes: a snapshot to prove the reader wrote nothing."""
    out: dict[str, tuple[int, int, bytes | None]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            try:
                body = p.read_bytes() if p.is_file() and not p.is_symlink() else None
            except OSError:
                body = None
            out[str(p.relative_to(root))] = (st.st_mode, st.st_size, body)
    return out


def _ignored(key: str, value: object) -> str:
    return (f'"{key}": {json.dumps(value)} in {_settings.SHOWN} is ignored: '
            "only the boolean false turns it off, so it is on")


# ── the names ──────────────────────────────────────────────────────────────

def test_the_four_keys_are_the_files_snake_case_names():
    assert KEYS == ("ride_along", "stop_nag", "mail_guard", "birth")
    assert (_settings.RIDE_ALONG, _settings.STOP_NAG, _settings.MAIL_GUARD,
            _settings.BIRTH) == KEYS


def test_switches_has_one_field_per_key_and_indexes_by_key():
    sw = _settings.Switches()
    for key in KEYS:
        assert sw[key] is getattr(sw, key)
        assert sw[key] == _settings.Switch(on=True, set=False, ignored="")
    with pytest.raises(KeyError):
        sw["hooks"]
    with pytest.raises(KeyError):
        sw["why"]


# ── every value kind, for each key ─────────────────────────────────────────

#: Value kind -> (the JSON value written, or ABSENT; on; set; ignored?).
ABSENT = object()
VALUES = {
    "absent": (ABSENT, True, False, False),
    "true": (True, True, True, False),
    "false": (False, False, True, False),
    "string-false": ("false", True, True, True),
    "string-off": ("off", True, True, True),
    "string-empty": ("", True, True, True),
    "zero": (0, True, True, True),
    "one": (1, True, True, True),
    "float-zero": (0.0, True, True, True),
    "null": (None, True, True, True),
    "list": ([False], True, True, True),
    "object": ({"on": False}, True, True, True),
}


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("kind", list(VALUES))
@pytest.mark.parametrize("hooks", [True, False, "absent"], ids=["hooks-true", "hooks-false",
                                                                "hooks-absent"])
def test_one_key_under_the_one_rule(tmp_path, key, kind, hooks):
    """KEY holds a value of KIND beside a `hooks` that is true, false or
    absent (a `hooks` fault): only the boolean false turns KEY off, every
    other value is ignored with its reason, and the other three keys are
    untouched (on, not set). `hooks` decides nothing here."""
    value, on, is_set, ignored = VALUES[kind]
    home, env = _home(tmp_path)
    data: dict[str, object] = {"version": 1}
    if hooks != "absent":
        data["hooks"] = hooks
    if value is not ABSENT:
        data[key] = value
    _write(home, json.dumps(data).encode())
    before = _tree(home)

    sw = _settings.switches(env)

    assert sw[key] == _settings.Switch(on=on, set=is_set,
                                       ignored=_ignored(key, value) if ignored else "")
    for other in KEYS:
        if other != key:
            assert sw[other] == _settings.Switch(), other
    assert sw.why == ""
    assert _tree(home) == before


def test_all_four_off_at_once(tmp_path):
    home, env = _home(tmp_path)
    _write(home, json.dumps({"version": 1, "hooks": True,
                             **{k: False for k in KEYS}}).encode())
    sw = _settings.switches(env)
    assert [sw[k].on for k in KEYS] == [False] * 4
    assert all(sw[k].set and not sw[k].ignored for k in KEYS)


def test_mixed_values_are_each_read_on_their_own(tmp_path):
    home, env = _home(tmp_path)
    _write(home, json.dumps({"ride_along": False, "stop_nag": True,
                             "mail_guard": "no", "hooks": False}).encode())
    sw = _settings.switches(env)
    assert sw.ride_along == _settings.Switch(on=False, set=True)
    assert sw.stop_nag == _settings.Switch(on=True, set=True)
    assert sw.mail_guard == _settings.Switch(on=True, set=True,
                                             ignored=_ignored("mail_guard", "no"))
    assert sw.birth == _settings.Switch()
    assert sw.why == ""


def test_a_typo_never_turns_a_switch_off(tmp_path):
    """`"mail_gaurd": false` is not the guard's key: the guard stays on, and
    the reader says nothing about a key it does not own."""
    home, env = _home(tmp_path)
    _write(home, b'{"hooks": true, "mail_gaurd": false, "Birth": false, "stop-nag": false}')
    assert _settings.switches(env) == ALL_ON


def test_the_last_of_a_duplicated_key_wins_as_json_says(tmp_path):
    home, env = _home(tmp_path)
    _write(home, b'{"birth": true, "birth": false}')
    assert _settings.switches(env).birth == _settings.Switch(on=False, set=True)


def test_an_unshowable_value_is_still_ignored_never_raised(tmp_path, monkeypatch):
    """The reason names the value; if the value cannot be shown, it names its
    kind instead. The reader never raises for the file's sake."""
    home, env = _home(tmp_path)
    _write(home, b'{"birth": [[[]]]}')
    real_dumps = json.dumps

    def dumps(value, *a, **kw):
        if isinstance(value, list):
            raise RecursionError("too deep")
        return real_dumps(value, *a, **kw)

    monkeypatch.setattr(_settings.json, "dumps", dumps)
    sw = _settings.switches(env)
    assert sw.birth.on and sw.birth.set
    assert sw.birth.ignored.startswith('"birth": a list in ')


# ── every file state: all four on ──────────────────────────────────────────

def _not_an_object(kind: str, text: bytes):
    return pytest.param(text, f"the top level of {_settings.SHOWN} is a {kind}, not an object, "
                              "so every switch is on", id=f"not-an-object-{kind}")


NOT_JSON = f"{_settings.SHOWN} is not JSON, so every switch is on"


@pytest.mark.parametrize("text, why", [
    pytest.param(b'{"birth": false', NOT_JSON, id="not-json-truncated"),
    pytest.param(b"", NOT_JSON, id="not-json-empty"),
    pytest.param(b"birth: false\n", NOT_JSON, id="not-json-yaml"),
    pytest.param(b'{"birth": false}\xff', NOT_JSON, id="not-utf8"),
    pytest.param(b'{"birth": ' + b"1" * 5000 + b"}", NOT_JSON, id="not-json-huge-number"),
    _not_an_object("list", b'[{"birth": false}]'),
    _not_an_object("string", b'"birth"'),
    _not_an_object("number", b"0"),
    _not_an_object("boolean", b"false"),
    _not_an_object("null", b"null"),
])
def test_a_file_that_is_not_a_json_object_gives_all_four_on(tmp_path, text, why):
    home, env = _home(tmp_path)
    _write(home, text)
    before = _tree(home)
    sw = _settings.switches(env)
    assert sw == _settings.Switches(why=why)
    assert all(sw[k].on and not sw[k].set and not sw[k].ignored for k in KEYS)
    assert _tree(home) == before


def test_a_file_too_deep_to_parse_gives_all_four_on(tmp_path, monkeypatch):
    """A file nested too deep for the parser is "not JSON": the reader
    catches the RecursionError and gives all four on. The depth that
    overflows depends on the C stack (`ulimit -s`), so the parser is made to
    raise rather than fed a deep file (SM-1003 do-011)."""
    home, env = _home(tmp_path)
    _write(home, b'{"birth": false, "mail_guard": false}')
    before = _tree(home)
    calls = []

    def loads(*a, **kw):
        calls.append(a)
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(_settings.json, "loads", loads)
    sw = _settings.switches(env)
    assert calls, "the reader parsed the file with json.loads"
    assert sw == _settings.Switches(why=NOT_JSON)
    assert all(sw[k].on and not sw[k].set and not sw[k].ignored for k in KEYS)
    assert _tree(home) == before


def test_a_missing_file_gives_all_four_on_and_no_why(tmp_path):
    home, env = _home(tmp_path)
    sw = _settings.switches(env)
    assert sw == ALL_ON
    assert sw.why == ""
    assert _tree(home) == {}, "the reader never creates the file"


def test_a_missing_settings_folder_gives_all_four_on(tmp_path):
    home, env = _home(tmp_path)
    (home / _settings.ROOT).mkdir()
    assert _settings.switches(env) == ALL_ON


@NOT_AS_ROOT
def test_a_mode_000_file_gives_all_four_on_with_why(tmp_path):
    home, env = _home(tmp_path)
    p = _write(home, b'{"birth": false, "mail_guard": false}')
    p.chmod(0o000)
    try:
        sw = _settings.switches(env)
    finally:
        p.chmod(0o600)
    assert sw == _settings.Switches(why=f"{_settings.SHOWN} could not be read "
                                        f"({os.strerror(errno.EACCES)}), so every switch is on")


@NOT_AS_ROOT
def test_an_unsearchable_settings_folder_gives_all_four_on_with_why(tmp_path):
    home, env = _home(tmp_path)
    p = _write(home, b'{"birth": false}')
    p.parent.chmod(0o000)
    try:
        sw = _settings.switches(env)
    finally:
        p.parent.chmod(0o700)
    assert sw == _settings.Switches(why=f"{_settings.SHOWN} could not be read "
                                        f"({os.strerror(errno.EACCES)}), so every switch is on")


def test_a_directory_where_the_file_goes_gives_all_four_on_with_why(tmp_path):
    home, env = _home(tmp_path)
    (home / _settings.RELATIVE).mkdir(parents=True)
    sw = _settings.switches(env)
    assert all(sw[k] == _settings.Switch() for k in KEYS)
    assert sw.why.startswith(f"{_settings.SHOWN} could not be read (")
    assert sw.why.endswith("), so every switch is on")


def test_a_plain_file_where_the_folder_goes_gives_all_four_on_with_why(tmp_path):
    home, env = _home(tmp_path)
    folder = home / _settings.DIR
    folder.parent.mkdir(parents=True)
    folder.write_bytes(b"not a folder")
    sw = _settings.switches(env)
    assert all(sw[k] == _settings.Switch() for k in KEYS)
    assert sw.why == (f"{_settings.SHOWN} could not be read "
                      f"({os.strerror(errno.ENOTDIR)}), so every switch is on")


def test_a_dangling_symlink_gives_all_four_on_with_why(tmp_path):
    home, env = _home(tmp_path)
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.symlink_to(tmp_path / "nowhere.json")
    assert _settings.switches(env) == _settings.Switches(
        why=f"{_settings.SHOWN} could not be read ({os.strerror(errno.ENOENT)}, "
            "a symlink to nothing), so every switch is on")


def test_a_symlink_to_a_real_file_is_followed(tmp_path):
    home, env = _home(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_bytes(b'{"stop_nag": false}')
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.symlink_to(target)
    assert _settings.switches(env).stop_nag == _settings.Switch(on=False, set=True)


# ── `hooks` is not folded in: the AND lives at each hook ───────────────────

@pytest.mark.parametrize("text", [
    pytest.param(b'{"version": 1, "hooks": false}\n', id="hooks-false"),
    pytest.param(b'{"version": 1, "hooks": true}\n', id="hooks-true"),
    pytest.param(b'{"version": 1}\n', id="hooks-fault-no-key"),
    pytest.param(b'{"hooks": "true"}', id="hooks-fault-string"),
    pytest.param(b'{"hooks": 1}', id="hooks-fault-number"),
    pytest.param(b'{"hooks": null}', id="hooks-fault-null"),
    pytest.param(b'{"hook": true}', id="hooks-fault-typo"),
])
def test_hooks_with_no_per_hook_key_gives_four_switches_on(tmp_path, text):
    """`"hooks": false`, and every `hooks` fault, with no per-hook key: four
    switches ON. `read()` says what it always said; `switches()` does not
    look at `hooks`."""
    home, env = _home(tmp_path)
    _write(home, text)
    assert _settings.switches(env) == ALL_ON
    settings = _settings.read(env)
    assert settings.hooks is (text == b'{"version": 1, "hooks": true}\n')


def test_the_template_carries_no_switch_and_reads_all_on(tmp_path):
    """The template is unchanged: no per-hook key, so a fresh file is four
    switches ON (and `hooks` false, as before)."""
    assert not any(k in json.loads(_settings.template()) for k in KEYS)
    home, env = _home(tmp_path)
    _write(home, _settings.template().encode())
    assert _settings.switches(env) == ALL_ON
    assert _settings.read(env).state == _settings.OFF


def test_a_hooks_fault_beside_a_switch_still_reads_the_switch(tmp_path):
    """Read on its own, as `monitor` is: a file that is a fault for `hooks`
    can still say `"mail_guard": false`."""
    home, env = _home(tmp_path)
    _write(home, b'{"hooks": "yes", "mail_guard": false}')
    assert _settings.read(env).fault
    sw = _settings.switches(env)
    assert sw.mail_guard == _settings.Switch(on=False, set=True)
    assert sw.why == ""


def test_monitor_and_switches_read_the_same_file_independently(tmp_path):
    home, env = _home(tmp_path)
    _write(home, b'{"monitor": false, "birth": false}')
    assert _settings.monitor_host(env).off
    sw = _settings.switches(env)
    assert sw.birth.on is False
    assert all(sw[k] == _settings.Switch() for k in KEYS if k != "birth")


# ── where it reads ─────────────────────────────────────────────────────────

def test_it_reads_the_given_HOME_only(tmp_path):
    home, env = _home(tmp_path)
    other = tmp_path / "other"
    _write(other, b'{"birth": false}')
    assert _settings.switches(env) == ALL_ON
    _write(home, b'{"stop_nag": false}')
    assert _settings.switches(env).stop_nag.on is False
    assert _settings.switches({"HOME": str(other)}).birth.on is False


def test_no_env_reads_the_process_HOME(tmp_path, monkeypatch):
    home, _env = _home(tmp_path)
    _write(home, b'{"ride_along": false}')
    monkeypatch.setenv("HOME", str(home))
    assert _settings.switches().ride_along.on is False


def test_the_old_settings_homes_are_never_read(tmp_path):
    """Only the host file: not the pre-move `~/.session/mail/settings.json`,
    not the old folder `~/.settings/mail/`."""
    home, env = _home(tmp_path)
    old = home / _settings.OLD_RELATIVE
    old.parent.mkdir(parents=True)
    old.write_bytes(b'{"hooks": true, "birth": false}')
    legacy = home / _settings.LEGACY_DIR
    legacy.mkdir(parents=True)
    (legacy / "mail.json").write_bytes(b'{"hooks": true, "birth": false}')
    assert _settings.switches(env) == ALL_ON


# ── nothing reached the real home ──────────────────────────────────────────

def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.fixture(scope="module", autouse=True)
def real_home_untouched(tmp_path_factory):
    """A marker before the module; after it, `find -newer` over the real
    home's kit mail folder. The reader never writes anywhere; this proves the
    tests never reached the real host file either."""
    marker = tmp_path_factory.mktemp("sweep") / "marker"
    marker.touch()
    time.sleep(0.01)
    yield
    kit = _real_home() / _settings.DIR.parent
    if kit.is_dir():
        found = subprocess.run(["find", str(kit), "-newer", str(marker)],
                               capture_output=True, text=True).stdout.split()
        assert found == [], f"the run wrote under the real {kit}: {found}"
