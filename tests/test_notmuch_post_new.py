"""The notmuch post-new hook: what makes a supersede survive losing the index.

notmuch tags live in a Xapian index, and the index is a derived artefact:
delete it and `notmuch new` rebuilds it from the maildir. On 2030-07-15 that
was done for real and the `superseded` tag did not come back, so the
current-truth query `tag:memory and not tag:superseded` served a disproved
memory as fact (the hook's own header tells it). The hook closes that
(`src/sessionmail/notmuch/post-new`, package data since SM-731 do-006;
`hooks/notmuch-post-new` is a relative symlink to it): it re-derives `superseded` from the `Supersedes:` (and the
pre-rename `X-Supersedes:`) headers in the immutable messages, and tags
`+agent` and `+memory` by path. Its only source was the retired `session`
repo, and an older version; it now lives here.

Driven the way it is called: by `notmuch new`, which runs it, over a
throwaway database under `tmp_path` - never the user's. The hook FINDS
notmuch (SM-796): `$SESSIONMAIL_NOTMUCH`, else PATH, else its NOTMUCH_PATHS
list. PATH is launchd's, the other place the hook runs, and the suite's
fake `SESSIONMAIL_NOTMUCH` (conftest) is dropped, so the binary the tests
run is the one the hook finds there, and the database tests skip where
there is none; `NOTMUCH_CONFIG` reaches the hook through `notmuch new`'s
environment, and HOME is moved too, so that a hook which somehow lost the
variable finds no config at all rather than the real one.

The second half covers the opt-in post-new command: with the notmuch key
`sessionmail.post_new_command` set, the hook ends by asking a detached
runner for one run of it. Those tests set the key in the scratch config
only, and the command is only ever a recorder under `tmp_path`. None of
them proves "does not block" with a clock: the command waits on a gate
file, and the caller must return while the gate is still shut.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: The packaged hook (SM-731 design §4): package data, so a wheel carries it.
HOOK = ROOT / "src" / "sessionmail" / "notmuch" / "post-new"
#: The old path, a relative symlink to HOOK: the install's `ln -s` lines
#: (docs/notmuch-settings.md, "The post-new hook") and hq's live
#: `post-new` link point here, so it must keep running the same bytes.
LINK = ROOT / "hooks" / "notmuch-post-new"
#: launchd's PATH: no /opt/homebrew/bin, no agent's `grep` shim.
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
#: The hook's fallback list, read from the hook so the two cannot drift.
NOTMUCH_PATHS = re.search(r'^NOTMUCH_PATHS="([^"]+)"$', HOOK.read_text(), re.M).group(1).split()
#: The binary the hook finds under Db's env (launchd's PATH, no
#: SESSIONMAIL_NOTMUCH): notmuch on that PATH, else the list's first runnable.
NOTMUCH = shutil.which("notmuch", path=LAUNCHD_PATH) or next(
    (p for p in NOTMUCH_PATHS if os.access(p, os.X_OK)), NOTMUCH_PATHS[0])
#: The setting the hook reads first (src/sessionmail/_notmuch.py BINARY_VAR).
BINARY_VAR = "SESSIONMAIL_NOTMUCH"
#: The perl the seam detaches with, read from the hook.
PERL = re.search(r"^PERL=(\S+)$", HOOK.read_text(), re.M).group(1)
INDEXED = "[index]\nheader.Supersedes=Supersedes\nheader.XSupersedes=X-Supersedes\n"


class Db:
    """A maildir tree like the fleet's (`agents/<seat>/`, `agents/memory/`)
    and a notmuch database over it whose hook_dir holds the repo's hook."""

    def __init__(self, tmp: Path, indexed: bool = True, hook: Path = HOOK):
        self.root = tmp / "mail"
        for box in ("alpha", "memory"):
            for sub in ("cur", "new", "tmp"):
                (self.root / "agents" / box / sub).mkdir(parents=True)
        hooks = tmp / "hooks"
        hooks.mkdir()
        (hooks / "post-new").symlink_to(hook)
        (hooks / "post-insert").symlink_to("post-new")   # as on the host
        config = tmp / "notmuch-config"
        self.tmp, self.config = tmp, config
        self.home = tmp / "home"
        self.home.mkdir()
        self.state = self.home / ".sessionmail" / "post-new"
        config.write_text(
            f"[database]\npath={self.root}\nhook_dir={hooks}\n"
            "[user]\nname=t\nprimary_email=t@example.invalid\n"
            "[new]\ntags=unread\n" + (INDEXED if indexed else ""))
        self.env = {**os.environ, "NOTMUCH_CONFIG": str(config), "HOME": str(self.home),
                    "XDG_CONFIG_HOME": str(tmp / "xdg"), "PATH": LAUNCHD_PATH}
        # BINARY_VAR too: conftest points it at a fake engine for every test,
        # and the hook reads it first. A test that wants a shim sets it.
        for var in ("MAILDIR", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE", BINARY_VAR):
            self.env.pop(var, None)
        self.count = 0

    def deliver(self, box: str, msgid: str, headers: str = "") -> None:
        self.count += 1
        (self.root / "agents" / box / "new" / f"{1908540800 + self.count}.M1P1.test").write_text(
            f"From: beta@example.invalid\nTo: alpha@example.invalid\n"
            f"Subject: item {self.count}\nDate: Tue, 01 Jan 2030 12:00:00 +0000\n"
            f"Message-ID: <{msgid}>\n{headers}\nbody\n")

    def notmuch(self, *args: str) -> subprocess.CompletedProcess:
        run = subprocess.run([NOTMUCH, *args], capture_output=True, text=True, env=self.env)
        assert run.returncode == 0, f"notmuch {' '.join(args)}: {run.stderr}"
        return run

    def message(self, msgid: str) -> str:
        self.count += 1
        return (f"From: beta@example.invalid\nTo: alpha@example.invalid\n"
                f"Subject: item {self.count}\nMessage-ID: <{msgid}>\n\nbody\n")

    def insert(self, msgid: str, box: str = "alpha") -> subprocess.CompletedProcess:
        """The postfix path: `notmuch insert`, which runs post-insert. The
        timeout is the failure bound for a hook that blocks."""
        run = subprocess.run([NOTMUCH, "insert", f"--folder=agents/{box}"],
                             input=self.message(msgid), capture_output=True, text=True,
                             env=self.env, timeout=30)
        assert run.returncode == 0, f"notmuch insert: {run.stderr}"
        return run

    def set_command(self, command: str | Path) -> None:
        """Set the opt-in key - in the SCRATCH config, never the real one."""
        assert Path(self.env["NOTMUCH_CONFIG"]).is_relative_to(self.tmp), \
            "config set may only touch a scratch config"
        self.notmuch("config", "set", "sessionmail.post_new_command", str(command))

    def quiet(self, limit: float = 30) -> None:
        """Wait until nothing is pending: `again` is absent and the lock is
        free. The hook touches `again` before it spawns, so this cannot pass
        before a requested run has been served. The lock is probed only
        while `again` is absent, so the probe can never turn a runner away
        from work."""
        end = time.monotonic() + limit
        again, lock = self.state / "again", self.state / "lock"
        while time.monotonic() < end:
            if not again.exists():
                if not lock.exists():
                    return
                with open(lock, "a") as f:
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(f, fcntl.LOCK_UN)
                        return
                    except BlockingIOError:
                        pass
            time.sleep(0.05)
        if again.exists() and not runner_alive(self.state):
            raise AssertionError("again stranded with no holder: an arrival was lost")
        raise AssertionError("still running")

    def new(self, *args: str, quiet: bool = True) -> subprocess.CompletedProcess:
        run = self.notmuch("new", *args)
        if quiet:
            assert "post-new:" not in run.stderr, f"the hook spoke: {run.stderr}"
        return run

    def tags(self, msgid: str) -> set[str]:
        return set(self.notmuch("search", "--output=tags", id_query(msgid)).stdout.split())

    def ids(self, query: str) -> set[str]:
        out = self.notmuch("search", "--output=messages", query).stdout.split()
        return {line.removeprefix("id:") for line in out}

    def wipe(self) -> None:
        """Lose the index: the maildir and the config stay, as on the host."""
        shutil.rmtree(self.root / ".notmuch" / "xapian")


def id_query(msgid: str) -> str:
    """id:"..." the notmuch way: an embedded `"` doubled (SM-796)."""
    return 'id:"' + msgid.replace('"', '""') + '"'


@pytest.fixture
def db(tmp_path):
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    return Db(tmp_path)


def supersede(db: Db, box: str = "alpha", header: str = "Supersedes") -> tuple[str, str]:
    """A, indexed; then B declaring it supersedes A, indexed - the order a
    correction arrives in."""
    old, new = f"old.{box}@example.invalid", f"new.{box}@example.invalid"
    db.deliver(box, old)
    db.new()
    db.deliver(box, new, f"{header}: <{old}>\n")
    db.new()
    return old, new


def test_the_hook_parses_and_is_executable():
    """notmuch runs a hook only if it can execute it; one that does not
    parse fails at the first `notmuch new`, which nobody watches."""
    assert subprocess.run(["sh", "-n", str(HOOK)], capture_output=True, text=True).returncode == 0
    assert os.access(HOOK, os.X_OK), "notmuch skips a hook it cannot execute"


def test_a_supersedes_header_tags_its_target_superseded(db):
    old, new = supersede(db)
    assert {"agent", "superseded"} <= db.tags(old)
    assert {"agent", "supersedes-applied"} <= db.tags(new)
    assert "superseded" not in db.tags(new)
    assert "supersedes-applied" not in db.tags(old)


def test_a_target_indexed_after_its_supersede_is_still_superseded(db):
    """The correction can be indexed first: another box, a sync, a late
    delivery. The carrier must stay pending until its target is in the
    index, or the target is never tagged until a wipe (the first cut of the
    O(new carriers) rewrite marked it applied at once)."""
    old, new = "late.alpha@example.invalid", "early.alpha@example.invalid"
    db.deliver("alpha", new, f"Supersedes: <{old}>\n")
    db.new()
    assert "supersedes-applied" not in db.tags(new), "applied before its target existed"
    db.deliver("alpha", old)
    db.new()
    assert "superseded" in db.tags(old)
    assert "supersedes-applied" in db.tags(new)


def test_a_failed_count_leaves_the_carrier_pending(tmp_path):
    """`notmuch count` runs while `tag --batch` holds the writer at the other
    end of the same pipe, so it can fail. A failed count must fail toward
    pending: an empty answer is not "the target is indexed". The hook's
    `$SESSIONMAIL_NOTMUCH` points at a shim that fails `count` and passes
    everything else to the real binary."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    shim = tmp_path / "notmuch-shim"
    failing = f'#!/bin/sh\n[ "$1" = count ] && exit 1\nexec {NOTMUCH} "$@"\n'
    shim.write_text(failing)
    shim.chmod(0o755)
    db = Db(tmp_path)
    db.env[BINARY_VAR] = str(shim)

    old, new = supersede(db)
    assert "superseded" in db.tags(old), "the retag does not depend on the count"
    assert "supersedes-applied" not in db.tags(new), "applied on a count that failed"

    shim.write_text(f'#!/bin/sh\nexec {NOTMUCH} "$@"\n')
    db.new()
    assert "supersedes-applied" in db.tags(new), "the next run did not retry"


def test_the_pre_rename_x_supersedes_spelling_is_honoured(db):
    """Mail from the first hours of 2030-07-15 carries `X-Supersedes:`, and
    it is immutable, so the hook reads both spellings forever."""
    old, new = supersede(db, header="X-Supersedes")
    assert {"agent", "superseded"} <= db.tags(old)
    assert {"agent", "supersedes-applied"} <= db.tags(new)


def test_the_memory_box_is_tagged_memory_and_a_seat_box_is_not(db):
    db.deliver("memory", "m@example.invalid")
    db.deliver("alpha", "a@example.invalid")
    db.new()
    assert {"agent", "memory"} <= db.tags("m@example.invalid")
    assert "agent" in db.tags("a@example.invalid")
    assert "memory" not in db.tags("a@example.invalid")


def test_rebuild_from_nothing_brings_the_supersede_back(db):
    """THE REASON THE HOOK EXISTS. Wipe the index and rebuild it: the
    correction must come back from the mail alone. The control first:
    rebuilt WITHOUT the hook the tag is gone, which is the 2030-07-15 loss
    and what shows the wipe was real."""
    old, new = supersede(db, box="memory")
    assert db.ids("tag:memory and not tag:superseded") == {new}

    db.wipe()
    db.new("--no-hooks")
    assert "superseded" not in db.tags(old), "the index alone keeps nothing"

    db.wipe()
    db.new()
    assert {"agent", "memory", "superseded"} <= db.tags(old)
    assert {"agent", "memory", "supersedes-applied"} <= db.tags(new)
    assert db.ids("tag:memory and not tag:superseded") == {new}


def test_a_second_run_with_no_new_mail_changes_nothing(db):
    """Idempotent, and cheap: the `not tag:` guards and `supersedes-applied`
    mean a re-run writes nothing - not one tag, not one revision."""
    supersede(db)
    supersede(db, box="memory")
    before = (db.notmuch("dump").stdout, db.notmuch("count", "--lastmod", "*").stdout)
    assert "No new mail" in db.new().stdout
    after = (db.notmuch("dump").stdout, db.notmuch("count", "--lastmod", "*").stdout)
    assert after == before


def test_without_the_indexed_headers_it_falls_back_and_says_so(tmp_path):
    """A host whose config lost the two `index.header` lines: the indexed
    query errors, the hook greps the corpus instead and SAYS SO on stderr,
    because a silent degrade here would look like an idle mailbox."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    db = Db(tmp_path, indexed=False)
    db.deliver("alpha", "old@example.invalid")
    db.deliver("alpha", "new@example.invalid", "Supersedes: <old@example.invalid>\n")
    run = db.new(quiet=False)
    assert "falling back to full-corpus grep" in run.stderr
    assert "superseded" in db.tags("old@example.invalid")
    assert "supersedes-applied" in db.tags("new@example.invalid")

# ---------------------------------------------------------------------------
# SM-796: every id spliced into a query is quoted the notmuch way, and the
# hook FINDS notmuch ($SESSIONMAIL_NOTMUCH, else PATH, else NOTMUCH_PATHS)
# instead of running /opt/homebrew/bin/notmuch as THE path.

def test_a_poison_id_holding_a_quote_supersedes_exactly_its_target(db):
    """`<q"t@x>` spliced raw made id:"q"t@x", which matched nothing: the
    target was never superseded, and the carrier never applied, so it was
    re-read on every run. Quoted (`id:"q""t@x"`), it supersedes exactly its
    target: not a decoy whose id shares its prefix, nor one named by the
    part before the quote."""
    target, carrier = 'q"t@example.invalid', "carrier@example.invalid"
    decoys = ('q"t@example.invalid.decoy', "q@example.invalid", 'q"t')
    db.deliver("alpha", target)
    for decoy in decoys:
        db.deliver("alpha", decoy)
    db.new()
    db.deliver("alpha", carrier, f"Supersedes: <{target}>\n")
    db.new()
    assert db.ids("tag:superseded") == {target}, "not exactly the target"
    assert "supersedes-applied" in db.tags(carrier), "the carrier stays pending forever"
    for decoy in decoys:
        assert "superseded" not in db.tags(decoy), decoy


def test_a_carrier_whose_own_id_holds_a_quote_is_applied(db):
    """The carrier's own id is spliced too (`+supersedes-applied`)."""
    old, carrier = "plain.old@example.invalid", 'c"arrier@example.invalid'
    db.deliver("alpha", old)
    db.new()
    db.deliver("alpha", carrier, f"Supersedes: <{old}>\n")
    db.new()
    assert "superseded" in db.tags(old)
    assert "supersedes-applied" in db.tags(carrier)
    assert db.ids("tag:supersedes-applied") == {carrier}


@pytest.mark.parametrize("target,stored", [
    ("p(a)ren@example.invalid", "p(a)ren@example.invalid"),
    # PINNED, not fixed: notmuch 0.40 drops whitespace from a Message-ID, and
    # the hook strips it from the header, so the two agree (measured, SM-796).
    ("sp ace@example.invalid", "space@example.invalid"),
])
def test_an_id_holding_parentheses_or_a_space_supersedes_its_target(db, target, stored):
    decoy = stored + ".decoy"
    db.deliver("alpha", target)
    db.deliver("alpha", decoy)
    db.new()
    db.deliver("alpha", "c@example.invalid", f"Supersedes: <{target}>\n")
    db.new()
    assert db.ids("tag:superseded") == {stored}
    assert "supersedes-applied" in db.tags("c@example.invalid")


def path_shim(tmp: Path, name: str = "notmuch") -> tuple[Path, Path]:
    """(a dir holding a logging pass-through to the real notmuch named NAME,
    the log it writes one line per call to)."""
    bindir, log = tmp / "bin", tmp / "path-shim.log"
    bindir.mkdir()
    shim = bindir / name
    shim.write_text(f'#!/bin/sh\necho "$1" >> {log}\nexec {NOTMUCH} "$@"\n')
    shim.chmod(0o755)
    return bindir, log


@pytest.mark.parametrize("how", ["path", "setting-by-name"])
def test_the_hook_finds_notmuch_on_a_scratch_path(tmp_path, how):
    """No absolute path: `notmuch` on PATH ahead of the list, and a bare
    name in `$SESSIONMAIL_NOTMUCH` is looked up on PATH too. The shim's
    log proves the hook ran THIS notmuch, not the one it used to name."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    name = "notmuch" if how == "path" else "nm-by-name"
    bindir, log = path_shim(tmp_path, name)
    db = Db(tmp_path)
    db.env["PATH"] = f"{bindir}:{LAUNCHD_PATH}"
    if how != "path":
        db.env[BINARY_VAR] = name
    old, new = supersede(db)
    assert "superseded" in db.tags(old) and "supersedes-applied" in db.tags(new)
    calls = log.read_text().split()
    assert {"config", "search", "tag", "count"} <= set(calls), calls


def run_hook(hook: Path, tmp: Path, **env: str) -> subprocess.CompletedProcess:
    """The hook run bare, the way notmuch would, with a scratch HOME and a
    config that does not exist: whatever it finds can open no database."""
    base = {"HOME": str(tmp / "home"), "NOTMUCH_CONFIG": str(tmp / "no-such-config"),
            "PATH": LAUNCHD_PATH}
    return subprocess.run([str(hook)], env={**base, **env}, capture_output=True,
                          text=True, timeout=30)


def test_a_setting_naming_a_missing_notmuch_fails_loudly_with_no_fallback(tmp_path):
    """A wrong `$SESSIONMAIL_NOTMUCH` is ONE stderr line and a non-zero exit,
    never a silent fallback to another binary (which could hide it)."""
    run = run_hook(HOOK, tmp_path, **{BINARY_VAR: str(tmp_path / "no-such-notmuch")})
    assert run.returncode != 0, run
    assert run.stderr.count("\n") == 1 and "post-new: notmuch not found" in run.stderr, run.stderr
    assert str(tmp_path / "no-such-notmuch") in run.stderr


def test_no_notmuch_anywhere_is_one_stderr_line_and_a_failure(tmp_path):
    """Not on PATH, not in the list: one line naming where it looked, exit
    non-zero, so `notmuch new` reports a failed hook, not an idle mailbox."""
    hook = copy_hook(tmp_path, "post-new-no-list",
                     (r'^NOTMUCH_PATHS=".*"$', f'NOTMUCH_PATHS="{tmp_path}/nope/notmuch"', 0))
    empty = tmp_path / "empty-path"
    empty.mkdir()
    run = run_hook(hook, tmp_path, PATH=str(empty))
    assert run.returncode != 0, run
    assert run.stderr.count("\n") == 1 and "post-new: notmuch not found" in run.stderr, run.stderr
    assert f"{tmp_path}/nope/notmuch" in run.stderr


def test_the_hook_names_no_notmuch_path_as_the_one_it_runs():
    """The Homebrew prefix may appear only as a candidate in NOTMUCH_PATHS
    (launchd's PATH has none), never assigned as THE binary."""
    text = HOOK.read_text()
    assert not re.search(r"^NOTMUCH=/", text, re.M), "a hardcoded NOTMUCH= is back"
    code = [line for line in text.splitlines()
            if "/opt/homebrew" in line and not line.lstrip().startswith("#")]
    assert code == [line for line in code if line.startswith("NOTMUCH_PATHS=")], code


# ---------------------------------------------------------------------------
# SM-731 do-006: the hook is package data, and its two path queries are keys
# (design §4; openspec/changes/store-index/specs/notmuch-hook). Every DB here
# is a scratch one, its env built by Db (MAILDIR, NOTMUCH_DATABASE and
# NOTMUCH_PROFILE dropped, NOTMUCH_CONFIG and HOME moved to tmp_path).

#: The batch the hook sent before the keys existed, byte for byte.
TODAYS_BATCH = ("+agent -- path:agents/** and not tag:agent\n"
                "+memory -- path:agents/memory/** and not tag:memory\n")


def test_the_old_hooks_path_is_a_relative_symlink_to_the_packaged_hook():
    """The install's `ln -s` lines and hq's live `post-new` link point at the old path,
    so it must run the same bytes. Relative, so a clone anywhere works."""
    assert LINK.is_symlink(), "hooks/notmuch-post-new is no longer a link"
    assert not os.path.isabs(os.readlink(LINK)), "an absolute link breaks in another clone"
    assert LINK.resolve() == HOOK.resolve()
    assert LINK.read_bytes() == HOOK.read_bytes()
    assert os.access(LINK, os.X_OK)


def batch_recorder(tmp: Path) -> tuple[Path, Path]:
    """(a shim for the hook's `$SESSIONMAIL_NOTMUCH`, the dir it logs to).
    The shim writes each `tag --batch` stdin to its own numbered file, in
    call order, and passes every call through to the real notmuch."""
    log = tmp / "batches"
    log.mkdir()
    shim = tmp / "notmuch-batch-shim"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = tag ] && [ "$2" = --batch ]; then\n'
        f'  n=$(/bin/ls {log} | /usr/bin/wc -l | /usr/bin/tr -d " ")\n'
        f'  /usr/bin/tee {log}/$n | {NOTMUCH} "$@"; exit $?\n'
        "fi\n"
        f'exec {NOTMUCH} "$@"\n')
    shim.chmod(0o755)
    return shim, log


