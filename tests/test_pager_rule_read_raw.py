"""The verbs read a rules file as the LOADER does, byte for byte: SM-474 do-016 (R1).

From the do-009 review (reviews/2030-10-03T0550-do-009/code-testing.md, R1, and
the do-008 review's N1): `_rule_read` used `Path.read_text`, whose universal
newlines turn a lone CR into a line break and fold CRLF to LF. The loader
(`rules._read`, `_lines`) decodes the bytes and splits ONLY on LF. So for a
file holding a lone CR the verb saw a different text from the loader:

    locked file bytes: b"wake! from:eve@*\\rfocus sleep\\n"   (ivan)
    loader:            focus None   (one malformed line; the fleet is awake)
    pager focus sleep --fleet -> "the whole fleet is already sleep"

and any rewrite turned every CR into LF, which could change how the loader
reads the OTHER lines; CRLF files were folded to LF (N1). The fix decodes the
raw bytes in `_rule_read`, which both `pager rule` and `pager focus` share,
and the write puts the same bytes back.

Hermetic: the rig of `test_pager_listen_judges` (temp roots, no live listener,
no real MTA), and `confirm_focus` replaced by a recorder.

Written to fail first against `60e895d`.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from click.testing import CliRunner

from sessionmail import _seat, pager, rules
from sessionmail.pager import cli
from test_pager_listen_judges import SESSION, rig  # noqa: F401  (the rig is a fixture)

EX_OK = 0


def pager_cmd(*argv: str):
    return CliRunner().invoke(cli, list(argv), prog_name="pager")


def as_seat(monkeypatch, address: str):
    monkeypatch.setenv("TM_AGENT_ADDRESS", address)
    return _seat.resolve()


def now() -> datetime:
    return datetime.now().astimezone()


def locked_with(data: bytes):
    path = rules.locked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def sent(monkeypatch):
    calls: list = []
    monkeypatch.setattr(pager, "confirm_focus",
                        lambda *a, **k: (calls.append((a, k)), "<focus@test.invalid>")[1])
    return calls


#: Files whose text the loader and a universal-newlines reader read differently.
RAW = [
    pytest.param(b"wake! from:eve@*\rfocus sleep\n", id="lone-CR"),
    pytest.param(b"wake! from:eve@*\r\nfocus sleep\r\n", id="CRLF"),
    pytest.param(b"ride to:x@*\rride to:y@*\r\nwake from:z@*\n", id="mixed"),
    pytest.param(b"\xef\xbb\xbfride to:x@*\r\n", id="BOM-CRLF"),
]


@pytest.mark.parametrize("data", RAW)
def test_rule_read_is_the_raw_bytes_decoded_with_no_newline_translation(rig, data):
    """The unit: what the verbs read is `data.decode()`, not its universal-
    newlines reading, and an undecodable byte survives to be written back."""
    path = locked_with(data)
    assert pager._rule_read(path) == data.decode("utf-8")
    bad = locked_with(b"ride to:x@*\r\xff\xfe\n")
    text = pager._rule_read(bad)
    assert "\r" in text and text.encode("utf-8", "surrogateescape") == b"ride to:x@*\r\xff\xfe\n"


def test_rule_write_puts_the_same_bytes_back(rig, tmp_path):
    path = tmp_path / "rules.out"
    data = b"ride to:x@*\r\nride to:y@*\rwake z\n\xff\n"
    pager._rule_write(path, data.decode("utf-8", "surrogateescape"))
    assert path.read_bytes() == data


# ── the measured R1: a lone CR, `focus` ────────────────────────────────────

def test_focus_sleep_fleet_is_not_already_so_when_the_loader_reads_the_fleet_awake(
        rig, sent, monkeypatch):
    """The measured case, verbatim. The loader reads ONE malformed line, so
    the fleet is awake; the verb must say it changed, not `already sleep`."""
    as_seat(monkeypatch, "ivan")
    locked_with(b"wake! from:eve@*\rfocus sleep\n")
    assert rules.load_layers(None, SESSION).focus(now()) is None, "the loader: awake"
    result = pager_cmd("focus", "sleep", "--fleet")
    assert result.exit_code == EX_OK, result.stderr
    assert "already" not in result.stdout, result.stdout
    mode = rules.load_layers(None, SESSION).focus(now())
    assert mode is not None and mode.mode == "sleep", \
        f"the verb said it changed; the loader reads {mode}"
    assert len(sent) == 1, "a real change mails the confirmation"


def test_focus_off_fleet_agrees_with_the_loader_on_a_lone_cr_file(rig, sent, monkeypatch):
    """The other direction: a `focus sleep` line the loader DOES read, on a
    file whose other lines carry lone CRs. `off` must end it, and leave every
    other byte as it was."""
    as_seat(monkeypatch, "ivan")
    path = locked_with(b"focus sleep\nwake! from:eve@*\rwake! from:ann@*\n")
    assert rules.load_layers(None, SESSION).focus(now()) is not None
    result = pager_cmd("focus", "off", "--fleet")
    assert result.exit_code == EX_OK, result.stderr
    assert rules.load_layers(None, SESSION).focus(now()) is None
    assert path.read_bytes() == b"wake! from:eve@*\rwake! from:ann@*\n", \
        "the focus line goes; the lone CR stays a CR"


@pytest.mark.parametrize("data, want", [
    (b"focus sleep\r\nwake! from:eve@*\r\n", b"wake! from:eve@*\r\n"),
    (b"wake! from:eve@*\r\nfocus sleep\r\nride to:x@*\r\n", b"wake! from:eve@*\r\nride to:x@*\r\n"),
], ids=["focus-first", "focus-middle"])
def test_focus_off_fleet_keeps_the_crlf_of_every_other_line(rig, sent, monkeypatch, data, want):
    """N1, for the focus verb: the CRLF fold is gone."""
    as_seat(monkeypatch, "ivan")
    path = locked_with(data)
    assert pager_cmd("focus", "off", "--fleet").exit_code == EX_OK
    assert path.read_bytes() == want
    assert b"\r\n" in path.read_bytes() and b"focus" not in path.read_bytes()
    assert rules.load_layers(None, SESSION).focus(now()) is None


# ── N1: `pager rule add|remove|list` keep and read the same bytes ──────────

def test_rule_add_to_a_crlf_file_leaves_the_existing_lines_as_they_were(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    path = locked_with(b"wake! from:eve@*\r\nride to:x@*\r\n")
    result = pager_cmd("rule", "add", "--locked", "ride", "from:*@delta.chat")
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)
    assert path.read_bytes() == b"wake! from:eve@*\r\nride to:x@*\r\nride from:*@delta.chat\n"
    assert not rules.load_layers(None, SESSION).errors


def test_rule_remove_from_a_crlf_file_leaves_the_other_lines_as_they_were(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    path = locked_with(b"wake! from:eve@*\r\nride to:x@*\r\nwake from:z@*\r\n")
    result = pager_cmd("rule", "remove", "--locked", "ride", "to:x@*")
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)
    assert path.read_bytes() == b"wake! from:eve@*\r\nwake from:z@*\r\n"


def test_rule_add_after_a_file_ending_in_a_lone_cr_starts_a_new_line(rig, monkeypatch):
    """A file that ends `\\r` has not ended its line for the loader: the new
    rule must not be glued onto it."""
    as_seat(monkeypatch, "ivan")
    path = locked_with(b"ride to:x@*\r")
    assert pager_cmd("rule", "add", "--locked", "ride", "to:y@*").exit_code == EX_OK
    assert path.read_bytes() == b"ride to:x@*\r\nride to:y@*\n"


def test_rule_list_numbers_lines_as_the_loader_does(rig, monkeypatch):
    """A lone CR is NOT a line break to the loader: this file is two lines,
    the first malformed (it holds a CR inside a rule) and the second good. The
    list shows line 2 and skips line 1 as bad; it never invents a line 3."""
    as_seat(monkeypatch, "ivan")
    path = locked_with(b"ride to:x@*\rwake to:y@*\nride to:z@*\n")
    layers = rules.load_layers(None, SESSION)
    assert [e.line for e in layers.errors if e.source == str(path)] == [1], "the loader's view"
    result = pager_cmd("rule", "list", "--locked")
    assert result.exit_code == EX_OK, result.stderr
    numbered = [ln.split()[0] for ln in result.stdout.splitlines() if ln.startswith("  ")]
    assert numbered == ["2"], (numbered, result.stdout)
    assert "bad (skipped)" in result.stdout


# ── P1 of the do-016 review: a byte that is not UTF-8 must not crash a verb ──
# Written to fail first against a93bdda: `pager rule list --locked` on a file
# holding b"ride subject:\xff*\n" printed line 1 and died with an uncaught
# UnicodeEncodeError('surrogates not allowed'), exit 1. The read keeps the byte
# as a lone surrogate so a rewrite can put it back; the PRINT boundary must
# show it (U+FFFD), never raise.

BAD_BYTE_FILE = b"ride to:x@*\nride subject:\xff*\n"


def test_rule_list_shows_a_line_with_a_byte_that_is_not_utf8(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    locked_with(BAD_BYTE_FILE)
    result = pager_cmd("rule", "list", "--locked")
    assert result.exception is None, repr(result.exception)
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)
    assert "ride to:x@*" in result.stdout
    assert "ride subject:\ufffd*" in result.stdout, result.stdout


@pytest.mark.parametrize("flag", ["--seat", "--locked", "--all", None])
def test_every_rule_list_layer_survives_a_byte_that_is_not_utf8(rig, monkeypatch, flag):
    as_seat(monkeypatch, "ivan")
    locked_with(BAD_BYTE_FILE)
    for layer in (rules._seat_rules(_seat.resolve(), _seat.home()),
                  _seat.session_root(SESSION) / rules.RULES_FILE):   # 3.3a: <session dir>/mail/
        layer.parent.mkdir(parents=True, exist_ok=True)
        layer.write_bytes(BAD_BYTE_FILE)
    result = pager_cmd("rule", "list", *([flag] if flag else []))
    assert result.exception is None, repr(result.exception)
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)


def test_rule_add_and_remove_keep_a_byte_that_is_not_utf8_and_do_not_crash(rig, monkeypatch):
    """The other print boundaries: `add` and `remove` echo the rule, and a
    rewrite must put the undecodable byte back exactly as it was."""
    as_seat(monkeypatch, "ivan")
    path = locked_with(BAD_BYTE_FILE)
    added = pager_cmd("rule", "add", "--locked", "ride", "to:y@*")
    assert added.exception is None and added.exit_code == EX_OK, (added.stdout, added.stderr)
    assert path.read_bytes() == BAD_BYTE_FILE + b"ride to:y@*\n"
    removed = pager_cmd("rule", "remove", "--locked", "ride", "to:y@*")
    assert removed.exception is None and removed.exit_code == EX_OK, (removed.stdout, removed.stderr)
    assert path.read_bytes() == BAD_BYTE_FILE


def test_rule_add_with_an_argv_word_that_is_not_utf8_does_not_crash(rig, monkeypatch):
    """argv can hold a lone surrogate too (a shell passing raw bytes)."""
    as_seat(monkeypatch, "ivan")
    locked_with(b"ride to:x@*\n")
    result = pager_cmd("rule", "remove", "--locked", "ride", "subject:\udcff*")
    assert not isinstance(result.exception, UnicodeError), repr(result.exception)
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "holds no `ride subject:\ufffd*`" in result.stdout
