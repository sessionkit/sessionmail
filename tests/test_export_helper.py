"""SM-849, SM-857: the export helper skips only where the export left a file
out (absent AND untracked), never in a checkout that tracks it. Prove its YES
and its NO in all three shapes, with REAL git repos planted in tmp_path,
before trusting the tests that use it:

- the private checkout (and a worktree, whose `.git` is a FILE): the file is
  tracked, so a deleted one is handed back and the read FAILS;
- the tarball: no repo, or no git at all, so an absent file SKIPS;
- a git clone of the export: `.git` is there, but the export never held the
  file, so an absent file SKIPS. `.git` alone decides nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import _export

REASON = "CLAUDE.md is export-ignored: absent in the git-archive release export"


@pytest.fixture(autouse=True)
def isolated_git(tmp_path, monkeypatch):
    """No user or system git config (no signing, no hooks), and no walk up
    out of tmp_path into whatever repo holds it."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def committed(repo: Path, files: dict[str, str]) -> Path:
    """A fresh repo at REPO holding FILES, all committed."""
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "--", *files)
    git(repo, "commit", "-q", "-m", "x")
    return repo


#: What the private repo tracks, and what the export of it keeps.
PRIVATE = {"README.md": "r\n", "CLAUDE.md": "c\n", "openspec/spec.md": "s\n"}
EXPORTED = {"README.md": "r\n"}


@pytest.fixture
def root(monkeypatch):
    """Point the helper at a tree: call it with the tree."""
    def at(tree: Path) -> Path:
        monkeypatch.setattr(_export, "ROOT", tree)
        return tree
    return at


@pytest.fixture
def checkout(tmp_path, root):
    """The private checkout: CLAUDE.md and openspec/ tracked."""
    return root(committed(tmp_path / "private", PRIVATE))


@pytest.fixture
def worktree(tmp_path, root):
    """A worktree of the private repo: its `.git` is a FILE."""
    main = committed(tmp_path / "private", PRIVATE)
    git(main, "worktree", "add", "-q", str(tmp_path / "wt"))
    assert (tmp_path / "wt" / ".git").is_file()
    return root(tmp_path / "wt")


@pytest.fixture
def clone(tmp_path, root):
    """A git clone of the export: `.git` present, CLAUDE.md never tracked."""
    export = committed(tmp_path / "export", EXPORTED)
    subprocess.run(["git", "clone", "-q", str(export), str(tmp_path / "clone")],
                   check=True, capture_output=True)
    assert (tmp_path / "clone" / ".git").is_dir()
    return root(tmp_path / "clone")


@pytest.fixture
def tarball(tmp_path, root):
    """The tarball: the exported files, and no repo at all."""
    tree = tmp_path / "tarball"
    tree.mkdir()
    for rel, text in EXPORTED.items():
        (tree / rel).write_text(text)
    return root(tree)


def handed_back(rel: str) -> Path:
    """export_ignored(REL), where a skip is a FAILURE: a helper that skipped
    here would turn the test it guards green, vacuously."""
    try:
        return _export.export_ignored(rel)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"export_ignored({rel!r}) skipped where it must not: {skipped}")


# --- the planted test: tracked but deleted FAILS ------------------------------

@pytest.mark.parametrize("shape", ["checkout", "worktree"])
@pytest.mark.parametrize("rel, gone", [("CLAUDE.md", "CLAUDE.md"),
                                       ("openspec/spec.md", "openspec")])
def test_a_tracked_file_that_was_deleted_is_handed_back_so_the_read_fails(request, shape, rel, gone):
    tree = request.getfixturevalue(shape)
    target = tree / gone
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    assert _export.tracked(rel), "deleted from disk, still in the index"
    path = handed_back(rel)
    assert path == tree / rel
    with pytest.raises(FileNotFoundError):
        path.read_text()


# --- absent and untracked SKIPS ----------------------------------------------

def test_in_a_clone_of_the_export_an_absent_file_skips_though_git_is_there(clone):
    assert (clone / ".git").exists()
    assert not _export.tracked("CLAUDE.md")
    with pytest.raises(pytest.skip.Exception) as skipped:
        _export.export_ignored("CLAUDE.md")
    assert str(skipped.value) == REASON


def test_in_the_tarball_an_absent_file_skips_naming_the_export(tarball):
    with pytest.raises(pytest.skip.Exception) as skipped:
        _export.export_ignored("CLAUDE.md")
    assert str(skipped.value) == REASON


@pytest.mark.parametrize("dotgit", ["dir", "file"])
def test_a_fake_dotgit_that_is_no_repo_decides_nothing_it_skips(tarball, dotgit):
    """`.git` alone is not a checkout: with no repo behind it, nothing is
    tracked, and the absent file skips."""
    (tarball / ".git").mkdir() if dotgit == "dir" else (tarball / ".git").write_text("gitdir: x\n")
    assert not _export.tracked("CLAUDE.md")
    with pytest.raises(pytest.skip.Exception):
        _export.export_ignored("CLAUDE.md")


@pytest.mark.parametrize("shape", ["tarball", "checkout"])
def test_with_no_git_installed_an_absent_file_skips(request, shape, tmp_path, monkeypatch):
    """No git on PATH: nothing can be asked, so nothing is tracked. Even the
    private checkout cannot tell its own loss apart from the export's."""
    tree = request.getfixturevalue(shape)
    (tree / "CLAUDE.md").unlink(missing_ok=True)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("git") is None
    assert not _export.tracked("CLAUDE.md")
    with pytest.raises(pytest.skip.Exception):
        _export.export_ignored("CLAUDE.md")


# --- present is handed back everywhere ---------------------------------------

@pytest.mark.parametrize("shape", ["checkout", "worktree", "clone", "tarball"])
def test_a_present_file_is_handed_back_in_every_shape(request, shape):
    tree = request.getfixturevalue(shape)
    (tree / "openspec").mkdir(exist_ok=True)
    (tree / "openspec" / "spec.md").write_text("x\n")
    assert handed_back("openspec/spec.md").read_text() == "x\n"


def test_tracked_reads_the_index_not_the_disk(checkout):
    """A directory counts when a file under it is tracked; an untracked file
    on disk does not count."""
    (checkout / "scratch.md").write_text("u\n")
    assert _export.tracked("openspec") and _export.tracked("CLAUDE.md")
    assert not _export.tracked("scratch.md") and not _export.tracked("nowhere.md")


def test_with_no_git_nothing_is_reported_ignored(tarball):
    """In the tarball there is no `.git` to ask; `exists()` does that job."""
    assert _export.ignored_by_export([tarball / "openspec" / "x.md"]) == set()


def test_the_check_attr_reader_sees_this_repos_export_ignore_lines():
    """Against the real `.gitattributes`: openspec/ and CLAUDE.md are left
    out of the archive, docs/ and AGENTS.md are not, and a path outside the
    repo is never asked about."""
    if not _export.in_checkout():
        pytest.skip("not a git checkout (the git-archive release export): "
                    ".gitattributes cannot be asked")
    root = _export.ROOT
    ignored = root / "openspec" / "changes" / "message-receipt"
    claude = root / "CLAUDE.md"
    shipped = [root / "docs" / "message-receipt.md", root / "AGENTS.md"]
    outside = root.parent / "elsewhere.md"
    assert _export.ignored_by_export([ignored, claude, *shipped, outside]) == {ignored, claude}
