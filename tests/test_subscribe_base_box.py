"""SM-690: a seat's own project BASE box, born flat, reads as seat-owned.

The incident: a project's super seat was started with
`SESSIONMAIL_SUBSCRIBE=sessionvoice`, and the subscription was REFUSED -
"sessionvoice is sessionvoice's own address, and a seat's mail is that
seat's to grant". settings+super, started the same way, was accepted. The
difference is where each project's base box was born: sessionvoice's FLAT,
at `~/.mail/agents/sessionvoice`; settings' NESTED, at
`~/.mail/agents/acmecorp/settings` under a marked store.

`_subscribe._seat_owned` calls a box that resolves flat under the mail root
with no store marker a seat's own (`return found.name`), and `Caller.owns`
has no exception for the BASE of the caller's own `<base>+...` address. So
the project's own super, whose address is `<project>+super`, is told its
project box is "another seat's".

repro-001 reproduced it through both doors that hold D5's gate: the verb
(`message subscription add`) and the launcher's birth (`SESSIONMAIL_SUBSCRIBE`,
gated in `_birth.birth_subscriptions` by `_subscribe.check_box`). fix-001
put the base of the caller's own plus address in `Caller.owns` (README
Objective item 1) and a repair line in every D5 refusal (item 2): the exact
`message subscription` line a privileged seat runs, the host it must run ON,
and that a grant is HOST-LOCAL. These are item 3's regression tests. The
controls (a NESTED base box; a flat box that is NOT the caller's base; a
sibling plus seat; a seat with no plus; a session with no seat) say what
the fix must NOT open.

Runs over the synthetic fleet of `test_message_subscription`: never the
live map."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _seatmap import session_env, session_live_path
from sessionmail import _seat, _subscribe, pager
from sessionmail.message import EX_REFUSED
from test_message_subscription import (HOST, SID, _maildir, as_seat,  # noqa: F401
                                       fleet, make_live, rows, sub)
from test_pager_subscribe_at_birth import EX_TIMEOUT, NEW, banner, listen

#: The project whose base box was born flat on hq (the incident's).
FLAT = "sessionvoice"
#: The fleet's nested project, under the marked store `st` (the control:
#: settings on hq was nested, and accepted).
NESTED = "voicemode"

#: The words every D5 refusal carries since fix-001 (README Objective item 2).
HOST_LOCAL = "a grant is HOST-LOCAL"


def seat_of(address: str) -> _seat.Seat:
    """A seat built by hand: the address is all `Caller.owns` reads."""
    return _seat.Seat(address, "test", Path("/nonexistent"))


def repaired(text: str, line: str) -> None:
    """TEXT carries the exact repair LINE, the host it runs ON, and HOST-LOCAL."""
    assert f"which a privileged seat runs ON {HOST}: {line} (" in text, text
    assert HOST_LOCAL in text, text


@pytest.fixture
def flat_base(fleet):
    """The fleet, plus a project base box born FLAT under the mail root,
    with no store marker: `~/.mail/agents/sessionvoice` on hq."""
    _maildir(fleet.agents / FLAT)
    assert not (fleet.agents / FLAT / ".tm-store").exists()
    return fleet


# ── the verb: `message subscription add` ─────────────────────────────────

def test_the_projects_super_may_subscribe_its_flat_born_base_box(flat_base, monkeypatch):
    """Objective item 1: `<project>+super` owns its project box by design,
    wherever that box was born."""
    as_seat(monkeypatch, f"{FLAT}+super", SID)
    done = sub("add", FLAT)
    assert done.exit_code == 0, done.output
    assert FLAT in flat_base.subscribed(SID)


def test_the_seat_owned_words_no_longer_name_the_callers_own_base(flat_base, monkeypatch):
    """repro-001 pinned the incident's words ("sessionvoice is sessionvoice's
    own address"); they are gone for the project's own super, and the row
    postfix holds now names its session."""
    as_seat(monkeypatch, f"{FLAT}+super", SID)
    done = sub("add", FLAT)
    assert done.exit_code == 0, done.output
    assert "own address" not in done.output, done.output
    assert SID in rows(flat_base.valias).get(f"{FLAT}@{HOST}", ""), flat_base.valias


def test_any_plus_seat_owns_its_base_not_just_super(flat_base, monkeypatch):
    """Grammar only (`_seat.split_plus`): a task seat `<base>+<task>.<role>`
    owns `<base>` as `+super` does; sessionmail gives `super` no meaning."""
    as_seat(monkeypatch, f"{FLAT}+sv-12.foreman", SID)
    done = sub("add", FLAT)
    assert done.exit_code == 0, done.output
    assert FLAT in flat_base.subscribed(SID)


def test_a_tag_of_the_callers_own_flat_base_is_accepted(flat_base, monkeypatch):
    """A tag nobody sits at lands in the base (`sub_resolve`), so it is the
    base's to grant, and the base is the caller's."""
    as_seat(monkeypatch, f"{FLAT}+super", SID)
    done = sub("add", f"{FLAT}+notes")
    assert done.exit_code == 0, done.output


def test_control_a_nested_base_box_is_accepted(flat_base, monkeypatch):
    """The control: the same seat shape over a base box NESTED in a marked
    store (settings on hq) is accepted today."""
    as_seat(monkeypatch, f"{NESTED}+super", SID)
    done = sub("add", NESTED)
    assert done.exit_code == 0, done.output
    assert NESTED in flat_base.subscribed(SID)


def test_control_a_flat_box_that_is_not_the_callers_base_stays_refused(flat_base, monkeypatch):
    """What the fix must NOT open: a seat whose base is some OTHER name is
    still refused a flat box nobody gave it."""
    as_seat(monkeypatch, f"{NESTED}+super", SID)
    done = sub("add", FLAT)
    assert done.exit_code == EX_REFUSED, done.output
    assert "that seat's to grant" in done.stderr
    assert flat_base.files() == []


def test_control_a_sibling_plus_seat_stays_refused(flat_base, monkeypatch):
    """Sharing a base is not owning a sibling: `voicemode+vm-2274` owns
    `voicemode`, never the declared seat `voicemode+super`."""
    as_seat(monkeypatch, f"{NESTED}+vm-2274", SID)
    done = sub("add", f"{NESTED}+super")
    assert done.exit_code == EX_REFUSED, done.output
    assert f"{NESTED}+super is {NESTED}+super's own address" in done.stderr, done.stderr
    assert flat_base.files() == []


def test_control_a_seat_with_no_plus_gains_no_base(flat_base, monkeypatch):
    """A bare seat (`worker`) has no plus address, so no base: a flat box
    that is not its own stays refused."""
    as_seat(monkeypatch, "worker", SID)
    done = sub("add", FLAT)
    assert done.exit_code == EX_REFUSED, done.output
    assert f"{FLAT} is {FLAT}'s own address" in done.stderr, done.stderr


# ── the gate itself: `check_box` and `Caller.owns` ───────────────────────

def test_check_box_accepts_the_callers_own_flat_base(flat_base, monkeypatch):
    as_seat(monkeypatch, f"{FLAT}+super", SID)
    assert _subscribe.check_box(FLAT, _subscribe.caller()) == FLAT


@pytest.mark.parametrize("address, base", [
    (f"{FLAT}+super", FLAT),
    ("sessionmail+sm-374.foreman", "sessionmail"),
])
def test_owns_holds_the_base_of_a_plus_seat(address, base):
    who = _subscribe.Caller(seat=seat_of(address), session=SID)
    assert who.owns(base) and who.owns(base.upper())
    assert who.owns(address) and who.owns(SID)
    assert not who.owns(f"{base}+other")


def test_owns_is_unchanged_for_a_bare_seat_and_a_session_only_caller():
    bare = _subscribe.Caller(seat=seat_of("worker"), session=SID)
    assert bare.owns("worker") and bare.owns(SID)
    assert not bare.owns(FLAT)
    alone = _subscribe.Caller(seat=None, session=SID)
    assert alone.owns(SID)
    assert not alone.owns(FLAT) and not alone.owns("")


def test_a_session_only_caller_is_still_refused_a_flat_box(flat_base):
    """No seat, so no base: a session with no seat is refused a flat box,
    and the repair grants THIS session."""
    with pytest.raises(_subscribe.Denied) as denied:
        _subscribe.check_box(FLAT, _subscribe.Caller(seat=None, session=SID))
    assert f"{FLAT} is {FLAT}'s own address" in str(denied.value)
    repaired(str(denied.value), f"message subscription add {FLAT} --for {SID}")


# ── every refusal carries its repair (Objective item 2) ──────────────────

def test_the_seat_owned_refusal_carries_the_repair_line(flat_base, monkeypatch):
    """The exact line a privileged seat runs, this caller's session in it,
    on THIS host, and HOST-LOCAL (Ivan's first repair, on nas, wrote a stray
    row on the wrong host). The route to the privileged seat stays."""
    as_seat(monkeypatch, f"{NESTED}+super", SID)
    done = sub("add", FLAT)
    assert done.exit_code == EX_REFUSED, done.output
    assert "request it by mail, from a privileged seat" in done.stderr, done.stderr
    repaired(done.stderr, f"message subscription add {FLAT} --for {SID}")


def test_the_seat_refusal_carries_the_seat_repair_line(flat_base, monkeypatch):
    as_seat(monkeypatch, "worker", SID)
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == EX_REFUSED, done.output
    repaired(done.stderr, "message subscription add atomnote --seat --for worker")


@pytest.mark.parametrize("extra, line", [
    (["--for", "ivan"], "message subscription add atomnote --for ivan"),
    (["--for", "ivan", "--seat"], "message subscription add atomnote --seat --for ivan"),
])
def test_the_for_refusal_carries_the_callers_own_line(flat_base, extra, line):
    """`--for` from an unprivileged seat: the repair is the same line, run
    by a privileged seat on this host."""
    done = sub("add", "atomnote", *extra)
    assert done.exit_code == EX_REFUSED, done.output
    repaired(done.stderr, line)


def test_the_remove_refusal_names_the_shape_never_a_guess(flat_base):
    """`remove`'s gate is asked before the verb names the box to it: the
    repair says `add|remove BOX`, never the wrong verb or box."""
    done = sub("remove", "atomnote", "--seat")
    assert done.exit_code == EX_REFUSED, done.output
    repaired(done.stderr, "message subscription add|remove BOX --seat --for worker")


@pytest.mark.parametrize("who, line", [
    (_subscribe.Caller(seat=None, session=""), "message subscription add b --for SESSION-ID"),
    (_subscribe.Caller(seat=seat_of("x+y"), session=""),
     "message subscription add b --seat --for x+y"),
])
def test_repair_with_no_session(who, line):
    """No session to grant: a seat's grant, else a placeholder; never blank."""
    repaired(_subscribe.repair(who, "b"), line)


