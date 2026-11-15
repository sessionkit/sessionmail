"""`message setup` and notmuch: the sessionkit profile, never a stranger's.

- The one `notmuch new --no-hooks` (SM-899) and every query run through the
  one resolution, on OUR profile and a temp mail root only.
- The hook-dir query runs through the one resolution (`_notmuch._engine`):
  `[exe, --config=<profile>, config, get, database.hook_dir]`, with
  NOTMUCH_CONFIG, NOTMUCH_DATABASE and NOTMUCH_PROFILE out of the child's
  environment. With no profile it is refused by name, and nothing is linked.
- The `notmuch-profile` step writes the profile from the package template
  only when none exists AND no notmuch database is in the mail root. It
  adopts no database, and never replaces a file.

A stranger's install sits in every scratch HOME: a `~/.notmuch-config`
naming their own database, whose hook dir already holds their own hook.
Each test proves both are unchanged. Every environment is built from NAMED
keys, never from a copy of the runner's.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import write_mail_settings
from fakes import write_fake
from sessionmail import _index, _operator, _setup

NOTMUCH = shutil.which("notmuch")
OVERRIDES = ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE")

#: notmuch, as far as setup asks it, in notmuch 0.40's order (measured).
#: Under `--config=FILE`: `database.path` is FILE's; the database is
#: `<path>/.notmuch/xapian`, which `new --no-hooks` makes; `count '*'` exits
#: 0 only when it exists. The hook dir is FILE's `hook_dir=` when it pins one
#: (with or without a database); else, once the database exists,
#: `$XDG_CONFIG_HOME/notmuch/<profile>/hooks` when that folder exists (the
#: profile is `default` unless NOTMUCH_PROFILE is set; XDG_CONFIG_HOME
#: defaults to `~/.config`), else `<path>/.notmuch/hooks`; empty before. With
#: no `--config` the hook dir is the stranger's (the default config's). It
#: logs its argv and the overrides it was handed to a baked-in log.
FAKE = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(@LOG@, "a") as log:
    log.write(json.dumps({{"argv": args,
                          "env": {{k: os.environ[k] for k in @OVERRIDES@ if k in os.environ}}}}) + "\\n")
config = None
if args and args[0].startswith("--config="):
    config, args = args[0][len("--config="):], args[1:]
if config is None:
    if args == ["config", "get", "database.hook_dir"]:
        print(@DEFAULT_HOOKS@)
        sys.exit(0)
    sys.exit(1)
path = pin = ""
for line in open(config):
    if line.startswith("path="):
        path = line.strip()[len("path="):]
    if line.startswith("hook_dir="):
        pin = line.strip()[len("hook_dir="):]
database = os.path.join(path, ".notmuch")
opens = os.path.isdir(os.path.join(database, "xapian"))
xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.environ["HOME"], ".config")
fallback = os.path.join(xdg, "notmuch", os.environ.get("NOTMUCH_PROFILE") or "default", "hooks")
if args == ["config", "get", "database.path"]:
    print(path)
elif args == ["config", "get", "database.hook_dir"]:
    if pin:
        print(pin)
    elif opens:
        print(fallback if os.path.isdir(fallback) else os.path.join(database, "hooks"))
    else:
        print("")
elif args == ["count", "*"]:
    if not opens:
        sys.stderr.write("Error: Cannot open database\\n")
        sys.exit(1)
    print(0)
elif args == ["new", "--no-hooks"]:
    os.makedirs(os.path.join(database, "xapian"), exist_ok=True)
else:
    sys.exit(1)
"""


def tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Every entry under ROOT: mode, mtime, and a link's target (lstat)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_mtime_ns,
                                             os.readlink(p) if os.path.islink(p) else "")
    return out


