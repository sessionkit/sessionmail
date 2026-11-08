"""The pager monitor's line names who wrote and what about.

specs/pager, "The pager is a nudge, and the re-arm carries": the one line
`pager listen --monitor` prints for a page keeps the count first, names the
NEWEST waiting mail's sender (the local part) and its quoted subject, says
who it was for when that was not this session, and stays one line within
MONITOR_LINE_MAX. It changes the TEXT of the line, never whether or when a
page fires. A heard voice turn is named as the ride-along's heard header
names it (T10, at the end): the speaker only when trusted, UNVERIFIED and no
speaker otherwise, never its subject (the speech's first words).

Hermetic: real message files under tmp_path with synthetic ids
(`@example.invalid`) and synthetic names; the monitor rig is
tests/test_pager_monitor.py's, with the mail hooks OFF, its confirm mail
sent to the conftest's recorder, never an MTA.
"""

from __future__ import annotations

import os
import time
import unicodedata
from email.header import Header
from pathlib import Path
from types import SimpleNamespace

import pytest

from sessionmail import _settings, _shorten, pager
from conftest import KEEPER_SIGNED
from test_pager_monitor import (DEADLINE, WatchedRunner, driven, lines, monitor,  # noqa: F401
                                rig, settle, until)

SID = "session-for-the-notice-tests-xyzwvut"  # synthetic, as long as a real one (36)
PROJECT = "proj+super"
_n = 0


def write(box: Path, mid: str, *, sender: "str | None" = "Ann Synthetic <ann@example.invalid>",
          subject: "str | bytes | None" = "Wake notices: ready for review",
          original_to: "str | None" = f"{PROJECT}@example.invalid",
          extra: bytes = b"") -> SimpleNamespace:
    """One message file in BOX's new/, as the MDA leaves it, and the arrival
    the pager hands `monitor_notice` for it (name, path, message_id)."""
    global _n
    _n += 1
    for d in ("tmp", "new", "cur"):
        (box / d).mkdir(parents=True, exist_ok=True)
    head = b""
    if sender is not None:
        head += f"From: {sender}\n".encode()
    if original_to is not None:
        head += f"X-Original-To: {original_to}\n".encode()
    if subject is not None:
        head += b"Subject: " + (subject if isinstance(subject, bytes) else subject.encode()) + b"\n"
    head += f"Message-ID: <{mid}>\n".encode() + extra
    name = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.notice"
    (box / "tmp" / name).write_bytes(head + b"\nTHE BODY, never on the line\n")
    os.rename(box / "tmp" / name, box / "new" / name)
    return SimpleNamespace(name=name, path=box / "new" / name, message_id=mid)


@pytest.fixture
def box(tmp_path) -> Path:
    return tmp_path / "mail" / SID


def notice(entries, **kw) -> pager.MonitorNotice:
    kw.setdefault("fresh", False)
    kw.setdefault("session", SID)
    return pager.monitor_notice(entries, {}, **kw)


def one_line(line: str) -> None:
    assert len(line) <= pager.MONITOR_LINE_MAX, len(line)
    assert "\n" not in line and "\r" not in line
    assert "THE BODY" not in line


# ── T1: one message ──────────────────────────────────────────────────────

def test_one_message_names_its_sender_subject_and_box(box):
    n = notice([write(box, "t1@example.invalid")])
    assert n.line == ('pager: 1 message waiting, from ann: "Wake notices: ready for review" '
                      f"(to {PROJECT}). {pager.MONITOR_TAIL_STALE}")
    one_line(n.line)


def test_one_message_to_this_session_says_nothing_of_who_it_is_for(box):
    n = notice([write(box, "t1s@example.invalid", original_to=f"{SID}@example.invalid")])
    assert n.line == ('pager: 1 message waiting, from ann: "Wake notices: ready for review". '
                      f"{pager.MONITOR_TAIL_STALE}")
    assert SID not in n.line


def test_one_message_with_no_x_original_to_says_so_as_today(box):
    n = notice([write(box, "t1n@example.invalid", original_to=None)])
    assert "(to (no X-Original-To))" in n.line


