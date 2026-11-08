"""Host mode's notmuch profile lives under the kit's settings folder.

A fresh host's profile is `~/.sessionkit/mail/settings/notmuch/<name>/config`
(`_index.kit_profile_config`, off HOME only). An old host keeps its file
while it is REAL, the kit's rule for a leaf that moved (`_settings` rule 2):

1. `$XDG_CONFIG_HOME/notmuch/<name>/config`, a file;
2. else `~/.notmuch-config.<name>`, a file;
3. else the kit's file.

"Real" is a file, symlinks followed, that is not the kit's file
(`os.path.samefile`): a link left at an old path that points AT the kit's
file hands over to it. Nothing is moved, copied or migrated. `message setup`
writes the kit's path only when nothing old is real, so a fresh host has
nothing under `~/.config/notmuch` or at `~/.notmuch-config.*`.

Store mode keeps notmuch's own order (`_index.candidates`, XDG then dot) and
never reads the kit's file: store-index's lookup is pinned to it.

The INDEX does not move: it stays at the mail root's `.notmuch`, and the
hook that `message setup` links there runs from there. The parity test
runs the real notmuch (skipped where it is absent): `notmuch --config=<the
kit's file> new`, with NOTMUCH_CONFIG unset in the caller, runs post-new,
and post-new reads a key set only in the kit's file.

Every environment here is built from NAMED keys, never from a copy of the
runner's: a scratch HOME, `SESSIONMAIL_SENDMAIL=drop`,
`SESSIONMAIL_VMAILBOX_GEN=/usr/bin/true`, and a fake notmuch unless the
test says otherwise.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import write_mail_settings
from fakes import write_fake
from sessionmail import _index, _operator, _setup
from test_setup_notmuch_profile import FAKE, OVERRIDES

NOTMUCH = shutil.which("notmuch")
#: The kit's profile folder, relative to HOME.
KIT = Path(".sessionkit") / "mail" / "settings" / "notmuch"
PROFILE = "[database]\npath={root}\n"


@pytest.fixture(autouse=True)
def no_settings_file():
    _index.clear_cache()
    write_mail_settings(_operator.settings_home(), None)
    yield
    _index.clear_cache()


class Home:
    """A scratch HOME. XDG_CONFIG_HOME is unset (so `~/.config`) unless
    `xdg` names one, and notmuch is the fake unless `engine` is a path."""

    def __init__(self, tmp: Path, xdg: Path | None = None, engine: str | None = None):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.mailroot = tmp / "mail"
        self.log = tmp / "nm.log"
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        exe = engine or str(write_fake(bin_dir / "notmuch", FAKE
                                       .replace("@LOG@", repr(str(self.log)))
                                       .replace("@OVERRIDES@", repr(OVERRIDES))
                                       .replace("@DEFAULT_HOOKS@", repr(""))))
        agents = str(self.mailroot / "agents")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "TMPDIR": str(tmp),
            "SESSIONMAIL_ROOT": str(Path(agents).parent),
            "SESSION_MAIL_HOST": "testhost", "SESSIONMAIL_HOST": "testhost",
            "SESSIONMAIL_SENDMAIL": "drop",
            "SESSIONMAIL_VMAILBOX_GEN": "/usr/bin/true",
            "SESSIONMAIL_NOTMUCH": exe,
        }
        self.xdg = xdg if xdg is not None else self.home / ".config"
        if xdg is not None:
            self.env["XDG_CONFIG_HOME"] = str(xdg)
        self.kit = self.home / KIT / "sessionkit" / "config"
        self.old_xdg = self.xdg / "notmuch" / "sessionkit" / "config"
        self.old_dot = self.home / ".notmuch-config.sessionkit"

    def write(self, path: Path, text: str | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text is not None else PROFILE.format(root=self.mailroot))
        return path

    def notmuch_files_outside_the_kit(self) -> list[Path]:
        found = list(self.home.glob(".notmuch-config*"))
        found += list((self.home / ".config").rglob("*")) if (self.home / ".config").exists() \
            else []
        return found


def setup(env, *argv):
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(list(argv), env, out=out, err=err, tty=False)
    return rc, out.getvalue(), err.getvalue()


def step(out: str, name: str) -> tuple[str, str]:
    """(state, detail) of step NAME in setup's report."""
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split(None, 2)
        if line.startswith("  ") and parts and parts[0] == name:
            return parts[1], parts[2] if len(parts) > 2 else ""
    return "", ""