def test_keys_unset_the_tag_batch_is_byte_identical_to_todays(tmp_path):
    """Both keys unset, then both set EMPTY: the first batch is exactly
    today's bytes, and today's tags follow."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    shim, log = batch_recorder(tmp_path)
    db = Db(tmp_path)
    db.env[BINARY_VAR] = str(shim)
    db.deliver("alpha", "a@example.invalid")
    db.deliver("memory", "m@example.invalid")
    db.new()
    assert (log / "0").read_text() == TODAYS_BATCH
    assert "agent" in db.tags("a@example.invalid") and "memory" not in db.tags("a@example.invalid")
    assert {"agent", "memory"} <= db.tags("m@example.invalid")

    for key in ("agent_query", "memory_query"):
        db.notmuch("config", "set", f"sessionmail.{key}", "")
    first = len(list(log.iterdir()))
    db.deliver("alpha", "b@example.invalid")
    db.new()
    assert (log / str(first)).read_text() == TODAYS_BATCH, "an empty key is not unset"


def store_db(tmp: Path) -> tuple[Db, Path]:
    """A Db whose database.path is a STORE directory, as a store profile's
    is: the store box itself (a store-level seat's box IS the store dir),
    the marker, a project box nested inside it, and a store-local `memory`
    box. Mail under the store root has no `agents/` in its notmuch path."""
    db = Db(tmp)
    store = db.root / "agents" / "acme"
    for box in (store, store / "proj", store / "memory"):
        for sub in ("cur", "new", "tmp"):
            (box / sub).mkdir(parents=True)
    (store / ".tm-store").write_text("")
    text, n = re.subn(r"^path=.*$", f"path={store}", db.config.read_text(), flags=re.M)
    assert n == 1
    db.config.write_text(text)
    return db, store


def put(box: Path, msgid: str, n: int) -> None:
    (box / "new" / f"{1908540800 + n}.M1P1.test").write_text(
        f"From: beta@example.invalid\nTo: alpha@example.invalid\nSubject: s{n}\n"
        f"Message-ID: <{msgid}>\n\nbody\n")


STORE_MAIL = {"store.box@example.invalid": "", "proj@example.invalid": "proj",
              "mem@example.invalid": "memory"}


def test_agent_query_path_all_tags_every_message_in_a_store_rooted_db(tmp_path):
    """E6: rooted at a store, `path:agents/**` matches nothing, so today's
    default tags no message `agent`. With `sessionmail.agent_query=path:**`,
    as the store template will set it, every message is tagged."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    db, store = store_db(tmp_path)
    for n, (msgid, box) in enumerate(STORE_MAIL.items()):
        put(store / box if box else store, msgid, n)
    db.new()
    assert db.ids("*") == set(STORE_MAIL), "the scratch store was not indexed"
    assert db.ids("tag:agent") == set(), "the default should match nothing here (E6)"

    db.notmuch("config", "set", "sessionmail.agent_query", "path:**")
    put(store / "proj", "late@example.invalid", 9)
    db.new()
    assert db.ids("tag:agent") == set(STORE_MAIL) | {"late@example.invalid"}
    assert db.ids("tag:memory") == set(), "agent_query must not move memory_query"


def test_memory_query_tags_only_its_box_in_a_store_rooted_db(tmp_path):
    """`sessionmail.memory_query=path:memory/**`, the store template's form
    (OQ3 still OPEN on where a store's +memory should come from)."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    db, store = store_db(tmp_path)
    db.notmuch("config", "set", "sessionmail.agent_query", "path:**")
    db.notmuch("config", "set", "sessionmail.memory_query", "path:memory/**")
    for n, (msgid, box) in enumerate(STORE_MAIL.items()):
        put(store / box if box else store, msgid, n)
    db.new()
    assert db.ids("tag:memory") == {"mem@example.invalid"}
    assert db.ids("tag:agent") == set(STORE_MAIL)


def test_a_wheel_carries_every_notmuch_file_with_its_mode(tmp_path):
    """Built offline from a scratch copy (never the worktree), as E18 did.
    A regression guard, as design §4 says: `packages` alone ships the dir
    (E18), so the listing cannot tell the `artifacts` edit from its absence;
    the next test pins that edit. Every file under src/sessionmail/notmuch/
    is checked, so do-007/008's two files are covered when they land."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("no uv on PATH to build a wheel with")
    copy = tmp_path / "copy"
    (copy / ".claude-plugin").mkdir(parents=True)
    for rel in ("pyproject.toml", "README.md", "LICENSE", ".claude-plugin/plugin.json"):
        shutil.copy2(ROOT / rel, copy / rel)
    shutil.copytree(ROOT / "src", copy / "src", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    out = tmp_path / "dist"
    # The conftest moves HOME into a sandbox, and with it uv's default cache,
    # which then has no hatchling. Offline, the build reads the USER's uv
    # cache (uv's own store, nothing under ~/.mail), found by the passwd
    # entry since HOME no longer says.
    import pwd
    env = {**os.environ}
    env.setdefault("UV_CACHE_DIR", str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "uv"))
    run = subprocess.run([uv, "build", "--offline", "--wheel", "--out-dir", str(out), str(copy)],
                         capture_output=True, text=True, timeout=120, env=env)
    if run.returncode != 0 and ("network was disabled" in run.stderr or "offline" in run.stderr):
        pytest.skip(f"the build backend is not in uv's cache: {run.stderr.strip()[-200:]}")
    assert run.returncode == 0, run.stderr
    import zipfile
    (wheel,) = out.glob("*.whl")
    with zipfile.ZipFile(wheel) as z:
        listed = {i.filename: (i.external_attr >> 16) & 0o777 for i in z.infolist()}
    shipped = sorted(p for p in (ROOT / "src" / "sessionmail" / "notmuch").rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    assert HOOK in shipped
    # SM-791: the index-at-delivery pair rides in the same wheel, executable.
    shipped += POSTFIX_INSERT_PATH
    for path in shipped:
        name = "sessionmail/" + path.relative_to(ROOT / "src" / "sessionmail").as_posix()
        assert name in listed, f"{name} is not in the wheel"
        assert listed[name] == path.stat().st_mode & 0o777, f"{name}: mode not kept"


#: SM-791's opt-in index-at-delivery files: package data, run by path.
POSTFIX_INSERT_PATH = [ROOT / "src" / "sessionmail" / "postfix" / name
                       for name in ("nm-deliver", "delivery-check")]


def test_both_artifacts_lines_name_the_index_at_delivery_files():
    """The twin of the next test, for SM-791's two postfix files: each is
    named in both artifacts lists, and is executable in the tree."""
    import tomllib
    build = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]
    for path in POSTFIX_INSERT_PATH:
        rel = path.relative_to(ROOT).as_posix()
        assert path.stat().st_mode & 0o111 == 0o111, f"{rel} is not executable"
        for target in ("wheel", "sdist"):
            assert rel in build[target]["artifacts"], (target, rel)


def test_both_artifacts_lines_name_the_notmuch_dir():
    """The precedent's guard (pyproject's own comment): `artifacts` names
    the dir so a later exclude cannot drop it from the wheel silently."""
    import tomllib
    build = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]
    for target in ("wheel", "sdist"):
        assert "src/sessionmail/notmuch" in build[target]["artifacts"], target


