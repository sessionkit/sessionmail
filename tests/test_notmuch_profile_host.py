"""Host mode reads the sessionkit notmuch profile, never a stranger's config.

Host mode is `SESSIONMAIL_INDEX` unset, empty or `host`. Its profile name is
`$SESSIONMAIL_NOTMUCH_PROFILE` when set, else the `notmuch.profile` key in
mail.json, else `sessionkit`. The profile FILE is found as notmuch finds it
(`$XDG_CONFIG_HOME/notmuch/<name>/config`, then `~/.notmuch-config.<name>`).
Every call passes `--config=<file>` and drops NOTMUCH_CONFIG,
NOTMUCH_DATABASE and NOTMUCH_PROFILE from the child's environment. With no
file, the call is refused by name: `~/.notmuch-config` is never a fallback.

Every environment here is built from NAMED keys, never from a copy of the
runner's. A stranger's `~/.notmuch-config` sits in the scratch HOME of every
test, and each test proves it is byte for byte unchanged. One test runs the
real notmuch, when it is installed, against two scratch databases.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import write_mail_settings
from fakes import write_fake
from sessionmail import _index, _mhs, _notmuch, _operator, _sender

NOTMUCH = shutil.which("notmuch")
OVERRIDES = ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE")

#: A fake notmuch that logs its argv and every override it was handed, to a
#: log whose path is baked in, so the log never depends on the env under test.
FAKE = """#!{python}
import json, os, sys
with open(@LOG@, "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:],
                          "env": {{k: os.environ[k] for k in @OVERRIDES@ if k in os.environ}}}}) + "\\n")
sys.stdout.write("[]")
"""

STRANGER_CONFIG = "[database]\npath=/somewhere/the/stranger/keeps/mail\n[user]\nname=Someone Else\n"


@pytest.fixture(autouse=True)
def fresh_resolver():
    _index.clear_cache()
    write_mail_settings(_operator.settings_home(), None)
    yield
    _index.clear_cache()


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.xdg = tmp / "xdg"
        self.stranger = self.home / ".notmuch-config"
        self.stranger.write_text(STRANGER_CONFIG)
        self.stranger_sha = self.sha()
        self.log = tmp / "nm.log"
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        source = (FAKE.replace("@LOG@", repr(str(self.log)))
                  .replace("@OVERRIDES@", repr(OVERRIDES)))
        self.fake = write_fake(bin_dir / "notmuch", source)
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.xdg),
                    "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
                    "SESSION_MAIL_ROOT": str(tmp / "mail" / "agents"),
                    "SESSIONMAIL_NOTMUCH": str(self.fake)}

    def sha(self) -> str:
        return hashlib.sha256(self.stranger.read_bytes()).hexdigest()

    def profile(self, name: str = "sessionkit") -> Path:
        path = self.xdg / "notmuch" / name / "config"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[database]\npath={self.tmp / 'mail'}\n")
        return path

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def world(tmp_path) -> World:
    w = World(tmp_path)
    yield w
    assert w.sha() == w.stranger_sha, "the stranger's ~/.notmuch-config was changed"


# ── 6.4: host mode reads the profile, never the default config ──────────────


@pytest.mark.parametrize("value", [None, "", "host"])
def test_host_mode_reads_profile_not_default(world, value):
    """With NOTMUCH_PROFILE unset and a stranger's ~/.notmuch-config
    present, every host-mode call reads the sessionkit profile."""
    path = world.profile()
    env = dict(world.env)
    if value is not None:
        env[_index.VAR] = value
    r = _index.resolve(env)
    assert (r.mode, r.config, r.profile, r.refused) == ("host", path, "sessionkit", False)
    assert r.host
    assert _index.argv("nm", env) == ["nm", f"--config={path}"]
    assert _index.oracle_env(env)["NOTMUCH_CONFIG"] == str(path)
    _notmuch.search("tag:unread", env)
    calls = world.calls()
    assert calls and all(c["argv"][0] == f"--config={path}" for c in calls)
    assert all(c["env"] == {} for c in calls)


def test_the_sender_lookup_reads_the_profile(world):
    """`_sender.notmuch_files` (the parent lookup) passes the profile too."""
    path = world.profile()
    root = world.tmp / "mail" / "agents"
    root.mkdir(parents=True)
    assert _sender.notmuch_files(root, {"<x@example.invalid>"}, world.env) == {}
    calls = world.calls()
    assert calls and calls[0]["argv"][0] == f"--config={path}" and calls[0]["env"] == {}


def test_the_oracle_runs_under_the_profile(world, tmp_path):
    """The bundled oracle calls bare `notmuch`: it is handed
    NOTMUCH_CONFIG=<the profile>, and nothing else of notmuch's."""
    path = world.profile()
    seen = tmp_path / "oracle-env.json"
    oracle = write_fake(tmp_path / "session-mail-search", (
        "#!{python}\nimport json, os\n"
        "open(@SEEN@, 'w').write(json.dumps("
        "{{k: os.environ[k] for k in @OVERRIDES@ if k in os.environ}}))\n")
        .replace("@SEEN@", repr(str(seen))).replace("@OVERRIDES@", repr(OVERRIDES)))
    env = {**world.env, "NOTMUCH_CONFIG": str(world.stranger), "NOTMUCH_PROFILE": "theirs"}
    assert _mhs.run([str(oracle)], env) == 0
    assert json.loads(seen.read_text()) == {"NOTMUCH_CONFIG": str(path)}


