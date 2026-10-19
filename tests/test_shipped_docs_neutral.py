"""What ships reads as product documentation, not as a project's notebook.

The release archive (`git archive`, export-ignore applied) carries every doc
a user reads: the markdown, text and html files, the plugin manifests, the
hook manifest. Each `--help` page of `message`, `pager` and `memory` is
read by users too. None of them may say who decided something, when, or on
which internal host: people's names, decision records and their ids, voice
timestamps, fleet host names, internal roles used as people. Outside the
CHANGELOG they carry no tracker ids either; the CHANGELOG keeps bare ones.

The fact a provenance line supported stays in the doc; the provenance stays
in the repo's history. A real product word that looks like a marker (the
author line of a manifest, the memory label for decisions) is allowed by an
explicit entry below, each with its reason, and every entry must still be
in use, so a stale allowance fails.

Every marker is built from pieces, so this file never matches itself
(`test_this_file_never_matches_itself` holds that).
"""

from __future__ import annotations

import fnmatch
import io
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from sessionmail import memory, message, pager

ROOT = Path(__file__).resolve().parent.parent

#: Every time a planted line carries is SYNTHETIC: the epoch's, built at run
#: time, so this file holds no real working time and no clock literal.
_EPOCH = time.gmtime(0)
CLOCK = time.strftime("%H:%M", _EPOCH)
CLOCK_S = time.strftime("%H:%M:%S", _EPOCH)
DAY = time.strftime("%a", _EPOCH)
DATE = time.strftime("%Y-%m-%d", _EPOCH)
RFC_DATE = time.strftime("%a, %d %b %Y %H:%M:%S +0000", _EPOCH)

# --- the markers -------------------------------------------------------------

_DAY = "Mon|Tue|Wed|Thu|Fri|Sat|Sun"
_SLICE_KINDS = ["do", "fix", "rca", "repro", "triage", "review", "verify", "deliver", "land",
                "regression-check", "test", "impl", "design", "spec", "write", "audit",
                "check", "probe"]

#: A name stands alone when no letter or digit touches it: `_` is not one,
#: so a name inside an identifier (`<NAME>_SID`, `test_<name>_pages`) or an
#: address (`<name>@host`, `seats/<name>`) is read too. In ANY case: a
#: lower-cased name is the same person. The one product word among them,
#: the Python installer, is allowed where it is one (PRODUCT_WORDS).
_ALONE = r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])"
_NAMES = ["mi" "ke", "co" "ra", "p" "ip", "jean" "nie", "ta" "rs", "k" "in", "doc" "tor"]
_HOSTS = ["m" "5", "m" "ba", "m" "s[12]", "kp" "01"]

#: (label, pattern).
MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("a person", re.compile(_ALONE % "|".join(_NAMES), re.IGNORECASE)),
    ("a decision record", re.compile(r"\b(?:%s)\b" % "|".join(
        ["RUL" "ING", "RUL" "INGS", "RUL" "ED"]))),
    # An internal role used as a person ("a <role>'s subject once ran ...").
    # A role inside an address example (`<project>+<role>`) is a product word.
    ("a role as a person", re.compile(r"\b(?:[Aa]|[Tt]he) (?:%s)(?:'s)?\b" % "|".join(
        ["su" "per", "fore" "man"]))),
    ("a voice timestamp", re.compile(r"\bvo" r"ice \d{1,2}:\d{2}")),
    # A decision's clock time with no weekday ("rul-ed" and a HH:MM).
    ("a ruling time", re.compile(r"\b[Rr]ul" r"ed \d{1,2}:\d{2}")),
    ("a private timestamp", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})? (?:%s)\b" % _DAY)),
    ("an internal host", re.compile(_ALONE % "|".join(_HOSTS), re.IGNORECASE)),
    # The word "tailnet" is a product word (MAGICDNS_SUFFIX, `example.ts.net`
    # in the postfix docs); the leak is one real tailnet's name.
    ("an internal tailnet", re.compile(r"\bspar" r"row-hydra\b")),
    ("an internal role address", re.compile(r"sessionmail\+" "super")),
    # The crew's improvement box, quoted from a live seat's pager line. It is
    # a box of the team that runs the project, not a product word.
    ("an internal box", re.compile(r"\bkai" r"zen\b", re.IGNORECASE)),
    # Work-process references: a slice of a task, a review item, a design
    # question, a go-ahead. They fail in the CHANGELOG too; the bare tracker
    # id beside them is what the CHANGELOG keeps.
    ("a work-slice id", re.compile(r"\b(?:%s)-\d{3}\b" % "|".join(_SLICE_KINDS))),
    # An item number, or a review's year, is at most six digits; a commit sha
    # is seven or more, so an all-digit sha after "review" is not an item.
    ("a review or design item", re.compile(r"\b(?:review|slices?|design) §?[A-Z]?\d{1,6}\b")),
    ("a go-ahead", re.compile(r"\bG" r"O\b")),
]

