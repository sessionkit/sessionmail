"""SM-562: a spoken turn's ride-along line shows WHO spoke, and no subject.

A spoken turn's line carries only what a reader needs to know who is
speaking. Before this, a heard mail rode as

    [mail] 12:00 heard+airpods Right, so the build is green. And the rele… (device: airpods) (ride)

then the body: the speaker (`Ears-Speaker`) was never shown, and the first
words printed twice (Ears writes a turn's Subject as its first words). Now a
TRUSTED turn whose `Ears-Kind` is `spoken` gets ONE header line,

    [heard 12:00:00 airpods judy] (ride)

then the whole view. This amends D4's header row (openspec/changes/
pager-genie/design.md:484); the amendment is recorded beside the row.
Every other mail's line is unchanged, byte for byte (pinned below).

SM-782: TRUSTED means the keeper SIGNED it (the `KEEPER_SIGNED` stand-in
here, `keeper_signs` for a scoop; the signature itself is
test_voice_signed.py's). The same turn UNSIGNED is UNVERIFIED voice: its
line is `[heard UNVERIFIED 17:10:40 airpods]`, never a speaker.

Hermetic: messages are built from bytes with an explicit ENV; the scoop
half runs `pager status --new` in test_views' scratch seat under tmp_path.
Nothing touches ~/.mail.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as default_policy

import pytest
from click.testing import CliRunner

from sessionmail import _seat, _views, pager
from sessionmail.pager import cli

from conftest import KEEPER_SIGNED
from test_views import ENV, _arrive, scoop  # noqa: F401  (scoop is a fixture)

_REAL_LAYERS = pager._layers_or_none

#: The reference mail: a spoken turn's headers as Ears writes them, the
#: Received/Return-Path lines aside. Its words are made up for the test.
REFERENCE = """\
X-Original-To: heard+airpods@hq
From: Ears <heard+airpods@hq>
To: heard+airpods@hq
Subject: Right, so the build is green. And the release notes, the long version. How...
Date: Sun, 09 Jun 2030 12:00:00 +0000
Message-ID: <1902000000.000000.11111.11111@hq.ears>
Ears-Kind: spoken
Ears-Rule: ivan@hq=reply
Ears-Seq: 160508
Ears-Start: 2030-06-09T11:59:40.000Z
Ears-End: 2030-06-09T12:00:00.000Z
Ears-Source: mic
Ears-Device: airpods
Ears-Speaker: {speaker}
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: quoted-printable
MIME-Version: 1.0

