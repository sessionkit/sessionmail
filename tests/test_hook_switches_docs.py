"""The per-hook switches, as the shipped text teaches them.

Every switch (`_settings.SWITCH_KEYS`) is in the schema, the settings doc,
the CHANGELOG notes of the release that shipped the switches, the hook
manifest's description, the pager skill and both help pages that name the
hooks; `mail_guard` is in the guard's README too. The text says the switches' truth, not the one-key
world they replaced: no "always on" for the guard, no "the birth is
ungated"; `"mail_guard": false` turns off the subject and body checks only,
the subagent rules stay on; the switches are a person's, by convention, and
each change is logged.

And the passages that teach a switch carry no tracker id and no sha-shaped
word: the shipped text is product documentation.

Reads files and help pages only: nothing is written, nothing is sent.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import re
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from sessionmail import _settings
from sessionmail.pager import cli

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = ROOT / "src/sessionmail/mail.schema.json"
SETTINGS_DOC = ROOT / "docs/settings.md"
CHANGELOG = ROOT / "CHANGELOG.md"
HOOKS_JSON = ROOT / "hooks/hooks.json"
SKILL = ROOT / "skills/pager/SKILL.md"
GUARD_README = ROOT / "hooks/mail-guard/README.md"
HOOK_LINES = ROOT / "src/sessionmail/hook_lines.toml"

#: The two markers the shipped text never gains: a tracker id and a
#: sha-shaped word (the slice's own patterns).
TRACKER_ID = re.compile(r"[A-Z]+-[0-9]+")
SHA_SHAPED = re.compile(r"\b[0-9a-f]{7,40}\b")


def _help(*argv: str) -> str:
    r = CliRunner().invoke(cli, [*argv, "-h"], prog_name="pager")
    assert r.exit_code == 0, r.output
    return r.output


#: A CHANGELOG section's heading: `## [Unreleased]` or `## [X.Y.Z] - DATE`.
SECTION = re.compile(r"^## \[", re.MULTILINE)


def _sections(text: str) -> list[str]:
    """The CHANGELOG's `## [` sections, newest first, each from its heading
    to the next one."""
    starts = [m.start() for m in SECTION.finditer(text)]
    return [text[a:b] for a, b in zip(starts, [*starts[1:], len(text)])]


def _keys_named(section: str) -> list[str]:
    """The switch keys a section names as keys: in code quotes or JSON
    quotes (`ride_along`, "birth"), not as a word in prose."""
    return [key for key in _settings.SWITCH_KEYS
            if re.search(rf"[`\"]{re.escape(key)}[`\"]", section)]


def _switch_notes(text: str) -> str:
    """The switches are named in the notes of the release that shipped
    them, found as the section that names the most switch keys (the newest
    on a tie), by what it says and not by where it sits.

    So the notes stay found when a later entry lands above them under
    [Unreleased] (naming no key, or one key in passing), and when
    scripts/release.py stamps them under a new heading; a key named only in
    another section does not count, since only the one section is read. No
    section that names a key reads as nothing (the length check in the
    surface test then says so)."""
    best, most = "", 0
    for section in _sections(text):
        named = len(_keys_named(section))
        if named > most:
            best, most = section, named
    return best


def _changelog_notes() -> str:
    return _switch_notes(CHANGELOG.read_text(encoding="utf-8"))


def surfaces() -> dict[str, str]:
    """Each shipped place a reader is told about the switches, as read."""
    return {
        "docs/settings.md": SETTINGS_DOC.read_text(encoding="utf-8"),
        "CHANGELOG.md switch notes": _changelog_notes(),
        "hooks/hooks.json description": json.loads(HOOKS_JSON.read_text())["description"],
        "skills/pager/SKILL.md": SKILL.read_text(encoding="utf-8"),
        "pager settings hooks --help": _help("settings", "hooks"),
        "pager hook --help": _help("hook"),
        "_settings docstring": _settings.__doc__ or "",
    }


# ── the schema ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", _settings.SWITCH_KEYS)
def test_every_switch_is_a_boolean_in_the_schema(key):
    props = json.loads(SCHEMA.read_text())["properties"]
    assert key in props, f"{key} is not in mail.schema.json"
    assert props[key]["type"] == "boolean"
    said = props[key]["description"]
    assert "Missing means on" in said, said
    assert "only the boolean false" in said, said
    assert "host mail.json alone" in said, said


def test_the_schema_accepts_a_file_that_sets_every_switch_and_refuses_a_string():
    """The schema keeps `additionalProperties: false`, so a key it did not
    declare would make a valid file invalid: the four are declared, and a
    value that is not a boolean is not valid (the reader ignores it; the
    schema names it wrong)."""
    schema = json.loads(SCHEMA.read_text())
    assert schema["additionalProperties"] is False
    body = {"version": 1, "hooks": True, **{k: False for k in _settings.SWITCH_KEYS}}
    jsonschema.validate(body, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"version": 1, "ride_along": "no"}, schema)


def test_the_schema_hooks_description_names_what_it_gates_and_what_it_does_not():
    said = json.loads(SCHEMA.read_text())["properties"]["hooks"]["description"]
    for key in (_settings.RIDE_ALONG, _settings.STOP_NAG, _settings.BIRTH):
        assert key in said, key
    assert "SessionStart box line" in said
    assert "does not govern the mail guard or the birth itself" in said


# ── every switch in every surface ────────────────────────────────────────────

#: A surface that does not teach a switch, and why: each is a fact about the
#: product, not a gap. These pairs are left out of the cases below, not
#: generated and skipped: a skipped case would read as a check not run.
NOT_THEIRS = {
    # The guard is not a `pager hook` verb: it is hooks/mail-guard-hook.
    ("pager hook --help", _settings.MAIL_GUARD):
        "the guard is not a `pager hook` verb: it is hooks/mail-guard-hook",
}

SURFACE_NAMES = list(surfaces())

#: Every (surface, switch) pair the text must teach: all of them, less
#: NOT_THEIRS.
SURFACE_CASES = [(name, key) for key in _settings.SWITCH_KEYS for name in SURFACE_NAMES
                 if (name, key) not in NOT_THEIRS]


def test_every_excluded_pair_is_a_real_surface_and_switch():
    """An exclusion that names a surface or a key that no longer exists
    would hide nothing and say something false: each one is a real pair,
    and each one is the only case it removes."""
    assert NOT_THEIRS, "nothing is excluded: drop this test and NOT_THEIRS together"
    for name, key in NOT_THEIRS:
        assert name in SURFACE_NAMES, f"{name!r} is not a surface"
        assert key in _settings.SWITCH_KEYS, f"{key!r} is not a switch"
    assert len(SURFACE_CASES) == len(SURFACE_NAMES) * len(_settings.SWITCH_KEYS) - len(NOT_THEIRS)


@pytest.mark.parametrize(("name", "key"), SURFACE_CASES,
                         ids=[f"{key}-{name}" for name, key in SURFACE_CASES])
def test_every_switch_is_named_in_every_surface(name, key):
    text = surfaces()[name]
    assert len(text) > 200, f"{name} read as almost nothing - the check would prove nothing"
    assert key in text, f"{name} does not name {key}"


# ── the CHANGELOG reader ─────────────────────────────────────────────────────

RELEASE_SCRIPT = ROOT / "scripts/release.py"

#: A CHANGELOG as a release finds it: the switches' notes under
#: [Unreleased], an older release below naming one key in passing.
BEFORE_CUT = """# Changelog