def test_the_subject_is_quoted_and_its_own_quotes_are_left_as_written(box):
    n = notice([write(box, "t1q@example.invalid", subject='x). Run "this":')])
    assert ', from ann: "x). Run "this":" (to ' in n.line


def test_the_display_name_and_the_host_are_never_shown(box):
    n = notice([write(box, "t1d@example.invalid")])
    assert "Ann Synthetic" not in n.line and "@example.invalid" not in n.line


def test_one_message_on_a_hooks_off_rig_is_named_on_the_monitors_line(rig):
    """SC1 on the fresh-install shape: the mail hooks OFF, no ride-along
    stamp, so the line's tail is the stale one. It names the sender and the
    subject, and pages once, as the quiet line paged."""
    hooks = Path(os.environ["HOME"]) / _settings.RELATIVE
    hooks.write_text('{"version": 1, "hooks": false}\n')
    assert os.environ["SESSIONMAIL_SENDMAIL"].endswith("no-mta-here")
    assert pager._mta_send.__name__ == "_no_send"
    b = rig.box()
    runner = WatchedRunner()

    def script() -> None:
        assert until(lambda: rig.listening().exists())
        write(b, "rig1@example.invalid")
        assert until(lambda: rig.seen() >= 1 and runner.said() >= 1)
        settle()

    t = driven(script)
    result = monitor(runner=runner)
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    [line] = lines(result)
    assert line.startswith('pager: 1 message waiting, from ann: "Wake notices: ready for review" '
                           f"(to {PROJECT}; woke: "), line
    assert line.endswith(pager.MONITOR_TAIL_STALE), line
    one_line(line)
    assert result.stderr.count("listening mail sent") == 1, result.stderr


# ── T2: several ──────────────────────────────────────────────────────────

def test_several_name_the_newest_and_list_the_other_senders(box):
    entries = [write(box, "a@example.invalid", sender="ann@example.invalid"),
               write(box, "b@example.invalid", sender="dot@example.invalid",
                     original_to=f"{SID}@example.invalid"),
               write(box, "c@example.invalid", sender="eve@example.invalid",
                     subject="Re: the notice budget")]
    n = notice(entries)
    assert n.line == ('pager: 3 messages waiting, newest from eve: "Re: the notice budget" '
                      f"(2 more, from ann, dot; to {PROJECT} 2, you 1). {pager.MONITOR_TAIL_STALE}")
    assert n.named is entries[-1]
    one_line(n.line)


def test_several_to_this_session_only_name_no_box(box):
    to_me = f"{SID}@example.invalid"
    entries = [write(box, f"s{i}@example.invalid", original_to=to_me) for i in range(2)]
    n = notice(entries)
    assert n.line == ('pager: 2 messages waiting, newest from ann: '
                      f'"Wake notices: ready for review" (1 more, from ann). '
                      f"{pager.MONITOR_TAIL_STALE}")


def test_other_senders_are_bounded_oldest_first_then_counted(box):
    names = ["ann", "bob", "cy", "dee", "eve", "fay"]
    entries = [write(box, f"o{i}@example.invalid", sender=f"{who}@example.invalid")
               for i, who in enumerate(names)]
    n = notice(entries)
    assert "(5 more, from ann, bob, cy, +2 more; " in n.line, n.line
    assert 'newest from fay: "' in n.line


def test_many_boxes_are_bounded_as_today(box):
    entries = [write(box, f"x{i}@example.invalid", original_to=f"box{i}@example.invalid")
               for i in range(pager.GROUP_LINES_MAX + 2)]
    n = notice(entries)
    assert "to box0 1, box1 1, box2 1, box3 1, +2 more)" in n.line, n.line


# ── T3: an over-long subject ─────────────────────────────────────────────

def test_a_long_subject_is_cut_at_a_word_with_an_ellipsis(box):
    subject = " ".join(f"word{i:04d}" for i in range(250))         # 2,249 chars
    n = notice([write(box, "long@example.invalid", subject=subject)])
    quoted = n.line.split(': "', 1)[1].split('"', 1)[0]
    assert quoted.endswith(_shorten.ELLIPSIS)
    assert len(quoted) <= pager.NOTICE_SUBJECT_MAX
    assert subject.startswith(quoted[: -len(_shorten.ELLIPSIS)] + " ")
    assert n.line.startswith("pager: 1 message waiting, from ann: ")
    assert n.line.endswith(pager.MONITOR_TAIL_STALE)
    one_line(n.line)


