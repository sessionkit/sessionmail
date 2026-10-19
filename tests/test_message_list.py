"""`message list` - one line per message, and the footer says what it hid.

Hermetic: every test writes its own maildir under `tmp_path` and points
`SESSION_MAIL_ROOT` at it; `SESSIONMAIL_HOME` is the sandbox conftest gives
every test. No notmuch, no real box. Fixtures are `alpha` and `beta` at
`example.invalid` (slipbox/project-conventions.md, 9).

Pinned (specs/message):
  - the index is 1-based over new/ AND cur/, sorted by the maildir key;
  - a superseded message is hidden and the footer counts it, with the why;
  - `--unread` hides what THIS SESSION's marks say is read - never the flag;
  - an EOM message with no body carries the no-body mark, others do not;
  - nothing is renamed or flagged by listing (a peek claims nothing);
  - the listing defaults to the newest 40 and its last line says how many
    it hid; `-n 0` / `-n all` show every row; and the index is the box's
    own, so a cut listing and a full one number a row the same.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from sessionmail import _seat
from sessionmail.message import cli

EX_OK, EX_USAGE, EX_NO_BOX = 0, 2, 2


def deliver(root, box, key, *, sender="beta@example.invalid", subject="hello",
            mid=None, new=True, body="a body\n", supersedes=None, to=None):
    d = root / box / ("new" if new else "cur")
    d.mkdir(parents=True, exist_ok=True)
    (root / box / "tmp").mkdir(exist_ok=True)
    (root / box / "new").mkdir(exist_ok=True)
    (root / box / "cur").mkdir(exist_ok=True)
    mid = mid or f"{key}@example.invalid"
    lines = [f"X-Original-To: {to if to is not None else box}",
             f"From: {sender}", f"To: {box}@example.invalid",
             f"Date: Wed, 12 Jun 2030 {int(key) % 24:02d}:00:00 +1000",
             f"Subject: {subject}", f"Message-ID: <{mid}>"]
    if supersedes:
        lines.append(f"Supersedes: <{supersedes}>")
    raw = "\r\n".join(lines) + "\r\n\r\n" + body
    name = f"{key}.M1P1.host" + ("" if new else ":2,S")
    (d / name).write_text(raw)
    return mid


def bulk(root, box, n, *, first_key=1923171200):
    """N messages in `new/`, keys zero-padded to ONE width so the maildir
    name sort is the delivery order (`_maildir._key` sorts by name).

    Written as bytes rather than through `deliver()` because 8,416 of them
    is the point of the test, not an aside.
    """
    d = root / box
    (d / "tmp").mkdir(parents=True, exist_ok=True)
    (d / "cur").mkdir(exist_ok=True)
    new = d / "new"
    new.mkdir(exist_ok=True)
    for i in range(1, n + 1):
        key = f"{first_key + i:010d}"
        (new / f"{key}.M1P1.host").write_text(
            f"X-Original-To: {box}\r\n"
            f"From: beta@example.invalid\r\n"
            f"To: {box}@example.invalid\r\n"
            f"Date: Wed, 12 Jun 2030 10:00:00 +1000\r\n"
            f"Subject: msg {i}\r\n"
            f"Message-ID: <{key}@example.invalid>\r\n"
            f"\r\n"
            f"a body\n"
        )


@pytest.fixture
def box(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    return root


def run(*args):
    return CliRunner().invoke(cli, ["list", *args])


def test_index_spans_new_and_cur_in_key_order(box):
    deliver(box, "alpha", "1923171202", subject="second", new=False)
    deliver(box, "alpha", "1923171201", subject="first")
    deliver(box, "alpha", "1923171203", subject="third")
    r = run()
    assert r.exit_code == EX_OK, r.output
    rows = r.output.splitlines()
    assert rows[0].startswith("   1 N beta         first")
    assert rows[1].startswith("   2 . beta         second")
    assert rows[2].startswith("   3 N beta         third")
    assert rows[-1] == "-- alpha: 3 shown, 0 hidden; index spans all 3"


def test_a_superseded_message_is_hidden_and_the_footer_says_why(box):
    old = deliver(box, "alpha", "1923171201", subject="v1")
    deliver(box, "alpha", "1923171202", subject="v2", supersedes=old)
    r = run()
    assert r.exit_code == EX_OK
    assert "v1" not in r.output and " v2" in r.output
    assert "1 shown, 1 hidden (1 superseded)" in r.output


def test_unread_hides_what_this_session_marked_read_not_the_maildir_flag(box, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    seen = deliver(box, "alpha", "1923171201", subject="already read", new=False)
    deliver(box, "alpha", "1923171202", subject="in cur but never marked", new=False)
    seat = _seat.resolve()
    _seat.write_mark(seat, seen, "read", "s1", "2030-06-12T16:00:00+10:00")
    r = run("--unread")
    assert r.exit_code == EX_OK
    assert "already read" not in r.output
    assert "in cur but never marked" in r.output
    assert "1 marked read (--unread)" in r.output


def test_unread_does_not_hide_what_ANOTHER_session_marked_read(box, monkeypatch):
    """Marks key on the session id, so `--unread` is what THIS session has not read. Before
    that they keyed on a seat invented from the box's name, and a
    predecessor's read hid a message from its successor for ever."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    seen = deliver(box, "alpha", "1923171201", subject="read by s1", new=False)
    seat = _seat.resolve()
    _seat.write_mark(seat, seen, "read", "s1", "2030-06-12T16:00:00+10:00")
    assert "read by s1" not in run("--unread").output

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2")
    r = run("--unread")
    assert r.exit_code == EX_OK
    assert "read by s1" in r.output
    assert "marked read (--unread)" not in r.output