class Host:
    """A stranger's Mac: their notmuch config, database and hook dir."""

    def __init__(self, tmp: Path, engine: str):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.xdg = tmp / "xdg"
        self.mailroot = tmp / "mail"
        self.ours = self.mailroot / ".notmuch" / "hooks"
        self.their_mail = tmp / "their-mail"
        self.theirs = self.their_mail / ".notmuch" / "hooks"
        self.their_mail.mkdir()
        self.default = self.home / ".notmuch-config"
        self.default.write_text(f"[database]\npath={self.their_mail}\n[user]\nname=Them\n")
        self.log = tmp / "nm.log"
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        if engine == "fake":
            exe = write_fake(bin_dir / "notmuch", FAKE
                             .replace("@LOG@", repr(str(self.log)))
                             .replace("@OVERRIDES@", repr(OVERRIDES))
                             .replace("@DEFAULT_HOOKS@", repr(str(self.theirs))))
        else:
            exe = Path(NOTMUCH)
            self._index(self.default)           # their database exists, as theirs does
        self.theirs.mkdir(parents=True, exist_ok=True)
        (self.theirs / "post-new").write_text("#!/bin/sh\n# their own hook\n")
        agents = str(self.mailroot / "agents")
        self.env = {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.xdg),
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "TMPDIR": str(tmp),
            "SESSIONMAIL_ROOT": str(Path(agents).parent),
            "SESSION_MAIL_HOST": "testhost", "SESSIONMAIL_HOST": "testhost",
            "SESSIONMAIL_SENDMAIL": "drop",
            "SESSIONMAIL_VMAILBOX_GEN": str(tmp / "no-generator-here"),
            "SESSIONMAIL_NOTMUCH": str(exe),
            # A stranger who exports notmuch's own variables in their shell.
            "NOTMUCH_CONFIG": str(self.default), "NOTMUCH_PROFILE": "theirs",
        }
        self.before = (self.sha(), tree(self.their_mail))

    def sha(self) -> str:
        return hashlib.sha256(self.default.read_bytes()).hexdigest()

    def _index(self, config: Path) -> None:
        clean = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        subprocess.run([NOTMUCH, f"--config={config}", "new", "--quiet"], env=clean,
                       check=True, capture_output=True)

    def profile(self) -> Path:
        """Our profile, its database the mail root, indexed once (as the
        host's own `notmuch new` would have)."""
        path = _index.profile_config(env=self.env)
        path.parent.mkdir(parents=True)
        path.write_text(f"[database]\npath={self.mailroot}\n")
        (self.mailroot / "agents").mkdir(parents=True, exist_ok=True)
        if self.env["SESSIONMAIL_NOTMUCH"] == NOTMUCH:
            self._index(path)
        else:
            (self.mailroot / ".notmuch" / "xapian").mkdir(parents=True, exist_ok=True)
        return path

    def calls(self) -> list[dict]:
        import json
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def stranger_untouched(self) -> None:
        assert self.sha() == self.before[0], "~/.notmuch-config changed"
        assert tree(self.their_mail) == self.before[1], "the stranger's database dir changed"
        assert sorted(os.listdir(self.theirs)) == ["post-new"]
        assert (self.theirs / "post-new").read_text() == "#!/bin/sh\n# their own hook\n"


def setup(env, *argv):
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(list(argv), env, out=out, err=err, tty=False)
    return rc, out.getvalue(), err.getvalue()


def state(out: str, step: str) -> str:
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split()
        if line.startswith("  ") and parts and parts[0] == step:
            return parts[1]
    return ""


def detail(out: str, step: str) -> str:
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split(None, 2)
        if line.startswith("  ") and parts and parts[0] == step:
            return parts[2] if len(parts) > 2 else ""
    return ""


ENGINES = ["fake", pytest.param("real", marks=pytest.mark.skipif(
    NOTMUCH is None, reason="notmuch is not installed"))]


@pytest.fixture(autouse=True)
def no_settings_file(stranger):
    write_mail_settings(_operator.settings_home(), None)


# ── 6.7: the stranger's hook dir is never touched ───────────────────────────


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("profile", ["with_profile", "without_profile"])
def test_setup_never_touches_stranger_hook_dir(tmp_path, engine, profile):
    host = Host(tmp_path, engine)
    if profile == "with_profile":
        path = host.profile()
    rc, out, err = setup(host.env)
    host.stranger_untouched()
    if profile == "with_profile":
        assert state(out, "notmuch-profile") == "DONE"
        for name in _setup.HOOK_NAMES:
            assert state(out, f"notmuch-hook:{name}") == "TODO", out
            assert detail(out, f"notmuch-hook:{name}").startswith(f"link {host.ours / name} -> ")
            assert os.readlink(host.ours / name) == str(_setup.PACKAGED_HOOK)
        assert rc == _setup.EX_OK, (out, err)
        if engine == "fake":
            calls = host.calls()
            # Reads only: does a database open, its hook dir, and its path
            # (the hook dir must lie under it).
            assert [c["argv"] for c in calls] == [
                [f"--config={path}", "count", "*"],
                [f"--config={path}", "config", "get", "database.hook_dir"],
                [f"--config={path}", "config", "get", "database.path"]], calls
            assert all(c["env"] == {} for c in calls), "an inherited override reached notmuch"
    else:
        # SM-899: the run that writes our profile builds OUR empty index once
        # (`new --no-hooks`, `--config=<our profile>`, overrides dropped) and
        # links the hooks there. Before, it took three runs.
        path = _index.profile_config(env=host.env)
        assert state(out, "notmuch-profile") == "TODO" and path.is_file()
        assert state(out, "notmuch-index") == "TODO", out
        assert state(out, "notmuch-hook") == "TODO", out
        assert (host.mailroot / ".notmuch").is_dir()
        for name in _setup.HOOK_NAMES:
            assert os.readlink(host.ours / name) == str(_setup.PACKAGED_HOOK)
        assert rc == _setup.EX_OK, (out, err)
        if engine == "fake":
            calls = host.calls()
            assert [c["argv"][1:] for c in calls] == [
                ["config", "get", "database.path"], ["new", "--no-hooks"],
                ["config", "get", "database.hook_dir"], ["config", "get", "database.path"]], calls
            assert all(c["argv"][0] == f"--config={path}" for c in calls)
            assert all(c["env"] == {} for c in calls), "an inherited override reached notmuch"


