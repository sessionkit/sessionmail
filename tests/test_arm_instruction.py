"""tasks.md 4.6 - the arm instruction's sentence occurs ONCE in the tree.

specs/pager, "The arm instruction has one source" (RATIFIED): the
instruction lives in ONE file in the package; the page, `pager status`, the help, a CLAUDE.md line and a skill read it
from there and NONE restates it.

This file never writes the sentence out. It READS it from `_arm` and then
looks for it - a test that spelled it a second time would itself be the
copy 3.5 exists to remove, and the scan below would fail on this file.

What is a HIT and what is not
-----------------------------
The scan looks for the instruction VERBATIM, the whole line, the words a
teller would print. `openspec/specs/pager/spec.md` is not a hit and is not
meant to be: it STATES THE REQUIREMENT ("the instruction SHALL name the
command and SHALL say that it runs backgrounded by the harness
(`run_in_background: true`), never with a trailing `&`") in its own words,
which is a contract about the sentence, not a telling of it. A spec that
could not describe its own requirement would be no spec. The failure mode
4.6 guards is a SECOND PLACE THAT TELLS A READER WHAT TO TYPE, and that is
always the sentence itself.
"""

from __future__ import annotations

import os
from pathlib import Path

from sessionmail import _arm, pager

REPO = Path(__file__).resolve().parent.parent

#: Not source: build output, caches, the virtualenv, and git's own store.
#: Symlinks are skipped, and `followlinks=False` (the default) keeps the
#: walk inside the tree should a linked directory ever point out of it.
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "dist", "build", "node_modules", ".egg-info",
}


def files_carrying(sentence: str, root: Path) -> list[str]:
    """Every readable file under `root` whose text holds `sentence`, as
    repo-relative paths, sorted. Unreadable and non-text files are skipped
    - they cannot be telling anybody anything."""
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            candidate = Path(dirpath) / name
            if candidate.is_symlink():
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if sentence in text:
                hits.append(str(candidate.relative_to(root)))
    return sorted(hits)


# ── 4.6: once, and only once ─────────────────────────────────────────────

def test_the_arm_sentence_occurs_once_in_the_source_tree():
    """The whole of 4.6. One file holds it; everything else reads it."""
    carriers = files_carrying(_arm.instruction(), REPO)
    assert carriers == [str(Path("src/sessionmail") / _arm.FILENAME)], (
        "the arm instruction is written out in more than one place: "
        f"{carriers}. specs/pager, 'The arm instruction has one source': "
        "read it from the file, do not restate it."
    )


def test_the_scan_can_fail(tmp_path):
    """Charter 2, and section 4's own standard: prove the instrument's YES
    before trusting its NO. A test that cannot fail proves nothing."""
    sentence = _arm.instruction()
    (tmp_path / "quiet").mkdir()
    assert files_carrying(sentence, tmp_path) == []

    (tmp_path / "one.md").write_text(f"to arm: {sentence}\n")
    (tmp_path / "quiet" / "two.py").write_text(f'HOW = "{sentence}"\n')
    assert files_carrying(sentence, tmp_path) == ["one.md", str(Path("quiet/two.py"))]


def test_the_scan_reaches_past_the_package(tmp_path):
    """The guard is only worth having if it walks the WHOLE tree - a second
    copy in a help page, a skill or a test is the copy that bites."""
    for where in ("tests", "skills/pager", "openspec/specs/pager"):
        (tmp_path / where).mkdir(parents=True)
        (tmp_path / where / "f.md").write_text(_arm.instruction())
    assert len(files_carrying(_arm.instruction(), tmp_path)) == 3


# ── the file, and what it must say ───────────────────────────────────────

def test_the_instruction_says_the_three_things_the_spec_requires():
    """"The instruction SHALL name the command and SHALL say that it runs
    backgrounded by the harness (`run_in_background: true`), never with a
    trailing `&`." Properties, asserted one at a time - not a second copy
    of the sentence."""
    text = _arm.instruction()
    assert "pager listen" in text, "it does not name the command"
    assert "run_in_background: true" in text, "it does not say how it is backgrounded"
    # SM-122 do-015 criterion 3: a bare `"&" in text` passes on any sentence
    # carrying an ampersand at all - including one that TELLS you to append
    # `&`, which is the exact instruction the clause exists to forbid. The
    # ratified clause is a WARNING OFF, so assert the warning, not the
    # character.
    assert "&" in text, "it does not warn off the trailing ampersand"
    assert "never a trailing" in text, (
        "it carries an ampersand but does not say `never a trailing &` - the "
        "ratified clause"
    )
    assert text == text.strip() and "\n" not in text, "it is one line, for a page"


def test_the_instruction_is_read_from_the_package_not_the_checkout():
    """It must survive `uv tool install`: read through importlib.resources,
    from inside the package, so an installed copy answers the same."""
    assert _arm.path().is_file()
    assert _arm.path().name == _arm.FILENAME
    assert _arm.path().read_text(encoding="utf-8").strip() == _arm.instruction()


# ── the tellers ──────────────────────────────────────────────────────────

