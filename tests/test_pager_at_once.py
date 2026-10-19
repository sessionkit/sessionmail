"""An honoured urgent given `wake` pages a RUNNING seat AT ONCE: SM-474 do-011
(design A4, unheld by ruling P-3).

`Verdict.at_once` (do-005) is the judgement's word that a mail is an honoured
urgent given `wake`. Until now the listener ignored it: a wake on a busy seat
waited SESSIONMAIL_PAGE_AGE_SECONDS for a tool call to carry it. Now an
`at_once` mail pages at T = 0, busy or not. WHY: urgent is the BACKSTOP for a
broken ride-along (hooks disabled, a ride-along bug) - the ride-along cannot
be trusted to be the thing that delivers the one mail that must not wait. It
still rides along too. Every other mail keeps today's page age, and the burst
window never holds an at_once mail back.

Both listen paths: the seated one (the queue) and the seatless one (the box's
new/). Hermetic: temp roots, a fake idle state, no live listener, no real
`~/.mail`.

Written to fail first against `60e895d` (the listener never read at_once).
"""

from __future__ import annotations

import re
import time

import pytest

from conftest import RUNNER

from sessionmail import _seat

# one rig, one runner, one seatless fixture: the judgement tests' own
from test_pager_listen_judges import (  # noqa: F401  (rig/seatless are fixtures)
    EX_PAGE, EX_TIMEOUT, FQDN, SESSION, counted, later, paged_mids, rig, run, seatless,
)
from sessionmail import pager

LONG_T = 6.0          # the running page age: far past anything a test waits for
WAIT = 1.5            # how long a mail that must NOT page is watched


@pytest.fixture(autouse=True)
def long_page_age(rig, monkeypatch, alert_runner):
    """The running page age, on a host whose mail settings name the alert
    runner (`conftest.RUNNER`, SM-798): its urgent is the honoured one."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(LONG_T))


URGENT = "Priority: urgent\r\n"


def deliver_urgent(rig, mid, *, sender, box=SESSION, to=SESSION):
    return rig.deliver(mid, sender=sender, to=to, box=box, extra=URGENT)


# ── the seated path (the queue) ──────────────────────────────────────────────

def test_a_running_seat_is_paged_at_once_by_an_honoured_urgent(rig):
    rig.box()
    rig.running()
    later(0.2, lambda: deliver_urgent(rig, "u1@x.invalid", sender=f"{RUNNER}@{FQDN}"))
    t0 = time.monotonic()
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert time.monotonic() - t0 < 2.0, "T = 0, not the running page age"
    assert counted(result) == 1
    assert paged_mids(rig) == {"u1@x.invalid"}


def test_the_urgent_that_pages_at_once_is_still_in_the_queue_for_the_ride_along(rig):
    rig.box()
    rig.running()
    later(0.2, lambda: deliver_urgent(rig, "u2@x.invalid", sender=f"{RUNNER}@{FQDN}"))
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, result.stderr
    assert len(rig.queued()) == 1, "it still rides along too"


def test_judys_urgent_pages_a_running_seat_at_once_by_wake_bang(rig):
    rig.box()
    rig.running()
    later(0.2, lambda: deliver_urgent(rig, "u3@x.invalid", sender=f"judy@{FQDN}"))
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"u3@x.invalid"}


def page_line(result) -> str:
    """The PAGE line itself ("pager listen: paged at once by ..."), not the
    banner: the banner carries a fixed "at once" line of its own (N2 of the
    do-011 review), so an assert on all of stderr is satisfied by it alone."""
    lines = [ln for ln in result.stderr.splitlines() if ln.startswith("pager listen: paged")]
    assert len(lines) == 1, result.stderr
    return lines[0]


def test_the_page_says_it_was_at_once_and_why(rig):
    rig.box()
    rig.running()
    later(0.2, lambda: deliver_urgent(rig, "u4@x.invalid", sender=f"{RUNNER}@{FQDN}"))
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, result.stderr
    page = page_line(result)
    assert "at once" in page and "urgent" in page, result.stderr
    assert "T=0s" in page
    assert "urgent at once" in rig.marks(), "the marks row names the reason"


def test_a_running_seat_waits_the_page_age_for_a_normal_direct_mail(rig):
    rig.box()
    rig.running()
    later(0.2, lambda: rig.deliver("n1@x.invalid"))             # direct: wake to:$me
    result = run("--timeout", str(WAIT))
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert counted(result) is None


def test_an_urgent_from_a_sender_not_on_the_list_waits_the_page_age(rig):
    """N3: anyone else's urgent counts as normal, so it gets normal timing."""
    rig.box()
    rig.running()
    later(0.2, lambda: deliver_urgent(rig, "u5@x.invalid", sender=f"beta@{FQDN}"))
    result = run("--timeout", str(WAIT))
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert counted(result) is None


