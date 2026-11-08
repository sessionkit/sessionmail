"""The ONE state resolver, `_settings.state_path` (and its
account-rooted twin, `_settings.account_state_path`), and the leaves that
follow it in this slice: `rules.locked` and `seats`.

Each leaf resolves on its own, in sessiontools' `leaf_path` order:
`$SESSIONMAIL_HOME/<leaf>` when that is
set; else the old `~/.sessionmail/<leaf>` while it is a real one that is not
the new one; else `~/.sessionkit/mail/state/<leaf>` when it is there; else the
new one, the fresh default (`fresh-new`: a fresh install writes under
`~/.sessionkit/mail/`, SM-969 do-008). Both real and distinct is STRANDED: old is used,
and `state_stranded` names new. The resolver never writes. Every reader AND
writer of a leaf goes through it, so writer path == reader path, case by
case. Seat keys follow the same rule under ENV's HOME
(`_settings.seat_keys_leaf`), pinned below with the wrapper and the installer.

Every case runs in a tmp HOME: no test here reads or writes the real
`~/.sessionmail` or `~/.sessionkit`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import SC6_OPERATOR, write_operator

from sessionmail import _launchd, _operator, _seat, _settings, _subscribe, pager, rules

#: The real `rules.locked_path`, taken at import, before the autouse conftest
#: points it into the sandbox: the writer == reader pins need the production
#: chain (`locked_path` -> `default_locked_path` -> `account_state_path`).
REAL_LOCKED_PATH = rules.locked_path

SID = "5e55-0000-4000-8000-000000000969"

#: both: old real and new real, new holding something (OLD, and new is
#: STRANDED); linked: old is a symlink to new (NEW: the move's hand-over);
#: new: old absent, new there (NEW); old: only old (OLD); neither: no leaf
#: at all (NEW, the fresh default, `fresh-new`); override: both, with $SESSIONMAIL_HOME
#: set (it wins over both); parent: the state folder exists for ANOTHER
#: leaf, this leaf is only in the old tree (OLD: a folder made for one leaf
#: never moves another).
CASES = ("both", "linked", "new", "old", "neither", "override", "parent")
#: The account-rooted leaves have no override: no variable moves them.
ACCOUNT_CASES = ("both", "linked", "new", "old", "neither", "parent")
#: The cases that resolve to NEW (the override aside): "neither" is the
#: fresh default, NEW since SM-969 do-008.
TO_NEW = ("linked", "new", "neither")


@dataclass
class Host:
    home: Path          # $HOME, in the process and in any env a test passes
    override: Path      # $SESSIONMAIL_HOME, in the "override" case only

    @property
    def new_root(self) -> Path:
        return self.home / ".sessionkit" / "mail" / "state"

    @property
    def old_root(self) -> Path:
        return self.home / ".sessionmail"

    def new(self, leaf: str) -> Path:
        return self.new_root / leaf

    def old(self, leaf: str) -> Path:
        return self.old_root / leaf

    def expected(self, case: str, leaf: str) -> Path:
        if case == "override":
            return self.override / leaf
        return self.new(leaf) if case in TO_NEW else self.old(leaf)

    def stranded(self, case: str, leaf: str) -> Path | None:
        return self.new(leaf) if case == "both" else None


def lay(root_new: Path, root_old: Path, case: str, leaf: str, *, file: bool | None = None) -> None:
    """Make CASE's tree for LEAF: a folder holding one file (or, for a FILE
    leaf, a file)."""
    file = leaf in _settings.FILE_LEAVES if file is None else file

    def make(p: Path) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        if file:
            p.write_text("")
        else:
            p.mkdir(exist_ok=True)
            (p / "held").write_text("")
    if case in ("both", "linked", "new", "override"):
        make(root_new / leaf)
    if case in ("both", "old", "override", "parent"):
        make(root_old / leaf)
    if case == "linked":
        root_old.mkdir(parents=True, exist_ok=True)
        (root_old / leaf).symlink_to(root_new / leaf)
    if case == "parent":
        root_new.mkdir(parents=True, exist_ok=True)
        (root_new / "some-other-leaf").mkdir(exist_ok=True)


@pytest.fixture
def host(tmp_path, monkeypatch) -> Host:
    """A tmp HOME with no $SESSIONMAIL_HOME (the conftest's is dropped): the
    production default, so the old root is `$HOME/.sessionmail`."""
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SESSIONMAIL_HOME", raising=False)
    return Host(home=home, override=tmp_path / "override")


def at(host: Host, case: str, monkeypatch) -> None:
    if case == "override":
        monkeypatch.setenv("SESSIONMAIL_HOME", str(host.override))


@pytest.fixture
def account(tmp_path, monkeypatch) -> Path:
    """The ACCOUNT's home (`_operator.settings_home`, the conftest's seam),
    in tmp, holding the SC6 operator block so seat ivan is privileged."""
    home = tmp_path / "account"
    write_operator(home, SC6_OPERATOR)
    monkeypatch.setattr(_operator, "settings_home", lambda: home)
    return home


def snapshot(*roots: Path) -> dict[str, tuple]:
    """Every path under ROOTS with its kind, size and mtime: what a write,
    a mkdir or a touch would change."""
    seen: dict[str, tuple] = {}
    for root in roots:
        if not os.path.lexists(root):
            seen[str(root)] = ("absent",)
            continue
        for p in [root, *root.rglob("*")]:
            st = os.lstat(p)
            seen[str(p)] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return seen


# ── the resolver ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("leaf", ["seats", "sessions", "puller", "imap", "rules.locked"])
def test_state_path_resolves_each_leaf_in_every_case(host, monkeypatch, case, leaf):
    lay(host.new_root, host.old_root, case, leaf)
    at(host, case, monkeypatch)
    assert _settings.state_path(leaf) == host.expected(case, leaf)
    # The same answer from an env handed in, as the hooks and tests pass one.
    assert _settings.state_path(leaf, dict(os.environ)) == host.expected(case, leaf)
    assert _settings.state_stranded(leaf) == host.stranded(case, leaf)
    want = {"override": "override", "linked": "new", "new": "new", "neither": "fresh-new"}
    assert _settings.state_leaf(leaf)[1] == want.get(case, "old")


@pytest.mark.parametrize("leaf", ["seats", "rules.locked"])
def test_stranded_is_a_report_that_reads_nothing_in_new(host, leaf):
    """Old real, new real: old is used, and new is NAMED, never read: a
    new file that cannot be read changes nothing."""
    lay(host.new_root, host.old_root, "both", leaf)
    target = host.new(leaf) if leaf in _settings.FILE_LEAVES else host.new(leaf) / "held"
    os.chmod(target, 0)
    try:
        assert _settings.state_path(leaf) == host.old(leaf)
        assert _settings.state_stranded(leaf) == host.new(leaf)
    finally:
        os.chmod(target, 0o600)


def test_an_empty_new_folder_beside_a_real_old_one_is_not_stranded(host):
    """As tools' `leaf_stranded`: nothing of its own in new, nothing to settle."""
    host.old("seats").mkdir(parents=True)
    host.new("seats").mkdir(parents=True)
    assert _settings.state_path("seats") == host.old("seats")
    assert _settings.state_stranded("seats") is None


