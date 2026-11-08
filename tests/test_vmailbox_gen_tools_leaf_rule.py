"""SM-1005 fix-008: vmailbox-gen's tools_settings fallback is sessiontools' leaf rule.

When `session path --json` cannot answer (no `session` on PATH, no jq, a
sessiontools without the verb), the generator used to name
`$HOME/.session/{seats,sessions}` outright. On a fresh host that is the
wrong tree, and a declared plus address there was dropped from valias
(rca-002 run B: rc 0, 4 keys, 0 plus addresses). The fallback now restates
sessiontools' rule, copied from `leaf()` and `fresh()` in hooks/busy-sound at
sessiontools e7f1118 (ST-288):

  1. SESSIONS_DIR set and non-empty (sessions only) -> it, a leading `~/` as HOME's
  2. ~/.session/<leaf> a directory, not -ef the new one -> OLD
  3. ~/.sessionkit/tools/state/<leaf> a directory      -> NEW
  4. neither -> OLD only if ~/.session is a directory of its own (not -ef
     ~/.sessionkit/tools/state), otherwise NEW

PARITY. `WANT` is e7f1118's `settings.leaf_path` for each layout, for both
leaves the request key carries (`seats`, `sessions`). The five layouts are
sessiontools test_leaf_paths.py's (old-only, fresh, migrated, new-only,
partial-old), with `migrated` in both of its forms:

  * migrated-leaf          ~/.session/<leaf> -> tools/state/<leaf>
  * migrated-root          ~/.session -> tools/state (the whole root moved): NEW
  * migrated-root-sessions ~/.session -> tools/state/sessions: a root of its
    own (not -ef tools/state), so a leaf that reaches step 4 (seats) is OLD,
    while sessions' new dir is there and step 3 answers NEW first.

The table is checked against the generator on every sessiontools. Each case
is ALSO checked against the installed resolver's `leaf_path` itself where
that resolver carries e7f1118's rule; sessiontools 0.3.2 has no step 4 root
test (its fresh answer is OLD), so there a case that reaches step 4 is
skipped against the resolver, and only that case.

CROSS-PATH. On a fresh HOME, a regen HOLDER with no `session` on PATH and a
WAITER with sessiontools' real `session` compute the SAME request key, and a
session's declared plus address lands in valias.

Hermetic: HOME, the mail root, `--out` and the sidecars are under tmp_path;
`--no-postmap`; SESSIONMAIL_POSTCONF names a missing file;
SESSIONMAIL_SENDMAIL=drop and SESSIONMAIL_VMAILBOX_GEN=/usr/bin/true. Never
the live map.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from test_regen_lock_burst import FAKE_PS, HOST, STARTED, SUFFIX, TRUE, _exe, _maildir

LEAVES = ("seats", "sessions")
OLD_ROOT = Path(".session")
STATE = Path(".sessionkit", "tools", "state")
JQ = shutil.which("jq")
needs_jq = pytest.mark.skipif(not JQ, reason="the generator's valias half needs jq")


def OLD(leaf: str) -> Path:
    return OLD_ROOT / leaf


def NEW(leaf: str) -> Path:
    return STATE / leaf


@pytest.fixture(scope="module")
def generator() -> Path:
    """The generator as the package ships it."""
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as path:
        yield Path(path)


def make(layout: str, leaf: str, home: Path) -> None:
    """LAYOUT for LEAF under HOME: sessiontools test_leaf_paths.make's, with
    `migrated` in both forms. Only LEAF's own folders are made."""
    home.mkdir(parents=True, exist_ok=True)
    if layout == "old-only":
        (home / OLD(leaf)).mkdir(parents=True)
    elif layout == "new-only":
        (home / NEW(leaf)).mkdir(parents=True)
    elif layout == "migrated-leaf":
        (home / NEW(leaf)).mkdir(parents=True)
        (home / OLD_ROOT).mkdir()
        (home / OLD(leaf)).symlink_to(home / NEW(leaf))
    elif layout == "migrated-root":
        (home / STATE).mkdir(parents=True)
        (home / OLD_ROOT).symlink_to(home / STATE)
    elif layout == "migrated-root-sessions":
        (home / NEW("sessions")).mkdir(parents=True)
        (home / OLD_ROOT).symlink_to(home / NEW("sessions"))
    elif layout == "partial-old":
        (home / OLD_ROOT).mkdir()                 # a live host's root, not this leaf
    else:
        assert layout == "fresh", layout


