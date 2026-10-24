"""pager-genie 3.2a and 3.2b: the ride-along carries a VIEW of each message.

openspec/changes/pager-genie, tasks.md 3.2a (the text view for mail) and
3.2b (the whole view for voice, and the header line's additions). The spec
requirement proven here: *The ride-along carries a view of each message, by
kind* (its scenarios "A short mail, whole", "A long mail, capped with a
pointer" and "A multipart mail"), plus 3.1's *"the marker From eve (capped
like mail)"* half, which is 3.2a's text view.

Hermetic: the pure half builds messages from bytes with an explicit ENV,
never the box's hostname. The scoop half runs `pager status --new` in a
scratch SESSIONMAIL_HOME and SESSION_MAIL_ROOT under tmp_path, with a fake
notmuch. Nothing touches ~/.mail, ~/.sessionmail, the real index or a live
`pager listen`. The keeper's host is the synthetic `hq`, set by env, as
test_voice_recognition.py sets it.
"""

from __future__ import annotations

import inspect
import json
import os
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as default_policy

import pytest
from click.testing import CliRunner

from sessionmail import _notmuch, _seat, _views, pager
from sessionmail.pager import cli
from conftest import KEEPER_SIGNED
from fakes import forest, write_fake

ENV = {"SESSIONMAIL_HOST": "hq", "MAGICDNS_SUFFIX": "example.ts.net"}
KEEPER_FROM = "Ears <heard+airpods@hq>"


def _msg(sender: str | None, *fields: str, body: str = "a short mail\n",
         content_type: str | None = None):
    head = ([f"From: {sender}"] if sender is not None else []) + [
        "To: alpha@hq", "Subject: a subject", "Message-ID: <v@example.invalid>",
    ]
    if content_type:
        head.append(f"Content-Type: {content_type}")
    raw = "\r\n".join(head + list(fields)) + "\r\n\r\n" + body
    return BytesParser(policy=default_policy).parsebytes(raw.encode())


def _alternative(text: str, html: str) -> str:
    return (
        "--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" + text + "\r\n"
        "--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + html + "\r\n"
        "--B--\r\n"
    )


