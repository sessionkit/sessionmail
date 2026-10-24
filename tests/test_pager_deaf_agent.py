"""THE DEAF-AGENT BUG, and the storm that fixing it could have caused.

SM-122 do-018. Two halves, and neither ships without the other.

**The deafness.** Every seat was born with `to:$SEAT and not from:$SEAT`.
notmuch matches an address field by WORD, not by substring, and `ivan-chat`
tokenises to the words `ivan` and `chat` - so `not from:ivan` also excluded
`ivan-chat@`, the Delta Chat bridge a phone's messages arrive over.
Measured on the live index 2030-06-09: `from:ivan-chat and to:ivan` = 44,
and `and not from:ivan` = **0**. Every one dropped, silently, for days,
because THE ALARM IS THE QUERY.

**The storm.** On a call the bridge mails the seat EVERY CHUNK - one box
held fourteen in five minutes, one of them "[COUGH]". Restoring that mail
without coalescing would wake an idle seat once per cough. A deaf seat
misses things; a seat woken fourteen times in five minutes is unusable.

Pinned here:
  - the complement of the exclusion, run against a REAL notmuch, falls out
    as ONLY the seat's own mail - the test that would have caught this;
  - the fix is for the CLASS (`-chat`, `+label`, any sibling), never the
    instance - no `-chat` special case exists to be matched;
  - `$HOST` is the SHORT host, because the FQDN would MISS the bare-host
    form and re-open the hole on the other side;
  - (RETIRED by message-subscriptions 4.2: the born template and the repair
    of seat files already deaf on disk went with the files themselves);
  - THE STORM GATE: 14 bridge mails in 5 minutes to an idle seat cost AT
    MOST ONE WAKE and one grouped desk line, with every message still given;
  - the coalescing is a WINDOW, not a sender or subject filter - proved by
    a second address ringing through while the first is mid-burst.

Hermetic: own maildir and own notmuch corpus under `tmp_path`. The
complement test drives the real `notmuch` binary over a corpus it builds
itself, and SKIPS when notmuch is not installed - it is a test about query
semantics, and a fake engine would only echo this file's own assumptions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _maildir, _seat
from sessionmail.pager import cli

IDLE_T = 60.0


# ── the complement test: what actually falls out of the exclusion ─────────

def _corpus(tmp_path: Path, senders: list[str], to: str = "ivan@hq") -> Path:
    """A notmuch corpus with ONE sender per message - no multi-recipient
    confound, which is what made the first live reading of this ambiguous."""
    mail = tmp_path / "corpus"
    for sub in ("new", "cur", "tmp"):
        (mail / sub).mkdir(parents=True, exist_ok=True)
    for i, sender in enumerate(senders, 1):
        (mail / "cur" / f"{i}:2,S").write_text(
            f"From: {sender}\nTo: {to}\nX-Original-To: {to}\n"
            f"Subject: probe {i}\nDate: Sun, 09 Jun 2030 12:00:00 +0000\n"
            f"Message-ID: <probe{i}@test>\n\nbody\n")
    (tmp_path / "config").write_text(
        f"[database]\npath={mail}\n[user]\nname=t\nprimary_email=t@hq\n[new]\ntags=\n")
    subprocess.run(["notmuch", "new"], check=True, capture_output=True,
                   env={**os.environ, "NOTMUCH_CONFIG": str(tmp_path / "config")})
    return tmp_path / "config"


def _senders_matching(config: Path, query: str) -> set[str]:
    out = subprocess.run(["notmuch", "search", "--output=files", query],
                         capture_output=True, text=True,
                         env={**os.environ, "NOTMUCH_CONFIG": str(config)})
    found = set()
    for path in out.stdout.split("\n"):
        if path.strip():
            for line in Path(path).read_text().splitlines():
                if line.startswith("From: "):
                    found.add(line[6:].strip())
    return found


SIBLINGS = ["ivan@hq", "ivan@hq.example.ts.net",
            "ivan-chat@hq", "ivan-chat@hq.example.ts.net",
            "ivan+label@hq", "ivan+memory@hq.example.ts.net",
            "ivanka@hq", "judy@hq"]

SELF = {"ivan@hq", "ivan@hq.example.ts.net"}


@pytest.mark.skipif(not shutil.which("notmuch"), reason="needs a real notmuch")
def test_the_complement_of_the_exclusion_is_only_the_seats_own_mail(tmp_path):
    """THE TEST THAT WOULD HAVE CAUGHT IT. Run the complement of the
    exclusion and read what falls out: it must be the seat's own mail and
    NOTHING ELSE. An exclusion is a place to go deaf and its failure is
    silent by construction, so the complement is the only thing that looks."""
    config = _corpus(tmp_path, SIBLINGS)
    excluded = _senders_matching(config, "from:ivan@hq")
    assert excluded == SELF, f"the exclusion drops mail that is not the seat's own: {excluded - SELF}"
    # ... and the QUOTED form the born query now carries (task-address-doorbell
    # D3) excludes exactly the same mail, at both host spellings.
    assert _senders_matching(config, 'from:"ivan@hq"') == SELF


@pytest.mark.skipif(not shutil.which("notmuch"), reason="needs a real notmuch")
def test_the_bare_exclusion_was_deaf_and_this_test_can_fail(tmp_path):
    """The bug itself, pinned - so the test above is shown able to FAIL.
    The bare term ate the bridge AND the `+label` sibling; it spared
    `ivanka`, which is what proves notmuch matches by WORD, not substring."""
    config = _corpus(tmp_path, SIBLINGS)
    bare = _senders_matching(config, "from:ivan")
    assert "ivan-chat@hq" in bare                       # the bug: the bridge
    assert "ivan+label@hq" in bare                      # and the whole class
    assert "ivanka@hq" not in bare                    # word, not substring
    assert bare != SELF                                 # the assertion above FAILS here


@pytest.mark.skipif(not shutil.which("notmuch"), reason="needs a real notmuch")
def test_self_exclusion_still_works_for_both_host_spellings(tmp_path):
    """Traded one bug for another? No: the seat's OWN mail is still excluded
    at BOTH `@hq` and the FQDN. This is why `$HOST` is the SHORT host - the
    term is a phrase PREFIX, so the short form covers both spellings while
    the FQDN would miss `ivan@hq` entirely."""
    config = _corpus(tmp_path, SIBLINGS)
    assert _senders_matching(config, "from:ivan@hq") == SELF
    fqdn = _senders_matching(config, "from:ivan@hq.example.ts.net")
    assert fqdn == {"ivan@hq.example.ts.net"}
    assert "ivan@hq" not in fqdn, "the FQDN form would leave the bare-host form un-excluded"


# ── the template and the migration: RETIRED ──────────────────────────────
#
# message-subscriptions 4.2 retired the born files, their template (`BORN`)
# and the deaf-seat repair (`migrate_born`, `migrate_seat_file`,
# `migrate_all`) with them: the pager reads no subscription file, so there is
# no query left to be deaf. The complement tests above stay - they pin how
# notmuch matches an address, which is still true and still worth knowing
# for anyone who writes an exclusion - and the storm gate below is the
# listener's, unchanged.


# ── THE STORM GATE ───────────────────────────────────────────────────────
#
# specs/pager: "Counts SHALL coalesce per address across a burst ... so that
# restoring mail to a seat that a bridge writes to many times a minute costs
# AT MOST ONE WAKE and one grouped line. No sender filter and no subject
# filter SHALL be used to achieve this."
#
# The ratified scenario tests the desk line. The "at most one wake" half is
# what these pin, because it is the half that makes the deaf fix safe to
# ship - and the reason do-018 does not land without do-017.

_seq = 0


@pytest.fixture
def storm(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    root.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "ivan")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv("SESSIONMAIL_POLL_SECONDS", "0.02")
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, str(IDLE_T))
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    # IDLE: `session get idle` says not-busy for longer than the threshold,
    # so T = 0 and a page happens at once. This is the storm's precondition -
    # a busy seat was never the problem.
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: IDLE_T + 15)

    class Storm:
        root = None

        def box(self, name="sess-a"):
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def chunk(self, original_to="ivan", subject="[Judy 12] Judy: Still up?",
                  sender="ivan-chat@hq", box="sess-a"):
            """One bridge chunk, as the Delta Chat bridge actually sends it."""
            global _seq
            _seq += 1
            b = self.box(box)
            fname = f"{int(time.time())}.M{_seq:06d}P{os.getpid()}.test"
            (b / "tmp" / fname).write_bytes(
                f"X-Original-To: {original_to}\r\nFrom: Delta Chat bridge <{sender}>\r\n"
                f"To: ivan@hq\r\nSubject: {subject}\r\n"
                f"Message-ID: <chunk{_seq}@bridge>\r\n\r\nchunk {_seq}\r\n".encode())
            os.rename(b / "tmp" / fname, b / "new" / fname)
            return f"chunk{_seq}@bridge"

    s = Storm()
    s.root = root
    s.box()
    return s


@pytest.fixture
def running(storm, monkeypatch):
    """The same storm, to a session that is RUNNING: busy by Claude Code's own
    status, page age 0 so the loop still reaches the window at once. This is
    the case the window was built for - the ride-along carries the rest.
    An IDLE session is the `storm` fixture itself, and it rings every time
    (changes/idle-rings-through-burst)."""
    _seat._idle_cache.clear()
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 0.0)
    monkeypatch.setenv("SESSIONMAIL_PAGE_AGE_SECONDS", "0")
    yield storm
    _seat._idle_cache.clear()


def listen_once(*argv):
    return CliRunner().invoke(cli, ["listen", "--timeout", "0.25", *argv], prog_name="pager")


PAGED, TIMED_OUT = 0, 124


def test_fourteen_bridge_mails_in_five_minutes_cost_at_most_one_wake(running):
    """THE GATE. Fourteen chunks of one call, to a RUNNING session, arriving
    one at a time with a re-arm between each. Before the coalescing window
    this was fourteen wakes. (Was "to an idle seat" until Sun 2030-06-09: an
    idle session now rings every time - see the idle pin below.)"""
    wakes = 0
    for _ in range(14):
        running.chunk()
        if listen_once().exit_code == PAGED:
            wakes += 1
    assert wakes == 1, f"a call cost {wakes} wakes; the gate is at most one"


def test_the_storm_gate_can_fail(running, monkeypatch):
    """Shown able to FAIL: with the window turned off, the same fourteen
    chunks ring the doorbell fourteen times. A gate that cannot fail proves
    nothing, and this one is the whole reason do-018 waits for do-017."""
    monkeypatch.setenv(_seat.BURST_SECONDS_VAR, "0")
    wakes = 0
    for _ in range(14):
        running.chunk()
        if listen_once().exit_code == PAGED:
            wakes += 1
    assert wakes == 14


def test_every_chunk_is_still_given_exactly_once_on_the_desk_line(storm):
    """Coalescing must not DROP anything - that is the difference between a
    window and a filter. After the storm, every chunk is still waiting to be
    given, and the ride-along carries them as ONE grouped line."""
    mids = [storm.chunk() for _ in range(14)]
    for _ in range(14):
        listen_once()

    # 1. NOTHING WAS DISCARDED: all fourteen are still in the box.
    entries = _maildir.enumerate_box(str(storm.root / "sess-a"))
    assert len({e.message_id for e in entries} & set(mids)) == 14

    # 2. THEY ARE WAITING TO BE GIVEN, not silently consumed: the coalesced
    #    chunks are still linked in this session's ride queue, which is what
    #    the ride-along scoops. A filter would have dropped them here.
    seat = _seat.resolve()
    queued = _seat.queue_names(seat, "sess-a")
    assert len(queued) == 14, f"only {len(queued)} of 14 chunks are waiting for the desk"

    # 3. ONE GROUPED DESK LINE, via do-017's own grouping - one reader, one
    #    group, the full count. This is the "and one grouped line" half of
    #    the requirement, asserted on the function the desk line is built on.
    groups = _maildir.group_by_original_to(entries)
    bridge = [g for g in groups if _maildir.original_to_key(g.address) == "ivan"]
    assert len(bridge) == 1, f"one reader produced {len(bridge)} groups"
    assert bridge[0].total == 14


def test_the_three_spellings_of_one_reader_are_ONE_burst(running):
    """The false-MISS rule. `X-Original-To` arrives bare, `@hq` and FQDN in
    the same box - for ivan the BARE form is the most common. If the window
    keyed on the verbatim string, each spelling would be its own burst and
    the storm would come back in thirds."""
    wakes = 0
    for spelling in ["ivan", "ivan@hq", "ivan@hq.example.ts.net"] * 5:
        running.chunk(original_to=spelling)
        if listen_once().exit_code == PAGED:
            wakes += 1
    assert wakes == 1, f"{wakes} wakes: the spellings are not being treated as one reader"


def test_a_different_reader_rings_through_while_the_first_is_mid_burst(running):
    """The window is not a mute button. A DIFFERENT address still wakes the
    seat while the bridge is mid-call - which is also what proves this is not
    a sender filter: the same bridge sender gets through for another reader."""
    running.chunk(original_to="ivan")
    assert listen_once().exit_code == PAGED
    running.chunk(original_to="ivan")
    assert listen_once().exit_code == TIMED_OUT          # mid-burst, rides the desk
    running.chunk(original_to="ivan+urgent")
    assert listen_once().exit_code == PAGED, "a second reader was silenced by the first's burst"


def test_the_window_expires_and_the_seat_can_be_woken_again(running, monkeypatch):
    """A burst silences an address for the window, never forever."""
    running.chunk()
    assert listen_once().exit_code == PAGED
    running.chunk()
    assert listen_once().exit_code == TIMED_OUT
    monkeypatch.setenv(_seat.BURST_SECONDS_VAR, "0.01")
    time.sleep(0.05)
    running.chunk()
    assert listen_once().exit_code == PAGED


def test_an_idle_session_is_paged_for_every_chunk_of_a_call(storm):
    """RULINGS 1330 outranks the window: a stopped agent is paged immediately.
    An idle session makes no tool calls, so nothing rides along; a chunk held
    inside the window is simply unheard. Measured on a live call: fourteen
    turns sat behind one wake for the full 300 s while the session waited on
    the pager, and the caller hung up. The `storm` fixture IS the idle session."""
    wakes = 0
    for _ in range(5):
        storm.chunk()
        if listen_once().exit_code == PAGED:
            wakes += 1
    assert wakes == 5, f"an idle session was paged {wakes} times for 5 chunks; idle rings every time"


def test_no_sender_or_subject_string_decides_anything():
    """REFUSE THE DODGE, asserted on the DECISION surface: a filter fixes one
    instance and leaves the class armed.

    Scoped to the functions that actually compute a wake - not the whole
    module, because the bridge's name is all over the prose there explaining
    the bug, and a test that cannot tell documentation from a decision would
    either fail on a comment or have to stop looking. The behavioural half of
    this claim is `test_a_different_reader_rings_through_while_the_first_is_
    mid_burst`: the SAME bridge sender wakes the seat for another reader, so
    the sender cannot be what is being matched on.
    """
    import inspect
    from sessionmail import pager as pager_mod

    deciders = [pager_mod._address_key, _seat.in_burst, _seat.woke_at,
                _seat.woke_stamp, _seat.burst_seconds]
    source = ""
    for fn in deciders:
        text = inspect.getsource(fn)
        doc = inspect.getdoc(fn)
        if doc:                                   # drop the docstring, keep the code
            for line in doc.splitlines():
                text = text.replace(line, "")
        source += text

    for dodge in ("ivan", "chat", "Judy", "Delta", "bridge"):
        assert dodge not in source, (
            f"{dodge!r} appears in the wake decision: that is a filter on one "
            "instance, and it leaves the class armed")


# ── the burst window must not cost a spinning core ───────────────────────
#
# do-018 review: the coalescing window
# made `age >= T` stop being a branch that always paged and returned, so the
# sleep `min(interval, max(0.0, T - age))` - correct only while the queue is
# too YOUNG - started being reached with `T - age <= 0`. A zero timeout
# returns from `detect` at once, so the loop re-listed the queue, re-parsed
# every message and re-read the idle verdict with no sleep at all, for as
# long as the window lasted (up to 300s per burst, per seat). Measured at
# 577b1ea: 6.26s CPU over a 6.36s listen (98% of a core); 0.34s with the
# window off.
#
# THE GATE HELD THE WHOLE TIME - "at most one wake" was true while the seat
# burned a core. A correctness assertion passed straight over a resource
# defect, which is why this one is pinned separately and by a different
# instrument: ITERATIONS, not wakes.
#
# The instrument is the loop's own `detect` call - exactly one per
# iteration, so counting it counts turns of the loop. It is deliberately
# NOT a count of `idle_seconds`, whose verdict is cached
# (IDLE_POLL_SECONDS): a cache would dampen the very signal being measured.


def _counting_detect(monkeypatch):
    """Count turns of the listen loop; return the counter."""
    from sessionmail import _detect

    calls = []
    real = _detect.detect

    def counted(*a, **kw):
        calls.append(kw.get("timeout"))
        return real(*a, **kw)

    monkeypatch.setattr(_detect, "detect", counted)
    return calls


# --timeout 0.25 with SESSIONMAIL_POLL_SECONDS=0.02 (the storm fixture) is
# ~12 sleeps of one interval, plus a turn either side.
_TURNS_ALLOWED = int(0.25 / 0.02) + 5


def test_a_mid_burst_listen_sleeps_instead_of_spinning(running, monkeypatch):
    """A second chunk for an address that is already mid-burst must not wake
    the seat - and must not cost a spinning core while it declines to."""
    running.chunk()
    assert listen_once().exit_code == PAGED          # arms the window

    running.chunk()                                     # same address, inside it
    turns = _counting_detect(monkeypatch)
    assert listen_once().exit_code == TIMED_OUT       # still no second wake

    assert len(turns) <= _TURNS_ALLOWED, (
        f"the listen loop turned {len(turns)} times in 0.25s; at one tick per "
        f"0.02s interval it should turn at most {_TURNS_ALLOWED}. A mid-burst "
        "queue is spinning, not sleeping")
    assert 0 not in turns, (
        "the loop asked `detect` for a zero timeout: that returns at once and "
        "is what a spin is made of")
