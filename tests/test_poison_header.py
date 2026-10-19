"""SM-805: a header the stdlib parser cannot read is DATA, never a crash.

Any sender can write `Message-ID: <x@[ ` (hex 3c78405b20). Under
`email.policy.default`, CPython (3.13.16 and the macOS system 3.9.6 measured) raises
UnboundLocalError when that header is READ: `msg.get`, `msg[...]`,
`items()`, `values()`, `get_all()`. On 3.13 `In-Reply-To` and `References`
reach the same frame. Before the fix one such mail made the pager DEAF (the
hand listener and the monitor died with exit 1, and every re-arm died
again) and made the whole box unreadable (`message list`, `read`, `tree`,
`mark`, `verify N`, `pager status`). Measured end to end in the task's
findings/repro-001.md.

The fix is ONE accessor, `_headers.safe_header`, used at every site: it
names the parser's fault, reads the header as `<unreadable header>`
(keeping the sender's raw text for an id to key on), and the mail is still
listed, read and paged, MARKED. Each test below goes through the surface a
seat uses, with the poison bytes, and failed on the base (ff3ca96).

Hermetic: test_pager_listen.py's rig (a maildir root and a seat home under
tmp_path), deliveries the way an MDA makes them. Nothing here touches a
live box or a live pager.
"""

from __future__ import annotations

import os
import re
import time
from email.parser import BytesHeaderParser, BytesParser
from email.policy import compat32, default as default_policy
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from sessionmail import _detect, _headers, _maildir, _say, _seat, _views, pager, rules
from sessionmail import message as message_mod
from test_pager_listen import EX_PAGE, counted, rig, run  # noqa: F401
from test_pager_monitor import DEADLINE, driven, until

#: The poison, byte for byte as the repro wrote it: the value `<x@[ `, and
#: the line ends in that space (`3c 78 40 5b 20`).
POISON_ID = b"<x@[ "
KEYED_AS = "x@["          # what the mail is keyed on: the raw text, bare

_n = 0


def _wire(*, mid: bytes = POISON_ID, subject: str = "poison", extra: bytes = b"",
          to: str = "sess-a@example.invalid") -> bytes:
    return (b"From: alpha@example.invalid\n"
            + f"To: {to}\nSubject: {subject}\n".encode()
            + b"Date: Tue, 01 Jan 2030 12:00:00 +0000\n"
            + b"Message-ID: " + mid + b"\n" + extra
            + b"\npoison body\n")


def deliver_raw(box: Path, raw: bytes) -> Path:
    """The MDA's move, for bytes no f-string should touch."""
    global _n
    _n += 1
    name = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.poison"
    (box / "tmp" / name).write_bytes(raw)
    os.rename(box / "tmp" / name, box / "new" / name)
    return box / "new" / name


def _stdlib_raises(name: str, value: bytes) -> bool:
    """Does THIS interpreter's parser fault on NAME: VALUE? (In-Reply-To and
    References are structured on 3.13, unstructured on 3.9.)"""
    m = BytesHeaderParser(policy=default_policy).parsebytes(name.encode() + b": " + value + b"\n\n")
    try:
        m.get(name)
    except Exception:
        return True
    return False


def test_the_poison_still_poisons_the_stdlib():
    """The premise, pinned: if this ever goes green upstream, the accessor's
    catch is dead weight and this test says so."""
    assert _stdlib_raises("Message-ID", POISON_ID)


# ── the accessor itself ──────────────────────────────────────────────────

def test_safe_header_reads_a_poison_header_as_unreadable_with_the_raw_kept():
    msg = BytesHeaderParser(policy=default_policy).parsebytes(_wire())
    value = _headers.safe_header(msg, "Message-ID")
    assert isinstance(value, _headers.Unreadable)
    assert value == _headers.UNREADABLE == "<unreadable header>"
    assert value.name == "Message-ID"
    assert value.raw.strip() == "<x@["
    assert value.fault == "UnboundLocalError"
    assert _headers.as_written(value) == value.raw
    # only the field asked for is built: the rest read as they always did
    assert _headers.safe_header(msg, "Subject") == "poison"
    assert _headers.safe_header(msg, "No-Such", "dflt") == "dflt"


