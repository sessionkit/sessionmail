"""SM-755: the README is a front door; the rest is in a flat docs/.

The store's kit-docs-standard: the README holds what it is, install,
quickstart and uninstall (plus Configuration and Logs, the family
template's two MUST-HAVEs, and a list of the docs), and nothing else. docs/ is flat Markdown, one subject per file,
linked reference-style (`[text][label]`), never inline. Every link resolves,
and every doc is reachable from the README.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _export import export_ignored, ignored_by_export, in_checkout

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

#: The README's H2s, in order: the standard's four front-door parts, the
#: family template's two MUST-HAVEs, Configuration and Logs (SM-858), then
#: the link list (SM-755 Q1, accepted).
README_SECTIONS = ["Quick start", "Installation", "Uninstall", "Configuration", "Logs", "Docs"]

#: Every shipped doc: the README, AGENTS.md and all of docs/, found, never
#: hand-kept (SM-849: a hand-kept list let two dead links hide). Each one
#: links reference-style only, and only to files the release archive keeps.
SHIPPED_DOCS = ["README.md", "AGENTS.md",
                *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.md"))]

#: Docs `.gitattributes` export-ignores: held to the same link style, but
#: free to link into openspec/, and read through `export_ignored`, so in the
#: release export they SKIP with the export's reason rather than vanish.
EXPORT_IGNORED_DOCS = ["CLAUDE.md"]

REFERENCE_ONLY = SHIPPED_DOCS + EXPORT_IGNORED_DOCS

FENCE = re.compile(r"^```.*?^```[^\n]*$", re.S | re.M)
CODE = re.compile(r"`[^`\n]*`")
USE = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
DEFINITION = re.compile(r"^\[([^\]]+)\]:[ \t]*(\S+)", re.M)
INLINE = re.compile(r"\]\(")
#: This repo's own GitHub URL for a file or directory. Docs are read in an
#: editor or offline, so a link into this repo is a relative path instead,
#: and a full URL to it fails.
FULL_REPO_LINK = re.compile(r"^https?://(?:www\.)?github\.com/sessionkit/sessionmail/(?:blob|tree|raw)/")


def prose(text: str) -> str:
    """TEXT without code: a `[--json]` in a code span is not a link."""
    return CODE.sub("", FENCE.sub("", text))


def label(text: str) -> str:
    return " ".join(text.split()).lower()


def slug(heading: str) -> str:
    """A heading's anchor, as GitHub makes it."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def headings(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text())   # a heading keeps its code spans
    return {slug(m.group(1)) for m in re.finditer(r"^#+ (.+)$", text, re.M)}


def definitions(path: Path) -> dict[str, str]:
    return {label(m.group(1)): m.group(2) for m in DEFINITION.finditer(path.read_text())}


def doc(rel: str) -> Path:
    """ROOT/REL; an export-ignored doc skips in the release export."""
    return export_ignored(rel) if rel in EXPORT_IGNORED_DOCS else ROOT / rel


def test_the_readme_holds_the_front_door_and_nothing_else():
    h2 = re.findall(r"^## (.+)$", prose(README.read_text()), re.M)
    assert h2 == README_SECTIONS, h2


@pytest.mark.parametrize("rel", REFERENCE_ONLY)
def test_links_are_reference_style_and_every_label_resolves(rel):
    path = doc(rel)
    text = prose(path.read_text())
    assert not INLINE.search(text), f"{rel} has an inline link"
    defined = definitions(path)
    used = {label(m.group(2) or m.group(1)) for m in USE.finditer(text)}
    assert used <= set(defined), f"{rel}: undefined labels {sorted(used - set(defined))}"
    assert set(defined) <= used, f"{rel}: unused definitions {sorted(set(defined) - used)}"


@pytest.mark.parametrize("rel", REFERENCE_ONLY)
def test_every_link_target_exists(rel):
    """Every target exists, and a SHIPPED doc's target ships too. In a
    checkout openspec/ is on disk, so `exists()` alone cannot see a link the
    release archive breaks: `git check-attr export-ignore` does (SM-849). In
    the export, an export-ignored target is absent and `exists()` fails it."""
    path = doc(rel)
    targets = {}
    for name, target in definitions(path).items():
        assert not FULL_REPO_LINK.match(target), f"{rel} [{name}]: {target} is this repo: link it relatively"
        if "://" in target or target.startswith("mailto:"):
            continue
        file, _, anchor = target.partition("#")
        dest = (path.parent / file).resolve() if file else path
        assert dest.exists(), f"{rel} [{name}]: {target} does not exist"
        if anchor:
            assert anchor in headings(dest), f"{rel} [{name}]: no heading #{anchor} in {file}"
        targets[dest] = f"[{name}]: {target}"
    if rel in SHIPPED_DOCS:
        dead = sorted(targets[d] for d in ignored_by_export(targets))
        assert not dead, f"{rel} ships, but these targets are export-ignored: {dead}"


