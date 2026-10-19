"""The built-in renderer: one markdown body to a self-contained HTML page.

Task 4b of openspec/changes/send-in-python (decision 2: built in,
overridable, switchable off, `--raw` per mail). COPIED from the earlier
bash renderer, session-mail-render (551 lines): the CSS, the extension set and the o03 mail profile below are
that file's lines 166-377 BYTE FOR BYTE, and `render_page` is its main()
without the file handling. The parity test renders a corpus through both
and compares the pages whole.

What the sender relies on, and what a later edit must not "restore":

  - the page ALWAYS begins `<!DOCTYPE html>`, byte one - `output_sane` is
    the nine-byte check (`mail-render.lib.sh`'s `mail_render_output_sane`,
    now a function beside the renderer, as 4b says);
  - NO <script> of any kind: md-page's mermaid CDN loader is stripped,
    and author markup is escaped, never live (o03). `attr_list` is out of
    the extension set on purpose;
  - the title is `--title`, else the RENDERED page's first H1, else "Mail".

What changed in the move: the built-in renders IN MEMORY. The bash fed the
renderer a 0600 temp copy of the private body and read a 0600 temp page
back; in-process there is no file at all, so `write_private` and its
partial-write cleanup had nothing left to guard and did not come across.
An EXTERNAL renderer ($SESSIONMAIL_RENDERER, `--renderer PATH`) still gets
the file contract, from the sender.

Two dependencies, not one: `markdown` and `pymdown-extensions` (the
original's PEP 723 block). Imported only when a render is owed, so agent
mail never loads them and a broken install fails the render, loudly.
"""

from __future__ import annotations

import html as html_mod
import xml.etree.ElementTree as etree
import re

import markdown
import pymdownx.superfences  # noqa: F401  (fence_div_format lives here)
import pymdownx.highlight

# NOT in the original, and the one line that is not a copy. The page must
# not depend on what else is installed: pymdownx highlights code with
# Pygments whenever it can IMPORT it, and superfences loads `highlight` with
# its own defaults whatever config is passed. The bash renderer's isolated
# uv environment has no Pygments; the package's test environment does
# (pytest brings it), and there a ```sh fence became `<span class="k">` soup
# the bash never wrote - measured with the same markdown 3.10.3
# and pymdown-extensions 12.0.1 on both sides. The CSS styles none of those
# classes. Pin the bash's effective behaviour: no Pygments, ever.
# LOUD on a rename: assigning a missing attribute just makes a
# new one and pins nothing, and pages would drift silently wherever Pygments
# is installed. Refusing here makes every owed render exit 4, everywhere.
if not hasattr(pymdownx.highlight, "pygments"):
    raise ImportError("pymdownx.highlight has no `pygments` switch any more - the "
                      "no-Pygments pin cannot hold, so the page would drift from the "
                      "bash renderer's; refusing to render (see the pin's comment)")
