"""SM-1028: a test's CHILDREN ask the leaf resolver the suite's pin asked,
whatever `python3` and `session` the runner's PATH reaches first.

`hooks/mail-guard-hook` runs `python3` from PATH, and `hook.py` finds the
session dir by the sessiontools that python3 imports, else by `session` from
PATH, else by its own copy of the leaf rule. `tests/_leaves.py` pins the answer
of the sessiontools the SUITE imports. A runner that ran
`.venv/bin/python -m pytest` with a bare interpreter first on PATH had the
hook child fall through to a newer `session` on PATH, which answered the NEW
tree where the venv's 0.3.2 answers the OLD one on a fresh HOME, and 96
tests failed with "the sessions leaf moved". `uv run pytest`, which puts
`.venv/bin` first, passed the same files.

The fix is the conftest's `interpreter_path`: every test's PATH starts with
this interpreter's bin. The last test here is the regression net: it runs
two of the 96 in a nested pytest whose PATH is HOSTILE (first `python3` one
that cannot import sessiontools, first `session` a stub answering the NEW
tree), and they must stay green.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _leaves
from conftest import interpreter_path
from fakes import write_fake

REPO = Path(__file__).resolve().parents[1]

#: The leaf resolver as a child asks it: PATH's python3, sessiontools imported.
ASK = ("import os\nfrom sessiontools import settings\n"
       "print(settings.sessions_root(os.environ))\n")

#: A `python3` that cannot import sessiontools: this interpreter with no
#: site-packages (`-S`). `hook.py` is stdlib only, so the guard still runs.
BARE_PYTHON3 = '#!/bin/sh\nexec "{python}" -S "$@"\n'

#: A `session` that answers `path <leaf> <name> --json` with the NEW tree, as
#: a newer sessiontools does on a fresh HOME.
#: Braces doubled: `write_fake` formats the source.
NEW_TREE_SESSION = """#!/bin/sh
[ "$1" = path ] && [ "$4" = --json ] || exit 2
printf '{{"path": "%s/.sessionkit/tools/state/%s/%s", "source": "fresh-new"}}\\n' "$HOME" "$2" "$3"
"""

#: Two of the 96, one per guard suite: each writes a deny row into the session
#: dir and reads it back through `_leaves`, so a child that resolved another
#: tree fails with "the sessions leaf moved".
VICTIMS = (
    "tests/test_mail_guard_subagent.py::test_the_deny_is_logged_like_any_other",
    "tests/test_mail_guard_subagent_mcp.py::test_the_deny_is_logged_like_any_other",
)


def _first_python3(path: str) -> Path:
    for d in path.split(os.pathsep):
        if d and (Path(d) / "python3").is_file():
            return Path(d) / "python3"
    raise AssertionError(f"no python3 on PATH: {path!r}")


def test_a_childs_python3_asks_the_resolver_the_pin_asked():
    """The suite's own PATH: a child's `python3` resolves the leaf as the pin did."""
    out = subprocess.run(["python3", "-c", ASK], capture_output=True, text=True,
                         timeout=60, check=True).stdout.strip()
    assert Path(out) == _leaves.sessions_root()


def test_the_suites_path_starts_with_this_interpreter():
    """The first `python3` on a test's PATH runs THIS interpreter (directly,
    or through the conftest's one-line shim)."""
    first = _first_python3(os.environ["PATH"])
    if not os.path.samefile(first, sys.executable):
        assert first.read_text() == f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'


def test_a_bin_without_this_python3_gets_a_shim_first(tmp_path, monkeypatch):
    """No `python3` beside the interpreter, or another one there: the shim
    dir goes first and its `python3` execs this interpreter."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "python3.99"
    exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(exe))
    shims = tmp_path / "shims"
    assert interpreter_path(shims, "/x:/y").split(os.pathsep) == [str(shims), str(bin_), "/x", "/y"]
    assert (shims / "python3").read_text() == f'#!/bin/sh\nexec "{exe}" "$@"\n'
    assert os.access(shims / "python3", os.X_OK)
    (bin_ / "python3").write_text("")          # another interpreter's python3
    assert interpreter_path(tmp_path / "s2", "").split(os.pathsep) == [str(tmp_path / "s2"), str(bin_)]


def test_a_bin_whose_python3_is_this_interpreter_needs_no_shim(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "python"
    exe.write_text("")
    (bin_ / "python3").symlink_to("python")    # a venv's shape
    monkeypatch.setattr(sys, "executable", str(exe))
    shims = tmp_path / "shims"
    assert interpreter_path(shims, "/x").split(os.pathsep) == [str(bin_), "/x"]
    assert not shims.exists()


def test_the_guard_suites_stay_green_under_a_hostile_path(tmp_path):
    """The regression: a runner whose first `python3` lacks sessiontools and
    whose first `session` answers the NEW tree. Red before SM-1028 (the hook
    child took the stub's answer: "the sessions leaf moved"), green after."""
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    write_fake(hostile / "python3", BARE_PYTHON3)
    write_fake(hostile / "session", NEW_TREE_SESSION)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PATH"] = os.pathsep.join([str(hostile), os.environ["PATH"]])
    # The rig is what it says: that python3 cannot import sessiontools, and
    # that session answers the NEW tree where the pin answered another.
    probe = subprocess.run(["python3", "-c", "import sessiontools"], env=env,
                           capture_output=True, text=True, timeout=60)
    assert probe.returncode != 0, "the hostile python3 imported sessiontools"
    said = subprocess.run(["session", "path", "sessions", "sid-x", "--json"], env=env,
                          capture_output=True, text=True, timeout=60, check=True).stdout
    assert ".sessionkit/tools/state/sessions/sid-x" in said and '"fresh-new"' in said
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          *VICTIMS], cwd=REPO, env=env, capture_output=True, text=True,
                         timeout=600)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    assert f"{len(VICTIMS)} passed" in run.stdout, run.stdout[-2000:]
