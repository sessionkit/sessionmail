"""SM-1053: `message list` and `read N` parse only what they print.

Measured on 96f6151: `message list BOX -n 1` ran the full `_maildir.parse`
on every file in the box (41 s on 46,867 files). Now a row the listing does
not print costs its header block and two ids (`_maildir.row_ids`); `parse`
runs for a printed row, and for a row a given filter reads.

Pinned here, in COUNTS, never seconds:
  - `list -n 1` parses one file, `list --unread -n 5` five, `read N --peek`
    and `read ID --peek` one (RED on 96f6151, where each parsed all N);
  - the contract did not move: the printed rows and the footer, every count
    in it, are byte-identical to the full-parse path (a frozen copy of the
    96f6151 loop, `reference_list`), on a box with superseded mail, drafts,
    unreadable headers, odd line ends and every filter;
  - `row_ids` reads exactly the `message_id` and `supersedes` `parse` reads,
    over line ends, folding, no blank line, huge headers and a header block
    that ends across a read boundary;
  - `find_row` finds what `find` over every parsed row finds.

Hermetic, as tests/test_message_list.py: a maildir under `tmp_path`,
`SESSIONMAIL_ROOT` pointed at it. Synthetic dates are the two Sundays.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from sessionmail import _maildir, _seat
from sessionmail.message import DEFAULT_ROWS, cli

SESSION = "s-1053"
DATE = "Sun, 09 Jun 2030 10:00:00 +0000"
LATER = "Sun, 08 Dec 2030 10:00:00 +0000"


@pytest.fixture
def box(tmp_path, monkeypatch):
    root = tmp_path / "mail" / "stores"
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root.parent))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    for sub in ("new", "cur", "tmp"):
        (root / "alpha" / sub).mkdir(parents=True)
    return root / "alpha"


@pytest.fixture
def parses(monkeypatch):
    """The `_maildir.parse` calls made while the test runs, counted."""
    calls: list[str] = []
    real = _maildir.parse

    def counted(path, index, unread):
        calls.append(path.name)
        return real(path, index, unread)

    monkeypatch.setattr(_maildir, "parse", counted)
    return calls


def put(d, n: int, raw: bytes, *, new: bool = True, flags: str = "S") -> str:
    """RAW as the box's file number N (the key sorts by N)."""
    name = f"{1907000000 + n:010d}.M{n}P1.host"
    if new:
        (d / "new" / name).write_bytes(raw)
    else:
        (d / "cur" / f"{name}:2,{flags}").write_bytes(raw)
    return name


def plain(n: int, *, sender="beta@example.invalid", subject=None, mid=None,
          to="alpha", extra=(), body="a body", eol="\r\n") -> bytes:
    lines = [f"X-Original-To: {to}", f"From: {sender}", "To: alpha@example.invalid",
             f"Date: {DATE if n % 2 else LATER}",
             f"Subject: {subject if subject is not None else f'note {n}'}",
             f"Message-ID: <{mid or f'{n}@example.invalid'}>", *extra]
    return (eol.join(lines) + eol + eol + body + eol).encode()


def bulk(d, n: int) -> None:
    for i in range(1, n + 1):
        put(d, i, plain(i), new=bool(i % 3))


def run(*args):
    return CliRunner().invoke(cli, list(args))


# ── counts: what a verb parses ──────────────────────────────────────────


def test_list_n_1_parses_the_one_row_it_prints(box, parses):
    bulk(box, 60)
    r = run("list", "alpha", "-n", "1")
    assert r.exit_code == 0, r.output
    assert r.output.splitlines()[-1] == (
        "-- alpha: 1 shown, 59 hidden (59 older than -n 1); index spans all 60")
    assert len(parses) == 1, parses


def test_list_default_parses_only_the_newest_forty(box, parses):
    bulk(box, 60)
    r = run("list", "alpha")
    assert r.exit_code == 0, r.output
    assert len(parses) == DEFAULT_ROWS


def test_list_unread_n_5_parses_five(box, parses):
    bulk(box, 60)
    _seat.write_mark(_seat.resolve(), "60@example.invalid", "read", SESSION,
                     "2030-06-09T10:00:00+10:00")
    r = run("list", "alpha", "--unread", "-n", "5")
    assert r.exit_code == 0, r.output
    assert "1 marked read (--unread)" in r.output
    assert len(parses) == 5