def _words(n: int) -> str:
    """N characters of body text, no trailing whitespace, one line."""
    return ("abcdefghi " * (n // 10 + 1))[:n - 1] + "z"


# ── 3.2a: the text view for mail ────────────────────────────────────────


def test_a_900_character_mail_is_given_whole():
    body = _words(900)
    v = _views.view(_msg("eve@hq", body=body + "\n"), "1", ENV)
    assert v.kind == _views.TEXT and v.voice is False
    assert "\n".join(v.body) == body
    assert v.chars == 900 and v.cut == 0
    assert not any("more - message read" in ln for ln in v.body)


def test_a_5412_character_mail_is_capped_at_2000_with_the_row_812():
    body = _words(5412)
    v = _views.view(_msg("eve@hq", body=body), "812", ENV)
    assert v.kind == _views.TEXT
    assert v.body[0] == body[:2000]
    assert v.body[-1] == "… 3,412 more - message read 812"
    assert v.chars == 2000 and v.cut == 3412


def test_the_cap_is_2000_exactly():
    assert _views.TEXT_CAP == 2000
    at = _views.view(_msg("eve@hq", body=_words(2000)), "9", ENV)
    over = _views.view(_msg("eve@hq", body=_words(2001)), "9", ENV)
    assert at.cut == 0 and len(at.body) == 1
    assert over.cut == 1 and over.body[-1] == "… 1 more - message read 9"


def test_multipart_alternative_gives_only_the_text_part():
    m = _msg("eve@hq", "MIME-Version: 1.0",
             content_type='multipart/alternative; boundary="B"',
             body=_alternative("the plain words", "<p>the <b>html</b> words</p>"))
    v = _views.view(m, "3", ENV)
    shown = "\n".join(v.body)
    assert shown == "the plain words"
    for never in ("<p>", "html", "--B", "Content-Type", "MIME"):
        assert never not in shown


def test_a_mail_with_no_text_part_names_its_door_and_gives_no_html():
    m = _msg("eve@hq", content_type="text/html; charset=utf-8",
             body="<p>only html</p>\n")
    v = _views.view(m, "4", ENV)
    assert v.body == ("… no text part - message read 4",)
    assert v.chars == 0


def test_an_empty_body_adds_no_lines():
    v = _views.view(_msg("eve@hq", body=""), "5", ENV)
    assert v.body == () and v.cut == 0


def test_the_pointer_is_asked_only_when_a_line_needs_it():
    asked: list[int] = []

    def ptr() -> str:
        asked.append(1)
        return "7"

    _views.view(_msg("eve@hq", body=_words(100)), ptr, ENV)
    assert asked == []                       # whole: no box is listed
    v = _views.view(_msg("eve@hq", body=_words(2100)), ptr, ENV)
    assert asked == [1] and v.body[-1].endswith("message read 7")


# ── 3.2b: the whole view for voice ──────────────────────────────────────


@pytest.mark.parametrize("kind", ["Ears-Kind: spoken", "X-Ears-Kind: spoken"])
def test_a_2500_character_voice_turn_is_printed_whole(kind):
    body = _words(2500)
    v = _views.view(_msg(KEEPER_FROM, kind, body=body), "6", ENV, KEEPER_SIGNED)
    assert v.kind == _views.WHOLE and v.voice is True
    assert "\n".join(v.body) == body
    assert v.chars == 2500 and v.cut == 0


# ── SM-782: UNVERIFIED voice is fenced ──────────────────────────────────


def test_sm782_regression_an_unsigned_keeper_turn_is_neither_whole_nor_believed():
    """THE regression test, in the API 57f9fa6 already had, so it FAILS there
    (measured: `whole`, 16,000 characters, speaker `judy`) and passes here."""
    m = _msg(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Speaker: judy@hq", body=_words(18_000))
    v = _views.view(m, "6", ENV)
    assert v.kind != _views.WHOLE and v.voice is False
    assert v.chars <= _views.TEXT_CAP
    assert _views.heard(m, ENV) is None
    assert _views.recognise(m, ENV).voice is False


@pytest.mark.parametrize("kind", ["Ears-Kind: spoken", "X-Ears-Kind: spoken"])
def test_an_unsigned_keeper_turn_is_bounded_at_the_mail_cap_not_given_whole(kind):
    # The regression: at 57f9fa6 this got the WHOLE view, 16,000 characters.
    body = _words(18_000)
    v = _views.view(_msg(KEEPER_FROM, kind, "Ears-Speaker: judy@hq", body=body), "6", ENV)
    assert v.kind == _views.TEXT and v.voice is False and v.unverified is True
    assert v.chars == _views.TEXT_CAP and v.cut == 18_000 - _views.TEXT_CAP
    assert v.body[0] == _views.UNVERIFIED_FENCE
    assert v.body[1] == body[:_views.TEXT_CAP]
    assert v.body[-1] == f"… {18_000 - _views.TEXT_CAP:,} more - message read 6"
    assert len(v.body) == 3


def test_an_unverified_turn_is_headed_unverified_and_its_speaker_is_not_believed():
    m = _msg(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
             "Ears-Speaker: judy@hq", "Priority: urgent")
    v = _views.view(m, "1", ENV)
    assert v.heard == _views.Heard(device="airpods", speaker="", verified=False)
    assert v.extras == ("priority: urgent",)     # the device is in the bracket
    assert "judy" not in "\n".join(v.body)
    assert _views.heard(m, ENV) is None          # the believed-speaker reader says no


def test_the_fence_says_the_words_are_unsigned_and_not_the_keepers():
    fence = _views.UNVERIFIED_FENCE
    assert "UNVERIFIED" in fence and "unsigned" in fence
    assert "NOT the keeper's verified words" in fence
    assert "\n" not in fence


@pytest.mark.parametrize("kind", ["Ears-Kind: cut", "Ears-Kind: spoken"])
def test_every_unverified_kind_is_fenced_not_only_a_spoken_turn(kind):
    v = _views.view(_msg(KEEPER_FROM, kind, body="a notice"), "1", ENV)
    assert v.unverified and v.body == (_views.UNVERIFIED_FENCE, "a notice")


def test_an_unverified_turn_with_no_words_still_carries_its_fence():
    assert _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=""), "1", ENV).body == (
        _views.UNVERIFIED_FENCE,)
    html = _msg(KEEPER_FROM, "Ears-Kind: spoken", body="<p>x</p>", content_type="text/html")
    v = _views.view(html, "4", ENV)
    assert v.body == (_views.UNVERIFIED_FENCE, "… no text part - message read 4")


def test_the_inject_log_records_an_unverified_turn_as_mail_not_heard():
    unverified = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body="hey"), "1", ENV)
    trusted = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body="hey"), "1", ENV,
                          KEEPER_SIGNED)
    assert pager._ride_item("u@hq", unverified, ["x"], True)["kind"] == "mail"
    assert pager._ride_item("t@hq", trusted, ["x"], True)["kind"] == "heard"


