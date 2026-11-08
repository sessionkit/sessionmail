"""SC1 of SM-969: a FRESH install writes nothing outside ~/.sessionkit/mail/
(the mail root since the move: boxes under its stores/, the dot-folders
beside them), never ~/.mail and never an agents/ folder under the root (the
two old names; either one made on a fresh host would flip the host's
answer back, so each is a red test here), except paths that are another
package's (each named here with
the code that writes it).

A fresh-VM walk of 0.3.2 saw `message mark` write
~/.sessionmail/seats/claude/marks: the state resolver's fresh default,
rule 4, was OLD. SM-969 do-008 made it NEW. This test is the receipt, and it
is HERMETIC (the project's standing order for every repro):

- HOME is a fresh, empty tmp folder, and so is the ACCOUNT's home
  (`_operator.settings_home`, the password database's, which a scratch HOME
  alone does not move): `message setup` and the account-rooted leaves
  (`rules.locked`, the puller) write there, never the runner's.
- `rules.locked_path` is the production chain again (the conftest points it
  into its sandbox), so the locked file resolves as a fresh host's would.
- No $SESSIONMAIL_HOME: a new user has none, and it is rule 1.
- The carrier is the file drop, ASSERTED before any send: nothing reaches an
  MTA. Where a recipient lands is asked of the PACKAGED map generator in its
  print modes, which touch no file (asserted to be the one found: no
  generator on PATH or under the mail root); postconf is the conftest's
  missing one, so no live postfix is read. notmuch is a fake. launchctl is
  the conftest's fake.
- Session ids are synthetic (`fresh_session`); the seat name is a fixture.
- Every verb runs IN-PROCESS (click's runner, `_setup.main`), so every seam
  above holds.

The verbs are the README's quick start and the hidden verbs a new user is
pointed at: `message setup --box`, `message send`, `message list`,
`message read`, `message mark`, `message tree`, `pager status`,
`pager rule add|list`, `pager focus sleep|off`, `memory wake`, `memory ls`,
each run once as a terminal seat (CLAUDE_CODE_AGENT, no session: the
the walk's case) and once as a Claude Code session (a session id). Left out,
with why: `pager listen` blocks until mail (its arm is the pager's own
suite's), and installing `message seat agent` as a LaunchAgent needs launchd (test_seat_install
and test_state_resolver pin where it looks for the keys). `message seat
init` runs, in-process, into a scratch registry OUTSIDE HOME, so the diff
shows where a fresh host's seat keys land.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import _leaves
from fakes import write_fake
from test_message_setup import FAKE_NOTMUCH
from test_seat_init import git_env, registry, tools  # noqa: F401

from sessionmail import _drop, _generator, _maildir, _operator, _settings, _setup, rules, seat_init
from sessionmail.memory import cli as memory_cli
from sessionmail.message import cli as message_cli
from sessionmail.pager import cli as pager_cli

#: The production `rules.locked_path`, taken before the conftest's autouse
#: patch: a fresh host's locked file resolves through the state resolver.
REAL_LOCKED_PATH = rules.locked_path

HOST = "testhost"
BOX = "alpha"
SEAT = "claude"          # the walk's seat name, a fixture here
KEY_SEAT = "desk.fresh"  # a seat minted with `message seat init`, a fixture

#: Where sessionmail's own state and the mail may land on a fresh host: the
#: one root. `~/.mail` and `<root>/agents` are the OLD names (FORBIDDEN): a
#: fresh host that births either flips its own root or folder back
#: (`_maildir.boxes_name` keeps `agents` while only it exists).
OURS = (Path(".sessionkit") / "mail",)
FORBIDDEN = (Path(".mail"), Path(".sessionkit") / "mail" / _maildir.OLD_BOXES)

#: Another package's, or the system's: each with what writes it. A new path
#: under one of these is listed, never failed.
OTHERS: dict[Path, str] = {}

#: sessiontools' sessions leaf joins OTHERS per HOME (`sessions_other`): its
#: place is the resolver's answer for that HOME, pinned before any verb runs
#: (`_leaves`), never spelled here.
SESSIONS_OWNER = ("sessiontools' session dir: the per-session mail state "
                  "(`_seat.session_root` -> sessiontools.settings.session_dir); "
                  "the ~/.session share, SM-969 do-005, scope out of do-008")

#: The bare folders above OURS and OTHERS: made by mkdir -p, holding nothing
#: of their own. The folders above the sessions leaf join them per HOME.
#: The notmuch profile is OURS (SM-1021): a fresh host's is
#: `~/.sessionkit/mail/settings/notmuch/<name>/config`, so nothing may land
#: under ~/.config or at ~/.notmuch-config.*.
ANCESTORS = {Path(".sessionkit")}


def sessions_other(home: Path) -> tuple[Path, set[Path]]:
    """sessiontools' sessions leaf under HOME, relative, and the folders on
    the way to it: the pinned answer (`_leaves`), checked again now."""
    leaf = _leaves.sessions_root(home).relative_to(home)
    return leaf, set(leaf.parents) - {Path(".")}


def tree(home: Path) -> set[Path]:
    """Every path under HOME, relative, links not followed."""
    return {p.relative_to(home) for p in home.rglob("*")}


def under(p: Path, top: Path) -> bool:
    return p == top or top in p.parents


@pytest.fixture
def fresh(tmp_path, monkeypatch, fresh_session):
    """A fresh user's HOME, which is also the account's home."""
    home = tmp_path / "fresh-home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    notmuch = write_fake(bin_dir / "notmuch", FAKE_NOTMUCH.format(python=sys.executable))

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(_operator, "settings_home", lambda: home)
    monkeypatch.setattr(rules, "locked_path", REAL_LOCKED_PATH)
    for var in ("SESSIONMAIL_HOME", "SESSION_MAIL_ROOT", "SESSIONMAIL_ROOT",
                "XDG_CONFIG_HOME", "NOTMUCH_CONFIG", "SESSIONS_DIR",
                _generator.VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    monkeypatch.setenv("SESSIONMAIL_NOTMUCH", str(notmuch))
    monkeypatch.setenv("FAKE_NOTMUCH_LOG", str(tmp_path / "notmuch.log"))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("SESSION_MAIL_HOST", HOST)
    # The carrier: the file drop, into this HOME's ~/.mail, and nothing else.
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "drop")
    _leaves.pin(home)            # the fresh HOME's leaves, before any verb runs
    return dict(home=home, sid=fresh_session)


def refuse_unless_hermetic(home: Path) -> None:
    """The order's rule 1 and 2, asserted before anything runs."""
    assert os.environ["SESSIONMAIL_SENDMAIL"] == "drop", "no send without the drop carrier"
    assert _operator.settings_home() == home, "the account home is not the scratch HOME"
    assert Path(os.environ["HOME"]) == home
    assert "SESSIONMAIL_HOME" not in os.environ
    assert Path.home() == home
    assert _drop.generator(dict(os.environ), home / ".sessionkit" / "mail") == str(_generator.PACKAGED), \
        "the drop must ask the packaged generator, in print modes, and no other"