# ---------------------------------------------------------------------------
# The opt-in post-new command


def runner_alive(state: Path) -> bool:
    """A runner's argv names its state dir (`perl - <state> ...`)."""
    return subprocess.run(["/usr/bin/pgrep", "-f", str(state)],
                          capture_output=True).returncode == 0


def wait_for(predicate, what: str, limit: float = 30) -> None:
    """Poll until `predicate()` holds. The limit is a failure bound, never
    the thing measured."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"never: {what}")


class Recorder:
    """A command under tmp_path that appends a `start` line (pid, pgid, cwd,
    guard, PATH) and, unless its body exits first, an `end` line to `runs`."""

    def __init__(self, tmp: Path, body: str = "", name: str = "recorder"):
        self.path, self.runs = tmp / name, tmp / f"{name}.runs"
        self.path.write_text(
            "#!/bin/sh\n"
            f'echo "start pid=$$ pgid=$(/bin/ps -o pgid= -p $$ | /usr/bin/tr -d \' \') cwd=$(pwd)'
            f' guard=${{SESSIONMAIL_IN_POST_NEW_COMMAND:-}} path=$PATH" >> {self.runs}\n'
            f"{body}\n"
            f'echo "end pid=$$" >> {self.runs}\n')
        self.path.chmod(0o755)

    def lines(self) -> list[str]:
        return self.runs.read_text().splitlines() if self.runs.exists() else []

    def starts(self) -> list[str]:
        return [line for line in self.lines() if line.startswith("start ")]

    def pgids(self) -> list[int]:
        return [int(m.group(1)) for line in self.starts() if (m := re.search(r"pgid=(\d+)", line))]


def gated(tmp: Path, name: str = "recorder", then: str = "") -> tuple[Recorder, Path]:
    """A recorder that waits on a gate file: it holds the run open for as
    long as the test likes, with no clock in it."""
    gate = tmp / f"{name}.gate"
    return Recorder(tmp, f"while [ ! -e {gate} ]; do /bin/sleep 0.05; done\n{then}", name), gate


def copy_hook(tmp: Path, name: str, *edits: tuple[str, str, int]) -> Path:
    """A copy of the hook with each (pattern, replacement) applied exactly
    once (re.M)."""
    text = HOOK.read_text()
    for pattern, replacement, flags in edits:
        text, n = re.subn(pattern, replacement, text, flags=re.M | flags)
        assert n == 1, f"the hook's {pattern!r} line moved"
    hook = tmp / name
    hook.write_text(text)
    hook.chmod(0o755)
    return hook


@pytest.fixture
def seam(tmp_path, request):
    """A Db for the seam tests, and a finalizer that leaves nothing running:
    it kills every process group a recorder logged and any process whose
    argv names tmp_path (a runner sits in its own session, so a failed test
    would otherwise leave one behind after the suite)."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}: no notmuch for the hook to find")
    if not os.access(PERL, os.X_OK):
        pytest.skip(f"no {PERL}: the seam detaches with it")
    recorders: list[Recorder] = []

    def cleanup():
        for rec in recorders:
            for pgid in rec.pgids():
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        subprocess.run(["/usr/bin/pkill", "-9", "-f", str(tmp_path)], capture_output=True)
        for path in (tmp_path / "home" / ".sessionmail" / "post-new",):
            if path.exists():
                path.chmod(0o755)
    request.addfinalizer(cleanup)

    def make(hook: Path = HOOK) -> Db:
        return Db(tmp_path, hook=hook)
    make.recorders = recorders
    return make


