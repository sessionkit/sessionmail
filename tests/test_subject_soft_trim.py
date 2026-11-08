"""SM-978: the subject shortener is the tool's DEFAULT, and `--json` says
what the subject became.

SM-978 flipped the TOOL default: no `set subject-shorten` line means ON.
Every off switch keeps working: the locked `set subject-shorten off`, the
seat variable, `--no-shorten` and the MCP tool's `no_shorten`. The cap stays
at 10 over (`DEFAULT_SHORTEN_MAX`).

`--json` success carries `"subject": {"shortened", "original", "sent"}` on
EVERY send. The MCP tool adds no block: its signal is the report in its
notices block, as specs/message's shorten change says.

Hermetic: the `world` fixture's fake sendmail, asserted before any send; the
locked file is the sandbox's (conftest's `rules.locked_path` seam in-process,
the three-doors wrapper across the MCP child's process boundary), never the
host's own `rules.locked`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from sessionmail import _mcp, _send, rules
from test_message_send_json import _parsed, _send_it, run, world  # noqa: F401  (the fixture)
from test_shorten_three_doors import _mcp_send, _setup

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
         "judy november oscar papa quebec romeo sierra tango uniform victor")
AT_69 = WORDS[:69]                 # exactly the limit
AT_70 = WORDS[:70]                 # one over, many words
ONE_WORD = "x" * 70                # one over, no word boundary at all
NON_ASCII = "SM-978 — “ready” the café is ok"
NON_ASCII_LONG = WORDS[:69] + "é"  # one over AND non-ASCII
OVER_CAP = WORDS[:80]              # 11 over: past the default cap of 10
for _s, _n in ((AT_69, 69), (AT_70, 70), (ONE_WORD, 70), (NON_ASCII_LONG, 70), (OVER_CAP, 80)):
    assert len(_s) == _n, (_s, _n)
assert rules.DEFAULT_SHORTEN_MAX == 10, "the cap is kept at 10"

ID = re.compile(r"<[^>\s]+@[^>\s]+>")


@pytest.fixture
def sandbox(world, tmp_path):  # noqa: F811
    """Assert the hermetic seams BEFORE anything is sent: the fake sendmail is
    the one under tmp, and the locked file is the sandbox's and absent (no
    line: the tool's default)."""
    fake = Path(os.environ["SESSIONMAIL_SENDMAIL"])
    assert fake.is_relative_to(tmp_path) and fake.exists(), fake
    locked = rules.locked_path()
    assert not str(locked).startswith(str(Path.home().resolve() / ".sessionmail")), locked
    assert not locked.exists(), "no locked line: the tool's default"
    return world


def _lock(*lines: str) -> None:
    path = rules.locked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines))


def _notices(r, said, door):
    return said["notices"] if door == "json" else r.stderr.splitlines()


def _sent_and_reported(sandbox, r, said, door, typed):
    """Exit 0; the wire's Subject fits; X-Original-Subject is what was typed;
    the ONE report line names the original length and the result; stdout is
    the id alone (CLI) or the object carries `subject` (--json)."""
    assert r.exit_code == 0, r.output
    msg = _parsed(sandbox)
    sent = msg["Subject"]
    assert sent != typed and sent.isascii() and _send.counted(sent) <= 69
    assert msg["X-Original-Subject"] == typed
    head = f"message send: shortened {len(typed)}->{len(sent)}: \"{sent}\""
    ours = [n for n in _notices(r, said, door) if n.startswith(head)]
    assert len(ours) == 1, _notices(r, said, door)
    if door == "cli":
        assert ID.fullmatch(r.stdout.strip()) and r.stdout.count("\n") == 1, "the id alone"
    else:
        assert r.stderr == "" and said["sent"] == "yes"
        assert said["subject"] == {"shortened": True, "original": typed, "sent": sent}
    return sent


def _refused_70(sandbox, r, said, door):
    """A 70-char subject with the shortener OFF: exit 2, nothing on the wire,
    the words `check_subject` gives, and no shortening said."""
    today = _send.check_subject(AT_70, {})[0]
    assert r.exit_code == 2 and sandbox.wire() is None, r.output
    if door == "json":
        assert said["sent"] == "no" and said["kind"] == "subject" and said["message"] == today
        assert "subject" not in said, "a refusal carries no `subject` object"
    else:
        assert r.stdout == "" and r.stderr.splitlines()[-1] == f"message send: {today}"
    assert "shortened" not in r.output


