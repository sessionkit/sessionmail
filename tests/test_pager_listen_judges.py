"""`pager listen` judges each arrival before it pages, and re-reads the rules
live: SM-474 do-006 (tasks.md 4.1; design A6).

- 4.1: every arrival is judged (`rules.judge` over its headers, with the
  layers `rules.load_layers` found) BEFORE the listener pages. A ride-only
  mail never pages: it stays in the ride queue for the next tool call. The
  burst window (`wakeable`) sees only mails judged wake. Both paths are
  judged: the seated one (the queue) and the seatless one (the box's new/).
- A6: the listener stats each layer file (and each focus file) at each
  arrival and re-reads one whose mtime/ctime or size DIFFERS (not newer).
  An edit applies from the next arrival, with no re-arm. A bad line is named
  with file and line in the banner if present at arm, else once, in the next
  ride-along line after it appears.
- do-013 review F1: the listener hands `judge` the `Layers` object, so the
  locked file's `set` lines reach its verdict (pinned end to end below).

Hermetic: a maildir root and a sessionmail home under tmp_path, the locked
file in conftest's sandbox, no live listener, no real `~/.mail`.

Written to fail first against `dafaa7e` (the listener paged every arrival).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import SC6_SETTINGS

from sessionmail import _seat, pager, rules
from sessionmail import _settings
from sessionmail.pager import cli

EX_PAGE, EX_TIMEOUT = 0, 124
RUNNING_T = 0.4
IDLE_T = 60.0
SESSION = "sess-a"
HOST = "hq"
SUFFIX = "tailnet.example"
FQDN = f"{HOST}.{SUFFIX}"

_n = 0


def _mail(sid: str) -> Path:
    """A session's mail state since session-settings 3.3a: under the session
    dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/` (spelt out,
    not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def counted(result) -> int | None:
    m = re.search(r"pager: (\d+) messages? waiting\.", result.stdout)
    return int(m.group(1)) if m else None


def later(seconds: float, fn, *args) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), fn(*args)))
    t.start()
    return t


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    root.mkdir()
    home = tmp_path / "sessionmail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("MAGICDNS_SUFFIX", SUFFIX)
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv(rules.PAGE_RULES_VAR, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(RUNNING_T))
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, str(IDLE_T))
    monkeypatch.delenv(pager.PAGE_LIST_VAR, raising=False)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    # SM-450 (refresh-002): the hooks ON in the sandbox HOME (conftest). The
    # ride-along tests built on this rig (SM-474 / SM-442) are about WHAT the
    # ride-along says, and under the settings file an off hook says nothing at
    # all. Hooks-off-by-default is pinned by tests/test_pager_hook.py.
    hooks_on = Path(os.environ["HOME"]) / _settings.RELATIVE
    hooks_on.parent.mkdir(parents=True, exist_ok=True)
    hooks_on.write_text('{"version": 1, "hooks": true}\n')

    class Rig:
        def __init__(self) -> None:
            self.root = root
            self.home = home
            self.seat = _seat.resolve()

        def box(self, name: str = SESSION) -> Path:
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def deliver(self, mid: str, *, sender: str = f"beta@{FQDN}", to: str = SESSION,
                    box: str = SESSION, extra: str = "") -> str:
            """One mail as postfix delivers it: `X-Original-To` names the
            address it came THROUGH (the session's own = direct)."""
            global _n
            _n += 1
            b = self.box(box)
            fname = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.judge"
            (b / "tmp" / fname).write_bytes(
                f"X-Original-To: {to}\r\nFrom: {sender}\r\nTo: {to}@{FQDN}\r\n"
                f"Subject: s {mid}\r\nMessage-ID: <{mid}>\r\n{extra}\r\nTHE BODY\r\n".encode())
            os.rename(b / "tmp" / fname, b / "new" / fname)
            return fname

        def stopped(self) -> None:
            monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: IDLE_T + 15)

        def running(self) -> None:
            monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 0.0)

        def session_rules(self, text: str, name: str = rules.RULES_FILE) -> Path:
            path = _mail(SESSION) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return path

        def locked(self, text: str) -> Path:
            path = rules.locked_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return path

        def marks(self) -> str:
            m = _seat.marks_path(self.seat, SESSION)
            return m.read_text() if m.exists() else ""

        def queued(self) -> list[str]:
            q = _seat.ride_dir(self.seat, SESSION) / _seat.QUEUE_NEW
            return sorted(os.listdir(q)) if q.is_dir() else []

    return Rig()


@pytest.fixture
def sid(rig, fresh_session, monkeypatch):
    """SM-558: this test's own fresh session id (conftest `fresh_session`),
    set over the rig's SESSION, for a test that runs a REAL child."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", fresh_session)
    return fresh_session


def run(*argv: str):
    return CliRunner().invoke(cli, ["listen", *argv], prog_name="pager")


def paged_mids(rig) -> set[str]:
    """The Message-IDs the page marked `seen` with a `pager wake` reason."""
    return {m for m in re.findall(r"([\w.-]+@[\w.-]+)", "\n".join(
        ln for ln in rig.marks().splitlines() if "pager wake" in ln))}


# ── 4.1: judged before paging ────────────────────────────────────────────────

def test_a_ride_only_arrival_on_an_idle_session_does_not_page(rig):
    """The spec's *Ride-along only, on an idle agent*: a mail that reached
    the box through a subscription meets only `ride *`, so the idle session
    is NOT paged, and the mail waits in the queue for the next tool call."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("ride1@x.invalid", to="atomnote"))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert counted(result) is None
    assert "ride1@x.invalid" not in rig.marks()
    assert len(rig.queued()) == 1, "the ride-only mail stays queued for the ride-along"


def test_a_wake_arrival_on_an_idle_session_pages(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("wake1@x.invalid"))          # direct: wake to:$me
    t0 = time.monotonic()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert time.monotonic() - t0 < 1.0
    assert counted(result) == 1


def test_a_ride_only_resident_beside_a_wake_arrival_pages_only_the_wake_one(rig):
    """The ride-only mail does not join the wake subset: not counted, not
    marked by the page, still in the queue after it."""
    rig.box()
    rig.deliver("ride2@x.invalid", to="atomnote")
    rig.stopped()
    later(0.3, lambda: rig.deliver("wake2@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert counted(result) == 1, result.stdout
    assert paged_mids(rig) == {"wake2@x.invalid"}
    assert len(rig.queued()) == 2


def test_a_session_rule_that_rides_a_direct_mail_keeps_it_from_paging(rig):
    rig.box()
    rig.session_rules("ride from:beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("r3@x.invalid"))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, result.stdout
    assert "r3@x.invalid" not in rig.marks()


def test_judys_mail_through_a_subscription_pages_by_the_built_in_wake_bang(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("m1@x.invalid", sender=f"judy@{FQDN}", to="atomnote"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert paged_mids(rig) == {"m1@x.invalid"}


def test_a_ride_only_mail_on_a_running_session_never_pages(rig):
    """Busy: a wake pages after the running page age if no tool call took
    it; a ride-only mail never pages at all, however long it waits."""
    rig.box()
    rig.deliver("ride4@x.invalid", to="atomnote")
    rig.running()
    result = run("--timeout", str(RUNNING_T * 3))
    assert result.exit_code == EX_TIMEOUT, result.stdout


# ── A6: the rules are re-read at the next arrival, no re-arm ────────────────

def test_editing_the_session_rules_between_two_arrivals_changes_the_second_verdict(rig):
    """The slice's criterion: ONE listen. The first direct mail meets the
    session's `ride from:beta@*` and rides; the file is then edited; the
    second, identical in shape, is judged under the edit and pages."""
    rig.box()
    path = rig.session_rules("ride from:beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("e1@x.invalid"))
    later(0.6, lambda: path.write_text("ride from:gamma@*\n", encoding="utf-8"))
    later(0.9, lambda: rig.deliver("e2@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert counted(result) == 1, result.stdout
    assert paged_mids(rig) == {"e2@x.invalid"}


def test_a_rules_file_that_appears_after_arm_is_read_at_the_next_arrival(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("f1@x.invalid", to="atomnote"))       # rides: built-in
    later(0.6, lambda: rig.session_rules("wake x-original-to:atomnote\n"))
    later(0.9, lambda: rig.deliver("f2@x.invalid", to="atomnote"))       # wakes: the new file
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"f2@x.invalid"}


def test_a_focus_file_is_re_read_at_the_next_arrival_too(rig):
    """A6: 'The focus file is read the same way.' Sleep set after arm
    stops the next direct mail from paging."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.session_rules("sleep\n", rules.FOCUS_FILE))
    later(0.5, lambda: rig.deliver("z1@x.invalid"))
    result = run("--timeout", "1.2")
    assert result.exit_code == EX_TIMEOUT, result.stdout


# ── do-013 review F1: the locked file's settings reach the listener ─────────

def test_the_locked_files_set_judy_reaches_the_listeners_verdict(rig):
    """`judge` falls back to the defaults when handed a plain list; the
    listener hands it the `Layers`, so `set operator` in the locked file
    decides: beta's mail through a subscription pages by `wake!`."""
    rig.box()
    rig.locked("set operator beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("s1@x.invalid", to="atomnote"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"s1@x.invalid"}


def test_the_locked_files_set_urgent_reaches_the_listeners_verdict(rig):
    rig.box()
    rig.locked("set urgent $operator beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("u1@x.invalid", to="atomnote", extra="Priority: urgent\r\n"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert paged_mids(rig) == {"u1@x.invalid"}


# ── the seatless path (no queue): judged the same ───────────────────────────

@pytest.fixture
def seatless(rig, monkeypatch):
    """No session id and no seat: the listener watches $MAIL's new/ with no
    queue (message-subscriptions D8)."""
    for var in (*_seat.SESSION_VARS, *_seat.SEAT_VARS):
        monkeypatch.delenv(var, raising=False)
    box = rig.box("human")
    monkeypatch.setenv("MAIL", str(box))
    return box


def test_the_seatless_path_promotes_a_ride_only_arrival_to_a_page(rig, seatless):
    """SM-474 do-014, F1 (b) RULED: this path has no queue and
    so no ride-along; a ride here would never be heard, so it PAGES. (Until
    do-014 this test pinned the opposite: exit 124.)"""
    later(0.1, lambda: rig.deliver("n1@x.invalid", to="atomnote", box="human"))
    result = run("--timeout", str(RUNNING_T * 3))
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert "no ride-along" in result.stdout


def test_the_seatless_path_pages_the_wake_and_the_promoted_ride_and_says_which(rig, seatless):
    """Until SM-474 do-014 this pinned "the wake and ONLY it" (the ride was
    left unheard). F1 (b), RULED: with no ride-along the ride pages too,
    and the page names each: the promotion, and the rule that woke."""
    rig.deliver("n2@x.invalid", to="atomnote", box="human")
    later(0.1, lambda: rig.deliver("n3@x.invalid", sender=f"judy@{FQDN}", to="atomnote",
                                   box="human"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert counted(result) == 2, result.stdout
    assert "promoted from ride by `ride *` (built-in)" in result.stdout
    assert "carry by `carry! from:$operator` (built-in)" in result.stdout


# ── bad lines: banner at arm, else once in the next ride-along ──────────────

def test_a_bad_line_present_at_arm_is_named_in_the_banner(rig):
    rig.box()
    path = rig.session_rules("ride from:gamma@*\n\nbogus x:y\n")
    rig.deliver("b1@x.invalid")
    rig.stopped()
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE
    assert f"{path}:3:" in result.stderr
    assert "bogus x:y" in result.stderr


def batch() -> list[str]:
    r = CliRunner().invoke(cli, ["hook", "batch"], input="{}")
    assert r.exit_code == 0, r.stderr
    if not r.stdout.strip():
        return []
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].splitlines()


def test_a_bad_line_that_appears_later_is_named_once_in_the_next_ride_along(rig):
    rig.box()
    path = rig.session_rules("ride from:gamma@*\n")
    assert not any("bogus" in ln for ln in batch())
    path.write_text("ride from:gamma@*\nbogus x:y\n", encoding="utf-8")
    first = [ln for ln in batch() if "bogus" in ln]
    assert len(first) == 1 and f"{path}:2:" in first[0], first
    assert not any("bogus" in ln for ln in batch()), "once, not on every tool call"


def test_a_bad_line_named_in_the_banner_is_not_named_again_in_the_ride_along(rig):
    rig.box()
    rig.session_rules("bogus x:y\n")
    rig.deliver("b2@x.invalid")
    rig.stopped()
    assert run("--timeout", "3").exit_code == EX_PAGE
    assert not any("bogus" in ln for ln in batch())


def test_a_bad_line_fixed_then_broken_again_is_named_again(rig):
    rig.box()
    path = rig.session_rules("bogus x:y\n")
    assert any("bogus" in ln for ln in batch())
    path.write_text("ride *\n", encoding="utf-8")
    assert not any("bogus" in ln for ln in batch())
    path.write_text("bogus x:y\n", encoding="utf-8")
    assert any("bogus" in ln for ln in batch())


# ── the live reader itself (rules.LiveLayers) ───────────────────────────────

def test_live_layers_reread_a_file_whose_stat_differs_even_when_older(tmp_path):
    """DIFFERENT, not newer: a same-size rewrite with its mtime put back
    still differs by ctime, and is re-read."""
    home = tmp_path / "home"
    path = _mail(SESSION) / rules.RULES_FILE
    path.parent.mkdir(parents=True)
    path.write_text("ride from:aa@*\n")
    old = path.stat().st_mtime - 3600
    os.utime(path, (old, old))
    live = rules.LiveLayers("alpha", SESSION, {}, home=home, locked=tmp_path / "none")
    assert [r.text for r in live.current()[0].rules] == ["ride from:aa@*"]
    loads = live.loads
    assert live.current() is live.current() and live.loads == loads, "unchanged: not re-read"
    time.sleep(0.01)
    path.write_text("ride from:bb@*\n")                  # same size
    os.utime(path, (old - 60, old - 60))                  # and OLDER
    assert [r.text for r in live.current()[0].rules] == ["ride from:bb@*"]
    assert live.loads == loads + 1


def test_live_layers_carry_the_settings(tmp_path):
    locked = tmp_path / "rules.locked"
    locked.write_text("set operator boss@*\n")
    live = rules.LiveLayers("alpha", SESSION, {}, home=tmp_path / "home", locked=locked)
    # SM-727: the loader fills `configured` from the host config (SC6 here)
    assert live.current().settings == replace(SC6_SETTINGS, operator=("boss@*",))


def test_hooks_on_the_ride_along_carries_the_mail_action_and_the_state_line_once(rig):
    """SM-450 refresh-002: the three branches meet in `hook_batch`. SM-474's
    `(action)` on the mail line, SM-450's D11 state line, and the record that
    the state line was given (written AFTER the emit, by `emit_all`). With the
    hooks ON the first batch carries BOTH; a second batch carries neither
    again - the mail was given once, the state line only on change."""
    rig.box()
    rig.session_rules("ride from:gamma@*\n")
    fname = rig.deliver("m1@x.invalid", sender="gamma@x.invalid")
    os.link(rig.box() / "new" / fname, _seat.queue_new(rig.seat, SESSION) / fname)
    first = batch()
    mail = [ln for ln in first if ln.startswith("[mail]")]
    assert len(mail) == 1 and mail[0].endswith("(ride)"), first
    assert pager.LINE_NOT_LISTENING in first, first       # D11: said once, with the mail
    again = batch()
    assert pager.LINE_NOT_LISTENING not in again, again   # ...and then only on change
    assert not any(ln.startswith("[mail]") for ln in again), again


# ── SM-640 do-003: the page writes the carry stamp (R5), and the notice
#    turn carries (R7) ─────────────────────────────────────────────────────────

def _carry_stamp(rig) -> Path:
    return _seat.stamp_dir(rig.seat, SESSION) / _seat.CARRY_STAMP


NOTICE = "<task-notification>\n<status>completed</status>\n</task-notification>"


def ups(prompt: str = NOTICE) -> list[str]:
    """The notice turn's UserPromptSubmit, through the same hook verb."""
    r = CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(
        {"hook_event_name": "UserPromptSubmit", "prompt": prompt}))
    assert r.exit_code == 0, r.output
    if not r.stdout.strip():
        return []
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    return out["additionalContext"].splitlines()


def test_a_carrying_page_writes_the_stamp_after_its_marks(rig):
    """Judy's mail pages by the built-in `carry!`: the page marks it seen,
    THEN writes the stamp. It takes nothing: the mail is still queued."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("c1@x.invalid", sender=f"judy@{FQDN}", to="atomnote"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert paged_mids(rig) == {"c1@x.invalid"}
    assert _carry_stamp(rig).exists()
    assert _carry_stamp(rig).stat().st_mtime >= _seat.marks_path(rig.seat, SESSION).stat().st_mtime
    assert len(rig.queued()) == 1                                  # the listener took nothing
    assert "carry by `carry! from:$operator` (built-in)" in result.stdout


def test_a_plain_wake_page_now_writes_the_stamp_and_the_same_page(rig):
    """Was `test_a_plain_wake_page_writes_no_stamp`. SM-656 R1 (a wake carries):
    `wake to:$me` carries,
    so the page leaves the stamp. The page's why line is unchanged (R5)."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("w9@x.invalid"))                # direct: `wake to:$me`
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert _carry_stamp(rig).exists()
    assert "pager: wake by `wake to:$me` (built-in)" in result.stdout.splitlines()


def test_set_carry_off_pages_judys_mail_as_a_plain_wake(rig):
    """R4: the host kill switch. The page still fires; nothing carries."""
    rig.box()
    rig.locked("set carry off\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("c2@x.invalid", sender=f"judy@{FQDN}", to="atomnote"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert not _carry_stamp(rig).exists()
    assert "wake by `carry! from:$operator` (built-in)" in result.stdout


def test_the_notice_turn_carries_the_paged_mail_once_marked_carry(rig):
    """The loop end to end, short of Claude Code: page -> the notice turn's
    UPS gives the mail, its line saying `(carry)` -> the re-arm's batch
    gives nothing twice."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("c3@x.invalid", sender=f"judy@{FQDN}", to="atomnote"))
    assert run("--timeout", "3").exit_code == EX_PAGE
    carried = ups()
    mail = [ln for ln in carried if ln.startswith("[mail]")]
    assert len(mail) == 1 and mail[0].endswith("(carry)"), carried
    assert not _carry_stamp(rig).exists() and rig.queued() == []
    assert ups() == []                                             # a second notice: no stamp
    assert not any(ln.startswith("[mail]") for ln in batch())      # the re-arm's batch


# ── SM-656 SC1: every wake carries; the stamp gate is unchanged ──────────

def _carried_once(rig, mid: str) -> None:
    """The wake turn's UserPromptSubmit gives MID exactly once, marked
    `(carry)`; a second notice and the re-arm's batch give nothing twice."""
    carried = ups()
    mail = [ln for ln in carried if ln.startswith("[mail]")]
    assert len(mail) == 1 and mid in mail[0] and mail[0].endswith("(carry)"), carried
    assert not _carry_stamp(rig).exists() and rig.queued() == []
    assert ups() == []
    assert not any(ln.startswith("[mail]") for ln in batch())


def test_sc1_a_wake_rule_writes_the_stamp_and_the_wake_turn_gives_the_mail_once(rig):
    rig.box()
    rig.session_rules("wake from:beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("s1@x.invalid", to="atomnote"))  # rides by default
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert paged_mids(rig) == {"s1@x.invalid"}
    assert _carry_stamp(rig).exists()
    assert "pager: wake by `wake from:beta@*` (session)" in result.stdout.splitlines()
    _carried_once(rig, "s1@x.invalid")


def test_sc1_a_wake_bang_rule_writes_the_stamp_and_the_wake_turn_gives_the_mail_once(rig):
    """`wake!` is `carry!`: a breakthrough, through a session's `ride *` above it."""
    rig.box()
    rig.session_rules("ride *\nwake! from:beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("s2@x.invalid", to="atomnote"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert _carry_stamp(rig).exists()
    assert "pager: wake by `wake! from:beta@*` (session)" in result.stdout.splitlines()
    _carried_once(rig, "s2@x.invalid")


def test_sc1_the_kill_switch_makes_a_wake_plain(rig):
    """R3: `set carry off`. The page still fires; no stamp, and the notice
    turn's UserPromptSubmit gives nothing; the re-arm's batch gives the mail."""
    rig.box()
    rig.locked("set carry off\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("s3@x.invalid"))                # `wake to:$me`
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert not _carry_stamp(rig).exists()
    assert "pager: wake by `wake to:$me` (built-in)" in result.stdout.splitlines()
    assert ups() == []
    assert [ln for ln in batch() if ln.startswith("[mail]")][0].endswith("(wake)")


def test_sc1_a_typed_prompt_does_not_carry_a_wake(rig):
    """R2: with no `set carry-prompt on`, a typed prompt gives nothing and
    takes nothing, the stamp included; the notice turn then carries."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("s4@x.invalid"))
    assert run("--timeout", "3").exit_code == EX_PAGE
    assert _carry_stamp(rig).exists()
    assert ups("what is on my desk?") == []
    assert _carry_stamp(rig).exists() and len(rig.queued()) == 1
    _carried_once(rig, "s4@x.invalid")


def test_sc1_a_subagent_never_carries_a_wake(rig):
    """R2, E3: a subagent's UserPromptSubmit (`agent_id`) gives nothing and
    leaves the stamp and the queue to the seat."""
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("s5@x.invalid"))
    assert run("--timeout", "3").exit_code == EX_PAGE
    r = CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(
        {"hook_event_name": "UserPromptSubmit", "prompt": NOTICE,
         "agent_id": "a81b552b29e268608", "agent_type": "general-purpose"}))
    assert r.exit_code == 0 and r.stdout == "", r.stdout
    assert _carry_stamp(rig).exists() and len(rig.queued()) == 1
    _carried_once(rig, "s5@x.invalid")


def test_sc1_an_unjudged_mail_wakes_and_does_not_carry(rig, monkeypatch):
    """R2, E10: a mail the rules could not judge wakes (an unknown wakes) but
    has earned no carry."""
    def broken(*args, **kwargs):
        raise RuntimeError("the rules could not judge this")
    monkeypatch.setattr(pager, "judge_head", broken)
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("s6@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    assert paged_mids(rig) == {"s6@x.invalid"}
    assert not _carry_stamp(rig).exists()
    assert ups() == []


# SM-656 R5: the page text is untouched. `why_lines` reaches the page bytes,
# so for every line and every switch the page's why line is what it was
# before SM-656, when only a line WRITTEN `carry`/`carry!` could carry.

@pytest.mark.parametrize("rule", ["wake to:$me", "wake! from:$operator", "carry call:$me",
                                  "carry! from:$operator", "carry *", "ride *", None])
@pytest.mark.parametrize("carry_on", [True, False])
def test_r5_the_pages_why_lines_are_byte_identical_to_before(rule, carry_on):
    action = "ride" if rule and rule.startswith("ride") else "wake"
    verb = rule.split()[0] if rule else ""
    before = rules.Verdict(action, rule, rules.BUILT_IN, 1,
                           carry=carry_on and verb in rules.CARRY_VERBS)
    now = rules.Verdict(action, rule, rules.BUILT_IN, 1,
                        carry=carry_on and verb in rules.WAKE_VERBS)
    assert pager.why_lines([now]) == pager.why_lines([before])
    def page(v: rules.Verdict) -> bytes:
        lines = pager.quiet_page(1, None, SESSION, now=1_910_176_496.0, why=pager.why_lines([v]))
        return "\n".join(lines).encode()
    assert page(now) == page(before)


def test_r5_pager_why_and_the_ride_along_say_the_wake_carried():
    """Off the page, the words say what happened: a wake that carried."""
    v = rules.Verdict("wake", "wake to:$me", rules.BUILT_IN, 6, carry=True)
    assert pager.action_text(v) == "carry"
    assert pager.why_lines([v]) == ["pager: wake by `wake to:$me` (built-in)"]
    rec = {"message_id": "x@y", "action": "wake", "rule": "wake to:$me",
           "layer": rules.BUILT_IN, "line": 6, "carry": True, "by": "pager listen",
           "stamp": "2030-01-01T12:00:00Z"}
    assert pager.why_text(rec).startswith("carry by `wake to:$me` (the built-in default)")