# ── 6.8: setup adopts no database ───────────────────────────────────────────


def test_setup_adopts_no_database(tmp_path):
    """A notmuch database already in the mail root, and no profile naming
    it: the profile step is MANUAL, nothing is written, the database dir is
    unchanged, and no hook is linked."""
    host = Host(tmp_path, "fake")
    db = host.mailroot / ".notmuch"
    (db / "xapian").mkdir(parents=True)
    (db / "xapian" / "iamglass").write_text("x")
    before = tree(db)
    rc, out, err = setup(host.env)
    assert state(out, "notmuch-profile") == "MANUAL", out
    assert detail(out, "notmuch-profile") == (
        f"a notmuch database already exists at {db} and no profile names it: setup adopts "
        "no database. Write the profile by hand (see the host-migration contract) or choose "
        "another mail root")
    assert not _index.profile_config(env=host.env).exists()
    assert not (host.xdg / "notmuch").exists()
    assert tree(db) == before
    assert state(out, "notmuch-hook") == "BLOCKED" and host.calls() == []
    assert rc == _setup.EX_REMAINS
    host.stranger_untouched()


def test_setup_writes_the_profile_from_the_template(tmp_path):
    """No profile and no database in the mail root: setup writes the
    profile from the package template, its database the mail root, with
    hook_dir pinned to `<mail root>/.notmuch/hooks` (unpinned, notmuch would
    take the user's own XDG hooks folder whenever it exists), and no hooks
    dir beside it. A second run is DONE."""
    host = Host(tmp_path, "fake")
    path = _index.profile_config(env=host.env)
    rc, out, _err = setup(host.env, "--dry-run")
    assert state(out, "notmuch-profile") == "TODO" and not path.exists()
    assert detail(out, "notmuch-profile") == f"write {path} (database.path={host.mailroot})"
    rc, out, _err = setup(host.env)
    text = path.read_text()
    assert f"\npath={host.mailroot}\n" in text
    database = text.split("\n[database]\n", 1)[1].split("\n[", 1)[0]
    assert f"\nhook_dir={host.mailroot}/.notmuch/hooks\n" in database
    assert _setup.pins_hook_dir(path)
    assert text.count("\nhook_dir=") == 1 and "${" not in text
    for section in ("[new]", "[search]", "[maildir]", "[index]", "[query]"):
        assert section in text, section
    assert "\n[sessionmail]\n" not in text, "the hook's own path defaults fit the mail root"
    assert "\npaper=path:stores/** and tag:unread\n" in text
    assert sorted(os.listdir(path.parent)) == ["config"], "a hooks dir beside the profile"
    rc, out, _err = setup(host.env)
    assert state(out, "notmuch-profile") == "DONE"
    assert path.read_text() == text
    host.stranger_untouched()


def test_an_existing_profile_is_never_replaced(tmp_path):
    host = Host(tmp_path, "fake")
    path = _index.profile_config(env=host.env)
    path.parent.mkdir(parents=True)
    path.write_text("# mine\n[database]\npath=/elsewhere\n")
    rc, out, _err = setup(host.env)
    assert state(out, "notmuch-profile") == "DONE"
    assert path.read_text() == "# mine\n[database]\npath=/elsewhere\n"


def test_the_template_ships_in_the_package():
    assert _setup.PROFILE_TEMPLATE.is_file()
    assert _setup.PROFILE_TEMPLATE.name == "notmuch-config.sessionkit.template"


# ── the XDG fallback: notmuch's hook dir for the user's OWN notmuch ─────────
#
# notmuch's hook-dir order: `database.hook_dir`, else
# `$XDG_CONFIG_HOME/notmuch/<profile>/hooks` when that folder EXISTS, else
# `<database.path>/.notmuch/hooks`. sessionmail drops NOTMUCH_PROFILE, so the
# profile is `default`: the folder the user's own notmuch runs its hooks from,
# whichever config file it read. Setup must never link there unasked.


