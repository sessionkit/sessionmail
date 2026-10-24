"""The exported tree carries no time of day beside a dated stamp.

A STAMP is a time, a weekday and a date. Every stamp is a HIT, at any hour on any
day. The guard runs the measure over `git archive HEAD` of this repo, so an
export-ignored path never counts, and fails on any hit, naming each file and its
count, never the matched text.

The matcher is IMPORTED from `scripts/strip-work-stamps` (the stamp pattern and
hits()), not copied, so the measure and the strip cannot drift.

A second guard reads the CHANGELOG in the same archive: it names no commit sha
(7 to 40 hex digits as a word), since a sha points into history the exported
repository does not carry. A bare tracker reference is fine. The script has no `.py` suffix, so it is loaded by path with
SourceFileLoader, the way tests/test_memory_run_gate.py loads session-mail-run.

Every example stamp below is BUILT at run time, so this file is never a hit.
Outside a git checkout (an sdist, an unpacked archive) the guard skips.
"""

from __future__ import annotations

import collections
import importlib.util
import io
import re
import subprocess
import sys
import tarfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "strip-work-stamps"


def _load_strip():
    loader = SourceFileLoader("strip_work_stamps", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _git_toplevel(repo: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out).resolve() if out else None


def measure(repo: Path, ref: str = "HEAD") -> collections.Counter:
    """Hits per file in `git archive REF`: the release measure, file -> count."""
    sws = _load_strip()
    data = subprocess.run(["git", "-C", str(repo), "archive", ref],
                          capture_output=True, check=True).stdout
    files: collections.Counter = collections.Counter()
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        for m in t.getmembers():
            if not m.isfile():
                continue
            body = t.extractfile(m).read()
            if sws.is_binary(body):
                continue
            n = len(sws.hits(body))
            if n:
                files[m.name] += n
    return files


#: A commit sha written as a word: 7 to 40 lowercase hex digits.
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def changelog_shas(repo: Path, ref: str = "HEAD") -> list[int]:
    """Line numbers in CHANGELOG.md of `git archive REF` that carry a sha."""
    data = subprocess.run(["git", "-C", str(repo), "archive", ref, "CHANGELOG.md"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        text = t.extractfile("CHANGELOG.md").read().decode("utf8", "replace")
    return [n for n, line in enumerate(text.splitlines(), 1) if SHA_RE.search(line)]


def report(files: collections.Counter) -> str:
    """The failure text: counts and paths only, never the matched text."""
    lines = [f"time-of-day stamps in git archive HEAD: files={len(files)} "
             f"hits={sum(files.values())} (strip: scripts/strip-work-stamps, then commit)"]
    lines += [f"    {c:3} {name}" for name, c in files.most_common()]
    return "\n".join(lines)


def _stamp(hh: int, mm: int, day: str = "Sun", date: str = "2030-12-08") -> str:
    return f"{hh:02d}:{mm:02d} {day} {date}"


# ── the guard ────────────────────────────────────────────────────────────────

def test_exported_tree_has_no_time_of_day_stamps():
    top = _git_toplevel(REPO)
    if top != REPO:
        pytest.skip(f"not a git checkout of this repo: {REPO}")
    files = measure(REPO)
    if files:
        pytest.fail(report(files), pytrace=False)


def test_the_exported_changelog_names_no_commit_sha():
    top = _git_toplevel(REPO)
    if top != REPO:
        pytest.skip(f"not a git checkout of this repo: {REPO}")
    lines = changelog_shas(REPO)
    assert not lines, (f"CHANGELOG.md in git archive HEAD names a commit sha on "
                       f"{len(lines)} line(s): {lines}; drop the sha, keep the line")


# ── the guard is not vacuous, and its report never shows the text ────────────

def test_the_imported_matcher_finds_a_built_stamp():
    sws = _load_strip()
    assert len(sws.hits(_stamp(9, 0))) == 1
    assert len(sws.hits(_stamp(16, 59).encode())) == 1
    assert len(sws.hits(_stamp(17, 0))) == 1
    assert len(sws.hits(_stamp(8, 59))) == 1
    assert len(sws.hits(_stamp(0, 0))) == 1
    assert len(sws.hits(_stamp(10, 0, day="Sat"))) == 1
    assert len(sws.hits(_stamp(23, 59, day="Sun").encode())) == 1
    # The day name is the matcher's input, not the date's weekday: a work-hour
    # stamp on a weekday name is the case this guard is named for (SM-917
    # moved the default to Sun, so name every day here).
    for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert len(sws.hits(_stamp(10, 0, day=day))) == 1, day
    assert sws.hits(f"{10:02d}:{0:02d} Thu") == []          # no date, no stamp


def test_the_sha_matcher_finds_a_built_sha_and_nothing_else():
    sha = "ab" + "c1234"                                  # built, so this file holds none
    ref = "(" + "SM" + "-123)"                            # a bare tracker ref, built too
    assert SHA_RE.search(f"through that map (`{sha}`)")
    assert SHA_RE.search(sha + "def0" * 8)                # a full 40-digit sha
    for plain in (ref, "0.2.0", "2030-12-08", "abc123", "v1 and v2", "ab" + "c12345g"):
        assert not SHA_RE.search(plain), plain


def test_changelog_shas_reads_the_archive(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
         "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    sha, ref = "ab" + "c1234", "SM" + "-1"
    (repo / "CHANGELOG.md").write_text(f"# Changelog\n\n- a fix ({ref})\n- a map (`{sha}`)\n")
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "base"], check=True)
    assert changelog_shas(repo) == [4]
    (repo / "CHANGELOG.md").write_text(f"# Changelog\n\n- a fix ({ref})\n- a map\n")
    subprocess.run(g + ["commit", "-qam", "drop"], check=True)
    assert changelog_shas(repo) == []


def test_this_file_and_the_script_are_not_hits():
    sws = _load_strip()
    for path in (Path(__file__), SCRIPT):
        assert sws.hits(path.read_bytes()) == [], path.name


def test_measure_counts_archive_members_and_reports_no_text(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
         "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    stamp = _stamp(10, 30)
    (repo / "a.md").write_text(f"noted {stamp}\nand {stamp}\n")
    (repo / "src").mkdir()
    (repo / "src" / "b.py").write_text(f"# {stamp}\n")
    (repo / "late.md").write_text(_stamp(17, 30, day="Sun") + "\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "c.md").write_text(stamp + "\n")
    (repo / ".gitattributes").write_text("/ignored/** export-ignore\n")
    (repo / "bin.dat").write_bytes(b"\0" + stamp.encode())
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "base"], check=True)
    (repo / "uncommitted.md").write_text(stamp + "\n")      # not in HEAD's archive

    files = measure(repo)
    assert dict(files) == {"a.md": 2, "src/b.py": 1, "late.md": 1}
    text = report(files)
    assert "files=3 hits=4" in text
    assert "a.md" in text and "src/b.py" in text and "late.md" in text
    assert stamp not in text and "10:30" not in text and "2030-12-08" not in text

    (repo / "a.md").write_text("noted Sun 2030-12-08\n")
    (repo / "src" / "b.py").write_text("# Sun 2030-12-08\n")
    (repo / "late.md").write_text("Sun 2030-12-08\n")
    subprocess.run(g + ["commit", "-qam", "strip"], check=True)
    assert measure(repo) == collections.Counter()


def test_the_guard_skips_outside_a_git_checkout(tmp_path, monkeypatch):
    unpacked = tmp_path / "sessionmail-0.2.0"      # an sdist or archive, no .git
    unpacked.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(sys.modules[__name__], "REPO", unpacked)
    with pytest.raises(pytest.skip.Exception, match="not a git checkout"):
        test_exported_tree_has_no_time_of_day_stamps()