#: SHIPPED PROSE, the scope of the openspec/ guard below: every Markdown file
#: the release archive keeps. Found by walking the tree, so a new doc is in
#: scope the moment it exists, in the export as in a checkout. Left out:
#: export-ignored files (CLAUDE.md, openspec/ itself), CHANGELOG.md (history:
#: it names the change each entry came from), tests/ (fixtures are data), and
#: any dot directory (.git, .venv, caches). Source comments are not prose.
PROSE_SKIP_DIRS = {"openspec", "tests"}
PROSE_SKIP_FILES = {"CLAUDE.md", "CHANGELOG.md"}


def shipped_prose(root: Path = ROOT) -> list[str]:
    found = []
    for path in root.rglob("*.md"):
        rel = path.relative_to(root)
        if rel.parts[0] in PROSE_SKIP_DIRS or (len(rel.parts) == 1 and rel.name in PROSE_SKIP_FILES):
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        found.append(str(rel))
    return sorted(found)


SHIPPED_PROSE = shipped_prose()

#: A pointer into openspec/, which the release archive leaves out.
OPENSPEC = re.compile(r"openspec/")
#: A commit-pinned citation, `openspec/<path>:<line> @ <sha>`: history, it
#: names what the tree said at that commit, so it is not a dead end.
PINNED = re.compile(r"openspec/[^\s`]*\s@\s[0-9a-f]{7,40}\b")
#: The AGENTS.md wording: the pointer says the archive leaves it out.
SAYS_SO = re.compile(r"the release archive leaves\b", re.I)


def blocks(text: str) -> list[str]:
    """TEXT's paragraphs and list items, each with its whitespace folded:
    the unit a pointer and its "the release archive leaves" sentence share."""
    out, cur = [], []
    for line in text.splitlines():
        if not line.strip() or re.match(r"\s*(?:[-*+]|\d+\.)\s", line):
            if cur:
                out.append(" ".join(cur))
            cur = [line.strip()] if line.strip() else []
        else:
            cur.append(line.strip())
    if cur:
        out.append(" ".join(cur))
    return out


def dead_openspec_pointers(text: str) -> list[str]:
    """Each block of TEXT that points into openspec/ unpinned and never says
    the release archive leaves it out: a dead end in the export."""
    dead = []
    for block in blocks(text):
        if not OPENSPEC.search(PINNED.sub("", block)):
            continue
        if not SAYS_SO.search(block):
            dead.append(block)
    return dead


@pytest.mark.parametrize("rel", SHIPPED_PROSE)
def test_shipped_prose_points_into_openspec_only_the_agents_md_way(rel):
    """SM-849: openspec/ is export-ignored, so a shipped doc that names a
    path in it sends an archive reader nowhere. A pointer is allowed when its
    paragraph (or list item) says so the AGENTS.md way, "in the git
    repository; the release archive leaves `openspec/` out", or when it is a
    commit-pinned citation, `openspec/<path>:<line> @ <sha>`."""
    dead = dead_openspec_pointers((ROOT / rel).read_text())
    assert not dead, f"{rel} points into openspec/ without saying the archive leaves it out: {dead}"


def test_the_prose_scope_reaches_every_shipped_markdown_file():
    """The walk and git agree: every tracked .md the archive keeps, outside
    the stated exclusions, is in SHIPPED_PROSE, so the export run checks the
    same files a checkout does."""
    if not in_checkout() or shutil.which("git") is None:
        pytest.skip("not a git checkout (the git-archive release export): git ls-files has no index")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.md"],
                             check=True, capture_output=True, text=True).stdout.split()
    keep = {ROOT / rel for rel in tracked} - ignored_by_export(ROOT / rel for rel in tracked)
    expected = {str(p.relative_to(ROOT)) for p in keep}
    expected -= {"CHANGELOG.md"}
    expected = {r for r in expected if not r.startswith("tests/")}
    assert expected <= set(SHIPPED_PROSE), sorted(expected - set(SHIPPED_PROSE))
    assert "README.md" in SHIPPED_PROSE and "AGENTS.md" in SHIPPED_PROSE
    assert "CLAUDE.md" not in SHIPPED_PROSE


