"""tasks.md 3.22: the header reader (design D16, the migration DECISION).

Proves *A header this change mints carries no X-, and the reader takes both
spellings* (openspec/changes/pager-genie/specs/pager/spec.md): a header this
change defines is read by its unprefixed name first, else its `X-` form;
when both are present the unprefixed one wins, whatever either says; the
reader records which spelling it read.

Pure: every message is built from bytes here, or written to a scratch
maildir under tmp_path. Nothing reads ~/.mail, ~/.sessionmail or notmuch.
"""

from __future__ import annotations

from email.parser import BytesHeaderParser, BytesParser
from email.policy import compat32
from email.policy import default as default_policy

import pytest

from sessionmail import _headers
from sessionmail._headers import header, read_header


def _msg(*fields: str, body: str = "a turn\n"):
    raw = "\r\n".join(("From: ears+airpods@hq", "To: ears+airpods@hq") + fields)
    raw += "\r\n\r\n" + body
    return BytesParser(policy=default_policy).parsebytes(raw.encode())


# -- the six cases tasks.md 3.22 names --------------------------------------


def test_x_form_only_is_read_and_recorded_as_x():
    r = read_header(_msg("X-Ears-Rule: eve@hq=call"), "Ears-Rule")
    assert r.value == "eve@hq=call"
    assert r.present
    assert r.prefixed is True
    assert r.spelling == "X-Ears-Rule"
    assert r.name == "Ears-Rule"


def test_unprefixed_only_is_read_and_recorded_as_unprefixed():
    r = read_header(_msg("Ears-Rule: eve@hq=call"), "Ears-Rule")
    assert r.value == "eve@hq=call"
    assert r.prefixed is False
    assert r.spelling == "Ears-Rule"


def test_both_disagreeing_the_unprefixed_wins():
    # the spec's scenario "Both spellings, and they disagree"
    m = _msg("Ears-Rule: ivan@hq=call", "X-Ears-Rule: eve@hq=call")
    r = read_header(m, "Ears-Rule")
    assert r.value == "ivan@hq=call"
    assert r.prefixed is False
    assert r.ignored == ("X-Ears-Rule",)
    assert header(m, "Ears-Rule") == "ivan@hq=call"


def test_both_the_unprefixed_wins_whatever_the_order():
    # the X- form FIRST on the wire: position does not decide, the name does
    m = _msg("X-Ears-Rule: eve@hq=call", "Ears-Rule: ivan@hq=call")
    r = read_header(m, "Ears-Rule")
    assert r.value == "ivan@hq=call"
    assert r.spelling == "Ears-Rule"
    assert r.ignored == ("X-Ears-Rule",)


def test_neither_is_absent_not_empty():
    r = read_header(_msg("Subject: hi"), "Ears-Rule")
    assert r.value is None
    assert r.present is False
    assert r.spelling is None
    assert r.prefixed is None
    assert r.ignored == ()
    assert header(_msg("Subject: hi"), "Ears-Rule") is None


@pytest.mark.parametrize(
    "field, asked",
    [
        ("ears-rule: eve@hq=call", "Ears-Rule"),     # lower case on the wire
        ("EARS-RULE: eve@hq=call", "Ears-Rule"),     # upper case on the wire
        ("x-ears-rule: eve@hq=call", "Ears-Rule"),   # lower-case X- form
        ("X-EARS-RULE: eve@hq=call", "ears-rule"),   # the caller's case, too
        ("Ears-Rule: eve@hq=call", "EARS-RULE"),
    ],
)
def test_mixed_case_in_the_name(field, asked):
    r = read_header(_msg(field), asked)
    assert r.value == "eve@hq=call"
    assert r.name == "Ears-Rule"                     # canonical, whatever was asked
    assert r.spelling == field.split(":", 1)[0]      # recorded as it was ON THE WIRE
    assert r.prefixed is field.lower().startswith("x-")


def test_mixed_case_both_spellings_the_unprefixed_still_wins():
    m = _msg("x-EARS-rule: eve@hq=call", "eArS-rUlE: ivan@hq=call")
    r = read_header(m, "Ears-Rule")
    assert r.value == "ivan@hq=call"
    assert r.prefixed is False
    assert r.spelling == "eArS-rUlE"


def test_x_pager_note_only():
    # SM-474's notes read Pager-Note through this reader (design D16, item 1)
    r = read_header(_msg("X-Pager-Note: listening"), "Pager-Note")
    assert r.value == "listening"
    assert r.prefixed is True
    assert r.spelling == "X-Pager-Note"


def test_pager_note_unprefixed_wins_over_x():
    r = read_header(_msg("X-Pager-Note: a", "Pager-Note: b"), "Pager-Note")
    assert (r.value, r.prefixed) == ("b", False)


# -- coverage: every header D16 names takes both spellings ------------------


