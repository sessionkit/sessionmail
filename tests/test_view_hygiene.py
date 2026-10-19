"""Fold-ins from the SM-442 engineer-fitness review (task store,
reviews/2030-10-03T0212-engineer-fitness.md), each small and pinned here.

  - F10: `pager.py` annotated `Callable` without importing it. Harmless at
    run time under `from __future__ import annotations`, but any tool that
    resolves the hints (typing.get_type_hints, a type checker) fails.
  - F2: the ride-along's words go straight into a seat's context and its
    terminal, so `_views.text_part` drops C0 control characters except
    newline and tab: an ESC in a body must not carry an ANSI escape
    sequence there. Hermetic: the scoop tests reuse test_views.py's
    scratch seat (temp roots only).
"""

from __future__ import annotations

import typing

import pytest

from sessionmail import _views, pager
from sessionmail import _voice as voice_mod
from test_views import ENV, _arrive, _given, _msg, _raw, scoop  # noqa: F401
from test_ride_budget_marks import _voice, ride  # noqa: F401

ESC = "\x1b"
#: Every C0 control but newline and tab.
DROPPED = "".join(chr(c) for c in range(0x20) if chr(c) not in "\n\t")


def test_the_pagers_annotations_resolve():
    """F10: every annotation in pager's view path names something imported."""
    hints = typing.get_type_hints(pager._entry_view)
    assert "Callable" in str(hints["read_boxes"])


# ── F2: C0 control characters never reach the view ─────────────────────

def test_the_text_part_drops_an_ansi_escape():
    body = f"before {ESC}[31mred{ESC}[0m after {ESC}]0;title\x07 end"
    words = _views.text_part(_msg("eve@hq", body=body + "\n"))
    assert ESC not in words and "\x07" not in words
    assert words == "before [31mred[0m after ]0;title end"


def test_the_text_part_keeps_newline_and_tab_and_drops_every_other_c0():
    body = "a\tb\nc" + DROPPED + "d"
    words = _views.text_part(_msg("eve@hq", body=body + "\n"))
    assert words == "a\tb\ncd"


def test_a_crlf_body_reads_as_lf():
    words = _views.text_part(_msg("eve@hq", body="one\r\ntwo\r\n"))
    assert words == "one\ntwo"


def test_an_undecodable_part_is_stripped_too():
    """The fallback branch (an odd charset: the raw bytes) gets the same
    treatment as a decoded one."""
    msg = _msg("eve@hq", body=f"x{ESC}[2Jy\n",
               content_type="text/plain; charset=no-such-charset")
    words = _views.text_part(msg)
    assert ESC not in words and words.startswith("x") and words.endswith("y")


def test_the_view_counts_the_words_it_gives():
    """The characters counted (the budget's `chars`) are the words GIVEN,
    after the strip, so a dropped ESC is never charged."""
    v = _views.view(_msg("eve@hq", body=f"ab{ESC}cd\n"), "1", ENV)
    assert v.body == ("abcd",) and v.chars == 4 and v.cut == 0


@pytest.mark.parametrize("kind", ["mail", "voice", "unverified"])
def test_the_ride_along_prints_no_escape(ride, kind, monkeypatch):
    """End to end through `pager status --new`: neither a mail's text view
    nor a voice turn's whole view carries a control character out, nor
    (SM-782) an UNVERIFIED turn's fenced view."""
    if kind == "voice":            # a TRUSTED turn: the keeper signed it
        monkeypatch.setattr(voice_mod, "keeper_signed", lambda path, msg, env=None: True)
    body = f"hello {ESC}[2J{ESC}[31mworld\x07"
    raw = (_raw("eve@hq", body=body, subject="esc") if kind == "mail"
           else _voice(body, "esc"))
    _arrive(ride, raw)
    out = _given().output
    assert "hello [2J[31mworld" in out
    head = out.splitlines()[0]
    assert (head.startswith("[heard ") and " airpods" in head) is (kind != "mail")  # its path
    assert ("[heard UNVERIFIED " in out) is (kind == "unverified")
    assert not any(ch in out for ch in DROPPED)


