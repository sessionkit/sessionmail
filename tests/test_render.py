"""The built-in renderer - task 4b of openspec/changes/send-in-python.

`_render.py` is session-mail-render's engine COPIED into the package
(RULED decision 2). These are the pins of its
733-line shell suite (`test_session_mail_render.sh`) that survive the move
to memory - the predicate, the page's first byte, no script of any kind,
author markup escaped (o03), the title rule, the dark palette - plus the
one pin the move ADDED: the page does not depend on whether Pygments is
importable. The file-writing pins (0600, no husk on a short write) stayed
with the file-writing CLI; in memory there is no file. Element claims are
made by an HTML PARSER, not a grep (the shell suite's own rule).

The parity test against the bash renderer retired with the bash
(2030-10-01, R19); see the note where it stood.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from sessionmail import _render
from sessionmail._render import EXTENSIONS, output_sane, render_page


class Elements(HTMLParser):
    def __init__(self, page: str):
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def named(self, tag):
        return [a for t, a in self.tags if t == tag]


# ── the one predicate ────────────────────────────────────────────────────────

@pytest.mark.parametrize("data, sane", [
    (b"<!DOCTYPE html>\n<html>", True),
    (b"<!DOCTYPE", True),
    (b"<!doctype html>", False),                                        # case-sensitive
    (b" <!DOCTYPE html>", False),                                       # byte one, no whitespace
    (b"<html><body>", False),
    (b"<!DOCTYP", False),
    (b"", False),
])
def test_the_predicate_is_strict_byte_one(data, sane):
    assert output_sane(data) is sane                                    # mail-render.lib.sh, D6


def test_there_is_one_predicate_in_the_package():
    src = Path(_render.__file__).parent
    hits = [p.name for p in src.glob("*.py") if "<!DOCTYPE\"" in p.read_text()
            or "b\"<!DOCTYPE\"" in p.read_text()]
    assert hits == ["_render.py"], hits                                 # two spellings WILL drift


# ── the page ─────────────────────────────────────────────────────────────────

def test_the_page_begins_doctype_and_is_whole():
    page = render_page("# Hi\n\nbody\n")
    assert page.startswith("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n")
    assert page.endswith("</body>\n</html>\n")
    assert output_sane(page.encode())


def test_an_empty_body_refuses():
    with pytest.raises(ValueError):
        render_page(" \n\t\n")


@pytest.mark.parametrize("text, title, want", [
    ("# First\n\n# Second\n", None, "First"),
    ("Setext\n======\n\nbody\n", None, "Setext"),                        # the rendered H1, not a regex
    ("```sh\n# a shell comment\n```\n", None, "Mail"),                  # a fence is not a title
    ("no heading\n", None, "Mail"),
    ("# *Bold* &amp; <b>raw</b>\n", None, "Bold & <b>raw</b>"),           # tags stripped, entities round-tripped
    ("# ignored\n", "--wip", "--wip"),                                  # given wins, option-shaped or not
])
def test_the_title_rule(text, title, want):
    page = render_page(text, title)
    import html
    got = page[page.index("<title>") + 7:page.index("</title>")]
    assert html.unescape(got) == want


# ── no script, author markup never live (do-008, o03) ────────────────────────

def test_no_script_element_for_a_plain_or_a_mermaid_body():
    for text in ("# plain\n\nbody\n", "```mermaid\ngraph TD; A-->B\n```\n"):
        els = Elements(render_page(text))
        assert els.named("script") == []
    mermaid = Elements(render_page("```mermaid\ngraph TD; A-->B\n```\n"))
    assert any("mermaid" in (a.get("class") or "") for a in mermaid.named("div"))


def test_raw_html_is_text_not_elements():
    body = ('<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n'
            '<div onclick="alert(3)">d</div>\n')
    page = render_page(body)
    els = Elements(page)
    assert els.named("script") == [] and els.named("img") == []
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page              # legible, not lost
    assert not any(k.lower().startswith("on") for _, attrs in els.tags for k in attrs)


def test_attr_list_is_out_and_its_handler_never_lands():
    assert "attr_list" not in EXTENSIONS
    els = Elements(render_page('[x](https://example.com){: onmouseover="alert(3)" }\n'))
    assert all("onmouseover" not in attrs for _, attrs in els.tags)


@pytest.mark.parametrize("link, kept", [
    ("javascript:alert(4)", False),
    ("&#106;avascript:alert(5)", False),                                # the browser's view, unescaped first
    ("JaVaScRiPt:alert(6)", False),
    ("java\tscript:alert(7)", False),
    ("data:text/html,x", False),
    ("https://example.com/a", True),
    ("mailto:judy@example.com", True),
    ("#fragment", True),
])
def test_link_schemes_are_allowlisted(link, kept):
    anchors = Elements(render_page(f"[t]({link})\n")).named("a")
    assert len(anchors) == 1                                            # the link text survives
    assert ("href" in anchors[0]) is kept


def test_ordinary_markdown_is_unharmed_and_not_double_escaped():
    page = render_page("a & b < c\n\n| h |\n|---|\n| 1 |\n\n- [x] done\n\n`<b>` in code\n")
    els = Elements(page)
    assert els.named("table") and els.named("input")
    assert "a &amp; b &lt; c" in page and "&amp;amp;" not in page
    assert "<code>&lt;b&gt;</code>" in page


# ── the environment must not change the page ─────────────────────────────────

def test_code_is_never_pygments_highlighted_even_where_pygments_imports():
    pytest.importorskip("pygments")                                     # pytest brings it; the point is it is HERE
    page = render_page("```sh\n# comment\necho $(x)\n```\n")
    assert '<span class="' not in page
    assert '<pre class="highlight"><code class="language-sh"># comment\necho $(x)</code></pre>' in page


def test_the_dark_palette_repaints_the_body_itself():
    page = render_page("# x\n")
    dark = page[page.index("@media (prefers-color-scheme: dark)"):]
    body_rule = dark[dark.index("body {"):dark.index("}", dark.index("body {"))]
    assert "background: #121417" in body_rule
    assert "var(--" not in page                                         # Thunderbird resolves no var()


# ── parity with the bash renderer: RETIRED 2030-10-01 ───────────────────────
# `_render.py` was a copy of session-mail-render, and a parametrised test
# held it byte-equal to the bash on a corpus of hostile, folded and code-heavy
# bodies. The bash retired with the session repo, so
# the test skipped silently from then on (a retirement audit, R19). It
# is retired, not repointed: the built-in renderer is now the only one.


# ── SM-394: top-level H2 sections fold, like the md-page spine ──────────────

FOLD_BODY = ("# Title\n\nlead\n\n## One\n\ntext one\n\n- a\n- b\n\n## Two\n\n"
             "> ## nested\n> quoted\n\ntail\n")


def test_each_top_level_h2_section_is_an_open_fold():
    page = render_page(FOLD_BODY)
    body = page.split("<body>", 1)[1]
    assert body.count('<details class="fold" open="open"><summary><h2') == 2
    # the lead before the first H2 stays outside every fold
    assert body.index("<p>lead</p>") < body.index("<details")
    # a section's content is inside its fold, before the next one opens
    one, two = body.split('<details class="fold"')[1:3]
    assert "<p>text one</p>" in one and "<li>a</li>" in one
    assert "<p>tail</p>" in two


def test_a_nested_h2_is_not_folded_and_the_page_stays_balanced():
    body = render_page(FOLD_BODY).split("<body>", 1)[1]
    assert "<blockquote>\n<h2" in body            # left where the author put it
    assert body.count("<details") == body.count("</details>") == 2


def test_no_top_level_h2_means_no_fold_and_no_fold_css():
    for text in ("no heading\n", "# Only a title\n\n### h3\n", "> ## quoted h2\n"):
        page = render_page(text)
        assert "<details" not in page and "details.fold" not in page, text


def test_the_fold_css_rides_only_with_a_fold():
    assert "details.fold > summary" in render_page(FOLD_BODY)


# ── SM-913: a newline in the body is a line break (nl2br) ───────────────────
# Python-Markdown joins a single newline into a space, so lines written one
# under another collapsed into one line. `nl2br` in EXTENSIONS keeps them:
# each line ends in `<br />`. It still does not start a list straight after a
# line (that is a separate change); here the lines only stay apart.

class BreaksInside(HTMLParser):
    """Counts `<br>` elements by the nearest enclosing pre / table / p."""

    WATCHED = ("pre", "table", "p")

    def __init__(self, page: str):
        super().__init__()
        self.open: list[str] = []
        self.breaks = dict.fromkeys(self.WATCHED, 0)
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            if self.open:
                self.breaks[self.open[-1]] += 1
        elif tag in self.WATCHED:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.WATCHED and self.open and self.open[-1] == tag:
            self.open.pop()


NL2BR_BODY = "**Fixes, cheapest first**\n1. one\n2. two\n"


def test_nl2br_is_in_the_extension_set():
    assert "nl2br" in EXTENSIONS


def test_lines_under_each_other_keep_their_breaks():
    body = render_page(NL2BR_BODY).split("<body>", 1)[1]
    assert ("<p><strong>Fixes, cheapest first</strong><br />\n"
            "1. one<br />\n2. two</p>") in body
    assert BreaksInside(body).breaks["p"] == 2


def test_a_fence_and_a_table_carry_no_break():
    text = (NL2BR_BODY + "\n```sh\nfirst\n\nsecond\nthird\n```\n\n"
            "| h | i |\n|---|---|\n| x | y |\n| z | w |\n")
    page = render_page(text)
    seen = BreaksInside(page)
    assert seen.breaks["p"] == 2                       # nl2br is live on this page
    assert seen.breaks["pre"] == 0 and seen.breaks["table"] == 0
    assert "<code class=\"language-sh\">first\n\nsecond\nthird</code>" in page
    assert len(Elements(page).named("td")) == 4