@pytest.mark.parametrize("name", ["Message-ID", "In-Reply-To", "References"])
def test_safe_header_marks_exactly_what_the_stdlib_cannot_read(name):
    """Three headers on 3.13, not one. Where this interpreter's parser reads
    the value (3.9's unstructured In-Reply-To), the accessor hands back what
    `get` hands back, unmarked."""
    raw = name.encode() + b": " + POISON_ID + b"\n\n"
    msg = BytesHeaderParser(policy=default_policy).parsebytes(raw)
    value = _headers.safe_header(msg, name)
    if _stdlib_raises(name, POISON_ID):
        assert isinstance(value, _headers.Unreadable), value
    else:
        assert not isinstance(value, _headers.Unreadable)
        assert value == msg.get(name)


def test_safe_header_is_msg_get_on_a_good_message_under_either_policy():
    raw = _wire(mid=b"<good1@example.invalid>", extra=b"References: <a@b> <c@d>\n")
    for policy in (default_policy, compat32):
        msg = BytesHeaderParser(policy=policy).parsebytes(raw)
        for name in ("Message-ID", "References", "From", "Subject", "Date", "Nope"):
            assert _headers.safe_header(msg, name) == msg.get(name), (policy, name)


def test_safe_header_does_not_catch_a_fault_outside_the_named_set():
    """Narrow on purpose: a fault that is not the parser's is a bug of ours
    and must still be seen."""
    class Boom(type(default_policy)):
        def header_fetch_parse(self, name, value):
            raise ValueError("ours")

    msg = BytesHeaderParser(policy=Boom()).parsebytes(b"Subject: x\n\n")
    with pytest.raises(ValueError):
        _headers.safe_header(msg, "Subject")


def test_read_header_is_total_over_a_poison_header_elsewhere():
    """`read_header` walked `msg.items()`, which builds EVERY header: asking
    for `Priority` raised on a poison Message-ID. It now builds only the
    fields asked for, through the accessor."""
    msg = BytesParser(policy=default_policy).parsebytes(
        _wire(extra=b"Ears-Kind: spoken\nPriority: high\n"))
    assert _headers.read_header(msg, "Priority").value == "high"
    kind = _headers.read_header(msg, "Ears-Kind")
    assert (kind.value, kind.spelling, kind.prefixed) == ("spoken", "Ears-Kind", False)
    assert _headers.read_header(msg, "Alert-Id").present is False


def test_say_turn_is_total():
    msg = BytesParser(policy=default_policy).parsebytes(_wire())
    assert _say.turn(msg) is None          # not voice: no turn, and no raise


# ── the parse sites ──────────────────────────────────────────────────────

def test_detect_hands_back_every_arrival_poison_included(rig):
    box = rig.box()
    poison = deliver_raw(box, _wire())
    rig.deliver("sess-a", "good1@example.invalid", "good one")
    assert _detect._message_id(poison) == KEYED_AS
    got = _detect.detect(box, poll=0.05, timeout=1)
    assert sorted(a.message_id for a in got) == sorted([KEYED_AS, "good1@example.invalid"])


def test_maildir_parse_keeps_the_poison_mail_and_marks_it(rig):
    box = rig.box()
    e = _maildir.parse(deliver_raw(box, _wire()), 1, True)
    assert e.message_id == KEYED_AS
    assert e.subject == "poison" and e.sender == "alpha@example.invalid"
    assert e.date is not None
    assert e.unreadable == ("Message-ID",)
    assert e.unreadable_mark == "  <unreadable header: Message-ID>"


def test_a_good_message_id_with_a_poison_in_reply_to(rig):
    if not _stdlib_raises("In-Reply-To", POISON_ID):
        pytest.skip("this interpreter reads In-Reply-To unstructured (3.9)")
    box = rig.box()
    e = _maildir.parse(deliver_raw(box, _wire(mid=b"<irt1@example.invalid>",
                                              extra=b"In-Reply-To: <x@[ \n")), 1, True)
    assert e.message_id == "irt1@example.invalid"
    assert e.in_reply_to == KEYED_AS
    assert e.unreadable == ("In-Reply-To",)


