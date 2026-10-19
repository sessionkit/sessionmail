"""`_settings` - the mail tool's host settings file,
`$HOME/.sessionkit/mail/settings/mail.json` (SM-873; until session-settings
3.1, `$HOME/.session/mail/settings.json`).

SM-450, openspec/changes/pager-settings-file, tasks.md 2.1-2.5 (slice 1).
Only the module and its two lines are tested here; what the hooks DO with
them is slice 2 (tasks.md 3.x, `tests/test_pager_hook.py`).

The `hermetic` fixture (conftest.py) points HOME at a sandbox, so nothing
here can read or create the developer's real file - the guard test at the
bottom pins that.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat, _settings, pager
from sessionmail.pager import cli

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = '{"version": 1, "hooks": false, "sendmail": "drop"}\n'
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def env_for(home: Path) -> dict[str, str]:
    return {"HOME": str(home)}


def settings_file(home: Path) -> Path:
    return home / ".sessionkit" / "mail" / "settings" / "mail.json"


def write(home: Path, data: bytes | str) -> Path:
    p = settings_file(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)
    return p


def leftovers(home: Path) -> list[str]:
    """Temp files `ensure` may have left: `.mail.json.<pid>.<random>`."""
    d = settings_file(home).parent
    return sorted(n for n in os.listdir(d) if n.startswith(".mail.json.")) if d.is_dir() else []


# ── 2.1 the template: one source ─────────────────────────────────────────

def test_the_template_is_one_file_and_says_off():
    on_disk = (ROOT / "src/sessionmail/settings.template.json").read_text()
    assert on_disk == TEMPLATE
    assert _settings.template() == TEMPLATE          # read through the package
    assert json.loads(TEMPLATE) == {"version": 1, "hooks": False, "sendmail": "drop"}


def test_the_template_ships_with_the_package():
    # named in `artifacts` so a later exclude cannot drop it silently (arm.txt's reason)
    text = (ROOT / "pyproject.toml").read_text()
    assert text.count('"src/sessionmail/settings.template.json"') == 2   # wheel and sdist


# ── 2.2 path ─────────────────────────────────────────────────────────────

def test_path_follows_HOME_in_the_env_given(tmp_path):
    assert _settings.path(env_for(tmp_path)) == tmp_path / ".sessionkit/mail/settings/mail.json"


def test_path_ignores_SESSIONMAIL_HOME(tmp_path, monkeypatch):
    # config is not state (RULED): SESSIONMAIL_HOME never moves it
    other = tmp_path / "state"
    monkeypatch.setenv("SESSIONMAIL_HOME", str(other))
    env = {"HOME": str(tmp_path / "me"), "SESSIONMAIL_HOME": str(other)}
    assert _settings.path(env) == tmp_path / "me/.sessionkit/mail/settings/mail.json"
    assert _settings.path() == Path(os.environ["HOME"]) / ".sessionkit/mail/settings/mail.json"
    assert other not in _settings.path(env).parents


def test_path_with_no_HOME_in_the_env_is_the_users_home():
    assert _settings.path({}) == Path.home() / ".sessionkit/mail/settings/mail.json"


def test_importing_the_module_does_no_io(tmp_path):
    # tasks.md 2.2: no I/O at import - a fresh interpreter whose HOME cannot be
    # listed still imports it, and nothing is created
    home = tmp_path / "h"
    home.mkdir()
    code = "import sessionmail._settings"
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "HOME": str(home)},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert list(home.iterdir()) == []


# ── 2.2 read ─────────────────────────────────────────────────────────────

def test_read_missing_is_missing_and_quiet(tmp_path):
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state, s.why, s.fault) == (False, "missing", "", False)
    assert not (tmp_path / ".sessionkit").exists()        # read never writes


def test_read_hooks_false_is_off_with_no_why(tmp_path):
    write(tmp_path, TEMPLATE)
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state, s.why, s.fault) == (False, "off", "", False)


def test_read_hooks_true_is_on(tmp_path):
    write(tmp_path, '{"version": 1, "hooks": true}\n')
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state, s.why, s.fault) == (True, "on", "", False)


@pytest.mark.parametrize("text, hooks, state", [
    ('{"version": 1, "hooks": true, "colour": "blue"}', True, "on"),    # unknown keys
    ('{"version": 7, "hooks": true}', True, "on"),                      # version never read
    ('{"version": 7, "hooks": false}', False, "off"),
    ('{"hooks": true}', True, "on"),                                     # no version at all
    ('{"version": "banana", "hooks": true, "required": false}', True, "on"),
])
def test_unknown_keys_and_version_change_nothing(tmp_path, text, hooks, state):
    write(tmp_path, text)
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state, s.why) == (hooks, state, "")


@pytest.mark.parametrize("data, names", [
    (b'{"hooks": true', "not JSON"),                                      # not JSON
    (b"", "not JSON"),                                                    # empty file
    (b"hooks = true\n", "not JSON"),
    (b"[]", "the top level is a list, not an object"),                    # a list
    (b'[{"hooks": true}]', "the top level is a list, not an object"),
    (b'"hooks"', "the top level is a string, not an object"),
    (b"true", "the top level is a boolean, not an object"),
    (b"null", "the top level is a null, not an object"),
    (b'{"hooks": "true"}', '`hooks` is "true", not true or false'),       # truthy string
    (b'{"hooks": "false"}', '`hooks` is "false", not true or false'),
    (b'{"hooks": 1}', "`hooks` is 1, not true or false"),                 # truthy number
    (b'{"hooks": null}', "`hooks` is null, not true or false"),
    (b'{"hooks": {"stop": true}}', '`hooks` is {"stop": true}, not true or false'),
    (b'{"version": 1}', "no `hooks` key"),                                # no hooks
    (b'{"hook": true}', "no `hooks` key"),                                # the typo D1 names
    (b"{}", "no `hooks` key"),
    (b'\xff\xfe{"hooks": true}', "not UTF-8"),
])
def test_read_a_malformed_file_is_a_fault_that_says_why(tmp_path, data, names):
    write(tmp_path, data)
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state, s.fault) == (False, "malformed", True)
    assert names in s.why
    assert "\n" not in s.why            # it goes on ONE line (settings_fault)


def test_read_a_directory_at_the_path_is_a_fault(tmp_path):
    settings_file(tmp_path).mkdir(parents=True)
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state) == (False, "malformed")
    assert "directory" in s.why.lower()


@pytest.mark.skipif(AS_ROOT, reason="root reads a mode-000 file")
def test_read_an_unreadable_file_is_a_fault(tmp_path):
    p = write(tmp_path, '{"hooks": true}')
    p.chmod(0)
    try:
        s = _settings.read(env_for(tmp_path))
    finally:
        p.chmod(0o600)
    assert (s.hooks, s.state) == (False, "malformed")
    assert "permission denied" in s.why.lower()


def test_read_a_dangling_symlink_is_a_fault_never_missing(tmp_path):
    p = settings_file(tmp_path)
    p.parent.mkdir(parents=True)
    p.symlink_to(p.parent / "nowhere.json")
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state) == (False, "malformed")
    assert "No such file" in s.why and "symlink" in s.why


def test_read_with_a_plain_file_where_the_directory_goes_is_a_fault(tmp_path):
    (tmp_path / ".sessionkit").write_text("not a directory")
    s = _settings.read(env_for(tmp_path))
    assert (s.hooks, s.state) == (False, "malformed")
    assert "not a directory" in s.why.lower()


def test_every_fault_has_a_why_and_nothing_else_does(tmp_path):
    cases = {"missing": None, "off": '{"hooks": false}', "on": '{"hooks": true}',
             "malformed": "{"}
    for state, text in cases.items():
        home = tmp_path / state
        home.mkdir()
        if text is not None:
            write(home, text)
        s = _settings.read(env_for(home))
        assert s.state == state
        assert bool(s.why) == s.fault == (state == "malformed")
        assert s.hooks == (state == "on")


# ── 2.2 ensure ───────────────────────────────────────────────────────────

def test_ensure_creates_the_parents_and_writes_exactly_the_template(tmp_path):
    assert not (tmp_path / ".sessionkit").exists()
    s, created = _settings.ensure(env_for(tmp_path))
    assert created is True
    assert (s.hooks, s.state, s.why) == (False, "off", "")
    assert settings_file(tmp_path).read_text() == TEMPLATE
    assert leftovers(tmp_path) == []


def test_ensure_a_second_time_creates_nothing(tmp_path):
    _settings.ensure(env_for(tmp_path))
    p = settings_file(tmp_path)
    before = p.stat()
    s, created = _settings.ensure(env_for(tmp_path))
    assert (created, s.state) == (False, "off")
    assert p.stat().st_mtime_ns == before.st_mtime_ns and p.stat().st_ino == before.st_ino


@pytest.mark.parametrize("data, state", [
    (b'{"version": 1, "hooks": true}\n', "on"),
    (b'{"hooks": true}', "on"),
    (b"{}", "malformed"),
    (b"\x00junk, not json at all\n", "malformed"),
    (b"", "malformed"),
])
def test_ensure_never_replaces_an_existing_file(tmp_path, data, state):
    p = write(tmp_path, data)
    os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))   # 2001: a rewrite would show
    before = p.stat()
    for _ in range(3):
        s, created = _settings.ensure(env_for(tmp_path))
        assert (created, s.state) == (False, state)
    assert p.read_bytes() == data
    after = p.stat()
    assert (after.st_mtime_ns, after.st_ino) == (before.st_mtime_ns, before.st_ino)
    assert leftovers(tmp_path) == []


def test_ensure_loses_the_race_when_the_file_appears_before_the_link(tmp_path, monkeypatch):
    # Another session wins between our read (missing) and our link: os.link
    # raises FileExistsError, this run created nothing, and says what is there.
    real_link = os.link
    theirs = b'{"version": 1, "hooks": true}\n'

    def racer_wins(src, dst, *a, **k):
        Path(dst).write_bytes(theirs)
        return real_link(src, dst, *a, **k)

    monkeypatch.setattr(_settings.os, "link", racer_wins)
    s, created = _settings.ensure(env_for(tmp_path))
    assert created is False
    assert (s.hooks, s.state) == (True, "on")               # what the winner wrote
    assert settings_file(tmp_path).read_bytes() == theirs   # ours never replaced it
    assert leftovers(tmp_path) == []


RACER = """
import os, sys, time
from sessionmail import _settings
go = sys.argv[1]
while not os.path.exists(go):
    time.sleep(0.001)