# ── do-013: F2 finished - C1 and DEL too, and the HEADER LINE ───────────
#
# do-005's verdict N1/N2 (task store, reviews/2030-10-03T0302-do-005): F2
# stripped C0 from the text part only. C1 (U+0080-U+009F, the 8-bit CSI
# U+009B among them) and DEL passed through it, and the header line -
# `_queue_line`'s subject and sender, the part a SENDER writes most freely -
# was not stripped at all: `" ".join(line.split())` takes whitespace, not
# ESC or BEL. One helper, `_views.without_controls`, now serves both.

BEL, CSI8, DEL, NEL = "\x07", "\x9b", "\x7f", "\x85"
#: Every C0, DEL and every C1: none may reach a seat's context or terminal.
CONTROLS = "".join(chr(c) for c in (*range(0x20), 0x7F, *range(0x80, 0xA0)))


class _Parsed:
    date = None

    def __init__(self, subject: str = "a subject", sender_local: str = "eve"):
        self.subject, self.sender_local = subject, sender_local


def _line(subject: str = "a subject", sender: str = "eve", extras=(),
          recovered: str | None = None, name: str = "1.M1P1.host", parsed=True):
    from sessionmail import _seat
    entry = _seat.QueueEntry(name, None, recovered=recovered)
    return pager._queue_line(entry, _Parsed(subject, sender) if parsed else None,
                             tuple(extras))


# the helper, pure

def test_the_helper_drops_every_c0_del_and_c1():
    assert _views.without_controls(f"a{CONTROLS}b") == "ab"


def test_the_helper_keeps_what_it_is_told_to_and_nothing_else():
    assert _views.without_controls(f"a\tb\nc{ESC}{CSI8}{DEL}d", keep="\n\t") == "a\tb\ncd"


def test_the_helper_leaves_printable_text_alone():
    """U+00A0 (no-break space) is the first code point past C1: kept, and so
    is everything a person writes."""
    words = "café naïve — 日本 ✓ ~"
    assert _views.without_controls(words) == words


# (b) the text part: C1 and DEL as well as C0

def _utf8(body: str):
    """A body declared UTF-8, so a C1 arrives AS a C1: undeclared, its two
    bytes decode as U+FFFD and the test would prove nothing."""
    return _msg("eve@hq", body=body + "\n", content_type="text/plain; charset=utf-8")


def test_the_text_part_drops_c1_and_del_too():
    body = f"a{CSI8}31mb{DEL}c{NEL}d\x80e\x9ff"
    assert _views.text_part(_utf8(body)) == "a31mbcdef"


def test_the_text_part_still_keeps_newline_and_tab():
    others = "".join(ch for ch in CONTROLS if ch not in "\n\t")
    assert _views.text_part(_utf8(f"a\tb\nc{others}d")) == "a\tb\ncd"


# (a) the header line

@pytest.mark.parametrize("bad", [ESC, BEL, CSI8, DEL, "\x80", "\x9f"])
def test_no_control_in_a_subject_reaches_the_header_line(bad):
    line = _line(subject=f"red{bad}[31malert{bad}")
    assert bad not in line
    assert line.endswith("eve red[31malert")


def test_no_control_in_the_sender_reaches_the_header_line():
    line = _line(sender=f"pi{ESC}]0;x{BEL}p")
    assert ESC not in line and BEL not in line and " pi]0;xp " in line


def test_the_header_line_carries_no_control_character_at_all():
    """Every C0 (newline and tab included: the header line keeps neither),
    DEL and every C1, in subject, sender, extras and the recovered name."""
    line = _line(subject=f"s{CONTROLS}t", sender=f"u{CONTROLS}v",
                 extras=[f"device: a{CONTROLS}b"], recovered=f"r{CONTROLS}q")
    assert not any(ch in line for ch in CONTROLS), repr(line)


def test_whitespace_controls_still_separate_words():
    """A tab or a newline in a subject is a word break, as it was: it
    becomes one space, and never glues two words together."""
    assert _line(subject="one\ttwo\nthree\r\nfour").endswith("eve one two three four")


