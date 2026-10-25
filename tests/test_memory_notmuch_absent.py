"""SM-928 fix-001: `memory ls | tree | cat` with notmuch ABSENT.

Before the fix all three died in mhs-thread's `sh()` with an unhandled
FileNotFoundError for 'notmuch': a Python traceback on stderr and rc 1
(measured at sessionmail 7cbbcea). Now:

- through the `memory` door, `_mhs.run` checks for the binary before the
  bundled oracle starts. It prints ONE stderr line naming notmuch and exits
  `_mhs.EX_REFUSED` (2), the as-built "the index cannot be used" refusal,
  which shares 2 with "path not found". The words tell the two apart;
- run by path (`mhs-thread`, or a `session-mail-ls|tree|cat` shim), the
  reader's own `sh()` says the same thing and exits 2, with no traceback;
- `wake` is unchanged: it DEGRADES (COULD NOT LOOK) and exits 0;
- `memory run`'s exit is unchanged. Its exit table is held for a ruling (fix-002).

notmuch is hidden by PATH only, never uninstalled. A scratch HOME would not
hide it (SM-923). Each test builds a PATH with no `notmuch` on it and
asserts that it holds, so a host with notmuch in /usr/bin is still covered.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import _mhs

READERS = ("ls", "tree", "cat")
ARGS = {"ls": ["/"], "tree": ["/"], "cat": ["/BOOT/latest"]}

#: What the bundled shims and mhs-thread need from PATH, apart from notmuch.
NEEDED = ("bash", "env", "realpath", "dirname")


def hidden_path(tmp: Path) -> str:
    """This PATH without every directory that holds a `notmuch`. A tool in
    NEEDED that lived only in such a directory is linked into a shim dir,
    so the bash shims still start. Asserted: no notmuch is reachable."""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    keep = [d for d in dirs if not os.access(os.path.join(d, "notmuch"), os.X_OK)]
    shim = tmp / "shim-bin"
    shim.mkdir(exist_ok=True)
    for tool in NEEDED:
        if shutil.which(tool, path=os.pathsep.join(keep)) is None:
            found = shutil.which(tool)
            assert found, f"{tool} is needed to start the shims"
            (shim / tool).symlink_to(found)
    path = os.pathsep.join([str(shim), *keep])
    assert shutil.which("notmuch", path=path) is None, "notmuch must be hidden"
    return path


def env_without_notmuch(tmp: Path, **extra: str) -> dict[str, str]:
    """The conftest's sandbox env (scratch HOME, XDG, the host-mode profile),
    with a seat and a PATH that has no notmuch on it."""
    env = {k: v for k, v in os.environ.items() if k != _mhs.ORACLE_VAR}
    env.update(PATH=hidden_path(tmp), CLAUDE_CODE_AGENT="alpha", **extra)
    return env


def run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=120)


def assert_the_notmuch_refusal(line: str, verb: str) -> None:
    """The one line: names notmuch, says it is not installed, and is not the
    resolver's "has no child" / "AMBIGUOUS" (the other 2s)."""
    assert "notmuch" in line
    assert "not installed" in line, line
    assert "has no child" not in line and "AMBIGUOUS" not in line
    assert verb in line, line


# --- through the memory door: refused before the oracle starts --------------


@pytest.mark.parametrize("verb", READERS)
def test_memory_reader_with_notmuch_hidden_says_so_once_and_exits_2(tmp_path, verb):
    p = run([sys.executable, "-m", "sessionmail.memory", verb, *ARGS[verb]],
            env_without_notmuch(tmp_path))
    assert "Traceback" not in p.stderr, p.stderr
    assert p.returncode == _mhs.EX_REFUSED == 2, p.stderr
    assert p.stdout == ""
    (line,) = p.stderr.splitlines()
    assert line.startswith(f"memory {verb}: "), line
    assert_the_notmuch_refusal(line, verb)


@pytest.mark.parametrize("verb", READERS)
def test_run_refuses_a_bundled_reader_before_it_starts(tmp_path, capfd, verb):
    """`_mhs.run` itself: the bundled oracle never starts (the shim would
    have printed mhs-thread's own root-hint notice first)."""
    env = env_without_notmuch(tmp_path)
    assert _mhs.run([*_mhs.oracle(verb, env), *ARGS[verb]], env=env) == _mhs.EX_REFUSED
    out = capfd.readouterr()
    assert out.out == ""
    (line,) = out.err.splitlines()
    assert_the_notmuch_refusal(line, verb)


@pytest.mark.parametrize("verb", READERS)
def test_a_named_oracle_is_not_checked_for_notmuch(tmp_path, capfd, verb):
    """$SESSIONMAIL_MHS_ORACLE names a substitute, which need not call
    notmuch at all: the check is for the bundled reader only."""
    fake = str(write_fake(tmp_path / "fake-mhs",
                          "#!{python}\nimport sys\nprint('oracle ran', sys.argv[1])\n"))
    env = env_without_notmuch(tmp_path, **{_mhs.ORACLE_VAR: fake})
    assert _mhs.run([fake, verb, *ARGS[verb]], env=env) == 0
    out = capfd.readouterr()
    assert out.out == f"oracle ran {verb}\n"
    assert "notmuch" not in out.err


# --- run by path: the reader's own guard ------------------------------------


@pytest.mark.parametrize("verb", READERS)
@pytest.mark.parametrize("how", ["mhs-thread", "shim"])
def test_the_reader_run_by_path_says_so_and_exits_2(tmp_path, verb, how):
    if how == "mhs-thread":
        argv = [sys.executable, os.path.join(_mhs.MHS_DIR, "mhs-thread"), verb]
    else:
        argv = [os.path.join(_mhs.MHS_DIR, f"session-mail-{verb}")]
    # The shim takes the package's interpreter from here, as `_mhs` hands it.
    env = env_without_notmuch(tmp_path, **{_mhs.PYTHON_VAR: sys.executable})
    p = run([*argv, *ARGS[verb]], env)
    assert "Traceback" not in p.stderr, p.stderr
    assert p.returncode == 2, p.stderr
    last = p.stderr.splitlines()[-1]
    assert last.startswith("memory: "), last
    assert_the_notmuch_refusal(last, verb)
    assert sum("notmuch" in ln and "not installed" in ln for ln in p.stderr.splitlines()) == 1


# --- unchanged: wake degrades, run's exit is held (fix-002) -----------------


def test_wake_with_notmuch_hidden_still_degrades_and_exits_0(tmp_path):
    root = tmp_path / "mail"
    for sub in ("new", "cur", "tmp"):
        (root / "alpha" / sub).mkdir(parents=True)
    p = run([sys.executable, "-m", "sessionmail.memory", "wake", "alpha"],
            env_without_notmuch(tmp_path, SESSION_MAIL_ROOT=str(root)))
    assert p.returncode == 0, p.stderr
    assert "COULD NOT LOOK" in p.stdout, p.stdout
    assert "not installed" not in p.stderr, "wake keeps its own degrade notice"


def test_run_list_exit_is_unchanged_with_notmuch_hidden(tmp_path):
    """Held for a ruling (fix-002): today `--list` falls back to DEFAULT and exits 0."""
    p = run([sys.executable, "-m", "sessionmail.memory", "run", "/BOOT/WAKE", "--list"],
            env_without_notmuch(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