def test_unset_the_hook_is_what_it_was(seam, tmp_path):
    """Opt-in: with the key unset, the hook behaves as before the seam: rc 0,
    nothing on notmuch's stderr, the tags, no file anywhere, and no process.
    "No process" is pinned by a copy whose PERL= is a recording shim; the
    control sets the key on the same copy and the shim is called."""
    called = tmp_path / "perl-called"
    shim = tmp_path / "perl-shim"
    shim.write_text(f"#!/bin/sh\necho \"$@\" >> {called}\n")
    shim.chmod(0o755)
    hook = copy_hook(tmp_path, "post-new-perl-shim", (r"^PERL=\S+$", f"PERL={shim}", 0))
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    db = seam(hook)
    db.env["TMPDIR"] = str(tmpdir)

    db.deliver("alpha", "a@example.invalid")
    run = db.new()
    assert run.returncode == 0 and run.stderr == ""
    db.insert("b@example.invalid")
    assert "agent" in db.tags("a@example.invalid")
    assert "agent" in db.tags("b@example.invalid")
    assert list(db.home.iterdir()) == [], "the unset hook left a file under HOME"
    assert list(tmpdir.iterdir()) == [], "the unset hook left a file under TMPDIR"
    assert not called.exists(), "the unset hook spawned the runner"

    db.set_command(Recorder(tmp_path).path)   # the control: the instrument can see
    db.new()
    assert called.exists(), "the shim never saw the set key's spawn"


