"""memory wake's RECENT section and its closed-thread filter match EVERY address of
this seat, the same set ADDRESSED matches (`seat_names`), against a REAL notmuch.

A seat is reached at more than one address: its session id, and the seat address
its kick named (`<project>+<task>.<role>`). RECENT asked notmuch for `to:<box>`
alone, so a wake of the seat address never listed a letter sent To the session id,
and a CLOSED marker sent From the seat address never closed a thread on a wake of
the session-id box. ADDRESSED already matched every name of the seat; RECENT and
the closure query now use the same set, and a wake of ANOTHER box still matches
that box alone.

notmuch has no exact address match: `to:"<name>"` is a phrase found anywhere in
the header, so it also returns letters To `<name>.<token>` or To the same role on
another task. Its answer is only a candidate set. A letter is listed when its To
or Cc local part is exactly a name of the seat (a closing marker: its From), the
rule ADDRESSED applies. A bare role word, which every kicked seat carries in
CLAUDE_CODE_AGENT, is kept out of the notmuch clause.

The query is what is under test, so the index is a real notmuch database (as in
tests/sm827_rig.py): its parse of `+`, `-` and `.` in an address is what decides
the match, and a stub would prove nothing. Hermetic: the mail root, the notmuch
config and its database are under pytest's tmp_path, and the environment is
built from nothing. Synthetic dates: the earlier letter on 2030-06-09, the later
on 2030-12-08.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs

WAKE = Path(_mhs.MHS_DIR) / "session-mail-wake"
NOTMUCH = shutil.which("notmuch")

pytestmark = pytest.mark.skipif(NOTMUCH is None, reason="needs a real notmuch on PATH")

PROJECT = "mail"
SEAT = f"{PROJECT}+ab-12.foreman"
SIBLING = f"{PROJECT}+ab-12.worker"
ROLE = "foreman"
#: This seat's role on another task, and another seat holding this seat's role
#: address with a per-seat token. notmuch reads both as containing the seat's name
#: (a phrase match), yet neither is this seat.
OTHER_TASK = f"{PROJECT}+cd-34.{ROLE}"
PER_SEAT = f"{SEAT}.deadbeef"
SID = "7ed6b16b-31bd-fe66-35a0-284b56bbe9ec"
HOST = "wake.invalid"
EARLY = "Sun, 09 Jun 2030 12:00:00 +0000"
LATE = "Sun, 08 Dec 2030 12:00:00 +0000"


def letter(mid: str, to: str, subject: str, date: str = EARLY, sender: str = "kick",
           parent: str | None = None, cc: str | None = None) -> str:
    """A delivered letter: the stamp postfix prepends, then the headers."""
    thread = (f"In-Reply-To: <{parent}>\nReferences: <{parent}>\n" if parent else "")
    return (f"Return-Path: <{sender}@{HOST}>\n"
            f"X-Original-To: {to}@{HOST}\n"
            f"From: {sender} <{sender}@{HOST}>\n"
            f"To: {to}@{HOST}\n"
            + (f"Cc: {cc}@{HOST}\n" if cc else "") +
            f"Date: {date}\n"
            f"Subject: {subject}\n"
            f"Message-ID: <{mid}>\n"
            f"{thread}"
            "\n"
            "The body.\n")


def maildir(d: Path) -> Path:
    for sub in ("new", "cur", "tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


class Home:
    """A scratch mail root with the project box and the session box, and a notmuch
    database over it, built after the letters are dropped (`index`)."""

    def __init__(self, tmp: Path):
        self.root = tmp / "mail" / "stores"
        self.base = maildir(self.root / PROJECT)
        self.sbox = maildir(self.root / SID)
        home = tmp / "home"
        home.mkdir()
        self.config = tmp / "notmuch-config"
        self.config.write_text(
            "# a SCRATCH notmuch config. Never the live database.\n"
            f"[database]\npath={self.root}\n[user]\nname=kick\n"
            f"primary_email=kick@{HOST}\n"
            "[new]\ntags=\nignore=\n[maildir]\nsynchronize_flags=false\n")
        self.env = {"PATH": f"{Path(NOTMUCH).parent}:/usr/bin:/bin", "HOME": str(home),
                    "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                    "SESSIONMAIL_ROOT": str(self.root.parent),
                    "NOTMUCH_CONFIG": str(self.config),
                    "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_DATA_HOME": str(home / ".local" / "share"),
                    "SESSIONMAIL_PYTHON": sys.executable}

    def drop(self, box: Path, name: str, text: str) -> None:
        (box / "cur" / f"{name}:2,").write_text(text)

    def index(self) -> None:
        got = subprocess.run([NOTMUCH, "config", "get", "database.path"], env=self.env,
                             capture_output=True, text=True, check=True).stdout.strip()
        assert Path(got).resolve() == self.root.resolve(), f"REFUSED: database.path {got}"
        subprocess.run([NOTMUCH, "new", "--quiet"], env=self.env, check=True,
                       capture_output=True)

    def wake(self, *argv: str, **extra: str) -> subprocess.CompletedProcess:
        self.index()
        return subprocess.run([sys.executable, str(WAKE), *argv], capture_output=True,
                              text=True, timeout=120, env={**self.env, **extra})


def recent(out: str) -> str:
    """The RECENT section, from its heading to its cost line."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("═══ RECENT"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("— "))
    return "\n".join(lines[start:end + 1])


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return Home(tmp_path)