@pytest.mark.parametrize("name", _headers.MIGRATING)
def test_every_renamed_header_takes_both_spellings(name):
    assert read_header(_msg(f"X-{name}: old"), name).value == "old"
    assert read_header(_msg(f"{name}: new"), name).value == "new"
    both = read_header(_msg(f"X-{name}: old", f"{name}: new"), name)
    assert (both.value, both.prefixed) == ("new", False)


def test_the_migrating_set_is_exactly_d16s():
    assert set(_headers.MIGRATING) == {
        "Ears-Kind", "Ears-Rule", "Ears-Seq", "Ears-Start", "Ears-End",
        "Ears-Source", "Ears-Device", "Ears-Speaker", "Pager-Note",
    }
    # 3970: none of the names this change mints starts with X-
    assert not any(n.lower().startswith("x-") for n in _headers.MIGRATING)


# -- Alert-Id: read through the reader, but NO X- fallback (D16, P-10) ------


def test_alert_id_is_read_in_either_case():
    # `message send --header alert-id=...` writes Alert-ID (_send.title_case);
    # field names are case-insensitive, so the reader reads it (D16, casing)
    r = read_header(_msg("Alert-ID: disk-full-7"), "Alert-Id")
    assert r.value == "disk-full-7"
    assert r.name == "Alert-Id"
    assert r.spelling == "Alert-ID"
    assert r.prefixed is False


def test_x_alert_id_is_not_a_fallback():
    # D16: "No code mints X-Alert-Id today, so there is nothing to migrate
    # and no reader to keep." The X- fallback is for the MIGRATING set only.
    r = read_header(_msg("X-Alert-Id: disk-full-7"), "Alert-Id")
    assert r.present is False
    assert r.value is None


# -- the fallback never widens past the migrating set -----------------------


def test_priority_never_falls_back_to_x_priority():
    # X-Priority is a mail client's own, different header (1-5); it is not
    # an old spelling of Priority:, and reading it would invent a level.
    r = read_header(_msg("X-Priority: 1 (Highest)"), "Priority")
    assert r.present is False
    r = read_header(_msg("X-Priority: 1", "Priority: urgent"), "Priority")
    assert (r.value, r.spelling, r.ignored) == ("urgent", "Priority", ())


def test_asking_for_an_x_name_is_refused():
    # callers name the header this change mints; the reader owns the X- form
    with pytest.raises(ValueError, match="unprefixed"):
        read_header(_msg("X-Ears-Rule: a"), "X-Ears-Rule")


# -- repeats: the first of the winning spelling, the rest recorded ----------


def test_repeats_the_first_of_the_winning_spelling_is_read():
    m = _msg("Ears-Rule: first", "Ears-Rule: second", "X-Ears-Rule: old")
    r = read_header(m, "Ears-Rule")
    assert r.value == "first"
    assert r.values == ("first", "second")
    assert r.ignored == ("X-Ears-Rule",)


def test_an_empty_unprefixed_header_is_present_and_still_wins():
    # presence decides, not truthiness: a keeper that writes the new name
    # meant the new name, even empty (D16 item 2, "whatever either says")
    m = _msg("Ears-Rule:", "X-Ears-Rule: eve@hq=call")
    r = read_header(m, "Ears-Rule")
    assert r.present is True
    assert r.value == ""
    assert r.prefixed is False


# -- the message shapes the pager actually holds -----------------------------


def test_folded_value_is_unfolded():
    m = _msg("Ears-Rule: eve@hq=call,\r\n ivan@hq=call")
    assert header(m, "Ears-Rule") == "eve@hq=call, ivan@hq=call"


def test_compat32_message_reads_the_same():
    raw = b"From: ears@hq\r\nX-Ears-Kind: turn\r\n\r\nbody\r\n"
    m = BytesParser(policy=compat32).parsebytes(raw)
    r = read_header(m, "Ears-Kind")
    assert (r.value, r.prefixed) == ("turn", True)


def test_a_synthetic_keeper_mail_in_a_scratch_box(tmp_path):
    # the shape the pager meets: a file in a scratch ears box, headers only
    cur = tmp_path / "ears" / "cur"
    cur.mkdir(parents=True)
    f = cur / "1908540800.1.hq:2,"
    f.write_bytes(
        b"From: ears+airpods@hq\r\nTo: ears+airpods@hq\r\n"
        b"X-Ears-Kind: turn\r\nEars-Device: airpods\r\n"
        b"X-Ears-Rule: eve@hq=call\r\n\r\nhey eve\r\n"
    )
    m = BytesHeaderParser(policy=default_policy).parsebytes(f.read_bytes())
    kind, dev, rule = (read_header(m, n) for n in ("Ears-Kind", "Ears-Device", "Ears-Rule"))
    assert (kind.value, kind.prefixed) == ("turn", True)
    assert (dev.value, dev.prefixed) == ("airpods", False)
    assert (rule.value, rule.prefixed) == ("eve@hq=call", True)