#: References a stranger cannot follow. They fail everywhere but in
#: REFS_OK: the CHANGELOG keeps bare tracker ids (and the spec ids beside
#: them).
REFS: list[tuple[str, re.Pattern[str]]] = [
    ("a tracker id", re.compile(r"\b(?:%s)-\d+\b" % "|".join(
        ["S" "M", "EA" "RS", "PHO" "NE", "S" "K", "S" "T", "S" "V", "T" "M", "SE" "SH"]))),
    ("a spec requirement id", re.compile(r"\b[RQAD]\d{1,2}\b")),
]
REFS_OK = {"CHANGELOG.md"}


# --- the allowlist -----------------------------------------------------------

class Allow:
    """A product word that looks like a marker: (where, what, why)."""

    def __init__(self, where: str, what: str, why: str) -> None:
        self.where, self.what, self.why = where, re.compile(what), why

    def __repr__(self) -> str:
        return f"Allow({self.where!r}, {self.what.pattern!r})"


_AUTHOR = r'"name":\s*"' "Mi" r'ke Bai' r'ley"'
_LABEL = r"\[" "RUL" r"INGS\]"

#: Product words that look like a name, in every scope. Each hides only its
#: own words. One may have no use in the archive today (no install line
#: ships), so these are not held to the in-use rule: the product-line tests
#: prove each one instead.
PRODUCT_WORDS = [
    Allow("*", r"\bp" r"ip3? (?:install|uninstall|show|download|wheel)\b|-m p" r"ip\b",
          "the Python installer, a command"),
    Allow("*", "mi" r"ke@sessionkit\.ai", "the author's published address, package metadata"),
    Allow("*", r"\b(?:its|their) k" r"in\b", "plain English: a thing's relatives"),
]

#: Shipped files. `where` is a glob over the archive path.
DOC_ALLOW = [
    *PRODUCT_WORDS,
    Allow(".claude-plugin/plugin.json", _AUTHOR, "the plugin's author metadata"),
    Allow(".claude-plugin/marketplace.json", _AUTHOR, "the marketplace owner and author"),
    Allow("plugins/*/.claude-plugin/plugin.json", _AUTHOR, "a bundled plugin's author"),
    Allow("*.md", _LABEL, "a memory label's name, a product word"),
    Allow("AGENTS.md", r"what is " "RUL" "ED", "a principle for contributors, not a record"),
]

#: Shipped source (comments, docstrings, help and every printed line).
SOURCE_ALLOW = [
    *PRODUCT_WORDS,
    Allow("pyproject.toml", r'name = "' "Mi" r'ke Bai' r'ley"', "the package's author metadata"),
    Allow("src/*", _LABEL, "a memory label's name, a product word"),
    Allow("src/sessionmail/memory.py", r"IDEAS " "RUL" r"INGS LESSONS",
          "the default memory labels `memory init --help` prints, a product word"),
]

#: `--help` pages. `where` is a glob over the command path (`memory init`).
HELP_ALLOW = [
    *PRODUCT_WORDS,
    Allow("memory init", r"IDEAS " "RUL" r"INGS LESSONS", "a memory label's name, a product word"),
]


# --- what ships --------------------------------------------------------------

#: What the guard reads, by archive path. To widen it (tests are to follow),
#: add a suffix or a glob here; an exclusion beats both.
SCOPE_SUFFIXES = {".md", ".txt", ".html"}
SCOPE_GLOBS = [".claude-plugin/*.json", "plugins/*/.claude-plugin/plugin.json",
               "hooks/hooks.json"]
SCOPE_EXCLUDE = ["tests/fixtures/*"]


def in_scope(name: str) -> bool:
    """Is this archive path one the guard reads?"""
    if any(fnmatch.fnmatchcase(name, glob) for glob in SCOPE_EXCLUDE):
        return False
    if Path(name).suffix in SCOPE_SUFFIXES:
        return True
    return any(fnmatch.fnmatchcase(name, glob) for glob in SCOPE_GLOBS)


