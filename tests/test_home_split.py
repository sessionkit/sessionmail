"""A split home: $HOME is not the account's home, and that is never silent.

Two homes are read today. The hooks file and the mail root's default follow
$HOME; `mail.json`'s keys (root, sendmail, host, ...), the host pin, the
locked rules, the operator, the registry and the puller follow the account's
home (the password database's, `_operator.settings_home`). A process whose
$HOME is a scratch folder therefore read the LIVE account's root and MTA,
and wrote there. The guard:

1. ONE definition, `_operator.home_split(env)`.
2. Say it: each writing verb prints one line per process; `message setup`
   shows a `home  SPLIT` plan row.
3. Refuse the live tree: a write into the account's mail tree, or a send
   through the account's filed MTA, is refused unless a SET variable
   (`SESSIONMAIL_ROOT`, `SESSIONMAIL_SENDMAIL`) names the target.
4. Account state does not move: the puller's state dir and lock stay on the
   account's home.

The conftest's sandbox answers "not split" (its two homes are a property of
the sandbox). Every test here restores the real `home_split` and builds its
own split: an empty `home` and a fake LIVE `account` with a filed root, a
filed MTA that leaves a marker when it runs, a host pin and boxes. The
`live` fixture snapshots the account before each test and checks it after.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest
from fakes import write_fake

from sessionmail import _birth, _operator, _puller, _seat, _settings, _setup

#: The real definition, taken before the conftest's sandbox patches it.
REAL_HOME_SPLIT = _operator.home_split

HOST = "testhost"
BOXES = ("alpha", "beta")

FAKE_MTA = """#!{python}
import sys
open({marker!r}, "a").write("ran\\n")
sys.stdin.buffer.read()
"""

FAKE_GEN = """#!{python}
import os, sys
args = sys.argv[1:]
if "--print" in args:
    sys.stdout.write(os.environ.get("FAKE_VMAILBOX", ""))
elif "--print-valias" in args:
    sys.stdout.write(os.environ.get("FAKE_VALIAS", ""))
"""

FAKE_NOTMUCH = """#!{python}
import sys
sys.exit(1)
"""


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    """Every path under ROOT: mode, size and mtime (lstat: a link is itself)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return out