def seat_wake(home: Home, *argv: str) -> str:
    """A wake run as this seat: its seat address, its session id and, as on every
    kicked seat, its bare role in CLAUDE_CODE_AGENT."""
    r = home.wake(*argv, TM_AGENT_ADDRESS=SEAT, CLAUDE_CODE_SESSION_ID=SID,
                  CLAUDE_CODE_AGENT=ROLE)
    assert r.returncode == 0, r.stderr
    assert "COULD NOT LOOK" not in r.stdout and "NOT APPLIED" not in r.stdout, r.stdout
    return recent(r.stdout)


def test_a_seat_address_wake_lists_a_letter_to_the_session_id(home):
    home.drop(home.sbox, "sid.1", letter(f"sid.1@{HOST}", SID, "sent to my session id"))
    got = seat_wake(home)
    assert "sent to my session id" in got, got
    assert "1 threads shown of 1" in got, got


def test_a_seat_address_wake_lists_a_letter_to_the_seat_address(home):
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    got = seat_wake(home)
    assert "sent to my seat address" in got, got


def test_a_session_id_wake_lists_a_letter_to_the_seat_address(home):
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    home.drop(home.sbox, "sid.1", letter(f"sid.1@{HOST}", SID, "sent to my session id"))
    got = seat_wake(home, SID)
    assert "sent to my seat address" in got and "sent to my session id" in got, got
    assert "2 threads shown of 2" in got, got


def test_a_letter_to_a_sibling_seat_is_not_listed(home):
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    home.drop(home.base, "sib.1", letter(f"sib.1@{HOST}", SIBLING, "sent to the sibling"))
    for argv in ((), (SID,)):
        got = seat_wake(home, *argv)
        assert "sent to my seat address" in got, got
        assert "sent to the sibling" not in got, got
        assert "1 threads shown of 1" in got, got


def test_a_wake_of_another_box_matches_that_box_alone(home):
    """`memory wake <other>` from this seat: the caller's own names must not list
    the caller's letters under the other box's heading."""
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    home.drop(home.sbox, "sid.1", letter(f"sid.1@{HOST}", SID, "sent to my session id"))
    home.drop(home.base, "sib.1", letter(f"sib.1@{HOST}", SIBLING, "sent to the sibling"))
    got = seat_wake(home, SIBLING)
    assert f"threads to {SIBLING}" in got, got
    assert "sent to the sibling" in got, got
    assert "my seat address" not in got and "my session id" not in got, got
    assert "1 threads shown of 1" in got, got