#: Shipped SOURCE, by archive path: what is not a doc under these globs. A
#: comment, a docstring, a `--help` line and a printed string are read by
#: users too, so they are held to the same markers, and to every tracker id
#: in its GENERIC form (any 2-5 capitals, a dash, a number), because a
#: fixed list of prefixes misses the ones nobody listed.
SOURCE_GLOBS = ["src/*", "scripts/*", "hooks/*", "Makefile", "pyproject.toml", ".github/*"]
SOURCE_REFS: list[tuple[str, re.Pattern[str]]] = [
    ("a tracker id", re.compile(r"\b(?!(?:%s)-)[A-Z]{2,5}-\d+\b" % "|".join(
        ["UTF", "SHA", "ISO", "GPL", "AGPL", "LGPL", "RFC", "CVE", "TLS", "MD", "PEP"]))),
    # The same ids lower-cased inside a box name (`<project>+<id>.<role>`).
    # `tm-123` is the generic example the address grammar is written with.
    ("a tracker id in a box name", re.compile(r"\b(?:%s|t" r"m(?!-123\b))-\d+\b" % "|".join(
        ["s" "m", "se" "sh", "ea" "rs", "pho" "ne", "s" "k", "s" "t", "s" "v", "v" "m",
         "c" "a", "in" "f", "gi" "t"]))),
    # How a decision was taken, and by whom: the team, a seat, a spoken order.
    ("a decision by voice", re.compile(r"\bby vo" r"ice\b")),
    ("the team that runs the project", re.compile(r"\bcr" r"ew\b", re.IGNORECASE)),
    ("a seat's session id", re.compile(r"\bseat [0-9a-f]{8}\b")),
    ("a decision record's id", re.compile(r"\brul" r"ing [0-9a-f]{6,}\b")),
    ("a decision's author", re.compile(r"\b[Rr]ul" r"ed (?:by|with)\b")),
    ("a voice date", re.compile(r"\bvo" r"ice,? \d{4}-\d{2}-\d{2}\b")),
    # A checkout on the maintainer's machine, and the team's own mail base.
    ("a private source path", re.compile(r"~/Co" r"de/")),
    ("an internal store address", re.compile(r"\bfail" r"mode\+")),
    # Whose words these were: a person by pronoun, with no one named; a
    # quoted first-person turn; a crew seat; the task a comment was written on.
    ("a person by pronoun", re.compile(r"\b(?:he|his|him|she|her|hers)\b", re.IGNORECASE)),
    ("a quoted first-person turn", re.compile(r"\*\"(?:I|we|let's)\b", re.IGNORECASE)),
    ("the team's seats", re.compile(r"\bfore" r"men\b")),
    ("the task a comment was written on", re.compile(r"\bthis ta" r"sk\b")),
]

#: A stamp a line break splits (`... carries, HH:MM` / `Day YYYY-MM-DD)`),
#: or a review or design item (`(design` / `<id>, <id>`):
#: the per-line scan sees neither half whole. Each line is read joined to
#: the next, with the next line's indent and comment lead folded into one
#: space; only a match that spans the break is reported, since the per-line
#: scan already reads the rest.
SPLIT_STAMPS: list[tuple[str, re.Pattern[str]]] = [
    ("a private timestamp", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})? (?:%s)\b" % _DAY)),
    ("a voice timestamp", re.compile(r"\bvo" r"ice,? (?:\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2})\b")),
    ("a decision by voice", re.compile(r"\bby vo" r"ice\b")),
    next(m for m in MARKERS if m[0] == "a review or design item"),
]
_LEAD = re.compile(r"^[ \t]*(?:#:?|//|\*|;+)?[ \t]*")


def split_stamps(texts: dict[str, str], skip=frozenset()) -> list[str]:
    """`where:line: label: text` for each stamp split across two lines."""
    hits = []
    for where, text in sorted(texts.items()):
        if where in skip:
            continue
        lines = text.splitlines()
        for n, (first, second) in enumerate(zip(lines, lines[1:]), 1):
            head = first.rstrip()
            joined = head + " " + _LEAD.sub("", second)
            hits += [f"{where}:{n}: {label}: {joined.strip()[:160]}"
                     for label, p in SPLIT_STAMPS for m in p.finditer(joined)
                     if m.start() < len(head) < m.end()]
    return hits


def in_source_scope(name: str) -> bool:
    """Is this archive path shipped source the guard reads?"""
    if in_scope(name) or any(fnmatch.fnmatchcase(name, glob) for glob in SCOPE_EXCLUDE):
        return False
    return any(fnmatch.fnmatchcase(name, glob) for glob in SOURCE_GLOBS)


#: Shipped TESTS, read for less. A test's data is what it is about, so its
#: timestamps, tracker ids, test ids and seat words stay. What may not ship
#: in a test is whose words or whose machine: a person (by name, in any
#: case, also as data: example people are alice and bob), a quoted turn,
#: an internal host (example hosts are alpha, beta and example.net), a
#: private address or path.
TEST_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    *[m for m in MARKERS if m[0] in {"a person", "an internal host", "an internal tailnet",
                                     "a voice timestamp", "a ruling time"}],
    *[m for m in SOURCE_REFS if m[0] in {"a decision by voice", "a voice date",
                                         "a quoted first-person turn", "a private source path",
                                         "an internal store address", "a decision's author"}],
    # Who decided, and when: a seat as the decider, a decision's own clock
    # or a seat's session id. A seat as a mail box is test data, and stays.
    ("a seat as the decider", re.compile(
        r"\b(?:%s)(?:'s)? (?:approved|agreed|decided|ruled|ruling|overrul\w*|(?:scope )?condition)\b"
        % "|".join(["su" "per", "fore" "man", "G" "C"]), re.IGNORECASE)),
    ("a decision's clock", re.compile(r"\b\d{1,2}:\d{2} rul" r"(?:ing|ed)\b")),
    ("a decision's session id", re.compile(r"\bcondition [0-9a-f]{8}\b", re.IGNORECASE)),
    # The owner's surname, kept beside an example first name (`<name>.<surname>`).
    ("a person's surname", re.compile(_ALONE % ("bai" "ley"), re.IGNORECASE)),
    # A named home; a one-letter placeholder (`/Users/u/`) names no one.
    ("a private home", re.compile(r"/Us" r"ers/(?!<)[a-z][a-z0-9._-]+")),
    ("a private address", re.compile(r"@fail" r"mode\.com\b")),
]

