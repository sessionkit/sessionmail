"""scripts/release.py never re-locks (SM-937, a copy of the kit's SK-180).

`uv version X --no-sync` still re-locks: `--no-sync` only skips the venv sync.
The release commits uv.lock, so a cut before the family's releases are on
PyPI failed as unsatisfiable, a bump needed the network, and with
UV_FIND_LINKS set the release committed a local-path lock. These tests pin
the fix:

- `uv version` carries --frozen, and not --no-sync (uv refuses the two
  together), on the real path and the dry path alike;
- a REAL release, in a throwaway git repo, leaves every line of uv.lock as
  it was except the root `sessionmail` version line, and `uv lock --check`
  (offline) passes after it;
- the bump needs no index: every uv call here runs offline (UV_OFFLINE=1)
  against an EMPTY cache of its own, so any resolve fails. An offline
  re-lock from a warm cache writes the same bytes as the fix when the deps
  are unchanged, and would pass unseen. One case also floors a dependency
  at a version no index has, the family-cut case the bug broke;
- the root-line edit fails closed: no root block, two, or two version lines
  in it, and the release refuses before `uv version` writes anything;
- `make release` runs the script with python3, never a re-locking `uv run`.

A dry run cannot catch the bug (`uv version --dry-run` never locks), so the
release tests are real ones, each on a copy in tmp_path, never this
checkout.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release.py"
UV = shutil.which("uv")
GIT = shutil.which("git")
# The files a release reads or writes, plus the readme pyproject names: the
# test repo is built from these as they are on disk, so the test checks the
# code under test, not HEAD's. It is built by copying, never by cloning ROOT:
# in the release tarball (`make test-export`) ROOT is not a git repo at all.
RELEASE_FILES = ("scripts/release.py", "pyproject.toml", "uv.lock", "CHANGELOG.md", "README.md", ".claude-plugin")

needs_uv_git = pytest.mark.skipif(UV is None or GIT is None, reason="uv and git are needed")


def load_release():
    spec = importlib.util.spec_from_file_location("release_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env(cwd: Path) -> dict[str, str]:
    """No UV_* from the host (a UV_FROZEN would hide the bug), offline, an
    empty uv cache beside the clone, and a git with no user or system config
    (no signing, no hooks), named here."""
    out = {k: v for k, v in os.environ.items() if not k.startswith(("UV_", "GIT_", "VIRTUAL_ENV"))}
    out.update(
        UV_OFFLINE="1",
        UV_CACHE_DIR=str(cwd.parent / "uv-cache"),
        UV_PYTHON_DOWNLOADS="never",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="release test",
        GIT_AUTHOR_EMAIL="release-test@example.invalid",
        GIT_COMMITTER_NAME="release test",
        GIT_COMMITTER_EMAIL="release-test@example.invalid",
    )
    return out


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env(cwd), capture_output=True, text=True, check=False)


def git(cwd: Path, *args: str) -> str:
    out = run(cwd, "git", *args)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A fresh git repo in tmp_path holding a copy of this tree's release
    files, committed, so the tree is clean for release.py. A copy, not a
    clone of ROOT: the tarball has no repo to clone, and the release reads
    only these files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel in RELEASE_FILES:
        src, dst = ROOT / rel, repo / rel
        assert src.exists(), f"{rel} is missing from this tree: the release reads it"
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    git(repo, "init", "-q")
    git(repo, "add", "--", *RELEASE_FILES)
    git(repo, "commit", "-q", "-m", "test: this tree's release files")
    return repo


def version_of(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]


def root_line(lock: str) -> str:
    m = re.search(r'^\[\[package\]\]\nname = "sessionmail"\n(version = "[^"]*")\n', lock, re.MULTILINE)
    assert m, "no root sessionmail block in uv.lock"
    return m.group(1)


def with_root(lock: str, old: str, new: str) -> str:
    """`lock` with the root sessionmail version line, and only it, moved to `new`."""
    head = '[[package]]\nname = "sessionmail"\nversion = '
    assert lock.count(f'{head}"{old}"\n') == 1
    return lock.replace(f'{head}"{old}"\n', f'{head}"{new}"\n')


def next_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


# --- the uv call carries --frozen --------------------------------------------


@pytest.mark.parametrize("dry", [False, True])
def test_uv_version_is_frozen_on_both_paths(monkeypatch, dry: bool) -> None:
    release = load_release()
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    release.set_version("9.8.7", dry)
    (argv,) = calls  # the dry path RUNS uv too (with --dry-run), it does not just print
    assert argv[:2] == ["uv", "version"]
    assert "--frozen" in argv
    # uv 0.13.4 refuses --no-sync beside --frozen (exit 2): it must be gone.
    assert "--no-sync" not in argv
    assert ("--dry-run" in argv) is dry
    # The dry argv is the real argv plus --dry-run: a rehearsal runs the same call.
    assert [a for a in argv if a != "--dry-run"] == ["uv", "version", "9.8.7", "--frozen"]


# --- a real release ------------------------------------------------------------


@needs_uv_git
def test_real_release_changes_only_the_root_lock_line(clone: Path) -> None:
    before = (clone / "uv.lock").read_text()
    old = version_of(clone)
    new = next_patch(old)

    out = run(clone, sys.executable, "scripts/release.py", new)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "--frozen" in out.stdout

    assert version_of(clone) == new
    after = (clone / "uv.lock").read_text()
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(a) == len(b), "uv.lock gained or lost lines: something re-locked"
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    assert changed == [(f'version = "{old}"\n', f'version = "{new}"\n')]
    assert root_line(after) == f'version = "{new}"'  # the changed line IS the root line
    # Nothing resolved: no uv.lock written by uv, so nothing else moved either.
    assert after == with_root(before, old, new)

    # The release committed it and tagged it, and left the tree clean.
    assert git(clone, "status", "--porcelain").strip() == ""
    assert git(clone, "log", "-1", "--format=%s").strip() == f"chore: release v{new}"
    assert git(clone, "tag", "-l", f"v{new}").strip() == f"v{new}"
    assert root_line(git(clone, "show", f"v{new}:uv.lock")) == f'version = "{new}"'
    assert not (clone / ".venv").exists(), "something synced an environment"


@needs_uv_git
def test_release_with_a_floor_no_index_has(clone: Path) -> None:
    """The family cut: a dependency floored at a release not yet on any
    index. A re-lock is unsatisfiable here; the release must not need one."""
    pyproject = clone / "pyproject.toml"
    text = pyproject.read_text()
    floored = re.sub(r'"sessiontools>=[^"]+"', '"sessiontools>=99.0.0"', text, count=1)
    assert floored != text
    pyproject.write_text(floored)
    git(clone, "commit", "-q", "-am", "test: floor a dep at an unreleased version")
    before = (clone / "uv.lock").read_text()
    old = version_of(clone)
    new = next_patch(old)

    out = run(clone, sys.executable, "scripts/release.py", new)
    assert out.returncode == 0, out.stdout + out.stderr

    assert (clone / "uv.lock").read_text() == with_root(before, old, new)
    assert root_line((clone / "uv.lock").read_text()) == f'version = "{new}"'
    assert git(clone, "status", "--porcelain").strip() == ""
    assert not (clone / ".venv").exists(), "something synced an environment"


@needs_uv_git
def test_release_needs_no_index(clone: Path) -> None:
    """An unroutable index on top of UV_OFFLINE and the empty cache: the bump
    must not reach for it at all."""
    old = version_of(clone)
    new = next_patch(old)
    e = env(clone) | {"UV_INDEX_URL": "http://192.0.2.1:9/simple", "UV_DEFAULT_INDEX": "http://192.0.2.1:9/simple"}
    out = subprocess.run(
        [sys.executable, "scripts/release.py", new], cwd=clone, env=e, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert version_of(clone) == new
    assert root_line((clone / "uv.lock").read_text()) == f'version = "{new}"'


@needs_uv_git
def test_lock_check_passes_offline_after_the_release(clone: Path) -> None:
    pre = run(clone, "uv", "lock", "--check")
    assert pre.returncode == 0, f"uv lock --check fails offline BEFORE the release: {pre.stderr.strip()}"
    new = next_patch(version_of(clone))
    out = run(clone, sys.executable, "scripts/release.py", new)
    assert out.returncode == 0, out.stdout + out.stderr
    check = run(clone, "uv", "lock", "--check")
    assert check.returncode == 0, check.stderr
    assert git(clone, "status", "--porcelain").strip() == ""


@needs_uv_git
def test_dry_run_reports_the_lock_edit_and_writes_nothing(clone: Path) -> None:
    files = ("pyproject.toml", "uv.lock", "CHANGELOG.md", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")
    before = {f: (clone / f).read_bytes() for f in files}
    old = version_of(clone)
    new = next_patch(old)

    out = run(clone, sys.executable, "scripts/release.py", new, "--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"uv version {new} --frozen --dry-run" in out.stdout
    assert f"uv.lock: root version {old} -> {new}" in out.stdout
    assert {f: (clone / f).read_bytes() for f in files} == before
    assert git(clone, "status", "--porcelain").strip() == ""
    assert git(clone, "tag", "-l").strip() == ""


# --- the root-line edit fails closed -------------------------------------------

ROOT_BLOCK = re.compile(r'^\[\[package\]\]\nname = "sessionmail"\n.*?(?=^\[\[package\]\]$|\Z)', re.MULTILINE | re.DOTALL)


def no_root(lock: str) -> str:
    return ROOT_BLOCK.sub("", lock, count=1)


def two_roots(lock: str) -> str:
    block = ROOT_BLOCK.search(lock).group(0)
    return lock + "\n" + block


def two_versions(lock: str) -> str:
    return re.sub(r'^(name = "sessionmail"\nversion = "[^"]*"\n)', r'\1version = "0.0.0"\n', lock, count=1, flags=re.MULTILINE)


def no_version(lock: str) -> str:
    return re.sub(r'^(name = "sessionmail"\n)version = "[^"]*"\n', r"\1", lock, count=1, flags=re.MULTILINE)


def not_the_root_source(lock: str) -> str:
    """The sessionmail block present, but sourced from an index: not the project."""
    return re.sub(
        r'^(name = "sessionmail"\nversion = "[^"]*"\n)source = \{ editable = "\." \}$',
        r'\1source = { registry = "https://pypi.org/simple" }',
        lock,
        count=1,
        flags=re.MULTILINE,
    )


@pytest.mark.parametrize(
    "mangle, says",
    [
        (no_root, "has 0 root [[package]] blocks"),
        (two_roots, "has 2 root [[package]] blocks"),
        (two_versions, "has 2 version lines"),
        (no_version, "has 0 version lines"),
        (not_the_root_source, "has 0 root [[package]] blocks"),
    ],
)
@pytest.mark.parametrize("dry", [False, True])
@needs_uv_git
def test_a_bad_lock_is_refused_before_anything_is_written(clone: Path, mangle, says: str, dry: bool) -> None:
    lock = clone / "uv.lock"
    mangled = mangle(lock.read_text())
    assert mangled != lock.read_text()
    lock.write_text(mangled)
    git(clone, "commit", "-q", "-am", "test: a lock release.py must refuse")
    files = ("pyproject.toml", "uv.lock", "CHANGELOG.md", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")
    before = {f: (clone / f).read_bytes() for f in files}
    head = git(clone, "rev-parse", "HEAD")

    out = run(clone, sys.executable, "scripts/release.py", next_patch(version_of(clone)), *(["--dry-run"] if dry else []))

    assert out.returncode != 0
    assert says in out.stderr
    assert "nothing was written" in out.stderr
    assert "uv version" not in out.stdout  # refused BEFORE uv ran
    assert {f: (clone / f).read_bytes() for f in before} == before
    assert git(clone, "status", "--porcelain").strip() == ""
    assert git(clone, "rev-parse", "HEAD") == head
    assert git(clone, "tag", "-l").strip() == ""


@needs_uv_git
def test_missing_lock_is_refused_before_anything_is_written(clone: Path) -> None:
    git(clone, "rm", "-q", "uv.lock")
    git(clone, "commit", "-q", "-m", "test: no uv.lock")
    before = (clone / "pyproject.toml").read_bytes()

    out = run(clone, sys.executable, "scripts/release.py", next_patch(version_of(clone)))

    assert out.returncode != 0
    assert "uv.lock not found" in out.stderr
    assert "uv version" not in out.stdout
    assert (clone / "pyproject.toml").read_bytes() == before
    assert git(clone, "status", "--porcelain").strip() == ""


def test_lock_root_finds_the_one_version_value() -> None:
    release = load_release()
    lock = (ROOT / "uv.lock").read_text()
    start, end = release.lock_root(lock, "sessionmail")
    assert lock[start:end] == version_of(ROOT)
    assert lock[:start].endswith('[[package]]\nname = "sessionmail"\nversion = "')


def test_lock_root_accepts_a_virtual_root() -> None:
    release = load_release()
    lock = '[[package]]\nname = "sessionmail"\nversion = "1.2.3"\nsource = { virtual = "." }\n'
    start, end = release.lock_root(lock, "sessionmail")
    assert lock[start:end] == "1.2.3"


def test_set_lock_root_edits_that_value_only(tmp_path: Path, monkeypatch) -> None:
    release = load_release()
    monkeypatch.chdir(tmp_path)
    lock = (ROOT / "uv.lock").read_text()
    span = release.lock_root(lock, "sessionmail")
    old = lock[span[0] : span[1]]

    release.set_lock_root(lock, span, "9.8.7", dry=True)
    assert not (tmp_path / "uv.lock").exists(), "the dry path wrote uv.lock"

    release.set_lock_root(lock, span, "9.8.7", dry=False)
    assert (tmp_path / "uv.lock").read_text() == with_root(lock, old, "9.8.7")


# --- make release ----------------------------------------------------------------


def release_recipe() -> list[str]:
    lines = (ROOT / "Makefile").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("release:"))
    recipe = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line)
    return recipe


def test_make_release_recipe_has_no_relocking_uv_run() -> None:
    recipe = release_recipe()
    assert recipe, "Makefile has no release: recipe"
    for line in recipe:
        assert not re.search(r"\buv\s+run\b(?!.*--no-project)", line), line
    assert any("python3 scripts/release.py" in line for line in recipe)