# ── the resolver ───────────────────────────────────────────────────────────


def test_the_kit_path_hangs_off_home_only(tmp_path):
    home = tmp_path / "h"
    for env in ({"HOME": str(home)}, {"HOME": str(home), "XDG_CONFIG_HOME": ""},
                {"HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "x")}):
        assert _index.kit_profiles(env) == home / KIT
        assert _index.kit_profile_config(env=env) == home / KIT / "sessionkit" / "config"
        assert _index.kit_profile_config("work", env) == home / KIT / "work" / "config"
    assert not home.exists(), "a lookup creates nothing"


def test_a_fresh_home_resolves_to_the_kit_file(tmp_path):
    h = Home(tmp_path)
    h.write(h.kit)
    r = _index.resolve(h.env)
    assert (r.mode, r.config, r.profile, r.refused) == ("host", h.kit, "sessionkit", False)
    assert r.tried == (str(h.old_xdg), str(h.old_dot))
    assert _index.argv("nm", h.env) == ["nm", f"--config={h.kit}"]
    assert _index.oracle_env(h.env)["NOTMUCH_CONFIG"] == str(h.kit)
    assert _index.profile_config(env=h.env) == h.kit


def test_no_file_anywhere_refuses_naming_all_three(tmp_path):
    h = Home(tmp_path)
    r = _index.resolve(h.env)
    assert r.refused and r.config is None
    assert r.tried == (str(h.old_xdg), str(h.old_dot), str(h.kit))
    assert f"tried {h.old_xdg}; {h.old_dot}; {h.kit}; `message setup` writes it" in r.refusal
    assert _index.profile_config(env=h.env) == h.kit, "the path to write is the kit's"
    assert h.notmuch_files_outside_the_kit() == [] and not h.kit.exists()


@pytest.mark.parametrize("custom_xdg", [False, True])
def test_an_old_xdg_file_wins_over_the_kit_file(tmp_path, custom_xdg):
    """Old and new both real: old is read, new never (stranded, not merged)."""
    h = Home(tmp_path, xdg=tmp_path / "xdg" if custom_xdg else None)
    h.write(h.old_xdg)
    h.write(h.kit, "[database]\npath=/never/read\n")
    assert _index.resolve(h.env).config == h.old_xdg
    assert _index.profile_config(env=h.env) == h.old_xdg
    assert _index.host_profile_file("sessionkit", h.env) == (h.old_xdg, ())


def test_an_old_dot_file_wins_over_the_kit_file(tmp_path):
    h = Home(tmp_path)
    h.write(h.old_dot)
    h.write(h.kit, "[database]\npath=/never/read\n")
    assert _index.resolve(h.env).config == h.old_dot
    assert _index.host_profile_file("sessionkit", h.env) == (h.old_dot, (str(h.old_xdg),))


def test_the_xdg_file_beats_the_dot_file(tmp_path):
    h = Home(tmp_path)
    h.write(h.old_xdg)
    h.write(h.old_dot)
    assert _index.resolve(h.env).config == h.old_xdg


@pytest.mark.parametrize("which", ["xdg", "dot"])
def test_an_old_link_to_the_kit_file_hands_over(tmp_path, which):
    """A move that leaves a symlink at the old path, pointing AT the kit's
    file: that old path is not real, so the kit's file is read."""
    h = Home(tmp_path)
    h.write(h.kit)
    old = h.old_xdg if which == "xdg" else h.old_dot
    old.parent.mkdir(parents=True, exist_ok=True)
    old.symlink_to(h.kit)
    assert _index.resolve(h.env).config == h.kit


def test_an_old_link_to_another_file_is_real(tmp_path):
    """Today's `is_file` already follows a link; one to a file that is not
    the kit's is a real old file."""
    h = Home(tmp_path)
    elsewhere = h.write(tmp_path / "dotfiles" / "notmuch-config")
    h.old_xdg.parent.mkdir(parents=True)
    h.old_xdg.symlink_to(elsewhere)
    h.write(h.kit)
    assert _index.resolve(h.env).config == h.old_xdg


def test_a_dangling_old_link_or_an_old_directory_is_absent(tmp_path):
    h = Home(tmp_path)
    h.old_xdg.parent.mkdir(parents=True)
    h.old_xdg.symlink_to(tmp_path / "gone")
    h.old_dot.mkdir()
    assert _index.resolve(h.env).refused
    h.write(h.kit)
    assert _index.resolve(h.env).config == h.kit


def test_a_kit_directory_is_not_a_profile_file(tmp_path):
    h = Home(tmp_path)
    h.kit.mkdir(parents=True)
    assert _index.resolve(h.env).refused


def test_the_profile_name_picks_the_kit_folder(tmp_path):
    h = Home(tmp_path)
    work = h.write(h.home / KIT / "work" / "config")
    r = _index.resolve({**h.env, _index.PROFILE_KEY_VAR: "work"})
    assert (r.profile, r.config) == ("work", work)


def test_store_mode_never_reads_the_kit_file(tmp_path):
    """Store mode keeps notmuch's own order: a kit file of the same name is
    never found by it, and its `tried` is notmuch's two paths only."""
    h = Home(tmp_path)
    h.write(h.kit)
    env = {**h.env, _index.VAR: _index.STORE, _index.PROFILE_VAR: "sessionkit"}
    r = _index.resolve(env)
    assert r.refused and r.config is None
    assert r.tried == tuple(map(str, _index.candidates("sessionkit", env)))
    assert str(h.kit) not in r.refusal
    assert _index.profile_file("sessionkit", env) == (None, r.tried)
    h.write(h.old_dot)
    assert _index.resolve(env).config == h.old_dot


# ── message setup ──────────────────────────────────────────────────────────


def test_setup_on_a_fresh_home_writes_the_kit_file_and_nothing_of_notmuchs(tmp_path):
    h = Home(tmp_path)
    rc, out, _err = setup(h.env, "--dry-run")
    assert step(out, "notmuch-profile") == (
        "TODO", f"write {h.kit} (database.path={h.mailroot})")
    assert not h.kit.exists()
    rc, out, err = setup(h.env)
    assert rc == _setup.EX_OK, (out, err)
    assert h.kit.is_file() and f"\npath={h.mailroot}\n" in h.kit.read_text()
    assert sorted(os.listdir(h.kit.parent)) == ["config"]
    assert h.notmuch_files_outside_the_kit() == []
    assert not (h.home / ".config").exists()
    assert _index.resolve(h.env).config == h.kit
    calls = [json.loads(line) for line in h.log.read_text().splitlines()]
    assert calls and all(c["argv"][0] == f"--config={h.kit}" for c in calls)
    rc, out, _err = setup(h.env)
    assert step(out, "notmuch-profile")[0] == "DONE" and str(h.kit) in out


@pytest.mark.parametrize("custom_xdg", [False, True])
def test_setup_on_an_old_xdg_home_changes_nothing(tmp_path, custom_xdg):
    h = Home(tmp_path, xdg=tmp_path / "xdg" if custom_xdg else None)
    text = PROFILE.format(root=h.mailroot) + "# the host's own\n"
    h.write(h.old_xdg, text)
    rc, out, err = setup(h.env)
    state, detail = step(out, "notmuch-profile")
    assert state == "DONE" and detail.startswith(f"{h.old_xdg} (profile sessionkit")
    assert h.old_xdg.read_text() == text, "the old file was edited"
    assert not _index.kit_profiles(h.env).exists(), "a kit profile was made beside a real one"
    assert not h.old_dot.exists()
    assert _index.resolve(h.env).config == h.old_xdg
    assert rc == _setup.EX_OK, (out, err)


def test_setup_on_an_old_dot_home_changes_nothing(tmp_path):
    h = Home(tmp_path)
    text = PROFILE.format(root=h.mailroot) + "# the host's own\n"
    h.write(h.old_dot, text)
    rc, out, err = setup(h.env)
    state, detail = step(out, "notmuch-profile")
    assert state == "DONE" and detail.startswith(f"{h.old_dot} (profile sessionkit")
    assert h.old_dot.read_text() == text
    assert not _index.kit_profiles(h.env).exists()
    assert not (h.home / ".config").exists(), "nothing was copied to the XDG path"
    assert _index.resolve(h.env).config == h.old_dot
    assert rc == _setup.EX_OK, (out, err)


def test_setup_never_writes_over_a_kit_file(tmp_path):
    h = Home(tmp_path)
    h.write(h.kit, "# mine\n[database]\npath=/elsewhere\n")
    rc, out, _err = setup(h.env)
    assert step(out, "notmuch-profile")[0] == "DONE"
    assert h.kit.read_text() == "# mine\n[database]\npath=/elsewhere\n"


# ── parity: the hook, run by notmuch on the kit file, reads the kit file ──


@pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")
def test_post_new_run_on_the_kit_file_reads_the_kit_file(tmp_path):
    """Setup on a fresh HOME with the real notmuch: it writes the kit file,
    builds the index at the mail root and links the hooks there. Then
    `notmuch --config=<resolved> new`, NOTMUCH_CONFIG unset, runs post-new,
    and post-new honours `sessionmail.agent_query`, set ONLY in the kit
    file: the message under that path is tagged `agent`, the one outside it
    is not (today's default query would have tagged both)."""
    h = Home(tmp_path, engine=NOTMUCH)
    for box in ("alpha", "beta"):
        for sub in ("cur", "new", "tmp"):
            (h.mailroot / "agents" / box / sub).mkdir(parents=True, exist_ok=True)
    rc, out, err = setup(h.env)
    assert h.kit.is_file(), (out, err)
    assert h.notmuch_files_outside_the_kit() == []
    config = _index.resolve(h.env).config
    assert config == h.kit
    hooks = h.mailroot / ".notmuch" / "hooks"
    assert os.readlink(hooks / "post-new") == str(_setup.PACKAGED_HOOK), (out, err)

    clean = {"HOME": h.env["HOME"], "PATH": h.env["PATH"], "TMPDIR": str(tmp_path),
             "SESSIONMAIL_NOTMUCH": NOTMUCH}
    nm = [NOTMUCH, f"--config={config}"]
    subprocess.run([*nm, "config", "set", "sessionmail.agent_query", "path:agents/alpha/**"],
                   env=clean, check=True, capture_output=True)
    epoch = 1907193600  # 2030-06-09 00:00 UTC, one of the two synthetic Sundays
    for n, box in enumerate(("alpha", "beta")):
        (h.mailroot / "agents" / box / "new" / f"{epoch + n}.M1P1.test").write_text(
            f"From: x@example.invalid\nTo: {box}@example.invalid\nSubject: {box}\n"
            f"Date: Sun, 09 Jun 2030 12:00:00 +0000\nMessage-ID: <{box}@kit.test>\n\nbody\n")
    run = subprocess.run([*nm, "new"], env=clean, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr

    def tags(msgid: str) -> list[str]:
        got = subprocess.run([*nm, "search", "--output=tags", f"id:{msgid}"], env=clean,
                             check=True, capture_output=True, text=True)
        return got.stdout.split()

    assert "agent" in tags("alpha@kit.test"), "post-new did not run, or read another file"
    assert "agent" not in tags("beta@kit.test"), "post-new read a config without the key"
    assert h.notmuch_files_outside_the_kit() == []