def test_from_subject_to_and_n_filters_each_disclose(box):
    deliver(box, "alpha", "1923171201", subject="alpha note", sender="alpha@example.invalid")
    deliver(box, "alpha", "1923171202", subject="beta note", to="alpha+memory")
    deliver(box, "alpha", "1923171203", subject="beta ask")
    r = run("--from", "BETA")
    assert "alpha note" not in r.output and "2 shown, 1 hidden (1 --from)" in r.output
    r = run("--subject", r"ask$")
    assert "1 shown, 2 hidden (2 --subject)" in r.output
    r = run("--to", "alpha+memory")
    assert "beta note" in r.output and "1 shown, 2 hidden (2 --to)" in r.output
    r = run("--to", "")
    assert "beta note" not in r.output and "2 shown" in r.output
    r = run("-n", "1")
    assert "beta ask" in r.output and "1 shown, 2 hidden (2 older than -n 1)" in r.output


def test_a_bad_subject_regex_is_usage(box):
    deliver(box, "alpha", "1923171201")
    assert run("--subject", "(").exit_code == EX_USAGE


def test_eom_with_no_body_carries_the_mark_and_others_do_not(box):
    deliver(box, "alpha", "1923171201", subject="lunch is ready EOM", body="")
    deliver(box, "alpha", "1923171202", subject="a letter", body="dear alpha\n")
    r = run()
    rows = r.output.splitlines()
    assert rows[0].endswith("lunch is ready EOM  (no body)")
    assert rows[1].endswith("a letter")


def test_listing_renames_and_flags_nothing(box):
    deliver(box, "alpha", "1923171201", subject="stay put")
    before = sorted(p.name for p in (box / "alpha" / "new").iterdir())
    assert run().exit_code == EX_OK
    assert sorted(p.name for p in (box / "alpha" / "new").iterdir()) == before
    assert list((box / "alpha" / "cur").iterdir()) == []


def test_another_box_by_name_and_an_empty_box(box):
    deliver(box, "beta", "1923171201", subject="for beta")
    r = run("beta")
    assert r.exit_code == EX_OK and "for beta" in r.output
    (box / "gamma" / "new").mkdir(parents=True); (box / "gamma" / "cur").mkdir()
    r = run("gamma")
    assert r.exit_code == EX_OK and "-- gamma: 0 shown, 0 hidden; index spans all 0" in r.output


def test_no_such_box_is_exit_2_and_no_box_no_identity_is_usage(box, monkeypatch):
    r = run("nobody")
    assert r.exit_code == EX_NO_BOX and "no box 'nobody'" in r.output
    monkeypatch.delenv("TM_AGENT_ADDRESS")
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    r = run()
    assert r.exit_code == 2 and "name the box" in r.output  # click usage error exits 2


# ── the listing defaults to the newest 40 (specs/message) ────────────────

def test_a_box_of_8416_prints_the_newest_40_and_says_how_many_it_hid(box):
    """specs/message, "A listing defaults to the newest 40", its scenario
    "A box of 8,416".

    The scenario is pinned VERBATIM: 40 rows, numbered 8377..8416, and the
    last line says 8,376 hidden. The index is the box's own, so the newest
    row is 8416 and not 40.
    """
    bulk(box, "alpha", 8416)
    r = run()
    assert r.exit_code == EX_OK, r.output
    rows = r.output.splitlines()
    assert len(rows) == 41, f"40 rows and a footer, got {len(rows)} lines"
    assert rows[0].startswith("8377 ")
    assert rows[39].startswith("8416 ")
    assert "8,376 hidden" in rows[-1]
    assert rows[-1] == ("-- alpha: 40 shown, 8,376 hidden "
                        "(8,376 older than the newest 40); index spans all 8,416")


@pytest.mark.parametrize("arg", ["0", "all", "ALL"])
def test_n_0_and_n_all_show_every_row(box, arg):
    """`-n 0` or `-n all` SHALL show every row - so the default cut is a
    default, never a ceiling. Case is not the user's problem."""
    bulk(box, "alpha", 45)
    r = run("-n", arg)
    assert r.exit_code == EX_OK, r.output
    rows = r.output.splitlines()
    assert len(rows) == 46
    assert rows[0].startswith("   1 ") and rows[44].startswith("  45 ")
    assert rows[-1] == "-- alpha: 45 shown, 0 hidden; index spans all 45"