def test_the_openspec_guard_finds_a_dead_pointer_and_passes_the_allowed_forms():
    """Prove the guard's YES before trusting its NO."""
    assert dead_openspec_pointers("See `openspec/specs/pager/spec.md`.\n")
    assert dead_openspec_pointers("Bare (openspec/changes/x, item 3).\n")
    # the AGENTS.md wording, wrapped across lines and in a list item
    assert not dead_openspec_pointers(
        "- `openspec/specs/pager/spec.md` - the contract, in the git repository; the\n"
        "  release archive leaves `openspec/` out.\n")
    # a commit-pinned citation is history, not a pointer
    assert not dead_openspec_pointers("(`openspec/specs/pager/spec.md:270-272 @ c854322`).\n")
    # but a pinned citation does not cover an unpinned pointer beside it
    assert dead_openspec_pointers("(`openspec/a.md:1 @ c854322`) and `openspec/b.md`.\n")
    # the archive sentence covers only its own block, not the next one
    assert dead_openspec_pointers(
        "`openspec/a.md`; the release archive leaves `openspec/` out.\n\n"
        "- `openspec/b.md`\n")
    # the CLI's name is not a path
    assert not dead_openspec_pointers("Run `openspec validate --all --strict`.\n")


def test_the_prose_scope_skips_the_export_ignored_and_history(tmp_path):
    for rel in ["README.md", "CLAUDE.md", "CHANGELOG.md", "docs/a.md", "openspec/specs/x.md",
                "tests/fixtures/f.md", ".venv/lib/pkg/README.md", "skills/s/CLAUDE.md"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    assert shipped_prose(tmp_path) == ["README.md", "docs/a.md", "skills/s/CLAUDE.md"]


def test_every_doc_is_reached_from_the_readme():
    """No orphan: every SHIPPED file in docs/ is linked from the README's
    list. A path `.gitattributes` export-ignores (docs/architecture/) is not
    in the release archive, so the README does not link it: a link there
    would be dead in the archive. Outside a git checkout no such path is on
    disk at all."""
    import subprocess

    def export_ignored(path: Path) -> bool:
        rel = str(path.relative_to(ROOT)) + ("/" if path.is_dir() else "")
        out = subprocess.run(["git", "-C", str(ROOT), "check-attr", "export-ignore", "--", rel],
                             capture_output=True, text=True).stdout
        return out.strip().endswith(": export-ignore: set")

    linked = {(ROOT / t.partition("#")[0]).resolve()
              for t in definitions(README).values()}
    for doc in sorted((ROOT / "docs").iterdir()):
        if export_ignored(doc):
            assert doc.resolve() not in linked, f"{doc.relative_to(ROOT)} is linked, but never ships"
            continue
        assert doc.resolve() in linked, f"{doc.relative_to(ROOT)} is linked from no README line"


def test_the_instrument_finds_an_inline_link_and_an_undefined_label():
    """Prove the checks' YES before trusting their NO."""
    text = prose("See [x](y.md) and [z][nope].\n\n`[--json]` is code.\n")
    assert INLINE.search(text)
    assert [label(m.group(2) or m.group(1)) for m in USE.finditer(text)] == ["nope"]
    assert slug("Index at delivery (opt-in)") == "index-at-delivery-opt-in"
    assert slug("How `--monitor` behaves") == "how---monitor-behaves"
    repo = "https://github.com/sessionkit/sessionmail"
    assert FULL_REPO_LINK.match(repo + "/blob/master/docs/a.md#b")
    assert FULL_REPO_LINK.match(repo + "/tree/master/docs")
    assert FULL_REPO_LINK.match(repo + "/raw/master/README.md")
    assert not FULL_REPO_LINK.match(repo)
    assert not FULL_REPO_LINK.match("https://github.com/sessionkit/sessionkit/blob/HEAD/a.md")
    assert not FULL_REPO_LINK.match("docs/a.md")


#: A `~` straight after `=` in a command's argument is not expanded by bash,
#: zsh or sh, so `notmuch --config=~/x` hands notmuch a literal `~`, and it
#: finds no config. A doc's command line writes `$HOME` there instead.
TILDE_AFTER_EQUALS = re.compile(r"(?<![\w$])--[a-z][a-z-]*=~")


@pytest.mark.parametrize("rel", SHIPPED_DOCS)
def test_no_command_line_puts_a_tilde_after_equals(rel):
    text = (ROOT / rel).read_text()
    hits = [line.strip() for line in text.splitlines() if TILDE_AFTER_EQUALS.search(line)]
    assert not hits, f"{rel}: `~` after `=` is not expanded: {hits}"


def test_the_tilde_check_finds_its_yes():
    assert TILDE_AFTER_EQUALS.search("notmuch --config=~/.config/notmuch/sessionkit/config new")
    assert not TILDE_AFTER_EQUALS.search('notmuch --config="$HOME/.config/x" new')
    assert not TILDE_AFTER_EQUALS.search("(`~` is the account's home)")