def test_read_n_peek_parses_one(box, parses):
    bulk(box, 60)
    r = run("read", "alpha", "7", "--peek")
    assert r.exit_code == 0, r.output
    assert r.output.startswith("#7  note 7\n")
    assert len(parses) == 1


def test_read_id_peek_parses_one(box, parses):
    bulk(box, 60)
    r = run("read", "alpha", "<41@example.invalid>", "--peek")
    assert r.exit_code == 0, r.output
    assert r.output.startswith("#41  note 41\n")
    assert len(parses) == 1


def test_read_a_missing_number_names_the_span_and_parses_nothing(box, parses):
    bulk(box, 60)
    r = run("read", "alpha", "61", "--peek")
    assert r.exit_code != 0
    assert "(index spans 1..60)" in r.output
    assert parses == []


# ── the contract: byte-identical to the full-parse path ─────────────────


def reference_list(box: str, *, unread=False, from_=None, subject=None, to=None,
                   count=None) -> str:
    """`message list`'s rows and footer computed the 96f6151 way: every row
    parsed (`enumerate_with_drafts`), then the loop `list_` ran at that
    commit, copied here verbatim but for the echo. The oracle the lazy path
    must equal, byte for byte."""
    entries, drafts = _maildir.enumerate_with_drafts(box)
    pattern = re.compile(subject) if subject else None
    rows = (DEFAULT_ROWS if count is None else
            None if count in ("0", "all") else int(count))
    marks = _seat.read_marks(_seat.resolve(), _seat.session_id()) if unread else {}
    superseded = {e.supersedes for e in entries if e.supersedes}
    hidden: dict[str, int] = {}

    def hide(why: str) -> None:
        hidden[why] = hidden.get(why, 0) + 1

    shown = []
    for e in entries:
        if e.message_id and e.message_id in superseded:
            hide("superseded"); continue
        own = marks.get(e.message_id, set())
        if unread and not _maildir.is_unread(own):
            hide("marked read (--unread)" if "read" in own
                 else "marked done (--unread)"); continue
        if from_ and from_.lower() not in e.sender.lower():
            hide("--from"); continue
        if pattern and not pattern.search(e.shown_subject):
            hide("--subject"); continue
        if to is not None and e.delivered_to != (to if to != "" else box):
            hide("--to"); continue
        shown.append(e)
    if rows is not None and len(shown) > rows:
        why = (f"older than -n {count}" if count is not None
               else f"older than the newest {DEFAULT_ROWS}")
        for _ in shown[: len(shown) - rows]:
            hide(why)
        shown = shown[len(shown) - rows:]
    if drafts:
        hide_n = len(drafts)
        hidden["draft" if hide_n == 1 else "drafts"] = hide_n
    out = []
    for e in shown:
        flag = "N" if e.unread else "."
        tail = "" if e.has_body else "  (no body)"
        out.append(f"{e.index:>4} {flag} {e.sender_local[:12]:<12} {e.shown_subject}{tail}"
                   f"{e.unreadable_mark}")
    total_hidden = sum(hidden.values())
    why = ", ".join(f"{n:,} {reason}" for reason, n in hidden.items())
    out.append(f"-- {box}: {len(shown):,} shown, {total_hidden:,} hidden"
               + (f" ({why})" if why else "") + f"; index spans all {len(entries):,}")
    return "\n".join(out) + "\n"