def test_set_the_command_runs_once_per_batch(seam, tmp_path):
    """Three mails, one `notmuch new`: one run. The command's environment is
    built from nothing - exactly HOME, launchd's PATH, NOTMUCH_CONFIG and the
    guard - so a variable on the caller (here a marker, and everything else
    in the test's own environment) never reaches it. A perl recorder reads
    the environment, so no shell adds names of its own; cwd is `/` and the
    command leads its own process group."""
    out = tmp_path / "env.runs"
    rec = tmp_path / "env-recorder"
    rec.write_text("#!/usr/bin/perl\n"
                   f'open my $f, ">>", "{out}" or die;\n'
                   'use Cwd; print $f join(" ", $$, getpgrp(), getcwd(), sort keys %ENV), "\\n";\n')
    rec.chmod(0o755)
    db = seam()
    db.env["SM_TEST_MARKER"] = "caller-only"
    db.set_command(rec)
    for i in range(3):
        db.deliver("alpha", f"b{i}@example.invalid")
    run = db.new()
    assert run.returncode == 0 and run.stderr == ""
    db.quiet()

    lines = out.read_text().splitlines()
    assert len(lines) == 1, lines
    pid, pgid, cwd, *names = lines[0].split()
    assert pid == pgid, "the command does not lead its own process group"
    assert cwd == "/"
    assert set(names) == {"HOME", "PATH", "NOTMUCH_CONFIG", "SESSIONMAIL_IN_POST_NEW_COMMAND"}, names
    assert all("agent" in db.tags(f"b{i}@example.invalid") for i in range(3))
    log = (db.state / "log").read_text()
    assert f"start {rec}" in log and "end exit=0" in log, log


