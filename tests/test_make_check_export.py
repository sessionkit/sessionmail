"""SM-849, SM-857: `make check` in the release export. `.gitattributes`
export-ignores openspec/, so `git archive` leaves it out, and the openspec
leg must SKIP there, saying so, not go red: in the tarball (no repo, or no
git at all) and in a git clone of the export (`.git` present, openspec/ never
tracked). In a checkout that TRACKS openspec/ it stays strict: openspec/
missing there fails, and openspec/ present is validated. `.git` alone decides
nothing; every shape below is a REAL repo planted in tmp_path.

The Makefile runs in a temp tree with a fake `openspec` and `skill` on PATH,
each logging its argv, so no real validator runs and none is needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")

SKIPPED = "openspec/ not in this tree (release archive): skipped"


@pytest.fixture
def tree(tmp_path):
    """A tree with the repo's Makefile, and a bin/ of fakes that log argv."""
    work = tmp_path / "tree"
    work.mkdir()
    shutil.copy(ROOT / "Makefile", work / "Makefile")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("openspec", "skill"):
        fake = bin_ / tool
        fake.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        fake.chmod(0o755)
    return work, bin_, log


#: No user or system git config: no signing, no hooks, in the planted repos.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def check(work: Path, bin_: Path, path: str | None = None) -> subprocess.CompletedProcess:
    """`make check` in WORK, with BIN_ first on PATH (or PATH itself, when
    given: the no-git shape)."""
    env = {**os.environ, **GIT_ENV, "GIT_CEILING_DIRECTORIES": str(work.parent),
           "PATH": path or f"{bin_}{os.pathsep}{os.environ['PATH']}"}
    for var in ("MAKEFLAGS", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    return subprocess.run([shutil.which("make"), "--no-print-directory", "check"], cwd=work,
                          env=env, capture_output=True, text=True, timeout=60)


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV, "GIT_CEILING_DIRECTORIES": str(repo.parent)})


def committed(repo: Path) -> Path:
    """REPO made a repo, everything in it committed."""
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    return repo


def private_checkout(work: Path) -> Path:
    """WORK as the private checkout: openspec/ committed, so tracked."""
    (work / "openspec").mkdir()
    (work / "openspec" / "project.md").write_text("p\n")
    return committed(work)


def assert_skipped(run: subprocess.CompletedProcess, log: Path) -> None:
    assert run.returncode == 0, run.stderr
    assert SKIPPED in run.stdout
    assert calls(log) == ["skill plugin validate ."]   # the other leg still runs


def assert_failed(run: subprocess.CompletedProcess, log: Path) -> None:
    assert run.returncode != 0
    assert "openspec/ is missing from this git checkout" in run.stderr
    assert SKIPPED not in run.stdout
    assert calls(log) == []


def test_in_the_release_export_the_openspec_leg_skips_naming_the_export(tree):
    work, bin_, log = tree                      # no repo, no openspec/: the tarball
    assert_skipped(check(work, bin_), log)


def test_in_a_clone_of_the_export_the_openspec_leg_skips_though_git_is_there(tree, tmp_path):
    """SM-857: the export is a repo of its own, and a stranger clones it.
    `.git` is there; openspec/ never was."""
    work, bin_, log = tree
    committed(work)                             # the export: Makefile, no openspec/
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(work), str(clone))
    assert (clone / ".git").is_dir()
    assert_skipped(check(clone, bin_), log)


@pytest.mark.parametrize("dotgit", ["dir", "file"])
def test_a_fake_dotgit_that_is_no_repo_decides_nothing_the_leg_skips(tree, dotgit):
    work, bin_, log = tree
    (work / ".git").mkdir() if dotgit == "dir" else (work / ".git").write_text("gitdir: x\n")
    assert_skipped(check(work, bin_), log)


def test_with_no_git_installed_the_openspec_leg_skips(tree, tmp_path):
    """PATH holds the fakes and bash, and no git: nothing can be asked."""
    work, bin_, log = tree
    (bin_ / "bash").symlink_to(shutil.which("bash"))
    assert shutil.which("git", path=str(bin_)) is None
    assert_skipped(check(work, bin_, path=str(bin_)), log)


def test_in_a_checkout_that_tracks_it_a_missing_openspec_fails(tree):
    """The planted test: openspec/ committed, then deleted. Tracked, absent:
    a broken checkout, not the export."""
    work, bin_, log = tree
    shutil.rmtree(private_checkout(work) / "openspec")
    assert_failed(check(work, bin_), log)


def test_in_a_worktree_that_tracks_it_a_missing_openspec_fails(tree, tmp_path):
    """The same, where `.git` is a FILE."""
    work, bin_, log = tree
    git(private_checkout(work), "worktree", "add", "-q", str(tmp_path / "wt"))
    wt = tmp_path / "wt"
    assert (wt / ".git").is_file()
    shutil.rmtree(wt / "openspec")
    assert_failed(check(wt, bin_), log)


@pytest.mark.parametrize("shape", ["checkout", "tarball"])
def test_a_present_openspec_is_validated_strictly(tree, shape):
    work, bin_, log = tree
    if shape == "checkout":
        private_checkout(work)
    else:
        (work / "openspec").mkdir()
    run = check(work, bin_)
    assert run.returncode == 0, run.stderr
    assert SKIPPED not in run.stdout
    assert calls(log) == ["openspec validate --all --strict", "skill plugin validate ."]


def test_test_export_runs_make_check_inside_the_export():
    """`make test-export` covers the check, not only the suite."""
    recipe = (ROOT / "Makefile").read_text().split("\ntest-export:", 1)[1].split("\n\n", 1)[0]
    assert "$(MAKE) --no-print-directory check" in recipe
    assert recipe.index("check;") < recipe.index("pytest")
