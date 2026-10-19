"""AGENTS.md holds the instructions; CLAUDE.md imports it (SM-755, SM-820).

The SK-36 shape: any coding agent reads AGENTS.md, and Claude Code reads it
through a CLAUDE.md whose FIRST line is `@AGENTS.md` (no symlink, no global
setting).

The release archive ships AGENTS.md and leaves CLAUDE.md out (SM-820):
`claude plugin validate --strict` fails on a CLAUDE.md at the plugin root.
So AGENTS.md must stand alone in `git archive`: every relative link or path
it names is in the archive, and it names no host, task id, person or seat.
The map of the specs (openspec/, which the archive leaves out) lives in
CLAUDE.md, below the import.

Until SM-820 this file asserted that AGENTS.md was export-ignored too (SM-755).
Inverting that assertion is a deliberate contract change (SK-101).
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from _export import export_ignored

ROOT = Path(__file__).resolve().parent.parent


def test_claude_md_is_the_import_and_nothing_restated():
    path = export_ignored("CLAUDE.md")
    claude = path.read_text()
    assert claude.splitlines()[0] == "@AGENTS.md"
    assert not path.is_symlink()
    assert "Three rules" not in claude   # the content lives in AGENTS.md


def test_agents_md_carries_the_spec_rule():
    agents = (ROOT / "AGENTS.md").read_text()
    head = "\n".join(agents.splitlines()[:40])
    assert "REFER TO THE SPECS BEFORE BUILDING ANYTHING" in head


def test_claude_md_carries_the_map_of_the_specs():
    """The openspec/ paths moved here from AGENTS.md: CLAUDE.md never ships."""
    claude = export_ignored("CLAUDE.md").read_text()
    for target in ("openspec/specs/", "openspec/changes/", "openspec/RATIFICATION.md"):
        assert f"]: {target}" in claude, target


# --- the release archive --------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout


def _need_git() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")


@pytest.mark.parametrize(("name", "ignored"), [("CLAUDE.md", True), ("AGENTS.md", False)])
def test_the_release_archive_leaves_out_claude_md_only(name, ignored):
    _need_git()
    want = "set" if ignored else "unspecified"
    assert _git("check-attr", "export-ignore", "--", name).strip() == \
        f"{name}: export-ignore: {want}"


@pytest.fixture(scope="module")
def archive() -> tarfile.TarFile:
    """`git archive` of HEAD, plus any uncommitted edits to tracked files.

    `git stash create` writes a commit object for the working tree and
    touches no ref and no stash entry; it prints nothing on a clean tree, so
    in CI (and on any clean checkout) this IS `git archive HEAD`.
    """
    _need_git()
    rev = _git("stash", "create").strip() or "HEAD"
    data = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", rev],
                          check=True, capture_output=True).stdout
    return tarfile.open(fileobj=io.BytesIO(data))


def _archived(tar: tarfile.TarFile, name: str) -> str:
    member = tar.extractfile(name)
    assert member is not None, f"{name} is not a file in the archive"
    return member.read().decode("utf-8")


def test_the_release_archive_ships_agents_md_and_not_claude_md(archive):
    names = set(archive.getnames())
    assert "AGENTS.md" in names
    assert "CLAUDE.md" not in names
    assert not any(n == "openspec" or n.startswith("openspec/") for n in names)


_FENCE = re.compile(r"^\s{0,3}(```+|~~~+).*?^\s{0,3}\1[^\n]*$", re.S | re.M)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_DEFINITION = re.compile(r"^\s{0,3}\[[^\]]+\]:[ \t]*<?(\S+?)>?(?:[ \t].*)?$", re.M)
_INLINE_TARGET = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)")
#: A bare path in prose: a word holding a `/` that ends in `/` or a file
#: extension (`and/or` is neither).
_PROSE_PATH = re.compile(r"(?<![\w/.:-])((?:[\w.-]+/)+(?:[\w.-]+\.\w+)?)(?![\w/])")
#: A code span is a path when it is one word with a `/`, or one word ending
#: in a file extension we ship (`AGENTS.md`, `plugin.json`).
_PATHLIKE = re.compile(r"^[\w.<>-]*(/[\w.<>-]*)+$|^[\w.-]+\.(md|json|toml|py|lock|yml|yaml)$")


def relative_paths(text: str) -> list[str]:
    """Every relative link target or path named in a Markdown TEXT.

    Fenced code is skipped (a shell recipe is not a path to follow). A path
    with a `<placeholder>` is checked up to the placeholder.
    """
    text = _FENCE.sub("", text)
    found: list[str] = []
    found += _DEFINITION.findall(text)
    found += _INLINE_TARGET.findall(text)
    found += [s.strip() for s in _CODE_SPAN.findall(text) if _PATHLIKE.match(s.strip())]
    prose = _CODE_SPAN.sub(" ", text)
    prose = _DEFINITION.sub(" ", prose)
    found += _PROSE_PATH.findall(prose)
    paths = []
    for target in found:
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        target = target.partition("#")[0].partition("<")[0]
        target = target.removeprefix("./").rstrip("/")
        if target:
            paths.append(target)
    return paths


def test_every_path_in_the_archived_agents_md_is_in_the_archive(archive):
    names = {n.rstrip("/") for n in archive.getnames()}
    paths = relative_paths(_archived(archive, "AGENTS.md"))
    assert "docs/pager.md" in paths   # the scan sees the one link it has
    dead = sorted({p for p in paths if p not in names})
    assert not dead, f"AGENTS.md names paths the release archive leaves out: {dead}"


#: Words an AGENTS.md for any coding agent, in any checkout, must not carry.
#: Built from pieces, so the scan of the shipped tests never reads them whole.
_HOSTS = ("m" "5", "m" "s1", "m" "s2", "m" "s3", "m" "ba", "kp" "01")
_PEOPLE = ("Mi" "ke", "Bai" "ley", "Co" "ra", "P" "ip", "Jean" "nie")
_SEATS = ("foreman", "general_foreman", "general_contractor", "superintendent")
_TASK_ID = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]{1,7}-\d{1,5}(?![\w-])")
_NOT_A_TASK_ID = {"UTF-8", "UTF-16", "ISO-8601", "RFC-5322"}
_SEAT_ADDRESS = re.compile(r"\+(super|gc|support|foreman|worker)\b|\b\w+\+[\w.-]+@?")
_SESSION_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def _word(word: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(word)}(?![\w-])", flags)


def local_names(text: str) -> list[str]:
    """Every host, task id, person or seat named in TEXT."""
    hits: list[str] = []
    for host in _HOSTS:
        hits += [m.group(0) for m in _word(host, re.I).finditer(text)]
    for person in _PEOPLE:
        hits += [m.group(0) for m in _word(person).finditer(text)]
    for seat in _SEATS:
        hits += [m.group(0) for m in _word(seat, re.I).finditer(text)]
    hits += [m for m in _TASK_ID.findall(text) if m not in _NOT_A_TASK_ID]
    hits += [m.group(0) for m in _SEAT_ADDRESS.finditer(text)]
    hits += _SESSION_ID.findall(text)
    return hits


def test_agents_md_names_no_host_task_person_or_seat():
    hits = local_names((ROOT / "AGENTS.md").read_text())
    assert not hits, f"AGENTS.md names local hosts, tasks, people or seats: {hits}"


@pytest.mark.parametrize(
    ("text", "paths"),
    [
        ("[`openspec/`][x]\n\n[x]: openspec/\n", ["openspec"]),
        ("see [a](docs/a.md#top)", ["docs/a.md"]),
        ("a change in `changes/`, not here", ["changes"]),
        ("each carries `proposal.md` (why)", ["proposal.md"]),
        ("[`openspec/changes/<name>/`][c]", ["openspec/changes"]),
        ("read openspec/specs/ first", ["openspec/specs"]),
        ("```\nmake install  # uv -e .\n```\n", []),
        ("`message` moves and/or `pager` alerts", []),
        ("[w]: https://example.com/a.md\n", []),
    ],
)
def test_the_path_scanner_itself(text, paths):
    """Prove the scan's YES before trusting its NO."""
    assert sorted(set(relative_paths(text))) == sorted(paths)


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("runs on m" "5 and m" "s2", True),
        ("filed as SM-820", True),
        ("Mi" "ke ruled it", True),
        ("ask the foreman", True),
        ("mail sessionmail+super", True),
        ("session 07502dcd-b56d-46fe-af52-eaf28820d3a4", True),
        ("UTF-8 text, one package, three commands", False),
        ("Mail for coding agents. `message` moves messages.", False),
    ],
)
def test_the_name_scanner_itself(text, flagged):
    assert bool(local_names(text)) is flagged
