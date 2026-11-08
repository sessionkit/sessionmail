"""The two single-folder owners follow the ONE state resolver, each choosing
its folder ONCE, so its lock, its cursors and its password file are never
split across two folders.

- The puller (`python -m sessionmail._puller`): `pull.state_dir` unset is
  the `puller` leaf, rooted at the ACCOUNT's home
  (`_settings.account_state_path`): no variable moves it, so an agent seat
  with `$SESSIONMAIL_HOME` set takes the LaunchAgent's `puller.lock`. An
  explicit `pull.state_dir` or `SESSIONMAIL_PULL_STATE_DIR` still wins.
- pull-imap (`python -m sessionmail._pull_imap`): its home is the root the
  `imap` leaf resolves under (`_settings.state_path`, `$SESSIONMAIL_HOME`
  first), and its password file and cursors are both there.

Every case of test_state_resolver's runs (old and new both real, old linked
to new, new only, old only, neither, the override, a state folder made for
another leaf), in a tmp HOME and a tmp account home: no test here reads or
writes the real `~/.sessionmail` or `~/.sessionkit`. Neither owner ever
creates the state folder.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from sessionmail import _operator, _puller, _pull_imap, _settings
from test_pull_imap import FakeImap, deliver_when_idle, QUICK
from test_state_resolver import (ACCOUNT_CASES, CASES, TO_NEW, Host, at, host, lay,  # noqa: F401
                                 snapshot)

OWN_IP = "192.0.2.10"


# ── the puller: the account-rooted `puller` leaf ─────────────────────────────

@pytest.fixture
def account(tmp_path, monkeypatch) -> Path:
    """The ACCOUNT's home, the settings home (`_operator.settings_home`, the
    conftest's seam), in tmp and apart from $HOME."""
    home = tmp_path / "account"
    home.mkdir()
    monkeypatch.setattr(_operator, "settings_home", lambda: home)
    return home


def new_puller(account: Path) -> Path:
    return account / ".sessionkit" / "mail" / "state" / "puller"


def old_puller(account: Path) -> Path:
    return account / ".sessionmail" / "puller"


def lay_puller(account: Path, case: str) -> Path:
    """CASE's tree for the puller leaf under the account home; returns the
    folder the puller must use. "override" lays both: nothing overrides an
    account leaf, so it is OLD."""
    lay(account / ".sessionkit/mail/state", account / ".sessionmail", case, "puller")
    return new_puller(account) if case in TO_NEW else old_puller(account)


