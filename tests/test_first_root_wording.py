"""SM-1027 (ruled B1): every refusal a stranger meets first names the recipe.

`memory init` refuses on every box today (exit 3: the box key has no home),
so the first lines a fresh seat sees must say plainly that memory has no
automated first step yet, and point at the by-hand recipe by its doc path.
Exit codes are unchanged; only the words move.

The wording lives twice: `_root.NO_FIRST_STEP` (the package) and the same
sentence in `mhs/mhs-thread` (a script run by path). This file pins the two
equal, pins that the doc they name exists and that the memory skill points
at it, and pins `memory ls /`'s exit 1 ("no agent") and exit 2 ("no memory
yet") lines. init's own exit 2 and exit 3 lines are pinned beside their
other assertions in tests/test_root.py.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

from sessionmail import _mhs, _root

REPO = Path(__file__).resolve().parents[1]
THREAD = REPO / "src" / "sessionmail" / "mhs" / "mhs-thread"

STUB = """#!/usr/bin/env bash
case "$*" in
  *show*) echo "[]" ;;
esac
exit 0
"""


def _thread_constant() -> str:
    """mhs-thread's NO_FIRST_STEP, read from the script's own source."""
    tree = ast.parse(THREAD.read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "NO_FIRST_STEP"):
            return ast.literal_eval(node.value)
    raise AssertionError("mhs-thread has no NO_FIRST_STEP constant")


def test_the_script_and_the_package_say_the_same_words():
    assert _thread_constant() == _root.NO_FIRST_STEP


def test_the_wording_says_no_first_step_and_names_the_doc():
    assert "no automated first step yet" in _root.NO_FIRST_STEP
    assert _root.FIRST_ROOT_DOC in _root.NO_FIRST_STEP
    # It must not send a stranger to the verb that refuses on every box.
    assert "memory init" not in _root.NO_FIRST_STEP


def test_the_named_doc_exists_and_carries_the_recipe():
    doc = REPO / _root.FIRST_ROOT_DOC
    assert doc.is_file(), f"{_root.FIRST_ROOT_DOC} is named by every refusal; it must ship"
    text = doc.read_text()
    blocks = "\n".join(re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M))
    # repro-001's four traps, each answered by a command the reader runs.
    assert "message setup --box claude" in blocks                       # (a) the box
    assert "export CLAUDE_ROOT=" in blocks and "-s '[ROOT]'" in blocks   # (b) root + hint
    assert '"claude@$(hostname -s)"' in blocks                           # (c) qualified To
    assert "CLAUDE_SESSION_AGENT_SOCK" in blocks and "CLAUDE_SESSION_PUB" in blocks  # (d) key
    assert "memory ls /" in blocks


def test_the_memory_skill_points_at_the_recipe():
    assert _root.FIRST_ROOT_DOC in (REPO / "skills" / "memory" / "SKILL.md").read_text()


def _seat(tmp: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "notmuch").write_text(STUB)
    (bin_dir / "notmuch").chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in (_mhs.ORACLE_VAR, "CLAUDE_ROOT", "CLAUDE_CODE_AGENT", "SESSION_AGENT")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", SESSIONMAIL_SENDMAIL="drop",
               SESSIONMAIL_VMAILBOX_GEN="/usr/bin/true", **extra)
    return env


def _ls(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sessionmail.memory", "ls", "/"],
                          capture_output=True, text=True, env=env, timeout=120)


def test_no_agent_is_still_exit_1_and_names_the_recipe(tmp_path):
    """A seat with only a session id (repro-001 09-A) meets this line first."""
    p = _ls(_seat(tmp_path, CLAUDE_CODE_SESSION_ID="00000000-0000-4000-8000-0000000f1a01"))
    assert p.returncode == 1, p.stderr
    assert "memory: no agent" in p.stderr, p.stderr
    assert _root.NO_FIRST_STEP in p.stderr, p.stderr


def test_no_memory_yet_is_still_exit_2_and_names_the_recipe(tmp_path):
    p = _ls(_seat(tmp_path, CLAUDE_CODE_AGENT="claude"))
    assert p.returncode == 2, p.stderr
    (line,) = p.stderr.splitlines()
    assert line.startswith("memory: no memory yet for claude: "), line
    assert line.endswith(_root.NO_FIRST_STEP), line