def fallback(host: Host, xdg: str) -> Path:
    """Plant the user's own XDG hooks folder, holding a hook of their own.
    XDG: under $XDG_CONFIG_HOME, or under `~/.config` with the variable unset."""
    if xdg == "unset":
        del host.env["XDG_CONFIG_HOME"]
        base = host.home / ".config"
    else:
        base = host.xdg
    folder = base / "notmuch" / "default" / "hooks"
    folder.mkdir(parents=True)
    (folder / "pre-new").write_text("#!/bin/sh\n# the user's own\n")
    return folder


def database(host: Host) -> Path:
    """Where OUR index lives: notmuch's Xapian folder under the mail root."""
    return host.mailroot / ".notmuch" / "xapian"


XDGS = ["XDG_CONFIG_HOME", "unset"]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("xdg", XDGS)
def test_a_fresh_host_never_links_into_the_users_xdg_hooks(tmp_path, engine, xdg):
    """A fresh host whose user has an XDG hooks folder: setup writes the
    profile with hook_dir PINNED, builds the index, and links the hooks
    under the mail root. The user's folder keeps exactly its own hook."""
    host = Host(tmp_path, engine)
    theirs = fallback(host, xdg)
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_OK, (out, err)
    assert sorted(os.listdir(theirs)) == ["pre-new"], out
    for name in _setup.HOOK_NAMES:
        assert os.readlink(host.ours / name) == str(_setup.PACKAGED_HOOK)
    assert database(host).is_dir()
    host.stranger_untouched()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("xdg", XDGS)
def test_an_unpinned_profile_is_refused_never_edited(tmp_path, engine, xdg):
    """A profile written before the pin (no hook_dir), indexed, and the
    user's XDG hooks folder there: notmuch reports that folder. Setup links
    nothing, never edits the profile, and the ONE `notmuch-hook` step is
    BLOCKED with a line naming the folder and the pin to add."""
    host = Host(tmp_path, engine)
    path = host.profile()
    before = path.read_bytes()
    theirs = fallback(host, xdg)
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_REMAINS, (out, err)
    assert state(out, "notmuch-index") == "DONE", out
    assert state(out, "notmuch-hook") == "BLOCKED", out
    line = detail(out, "notmuch-hook")
    assert line.startswith(f"notmuch reports {theirs} as the hook dir for {path}, "), line
    assert (f"config set database.hook_dir {host.mailroot}/.notmuch/hooks`" in line
            and f"add `hook_dir={host.mailroot}/.notmuch/hooks` under [database]" in line), line
    assert "rm " not in line, "nothing of ours is there to remove"
    assert sorted(os.listdir(theirs)) == ["pre-new"]
    assert not host.ours.exists()
    assert path.read_bytes() == before, "setup edited the profile"
    host.stranger_untouched()


@pytest.mark.parametrize("engine", ENGINES)
def test_links_already_in_the_fallback_are_named_never_removed(tmp_path, engine):
    """An older setup linked our hooks into the user's folder. They are
    reported with the `rm` that removes exactly them, and left in place:
    the folder is the user's, so setup deletes nothing there."""
    host = Host(tmp_path, engine)
    host.profile()
    theirs = fallback(host, "XDG_CONFIG_HOME")
    for name in _setup.HOOK_NAMES:
        (theirs / name).symlink_to(_setup.PACKAGED_HOOK)
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_REMAINS, (out, err)
    assert state(out, "notmuch-hook") == "BLOCKED", out
    assert detail(out, "notmuch-hook").endswith(
        f"remove them: `rm {theirs / 'post-new'} {theirs / 'post-insert'}`"), out
    assert sorted(os.listdir(theirs)) == ["post-insert", "post-new", "pre-new"]
    assert all(os.readlink(theirs / n) == str(_setup.PACKAGED_HOOK) for n in _setup.HOOK_NAMES)


def test_a_dangling_link_of_ours_in_the_fallback_is_named_too(tmp_path):
    """Our own link left dangling there (a rebuilt tool venv) is ours as
    well: named in the `rm`, never repaired in the user's folder."""
    host = Host(tmp_path, "fake")
    host.profile()
    theirs = fallback(host, "XDG_CONFIG_HOME")
    gone = "/nowhere/site-packages/sessionmail/notmuch/post-new"
    (theirs / "post-new").symlink_to(gone)
    rc, out, _err = setup(host.env)
    assert state(out, "notmuch-hook") == "BLOCKED", out
    assert detail(out, "notmuch-hook").endswith(f"remove them: `rm {theirs / 'post-new'}`"), out
    assert os.readlink(theirs / "post-new") == gone
    assert sorted(os.listdir(theirs)) == ["post-new", "pre-new"]