def test_the_command_gets_launchds_path_and_the_guard(seam, tmp_path):
    """The values behind D7's names: PATH is launchd's, the guard is 1, and
    HOME and NOTMUCH_CONFIG are the caller's."""
    rec = Recorder(tmp_path, 'echo "home=$HOME config=$NOTMUCH_CONFIG" >> ' + str(tmp_path / "vals"))
    db = seam()
    seam.recorders.append(rec)
    db.set_command(rec.path)
    db.deliver("alpha", "v@example.invalid")
    db.new()
    db.quiet()
    [start] = rec.starts()
    assert f"path={LAUNCHD_PATH}" in start and "guard=1" in start and "cwd=/ " in start, start
    assert (tmp_path / "vals").read_text().strip() == \
        f"home={db.home} config={db.env['NOTMUCH_CONFIG']}"


def test_a_burst_during_a_run_gives_one_more_run(seam, tmp_path):
    """Run 1 is held on a gate; ten inserts each return while it is shut;
    released, exactly one more run follows, and the two never overlap. The
    wrong fix it catches: no re-check after the release strands `again`."""
    rec, gate = gated(tmp_path)
    seam.recorders.append(rec)
    db = seam()
    db.set_command(rec.path)
    db.deliver("alpha", "c0@example.invalid")
    db.new()
    wait_for(lambda: len(rec.starts()) == 1, "run 1 started")
    for i in range(1, 11):
        db.insert(f"c{i}@example.invalid")
        assert not gate.exists()
    assert len(rec.starts()) == 1, "a second run overlapped the first"
    gate.touch()
    db.quiet()
    assert [line.split()[0] for line in rec.lines()] == ["start", "end", "start", "end"], rec.lines()
    assert all("agent" in db.tags(f"c{i}@example.invalid") for i in range(11))