@pytest.fixture
def penv(tmp_path) -> dict[str, str]:
    """A full puller config by variables, `pull.state_dir` UNSET."""
    boxes = tmp_path / "pull.boxes"
    boxes.write_text("alpha\n")
    return {
        "HOME": str(tmp_path / "user"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SESSIONMAIL_NOTMUCH": os.environ["SESSIONMAIL_NOTMUCH"],
        "SESSIONMAIL_ROOT": str(tmp_path / "mail" / "agents"),
        "SESSIONMAIL_PULL_IMAP_HOST": "imap.example.org",
        "SESSIONMAIL_PULL_LOGIN": "{box}*master",
        "SESSIONMAIL_PULL_SECRET_COMMAND": "printf s3cret",
        "SESSIONMAIL_PULL_RELAY_DOMAIN": "relay.example",
        "SESSIONMAIL_PULL_LISTENER": "127.0.0.1:2525",
        "SESSIONMAIL_PULL_VMAILBOX": "hash:/nonexistent/vmailbox",
        "SESSIONMAIL_PULL_BOXES_FILE": str(boxes),
        "SESSIONMAIL_PULL_LOCAL_DOMAIN": "host-a",
        "SESSIONMAIL_PULL_OWN_IPS": OWN_IP,
    }


def state_dir(env: dict[str, str]) -> Path:
    got = _puller.config(env)
    assert isinstance(got, _puller.Config), got
    return got.state_dir


@pytest.mark.parametrize("case", CASES)
def test_the_puller_default_is_the_account_puller_leaf(account, penv, case):
    where = lay_puller(account, case)
    assert state_dir(penv) == _settings.account_state_path("puller") == where
    assert _settings.account_state_stranded("puller") == (
        new_puller(account) if case in ("both", "override") else None)


@pytest.mark.parametrize("case", CASES)
def test_no_variable_moves_the_puller_default(account, penv, tmp_path, case):
    """Neither $SESSIONMAIL_HOME nor a $HOME apart from the account's moves
    it, even when each holds a state folder of its own with a puller leaf."""
    where = lay_puller(account, case)
    seat, elsewhere = tmp_path / "seat-home", tmp_path / "elsewhere"
    for root in (seat, elsewhere / ".sessionkit/mail/state", elsewhere / ".sessionmail"):
        (root / "puller").mkdir(parents=True)
    moved = {**penv, "SESSIONMAIL_HOME": str(seat), "HOME": str(elsewhere)}
    assert state_dir(moved) == state_dir(penv) == where


def test_an_agent_seat_on_an_unmigrated_host_takes_todays_lock_folder(account, penv, tmp_path):
    """The super's R12 pin: SESSIONMAIL_HOME set (as an agent seat sets
    it), a real old puller folder and no state folder: EXACTLY the folder
    today's puller.lock is in, under the settings home."""
    lay_puller(account, "old")
    seat = {**penv, "SESSIONMAIL_HOME": str(tmp_path / "seat-home")}
    today = account / ".sessionmail" / "puller"
    assert state_dir(seat) == today == _puller._under_settings_home("~/.sessionmail/puller")


def test_a_fresh_host_takes_the_keys_default_the_state_folder(account, penv, tmp_path):
    """Neither folder (a fresh install): the key's default, the state
    folder's `puller/`, under the settings home, whatever $SESSIONMAIL_HOME
    says (SM-969 do-008, fresh-new)."""
    lay_puller(account, "neither")
    seat = {**penv, "SESSIONMAIL_HOME": str(tmp_path / "seat-home")}
    default = str(_settings.KEYS["pull.state_dir"].default)
    assert default == "~/.sessionkit/mail/state/puller"
    assert state_dir(seat) == state_dir(penv) == new_puller(account) \
        == _puller._under_settings_home(default)


@pytest.mark.parametrize("case", CASES)
def test_an_explicit_state_dir_wins_in_every_case(account, penv, tmp_path, case):
    lay_puller(account, case)
    assert state_dir({**penv, "SESSIONMAIL_PULL_STATE_DIR": str(tmp_path / "given")}) \
        == tmp_path / "given"
    # From the file too, `~` the settings home, as before.
    f = account / _settings.RELATIVE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"version": 1, "hooks": false, "pull": {"state_dir": "~/puller-state"}}\n')
    assert state_dir(penv) == account / "puller-state"
    # A setting that SPELLS the default is a setting: it wins as given, and
    # the leaf does not follow the state folder.
    f.write_text('{"version": 1, "hooks": false, "pull": {"state_dir": "~/.sessionmail/puller"}}\n')
    assert state_dir(penv) == old_puller(account)
    assert state_dir({**penv, "SESSIONMAIL_PULL_STATE_DIR": str(old_puller(account))}) \
        == old_puller(account)


@pytest.mark.parametrize("case", CASES)
def test_choosing_the_puller_folder_writes_nothing(account, penv, case):
    lay_puller(account, case)
    before = snapshot(account)
    state_dir(penv)
    assert snapshot(account) == before


@pytest.fixture
def ran(monkeypatch):
    """`_puller.main` up to its run: the Config it ran with, and whether the
    state dir's lock was held then (tried from a second open file)."""
    seen: dict[str, object] = {}

    def fake_run(cfg, args, boxes_file):
        seen["cfg"] = cfg
        with open(cfg.state_dir / "puller.lock") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["held"] = False
            except OSError:
                seen["held"] = True
        return 0
    monkeypatch.setattr(_puller, "_run", fake_run)
    return seen


@pytest.mark.parametrize("case", CASES)
def test_the_puller_locks_and_keeps_cursors_in_the_folder_it_resolved(account, penv, ran, case):
    """Writer == reader: the mkdir, the lock and every cursor are in the ONE
    folder the default resolved to, and nothing is made in the other root."""
    where = lay_puller(account, case)
    other = old_puller(account) if case in TO_NEW else new_puller(account)
    other_before = snapshot(other)
    assert _puller.main([], penv) == 0
    cfg = ran["cfg"]
    assert cfg.state_dir == where and ran["held"] is True
    assert (where / "puller.lock").read_text().strip() == str(os.getpid())
    assert _puller.cursor_path(cfg, "alpha").is_relative_to(where)
    if case != "linked":                             # linked: other IS where, by its link
        assert snapshot(other) == other_before