def run(cli, *args: str, env: dict[str, str]) -> str:
    """One verb, in-process, with ENV laid over the fixture's environment."""
    got = CliRunner().invoke(cli, list(args), env=env, catch_exceptions=False)
    return f"$ {cli.name} {' '.join(args)} -> {got.exit_code}\n{got.output}"


def new_user_path(home: Path, sid: str, registry: Path) -> list[str]:
    said: list[str] = []
    assert not registry.is_relative_to(home), "the scratch registry is not the user's"
    umask = os.umask(0o022)
    os.umask(umask)
    try:
        rc = seat_init.main(["--registry", str(registry), "--machine", HOST, KEY_SEAT])
    finally:
        os.umask(umask)          # seat init sets 077 for its own process
    said.append(f"$ message seat init --registry <scratch> {KEY_SEAT} -> {rc}")
    assert rc == 0, said[-1]
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(["--box", BOX], dict(os.environ), out=out, err=err, tty=False)
    said.append(f"$ message setup --box {BOX} -> {rc}\n{out.getvalue()}{err.getvalue()}")
    assert rc == 0, said[-1]
    body = home.parent / "body.md"
    body.write_text("hello from a fresh host\n")

    terminal = {"CLAUDE_CODE_AGENT": SEAT, "CLAUDE_CODE_SESSION_ID": None}
    session = {"CLAUDE_CODE_AGENT": None, "CLAUDE_CODE_SESSION_ID": sid}
    for who in (terminal, session):
        said.append(run(message_cli, "send", BOX, "-s", "fresh host", "-f", str(body), env=who))
        said.append(run(message_cli, "list", BOX, env=who))
        said.append(run(message_cli, "read", BOX, "1", env=who))
        said.append(run(message_cli, "mark", BOX, "1", "read", env=who))
        said.append(run(message_cli, "list", BOX, "--unread", env=who))
        said.append(run(message_cli, "tree", BOX, env=who))
        said.append(run(pager_cli, "status", env=who))
        said.append(run(pager_cli, "rule", "add", "ride from:*@example.invalid", env=who))
        said.append(run(pager_cli, "rule", "list", env=who))
        said.append(run(pager_cli, "focus", "sleep", "--until", "23:59", env=who))
        said.append(run(pager_cli, "focus", "off", env=who))
        said.append(run(memory_cli, "wake", BOX, env=who))
        said.append(run(memory_cli, "ls", "--box", BOX, "/", env=who))
    return said


