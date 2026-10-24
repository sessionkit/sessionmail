"""Grouping and counting by `X-Original-To` - the page, and the desk line.

specs/pager, "The pager is a nudge, and the re-arm carries", amended: a page
counts by ENVELOPE RECIPIENT.

Pinned here:
  - the key is `X-Original-To`, and NEITHER `To:` NOR `Delivered-To:` - each
    proved separately, by a message whose headers disagree, so that a test
    fails if either wrong header is ever reached for;
  - three counts per address - unseen, unread, total - which are three
    different readings and not two dressed up as three;
  - the row range is the BOX's index, the number `message read` takes;
  - a count carries a thousands separator, a row number does not;
  - one reader host-qualified many ways is ONE group; `+extension` is not;
  - a burst from one address coalesces to a single desk line, and every
    message in it is still given;
  - the page still names no sender and no subject.

Hermetic: own maildir under `tmp_path`, `SESSION_MAIL_ROOT` pointed at it.
"""

from __future__ import annotations

import pytest

from sessionmail import _maildir, pager


def write(root, box, key, *, to=None, header_to=None, delivered_to=None,
          sender="beta@example.invalid", subject="hello", mid=None, new=True):
    """One message. `to` is X-Original-To; `header_to` is the To: header;
    `delivered_to` is the Delivered-To: header. They are separate arguments
    on purpose - the whole point of these tests is to make them disagree."""
    d = root / box
    for sub in ("new", "cur", "tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    mid = mid or f"{key}@example.invalid"
    lines = []
    if to is not None:
        lines.append(f"X-Original-To: {to}")
    if delivered_to is not None:
        lines.append(f"Delivered-To: {delivered_to}")
    lines += [f"From: {sender}",
              f"To: {header_to if header_to is not None else box}@example.invalid",
              "Date: Sun, 09 Jun 2030 22:34:56 +1000",
              f"Subject: {subject}",
              f"Message-ID: <{mid}>"]
    raw = "\r\n".join(lines) + "\r\n\r\n" + "a body\n"
    name = f"{key}.M1P1.host" + ("" if new else ":2,S")
    (d / ("new" if new else "cur") / name).write_text(raw)
    return mid


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(r))
    return r


def groups(root, box, marks=None):
    return _maildir.group_by_original_to(_maildir.enumerate_box(box), marks)


# ── the key is X-Original-To, and nothing else ───────────────────────────

def test_the_to_header_is_not_the_grouping_key(root):
    """`To:` names the NOMINAL recipient, so a Cc/Bcc copy names somebody
    else. If `To:` were reached for, these two would be one group called
    `someone-else`; they are two, named by their X-Original-To."""
    write(root, "sess", "1923171201", to="ivan", header_to="someone-else")
    write(root, "sess", "1923171202", to="ivan+kaizen", header_to="someone-else")
    got = {g.address for g in groups(root, "sess")}
    assert got == {"ivan", "ivan+kaizen"}, got
    assert "someone-else" not in got


def test_the_delivered_to_header_is_not_the_grouping_key(root):
    """`Delivered-To:` is always the box being read, so it has ONE value for
    every message in it and cannot distinguish anything. If it were reached
    for, these three would collapse into a single group called `sess`."""
    write(root, "sess", "1923171201", to="ivan", delivered_to="sess")
    write(root, "sess", "1923171202", to="ivan+kaizen", delivered_to="sess")
    write(root, "sess", "1923171203", to="judy", delivered_to="sess")
    got = {g.address for g in groups(root, "sess")}
    assert got == {"ivan", "ivan+kaizen", "judy"}, got
    assert "sess" not in got, "grouped by Delivered-To: - the one header that cannot tell them apart"


def test_all_three_headers_disagree_and_x_original_to_wins(root):
    """The criterion's own construction: one message, three disagreeing
    headers, one right answer."""
    write(root, "sess", "1923171201",
          to="ivan+kaizen", header_to="someone-else", delivered_to="sess")
    got = groups(root, "sess")
    assert [g.address for g in got] == ["ivan+kaizen"]
    assert got[0].spellings == ("ivan+kaizen",)


def test_a_message_with_no_x_original_to_is_grouped_not_dropped(root):
    write(root, "sess", "1923171201", to=None)
    write(root, "sess", "1923171202", to="ivan")
    got = {g.address: g.total for g in groups(root, "sess")}
    assert got == {"": 1, "ivan": 1}


# ── one reader is one group ──────────────────────────────────────────────

def test_one_reader_host_qualified_many_ways_is_one_group(root):
    """Measured on one real box: 8,669 messages, five spellings, three
    readers. Grouping verbatim reported one reader three times."""
    write(root, "sess", "1923171201", to="judy")
    write(root, "sess", "1923171202", to="judy@hq")
    write(root, "sess", "1923171203", to="judy@hq.example.ts.net")
    write(root, "sess", "1923171204", to="judy+taskmaster")
    got = {g.address: g.total for g in groups(root, "sess")}
    assert got == {"judy": 3, "judy+taskmaster": 1}, got


def test_the_verbatim_spellings_are_kept_beside_the_group(root):
    """Folding is not discarding: what was folded stays readable, so a later
    slice (the deaf-agent fix host-qualifies this same header) can still see
    the host it needs."""
    write(root, "sess", "1923171201", to="judy")
    write(root, "sess", "1923171202", to="judy@hq")
    g = groups(root, "sess")[0]
    assert g.spellings == ("judy", "judy@hq")