pymdownx.highlight.pygments = False
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.1rem 1.15rem 4rem;
  background: #ffffff; color: #1a1a1a;
  font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
        Roboto, sans-serif;
  max-width: 44rem; margin-inline: auto;
  -webkit-text-size-adjust: 100%;
}
h1 { font-size: 1.55rem; line-height: 1.25; margin: 0.2rem 0 0.8rem; }
h2 {
  font-size: 1.18rem; margin: 1.9rem 0 0.6rem; padding-top: 0.9rem;
  border-top: 1px solid #e3e3e3;
  letter-spacing: 0.02em; text-transform: uppercase;
  color: #0b6bcb;
}
h3 { font-size: 1.05rem; margin: 1.3rem 0 0.4rem; }
p, ul, ol { margin: 0.55rem 0; }
li { margin: 0.3rem 0; }
li > p { margin: 0.2rem 0; }
a { color: #0b6bcb; text-decoration: none; }
a:active { opacity: 0.7; }
code {
  font: 0.86em ui-monospace, "SF Mono", Menlo, monospace;
  background: #f5f5f5; padding: 0.12em 0.35em;
  border-radius: 5px;
}
pre {
  background: #f5f5f5; padding: 0.8rem; border-radius: 10px;
  overflow-x: auto;
}
pre code { background: none; padding: 0; }
blockquote {
  margin: 0.8rem 0; padding: 0.1rem 0 0.1rem 0.9rem;
  border-left: 3px solid #8e44ad; color: #6a6a6a;
}
table {
  border-collapse: collapse; width: 100%; margin: 0.8rem 0;
  font-size: 0.92em; display: block; overflow-x: auto;
}
th, td {
  border: 1px solid #e3e3e3; padding: 0.45rem 0.6rem;
  text-align: left;
}
th { background: #f5f5f5; }
hr { border: none; border-top: 1px solid #e3e3e3; margin: 1.6rem 0; }
.admonition {
  margin: 1rem 0; padding: 0.7rem 0.95rem; border-radius: 10px;
  border-left: 4px solid #0b6bcb; background: #f5f5f5;
}
.admonition-title {
  font-weight: 650; margin: 0 0 0.35rem; font-size: 0.98em;
}
.admonition.warning { border-left-color: #b58105; }
.admonition.danger, .admonition.error {
  border-left-color: #c0392b;
}
.admonition.tip, .admonition.success { border-left-color: #1e8e3e; }
details.admonition summary {
  font-weight: 650; cursor: pointer; margin: -0.2rem 0 0.2rem;
}
.task-list-item { list-style: none; margin-left: -1.2rem; }
.task-list-item input { margin-right: 0.45rem; }
.mermaid { text-align: center; margin: 1rem 0; }
.pagefoot {
  margin-top: 3rem; padding-top: 0.8rem;
  border-top: 1px solid #e3e3e3;
  color: #6a6a6a; font-size: 0.8em;
}
/* Thunderbird's dark reader does not resolve var(); literal colours only */
@media (prefers-color-scheme: dark) {
  body { margin: 0; padding: 1.1rem 1.15rem 4rem; background: #121417; color: #e8e6e3; font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 44rem; margin-inline: auto; -webkit-text-size-adjust: 100%; }
  h2 { font-size: 1.18rem; margin: 1.9rem 0 0.6rem; padding-top: 0.9rem; border-top: 1px solid #2a2e33; letter-spacing: 0.02em; text-transform: uppercase; color: #6cb2f7; }
  a { color: #6cb2f7; text-decoration: none; }
  code { font: 0.86em ui-monospace, "SF Mono", Menlo, monospace; background: #1d2126; padding: 0.12em 0.35em; border-radius: 5px; }
  pre { background: #1d2126; padding: 0.8rem; border-radius: 10px; overflow-x: auto; }
  blockquote { margin: 0.8rem 0; padding: 0.1rem 0 0.1rem 0.9rem; border-left: 3px solid #b07cc6; color: #9a9a9a; }
  th, td { border: 1px solid #2a2e33; padding: 0.45rem 0.6rem; text-align: left; }
  th { background: #1d2126; }
  hr { border: none; border-top: 1px solid #2a2e33; margin: 1.6rem 0; }
  .admonition { margin: 1rem 0; padding: 0.7rem 0.95rem; border-radius: 10px; border-left: 4px solid #6cb2f7; background: #1d2126; }
  .admonition.warning { border-left-color: #e6b422; }
  .admonition.danger, .admonition.error { border-left-color: #e5735f; }
  .admonition.tip, .admonition.success { border-left-color: #57bb70; }
  .pagefoot { margin-top: 3rem; padding-top: 0.8rem; border-top: 1px solid #2a2e33; color: #9a9a9a; font-size: 0.8em; }
}
"""

# md-page's MERMAID_JS CDN loader constant was here. STRIPPED — the
# docstring's "NO <script> OF ANY KIND" bullet carries the decision and both
# reasons. The .mermaid CSS class above stays (inert styling for the source
# div); the superfences custom fence below stays (fence -> styled div).

# md-page's extension set MINUS `attr_list` — o03 step 2.
# attr_list exists so a PAGE author can hang classes on
# elements; in MAIL it is an attribute-injection door with no legitimate use.
# Measured live before the drop: `[x](https://example.com){: onmouseover="..." }`
# in a body wrote the handler straight onto the <a>. Do NOT "restore" it as a
# port-fidelity fix — see MailRenderProfile below and the docstring bullet.
EXTENSIONS = [
    "admonition",
    "footnotes",
    "sane_lists",
    "smarty",
    "tables",
    "toc",
    "pymdownx.details",
    "pymdownx.superfences",
    "pymdownx.tasklist",
    "pymdownx.tilde",
]

EXTENSION_CONFIGS = {
    "pymdownx.superfences": {
        "custom_fences": [{
            "name": "mermaid",
            "class": "mermaid",
            "format": pymdownx.superfences.fence_div_format,
        }],
    },
    "pymdownx.tasklist": {"custom_checkbox": False},
    "toc": {"permalink": False},
}


# --- o03: the mail render profile -----------------
# AUTHOR MARKUP NEVER BECOMES LIVE HTML. Three parts, all stdlib + the engine
# we already depend on; no new dependency (o03's measured claim, re-measured
# here). See decisions/d08-html-posture.md and
# options/o03-escape-raw-html.md (the option, with the four measured paths).
#
#   1. raw HTML is DEREGISTERED (below) — block and inline alike, so author
#      HTML is parsed as TEXT and serialised escaped, exactly as a fenced
#      ```html example already was;
#   2. `attr_list` is dropped from EXTENSIONS (above);
#   3. this treeprocessor is the BELT on the output tree: no `on*` attribute
#      survives, and `href`/`src` carry only http/https/mailto.
#
# Part 3 covers paths nobody has found yet, including anything a future
# extension emits. It does NOT police markup the RENDERER ITSELF emits after
# an engine upgrade — that was o01, the named FALLBACK, deliberately NOT
# ruled and deliberately NOT built here.

MAIL_URL_SCHEMES = frozenset(("http", "https", "mailto"))
_URL_ATTRIBUTES = frozenset(("href", "src"))
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
# Browsers ignore ASCII whitespace and C0 controls INSIDE a URL scheme, so
# `java\tscript:` and `java\nscript:` are live javascript: URLs. Strip them
# before deciding, or the allowlist is bypassable with a tab.
_URL_NOISE_RE = re.compile(r"[\x00-\x20\x7f]+")


def url_scheme_allowed(value: str) -> bool:
    """True when `value` names no scheme, or one this mail profile allows.

    Order is load-bearing: UNESCAPE first (`&#106;avascript:` is
    `javascript:` to a browser and would sail past a raw compare), then strip
    control characters and whitespace, then match the scheme. A value with no
    scheme at all — a relative path, a `#fragment`, a protocol-relative
    `//host/x` — is allowed: it is not an execution vector, and http/https
    are on the allowlist anyway.
    """
    probe = _URL_NOISE_RE.sub("", html_mod.unescape(value))
    m = _SCHEME_RE.match(probe)
    return m is None or m.group(1).lower() in MAIL_URL_SCHEMES


class _MailSafeAttributes(Treeprocessor):
    """Delete `on*` handlers and non-allowlisted `href`/`src` schemes.

    Deleting the ATTRIBUTE rather than the element is deliberate: nothing is
    withheld from the reader (o03's "legible, not lost" — the link text, the
    alt text and the surrounding prose all still render), only the behaviour
    goes. The text/plain part is the byte-identical source markdown regardless
    (AC2/AC3), so the full original is always one part away.
    """

    def run(self, root):
        for el in root.iter():
            for name in list(el.attrib):
                low = name.lower()
                if low.startswith("on"):
                    del el.attrib[name]
                elif low in _URL_ATTRIBUTES and not url_scheme_allowed(el.attrib[name]):
                    del el.attrib[name]
        return root


class MailRenderProfile(Extension):
    """Raw HTML off, plus the attribute belt. o03, in one extension."""

    def extendMarkdown(self, md):
        # `deregister` RAISES on a name the engine no longer has, and that is
        # the behaviour we want: if a Python-Markdown upgrade renames these
        # processors, every send that owes a render fails LOUDLY at send time
        # (D5/D7 refuse) instead of quietly resuming passthrough. o03's
        # "Against" section names this as the accepted risk; it fails closed.
        md.preprocessors.deregister("html_block")     # block-level raw HTML
        md.inlinePatterns.deregister("html")          # inline raw HTML
        # Priority 1: AFTER `inline` (20) and `prettify` (10), so every
        # element the document will contain already exists when we sweep.
        md.treeprocessors.register(
            _MailSafeAttributes(md), "mail_safe_attributes", 1,
        )


# --- every H2 section folds, like an md-page `--fold` page --------------------
# Ordinary mail renders as nicely as a folded page: every section collapsible,
# each in its place. md-page `--fold` (its `fold_h2`) is where this came from.
# md-page folds by regex on the page string, which splits an H2 nested in a
# blockquote or list and leaves the <details> unbalanced. Here it is a
# treeprocessor over the TOP-LEVEL children only: a nested H2 stays where it
# is. Sections are OPEN, so nothing is hidden until the reader folds it. A
# body with no top-level H2 is untouched, and FOLD_CSS is emitted only when
# a fold exists, so such a page stays byte-identical to the bash renderer's.
FOLD_CSS = """
details.fold > summary { list-style: none; cursor: pointer; }
details.fold > summary::-webkit-details-marker { display: none; }
details.fold > summary h2 { display: inline-block; margin: 0; }
details.fold > summary h2::before { content: '\\25BE '; opacity: .6; }
details.fold:not([open]) > summary h2::before { content: '\\25B8 '; }
"""


class _FoldH2(Treeprocessor):
    """Wrap each top-level H2 and everything after it, up to the next
    top-level H2, in `<details class="fold" open><summary><h2>`. Content
    before the first H2 (an H1, a lead) stays outside every fold."""

    def run(self, root):
        kids = list(root)
        if not any(el.tag == "h2" for el in kids):
            return root
        for el in kids:
            root.remove(el)
        fold = None
        for el in kids:
            if el.tag == "h2":
                fold = etree.SubElement(root, "details", {"class": "fold", "open": "open"})
                fold.tail = "\n"
                summary = etree.SubElement(fold, "summary")
                summary.append(el)
            elif fold is None:
                root.append(el)
            else:
                fold.append(el)
        return root


class MailFold(Extension):
    """The H2 fold as an extension. Registered after `inline` (20) and
    `prettify` (10), so headings are whole, and BEFORE the o03 attribute belt
    (1), so the belt still sweeps the tree last."""

    def extendMarkdown(self, md):
        md.treeprocessors.register(_FoldH2(md), "mail_fold_h2", 5)


DOCTYPE = b"<!DOCTYPE"


def output_sane(page: bytes) -> bool:
    """Strict, case-sensitive, byte one (mail-render.lib.sh, D6): a page
    that does not begin `<!DOCTYPE` does not ship."""
    return page[:9] == DOCTYPE


def render_page(text: str, title: str | None = None) -> str:
    """session-mail-render's main(), minus the files. Raises ValueError on
    an empty body (the original's exit 2)."""
    if not text.strip():
        raise ValueError("refusing to render empty input")
    body = markdown.markdown(
        text, extensions=EXTENSIONS + [MailRenderProfile(), MailFold()],
        extension_configs=EXTENSION_CONFIGS,
    )
    if not title:
        # The FIRST H1 OF THE RENDERED PAGE, not the first `# ` line of the
        # source: a shell comment inside a fence is not a title.
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, flags=re.S)
        title = (
            html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            if m else "Mail"
        )
    page = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"color-scheme\" content=\"light dark\">\n"
        "<meta name=\"supported-color-schemes\" content=\"light dark\">\n"
        f"<title>{html_mod.escape(title)}</title>\n"
        f"<style>{CSS}{FOLD_CSS if '<details class="fold"' in body else ''}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
    return page