TEST_ALLOW = [
    *PRODUCT_WORDS,
    Allow("tests/test_shipped_docs_neutral.py", r"\"a decision by vo" r"ice\"",
          "the guard's own label for what it forbids"),
]


def in_test_scope(name: str) -> bool:
    """Is this archive path a shipped test (or its fixture) the guard reads?
    A test's own doc is the docs scan's."""
    return name.startswith("tests/") and not in_scope(name)


def _git(*args: str, root: Path = ROOT) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout


def _need_git() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout: there is no release archive to read")


def shipped_docs(root: Path, keep=in_scope) -> dict[str, str]:
    """{archive path: text} for every file of ROOT's release archive that
    `keep` takes (default: the in-scope docs). A file that is not UTF-8
    text is skipped when `keep` takes everything.

    `git stash create` writes a commit object for the working tree and
    touches no ref and no stash entry; on a clean tree it prints nothing, so
    this is `git archive HEAD` there.
    """
    rev = _git("stash", "create", root=root).decode().strip() or "HEAD"
    tar = tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", rev, root=root)))
    docs = {}
    for member in tar.getmembers():
        if member.isfile() and keep(member.name):
            raw = tar.extractfile(member).read()
            if keep is in_scope:
                docs[member.name] = raw.decode("utf-8")
                continue
            try:
                docs[member.name] = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
    return docs


@pytest.fixture(scope="module")
def archive() -> dict[str, str]:
    _need_git()
    docs = shipped_docs(ROOT)
    assert docs, "the archive holds no shipped docs: the scope is wrong"
    return docs


@pytest.fixture(scope="module")
def source() -> dict[str, str]:
    _need_git()
    texts = shipped_docs(ROOT, keep=in_source_scope)
    assert "src/sessionmail/pager.py" in texts, "the archive holds no source: the scope is wrong"
    return texts


@pytest.fixture(scope="module")
def shipped_tests() -> dict[str, str]:
    _need_git()
    texts = shipped_docs(ROOT, keep=in_test_scope)
    assert "tests/conftest.py" in texts, "the archive holds no tests: the scope is wrong"
    return texts


def _commands(group: click.Group, path: tuple[str, ...]):
    for name in group.list_commands(None):
        cmd = group.commands[name]
        yield (*path, name), cmd
        if isinstance(cmd, click.Group):
            yield from _commands(cmd, (*path, name))


@pytest.fixture(scope="module")
def help_pages() -> dict[str, str]:
    """{command path: rendered --help} for every command of the three CLIs."""
    pages = {}
    for prog, group in (("message", message.cli), ("pager", pager.cli),
                        ("memory", memory.cli)):
        pages[prog] = CliRunner().invoke(group, ["--help"], prog_name=prog).output
        for path, _cmd in _commands(group, ()):
            result = CliRunner().invoke(group, [*path, "--help"], prog_name=prog)
            assert result.exit_code == 0, (path, result.output)
            pages[" ".join((prog, *path))] = result.output
    return pages


# --- the scan ----------------------------------------------------------------

def scan(texts: dict[str, str], allow: list[Allow], refs_ok: set[str], refs=None,
         markers=None):
    """(hits, used): each hit is `where:line: label: text`; `used` holds the
    allowances that hid at least one marker. REFS (default: the docs' REFS)
    are the reference patterns checked outside REFS_OK; MARKERS (default:
    every marker) are checked everywhere."""
    refs = REFS if refs is None else refs
    markers = MARKERS if markers is None else markers
    hits, used = [], set()
    for where, text in sorted(texts.items()):
        mine = [a for a in allow if fnmatch.fnmatchcase(where, a.where)]
        for n, line in enumerate(text.splitlines(), 1):
            for a in mine:
                if any(p.search(m.group(0)) for m in a.what.finditer(line)
                       for _label, p in (*markers, *refs)):
                    used.add(id(a))
                line = a.what.sub("\0", line)
            found = [label for label, p in markers if p.search(line)]
            if where not in refs_ok:
                found += [label for label, p in refs if p.search(line)]
            hits += [f"{where}:{n}: {label}: {line.strip()[:160]}" for label in found]
    return hits, used


def _fail(kind: str, hits: list[str]) -> str:
    return f"{len(hits)} provenance line(s) in {kind}:\n" + "\n".join(hits)


# --- the tests ---------------------------------------------------------------

def test_shipped_docs_carry_no_provenance(archive):
    hits, _used = scan(archive, DOC_ALLOW, REFS_OK)
    assert not hits, _fail("the shipped docs", hits)


def test_shipped_source_carries_no_provenance(source):
    hits, _used = scan(source, SOURCE_ALLOW, set(), SOURCE_REFS)
    assert not hits, _fail("the shipped source", hits)


def test_shipped_text_carries_no_split_stamp(archive, source):
    """The docs and the source, a stamp the line break hides."""
    hits = split_stamps({**archive, **source})
    assert not hits, _fail("the shipped text, across a line break", hits)