def test_a_non_urgent_mail_from_judy_is_not_at_once_but_keeps_its_own_wake(rig):
    """Judy's `wake!` is unchanged, and it is not `at_once` without a
    `Priority: urgent`: it waits the running page age like any wake."""
    rig.box()
    rig.running()
    later(0.2, lambda: rig.deliver("m2@x.invalid", sender=f"judy@{FQDN}"))
    result = run("--timeout", str(WAIT))
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


def test_a_normal_mail_waiting_beside_the_urgent_is_carried_by_the_page(rig):
    """The page is the doorbell for the queue: the waiting wake rings with it."""
    rig.box()
    rig.running()
    rig.deliver("n2@x.invalid")
    later(0.3, lambda: deliver_urgent(rig, "u6@x.invalid", sender=f"{RUNNER}@{FQDN}"))
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert counted(result) == 2, result.stdout


def test_an_urgent_already_in_the_box_at_arm_pages_at_once(rig):
    rig.box()
    rig.running()
    deliver_urgent(rig, "u7@x.invalid", sender=f"{RUNNER}@{FQDN}")
    t0 = time.monotonic()
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert time.monotonic() - t0 < 2.0


def test_the_burst_window_does_not_hold_back_an_urgent(rig, monkeypatch):
    """`wakeable`: a mail whose address already woke this seat inside the burst
    window rides the desk line - a normal one. An at_once mail is never held."""
    rig.box()
    rig.running()
    monkeypatch.setattr(_seat, "in_burst", lambda *a, **k: True)
    later(0.2, lambda: deliver_urgent(rig, "u8@x.invalid", sender=f"{RUNNER}@{FQDN}"))
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"u8@x.invalid"}


def test_the_burst_window_still_holds_back_a_normal_mail(rig, monkeypatch):
    rig.box()
    rig.running()
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "0.3")
    monkeypatch.setattr(_seat, "in_burst", lambda *a, **k: True)
    later(0.2, lambda: rig.deliver("n3@x.invalid"))
    result = run("--timeout", "1.5")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


def test_an_idle_seat_is_paged_as_before_by_an_urgent(rig):
    """Nothing changes for an idle seat: T was already 0. The page does not
    claim 'at once' as a NEW behaviour; it is paged by the idle verdict."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: deliver_urgent(rig, "u9@x.invalid", sender=f"{RUNNER}@{FQDN}"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert "paged by the idle verdict" in result.stderr


def test_an_urgent_already_given_by_the_ride_along_does_not_page_again(rig):
    """At once is a backstop for a ride-along that did NOT deliver. One it did
    deliver is `given`, and paging it would be the page for a mail already seen."""
    rig.box()
    rig.running()
    deliver_urgent(rig, "u10@x.invalid", sender=f"{RUNNER}@{FQDN}")
    from datetime import datetime
    _seat.write_mark(rig.seat, "u10@x.invalid", "seen", SESSION,
                     datetime.now().astimezone().isoformat(timespec="seconds"), "ride-along")
    result = run("--timeout", str(WAIT))
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


# ── the seatless path (no queue): the same, from the box's own new/ ─────────

def test_the_seatless_path_pages_an_honoured_urgent_at_once(rig, seatless):
    later(0.2, lambda: deliver_urgent(rig, "s1@x.invalid", sender=f"{RUNNER}@{FQDN}",
                                      box="human", to="human"))
    t0 = time.monotonic()
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert time.monotonic() - t0 < 2.0, "T = 0, not the running page age"
    assert "at once" in page_line(result), result.stderr


def test_the_seatless_path_holds_a_normal_mail_for_the_page_age(rig, seatless):
    later(0.2, lambda: rig.deliver("s2@x.invalid", box="human", to="human"))
    result = run("--timeout", str(WAIT))
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


def test_the_seatless_path_holds_an_urgent_from_a_stranger_for_the_page_age(rig, seatless):
    later(0.2, lambda: deliver_urgent(rig, "s3@x.invalid", sender=f"beta@{FQDN}",
                                      box="human", to="human"))
    result = run("--timeout", str(WAIT))
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


def test_the_seatless_burst_window_does_not_apply(rig, seatless):
    """No seat, no burst memory: `wakeable` returns every entry - an at_once
    mail in particular. Pinned so the two paths cannot drift."""
    later(0.2, lambda: deliver_urgent(rig, "s4@x.invalid", sender=f"judy@{FQDN}",
                                      box="human", to="human"))
    result = run("--timeout", "4")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
