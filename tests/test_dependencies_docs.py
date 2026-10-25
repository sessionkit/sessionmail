"""The family's dependency docs: README `## Requirements`, docs/dependencies.md
and .github/dependabot.yml.

The family standard (the kit's docs/family-docs-dependencies.md) asks each
repo for three things, and gives each a check:

- the README's `## Requirements` holds the operating systems as checkboxes
  and one line linking docs/dependencies.md, and no `**Required:**` or
  `**Optional:**` line: it is never a prerequisites list;
- docs/dependencies.md exists, with its lists and upgrade lines on the first
  screen, and the tables after;
- .github/dependabot.yml asks for a daily look at each ecosystem the repo
  has: `uv` at `/` for uv.lock, and `github-actions` only once there is a
  .github/workflows/.

What each entry SAYS is measured against the code by the change's review;
these tests hold the shape, so it cannot drift silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DEPS = ROOT / "docs" / "dependencies.md"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

OPERATING_SYSTEMS = ["macOS", "Linux", "WSL", "Windows"]

#: Not a class: these are held, and their entries say so in place of one.
PENDING = ["Homebrew", "notmuch"]


def section(text: str, heading: str) -> str:
    """The body of `## HEADING`, up to the next H2."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"no '## {heading}'"
    return m.group(1)


def test_requirements_lists_the_operating_systems_as_checkboxes():
    body = section(README.read_text(), "Requirements")
    boxes = re.findall(r"^- \[([ x])\] (\S+)", body, re.M)
    assert [name for _, name in boxes] == OPERATING_SYSTEMS, boxes
    assert ("x", "macOS") in boxes, "macOS is the tested one"


def test_requirements_links_the_dependencies_page_and_lists_nothing():
    text = README.read_text()
    body = section(text, "Requirements")
    assert "[dependencies][dependencies]" in body
    assert re.search(r"^\[dependencies\]: docs/dependencies\.md$", text, re.M)
    assert "**Required:**" not in body and "**Optional:**" not in body
    # Only the checkboxes and the one link line: no prerequisites list.
    bullets = re.findall(r"^- (?!\[[ x]\] )", body, re.M)
    assert bullets == [], body


def test_the_first_screen_holds_the_lists_and_the_upgrade_line():
    text = DEPS.read_text()
    head = text.split("\n## ", 1)[0]
    for word in ("**Required:**", "**Optional:**", "**Pending a ruling:**", "**Upgrade:**"):
        assert word in head, word
    assert "|" not in head, "the tables come after the first screen"
    assert len(head.splitlines()) <= 40


@pytest.mark.parametrize("name", PENDING)
def test_a_pending_dependency_has_no_class_and_no_install_step(name):
    text = DEPS.read_text()
    m = re.search(rf"^### \[{name}\]\[[^\]]+\]\n(.*?)(?=^##|\Z)", text, re.S | re.M)
    assert m, f"no entry for {name}"
    entry = m.group(1)
    assert entry.lstrip().startswith("Status: pending a ruling."), entry
    assert not re.search(r"\b(brew|apt|port) install\b", entry), entry
    pending_tables = section(text, "Required") + section(text, "Optional")
    assert not re.search(rf"^\| \[?{name}\b", pending_tables, re.M), (
        f"{name} is held: it has no row in the Required or Optional table")


def test_the_notmuch_entry_links_its_own_page():
    text = DEPS.read_text()
    assert re.search(r"^\[notmuch-page\]: notmuch\.md$", text, re.M)
    assert "[notmuch][notmuch-page]" in section(text, "Pending a ruling")


def test_dependabot_watches_uv_daily_and_only_what_the_repo_has():
    text = DEPENDABOT.read_text()
    assert re.search(r"^version: 2$", text, re.M)
    entries = re.findall(
        r'^  - package-ecosystem: "([^"]+)"\n    directory: "([^"]+)"\n'
        r'    schedule:\n      interval: "([^"]+)"$', text, re.M)
    ecosystems = {"uv"} if (ROOT / "uv.lock").exists() else set()
    if (ROOT / ".github" / "workflows").is_dir():
        ecosystems.add("github-actions")
    assert {e for e, _, _ in entries} == ecosystems, entries
    assert all(d == "/" and i == "daily" for _, d, i in entries), entries