def test_the_key_is_case_insensitive_and_survives_angle_brackets(root):
    write(root, "sess", "1923171201", to="Ivan")
    write(root, "sess", "1923171202", to="<ivan@hq>")
    assert [g.address for g in groups(root, "sess")] == ["ivan"]


# ── three counts, not two ────────────────────────────────────────────────

def test_unseen_unread_and_total_are_three_different_numbers(root):
    """A message can be SEEN and UNREAD - paged, never opened - which is
    exactly the state a page exists to report. Collapsing the two would
    lose the reason for the page, so the test pins a case where all three
    numbers differ."""
    a = write(root, "sess", "1923171201", to="ivan")
    b = write(root, "sess", "1923171202", to="ivan")
    write(root, "sess", "1923171203", to="ivan")
    marks = {a: {"seen", "read"}, b: {"seen"}}
    g = groups(root, "sess", marks)[0]
    assert (g.unseen, g.unread, g.total) == (1, 2, 3), (g.unseen, g.unread, g.total)


def test_without_marks_everything_is_unseen_and_unread(root):
    write(root, "sess", "1923171201", to="ivan")
    g = groups(root, "sess")[0]
    assert (g.unseen, g.unread, g.total) == (1, 1, 1)


# ── the row range, and how numbers are formatted ─────────────────────────

def test_the_row_range_is_the_boxs_own_index(root):
    for i in range(1, 6):
        write(root, "sess", f"170000000{i}", to="ivan" if i in (2, 4) else "judy")
    by = {g.address: g for g in groups(root, "sess")}
    assert by["ivan"].rows == "2..4"
    assert by["judy"].rows == "1..5"


def test_a_single_row_is_not_printed_as_a_range(root):
    write(root, "sess", "1923171201", to="ivan")
    assert groups(root, "sess")[0].rows == "1"


def test_counts_carry_a_separator_and_row_numbers_do_not(root):
    """The already-ratified convention, not re-decided here: `8,376` hidden
    but rows `8377..8416`, because a row number is pasted into `message
    read` and a comma would not survive the paste."""
    g = _maildir.Group(address="ivan", spellings=("ivan",),
                       unseen=1234, unread=2345, total=8376,
                       first=8377, last=8416)
    line = pager.group_line(g)
    assert "1,234 unseen" in line and "2,345 unread" in line and "8,376 total" in line
    assert "rows 8377..8416" in line
    assert "8,377" not in line and "8,416" not in line


def test_the_group_line_names_no_sender_and_no_subject(root):
    """The page's standing prohibition is on the SENDER and the SUBJECT.
    Grouping by who it was addressed TO does not touch it."""
    write(root, "sess", "1923171201", to="ivan",
          sender="judy@example.invalid", subject="a very distinctive subject")
    line = pager.group_line(groups(root, "sess")[0])
    assert "judy" not in line and "distinctive" not in line, line


def test_a_group_with_no_index_prints_no_row_range(root):
    g = _maildir.Group(address="ivan", spellings=("ivan",), unseen=1, unread=1,
                       total=1, first=0, last=0)
    assert g.rows == ""
    assert "rows" not in pager.group_line(g)


def test_a_group_with_no_address_says_so_rather_than_printing_nothing(root):
    g = _maildir.Group(address="", spellings=("",), unseen=1, unread=1,
                       total=1, first=1, last=1)
    assert "(no X-Original-To)" in pager.group_line(g)


# ── order ────────────────────────────────────────────────────────────────

def test_the_oldest_group_comes_first(root):
    write(root, "sess", "1923171201", to="zulu")
    write(root, "sess", "1923171202", to="alpha")
    assert [g.address for g in groups(root, "sess")] == ["zulu", "alpha"]


# ── the page carries the groups ──────────────────────────────────────────

def test_the_quiet_page_carries_a_line_per_address_under_the_count(root):
    gs = [_maildir.Group("ivan", ("ivan",), 1, 1, 3, 1, 3),
          _maildir.Group("ivan+kaizen", ("ivan+kaizen",), 2, 2, 2, 4, 5)]
    page = pager.quiet_page(5, None, "sess", groups=gs)
    assert page[0] == pager.PAGE_OFF                  # item 1 still first
    assert page[1] == "pager: 5 messages waiting."    # item 2
    assert "ivan -" in page[2] and "ivan+kaizen -" in page[3]
    assert page[-1] == pager.PAGE_COMMANDS            # item 4 still last


def test_the_page_without_groups_is_byte_identical_to_before(root):
    """Every caller written before the grouping ruling keeps working: the
    groups are optional and add nothing when absent."""
    assert pager.quiet_page(2, None, "sess") == pager.quiet_page(2, None, "sess", groups=[])


# ── the burst threshold ──────────────────────────────────────────────────

def test_the_threshold_ignores_a_typo_rather_than_obeying_it(monkeypatch):
    """A misspelling must not silently turn grouping off - the same
    reasoning `page_list_on` uses for its own typos."""
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "banana")
    assert pager.group_after() == pager.GROUP_AFTER
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "0")
    assert pager.group_after() == pager.GROUP_AFTER
    monkeypatch.setenv(pager.GROUP_AFTER_VAR, "7")
    assert pager.group_after() == 7