def test_the_profile_name_variable_beats_the_file_beats_the_default(world):
    """The name: $SESSIONMAIL_NOTMUCH_PROFILE when set and not empty, else
    `notmuch.profile` in mail.json, else `sessionkit`."""
    for name in ("sessionkit", "filed", "given"):
        world.profile(name)
    r = _index.resolve(world.env)
    assert (r.profile, r.via) == ("sessionkit", "the default")
    write_mail_settings(_operator.settings_home(),
                        json.dumps({"version": 1, "hooks": True,
                                    "notmuch": {"profile": "filed"}}))
    r = _index.resolve(world.env)
    assert r.profile == "filed" and r.via.startswith("from notmuch.profile in ")
    assert r.config == world.xdg / "notmuch" / "filed" / "config"
    for blank in ("", "  "):
        assert _index.resolve({**world.env, _index.PROFILE_KEY_VAR: blank}).profile == "filed"
    r = _index.resolve({**world.env, _index.PROFILE_KEY_VAR: "given"})
    assert (r.profile, r.via) == ("given", f"from ${_index.PROFILE_KEY_VAR}")
    assert r.config == world.xdg / "notmuch" / "given" / "config"


def test_a_dot_profile_is_found_as_notmuch_finds_it(world):
    """No XDG file: `~/.notmuch-config.<name>`, notmuch's own second place.
    Never `~/.notmuch-config` itself."""
    dot = world.home / ".notmuch-config.sessionkit"
    dot.write_text("[database]\n")
    assert _index.resolve(world.env).config == dot


@pytest.mark.parametrize("bad", ["../escape", "a/b", "."])
def test_a_profile_name_that_is_not_one_is_refused(world, bad):
    r = _index.resolve({**world.env, _index.PROFILE_KEY_VAR: bad})
    assert r.refused and json.dumps(bad) in r.refusal and r.config is None


def test_profile_config_is_the_path_to_write(tmp_path):
    """A function of HOME and XDG_CONFIG_HOME at call time: XDG unset or
    empty is $HOME/.config."""
    assert _index.SESSIONKIT_PROFILE == "sessionkit"
    assert _index.PROFILE_KEY_VAR == "SESSIONMAIL_NOTMUCH_PROFILE"
    home = tmp_path / "h"
    for xdg in (None, ""):
        env = {"HOME": str(home)} if xdg is None else {"HOME": str(home), "XDG_CONFIG_HOME": xdg}
        assert _index.profile_config(env=env) == home / ".config" / "notmuch" / "sessionkit" / "config"
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "x")}
    assert _index.profile_config("other", env) == tmp_path / "x" / "notmuch" / "other" / "config"


# ── 6.5: the inherited notmuch variables never reach a child ────────────────


def test_inherited_notmuch_vars_scrubbed(world):
    """A stranger who exports notmuch's own variables in their shell: none
    of them reaches a host-mode child, and none selects the config."""
    path = world.profile()
    world.profile("theirs")
    env = {**world.env, "NOTMUCH_CONFIG": str(world.stranger),
           "NOTMUCH_DATABASE": str(world.tmp / "their-db"), "NOTMUCH_PROFILE": "theirs",
           "KEEP": "me"}
    r = _index.resolve(env)
    assert (r.config, r.profile) == (path, "sessionkit")
    child = _index.child_env(env)
    assert not any(k in child for k in OVERRIDES) and child["KEEP"] == "me"
    oracle = _index.oracle_env(env)
    assert oracle["NOTMUCH_CONFIG"] == str(path)
    assert "NOTMUCH_DATABASE" not in oracle and "NOTMUCH_PROFILE" not in oracle
    assert env["NOTMUCH_CONFIG"] == str(world.stranger), "the caller's env is not edited"
    _notmuch.search("tag:unread", env)
    _notmuch.revision(env)
    calls = world.calls()
    assert calls and all(c["argv"][0] == f"--config={path}" and c["env"] == {} for c in calls)