@pytest.mark.parametrize("text", [
    f"    every wake carries, {CLOCK}\n    {DAY} {DATE}), the host switch",
    "# is escaped, never live (o03, vo" f"ice\n#   {DATE} {CLOCK}).",
    f"#: found {CLOCK}\n#: {DAY} {DATE}: cat showed v172",
    "    re-read when a file CHANGES (des" "ign\n    A" "6, N" "5: re-read",
    "(the owner's decisions, by\n * vo" "ice, on a thread)",
])
def test_a_split_stamp_is_caught(text):
    assert split_stamps({"src/sessionmail/x.py": text}), text


@pytest.mark.parametrize("text", [
    f"an ISO time (`{DATE} {CLOCK}`), local\nwhen it names no zone",
    "a voice mail, heard in voice\nmode, on Wed",
    "(D" "9;\n    spec \"A box argument\")", "by design\n    the box is read",
    f"the clock reads {CLOCK}\n\n{DAY} is the next line's word",
    "per vo" f"ice {CLOCK} {DAY} on one line",
])
def test_a_line_pair_with_no_split_stamp_passes(text):
    """A stamp whole on one line is the per-line scan's, not this one's."""
    assert not split_stamps({"src/sessionmail/x.py": text}), text


def test_help_pages_carry_no_provenance(help_pages):
    hits, _used = scan(help_pages, HELP_ALLOW, set())
    assert not hits, _fail("the --help pages", hits)


#: `vmailbox-gen --help` prints the script's own header comment, so it is
#: held to every marker the source is: people, hosts, clocks and ids.
VMAILBOX_GEN = ROOT / "src/sessionmail/postfix/vmailbox-gen"


