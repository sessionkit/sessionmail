"""`make pypi-dist`: the wheel + sdist PyPI gets, README links pinned to the tag.

The repo keeps relative links in README.md; on PyPI they 404. The target
builds from tag v<version> (never the checkout), has the family's README
rewriter (sessionkit's scripts/pypi_readme.py under KIT_DIR, which this repo
keeps no copy of) pin the STAGED README's links, builds there, and has the
same rewriter check both dists. Before any of that, the kit's `tag-check`
refuses unless tag v<version> is the SAME commit here and on PUBLIC_REMOTE,
naming both shas when they differ: the links name the public tag, the dists
are built from the local one.

Network-free and needs no sessionkit checkout: the Makefile runs in a planted
repo under tmp_path, PUBLIC_REMOTE is a local bare repo, KIT_DIR holds a stub
scripts/pypi_readme.py that logs its argv, and `uv` on PATH is a fake that
writes two empty dists. The stub's `tag-check` refuses as the kit's does; given
a real kit (KIT_DIR in the environment, else a sibling ../sessionkit, when it
holds scripts/pypi_readme.py), the tag-check tests also run the kit's own verb.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

REAL_UV = shutil.which("uv")

pytestmark = [
    pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed"),
    pytest.mark.skipif(REAL_UV is None, reason="uv is not installed"),
]

VERSION = "9.8.7"
TAG = f"v{VERSION}"
README = "# sessionmail\n\nSee [the pager][pager].\n\n[pager]: docs/pager.md\n"
MARK = "<!-- rewritten by the stub -->\n"

def real_kit_script() -> Path | None:
    """The kit's own pypi_readme.py: $KIT_DIR's, else the sibling ../sessionkit's."""
    base = os.environ.get("KIT_DIR") or str(ROOT.parent / "sessionkit")
    path = Path(base) / "scripts" / "pypi_readme.py"
    return path if path.is_file() else None


REAL_KIT = real_kit_script()

#: No user or system git config: no signing, no hooks, in the planted repos.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
        env={**os.environ, **GIT_ENV, "GIT_CEILING_DIRECTORIES": str(repo.parent)},
    ).stdout