# ── 6.6: no profile file is a refusal, by name ──────────────────────────────


def test_no_profile_refuses_by_name(world):
    """No sessionkit profile and a stranger's ~/.notmuch-config present: the
    call is refused, naming the profile and every path tried, and notmuch is
    never run, so the default config is never read."""
    r = _index.resolve(world.env)
    assert r.refused and r.config is None and r.mode == "host" and not r.host
    xdg, dot = _index.candidates("sessionkit", world.env)
    assert r.refusal == (
        'no notmuch profile for this host: profile "sessionkit" (the default) has no file; '
        f"tried {xdg}; {dot}; `message setup` writes it. "
        "The default notmuch config is never read")
    assert str(world.stranger) not in r.refusal.replace(str(dot), "")
    with pytest.raises(_notmuch.CouldNotLook, match='profile "sessionkit"'):
        _notmuch.search("tag:unread", world.env)
    with pytest.raises(_notmuch.CouldNotLook):
        _index.argv("nm", world.env)
    assert _notmuch.revision(world.env) is None
    assert _sender.notmuch_files(world.tmp, {"<x@example.invalid>"}, world.env) == {}
    assert world.calls() == [], "notmuch ran with no profile"


def test_the_refusal_names_where_the_name_came_from(world):
    r = _index.resolve({**world.env, _index.PROFILE_KEY_VAR: "given"})
    assert r.refusal.startswith(
        f'no notmuch profile for this host: profile "given" (from ${_index.PROFILE_KEY_VAR}) '
        "has no file; tried ")
    write_mail_settings(_operator.settings_home(),
                        json.dumps({"version": 1, "hooks": True,
                                    "notmuch": {"profile": "filed"}}))
    r = _index.resolve(world.env)
    assert r.refusal.startswith('no notmuch profile for this host: profile "filed" (from '
                                "notmuch.profile in ")


def test_no_profile_even_with_notmuch_config_set(world):
    """An inherited NOTMUCH_CONFIG is no way round the refusal."""
    r = _index.resolve({**world.env, "NOTMUCH_CONFIG": str(world.stranger),
                        "NOTMUCH_PROFILE": "theirs"})
    assert r.refused and 'profile "sessionkit" (the default)' in r.refusal


# ── the real notmuch: two scratch databases ─────────────────────────────────


def _db(tmp: Path, name: str, msgid: str) -> Path:
    """A scratch maildir with one message, indexed under its own config."""
    mail = tmp / name / "mail"
    for leaf in ("cur", "new", "tmp"):
        (mail / "box" / leaf).mkdir(parents=True)
    (mail / "box" / "cur" / "1.M1P1.h:2,S").write_text(
        f"From: a@example.invalid\nTo: b@example.invalid\nSubject: {name}\n"
        f"Message-ID: <{msgid}>\nDate: Sun, 09 Jun 2030 00:00:00 +0000\n\nx\n")
    cfg = tmp / name / "config"
    cfg.write_text(f"[database]\npath={mail}\n[new]\ntags=unread;\n")
    clean = {"HOME": str(tmp), "PATH": os.environ.get("PATH", "")}
    subprocess.run([NOTMUCH, f"--config={cfg}", "new", "--quiet"], env=clean, check=True,
                   capture_output=True)
    return cfg


@pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")
def test_host_mode_reads_profile_not_default_real_notmuch(world, tmp_path):
    """The stranger's default config names a database holding
    `theirs@example.invalid`; the sessionkit profile names one holding
    `ours@example.invalid`. A host-mode search finds ours alone, even with
    NOTMUCH_CONFIG exported to the stranger's file."""
    theirs = _db(tmp_path, "theirs", "theirs@example.invalid")
    ours = _db(tmp_path, "ours", "ours@example.invalid")
    world.stranger.write_text(theirs.read_text())
    world.stranger_sha = world.sha()
    profile = world.xdg / "notmuch" / "sessionkit" / "config"
    profile.parent.mkdir(parents=True)
    shutil.copyfile(ours, profile)
    env = {**world.env, "SESSIONMAIL_NOTMUCH": NOTMUCH, "NOTMUCH_CONFIG": str(world.stranger)}
    found = [m.id for m in _notmuch.search("*", env)]
    assert found == ["ours@example.invalid"]