def test_vmailbox_gen_help_names_no_one(tmp_path):
    """`--help` exits before the script reads a map, a root or postconf: run
    it with a scratch HOME and nothing else in the environment."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash to print vmailbox-gen's help")
    done = subprocess.run([bash, str(VMAILBOX_GEN), "--help"], capture_output=True, text=True,
                          env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, timeout=60)
    assert done.returncode == 0, done.stderr
    assert len(done.stdout) > 5000, "the help printed almost nothing: the check would prove nothing"
    hits, _used = scan({"vmailbox-gen --help": done.stdout}, PRODUCT_WORDS, set(), SOURCE_REFS)
    assert not hits, _fail("vmailbox-gen --help", hits)


#: A conflict's own lines, left by a hand-resolved merge: ours, base,
#: theirs and the divider. Built from counts, so this file holds none.
CONFLICT_LINE = re.compile(r"^(?:<{7} |\|{7} |>{7} |={7}$)", re.M)


def conflict_lines(texts: dict[str, str]) -> list[str]:
    return [f"{where}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}"
            for where, text in sorted(texts.items()) for m in CONFLICT_LINE.finditer(text)]


def test_no_shipped_file_carries_a_merge_conflict_line():
    """Every text file the archive ships, not only the docs."""
    _need_git()
    texts = shipped_docs(ROOT, keep=lambda name: True)
    assert "src/sessionmail/message.py" in texts and "CHANGELOG.md" in texts
    hits = conflict_lines(texts)
    assert not hits, f"{len(hits)} merge conflict line(s) ship:\n" + "\n".join(hits)


@pytest.mark.parametrize("line", ["<" * 7 + " HEAD", "|" * 7 + " 1c5f5b0", "=" * 7,
                                  ">" * 7 + " theirs"])
def test_a_conflict_line_is_caught(line):
    assert conflict_lines({"CHANGELOG.md": f"- an entry\n{line}\n- the next\n"})


@pytest.mark.parametrize("line", ["=" * 7 + " a heading rule with words", "=" * 8,
                                  "<" * 8 + " eight", "a line with " + "|" * 7 + " inside",
                                  " " + "=" * 7])
def test_a_line_like_a_conflict_line_passes(line):
    assert not conflict_lines({"CHANGELOG.md": f"{line}\n"})


def test_internal_files_are_not_in_the_archive(archive):
    """The wholly internal docs are export-ignored, and the scope still
    reaches every kind of file it names."""
    names = set(archive)
    assert not any(n.startswith("docs/architecture/") for n in names), sorted(names)
    assert ".gitready" not in names
    for want in ("README.md", "CHANGELOG.md", "AGENTS.md", ".claude-plugin/plugin.json",
                 ".claude-plugin/marketplace.json", "hooks/hooks.json",
                 "plugins/pager-monitor/.claude-plugin/plugin.json"):
        assert want in names, want
    assert not any(n.startswith("tests/fixtures/") for n in names)


def test_internal_files_are_export_ignored():
    """`git check-attr` agrees with the archive, per file (the `**` line)."""
    _need_git()
    for name in (".gitready", "docs/architecture/pager.html",
                 "docs/architecture/task-flow-by-mail.html"):
        out = _git("check-attr", "export-ignore", "--", name).decode().strip()
        assert out == f"{name}: export-ignore: set", out


def test_every_allowance_is_used(archive, help_pages):
    """A stale allowance would hide a future leak: each one must hide a
    marker somewhere today."""
    _hits, used = scan(archive, DOC_ALLOW, REFS_OK)
    stale = [a for a in DOC_ALLOW
             if id(a) not in used and a not in PRODUCT_WORDS]
    _hits, used = scan(help_pages, HELP_ALLOW, set())
    stale += [a for a in HELP_ALLOW if id(a) not in used and a not in PRODUCT_WORDS]
    assert not stale, f"allowances that hide nothing: {stale}"


def test_every_source_allowance_is_used(source):
    """The same rule for the source allowances."""
    _hits, used = scan(source, SOURCE_ALLOW, set(), SOURCE_REFS)
    stale = [a for a in SOURCE_ALLOW
             if id(a) not in used and a not in PRODUCT_WORDS]
    assert not stale, f"source allowances that hide nothing: {stale}"


def _scan_tests(texts: dict[str, str]):
    return scan(texts, TEST_ALLOW, set(), refs=[], markers=TEST_MARKERS)


def test_shipped_tests_name_no_one(shipped_tests):
    """The tests and their fixtures: no person, quoted turn, internal host,
    private address or path. Their timestamps and ids are data, and stay."""
    hits, _used = _scan_tests(shipped_tests)
    assert not hits, _fail("the shipped tests", hits)


def test_every_test_allowance_is_used(shipped_tests):
    _hits, used = _scan_tests(shipped_tests)
    stale = [a for a in TEST_ALLOW
             if id(a) not in used and a not in PRODUCT_WORDS]
    assert not stale, f"test allowances that hide nothing: {stale}"


#: A crew name lower-cased, as data or inside a name, in each scope.
_LOWER_NAMES = ["seats/p" "ip", "a `To: co" "ra` message", "CO" "RA_SID = 'x'",
                "def test_mi" "ke_pages():", "From: K" "in <k" "in@host>", "the doc" "tor's case",
                "`p" "ip`'s shape again", "subscribe(home, 'seats/p" "ip', 'x')"]


@pytest.mark.parametrize("line", [
    *_LOWER_NAMES,
    "x@m" "5.session-mail", "HOST = 'm" "s2'", "host-a.spar" "row-hydra.ts.net",
    "(alice, vo" f"ice {CLOCK})", "instruction (*\"w" "e don't want that\"*)",
    "    cwd = '/Us" "ers/co" "ra/x'", "`~/Co" "de/acme/widget`", "to: someone@fail" "mode.com",
    "signed by session-birth@fail" "mode.com", "a shared base (`fail" "mode+gc`)", "Rul" "ed by vo" f"ice, {DATE}",
    "Su" "per condi" "tion 3987c186: 0 is invalid", f"(the {CLOCK} rul" "ing: unchanged)",
    "the S" "M-714 su" "per approved it", "the fore" "man's scope condition",
    "plus, rul" "ed by the S" "M-755 fore" "man, a list", "# G" f"C overrule {CLOCK}: no suffix", "(--cc, judy.bai" "ley)",
])
def test_a_test_marker_line_is_caught(line):
    hits, _used = _scan_tests({"tests/test_x.py": line})
    assert hits, line


@pytest.mark.parametrize("line", [
    f"Date: {RFC_DATE}", "subject = 'SM" "-538 the build'",
    f"a stamp {CLOCK} {DAY} {DATE}", "alice@alpha.example.net", "HOST = 'beta'",
    "p" "ip install sessionmail", "python -m p" "ip show sessionmail",
    "md5, m50, ms3 and skin of the kind",
    "`<project>+" "su" "per`, a seat", "the fore" "man's box", "the doctrine", "pipeline",
    "mi" "ke@sessionkit.ai", "the boot shelf and its k" "in", "/Us" "ers/u/.local/bin/claude",
    "the su" "per's copy is the project box's", f"a verdict at {CLOCK}, before the marks",
    "the scope condition of design section 3",
])
def test_a_test_data_line_passes(line):
    hits, _used = _scan_tests({"tests/test_x.py": line})
    assert not hits, hits


@pytest.mark.parametrize("where", ["tests/test_sig_v2.py", "tests/test_sender_v2.py"])
@pytest.mark.parametrize("line", ["# per Mi" "ke, the fix", "seat = 'p" "ip'", "x@m" "5.session-mail",
                                  "/Us" "ers/co" "ra/x"])
def test_a_vector_pinning_file_still_catches_a_new_name(where, line):
    """The two files that pin the signed vectors are read like any other test:
    the vectors carry neutral names and hosts, so nothing in them is hidden."""
    hits, _used = _scan_tests({where: line})
    assert hits, (where, line)


@pytest.mark.parametrize("line", _LOWER_NAMES)
def test_a_lower_case_name_is_caught_in_the_docs_and_the_source(line):
    hits, _used = scan({"docs/x.md": line}, DOC_ALLOW, REFS_OK)
    assert hits, line
    hits, _used = scan({"src/sessionmail/x.py": line}, SOURCE_ALLOW, set(), SOURCE_REFS)
    assert hits, line


def test_a_planted_test_line_is_caught_through_the_archive(tmp_path):
    """The tests half, end to end: a lower-case crew name and a host planted
    in a test and a fixture of a real repo are read and caught, a signed
    vector's directory included: the vectors carry neutral names and hosts,
    so nothing there is hidden."""
    _need_git()
    planted = "seat = 'p" "ip'  # on m" "5\n"
    repo = tmp_path / "repo"
    rels = ("tests/test_x.py", "tests/fixtures/x.eml", "tests/mhs/x.sh",
            "tests/fixtures/sig_v2/x.eml")
    for rel in rels:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(planted)
    shutil.copy(ROOT / ".gitattributes", repo / ".gitattributes")
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "plant"]):
        subprocess.run(["git", "-C", str(repo), *cfg, *args], check=True, capture_output=True)

    texts = shipped_docs(repo, keep=in_test_scope)
    assert set(texts) == set(rels), sorted(texts)
    hits, _used = _scan_tests(texts)
    caught = {hit.split(":")[0] for hit in hits}
    assert caught == set(rels), hits
    labels = {hit.split(": ")[1] for hit in hits}
    assert labels == {"a person", "an internal host"}, hits


@pytest.mark.parametrize("line", [
    "# per alice, vo" f"ice {CLOCK} {DAY}",
    "#: the fix (S" "M-123 do" "-004)",
    'click.echo("refused (V' 'M-1802)")',
    "# measured on " "m" "s2", "# the " "su" "per's call",
    "    (design " "D" "9, review " "N" "1)",
    "    e.g. `sessionmail+s" "m-374.foreman`", "`Foreman <foreman.c" "a-104@host>`",
    "`session+se" "sh-572.foreman`", "`voicemode+v" "m-2266`", "box t" "m-5442.worker",
    "(the owner's decisions, by vo" "ice, on a thread)", "bodies are markdown (cr" "ew rule)",
    "# Decided by a seat (seat fac" "93f14)", "    the other's rul" "ing 3987c186), or",
    "# Rul" "ed with the other lane by mail", "Rul" "ed by the owner",
    "    fault (CR" "EW RECOMMENDATION, D" "4 step 5)", "(o03, vo" f"ice {DATE})",
    "    `~/Co" "de/acme/widget` @ c0f3cf8", "a shared base (`fail" "mode+gc`)",
    "    fallback h" "e allowed, behind the watch", "  - SETTLE (h" "is batch window)",
    "instruction (*\"w" "e don't want to suggest things\"*)", "(*\"I" " think so\"*)",
    "fooled two successive fore" "men's boot readouts", "unchanged by this ta" "sk, still",
])
def test_a_source_marker_line_is_caught(line):
    hits, _used = scan({"src/sessionmail/x.py": line}, SOURCE_ALLOW, set(), SOURCE_REFS)
    assert hits, line


@pytest.mark.parametrize("line", [
    "# D" "9: the box argument's release of grace",
    "# UTF" "-8 and SHA" "-256 and RFC" "-5322",
    "p" "ip install sessionmail", "# a measurement is not a ruling",
    "#   tm-123.worker.<sid>  -> <sid>", "a voice mail, heard in voice mode",
    "the ruled ten", "screwdriver and crewcut", "a seat of its own", "utf-8 and iso-8859-1",
    "signed under session-birth@fail" "mode.com", "a shared base (`example+ops`)",
    "~/.mail/agents/STORE", "a voice turn, 2 words",
    "the heap and the shell", "a hero, other, there", "`foreman.web-12`, a box",
    "*\"never read from an environment variable\"*", "a task's own rows", "his" "tory",
])
def test_a_source_product_line_passes(line):
    """A bare spec id is a pointer into the specs, not a tracker id; the
    source scan does not read it (the docs scan does)."""
    hits, _used = scan({"src/sessionmail/x.py": line}, SOURCE_ALLOW, set(), SOURCE_REFS)
    assert not hits, hits


@pytest.mark.parametrize("line", [
    "Rul" f"ed by alice at {CLOCK} {DAY} {DATE}.",
    "the median of 50 on " "m" "s2",
    "@m" "5.session-mail",
    "(SM" "-792)",
    "RUL" "INGS 840",
    "vo" f"ice {CLOCK}",
    "the " "Doc" "tor's review", "Ta" "rs's finding", "TA" "RS said",
    "host-a.spar" "row-hydra.ts.net",
    "the do" "-008 review", "verify" "-001", "regression-check" "-001",
    "review " "N1", "slice " "2", "design " "Q" "4", "RUL" "INGS 4600", "deploy " "G" "O",
    "(Q" "11; A" "37)", "(R" "01-R" "07)",
    "`carry! from:$" "mi" "ke`", "a `To: " "co" "ra` message",
    "a " "su" "per's subject once ran it", "the " "fore" "man followed the remedy",
    "goes on `(kai" "zen 55, own 40, +2 more 46)`", "via the " "Kai" "zen box",
    "forms (review " f"{DATE}, H2).",
    "from `EMAIL` (rul" f"ed {CLOCK_S}; the fallback)", "declared (Rul" f"ed {CLOCK}).",
])
def test_a_marker_line_is_caught(line):
    hits, _used = scan({"docs/x.md": line}, DOC_ALLOW, REFS_OK)
    assert hits, line


@pytest.mark.parametrize("line", [
    "p" "ip install sessionmail",
    "a measurement is not a ruling",
    "voice mode and a seat",
    "the [" "RUL" "INGS] label",
    "md5 and m50 and ms3",
    "a host with no tailnet sets MAGICDNS_SUFFIX, e.g. example.ts.net",
    "do it, a review of the design, Go on",
    "a slice of 200 lines, review the design, R2D2",
    "a proposed change is not ruled until it is ratified", "ruled 3 ways",
    '"email": "' "mi" 'ke@sessionkit.ai"', "`<project>+" "su" "per`, a superset",
])
def test_a_product_line_passes(line):
    hits, _used = scan({"docs/x.md": line}, DOC_ALLOW, REFS_OK)
    assert not hits, hits


#: Commit shas stay in the docs: no marker may read one as provenance. The
#: all-digit ones are the edge (a review item is "review" and a number).
_SHAS = ["d5984a7", "7e6682fee24af65cf36a73c5d77afe560526f50c", "1234567", "68661090",
         "4b2e63f..9d63b62"]


@pytest.mark.parametrize("where", ["CHANGELOG.md", "docs/x.md"])
@pytest.mark.parametrize("line", [
    "fixed in {sha}.", "({sha})", "commit `{sha}`", "the review {sha} fixed it",
    "re-review at {sha}", "slice {sha}", "design {sha}",
])
@pytest.mark.parametrize("sha", _SHAS)
def test_a_commit_sha_is_never_a_marker(where, line, sha):
    hits, _used = scan({where: line.format(sha=sha)}, DOC_ALLOW, REFS_OK)
    assert not hits, hits


def test_the_changelog_keeps_bare_refs():
    hits, _used = scan({"CHANGELOG.md": "(SM" "-792)"}, DOC_ALLOW, REFS_OK)
    assert not hits
    hits, _used = scan({"CHANGELOG.md": "(SM" "-792; " "Mi" "ke)"}, DOC_ALLOW, REFS_OK)
    assert hits


def test_a_planted_marker_is_caught_through_the_archive(tmp_path):
    """The whole path, end to end: a doc planted in a real repo, archived
    with this repo's export rules, is read and caught; what the rules or
    the scope leave out is not read at all."""
    _need_git()
    planted = ("the fix, per " "Mi" "ke, measured on " "m" "s2\n"
               "heard by vo" f"ice {CLOCK} {DAY}\n")
    repo = tmp_path / "repo"
    for rel in ("docs/planted.md", "tests/fixtures/x.md", ".gitready",
                "docs/architecture/x.html", "src/x.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(planted)
    shutil.copy(ROOT / ".gitattributes", repo / ".gitattributes")
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "plant"]):
        subprocess.run(["git", "-C", str(repo), *cfg, *args], check=True, capture_output=True)

    docs = shipped_docs(repo)
    assert set(docs) == {"docs/planted.md"}, sorted(docs)
    hits, _used = scan(docs, DOC_ALLOW, REFS_OK)
    labels = {hit.split(": ")[1] for hit in hits}
    assert labels == {"a person", "a voice timestamp", "a private timestamp",
                      "an internal host"}, hits


def test_a_planted_source_line_is_caught_through_the_archive(tmp_path):
    """The source half, end to end: a line planted in each source scope of
    a real repo, archived with this repo's export rules, is read and caught;
    a doc is left to the docs scan."""
    _need_git()
    planted = ("# per " "Mi" "ke, measured on " "m" "s2 (V" "M-1802)\n"
               "# heard, vo" f"ice {CLOCK} {DAY}\n")
    repo = tmp_path / "repo"
    for rel in ("src/x.py", "scripts/x", "hooks/x", "Makefile", "pyproject.toml",
                ".github/CODEOWNERS", "src/x.md", "tests/x.py",
                "src/sessionmail/postfix/vmailbox-gen"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(planted)
    shutil.copy(ROOT / ".gitattributes", repo / ".gitattributes")
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "plant"]):
        subprocess.run(["git", "-C", str(repo), *cfg, *args], check=True, capture_output=True)

    texts = shipped_docs(repo, keep=in_source_scope)
    want = {"src/x.py", "scripts/x", "hooks/x", "Makefile", "pyproject.toml",
            ".github/CODEOWNERS", "src/sessionmail/postfix/vmailbox-gen"}
    assert set(texts) == want, sorted(texts)
    hits, _used = scan(texts, SOURCE_ALLOW, set(), SOURCE_REFS)
    caught = {hit.split(":")[0] for hit in hits}
    assert caught == set(texts), hits
    labels = {hit.split(": ")[1] for hit in hits}
    assert labels == {"a person", "a voice timestamp", "a private timestamp",
                      "an internal host", "a tracker id"}, hits


def test_this_file_never_matches_itself():
    """The guard will widen to tests/: it must not then hit its own source."""
    hits, _used = scan({"tests/x.md": Path(__file__).read_text()}, [], set())
    assert not hits, hits