def awkward_box(d) -> None:
    """Every kind of row a count depends on, in one box."""
    n = 0

    def add(raw, **kw):
        nonlocal n
        n += 1
        return put(d, n, raw, **kw)

    for i in range(1, 9):                                     # plain, new and cur
        add(plain(i), new=bool(i % 2))
    add(plain(9, mid="old@example.invalid", subject="v1"))
    add(plain(10, subject="v2", extra=["Supersedes: <old@example.invalid>"]))
    add(plain(11, subject="v3 folded supersedes",
              extra=["Supersedes:", " <10@example.invalid>"]))  # folded: supersedes v2
    add(plain(12, subject="draft-ish", extra=[]), new=False, flags="D")    # a draft
    add(plain(13, subject="another draft"), new=False, flags="DS")         # a draft
    add(b"From: gamma@example.invalid\r\nSubject: unreadable id\r\n"
        b"Message-ID: <x@[ broken.example.invalid]>\r\n\r\nbody\r\n")      # unreadable
    add(b"From: gamma@example.invalid\r\nSubject: supersedes the unreadable\r\n"
        b"Message-ID: <s@example.invalid>\r\n"
        b"Supersedes: <x@[ broken.example.invalid]>\r\n\r\nbody\r\n")
    add(b"From: gamma@example.invalid\r\nSubject: unreadable, kept\r\n"
        b"Message-ID: <y@[ kept.example.invalid]>\r\n\r\nbody\r\n")    # shown, marked
    add(b"From: delta@example.invalid\nSubject: LF only\n"
        b"Message-ID: <lf@example.invalid>\n\nbody\n")
    add(b"From: delta@example.invalid\rSubject: CR only\r"
        b"Message-ID: <cr@example.invalid>\r\rbody\r")
    add(b"From: delta@example.invalid\r\nSubject: no blank line\r\n"
        b"Message-ID: <nb@example.invalid>\r\n")                          # all header
    add(b"From: delta@example.invalid\r\nSubject: eom\r\n"
        b"Message-ID: <eom@example.invalid>\r\n\r\n")                     # no body
    add(b"From: delta@example.invalid\r\nSubject: not a header next\r\n"
        b"Message-ID: <nh@example.invalid>\r\nthis line is no header\r\n"
        b"Supersedes: <1@example.invalid>\r\n\r\nbody\r\n")   # body, not a Supersedes
    add(plain(22, subject="=?utf-8?q?caf=C3=A9?= encoded", to="alpha+tag"))
    add(plain(23, subject="outside", extra=["Outside: github", "Type: issue",
                                           "Number: 7", "Repo: example/fixture"]))
    add(b"From: eps@example.invalid\r\nSubject: no id at all\r\n\r\nbody\r\n")
    add(b"From: eps@example.invalid\r\nSubject: no id either\r\n\r\nbody\r\n")
    add(plain(26, mid="dup@example.invalid", subject="dup one"))
    add(plain(27, mid="dup@example.invalid", subject="dup two"))
    add(plain(28, subject="huge header",
              extra=[f"X-Pad-{k}: " + "p" * 900 for k in range(40)]))  # past one read
    add(plain(29, subject="body says Supersedes",
              body="Supersedes: <2@example.invalid>\r\nMessage-ID: <zz@x>"))
    add(plain(30, sender="Zeta Z <zeta@example.invalid>", subject="read me"))
    add(plain(31, sender="Zeta Z <zeta@example.invalid>", subject="done me"))


CASES = [
    {}, {"count": "1"}, {"count": "5"}, {"count": "all"}, {"count": "0"},
    {"count": "3"}, {"unread": True}, {"unread": True, "count": "2"},
    {"from_": "zeta"}, {"from_": "GAMMA", "count": "1"}, {"subject": "^v"},
    {"subject": "no id|outside", "count": "1"}, {"to": "alpha+tag"},
    {"to": ""}, {"to": "nobody"}, {"unread": True, "from_": "zeta"},
    {"unread": True, "from_": "e", "subject": "o", "to": "", "count": "2"},
]


def argv(case: dict) -> list[str]:
    out = ["list", "alpha"]
    if case.get("unread"):
        out.append("--unread")
    for key, flag in (("from_", "--from"), ("subject", "--subject"),
                      ("to", "--to"), ("count", "-n")):
        if key in case:
            out += [flag, case[key]]
    return out


@pytest.mark.parametrize("case", CASES, ids=lambda c: " ".join(argv(c)[2:]) or "bare")
def test_the_listing_is_byte_identical_to_the_full_parse_path(box, case):
    awkward_box(box)
    seat = _seat.resolve()
    _seat.write_mark(seat, "30@example.invalid", "read", SESSION, "2030-06-09T10:00:00+10:00")
    _seat.write_mark(seat, "31@example.invalid", "done", SESSION, "2030-06-09T10:00:00+10:00")
    _seat.write_mark(seat, "dup@example.invalid", "read", SESSION, "2030-06-09T10:00:00+10:00")
    want = reference_list("alpha", **case)
    r = run(*argv(case))
    assert r.exit_code == 0, r.output
    assert r.output == want