@pytest.fixture
def rig(tmp_path):
    """A repo holding the real Makefile, tagged TAG;
    a bin/ with a fake `uv`; a stub rewriter; a log both write to."""
    work = tmp_path / "repo"
    (work / "docs").mkdir(parents=True)
    shutil.copy(ROOT / "Makefile", work / "Makefile")
    (work / "pyproject.toml").write_text(f'[project]\nname = "sessionmail"\nversion = "{VERSION}"\n')
    (work / "README.md").write_text(README)
    (work / "docs" / "pager.md").write_text("# pager\n")
    git(work, "init", "-q")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "x")
    git(work, "tag", TAG)

    log = tmp_path / "calls.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    uv = bin_ / "uv"
    # `uv version` is the real uv, offline, reading the planted pyproject.toml.
    # `uv build --out-dir D`: log the cwd, keep the README it built from,
    # write an empty wheel and sdist named as hatchling names them.
    uv.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = version ]; then UV_OFFLINE=1 exec "{REAL_UV}" "$@"; fi\n'
        f'echo "uv $* cwd=$PWD" >> "{log}"\n'
        f'cp README.md "{tmp_path}/built-from-README.md"\n'
        'while [ $# -gt 0 ]; do [ "$1" = --out-dir ] && out="$2"; shift; done\n'
        'mkdir -p "$out"\n'
        f'touch "$out/sessionmail-{VERSION}-py3-none-any.whl" "$out/sessionmail-{VERSION}.tar.gz"\n'
    )
    uv.chmod(0o755)
    kit = tmp_path / "kit"
    (kit / "scripts").mkdir(parents=True)
    stub = kit / "scripts" / "pypi_readme.py"
    # The kit's three verbs, as argv only: `rewrite ... FILE` marks FILE,
    # `check ...` exits STUB_CHECK_EXIT (0 unless a test sets it), and
    # `tag-check` refuses as the kit's does (same words), or, with
    # STUB_REAL_KIT set, hands over to the kit's own script.
    stub.write_text(
        "import os, subprocess, sys\n"
        f"with open({str(log)!r}, 'a') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'tag-check':\n"
        "    if os.environ.get('STUB_REAL_KIT'):\n"
        "        real = os.environ['STUB_REAL_KIT']\n"
        "        os.execv(sys.executable, [sys.executable, real, *sys.argv[1:]])\n"
        "    a = sys.argv[1:]\n"
        "    tag, remote = a[a.index('--tag') + 1], a[a.index('--remote') + 1]\n"
        "    def run(*args):\n"
        "        return subprocess.run(['git', *args], capture_output=True, text=True).stdout\n"
        "    here = run('rev-parse', '--verify', '--quiet', f'refs/tags/{tag}^{{commit}}').strip()\n"
        "    refs = {}\n"
        "    for line in run('ls-remote', remote, f'refs/tags/{tag}',\n"
        "                    f'refs/tags/{tag}^{{}}').splitlines():\n"
        "        sha, _, ref = line.partition('\\t')\n"
        "        refs[ref] = sha\n"
        "    there = refs.get(f'refs/tags/{tag}^{{}}') or refs.get(f'refs/tags/{tag}')\n"
        "    if not here:\n"
        "        sys.exit(f'pypi_readme tag-check: tag {tag} is not in this repo; tag the release first')\n"
        "    if there is None:\n"
        "        sys.exit(f'pypi_readme tag-check: tag {tag} is not on {remote}; '\n"
        "                 'push the tag to the public repo first')\n"
        "    if here != there:\n"
        "        sys.exit(f'pypi_readme tag-check: tag {tag} differs: local {here}, public {there} '\n"
        "                 f'on {remote}; the local tag must be the commit the public repo tagged')\n"
        "    print(f'pypi_readme tag-check: {tag} is {here} here and on {remote}')\n"
        "if sys.argv[1] == 'rewrite':\n"
        "    with open(sys.argv[-1], 'a') as f:\n"
        f"        f.write({MARK!r})\n"
        "if sys.argv[1] == 'check':\n"
        "    sys.exit(int(os.environ.get('STUB_CHECK_EXIT', '0')))\n"
    )
    remote = tmp_path / "public.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(remote))
    return {"work": work, "bin": bin_, "log": log, "kit": kit, "stub": stub, "remote": remote,
            "tmp": tmp_path}


def make(rig, *assignments: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **GIT_ENV, "GIT_CEILING_DIRECTORIES": str(rig["tmp"]),
           "PATH": f"{rig['bin']}{os.pathsep}{os.environ['PATH']}", **(env_extra or {})}
    for var in ("MAKEFLAGS", "KIT_DIR", "PYPI_README", "PUBLIC_REMOTE", "GIT_DIR", "GIT_WORK_TREE",
                "GIT_INDEX_FILE"):
        env.pop(var, None)
    env.update(env_extra or {})
    return subprocess.run([shutil.which("make"), "--no-print-directory", "pypi-dist", *assignments],
                          cwd=rig["work"], env=env, capture_output=True, text=True, timeout=60)


def calls(rig) -> list[str]:
    return rig["log"].read_text().splitlines() if rig["log"].exists() else []


def kit_env(kit: str) -> dict:
    """`stub`: the stub's own tag-check; `kit`: the kit's real one."""
    if kit == "kit":
        if REAL_KIT is None:
            pytest.skip("no sessionkit checkout with scripts/pypi_readme.py (set KIT_DIR=)")
        return {"STUB_REAL_KIT": str(REAL_KIT)}
    return {}