def test_one_word_longer_than_the_room_is_cut_hard(box):
    word = "w" * 500
    n = notice([write(box, "word@example.invalid", subject=word)])
    want = word[: pager.NOTICE_SUBJECT_MAX - len(_shorten.ELLIPSIS)] + _shorten.ELLIPSIS
    assert f'"{want}"' in n.line
    one_line(n.line)


def test_a_subject_within_the_budget_keeps_every_word(box):
    subject = " ".join(["word"] * 24)                                 # 119 chars
    assert len(subject) < pager.NOTICE_SUBJECT_MAX
    n = notice([write(box, "fits@example.invalid", subject=subject)])
    assert f'"{subject}"' in n.line


def test_trim_is_the_display_cut_itself():
    assert _shorten.trim is _shorten._trim
    assert _shorten.trim("alpha beta gamma", 12) == ("alpha...", False)


# ── T4: non-ASCII ────────────────────────────────────────────────────────

WORDS = "Café naïve – 日本語の件名"


def test_an_encoded_word_subject_is_decoded_not_folded(box):
    encoded = Header(WORDS, "utf-8").encode().encode()
    assert encoded.startswith(b"=?utf-8?")
    n = notice([write(box, "enc@example.invalid", subject=encoded)])
    assert f'from ann: "{WORDS}"' in n.line, n.line
    one_line(n.line)


def test_a_raw_utf8_subject_is_kept_as_written(box):
    n = notice([write(box, "raw@example.invalid", subject=WORDS.encode())])
    assert f'"{WORDS}"' in n.line, n.line
    assert "Ã" not in n.line                                    # no mojibake


def test_a_long_non_ascii_subject_counts_code_points(box):
    subject = " ".join(["日本語"] * 60)                        # 239 code points
    n = notice([write(box, "cjk@example.invalid", subject=subject)])
    quoted = n.line.split(': "', 1)[1].split('"', 1)[0]
    assert len(quoted) <= pager.NOTICE_SUBJECT_MAX and quoted.endswith(_shorten.ELLIPSIS)


# ── T5: missing and unreadable headers ───────────────────────────────────

def test_a_missing_from_is_a_question_mark(box):
    n = notice([write(box, "nofrom@example.invalid", sender=None)])
    assert n.line.startswith('pager: 1 message waiting, from ?: "Wake notices'), n.line


def test_a_missing_subject_says_so(box):
    n = notice([write(box, "nosubj@example.invalid", subject=None)])
    assert 'from ann: "(no subject)" (to ' in n.line, n.line


def test_an_unreadable_header_is_marked_not_hidden(box):
    # The poison Message-ID value tests/test_poison_header.py measures: the
    # stdlib parser faults on it, and `parse` marks it instead of raising.
    a = write(box, "ignored@example.invalid")
    raw = a.path.read_bytes().replace(b"<ignored@example.invalid>", b"<x@[ ")
    a.path.write_bytes(raw)
    n = notice([a])
    assert ('from ann: "Wake notices: ready for review" <unreadable header: Message-ID> (to '
            in n.line), n.line
    assert n.named is a


# ── T6: the extremes ─────────────────────────────────────────────────────

def test_the_extremes_stay_within_the_budget_and_keep_head_and_tail(box, monkeypatch):
    """A seven-digit count, a sender at its budget, a subject past it, more
    boxes and senders than are listed, a mixed reminder (the longest tail):
    at most 480, the count first and the tail last."""
    sender = "s" * 60
    subject = " ".join(["subject"] * 40)
    seeds = [write(box, f"e{i}@example.invalid", sender=f"{sender}{i}@example.invalid",
                   subject=subject, original_to=f"{'b' * 30}{i}@example.invalid")
             for i in range(8)]
    parsed = {a.path: pager._notice_read(a) for a in seeds}
    monkeypatch.setattr(pager, "_notice_read", lambda a: parsed.get(a.path))  # memo: 10**6 reads
    count = 10**6
    entries = [SimpleNamespace(name=f"n{i}", path=seeds[i % 8].path, message_id=f"{i}@example.invalid")
               for i in range(count)]
    rem = {f"n{i}": False for i in range(0, count, 2)}
    for fresh in (True, False):
        n = notice(entries, fresh=fresh, reminders=rem)
        assert n.line.startswith(f"pager: {count} messages waiting, {len(rem)} of them a reminder")
        assert n.line.endswith(f"Reminders: {pager.MONITOR_TAIL_REMINDER}")
        assert "newest from " + "s" * 30 in n.line
        assert "…" in n.line                                   # the middle was cut first
        one_line(n.line)
    every = notice(entries, reminders={a.name: True for a in entries[:10]} | rem)
    one_line(every.line)