class World:
    """The split: TMP/home ($HOME) and TMP/account (the account's home)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.account = tmp / "account"
        self.marker = tmp / "mta-ran"
        self.mta = tmp / "mta"
        self.bin = tmp / "bin"

    @property
    def account_file(self) -> Path:
        return self.account / _settings.RELATIVE

    def file(self, **values) -> None:
        """The account's `mail.json`, as VALUES."""
        self.account_file.write_text(json.dumps(values) + "\n")

    def env(self, **extra: str) -> dict[str, str]:
        vmailbox = "".join(f"{n}@{HOST}\tstores/{n}/\n" for n in BOXES)
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "TMPDIR": str(self.tmp),
            "SESSIONMAIL_HOST": HOST,
            "SESSIONMAIL_VMAILBOX_GEN": str(self.bin / "vmailbox-gen"),
            "SESSIONMAIL_NOTMUCH": str(self.bin / "notmuch"),
            "FAKE_VMAILBOX": vmailbox,
            "FAKE_VALIAS": "",
        }
        env.update(extra)
        return env

    def scratch_root(self) -> Path:
        """A mail root under $HOME with the boxes a send needs."""
        root = self.home / ".sessionkit" / "mail"
        for box in BOXES:
            for d in ("cur", "new", "tmp"):
                (root / "stores" / box / d).mkdir(parents=True, exist_ok=True)
        return root

    def outside_home(self) -> dict[str, tuple[int, int, int]]:
        """Every path under TMP that is not under $HOME."""
        return {k: v for k, v in snapshot(self.tmp).items()
                if not (k == "home" or k.startswith("home" + os.sep))}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A split home over a fake live account, checked unchanged after the test."""
    w = World(tmp_path)
    w.home.mkdir()
    w.bin.mkdir()
    write_fake(w.bin / "vmailbox-gen", FAKE_GEN.format(python=sys.executable))
    write_fake(w.bin / "notmuch", FAKE_NOTMUCH.format(python=sys.executable))
    write_fake(w.mta, FAKE_MTA.format(python=sys.executable, marker=str(w.marker)))
    w.account_file.parent.mkdir(parents=True)
    w.file(root="~/.mail", sendmail=str(w.mta), host="live")
    for box in BOXES:
        for d in ("cur", "new", "tmp"):
            (w.account / ".mail" / "agents" / box / d).mkdir(parents=True)
    monkeypatch.setattr(_operator, "settings_home", lambda: w.account)
    monkeypatch.setattr(_operator, "home_split", REAL_HOME_SPLIT)
    monkeypatch.setattr(_operator, "_SPLIT_SAID", False)
    for var in ("SESSIONMAIL_ROOT", "SESSIONMAIL_SENDMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(w.home))
    w.before = snapshot(w.account)
    yield w
    assert snapshot(w.account) == w.before, "the live account's tree changed"
    assert not w.marker.exists(), "the account's MTA ran"


def setup(env, *argv):
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(list(argv), env, out=out, err=err, tty=False)
    return rc, out.getvalue(), err.getvalue()


def row(out: str, step: str) -> tuple[str, str]:
    """STEP's (state, detail) on the plan's `steps:` lines."""
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split(None, 2)
        if line.startswith("  ") and parts and parts[0] == step:
            return parts[1], (parts[2] if len(parts) > 2 else "")
    return "", ""


def send(env, tmp: Path, to: str = "alpha"):
    from sessionmail import _sender
    body = tmp / "body.md"
    body.write_bytes(b"hello\n")
    return _sender.run([to, "--from", "beta", "--subject", "split test",
                        "--body-file", str(body)], env)


# ── 1. one definition ────────────────────────────────────────────────────────

def test_home_split_names_both_homes_only_when_they_differ(live):
    assert _operator.home_split(live.env()) == (live.home, live.account)
    assert _operator.home_split({"HOME": str(live.account)}) is None
    link = live.tmp / "via-link"
    link.symlink_to(live.account)
    assert _operator.home_split({"HOME": str(link)}) is None, "one folder by two names"
    assert _operator.home_split({}) is None, "no HOME set: nothing to compare"
    assert _operator.home_split({"HOME": str(live.tmp / "not-made")}) == (
        live.tmp / "not-made", live.account)


def test_the_notice_is_said_once_in_a_process(live):
    first = _operator.split_notice(live.env())
    assert first is not None and "home SPLIT" in first
    assert str(live.home) in first and str(live.account) in first
    assert _operator.split_notice(live.env()) is None


# ── 2 and 3. setup ───────────────────────────────────────────────────────────

def test_setup_dry_run_shows_the_split_row_before_anything_is_written(live):
    root = live.home / ".sessionkit" / "mail"
    rc, out, err = setup(live.env(SESSIONMAIL_ROOT=str(root), SESSIONMAIL_SENDMAIL="drop"),
                         "--dry-run")
    assert rc != _setup.EX_USAGE, out + err
    state, detail = row(out, "home")
    assert state == _setup.SPLIT, out
    assert str(live.home) in detail and str(live.account) in detail
    assert out.index("  home ") < out.index("  mail-root ")
    assert err.count("home SPLIT") == 1, err
    assert "home (SPLIT)" not in out, "a fact, never a step left for anyone"
    assert not root.exists(), "a dry run writes nothing"


def test_setup_on_the_live_accounts_mta_is_refused_and_writes_nothing(live):
    before = live.outside_home()
    rc, out, err = setup(live.env())
    assert rc == _setup.EX_USAGE, out + err
    assert "REFUSED" in err and "the account's MTA" in err and "SESSIONMAIL_SENDMAIL" in err
    assert err.count("home SPLIT") == 1, err
    assert live.outside_home() == before
    assert list(live.home.iterdir()) == []


def test_setup_into_the_live_accounts_root_is_refused_and_writes_nothing(live):
    live.file(root="~/.mail", sendmail="drop", host="live")
    live.before = snapshot(live.account)
    before = live.outside_home()
    rc, out, err = setup(live.env())
    assert rc == _setup.EX_USAGE, out + err
    assert "live mail tree" in err and "SESSIONMAIL_ROOT" in err
    assert str(live.account / ".mail") in err
    assert live.outside_home() == before
    assert list(live.home.iterdir()) == []


def test_setup_with_pinned_targets_writes_nothing_outside_home_and_says_split(live):
    root = live.home / ".sessionkit" / "mail"
    before = live.outside_home()
    rc, out, err = setup(live.env(SESSIONMAIL_ROOT=str(root), SESSIONMAIL_SENDMAIL="drop"),
                         "--box", "alpha")
    assert rc in (_setup.EX_OK, _setup.EX_REMAINS), out + err
    assert (root / "stores" / "alpha" / "new").is_dir(), out
    assert row(out, "home")[0] == _setup.SPLIT
    assert row(out, "host")[0] == _setup.DONE, "the account's pin is read, never written"
    assert err.count("home SPLIT") == 1, err
    assert live.outside_home() == before


def test_the_host_pin_is_never_written_into_the_accounts_file(live):
    live.file(root="~/.mail", sendmail="drop")          # no host: setup would pin one
    live.before = snapshot(live.account)
    root = live.home / ".sessionkit" / "mail"
    rc, out, err = setup(live.env(SESSIONMAIL_ROOT=str(root), SESSIONMAIL_SENDMAIL="drop"))
    state, detail = row(out, "host")
    assert state == _setup.BLOCKED, out
    assert "the host pin is not written" in detail and str(live.account) in detail
    assert rc == _setup.EX_REMAINS, out + err


def test_a_root_the_variable_names_in_the_live_tree_is_a_deliberate_pin(live):
    rc, out, err = setup(live.env(SESSIONMAIL_ROOT=str(live.account / ".mail"),
                                  SESSIONMAIL_SENDMAIL="drop"), "--dry-run")
    assert rc != _setup.EX_USAGE, out + err
    assert row(out, "home")[0] == _setup.SPLIT
    assert row(out, "mail-root")[0] == _setup.DONE, out
    assert err.count("home SPLIT") == 1, err


def test_no_split_no_row_and_no_line(live):
    rc, out, err = setup(live.env(HOME=str(live.account), SESSIONMAIL_ROOT=str(live.tmp / "r"),
                                  SESSIONMAIL_SENDMAIL="drop"), "--dry-run")
    assert rc != _setup.EX_USAGE, out + err
    assert row(out, "home") == ("", "")
    assert "SPLIT" not in out + err


# ── 2 and 3. a send ──────────────────────────────────────────────────────────

def test_a_send_through_the_accounts_filed_mta_is_refused(live):
    before = live.outside_home()
    rc, _out, err = send(live.env(), live.tmp)
    assert rc == 3, err
    assert "no MTA:" in err and "the account's MTA" in err and "NOTHING WAS SENT" in err
    assert err.count("home SPLIT") == 1, err
    assert {k: v for k, v in live.outside_home().items() if k != "body.md"} == before


def test_a_drop_send_into_the_accounts_root_is_refused(live):
    live.file(root="~/.mail", sendmail="drop", host="live")
    live.before = snapshot(live.account)
    rc, _out, err = send(live.env(), live.tmp)
    assert rc == 3, err
    assert "REFUSING TO SEND (mail drop)" in err and "live mail tree" in err
    assert "NOTHING WAS SENT" in err


def test_a_send_with_pinned_targets_lands_under_home_and_says_split_once(live):
    root = live.scratch_root()
    before = live.outside_home()
    env = live.env(SESSIONMAIL_ROOT=str(root), SESSIONMAIL_SENDMAIL="drop")
    rc, _out, err = send(env, live.tmp)
    assert rc == 0, err
    assert len(list((root / "stores" / "alpha" / "new").iterdir())) == 1
    assert err.count("home SPLIT") == 1, err
    rc, _out, err = send(env, live.tmp)
    assert rc == 0, err
    assert "home SPLIT" not in err, "once per process, never per send"
    assert {k: v for k, v in live.outside_home().items() if k != "body.md"} == before


def test_a_pinned_mta_is_a_deliberate_act(live):
    env = live.env(SESSIONMAIL_SENDMAIL=str(live.mta))
    carrier = _settings.carrier(env)
    assert carrier.kind == "mta" and carrier.path == str(live.mta)


# ── 3. a box birth, a regen, the hooks switch ─────────────────────────────────

def test_a_box_birth_into_the_accounts_root_is_refused(live, capsys):
    sid = "0000aaaa-1111-2222-3333-444455556666"
    got = _birth.ensure_session_box(sid, None, env=live.env())
    assert got.born is False or not got.born
    assert got.error and "live mail tree" in got.error and got.error.endswith("no box born")
    assert not (live.account / ".mail" / "agents" / sid).exists()
    assert capsys.readouterr().err.count("home SPLIT") == 1


def test_a_box_birth_under_a_pinned_root_is_born(live, capsys):
    sid = "0000aaaa-1111-2222-3333-444455556666"
    root = live.scratch_root()
    got = _birth.ensure_session_box(sid, None, env=live.env(SESSIONMAIL_ROOT=str(root),
                                                             SESSIONMAIL_SENDMAIL="drop"))
    assert not got.error, got.error
    assert (root / "stores" / sid / "new").is_dir()
    assert capsys.readouterr().err.count("home SPLIT") == 1


def test_a_regen_into_the_accounts_root_is_refused(live, capsys):
    env = live.env(SESSIONMAIL_SENDMAIL=str(live.mta))          # a deliberate MTA
    ok, line = _seat._run_generator(live.account / ".mail" / "agents", env, ())
    assert not ok
    assert line.startswith("generator not run:") and "live mail tree" in line
    assert capsys.readouterr().err.count("home SPLIT") == 1


def test_the_hooks_switch_says_split_and_writes_homes_file(live, capsys):
    result = _settings.set_hooks(True, live.env())
    assert not result.refused, result
    assert result.path == live.home / _settings.RELATIVE
    assert json.loads(result.path.read_text())["hooks"] is True
    assert capsys.readouterr().err.count("home SPLIT") == 1


def test_the_hooks_switch_never_writes_through_a_link_into_the_account(live):
    (live.home / ".sessionkit").mkdir()
    (live.home / ".sessionkit" / "mail").symlink_to(live.account / ".sessionkit" / "mail")
    result = _settings.set_hooks(True, live.env())
    assert result.refused == _settings.REFUSED_SPLIT, result
    assert "live mail tree" in result.why


# ── 4. account state does not move with HOME ──────────────────────────────────

def test_the_puller_state_and_lock_stay_on_the_accounts_home(live):
    assert _puller._state_dir("") == _settings.account_state_path(_puller.STATE_LEAF)
    assert live.account in _puller._state_dir("").parents
    filed = _puller._state_dir("~/pull-state")
    assert filed == live.account / "pull-state", "a filed ~ is the account's, never $HOME's"
    filed.mkdir()
    lock = _puller._take_lock(filed)
    try:
        assert lock is not None
        assert Path(lock.name) == live.account / "pull-state" / "puller.lock"
        assert not (live.home / "pull-state").exists()
    finally:
        lock.close()
    live.before = snapshot(live.account)      # this test made the state dir itself