def test_the_awkward_box_exercises_every_footer_reason(box):
    """The equivalence above is only as good as its fixture: prove it hides
    for each reason, and marks an unreadable row."""
    awkward_box(box)
    _seat.write_mark(_seat.resolve(), "30@example.invalid", "read", SESSION,
                     "2030-06-09T10:00:00+10:00")
    _seat.write_mark(_seat.resolve(), "31@example.invalid", "done", SESSION,
                     "2030-06-09T10:00:00+10:00")
    full = run("list", "alpha", "-n", "all").output
    assert "<unreadable header: Message-ID>" in full
    assert "(no body)" in full and "[from github] issue #7" in full
    footer = full.splitlines()[-1]
    assert re.search(r"\(3 superseded, 2 drafts\); index spans all 29$", footer), footer
    assert "marked read (--unread), 1 marked done (--unread)" in run(
        "list", "alpha", "--unread").output
    for flag, value in (("--from", "zeta"), ("--subject", "^v"), ("--to", "")):
        footer = run("list", "alpha", flag, value, "-n", "all").output.splitlines()[-1]
        assert re.search(rf"[( ]\d+ {flag}[,)]", footer), footer


# ── row_ids reads what parse reads ──────────────────────────────────────


HEADERS = [
    b"Message-ID: <a@example.invalid>\r\nSupersedes: <b@example.invalid>\r\n\r\nbody\r\n",
    b"Message-ID: <a@example.invalid>\nSupersedes: <b@example.invalid>\n\nbody\n",
    b"Message-ID: <a@example.invalid>\rSupersedes: <b@example.invalid>\r\rbody\r",
    b"Message-ID: <a@example.invalid>\r\nSupersedes: <b@example.invalid>\n\r\nbody",
    b"Message-ID: <a@example.invalid>\r\r\nSupersedes: <b@x>\r\n\r\nbody",
    b"Message-ID:\r\n <folded@example.invalid>\r\nSupersedes:\r\n\t<f2@x>\r\n\r\n",
    b"Message-ID: <a@example.invalid>\r\nSupersedes: <b@example.invalid>\r\n",
    b"Message-ID: <a@example.invalid>\r\nSupersedes: <b@example.invalid>",
    b"Message-ID: <a@example.invalid>\r\nno header here\r\nSupersedes: <b@x>\r\n\r\n",
    b"Message-ID: <x@[ broken.example.invalid]>\r\nSupersedes: <x@[ also]>\r\n\r\nb",
    b"Message-ID: =?utf-8?q?caf=C3=A9?=\r\nSupersedes: plain-no-brackets\r\n\r\n",
    b"Subject: none of either\r\n\r\nMessage-ID: <in-body@x>\r\n",
    b"Message-ID: <first@x>\r\nMessage-ID: <second@x>\r\n\r\n",
    b"From x@y Sun Jun  9 10:00:00 2030\r\nMessage-ID: <mbox@x>\r\n\r\n",
    b"Message-ID: <caf\xc3\xa9@x>\r\nSupersedes: <\xff\xfe@x>\r\n\r\n",
    b"",
    b"\r\n\r\nMessage-ID: <after-a-blank@x>\r\n",
    b"X-Pad: " + b"p" * 20000 + b"\r\nMessage-ID: <after-huge@x>\r\n\r\nbody",
    b"Message-ID: <a@x>\r\n" + b"".join(b"X-%d: v\r\n" % k for k in range(3000))
    + b"Supersedes: <late@x>\r\n\r\n" + b"body\r\n" * 5000,
]


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 8, 64, 8192])
def test_row_ids_reads_what_parse_reads(tmp_path, monkeypatch, chunk):
    """At every read size, so a header block that ends ACROSS a read (an
    `\\r` in one read and its `\\n` in the next) is among the cases."""
    monkeypatch.setattr(_maildir, "_HEAD_CHUNK", chunk, raising=False)
    for k, raw in enumerate(HEADERS):
        p = tmp_path / f"m{k}"
        p.write_bytes(raw)
        e = _maildir.parse(p, 1, True)
        assert _maildir.row_ids(p) == (e.message_id, e.supersedes), (k, raw[:80])


def test_header_block_stops_before_the_body(tmp_path):
    p = tmp_path / "m"
    p.write_bytes(b"Message-ID: <a@x>\r\n\r\n" + b"body\r\n" * 100_000)
    assert _maildir.header_block(p) == b"Message-ID: <a@x>\r\n\r\n"


def test_find_row_finds_what_find_finds(box):
    awkward_box(box)
    entries, _drafts = _maildir.enumerate_with_drafts("alpha")
    rows, _ = _maildir.scan("alpha")
    known: dict[int, str] = {}
    asks = [str(i) for i in range(0, len(entries) + 3)]
    asks += [e.message_id for e in entries] + [f"<{e.message_id}>" for e in entries]
    asks += ["", "<>", "nobody@example.invalid", "x@[ broken.example.invalid]"]
    for which in asks:
        assert _maildir.find_row(rows, which, known) == _maildir.find(entries, which), which