def test_the_sender_is_cut_to_its_budget(box):
    local = "a" * (pager.NOTICE_SENDER_MAX + 30)
    n = notice([write(box, "snd@example.invalid", sender=f"{local}@example.invalid")])
    want = local[: pager.NOTICE_SENDER_MAX - len(_shorten.ELLIPSIS)] + _shorten.ELLIPSIS
    assert f"from {want}: " in n.line
    session_local = SID                                     # 36: fits whole
    n = notice([write(box, "sid@example.invalid", sender=f"{session_local}@example.invalid")])
    assert f"from {session_local}: " in n.line


def test_when_the_middle_is_not_enough_the_subject_is_cut_next(monkeypatch):
    """Order of cuts: the middle, then the subject, never the count or the
    tail. A stand-in with a long unreadable mark forces the second cut."""
    mark = "  <unreadable header: Message-ID, X-Original-To, Supersedes, In-Reply-To, References>"
    parsed = SimpleNamespace(sender_local="s" * 40, subject=" ".join(["word"] * 24),
                             unreadable_mark=mark, original_to="far@example.invalid")
    monkeypatch.setattr(pager, "_notice_read", lambda a: parsed)
    entries = [SimpleNamespace(name=f"n{i}", path=Path("/nonexistent"), message_id=None)
               for i in range(1_000_000)]
    rem = {f"n{i}": False for i in range(0, 1_000_000, 3)}
    n = pager.monitor_notice(entries, {}, fresh=True, reminders=rem, session=SID)
    one_line(n.line)
    quoted = n.line.split(': "', 1)[1].split('"', 1)[0]
    assert quoted.endswith(_shorten.ELLIPSIS) and len(quoted) < len(parsed.subject)
    assert "(…). " in n.line                                  # the middle went first
    assert n.line.startswith(f"pager: {10**6} messages waiting, ")
    assert n.line.endswith(f"Reminders: {pager.MONITOR_TAIL_REMINDER}")


# ── T7: control characters ───────────────────────────────────────────────

CONTROLS = ("\x1b", "\x9b", "\x07", "\x7f", "\x00")


def _clean(line: str) -> bool:
    return not any(unicodedata.category(c) == "Cc" for c in line)


def test_no_control_character_reaches_the_line(box):
    subject = Header("a\x1b[31mred\x9b2J\x07bell\nnext", "utf-8").encode().encode()
    a = write(box, "ctl@example.invalid", subject=subject,
              sender="ev\x1bil@example.invalid")
    n = notice([a])
    assert _clean(n.line), repr(n.line)
    assert "red" in n.line and "bell" in n.line and "next" in n.line
    one_line(n.line)


def test_a_box_key_with_a_control_character_is_flattened(box):
    a = write(box, "ctlbox@example.invalid", original_to="pro\x1bj@example.invalid")
    n = notice([a])
    assert _clean(n.line), repr(n.line)


# ── T8: reminders ────────────────────────────────────────────────────────

def test_a_reminder_only_page_keeps_its_head_and_tail_and_names_its_mail(box):
    a = write(box, "rem@example.invalid")
    n = notice([a], reminders={a.name: True})
    assert n.line == ('pager: reminder - 1 message read, not done, from ann: '
                      f'"Wake notices: ready for review" (to {PROJECT}). '
                      f"{pager.MONITOR_TAIL_REMINDER_ONE}")