def test_a_failing_command_leaves_the_hook_alone(seam, tmp_path):
    """Exit 7 with text on stderr: the hook still exits 0 with the tags
    applied, notmuch's stderr stays empty, and the log holds both."""
    rec = Recorder(tmp_path, "echo boom-on-stderr >&2; exit 7")
    seam.recorders.append(rec)
    db = seam()
    db.set_command(rec.path)
    db.deliver("alpha", "d@example.invalid")
    run = db.new()
    assert run.returncode == 0 and run.stderr == ""
    db.quiet()
    assert "agent" in db.tags("d@example.invalid")
    log = (db.state / "log").read_text()
    assert "boom-on-stderr" in log and "end exit=7" in log, log


def test_a_hanging_command_never_holds_indexing(seam, tmp_path):
    """The command blocks on a gate; a `new` and an `insert` each return
    while it is shut, rc 0, tags applied. A synchronous run would hold them
    to the subprocess timeout instead."""
    rec, gate = gated(tmp_path)
    seam.recorders.append(rec)
    db = seam()
    db.set_command(rec.path)
    db.deliver("alpha", "e@example.invalid")
    run = db.new()
    assert run.returncode == 0 and run.stderr == ""
    wait_for(lambda: len(rec.starts()) == 1, "the run started")
    db.deliver("alpha", "e1@example.invalid")
    assert db.new().stderr == ""
    run = db.insert("e2@example.invalid")
    assert run.stderr == ""
    assert not gate.exists() and len(rec.lines()) == 1, "the run ended before the gate opened"
    assert all("agent" in db.tags(m) for m in
               ("e@example.invalid", "e1@example.invalid", "e2@example.invalid"))
    gate.touch()
    db.quiet()


def test_a_hung_command_is_killed_at_the_timeout(seam, tmp_path):
    """A copy with POST_NEW_TIMEOUT=1: the first run hangs and is killed (TERM
    to its group), the log says so, and the next arrival runs. Here the time
    is the subject, so this test is the one that waits on a clock."""
    hook = copy_hook(tmp_path, "post-new-short", (r"^POST_NEW_TIMEOUT=\d+$", "POST_NEW_TIMEOUT=1", 0))
    flag = tmp_path / "hung-once"
    rec = Recorder(tmp_path, f"if [ ! -e {flag} ]; then : > {flag}; /bin/sleep 600; fi")
    seam.recorders.append(rec)
    db = seam(hook)
    db.set_command(rec.path)
    db.deliver("alpha", "f@example.invalid")
    db.new()
    db.quiet()
    log = (db.state / "log").read_text()
    assert "end timeout=1s" in log, log
    db.deliver("alpha", "f2@example.invalid")
    db.new()
    db.quiet()
    assert len(rec.starts()) == 2, rec.lines()
    assert (db.state / "log").read_text().count("end exit=0") == 1


def test_the_commands_own_notmuch_new_does_not_refire(seam, tmp_path):
    """The command writes a mail and runs `notmuch new` WITH hooks: its mail
    is tagged by the hook, and the guard keeps the seam from asking for
    another run. Without the guard, runs follow back to back forever."""
    db = seam()
    nested = db.root / "agents" / "alpha" / "new" / "1908540899.M9P9.nested"
    rec = Recorder(tmp_path,
                   f"printf 'From: b@example.invalid\\nTo: a@example.invalid\\nSubject: n\\n"
                   f"Message-ID: <nested@example.invalid>\\n\\nbody\\n' > {nested}\n"
                   f"{NOTMUCH} new >/dev/null 2>&1")
    seam.recorders.append(rec)
    db.set_command(rec.path)
    db.deliver("alpha", "g@example.invalid")
    db.new()
    db.quiet()
    assert len(rec.starts()) == 1, rec.lines()
    assert "end" in rec.lines()[-1]
    assert "agent" in db.tags("nested@example.invalid"), "the command's notmuch new lost the tagging"
    assert not (db.state / "again").exists()


