"""The ride-along's header line keeps its bytes through a refactor.

`pager._queue_line` is the ride-along's one renderer of who and what. The
monitor's notice is to reuse its pieces, so they are lifted out of it into
a helper. That lift must change nothing a reader sees: every line below
was captured from `_queue_line` BEFORE the lift and is pinned here as a
literal. A failure means a caller's output changed.

The voice half pins the other lift: the choice between a heard header, a
fenced one and none, now `_views.voice_header`, must stay the one
`_views.view` makes.

Hermetic: pure calls on stand-ins, messages built from bytes with an
explicit ENV (test_views.py's), and two message files written under
`tmp_path` and parsed by `_maildir.parse`. No date is read through the
local zone: the dated stand-in returns a fixed clock from `astimezone()`.
Synthetic ids only.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sessionmail import _maildir, _seat, _views, pager


class _Date:
    """A stand-in date: its `astimezone()` is fixed, so no TZ is read."""

    def astimezone(self):
        return datetime(2030, 6, 9, 9, 5, 7)


class _Parsed:
    def __init__(self, subject="a subject", sender_local="eve", date=None, mark=None):
        self.subject, self.sender_local, self.date = subject, sender_local, date
        if mark is not None:
            self.unreadable_mark = mark


def _entry(name: str = "1.M1P1.host", recovered: str = "") -> _seat.QueueEntry:
    return _seat.QueueEntry(name, None, recovered=recovered)


H = _views.Heard
ESC, CSI, BEL = "\x1b", "\x9b", "\x07"
LONG = ("word " * 60).strip()

#: name: (entry, parsed, extras, action, heard)
CASES = {
    "unparsed": (_entry(), None, (), "", None),
    "unparsed_recovered": (_entry(recovered="tmp.abc"), None, (), "ride", None),
    "mail": (_entry(), _Parsed(), (), "", None),
    "mail_dated": (_entry(), _Parsed(date=_Date()), (), "", None),
    "mail_empty_fields": (_entry(), _Parsed(subject="", sender_local=""), (), "", None),
    "mail_none_fields": (_entry(), _Parsed(subject=None, sender_local=None), (), "", None),
    "mail_controls": (_entry(), _Parsed(subject=f"a{ESC}[31mred{CSI}b{BEL}\nnext\tcol",
                                        sender_local=f"e{ESC}ve"), (), "", None),
    "mail_control_only": (_entry(), _Parsed(subject=ESC, sender_local=BEL), (), "", None),
    "mail_unreadable": (_entry(), _Parsed(subject="trailing \t",
                                          mark="  <unreadable header: Message-ID, Date>"),
                        (), "", None),
    "mail_unreadable_empty_subject": (_entry(), _Parsed(subject=ESC,
                                                        mark="  <unreadable header: Subject>"),
                                      (), "", None),
    "mail_spaces": (_entry(), _Parsed(subject="  lots   of   space  ", sender_local=" eve "),
                    (), "", None),
    "mail_nonascii": (_entry(), _Parsed(subject="café naïve — 日本 ✓", sender_local="zoë"),
                      (), "", None),
    "mail_action": (_entry(), _Parsed(), (), "ride", None),
    "mail_extras_action": (_entry(), _Parsed(), ("priority: urgent", "device: air"), "ride", None),
    "mail_long_fit": (_entry(), _Parsed(subject=LONG), (), "ride", None),
    "mail_long_extras": (_entry(), _Parsed(subject=LONG), ("priority: urgent",), "wake", None),
    "mail_recovered_extras": (_entry(recovered="tmp.x"), _Parsed(date=_Date()),
                              ("priority: low",), "ride", None),
    "heard_trusted": (_entry(), _Parsed(date=_Date()), (), "", H(device="airpods", speaker="judy")),
    "heard_trusted_nodate_nospeaker": (_entry(), _Parsed(), (), "ride", H(device="", speaker="")),
    "heard_unverified": (_entry(), _Parsed(date=_Date()), ("priority: urgent",), "ride",
                         H(device="air pods", speaker="", verified=False)),
    "heard_controls": (_entry(), _Parsed(date=_Date()), (), "",
                       H(device=f"air{ESC}pods", speaker=f"ju{BEL}dy")),
    "heard_long_device": (_entry(), _Parsed(), (), "ride", H(device="d" * 200, speaker="judy")),
}

#: Captured from `_queue_line` before the lift. Never edit one to pass.
BEFORE = {
    "unparsed": "[mail] 1.M1P1.host",
    "unparsed_recovered": "[mail] 1.M1P1.host (recovered from tmp.abc) (ride)",
    "mail": "[mail] --:-- eve a subject",
    "mail_dated": "[mail] 09:05 eve a subject",
    "mail_empty_fields": "[mail] --:-- ? (no subject)",
    "mail_none_fields": "[mail] --:-- ? (no subject)",
    "mail_controls": "[mail] --:-- eve a[31mredb next col",
    "mail_control_only": "[mail] --:--",
    "mail_unreadable": "[mail] --:-- eve trailing <unreadable header: Message-ID, Date>",
    "mail_unreadable_empty_subject": "[mail] --:-- eve <unreadable header: Subject>",
    "mail_spaces": "[mail] --:-- eve lots of space",
    "mail_nonascii": "[mail] --:-- zoë café naïve — 日本 ✓",
    "mail_action": "[mail] --:-- eve a subject (ride)",
    "mail_extras_action": "[mail] --:-- eve a subject  (priority: urgent)  (device: air) (ride)",
    "mail_long_fit": "[mail] --:-- eve word word word word word word word word word word word"
                     " word word word w… (ride)",
    "mail_long_extras": "[mail] --:-- eve word word word word word word word word word word w…"
                        "  (priority: urgent) (wake)",
    "mail_recovered_extras": "[mail] 09:05 eve a subject  (priority: low)  (recovered from tmp.x)"
                             " (ride)",
    "heard_trusted": "[heard 09:05:07 airpods judy]",
    "heard_trusted_nodate_nospeaker": "[heard --:--:-- ?] (ride)",
    "heard_unverified": "[heard UNVERIFIED 09:05:07 air pods]  (priority: urgent) (ride)",
    "heard_controls": "[heard 09:05:07 airpods judy]",
    "heard_long_device": "[heard --:--:-- " + "d" * 72 + "… (ride)",
}


def test_every_case_has_its_captured_line():
    assert CASES.keys() == BEFORE.keys()


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_header_line_is_byte_identical_to_before(name):
    entry, parsed, extras, action, heard = CASES[name]
    assert pager._queue_line(entry, parsed, extras, action=action, heard=heard) == BEFORE[name]


#: name: (raw message, captured line). Parsed for real, no `Date:` header.
FILES = {
    "named_sender_encoded_subject": (
        "From: Eve <eve@example.invalid>\r\nTo: alpha@example.invalid\r\n"
        "Subject: =?utf-8?b?Y2Fmw6kg4oCUIOaXpeacrA==?=\r\n"
        "Message-ID: <t9-a@example.invalid>\r\n\r\nbody\r\n",
        "[mail] --:-- eve café — 日本",
    ),
    "no_from_no_subject": (
        "To: alpha@example.invalid\r\nMessage-ID: <t9-b@example.invalid>\r\n\r\nbody\r\n",
        "[mail] --:-- ? (no subject)",
    ),
}


@pytest.mark.parametrize("name", sorted(FILES))
def test_a_parsed_file_renders_as_before(tmp_path, name):
    raw, line = FILES[name]
    path = tmp_path / "1.M1P1.host"
    path.write_bytes(raw.encode())
    parsed = _maildir.parse(path, 1, True)
    assert pager._queue_line(_entry(), parsed) == line


@pytest.mark.parametrize("name", sorted(CASES))
def test_who_what_is_the_header_lines_own_pieces(name):
    """The lifted helper gives the mail line's sender, subject and mark,
    each already one line with no control character in it."""
    _entry_, parsed, *_ = CASES[name]
    if parsed is None:
        return
    sender, subject, mark = pager._who_what(parsed)
    for piece in (sender, subject, mark):
        assert piece == " ".join(piece.split())
        assert not any(ord(c) in _views.CONTROLS for c in piece)
    if CASES[name][4] is None:
        line = BEFORE[name]
        whole = " ".join(f"{sender} {subject} {mark}".split())
        if "…" in line:                 # a long subject, cut: the kept part is a prefix
            assert whole.startswith(line[len("[mail] --:-- "):line.index("…")])
        else:
            assert whole in line


# The heard/fenced choice, lifted out of `_views.view`: the one place a
# renderer asks which header a voice mail is headed by.

from conftest import KEEPER_SIGNED  # noqa: E402
from test_views import ENV, KEEPER_FROM, _msg  # noqa: E402

VOICE = {
    "trusted_spoken": ((KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
                        "Ears-Speaker: judy@hq"), KEEPER_SIGNED,
                       H(device="airpods", speaker="judy")),
    "trusted_not_spoken": ((KEEPER_FROM, "Ears-Kind: cut", "Ears-Device: airpods"),
                           KEEPER_SIGNED, None),
    "unverified": ((KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
                    "Ears-Speaker: judy@hq"), None,
                   H(device="airpods", speaker="", verified=False)),
    "plain_mail": (("eve@hq",), KEEPER_SIGNED, None),
    "plain_from_the_keeper": ((KEEPER_FROM,), KEEPER_SIGNED, None),
}


@pytest.mark.parametrize("name", sorted(VOICE))
def test_voice_header_is_the_views_own_choice(name):
    fields, signed, expected = VOICE[name]
    m = _msg(*fields)
    assert _views.voice_header(m, ENV, signed) == expected
    assert _views.view(m, "1", ENV, signed).heard == expected


def test_voice_header_takes_a_recognition_already_made():
    m = _msg(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods", "Ears-Speaker: judy@hq")
    rec = _views.recognise(m, ENV, KEEPER_SIGNED)
    assert _views.voice_header(m, ENV, voice=rec) == H(device="airpods", speaker="judy")