@pytest.mark.parametrize("engine", ENGINES)
def test_the_deferred_hook_step_refuses_the_fallback_too(tmp_path, engine):
    """An unpinned profile with no index yet: the index is built in this
    run, then the hook dir is asked. notmuch now reports the user's folder,
    so the deferred step is left BLOCKED with the same line."""
    host = Host(tmp_path, engine)
    path = _index.profile_config(env=host.env)
    path.parent.mkdir(parents=True)
    path.write_text(f"[database]\npath={host.mailroot}\n")
    theirs = fallback(host, "XDG_CONFIG_HOME")
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_REMAINS, (out, err)
    assert state(out, "notmuch-index") == "TODO" and database(host).is_dir(), out
    assert f"notmuch-hook      BLOCKED: notmuch reports {theirs} as the hook dir" in out, out
    assert sorted(os.listdir(theirs)) == ["pre-new"]
    host.stranger_untouched()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_hook_dir_the_profile_pins_outside_the_root_is_honoured(tmp_path, engine):
    """The profile is the user's to edit: a hook_dir it pins outside its
    database is theirs to choose, and setup links there, even with the XDG
    folder planted."""
    host = Host(tmp_path, engine)
    path = host.profile()
    chosen = tmp_path / "chosen" / "hooks"
    path.write_text(f"[database]\npath={host.mailroot}\nhook_dir={chosen}\n")
    theirs = fallback(host, "XDG_CONFIG_HOME")
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_OK, (out, err)
    for name in _setup.HOOK_NAMES:
        assert os.readlink(chosen / name) == str(_setup.PACKAGED_HOOK)
    assert sorted(os.listdir(theirs)) == ["pre-new"]
    host.stranger_untouched()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_pinned_profile_with_no_index_still_builds_it(tmp_path, engine):
    """notmuch reports a PINNED hook_dir with no database at all, so a hook
    dir proves nothing. The first run has no notmuch (the profile is
    written, the index is left); the next run, with notmuch, must build the
    index, not call it DONE."""
    host = Host(tmp_path, engine)
    gone = {**host.env, "SESSIONMAIL_NOTMUCH": str(tmp_path / "no-notmuch-here")}
    rc, out, _err = setup(gone)
    path = _index.profile_config(env=host.env)
    assert rc == _setup.EX_REMAINS and "\nhook_dir=" in path.read_text(), out
    assert state(out, "notmuch-index") == "BLOCKED"
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_OK, (out, err)
    assert state(out, "notmuch-index") == "TODO", out
    assert database(host).is_dir(), "the index was never built"
    for name in _setup.HOOK_NAMES:
        assert os.readlink(host.ours / name) == str(_setup.PACKAGED_HOOK)
    host.stranger_untouched()


@pytest.mark.parametrize("engine", ENGINES)
def test_a_hooks_folder_is_no_database(tmp_path, engine):
    """A pinned profile, no index, and `<root>/.notmuch/hooks` already made
    (setup's own link makes it): that `.notmuch` is no database, so the
    index is built."""
    host = Host(tmp_path, engine)
    path = _index.profile_config(env=host.env)
    path.parent.mkdir(parents=True)
    path.write_text(f"[database]\npath={host.mailroot}\nhook_dir={host.ours}\n")
    host.ours.mkdir(parents=True)
    rc, out, err = setup(host.env)
    assert rc == _setup.EX_OK, (out, err)
    assert state(out, "notmuch-index") == "TODO", out
    assert database(host).is_dir()


@pytest.mark.parametrize("text, pinned", [
    ("[database]\npath=/m\nhook_dir=/m/.notmuch/hooks\n", True),
    ("[database]\npath=/m\n  hook_dir = /h  \n", True),
    ("[database]\npath=/m\n# hook_dir=/h\n", False),
    ("[database]\npath=/m\nhook_dir=\n", False),
    ("[database]\npath=/m\n[new]\nhook_dir=/h\n", False),
    ("hook_dir=/h\n[database]\npath=/m\n", False),
])
def test_the_pin_is_read_from_the_file(tmp_path, text, pinned):
    profile = tmp_path / "config"
    profile.write_text(text)
    assert _setup.pins_hook_dir(profile) is pinned
    assert _setup.pins_hook_dir(tmp_path / "not-there") is False