def move_the_local_tag(rig) -> tuple[str, str]:
    """Re-point TAG at a new commit after the public repo took it:
    returns (public sha, local sha)."""
    work = rig["work"]
    public = git(work, "rev-parse", f"{TAG}^{{commit}}").strip()
    (work / "docs" / "pager.md").write_text("# pager, after the tag\n")
    git(work, "commit", "-q", "-am", "after the tag")
    git(work, "tag", "-f", TAG)
    local = git(work, "rev-parse", f"{TAG}^{{commit}}").strip()
    assert local != public
    return public, local


def wired(rig) -> tuple[str, str]:
    """PUBLIC_REMOTE and KIT_DIR, as a release would pass them; PYPI_README
    is left at its default, which runs KIT_DIR's script."""
    return f"PUBLIC_REMOTE={rig['remote']}", f"KIT_DIR={rig['kit']}"


def test_kit_dir_without_the_script_refuses_naming_kit_dir(rig) -> None:
    bare = rig["tmp"] / "not-a-kit"
    bare.mkdir()
    r = make(rig, f"PUBLIC_REMOTE={rig['remote']}", f"KIT_DIR={bare}")
    assert r.returncode != 0
    assert "Error 1" in r.stderr, r.stderr
    assert f"no {bare}/scripts/pypi_readme.py" in r.stderr and "KIT_DIR=" in r.stderr, r.stderr
    assert calls(rig) == []
    assert not (rig["work"] / "dist").exists()


def test_default_kit_dir_is_a_sibling_checkout(rig) -> None:
    # tmp_path holds no `sessionkit`, so the default ../sessionkit is missing.
    r = make(rig, f"PUBLIC_REMOTE={rig['remote']}")
    assert "no ../sessionkit/scripts/pypi_readme.py" in r.stderr, r.stderr
    assert calls(rig) == []


def test_kit_dir_is_read_from_the_environment(rig) -> None:
    r = make(rig, f"PUBLIC_REMOTE={rig['remote']}", env_extra={"KIT_DIR": str(rig["kit"])})
    assert r.returncode == 0, r.stderr
    assert calls(rig)[-1].startswith("check ")


def test_pypi_readme_set_outright_needs_no_kit_dir(rig) -> None:
    r = make(rig, f"PUBLIC_REMOTE={rig['remote']}", f"KIT_DIR={rig['tmp']}/nowhere",
             f"PYPI_README=python3 {rig['stub']}")
    assert r.returncode == 0, r.stderr
    assert [c.split()[0] for c in calls(rig)] == ["tag-check", "rewrite", "uv", "check"]


def refused_before_any_build(rig, r: subprocess.CompletedProcess) -> None:
    """The tag-check refused: make failed, and the check is the only call."""
    assert r.returncode != 0
    assert "Error 1" in r.stderr, r.stderr
    assert [c.split()[0] for c in calls(rig)] == ["tag-check"], calls(rig)
    assert not (rig["work"] / "dist").exists()


@pytest.mark.parametrize("kit", ["stub", "kit"])
def test_tags_match_proceeds(rig, kit) -> None:
    r = make(rig, *wired(rig), env_extra=kit_env(kit))
    assert r.returncode == 0, r.stderr
    sha = git(rig["work"], "rev-parse", f"{TAG}^{{commit}}").strip()
    assert f"{TAG} is {sha} here and on {rig['remote']}" in r.stdout, r.stdout
    assert [c.split()[0] for c in calls(rig)] == ["tag-check", "rewrite", "uv", "check"]


@pytest.mark.parametrize("kit", ["stub", "kit"])
def test_tag_absent_on_public_remote_refuses_before_any_build(rig, kit) -> None:
    empty = rig["tmp"] / "empty.git"
    git(rig["tmp"], "init", "-q", "--bare", str(empty))
    r = make(rig, f"PUBLIC_REMOTE={empty}", f"KIT_DIR={rig['kit']}", env_extra=kit_env(kit))
    refused_before_any_build(rig, r)
    assert f"tag {TAG} is not on {empty}" in r.stderr, r.stderr