def test_a_clean_mail_is_unmarked(rig):
    rig.deliver("sess-a", "good1@example.invalid", "good one")
    (e,) = _maildir.enumerate_box("sess-a")
    assert e.unreadable == () and e.unreadable_mark == ""


def test_the_view_is_built_for_a_poison_mail(rig):
    """pager.py `_entry_view` turned the view's raise into None: the body was
    dropped from the ride-along line."""
    path = deliver_raw(rig.box(), _wire())
    entry = SimpleNamespace(path=path, name=path.name)
    v = pager._entry_view(entry, object(), KEYED_AS, lambda: ("sess-a", ()))
    assert v is not None and v.body == ("poison body",)


# ── message: list, read, tree, mark, verify ──────────────────────────────

def message(*argv: str):
    return CliRunner().invoke(message_mod.cli, list(argv), prog_name="message")


def _no_crash(result) -> None:
    exc = result.exception
    assert exc is None or isinstance(exc, SystemExit), repr(exc)


@pytest.fixture
def poisoned(rig):
    """Row 1 the poison, row 2 a good mail."""
    box = rig.box()
    deliver_raw(box, _wire())
    time.sleep(1.01)       # a later maildir name: the good one is row 2
    rig.deliver("sess-a", "good1@example.invalid", "good one")
    return rig


def test_message_list_lists_the_poison_marked_and_the_good_one(poisoned):
    r = message("list", "sess-a")
    _no_crash(r)
    assert r.exit_code == 0, r.output
    rows = [ln for ln in r.output.splitlines() if re.match(r"\s+\d+ ", ln)]
    assert len(rows) == 2, r.output
    assert "poison" in rows[0] and "<unreadable header: Message-ID>" in rows[0]
    assert "good one" in rows[1] and "unreadable" not in rows[1]


def test_message_read_reads_the_poison_marked(poisoned):
    r = message("read", "sess-a", "1", "--peek")
    _no_crash(r)
    assert r.exit_code == 0, r.output
    assert "Message-ID: <unreadable header> (keyed as 'x@[')" in r.output
    assert "Unreadable: Message-ID" in r.output
    assert "poison body" in r.output


@pytest.mark.parametrize("which", ["2", "<good1@example.invalid>"])
def test_message_read_reads_the_good_one_beside_it(poisoned, which):
    r = message("read", "sess-a", which, "--peek")
    _no_crash(r)
    assert r.exit_code == 0, r.output
    assert "Message-ID: <good1@example.invalid>" in r.output
    assert "Unreadable" not in r.output


def test_message_tree_beside_a_poison_mail(rig):
    # `tree` reads the seat's box: the rig's TM_AGENT_ADDRESS, `alpha`
    deliver_raw(rig.box("alpha"), _wire())
    rig.deliver("alpha", "good1@example.invalid", "good one")
    r = message("tree", "<good1@example.invalid>")
    _no_crash(r)
    assert r.exit_code == 0, r.output
    assert "good one" in r.output


def test_message_mark_beside_a_poison_mail(poisoned):
    r = message("mark", "sess-a", "2", "seen")
    _no_crash(r)


def test_message_verify_n_on_a_poison_mail(poisoned):
    r = message("verify", "sess-a", "1")
    _no_crash(r)
    assert "UnboundLocalError" not in r.output


# ── pager: status, the hand listener, the monitor ────────────────────────

def test_pager_status_beside_a_poison_mail(poisoned):
    r = CliRunner().invoke(pager.cli, ["status"], prog_name="pager")
    _no_crash(r)
    assert "UnboundLocalError" not in r.output


def test_the_box_line_and_the_arrival_line_are_marked(rig):
    path = deliver_raw(rig.box(), _wire())
    e = _maildir.parse(path, 1, True)
    assert pager._box_line(e).endswith("<unreadable header: Message-ID>")
    a = _detect.Arrival(path.name, KEYED_AS, path)
    assert pager._arrival_line("sess-a", a, _maildir).endswith(
        "<unreadable header: Message-ID>")