def test_a_closed_marker_from_the_seat_address_hides_its_thread_on_a_session_id_wake(home):
    home.drop(home.sbox, "ask.1", letter(f"ask.1@{HOST}", SID, "the ask"))
    home.drop(home.base, "closed.1",
              letter(f"closed.1@{HOST}", f"{SEAT}+closed", "CLOSED: the ask", date=LATE,
                     sender=SEAT, parent=f"ask.1@{HOST}"))
    home.drop(home.sbox, "open.1", letter(f"open.1@{HOST}", SID, "still open"))
    got = seat_wake(home, SID)
    assert "still open" in got, got
    assert "the ask" not in got, got
    assert "1 closed" in got, got


def test_a_closed_marker_is_overtaken_by_newer_activity_to_the_session_id(home):
    """A thread closed From the seat address, then written to again at the session id,
    is open again: the activity side matches every name of the seat too."""
    home.drop(home.base, "ask.1", letter(f"ask.1@{HOST}", SEAT, "the ask"))
    home.drop(home.base, "closed.1",
              letter(f"closed.1@{HOST}", f"{SEAT}+closed", "CLOSED: the ask", sender=SEAT,
                     parent=f"ask.1@{HOST}"))
    home.drop(home.sbox, "again.1",
              letter(f"again.1@{HOST}", SID, "Re: the ask", date=LATE,
                     parent=f"closed.1@{HOST}"))
    got = seat_wake(home)
    assert "the ask" in got, got
    assert "closed" not in got.split("\n", 1)[1], got


def test_a_name_that_only_contains_the_seat_address_is_not_listed(home):
    """notmuch's to:"<name>" is a phrase matched anywhere in the header, so it hands
    back letters To this seat's role on another task and To a per-seat address built
    on this seat's. Those are candidates only: neither is this seat, on either wake."""
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    home.drop(home.base, "other.1", letter(f"other.1@{HOST}", OTHER_TASK, "sent to another task"))
    home.drop(home.base, "token.1", letter(f"token.1@{HOST}", PER_SEAT, "sent to a per-seat token"))
    for argv in ((), (SID,)):
        got = seat_wake(home, *argv)
        assert "sent to my seat address" in got, got
        assert "another task" not in got and "per-seat token" not in got, got
        assert "1 threads shown of 1" in got, got


def test_the_bare_role_word_does_not_list_every_seat_of_that_role(home):
    """CLAUDE_CODE_AGENT carries the bare role on a kicked seat. A bare word is not an
    address: matched as one, it would list every seat of that role on the host."""
    home.drop(home.sbox, "sid.1", letter(f"sid.1@{HOST}", SID, "sent to my session id"))
    home.drop(home.base, "role.1", letter(f"role.1@{HOST}", f"{PROJECT}+ef-56.{ROLE}",
                                          "sent to some other foreman"))
    got = seat_wake(home, SID)
    assert "sent to my session id" in got, got
    assert "some other foreman" not in got, got
    assert "1 threads shown of 1" in got, got


def test_a_letter_copied_to_the_seat_on_cc_is_listed(home):
    """The exact check reads Cc as well as To, as notmuch's to: does."""
    home.drop(home.base, "cc.1", letter(f"cc.1@{HOST}", SIBLING, "copied to me", cc=SEAT))
    got = seat_wake(home, SID)
    assert "copied to me" in got, got


def test_a_closed_marker_from_a_per_seat_address_does_not_hide_my_thread(home):
    """The closure query's from: is a phrase match too: a CLOSED marker From another
    seat whose address only CONTAINS this seat's is not this seat closing it."""
    home.drop(home.sbox, "ask.1", letter(f"ask.1@{HOST}", SID, "the ask"))
    home.drop(home.base, "closed.1",
              letter(f"closed.1@{HOST}", f"{PER_SEAT}+closed", "CLOSED: the ask", date=LATE,
                     sender=PER_SEAT, parent=f"ask.1@{HOST}"))
    got = seat_wake(home, SID)
    assert "the ask" in got, got
    assert "closed" not in got.split("\n", 1)[1], got


