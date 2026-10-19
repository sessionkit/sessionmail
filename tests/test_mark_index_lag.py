"""SM-714: `message mark` marks a mail `list` just showed, while the index
has not caught up with delivery.

`list` and `read` find a message by walking the maildir; `mark` turns an
index into a Message-ID the same way, then asks notmuch for `id:<it>`. A
live indexer is a loop (`notmuch new`, then sleep), so every delivery has a
window between landing in `new/` and being indexed. Until fix-001, host mode
said "no such message" in that window (repro-001; rca-001 verdict a).

Since fix-001, when the index answers empty, mark looks on disk in the
seat's OWN two boxes - its session box and its base box - and nowhere else
(SM-731 design section 3's store-mode scope, now in host mode too). A
listed mail in any OTHER box the index has not reached is still "no such
message": the honest answer there.

These tests use the REAL notmuch on a database under tmp_path: the race
needs an index that genuinely lags, which a fake engine cannot show. The
conftest's sandbox already moved HOME and the mail root; the host's notmuch
profile is a tmp config here, so no host index is ever opened.

The REQUIREMENT (card SM-714): "an index `list` prints, and a bare
Message-ID, both mark the message whenever the bracketed id would; when mark
does refuse, its error names what it actually looked up and where".
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _index, _notmuch
from sessionmail.message import cli
from test_message_list import deliver

NOTMUCH = shutil.which("notmuch")
pytestmark = pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")

SESSION = "5e55a714-0000-4000-8000-000000000714"
OLD = "old@example.invalid"
FRESH = "fresh@example.invalid"
EX_OK, EX_REFUSED = 0, 2


@pytest.fixture
def lagging(tmp_path, monkeypatch):
    """A session box under the sandboxed mail root, a REAL notmuch database
    over that root holding OLD (indexed), and FRESH delivered after the last
    `notmuch new`: in `list`, not yet in the index. Host mode."""
    root = Path(os.environ["SESSION_MAIL_ROOT"])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config = _index.profile_config()                     # host mode's profile
    config.parent.mkdir(parents=True)
    config.write_text(f"[database]\npath={root}\n[new]\ntags=unread;\n"
                      "[maildir]\nsynchronize_flags=false\n")
    monkeypatch.setenv(_notmuch.BINARY_VAR, NOTMUCH)
    monkeypatch.delenv(_index.VAR, raising=False)        # host mode, the default
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    _index.clear_cache()

    def index() -> None:
        subprocess.run([NOTMUCH, f"--config={config}", "new", "--quiet", "--no-hooks"],
                       check=True, capture_output=True, timeout=60)

    deliver(root, SESSION, "1923171201", mid=OLD, subject="old, indexed")
    index()
    deliver(root, SESSION, "1923171202", mid=FRESH, subject="fresh, not indexed")

    class Fixture:
        pass

    def count(mid: str) -> str:
        """notmuch's own answer for MID, from the tmp config, named with
        `--config` and never inherited: it cannot open a host index."""
        done = subprocess.run([NOTMUCH, f"--config={config}", "count", f"id:{mid}"],
                              capture_output=True, text=True, timeout=30, check=True)
        return done.stdout.strip()

    fx = Fixture()
    fx.root, fx.index, fx.count = root, index, count
    fx.marks = (Path(os.environ["HOME"]) / ".session" / "sessions" / SESSION
                / "mail" / "marks")
    yield fx
    _index.clear_cache()


def message(*argv: str):
    return CliRunner().invoke(cli, list(argv), prog_name="message")


def test_the_window_exists_list_shows_it_and_the_index_does_not(lagging):
    """The precondition, measured, so the marks below cannot pass by luck."""
    shown = message("list", SESSION)
    assert shown.exit_code == EX_OK, shown.output
    assert "fresh, not indexed" in shown.output and "old, indexed" in shown.output
    assert (lagging.count(OLD), lagging.count(FRESH)) == ("1", "0")


def test_an_indexed_mail_marks_by_index(lagging):
    """Control: index 1 is OLD, which the index holds."""
    r = message("mark", "1", "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{OLD} done" in lagging.marks.read_text()


def test_read_by_index_finds_the_unindexed_mail(lagging):
    """Contrast: `read` walks the maildir and records its own `read` mark, so
    it works where `mark` refuses (card log 2030-10-17, #66a: "worked after a
    `message read`")."""
    r = message("read", "2")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"Message-ID: <{FRESH}>" in r.output
    assert f"{FRESH} read" in lagging.marks.read_text()


FORMS = {
    "index": ("2",),
    "box-named-index": (SESSION, "2"),
    "bare-id": (FRESH,),
    "bracketed-id": (f"<{FRESH}>",),
}


@pytest.mark.parametrize("form", FORMS.values(), ids=FORMS.keys())
def test_a_listed_mail_the_index_has_not_reached_is_marked(lagging, form):
    """The requirement, in every form: the index has not reached FRESH, and
    each form marks it from the session box on disk."""
    assert lagging.count(FRESH) == "0"
    r = message("mark", *form, "done")
    assert r.exit_code == EX_OK, r.stderr
    assert f"{FRESH} done" in lagging.marks.read_text()
    assert "no such message" not in r.stderr


@pytest.mark.parametrize("form", FORMS.values(), ids=FORMS.keys())
def test_every_form_marks_before_and_after_the_index_catches_up(lagging, form):
    """Before fix-001 the first half said "no such message" (bare and
    bracketed are the same query after the strip). Now both halves mark, and
    the row is the same row either side of `notmuch new`."""
    r = message("mark", *form, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    before = lagging.marks.read_text().splitlines()
    assert len(before) == 1 and f"{FRESH} done" in before[0]

    lagging.index()
    assert lagging.count(FRESH) == "1"
    r = message("mark", *form, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    after = lagging.marks.read_text().splitlines()
    assert len(after) == 2
    assert after[0].split()[1:] == after[1].split()[1:], "same row shape, stamp aside"


def test_the_fallback_only_reads(lagging):
    """Condition 3: the file is found, never touched. No rename,
    no flag, no notmuch write: the mail stays where it was delivered, and the
    index still does not hold it."""
    box = lagging.root / SESSION
    before = sorted(p.relative_to(box) for p in box.rglob("*") if p.is_file())
    stats = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in box.rglob("*") if p.is_file()}
    r = message("mark", FRESH, "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    after = sorted(p.relative_to(box) for p in box.rglob("*") if p.is_file())
    assert after == before
    assert {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in box.rglob("*")
            if p.is_file()} == stats
    assert lagging.count(FRESH) == "0", "mark wrote nothing to the index"


OTHER = "gamma"
THEIRS = "theirs@example.invalid"


@pytest.fixture
def foreign(lagging):
    """A mail in ANOTHER seat's box, delivered after the last `notmuch new`:
    `list gamma` shows it, the index does not hold it."""
    deliver(lagging.root, OTHER, "1923171203", mid=THEIRS, subject="theirs, not indexed")
    assert lagging.count(THEIRS) == "0"
    return lagging


@pytest.mark.parametrize("form", [(OTHER, "1"), (THEIRS,), (f"<{THEIRS}>",)],
                         ids=["box-named-index", "bare-id", "bracketed-id"])
def test_a_listed_mail_in_another_box_is_still_no_such_message(foreign, form):
    """Conditions 1 and 2: the disk fallback has the scope of the
    seat's own two boxes. A mail in another seat's box that the index has
    not reached gets "no such message", and the refusal names where it
    looked; an index resolved there carries no file."""
    shown = message("list", OTHER)
    assert "theirs, not indexed" in shown.output
    r = message("mark", *form, "done")
    assert r.exit_code == EX_REFUSED
    assert f"no such message: {THEIRS}" in r.stderr
    assert "not in the index (notmuch)" in r.stderr
    assert f"not in this seat's own boxes ({SESSION}" in r.stderr
    if form[0] == OTHER:
        assert f"listed in {OTHER}, which is not this seat's own box" in r.stderr
    assert not foreign.marks.exists() or THEIRS not in foreign.marks.read_text()


def test_another_box_marks_once_the_index_holds_it(foreign):
    """Control for the scope pin: the index is still the way to mark mail
    outside the own boxes, so it marks the moment `notmuch new` has run."""
    foreign.index()
    r = message("mark", OTHER, "1", "done")
    assert r.exit_code == EX_OK, r.output + r.stderr
    assert f"{THEIRS} done" in foreign.marks.read_text()