def test_a_resident_poison_pages_and_the_re_arm_does_not_die(rig):
    """Base: exit 1 and a traceback, and every re-arm died again in ~0.3 s."""
    deliver_raw(rig.box(), _wire())
    rig.stopped()
    first = run("--timeout", "3")
    assert first.exit_code == EX_PAGE, (first.exception, first.stdout)
    assert counted(first) == 1, first.stdout
    again = run("--timeout", "1")
    _no_crash(again)
    assert again.exit_code != 1, (again.exception, again.stdout)


def test_the_listener_pages_the_next_good_mail_after_a_poison_mail(rig, monkeypatch):
    """THE DEAFNESS. The poison only rides; the good mail after it wakes.
    Base: the listener died on the poison and never heard the good one."""
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "wake subject:good*\nride *")
    box = rig.box()
    deliver_raw(box, _wire())
    rig.stopped()
    from test_pager_listen import later
    later(0.5, rig.deliver, "sess-a", "good1@example.invalid", "good one")
    t0 = time.monotonic()
    result = run("--timeout", "5")
    assert result.exit_code == EX_PAGE, (result.exception, result.stdout)
    assert time.monotonic() - t0 >= 0.45, "paged before the good mail landed"
    assert "good1@example.invalid" in rig.marks(), rig.marks()


def test_an_urgent_poison_takes_the_at_once_path(rig, monkeypatch):
    """The at-once loop read the id through the same `_message_id`."""
    deliver_raw(rig.box(), _wire(extra=b"Priority: urgent\n"))
    rig.running()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.exception, result.stdout)


def test_a_poison_in_reply_to_keeps_its_row_on_the_page(rig):
    """Base: it paged, but `waiting_groups` caught the raise and SILENTLY
    dropped the mail's row (no `rows` line)."""
    if not _stdlib_raises("In-Reply-To", POISON_ID):
        pytest.skip("this interpreter reads In-Reply-To unstructured (3.9)")
    deliver_raw(rig.box(), _wire(mid=b"<irt1@example.invalid>",
                                 extra=b"In-Reply-To: <x@[ \n"))
    rig.stopped()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.exception, result.stdout)
    assert re.search(r"1 total; rows 1\.", result.stdout), result.stdout


def test_waiting_groups_counts_a_file_it_cannot_parse(rig, monkeypatch):
    """The broad guard that is left (a fault outside the accessor's set)
    counts the mail, marked, instead of dropping it; a file that WENT is
    still passed over."""
    box = rig.box()
    path = deliver_raw(box, _wire(mid=b"<other@example.invalid>"))
    gone = box / "new" / "gone.x"
    arrivals = [_detect.Arrival(path.name, "other@example.invalid", path),
                _detect.Arrival("gone.x", "gone@example.invalid", gone)]
    real = _maildir.parse

    def parse(p, index, unread):
        if p == path:
            raise RuntimeError("some fault that is not the parser's")
        return real(p, index, unread)

    monkeypatch.setattr(_maildir, "parse", parse)
    monkeypatch.setattr(_maildir, "enumerate_box", lambda box: [])
    groups = pager.waiting_groups(box, arrivals, None, "sess-a", _maildir)
    assert sum(g.total for g in groups) == 1, groups


def test_the_monitor_stays_hearing_after_a_poison_mail(rig, monkeypatch):
    """Base: the monitor died with exit 1 three seconds after the poison
    landed, and wrote nothing to stdout, the seat's one channel."""
    monkeypatch.setenv(pager.MONITOR_SPACING_VAR, "0.05")
    rig.stopped()
    box = rig.box()

    def script() -> None:
        assert until(lambda: _seat.stamp_dir(rig.seat, "sess-a").joinpath(
            _seat.LISTENING_STAMP).exists(), 10)
        deliver_raw(box, _wire())
        time.sleep(0.5)
        rig.deliver("sess-a", "good1@example.invalid", "good one")
        assert until(lambda: "good1@example.invalid" in rig.marks(), 10), rig.marks()
        time.sleep(0.4)

    t = driven(script)
    result = CliRunner().invoke(pager.cli, ["listen", "--monitor"], prog_name="pager")
    t.join(DEADLINE)
    assert result.exit_code == 0, (result.exception, result.stderr)
    assert "good1@example.invalid" in rig.marks(), rig.marks()
    assert [ln for ln in result.stdout.splitlines() if ln.startswith("pager: ")], result.stdout