def test_a_cut_listing_and_a_full_one_number_a_row_the_same(box):
    """"The index it prints SHALL be the box's index ... so a row's number
    is the same whether or not the listing was cut." The number is what
    `read` takes; if the cut renumbered, a paste from a default listing
    would open the wrong message."""
    bulk(box, "alpha", 45)
    cut = {ln.split()[0]: ln for ln in run().output.splitlines()[:-1]}
    full = {ln.split()[0]: ln for ln in run("-n", "all").output.splitlines()[:-1]}
    assert len(cut) == 40 and len(full) == 45
    assert sorted(int(n) for n in cut) == list(range(6, 46)), "the NEWEST 40"
    for n, line in cut.items():
        assert full[n] == line, f"row {n} differs between a cut and a full listing"


def test_the_default_does_not_cut_a_box_smaller_than_40(box):
    """Nothing is hidden, and the footer says so - the disclosure has to be
    honest in both directions or it is noise."""
    bulk(box, "alpha", 39)
    rows = run().output.splitlines()
    assert len(rows) == 40
    assert rows[-1] == "-- alpha: 39 shown, 0 hidden; index spans all 39"


def test_a_bad_n_is_usage_and_names_what_it_takes(box):
    bulk(box, "alpha", 3)
    r = run("-n", "twelve")
    assert r.exit_code == EX_USAGE, r.output
    assert "a row count" in r.output
    # Both spellings click accepts for a negative short-option value reach
    # the guard; a negative used to mean "show everything", in silence.
    for neg in (("-n-3",), ("-n", "-3")):
        r = run(*neg)
        assert r.exit_code == EX_USAGE, r.output
        assert "not negative" in r.output, neg


def test_an_explicit_n_still_says_older_than_n(box):
    """The existing disclosure wording for a user-typed -n is unchanged: the
    default cut is not reported as an `-n` nobody typed."""
    bulk(box, "alpha", 5)
    assert "3 hidden (3 older than -n 2)" in run("-n", "2").output


# ── a bare listing is the doorbell's box ─────────────────────────────────
#
# specs/message, "A bare listing is the doorbell's box". `pager listen` with no BOX blocks on the
# SESSION's box; `message list` with no BOX now reads the same one, so the
# reader who was just paged gets the box they were paged about.

def test_a_bare_listing_reads_the_session_box_not_the_seat_box(box, monkeypatch):
    deliver(box, "alpha", "1923171201", subject="the seats mail")
    deliver(box, "sess-a", "1923171202", subject="the sessions mail")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    r = run()
    assert r.exit_code == EX_OK, r.output
    assert "the sessions mail" in r.output
    assert "the seats mail" not in r.output
    assert "-- sess-a:" in r.output          # and it says which box it read


def test_a_named_box_still_wins_over_both(box, monkeypatch):
    deliver(box, "alpha", "1923171201", subject="the seats mail")
    deliver(box, "sess-a", "1923171202", subject="the sessions mail")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    r = run("alpha")
    assert r.exit_code == EX_OK and "the seats mail" in r.output
    assert "the sessions mail" not in r.output


def test_with_no_session_id_it_falls_back_to_the_seat_box_silently(box):
    """No session to confuse it with, and the footer already names the box
    it read - so nothing extra is printed. (The `hermetic` fixture deletes
    the runner's own session id, so this is the real no-session case.)"""
    deliver(box, "alpha", "1923171201", subject="the seats mail")
    r = run()
    assert r.exit_code == EX_OK and "the seats mail" in r.output
    assert "-- alpha:" in r.output
    assert "instead of this session's" not in r.output


def test_a_session_whose_box_is_not_born_yet_says_it_fell_back(box, monkeypatch):
    """The SURPRISING case: the reader HAS a session and is being shown a
    different box. A silent fallback here would make the default untrue
    while looking correct."""
    deliver(box, "alpha", "1923171201", subject="the seats mail")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-unborn")
    r = run()
    assert r.exit_code == EX_OK and "the seats mail" in r.output
    assert "sess-unborn" in r.output and "alpha" in r.output
    assert "instead of this session's" in r.output


def test_read_takes_its_index_from_the_same_box_the_bare_listing_printed(box, monkeypatch):
    """`read` takes the index `list` prints. If the two disagreed about the
    default box, a pasted row number would read a DIFFERENT message - worse
    than either box alone. Pinned across both verbs, not just asserted."""
    from sessionmail.message import cli as message_cli
    deliver(box, "alpha", "1923171201", subject="the seats mail")
    deliver(box, "sess-a", "1923171202", subject="the sessions mail")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    listed = run()
    assert "   1 N beta         the sessions mail" in listed.output, listed.output
    got = CliRunner().invoke(message_cli, ["read", "1", "--peek"])
    assert got.exit_code == EX_OK, got.output
    assert "the sessions mail" in got.output
    assert "the seats mail" not in got.output