def test_the_fence_line_is_not_counted_as_words_given():
    v = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(900)), "1", ENV)
    assert v.chars == 900 and v.cut == 0 and v.given_whole


def test_an_unverified_turn_is_spent_against_the_budget_like_mail():
    # Not the always-admit voice branch: past the budget it gets the header only.
    budget = _views.Budget(limit=2_500)
    first = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(2_000)), "1", ENV)
    second = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(900)), "2", ENV)
    assert budget.admit(first) is True and budget.spent == 2_000
    assert budget.admit(second) is False and budget.past == 1 and budget.closed
    trusted = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(900)), "3", ENV,
                          KEEPER_SIGNED)
    assert budget.admit(trusted) is True          # O12: a trusted turn is still given


def test_the_marker_from_eve_is_capped_like_mail():
    # 3.1: "the marker From eve@<host> (capped like mail)": the same
    # 2,500-character turn, not from the keeper, gets the TEXT view
    v = _views.view(_msg("eve@hq", "Ears-Kind: spoken", body=_words(2500)), "8", ENV)
    assert v.kind == _views.TEXT and v.voice is False
    assert v.cut == 500 and v.body[-1] == "… 500 more - message read 8"


def test_a_voice_turn_past_the_ceiling_is_cut_at_16000():
    v = _views.view(_msg(KEEPER_FROM, "Ears-Kind: spoken", body=_words(40_000)), "11", ENV,
                    KEEPER_SIGNED)
    assert v.kind == _views.WHOLE
    assert v.chars == 16_000 and v.cut == 24_000
    assert v.body[-1] == "… 24,000 more - message read 11"


# ── 3.2b: the header line's additions ───────────────────────────────────


@pytest.mark.parametrize("value, shown", [
    ("non-urgent", ("priority: non-urgent",)),
    ("URGENT", ("priority: urgent",)),
    ("normal", ()),
    ("soon", ()),                   # not a level: normal, so nothing
])
def test_the_header_line_shows_the_level_when_it_is_not_normal(value, shown):
    assert _views.extras(_msg("eve@hq", f"Priority: {value}"), voice=False) == shown


def test_a_missing_priority_shows_nothing():
    assert _views.extras(_msg("eve@hq"), voice=False) == ()


@pytest.mark.parametrize("field", ["Ears-Device: airpods", "X-Ears-Device: airpods"])
def test_a_voice_turn_shows_its_device(field):
    """SM-562 amends 3.2b for a SPOKEN turn: its device is shown in the
    heard bracket (`View.heard`), not as a `device:` extra; a level that is
    not normal is still an extra."""
    m = _msg(KEEPER_FROM, "Ears-Kind: spoken", field, "Priority: urgent")
    v = _views.view(m, "1", ENV, KEEPER_SIGNED)
    assert v.extras == ("priority: urgent",)
    assert v.heard is not None and v.heard.device == "airpods" and v.heard.verified


@pytest.mark.parametrize("field", ["Ears-Device: airpods", "X-Ears-Device: airpods"])
def test_a_voice_turn_that_is_not_spoken_still_shows_its_device_as_an_extra(field):
    """Only a SPOKEN turn takes the heard line (SM-562): any other trusted
    voice kind (a cut notice) keeps 3.2b's `device:` extra, unchanged."""
    m = _msg(KEEPER_FROM, "Ears-Kind: cut", field, "Priority: urgent")
    v = _views.view(m, "1", ENV, KEEPER_SIGNED)
    assert v.extras == ("priority: urgent", "device: airpods") and v.heard is None


def test_mail_that_is_not_trusted_voice_shows_no_device():
    m = _msg("eve@hq", "Ears-Kind: spoken", "Ears-Device: airpods")
    assert _views.view(m, "1", ENV).extras == ()