#: e7f1118's `settings.leaf_path(leaf)[0]`, relative to HOME, per layout.
WANT = {
    ("old-only", "seats"): OLD("seats"),
    ("old-only", "sessions"): OLD("sessions"),
    ("fresh", "seats"): NEW("seats"),
    ("fresh", "sessions"): NEW("sessions"),
    ("migrated-leaf", "seats"): NEW("seats"),
    ("migrated-leaf", "sessions"): NEW("sessions"),
    ("migrated-root", "seats"): NEW("seats"),
    ("migrated-root", "sessions"): NEW("sessions"),
    ("migrated-root-sessions", "seats"): OLD("seats"),      # step 4: a root of its own
    ("migrated-root-sessions", "sessions"): NEW("sessions"),  # step 3 first
    ("new-only", "seats"): NEW("seats"),
    ("new-only", "sessions"): NEW("sessions"),
    ("partial-old", "seats"): OLD("seats"),
    ("partial-old", "sessions"): OLD("sessions"),
}
LAYOUTS = sorted({layout for layout, _ in WANT})


def _no_session_path(tmp: Path) -> str:
    """A PATH with no `session` on it: an empty dir, then /usr/bin and /bin."""
    empty = tmp / "empty-bin"
    empty.mkdir(exist_ok=True)
    path = os.pathsep.join([str(empty), "/usr/bin", "/bin"])
    assert shutil.which("session", path=path) is None
    return path


def _refusing_session_path(tmp: Path) -> str:
    """A `session` that cannot answer `path --json` (a sessiontools before
    the verb: exit 2), with jq beside it."""
    stub = tmp / "refusing-bin"
    stub.mkdir(exist_ok=True)
    _exe(stub / "session", "#!/bin/sh\necho \"session: unknown verb '$1'\" >&2\nexit 2\n")
    if JQ:
        (stub / "jq").symlink_to(JQ)
    return os.pathsep.join([str(stub), "/usr/bin", "/bin"])


PATHS = {"no-session": _no_session_path, "verb-refuses": _refusing_session_path}


def tools_leaf(generator: Path, home: Path, leaf: str, path: str,
               **extra: str) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home.parent), "LC_ALL": "C", **extra}
    return subprocess.run([str(generator), "--tools-leaf", leaf],
                          capture_output=True, text=True, env=env, check=False)


def _resolver():
    """The installed sessiontools' settings module, and whether it carries
    e7f1118's step 4 root test (`old_root_real`)."""
    from sessiontools import settings
    return settings, hasattr(settings, "old_root_real")


def _files(home: Path) -> list[tuple[str, bool]]:
    return sorted((str(p.relative_to(home)), p.is_symlink()) for p in home.rglob("*"))


# ── parity: the table, on every sessiontools ─────────────────────────────

@pytest.mark.parametrize("how", list(PATHS))
@pytest.mark.parametrize("leaf", LEAVES)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_fallback_is_e7f1118s_leaf_rule(generator, tmp_path, layout, leaf, how):
    home = tmp_path / "home"
    make(layout, leaf, home)
    before = _files(home)
    done = tools_leaf(generator, home, leaf, PATHS[how](tmp_path))
    assert (done.returncode, done.stdout, done.stderr) == (0, str(home / WANT[layout, leaf]), "")
    assert _files(home) == before                    # it only looks: nothing made


@pytest.mark.parametrize("leaf", LEAVES)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_table_is_the_installed_resolvers_answer(tmp_path, layout, leaf):
    """The same layout through the installed resolver's `leaf_path`. Every
    case on a resolver with e7f1118's rule; on one without it (0.3.2), every
    case its steps 1-3 answer, which both rules share."""
    settings, has_root_rule = _resolver()
    home = tmp_path / "home"
    make(layout, leaf, home)
    path, source = settings.leaf_path(leaf, {"HOME": str(home)})
    if source.startswith("fresh-") and not has_root_rule:
        pytest.skip(f"step 4: this sessiontools has no old_root_real (answers {source}); "
                    "the fallback restates e7f1118's rule")
    assert path == home / WANT[layout, leaf], source


