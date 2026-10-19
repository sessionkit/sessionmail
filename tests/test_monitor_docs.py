"""SM-726 do-006: the sessionmail docs teach the pager monitor AS BUILT.

design.md (h), (l1)-(l7). The monitor is ON by default in the root plugin,
so no doc may still call it "opt-in". The docs a reader meets (the monitor's
doc, docs/pager-monitor.md since SM-755, and the pager skill) name the verbs and settings that were built: `pager
mute` / `pager unmute`, `--kill`, the switch (`"monitor": false`,
`SESSIONMAIL_MONITOR`), `pager_required` / `SESSIONMAIL_PAGER_REQUIRED`,
and headless `-p` seats. They teach that `pager off` / `pager stop` REFUSE
on a monitored seat (design.md (l7)), never do-003's off=mute.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The docs: every place a reader (person or agent) is TOLD something, as
#: opposed to the code and its tests. Globs from the repo root.
DOC_GLOBS = (
    "README.md", "CHANGELOG.md", "INSTALL.md", "*.md",
    "skills/**/*.md", "plugins/**/*.md", "plugins/**/plugin.json",
    ".claude-plugin/*.json", "hooks/hooks.json", "monitors/*.json",
    "docs/**/*",
)

OPT_IN = re.compile(r"\bopt[- ]?in\b", re.IGNORECASE)
MONITOR = re.compile(r"monitor", re.IGNORECASE)


def doc_files(root: Path = ROOT) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in DOC_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and not p.is_symlink() and ".venv" not in p.parts:
                seen[p] = None
    return list(seen)


def passages(path: Path) -> list[str]:
    """A file as the units a reader takes in at once: the paragraphs of a
    text file, or every string value of a JSON file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if path.suffix == ".json":
        out: list[str] = []

        def walk(node):
            if isinstance(node, str):
                out.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(json.loads(text))
        return out
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def calls_the_monitor_opt_in(root: Path = ROOT) -> list[str]:
    """Each doc passage that says "opt-in" about the monitor, as
    `path: passage-start`."""
    hits = []
    for path in doc_files(root):
        for passage in passages(path):
            if OPT_IN.search(passage) and MONITOR.search(passage):
                hits.append(f"{path.relative_to(root)}: {passage.strip()[:80]!r}")
    return hits


# ── "opt-in" is gone ─────────────────────────────────────────────────────

def test_no_sessionmail_doc_calls_the_monitor_opt_in():
    files = doc_files()
    assert len(files) >= 5, f"the doc scan found almost nothing: {files}"
    assert ROOT / "README.md" in files and ROOT / "skills/pager/SKILL.md" in files
    assert calls_the_monitor_opt_in() == []


def test_the_opt_in_scan_can_fail(tmp_path):
    """Prove the instrument's YES before trusting its NO."""
    (tmp_path / "README.md").write_text("# x\n\nThe pager monitor is opt-in.\n")
    (tmp_path / "plugins" / "p" / ".claude-plugin").mkdir(parents=True)
    (tmp_path / "plugins" / "p" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"description": "Opt-in: the pager as a plugin monitor"}))
    (tmp_path / "CHANGELOG.md").write_text("Opt-in by a notmuch key.\n\nThe monitor.\n")
    hits = calls_the_monitor_opt_in(tmp_path)
    assert len(hits) == 2, hits                      # the changelog pairs them in no one paragraph
    assert hits[0].startswith("README.md") and "plugin.json" in hits[1]


# ── the docs name what was built ─────────────────────────────────────────

#: SM-755: the README's monitor section moved to docs/pager-monitor.md.
READER_DOCS = ("docs/pager-monitor.md", "skills/pager/SKILL.md")

#: design.md (l1)-(l7), as built at do-001..do-007.
MUST_NAME = (
    "pager mute", "pager unmute", "--kill",
    "SESSIONMAIL_MONITOR=off", '"monitor": false', "SESSIONMAIL_PAGER=off",
    "pager_required", "SESSIONMAIL_PAGER_REQUIRED",
    "claude -p",
)


def flat(rel: str) -> str:
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("rel", READER_DOCS)
@pytest.mark.parametrize("needle", MUST_NAME)
def test_the_reader_docs_name_what_was_built(rel, needle):
    assert needle in flat(rel), f"{rel} does not name {needle!r}"


@pytest.mark.parametrize("rel", READER_DOCS)
def test_the_docs_teach_the_refusal_not_off_equals_mute(rel):
    """design.md (l7): `pager off` / `pager stop` REFUSE on the monitor, exit
    4. do-003's off=mute wording ("muted, NOT stopped") is not taught."""
    text = flat(rel)
    low = text.lower()
    assert "refuse" in low and "exit 4" in low, rel
    for stale in ("muted, NOT stopped", "it MUTES", "on a monitored seat it mutes",
                  "so do `pager off`/`stop`", "ends a monitor for good"):
        assert stale not in text, f"{rel} still teaches {stale!r}"


@pytest.mark.parametrize("rel", READER_DOCS)
def test_the_docs_say_unmute_when_not_muted_is_a_no_op(rel):
    assert "not muted: already listening" in flat(rel), rel


@pytest.mark.parametrize("rel", READER_DOCS)
def test_the_docs_say_a_required_seat_stays_deaf_after_kill(rel):
    """The do-005 rework review (1ab28206, R1): after `pager stop --kill` a
    required seat stays DEAF even through `pager mute` / `pager off`; only a
    hand `pager listen` or a restart clears it."""
    text = flat(rel)
    at = text.find("stays DEAF")
    assert at != -1, rel
    near = text[max(0, at - 200): at + 200]
    assert "--kill" in near and "pager mute" in near and "restart" in near, near


def test_the_retired_plugin_says_retired_everywhere_it_describes_itself():
    plugin = json.loads((ROOT / "plugins/pager-monitor/.claude-plugin/plugin.json").read_text())
    assert plugin["description"].startswith("Retired"), plugin["description"]
    assert "retired" in (ROOT / "plugins/pager-monitor/README.md").read_text().lower()