def test_a_fresh_puller_makes_the_new_folder_never_the_old(account, penv, ran):
    """No puller leaf anywhere and no state at all: the fresh default is
    NEW (SM-969 do-008), so main makes and locks the state folder's
    `puller/`, parents included, and never makes the old one."""
    lay_puller(account, "neither")
    assert _puller.main([], penv) == 0
    assert ran["cfg"].state_dir == new_puller(account) and new_puller(account).is_dir()
    assert ran["held"] is True
    assert not os.path.lexists(old_puller(account))
    assert not (account / ".sessionmail").exists()


def test_one_lock_across_a_move_that_leaves_a_symlink_at_the_old_leaf(account, penv, ran):
    """R9: a puller started before the move holds old/puller.lock; once old
    is a symlink to new, a puller started after resolves NEW and finds the
    SAME lock (one inode), so it refuses (exit 3) instead of pulling twice."""
    lay_puller(account, "linked")
    with open(old_puller(account) / "puller.lock", "a+") as before_the_move:
        fcntl.flock(before_the_move, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _puller.main([], penv) == _puller.EX_LOCKED
    assert "cfg" not in ran


# ── pull-imap: the `imap` leaf, $SESSIONMAIL_HOME first ──────────────────────

TARGET = _pull_imap.parse("imap://alpha@127.0.0.1:1143/INBOX")


@pytest.mark.parametrize("case", CASES)
def test_pull_imap_home_is_the_root_the_imap_leaf_resolves_under(host, monkeypatch, case):
    lay(host.new_root, host.old_root, case, "imap")
    at(host, case, monkeypatch)
    monkeypatch.delenv(_pull_imap.ENV_PASSWORD_FILE, raising=False)
    where = host.expected(case, "imap")
    home = _pull_imap.state_home()
    assert home / "imap" == _settings.state_path("imap") == where
    assert _pull_imap.state_home(dict(os.environ)) == home
    # The password file and the cursors: one folder.
    assert _pull_imap.password_path(TARGET, home).parent == where
    assert _pull_imap.cursor_path(TARGET, home).is_relative_to(where)


@pytest.mark.parametrize("case", CASES)
def test_choosing_the_imap_folder_writes_nothing(host, monkeypatch, case):
    lay(host.new_root, host.old_root, case, "imap")
    at(host, case, monkeypatch)
    roots = (host.home, host.override)
    before = snapshot(*roots)
    _pull_imap.state_home()
    assert snapshot(*roots) == before


@pytest.fixture
def server():
    s = FakeImap()
    yield s
    s.srv.close()


@pytest.mark.parametrize("case", CASES)
def test_pull_imap_reads_its_password_and_writes_its_cursor_in_one_folder(
        host, monkeypatch, server, tmp_path, case):
    """Writer == reader, end to end through `main`: the password is read
    from the resolved imap/ folder, and the cursor the arrival advances is
    written beside it; the other root gets nothing."""
    lay(host.new_root, host.old_root, case, "imap")
    at(host, case, monkeypatch)
    monkeypatch.delenv(_pull_imap.ENV_PASSWORD_FILE, raising=False)
    where = host.expected(case, "imap")
    pw = where / "alpha@127.0.0.1.pass"
    pw.parent.mkdir(parents=True, exist_ok=True)     # neither, override: the folder the user makes
    pw.write_text("secret\n")
    pw.chmod(0o600)
    other = {"both": host.new("imap"), "override": host.old("imap")}.get(case)
    other_before = snapshot(other) if other else None
    deliver_when_idle(server, "one@alpha")
    code = _pull_imap.main(["--into", str(tmp_path / "mirror"), "--once", "--timeout", "3",
                            "--poll", str(QUICK),
                            "--imap", f"imap://alpha@127.0.0.1:{server.port}/INBOX"])
    assert code == 0
    target = _pull_imap.parse(f"imap://alpha@127.0.0.1:{server.port}/INBOX")
    cursor = _pull_imap.cursor_path(target, where.parent)
    assert cursor.is_relative_to(where) and _pull_imap.read_cursor(cursor) is not None
    if other:
        assert snapshot(other) == other_before


def test_pull_imap_with_no_old_folder_is_the_state_folder(host, monkeypatch):
    """No `imap` leaf anywhere: the fresh default, NEW (SM-969 do-008).
    Choosing makes nothing."""
    lay(host.new_root, host.old_root, "neither", "imap")
    assert _pull_imap.state_home() == host.new_root
    assert not os.path.lexists(host.new("imap"))
    assert not os.path.lexists(host.old("imap"))