@pytest.mark.parametrize("leaf", LEAVES)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_generator_matches_the_installed_resolver(generator, tmp_path, layout, leaf):
    """The direct parity, generator against resolver, no table between them."""
    settings, has_root_rule = _resolver()
    home = tmp_path / "home"
    make(layout, leaf, home)
    path, source = settings.leaf_path(leaf, {"HOME": str(home)})
    if source.startswith("fresh-") and not has_root_rule:
        pytest.skip(f"step 4: this sessiontools has no old_root_real (answers {source})")
    assert tools_leaf(generator, home, leaf, _no_session_path(tmp_path)).stdout == str(path)


# ── SESSIONS_DIR: the sessions leaf's override, and only its ─────────────

@pytest.mark.parametrize("layout", LAYOUTS)
def test_sessions_dir_wins_for_sessions_and_never_moves_seats(generator, tmp_path, layout):
    home = tmp_path / "home"
    make(layout, "sessions", home)
    path = _no_session_path(tmp_path)
    sd = tmp_path / "sd"
    assert tools_leaf(generator, home, "sessions", path, SESSIONS_DIR=str(sd)).stdout == str(sd)
    seats = tools_leaf(generator, home, "seats", path, SESSIONS_DIR=str(sd)).stdout
    assert seats == tools_leaf(generator, home, "seats", path).stdout


