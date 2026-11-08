"""SM-996: the readers keep the notmuch index fresh after `message setup`.

`message setup` builds the EMPTY index once (`notmuch new --no-hooks`, the
`notmuch-index` step) and says so in NOTMUCH_NEW_NOTE: "nothing here keeps it
fresh". Mail delivered after setup rests in `new/`, and every reader that asks
the index cannot see it until something runs `notmuch --config=<profile> new`:

- `memory wake` RECENT shows 0 threads (ADDRESSED and COUNTS walk the
  maildir, so the same readout shows the mail resting there);
- `message mark` on a box that is not the seat's own refuses "not in the
  index" (SM-714's disk fallback covers the seat's OWN two boxes only);
- `memory ls --box` finds no `[ROOT]`.

repro-001 committed the three readers as strict xfails. fix-001 turned them
green by making the readers refresh the index themselves (`_fresh`): `memory`
runs a bounded `notmuch new --no-hooks` before it reads, and `message mark`
runs it once on a miss. No test here runs `notmuch new` by hand except the
control, which shows that one hand `notmuch new` is the whole difference;
`test_refresh_off_keeps_the_window` shows the off switch brings the window
back, so the wake itself indexes nothing.

Hermetic, on the conftest's seams: setup runs IN-PROCESS, so the patched
`_operator.settings_home` (the password-database home, NOT `$HOME`) holds; a
scratch `$HOME` alone does not isolate setup's `host` step (measured in
repro-001: it wrote the live host file). The carrier is the drop under a temp
root, asserted before any delivery. The real notmuch runs on temp paths only,
with an environment built from named keys.
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
from click.testing import CliRunner

import _leaves
from conftest import write_mail_settings
from sessionmail import _fresh, _index, _operator, _settings, _setup
from sessionmail.memory import cli as memory_cli
from sessionmail.message import cli as message_cli

NOTMUCH = shutil.which("notmuch")
pytestmark = pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")

BOX = "alpha"
SESSION = "5e55a996-0000-4000-8000-000000000996"
HOST = "testhost"
DATE = "Sun, 09 Jun 2030 12:00:00 +1000"
AFTER = "after-setup@example.invalid"
SUBJECT = "delivered after setup"
ROOT_MID = "root-after-setup@example.invalid"


def letter(mid: str, subject: str) -> str:
    """A letter as the drop writes it: the stamp first, then the headers."""
    return (f"X-Original-To: {BOX}\n"
            f"From: beta@example.invalid\n"
            f"To: {BOX}@{HOST}\n"
            f"Date: {DATE}\n"
            f"Subject: {subject}\n"
            f"Message-ID: <{mid}>\n"
            "\n"
            "a body\n")


def tree(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            out[os.path.relpath(os.path.join(dirpath, name), root)] = (st.st_mode, st.st_mtime_ns)
    return out


class Host:
    """A fresh host, after `message setup` and one delivery."""

    def __init__(self, tmp: Path, monkeypatch):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        # sessiontools' leaves for this fresh HOME, pinned before any verb
        # runs (`_leaves`): a session's marks are named by them.
        _leaves.pin(self.home)
        self.mailroot = tmp / "mail"
        self.agents = self.mailroot / "stores"
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "notmuch").symlink_to(NOTMUCH)
        self.env = {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(tmp / "xdg"),
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "TMPDIR": str(tmp),
            "SESSIONMAIL_ROOT": str(self.agents.parent),
            "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
            "SESSIONMAIL_SENDMAIL": "drop",
            "SESSIONMAIL_VMAILBOX_GEN": str(tmp / "no-generator-here"),
            "SESSIONMAIL_NOTMUCH": str(bin_dir / "notmuch"),
            "SESSIONMAIL_PYTHON": sys.executable,
            "CLAUDE_CODE_SESSION_ID": SESSION,
        }
        # The process the in-process verbs read: the same keys, and none of
        # notmuch's own overrides (a fresh user exports none).
        for key, value in self.env.items():
            monkeypatch.setenv(key, value)
        for key in ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE"):
            monkeypatch.delenv(key, raising=False)
        _index.clear_cache()
        self.profile = _index.profile_config(env=self.env)
        # "Hooks off", measured, not assumed: no pager settings file under
        # this HOME, so the hooks read OFF (a fresh install's default).
        assert _settings.read(self.env).hooks is False

    def setup(self) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        rc = _setup.main(["--box", BOX], self.env, out=out, err=err, tty=False)
        return rc, out.getvalue() + err.getvalue()

    def deliver(self, mid: str, subject: str) -> Path:
        # Standing order (sessionmail+super, Fri 2026-10-30): the carrier
        # is the drop under a temp root, asserted, before any delivery.
        assert self.env["SESSIONMAIL_SENDMAIL"] == "drop"
        assert os.environ["SESSIONMAIL_SENDMAIL"] == "drop"
        assert self.agents.is_relative_to(self.tmp)
        p = self.agents / BOX / "new" / f"{hashlib.sha256(mid.encode()).hexdigest()[:10]}.M1P1.{HOST}"
        p.write_text(letter(mid, subject))
        return p

    def notmuch(self, *args: str) -> str:
        """notmuch on OUR profile, named with --config and never inherited."""
        done = subprocess.run([NOTMUCH, f"--config={self.profile}", *args],
                              capture_output=True, text=True, timeout=60, check=True,
                              env={"HOME": str(self.home), "PATH": self.env["PATH"]})
        return done.stdout.strip()

    def wake(self) -> str:
        """`memory wake BOX`, the user's verb, in-process: it hands the oracle
        the resolved profile (`_mhs`), which the bare script never finds.
        A RECENT that could not look is a broken rig, never the bug."""
        r = CliRunner().invoke(memory_cli, ["wake", BOX], prog_name="memory")
        assert r.exit_code == 0, r.output + r.stderr
        assert "RECENT degraded" not in r.output, r.output + r.stderr
        return r.output


def section(out: str, heading: str) -> str:
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"═══ {heading}"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("— "))
    return "\n".join(lines[start:end + 1])


@pytest.fixture
def host(tmp_path, monkeypatch, stranger):
    """Setup has run on a fresh host (no host settings file, so hooks are
    off, the default), and then ONE letter was delivered to BOX."""
    settings_home = _operator.settings_home()
    write_mail_settings(settings_home, None)
    assert settings_home.is_relative_to(Path(os.environ["SESSIONMAIL_HOME"]).parent)
    before = tree(settings_home)
    h = Host(tmp_path, monkeypatch)
    rc, out = h.setup()
    assert rc in (0, 4), out                  # 4: the maps step is left (no generator)
    assert (h.mailroot / ".notmuch").is_dir(), out
    assert h.notmuch("count", "*") == "0", "setup's index starts empty"
    assert tree(settings_home) == before, "setup wrote the settings home"
    h.deliver(AFTER, SUBJECT)
    h.notmuch_before = h.notmuch("count", f"id:{AFTER}")
    yield h
    _index.clear_cache()


def test_the_window_is_real(host):
    """The precondition, measured: the letter rests in new/ and the index
    has not reached it. Nothing after setup ran `notmuch new`."""
    assert host.notmuch_before == "0"
    assert list((host.agents / BOX / "new").iterdir())
    out = host.wake()
    assert SUBJECT in section(out, "ADDRESSED"), out    # the maildir walk sees it


def test_wake_recent_shows_mail_delivered_after_setup(host):
    """The acceptance: hooks off, no hand `notmuch new`, and RECENT shows the
    letter. This test's only notmuch calls are `count`, so the reader's own
    refresh is what moved the index from 0 to 1."""
    assert host.notmuch("count", f"id:{AFTER}") == "0"
    recent = section(host.wake(), "RECENT")
    assert SUBJECT in recent, recent
    assert host.notmuch("count", f"id:{AFTER}") == "1"


def test_mark_finds_mail_delivered_after_setup(host):
    r = CliRunner().invoke(message_cli, ["mark", BOX, f"<{AFTER}>", "seen"],
                           prog_name="message")
    assert r.exit_code == 0, r.output + r.stderr
    marks = _leaves.session_mail(SESSION, host.home) / "marks"
    assert f"{AFTER} seen" in marks.read_text()


def test_memory_ls_finds_a_root_delivered_after_setup(host):
    host.deliver(ROOT_MID, "[ROOT]")
    r = CliRunner().invoke(memory_cli, ["ls", "--box", BOX], prog_name="memory")
    assert r.exit_code == 0, r.output + r.stderr


def test_refresh_off_keeps_the_window(host, monkeypatch):
    """`notmuch.refresh: 0` turns the refresh off: RECENT is empty again and
    the index still holds nothing. So the wake indexes nothing of its own;
    the refresh is the whole fix."""
    monkeypatch.setenv(_fresh.VAR, "0")
    recent = section(host.wake(), "RECENT")
    assert SUBJECT not in recent, recent
    assert "— 0 threads shown of 0" in recent, recent
    assert host.notmuch("count", f"id:{AFTER}") == "0"


def test_a_hand_notmuch_new_is_the_whole_difference(host):
    """Control: one `notmuch --config=<profile> new` and every reader sees
    the letter. The readers are right; only the refresh is missing."""
    host.deliver(ROOT_MID, "[ROOT]")
    host.notmuch("new", "--quiet")
    assert host.notmuch("count", f"id:{AFTER}") == "1"
    assert SUBJECT in section(host.wake(), "RECENT")
    r = CliRunner().invoke(message_cli, ["mark", BOX, f"<{AFTER}>", "seen"],
                           prog_name="message")
    assert r.exit_code == 0, r.output + r.stderr
    marks = _leaves.session_mail(SESSION, host.home) / "marks"
    assert f"{AFTER} seen" in marks.read_text()
    r = CliRunner().invoke(memory_cli, ["ls", "--box", BOX], prog_name="memory")
    assert r.exit_code == 0, r.output + r.stderr