def test_the_header_line_keeps_its_additions_however_long_the_subject():
    entry = _seat.QueueEntry("1.M1P1.host", None)

    class Parsed:
        date = None
        sender_local = "eve"
        subject = "s" * 300

    line = pager._queue_line(entry, Parsed(), ("priority: urgent",))
    assert len(line) == pager.LINE_WIDTH
    assert line.endswith("…  (priority: urgent)")
    assert pager._queue_line(entry, Parsed()) == pager._queue_line(entry, Parsed(), ())


# ── the row: `message read` must open THIS message ──────────────────────


def _box(root, name: str, count: int, cur_every: int = 3) -> list[str]:
    """COUNT files in box NAME, oldest first; every CUR_EVERY-th in cur/
    with a flag suffix, as a read message sits. Returns their names."""
    for d in ("tmp", "new", "cur"):
        (root / name / d).mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(1, count + 1):
        n = f"{1923171200 + i}.M{i}P1.test"
        where, fname = ("cur", n + ":2,S") if i % cur_every == 0 else ("new", n)
        (root / name / where / fname).write_text(
            f"From: beta@example.invalid\nSubject: {i}\nMessage-ID: <{i}@example.invalid>\n\nx\n")
        names.append(n)
    return names


def test_the_row_is_the_index_message_list_prints(tmp_path, monkeypatch):
    from sessionmail import _maildir
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    names = _box(tmp_path, "sess-a", 30)
    listed = _maildir.enumerate_box("sess-a")
    for n in (names[0], names[11], names[-1]):
        box, row = _views.locate(n, ["sess-a"])
        assert box == "sess-a"
        assert _maildir._key(listed[row - 1].path) == n     # the same message


def test_the_pointer_names_the_box_when_it_is_not_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    _box(tmp_path, "sess-a", 2)
    names = _box(tmp_path, "alpha", 5)
    assert _views.pointer(names[3], "4@example.invalid", "sess-a", ("alpha",)) == "alpha 4"
    assert _views.pointer("9.M9P9.gone", "g@example.invalid", "sess-a", ("alpha",)) \
        == "g@example.invalid"


def test_a_pointer_by_id_is_a_command_a_shell_can_run(tmp_path, monkeypatch):
    """Found in no box, the pointer is the Message-ID: BARE, because the cut
    line is pasted into a shell and `message read <g@h>` is a syntax error
    there (`<` redirects). `message read` still reads it as an id."""
    import shlex
    import subprocess
    from sessionmail import message
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    _box(tmp_path, "sess-a", 1)
    for mid in ("g@example.invalid", "<g@example.invalid>"):
        p = _views.pointer("9.M9P9.gone", mid, "sess-a")
        assert p == "g@example.invalid"
        assert message._looks_like_an_id(p)
        line = f"message read {p}"
        assert shlex.split(line) == ["message", "read", "g@example.invalid"]
        assert subprocess.run(["bash", "-n", "-c", line]).returncode == 0


def test_a_control_in_a_senders_message_id_never_reaches_the_pointer(tmp_path, monkeypatch):
    """do-013 residue N2 (ruled): the Message-ID fallback is a
    sender's words, so every control is dropped from it - an ESC (and the
    CSI it would start) never reaches the pointer. An ordinary id comes
    through unchanged, and stays a command a shell can run."""
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    _box(tmp_path, "sess-a", 1)
    ordinary = "2000000000.11111.11111@hq.session-mail"
    assert _views.pointer("9.M9P9.gone", f"<{ordinary}>", "sess-a") == ordinary
    evil = "<g\x1b[2J\x1b]0;pwn\x07\x9b31m@example.invalid>"
    p = _views.pointer("9.M9P9.gone", evil, "sess-a")
    assert "\x1b" not in p and "\x9b" not in p and "\x07" not in p
    assert not any(ord(c) in _views.CONTROLS for c in p)
    import shlex
    assert shlex.split(p) == ["g[2J]0;pwn31m@example.invalid"]   # stripped, then quoted (V3)


#: V3 (do-014): ids RFC 5322 allows, or a sender can write, that a shell
#: would act on if the pointer were pasted bare. None is ever executed here.
#: These pin `pointer`'s contract for ANY string it is given. Some never reach
#: it whole from a real message (the parse cuts them: CUT_IDS below); the
#: end-to-end tests cover what the pager actually holds.
HOSTILE_IDS = ("a$(id)@h", "a`id`@h", "a;id@h", "a|id@h", "a'b@h", "a b@h",
               "a&&id@h", "a>x@h", "$HOME@h", "a\\b@h", "a\"b@h")