def _caller_in_its_own_group(db: Db) -> None:
    """Run `notmuch new` the way launchd and pipe(8) do: pipes on all three
    fds and a process group of its own. It must return while the command is
    still held (a child that kept a pipe would hold `communicate` to the
    timeout); then its whole group is killed, as launchd does."""
    caller = subprocess.Popen([NOTMUCH, "new"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=db.env, start_new_session=True)
    _, err = caller.communicate(timeout=30)
    assert caller.returncode == 0 and err == b""
    try:
        os.killpg(caller.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_the_command_escapes_the_callers_pipes_and_process_group(seam, tmp_path):
    """The command blocks on a gate; the caller returns while it is shut; the
    caller's group is killed; released, the command still finishes. Wrong
    fixes: no setsid (the command dies with the group); inherited fds (the
    caller waits for EOF)."""
    rec, gate = gated(tmp_path)
    seam.recorders.append(rec)
    db = seam()
    db.set_command(rec.path)
    db.deliver("alpha", "h@example.invalid")
    _caller_in_its_own_group(db)
    assert not gate.exists()
    gate.touch()
    wait_for(lambda: any(line.startswith("end ") for line in rec.lines()), "the command finished")
    db.quiet()
    assert len(rec.starts()) == 1


def test_the_runner_leaves_the_callers_group_before_it_forks(seam, tmp_path):
    """The runner must leave the caller's group in the PARENT, before the
    fork: a child that setsids for itself sits in the caller's group until
    the scheduler runs it, and launchd kills that group as soon as `notmuch
    new` exits. A copy holds the child on a gate right after the fork (no
    clock), the caller's group is killed, the gate opens: the command runs.
    The control moves setsid into the child alone, the order first drafted,
    and the same kill takes the runner with it: no run."""
    hold = tmp_path / "fork.gate"
    pause = (r"^exit 0 if \$pid;$",
             f'exit 0 if $pid;\nselect(undef, undef, undef, 0.02) until -e "{hold}";', 0)
    for name, edits, survives in (
            ("ordered", (pause,), True),
            ("control", (pause, (r"^my \$sid = POSIX::setsid\(\);$", "my $sid = -1;", 0)), False)):
        tmp = tmp_path / name
        tmp.mkdir()
        hold.unlink(missing_ok=True)
        rec = Recorder(tmp)
        seam.recorders.append(rec)
        db = Db(tmp, hook=copy_hook(tmp, "post-new-held", *edits))
        db.set_command(rec.path)
        db.deliver("alpha", f"{name}@example.invalid")
        _caller_in_its_own_group(db)
        if not survives:
            wait_for(lambda: not runner_alive(db.state), "the control's runner was killed")
        hold.touch()
        if survives:
            db.quiet()
            assert len(rec.starts()) == 1, f"{name}: the runner died with the caller's group"
        else:
            assert rec.lines() == [], "control: the kill missed the child, so the test cannot see"
            assert (db.state / "again").exists()


def test_a_relative_command_is_refused_in_the_log(seam, tmp_path):
    """The value is an absolute path to an executable, run with no shell and
    no PATH lookup. A relative value is refused, in the log, and never run:
    a bare word, and the sharper case, the recorder's own path without its
    leading `/`, which names a real file from the runner's cwd `/`."""
    rec = Recorder(tmp_path)
    db = seam()
    for n, value in enumerate(("recorder", str(rec.path).lstrip("/"))):
        assert not value.startswith("/") and (Path("/") / value).exists() == bool(n)
        db.set_command(value)
        db.deliver("alpha", f"r{n}@example.invalid")
        run = db.new()
        assert run.returncode == 0 and run.stderr == ""
        db.quiet()
        assert f"not run: '{value}' is not an absolute path to an executable" in \
            (db.state / "log").read_text()
    assert rec.lines() == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode")
@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/dash"])
def test_an_unwritable_state_dir_is_silent(seam, tmp_path, shell):
    """Key set, state dir mode 555 with no `again`: rc 0, nothing on
    notmuch's stderr, tags applied, no run. Under dash, `: > again` would
    fail the hook with status 2 (a special builtin's failed redirection
    exits a POSIX shell): the hook uses `true`."""
    if not os.access(shell, os.X_OK):
        pytest.skip(f"no {shell}")
    hook = copy_hook(tmp_path, "post-new-shell", (r"\A#!/bin/sh$", f"#!{shell}", 0))
    rec = Recorder(tmp_path)
    db = seam(hook)
    db.set_command(rec.path)
    db.state.mkdir(parents=True)
    db.state.chmod(0o555)
    db.deliver("alpha", "u@example.invalid")
    run = db.new()
    assert run.returncode == 0 and run.stderr == "", run.stderr
    assert "agent" in db.tags("u@example.invalid")
    assert list(db.state.iterdir()) == []
    assert not runner_alive(db.state)
    assert rec.lines() == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode")
def test_an_again_it_cannot_clear_ends_the_runner(seam, tmp_path):
    """Run 1 is held; an insert sets `again`; the state dir goes to 555; on
    release the runner cannot clear `again`, logs why and exits instead of
    spinning. With the mode back, the next arrival runs."""
    rec, gate = gated(tmp_path)
    seam.recorders.append(rec)
    db = seam()
    db.set_command(rec.path)
    db.deliver("alpha", "s0@example.invalid")
    db.new()
    wait_for(lambda: len(rec.starts()) == 1, "run 1 started")
    db.insert("s1@example.invalid")
    db.state.chmod(0o555)
    gate.touch()
    wait_for(lambda: "cannot clear again" in (db.state / "log").read_text(), "the runner logged why")
    wait_for(lambda: not runner_alive(db.state), "the runner exited")
    assert (db.state / "again").exists()
    assert len(rec.starts()) == 1
    db.state.chmod(0o755)
    db.deliver("alpha", "s2@example.invalid")
    db.new()
    db.quiet()
    assert len(rec.starts()) == 2, rec.lines()