# ── no locked line: the tool's default is ON ────────────────────────────────

@pytest.mark.parametrize("door", ["cli", "json"])
def test_exactly_69_is_untouched(sandbox, door):
    r, said = _send_it(sandbox, door, AT_69)
    assert r.exit_code == 0, r.output
    msg = _parsed(sandbox)
    assert msg["Subject"] == AT_69 and msg["X-Original-Subject"] is None
    assert not any("shortened" in n or "folded" in n for n in _notices(r, said, door))
    if door == "json":
        assert said["subject"] == {"shortened": False, "original": AT_69, "sent": AT_69}
    else:
        assert ID.fullmatch(r.stdout.strip())


@pytest.mark.parametrize("door", ["cli", "json"])
def test_70_is_shortened_at_a_word_boundary_with_an_ellipsis(sandbox, door):
    r, said = _send_it(sandbox, door, AT_70)
    sent = _sent_and_reported(sandbox, r, said, door, AT_70)
    assert sent.endswith("...")
    kept = sent[:-3]
    assert AT_70.startswith(kept) and AT_70[len(kept)] == " ", "cut where a space was"


@pytest.mark.parametrize("door", ["cli", "json"])
def test_one_word_longer_than_the_limit_is_hard_cut(sandbox, door):
    r, said = _send_it(sandbox, door, ONE_WORD)
    sent = _sent_and_reported(sandbox, r, said, door, ONE_WORD)
    assert sent == "x" * 66 + "..." and len(sent) == 69
    assert any("hard cut" in n for n in _notices(r, said, door))


@pytest.mark.parametrize("door", ["cli", "json"])
def test_non_ascii_is_folded_and_sent(sandbox, door):
    """The fold is ACCEPTED. A fitting subject is only
    folded; a long one is folded and shortened. Never a broken character."""
    r, said = _send_it(sandbox, door, NON_ASCII)
    assert r.exit_code == 0, r.output
    msg = _parsed(sandbox)
    assert msg["Subject"] == 'SM-978 - "ready" the cafe is ok'
    assert msg["X-Original-Subject"] == NON_ASCII
    assert any(n.startswith("message send: folded 31->31:") for n in _notices(r, said, door))
    if door == "json":
        assert said["subject"] == {"shortened": True, "original": NON_ASCII,
                                   "sent": 'SM-978 - "ready" the cafe is ok'}


@pytest.mark.parametrize("door", ["cli", "json"])
def test_non_ascii_and_over_is_folded_and_shortened(sandbox, door):
    r, said = _send_it(sandbox, door, NON_ASCII_LONG)
    sent = _sent_and_reported(sandbox, r, said, door, NON_ASCII_LONG)
    assert sent.isascii() and "é" not in sent


@pytest.mark.parametrize("door", ["cli", "json"])
def test_over_the_cap_is_refused_not_shortened(sandbox, door):
    r, said = _send_it(sandbox, door, OVER_CAP)
    today = _send.check_subject(OVER_CAP, {})[0]
    why = " (not shortened: 11 over; the cap is 10: rewrite it, or front-load the point)"
    assert r.exit_code == 2 and sandbox.wire() is None, r.output
    if door == "json":
        assert said["sent"] == "no" and said["kind"] == "subject"
        assert said["message"] == today + why and "subject" not in said
    else:
        assert r.stdout == "" and r.stderr.splitlines()[-1] == f"message send: {today}{why}"


def test_set_subject_shorten_on_says_the_default_aloud(sandbox):
    _lock("set subject-shorten on")
    r, said = _send_it(sandbox, "json", AT_70)
    _sent_and_reported(sandbox, r, said, "json", AT_70)


# ── every off switch refuses a 70-char subject as before ────────────────────

@pytest.mark.parametrize("door", ["cli", "json"])
def test_off_the_locked_line(sandbox, door):
    _lock("set subject-shorten off")
    r, said = _send_it(sandbox, door, AT_70)
    _refused_70(sandbox, r, said, door)


@pytest.mark.parametrize("off", ["off", "0", "no"])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_off_the_seat_variable(sandbox, door, off, monkeypatch):
    monkeypatch.setenv(_send.SHORTEN_VAR, off)
    r, said = _send_it(sandbox, door, AT_70)
    _refused_70(sandbox, r, said, door)