@pytest.mark.parametrize("mid", HOSTILE_IDS)
def test_a_pointer_is_shell_quoted_and_round_trips(tmp_path, monkeypatch, mid):
    """do-014, V3 (ruled): the printed pointer is shell-quoted,
    so pasting `message read <pointer>` runs nothing and hands `message
    read` EXACTLY the id. Proven with `shlex.split` (POSIX shell word
    splitting); the payload is never given to a real shell."""
    import shlex
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    _box(tmp_path, "sess-a", 1)
    for given in (mid, f"<{mid}>"):
        p = _views.pointer("9.M9P9.gone", given, "sess-a")
        assert shlex.split(f"message read {p}") == ["message", "read", mid]
        assert p != mid                       # every one of these needed quoting


def test_a_plain_id_prints_unchanged_and_round_trips(tmp_path, monkeypatch):
    import shlex
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    _box(tmp_path, "sess-a", 1)
    for mid in ("2000000000.11111.11111@hq.session-mail", "g@example.invalid",
                "a+b-c_d.e@h.example"):
        p = _views.pointer("9.M9P9.gone", f"<{mid}>", "sess-a")
        assert p == mid
        assert shlex.split(f"message read {p}") == ["message", "read", mid]


def test_the_cut_line_carries_the_quoted_pointer():
    """Both printed forms (`… N more - message read P` and `… no text part -
    message read P`) take the pointer whole, so the quoting reaches them."""
    import shlex
    mid = "a$(id)@h"
    quoted = shlex.quote(mid)
    long_mail = _msg("eve@hq", body="x" * (_views.TEXT_CAP + 10))
    cut = _views.view(long_mail, quoted).body[-1]
    assert shlex.split(cut.split(" - ", 1)[1]) == ["message", "read", mid]
    html = _msg("eve@hq", body="<p>x</p>", content_type="text/html")
    none = _views.view(html, quoted).body[-1]
    assert shlex.split(none.split(" - ", 1)[1]) == ["message", "read", mid]


#: do-014 review: ids that start with `-` (shell-safe, so unquoted) would be
#: read by `message read` as an OPTION; the pointer ends option parsing.
DASH_IDS = ("-rf@h", "--help@h", "-a$(id)@h")


@pytest.mark.parametrize("mid", DASH_IDS)
def test_a_leading_dash_id_is_printed_after_the_end_of_options(tmp_path, monkeypatch, mid):
    import shlex
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(tmp_path))
    _box(tmp_path, "sess-a", 1)
    p = _views.pointer("9.M9P9.gone", f"<{mid}>", "sess-a")
    assert p.startswith("-- ")
    assert shlex.split(f"message read {p}") == ["message", "read", "--", mid]


#: Ids that SURVIVE the pager's own header parse (`_maildir.parse`,
#: policy=default) and are RFC 5322 atext: a backtick, `$`, `|`, `&`, `'`, a
#: leading `-`. (`(`, `;` and a space are not atext, and the parse cuts an
#: id there, the same for the pager and for `message read`.)
PARSED_IDS = ("-rf@h", "a`id`@h", "a|id@h", "a&&id@h", "a'b@h", "$HOME@h",
              "-a`id`@h", "2000000000.11111.11111@hq.session-mail")


@pytest.mark.parametrize("mid", PARSED_IDS)
def test_the_printed_pointer_opens_that_message_in_message_read(tmp_path, monkeypatch, mid):
    """End to end, with NO shell: the id as the pager holds it (parsed from
    the file by `_maildir.parse`), its printed pointer, `shlex.split` of the
    pasted line, and the words handed to the real `message read` (click, in
    process). It opens exactly that message, reads no word of the id as an
    option, and nothing is ever run."""
    assert _held_pointer_opens_it(tmp_path, monkeypatch, mid) == mid   # the parse keeps it


#: do-014 re-submission (the review's section 2): the HOSTILE_IDS that are
#: NOT atext. The pager never holds them whole: `_maildir.parse` cuts the id
#: at `(`, `;`, a space, `>`, a backslash or a double quote, the same for the
#: pager and for `message read`. What it DOES hold is still a sender's words,
#: so the pointer of the cut id is tested through the same end-to-end path.
#: The cut is pinned so a change to the parse shows here, not in the field.
CUT_IDS = {"a$(id)@h": "a$", "a;id@h": "a", "a b@h": "a", "a>x@h": "a",
           "a\\b@h": "a", 'a"b@h': "a"}