def test_a_mixed_page_names_the_newest_new_mail_not_a_reminder(box):
    new = write(box, "new@example.invalid", sender="eve@example.invalid", subject="new one")
    old = write(box, "old@example.invalid", sender="dot@example.invalid", subject="reminded")
    n = notice([new, old], fresh=True, reminders={old.name: False})
    assert n.line.startswith('pager: 2 messages waiting, 1 of them a reminder (paged before, '
                             'not done), newest from eve: "new one" (1 more, from dot; '), n.line
    assert n.line.endswith(f"Reminders: {pager.MONITOR_TAIL_REMINDER}")
    assert n.named is new


# ── T11: nothing pages that did not; the line is pure ────────────────────

def test_the_notice_writes_nothing(box, tmp_path):
    entries = [write(box, f"p{i}@example.invalid") for i in range(3)]
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    time.sleep(0.01)
    pager.monitor_notice(entries, {}, fresh=False, session=SID)
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    assert after == before


def test_each_entry_is_parsed_once(box, monkeypatch):
    entries = [write(box, f"once{i}@example.invalid") for i in range(3)]
    seen: list = []
    real = pager._notice_read
    monkeypatch.setattr(pager, "_notice_read", lambda a: seen.append(a.name) or real(a))
    monkeypatch.setattr(pager, "_address_key", lambda a: pytest.fail("parsed twice"))
    pager.monitor_notice(entries, {}, session=SID)
    assert sorted(seen) == sorted(a.name for a in entries)


# ── T12: the inject log ──────────────────────────────────────────────────

def test_the_named_entry_is_a_header_item_and_the_rest_are_counts(box):
    entries = [write(box, f"i{i}@example.invalid") for i in range(3)]
    n = notice(entries)
    items = pager._page_items(entries, named=n.named, given=n.given)
    assert [it["view"] for it in items] == ["count", "count", "header"]
    assert n.given == len(', newest from ann: "Wake notices: ready for review"') > 0
    assert items[2]["given"] == n.given and items[2]["id"] == "i2@example.invalid"
    assert all(it["given"] == 0 for it in items[:2])
    assert all(it["in_mail"] is None and it["cut"] is True for it in items)


def test_a_hand_pages_items_are_counts_as_before(box):
    entries = [write(box, f"h{i}@example.invalid") for i in range(2)]
    assert pager._page_items(entries) == [
        {"id": f"h{i}@example.invalid", "kind": "mail", "view": "count", "given": 0,
         "in_mail": None, "cut": True} for i in range(2)]


def test_a_count_only_line_names_no_item(box):
    gone = write(box, "gone@example.invalid")
    gone.path.unlink()
    n = notice([gone])
    assert n.named is None and n.given == 0
    assert [it["view"] for it in pager._page_items([gone], named=n.named, given=n.given)] == ["count"]


# ── T13: a file scooped between the decision and the line ────────────────

def test_a_scooped_newest_gives_way_to_the_next_readable(box):
    older = write(box, "older@example.invalid", sender="ann@example.invalid", subject="still here")
    newest = write(box, "newest@example.invalid", sender="eve@example.invalid", subject="scooped")
    newest.path.unlink()
    n = notice([older, newest])
    assert 'newest from ann: "still here" (1 more; ' in n.line, n.line
    assert "scooped" not in n.line and n.named is older


def test_when_none_can_be_read_the_line_is_todays(box):
    entries = [write(box, f"g{i}@example.invalid") for i in range(2)]
    for a in entries:
        a.path.unlink()
    n = notice(entries)
    assert n.line == pager._monitor_count_line(entries, {}, fresh=False)
    assert n.line.startswith("pager: 2 messages waiting (")
    assert n.named is None


def test_an_arrival_with_no_path_never_raises():
    n = pager.monitor_notice([SimpleNamespace(name="n1", message_id=None)], {}, session=SID)
    assert n.line.startswith("pager: 1 message waiting") and n.named is None


def test_no_entries_is_todays_line():
    assert pager.monitor_notice([], {}).line == pager._monitor_count_line([], {})


