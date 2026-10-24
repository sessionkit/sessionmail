"""`pager listen` - one box, the detector under it, T by stamp.

Rulings 1340: one mailbox, the session's, by convention: the session id is
a box, and listen reads no other subscriptions. Rulings 1330: T = 0 stopped, ~60 s
running, chosen by the stopped stamp. Rulings 1320: the page says what
to do next; the listener leaves a stamp so "armed" is a fact.

Hermetic: a maildir root under tmp_path (SESSION_MAIL_ROOT), a seat under
SESSIONMAIL_HOME, deliveries the way an MDA makes them; the running page
age turned down to fractions of a second. No notmuch anywhere.
"""

from __future__ import annotations

import ast
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _arm, _detect, _seat, pager, rules
from sessionmail import _settings
from sessionmail.pager import cli

EX_PAGE, EX_COULD_NOT_LOOK, EX_TIMEOUT = 0, 2, 124
RUNNING_T = 0.4          # the running page age, for tests
IDLE_T = 60.0            # SESSIONMAIL_IDLE_SECONDS, for tests

FORBIDDEN = re.compile(r"dead|died|fail", re.I)


def _mail(sid: str) -> Path:
    """Where a session's mail state is since session-settings 3.3a: under
    the session dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/`
    (spelt out, not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def counted(result) -> int | None:
    """The count off the quiet page - since the page carries no per-message
    detail, the count is what says a page happened and how much it holds."""
    m = re.search(r"pager: (\d+) messages? waiting\.", result.stdout)
    return int(m.group(1)) if m else None


def _copy_of_pager(directory, version: str):
    """A `pager` on the PATH that answers --version and nothing else."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "pager"
    script.write_text(f"#!{sys.executable}\nprint({version!r})\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


_n = 0


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    root.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(RUNNING_T))
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, str(IDLE_T))
    monkeypatch.delenv(pager.PAGE_LIST_VAR, raising=False)   # the switch is OFF here
    # The PAGE is under test here, not the rules. Since pager-filters 4.1
    # (SM-474 do-006) the listener judges each arrival first, and these
    # mails (`To: <box>@example.invalid`, no `X-Original-To`) are not "to
    # me" under the built-ins, so they would ride and never page. The env
    # layer (P11, a ruled layer) says every mail wakes, so each test here
    # still pins what it pinned. The judgement: tests/test_pager_listen_judges.py.
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "wake *")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    # No real `session get idle` under test: unknown until a test says otherwise.
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    # SM-450 (tasks.md 3.3): the hooks ON in the sandbox HOME (conftest), so
    # the `pager hook stop` runs below keep their meaning.
    hooks_on = Path(os.environ["HOME"]) / _settings.RELATIVE
    hooks_on.parent.mkdir(parents=True, exist_ok=True)
    hooks_on.write_text('{"version": 1, "hooks": true}\n')

    class Rig:
        def __init__(self) -> None:
            self.root = root
            self.seat = _seat.resolve()

        def box(self, name: str = "sess-a") -> Path:
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def deliver(self, name: str, mid: str, subject: str = "hello", body: str = "THE BODY") -> str:
            global _n
            _n += 1
            b = self.box(name)
            fname = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.test"
            (b / "tmp" / fname).write_bytes(
                f"From: beta@example.invalid\r\nTo: {name}@example.invalid\r\n"
                f"Subject: {subject}\r\nMessage-ID: <{mid}>\r\n\r\n{body}\r\n".encode())
            os.rename(b / "tmp" / fname, b / "new" / fname)
            return fname

        def idle(self, seconds: float | None) -> None:
            """The verdict `session get idle` would give: None = unknown."""
            monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: seconds)

        def stopped(self, session: str = "sess-a") -> None:
            # changes/stopped-means-idle: "stopped" now means IDLE - not busy
            # for the threshold by Claude's own status - never a stamp.
            self.idle(IDLE_T + 15)

        def running(self, session: str = "sess-a") -> None:
            self.idle(0.0)

        def marks(self) -> str:
            m = _seat.marks_path(self.seat, "sess-a")
            return m.read_text() if m.exists() else ""

    return Rig()


@pytest.fixture
def sid(rig, fresh_session, monkeypatch):
    """SM-558: this test's own fresh session id (conftest `fresh_session`),
    set over the rig's `sess-a`. Every test here that runs a REAL `pager
    listen` child takes it; the child inherits it through the environment."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", fresh_session)
    return fresh_session


def run(*argv: str):
    return CliRunner().invoke(cli, ["listen", *argv], prog_name="pager")


def later(seconds: float, fn, *args) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), fn(*args)))
    t.start()
    return t


# ── events, not the clock (SM-863) ─────────────────────────────────────────
#
# A bound on how SOON a page lands goes red under load with no product change
# (SM-150, SM-863). The default lane asserts the EVENT or the ORDERING a test
# claims; a bound that measures a real latency is kept, unchanged, in a twin
# marked `latency`, which `make test` leaves out and `make test-latency` runs
# alone. tests/test_latency_lane.py holds this file to that.

#: The running page age when a test must make T unreachable: far past any
#: `--timeout` below, so only the T the test means can page, and anything
#: else times out (124).
OUT_OF_REACH_T = 3600.0
#: A timeout no load reaches. A page that is due lands on the first look, so
#: it costs nothing when the test is green.
LONG_TIMEOUT = "30"


def on_first_wait(monkeypatch, fn, *args) -> list:
    """Run FN(*ARGS) once, on the EVENT "the listener has waited once and
    found nothing due": its first detector call that ends in a Timeout. In
    the queue loop (every rig here has a seat) that Timeout is a tick and
    the loop looks again, so what FN changed is seen on the next look.
    Returns a list that holds one True once FN has run."""
    real = _detect.detect
    fired: list = []

    def detect(*a, **kw):
        try:
            return real(*a, **kw)
        except _detect.Timeout:
            if not fired:
                fired.append(True)
                fn(*args)
            raise

    monkeypatch.setattr(_detect, "detect", detect)
    return fired


def count_waits(monkeypatch) -> list:
    """Record each call into the detector (the only place `listen` waits)."""
    real = _detect.detect
    calls: list = []

    def detect(*a, **kw):
        calls.append(kw.get("timeout"))
        return real(*a, **kw)

    monkeypatch.setattr(_detect, "detect", detect)
    return calls


#: The page's own line for which T chose it (see test_the_page_says_which_stamp_chose_t).
PAGED_BY_IDLE = "pager listen: paged by the idle verdict (T=0s)"


def paged_by_running(t: float) -> str:
    return f"pager listen: paged by the running setting (T={t:g}s)"


def aged_since_delivery(rig, name: str, box: str = "sess-a") -> float:
    """Seconds since NAME's delivery, on the clock the pager ages it by: the
    file's mtime against `time.time()`. A floor read this way cannot go red
    from a slow test; a `t0` read after `rig.deliver` can."""
    return time.time() - (rig.root / box / "new" / name).stat().st_mtime


# ── one box, given ─────────────────────────────────────────────────────

def test_the_default_box_is_the_session_id_and_a_resident_pages_at_once_when_stopped(rig, monkeypatch):
    """"At once" is an ORDERING here (SM-863): stopped means T = 0, so the
    resident pages on the idle verdict and does not wait the running age.
    That age is put out of reach, so waiting it could only time out (124)."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(OUT_OF_REACH_T))
    rig.box()
    rig.deliver("sess-a", "r1@example.invalid", "resident")
    rig.stopped()
    result = run("--timeout", LONG_TIMEOUT)
    assert result.exit_code == EX_PAGE, result.stderr
    assert PAGED_BY_IDLE in result.stderr, result.stderr
    assert counted(result) == 1, result.stdout


@pytest.mark.latency
def test_the_default_box_is_the_session_id_and_a_resident_pages_at_once_when_stopped_latency(rig):
    """LATENCY lane (SM-863): the original body and its 0.3 s bound, kept
    exactly (SM-150: never loosen the 0.3 s bound). Load reddens it with no
    product change, so `make test` leaves it out; run it on a quiet host
    with `make test-latency`."""
    rig.box()
    rig.deliver("sess-a", "r1@example.invalid", "resident")
    rig.stopped()
    t0 = time.monotonic()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert time.monotonic() - t0 < 0.3
    assert counted(result) == 1, result.stdout


def test_a_named_box_and_a_path_both_work(rig):
    rig.deliver("judy", "m1@example.invalid", "as judy")
    rig.stopped()
    assert counted(run("judy", "--timeout", "3")) == 1
    rig.deliver("judy", "m2@example.invalid", "by path")
    result = run(str(rig.root / "judy"), "--timeout", "3")
    assert result.exit_code == EX_PAGE and counted(result) == 1