@pytest.mark.parametrize("mid", sorted(CUT_IDS))
def test_an_id_the_parse_cuts_still_opens_that_message(tmp_path, monkeypatch, mid):
    """End to end, with NO shell, for the hostile ids that are not atext:
    the pager holds the CUT id, its pointer is `shlex.split` into the words
    `message read` is given, and those words open exactly that message."""
    assert _held_pointer_opens_it(tmp_path, monkeypatch, mid) == CUT_IDS[mid]


def _held_pointer_opens_it(tmp_path, monkeypatch, mid):
    """Deliver a message with Message-ID <MID> and a decoy; take the id as
    the pager holds it (`_maildir.parse`); print its by-id pointer; hand
    `shlex.split` of the pasted line to the real `message read` (click, in
    process, `--peek`). Assert it opens that message and not the decoy, and
    return the held id. Nothing is ever run in a shell."""
    import shlex
    from sessionmail import _maildir
    from sessionmail.message import cli as message_cli
    from test_message_list import deliver
    root = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    deliver(root, "alpha", "1923171201", subject="the one", mid=mid)
    deliver(root, "alpha", "1923171202", subject="a decoy")
    held = _maildir.parse(root / "alpha" / "new" / "1923171201.M1P1.host", 1, True).message_id
    p = _views.pointer("9.M9P9.gone", held, "nowhere")    # the by-id form
    words = shlex.split(f"message read {p}")
    assert words[:2] == ["message", "read"]
    assert words[2:] in ([held], ["--", held])
    r = CliRunner().invoke(message_cli, ["read", "--peek", *words[2:]])
    assert r.exit_code == 0, r.output
    assert "#1  the one" in r.output and "a decoy" not in r.output
    return held


def test_the_views_hold_no_wake_logic():
    src = inspect.getsource(_views)
    assert "wakeable" not in src.replace("NO wake logic", "")
    assert "def wakeable" not in src


# ── the scoop: `pager status --new` gives the views ─────────────────────

SESSION = "sess-a"


@pytest.fixture
def scoop(tmp_path, monkeypatch):
    """A scratch seat `alpha`, session `sess-a`, its box born, armed."""
    home = tmp_path / "sessionmail"
    (home / "seats" / "alpha").mkdir(parents=True)
    mail = tmp_path / "mail"
    for d in ("tmp", "new", "cur"):
        (mail / SESSION / d).mkdir(parents=True)
    payload = tmp_path / "answer.json"
    payload.write_text(json.dumps(forest()))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(mail))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(write_fake(tmp_path / "fake-notmuch")))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_JSON", str(payload))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "is_listening", lambda seat, session, ps=None: os.getpid())
    # SM-442 x SM-474 (refresh-001's merge): these tests read the VIEW, so
    # the scoop runs with no rules layers and its lines carry no `(action)`.
    # The action that applied is SM-474's half (test_pager_notes_and_actions),
    # and the two together on one line are test_ride_views_and_actions.
    monkeypatch.setattr(pager, "_layers_or_none", lambda seat, session: None)
    return mail


_n = 0


def _arrive(mail, raw: str, *, before: int = 0) -> str:
    """BEFORE filler files, then RAW, delivered into the session box and
    linked into the ride-along queue as the listener links it. Returns the
    arrival's name."""
    global _n
    box = mail / SESSION
    for _ in range(before):
        _n += 1
        (box / "cur" / f"{1907238896 + _n}.M{_n}P1.test:2,S").write_text(
            f"From: beta@example.invalid\nSubject: old\nMessage-ID: <old{_n}@example.invalid>\n\nx\n")
    _n += 1
    name = f"{1923171200 + _n}.M{_n}P1.test"
    (box / "new" / name).write_bytes(raw.encode())
    q = _seat.ride_dir(None, SESSION) / _seat.QUEUE_NEW
    q.mkdir(parents=True, exist_ok=True)
    os.link(box / "new" / name, q / name)
    return name


def _raw(sender: str, *fields: str, body: str, subject: str = "a subject") -> str:
    return "\n".join([f"From: {sender}", "To: alpha@hq", f"Subject: {subject}",
                      f"Message-ID: <{abs(hash(body + subject)) % 10**9}@example.invalid>",
                      "Date: Sun, 09 Jun 2030 12:00:00 +0000", *fields]) + "\n\n" + body + "\n"


