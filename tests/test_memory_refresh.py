"""SM-996: the `memory` verbs refresh the host index before their oracle runs.

`_mhs.run` is the one door for `memory wake | ls | tree | cat | run`. In host
mode it runs `_fresh.refresh` (a bounded `notmuch new --no-hooks`) BEFORE the
oracle starts, because these readers cannot tell a stale answer from a true
empty one. A named oracle and a fake engine log to ONE file, so the order is
measured: `new` first, then the oracle, once each.

Not refreshed: `wake --dry-run` (it runs no index call), `verify` (it reads
no index), a refused profile, store mode, and `notmuch.refresh: 0`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _fresh, _index, _mhs, _notmuch
from sessionmail.memory import cli as memory_cli

#: The engine: logs `notmuch <args>` (profile stripped), answers `[]`.
ENGINE = """#!{python}
import os, sys
args = sys.argv[1:]
if args and args[0].startswith("--config="):
    args = args[1:]
with open(os.environ["ORDER_LOG"], "a") as log:
    log.write("notmuch " + " ".join(args) + "\\n")
sys.stdout.write("[]")
"""

#: The named oracle: logs `oracle <verb> <args>`, prints a line, exits 0.
ORACLE = """#!{python}
import os, sys
with open(os.environ["ORDER_LOG"], "a") as log:
    log.write("oracle " + " ".join(sys.argv[1:]) + "\\n")
print("oracle ran")
"""


@pytest.fixture
def order(tmp_path, monkeypatch):
    """Host mode with a profile file, the logging engine and the logging
    oracle. Returns a function giving the log's lines."""
    log = tmp_path / "order.log"
    monkeypatch.setenv("ORDER_LOG", str(log))
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(write_fake(tmp_path / "notmuch", ENGINE)))
    monkeypatch.setenv(_mhs.ORACLE_VAR, str(write_fake(tmp_path / "oracle", ORACLE)))
    monkeypatch.setenv(_mhs.RUN_GATE_VAR, "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(_index.VAR, raising=False)
    monkeypatch.delenv(_fresh.VAR, raising=False)
    profile = _index.profile_config()
    profile.parent.mkdir(parents=True)
    profile.write_text("[database]\npath=/nonexistent\n")
    _index.clear_cache()
    assert _index.resolve().host
    yield lambda: log.read_text().splitlines() if log.exists() else []
    _index.clear_cache()


VERBS = {
    "wake": ["wake", "alpha"],
    "ls": ["ls"],
    "tree": ["tree", "/"],
    "cat": ["cat", "/1"],
    "run": ["run", "/BOOT"],
}


def memory(*argv: str):
    return CliRunner().invoke(memory_cli, list(argv), prog_name="memory")


@pytest.mark.parametrize("argv", VERBS.values(), ids=VERBS.keys())
def test_each_reader_refreshes_once_before_its_oracle(order, argv):
    r = memory(*argv)
    assert r.exit_code == 0, r.output + r.stderr
    lines = order()
    assert lines[0] == "notmuch new --no-hooks --quiet", lines
    assert [ln for ln in lines if ln.startswith("notmuch new")] == [lines[0]]
    assert lines[1].startswith(f"oracle {argv[0]}"), lines


def test_wake_dry_run_does_not_refresh(order):
    r = memory("wake", "alpha", "--dry-run")
    assert r.exit_code == 0, r.output + r.stderr
    assert order() == ["oracle wake alpha --dry-run"]


def test_verify_does_not_refresh(order):
    """`verify` reads no index (NO_INDEX): its door is never refreshed."""
    code = _mhs.run([os.environ[_mhs.ORACLE_VAR], "verify", "x"])
    assert code == 0
    assert order() == ["oracle verify x"]


def test_refresh_off_runs_no_new(order, monkeypatch):
    monkeypatch.setenv(_fresh.VAR, "0")
    r = memory("ls")
    assert r.exit_code == 0, r.output + r.stderr
    assert order() == ["oracle ls"]


def test_a_refused_profile_does_not_refresh(order):
    _index.profile_config().unlink()
    _index.clear_cache()
    r = memory("ls")
    assert r.exit_code == _mhs.EX_REFUSED
    assert order() == []
    r = memory("wake", "alpha")                 # DEGRADES: runs, refused config, no refresh
    assert order() == ["oracle wake alpha"]


def test_store_mode_does_not_refresh(order, monkeypatch, tmp_path):
    home = Path(os.environ["HOME"])
    (home / ".notmuch-config.somestore").write_text("[database]\npath=/nonexistent\n")
    monkeypatch.setenv(_index.VAR, _index.STORE)
    monkeypatch.setenv("NOTMUCH_PROFILE", "somestore")
    _index.clear_cache()
    r = memory("ls")
    assert r.exit_code == 0, r.output + r.stderr
    assert order() == ["oracle ls"]


FAILING_ENGINE = """#!{python}
import os, sys
args = sys.argv[1:]
if args and args[0].startswith("--config="):
    args = args[1:]
with open(os.environ["ORDER_LOG"], "a") as log:
    log.write("notmuch " + " ".join(args) + "\\n")
if args[:1] == ["new"]:
    sys.stderr.write("Error: the database is on fire\\n")
    sys.exit(1)
sys.stdout.write("[]")
"""


def test_a_failed_refresh_says_so_once_and_the_reader_still_reads(order, tmp_path, monkeypatch):
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(write_fake(tmp_path / "bad", FAILING_ENGINE)))
    r = memory("ls")
    assert r.exit_code == 0, r.output + r.stderr
    assert "oracle ran" in r.output
    notes = [ln for ln in r.stderr.splitlines() if "index refresh" in ln]
    assert len(notes) == 1, r.stderr
    assert notes[0].startswith("memory ls: index refresh: ")
    assert "Error: the database is on fire" in notes[0]
    assert "reading the index as it was" in notes[0]
    assert order()[-1].startswith("oracle ls")