Right, so the build is green. And the release notes, the long version. How =
long's a release note when it ships? Does it list every change? Um, we shou=
ld keep it to the changes a user sees um, so that it says what to do next.
"""

FIRST_WORDS = "Right, so the build is green. And the release notes"

#: 12:00:00 UTC, in THIS box's local time, as the line prints it.
WHEN = datetime(2030, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
HHMMSS = WHEN.astimezone().strftime("%H:%M:%S")

ACTION_LAST = re.compile(r" \([a-z]+\)$")


def _reference(speaker: str = "judy@hq", drop: tuple[str, ...] = (),
               sender: str = "Ears <heard+airpods@hq>") -> str:
    raw = REFERENCE.format(speaker=speaker).replace("From: Ears <heard+airpods@hq>",
                                                    f"From: {sender}")
    return "".join(ln for ln in raw.splitlines(keepends=True)
                   if not any(ln.startswith(f"{d}:") for d in drop))


def _msg(raw: str):
    return BytesParser(policy=default_policy).parsebytes(raw.encode())


class _Parsed:
    def __init__(self, subject="Right, so.", sender_local="heard+airpods", date=WHEN):
        self.subject, self.sender_local, self.date = subject, sender_local, date


def _entry(recovered: str | None = None):
    return _seat.QueueEntry("1.M1P1.host", None, recovered=recovered)


# ── the speaker, pure ───────────────────────────────────────────────────

@pytest.mark.parametrize("value, shown", [
    ("judy@hq", "judy"),                                   # local part at the host
    ("judy+other@hq", "judy+other"),                       # a tag is kept
    ("Judy@HQ", "Judy"),                                   # the domain is caseless
    ("judy@hq.example.ts.net", "judy"),              # the FQDN is the host too
    ("Judy <judy@hq>", "judy"),                            # a display name is dropped
    ("carol@example.com", "carol@example.com"),            # a foreign domain is whole
    ("carol@m6", "carol@m6"),                              # another host is foreign
    ("carol", "carol"),                                    # not an address: as written
    (None, "?"),                                           # missing: SEEN as missing
    ("   ", "?"),                                          # empty: the same
])
def test_the_speaker_is_its_local_part_at_this_host_and_whole_elsewhere(value, shown):
    assert _views.speaker_label(value, ENV) == shown


def test_a_speaker_behind_nested_comments_never_raises():
    assert _views.speaker_label("(" * 600 + "x@hq", ENV)


# ── recognition: only a TRUSTED spoken turn ─────────────────────────────

def test_the_reference_turn_is_heard_with_its_device_and_speaker():
    h = _views.heard(_msg(_reference()), ENV, KEEPER_SIGNED)
    assert h == _views.Heard(device="airpods", speaker="judy")


def test_the_reference_turn_unsigned_names_no_speaker():
    # SM-782, the regression: at 57f9fa6 an unsigned keeper From put `judy`,
    # from a header anyone can write, on the line.
    m = _msg(_reference())
    assert _views.heard(m, ENV) is None
    v = _views.view(m, "1", ENV)
    assert v.heard == _views.Heard(device="airpods", speaker="", verified=False)
    line = pager._queue_line(_entry(), _Parsed(), (), action="carry", heard=v.heard)
    assert line == f"[heard UNVERIFIED {HHMMSS} airpods] (carry)"
    assert "judy" not in line


def test_the_unverified_line_has_no_speaker_even_if_one_is_handed_in():
    line = pager._queue_line(_entry(), _Parsed(), (),
                             heard=_views.Heard("airpods", "judy", verified=False))
    assert line == f"[heard UNVERIFIED {HHMMSS} airpods]"


def test_the_x_spellings_are_read_through_the_d16_reader():
    raw = (_reference().replace("Ears-Device:", "X-Ears-Device:")
           .replace("Ears-Speaker:", "X-Ears-Speaker:"))
    assert _views.heard(_msg(raw), ENV, KEEPER_SIGNED) == _views.Heard("airpods", "judy")


def test_a_forged_speaker_from_another_sender_is_not_heard():
    """O22 (F), as `_say.turn` believes `Ears-Speaker`: anyone can write
    `Ears-Speaker: judy@hq`. From eve, it is mail: the mail line, no
    speaker, no bracket."""
    v = _views.view(_msg(_reference(sender="eve@hq")), "1", ENV, KEEPER_SIGNED)
    assert v.heard is None and v.voice is False and v.unverified is False


def test_a_trusted_turn_that_is_not_spoken_keeps_the_mail_line():
    v = _views.view(_msg(_reference().replace("Ears-Kind: spoken", "Ears-Kind: cut")),
                    "1", ENV, KEEPER_SIGNED)
    assert v.heard is None and v.extras == ("device: airpods",)


def test_a_spoken_turn_carries_no_device_extra():
    for signed in (KEEPER_SIGNED, None):
        v = _views.view(_msg(_reference()), "1", ENV, signed)
        assert v.extras == () and v.heard.device == "airpods"


# ── the line, pure ──────────────────────────────────────────────────────

def test_the_heard_line_is_time_device_speaker_then_the_action():
    line = pager._queue_line(_entry(), _Parsed(), (), action="ride",
                             heard=_views.Heard("airpods", "judy"))
    assert line == f"[heard {HHMMSS} airpods judy] (ride)"


def test_the_heard_line_has_no_subject_preview():
    line = pager._queue_line(_entry(), _Parsed(subject=FIRST_WORDS), (),
                             heard=_views.Heard("airpods", "judy"))
    assert line == f"[heard {HHMMSS} airpods judy]"
    assert "Right" not in line and "heard+airpods" not in line


def test_a_missing_device_is_omitted_and_never_crashes():
    line = pager._queue_line(_entry(), _Parsed(), (), action="ride",
                             heard=_views.Heard("", "judy"))
    assert line == f"[heard {HHMMSS} judy] (ride)"


def test_a_missing_speaker_shows_a_question_mark():
    h = _views.heard(_msg(_reference(drop=("Ears-Speaker",))), ENV, KEEPER_SIGNED)
    assert h == _views.Heard("airpods", "?")
    line = pager._queue_line(_entry(), _Parsed(), (), heard=h)
    assert line == f"[heard {HHMMSS} airpods ?]"


def test_a_missing_date_keeps_the_shape():
    line = pager._queue_line(_entry(), _Parsed(date=None), (),
                             heard=_views.Heard("airpods", "judy"))
    assert line == "[heard --:--:-- airpods judy]"


def test_a_level_the_recovered_note_and_the_action_still_follow():
    line = pager._queue_line(_entry(recovered="taking.1"), _Parsed(),
                             ("priority: urgent",), action="wake",
                             heard=_views.Heard("airpods", "judy+other"))
    assert line == (f"[heard {HHMMSS} airpods judy+other]  (priority: urgent)"
                    "  (recovered from taking.1) (wake)")


def test_no_control_character_rides_in_on_the_device_or_the_speaker():
    esc = "\x1b"
    line = pager._queue_line(_entry(), _Parsed(), (), action="ride",
                             heard=_views.Heard(f"air{esc}[2Jpods\n", f"mi{esc}]0;t\x07ke"))
    assert esc not in line and "\x07" not in line and "\n" not in line
    assert line == f"[heard {HHMMSS} air[2Jpods mi]0;tke] (ride)"


def test_a_long_speaker_is_fitted_to_the_line_and_keeps_its_action():
    line = pager._queue_line(_entry(), _Parsed(), (), action="ride",
                             heard=_views.Heard("airpods", "x" * 400))
    assert len(line) == pager.LINE_WIDTH and line.endswith("… (ride)")


# ── ordinary mail: byte-identical ───────────────────────────────────────

def test_an_ordinary_mail_line_is_unchanged_byte_for_byte():
    """Pinned: the mail line SM-562 must not touch, with and without
    extras, an action and a recovered note."""
    hhmm = WHEN.astimezone().strftime("%H:%M")
    parsed = _Parsed(subject="the build is green", sender_local="eve")
    assert pager._queue_line(_entry(), parsed, (), action="ride") == (
        f"[mail] {hhmm} eve the build is green (ride)")
    assert pager._queue_line(_entry(), parsed) == f"[mail] {hhmm} eve the build is green"
    assert pager._queue_line(_entry(recovered="taking.1"), parsed, ("priority: urgent",),
                             action="wake") == (
        f"[mail] {hhmm} eve the build is green  (priority: urgent)"
        "  (recovered from taking.1) (wake)")
    assert pager._queue_line(_entry(), parsed, heard=None) == pager._queue_line(_entry(), parsed)


# ── through `pager status --new` ────────────────────────────────────────

def _given():
    return CliRunner().invoke(cli, ["status", "--new"], prog_name="pager")


def test_scoop_the_reference_turn_rides_as_one_heard_line_then_its_body(scoop, keeper_signs):
    _arrive(scoop, _reference())
    out = _given().output
    lines = out.splitlines()
    assert lines[0] == f"[heard {HHMMSS} airpods judy]"
    assert out.count(FIRST_WORDS) == 1                    # the first words, ONCE
    assert lines[1].startswith(FIRST_WORDS) and "what to do next." in out
    assert "heard+airpods" not in out and "(device:" not in out


def test_scoop_a_guest_is_told_from_judy(scoop, keeper_signs):
    _arrive(scoop, _reference(speaker="judy+other@hq"))
    assert _given().output.splitlines()[0] == f"[heard {HHMMSS} airpods judy+other]"


def test_scoop_judged_the_heard_line_ends_with_its_action(scoop, monkeypatch, keeper_signs):
    monkeypatch.setattr(pager, "_layers_or_none", _REAL_LAYERS)
    _arrive(scoop, _reference())
    out = _given().output
    head = out.splitlines()[0]
    assert head.startswith(f"[heard {HHMMSS} airpods judy] (") and ACTION_LAST.search(head), head
    assert out.count(FIRST_WORDS) == 1


def test_scoop_the_reference_turn_unsigned_is_the_unverified_line_then_the_fence(scoop):
    _arrive(scoop, _reference())
    out = _given().output
    lines = out.splitlines()
    assert lines[0] == f"[heard UNVERIFIED {HHMMSS} airpods]"
    assert lines[1] == _views.UNVERIFIED_FENCE
    assert lines[2].startswith(FIRST_WORDS) and out.count(FIRST_WORDS) == 1
    assert "judy" not in out


def test_scoop_judged_the_unverified_line_ends_with_its_action(scoop, monkeypatch):
    monkeypatch.setattr(pager, "_layers_or_none", _REAL_LAYERS)
    _arrive(scoop, _reference())
    head = _given().output.splitlines()[0]
    assert head.startswith(f"[heard UNVERIFIED {HHMMSS} airpods] (") and ACTION_LAST.search(head)


def test_scoop_an_ordinary_mail_line_is_unchanged(scoop):
    _arrive(scoop, "From: eve@hq\nTo: alpha@hq\nSubject: the build is green\n"
                   "Message-ID: <plain-562@example.invalid>\n"
                   "Date: Sun, 09 Jun 2030 12:00:00 +0000\n\nall green\n")
    lines = _given().output.splitlines()
    hhmm = WHEN.astimezone().strftime("%H:%M")
    assert lines == [f"[mail] {hhmm} eve the build is green", "all green"]
