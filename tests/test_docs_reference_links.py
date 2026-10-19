"""SM-811: the docs use reference-style links (`[text][label]`), never inline.

kit-docs-standard: "Reference-style links (`[text][label]`), never inline,
never wiki." Nothing guarded it, so docs/postfix-settings.md:6 and
README.md:278 drifted to `[text](target)`. This test reads README.md,
AGENTS.md (it ships in the release archive, SM-820) and every Markdown file
under docs/, and fails on any inline link.

Exempt, because they are not links a reader follows from prose:
  - image links `![alt](src)`;
  - fenced code blocks (``` or ~~~);
  - inline code spans (`like [this](that)`).
A link whose TEXT is a code span (``[`path`][label]``) is still a link and
is checked: the span is masked, not dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: README.md and AGENTS.md, plus every Markdown file under docs/, at any depth.
DOC_FILES = sorted(
    [ROOT / "README.md", ROOT / "AGENTS.md", *(ROOT / "docs").rglob("*.md")]
)

_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
#: `[text](target)` not preceded by `!`. Text may hold balanced-free brackets
#: only; a nested `]` ends it, which is enough for prose links.
_INLINE_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(")


def _mask_code_span(match: re.Match[str]) -> str:
    # Same length, no brackets or parens: the link text stays a link.
    return "x" * len(match.group(0))


def inline_links(text: str) -> list[tuple[int, str]]:
    """(line number, line) for each line holding an inline `[text](...)`."""
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
        if _INLINE_LINK.search(_CODE_SPAN.sub(_mask_code_span, line)):
            found.append((number, line))
    return found


def test_there_are_docs_to_check():
    assert (ROOT / "README.md") in DOC_FILES
    assert (ROOT / "AGENTS.md") in DOC_FILES
    assert (ROOT / "docs" / "postfix-configuration.md") in DOC_FILES


@pytest.mark.parametrize("path", DOC_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_inline_links(path: Path):
    bad = inline_links(path.read_text(encoding="utf-8"))
    rel = path.relative_to(ROOT)
    assert not bad, "inline links (use [text][label] and a label definition):\n" + "\n".join(
        f"  {rel}:{n}: {line.strip()}" for n, line in bad
    )


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("see [a](#b) now", True),
        ("[`path`](path/README.md)", True),
        ("see [a][b] now\n\n[b]: #b\n", False),
        ("![alt](img.png)", False),
        ("a `[x](y)` code span", False),
        ("```md\n[x](y)\n```\n", False),
        ("~~~\n[x](y)\n~~~\n[z](w)\n", True),
        ("````\n```\n[x](y)\n````\n", False),
    ],
)
def test_the_scanner_itself(text: str, flagged: bool):
    assert bool(inline_links(text)) is flagged
