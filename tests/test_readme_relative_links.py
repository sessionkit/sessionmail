"""README.md links into this repo by RELATIVE paths, and each one resolves.

The docs are read where they are: in an editor, or in an unpacked release
offline, with no browser. A link into this repo is therefore a relative
path (`docs/settings.md`, `docs/pager.md#section`, `docs/` for a
directory), never a full GitHub URL to the same repository, and every one of
them names a file or directory here, and a heading when it has an anchor.

Every link in README.md is one of:

  - a relative path into this repo, which must resolve;
  - an absolute `http://` or `https://` URL to somewhere else;
  - a `mailto:` address;
  - an anchor in the same page (`#section`), which must resolve.

Checked forms: inline links and images `[x](y)` / `![x](y)`, reference
definitions `[label]: y`, and HTML `href="..."` / `src="..."`. Fenced code
blocks and inline code spans are not links and are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_readme_docs import headings

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

#: The one repository README.md may name on GitHub.
REPO_URL = "https://github.com/sessionkit/sessionmail"

_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
#: `[text](target ...)` or `![alt](target ...)`; the target is the first
#: whitespace-free run inside the parens (an optional title may follow).
_INLINE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]*)")
#: `[label]: target`, up to three spaces of indent (CommonMark).
_REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?(\S*?)>?(?:\s|$)")
#: HTML attributes that carry a URL.
_HTML_ATTR = re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
#: A full URL to a file or directory in this repo: it must be relative.
_FULL_REPO_LINK = re.compile(re.escape(REPO_URL) + r"/(?:blob|tree|raw)/")
#: Any GitHub URL naming a repository called sessionmail, in any owner.
_GITHUB_REPO = re.compile(r"https?://(?:www\.)?github\.com/([^/\s)>\"']+)/sessionmail\b")

_ALLOWED = re.compile(r"^(?:https?://|mailto:|#)", re.IGNORECASE)


def _mask_code_span(match: re.Match[str]) -> str:
    # Same length, no brackets, parens or quotes: what is left is prose.
    return "x" * len(match.group(0))


def link_targets(text: str) -> list[tuple[int, str]]:
    """(line number, target) for every link outside code, in reading order."""
    found: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        opener = _FENCE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
            continue
        if opener:
            fence = opener.group(1)
            continue
        prose = _CODE_SPAN.sub(_mask_code_span, line)
        reference = _REFERENCE.match(prose)
        if reference:
            found.append((number, reference.group(1)))
        for pattern in (_INLINE, _HTML_ATTR):
            found.extend((number, m.group(1)) for m in pattern.finditer(prose))
    return found


def relative_links(text: str) -> list[tuple[int, str]]:
    """The link targets that are not absolute, mailto or anchor-only."""
    return [(n, t) for n, t in link_targets(text) if not _ALLOWED.match(t)]


def full_repo_links(text: str) -> list[tuple[int, str]]:
    """The link targets that name this repo by a full GitHub URL."""
    return [(n, t) for n, t in link_targets(text) if _FULL_REPO_LINK.match(t)]


def unresolved(text: str, base: Path, page: Path) -> list[tuple[int, str]]:
    """The relative and same-page link targets in TEXT that name no file or
    directory under BASE, or an anchor PAGE (or the linked .md) lacks."""
    wrong = []
    for number, target in relative_links(text) + [
            (n, t) for n, t in link_targets(text) if t.startswith("#")]:
        file, _, anchor = target.partition("#")
        dest = (base / file) if file else page
        if not dest.exists():
            wrong.append((number, target))
        elif anchor and (dest.suffix != ".md" or anchor not in headings(dest)):
            wrong.append((number, target))
    return wrong


def test_readme_has_links_to_check():
    # A scanner that finds nothing would pass every README; make it see some.
    text = README.read_text(encoding="utf-8")
    assert len(link_targets(text)) >= 10
    assert len(relative_links(text)) >= 10


def test_readme_links_into_this_repo_are_relative():
    bad = full_repo_links(README.read_text(encoding="utf-8"))
    assert not bad, (
        "README.md links into this repo by full URL; link a file by its "
        "relative path (docs/<file>.md, a directory as docs/):\n"
        + "\n".join(f"  README.md:{n}: {t}" for n, t in bad)
    )


def test_readme_relative_links_resolve():
    """Each relative link names a file or directory here, and each anchor,
    in this page or in a linked doc, names one of its headings."""
    wrong = unresolved(README.read_text(encoding="utf-8"), ROOT, README)
    assert not wrong, "README.md links to what this repo does not have:\n" + "\n".join(
        f"  README.md:{n}: {t}" for n, t in wrong)


def test_readme_names_only_this_repo_on_github():
    text = README.read_text(encoding="utf-8")
    owners = sorted({m.group(1) for m in _GITHUB_REPO.finditer(text)})
    assert owners in ([], ["sessionkit"]), f"github.com/<owner>/sessionmail owners: {owners}"


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("see [a](docs/a.md) now", True),
        ("see [a](./a.md) now", True),
        ("see [a](../a.md) now", True),
        ("![logo](img/logo.png)", True),
        ("[a]: docs/a.md\n", True),
        ("   [a]: docs/a.md#part\n", True),
        ("[a]: <docs/a.md>\n", True),
        ('<a href="docs/a.md">a</a>', True),
        ("<img src='img/logo.png'>", True),
        ("see [a](https://example.org/a) now", False),
        ("see [a](http://example.org/a) now", False),
        ("[a]: " + REPO_URL + "/blob/master/docs/a.md\n", False),
        ("[a]: mailto:someone@example.org\n", False),
        ("see [a](#section) now", False),
        ("[a]: #section\n", False),
        ('<a href="https://example.org/">a</a>', False),
        ("see [a][b] now", False),
        ("a `[x](y)` code span", False),
        ("```md\n[x](y)\n[a]: b\n```\n", False),
        ("~~~\n[x](y)\n~~~\n[z](w)\n", True),
    ],
)
def test_the_scanner_itself(text: str, flagged: bool):
    assert bool(relative_links(text)) is flagged


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("uv tool install git+https://github.com/sessionkit/sessionmail", True),
        ("[r]: https://github.com/sessionkit/sessionmail\n", True),
        ("no github link here", True),
        ("git+https://github.com/someone-else/sessionmail", False),
    ],
)
def test_the_owner_check_itself(text: str, ok: bool):
    owners = sorted({m.group(1) for m in _GITHUB_REPO.finditer(text)})
    assert (owners in ([], ["sessionkit"])) is ok


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("[a]: docs/a.md\n", False),
        ("[a]: docs/\n", False),
        ("[a]: " + REPO_URL + "\n", False),
        ("[a]: https://github.com/sessionkit/sessionkit/blob/HEAD/docs/a.md\n", False),
        ("[a]: " + REPO_URL + "/blob/master/docs/a.md\n", True),
        ("[a]: " + REPO_URL + "/tree/master/docs\n", True),
        ("[a]: " + REPO_URL + "/raw/master/README.md\n", True),
        ("see [a](" + REPO_URL + "/blob/master/docs/a.md#part) now", True),
        ("a `" + REPO_URL + "/blob/master/a.md` code span", False),
    ],
)
def test_the_full_url_check_itself(text: str, flagged: bool):
    assert bool(full_repo_links(text)) is flagged


def test_the_resolver_itself(tmp_path):
    """Prove the resolver's YES before trusting its NO."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\n## The part\n")
    page = tmp_path / "README.md"
    page.write_text("# Top\n")
    ok = ("[a]: docs/a.md\n[b]: docs/a.md#the-part\n[c]: docs/\n[d]: #top\n"
          "see [e](docs/a.md#a) now\n")
    assert unresolved(ok, tmp_path, page) == []
    bad = ("[a]: docs/b.md\n[b]: docs/a.md#no-such\n[c]: nowhere/\n[d]: #nope\n"
           "see [e](docs/a.md#b) now\n")
    assert [t for _, t in unresolved(bad, tmp_path, page)] == [
        "docs/b.md", "docs/a.md#no-such", "nowhere/", "docs/a.md#b", "#nope"]