def test_a_wake_of_a_bare_box_still_matches_that_box(home):
    """A bare name is kept out of the notmuch clause, but never the wake box itself."""
    home.drop(home.base, "base.1", letter(f"base.1@{HOST}", PROJECT, "sent to the project"))
    got = seat_wake(home, PROJECT)
    assert f"threads to {PROJECT}" in got, got
    assert "sent to the project" in got, got


def failing_show(home: Home, tmp: Path, when: str) -> str:
    """A PATH whose `notmuch` is the real one, except that a `show` whose query holds
    WHEN exits 1: the exact check's own call cannot answer."""
    bin_dir = tmp / "failing-show"
    bin_dir.mkdir()
    shim = bin_dir / "notmuch"
    shim.write_text("#!/bin/sh\n"
                    f'if [ "$1" = show ]; then case "$*" in *"{when}"*) exit 1 ;; esac; fi\n'
                    f'exec "{NOTMUCH}" "$@"\n')
    shim.chmod(0o755)
    return f"{bin_dir}:{home.env['PATH']}"


def test_recent_could_not_look_when_the_exact_check_cannot_query(home, tmp_path):
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    r = home.wake(SID, TM_AGENT_ADDRESS=SEAT, CLAUDE_CODE_SESSION_ID=SID,
                  CLAUDE_CODE_AGENT=ROLE, PATH=failing_show(home, tmp_path, "date:"))
    assert r.returncode == 0, r.stderr
    got = recent(r.stdout)
    assert "COULD NOT LOOK" in got and "could not look" in got, got
    assert "threads shown of" not in got, got


def test_closed_filter_not_applied_when_its_exact_check_cannot_query(home, tmp_path):
    home.drop(home.base, "seat.1", letter(f"seat.1@{HOST}", SEAT, "sent to my seat address"))
    r = home.wake(SID, TM_AGENT_ADDRESS=SEAT, CLAUDE_CODE_SESSION_ID=SID,
                  CLAUDE_CODE_AGENT=ROLE, PATH=failing_show(home, tmp_path, "subject:closed"))
    assert r.returncode == 0, r.stderr
    got = recent(r.stdout)
    assert "closed-thread filter NOT APPLIED" in got, got
    assert "sent to my seat address" in got, got


# ── An over-long id query: chunked, and the floor when it is not ─────────────
#
# The exact check hands RECENT and the closure filter a list of Message-IDs, and
# notmuch is asked about them as one `id:"…" or id:"…"` argument. Past the OS's cap
# on one argument (Linux: 128 KiB) the exec itself fails, with no notmuch run at
# all. These tests make that cap small instead of making the letters many: the wake
# runs under a wrapper (`CAPPED`) that refuses, as the kernel does, any child whose
# single argument is longer than SM_TEST_ARG_CAP bytes, and logs each notmuch call.
# SESSIONMAIL_WAKE_QUERY_BYTES is the wake's own budget per id query.

CAPPED = '''\
import errno, json, os, runpy, subprocess, sys
cap = int(os.environ["SM_TEST_ARG_CAP"])
log = os.environ["SM_TEST_CALL_LOG"]
real = subprocess.run
def run(args, *a, **k):
    if isinstance(args, (list, tuple)) and args and os.path.basename(str(args[0])) == "notmuch":
        with open(log, "a") as fh:
            fh.write(json.dumps([str(x) for x in args]) + "\\n")
    if isinstance(args, (list, tuple)) and any(len(os.fsencode(str(x))) > cap for x in args):
        raise OSError(errno.E2BIG, os.strerror(errno.E2BIG), str(args[0]))
    return real(args, *a, **k)
subprocess.run = run
wake = sys.argv[1]
sys.argv = [wake, *sys.argv[2:]]
runpy.run_path(wake, run_name="__main__")
'''

