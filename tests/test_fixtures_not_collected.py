"""SM-1063: pytest never imports a file under `tests/fixtures/`, even one
NAMED on its command line, so it never writes `__pycache__/` there.

`tests/fixtures/sig_v2/make_vectors.py` mints the golden vectors beside it.
`pytest tests/fixtures/sig_v2/make_vectors.py` (a gate that built its test
list with `git grep` ran exactly that) imported it and wrote
`__pycache__/make_vectors.cpython-*-pytest-*.pyc` into the vector folder.
`tests/conftest.py`'s `pytest_collect_file` wrapper is the fix: a fixture
file is collected as one empty node, and nothing imports it.

Each test copies the suite (`pyproject.toml` and `tests/`) to a temp dir,
runs a child pytest there, and looks under the copy's `tests/fixtures/`.
The copy's `tests/__pycache__/` must fill (the conftest's own bytecode), so
a child that wrote no bytecode at all cannot pass by accident.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

#: Every Python file under tests/fixtures, as the command line names it.
FIXTURE_SCRIPTS = sorted(p.relative_to(ROOT).as_posix() for p in FIXTURES.rglob("*.py"))

#: Variables that would let a child skip writing bytecode, or write it
#: somewhere else: either would make "no __pycache__" prove nothing.
BYTECODE_VARS = ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTEST_ADDOPTS")

#: A real test for the copy, so a list naming a fixture beside it can be run.
PROBE = "tests/test_probe_collect.py"
PROBE_SOURCE = "def test_probe():\n    pass\n"

#: pytest's exit codes, as `pytest.ExitCode` names them.
OK, NO_TESTS, USAGE = int(pytest.ExitCode.OK), int(pytest.ExitCode.NO_TESTS_COLLECTED), int(pytest.ExitCode.USAGE_ERROR)


def _copy_suite(dest: Path) -> Path:
    dest.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", dest / "pyproject.toml")
    shutil.copytree(ROOT / "tests", dest / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
    (dest / PROBE).write_text(PROBE_SOURCE)
    return dest


def _bytecode_under(path: Path) -> list[str]:
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*")
                  if p.name == "__pycache__" or p.suffix == ".pyc")


def _child_pytest(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in BYTECODE_VARS}
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=300)


def test_the_fixtures_hold_a_script_to_guard():
    """The guard has something to guard: a test over no files proves nothing."""
    assert "tests/fixtures/sig_v2/make_vectors.py" in FIXTURE_SCRIPTS, FIXTURE_SCRIPTS


@pytest.mark.parametrize("script", FIXTURE_SCRIPTS)
@pytest.mark.parametrize(("args", "rc"), [
    pytest.param(("{script}",), NO_TESTS, id="run"),
    pytest.param(("--collect-only", "{script}"), NO_TESTS, id="collect-only"),
    pytest.param(("--doctest-modules", "{script}"), NO_TESTS, id="doctest-modules"),
    pytest.param(("--collect-only", "--doctest-modules", "tests/fixtures"), NO_TESTS,
                 id="fixtures-dir-doctest-modules"),
    pytest.param(("-q", "{script}", PROBE), OK, id="beside-a-real-test"),
])
def test_a_named_fixture_is_never_imported(tmp_path, script, args, rc):
    repo = _copy_suite(tmp_path / "repo")
    run = _child_pytest(repo, *(a.format(script=script) for a in args))
    out = run.stdout + run.stderr

    assert _bytecode_under(repo / "tests" / "fixtures") == [], out
    assert "ERROR collecting" not in out, out
    assert run.returncode == rc, out
    if PROBE in args:
        assert "1 passed" in out, out
    # The child did write bytecode: the conftest's own, beside it.
    assert any(n.startswith("conftest.") for n in os.listdir(repo / "tests" / "__pycache__")), out


def test_a_named_data_file_is_left_to_pytest(tmp_path):
    """The wrapper replaces only what a plugin built. A fixture file no plugin
    collects (a README) is still "not found", exactly as without it."""
    repo = _copy_suite(tmp_path / "repo")
    run = _child_pytest(repo, "tests/fixtures/sig_v2/README.md")
    out = run.stdout + run.stderr

    assert run.returncode == USAGE, out
    assert "not found" in out, out
    assert _bytecode_under(repo / "tests" / "fixtures") == [], out