def test_every_page_text_carries_the_instruction():
    """specs/pager scenario "One source, many tellers"."""
    instruction = _arm.instruction()
    assert pager.ARM_HOW == instruction
    for page in (pager.PAGE_WAKE, pager.PAGE_NOTHING, pager.PAGE_NOT_ARMED):
        assert instruction in page


def test_the_refusal_of_pager_on_carries_the_instruction():
    """`pager on` is a teller too: it refuses and says how to arm instead.
    Was the second written-out copy, `pager.py:142` (proposal item 8)."""
    from click.testing import CliRunner

    result = CliRunner().invoke(pager.cli, ["on"], prog_name="pager")
    assert result.exit_code != 0
    said = (result.output or "") + (result.stderr or "")
    assert "there is no `pager on`" in said
    assert _arm.instruction() in said


def test_changing_the_file_changes_every_teller(monkeypatch):
    """"GIVEN the arm instruction changed in its file, WHEN a page,
    `pager status` and the help are printed, THEN all three carry the new
    wording and none carries the old."

    The CLI is a fresh process per invocation, so composing at import time
    is still a live read; this reloads the module to make that visible
    inside one test run, and reloads it back afterwards.
    """
    import importlib

    old = _arm.instruction()
    new = "run `pager listen` some entirely other way (harness-backgrounded)"
    monkeypatch.setattr(_arm, "instruction", lambda: new)
    try:
        importlib.reload(pager)
        assert pager.ARM_HOW == new
        for page in (pager.PAGE_WAKE, pager.PAGE_NOTHING, pager.PAGE_NOT_ARMED):
            assert new in page and old not in page
    finally:
        monkeypatch.undo()
        importlib.reload(pager)
    assert pager.ARM_HOW == old


def test_the_not_yet_line_is_a_teller_and_moves_with_the_file(monkeypatch):
    """Test 20 (SM-706): hook_lines.toml's `not_yet` composes {arm}, so it is
    one more teller of the one source - changing arm.txt changes it, and it
    never spells the sentence itself (the once-only scan above covers the
    toml, which holds `{arm}`, not the words)."""
    import importlib
    import tomllib
    from importlib import resources

    raw = tomllib.loads(resources.files("sessionmail").joinpath("hook_lines.toml")
                        .read_text(encoding="utf-8"))["not_yet"]
    assert "{arm}" in raw and _arm.instruction() not in raw
    assert _arm.instruction() in pager.LINE_NOT_YET
    old = _arm.instruction()
    new = "run `pager listen` some entirely other way (harness-backgrounded)"
    monkeypatch.setattr(_arm, "instruction", lambda: new)
    try:
        importlib.reload(pager)
        assert new in pager.LINE_NOT_YET and old not in pager.LINE_NOT_YET
    finally:
        monkeypatch.undo()
        importlib.reload(pager)
    assert old in pager.LINE_NOT_YET


def test_the_instruction_stands_alone_and_names_bare_pager_listen():
    """SM-891 (walk-145), REWRITTEN ON PURPOSE: this test used to assert
    "your boot" (A42, 95d49d6, receipts F36/F38), pointing the re-arm at the
    seat's own boot-brief line. A stranger - a kit seat, no taskmaster brief -
    never had one, and could not act on the words as printed. The
    instruction now names bare `pager listen`, the design SM-891 carries.

    The A42 residual is ACCEPTED in SM-891: a legacy `role.scope` seat on a
    host failing the plus-v1/subscribe gate still gets its named
    `pager listen <box>` from its taskmaster kick brief, which outranks
    generic advice. The ratified background clause (specs/pager/spec.md
    "The arm instruction has one source") stays."""
    text = _arm.instruction()
    assert "boot instruction" not in text and "your boot" not in text, text
    assert text.startswith("re-arm with `pager listen`,"), text
    assert "run_in_background: true" in text and "never a trailing" in text, text


def test_the_instruction_carries_the_monitor_rider():
    """SM-726 do-006 (design.md (h), (i)): the arm instruction is still right
    for a headless (`-p`) seat, a seat whose monitor gate is off, and a seat
    whose monitor is switched off - but a seat its pager monitor hears for
    has nothing to arm. The ONE file says so, as a rider, so every teller
    carries it and none restates it."""
    text = _arm.instruction()
    assert text.endswith("(not needed when the pager monitor is listening)"), text
    assert text.count("pager monitor") == 1, text


def test_without_rider_drops_only_the_trailing_rider(monkeypatch):
    """SM-726 do-008 (verify-001 F3): the instruction minus its trailing
    parenthesised rider, for a teller where the rider cannot apply. A
    parenthesis inside the line stays; a line with no rider comes back whole;
    it reads the same one file, so it moves with it."""
    text = _arm.instruction()
    bare = _arm.without_rider()
    assert text == f"{bare} (not needed when the pager monitor is listening)"
    assert "(Bash run_in_background: true)" in bare
    monkeypatch.setattr(_arm, "instruction", lambda: "run `pager listen` (in the background)")
    assert _arm.without_rider() == "run `pager listen`"
    monkeypatch.setattr(_arm, "instruction", lambda: "run `pager listen` now")
    assert _arm.without_rider() == "run `pager listen` now"
    monkeypatch.setattr(_arm, "instruction", lambda: "run (this) and (that)")
    assert _arm.without_rider() == "run (this) and"
