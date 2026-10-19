"""SM-866: the runner's git config never reaches a test.

A seat exports GIT_CONFIG_COUNT with GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n
(Claude Code forces commit.gpgsign=true through the session signer), and a
stranger's global config may carry a refusing core.hooksPath. Either one
turned every test that commits in a temp repo red, for a reason that is not
the code. `tests/conftest.py`'s autouse `hermetic` fixture removes the first
and points GIT_CONFIG_GLOBAL at /dev/null (GIT_CONFIG_NOSYSTEM=1) for the
second. This file proves both: in-process, and by running the two files that
went red in a child pytest whose PARENT env carries the hostile config.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import hostile_git

ROOT = Path(__file__).resolve().parents[1]


def committing_tests() -> list[str]:
    """Every node id that commits in a temp repo: all of test_record.py, and
    each test in test_init_mint.py that takes the `store` fixture or calls
    `_repo(` (the 1 failure + 10 errors measured under the hostile global at
    034fbe4 were all among these). Found by reading the source, so a new
    committing test is picked up without editing this list."""
    path = ROOT / "tests" / "test_init_mint.py"
    src = path.read_text()
    ids = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            if "store" in [a.arg for a in node.args.args] or \
                    "_repo(" in (ast.get_source_segment(src, node) or ""):
                ids.append(f"tests/test_init_mint.py::{node.name}")
    assert len(ids) >= 11, ids
    return ["tests/test_record.py", *ids]


def test_no_git_config_from_the_runner_reaches_a_test():
    assert not [v for v in os.environ
                if v in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
                or v.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]
    assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.parametrize("shape", ["count-gpgsign", *hostile_git.SHAPES])
def test_the_files_that_commit_pass_under_a_hostile_parent_env(tmp_path, shape):
    """The README's receipt: with GIT_CONFIG_COUNT=1, GIT_CONFIG_KEY_0=
    commit.gpgsign, GIT_CONFIG_VALUE_0=true in the parent env (and with the
    GC's hostile global, and with every hostile key as COUNT entries),
    test_record.py and test_init_mint.py's committing tests pass. The rig is
    shown hostile first: a plain commit under the same env is refused.

    The child runs ALL of test_record.py but only the tests in
    test_init_mint.py that commit (`committing_tests`), on purpose: a test
    that never runs git cannot read the runner's git config, and the whole
    file three times over would add its 24s run to each child for nothing
    the subset does not show. The rest of test_init_mint.py is covered by the
    full suite run under the hostile global, which is this task's receipt."""
    if shape == "count-gpgsign":
        hostile = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "commit.gpgsign",
                   "GIT_CONFIG_VALUE_0": "true",
                   "GIT_CONFIG_GLOBAL": str(tmp_path / "no-such-global")}
    else:
        hostile = hostile_git.hostile_env(tmp_path, shape)
    env = {**os.environ, **hostile}
    if shape != "count-gpgsign":   # the gpgsign-only shape signs; it need not refuse
        hostile_git.plain_commit_is_refused(tmp_path, env)
    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *committing_tests()],
        cwd=ROOT, capture_output=True, text=True, env=env,
        timeout=300)   # pre-fix, the gpgsign shape sat 7m45s on the signer (measured)
    tail = "\n".join(child.stdout.splitlines()[-15:])
    assert child.returncode == 0, tail
    assert " passed" in tail and " failed" not in tail and " error" not in tail, tail