#: The letters below make an id query of about 1.8 KB; a cap of 1024 bytes refuses it
#: whole, and a budget of 300 bytes cuts it into several chunks.
CAP = 1024
BUDGET = 300


def capped_wake(home: Home, tmp: Path, budget: int, *argv: str, cap: int = CAP,
                path: str | None = None) -> tuple[subprocess.CompletedProcess, list]:
    wrapper = tmp / "capped_wake.py"
    wrapper.write_text(CAPPED)
    log = tmp / "calls.jsonl"
    log.write_text("")
    home.index()
    env = {**home.env, "TM_AGENT_ADDRESS": SEAT, "CLAUDE_CODE_SESSION_ID": SID,
           "CLAUDE_CODE_AGENT": ROLE, "SM_TEST_ARG_CAP": str(cap),
           "SM_TEST_CALL_LOG": str(log), "SESSIONMAIL_WAKE_QUERY_BYTES": str(budget)}
    if path:
        env["PATH"] = path
    r = subprocess.run([sys.executable, str(wrapper), str(WAKE), *argv],
                       capture_output=True, text=True, timeout=120, env=env)
    import json
    calls = [json.loads(ln) for ln in log.read_text().splitlines() if ln]
    return r, calls


def many_letters(home: Home) -> None:
    """Forty one-letter threads, a six-letter thread whose letters fall between
    theirs in time (one To a sibling, so not every letter of it is mine), and a
    thread this seat closed. Every clock differs, so newest-first has one answer."""
    for i in range(40):
        to, box = (SID, home.sbox) if i % 2 else (SEAT, home.base)
        home.drop(box, f"one.{i}", letter(f"one.{i:02d}@{HOST}", to, f"single {i:02d}",
                                          date=f"Sun, 09 Jun 2030 10:{i:02d}:00 +0000"))
    parent = None
    for j in range(6):
        to, box = [(SEAT, home.base), (SID, home.sbox), (SIBLING, home.base)][j % 3]
        mid = f"long.{j}@{HOST}"
        home.drop(box, f"long.{j}", letter(mid, to, "the long thread" if j == 0 else
                                           "Re: the long thread", parent=parent,
                                           date=f"Sun, 09 Jun 2030 10:{7 * j + 3:02d}:30 +0000"))
        parent = mid
    home.drop(home.sbox, "ask.1", letter(f"ask.1@{HOST}", SID, "the closed ask",
                                         date="Sun, 09 Jun 2030 09:00:00 +0000"))
    home.drop(home.base, "closed.1",
              letter(f"closed.1@{HOST}", f"{SEAT}+closed", "CLOSED: the closed ask",
                     date="Sun, 09 Jun 2030 09:30:00 +0000", sender=SEAT,
                     parent=f"ask.1@{HOST}"))


def searches(calls: list) -> list:
    return [c for c in calls if len(c) > 1 and c[1] == "search"]


def test_a_chunked_id_query_lists_what_one_query_lists(home, tmp_path):
    """Several searches, each under the budget, and the same RECENT, byte for byte,
    as one search: every thread once (the six-letter one is one line, [4/6]), newest
    first, the 25 cap, the '…and N more' line, the closed count and the accounting."""
    many_letters(home)
    one = seat_wake(home, SID)
    r, calls = capped_wake(home, tmp_path, BUDGET, SID)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    got = recent(r.stdout)
    assert got == one, f"--- one query ---\n{one}\n--- chunked ---\n{got}"
    assert got.count("the long thread") == 1, got
    assert "[4/6]" in got, got
    assert "…and 16 more threads" in got, got
    assert "1 closed" in got, got
    assert "41 threads shown of 42" in got, got
    ids = [c for c in searches(calls) if 'id:"' in c[-1]]
    assert len(ids) > 3, ids  # RECENT, the closure and the activity each chunked
    assert max(len(c[-1].encode()) for c in ids) <= BUDGET, ids