def _given():
    return CliRunner().invoke(cli, ["status", "--new"], prog_name="pager")


def test_scoop_a_short_mail_whole(scoop):
    body = _words(900)
    _arrive(scoop, _raw("eve@hq", body=body, subject="short"))
    out = _given()
    lines = out.output.splitlines()
    assert lines[0].startswith("[mail] ") and "short" in lines[0]
    assert lines[1:] == [body]


def test_scoop_a_long_mail_capped_with_a_pointer_at_row_812(scoop):
    body = _words(5412)
    _arrive(scoop, _raw("eve@hq", body=body, subject="long"), before=811)
    lines = _given().output.splitlines()
    assert "long" in lines[0]
    assert lines[1] == body[:2000]
    assert lines[2] == "… 3,412 more - message read 812"
    assert len(lines) == 3


def test_scoop_a_multipart_mail_gives_only_its_text_part(scoop):
    raw = ("From: eve@hq\nTo: alpha@hq\nSubject: multi\nMessage-ID: <mp@example.invalid>\n"
           'MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary="B"\n\n'
           + _alternative("plain only", "<p>HTML ONLY</p>").replace("\r\n", "\n"))
    _arrive(scoop, raw)
    out = _given().output
    assert "plain only" in out
    assert "HTML ONLY" not in out and "--B" not in out and "<p>" not in out


def test_scoop_a_voice_turn_whole_with_its_device(scoop, keeper_signs):
    body = _words(2500)
    _arrive(scoop, _raw(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
                        body=body, subject="hey alpha"))
    lines = _given().output.splitlines()
    # SM-562: the heard line, device in the bracket, no subject preview.
    hhmmss = datetime(2030, 6, 9, 12, 0, tzinfo=timezone.utc).astimezone().strftime("%H:%M:%S")
    assert lines[0] == f"[heard {hhmmss} airpods ?]"
    assert lines[1:] == [body]


def test_scoop_an_unverified_turn_is_fenced_and_capped(scoop):
    # SM-782: the same turn, unsigned (no keeper_signs): the UNVERIFIED line,
    # no speaker, the fence, then the words capped at the mail cap.
    body = _words(2500)
    _arrive(scoop, _raw(KEEPER_FROM, "Ears-Kind: spoken", "Ears-Device: airpods",
                        "Ears-Speaker: judy@hq", body=body, subject="hey alpha"))
    lines = _given().output.splitlines()
    hhmmss = datetime(2030, 6, 9, 12, 0, tzinfo=timezone.utc).astimezone().strftime("%H:%M:%S")
    assert lines[0] == f"[heard UNVERIFIED {hhmmss} airpods]"
    assert lines[1] == _views.UNVERIFIED_FENCE
    assert lines[2] == body[:_views.TEXT_CAP]
    assert lines[3].startswith("… 500 more - message read ")
    assert "judy" not in "\n".join(lines)


@pytest.mark.parametrize("field, shown", [
    ("Priority: non-urgent", True), ("Priority: normal", False), (None, False),
])
def test_scoop_the_header_line_shows_a_level_that_is_not_normal(scoop, field, shown):
    fields = [field] if field else []
    _arrive(scoop, _raw("eve@hq", *fields, body="hi", subject="levels"))
    head = _given().output.splitlines()[0]
    assert ("(priority: non-urgent)" in head) is shown
    if not shown:
        assert "priority:" not in head


def test_scoop_resolves_the_read_boxes_only_for_a_pointer(scoop, monkeypatch):
    """The scoop runs on every tool batch. The boxes a row is found in are
    resolved only when a cut line needs one, and once per scoop: an empty
    queue and a whole view never pay for it."""
    calls: list[int] = []
    real = pager._read_boxes

    def counted(seat, session):
        calls.append(1)
        return real(seat, session)

    monkeypatch.setattr(pager, "_read_boxes", counted)
    _given()                                              # nothing queued
    _arrive(scoop, _raw("eve@hq", body="short", subject="whole"))
    _given()                                              # a whole view
    assert calls == []
    _arrive(scoop, _raw("eve@hq", body=_words(2100), subject="cut one"))
    _arrive(scoop, _raw("eve@hq", body=_words(2200), subject="cut two"))
    out = _given().output
    assert calls == [1]                                   # two pointers, one lookup
    assert out.count("more - message read ") == 2