def test_a_dropped_control_leaves_no_double_space():
    assert _line(subject=f"one {ESC} two").endswith("eve one two")


def test_an_extra_from_a_header_is_stripped():
    """`device:` is the Ears-Device header's value (3.2b): a header too."""
    line = _line(extras=[f"device: air{ESC}[2Jpods"])
    assert ESC not in line and line.endswith("  (device: air[2Jpods)")


def test_an_unparsed_entry_is_stripped_too():
    line = _line(name=f"1.M1P1.ho{ESC}st", parsed=False)
    assert ESC not in line and line.endswith("1.M1P1.host")


def test_the_cut_counts_what_is_printed_not_what_was_dropped():
    """A subject padded with controls is not cut for their length: the
    line is measured after the strip."""
    line = _line(subject=ESC * 400 + "hello", extras=["priority: urgent"])
    assert line.endswith("eve hello  (priority: urgent)")
    assert "…" not in line


# end to end: `pager status --new`

#: click.echo strips a CSI colour sequence (`ESC[31m`) itself when stdout is
#: not a terminal, which would hide an unstripped ESC from these tests. An
#: OSC title sequence, BEL, the 8-bit CSI and DEL it leaves alone, so they
#: prove the strip is OURS. (The pure `_queue_line` tests above bypass click.)
BAD = f"{ESC}]0;t{BEL}{CSI8}2J{DEL}"
UTF8 = "Content-Type: text/plain; charset=utf-8"


@pytest.mark.parametrize("kind", ["mail", "voice"])
def test_the_ride_along_prints_no_control_from_subject_or_body(ride, kind):
    subject, body = f"alert{BAD}now", f"hello {BAD}world"
    raw = (_raw("eve@hq", UTF8, body=body, subject=subject) if kind == "mail"
           else _voice(body, subject, UTF8))
    _arrive(ride, raw)
    out = _given().output
    assert not any(ch in out for ch in CONTROLS if ch != "\n"), repr(out)
    assert "hello ]0;t2Jworld" in out
    # SM-562: a spoken turn's line shows no subject, so only mail prints it.
    assert ("alert]0;t2Jnow" in out) is (kind == "mail")


def test_an_encoded_word_subject_is_stripped_on_the_header_line(ride):
    """RFC 2047 is how a sender puts a C1 or an ESC in a Subject without a
    raw byte: decoded, it must still never reach the header line."""
    import base64
    word = base64.b64encode(f"alert{BAD}now".encode()).decode()
    _arrive(ride, _raw("eve@hq", body="b", subject=f"=?utf-8?b?{word}?="))
    out = _given().output
    assert not any(ch in out for ch in CONTROLS if ch != "\n"), repr(out)
    assert "alert]0;t2Jnow" in out


# (c) the resident `pager status` line (do-014 review; the F2 family)

def _entry(subject="a subject", sender="eve@hq", to="alpha"):
    from datetime import datetime, timezone
    from pathlib import Path
    from sessionmail import _maildir
    return _maildir.Entry(index=1, path=Path("/nowhere/new/1.M1P1.host"), unread=True,
                          message_id="1@h", sender=sender, subject=subject,
                          date=datetime(2030, 6, 9, tzinfo=timezone.utc),
                          delivered_to=to, supersedes="", has_body=True)


@pytest.mark.parametrize("bad", [ESC, BEL, CSI8, DEL, "\x80", "\x9f"])
def test_no_control_in_a_subject_reaches_the_status_line(bad):
    line = pager._box_line(_entry(subject=f"red{bad}[31malert{bad}"))
    assert bad not in line
    assert line.endswith("eve red[31malert")


def test_the_status_line_carries_no_control_character_at_all():
    """Every C0, DEL and C1, in subject, sender and X-Original-To."""
    line = pager._box_line(_entry(subject=f"s{CONTROLS}t", sender=f"u{CONTROLS}v@hq",
                                  to=f"al{CONTROLS}pha"))
    assert not any(ch in line for ch in CONTROLS), repr(line)


def test_the_status_line_still_breaks_words_on_whitespace():
    assert pager._box_line(_entry(subject="one\ttwo\nthree")).endswith("eve one two three")
