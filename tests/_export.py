"""The release export, as the suite sees it (SM-849, SM-857).

`.gitattributes` export-ignores `CLAUDE.md` and `openspec/`: `git archive`,
and so the release export, leaves them out. The suite runs in three shapes,
and a test that reads an export-ignored file goes through `export_ignored()`:

- the private checkout (or a worktree): the file is TRACKED, so it must be
  there. No skip: a missing file fails loudly on the read, and the test can
  never pass vacuously;
- the tarball (`make test-export`, `git archive | tar -x`): no git at all.
  The file is absent by design: the test SKIPS, and the reason names the
  export;
- a git clone of the export: `.git` is present, but the export never held
  the file, so it is not tracked there. Absent and untracked: it SKIPS too.

So the rule is never keyed on `.git` alone (SM-857: a clone has one). SKIP
only when the file is ABSENT and NOT TRACKED in this checkout (`git ls-files
--error-unmatch` fails, or there is no git or no repo). `in_checkout()` is
kept for the callers that only need a working git (`check-attr`, `ls-files`,
`archive`), which a clone has as much as the private checkout does.

`.git` is a FILE in a worktree; `.exists()` is true for it either way.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def in_checkout() -> bool:
    """True in a git checkout (or worktree, or a clone of the export); False
    in the tarball. For callers that need a working git, not a file."""
    return (ROOT / ".git").exists()


def tracked(rel: str) -> bool:
    """True when REL (a file, or a directory holding one) is in this
    checkout's index, whether or not it is on disk: a tracked file that was
    deleted is still tracked. False with no git, no repo, or a path the index
    never held, as in a clone of the export."""
    if shutil.which("git") is None:
        return False
    run = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", rel],
                         capture_output=True)
    return run.returncode == 0


def export_absent(rel: str) -> bool:
    """True when ROOT/REL is left out by the export: absent AND untracked.
    A tracked file that is missing is not the export's absence; it is broken."""
    return not (ROOT / rel).exists() and not tracked(rel)


def export_reason(rel: str) -> str:
    return f"{rel} is export-ignored: absent in the git-archive release export"


def export_ignored(rel: str) -> Path:
    """ROOT/REL, a path `.gitattributes` export-ignores. SKIP when it is
    absent and untracked (the tarball, or a clone of the export); otherwise
    hand it back, so a checkout that tracks the file but lost it still fails
    on the read."""
    if export_absent(rel):
        pytest.skip(export_reason(rel))
    return ROOT / rel


def ignored_by_export(paths: Iterable[Path]) -> set[Path]:
    """Those of PATHS (inside ROOT) that `git archive` leaves out, by
    `git check-attr export-ignore`. In the tarball there is no `.git` to ask:
    an export-ignored target is simply absent there, which `exists()` sees.
    A clone of the export answers from its own `.gitattributes`."""
    inside = sorted({p for p in paths if p.is_relative_to(ROOT) and p != ROOT})
    if not inside or not in_checkout() or shutil.which("git") is None:
        return set()
    rels = [str(p.relative_to(ROOT)) for p in inside]
    out = subprocess.run(["git", "-C", str(ROOT), "check-attr", "export-ignore", "--", *rels],
                         check=True, capture_output=True, text=True).stdout
    ignored = set()
    for line in out.splitlines():
        rel, _, rest = line.partition(": export-ignore: ")
        if rest.strip() == "set":
            ignored.add(ROOT / rel)
    return ignored