def test_the_wrong_kind_at_a_leaf_is_not_the_leaf(host):
    """A FILE leaf asks for a file and a folder leaf for a folder: a folder
    named `rules.locked`, or a file named `seats`, reads as absent."""
    (host.old_root / "rules.locked").mkdir(parents=True)
    host.new_root.mkdir(parents=True)
    (host.new_root / "rules.locked").write_text("")
    assert _settings.state_path("rules.locked") == host.new("rules.locked")
    (host.old_root / "seats").write_text("")
    assert _settings.state_leaf("seats") == (host.new("seats"), "fresh-new")


def test_an_empty_sessionmail_home_is_unset_as_seat_home_reads_it(host, monkeypatch):
    lay(host.new_root, host.old_root, "new", "seats")
    monkeypatch.setenv("SESSIONMAIL_HOME", "")
    assert _settings.state_path("seats") == host.new("seats")


def test_the_override_is_expanded_as_seat_home_expands_it(host, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_HOME", "~/elsewhere")
    assert _settings.state_path("seats") == host.home / "elsewhere" / "seats"
    assert _settings.state_path("seats") == _seat.home() / "seats"


def test_the_old_branch_is_the_writers_path_when_env_home_is_not_the_process_home(tmp_path, monkeypatch):
    """The old branch is `_seat.home(env)/leaf`, which expands `~` with
    the PROCESS home, so a reader finds what an unchanged writer wrote. Step
    2 looks under the env's HOME."""
    proc, given = tmp_path / "process-home", tmp_path / "env-home"
    proc.mkdir()
    monkeypatch.setenv("HOME", str(proc))
    monkeypatch.delenv("SESSIONMAIL_HOME", raising=False)
    env = {"HOME": str(given)}
    assert _settings.old_state_dir(env) == _seat.home(env) == proc / ".sessionmail"
    # Neither leaf: the fresh default is NEW, under the ENV's HOME.
    assert _settings.state_leaf("seats", env) == (
        given / ".sessionkit" / "mail" / "state" / "seats", "fresh-new")
    # A real old leaf under the PROCESS home wins (rule 2).
    (proc / ".sessionmail" / "seats").mkdir(parents=True)
    assert _settings.state_path("seats", env) == proc / ".sessionmail" / "seats"
    assert _settings.state_path("seats", env) == _seat.home(env) / "seats"
    (proc / ".sessionmail" / "seats").rmdir()
    (given / ".sessionkit" / "mail" / "state" / "seats").mkdir(parents=True)
    assert _settings.state_path("seats", env) == given / ".sessionkit" / "mail" / "state" / "seats"


def test_a_dangling_link_reads_as_absent(host):
    """As tools' `leaf_path`: a path that cannot be stat'd is not there. A
    dangling new leaf leaves old in use (or, with no old, the fresh default,
    new); a dangling old leaf hands over to a real new one."""
    host.new_root.mkdir(parents=True)
    host.new("seats").symlink_to(host.home / "gone")
    assert _settings.state_leaf("seats") == (host.new("seats"), "fresh-new")
    host.old("seats").mkdir(parents=True)
    assert _settings.state_leaf("seats") == (host.old("seats"), "old")
    assert _settings.state_stranded("seats") is None
    host.new("seats").unlink()
    host.new("seats").mkdir()
    host.old("seats").rmdir()
    host.old("seats").symlink_to(host.home / "gone")
    assert _settings.state_leaf("seats") == (host.new("seats"), "new")


@pytest.mark.parametrize("bad", ["", ".", "..", "seats/ivan", "seats/ivan/rules", "/abs", "a\\b", "a\0b"])
def test_a_leaf_is_one_top_level_name(bad):
    """A leaf is never deeper than the root's first level."""
    with pytest.raises(ValueError):
        _settings.state_path(bad)
    with pytest.raises(ValueError):
        _settings.account_state_path(bad)


def test_the_constants_name_each_root_once():
    assert _settings.STATE_DIR == Path(".sessionkit/mail/state")
    assert _settings.SHOWN_STATE_DIR == "~/.sessionkit/mail/state/"
    assert _settings.OLD_STATE_DIR == Path(".sessionmail")
    assert _seat.DEFAULT_HOME == "~/.sessionmail"           # value unchanged
    assert _settings.state_dir({"HOME": "/h"}) == Path("/h/.sessionkit/mail/state")


@pytest.mark.parametrize("case", ACCOUNT_CASES)
def test_account_state_path_ignores_home_and_sessionmail_home(host, account, monkeypatch, case):
    """D3 10.28: the locked file's path is never taken from ENV. The env's
    trees hold the leaf in BOTH places and are never chosen."""
    for root in (host.home, host.override):
        lay(root / ".sessionkit/mail/state", root / ".sessionmail", "new", "rules.locked")
    lay(account / ".sessionkit/mail/state", account / ".sessionmail", case, "rules.locked")
    monkeypatch.setenv("SESSIONMAIL_HOME", str(host.override))
    new, old = account / ".sessionkit/mail/state/rules.locked", account / ".sessionmail/rules.locked"
    want = new if case in TO_NEW else old
    assert _settings.account_state_path("rules.locked") == want
    assert rules.default_locked_path() == want
    assert _settings.account_state_stranded("rules.locked") == (new if case == "both" else None)
    monkeypatch.delenv("SESSIONMAIL_HOME")
    assert rules.default_locked_path() == want


@pytest.mark.parametrize("case", CASES)
def test_the_resolver_writes_nothing(host, account, monkeypatch, case):
    """No case creates, moves or touches a file: a snapshot of every root
    before and after every resolver and reader call in this slice."""
    for leaf in ("seats", "sessions", "puller", "imap"):
        lay(host.new_root, host.old_root, case, leaf)
        if case == "override":
            (host.override / leaf).mkdir(parents=True)
    lay(account / ".sessionkit/mail/state", account / ".sessionmail", case, "rules.locked")
    roots = (host.home, host.override, account)
    before = snapshot(*roots)
    at(host, case, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "ivan")
    for leaf in ("seats", "sessions", "puller", "imap", "rules.locked"):
        _settings.state_path(leaf)
        _settings.state_stranded(leaf)
        _settings.account_state_path(leaf)
        _settings.account_state_stranded(leaf)
    seat = _seat.resolve()
    _seat.seats_root()
    locked = rules.default_locked_path()
    rules.load_layers(seat, SID, locked=locked)
    rules.load_layers("ivan", SID, locked=locked)
    rules.layer_files(seat, SID, locked=locked)
    rules.layer_files("ivan", SID, locked=locked)
    assert snapshot(*roots) == before


def test_with_nothing_laid_no_state_folder_appears(host, account, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "ivan")
    for leaf in ("seats", "sessions", "puller", "imap", "rules.locked"):
        assert _settings.state_path(leaf) == host.new(leaf)
        assert _settings.state_leaf(leaf)[1] == _settings.FRESH_NEW
        assert _settings.account_state_path(leaf) == account / ".sessionkit/mail/state" / leaf
    rules.load_layers(_seat.resolve(), SID, locked=rules.default_locked_path())
    assert not (host.home / ".sessionkit").exists()
    assert not (host.home / ".sessionmail").exists()
    assert not (account / ".sessionkit" / "mail").exists()
    assert not (account / ".sessionmail").exists()


# ── the `seats` leaf: Seat.root and every seat-folder reader and writer ─────

@pytest.mark.parametrize("case", CASES)
def test_seat_root_follows_the_seats_leaf(host, monkeypatch, case):
    lay(host.new_root, host.old_root, case, "seats")
    at(host, case, monkeypatch)
    env = {"CLAUDE_CODE_AGENT": "ivan", "HOME": str(host.home)}
    if case == "override":
        env["SESSIONMAIL_HOME"] = str(host.override)
    seat = _seat.resolve(env)
    assert seat.root == host.expected(case, "seats") / "ivan"
    assert seat.subscriptions == host.expected(case, "seats") / "ivan" / "subscriptions"
    assert _seat.seats_root(env) == host.expected(case, "seats")


@pytest.mark.parametrize("case", CASES)
def test_writer_path_is_reader_path_for_every_rules_layer(host, account, monkeypatch, case):
    """Writer path == reader path: `pager rule` (session,
    `--seat`, `--locked`) and `pager focus` (session, `--fleet`) write the
    very files `rules.layer_files` watches and `rules.load_layers` reads, in
    each case. No split, so no refusal code."""
    monkeypatch.setattr(rules, "locked_path", REAL_LOCKED_PATH)
    lay(host.new_root, host.old_root, case, "seats")
    lay(account / ".sessionkit/mail/state", account / ".sessionmail", case, "rules.locked")
    at(host, case, monkeypatch)
    for var in (*_seat.ADDRESS_VARS, *_seat.SEAT_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "ivan")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    who = _subscribe.caller()
    assert who.privileged, "the SC6 operator block makes ivan privileged"
    locked, s_focus, seat_focus, s_rules, seat_rules = rules.layer_files(who.seat, who.session)

    # The account leaf has no override: "override" lays both, so OLD.
    want_locked = (account / ".sessionkit/mail/state/rules.locked" if case in TO_NEW
                   else account / ".sessionmail/rules.locked")
    assert locked == want_locked
    assert seat_rules == host.expected(case, "seats") / "ivan" / rules.RULES_FILE
    assert seat_focus == host.expected(case, "seats") / "ivan" / rules.FOCUS_FILE

    assert pager._rule_layer(who, False, True) == (rules.LOCKED, locked)
    assert pager._rule_layer(who, True, False) == (rules.SEAT, seat_rules)
    assert pager._rule_layer(who, False, False) == (rules.SESSION, s_rules)
    assert pager._focus_target(who, None, True)[1] == locked
    assert pager._focus_target(who, None, False)[1] == s_focus
    # The str-seat branch (`_seat_rules` fed by `_seat.seats_root`) agrees.
    assert rules.layer_files("ivan", SID)[2:] == (seat_focus, s_rules, seat_rules)

    # And what is written there is what is read.
    seat_rules.parent.mkdir(parents=True, exist_ok=True)
    seat_rules.write_text("wake from:eve@*\n")
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text("ride from:judy@*\n")
    got = [layer.name for layer in rules.load_layers(who.seat, SID)]
    assert "seat" in got and "locked" in got
    assert [layer.name for layer in rules.load_layers("ivan", SID)] == got


@pytest.mark.parametrize("case", CASES)
def test_an_explicit_home_is_honoured_as_is(host, tmp_path, monkeypatch, case):
    """`home=` (tests, `scan(home)`) wins over the resolver, exactly as
    `$SESSIONMAIL_HOME` does: `<home>/seats`, whatever the state folder holds."""
    lay(host.new_root, host.old_root, case, "seats")
    at(host, case, monkeypatch)
    given = tmp_path / "given"
    files = rules.layer_files("ivan", None, home=given, locked=tmp_path / "l")
    assert files[1:] == (given / "seats" / "ivan" / rules.FOCUS_FILE,
                         given / "seats" / "ivan" / rules.RULES_FILE)


@pytest.mark.parametrize("case", CASES)
def test_a_seat_subscription_write_goes_where_the_seat_folder_is_read(host, account, monkeypatch, case):
    """`_subscribe.target`'s agent-name branch writes the
    folder `Seat.subscriptions` (and the scan) read; `_seat_owned` probes the
    same leaf."""
    lay(host.new_root, host.old_root, case, "seats")
    at(host, case, monkeypatch)
    for var in (*_seat.ADDRESS_VARS, *_seat.SEAT_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "ivan")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    who = _subscribe.caller()
    got = _subscribe.target(who, seat=True, for_=None)
    assert (got.kind, got.owner, got.map) == ("seat", "ivan", False)
    assert got.folder == who.seat.subscriptions == host.expected(case, "seats") / "ivan" / "subscriptions"

    box = "sessionmail+web-12.foreman"
    (host.expected(case, "seats") / box).mkdir(parents=True)
    assert _subscribe._seat_owned(box, host.home / "not-a-box", None) == box


# ── seat keys: an old real folder wins, a fresh host mints new ─────────────
#
# `_settings.seat_keys_leaf` is the state rule for the `seat-keys` folder,
# both roots under ENV's HOME (the wrapper reads it under launchd, as the
# job). The wrapper (`seat/seat-agent --keys`), the LaunchAgent installer's
# plan (`_launchd.plan`) and `message seat init` find the one folder it names.

#: case -> (the root the keys are in, the source).
SEAT_KEY_CASES = {
    "old": ("old", "old"),            # an existing host: its keys never move
    "neither": ("new", "fresh-new"),  # a fresh host mints under the state folder
    "new": ("new", "new"),
    "linked": ("new", "new"),         # old is a link to new: the hand-over
    "both": ("old", "old"),           # both real: old, nothing merged
    "override": ("override", "override"),
}

WRAPPER = Path(_launchd.__file__).parent / "seat" / "seat-agent"


def lay_keys(host: Host, case: str) -> None:
    old, new = host.old("seat-keys"), host.new("seat-keys")
    if case in ("old", "both", "override"):
        (old / "desk.uma").mkdir(parents=True)
    if case in ("new", "both", "linked", "override"):
        (new / "desk.uma").mkdir(parents=True)
    if case == "linked":
        old.parent.mkdir(parents=True)
        old.symlink_to(new)


def keys_env(host: Host, case: str) -> dict[str, str]:
    env = {"HOME": str(host.home), "PATH": "/usr/bin:/bin"}
    if case == "override":
        env["SESSIONMAIL_HOME"] = str(host.override)
    return env


@pytest.mark.parametrize("case", sorted(SEAT_KEY_CASES))
def test_seat_keys_old_real_wins_fresh_goes_new_override_wins(host, case):
    lay_keys(host, case)
    env = keys_env(host, case)
    root, source = SEAT_KEY_CASES[case]
    want = {"old": host.old("seat-keys"), "new": host.new("seat-keys"),
            "override": host.override / "seat-keys"}[root]
    before = snapshot(host.home, host.override)
    assert _settings.seat_keys_leaf(env) == (want, source)
    assert _settings.seat_keys_dir(env) == want
    # The wrapper, in bash, finds the same folder.
    done = subprocess.run(["/bin/bash", str(WRAPPER), "--keys"], env=env,
                          capture_output=True, text=True, check=False)
    assert (done.returncode, done.stdout, done.stderr) == (0, str(want), ""), done
    # The installer's plan looks for the key home there.
    assert _launchd._key_root(env, str(host.home)) == str(want)
    if sys.platform == "darwin":
        with pytest.raises(_launchd.Refused) as got:
            _launchd.plan("desk.uma", None, env)
        assert str(want / "desk.uma") in str(got.value)
    # Looking makes nothing.
    assert snapshot(host.home, host.override) == before


def test_the_plist_pins_sessionmail_home_only_when_the_installer_set_it(host):
    """Unset, the job carries no SESSIONMAIL_HOME: the wrapper resolves the
    keys under the job's HOME by the same rule (an old real folder keeps
    working, a fresh host finds the new one). Set, it is pinned as given."""
    template = Path(_launchd.TEMPLATE).read_bytes()
    base = dict(address="desk.uma", label=_launchd.label_for("desk.uma"),
                plist="/x/p.plist", socket="/x/a.sock",
                pub=str(host.new("seat-keys") / "desk.uma" / "seat.pub"),
                key="/x/id_ed25519", log="/x/l/seat-agent.desk.uma.log", log_dir="/x/l",
                old_log="/x/o/seat-agent.desk.uma.log", message="/x/bin/message",
                domain="gui/501")
    import plistlib
    unset = plistlib.loads(_launchd.render(template, _launchd.Job(**base, sessionmail_home=None)))
    assert "SESSIONMAIL_HOME" not in unset["EnvironmentVariables"]
    assert unset["EnvironmentVariables"] == {"PATH": "/usr/bin:/bin"}
    pinned = plistlib.loads(_launchd.render(template, _launchd.Job(**base, sessionmail_home="/x/h")))
    assert pinned["EnvironmentVariables"]["SESSIONMAIL_HOME"] == "/x/h"
    assert _launchd._sessionmail_home({"HOME": str(host.home)}, str(host.home)) is None
    assert _launchd._sessionmail_home({"SESSIONMAIL_HOME": "~/h"}, str(host.home)) == \
        str(host.home / "h")
    with pytest.raises(_launchd.Refused, match="SESSIONMAIL_HOME"):
        _launchd.render(template.replace(b"<key>SESSIONMAIL_HOME</key>", b"<key>X</key>"),
                        _launchd.Job(**base, sessionmail_home=None))