def test_a_line_that_cannot_fit_its_naming_is_todays_line(monkeypatch):
    """The third step: when even a cut subject cannot fit, the count and
    the tail are kept and the naming goes - today's count-only line."""
    mark = "  <unreadable header: " + ", ".join(["X-Header-Name"] * 12) + ">"
    parsed = SimpleNamespace(sender_local="s" * 40, subject="word " * 20,
                             unreadable_mark=mark, original_to="far@example.invalid")
    monkeypatch.setattr(pager, "_notice_read", lambda a: parsed)
    entries = [SimpleNamespace(name=f"n{i}", path=Path("/nonexistent"), message_id=None)
               for i in range(1_000_000)]
    rem = {f"n{i}": False for i in range(0, 1_000_000, 3)}
    n = pager.monitor_notice(entries, {}, fresh=True, reminders=rem, session=SID)
    assert n.named is None and n.given == 0
    assert n.line == pager._monitor_count_line(entries, {}, fresh=True, reminders=rem)
    one_line(n.line)


# ── T10: voice turns ─────────────────────────────────────────────────────
#
# A voice turn is judged by `_views.voice_header`, the ride-along's ONE
# trust decision: TRUSTED (marked, From the keeper, keeper-signed) names the
# speaker; UNVERIFIED (marked, From the keeper, unsigned) names none. Neither
# shows the subject, which Ears writes as the speech's first words. Mail From
# the keeper with no `Ears-Kind` is mail. Synthetic host `hq` (as the views'
# tests), synthetic ids, the keeper's verdict a stand-in (conftest), so no
# verifier runs.

FIRST_WORDS = "Right so the build is green and the release notes"


@pytest.fixture
def hq(monkeypatch):
    """This host is `hq` for the keeper test, with no lookup."""
    monkeypatch.setenv("SESSIONMAIL_HOST", "hq")
    monkeypatch.setenv("MAGICDNS_SUFFIX", "example.invalid")


def turn(box: Path, mid: str, *, kind: "str | None" = "spoken", device: str = "airpods",
         speaker: str = "judy@hq", sender: str = "Ears <heard+airpods@hq>",
         original_to: str = "heard+airpods@hq") -> SimpleNamespace:
    """A spoken turn's file, its Subject the speech's first words, as Ears writes it."""
    extra = b""
    if kind is not None:
        extra += f"Ears-Kind: {kind}\nEars-Device: {device}\nEars-Speaker: {speaker}\n".encode()
    return write(box, mid, sender=sender, subject=FIRST_WORDS, original_to=original_to,
                 extra=extra)


def trusted(_path):
    """The seam BY PATH, every file keeper-signed (the conftest's stand-in)."""
    return KEEPER_SIGNED


def unsigned(_path):
    return lambda _msg: False


def test_a_trusted_spoken_turn_names_its_speaker_and_device_and_no_subject(box, hq):
    n = notice([turn(box, "v1@example.invalid")], signed=trusted)
    assert n.line == ("pager: 1 message waiting, heard from judy on airpods "
                      f"(to heard+airpods). {pager.MONITOR_TAIL_STALE}")
    assert "Right so" not in n.line and '"' not in n.line
    one_line(n.line)


def test_an_unverified_turn_says_so_and_names_no_speaker(box, hq):
    for seam in (unsigned, None):           # a False verdict; no seam: the file has no signature
        n = notice([turn(box, f"v2{seam is None}@example.invalid")], signed=seam)
        assert n.line == ("pager: 1 message waiting, heard (UNVERIFIED) on airpods "
                          f"(to heard+airpods). {pager.MONITOR_TAIL_STALE}")
        assert "judy" not in n.line and "Right so" not in n.line


def test_a_turn_with_no_device_leaves_the_device_out(box, hq):
    n = notice([turn(box, "v3@example.invalid", device="")], signed=trusted)
    assert n.line.startswith("pager: 1 message waiting, heard from judy (to ")
    n = notice([turn(box, "v4@example.invalid", device="")], signed=unsigned)
    assert n.line.startswith("pager: 1 message waiting, heard (UNVERIFIED) (to ")


def test_plain_keeper_mail_is_named_as_mail(box, hq):
    """From the keeper with no `Ears-Kind`: mail, named with its subject."""
    n = notice([turn(box, "v5@example.invalid", kind=None)], signed=trusted)
    assert n.line.startswith(f'pager: 1 message waiting, from heard+airpods: "{FIRST_WORDS}"')
    assert "heard from" not in n.line and "UNVERIFIED" not in n.line