@pytest.mark.parametrize("kit", ["stub", "kit"])
def test_public_tag_at_a_different_commit_refuses_naming_both_shas(rig, kit) -> None:
    """The tag is on PUBLIC_REMOTE but was moved here since: the links would
    name one commit and the dists would be built from another."""
    public, local = move_the_local_tag(rig)
    r = make(rig, *wired(rig), env_extra=kit_env(kit))
    refused_before_any_build(rig, r)
    assert f"tag {TAG} differs: local {local}, public {public}" in r.stderr, r.stderr


@pytest.mark.parametrize("kit", ["stub", "kit"])
def test_an_annotated_public_tag_is_judged_by_its_commit(rig, kit) -> None:
    """An annotated tag's own sha is not a commit: the peeled one is compared."""
    git(rig["work"], "tag", "-f", "-a", TAG, "-m", TAG)
    git(rig["work"], "push", "-q", "-f", str(rig["remote"]), f"refs/tags/{TAG}")
    r = make(rig, *wired(rig), env_extra=kit_env(kit))
    assert r.returncode == 0, r.stderr


def test_the_recipe_runs_the_kits_tag_check_not_a_bare_ls_remote() -> None:
    text = (ROOT / "Makefile").read_text()
    recipe = text.split("\npypi-dist:", 1)[1].split("\n\n", 1)[0]
    assert '\t$(PYPI_README) tag-check --tag "$$v" --remote "$(PUBLIC_REMOTE)"; \\\n' in recipe
    assert "ls-remote" not in recipe, "the bare existence check is back"


def test_rewrites_the_staged_readme_builds_there_and_checks_both_dists(rig) -> None:
    r = make(rig, *wired(rig))
    assert r.returncode == 0, r.stderr
    tag_check, rewrite, build, check = calls(rig)
    assert tag_check.split() == ["tag-check", "--tag", TAG, "--remote", str(rig["remote"])]

    words = rewrite.split()
    assert words[:5] == ["rewrite", "--repo", "sessionkit/sessionmail", "--ref", TAG]
    assert words[5] == "--root"
    stage = words[6]
    assert words[7:] == [f"{stage}/README.md"]
    assert Path(stage) != rig["work"]
    assert not Path(stage).exists(), "the scratch stage was left behind"

    assert build == f"uv build --out-dir {rig['work']}/dist cwd={stage}"
    assert check.split() == ["check", f"dist/sessionmail-{VERSION}-py3-none-any.whl",
                             f"dist/sessionmail-{VERSION}.tar.gz"]

    assert (rig["tmp"] / "built-from-README.md").read_text() == README + MARK
    assert (rig["work"] / "README.md").read_text() == README, "the checkout's README was touched"
    assert git(rig["work"], "status", "--porcelain", "--untracked-files=no") == ""


def test_builds_from_the_tag_not_the_checkout(rig) -> None:
    (rig["work"] / "README.md").write_text(README + "after the tag\n")
    git(rig["work"], "commit", "-q", "-am", "after the tag")
    r = make(rig, *wired(rig))
    assert r.returncode == 0, r.stderr
    assert "after the tag" not in (rig["tmp"] / "built-from-README.md").read_text()


def test_a_failed_check_fails_the_target(rig) -> None:
    r = make(rig, *wired(rig), env_extra={"STUB_CHECK_EXIT": "1"})
    assert r.returncode != 0
    assert calls(rig)[-1].startswith("check ")


def test_pypi_dist_is_phony() -> None:
    phony = [line for line in (ROOT / "Makefile").read_text().splitlines()
             if line.startswith(".PHONY:") and " build " in line]
    assert phony and "pypi-dist" in phony[0].split()


def test_the_release_path_names_pypi_dist_not_a_bare_uv_build() -> None:
    text = (ROOT / "scripts" / "release.py").read_text()
    assert "make pypi-dist" in text
    assert "uv build &&" not in text