def test_a_fresh_install_writes_nothing_outside_sessionkit_mail_and_mail(fresh, registry, capsys):
    home, sid = fresh["home"], fresh["sid"]
    refuse_unless_hermetic(home)
    before = tree(home)
    assert before == set()
    said = new_user_path(home, sid, registry)
    made = sorted(tree(home) - before)

    leaf, above_leaf = sessions_other(home)
    owners = {leaf: SESSIONS_OWNER, **OTHERS}
    ours = [p for p in made if any(under(p, top) for top in OURS)]
    others = {top: [p for p in made if under(p, top)] for top in owners}
    ancestors = [p for p in made if p in ANCESTORS | above_leaf]
    stray = [p for p in made if p not in ours and p not in ancestors
             and not any(under(p, top) for top in owners)]

    with capsys.disabled():
        print("\n# SC1 fresh-HOME diff (SM-969 do-008): every path made under HOME")
        for p in made:
            kind = "/" if (home / p).is_dir() and not (home / p).is_symlink() else ""
            print(f"+ ~/{p}{kind}")
        for top, paths in others.items():
            if paths:
                print(f"# OTHER ~/{top}: {owners[top]} ({len(paths)} paths)")
        if os.environ.get("SC1_VERBOSE"):
            print("\n".join(said))

    assert not (home / ".sessionmail").exists(), \
        "a fresh install wrote the old root:\n" + "\n".join(
            str(p) for p in made if under(p, Path(".sessionmail")))
    for old in FORBIDDEN:
        assert not os.path.lexists(home / old), \
            f"a fresh install made the OLD name ~/{old} (flips the root or folder back):\n" + \
            "\n".join(str(p) for p in made if under(p, old))
    assert stray == [], f"made outside ~/.sessionkit/mail, by no known owner: {stray}"
    # The state did land, and where: the seat's marks (the walk's file),
    # the seat's rules and the locked folder's parent are under the new root.
    state = home / _settings.STATE_DIR
    assert (state / "seats" / SEAT / "marks").is_file(), sorted(map(str, made))
    assert (state / "seats").is_dir()
    keys = state / "seat-keys" / KEY_SEAT
    assert (keys / "id_ed25519").is_file() and (keys / "seat.pub").is_file()
    assert _settings.seat_keys_leaf() == (state / "seat-keys", _settings.NEW)
    assert _settings.state_leaf("seats")[1] == _settings.NEW
    assert (home / ".sessionkit" / "mail" / _maildir.BOXES / BOX).is_dir()
    assert _maildir.boxes_name(home / ".sessionkit" / "mail") == _maildir.BOXES
    # The notmuch profile (SM-1021): the kit's path, and none of notmuch's.
    assert (home / ".sessionkit" / "mail" / "settings" / "notmuch" / "sessionkit"
            / "config").is_file(), sorted(map(str, made))
    assert not (home / ".config").exists()
    assert not list(home.glob(".notmuch-config*"))
