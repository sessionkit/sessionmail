"""SM-983: `pager settings hooks [on|off]`, the supported verb that turns the
mail hooks on and off (for the kit installer, SK-236).

SM-983's design, sections (a) (b) (c) (d) (g),
with the super's rulings on its open questions:

- `_settings.with_value`: one top-level value changed in place, every other
  byte kept; `add_key` and the verb share one replace helper.
- `_settings.set_hooks(on, env)` -> `HooksResult`: judged before anything is
  written, `ensure` only when a write is coming, one atomic replace, the
  read-back last. Refused at euid 0 or under sudo (Q8).
- `pager settings hooks [on|off]`: one stdout line that starts with the
  state it left; exit 0 / 2 / 3 (c). One audit row on a FLIP only, in the
  host's mail log under `_settings.log_dir` (Q1), seated or seatless.

Every test runs under the suite's `hermetic` HOME (conftest.py); no test
touches the real one.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, _seat, _settings, pager
from sessionmail.pager import cli

SESSION = "0ab5deee-7ec5-467b-9eda-08e512cc0983"
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def home() -> Path:
    return Path(os.environ["HOME"])


def env() -> dict[str, str]:
    return {"HOME": str(home())}


def mail_json() -> Path:
    return home() / _settings.RELATIVE


def write(data: bytes | str) -> Path:
    p = mail_json()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)
    return p


def old_file() -> Path:
    return home() / _settings.OLD_RELATIVE


def write_old(data: str) -> Path:
    p = old_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    return p


def listing(d: Path) -> list[str]:
    return sorted(os.listdir(d)) if d.is_dir() else []


def tree(root: Path) -> list[str]:
    """Every path under ROOT, relative: what a refusal must leave alone."""
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def snap(p: Path):
    st = os.lstat(p)
    return st.st_ino, st.st_mtime_ns, p.read_bytes()


def hooks(*words: str):
    return CliRunner().invoke(cli, ["settings", "hooks", *words], prog_name="pager")


@pytest.fixture(autouse=True)
def no_seat(monkeypatch):
    """A person's shell: no session, no seat, no sudo, not root, and the
    pager variable unset, unless a test says otherwise."""
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("EMAIL", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
    if AS_ROOT:
        monkeypatch.setattr(os, "geteuid", lambda: 1000)


# ── with_value: one value, in place ──────────────────────────────────────

TEMPLATE = _settings.template()

#: design.md (b)'s spike table, each row with its exact bytes out (on).
WITH_VALUE_ON = [
    ("template", TEMPLATE, TEMPLATE.replace('"hooks": false', '"hooks": true')),
    ("tight", '{"version":1,"hooks":false}', '{"version":1,"hooks":true}'),
    ("pretty", '{\n    "version": 1,\n    "hooks"  :\n    false\n}\n',
     '{\n    "version": 1,\n    "hooks"  :\n    true\n}\n'),
    ("crlf", '{\r\n  "version": 1,\r\n  "hooks": false\r\n}\r\n',
     '{\r\n  "version": 1,\r\n  "hooks": true\r\n}\r\n'),
    ("no-key", '{"version": 1, "sendmail": "drop"}',
     '{"version": 1, "sendmail": "drop", "hooks": true}'),
    ("empty", "{}", '{"hooks": true}'),
    ("nested", '{"rules": {"hooks": false}, "hooks": false}',
     '{"rules": {"hooks": false}, "hooks": true}'),
    ("string", '{"hooks": "true"}', '{"hooks": true}'),
    ("one", '{"hooks": 1}', '{"hooks": true}'),
    ("zero", '{"hooks": 0}', '{"hooks": true}'),
    ("null", '{"hooks": null}', '{"hooks": true}'),
    ("numbers", '{"a": 1e2, "b": 1.10, "c": -0, "hooks": false}',
     '{"a": 1e2, "b": 1.10, "c": -0, "hooks": true}'),
    ("escape", '{"name": "caf\\u00e9", "hooks": false}', '{"name": "caf\\u00e9", "hooks": true}'),
    ("non-ascii", '{"name": "café", "hooks": false}', '{"name": "café", "hooks": true}'),
    ("typo", '{"hook": true}', '{"hook": true, "hooks": true}'),
    ("key-in-a-string", '{"note": "\\"hooks\\": false", "hooks": false}',
     '{"note": "\\"hooks\\": false", "hooks": true}'),
    ("first", '{"hooks": false, "version": 1}', '{"hooks": true, "version": 1}'),
]


@pytest.mark.parametrize("raw,out", [(r, o) for _, r, o in WITH_VALUE_ON],
                         ids=[c for c, *_ in WITH_VALUE_ON])
def test_with_value_changes_that_one_value_and_keeps_every_other_byte(raw, out):
    got = _settings.with_value(raw.encode(), "hooks", True)
    assert got == out.encode()
    data = json.loads(raw)
    assert json.loads(got) == {**data, "hooks": True}       # the meaning check
    assert type(json.loads(got)["hooks"]) is bool


@pytest.mark.parametrize("raw", ['{"hooks": true}', '{"version":1,"hooks":true}',
                                 '{"hooks": false, "hooks": true}'])
def test_with_value_already_so_is_none(raw):
    assert _settings.with_value(raw.encode(), "hooks", True) is None


@pytest.mark.parametrize("raw", ['{"hooks": 1}', '{"hooks": 0}'])
def test_with_value_is_strict_one_is_never_already_on_or_off(raw):
    """`1 == True` in Python: a strict check, so 1 and 0 are rewritten."""
    assert _settings.with_value(raw.encode(), "hooks", True) == b'{"hooks": true}'
    assert _settings.with_value(raw.encode(), "hooks", False) == b'{"hooks": false}'


@pytest.mark.parametrize("raw,names", [
    (b'{"version": 1, "hooks": false', "not JSON"),
    (b'[{"hooks": false}]', "list"),
    (b'\xef\xbb\xbf{"hooks": false}', "BOM"),
    (b'{"hooks": \xff}', "utf-8"),
    (b'"hooks"', "string"),
])
def test_with_value_refuses_what_is_not_a_json_object(raw, names):
    with pytest.raises(ValueError) as err:
        _settings.with_value(raw, "hooks", True)
    assert names.lower() in str(err.value).lower(), err.value


def test_with_value_refuses_a_duplicate_only_when_a_write_is_needed():
    raw = b'{"hooks": false, "hooks": true}'
    assert _settings.with_value(raw, "hooks", True) is None     # the last wins, as in the reader
    with pytest.raises(ValueError, match="is a member 2 times"):
        _settings.with_value(raw, "hooks", False)


def test_add_key_still_adds_through_the_shared_replace(tmp_path):
    """`add_key` moved onto the shared helper: same answer, mode kept, link kept."""
    real = tmp_path / "real.json"
    real.write_text('{"version": 1}')
    os.chmod(real, 0o640)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert _settings.add_key(link, "host", "alpha") is True
    assert link.is_symlink() and real.read_text() == '{"version": 1, "host": "alpha"}'
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o640
    assert _settings.add_key(link, "host", "beta") is False
    assert sorted(os.listdir(tmp_path)) == ["link.json", "real.json"]


# ── set_hooks: one per edge ──────────────────────────────────────────────

def test_set_hooks_with_no_file_makes_it_from_the_template_and_sets_it():
    r = _settings.set_hooks(True, env())
    assert (r.refused, r.made, r.wrote, r.state) == ("", True, True, _settings.ON)
    want = _settings.fresh_body().decode().replace('"hooks": false', '"hooks": true')
    assert mail_json().read_text() == want
    assert _settings.read(env()).state == _settings.ON


def test_off_with_no_file_makes_the_quiet_off():
    r = _settings.set_hooks(False, env())
    assert (r.refused, r.made, r.wrote, r.state) == ("", True, False, _settings.OFF)
    assert mail_json().read_bytes() == _settings.fresh_body()       # the template already says false
    assert _settings.read(env()).state == _settings.OFF


def test_set_hooks_flips_both_ways_and_the_read_back_says_so():
    write('{"version": 1, "hooks": false, "sendmail": "drop"}\n')
    r = _settings.set_hooks(True, env())
    assert (r.wrote, r.made, r.before, r.was) == (True, False, _settings.OFF, False)
    assert _settings.read(env()).hooks is True
    r = _settings.set_hooks(False, env())
    assert (r.wrote, r.before, r.was) == (True, _settings.ON, True)
    assert _settings.read(env()).hooks is False
    assert mail_json().read_text() == '{"version": 1, "hooks": false, "sendmail": "drop"}\n'


def test_a_second_run_touches_no_file():
    """Idempotent: the inode, the mtime, the bytes and the folder listing
    (no temp file, no schema refresh) are all the same."""
    _settings.set_hooks(True, env())
    d = mail_json().parent
    before = {n: snap(d / n) for n in listing(d)}
    r = _settings.set_hooks(True, env())
    assert (r.refused, r.wrote, r.made, r.already) == ("", False, False, True)
    assert {n: snap(d / n) for n in listing(d)} == before


def test_already_runs_no_ensure_even_with_a_stale_schema(monkeypatch):
    write('{"version": 1, "hooks": true}\n')
    schema = mail_json().parent / _settings.SCHEMA
    schema.write_text("{}")                                    # stale: ensure would refresh it
    called = []
    monkeypatch.setattr(_settings, "ensure", lambda *a, **k: called.append(1))
    r = _settings.set_hooks(True, env())
    assert r.already and not r.wrote and called == []
    assert schema.read_text() == "{}"
    assert listing(mail_json().parent) == ["mail.json", "mail.schema.json"]


def test_other_keys_are_byte_preserved():
    raw = '{\n  "version": 1,\n  "sendmail": "/usr/sbin/sendmail",\n  "a": 1e2,\n  "hooks": false,\n  "z": "café"\n}\n'
    write(raw)
    _settings.set_hooks(True, env())
    assert mail_json().read_text() == raw.replace('"hooks": false', '"hooks": true')


def test_ensure_loses_the_race_and_the_verb_still_sets_it(monkeypatch):
    """The file appears (another session's hook) between the judging and
    `ensure`'s publish: the verb edits the file that is there."""
    real_publish = _settings._publish

    def racer(target, body):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"version": 1, "hooks": false, "host": "other"}\n')
        return real_publish(target, body)

    monkeypatch.setattr(_settings, "_publish", racer)
    r = _settings.set_hooks(True, env())
    assert r.refused == "" and r.wrote
    assert mail_json().read_text() == '{"version": 1, "hooks": true, "host": "other"}\n'