Preamble.

## [Unreleased]

### Added

- **One switch per hook.** `ride_along` and `mail_guard` are new.

## [0.1.0] - 2030-06-09

- `stop_nag` was in the old release only.
"""

#: A later entry that lands under [Unreleased], above the release that
#: shipped the switches, and names no key.
UNRELATED = "### Changed\n\n- An unrelated page says which box it reads.\n\n"

#: A later entry that names one key in passing.
IN_PASSING = "### Fixed\n\n- The guard's `mail_guard` line reads cleaner.\n\n"


def _under_unreleased(text: str, entry: str) -> str:
    """TEXT with ENTRY written under its [Unreleased] heading, as the next
    change after a release lands."""
    return text.replace("## [Unreleased]\n", "## [Unreleased]\n\n" + entry, 1)


def _load_release(monkeypatch, changelog: Path):
    """scripts/release.py as a module, its CHANGELOG pointed at `changelog`
    and its day fixed, so the test runs the script's own stamp."""
    spec = importlib.util.spec_from_file_location("release_for_switch_docs", RELEASE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CHANGELOG", changelog)

    class Day:
        @staticmethod
        def today() -> datetime.date:
            return datetime.date(2030, 12, 8)

    monkeypatch.setattr(module, "date", Day)
    return module


def _stamp(monkeypatch, tmp_path: Path, text: str, version: str = "0.2.0") -> str:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    return _load_release(monkeypatch, path).stamp_changelog(version)


def test_the_reader_takes_unreleased_while_it_holds_the_switches_notes():
    got = _switch_notes(BEFORE_CUT)
    assert got.startswith("## [Unreleased]\n")
    assert "ride_along" in got
    assert "stop_nag" not in got, "an older release's notes were read"
    assert "Preamble" not in got


def test_the_release_stamp_moves_the_notes_and_the_reader_follows(monkeypatch, tmp_path):
    """The cut-blocker, as the release makes it: after scripts/release.py's
    own stamp, [Unreleased] read by name is its heading alone, and the
    reader still finds the notes, under the new release's heading."""
    stamped = _stamp(monkeypatch, tmp_path, BEFORE_CUT)
    by_name = next(s for s in _sections(stamped) if s.startswith("## [Unreleased]"))
    assert not by_name.partition("\n")[2].strip(), "the stamp no longer empties [Unreleased]"
    got = _switch_notes(stamped)
    assert got.startswith("## [0.2.0] - 2030-12-08\n")
    assert "ride_along" in got
    assert "stop_nag" not in got


@pytest.mark.parametrize("entry", [UNRELATED, IN_PASSING], ids=["names-no-key", "names-one-key"])
def test_a_later_entry_above_the_release_does_not_move_the_notes(monkeypatch, tmp_path, entry):
    """The next change after the release lands under [Unreleased], above
    the release that shipped the switches: the notes are still that
    release's, whether the new entry names no key or one in passing."""
    later = _under_unreleased(_stamp(monkeypatch, tmp_path, BEFORE_CUT), entry)
    assert _sections(later)[0].startswith("## [Unreleased]\n\n###"), "the entry did not land"
    got = _switch_notes(later)
    assert got.startswith("## [0.2.0] - 2030-12-08\n")
    assert "ride_along" in got
    assert "unrelated page" not in got and "reads cleaner" not in got


def test_the_reader_takes_the_newest_section_on_a_tie():
    text = BEFORE_CUT.replace("`ride_along` and `mail_guard` are", "`ride_along` is")
    assert _switch_notes(text).startswith("## [Unreleased]\n")


def test_a_key_in_prose_is_not_a_key_named():
    """`birth` and `stop_nag` read as words ("the birth", "the Stop nag")
    in notes about other things: only a quoted key counts."""
    assert _keys_named("- the birth's box line; the Stop nag; birth and ride along.") == []
    assert _keys_named('- `birth`, and `"stop_nag": false`.') == [_settings.STOP_NAG, _settings.BIRTH]


def test_the_reader_reads_a_changelog_naming_no_key_as_nothing():
    assert _switch_notes("# Changelog\n\nPreamble.\n") == ""
    assert _switch_notes("# Changelog\n\n## [Unreleased]\n\n") == ""
    assert _switch_notes(BEFORE_CUT.replace("`", "")) == ""


def test_the_real_changelog_reads_the_same_notes_after_the_stamp(monkeypatch, tmp_path):
    """This checkout's CHANGELOG, stamped by scripts/release.py: the notes
    the surface test reads are the same lines before and after the cut, and
    every switch is still in them."""
    before = CHANGELOG.read_text(encoding="utf-8")
    stamped = _stamp(monkeypatch, tmp_path, before, version="9.9.9")
    want = _switch_notes(before).partition("\n")[2]
    got = _switch_notes(stamped)
    assert len(got) > 200, "the stamped CHANGELOG read as almost nothing"
    assert got.partition("\n")[2] == want
    for key in _settings.SWITCH_KEYS:
        assert key in got, f"the stamped CHANGELOG's notes do not name {key}"


@pytest.mark.parametrize("entry", [UNRELATED, IN_PASSING], ids=["names-no-key", "names-one-key"])
def test_the_real_changelog_reads_the_same_notes_under_a_later_entry(monkeypatch, tmp_path, entry):
    """This checkout's CHANGELOG, stamped by scripts/release.py, with the
    next change written under its [Unreleased]: the notes read are the same
    lines as before.

    The entry goes under the [Unreleased] the stamp empties, so it is the
    only thing there: what the CHANGELOG's own [Unreleased] holds on the day
    is in the stamped release, and cannot tie the entry with the notes."""
    before = CHANGELOG.read_text(encoding="utf-8")
    want = _switch_notes(before)
    assert len(want) > 200, "the CHANGELOG read as almost nothing"
    later = _under_unreleased(_stamp(monkeypatch, tmp_path, before, version="9.9.9"), entry)
    alone = ("## [Unreleased]\n\n" + entry).rstrip()
    assert _sections(later)[0].rstrip() == alone, "the entry is not alone there"
    assert _switch_notes(later).partition("\n")[2] == want.partition("\n")[2]


def test_the_switch_notes_name_every_switch_as_a_key():
    """The CHANGELOG notes name each switch by its key, quoted, not only as
    a word: "the birth" in prose is not `birth`."""
    notes = _changelog_notes()
    assert len(notes) > 200, "the CHANGELOG notes read as almost nothing"
    assert _keys_named(notes) == list(_settings.SWITCH_KEYS)


@pytest.mark.parametrize("key", _settings.SWITCH_KEYS)
def test_a_key_dropped_from_the_switch_notes_is_missed(key):
    """The check fails when a key is gone from the switches' notes, even
    though it is named elsewhere: the same section is still read, and the
    key is not in it.

    The real notes are set under a new entry that names only the dropped
    key, with nothing else around them, so what the CHANGELOG's own
    [Unreleased] holds on the day cannot decide which section is read."""
    notes = _switch_notes(CHANGELOG.read_text(encoding="utf-8"))
    assert len(notes) > 200, "the CHANGELOG notes read as almost nothing"
    dropped = re.sub(rf"[`\"]{re.escape(key)}[`\"]", "", notes)
    mutated = (f"# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n"
               f"- `{key}` is named here only.\n\n{dropped}")
    got = _switch_notes(mutated)
    assert got.partition("\n")[0] == notes.partition("\n")[0], "another section was read"
    assert key not in _keys_named(got)


def test_the_guard_readme_teaches_its_switch():
    text = " ".join(GUARD_README.read_text(encoding="utf-8").split())
    assert '"mail_guard": false' in text
    assert "pager settings hooks mail_guard off" in text
    assert "The subagent rules stay on." in text
    assert "Missing means on." in text


def test_the_settings_doc_lists_every_switch_in_the_keys_table():
    text = SETTINGS_DOC.read_text(encoding="utf-8")
    table = text[text.index("## The keys"):text.index("### hooks")]
    for key in _settings.SWITCH_KEYS:
        row = re.search(rf"^\| `{key}` +\| boolean +\| on +\| the host `mail.json` only +\|$",
                        table, re.MULTILINE)
        assert row, f"{key} has no row in the keys table"


def test_the_settings_doc_says_each_switch_is_host_only():
    text = SETTINGS_DOC.read_text(encoding="utf-8")
    override = text[text.index("## What a seat or a session can override"):]
    host_only = override[override.index("- `hooks`"):].split("\n- ")[0]
    for key in _settings.SWITCH_KEYS:
        assert key in host_only, key


# ── what the text says ───────────────────────────────────────────────────────


def test_no_surface_still_calls_the_guard_always_on_or_the_birth_ungated():
    texts = {**surfaces(), "hooks/mail-guard/README.md": GUARD_README.read_text(encoding="utf-8")}
    for name, text in texts.items():
        flat = " ".join(text.split())
        assert "always on" not in flat, name
        assert "birth is ungated" not in flat.lower(), name


def test_the_settings_doc_says_mail_guard_off_leaves_the_subagent_rules_on():
    flat = " ".join(SETTINGS_DOC.read_text(encoding="utf-8").split())
    assert "turns off the subject and body checks only" in flat
    assert "Its subagent rules stay on whatever the switch says" in flat


def test_the_settings_doc_says_the_switch_is_a_persons_by_convention_and_logged():
    """The guard switch is a person's by convention, and audited: nothing
    refuses a main-thread agent on one uid; each change is logged with its
    key."""
    flat = " ".join(SETTINGS_DOC.read_text(encoding="utf-8").split())
    assert "A person's switch, by convention." in flat
    assert "nothing refuses a main-thread agent that writes it, `mail_guard` included" in flat
    assert "logged in `settings.jsonl`" in flat
    assert "the key, from and to" in flat


def test_the_settings_doc_says_all_of_them_off_is_three_keys():
    flat = " ".join(SETTINGS_DOC.read_text(encoding="utf-8").split())
    assert ('"All of them off" is three keys:** `"hooks": false`, `"mail_guard": false` '
            'and `"birth": false`') in flat


def test_the_settings_doc_anchors_resolve():
    """The doc's own `#the-per-hook-switches` link, and the guard README's
    link to it, land on the section's heading."""
    text = SETTINGS_DOC.read_text(encoding="utf-8")
    assert "\n### The per-hook switches\n" in text
    assert "[switches]: #the-per-hook-switches" in text
    assert "../../docs/settings.md#the-per-hook-switches" in GUARD_README.read_text(encoding="utf-8")


# ── no tracker id, no sha ────────────────────────────────────────────────────


def _passages(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _switch_passages() -> list[tuple[str, str]]:
    """Every passage of the shipped text that names a switch: the
    paragraphs of the docs, the help pages and the docstring, the schema's
    descriptions, and hook_lines.toml's switch entries."""
    out: list[tuple[str, str]] = []
    texts = {**surfaces(), "hooks/mail-guard/README.md": GUARD_README.read_text(encoding="utf-8")}
    for name, text in texts.items():
        out += [(name, p) for p in _passages(text)]
    props = json.loads(SCHEMA.read_text())["properties"]
    out += [(f"schema {k}", v["description"]) for k, v in props.items()
            if k == "hooks" or k in _settings.SWITCH_KEYS]
    out += [("hook_lines.toml", p) for p in _passages(HOOK_LINES.read_text(encoding="utf-8"))]
    keys = (*_settings.SWITCH_KEYS, "switch_ignored", "switch_overridden")
    return [(n, p) for n, p in out if any(k in p for k in keys)]


def test_some_passages_name_a_switch():
    """The check below must not pass on nothing."""
    assert len(_switch_passages()) >= 20


@pytest.mark.parametrize("pattern", [TRACKER_ID, SHA_SHAPED], ids=["tracker-id", "sha-shaped"])
def test_no_switch_passage_carries_a_tracker_id_or_a_sha(pattern):
    hits = [(name, m.group(0)) for name, p in _switch_passages() for m in pattern.finditer(p)]
    assert not hits, hits


def test_the_markers_find_what_they_mean():
    """The two patterns, against built examples (built, so this file holds
    neither marker as a literal word)."""
    assert TRACKER_ID.search("see " + "AB" + "-" + "12")
    assert SHA_SHAPED.search("at " + "ab" * 4)
    assert not TRACKER_ID.search("ride_along stop_nag mail_guard birth")
    assert not SHA_SHAPED.search("deadline accepted")


# ── what the text says about exit codes and ignored values ───────────────────


def test_the_key_read_exit_codes_are_all_documented():
    """`pager settings hooks KEY` exits 2 when `hooks` holds a gated hook
    off by mistake, as the bare form does (`_switch_read`): the settings
    doc and the verb's own help name that code for the KEY form too, not
    only 0 and 3."""
    flat = " ".join(SETTINGS_DOC.read_text(encoding="utf-8").split())
    assert "exit 0 it acts, 3 off, and 2 when `hooks` holds it off by mistake" in flat
    epilog = " ".join(_help("settings", "hooks").split())
    assert "either read (bare, or KEY alone): off by mistake" in epilog
    assert "3 off" in epilog


#: The ride-along's switch_ignored line names a value that is not a boolean;
#: a `true` is never "ignored" and never said. Text that says "any other
#: value" is ignored and said once would teach that a `true` is reported.
LOOSE_IGNORED = (
    "any value but false is ignored",
    "any other value is ignored, said once",
    "any other value is ignored (the ride-along says so once)",
    "Any other value is ignored (on), and the ride-along says so once",
)


def test_only_a_value_that_is_not_a_boolean_is_said_to_be_ignored_and_said():
    props = json.loads(SCHEMA.read_text())["properties"]
    texts = {**surfaces(), "schema ride_along": props[_settings.RIDE_ALONG]["description"]}
    for name, text in texts.items():
        flat = " ".join(text.split())
        for loose in LOOSE_IGNORED:
            assert loose not in flat, (name, loose)
    assert "A value that is not a boolean is ignored (on), and the ride-along says so once" \
        in texts["schema ride_along"]