def test_no_box_and_no_session_id_refuses(rig, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    result = run("--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert "the session id is a box" in result.stderr


def test_a_box_without_new_refuses(rig):
    (rig.root / "bare").mkdir()
    result = run("bare", "--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK and "no new/" in result.stderr


def test_a_bare_name_nested_under_its_store_resolves_through_the_map(rig, monkeypatch):
    """A project box under nested-v1 is <root>/<store>/<name>/, not <root>/<name>:
    `pager listen sessionmail` refused "no maildir" while mail to that name
    landed (measured). The map postfix delivers by is
    asked, and the banner says so."""
    import socket
    host = socket.gethostname().split(".")[0].lower()
    nested = rig.root / "store" / "proj"
    for d in ("tmp", "new", "cur"):
        (nested / d).mkdir(parents=True)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: ["hash:/fake/vmailbox"] if p == "virtual_mailbox_maps" else None)
    monkeypatch.setattr(_seat, "_postmap_lookup", lambda key, maps: ("/fake/vmailbox", "store/proj/") if key == f"proj@{host}" else None)
    monkeypatch.setattr(_seat, "_postconf_value", lambda p: str(rig.root) if p == "virtual_mailbox_base" else None)
    # deliver into the NESTED box, then listen by the bare name
    fname = f"{int(time.time())}.Mnested.P{os.getpid()}.test"
    (nested / "tmp" / fname).write_bytes(b"From: beta@example.invalid\r\nTo: proj@example.invalid\r\nSubject: nested hello\r\nMessage-ID: <n1@example.invalid>\r\n\r\nBODY\r\n")
    os.rename(nested / "tmp" / fname, nested / "new" / fname)
    rig.stopped()
    result = run("proj", "--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert f"pager listen: box {nested}" in result.stderr
    assert f"via map    : proj@{host} -> store/proj/, in /fake/vmailbox" in result.stderr
    assert counted(result) == 1, result.stdout
    # a name the map does not know still refuses, and says the map was asked
    result = run("nowhere", "--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert "no new/" in result.stderr and f"nowhere@<host>" in result.stderr
    # a path never goes through the map (and never gets the hint)
    (rig.root / "bare").mkdir()
    result = run(str(rig.root / "bare"), "--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK and "virtual_mailbox_maps" not in result.stderr


def test_it_reads_no_subscriptions_and_writes_none(rig):
    """Rulings 1340: the subscriptions WALK is gone from listen. The BIRTH
    of the two born files went too (message-subscriptions 4.2): a bare seat
    gets no subscriptions folder, and one a human left is neither read nor
    rewritten, however loud it is."""
    rig.box()
    assert run("--timeout", "0").exit_code == EX_TIMEOUT
    assert not (rig.seat.root / "subscriptions").exists()
    (rig.seat.root / "subscriptions").mkdir(parents=True)
    loud = rig.seat.root / "subscriptions" / "loud"
    loud.write_text("query: to:me\nroute: pager\n")
    result = run("--timeout", "0")
    assert result.exit_code == EX_TIMEOUT                           # not consulted
    assert "born       :" not in result.stderr and "repaired" not in result.stderr
    assert [p.name for p in (rig.seat.root / "subscriptions").iterdir()] == ["loud"]


# ── T by stamp (Rulings 1330) ───────────────────────────────────────────

def test_running_waits_the_page_age_then_pages(rig):
    """The ORDERING (SM-863): a running seat waits T, then pages, by the
    running setting. The floor is read off the delivery's own mtime, the
    clock the pager ages it by, so neither a slow listener nor a slow test
    can redden it. How SOON after T is the latency twin's."""
    rig.box()
    name = rig.deliver("sess-a", "r2@example.invalid")
    rig.running()
    result = run("--timeout", LONG_TIMEOUT)
    assert result.exit_code == EX_PAGE, result.stderr
    assert paged_by_running(RUNNING_T) in result.stderr, result.stderr
    assert aged_since_delivery(rig, name) >= RUNNING_T


@pytest.mark.latency
def test_running_waits_the_page_age_then_pages_latency(rig):
    """LATENCY lane (SM-863): the original window, kept exactly. Run with
    `make test-latency` on a quiet host."""
    rig.box()
    rig.deliver("sess-a", "r2@example.invalid")
    rig.running()
    t0 = time.monotonic()
    result = run("--timeout", "3")
    took = time.monotonic() - t0
    assert result.exit_code == EX_PAGE
    assert RUNNING_T - 0.05 <= took < RUNNING_T + 0.6, f"paged after {took:.2f}s"


def test_an_arrival_while_stopped_pages_at_once(rig, monkeypatch):
    """The ORDERING (SM-863): mail that ARRIVES while the seat is stopped
    pages on the idle verdict (T = 0). It lands on the event "the listener
    has waited once and found the box empty", not at a fixed +0.2 s, and
    the running age is out of reach."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(OUT_OF_REACH_T))
    rig.box()
    rig.stopped()
    arrived = on_first_wait(monkeypatch, rig.deliver, "sess-a", "a1@example.invalid")
    result = run("--timeout", LONG_TIMEOUT)
    assert arrived == [True], "the mail never arrived mid-listen"
    assert result.exit_code == EX_PAGE, result.stderr
    assert PAGED_BY_IDLE in result.stderr, result.stderr
    assert counted(result) == 1, result.stdout


@pytest.mark.latency
def test_an_arrival_while_stopped_pages_at_once_latency(rig):
    """LATENCY lane (SM-863): the original body and its 0.7 s bound, kept
    exactly. Run with `make test-latency` on a quiet host."""
    rig.box()
    rig.stopped()
    later(0.2, rig.deliver, "sess-a", "a1@example.invalid")
    t0 = time.monotonic()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE
    assert time.monotonic() - t0 < 0.7


def test_a_stop_mid_wait_pages_inside_one_poll(rig, monkeypatch):
    """The ORDERING (SM-863): T is re-read while the listener waits. The
    stop lands on the event "the listener has waited once with this mail
    not yet due", and with the running age out of reach only the re-read
    idle verdict can page. "Inside one poll" is the latency twin's."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(OUT_OF_REACH_T))
    rig.box()
    rig.deliver("sess-a", "s2@example.invalid")
    rig.running()
    stopped = on_first_wait(monkeypatch, rig.stopped)
    result = run("--timeout", LONG_TIMEOUT)
    assert stopped == [True], "the seat never stopped mid-wait"
    assert result.exit_code == EX_PAGE, result.stderr
    assert PAGED_BY_IDLE in result.stderr, result.stderr


@pytest.mark.latency
def test_a_stop_mid_wait_pages_inside_one_poll_latency(rig, monkeypatch):
    """LATENCY lane (SM-863): the original window, kept exactly. Run with
    `make test-latency` on a quiet host."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "5")
    rig.box()
    rig.deliver("sess-a", "s2@example.invalid")
    rig.running()
    later(0.3, rig.stopped)
    t0 = time.monotonic()
    result = run("--timeout", "3")
    took = time.monotonic() - t0
    assert result.exit_code == EX_PAGE
    assert 0.25 <= took < 0.8, f"paged after {took:.2f}s"


def test_no_stamps_at_all_means_running(rig):
    """The floor is read off the delivery's mtime (SM-863): `>= T` exactly,
    where it was `>= T - 0.05` from a clock read after the delivery."""
    rig.box()
    name = rig.deliver("sess-a", "n1@example.invalid")
    assert run("--timeout", "3").exit_code == EX_PAGE
    assert aged_since_delivery(rig, name) >= RUNNING_T


# ── the QUIET page (tasks.md 3.1-3.3, 4.1; Rulings 1320 #1) ──────────────
#
# specs/pager, "The pager is a nudge, and the re-arm carries", AMENDED and
# RATIFIED: "A page SHALL carry no
# per-message detail by default: no sender, no subject, no count of who it
# is from." Four things, in this order: the pager is off + how to arm it,
# the count, the ride-along's state, the commands to reach the mail.

SENDER = "beta"                  # what rig.deliver() puts in From:


def quiet_gate(page: str, subjects: list[str]) -> None:
    """THE 4.1 GATE. Raises AssertionError on a page that carries
    per-message detail. Applied to the real page below, and to a page
    built the OLD way in test_the_quiet_gate_can_fail - a gate that
    cannot fail proves nothing."""
    assert SENDER not in page, f"the page names the sender: {page!r}"
    for subject in subjects:
        assert subject not in page, f"the page names a subject ({subject}): {page!r}"


def _listing_lines(rig, box: str = "sess-a") -> list[str]:
    """THE LISTING FORM, oldest first, built by the real code that builds
    it (`pager._arrival_line`), never by a hand-written imitation - which
    is what makes 4.2's comparison a byte-for-byte one and not a fresh
    guess at the bytes."""
    from sessionmail import _detect, _maildir
    new_dir = rig.root / box / "new"
    waiting = sorted((p.stat().st_mtime, p.name, p) for p in new_dir.iterdir())
    return [pager._arrival_line(box, _detect.Arrival(n, _detect._message_id(p), p), _maildir)
            for _, n, p in waiting]


def _old_style_page(rig, box: str = "sess-a") -> str:
    """The page as it was built BEFORE the quiet page: the lead line, then
    one line per message."""
    return "\n".join([pager.PAGE_WAKE, *_listing_lines(rig, box)])


def test_the_default_page_has_no_sender_no_subject_and_carries_the_four_things(rig):
    rig.box()
    rig.deliver("sess-a", "p1@example.invalid", "first", body="SECRET BODY ONE")
    rig.deliver("sess-a", "p2@example.invalid", "second", body="SECRET BODY TWO")
    rig.stopped()
    result = run("--timeout", "3")
    page = result.stdout
    quiet_gate(page, ["first", "second"])
    assert "SECRET" not in result.output
    lines = page.splitlines()
    off, count, groups, ride, commands = _page_parts(lines)
    # Item 2 also says WHAT WOKE IT since pager-filters D7 (SM-474 do-007):
    # the deciding rule and its layer, never a sender or a subject. The rig
    # sets `wake *` in the env layer, so that is the one rule.
    why = [g for g in groups if g.startswith("pager: wake by ")]
    groups = [g for g in groups if g not in why]
    assert why == ["pager: wake by `wake *` (env)"], why
    # THE FOUR THINGS ARE STILL FOUR. What changed on 2030-06-09 is that
    # item 2 - "how many messages are waiting" - is now said per envelope
    # recipient as well as in total, so the page is 4 lines PLUS one per
    # address (and, since D7, one per deciding rule). The items themselves,
    # their order, and the prohibition on sender and subject are untouched,
    # and `quiet_gate` above still holds.
    assert len(lines) == 4 + len(groups) + len(why), lines
    # 1. the pager is off now, and the command that arms it again - read
    #    from the one arm source, never spelled here.
    assert off == pager.PAGE_OFF
    assert _arm.instruction() in off
    # 2. the count, never a manifest
    assert count == "pager: 2 messages waiting."
    # 2. ...and the same count by address: these fixtures carry no
    #    X-Original-To, so they are one anonymous group.
    assert groups == ["pager: (no X-Original-To) - 2 unseen, 2 unread, 2 total; rows 1..2."], groups
    # 3. the ride-along's state (its own test below)
    assert ride.startswith("pager: ride-along - ")
    # 4. the way to reach the mail, which is not optional
    assert commands == pager.PAGE_COMMANDS
    for verb in ("message list", "message read", "message mark"):
        assert verb in commands


def test_the_quiet_gate_can_fail(rig):
    """The same gate, over a page built the old way: it catches it."""
    rig.box()
    rig.deliver("sess-a", "q1@example.invalid", "first")
    rig.deliver("sess-a", "q2@example.invalid", "second")
    old_page = _old_style_page(rig)
    assert "[sess-a] beta first" in old_page, old_page      # it IS the old page
    with pytest.raises(AssertionError, match="names the sender"):
        quiet_gate(old_page, ["first", "second"])
    with pytest.raises(AssertionError, match="names a subject"):
        quiet_gate(old_page.replace(SENDER, "?"), ["first", "second"])


def test_one_waiting_is_singular(rig):
    rig.box()
    rig.deliver("sess-a", "s1@example.invalid", "alone")
    rig.stopped()
    assert "pager: 1 message waiting." in run("--timeout", "3").stdout


# ── the SWITCH (tasks.md 3.4, 4.2) ───────────────────────────────────────
#
# specs/pager, "The pager is a nudge, and the re-arm carries": "A page MAY
# carry one line per entry - box, sender, subject, never a body - when a
# switch is set. The switch SHALL default off." RULED: BOTH an environment
# variable in the `SESSIONMAIL_` family AND a flag on `pager listen`; default off for both; WHERE BOTH ARE GIVEN THE
# FLAG WINS.
#
# The switch ADDS the lines under the four things; it does not put the old
# PAGE_WAKE page back. Items 1-4 are SHALL - "A page SHALL NOT leave the
# reader without a way to reach the mail. Item 4 is not optional" - so a
# reader who asked for detail still gets the re-arm command first.


def listing_gate(lines: list[str], expected: list[str]) -> None:
    """THE 4.2 GATE, and it is EXACT: the page's per-entry lines must be
    the listing form byte for byte, in order. Applied to the real page
    below, and to a one-character near-miss in
    `test_the_byte_for_byte_gate_can_fail` - a gate that cannot fail
    proves nothing."""
    assert lines == expected, f"not the listing form, byte for byte: {lines!r} != {expected!r}"


def _ride_at(lines: list[str]) -> int:
    """Where item 3 sits. Found by SEARCH, not by a fixed index, because
    item 2 is no longer one line: since the grouping ruling the count is said
    per `X-Original-To`, so a variable number of grouped-count lines sits between the count and the ride-along line."""
    for n, line in enumerate(lines):
        if line.startswith("pager: ride-along - "):
            return n
    raise AssertionError(f"no ride-along line on the page: {lines!r}")


def _page_parts(lines: list[str]) -> tuple[str, str, list[str], str, str]:
    """The page split into its ratified items: (off, count, groups, ride,
    commands). `groups` is item 2's per-address detail and may be empty."""
    r = _ride_at(lines)
    return lines[0], lines[1], lines[2:r], lines[r], lines[r + 1]


def _quiet_len(lines: list[str]) -> int:
    """How many lines the QUIET page occupies - the four items plus item
    2's grouped counts. Anything after this is the switch's listing."""
    return _ride_at(lines) + 2


def _under_the_page(result) -> list[str]:
    """What the page carries UNDER its quiet items."""
    lines = result.stdout.splitlines()
    return lines[_quiet_len(lines):]


def test_no_flag_and_no_variable_is_the_quiet_page(rig):
    """Default off, and off means the page is unchanged: four lines."""
    rig.box()
    rig.deliver("sess-a", "d1@example.invalid", "first", body="SECRET BODY")
    rig.deliver("sess-a", "d2@example.invalid", "second", body="SECRET BODY")
    rig.stopped()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE
    quiet_gate(result.stdout, ["first", "second"])
    lines = result.stdout.splitlines()
    assert len(lines) == 4 + len(_page_parts(lines)[2])
    assert _under_the_page(result) == []


def test_the_switch_restores_the_listing_form_byte_for_byte(rig):
    """4.2. `--list` brings back exactly the lines the quiet page took
    away - the same `_arrival_line` bytes, oldest first - and puts them
    UNDER the four things, which do not go away."""
    rig.box()
    rig.deliver("sess-a", "L1@example.invalid", "first", body="SECRET BODY ONE")
    time.sleep(0.02)                    # so oldest-first is a real ordering
    rig.deliver("sess-a", "L2@example.invalid", "second", body="SECRET BODY TWO")
    rig.stopped()
    result = run("--list", "--timeout", "3")
    assert result.exit_code == EX_PAGE
    lines = result.stdout.splitlines()

    # the four things, still in the requirement's order
    off, count, _groups, ride, commands = _page_parts(lines)
    assert off == pager.PAGE_OFF
    assert count == "pager: 2 messages waiting."
    assert ride.startswith("pager: ride-along - ")
    assert commands == pager.PAGE_COMMANDS

    # and under them, the listing form, byte for byte
    listing_gate(lines[_quiet_len(lines):], _listing_lines(rig))
    # ... which is these bytes, oldest first - written out once, so that a
    # change to BOTH the page and the builder still fails here.
    listing_gate(lines[_quiet_len(lines):],
                 ["[sess-a] beta first", "[sess-a] beta second"])

    # never a body, however the switch is set
    assert "SECRET" not in result.output


def test_the_byte_for_byte_gate_can_fail(rig):
    """The same gate, over a one-character near-miss: it catches it. The
    listing form is bytes, not a shape."""
    rig.box()
    rig.deliver("sess-a", "n1@example.invalid", "first")
    real = _listing_lines(rig)
    assert real == ["[sess-a] beta first"], real
    for near_miss in (["[sess-a] beta  first"],          # two spaces, not one
                      ["[sess-a]: beta first"],          # a colon after the box
                      ["[sess-a] beta@example.invalid first"],   # the whole address
                      []):                               # no lines at all
        with pytest.raises(AssertionError, match="byte for byte"):
            listing_gate(near_miss, real)


def test_the_listed_lines_are_oldest_first(rig):
    """The order the lines print in - RESTORED wording, ratified at cc86b8a
    ("it returns at once with the page text and five lines, oldest first")
    and now stated in the switch's own requirement. Delivery order, not
    the reverse and not the directory's."""
    rig.box()
    for n, subject in enumerate(("eldest", "middle", "newest")):
        rig.deliver("sess-a", f"o{n}@example.invalid", subject)
        time.sleep(0.02)
    rig.stopped()
    lines = _under_the_page(run("--list", "--timeout", "3"))
    assert [l.split()[-1] for l in lines] == ["eldest", "middle", "newest"], lines


def test_the_flag_wins_when_the_variable_says_list(rig, monkeypatch):
    """Both given, disagreeing, direction one: the variable says list, the
    flag says no. THE FLAG WINS - the page stays quiet."""
    monkeypatch.setenv(pager.PAGE_LIST_VAR, "on")
    rig.box()
    rig.deliver("sess-a", "w1@example.invalid", "first")
    rig.stopped()
    result = run("--no-list", "--timeout", "3")
    assert result.exit_code == EX_PAGE
    quiet_gate(result.stdout, ["first"])
    assert _under_the_page(result) == []


def test_the_flag_wins_when_the_variable_says_nothing(rig, monkeypatch):
    """Both given, disagreeing, direction two: the variable is explicitly
    off, the flag says list. THE FLAG WINS - the lines are there."""
    monkeypatch.setenv(pager.PAGE_LIST_VAR, "off")
    rig.box()
    rig.deliver("sess-a", "w2@example.invalid", "first")
    rig.stopped()
    result = run("--list", "--timeout", "3")
    assert result.exit_code == EX_PAGE
    listing_gate(_under_the_page(result), _listing_lines(rig))


def test_the_variable_alone_sets_the_switch_and_a_typo_does_not(rig, monkeypatch):
    """With no flag, the variable decides - and only when it says one of
    the words. A misspelling leaves the switch OFF, because the switch
    defaults off and a typo must not put senders on a page."""
    rig.box()
    rig.deliver("sess-a", "v1@example.invalid", "first")
    rig.stopped()
    monkeypatch.setenv(pager.PAGE_LIST_VAR, "on")
    assert _under_the_page(run("--timeout", "3")) == _listing_lines(rig)

    rig.deliver("sess-a", "v2@example.invalid", "second")   # v1 is already given
    monkeypatch.setenv(pager.PAGE_LIST_VAR, "yeah nah")
    result = run("--timeout", "3")
    assert _under_the_page(result) == []
    quiet_gate(result.stdout, ["second"])


@pytest.mark.parametrize("flag, var, expected", [
    (None, None, False),          # default off
    (None, "", False),
    (None, "on", True),
    (None, " YES ", True),        # stripped, case-folded
    (None, "1", True),
    (None, "true", True),
    (None, "off", False),
    (None, "banana", False),      # not one of the words: off
    (True, None, True),
    (True, "off", True),          # the flag wins
    (True, "banana", True),
    (False, "on", False),         # the flag wins, the other way
    (False, None, False),
])
def test_the_switch_resolves_the_flag_over_the_variable(flag, var, expected):
    env = {} if var is None else {pager.PAGE_LIST_VAR: var}
    assert pager.page_list_on(flag, env) is expected


# ── the ride-along line, off the stamp's mtime (3.2, 3.3, 4.3) ───────────

def _ride_line(result) -> str:
    return [l for l in result.stdout.splitlines() if l.startswith("pager: ride-along")][0]


def test_the_ride_along_line_changes_with_the_stamps_mtime(rig):
    """4.3: run against a stamp the test sets itself. Absent, fresh and
    stale are three different sentences off ONE instrument - the mtime of
    `<seat>/stamps/<session>/ride-along`, which the ride-along already
    touches. Nothing new is built to read it."""
    rig.box()
    rig.deliver("sess-a", "r1@example.invalid", "hello")
    rig.stopped()                      # the stopped stamp only: no ride-along stamp
    stamp = _seat.stamp_dir(rig.seat, "sess-a") / _seat.RIDE_ALONG_STAMP
    assert not stamp.exists()

    absent = _ride_line(run("--timeout", "3"))
    assert "has not run for this session" in absent
    assert str(stamp) in absent                     # it says what it read
    assert not FORBIDDEN.search(absent), absent     # absent is not dead

    # FRESH: the ride-along ran a moment ago, so the entries arrive on the
    # next tool call and the reader should wait.
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    rig.deliver("sess-a", "r2@example.invalid", "hello")   # r1 is already given
    fresh = _ride_line(run("--timeout", "3"))
    assert "live, last ran" in fresh and "next tool call" in fresh
    assert "Wait rather than act." in fresh

    # STALE: the same stamp, its mtime pushed past the REMINDER window.
    # (It was the busy TTL until 2030-06-09; stopped-means-idle retired
    # `_seat.busy_ttl`, and ride_along_line now reads `remind_minutes()`.
    # Same instrument - the mtime - different sentence, and it still says
    # the age it read. ttl + 600 keeps that age at 70m, as before.)
    ttl = _seat.remind_minutes() * 60
    old = time.time() - (ttl + 600)
    os.utime(stamp, (old, old))
    rig.deliver("sess-a", "r3@example.invalid", "hello")
    stale = _ride_line(run("--timeout", "3"))
    assert "past the" in stale and "min window" in stale
    assert "70m ago" in stale, stale                # the evidence, not just a verdict
    assert not FORBIDDEN.search(stale), stale
    assert stale != fresh != absent


def test_the_three_ride_along_states_are_read_off_the_stamp_alone(rig, monkeypatch):
    """The same three states, at the function, with the clock given: no
    listener, no maildir, nothing but the stamp."""
    session = "sess-a"
    stamp = _seat.stamp_dir(rig.seat, session) / _seat.RIDE_ALONG_STAMP
    assert "has not run for this session" in pager.ride_along_line(rig.seat, session)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    now = time.time()
    assert "live, last ran" in pager.ride_along_line(rig.seat, session, now)
    ttl = _seat.remind_minutes() * 60
    assert "min window" in pager.ride_along_line(rig.seat, session, now + ttl + 1)
    # A seat is a NAME: with none, and the same session, it is the same
    # line - the stamp is under the session id (3.6). It is a run with no
    # session id EITHER (a human at a shell) that has no stamp to read,
    # and it says THAT rather than that the ride-along has not run.
    assert pager.ride_along_line(None, session, now) == pager.ride_along_line(rig.seat, session, now)
    nothing = pager.ride_along_line(None, "-")
    assert "no session id and no seat identity" in nothing
    assert "has not run" not in nothing


# ── 3.6/4.7: everything keys on the session id; a seat is a name ────────
#: specs/pager, "The pager is a nudge": *"Everything a page reads - the
#: queue, the stamps, the marks - SHALL be keyed on the session id. A seat
#: is a name and MAY be absent; nothing in a page depends on one."*
#: Scenario "A seatless session": the page is
#: the same page a seated session gets.

def _seatless(monkeypatch) -> None:
    """The SAME session, with no seat name in the environment at all."""
    for var in _seat.SEAT_VARS:
        monkeypatch.delenv(var, raising=False)


def test_a_seatless_session_gets_the_same_page_a_seated_one_gets(rig, monkeypatch):
    """4.7. Two real `pager listen` runs over one session id - the first
    with a seat, the second with none - and the pages are compared BYTE
    FOR BYTE.

    Byte for byte is an honest comparison here precisely because the page
    is quiet: it carries no sender and no subject (4.1), so two different
    arrivals produce the same text, and any difference left is a
    difference in what the two runs READ. Two such differences are what
    this pins, and both are the slice's subject:

    ⚠️ ONE EXCEPTION, added 2030-06-09 with the `X-Original-To` grouping,
    and named rather than quietly absorbed: item 2 now carries the ROW
    RANGE of each group, and the two runs page about DIFFERENT messages
    (k1, then k2), which sit at different rows. That difference is a
    property of WHICH MESSAGE ARRIVED, not of having a seat, so it is
    normalised out below - and the assertion is made STRICTER in exchange:
    the two pages must differ in the row range and in NOTHING ELSE. The
    old `==` would not have caught a second difference appearing beside
    it; this does.

    - the STAMP: keyed on the seat, the seatless run looked in a
      different directory, so its ride-along line named a different path;
    - the MARKS: keyed on the seat, the seatless run did not see the
      `seen` mark the seated run had just written, so it paged the first
      message again and its count said 2, not 1.
    """
    rig.box()
    rig.deliver("sess-a", "k1@example.invalid", "seated")
    rig.stopped()
    seated = run("--timeout", "3")
    assert seated.exit_code == EX_PAGE, seated.stderr
    assert counted(seated) == 1, seated.stdout

    _seatless(monkeypatch)
    seatless_seat = _seat.resolve()
    assert seatless_seat.source == "CLAUDE_CODE_SESSION_ID"   # a name, not a seat
    rig.deliver("sess-a", "k2@example.invalid", "seatless")
    rig.stopped()
    seatless = run("--timeout", "3")
    assert seatless.exit_code == EX_PAGE, seatless.stderr

    assert counted(seatless) == 1, seatless.stdout   # k1 was marked seen, and it saw it

    def without_rows(page: str) -> str:
        return re.sub(r"rows \d+(\.\.\d+)?", "rows <row>", page)

    # everything that is not the row range is byte-identical ...
    assert without_rows(seatless.stdout) == without_rows(seated.stdout)
    # ... and the row range really is the ONLY difference, so this test
    # cannot pass by normalising away a second, unrelated divergence.
    assert seatless.stdout != seated.stdout
    assert re.findall(r"rows \d+(?:\.\.\d+)?", seated.stdout) == ["rows 1"]
    assert re.findall(r"rows \d+(?:\.\.\d+)?", seatless.stdout) == ["rows 2"]


def test_the_queue_the_stamps_and_the_marks_are_found_under_the_session_id(rig, monkeypatch):
    """The other half of the scenario - WHERE they were found - asserted on
    the paths themselves, so a page that happened to match for some other
    reason cannot carry this test."""
    rig.box()
    rig.deliver("sess-a", "k3@example.invalid", "hello")
    rig.stopped()
    assert run("--timeout", "3").exit_code == EX_PAGE

    under = _seat.session_root("sess-a")
    assert under == _mail("sess-a")   # 3.3a: the session dir, not SESSIONMAIL_HOME
    assert (under / _seat.RIDE_DIR / _seat.QUEUE_NEW).is_dir()          # the queue
    # The stamps. The witness was the `stopped` stamp until 2030-06-09;
    # stopped-means-idle stopped writing it (idle is now Claude Code's own
    # verdict, never a stamp), so it can no longer witness WHERE a stamp
    # lands. The ride-along stamp is the one stamp still written, and it is
    # what the page reads - so it is the honest witness for this claim.
    # `touch` takes the seat and the session, and must key on the SESSION:
    assert _seat.touch(rig.seat, _seat.RIDE_ALONG_STAMP, "sess-a") == (
        under / _seat.STAMPS_DIR / _seat.RIDE_ALONG_STAMP)
    assert (under / _seat.STAMPS_DIR / _seat.RIDE_ALONG_STAMP).exists()  # the stamps
    assert "k3@example.invalid seen" in (under / "marks").read_text()   # the marks

    # and NOTHING of the three was written under the seat. The seat keeps
    # its subscriptions; that is all it keeps.
    assert not (rig.seat.root / _seat.RIDE_DIR).exists()
    assert not (rig.seat.root / _seat.STAMPS_DIR).exists()
    assert not (rig.seat.root / "marks").exists()

    # the seatless run of the same session resolves to the same three paths
    _seatless(monkeypatch)
    bare = _seat.resolve()
    assert _seat.ride_dir(bare, "sess-a") == _seat.ride_dir(rig.seat, "sess-a")
    assert _seat.stamp_dir(bare, "sess-a") == _seat.stamp_dir(rig.seat, "sess-a")
    assert _seat.marks_path(bare, "sess-a") == _seat.marks_path(rig.seat, "sess-a")


def test_the_ride_along_line_does_not_depend_on_a_seat(rig):
    """Item 3 is the one page item that reads a per-session file, so it is
    where a seat could still leak into the page. With NO seat and the same
    session it is the same sentence, in all three states."""
    stamp = _seat.stamp_dir(rig.seat, "sess-a") / _seat.RIDE_ALONG_STAMP
    assert pager.ride_along_line(None, "sess-a") == pager.ride_along_line(rig.seat, "sess-a")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    now = time.time()
    assert pager.ride_along_line(None, "sess-a", now) == pager.ride_along_line(rig.seat, "sess-a", now)
    late = now + _seat.remind_minutes() * 60 + 1
    assert pager.ride_along_line(None, "sess-a", late) == pager.ride_along_line(rig.seat, "sess-a", late)
    assert "live" in pager.ride_along_line(None, "sess-a", now)     # not a pair of refusals


def test_a_run_with_no_session_id_and_no_seat_says_so_and_pages_anyway(rig, monkeypatch):
    """The case 1.3 left OPEN, pinned so the change above did not quietly
    settle it: no session id and no seat is not a session, so there is no
    queue and nothing to key a stamp on - and the page says that rather
    than inventing a bucket. It still pages, on the box's own new/."""
    _seatless(monkeypatch)
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(_seat.NoIdentity):
        _seat.resolve()
    rig.box("judy")
    rig.deliver("judy", "k4@example.invalid", "hello")
    result = run("judy", "--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert counted(result) == 1
    line = _ride_line(result)
    assert "no session id and no seat identity" in line
    assert not FORBIDDEN.search(line), line
    assert "queue      : NONE" in result.stderr


def test_a_timeout_returns_124_and_says_what_to_do_next(rig):
    rig.box()
    result = run("--timeout", "0")
    assert result.exit_code == EX_TIMEOUT
    assert "returned" in result.stderr
    assert result.stdout.strip() == pager.PAGE_NOTHING


# ── BOX and --timeout: out of the help, still honoured (SM-374 do-001) ──
#
# An argument in the help is one agents will use, so both leave the help, and
# the timeout argument goes at once (spine 10.34). BOX keeps one release of grace
# (openspec/changes/message-subscriptions/specs/pager/spec.md:88); --timeout
# keeps working until taskmaster stops calling it (tasks.md 4.8b / 5.2).
# So the HELP loses both now and the BEHAVIOUR loses neither.

def test_the_help_names_neither_box_nor_timeout(rig):
    result = run("--help")
    assert result.exit_code == 0, result.output
    page = result.output
    # D10 (tasks.md 4.9) names BOX once, as the argument of the verb that
    # replaces listen's: `message subscription add BOX`. Listen's own usage
    # and prose still never take one.
    assert page.count("message subscription add BOX") == 1, page
    page = page.replace("message subscription add BOX", "")
    assert not re.search(r"\bBOX\b", page), page
    assert "--timeout" not in page and not re.search(r"(^|\s)-t\b", page), page
    assert "124" not in page, page       # the exit only --timeout can cause
    assert len(page.splitlines()) <= 40, page


def test_box_and_timeout_are_still_honoured_though_unlisted(rig):
    rig.deliver("judy", "g1@example.invalid", "a named box, grace")
    rig.stopped()
    result = run("judy", "--timeout", "3")
    assert result.exit_code == EX_PAGE and counted(result) == 1, result.stderr
    assert "box " + str(rig.root / "judy") in result.stderr
    rig.box()
    for flag in ("--timeout", "-t"):
        result = run(flag, "0")
        assert result.exit_code == EX_TIMEOUT, (flag, result.output)
        assert "returned - nothing paged in 0s" in result.stderr


def test_quiet_prints_no_banner(rig):
    rig.box()
    result = run("--timeout", "0", "--quiet")
    assert result.exit_code == EX_TIMEOUT
    assert result.stderr == "" and result.output == ""


def test_the_banner_tells_the_truth(rig):
    rig.box()
    rig.deliver("sess-a", "b1@example.invalid")
    rig.stopped()
    result = run("--timeout", "3")
    err = result.stderr
    assert "box " + str(rig.root / "sess-a") in err
    assert "page age   : now idle 75s >= 60s -> at once" in err
    assert "box now    : 1 in new/" in err
    assert "seat       : alpha (from TM_AGENT_ADDRESS), session sess-a" in err


def test_the_banner_says_what_to_do_when_nothing_can_say_busy(rig, monkeypatch):
    """Idleness is Claude Code's own word (its sidecar, then `session get
    idle`). A client that is NEITHER has no verdict for the life of the
    session, so every page waits the running age - mail late, never lost.
    The banner names the cure where a stranger meets it. Behaviour is
    unchanged: unknown is still not idle (changes/stopped-means-idle item
    5). The case was measured live."""
    cure = f"not Claude Code? set {pager.PAGE_AGE_VAR}=0 to page at once"

    rig.box()
    rig.idle(0.0)                        # a seat Claude Code answers for:
    err = run("--timeout", "0").stderr   # nothing is blind, so no advice
    assert "page age   : now busy" in err, err
    assert cure not in err, err

    rig.idle(None)                       # no sidecar, no `session`: unknown
    err = run("--timeout", "0").stderr
    assert "page age   : now unknown" in err, err
    assert err.count(cure) == 1, err

    for var in _seat.IDENTITY_VARS:      # a human at a shell: no seat at all
        monkeypatch.delenv(var, raising=False)
    rig.box("judy")
    err = run("judy", "--timeout", "0").stderr
    assert "seat       : NONE" in err, err
    assert "(no seat: idleness is never asked)" in err, err
    assert err.count(cure) == 1, err


# ── never twice; marks ────────────────────────────────────────────────────

def test_a_wake_marks_seen_pager_wake_and_is_not_paged_again_inside_the_window(rig):
    rig.box()
    rig.deliver("sess-a", "w1@example.invalid")
    rig.stopped()
    assert run("--timeout", "3").exit_code == EX_PAGE
    assert re.search(r"w1@example.invalid seen alpha sess-a pager wake", rig.marks())
    again = run("--timeout", "0.3")
    assert again.exit_code == EX_TIMEOUT, again.output
    assert "1 id(s) already given to this session" in again.stderr


def test_another_sessions_seen_does_not_silence_this_one(rig):
    rig.box()
    rig.deliver("sess-a", "o1@example.invalid")
    rig.stopped()
    stamp = "2030-06-09T22:34:56+10:00"
    _seat.write_mark(rig.seat, "o1@example.invalid", "seen", "sess-b", stamp, "pager wake")
    assert run("--timeout", "3").exit_code == EX_PAGE


def test_a_given_resident_beside_a_fresh_arrival_pages_only_the_fresh_one(rig):
    rig.box()
    rig.deliver("sess-a", "g1@example.invalid", "old")
    rig.stopped()
    assert run("--timeout", "3").exit_code == EX_PAGE
    later(0.2, rig.deliver, "sess-a", "g2@example.invalid", "new")
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE
    assert counted(result) == 1, result.stdout      # one entry, not two
    # WHICH one, though. The page no longer names it (the quiet page carries
    # a count, never a manifest), so the count alone cannot tell the fresh
    # entry from the given one - and this test's whole subject is WHICH. The
    # page's own other record can: it marks each paged entry `seen` with
    # reason `pager wake`, one row per page. So read the id off the marks.
    marks = rig.marks()
    assert re.search(r"g2@example.invalid seen alpha sess-a pager wake", marks), marks
    assert len(re.findall(r"g1@example.invalid seen", marks)) == 1, marks


# ── stamps, identity, caller ─────────────────────────────────────────────

def test_listen_writes_its_pid_as_the_listening_stamp_and_clears_it_on_exit(rig):
    rig.box()
    stamp = _seat.stamp_dir(rig.seat, "sess-a") / "listening"
    seen: list[str] = []
    returned = threading.Event()

    def watch() -> None:
        # Until the listen RETURNS (SM-863), not for a fixed 2 s: a listener
        # slow to start under load is still one that wrote its stamp.
        while not returned.is_set():
            if stamp.exists():
                seen.append(stamp.read_text().strip())
                return
            time.sleep(0.01)

    t = threading.Thread(target=watch)
    t.start()
    try:
        result = run("--timeout", "0.5")
    finally:
        returned.set()
        t.join()
    assert result.exit_code == EX_TIMEOUT
    assert seen == [str(os.getpid())]
    assert not stamp.exists()


def test_a_reader_racing_the_listening_stamp_never_reads_it_empty(rig):
    """SM-753: the stamp is written whole or not at all. It was touch() then
    write_text(), and a reader between them read '' - the flake in the test
    above (`'' != '714'`), and a hook's "not listening" for that instant. A
    second's worth of fresh arms (each one a new file, the widest window),
    with a reader polling as the hooks do: exists(), then read."""
    stamp = _seat.stamp_dir(rig.seat, "sess-a") / _seat.LISTENING_STAMP
    seen: list[str] = []
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            if stamp.exists():
                try:
                    seen.append(stamp.read_text().strip())
                except FileNotFoundError:
                    pass                           # unlinked for the next arm

    t = threading.Thread(target=read)
    t.start()
    try:
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            stamp.unlink(missing_ok=True)
            _seat.write_listening(rig.seat, "sess-a", os.getpid())
    finally:
        stop.set()
        t.join()
    assert seen, "the reader never saw the stamp at all"
    assert "" not in seen, f"{seen.count('')} of {len(seen)} reads saw the stamp EMPTY"
    assert set(seen) == {str(os.getpid())}
    # The temp file never outlives the write.
    assert [p.name for p in stamp.parent.iterdir() if p.name.startswith(".listening")] == []


def test_no_seat_identity_still_listens_and_says_so(rig, monkeypatch):
    """A human at a shell: `pager listen judy` with no seat in the
    environment. No stamp, no marks, page age = running; announced."""
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    rig.deliver("judy", "h1@example.invalid", "for judy")
    result = run("judy", "--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert "seat       : NONE" in result.stderr
    assert "queue      : NONE - no session id and no seat" in result.stderr
    assert counted(result) == 1, result.stdout
    assert rig.marks() == ""
    assert not (rig.seat.root / _seat.RIDE_DIR).exists()


# The child for the caller-going test (SM-150 fix-001): the real `pager
# listen`, plus one observation. Its FIRST call into `_detect.detect` (the
# wait) writes the `parent=` that call was handed, atomically, to the file
# named by argv[1]. That call comes after `listen` read its caller's pid,
# so a caller that waits for this file can never go too early.
_WAITING_CHILD = """\
import os, sys
from pathlib import Path
from sessionmail import _detect, _seat
from sessionmail.pager import cli
# No real `ps` in this child (the rig stubs `_seat._ps_table` the same way,
# in-process). Kept for TIMING, not isolation: since SM-558 the second-arm
# guard's walk ends at the nearest claude, which carries the running seat's
# id and not this test's, so a seat's live `pager listen` is no longer found
# here either way (the other child tests below run on the REAL `ps` and prove
# that). D measures the caller watch, and SM-150 fix-003 measured it 0/120 red
# with this stub; taking `ps` out of startup removes a variable D does not
# test. The guard still reads the TEST's stamp, under the rig's
# SESSIONMAIL_HOME; the caller watch reads `os.getppid()`, never `ps`.
_seat._ps_table = lambda: ({}, {})
marker = Path(sys.argv.pop(1))
real_detect = _detect.detect
def detect(*args, **kwargs):
    if not marker.exists():
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(str(kwargs.get("parent")))
        os.replace(tmp, marker)
    return real_detect(*args, **kwargs)
_detect.detect = detect
cli()
"""


def test_the_caller_going_ends_the_wait(rig, sid, tmp_path):
    """specs/pager "No children, and it watches itself": measured with a
    real parent that exits.

    The caller goes on the EVENT "the listener is waiting" (its first call
    into the detector, recorded by `_WAITING_CHILD`), never after a fixed
    sleep (SM-150). `sleep 0.3` lost whenever startup took longer under
    load: the caller was gone before the listener read its parent pid, so
    the orphan took its new parent for its caller and waited for ever.
    The `listening` stamp was NOT a safe event (rca-001 §4 proposed it):
    at d0b9935 `os.getppid()` was still read after the stamp; measured
    5/30 red at load 10-20 with the caller going on the stamp (SM-150
    evidence/fix-001). Stronger than it was: the pid the
    listener watches is the caller's. Bounded: the shell exits 3 if the
    listener never waits. The child sees no real `ps`, as the rig does
    (SM-150 fix-003), kept for timing (see `_WAITING_CHILD`). A caller
    that went DURING startup used to orphan a listener with no `--timeout`;
    SM-150 fix-002 closed that in `src/` (the caller is read first), and
    the three tests below cover it. Its session is its own (SM-558)."""
    import subprocess, sys
    rig.box(sid)
    env = dict(os.environ, PYTHONPATH="src")
    child = tmp_path / "waiting_child.py"
    child.write_text(_WAITING_CHILD)
    pidfile, callerfile, waiting = tmp_path / "pid", tmp_path / "caller", tmp_path / "waiting"
    parent = subprocess.Popen(
        ["sh", "-c", f"echo $$ > {callerfile}; "
                     f"{sys.executable} {child} {waiting} listen --quiet & echo $! > {pidfile}; "
                     f"i=0; until [ -s {waiting} ]; do i=$((i+1)); "
                     f"[ $i -gt 1000 ] && exit 3; sleep 0.01; done; exit 0"], env=env)
    went = parent.wait(timeout=30)
    pid = int(pidfile.read_text())
    if went != 0:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:    # it already exited (e.g. it was refused):
            pass                      # say why D failed, not that the kill did
        pytest.fail(f"listener {pid} never began to wait; its caller gave up (rc {went})")
    watched = waiting.read_text()
    for _ in range(60):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail(f"listener {pid} outlived its caller by 3 s (it watched parent {watched})")
    assert watched == callerfile.read_text().strip()        # it watched ITS caller


def test_a_caller_that_goes_at_once_never_leaves_an_orphan(rig, sid, tmp_path):
    """SM-150 fix-002: a REAL caller that exits at once (`sleep 0`, gone
    long before Python reaches `listen`). Before, the listener read its
    parent pid after startup, took launchd/init for its caller and, with no
    --timeout, waited for ever. Now it reads it first, and a parent that is
    already pid 1 means the caller went: it returns and says so. Either
    way (gone before the read, or after it and caught by the wait), the
    line says the caller went. Bounded at 10 s; the old code never exits.
    On the REAL `ps`, with its own session (SM-558)."""
    import subprocess, sys
    rig.box(sid)
    env = dict(os.environ, PYTHONPATH="src")
    pidfile, errfile = tmp_path / "pid", tmp_path / "stderr"
    caller = subprocess.Popen(
        ["sh", "-c", f"{sys.executable} -c 'from sessionmail.pager import cli; cli()' "
                     f"listen --quiet 2> {errfile} & echo $! > {pidfile}; exit 0"], env=env)
    assert caller.wait(timeout=30) == 0
    pid = int(pidfile.read_text())
    for _ in range(200):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail(f"listener {pid} outlived a caller that went at once, by 10 s")
    assert "pager listen: the caller went" in errfile.read_text()


def test_an_orphan_at_start_returns_at_once_and_says_so(rig, monkeypatch):
    """SM-150 fix-002: parent already pid 1 (init / launchd) when `listen`
    starts = the caller is gone. A stated exit and one stderr line, and
    nothing born, stamped, marked or mailed for nobody."""
    rig.running()
    monkeypatch.setattr(os, "getppid", lambda: pager.ORPHAN_PARENT)
    waits = count_waits(monkeypatch)
    result = run("--timeout", "5")
    assert waits == [], f"it waited first ({len(waits)} detector call(s))"   # at once: no wait at all (SM-863)
    assert result.exit_code == pager.EX_CALLER_GONE
    assert ("pager listen: the caller went before the listener started (parent is pid 1); "
            "returning rather than waiting alone") in result.stderr
    assert result.stdout == ""
    assert not (rig.root / "sess-a").exists()                   # no box born
    assert not (_seat.stamp_dir(rig.seat, "sess-a") / "listening").exists()
    assert rig.marks() == ""


@pytest.mark.parametrize("goes_at", ["the listening stamp", "the confirm mail"])
def test_the_caller_is_read_before_the_stamp_and_the_confirm_mail(rig, monkeypatch, goes_at):
    """SM-150 fix-002: WHERE the parent pid is read. The caller goes at the
    stamp, or during the confirm mail, and the orphan is adopted by a
    reaper that is NOT pid 1 (a subreaper), so only the read's position can
    tell. Read first, the listener watches the real caller and sees it go;
    read after (it was), it would watch the reaper and wait to its timeout."""
    rig.box()
    rig.running()
    ppid = {"now": 4242}
    monkeypatch.setattr(os, "getppid", lambda: ppid["now"])

    def going(real):
        def wrapper(*args, **kwargs):
            out = real(*args, **kwargs)
            ppid["now"] = 5151                    # the caller went; a reaper adopted us
            return out
        return wrapper

    if goes_at == "the listening stamp":
        monkeypatch.setattr(_seat, "write_listening", going(_seat.write_listening))
    else:
        monkeypatch.setattr(pager, "confirm_listening", going(pager.confirm_listening))
    waits = count_waits(monkeypatch)
    result = run("--timeout", "5")
    assert ppid["now"] == 5151, "the caller never went: the seam was not reached"
    assert result.exit_code == pager.EX_CALLER_GONE, result.stderr      # not the timeout (124)
    assert "pager listen: the caller went; returning rather than waiting alone" in result.stderr
    assert len(waits) == 1, f"seen on wait {len(waits)}, not the first"   # SM-863: an event, not < 4 s


# ── carried over: version alarm, forbidden words ───────────────────────────

def test_two_copies_at_different_versions_alarm_and_it_still_arms(rig, tmp_path, monkeypatch):
    """specs/pager: "the banner names both paths and versions and the
    listener still arms"."""
    a, b = tmp_path / "bin-a", tmp_path / "bin-b"
    _copy_of_pager(a, "pager, version 1.0.0")
    _copy_of_pager(b, "pager, version 2.0.0")
    monkeypatch.setenv("PATH", f"{a}:{b}")
    rig.box()
    result = run("--timeout", "0")
    assert result.exit_code == EX_TIMEOUT, "a mismatch must never refuse"
    assert "DIVERGED" in result.stderr
    assert "1.0.0" in result.stderr and "2.0.0" in result.stderr


def test_one_copy_is_not_an_alarm(rig, tmp_path, monkeypatch):
    only = tmp_path / "bin-only"
    _copy_of_pager(only, "pager, version 1.0.0")
    monkeypatch.setenv("PATH", str(only))
    rig.box()
    assert "DIVERGED" not in run("--timeout", "0").stderr


def test_no_printable_string_in_pager_calls_a_return_a_failure():
    """specs/pager, "A fire is not a failure". Docstrings are excluded -
    this gate is about what a reader SEES."""
    source = open(pager.__file__).read()
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    offenders = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings and FORBIDDEN.search(node.value)
    ]
    assert offenders == [], offenders


def test_the_forbidden_word_gate_can_fail():
    assert FORBIDDEN.search("ARM DEAD - re-arm now")


def test_every_rendered_pager_page_is_clean_too():
    runner = CliRunner()
    pages = [runner.invoke(cli, ["-h"], prog_name="pager").output]
    pages += [runner.invoke(cli, [verb, "-h"], prog_name="pager").output
              for verb in cli.list_commands(None)]
    for page in pages:
        assert not FORBIDDEN.search(page), page


# ── pager off (Rulings 1360) ──────────────────────────────────────────────

def _spawn_listener(rig, sid):
    """A real `pager listen` child, armed on the test's own session box (SID,
    fresh per test), waited for by its stamp. It runs on the REAL `ps`, no
    stub: under a seat whose own listener is live, the second-arm guard
    walks up to that seat's claude, which carries another id, and finds
    nothing (SM-558; before, it refused on the seat's listener)."""
    import subprocess
    rig.box(sid)
    env = dict(os.environ, PYTHONPATH="src")
    proc = subprocess.Popen(
        [sys.executable, "-c", "from sessionmail.pager import cli; cli()", "listen", "--quiet"],
        env=env)
    stamp = _seat.stamp_dir(rig.seat, sid) / "listening"
    for _ in range(100):
        if stamp.exists() and stamp.read_text().strip() == str(proc.pid):
            return proc, stamp
        time.sleep(0.05)
    proc.kill()
    pytest.fail("the child listener never wrote its stamp")


def test_off_stops_this_sessions_listener_by_its_stamp_and_clears_it(rig, sid):
    proc, stamp = _spawn_listener(rig, sid)
    result = CliRunner().invoke(cli, ["off"], prog_name="pager")
    assert result.exit_code == EX_PAGE, result.output
    assert result.stdout.strip() == f"pager off: stopped listener {proc.pid} for session {sid}"
    assert proc.wait(timeout=3) is not None
    assert not stamp.exists()


def test_off_with_nothing_listening_says_so(rig):
    result = CliRunner().invoke(cli, ["off"], prog_name="pager")
    assert result.exit_code == 3
    assert result.stdout.strip() == "pager off: nothing listening for session sess-a"


def test_stop_with_nothing_listening_says_so_by_its_own_name(rig):
    """SM-726 do-008 (verify-001 F2): typed as `pager stop`, it says so."""
    result = CliRunner().invoke(cli, ["stop"], prog_name="pager")
    assert result.exit_code == 3
    assert result.stdout.strip() == "pager stop: nothing listening for session sess-a"


def test_sm410_off_marks_the_stop_deliberate_and_a_real_listen_clears_it(rig, sid, monkeypatch):
    """`pager stop` leaves the off stamp; the next real `pager listen` clears
    it when it arms, so once THAT listener is gone the Stop hook nags again."""
    monkeypatch.delenv("EMAIL", raising=False)
    proc, stamp = _spawn_listener(rig, sid)
    off = _seat.stamp_dir(rig.seat, sid) / _seat.OFF_STAMP
    assert CliRunner().invoke(cli, ["off"], prog_name="pager").exit_code == EX_PAGE
    proc.wait(timeout=3)
    assert off.exists()
    hook = CliRunner().invoke(cli, ["hook", "stop"], input="{}", prog_name="pager")
    assert hook.exit_code == 0 and hook.stderr == ""
    assert run("--timeout", "0.2", "--quiet").exit_code == 124
    assert not off.exists(), "arming must clear the deliberate-stop stamp"
    hook = CliRunner().invoke(cli, ["hook", "stop"], input="{}", prog_name="pager")
    assert hook.exit_code == 2 and hook.stderr.strip() == pager.LINE_NOT_LISTENING


def test_sm410_a_listener_killed_by_sigterm_leaves_no_off_stamp_and_is_nagged(rig, sid, monkeypatch):
    """Ruling item 3: only the `pager stop`/`off` VERB marks a stop deliberate.
    A listener's own exit path - here SIGTERM, as a closed pane sends - must
    leave no off stamp, so the seat is still told it is not listening."""
    monkeypatch.delenv("EMAIL", raising=False)
    proc, stamp = _spawn_listener(rig, sid)
    proc.terminate()
    proc.wait(timeout=3)
    assert not (_seat.stamp_dir(rig.seat, sid) / _seat.OFF_STAMP).exists()
    hook = CliRunner().invoke(cli, ["hook", "stop"], input="{}", prog_name="pager")
    assert hook.exit_code == 2 and hook.stderr.strip() == pager.LINE_NOT_LISTENING


def test_sm410_a_listener_that_fired_and_was_not_rearmed_is_nagged(rig, monkeypatch):
    """Ruling item 2: a page ends the listener with NO off stamp - the seat
    that does not re-arm is deaf, not settled, and the Stop hook says so."""
    monkeypatch.delenv("EMAIL", raising=False)
    rig.box()
    rig.deliver("sess-a", "f1@example.invalid", "fires")
    rig.stopped()
    assert run("--timeout", "3").exit_code == EX_PAGE
    assert not (_seat.stamp_dir(rig.seat, "sess-a") / _seat.OFF_STAMP).exists()
    hook = CliRunner().invoke(cli, ["hook", "stop"], input="{}", prog_name="pager")
    assert hook.exit_code == 2 and hook.stderr.strip() == pager.LINE_NOT_LISTENING


def test_off_never_touches_another_sessions_listener(rig, sid, fresh_session, monkeypatch):
    """Rules 70 by construction: the stamp is per session. Two ids, both
    this test's own, both fresh."""
    proc, stamp = _spawn_listener(rig, sid)
    other = f"{fresh_session}-b"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", other)
    result = CliRunner().invoke(cli, ["off"], prog_name="pager")
    assert result.exit_code == 3 and other in result.stdout
    assert proc.poll() is None and stamp.exists()          # sid's listener untouched
    proc.terminate()
    proc.wait(timeout=3)


def test_there_is_no_on_and_it_says_where_to_go(rig):
    """Not a verb - a refusal in the resolver, so the verb count holds."""
    result = CliRunner().invoke(cli, ["on"], prog_name="pager")
    assert result.exit_code == 2                       # click's usage error
    assert "pager listen" in result.output and "pager off" in result.output
    assert "on" not in cli.list_commands(None)


# ── the queue chain (G3): links in, the page is the queue's age ──────────

def _queue(rig, session: str = "sess-a") -> Path:
    return _seat.ride_dir(rig.seat, session) / _seat.QUEUE_NEW


def test_an_arrival_is_linked_into_this_sessions_queue_before_any_page(rig, monkeypatch):
    """D08: same inode, a second name, zero copy - and it is there for the
    ride-along long before T, while the box's new/ is untouched.

    T is put out of reach here (SM-150 rca-001 §2): the page age runs from
    the DELIVERY's mtime and the timeout from after the banner, so with
    T = 0.4 s and a 0.15 s timeout, which clock won was load's call. Now
    the only way out is the timeout, and what is asserted is the link.
    Paging AT the crossing is pinned by the neighbour below
    (`test_nobody_scooped_so_it_pages_at_the_crossing_and_moves_nothing`)."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "3600")
    rig.box()
    rig.running()
    name = rig.deliver("sess-a", "q1@example.invalid", "queued")
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    link = _queue(rig) / name
    assert link.exists()
    assert link.stat().st_ino == (rig.root / "sess-a" / "new" / name).stat().st_ino
    assert (rig.root / "sess-a" / "new" / name).exists()
    assert "queued" not in result.stdout
    assert rig.marks() == ""


def test_the_ride_along_keeps_up_so_nothing_pages(rig, monkeypatch):
    """"Empty ride/new = the ride-along is keeping up = nothing to do" (D10).

    The ride-along scoops on the EVENT "the listener linked it", inside the
    listener's own link step, never on a clock (SM-150 rca-001 §1): the
    old `later(0.15, scoop)` fired before the link under load, took
    nothing, and the link then aged past T and paged. Now the scoop always
    lands after the link and before the next age check. Stronger than it
    was: the scoop took exactly THIS name (an empty scoop makes `done/`
    too, so `done/` alone proved nothing), and the listener was still
    listening when the delivery crossed T."""
    rig.box()
    rig.running()
    name = rig.deliver("sess-a", "k1@example.invalid", "kept up")
    delivered = (rig.root / "sess-a" / "new" / name).stat().st_mtime
    real_link_into = _detect.link_into
    scooped: list[str] = []

    def link_then_scoop(arrivals, queues):
        made = real_link_into(arrivals, queues)
        taken = _seat.take_queue(rig.seat, "sess-a", os.getpid())
        scooped.extend(e.name for e in taken.entries)
        _seat.finish_queue(rig.seat, "sess-a", taken)
        return made

    monkeypatch.setattr(_detect, "link_into", link_then_scoop)
    result = run("--timeout", str(RUNNING_T + 0.5))
    assert scooped == [name]                                  # the scoop took THIS link
    assert time.time() - delivered > RUNNING_T                # listening past the crossing
    assert result.exit_code == EX_TIMEOUT, result.stdout
    assert "kept up" not in result.stdout
    assert list(_queue(rig).iterdir()) == []
    assert (_seat.ride_dir(rig.seat, "sess-a") / _seat.QUEUE_DONE).is_dir()
    assert (_seat.ride_dir(rig.seat, "sess-a") / _seat.QUEUE_DONE / name).exists()


def test_nobody_scooped_so_it_pages_at_the_crossing_and_moves_nothing(rig):
    """The ORDERING (SM-863): unscooped, the link pages by the running
    setting, and not before the delivery is T old (read off its mtime, the
    pager's own clock); the page moves nothing. How SOON after the crossing
    is the latency twin's: it went red under load with no product change
    (a full-suite run of 637888d, 2030-12-08)."""
    rig.box()
    rig.running()
    name = rig.deliver("sess-a", "n1@example.invalid", "nobody scooped")
    result = run("--timeout", LONG_TIMEOUT)
    assert result.exit_code == EX_PAGE, result.stderr
    assert paged_by_running(RUNNING_T) in result.stderr, result.stderr
    assert aged_since_delivery(rig, name) >= RUNNING_T       # not before the crossing
    assert counted(result) == 1, result.stdout
    assert (_queue(rig) / name).exists()                     # the page moves nothing
    assert re.search(r"n1@example.invalid seen alpha sess-a pager wake", rig.marks())


@pytest.mark.latency
def test_nobody_scooped_so_it_pages_at_the_crossing_and_moves_nothing_latency(rig):
    """LATENCY lane (SM-863): the original body and its window, kept
    exactly; THE pin for paging AT the crossing. Run with
    `make test-latency` on a quiet host."""
    rig.box()
    rig.running()
    name = rig.deliver("sess-a", "n1@example.invalid", "nobody scooped")
    t0 = time.monotonic()
    result = run("--timeout", "3")
    elapsed = time.monotonic() - t0
    assert result.exit_code == EX_PAGE, result.stderr
    assert RUNNING_T - 0.1 <= elapsed < RUNNING_T + 0.4, elapsed
    assert counted(result) == 1, result.stdout
    assert (_queue(rig) / name).exists()                     # the page moves nothing
    assert re.search(r"n1@example.invalid seen alpha sess-a pager wake", rig.marks())


def test_a_restart_never_relinks_what_a_scoop_has_given(rig):
    """The name is the key: a link in done/ is a giving; a fresh listener
    on the same box does not put it back in new/."""
    rig.box()
    rig.stopped()
    name = rig.deliver("sess-a", "d1@example.invalid", "given once")
    assert run("--timeout", "3").exit_code == EX_PAGE
    taken = _seat.take_queue(rig.seat, "sess-a", os.getpid())
    _seat.finish_queue(rig.seat, "sess-a", taken)
    assert (_seat.ride_dir(rig.seat, "sess-a") / _seat.QUEUE_DONE / name).exists()
    again = run("--timeout", "0.3")
    assert again.exit_code == EX_TIMEOUT, again.stdout
    assert list(_queue(rig).iterdir()) == []
    assert "0 waiting, 1 given" in again.stderr


def test_age_is_the_delivery_so_a_stale_message_linked_now_pages_at_once(rig, monkeypatch):
    """The ORDERING (SM-863): the age that decides is the DELIVERY's, not
    the link's. T is an hour and the timeout 30 s, so a link aged from now
    could only time out (124); a delivery 100 s older than T, linked now,
    pages by the running setting on its first look."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(OUT_OF_REACH_T))
    rig.box()
    rig.running()
    name = rig.deliver("sess-a", "s1@example.invalid", "stale")
    old = time.time() - (OUT_OF_REACH_T + 100)
    os.utime(rig.root / "sess-a" / "new" / name, (old, old))
    result = run("--timeout", LONG_TIMEOUT)
    assert result.exit_code == EX_PAGE and counted(result) == 1, result.stderr
    assert paged_by_running(OUT_OF_REACH_T) in result.stderr, result.stderr


@pytest.mark.latency
def test_age_is_the_delivery_so_a_stale_message_linked_now_pages_at_once_latency(rig):
    """LATENCY lane (SM-863): the original body and its 0.3 s bound, kept
    exactly (SM-150: never loosen the 0.3 s bound). Run with
    `make test-latency` on a quiet host."""
    rig.box()
    rig.running()
    name = rig.deliver("sess-a", "s1@example.invalid", "stale")
    old = time.time() - 100
    os.utime(rig.root / "sess-a" / "new" / name, (old, old))
    t0 = time.monotonic()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE and counted(result) == 1
    assert time.monotonic() - t0 < 0.3


def test_a_stop_mid_wait_pages_the_queue_inside_one_poll(rig, monkeypatch):
    """The ORDERING (SM-863), on the queue: the stop lands on the event
    "the listener has waited once with this link not yet due", and with the
    running age out of reach only the re-read idle verdict can page it."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(OUT_OF_REACH_T))
    rig.box()
    rig.running()
    rig.deliver("sess-a", "m1@example.invalid", "then stopped")
    stopped = on_first_wait(monkeypatch, rig.stopped)
    result = run("--timeout", LONG_TIMEOUT)
    assert stopped == [True], "the seat never stopped mid-wait"
    assert result.exit_code == EX_PAGE and counted(result) == 1, result.stderr
    assert PAGED_BY_IDLE in result.stderr, result.stderr


@pytest.mark.latency
def test_a_stop_mid_wait_pages_the_queue_inside_one_poll_latency(rig, monkeypatch):
    """LATENCY lane (SM-863): the original body and its 0.6 s bound, kept
    exactly. Run with `make test-latency` on a quiet host."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "5")
    rig.box()
    rig.running()
    rig.deliver("sess-a", "m1@example.invalid", "then stopped")
    later(0.2, rig.stopped)
    t0 = time.monotonic()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE and counted(result) == 1
    assert time.monotonic() - t0 < 0.6


def test_the_banner_names_the_queue(rig):
    rig.box()
    rig.stopped()
    rig.deliver("sess-a", "b2@example.invalid")
    err = run("--timeout", "3").stderr
    assert f"queue      : {_seat.ride_dir(rig.seat, 'sess-a')} - 0 waiting, 0 given" in err


def test_the_page_says_which_stamp_chose_t(rig):
    rig.box()
    rig.stopped()
    rig.deliver("sess-a", "t1@example.invalid", "stopped")
    assert "pager listen: paged by the idle verdict (T=0s)" in run("--timeout", "3").stderr
    rig.running()
    rig.deliver("sess-a", "t2@example.invalid", "running")
    assert f"pager listen: paged by the running setting (T={RUNNING_T:g}s)" in run("--timeout", "3").stderr
    # ... and the marks file says it too, in the reason, for the morning after
    marks = rig.marks()
    assert re.search(r"t1@example.invalid seen alpha sess-a pager wake \(idle T=0s\)", marks)
    assert re.search(rf"t2@example.invalid seen alpha sess-a pager wake \(running T={RUNNING_T:g}s\)", marks)


# ── F19: the session's own box is born by its first listen ────────────────

FAKE_GEN = """#!{python}
import os, sys, time
open(os.environ["GEN_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("GEN_HANG"):
    time.sleep(30)
if os.environ.get("GEN_FAIL"):
    sys.stderr.write("vmailbox-gen: error: refusing\\n"); sys.exit(2)
"""


def _gen(tmp_path, monkeypatch):
    from fakes import write_fake
    log = tmp_path / "gen.log"
    script = write_fake(tmp_path / "fake-vmailbox-gen", FAKE_GEN)
    monkeypatch.setenv(_seat.GEN_VAR, str(script))
    monkeypatch.setenv("GEN_LOG", str(log))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))    # no postmap: not measured
    return log


def test_a_fresh_sessions_box_is_born_by_its_first_listen_and_the_generator_runs_once(rig, tmp_path, monkeypatch):
    """F19 (SM-70, Sun 2030-06-09): after G4 the retired arm was the
    only mkdir and the only regen. The first listen births the box and mints
    the line; a re-arm on an existing box runs no generator."""
    log = _gen(tmp_path, monkeypatch)
    assert not (rig.root / "sess-a").exists()
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert (rig.root / "sess-a" / "new").is_dir() and (rig.root / "sess-a" / "cur").is_dir()
    assert f"born box   : {rig.root / 'sess-a'}" in result.stderr
    assert log.read_text().splitlines() == [f"--root {rig.root.parent}"]    # WHOLE: both maps (F21)
    assert "doorbell   : not measured: no postconf/postmap" in result.stderr
    assert "generator ran in" in result.stderr
    again = run("--timeout", "0.1")
    assert "born box" not in again.stderr
    assert len(log.read_text().splitlines()) == 1


# ── SM-674: no MagicDNS suffix - listen anyway, regenerate nothing, say so ──

UNKNOWN_LINE = ("MagicDNS suffix unknown: set MAGICDNS_SUFFIX or start Tailscale; "
                "maps NOT regenerated")


def _no_suffix(tmp_path, monkeypatch):
    """No MAGICDNS_SUFFIX, no tailscale on PATH (system dirs only, which carry
    none), the app-bundle seam pointed at nothing."""
    log = _gen(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{tmp_path / 'nothing-here'}:/usr/bin:/bin")
    monkeypatch.delenv("MAGICDNS_SUFFIX", raising=False)
    monkeypatch.setenv("MAGICDNS_TAILSCALE_BUNDLE", str(tmp_path / "no-app" / "Tailscale"))
    return log


def test_with_no_suffix_a_birth_listen_arms_names_the_fix_and_writes_no_map(rig, tmp_path, monkeypatch):
    """A host where no source answers must not have
    every seat deaf. The listen that would regenerate (a birth) skips
    the generator, says so on stderr and in the banner, and listens."""
    log = _no_suffix(tmp_path, monkeypatch)
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr          # armed, listened, timed out
    assert f"pager listen: {UNKNOWN_LINE}" in result.stderr
    assert f"  suffix     : {UNKNOWN_LINE} - listening on the existing maps" in result.stderr
    assert "generator NOT run (MagicDNS suffix unknown)" in result.stderr
    assert not log.exists(), "the generator ran with no suffix"
    assert (rig.root / "sess-a" / "new").is_dir()                  # the box is still born
    assert "Traceback" not in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_with_no_suffix_the_listener_still_pages(rig, tmp_path, monkeypatch):
    log = _no_suffix(tmp_path, monkeypatch)
    rig.deliver("sess-a", "nosuffix@example.invalid")
    result = run("--timeout", "3")
    assert result.exit_code == 0, result.stderr                    # paged
    assert "pager listen: paged by" in result.stderr
    assert not log.exists()


def test_on_a_drop_host_with_no_tailnet_the_listen_says_it_once_as_info(rig, tmp_path,
                                                                       monkeypatch):
    """The mail drop needs no suffix: no warning line, and the banner's
    suffix line is INFO with nothing to install or set."""
    from sessionmail import _birth, _magicdns
    log = _no_suffix(tmp_path, monkeypatch)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "drop")
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert "MagicDNS suffix unknown" not in result.stderr
    suffix = [ln for ln in result.stderr.splitlines() if ln.startswith("  suffix     :")]
    assert suffix == [f"  suffix     : none - {_magicdns.NO_TAILNET}"]
    for advice in ("install", "Tailscale", "MAGICDNS_SUFFIX"):
        assert advice not in suffix[0]
    assert f"; {_birth.DROP_REGEN}" in result.stderr               # the doorbell's clause only
    # No regen. The drop's doorbell (SM-908) reads the drop's own maps
    # through the generator's read-only modes, the same ones a drop send
    # runs; any other call would be a regen.
    from sessionmail import _drop
    read_only = {"--capabilities", *_drop.PRINT_MODES.values()}
    calls = log.read_text().splitlines() if log.exists() else []
    assert all(call.split()[-1] in read_only for call in calls), calls
    assert (rig.root / "sess-a" / "new").is_dir()


def test_on_a_drop_host_whose_tailscale_fails_the_listen_still_warns(rig, tmp_path, monkeypatch):
    from test_magicdns_suffix import fake_tailscale
    _no_suffix(tmp_path, monkeypatch)
    fake_tailscale(tmp_path / "nothing-here" / "tailscale", fail=True)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "drop")
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert f"pager listen: {UNKNOWN_LINE}" in result.stderr
    assert f"  suffix     : {UNKNOWN_LINE} - listening on the existing maps" in result.stderr


def test_with_a_suffix_the_birth_listen_still_regenerates(rig, tmp_path, monkeypatch):
    """The control: the same birth with MAGICDNS_SUFFIX set runs the generator
    and says nothing of an unknown suffix."""
    log = _gen(tmp_path, monkeypatch)
    monkeypatch.setenv("MAGICDNS_SUFFIX", "tail.example")
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert log.read_text().splitlines() == [f"--root {rig.root.parent}"]
    assert "MagicDNS suffix unknown" not in result.stderr


# ── F21: the alias half alone is a bounce - LIVE means a mail would land ──

FAKE_POSTCONF = """#!{python}
import os, sys
# `postconf -h PARAM`: the maps live where POSTFIX_MAPS says
maps = os.environ["POSTFIX_MAPS"]
print({{"virtual_alias_maps": "hash:" + maps + "/valias",
       "virtual_mailbox_maps": "hash:" + maps + "/vmailbox"}}[sys.argv[2]])
"""

FAKE_POSTMAP = """#!{python}
import sys
# `postmap -q KEY hash:FILE`: exact key in the TEXT file, exit 1 when absent
key, path = sys.argv[2], sys.argv[3].split(":", 1)[1]
for line in open(path):
    parts = line.rstrip("\\n").split(None, 1)
    if len(parts) == 2 and parts[0] == key:
        print(parts[1]); sys.exit(0)
sys.exit(1)
"""


def _postfix(tmp_path, monkeypatch, alias: bool, mailbox: bool):
    """A fake postconf/postmap pair on PATH answering from two text maps: the
    alias line for alpha@<host> when ALIAS, the mailbox line for sess-a@<host>
    when MAILBOX. Returns the host postfix keys are folded to."""
    import socket
    from fakes import write_fake
    host = socket.gethostname().split(".")[0].lower()
    bin_dir = tmp_path / "postfix-bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "postconf", FAKE_POSTCONF)
    write_fake(bin_dir / "postmap", FAKE_POSTMAP)
    maps = tmp_path / "postfix-maps"
    maps.mkdir()
    (maps / "valias").write_text(f"alpha@{host}\talpha@{host}, sess-a@{host}\n" if alias else "")
    (maps / "vmailbox").write_text(f"sess-a@{host}\tagents/sess-a/\n" if mailbox else "")
    monkeypatch.setenv("POSTFIX_MAPS", str(maps))
    monkeypatch.setenv("PATH", str(bin_dir))
    return host


def test_an_alias_line_without_a_mailbox_line_is_NOT_DELIVERABLE_and_the_generator_runs_whole(rig, tmp_path, monkeypatch):
    """F21 (measured): a cold seat's box was born
    and its alias minted by --valias-only, and every doorbell copy bounced
    5.1.1 "no mailbox mapping" for 17 minutes under a banner that said LIVE.
    The doorbell asks BOTH maps now, and a missing mailbox line runs the
    generator WHOLE."""
    log = _gen(tmp_path, monkeypatch)
    host = _postfix(tmp_path, monkeypatch, alias=True, mailbox=False)
    rig.box()                                   # an existing box: only the measure triggers the regen
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert "born box" not in result.stderr
    assert ("doorbell   : NOT DELIVERABLE - the seat's mail will not reach this box: "
            f"alpha@{host} -> this box in {tmp_path / 'postfix-maps' / 'valias'}, but no mailbox line for "
            f"sess-a@{host} in virtual_mailbox_maps - the copy would bounce 5.1.1 (F21)") in result.stderr
    assert log.read_text().splitlines() == [f"--root {rig.root.parent}"]
    assert "generator ran in" in result.stderr


def test_both_lines_present_is_LIVE_and_names_both_maps_and_runs_no_generator(rig, tmp_path, monkeypatch):
    log = _gen(tmp_path, monkeypatch)
    host = _postfix(tmp_path, monkeypatch, alias=True, mailbox=True)
    rig.box()
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert (f"doorbell   : LIVE: alpha@{host} -> this box, in {tmp_path / 'postfix-maps' / 'valias'}; "
            f"sess-a@{host} -> agents/sess-a/, in {tmp_path / 'postfix-maps' / 'vmailbox'}") in result.stderr
    assert not log.exists()


def test_no_alias_line_is_NOT_COPIED(rig, tmp_path, monkeypatch):
    _gen(tmp_path, monkeypatch)
    host = _postfix(tmp_path, monkeypatch, alias=False, mailbox=True)
    rig.box()
    result = run("--timeout", "0.1")
    assert ("doorbell   : NOT COPIED - the seat's mail will not reach this box: "
            f"no alias map postfix reads copies alpha@{host} into this box") in result.stderr


def test_a_box_named_by_hand_is_never_minted(rig, tmp_path, monkeypatch):
    """The NAMED box is never minted. The SESSION's box now is, on this listen
    too (message-subscriptions D9, /RULINGS/3160: the `own_box` condition
    went), so the generator runs once - for sess-a's birth, not for the name."""
    log = _gen(tmp_path, monkeypatch)
    result = run("someone-else", "--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK and "no new/" in result.stderr
    assert not (rig.root / "someone-else").exists()
    assert (rig.root / "sess-a" / "new").is_dir()
    assert log.read_text().splitlines() == [f"--root {rig.root.parent}"]
    again = run("someone-else", "--timeout", "1")
    assert again.exit_code == EX_COULD_NOT_LOOK
    assert len(log.read_text().splitlines()) == 1      # born once, regenerated once


def test_a_hanging_generator_is_bounded_and_announced_and_listen_still_arms(rig, tmp_path, monkeypatch):
    _gen(tmp_path, monkeypatch)
    monkeypatch.setenv("GEN_HANG", "1")
    monkeypatch.setenv(_seat.REGEN_BUDGET_VAR, "0.3")
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT
    # Bounded is the EVENT "the budget fired and the hang was sent TERM"
    # (SM-863), not "back inside 3 s": an unbounded 30 s hang says neither.
    assert "generator TIMED OUT after 0.3s" in result.stderr
    assert "sent TERM" in result.stderr, result.stderr
    assert (rig.root / "sess-a" / "new").is_dir()


def test_a_failing_generator_is_named_not_hidden(rig, tmp_path, monkeypatch):
    _gen(tmp_path, monkeypatch)
    monkeypatch.setenv("GEN_FAIL", "1")
    result = run("--timeout", "0.1")
    assert "generator exit 2" in result.stderr and "refusing" in result.stderr


def test_a_missing_generator_is_named_not_hidden(rig, tmp_path, monkeypatch):
    monkeypatch.setenv(_seat.GEN_VAR, str(tmp_path / "no-such-gen"))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    result = run("--timeout", "0.1")
    assert "generator not found" in result.stderr and "NOT written" in result.stderr
    assert (rig.root / "sess-a" / "new").is_dir()


def test_the_page_names_the_rearm_first_and_the_manual_recipe_only_as_fallback():
    """A seat re-armed and ran `message list` in the same batch. The page
    now says it plainly: run `pager listen` and the messages it names ride
    along; run `message list` only if that does not happen. The
    old item 4 offered the manual recipe unconditionally, right after item 3
    said "wait rather than act"; a fresh seat read the recipe as the next
    step, every session."""
    line = pager.PAGE_COMMANDS
    assert line.index("pager listen") < line.index("message list"), line
    assert "ride along" in line, line
    assert "Only if" in line, line
    assert line.index("Only if") < line.index("message list"), line


def test_the_page_quotes_the_arm_instruction_it_does_not_restate_it():
    """The re-arm wording comes from ONE source so the Stop hook and the pages never
    drift. `hook_lines.toml`, PAGE_WAKE and PAGE_NOTHING already splice
    ARM_HOW; the new PAGE_COMMANDS said it in its own words. Now it is
    composed from ARM_HOW too, so an edit to arm.txt changes all four."""
    assert pager.ARM_HOW in pager.PAGE_COMMANDS, pager.PAGE_COMMANDS
    assert pager.PAGE_COMMANDS.startswith("pager: " + pager.ARM_HOW), pager.PAGE_COMMANDS


def test_sm450_the_commands_are_not_the_hooks_listen_pages_with_the_hooks_off(rig):
    """SM-450 "The commands are not the hooks" (Q1c, PROPOSED): the hooks off
    since the session began - the ride-along ran and said nothing - and
    `pager listen` still pages, saying the ride-along has not run."""
    settings = Path(os.environ["HOME"]) / _settings.RELATIVE
    settings.write_text('{"version": 1, "hooks": false}\n')
    rig.box()
    rig.deliver("sess-a", "q1c@example.invalid", "hooks off")
    rig.stopped()
    hook = CliRunner().invoke(cli, ["hook", "batch"], input="{}", prog_name="pager")
    assert hook.exit_code == 0 and hook.stdout == ""
    page = run("--timeout", "3")
    assert page.exit_code == EX_PAGE
    assert "has not run for this session" in _ride_line(page)


# ── message-subscriptions 4.5-4.7 (design D8, D9): no BOX; $MAIL; birth ──
#
# specs/pager "listen takes one box" (openspec/changes/message-subscriptions/
# specs/pager/spec.md:64-92): the box is the session's, else $MAIL (a
# maildir), else exit 2 naming both. EVERY session listen births the box
# (/RULINGS/3160: 3 of 8 live sessions had none, all launched with
# `pager listen <store or project>`). BOX keeps ONE release of grace.

GRACE = "run message subscription add"


def test_a_box_argument_births_the_session_box_and_says_the_one_line(rig, monkeypatch):
    """4.6 + 4.7, WATCHED RED FIRST at c89420a: `pager listen acmecorp` in a
    session left the session boxless - the own_box condition minted only on
    the bare form. Spec scenario "A box argument, in the release of grace"."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-c")
    rig.box("acmecorp")
    assert not (rig.root / "sess-c").exists()
    result = run("acmecorp", "--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert "box " + str(rig.root / "acmecorp") in result.stderr        # it watches acmecorp
    for d in ("cur", "new", "tmp"):
        assert (rig.root / "sess-c" / d).is_dir(), d                  # and sess-c is born
    assert result.stderr.count(GRACE) == 1, result.stderr             # the ONE line, once
    assert f"{GRACE} acmecorp" in result.stderr
    assert "message subscription add" not in result.stdout            # stderr only: the page is the page


def test_the_bare_listen_prints_no_grace_line(rig):
    rig.box()
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert "subscription add" not in result.output + result.stderr


def test_a_box_argument_that_cannot_be_found_still_births_the_session_box(rig, monkeypatch):
    """Every SESSION listen births the box, even one whose BOX then refuses:
    the birth is the session's, not the argument's. The named box is still
    never minted (a box named by hand is not this listen's to make)."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-d")
    result = run("nowhere", "--timeout", "0.1")
    assert result.exit_code == EX_COULD_NOT_LOOK and "no new/" in result.stderr
    assert (rig.root / "sess-d" / "new").is_dir()
    assert not (rig.root / "nowhere").exists()
    assert result.stderr.count(GRACE) == 1, result.stderr


def test_an_inherited_MAIL_is_ignored_in_a_session(rig, monkeypatch):
    """Spec scenario of that name: MAIL is inherited, so an agent started from
    Judy's shell would otherwise listen on her mailbox (spine 10.1)."""
    judy = rig.box("judy")
    rig.deliver("judy", "inh1@example.invalid", "for judy, not the session")
    rig.stopped()
    monkeypatch.setenv("MAIL", str(judy))
    rig.box()                                     # sess-a, empty
    result = run("--timeout", "0.2")
    assert result.exit_code == EX_TIMEOUT, result.stderr    # Judy's mail did not page it
    assert "box " + str(rig.root / "sess-a") in result.stderr
    assert str(judy) not in result.stderr
    assert counted(result) is None


def _no_session(monkeypatch) -> None:
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_listening_as_judy_with_MAIL_and_no_session(rig, monkeypatch):
    """Spec scenario "Listening as Judy": no session id, MAIL names a maildir."""
    _no_session(monkeypatch)
    judy = rig.box("judy")
    rig.deliver("judy", "mk1@example.invalid", "for judy")
    monkeypatch.setenv("MAIL", str(judy))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert counted(result) == 1, result.stdout
    assert "box " + str(judy) in result.stderr
    assert GRACE not in result.stderr


def test_an_mbox_MAIL_is_refused_never_read(rig, monkeypatch, tmp_path):
    """Spec scenario "MAIL is not a maildir" (RULED S7)."""
    _no_session(monkeypatch)
    mbox = tmp_path / "var-mail-admin"
    mbox.write_text("From MAILER-DAEMON Thu Jan  1 00:00:00 1970\nSubject: x\n\nbody\n")
    before = mbox.read_bytes()
    monkeypatch.setenv("MAIL", str(mbox))
    result = run("--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK, result.output
    assert "MAIL must name a maildir" in result.stderr
    assert str(mbox) in result.stderr
    assert mbox.read_bytes() == before and mbox.is_file()


def test_a_MAIL_box_is_never_created(rig, monkeypatch, tmp_path):
    """D9: "A $MAIL box is never created: a human's mailbox is theirs to make.\""""
    _no_session(monkeypatch)
    missing = tmp_path / "not-yet" / "judy"
    monkeypatch.setenv("MAIL", str(missing))
    result = run("--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK, result.output
    assert "MAIL must name a maildir" in result.stderr
    assert not missing.exists() and not missing.parent.exists()


def test_neither_a_session_nor_MAIL_refuses_naming_both_in_order(rig, monkeypatch):
    _no_session(monkeypatch)
    monkeypatch.delenv("MAIL", raising=False)
    result = run("--timeout", "1")
    assert result.exit_code == EX_COULD_NOT_LOOK, result.output
    err = result.stderr
    first = err.index("CLAUDE_CODE_SESSION_ID")
    assert first < err.index("CLAUDE_SESSION_ID", first + 1) < err.index("$MAIL"), err


def test_quiet_keeps_its_word_even_in_the_release_of_grace(rig, monkeypatch):
    """--quiet is "exit code only" (design D10), so the grace line is not
    printed under it - but the session box is still born."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-q")
    rig.box("acmecorp")
    result = run("acmecorp", "--timeout", "0.1", "--quiet")
    assert result.exit_code == EX_TIMEOUT
    assert result.stderr == "" and result.output == ""
    assert (rig.root / "sess-q" / "new").is_dir()


def test_a_box_that_is_the_session_says_to_run_the_bare_verb(rig):
    """`pager listen sess-a` has nothing to subscribe to: the one line says
    the bare verb replaces it, and says it once."""
    rig.box()
    result = run("sess-a", "--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert result.stderr.count("BOX is going away") == 1, result.stderr
    assert "run the bare `pager listen`" in result.stderr
    assert "subscription add" not in result.stderr


def test_the_banner_names_the_session_box_it_bore_not_the_BOX_it_watches(rig, monkeypatch):
    """do-010 review (worker 4056cd3d): under `pager listen acmecorp` the born
    line named the WATCHED box - `born box : .../acmecorp ... this session's
    own` - which is false, and the doorbell said `this box` of a box it never
    measured. specs/pager "The banner tells the truth": the birth and the
    doorbell are the SESSION's, so they name the session's box."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-c")
    rig.box("acmecorp")
    result = run("acmecorp", "--timeout", "0.1")     # no generator: NOT COPIED
    assert result.exit_code == EX_TIMEOUT, result.stderr
    err = result.stderr
    assert f"box {rig.root / 'acmecorp'}" in err                     # it watches acmecorp
    assert f"born box   : {rig.root / 'sess-c'} (cur/new/tmp)" in err, err
    assert f"born box   : {rig.root / 'acmecorp'}" not in err, err
    bell = next(line for line in err.splitlines() if "doorbell   :" in line)
    assert "NOT COPIED" in bell and "reach this box" not in bell, bell
    assert f"will not reach this session's box {rig.root / 'sess-c'}" in bell, bell


def test_with_no_session_the_grace_line_names_MAIL_not_subscription_add(rig, monkeypatch):
    """A ruling on the do-010 review: `message subscription add` defaults to the SESSION, which a human at a
    shell lacks. Design D8 (openspec/changes/message-subscriptions/design.md:
    270-272 @ aee394a) names the human's replacement: set MAIL to the maildir
    and run the bare verb. So with no session id the ONE line names
    `MAIL=<that box path> pager listen` instead. WATCHED RED at aee394a."""
    _no_session(monkeypatch)
    monkeypatch.delenv("MAIL", raising=False)
    judy = rig.box("judy")
    result = run("judy", "--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    err = result.stderr
    assert "box " + str(judy) in err                                  # BOX still honoured
    assert err.count("BOX is going away") == 1, err                  # the ONE line, once
    assert f"MAIL={judy} pager listen" in err, err                   # the human's replacement
    assert "subscription add" not in err, err                        # never the session's verb
    assert "BOX is going away" not in result.stdout


# ── do-012: two banner nits from the do-010 review (reviews/2030-12-08T1749-
# do-010-peer-review-2.md:100-113), folded in where HEARS retires ──────

def test_with_no_session_a_relative_BOX_gives_an_absolute_MAIL(rig, monkeypatch):
    """Observation A: `pager listen mail/judy` from the directory holding it
    printed `MAIL=mail/judy pager listen` - a value that works only from
    there. Pasted into a profile, it later refuses. The line names the
    ABSOLUTE path. WATCHED RED at b1a283f."""
    _no_session(monkeypatch)
    monkeypatch.delenv("MAIL", raising=False)
    judy = rig.box("judy")
    monkeypatch.chdir(rig.root.parent)
    result = run(f"{rig.root.name}/judy", "--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    line = next(l for l in result.stderr.splitlines() if "BOX is going away" in l)
    assert line.endswith(f"MAIL={judy.absolute()} pager listen"), line
    assert f"MAIL={rig.root.name}/judy" not in line, line


def test_under_BOX_the_not_measured_doorbell_names_the_session_box(rig, monkeypatch, tmp_path):
    """Observation B: aee394a made the NOT COPIED branch name the session's
    box under `pager listen BOX`; the not-measured branch still said `this
    box` (in the generator's clause) of a box the header line does not
    name. Every branch of the doorbell line names the session's box.
    WATCHED RED at b1a283f."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-d")
    monkeypatch.setenv(_seat.GEN_VAR, str(tmp_path / "no-such-gen"))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))     # no postmap: not measured
    rig.box("acmecorp")
    result = run("acmecorp", "--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    bell = next(l for l in result.stderr.splitlines() if "doorbell   :" in l)
    assert bell.startswith("  doorbell   : not measured:"), bell
    assert "this box" not in bell, bell
    assert f"this session's box {rig.root / 'sess-d'}" in bell, bell


def test_with_no_BOX_the_doorbell_still_says_this_box(rig, monkeypatch, tmp_path):
    """The bare verb watches the session's box, so `this box` is true there
    and stays: the rename is only for a BOX in its release of grace."""
    monkeypatch.setenv(_seat.GEN_VAR, str(tmp_path / "no-such-gen"))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    result = run("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    bell = next(l for l in result.stderr.splitlines() if "doorbell   :" in l)
    assert "this box's alias and mailbox lines NOT written" in bell, bell
    assert "this session's box" not in bell, bell