def test_a_thread_is_never_cut_between_two_chunks(home, tmp_path):
    """notmuch's line for a thread (its [matched/total], its matched-first authors) is
    only true when every letter of the thread that is mine is in the same query, so a
    chunk boundary never falls inside a thread."""
    many_letters(home)
    r, calls = capped_wake(home, tmp_path, BUDGET, SID)
    assert r.returncode == 0, r.stderr
    long_mine = {f'id:"long.{j}@{HOST}"' for j in (0, 1, 3, 4)}
    holding = [c[-1] for c in searches(calls) if any(t in c[-1] for t in long_mine)]
    for q in holding:
        assert all(t in q for t in long_mine), holding


def test_a_thread_longer_than_the_budget_is_one_query_of_its_own(home, tmp_path):
    """A thread whose own ids pass the budget is not cut: it goes as one search,
    and is listed true."""
    many_letters(home)
    one = seat_wake(home, SID)
    r, _ = capped_wake(home, tmp_path, 60, SID, cap=10_000)
    assert r.returncode == 0, r.stderr
    assert recent(r.stdout) == one


def test_an_id_query_past_the_os_cap_degrades_recent_and_the_wake_goes_on(home, tmp_path):
    """The floor: with no chunking (a budget past any query), the exec of the one
    over-long search is refused. RECENT says COULD NOT LOOK, the later sections still
    print, and the wake exits 0, never a traceback mid-readout."""
    many_letters(home)
    r, _ = capped_wake(home, tmp_path, 10 ** 9, SID)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    got = recent(r.stdout)
    assert "COULD NOT LOOK" in got and "could not look" in got, got
    assert "threads shown of" not in got, got
    after = r.stdout.split(got, 1)[1]
    assert "═══ ADDRESSED" in after and "═══ COUNTS" in after, r.stdout
    assert "argument list too long" in r.stderr, r.stderr
    assert "Errno" not in r.stderr, r.stderr


def failing_search(home: Home, tmp: Path, when: str) -> str:
    """A PATH whose `notmuch search` exits 1 when its query holds WHEN."""
    bin_dir = tmp / "failing-search"
    bin_dir.mkdir()
    shim = bin_dir / "notmuch"
    shim.write_text("#!/bin/sh\n"
                    f'if [ "$1" = search ]; then case "$*" in *"{when}"*) exit 1 ;; esac; fi\n'
                    f'exec "{NOTMUCH}" "$@"\n')
    shim.chmod(0o755)
    return f"{bin_dir}:{home.env['PATH']}"


def test_one_failed_chunk_degrades_the_whole_of_recent(home, tmp_path):
    """One chunk's search fails: RECENT is COULD NOT LOOK, never the other chunks'
    threads shown as if they were all."""
    many_letters(home)
    r, _ = capped_wake(home, tmp_path, BUDGET, SID,
                       path=failing_search(home, tmp_path, f"one.39@{HOST}"))
    assert r.returncode == 0, r.stderr
    got = recent(r.stdout)
    assert "COULD NOT LOOK" in got, got
    assert "single" not in got and "threads shown of" not in got, got


def test_one_failed_closure_chunk_leaves_the_filter_not_applied(home, tmp_path):
    many_letters(home)
    r, _ = capped_wake(home, tmp_path, BUDGET, SID,
                       path=failing_search(home, tmp_path, f"closed.1@{HOST}"))
    assert r.returncode == 0, r.stderr
    got = recent(r.stdout)
    assert "closed-thread filter NOT APPLIED" in got, got
    # The closed thread is counted as listed (it is the oldest, past the 25 cap).
    assert "42 threads shown of 42" in got and "1 closed" not in got, got
