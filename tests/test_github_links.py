"""No shipped file links into this repo by URL, or pins a sessionkit link
to a branch.

The docs are read in an editor or offline, so a link to a file in this repo
is a relative path: a full GitHub URL to it (`.../sessionmail/blob/...`,
`/tree/`, `/raw/`) fails here. A link to a file in another sessionkit
repository stays a full URL, and its ref is `HEAD`, which GitHub resolves to
the default branch, or a release tag (`v1.2.3`): never a branch name, which
a rename breaks. A repository's own URL, with no `/blob/`, is always fine.
A clone that names the common default branch with `-b` / `--branch` fails
too: the release check greps the archive for it.

"Shipped" is what the release archive keeps: in a git checkout (or a clone
of the export), the tracked files, and new ones git does not ignore, less
those `.gitattributes` export-ignores; in the tarball, which has no git,
every file in the tree but the generated ones (caches, the venv, build
output). The test runs in both shapes and never skips.

This repo's URL is held in REPO and the branch names in BRANCHES, and every
pattern and fixture is built from them, so this file does not match its own
check, nor the release greps that run over the archive.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from _export import ignored_by_export

ROOT = Path(__file__).resolve().parent.parent

#: This repository on GitHub.
REPO = "github.com/sessionkit/" + "sessionmail"
#: Branch names a link might carry: the public one, and the common default.
BRANCHES = ["master", "main"]

#: A full URL to a file or directory in this repository: it must be relative.
SELF_URL = re.compile(re.escape(REPO) + r"/(?:blob|tree|raw)/")
#: A sessionkit repository's file or directory URL, capturing its ref.
REF_URL = re.compile(r"github\.com/sessionkit/[^/\s]+/(?:blob|tree|raw)/([^/\s#?)>\]\"'`]+)")
#: The refs a cross-repo link may carry: HEAD, or a release tag.
GOOD_REF = re.compile(r"HEAD|v\d[\w.+-]*")
#: A clone or checkout that asks for the common default branch by name: the
#: release check greps the archive for it.
BRANCH_FLAG = re.compile(rf"(?:-b|--branch)[ =]{BRANCHES[1]}\b")

#: What the tarball walk leaves out: git, tool caches, the venv and build
#: output, made by running the suite, never shipped. Dot directories that do
#: ship (.claude-plugin/, .github/) are walked.
WALK_SKIP_DIRS = {".git", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
                  "__pycache__", "build", "dist", "node_modules"}


def shipped_files(root: Path = ROOT, checkout: bool | None = None) -> list[Path]:
    """Every file the release archive keeps, under ROOT. In a checkout, git's
    list (tracked, and untracked but not ignored) less the export-ignored;
    else a walk of the tree. CHECKOUT defaults to whether ROOT has `.git`."""
    if checkout is None:
        checkout = (root / ".git").exists()
    if checkout:
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached",
                                 "--others", "--exclude-standard"],
                                check=True, capture_output=True, text=True).stdout
        files = [p for p in (root / rel for rel in listed.split("\0") if rel) if p.is_file()]
        return sorted(set(files) - ignored_by_export(files))
    found = []
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts[:-1]
        if any(part in WALK_SKIP_DIRS or part.endswith(".egg-info") for part in parts):
            continue
        if path.is_file():
            found.append(path)
    return sorted(found)


def bad_refs(line: str) -> list[str]:
    """The refs in LINE's sessionkit file URLs that are not HEAD or a tag."""
    return [m.group(1) for m in REF_URL.finditer(line) if not GOOD_REF.fullmatch(m.group(1))]


def hits(text: str) -> list[str]:
    """Each line of TEXT that links into this repo by URL, pins a sessionkit
    link to a branch, or clones the default branch by name."""
    return [line.strip() for line in text.splitlines()
            if SELF_URL.search(line) or bad_refs(line) or BRANCH_FLAG.search(line)]


def scan(files: list[Path], root: Path = ROOT) -> list[str]:
    found = []
    for path in files:
        text = path.read_bytes().decode("utf-8", errors="replace")
        rel = path.relative_to(root)
        found.extend(f"{rel}: {line}" for line in hits(text))
    return found


def test_shipped_files_link_relatively_and_pin_no_branch():
    files = shipped_files()
    # A scan that reads nothing passes everything: make it see the tree.
    assert ROOT / "README.md" in files and Path(__file__).resolve() in files
    found = scan(files)
    assert not found, (
        "link a file in this repo by its relative path, and a file in another "
        "sessionkit repo at /blob/HEAD/ (a directory /tree/HEAD/) or a release "
        "tag, never a branch name:\n"
        + "\n".join(f"  {h}" for h in found)
    )


KIT = "https://github.com/sessionkit/sessionkit"


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        # this repo by full URL, whatever the ref
        (f"[a]: https://{REPO}/blob/HEAD/docs/a.md", True),
        (f"[a]: https://{REPO}/tree/{BRANCHES[0]}/docs", True),
        (f"https://{REPO}/raw/v1.0.0/README.md", True),
        # another sessionkit repo on a branch name
        *[(f"[a]: {KIT}/{kind}/{branch}/docs/a.md", True)
          for kind in ("blob", "tree", "raw") for branch in BRANCHES],
        (f"[a]: {KIT}/blob/{BRANCHES[1]}/docs/a.md#part", True),
        (f"see [a]({KIT}/blob/{BRANCHES[0]}/docs/a.md) now", True),
        (f"[a]: {KIT}/blob/{BRANCHES[1]}tenance/a.md", True),
        (f"[a]: {KIT}/blob/head/docs/a.md", True),
        # the default branch cloned by name
        (f"git clone -b {BRANCHES[1]} https://{REPO}", True),
        (f"git clone --branch {BRANCHES[1]} https://{REPO}", True),
        (f"git clone --branch={BRANCHES[1]} https://{REPO}", True),
        # allowed
        ("[a]: docs/a.md", False),
        ("[a]: ../README.md#install", False),
        (f"[a]: {KIT}/blob/HEAD/docs/settings.md", False),
        (f"[a]: {KIT}/tree/HEAD/docs", False),
        (f"see [a]({KIT}/blob/HEAD/docs/a.md#part) now", False),
        (f"[a]: {KIT}/blob/v0.4.1/docs/a.md", False),
        (f"[a]: {KIT}", False),
        (f"uv tool install git+https://{REPO}", False),
        ("/etc/postfix/main.cf", False),
    ],
)
def test_the_scan_itself(text: str, flagged: bool):
    assert bool(hits(text)) is flagged


def test_the_tarball_walk_keeps_shipped_files_and_leaves_generated_ones(tmp_path):
    for rel in ["README.md", "docs/a.md", "src/p/m.py", ".gitattributes",
                ".claude-plugin/plugin.json", ".venv/lib/x.md", ".git/config",
                ".pytest_cache/v/x", "src/p/__pycache__/m.pyc", "src/p.egg-info/PKG-INFO"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    walked = [p.relative_to(tmp_path).as_posix() for p in shipped_files(tmp_path, checkout=False)]
    assert walked == [".claude-plugin/plugin.json", ".gitattributes", "README.md",
                      "docs/a.md", "src/p/m.py"]
