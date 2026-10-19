"""`memory ls|tree|cat|run|wake` - the click door, the bash MHS verbs as oracle.

Hermetic: $SESSIONMAIL_MHS_ORACLE names a fake (full-path shebang) that
logs the argv it got, echoes a line, and exits as told. Pinned: each verb's
authored flags translate to the bash ones (`--id` -> `--resolve`, `--full`
-> `--bodies`), stdout is the oracle's, the exit code is the oracle's.
`--box` is CARRIED on the two read-only listings (`ls`, `tree`), whose bash
already takes it, and still refused on `cat` and `run`: `run --box` must
never execute another box's script-mail.
"""

from __future__ import annotations

import stat
import sys

import pytest
from click.testing import CliRunner

from sessionmail.memory import cli

FAKE = """#!{python}
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
sys.stdout.write("oracle says: " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_FAIL") or 0))
"""


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    fake = tmp_path / "fake-mhs"
    fake.write_text(FAKE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(fake))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_FAIL", raising=False)

    class O:
        def calls(self):
            return [l.split("\x1f") for l in log.read_text().splitlines()] if log.exists() else []
    return O()


def run(*args):
    return CliRunner().invoke(cli, list(args))


@pytest.mark.parametrize("argv, expect", [
    (["ls"], ["ls"]),
    (["ls", "/ASKS", "--all"], ["ls", "/ASKS", "--all"]),
    (["tree", "/RULES", "--depth", "1", "--all", "--dates"], ["tree", "/RULES", "--depth", "1", "--all", "--dates"]),
    (["cat", "/BOOT/latest"], ["cat", "/BOOT/latest"]),
    (["cat", "/BOOT/latest", "--id", "--full", "--all"], ["cat", "/BOOT/latest", "--all", "--resolve", "--bodies"]),
    (["run", "/BOOT/WAKE", "--list", "--only", "10,15", "--json"], ["run", "/BOOT/WAKE", "--list", "--only", "10,15", "--json"]),
    (["wake"], ["wake"]),
    (["wake", "eve", "--dry-run"], ["wake", "eve", "--dry-run"]),
])
def test_each_verb_translates_its_flags_onto_the_oracle(oracle, argv, expect):
    r = run(*argv)
    assert r.exit_code == 0, r.output
    assert oracle.calls() == [expect]


def test_the_oracles_exit_code_is_the_doors(oracle, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "3")
    assert run("cat", "/nowhere").exit_code == 3
    monkeypatch.setenv("FAKE_FAIL", "2")
    monkeypatch.setenv("SESSIONMAIL_MEMORY_RUN", "1")   # SM-780: run is opt-in
    assert run("run", "/BOOT/WAKE").exit_code == 2


@pytest.mark.parametrize("argv, expect", [
    (["ls", "--box", "acmecorp+gc"], ["ls", "--box", "acmecorp+gc"]),
    (["ls", "/ASKS", "--all", "--box", "eve"], ["ls", "/ASKS", "--all", "--box", "eve"]),
    (["tree", "/ASKS", "--depth", "1", "--box", "eve"], ["tree", "/ASKS", "--depth", "1", "--box", "eve"]),
])
def test_box_is_carried_on_the_listings(oracle, argv, expect):
    r = run(*argv)
    assert r.exit_code == 0, r.output
    assert oracle.calls() == [expect]


def test_box_is_still_refused_on_cat_not_dropped(oracle):
    r = run("cat", "/BOOT/latest", "--box", "eve")
    assert r.exit_code == 2 and "not carried" in r.output and oracle.calls() == []


def test_run_has_no_box_at_all(oracle):
    # another box's script-mail must never execute here: click refuses
    # the flag before the oracle is reached
    r = run("run", "/BOOT/WAKE", "--box", "eve")
    assert r.exit_code == 2 and oracle.calls() == []


def test_no_oracle_is_a_loud_2(oracle, monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(tmp_path / "missing"))
    r = run("ls")
    assert r.exit_code == 2 and "memory ls" in r.output