# ── the launcher's birth: SESSIONMAIL_SUBSCRIBE ──────────────────────────

@pytest.fixture
def fresh_super(flat_base, tmp_path, monkeypatch):
    """A live `sessionvoice+super` session NEW whose box is not born yet,
    launched with SESSIONMAIL_SUBSCRIBE=sessionvoice (what `tm kick` set)."""
    make_live(tmp_path, NEW, f"{FLAT}+super")
    as_seat(monkeypatch, f"{FLAT}+super", NEW)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, FLAT)
    session_env(NEW, FLAT, flat_base.userhome)
    assert not (flat_base.agents / NEW).exists()
    return flat_base


def test_the_supers_birth_subscription_to_its_flat_base_is_routed(fresh_super):
    """The incident's own door: the kick's SESSIONMAIL_SUBSCRIBE=<project>
    is applied at birth, and the project's row names the super's session."""
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    (line,) = banner(result)
    assert "REFUSED" not in line, line
    assert FLAT in fresh_super.subscribed(NEW)
    assert NEW in rows(fresh_super.valias).get(f"{FLAT}@{HOST}", ""), fresh_super.valias


def test_the_supers_birth_subscription_is_not_turned_off(fresh_super):
    """repro-001 saw the birth write `false` into the verbs' file; the
    project's own super's birth writes nothing there now."""
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    live = session_live_path(NEW, fresh_super.userhome)
    assert not live.exists() or FLAT not in json.loads(live.read_text()).get(
        "subscriptions", {}), live.read_text()


@pytest.fixture
def fresh_other(flat_base, tmp_path, monkeypatch):
    """A live `voicemode+super` session NEW, launched with
    SESSIONMAIL_SUBSCRIBE=sessionvoice: a flat box that is NOT its base."""
    make_live(tmp_path, NEW, f"{NESTED}+super")
    as_seat(monkeypatch, f"{NESTED}+super", NEW)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, FLAT)
    session_env(NEW, FLAT, flat_base.userhome)
    return flat_base


def test_a_birth_refusal_carries_the_repair_line(fresh_other):
    """The birth door embeds the gate's words, so its REFUSED banner carries
    the repair line and HOST-LOCAL with no edit to `_birth.py`."""
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    (line,) = banner(result)
    assert "REFUSED" in line and "turned off" in line, line
    repaired(line, f"message subscription add {FLAT} --for {NEW}")