def test_a_marked_mail_not_from_the_keeper_is_mail_as_the_ride_along_reads_it(box, hq):
    n = notice([turn(box, "v6@example.invalid", sender="Ann <ann@example.invalid>")],
               signed=trusted)
    assert n.line.startswith(f'pager: 1 message waiting, from ann: "{FIRST_WORDS}"')


def test_the_trust_choice_is_the_views_own(box, hq, monkeypatch):
    """The notice makes no trust decision of its own: it names what
    `_views.voice_header` chose, and mail when it chose none."""
    from sessionmail import _views
    calls: list = []
    real = _views.voice_header
    monkeypatch.setattr(_views, "voice_header",
                        lambda msg, *a, **kw: calls.append(msg["Message-ID"]) or real(msg, *a, **kw))
    notice([turn(box, "v7@example.invalid")], signed=trusted)
    assert calls == ["<v7@example.invalid>"]
    monkeypatch.setattr(_views, "voice_header", lambda *a, **kw: None)
    n = notice([turn(box, "v8@example.invalid")], signed=trusted)
    assert n.line.startswith(f'pager: 1 message waiting, from heard+airpods: "{FIRST_WORDS}"')


def test_several_with_a_turn_newest_name_it_and_list_the_others(box, hq):
    entries = [write(box, "w1@example.invalid"),
               write(box, "w2@example.invalid", sender="cy@example.invalid"),
               turn(box, "w3@example.invalid")]
    n = notice(entries, signed=trusted)
    assert n.line.startswith("pager: 3 messages waiting, newest heard from judy on airpods "
                             "(2 more, from ann, cy; to ")
    assert "Right so" not in n.line
    one_line(n.line)


def test_a_turn_among_the_others_shows_no_subject(box, hq):
    """Only the NAMED entry shows a subject; the others are senders only."""
    entries = [turn(box, "o1@example.invalid"), write(box, "o2@example.invalid")]
    n = notice(entries, signed=trusted)
    assert 'newest from ann: "Wake notices' in n.line and "Right so" not in n.line


def test_the_named_turn_is_a_header_item(box, hq):
    entries = [write(box, "ih1@example.invalid"), turn(box, "ih2@example.invalid")]
    n = notice(entries, signed=trusted)
    items = pager._page_items(entries, named=n.named, given=n.given)
    assert [it["view"] for it in items] == ["count", "header"]
    assert n.given == len(", newest heard from judy on airpods")


def test_a_turn_that_cannot_be_read_gives_way_to_the_next_readable(box, hq, monkeypatch):
    """Scooped between its header parse and its voice read: the next one is
    named, and never as mail with the speech's first words."""
    entries = [write(box, "u1@example.invalid"), turn(box, "u2@example.invalid")]
    real_parse = pager._notice_read
    monkeypatch.setattr(pager, "_notice_read",
                        lambda a: (real_parse(a), a.path.unlink() if a is entries[1] else None)[0])
    n = notice(entries, signed=trusted)
    assert n.line.startswith('pager: 2 messages waiting, newest from ann: "Wake notices')
    assert "Right so" not in n.line
    lone = [turn(box, "u3@example.invalid")]
    monkeypatch.setattr(pager, "_notice_read",
                        lambda a: (real_parse(a), a.path.unlink())[0])
    n = notice(lone, signed=trusted)
    assert n.named is None and n.line.startswith("pager: 1 message waiting (")
    assert "Right so" not in n.line


# ── T10: what the voice path costs ───────────────────────────────────────

def _parses(monkeypatch) -> list:
    """Every header parse made, by the parser `_maildir` and the voice read
    both use."""
    from email.parser import BytesHeaderParser
    seen: list = []
    real = BytesHeaderParser.parsebytes
    monkeypatch.setattr(BytesHeaderParser, "parsebytes",
                        lambda self, raw, *a, **kw: seen.append(raw[:0]) or real(self, raw, *a, **kw))
    return seen