@pytest.mark.parametrize("door", ["cli", "json"])
def test_off_the_flag(sandbox, door):
    r, said = _send_it(sandbox, door, AT_70, "--no-shorten")
    _refused_70(sandbox, r, said, door)


@pytest.mark.parametrize("value", ["on", "yes"])
def test_the_seat_variable_still_cannot_turn_it_on_and_the_default_stands(sandbox, value,
                                                                          monkeypatch):
    monkeypatch.setenv(_send.SHORTEN_VAR, value)
    r, said = _send_it(sandbox, "json", AT_70)
    _sent_and_reported(sandbox, r, said, "json", AT_70)
    ours = [n for n in said["notices"] if "a seat can only turn the subject shortener off" in n]
    assert len(ours) == 1 and f"{_send.SHORTEN_VAR}={value!r}" in ours[0], said["notices"]
    assert "locked file says off" in ours[0], "the notice names the default truly"


def test_shorten_on_reads_none_as_on_and_false_as_off():
    """The ONE reader, its layers, pure: settings given, the environment given."""
    assert _send.shorten_on({}, settings=rules.Settings()) == (True, "")
    assert _send.shorten_on({}, settings=rules.Settings(subject_shorten=True)) == (True, "")
    assert _send.shorten_on({}, settings=rules.Settings(subject_shorten=False)) == (False, "")
    assert _send.shorten_on({_send.SHORTEN_VAR: "off"}, settings=rules.Settings())[0] is False
    assert _send.shorten_on({}, no_shorten=True, settings=rules.Settings())[0] is False


# ── --json: `subject` on every success ──────────────────────────────────────

def test_json_subject_is_on_a_send_the_shortener_was_off_for(sandbox):
    """`shortened` false, never a missing key, even with the shortener off."""
    r, said = run("beta", "-s", "the point", "-f", str(sandbox.body), "--json", "--no-shorten")
    assert r.exit_code == 0 and said["sent"] == "yes", r.output
    assert said["subject"] == {"shortened": False, "original": "the point", "sent": "the point"}


def test_json_subject_is_not_on_a_refusal(sandbox):
    r, said = run("beta,gamma", "-s", AT_70, "-f", str(sandbox.body), "--json")
    assert r.exit_code == 2 and said["kind"] == "address" and "subject" not in said


# ── the MCP door: a real child, the sandbox's locked file ───────────────────

def _via_mcp(sandbox, monkeypatch, subject, *, locked=None, **more):
    wrapper = _setup(sandbox, monkeypatch, host_on=False)   # the child's file: absent
    if locked:
        (sandbox.tmp / "locked" / "rules.locked").write_text(f"{locked}\n")
    wire = sandbox.tmp / "wire-mcp"
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setattr(_mcp, "SEND_COMMAND", [sys.executable, str(wrapper), "send"])
    result = _mcp_send({"to": ["beta"], "subject": subject, "body": "dear beta\n", **more})
    texts = [getattr(c, "text", "") for c in result.content]
    return result.is_error, texts, wire.read_bytes() if wire.exists() else None


def test_mcp_default_on_sends_70_the_id_alone_then_the_report(sandbox, monkeypatch):
    is_error, texts, wire = _via_mcp(sandbox, monkeypatch, AT_70)
    assert not is_error and wire, texts
    assert ID.fullmatch(texts[0]), "the first block is the Message-ID alone"
    assert len(texts) == 2, "no new block: the report rides in the notices"
    assert re.search(r"^message send: shortened 70->\d+: ", texts[1], re.MULTILINE), texts[1]
    assert b"X-Original-Subject: " + AT_70.encode() in wire.replace(b"\r\n ", b" ").replace(b"\n ", b" ")


@pytest.mark.parametrize("how", ["no_shorten", "locked"])
def test_mcp_off_refuses_70_as_before(sandbox, monkeypatch, how):
    kw = {"no_shorten": True} if how == "no_shorten" else {"locked": "set subject-shorten off"}
    is_error, texts, wire = _via_mcp(sandbox, monkeypatch, AT_70, **kw)
    assert is_error and wire is None, texts
    assert texts[0].startswith(f"not sent: subject: {_send.check_subject(AT_70, {})[0]}"), texts


def test_mcp_over_the_cap_is_refused(sandbox, monkeypatch):
    is_error, texts, wire = _via_mcp(sandbox, monkeypatch, OVER_CAP)
    assert is_error and wire is None, texts
    assert texts[0].startswith("not sent: subject: ") and "not shortened: 11 over" in texts[0]
