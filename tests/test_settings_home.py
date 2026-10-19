"""SM-873 repro-001: the mail tool's settings live under `~/.sessionkit/`.

The ruled path is PACKAGE FIRST: `~/.sessionkit/mail/settings/mail.json`,
with `mail.schema.json` and `mail.template.json` beside it. There is no
override variable and no read-old fallback, so no code path may read,
create or write `~/.settings`.

This is the GUARD. Each test runs under a scratch HOME where `~/.settings`
is absent, and it must still be absent afterwards. The settings home that
`_operator` reads (pwd's home, `_operator.settings_home`) is pointed at the
same scratch HOME, so the operator reads are held to the same rule:

- `_settings.path()`: where the file is
- `_settings.read()`, `ensure()`, `install()`: read, create, install
- `pager settings install` and the SessionStart birth
  (`_birth.ensure_session_box`), which install the schema and template
- `rules.alert_runner()` and `_mhs.configured_registry()`, which read the
  mail tool's layers through `_operator.host_values`

Red when it was written (sessionmail df30778): everything above read or
wrote `~/.settings/mail/`. Ten of these tests were red there; one (an
`install` without `make`) held before the fix and holds after it.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from sessionmail import _birth, _mhs, _operator, _seat, _settings, rules
from sessionmail.pager import cli

#: The home of the mail tool's settings (package first), relative to
#: HOME. Spelled out here, not taken from `_settings`, so the guard does
#: not follow the code it guards.
NEW_DIR = Path(".sessionkit") / "mail" / "settings"
OLD_ROOT = ".settings"

SID = "sess-873"
PLUS = "sessionmail+sm-873.worker"
LIVE = (True, f"{PLUS}@hq -> this box")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A scratch HOME with no `~/.settings`, used for `$HOME` and for the
    settings home `_operator` reads."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(_operator, "settings_home", lambda: h)
    assert not (h / OLD_ROOT).exists()
    return h


def absent(h: Path) -> None:
    """The guard: nothing made `~/.settings`, not even an empty directory."""
    assert not os.path.lexists(h / OLD_ROOT), sorted(
        str(p.relative_to(h)) for p in (h / OLD_ROOT).rglob("*"))


def write_new(h: Path, data: dict) -> Path:
    p = h / NEW_DIR / "mail.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) + "\n")
    return p


# ── where the file is ────────────────────────────────────────────────────────

def test_the_settings_file_is_under_sessionkit_mail_settings(home):
    assert _settings.path() == home / NEW_DIR / "mail.json"
    assert _settings.path({"HOME": str(home)}) == home / NEW_DIR / "mail.json"
    absent(home)


# ── read, ensure, install ────────────────────────────────────────────────────

def test_read_reads_the_new_path(home):
    write_new(home, {"hooks": True})
    assert _settings.read().state == _settings.ON
    assert _settings.host_value("hooks") == (True, True, "")
    absent(home)


def test_read_never_reads_the_old_path(home):
    """No fallback: a file at the old path decides nothing."""
    old = home / OLD_ROOT / "mail" / "mail.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"hooks": true, "monitor": false}\n')
    assert _settings.read().state == _settings.MISSING
    assert _settings.monitor_host().off is False


def test_ensure_creates_the_file_at_the_new_path_only(home):
    settings, created = _settings.ensure()
    assert created is True and settings.state == _settings.OFF
    d = home / NEW_DIR
    assert sorted(os.listdir(d)) == ["mail.json", "mail.schema.json", "mail.template.json"]
    absent(home)


def test_install_makes_the_new_folder_only(home):
    wrote = _settings.install(make=True)
    d = home / NEW_DIR
    assert sorted(wrote) == [d / "mail.schema.json", d / "mail.template.json"]
    assert (d / "mail.schema.json").read_text() == _settings.schema()
    absent(home)


def test_install_without_make_makes_no_folder_anywhere(home):
    assert _settings.install() == []
    assert not (home / ".sessionkit").exists()
    absent(home)


# ── the verbs that install before a launch ───────────────────────────────────

def test_pager_settings_install_installs_under_sessionkit(home):
    result = CliRunner().invoke(cli, ["settings", "install"], prog_name="pager")
    assert result.exit_code == 0, result.output
    d = home / NEW_DIR
    assert (d / "mail.schema.json").read_text() == _settings.schema()
    assert (d / "mail.template.json").read_text() == _settings.template()
    assert str(d / "mail.schema.json") in result.output
    absent(home)


def test_the_sessionstart_birth_installs_under_sessionkit(home, tmp_path, monkeypatch):
    """`_birth.ensure_session_box` is the ONE birth path the SessionStart hook
    calls; it installs the schema and template before anything reads them."""
    monkeypatch.setattr(_seat, "doorbell", lambda address, session, env=None: LIVE)
    monkeypatch.setattr(_seat, "regen_maps", lambda root, env=None: "generator ran")
    env = {"HOME": str(home),
           "SESSION_MAIL_ROOT": str(tmp_path / "mail" / "agents"),
           "SESSIONMAIL_HOME": str(tmp_path / "state"),
           "MAGICDNS_SUFFIX": os.environ["MAGICDNS_SUFFIX"]}
    birth = _birth.ensure_session_box(SID, SimpleNamespace(address=PLUS), env=env)
    assert birth.error is None and birth.born is True
    assert (home / NEW_DIR / "mail.schema.json").read_text() == _settings.schema()
    absent(home)


# ── the operator's reads of the mail tool's layers ───────────────────────────

def test_the_alert_runner_is_read_from_the_new_path(home):
    write_new(home, {rules.ALERT_RUNNER_KEY: "alerts"})
    assert rules.alert_runner() == "alerts"
    absent(home)


def test_the_registry_is_read_from_the_new_path(home):
    write_new(home, {_mhs.REGISTRY_KEY: "~/registry.json"})
    assert _mhs.configured_registry() == str(home / "registry.json")
    absent(home)


def test_the_watcher_watches_the_new_folder(home):
    """`_operator.files`, what `rules.LiveLayers` watches for the alert runner."""
    write_new(home, {})
    watched = _operator.files(_settings.TOOL)
    assert watched and all(str(p).startswith(str(home / NEW_DIR)) for p in watched), watched
    absent(home)


def test_the_session_tool_has_its_own_kit_folder(home):
    """SM-873 moved the MAIL tool; SM-875 moves the SESSION tool's read to
    its own package folder, `~/.sessionkit/tools/settings/`, never the mail
    folder and never `~/.settings/session/`."""
    watched = _operator.files("session")
    assert all(not str(p).startswith(str(home / NEW_DIR)) for p in watched), watched
    assert _operator._tool_dir("session") == home / ".sessionkit" / "tools" / "settings"
    absent(home)


# ── the one definition, and the text that shows it ──────────────────────────

ROOT = Path(__file__).resolve().parents[1]
SHOWN = "~/" + NEW_DIR.as_posix() + "/mail.json"


def test_the_one_definition_is_package_first():
    """ONE Python definition: the kit root, the package folder, then
    `settings` (sessiontools' map, `host_dir(tool) = ROOT / PACKAGES[tool] /
    "settings"`, mail -> mail). No "session" prefix on the package."""
    assert _settings.ROOT == Path(".sessionkit")
    assert _settings.PACKAGE == "mail"
    assert _settings.DIR == _settings.ROOT / _settings.PACKAGE / "settings" == NEW_DIR
    assert _settings.RELATIVE == _settings.DIR / "mail.json"
    assert _settings.SHOWN == SHOWN
    assert _settings.SHOWN_DIR == "~/" + NEW_DIR.as_posix() + "/"


def test_no_override_variable_moves_it(tmp_path):
    """No variable but HOME moves the file: not SESSIONMAIL_HOME, not any
    SESSIONKIT_* or *SETTINGS* name a reader might reach for."""
    env = {"HOME": str(tmp_path), "SESSIONMAIL_HOME": str(tmp_path / "x"),
           "SESSIONKIT_HOME": str(tmp_path / "y"), "SESSIONKIT_SETTINGS": str(tmp_path / "z"),
           "SETTINGS_HOME": str(tmp_path / "w"), "XDG_CONFIG_HOME": str(tmp_path / "v")}
    assert _settings.path(env) == tmp_path / NEW_DIR / "mail.json"


def _help(main, *args: str) -> str:
    r = CliRunner().invoke(main, [*args, "--help"])
    assert r.exit_code == 0, r.output
    return r.output


@pytest.mark.parametrize("verb", ["message verify", "memory run", "pager hook",
                                  "pager settings install"])
def test_the_help_names_the_new_home(verb):
    from sessionmail import memory, message, pager
    main = {"message": message.cli, "memory": memory.cli, "pager": pager.cli}[verb.split()[0]]
    text = _help(main, *verb.split()[1:])
    if verb == "pager settings install":      # the one page that tells a 0.1.0 host what to move
        assert text.count("~/.settings/mail/") == 1, text
        text = text.replace("~/.settings/mail/", "")
    assert ".settings/" not in text.replace(".sessionkit/mail/settings/", ""), text
    want = _settings.SHOWN_DIR if verb == "pager settings install" else _settings.SHOWN
    assert want in text, text


#: Every file that names the mail settings file in text it ships, with the
#: path it must name. The mhs scripts stand alone (they import no package),
#: so they spell the path out, and this test holds them to `_settings`.
NAMES_IT = ("src/sessionmail/mhs/session-mail-run", "src/sessionmail/mhs/session-mail-verify",
            "src/sessionmail/mail.schema.json", "hooks/hooks.json", "pyproject.toml")


@pytest.mark.parametrize("rel", NAMES_IT)
def test_the_shipped_text_names_the_new_home(rel):
    text = (ROOT / rel).read_text()
    assert _settings.SHOWN in text, rel


def test_no_source_names_the_old_mail_home():
    """No shipped file builds or names the mail tool's old folder, but the
    ONE definition that checks a stranded host (`_settings.LEGACY_DIR`,
    whose kind alone is checked). (The SESSION tool's old folder is
    held by tests/test_operator_home.py.)"""
    import re
    old = re.compile(r"""(~|HOME|/)\.settings/mail\b|["']\.settings["']\s*\)?\s*/\s*["']mail"""
                     r"""|Path\(\s*["']\.settings["']""")
    seen, bad = set(), []
    for top in ("src/sessionmail", "hooks"):
        for p in sorted((ROOT / top).rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            try:
                text = p.read_text()
            except UnicodeDecodeError:
                continue
            rel = p.relative_to(ROOT).as_posix()
            seen.add(rel)
            bad += [f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1)
                    if old.search(line) and not line.startswith("LEGACY_DIR = ")]
    bad += [f"pyproject.toml:{n}" for n, line in
            enumerate((ROOT / "pyproject.toml").read_text().splitlines(), 1) if old.search(line)]
    assert {"src/sessionmail/_settings.py", "src/sessionmail/mhs/session-mail-verify",
            "hooks/hooks.json"} <= seen, sorted(seen)
    assert bad == [], bad


# ── the stranded host: three branches (ensure, install, the verb, the birth) ──
#
# STRANDED: `~/.settings/mail/` is still a real folder, because a host
# upgraded from 0.1.0 has not moved its files to `~/.sessionkit/mail/settings/`.
# The writers write NOTHING, under either tree, and only the old folder's
# KIND is checked. Its files are never read: each test makes the old
# `mail.json` unreadable, so a read would show as a fault.

OLD_DIR = Path(OLD_ROOT) / "mail"
OLD_DATA = {"version": 1, "hooks": True, rules.ALERT_RUNNER_KEY: "alerts",
            _mhs.REGISTRY_KEY: "~/registry"}


def tree(h: Path) -> dict[str, tuple]:
    """Every path under HOME: its kind, bytes and mtime. Equal before and
    after means nothing was written, made, replaced or touched."""
    out = {}
    for p in sorted(h.rglob("*")):
        st = p.lstat()
        body = p.read_bytes() if p.is_file() and not p.is_symlink() and os.access(p, os.R_OK) else None
        out[str(p.relative_to(h))] = (st.st_mode, st.st_mtime_ns, body)
    return out


@pytest.fixture
def stranded(home):
    """HOME as a host the move has not reached: `~/.settings/mail/` holds the
    file, the schema and the template; `~/.sessionkit` is absent. The old
    file is mode 000 for the test."""
    d = home / OLD_DIR
    d.mkdir(parents=True)
    (d / "mail.schema.json").write_text(_settings.schema())
    (d / "mail.template.json").write_text(_settings.template())
    old = d / "mail.json"
    old.write_text(json.dumps(OLD_DATA) + "\n")
    old.chmod(0o000)
    yield home
    old.chmod(0o600)


def nothing_written(h: Path, before: dict) -> None:
    assert not os.path.lexists(h / ".sessionkit"), sorted(
        str(p.relative_to(h)) for p in (h / ".sessionkit").rglob("*"))
    assert tree(h) == before


def test_branch_1_ensure_writes_nothing_and_says_why(stranded):
    before = tree(stranded)
    assert _settings.stranded() is True
    settings, created = _settings.ensure()
    assert (settings.state, settings.hooks, settings.fault, created) == (_settings.STRANDED, False, True, False)
    assert settings.why == ("the settings in ~/.settings/mail/ have not been moved to "
                            "~/.sessionkit/mail/settings/")      # a fault always has a why
    nothing_written(stranded, before)


def test_branch_1_install_writes_nothing_make_or_not(stranded):
    before = tree(stranded)
    assert _settings.stranded() is True
    assert _settings.install(make=True) == []
    assert _settings.install() == []
    nothing_written(stranded, before)


def test_branch_1_pager_settings_install_refuses_and_names_both(stranded):
    before = tree(stranded)
    r = CliRunner().invoke(cli, ["settings", "install"], prog_name="pager")
    assert r.exit_code == 3, r.output
    assert r.output == ("pager settings install: refused: " + MOVE_LINE.removeprefix("sessionmail: ")
                        + "\n"), r.output
    nothing_written(stranded, before)


def test_branch_1_the_birth_installs_nothing(stranded, tmp_path, monkeypatch):
    monkeypatch.setattr(_seat, "doorbell", lambda address, session, env=None: LIVE)
    monkeypatch.setattr(_seat, "regen_maps", lambda root, env=None: "generator ran")
    env = {"HOME": str(stranded),
           "SESSION_MAIL_ROOT": str(tmp_path / "mail" / "agents"),
           "SESSIONMAIL_HOME": str(tmp_path / "state"),
           "MAGICDNS_SUFFIX": os.environ["MAGICDNS_SUFFIX"]}
    before = tree(stranded)
    birth = _birth.ensure_session_box(SID, SimpleNamespace(address=PLUS), env=env)
    assert birth.error is None and birth.born is True        # the box is born all the same
    nothing_written(stranded, before)


def test_branch_1_the_readers_read_defaults_never_the_old_file(stranded):
    """No fallback: the readers see a missing file, not the old one."""
    before = tree(stranded)
    assert _settings.read().state == _settings.MISSING
    assert rules.alert_runner() is None
    assert _mhs.configured_registry() is None
    nothing_written(stranded, before)


def test_branch_2_neither_path_writes_the_template_at_the_new_path(home):
    assert _settings.stranded() is False
    settings, created = _settings.ensure()
    assert (settings.state, created) == (_settings.OFF, True)
    assert (home / NEW_DIR / "mail.json").read_text() == _settings.template()
    absent(home)


def test_branch_3_the_new_file_is_used_and_the_old_one_left_alone(stranded):
    """Both present: the new file decides, and nothing is written while the
    old folder is still real (`install` too): the old tree is untouched."""
    write_new(stranded, {"version": 1, "hooks": False})
    before = tree(stranded)
    assert _settings.stranded() is True
    settings, created = _settings.ensure()
    assert (settings.state, created) == (_settings.OFF, False)
    assert rules.alert_runner() is None                       # the new file sets none
    assert tree(stranded) == before


#: The stranded line, word for word: what a host upgraded from 0.1.0 is told.
MOVE_LINE = ("sessionmail: the settings in ~/.settings/mail/ have not been moved, so nothing "
             "was written and the hooks are off. Move the files in ~/.settings/mail/ to "
             "~/.sessionkit/mail/settings/, then re-run.")


def test_the_stranded_line_says_what_to_move_and_where():
    from sessionmail import pager
    assert pager.SETTINGS_STRANDED.format(old=_settings.SHOWN_LEGACY_DIR,
                                          new=_settings.SHOWN_DIR) == MOVE_LINE


def test_an_old_folder_with_no_mail_json_is_stranded_too(home):
    """The schema and template alone (no `mail.json`) would still collide
    with the move: the folder decides, not the file."""
    d = home / OLD_DIR
    d.mkdir(parents=True)
    (d / "mail.schema.json").write_text(_settings.schema())
    before = tree(home)
    assert _settings.ensure()[0].state == _settings.STRANDED
    assert _settings.install(make=True) == []
    nothing_written(home, before)


def test_a_moved_host_is_not_stranded(home):
    """After the move, `~/.settings/mail` may be a link to the new folder:
    that is not stranded, and everything works through the new path."""
    new = home / NEW_DIR
    new.mkdir(parents=True)
    (home / OLD_ROOT).mkdir()
    (home / OLD_DIR).symlink_to(new)
    assert _settings.stranded() is False
    settings, created = _settings.ensure()
    assert (settings.state, created) == (_settings.OFF, True)
    assert sorted(os.listdir(new)) == ["mail.json", "mail.schema.json", "mail.template.json"]
    assert (home / OLD_DIR).is_symlink()


# ── both real: the old files were COPIED, not moved ───────────────────────────
#
# `~/.settings/mail/` is still a real folder AND `~/.sessionkit/mail/settings/`
# is a real folder too. The new file is read and used; nothing is written to
# either folder, nothing is read from the old one, and ONE line says the old
# folder has to go. `pager settings install` refuses with the same line.

#: The copied line, word for word.
COPIED_LINE = ("sessionmail: Remove ~/.settings/mail/ once its files are in "
               "~/.sessionkit/mail/settings/.")


@pytest.fixture
def both_real(stranded):
    """The stranded host, plus a real new folder holding a mail.json (OFF)."""
    write_new(stranded, {"version": 1, "hooks": False})
    return stranded


def test_the_copied_line_says_remove_the_old_folder():
    from sessionmail import pager
    assert pager.SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR,
                                        new=_settings.SHOWN_DIR) == COPIED_LINE


def test_both_real_is_copied(both_real):
    assert (_settings.stranded(), _settings.copied()) == (True, True)


def test_only_the_old_folder_is_not_copied(stranded):
    assert (_settings.stranded(), _settings.copied()) == (True, False)


def test_only_the_new_folder_is_not_copied(home):
    write_new(home, {"version": 1, "hooks": False})
    assert (_settings.stranded(), _settings.copied()) == (False, False)
    absent(home)


def test_a_moved_host_is_not_copied(home):
    """The old folder a link to the new one (or gone): not copied."""
    new = home / NEW_DIR
    new.mkdir(parents=True)
    assert _settings.copied() is False                        # the old folder gone
    (home / OLD_ROOT).mkdir()
    (home / OLD_DIR).symlink_to(new)
    assert _settings.copied() is False                        # the old folder a link


def test_a_new_folder_that_is_a_link_is_not_copied(stranded, tmp_path):
    """Only a REAL new folder makes the pair: a link there is not one."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (stranded / NEW_DIR).parent.mkdir(parents=True)
    (stranded / NEW_DIR).symlink_to(elsewhere)
    assert (_settings.stranded(), _settings.copied()) == (True, False)


def test_both_real_ensure_and_install_write_nothing(both_real):
    """The new file decides (OFF); no schema or template is installed
    beside it, and the old tree is untouched (its mail.json is mode 000,
    so a read would be a fault)."""
    before = tree(both_real)
    settings, created = _settings.ensure()
    assert (settings.state, created) == (_settings.OFF, False)
    assert _settings.install(make=True) == []
    assert _settings.install() == []
    assert tree(both_real) == before


def test_both_real_with_no_new_mail_json_writes_nothing(stranded):
    """A real new folder with no mail.json yet: still stranded for ensure,
    and still nothing written to either folder."""
    (stranded / NEW_DIR).mkdir(parents=True)
    before = tree(stranded)
    assert _settings.copied() is True
    assert _settings.ensure()[0].state == _settings.STRANDED
    assert _settings.install(make=True) == []
    assert tree(stranded) == before


def test_both_real_pager_settings_install_refuses_with_the_copied_line(both_real):
    from sessionmail import pager
    before = tree(both_real)
    r = CliRunner().invoke(cli, ["settings", "install"], prog_name="pager")
    assert r.exit_code == pager.EX_COPIED == 4, r.output
    assert r.output == ("pager settings install: refused: "
                        + COPIED_LINE.removeprefix("sessionmail: ") + "\n"), r.output
    assert tree(both_real) == before


def test_the_install_epilog_names_the_copied_exit():
    text = " ".join(_help(cli, "settings", "install").split())
    assert "4 refused: both folders are present; remove the old one (both named)" in text, text


# ── SM-877: a settings folder that cannot be searched is "cannot tell" ──────
#
# `~/.settings` mode r--: it lists, but `~/.settings/mail` cannot be stat'd.
# Up to CPython 3.13 `Path.is_dir()` raised PermissionError there; from 3.14
# it answers False, so `stranded()` read "not stranded" and `ensure()` wrote
# a fresh OFF file at the new path while the real settings sat unseen (the
# silent OFF the stranded refusal exists to prevent). Now `stranded()` and
# `copied()` stat through `_maildir.strict_mode` and RAISE on every version,
# and every writer writes NOTHING. Each test restores the mode before the
# tree is compared (a locked folder cannot be walked).

UNCHECKED = ("~/.settings/mail/ could not be checked (Permission denied), "
             "so nothing was written")


@contextmanager
def unsearchable(folder: Path):
    """FOLDER mode 0o444 for the block: listable, not searchable."""
    folder.chmod(0o444)
    try:
        if os.access(folder, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        yield folder
    finally:
        folder.chmod(0o755)


@pytest.fixture(params=["legacy-only", "both-real"])
def locked_old(request, stranded):
    """The stranded host (and, for both-real, a real new folder with an OFF
    mail.json), with `~/.settings` made unsearchable inside the test."""
    if request.param == "both-real":
        write_new(stranded, {"version": 1, "hooks": False})
    return stranded


def test_stranded_and_copied_raise_when_the_old_folder_cannot_be_searched(locked_old):
    with unsearchable(locked_old / OLD_ROOT):
        assert sorted(os.listdir(locked_old / OLD_ROOT)) == ["mail"]   # it lists
        with pytest.raises(PermissionError):
            _settings.stranded()
        with pytest.raises(PermissionError):
            _settings.copied()


def test_ensure_writes_nothing_and_says_why_when_the_old_folder_cannot_be_searched(stranded):
    """The SM-877 fix-002 regression: on 3.14 this wrote an OFF mail.json at
    the new path (created=True). It must write NOTHING, on every version,
    and be a fault that says why."""
    before = tree(stranded)
    new = stranded / NEW_DIR / "mail.json"
    with unsearchable(stranded / OLD_ROOT):
        settings, created = _settings.ensure()
        assert (settings.state, settings.hooks, settings.fault, created) == (
            _settings.MALFORMED, False, True, False)
        assert settings.why == UNCHECKED
        assert _settings.install(make=True) == []
        assert _settings.install() == []
    assert not os.path.lexists(new), "ensure() wrote the new path"
    nothing_written(stranded, before)


def test_ensure_reads_an_existing_new_file_and_writes_nothing_when_the_old_folder_cannot_be_searched(locked_old):
    """A new mail.json that is there is read and used, as on a stranded host;
    `install` (inside `ensure`) cannot tell, so it installs nothing."""
    write_new(locked_old, {"version": 1, "hooks": True})
    before = tree(locked_old)
    with unsearchable(locked_old / OLD_ROOT):
        settings, created = _settings.ensure()
        assert (settings.state, created) == (_settings.ON, False)
    assert tree(locked_old) == before


def test_pager_settings_install_refuses_when_the_old_folder_cannot_be_searched(locked_old):
    from sessionmail import pager
    before = tree(locked_old)
    with unsearchable(locked_old / OLD_ROOT):
        r = CliRunner().invoke(cli, ["settings", "install"], prog_name="pager")
    assert r.exit_code == pager.EX_COULD_NOT_LOOK == 2, r.output
    assert r.output == f"pager settings install: refused: {UNCHECKED}\n", r.output
    assert r.exception is None or isinstance(r.exception, SystemExit), r.exception
    assert tree(locked_old) == before


def test_copied_raises_and_install_refuses_when_the_new_folder_cannot_be_searched(stranded):
    """The old folder real, `~/.sessionkit` unsearchable: `stranded` is
    True, `copied` cannot tell, and the refusal names the NEW folder."""
    (stranded / NEW_DIR).mkdir(parents=True)
    before = tree(stranded)
    with unsearchable(stranded / ".sessionkit"):
        assert _settings.stranded() is True
        with pytest.raises(PermissionError):
            _settings.copied()
        r = CliRunner().invoke(cli, ["settings", "install"], prog_name="pager")
    assert r.exit_code == 2, r.output
    assert r.output == ("pager settings install: refused: ~/.sessionkit/mail/settings/ could not "
                        "be checked (Permission denied), so nothing was written\n"), r.output
    assert tree(stranded) == before


def test_the_birth_installs_nothing_when_the_old_folder_cannot_be_searched(stranded, tmp_path, monkeypatch):
    monkeypatch.setattr(_seat, "doorbell", lambda address, session, env=None: LIVE)
    monkeypatch.setattr(_seat, "regen_maps", lambda root, env=None: "generator ran")
    env = {"HOME": str(stranded),
           "SESSION_MAIL_ROOT": str(tmp_path / "mail" / "agents"),
           "SESSIONMAIL_HOME": str(tmp_path / "state"),
           "MAGICDNS_SUFFIX": os.environ["MAGICDNS_SUFFIX"]}
    before = tree(stranded)
    with unsearchable(stranded / OLD_ROOT):
        birth = _birth.ensure_session_box(SID, SimpleNamespace(address=PLUS), env=env)
    assert birth.error is None and birth.born is True        # the box is born all the same
    nothing_written(stranded, before)


def test_the_install_epilog_names_the_could_not_check_exit():
    text = " ".join(_help(cli, "settings", "install").split())
    assert "2 a file could not be written, or a settings folder could not be checked (named)" in text, text


# ── schema-001: the sendmail key is named in the schema ──
#
# The key was named first so that `session settings <sid> mail` does not
# refuse a host file that carries it. It is now REFINED to the carrier
# vocabulary (infra-settings D3): exactly "drop" or an absolute path, with
# the drop as the template's value. tests/test_settings_keys.py holds the
# vocabulary's own tests; this keeps the property's shape pinned.

SENDMAIL_PROPERTY = {
    "description": ("How mail leaves: \"drop\" (the default) writes into this host's "
                    "maildirs with no MTA; an absolute path names the MTA (postfix's "
                    "sendmail, opt-in). Anything else is refused by name and nothing is "
                    "sent. Read from the template and host layers only. "
                    "SESSIONMAIL_SENDMAIL, when set and not empty, wins over every layer."),
    "anyOf": [{"const": "drop"}, {"type": "string", "pattern": "^/"}],
    "x-env": "SESSIONMAIL_SENDMAIL",
}


def test_the_schema_names_sendmail_as_the_carrier_vocabulary():
    prop = json.loads(_settings.schema())["properties"]["sendmail"]
    assert prop == SENDMAIL_PROPERTY
    assert "default" not in prop


def test_the_template_carries_the_drop():
    """The default carrier is set in the file: the template names the drop."""
    assert json.loads(_settings.template())["sendmail"] == "drop"


@pytest.mark.parametrize("value", ["/usr/sbin/sendmail", "drop"])
def test_the_schema_accepts_a_sendmail_layer(value):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({"sendmail": value}, json.loads(_settings.schema()))


@pytest.mark.parametrize("value", ["", 1, True, None])
def test_the_schema_refuses_an_empty_or_non_string_sendmail(value):
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"sendmail": value}, json.loads(_settings.schema()))
