"""`message setup` and notmuch: the sessionkit profile, never a stranger's.

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

#: notmuch, as far as setup asks it. Under `--config=FILE` the hook dir is
#: `<FILE's database.path>/.notmuch/hooks`, notmuch's own default; with no
#: `--config` it is the stranger's (the default config's). It logs its argv
#: and the overrides it was handed to a baked-in log.
FAKE = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(@LOG@, "a") as log:
    log.write(json.dumps({{"argv": args,
                          "env": {{k: os.environ[k] for k in @OVERRIDES@ if k in os.environ}}}}) + "\\n")
config = None
if args and args[0].startswith("--config="):
    config, args = args[0][len("--config="):], args[1:]
if args != ["config", "get", "database.hook_dir"]:
    sys.exit(1)
if config is None:
    print(@DEFAULT_HOOKS@)
    sys.exit(0)
path = ""
for line in open(config):
    if line.startswith("path="):
        path = line.strip()[len("path="):]
print(os.path.join(path, ".notmuch", "hooks"))
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
            "SESSION_MAIL_ROOT": agents, "SESSIONMAIL_ROOT": agents,
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
            (self.mailroot / ".notmuch").mkdir(parents=True, exist_ok=True)
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
            assert calls and all(c["argv"] == [f"--config={path}", "config", "get",
                                               "database.hook_dir"] for c in calls)
            assert all(c["env"] == {} for c in calls), "an inherited override reached notmuch"
    else:
        assert state(out, "notmuch-hook") == "BLOCKED", out
        assert 'no notmuch profile for this host: profile "sessionkit" (the default)' in out
        assert "The default notmuch config is never read" in out
        assert not host.ours.exists(), "a hook was linked with no profile"
        assert host.calls() == [], "notmuch ran with no profile"
        assert rc == _setup.EX_REMAINS, (out, err)


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
    profile from the package template, its database the mail root, with no
    hook_dir line and no hooks dir beside it. A second run is DONE."""
    host = Host(tmp_path, "fake")
    path = _index.profile_config(env=host.env)
    rc, out, _err = setup(host.env, "--dry-run")
    assert state(out, "notmuch-profile") == "TODO" and not path.exists()
    assert detail(out, "notmuch-profile") == f"write {path} (database.path={host.mailroot})"
    rc, out, _err = setup(host.env)
    text = path.read_text()
    assert f"\npath={host.mailroot}\n" in text
    assert "\nhook_dir=" not in text and "${" not in text
    for section in ("[new]", "[search]", "[maildir]", "[index]", "[query]"):
        assert section in text, section
    assert "\n[sessionmail]\n" not in text, "the hook's own path defaults fit the mail root"
    assert "\npaper=path:agents/** and tag:unread\n" in text
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