def test_a_plain_mail_notice_does_no_extra_parse(box, hq, monkeypatch):
    entries = [write(box, f"pp{i}@example.invalid") for i in range(3)]
    asked: list = []
    seen = _parses(monkeypatch)
    notice(entries, signed=lambda path: asked.append(path) or KEEPER_SIGNED)
    assert len(seen) == len(entries)                       # one each, the notice's own read
    assert asked == []                                     # no seam is asked for mail


def test_a_named_turn_costs_one_headers_parse_and_asks_the_seam_once(box, hq, monkeypatch):
    entries = [write(box, "pc1@example.invalid"), turn(box, "pc2@example.invalid")]
    asked: list = []
    seen = _parses(monkeypatch)
    n = notice(entries, signed=lambda path: asked.append(path) or KEEPER_SIGNED)
    assert "heard from judy" in n.line
    assert len(seen) == len(entries) + 1                   # the named turn's headers, once
    assert asked == [entries[1].path]


def test_a_turn_not_named_is_not_parsed_again(box, hq, monkeypatch):
    entries = [turn(box, "pn1@example.invalid"), write(box, "pn2@example.invalid")]
    seen = _parses(monkeypatch)
    notice(entries, signed=trusted)
    assert len(seen) == len(entries)


def test_the_entry_says_whether_it_is_marked_voice(box):
    from sessionmail import _maildir
    for mid, extra, want in (("m1@example.invalid", b"Ears-Kind: spoken\n", True),
                             ("m2@example.invalid", b"X-Ears-Kind: spoken\n", True),
                             ("m3@example.invalid", b"Ears-Kind:\n", True),
                             ("m4@example.invalid", b"", False)):
        a = write(box, mid, extra=extra)
        assert _maildir.parse(a.path, 1, True).voice_marked is want, mid


def test_the_arrival_verdict_asks_the_seam_it_is_handed(box, monkeypatch):
    """The listener hands its judgement and its notice ONE seam per mail."""
    got: list = []
    monkeypatch.setattr(pager, "judge_head", lambda *a, **kw: got.append(kw["signed"]))
    a = write(box, "sv@example.invalid")
    seam = object()
    pager.arrival_verdict(a.path, [], None, signed=seam)
    pager.arrival_verdict(a.path, [], None)
    assert got[0] is seam and got[1] is not seam and callable(got[1])


# ── T6: the voice extreme ────────────────────────────────────────────────

def test_a_turn_with_a_huge_device_and_speaker_stays_within_the_budget(box, hq, monkeypatch):
    big = "d" * 500
    seed = turn(box, "vx@example.invalid", device=big, speaker=f"{'s' * 500}@hq")
    cut = big[: pager.NOTICE_SENDER_MAX - len(_shorten.ELLIPSIS)] + _shorten.ELLIPSIS
    n = notice([seed], signed=trusted)
    assert f"heard from {'s' * 37}{_shorten.ELLIPSIS} on {cut} (" in n.line
    one_line(n.line)
    n = notice([seed], signed=unsigned)
    assert f"heard (UNVERIFIED) on {cut} (" in n.line
    one_line(n.line)
    others = [write(box, f"vo{i}@example.invalid", sender=f"{'o' * 60}{i}@example.invalid",
                    original_to=f"{'b' * 30}{i}@example.invalid") for i in range(8)]
    parsed = {a.path: pager._notice_read(a) for a in [*others, seed]}
    monkeypatch.setattr(pager, "_notice_read", lambda a: parsed.get(a.path))
    count = 10**6
    entries = [SimpleNamespace(name=f"n{i}", path=others[i % 8].path,
                               message_id=f"{i}@example.invalid") for i in range(count - 1)]
    entries.append(SimpleNamespace(name="last", path=seed.path, message_id="last@example.invalid"))
    rem = {f"n{i}": False for i in range(0, count - 1, 2)}
    for fresh in (True, False):
        n = notice(entries, fresh=fresh, reminders=rem, signed=trusted)
        assert n.line.startswith(f"pager: {count} messages waiting, {len(rem)} of them a reminder"
                                 f" ({pager.reminder_state(rem)}, not done), newest heard from ")
        assert n.line.endswith(f"Reminders: {pager.MONITOR_TAIL_REMINDER}")
        one_line(n.line)