def test_a_leading_tilde_in_sessions_dir_is_homes(generator, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    done = tools_leaf(generator, home, "sessions", _no_session_path(tmp_path), SESSIONS_DIR="~/x/s")
    assert done.stdout == str(home / "x" / "s")
    settings, _ = _resolver()
    monkeypatch.setenv("HOME", str(home))        # python's expanduser reads the process HOME
    assert settings.leaf_path("sessions", {"HOME": str(home), "SESSIONS_DIR": "~/x/s"}) == (
        home / "x" / "s", "override")


def test_an_empty_sessions_dir_is_unset(generator, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = _no_session_path(tmp_path)
    assert (tools_leaf(generator, home, "sessions", path, SESSIONS_DIR="").stdout
            == tools_leaf(generator, home, "sessions", path).stdout == str(home / NEW("sessions")))


def test_the_copy_names_its_source(generator):
    """The fallback says where the rule comes from, and no longer claims the
    script never spells tools' root."""
    text = generator.read_text()
    assert "hooks/busy-sound at\n# sessiontools e7f1118" in text
    assert "never spells tools'" not in text
    assert "tools_rule_leaf()" in text and "tools_rule_fresh()" in text


# ── cross-PATH: a holder without `session`, a waiter with it ─────────────

PLUS = "sessionmail+sm-1005.worker"
HOLDER_SID = "11111111-2222-4333-8444-555555555555"
WAITER_SID = "66666666-7777-4888-9999-aaaaaaaaaaaa"
DECLARED_SID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"


def _real_session() -> Path:
    """sessiontools' own `session`, beside the interpreter running the suite."""
    exe = Path(sys.executable).parent / "session"
    if not os.access(exe, os.X_OK):
        pytest.skip(f"no sessiontools `session` beside {sys.executable}")
    return exe


class _Regen:
    """A scratch root, a map dir, sidecars, a fake `ps` that can hold a run."""

    def __init__(self, tmp: Path, generator: Path) -> None:
        self.tmp, self.generator = tmp, generator
        self.home = tmp / "home"
        self.home.mkdir()
        self.root = tmp / "root"
        self.out = tmp / "maps" / "vmailbox"
        self.sidecars = tmp / "claude-sessions"
        self.sidecars.mkdir()
        self.table = tmp / "ps.txt"
        self.table.write_text("")
        self.holding = tmp / "holding"
        self.bin = tmp / "bin"                       # ps, sleep, jq: NO session
        self.bin.mkdir()
        _exe(self.bin / "ps", FAKE_PS)
        (self.bin / "sleep").symlink_to(TRUE)
        (self.bin / "jq").symlink_to(JQ)
        self.logs = tmp / "logs"
        self.logs.mkdir()
        (self.root / "agents").mkdir(parents=True)

    def birth(self, pid: int, sid: str) -> None:
        _maildir(self.root / "agents" / sid)
        car = {"pid": pid, "sessionId": sid, "procStart": STARTED, "agent": "worker"}
        (self.sidecars / f"{pid}.json").write_text(json.dumps(car))
        with self.table.open("a") as table:
            table.write(f"{pid:>5} {STARTED}    \n")

    def start(self, name: str, path: str, sid: str, hold: float | None = None) -> subprocess.Popen:
        env = {"PATH": path, "HOME": str(self.home), "TMPDIR": str(self.tmp), "LC_ALL": "C",
               "MAGICDNS_SUFFIX": SUFFIX, "SESSION_SIDECAR_DIR": str(self.sidecars),
               "SESSIONMAIL_HOME": str(self.tmp / "sessionmail"),
               "SESSIONMAIL_POSTCONF": str(self.tmp / "no-postconf"),
               "SESSIONMAIL_SENDMAIL": "drop", "SESSIONMAIL_VMAILBOX_GEN": "/usr/bin/true",
               "FAKE_PS_TABLE": str(self.table), "FAKE_PS_HOLDING": str(self.holding),
               "CLAUDE_CODE_SESSION_ID": sid}
        if hold is not None:
            env["FAKE_PS_HOLD"] = str(hold)
        return subprocess.Popen(
            [str(self.generator), "--host", HOST, "--root", str(self.root),
             "--out", str(self.out), "--no-postmap"],
            stdout=(self.logs / f"{name}.out").open("w"),
            stderr=(self.logs / f"{name}.err").open("w"), env=env)

    @property
    def reqdir(self) -> Path:
        return self.out.parent / ".vmailbox-gen.req"

    def requests(self) -> list[str]:
        return sorted(p.name for p in self.reqdir.iterdir()) if self.reqdir.is_dir() else []

    def err(self, name: str) -> str:
        return (self.logs / f"{name}.err").read_text()


def _wait(pred, what: str, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not pred():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


@needs_jq
def test_a_holder_without_session_and_a_waiter_with_it_share_a_request_key(generator, tmp_path):
    """On a fresh HOME: a session declares its plus address where sessiontools
    puts its layer (as its `pager listen` does), then a holder with no
    `session` on PATH and a waiter with the real one regen together. Both
    requests carry ONE key, and the address is in valias."""
    session = _real_session()
    rig = _Regen(tmp_path, generator)
    assert not any(rig.home.iterdir())                     # fresh: nothing of tools' yet
    settings, _ = _resolver()
    layer = settings.leaf_path("sessions", {"HOME": str(rig.home)})[0] / DECLARED_SID / "settings"
    layer.mkdir(parents=True)
    (layer / "mail.json").write_text(json.dumps({"address": PLUS}) + "\n")
    rig.birth(40001, DECLARED_SID)
    rig.birth(40002, HOLDER_SID)
    rig.birth(40003, WAITER_SID)
    holder_path = os.pathsep.join([str(rig.bin), "/usr/bin", "/bin"])
    waiter_path = os.pathsep.join([str(rig.bin), str(session.parent), "/usr/bin", "/bin"])
    assert shutil.which("session", path=holder_path) is None
    assert shutil.which("session", path=waiter_path) == str(session)

    holder = rig.start("holder", holder_path, HOLDER_SID, hold=2.0)
    _wait(rig.holding.exists, "the holder never reached its read")
    waiter = rig.start("waiter", waiter_path, WAITER_SID)
    _wait(lambda: len(rig.requests()) == 2, f"requests: {rig.requests()}")
    keys = {name.split(".", 1)[0] for name in rig.requests()}
    owners = {name.split(".")[2] for name in rig.requests()}
    assert owners == {HOLDER_SID, WAITER_SID}
    assert len(keys) == 1, f"the holder and the waiter keyed apart: {rig.requests()}"
    assert holder.wait(timeout=60) == 0, rig.err("holder")
    assert waiter.wait(timeout=60) == 0, rig.err("waiter")

    valias = (rig.out.parent / "valias").read_text()
    rows = [line for line in valias.splitlines() if line.startswith(f"{PLUS}@")]
    assert rows and all(DECLARED_SID in line for line in rows), valias
    assert not (rig.home / OLD_ROOT).exists() or settings.leaf_path(
        "sessions", {"HOME": str(rig.home)})[1] == "old"     # no stray old root on a new host
