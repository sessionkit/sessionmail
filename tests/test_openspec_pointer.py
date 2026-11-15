"""openspec/ is a pointer to the project's OpenSpec store.

The specs, the open changes and RATIFICATION.md are kept in the store. This
tree keeps ONE OpenSpec file, `openspec/config.yaml`, reading exactly
`store: sessionmail`; the openspec CLI resolves the store from it. A real
`specs/` or `changes/` in the tree would win over the pointer, so none may
come back. Nothing tracked may name a path under the old in-tree layout,
except history: CHANGELOG.md, and citations pinned to a commit
(`<path>:<line> @ <sha>`), which name what the tree said at that commit.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import _openspec
from _export import export_ignored, in_checkout
from _openspec import POINTER, openspec_root, pointer_store, spec_file

ROOT = Path(__file__).resolve().parent.parent

#: The five ratified capabilities the store holds.
SPECS = ("help", "memory", "message", "package", "pager")


def git_lines(*args: str) -> list[str]:
    if not in_checkout() or shutil.which("git") is None:
        pytest.skip("not a git checkout (the git-archive release export): no index to ask")
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True,
                          text=True).stdout.splitlines()


# ── the pointer ──────────────────────────────────────────────────────────

def test_the_pointer_reads_exactly_store_sessionmail():
    assert export_ignored(POINTER).read_bytes() == b"store: sessionmail\n"
    assert pointer_store() == "sessionmail"


def test_openspec_holds_the_pointer_and_nothing_else():
    """No specs/, changes/ or RATIFICATION.md left in the tree: a real one
    would beat the pointer, and the copy would drift from the store."""
    export_ignored(POINTER)
    assert git_lines("ls-files", "--", "openspec") == [POINTER]
    assert sorted(p.name for p in (ROOT / "openspec").iterdir()) == ["config.yaml"]


# ── no path into the old layout ──────────────────────────────────────────

#: A path under the old in-tree layout.
STALE = re.compile(r"openspec/(?:specs|changes|RATIFICATION)")
#: A citation pinned to a commit, possibly wrapped onto the next line.
PINNED = re.compile(r"openspec/[^\s`]*(?:\s*\n\s*#?\s*[^\s`]*)?`?\s@\s[0-9a-f]{7,40}\b")
#: Files whose hits are DATA, not pointers: each one tests a guard or a parser
#: on a string that names the old layout, on purpose.
DATA = {
    "CHANGELOG.md",                   # history: each entry names its change
    "tests/test_openspec_pointer.py",  # this guard
    "tests/test_readme_docs.py",       # the shipped-prose guard's fixtures
    "tests/test_agents_md.py",         # the path scanner's fixtures
    "tests/test_scaffold.py",          # STUB_SPEC refuses the old spelling
}


def stale_hits(text: str) -> list[str]:
    """Each line of TEXT naming the old layout, pinned citations removed."""
    return [line.strip() for line in PINNED.sub("", text).splitlines() if STALE.search(line)]


def test_nothing_tracked_points_into_the_old_layout():
    hits = {}
    for rel in git_lines("ls-files"):
        if rel in DATA or not (ROOT / rel).is_file():
            continue
        try:
            text = (ROOT / rel).read_text()
        except UnicodeDecodeError:
            continue
        if found := stale_hits(text):
            hits[rel] = found
    assert not hits, f"paths into the old in-tree openspec/ layout: {hits}"


def test_the_stale_guard_finds_a_path_and_passes_pinned_history():
    """Prove the guard's YES before trusting its NO."""
    assert stale_hits("see openspec/specs/pager/spec.md\n")
    assert stale_hits("(`openspec/changes/x/`)\n")
    assert stale_hits("the receipts, openspec/RATIFICATION.md\n")
    assert not stale_hits("the OpenSpec change `x`, in the store\n")
    assert not stale_hits("(openspec/changes/pager-genie @ d156f13)\n")
    assert not stale_hits("`openspec/RATIFICATION.md:301 @ 3c918c9`\n")
    assert not stale_hits("# `openspec/changes/a/specs/\n# pager/spec.md:20-23 @ 3c918c9`\n")
    # a pin covers its own citation, not an unpinned one beside it
    assert stale_hits("openspec/specs/a.md:1 @ c854322 and openspec/specs/b.md\n")


# ── read through the pointer ─────────────────────────────────────────────

@pytest.mark.parametrize("capability", SPECS)
def test_each_spec_resolves_through_the_pointer(capability):
    text = spec_file(f"specs/{capability}/spec.md").read_text()
    assert re.search(r"^### Requirement: ", text, re.M), capability


def test_the_root_is_the_stores_and_not_this_tree():
    root = openspec_root()
    assert not root.resolve().is_relative_to(ROOT.resolve())
    assert (root / "RATIFICATION.md").is_file()


# ── the helper's own shapes ──────────────────────────────────────────────

def fake_cli(tmp_path: Path, monkeypatch, stores: list[dict] | None, rc: int = 0) -> None:
    """An `openspec` on PATH whose `store list --json` prints STORES and exits
    RC; STORES None puts no openspec on PATH at all."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    if stores is not None:
        out = tmp_path / "stores.json"
        out.write_text(json.dumps({"stores": stores, "status": []}))
        fake = bin_ / "openspec"
        fake.write_text(f'#!/bin/sh\ncat "{out}"\nexit {rc}\n')
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))
    _openspec._store_root.cache_clear()
    monkeypatch.setattr(_openspec, "pointer_store", lambda: "demo")


@pytest.fixture(autouse=True)
def _fresh_cache():
    yield
    _openspec._store_root.cache_clear()


def test_no_cli_on_path_skips_naming_it(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, None)
    with pytest.raises(pytest.skip.Exception, match="not on PATH"):
        _openspec.openspec_root()


def test_an_unregistered_store_skips_naming_it(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, [{"id": "other", "root": str(tmp_path)}])
    with pytest.raises(pytest.skip.Exception, match="'demo' is not registered"):
        _openspec.openspec_root()


def test_a_registered_store_resolves_and_a_missing_file_fails(tmp_path, monkeypatch):
    store = tmp_path / "store"
    (store / "openspec" / "specs" / "x").mkdir(parents=True)
    (store / "openspec" / "specs" / "x" / "spec.md").write_text("### Requirement: R\n")
    fake_cli(tmp_path, monkeypatch, [{"id": "demo", "root": str(store)}])
    assert _openspec.spec_file("specs/x/spec.md").read_text() == "### Requirement: R\n"
    with pytest.raises(FileNotFoundError):
        _openspec.spec_file("specs/y/spec.md").read_text()


def test_a_cli_that_errors_fails_and_never_skips(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, [], rc=1)
    with pytest.raises(AssertionError, match="exit 1"):
        _openspec.openspec_root()


def test_a_path_under_the_old_layout_is_refused():
    with pytest.raises(AssertionError, match="inside the OpenSpec root"):
        spec_file("openspec/specs/help/spec.md")


def test_a_pointer_that_says_more_fails(tmp_path, monkeypatch):
    bad = tmp_path / "config.yaml"
    bad.write_text("store: sessionmail\nschema: spec-driven\n")
    monkeypatch.setattr(_openspec, "export_ignored", lambda rel: bad)
    with pytest.raises(AssertionError, match="exactly 'store: <id>'"):
        pointer_store()