s, created = _settings.ensure({"HOME": sys.argv[2]})
print(created, s.state)
"""


def test_two_processes_on_one_HOME_create_it_exactly_once(tmp_path):
    home = tmp_path / "shared"
    home.mkdir()
    go = tmp_path / "go"
    procs = [subprocess.Popen([sys.executable, "-c", RACER, str(go), str(home)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(8)]
    go.touch()
    results = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        results.append(out.split())
    assert sorted(r[0] for r in results) == ["False"] * 7 + ["True"]
    assert all(r[1] == "off" for r in results)
    assert settings_file(home).read_text() == TEMPLATE      # whole, once
    assert leftovers(home) == []


@pytest.mark.skipif(AS_ROOT, reason="root writes into a read-only HOME")
def test_an_unwritable_HOME_is_off_with_the_os_error_and_the_next_call_creates(tmp_path):
    home = tmp_path / "ro"
    home.mkdir()
    home.chmod(0o500)
    try:
        s, created = _settings.ensure(env_for(home))        # never raises
        assert created is False
        assert (s.hooks, s.state, s.fault) == (False, "malformed", True)
        assert "permission denied" in s.why.lower()
        assert not (home / ".sessionkit").exists()
    finally:
        home.chmod(0o700)
    s, created = _settings.ensure(env_for(home))            # writable now: try again
    assert (created, s.state) == (True, "off")
    assert settings_file(home).read_text() == TEMPLATE


@pytest.mark.skipif(AS_ROOT, reason="root writes into a read-only directory")
def test_an_unwritable_settings_directory_leaves_no_temp_file(tmp_path):
    d = settings_file(tmp_path).parent
    d.mkdir(parents=True)
    d.chmod(0o500)
    try:
        s, created = _settings.ensure(env_for(tmp_path))
        assert (created, s.state) == (False, "malformed")
        assert "permission denied" in s.why.lower()
        assert leftovers(tmp_path) == []
    finally:
        d.chmod(0o700)


def test_a_plain_file_where_the_directory_goes_is_a_fault_not_a_crash(tmp_path):
    (tmp_path / ".sessionkit").write_text("a file, not a directory")
    s, created = _settings.ensure(env_for(tmp_path))
    assert (created, s.hooks, s.state) == (False, False, "malformed")
    assert s.why
    assert (tmp_path / ".sessionkit").read_text() == "a file, not a directory"


def test_a_link_that_fails_otherwise_is_a_fault_and_leaves_no_temp_file(tmp_path, monkeypatch):
    # EPERM: a filesystem that does not support hard links (D4 step 5)
    def no_links(src, dst, *a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(_settings.os, "link", no_links)
    s, created = _settings.ensure(env_for(tmp_path))
    assert (created, s.state) == (False, "malformed")
    assert s.why == "Operation not permitted"
    assert not settings_file(tmp_path).exists()
    assert leftovers(tmp_path) == []


def test_a_dangling_symlink_is_not_replaced_and_is_a_fault(tmp_path):
    p = settings_file(tmp_path)
    p.parent.mkdir(parents=True)
    target = p.parent / "nowhere.json"
    p.symlink_to(target)
    s, created = _settings.ensure(env_for(tmp_path))
    assert created is False
    assert (s.hooks, s.state) == (False, "malformed")        # never a silent "missing"
    assert "No such file" in s.why
    assert p.is_symlink() and os.readlink(p) == str(target)   # untouched
    assert not target.exists()                                # and not followed
    assert leftovers(tmp_path) == []


def test_a_deleted_file_is_created_again(tmp_path):
    _, first = _settings.ensure(env_for(tmp_path))
    settings_file(tmp_path).unlink()
    _, again = _settings.ensure(env_for(tmp_path))
    _, third = _settings.ensure(env_for(tmp_path))
    assert (first, again, third) == (True, True, False)


# ── 2.3 the two lines, in hook_lines.toml ────────────────────────────────

def test_the_creation_note_says_the_five_things_on_one_line():
    where = "/home/x/.sessionkit/mail/settings/mail.json"
    note = pager.SETTINGS_CREATED.format(path=where)
    assert "\n" not in note
    assert where in note                                   # 1. where it was written
    assert "OFF by default" in note                        # 2. off by default
    assert "mail" in note and "pager listen" in note       # 3. what the hooks are for
    assert '"hooks": true' in note                         # 4. the ONE setting
    assert "sessionmail:pager skill" in note               # 5. for more
    assert (ROOT / "skills/pager/SKILL.md").is_file()      # ...a skill that exists
    assert pager.ARM_HOW not in note                       # never restates arm.txt


def test_the_fault_line_says_treated_as_off_verbatim():
    line = pager.SETTINGS_FAULT.format(path="/h/.sessionkit/mail/settings/mail.json", error="no `hooks` key")
    assert line == "sessionmail: /h/.sessionkit/mail/settings/mail.json is treated as off: no `hooks` key"
    assert "treated as off" in pager.SETTINGS_FAULT


def test_every_why_fits_the_fault_line(tmp_path):
    for i, data in enumerate([b"{", b"[]", b'{"hooks": 1}', b"{}"]):
        home = tmp_path / str(i)
        home.mkdir()
        write(home, data)
        s = _settings.read(env_for(home))
        line = pager.SETTINGS_FAULT.format(path=_settings.path(env_for(home)), error=s.why)
        assert len(line.splitlines()) == 1 and "treated as off" in line and s.why in line


# ── 2.5 the guard: the suite never touches the real file ─────────────────

def real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def snapshot(p: Path):
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def test_the_hermetic_HOME_is_a_sandbox_not_the_real_one(tmp_path, monkeypatch):
    home = Path(os.environ["HOME"])
    assert home.is_dir()
    assert home != real_home()
    assert home != Path(os.environ["SESSIONMAIL_HOME"])      # config is not state
    real = real_home() / ".sessionkit/mail/settings/mail.json"
    before = snapshot(real)

    # what the hooks call, with the environment the fixture gave them
    s, created = _settings.ensure()
    assert (created, s.state) == (True, "off")
    assert _settings.path() == home / ".sessionkit/mail/settings/mail.json"
    assert _settings.path().read_text() == TEMPLATE

    # and a hook run itself, as a seat: whatever it writes lands in the sandbox
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "99999999-0000-0000-0000-000000000000")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    for event in ("batch", "stop"):
        r = CliRunner().invoke(cli, ["hook", event], input="{}")
        assert r.exit_code in (0, 2), r.output

    assert snapshot(real) == before                           # the real file: untouched


# ── session-settings 3.1: the move from ~/.session/mail/settings.json ────
# sessiontools openspec/changes/session-settings, tasks 3.1; the move rule
# (RULED): a conversion, the new
# writer and a symlink at the old path, shipped together.

def old_file(home: Path) -> Path:
    return home / ".session" / "mail" / "settings.json"


def write_old(home: Path, data: str) -> Path:
    p = old_file(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    return p


def test_the_new_path_is_the_tools_name_at_the_host_layer(tmp_path):
    assert _settings.TOOL == "mail"
    assert _settings.path(env_for(tmp_path)) == tmp_path / ".sessionkit/mail/settings/mail.json"
    assert _settings.old_path(env_for(tmp_path)) == old_file(tmp_path)


def test_an_old_file_is_converted_not_created(tmp_path):
    """The host's setting carries over: hooks ON stays on, and `created` is
    False - nothing was written OFF, so there is no first-run note."""
    env = env_for(tmp_path)
    body = '{"version": 1, "hooks": true}\n'
    write_old(tmp_path, body)
    s, created = _settings.ensure(env)
    assert (s.hooks, s.state, created) == (True, "on", False)
    assert settings_file(tmp_path).read_text() == body           # its bytes, whole
    assert old_file(tmp_path).is_symlink()
    assert old_file(tmp_path).resolve() == settings_file(tmp_path).resolve()
    assert old_file(tmp_path).read_text() == body                # an old reader still reads it
    assert not os.path.isabs(os.readlink(old_file(tmp_path)))    # relative: HOME can move
    assert leftovers(tmp_path) == []
    assert [n for n in os.listdir(old_file(tmp_path).parent) if n != "settings.json"] == []


def test_a_faulty_old_file_converts_as_it_is(tmp_path):
    """The conversion copies bytes; it never repairs. The fault reads the
    same at the new path, and is not a created file."""
    env = env_for(tmp_path)
    write_old(tmp_path, '{"hook": true}')
    s, created = _settings.ensure(env)
    assert (s.state, s.why, created) == ("malformed", "no `hooks` key", False)
    assert settings_file(tmp_path).read_text() == '{"hook": true}'
    assert old_file(tmp_path).is_symlink()


def test_no_old_file_is_the_template_and_no_old_dir_is_made(tmp_path):
    env = env_for(tmp_path)
    s, created = _settings.ensure(env)
    assert (s.state, created) == ("off", True)
    assert settings_file(tmp_path).read_text() == TEMPLATE
    assert not (tmp_path / ".session").exists()          # no symlink for nothing


def test_a_converted_host_is_not_converted_again(tmp_path):
    env = env_for(tmp_path)
    write_old(tmp_path, '{"version": 1, "hooks": true}\n')
    _settings.ensure(env)
    write(tmp_path, '{"version": 1, "hooks": false}\n')    # a later edit, at the new path
    s, created = _settings.ensure(env)
    assert (s.state, created) == ("off", False)
    assert old_file(tmp_path).read_text() == '{"version": 1, "hooks": false}\n'


def test_a_dangling_old_symlink_is_not_a_source(tmp_path):
    """Converted once, then the new file was removed: the old path is a
    symlink to nothing. That is no file to carry over, so the template is
    written, and the symlink resolves again."""
    env = env_for(tmp_path)
    write_old(tmp_path, '{"version": 1, "hooks": true}\n')
    _settings.ensure(env)
    settings_file(tmp_path).unlink()
    s, created = _settings.ensure(env)
    assert (s.state, created) == ("off", True)
    assert old_file(tmp_path).read_text() == TEMPLATE


def test_an_old_file_that_differs_from_the_new_is_never_replaced(tmp_path):
    """A hand edit to the OLD file after the copy is not thrown away: only an
    old file whose bytes the new one holds becomes the symlink."""
    env = env_for(tmp_path)
    write(tmp_path, '{"version": 1, "hooks": true}\n')
    write_old(tmp_path, '{"version": 1, "hooks": false}\n')
    s, created = _settings.ensure(env)
    assert (s.state, created) == ("on", False)
    assert not old_file(tmp_path).is_symlink()
    assert old_file(tmp_path).read_text() == '{"version": 1, "hooks": false}\n'



def test_drift_is_an_old_real_file_that_differs_from_the_new(tmp_path):
    """A review of 3.1: the case the
    ride-along must name, since the two files would say different things."""
    env = env_for(tmp_path)
    assert not _settings.drift(env)                      # neither file
    write_old(tmp_path, '{"version": 1, "hooks": false}\n')
    assert not _settings.drift(env)                      # no new file yet
    write(tmp_path, '{"version": 1, "hooks": true}\n')
    assert _settings.drift(env)
    write_old(tmp_path, '{"version": 1, "hooks": true}\n')
    assert not _settings.drift(env)                      # equal bytes
    _settings.ensure(env)
    assert old_file(tmp_path).is_symlink() and not _settings.drift(env)


def test_a_symlink_replaced_by_a_file_is_drift(tmp_path):
    """A writer that saves by temp file and rename turns the old path's
    symlink back into a real file, and the two then differ."""
    env = env_for(tmp_path)
    write_old(tmp_path, '{"version": 1, "hooks": true}\n')
    _settings.ensure(env)
    assert old_file(tmp_path).is_symlink()
    tmp = old_file(tmp_path).with_name("x.tmp")
    tmp.write_text('{"version": 1, "hooks": false}\n')
    os.replace(tmp, old_file(tmp_path))
    assert _settings.drift(env)


def test_read_never_converts(tmp_path):
    """The Stop hook reads; only `ensure` (the ride-along) converts."""
    env = env_for(tmp_path)
    write_old(tmp_path, '{"version": 1, "hooks": true}\n')
    assert _settings.read(env).state == "missing"         # new paths only (RULED)
    assert not old_file(tmp_path).is_symlink()
    assert not (tmp_path / ".sessionkit").exists()


def test_the_schema_and_template_are_installed_beside_the_file(tmp_path):
    env = env_for(tmp_path)
    _settings.ensure(env)
    d = settings_file(tmp_path).parent
    assert (d / "mail.schema.json").read_text() == _settings.schema()
    assert (d / "mail.template.json").read_text() == TEMPLATE
    (d / "mail.schema.json").write_text("{}")             # the release owns it
    _settings.ensure(env)
    assert (d / "mail.schema.json").read_text() == _settings.schema()
    assert sorted(os.listdir(d)) == ["mail.json", "mail.schema.json", "mail.template.json"]


def test_install_makes_no_directory_of_its_own(tmp_path):
    assert _settings.install(env_for(tmp_path)) == []
    assert not (tmp_path / ".sessionkit").exists()


def test_the_schema_is_2020_12_and_names_every_template_key():
    schema = json.loads(_settings.schema())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(json.loads(TEMPLATE)) <= set(schema["properties"])
    # keys added and removed over time are MAPS, so one entry can be set false
    for key in ("subscriptions", "rules"):
        assert schema["properties"][key]["type"] == "object"


def test_the_schema_ships_with_the_package():
    text = (ROOT / "pyproject.toml").read_text()
    assert text.count('"src/sessionmail/mail.schema.json"') == 2   # wheel and sdist