def test_a_symlinked_mail_json_survives_and_its_target_changes(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text('{"version": 1, "hooks": false}')
    mail_json().parent.mkdir(parents=True)
    mail_json().symlink_to(target)
    r = _settings.set_hooks(True, env())
    assert r.wrote and mail_json().is_symlink()
    assert target.read_text() == '{"version": 1, "hooks": true}'


def test_a_0600_file_stays_0600():
    p = write('{"hooks": false}')
    os.chmod(p, 0o600)
    _settings.set_hooks(True, env())
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


@pytest.mark.skipif(AS_ROOT, reason="root reads a mode-000 file")
def test_an_unreadable_file_is_refused_bytes_unchanged():
    p = write('{"hooks": false}')
    os.chmod(p, 0)
    try:
        r = _settings.set_hooks(True, env())
        assert r.refused == _settings.REFUSED_FAULT and "Permission denied" in r.why
    finally:
        os.chmod(p, 0o600)
    assert p.read_text() == '{"hooks": false}'
    assert listing(p.parent) == ["mail.json"]


def test_a_directory_at_the_path_is_refused():
    mail_json().mkdir(parents=True)
    r = _settings.set_hooks(True, env())
    assert r.refused == _settings.REFUSED_FAULT
    assert mail_json().is_dir() and listing(mail_json()) == []
    assert listing(mail_json().parent) == ["mail.json"]


def test_a_dangling_symlink_is_refused_and_left(tmp_path):
    mail_json().parent.mkdir(parents=True)
    mail_json().symlink_to(tmp_path / "nothing.json")
    r = _settings.set_hooks(True, env())
    assert r.refused == _settings.REFUSED_FAULT and "symlink to nothing" in r.why
    assert mail_json().is_symlink() and not (tmp_path / "nothing.json").exists()


@pytest.mark.skipif(AS_ROOT, reason="root writes a mode-500 folder")
def test_an_unwritable_folder_is_refused_and_leaves_no_temp_file():
    p = write('{"hooks": false}')
    os.chmod(p.parent, 0o500)
    try:
        r = _settings.set_hooks(True, env())
        assert r.refused == _settings.REFUSED_FAULT and "could not be written" in r.why
        assert not r.wrote
    finally:
        os.chmod(p.parent, 0o700)
    assert p.read_text() == '{"hooks": false}'
    assert not [n for n in listing(p.parent) if n.startswith(".mail.json.")]


def test_not_json_is_refused_and_nothing_is_installed_beside_it():
    """[review]: judged before `ensure`, so no schema or template appears."""
    p = write('{"version": 1, "hooks": false')
    r = _settings.set_hooks(True, env())
    assert r.refused == _settings.REFUSED_FAULT and r.why.startswith("not JSON")
    assert listing(p.parent) == ["mail.json"]
    assert p.read_text() == '{"version": 1, "hooks": false'


def test_a_duplicate_member_is_refused_when_a_write_is_needed():
    p = write('{"hooks": false, "hooks": true}')
    assert _settings.set_hooks(True, env()).already
    r = _settings.set_hooks(False, env())
    assert r.refused == _settings.REFUSED_FAULT and "is a member 2 times" in r.why
    assert p.read_text() == '{"hooks": false, "hooks": true}'


def test_an_old_file_that_is_not_json_is_refused_and_nothing_is_made():
    old = write_old('{"hooks": false')
    r = _settings.set_hooks(True, env())
    assert r.refused == _settings.REFUSED_FAULT and r.path == old
    assert not (home() / _settings.ROOT).exists()             # no settings folder at all
    assert old.is_file() and not old.is_symlink()


def test_the_old_file_is_converted_then_set_and_linked():
    old = write_old('{"hooks": false, "sendmail": "/usr/sbin/sendmail"}')
    r = _settings.set_hooks(True, env())
    assert (r.refused, r.made, r.converted, r.wrote) == ("", True, True, True)
    assert mail_json().read_text() == '{"hooks": true, "sendmail": "/usr/sbin/sendmail"}'
    assert old.is_symlink() and old.resolve() == mail_json().resolve()
    assert _settings.drift(env()) is False


def test_the_drift_order_ensure_links_before_the_write():
    """[review] (b) step 3: an old real file equal to mail.json, saying false."""
    body = '{"version": 1, "hooks": false}\n'
    write(body)
    old = write_old(body)
    r = _settings.set_hooks(True, env())
    assert r.wrote and old.is_symlink()
    assert _settings.drift(env()) is False


@pytest.mark.parametrize("host", ["stranded", "copied"])
def test_a_stranded_or_copied_host_is_refused_and_nothing_is_made(host):
    (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    if host == "copied":
        write('{"hooks": false}')
    before = tree(home())
    r = _settings.set_hooks(True, env())
    assert r.refused == (_settings.REFUSED_COPIED if host == "copied" else _settings.REFUSED_STRANDED)
    assert tree(home()) == before


@pytest.mark.skipif(AS_ROOT, reason="root searches a mode-000 folder")
def test_an_unchecked_host_is_refused():
    d = home() / ".settings"
    (d / "mail").mkdir(parents=True)
    os.chmod(d, 0)
    try:
        r = _settings.set_hooks(True, env())
    finally:
        os.chmod(d, 0o700)
    assert r.refused == _settings.REFUSED_UNCHECKED and "could not be checked" in r.why
    assert not mail_json().exists()


@pytest.mark.parametrize("how", ["euid", "sudo"])
def test_root_or_sudo_is_refused_and_nothing_is_written(how, monkeypatch):
    """Q8 (ruled): refuse at euid 0, or with SUDO_USER set; write nothing."""
    if how == "euid":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    else:
        monkeypatch.setenv("SUDO_USER", "someone")
    r = _settings.set_hooks(True, {**env(), **({"SUDO_USER": "someone"} if how == "sudo" else {})})
    assert r.refused == _settings.REFUSED_ROOT
    assert tree(home()) == []


def test_set_hooks_never_reads_SESSIONMAIL_PAGER(monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    assert _settings.set_hooks(True, env()).state == _settings.ON


def test_the_output_validates_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(json.loads(_settings.schema()))
    for on in (True, False, True):
        _settings.set_hooks(on, env())
        validator.validate(json.loads(mail_json().read_text()))


def test_setup_never_calls_set_hooks():
    """Setup's stance (design (d)): `message setup` never writes the key."""
    src = (Path(__file__).resolve().parents[1] / "src/sessionmail/_setup.py").read_text()
    assert "set_hooks" not in src and "with_value" not in src


# ── the verb: every (c) line and exit code ───────────────────────────────

def P() -> str:
    return str(mail_json())


def test_on_with_no_file_says_made_exit_0():
    r = hooks("on")
    assert r.exit_code == 0, r.output
    assert r.stdout == (f'pager settings hooks: on - made {P()} from the template, with "hooks": true; '
                        "each session's next hook reads it\n")
    assert r.stderr == ""


def test_on_over_false_says_wrote_and_what_it_said():
    write('{"hooks": false}')
    r = hooks("on")
    assert r.exit_code == 0
    assert r.stdout == (f'pager settings hooks: on - wrote "hooks": true in {P()} (it said false); '
                        "each session's next hook reads it\n")


@pytest.mark.parametrize("raw,said", [
    ('{"version": 1}', '(it had no "hooks")'),
    ('{"hooks": "true"}', '(it said "true", which the hooks read as off)'),
    ('{"hooks": 1}', "(it said 1, which the hooks read as off)"),
])
def test_on_over_a_fault_it_can_fix_names_what_was_there(raw, said):
    write(raw)
    r = hooks("on")
    assert r.exit_code == 0
    assert said in r.stdout and r.stdout.startswith('pager settings hooks: on - wrote "hooks": true in ')


def test_already_on_says_nothing_written():
    write('{"hooks": true}')
    r = hooks("on")
    assert r.exit_code == 0
    assert r.stdout == f'pager settings hooks: on - {P()} already says "hooks": true; nothing written\n'


def test_off_says_the_quiet_off():
    write('{"hooks": true}')
    r = hooks("off")
    assert r.exit_code == 0
    assert r.stdout == (f'pager settings hooks: off - wrote "hooks": false in {P()} (it said true): '
                        "the quiet off\n")


def test_off_with_no_file_says_made():
    r = hooks("off")
    assert r.exit_code == 0
    assert r.stdout == (f'pager settings hooks: off - made {P()} from the template, with "hooks": false: '
                        "the quiet off\n")


def test_already_off():
    write('{"hooks": false}')
    r = hooks("off")
    assert r.exit_code == 0
    assert r.stdout == f'pager settings hooks: off - {P()} already says "hooks": false; nothing written\n'


def test_on_converting_the_old_file_names_it():
    write_old('{"hooks": false}')
    r = hooks("on")
    assert r.exit_code == 0
    assert r.stdout == (f'pager settings hooks: on - made {P()} from {_settings.SHOWN_OLD}, with '
                        '"hooks": true; each session\'s next hook reads it\n')


@pytest.mark.parametrize("word", ["yes", "true", "enable", "ON"])
def test_any_other_word_is_a_usage_error_naming_the_two(word):
    r = hooks(word)
    assert r.exit_code == 2
    assert "'on'" in r.output and "'off'" in r.output
    assert not mail_json().exists()


def test_a_second_word_is_a_usage_error():
    r = hooks("on", "off")
    assert r.exit_code == 2 and not mail_json().exists()


def test_not_json_is_refused_on_stderr_exit_2_never_written():
    p = write('{"hooks": false')
    r = hooks("on")
    assert r.exit_code == 2 and r.stdout == ""
    assert r.stderr.startswith(f"pager settings hooks: refused: {P()}: not JSON: ")
    assert r.stderr.endswith("; nothing written: fix it by hand\n")
    assert p.read_text() == '{"hooks": false'


def test_a_duplicate_is_refused_naming_it():
    write('{"hooks": false, "hooks": true}')
    r = hooks("off")
    assert r.exit_code == 2
    assert r.stderr == (f"pager settings hooks: refused: {P()}: `hooks` is a member 2 times; "
                        "nothing written: fix it by hand\n")


def test_stranded_is_refused_with_installs_line():
    (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    r = hooks("on")
    line = pager.SETTINGS_STRANDED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
    assert r.exit_code == 2
    assert r.stderr == f"pager settings hooks: refused: {line.removeprefix('sessionmail: ')}\n"


def test_copied_is_refused_with_installs_line():
    (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    write('{"hooks": false}')
    r = hooks("on")
    line = pager.SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
    assert r.exit_code == 2
    assert r.stderr == f"pager settings hooks: refused: {line.removeprefix('sessionmail: ')}\n"


@pytest.mark.parametrize("how", ["euid", "sudo"])
def test_root_and_sudo_are_refused_exit_2(how, monkeypatch):
    if how == "euid":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    else:
        monkeypatch.setenv("SUDO_USER", "someone")
    r = hooks("on")
    assert r.exit_code == 2 and r.stdout == ""
    assert r.stderr.startswith("pager settings hooks: refused: ")
    assert ("root" if how == "euid" else "SUDO_USER=someone") in r.stderr
    assert "run it as yourself" in r.stderr
    assert tree(home()) == []


@pytest.mark.parametrize("how", ["euid", "sudo"])
def test_the_bare_read_stays_open_to_root_and_sudo(how, monkeypatch):
    write('{"hooks": true}')
    if how == "euid":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    else:
        monkeypatch.setenv("SUDO_USER", "someone")
    r = hooks()
    assert r.exit_code == 0 and r.stdout.startswith("pager settings hooks: on - ")


def test_a_failed_read_back_says_it_wrote(monkeypatch):
    """(b) step 7: the one refusal after the write. Only a race or a bug
    reaches it, so the bug is simulated: the reader disagrees."""
    write('{"hooks": false}')
    monkeypatch.setattr(_settings, "read", lambda env=None: _settings.Settings(False, _settings.OFF))
    r = hooks("on")
    assert r.exit_code == 2
    assert r.stderr == (f'pager settings hooks: refused: wrote "hooks": true in {P()}, but it reads as off; '
                        "a race or a bug: look at the file\n")


# the bare read

def test_bare_on_exit_0():
    write('{"hooks": true}')
    r = hooks()
    assert r.exit_code == 0
    assert r.stdout == f'pager settings hooks: on - {P()} says "hooks": true\n'


def test_bare_off_exit_3():
    write('{"hooks": false}')
    r = hooks()
    assert r.exit_code == 3
    assert r.stdout == f'pager settings hooks: off - {P()} says "hooks": false\n'


def test_bare_no_file_exit_3_and_makes_nothing():
    r = hooks()
    assert r.exit_code == 3
    assert r.stdout == f"pager settings hooks: off - no {P()} yet; the first PostToolBatch makes it, off\n"
    assert tree(home()) == []


def test_bare_no_file_with_an_old_file_names_the_conversion():
    write_old('{"hooks": true}')
    r = hooks()
    assert r.exit_code == 3
    assert r.stdout == (f"pager settings hooks: off - no {P()} yet; the first PostToolBatch makes it "
                        f"from {_settings.SHOWN_OLD}\n")


def test_bare_no_key_is_off_by_mistake_exit_2():
    write('{"version": 1}')
    r = hooks()
    assert r.exit_code == 2
    assert r.stdout == (f"pager settings hooks: off by mistake - {P()}: no `hooks` key; "
                        "`pager settings hooks on` or `off` sets it\n")


def test_bare_not_json_is_off_by_mistake_fix_it_by_hand():
    write('{"hooks": ')
    r = hooks()
    assert r.exit_code == 2
    assert r.stdout.startswith(f"pager settings hooks: off by mistake - {P()}: not JSON: ")
    assert r.stdout.endswith("; fix it by hand\n")


def test_bare_stranded_with_no_file_is_off_by_mistake_and_makes_nothing():
    (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    before = tree(home())
    r = hooks()
    assert r.exit_code == 2
    assert r.stdout == (f"pager settings hooks: off by mistake - no {P()} and the settings in "
                        f"{_settings.SHOWN_LEGACY_DIR} have not been moved, so no hook makes it\n")
    assert tree(home()) == before


def _stranded_not_copied(tmp_path: Path) -> None:
    """A stranded host that is not copied, WITH a file: ~/.sessionkit/mail/settings/
    is a symlink."""
    (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    real = tmp_path / "real-settings"
    real.mkdir()
    (real / "mail.json").write_text('{"hooks": true}')
    (home() / _settings.DIR).parent.mkdir(parents=True)
    (home() / _settings.DIR).symlink_to(real)


@pytest.mark.parametrize("host", ["stranded", "copied", "unchecked"])
def test_bare_with_a_file_reads_it_as_the_hooks_do_whatever_the_old_folder(host, tmp_path):
    """[r2]: a file that is there is read, never `stranded` first."""
    if host == "stranded":
        _stranded_not_copied(tmp_path)
        assert _settings.stranded(env()) and not _settings.copied(env())
    else:
        write('{"hooks": true}')
        (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    d = home() / ".settings"
    if host == "unchecked":
        if AS_ROOT:
            pytest.skip("root searches a mode-000 folder")
        os.chmod(d, 0)
    try:
        r = hooks()
    finally:
        os.chmod(d, 0o700)
    assert r.exit_code == 0, r.output
    assert r.stdout.startswith("pager settings hooks: on - ")


@pytest.mark.skipif(AS_ROOT, reason="root searches a mode-000 folder")
def test_bare_unchecked_with_no_file_is_could_not_look():
    """[r3]: the unchecked host is ~/.settings/ at mode 000."""
    d = home() / ".settings"
    (d / "mail").mkdir(parents=True)
    os.chmod(d, 0)
    try:
        r = hooks()
    finally:
        os.chmod(d, 0o700)
    assert r.exit_code == 2
    assert r.stdout.startswith(f"pager settings hooks: off by mistake - no {P()} and ")
    assert "could not be checked" in r.stdout


# SESSIONMAIL_PAGER

@pytest.mark.parametrize("words,code", [(("on",), 0), ((), 0), (("off",), 0)])
def test_SESSIONMAIL_PAGER_off_adds_a_note_and_changes_no_exit_code(words, code, monkeypatch):
    write('{"hooks": true}')
    monkeypatch.setenv(_seat.PAGER_VAR, "0")
    r = hooks(*words)
    assert r.exit_code == code
    lines = r.stdout.splitlines()
    assert len(lines) == 2
    assert lines[1] == ("pager settings hooks: note - SESSIONMAIL_PAGER=0 is set here: a session "
                        "started with it stays silent, whatever the file says")


def test_no_note_when_SESSIONMAIL_PAGER_is_on(monkeypatch):
    monkeypatch.setenv(_seat.PAGER_VAR, "on")
    r = hooks("on")
    assert len(r.stdout.splitlines()) == 1


# the page

def test_the_page_pins_what_a_reader_needs():
    r = hooks("--help")
    assert r.exit_code == 0
    out = r.output
    assert out.startswith("Usage: pager settings hooks [on|off]")
    for pin in (_settings.SHOWN, "SESSIONMAIL_PAGER=off", '"monitor"', "Exit:", "never",
                "sudo", "message setup"):
        assert pin in out, pin


def test_the_verb_is_on_the_hidden_group_and_no_mcp_tool():
    assert "hooks" in pager.settings.commands
    assert pager.cli.commands["settings"].hidden
    names = [n for n, _fn, _d in _mcp.tools(pager.cli, "pager")]
    assert not [n for n in names if n.startswith("pager_settings")], names


# ── Q1: one audit row, on a flip only, in the host's mail log ─────────────

@pytest.fixture
def seated(monkeypatch):
    monkeypatch.setenv("TM_AGENT_ADDRESS", "sessionmail+sm-983.worker")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


def audit_log() -> Path:
    return home() / _settings.LOG_DIR / _settings.AUDIT_LOG


def audit_rows() -> list[dict]:
    p = audit_log()
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_the_audit_log_is_under_the_host_log_dir():
    assert _settings.audit_path(env()) == _settings.log_dir(env()) / "settings.jsonl"
    assert _settings.audit_path(env()) == audit_log()


def test_a_flip_writes_one_audit_row_naming_who_when_what_and_where(seated):
    write('{"hooks": false}')
    r = hooks("on")
    assert r.exit_code == 0
    rows = audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "settings"
    assert row["sid"] == seated and row["seat"] == "sessionmail+sm-983.worker"
    assert row["who"] == "sessionmail+sm-983.worker"
    assert (row["key"], row["from"], row["to"]) == ("hooks", "off", "on")
    assert row["path"] == P()
    assert row["ts"][:4].isdigit() and "T" in row["ts"]
    assert list(row)[0] == "ts"


def test_the_row_is_one_line_and_the_session_log_gains_nothing(seated):
    hooks("on")
    assert audit_log().read_text().count("\n") == 1
    sessions = home() / ".session"
    assert not sessions.exists() or not list(sessions.rglob("pager.jsonl"))


def test_the_log_dir_is_made_0700_and_the_file_0600(seated):
    assert not audit_log().parent.exists()
    hooks("on")
    assert stat.S_IMODE(os.stat(audit_log().parent).st_mode) == _settings.LOG_MODE
    assert stat.S_IMODE(os.stat(audit_log()).st_mode) == _settings.AUDIT_MODE


def test_an_existing_log_dir_keeps_its_mode_and_rows_append(seated):
    d = audit_log().parent
    d.mkdir(parents=True)
    os.chmod(d, 0o755)
    audit_log().write_text('{"ts": "earlier"}\n')
    hooks("on")
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o755
    assert [r.get("to") for r in audit_rows()] == [None, "on"]


def test_making_the_file_is_a_flip_from_missing(seated):
    hooks("off")
    assert [(r["from"], r["to"]) for r in audit_rows()] == [("missing", "off")]


def test_already_so_logs_nothing(seated):
    write('{"hooks": true}')
    hooks("on")
    hooks()
    assert audit_rows() == []
    assert not audit_log().exists()


def test_a_refusal_logs_nothing(seated):
    write('{"hooks": ')
    assert hooks("on").exit_code == 2
    assert audit_rows() == []


def test_a_fault_fixed_is_logged_from_malformed(seated):
    write('{"hooks": "true"}')
    hooks("on")
    assert [(r["from"], r["to"]) for r in audit_rows()] == [("malformed", "on")]


def test_two_flips_two_rows(seated):
    hooks("on")
    hooks("off")
    hooks("off")
    assert [(r["from"], r["to"]) for r in audit_rows()] == [("missing", "on"), ("on", "off")]


def test_a_refusal_after_the_file_was_made_logs_what_it_says_not_what_was_asked(seated, monkeypatch):
    """`ensure` made the file (a flip from missing), then the verb's own
    write failed: the file says off, so the row's `to` is off, never on."""
    def no_space(real, body):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_settings, "_replace", no_space)
    r = hooks("on")
    assert r.exit_code == 2
    assert _settings.read(env()).state == _settings.OFF
    assert [(r["from"], r["to"]) for r in audit_rows()] == [("missing", "off")]


def test_a_failed_read_back_logs_what_the_file_reads_as(seated, monkeypatch):
    write('{"hooks": false}')
    monkeypatch.setattr(_settings, "read", lambda env=None: _settings.Settings(False, _settings.MALFORMED, "x"))
    assert hooks("on").exit_code == 2
    assert [(r["from"], r["to"]) for r in audit_rows()] == [("off", "malformed")]


def test_a_session_with_no_seat_names_the_os_user(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    def no_seat_here(env=None):
        raise _seat.NoIdentity()

    monkeypatch.setattr(_seat, "resolve", no_seat_here)
    hooks("on")
    rows = audit_rows()
    assert len(rows) == 1 and rows[0]["seat"] is None and rows[0]["sid"] == SESSION
    assert rows[0]["who"] == pager._os_user()


def test_a_seatless_run_is_logged_too():
    """A person's shell: no seat, no session. The file is host-wide, so the
    flip is logged all the same, naming the OS user."""
    r = hooks("on")
    assert r.exit_code == 0
    rows = audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert (row["from"], row["to"], row["path"]) == ("missing", "on", P())
    assert row["who"] == pager._os_user()
    assert row["seat"] is None and row["sid"] is None


def test_a_caller_that_cannot_be_read_still_logs_the_os_user(monkeypatch):
    from sessionmail import _subscribe

    def broken(env=None):
        raise RuntimeError("no caller")

    monkeypatch.setattr(_subscribe, "caller", broken)
    assert hooks("on").exit_code == 0
    assert [(r["who"], r["seat"], r["sid"]) for r in audit_rows()] == [(pager._os_user(), None, None)]


def test_an_audit_that_cannot_be_written_never_fails_the_verb(seated):
    d = audit_log().parent
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("not a folder")
    r = hooks("on")
    assert r.exit_code == 0
    assert _settings.read(env()).state == _settings.ON
    assert _settings.audit({"x": 1}, env()) is False


# ── the hooks read the verb's file (criterion 1 as a regression) ─────────

@pytest.fixture
def hook_seat(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    (tmp_path / "sessionmail" / "seats" / "alpha").mkdir(parents=True)
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))


def batch_context(r) -> list[str]:
    if not r.stdout.strip():
        return []
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].splitlines()


def test_after_on_the_hooks_speak_and_no_creation_note(hook_seat):
    assert hooks("on").exit_code == 0
    b = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert batch_context(b) == [pager.LINE_NOT_LISTENING]
    s = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert s.exit_code == 2 and s.stderr.strip() == pager.LINE_NOT_LISTENING


def test_after_off_the_hooks_are_silent(hook_seat):
    hooks("on")
    hooks("off")
    b = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert b.exit_code == 0 and batch_context(b) == []
    s = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert s.exit_code == 0 and s.stderr == ""


def test_SESSIONMAIL_PAGER_off_still_silences_after_on(hook_seat, monkeypatch):
    hooks("on")
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    b = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert batch_context(b) == []
    s = CliRunner().invoke(cli, ["hook", "stop"], input="{}")
    assert s.exit_code == 0 and s.stderr == ""
