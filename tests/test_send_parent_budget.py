"""The parent lookup has a budget: a send never waits on an unbounded walk.

A reply or a supersede looks its target up (notmuch, then a walk of the
whole mail root) to build `References:`. For an id this host does not hold,
the walk read every head under the root before the send could mint or
deliver: seconds on a large root, and a caller's time limit then killed the
send before anything was delivered (the MCP door included, since its child
IS `message send`).

What is pinned here, with NO wall-clock assertion anywhere: the budget runs
on `_sender.parent_clock`, a fake clock driven by the test, and is judged by
how many heads the walk read.

- the budget runs out: the walk stops early, the send threads one hop, says
  so in ONE line, and still delivers;
- `SESSIONMAIL_PARENT_CHECK=off` skips the lookup entirely;
- a parent found within the budget threads exactly as before;
- notmuch is asked with no more time than is left, and not at all once spent;
- the MCP door's real child, with an unknown `in_reply_to`, sends and
  carries the notice back.

Fixtures are `eve` and `ivan` on `testhost` (convention 9).
"""

from __future__ import annotations

import itertools
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import _mcp, _sender, message
from test_sender import (FAKE_SENDMAIL, HOST, bin_dir, deliver, env, fake_sendmail,  # noqa: F401
                         headers_of, root, send, sender)

UNKNOWN = "<1907238896.9.9@elsewhere.session-mail>"
PLANTED = 2000
ONE_HOP = "threaded one hop"


def plant(root: Path, n: int = PLANTED, boxes: int = 20) -> None:
    """N small messages across BOXES boxes, none of them anyone's parent."""
    per = n // boxes
    for b in range(boxes):
        for sub in ("new", "cur", "tmp"):
            (root / f"box{b:02d}" / sub).mkdir(parents=True, exist_ok=True)
        for j in range(per):
            k = b * per + j
            (root / f"box{b:02d}" / ("cur" if j % 2 else "new") / f"1907238896.M{k}P1.{HOST}").write_text(
                f"From: ivan\nTo: box{b:02d}\nSubject: planted {k}\n"
                f"Message-ID: <1907238896.{k}.1@{HOST}.session-mail>\n\nbody\n")


def stepping(step: float):
    """A clock that moves STEP seconds every time it is read."""
    ticks = itertools.count()
    return lambda: next(ticks) * step


@pytest.fixture
def heads(monkeypatch):
    """How many heads the sender read (the walk's counter, kept by the test)."""
    count = [0]
    real = _sender._head

    def counted(path):
        count[0] += 1
        return real(path)

    monkeypatch.setattr(_sender, "_head", counted)
    return count


def target_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if "target" in line and "⚠" in line]


# ── the budget runs out ──────────────────────────────────────────────────────

def test_an_unknown_parent_stops_the_walk_at_the_budget_and_threads_one_hop(
        sender, env, tmp_path, fake_sendmail, root, heads, monkeypatch):
    plant(root)
    # 1 ms a read: the 1 s budget is gone after ~1000 reads of the clock.
    # raising=False: before the budget existed, this set an unused name and
    # the test fails on what the send did, not on the patch.
    monkeypatch.setattr(_sender, "parent_clock", stepping(0.001), raising=False)
    rc, out, err = send(sender, env, tmp_path, "--in-reply-to", UNKNOWN)
    assert rc == 0 and out.strip(), err                       # the mail still goes
    assert heads[0] < PLANTED, f"the walk read all {heads[0]} heads"
    h = dict(headers_of(fake_sendmail.wire))
    assert h["In-Reply-To"] == UNKNOWN                        # never changed
    assert h["References"] == UNKNOWN                         # one hop
    lines = target_lines(err)
    assert len(lines) == 1, err                               # ONE line, not two
    assert ONE_HOP in lines[0] and UNKNOWN in lines[0]
    assert f"({heads[0]} files read)" in lines[0]
    assert "NOT in the local index" not in err                # the walk did not finish


def test_a_supersede_and_a_reply_both_unfound_share_the_one_line(
        sender, env, tmp_path, fake_sendmail, root, monkeypatch):
    plant(root)
    monkeypatch.setattr(_sender, "parent_clock", stepping(0.001), raising=False)
    other = "<1907238897.9.9@elsewhere.session-mail>"
    rc, _, err = send(sender, env, tmp_path, "--in-reply-to", UNKNOWN, "--supersedes", other)
    assert rc == 0, err
    lines = target_lines(err)
    assert len(lines) == 1 and ONE_HOP in lines[0], err
    assert f"supersede target {other}" in lines[0] and f"reply target {UNKNOWN}" in lines[0]


def test_a_clock_that_never_moves_lets_the_walk_finish_as_before(
        sender, env, tmp_path, fake_sendmail, root, heads, monkeypatch):
    """The guard: with time to spare the walk reads everything and the
    notice is today's, word for word."""
    plant(root)
    monkeypatch.setattr(_sender, "parent_clock", lambda: 0.0, raising=False)
    rc, _, err = send(sender, env, tmp_path, "--in-reply-to", UNKNOWN)
    assert rc == 0, err
    assert heads[0] >= PLANTED
    assert "NOT in the local index" in err and ONE_HOP not in err


# ── PARENT_CHECK=off skips the lookup ────────────────────────────────────────

def test_parent_check_off_never_looks_the_parent_up_and_threads_one_hop(
        sender, env, tmp_path, fake_sendmail, root, monkeypatch):
    deliver(root, "eve", mid="<r@h.session-mail>", sender="ivan", to="eve", subject="root")
    deliver(root, "eve", mid="<a@h.session-mail>", sender="ivan", to="eve", subject="a",
            in_reply_to="<r@h.session-mail>", references=["<r@h.session-mail>"])

    def tripwire(*_a, **_k):
        raise AssertionError("the parent was looked up with PARENT_CHECK=off")

    monkeypatch.setattr(_sender, "find_messages", tripwire)
    monkeypatch.setattr(_sender, "notmuch_files", tripwire)
    env["SESSION_MAIL_PARENT_CHECK"] = env["SESSIONMAIL_PARENT_CHECK"] = "off"
    rc, _, err = send(sender, env, tmp_path, "--in-reply-to", "<a@h.session-mail>")
    assert rc == 0, err
    h = dict(headers_of(fake_sendmail.wire))
    assert h["In-Reply-To"] == "<a@h.session-mail>"
    assert h["References"] == "<a@h.session-mail>"            # one hop, as ruled
    assert target_lines(err) == []                            # off is silent


# ── a parent found within the budget: exactly as before ──────────────────────

def test_a_parent_found_within_the_budget_keeps_the_full_chain(
        sender, env, tmp_path, fake_sendmail, root, monkeypatch):
    plant(root, n=200, boxes=4)
    deliver(root, "eve", mid="<r@h.session-mail>", sender="ivan", to="eve", subject="root")
    deliver(root, "eve", mid="<a@h.session-mail>", sender="ivan", to="eve", subject="a",
            in_reply_to="<r@h.session-mail>", references=["<r@h.session-mail>"])
    monkeypatch.setattr(_sender, "parent_clock", stepping(0.001), raising=False)
    rc, _, err = send(sender, env, tmp_path, "--in-reply-to", "a@h.session-mail")
    assert rc == 0, err
    h = dict(headers_of(fake_sendmail.wire))
    assert h["In-Reply-To"] == "<a@h.session-mail>"
    assert h["References"] == "<r@h.session-mail>\n <a@h.session-mail>"
    assert target_lines(err) == []


# ── notmuch is asked inside the budget ───────────────────────────────────────

FAKE_NOTMUCH = """#!{python}
import sys
sys.exit(0)   # knows nothing
"""


@pytest.fixture
def notmuch_world(tmp_path, monkeypatch):
    """A fake notmuch on PATH and a host profile, and every notmuch call's
    `timeout=` recorded."""
    b, mail = tmp_path / "nmbin", tmp_path / "nmmail"
    b.mkdir()
    mail.mkdir()
    write_fake(b / "notmuch", FAKE_NOTMUCH.format(python=sys.executable))
    profile = tmp_path / ".config" / "notmuch" / "sessionkit" / "config"
    profile.parent.mkdir(parents=True)
    profile.write_text(f"[database]\npath={mail}\n")
    asked: list[float | None] = []
    real_run = subprocess.run

    def run(cmd, *a, **k):
        if cmd and str(cmd[0]).endswith("notmuch"):
            asked.append(k.get("timeout"))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(_sender.subprocess, "run", run)
    env = {"PATH": str(b), "HOME": str(tmp_path)}
    return mail, env, asked


def test_notmuch_gets_no_more_time_than_the_budget_has_left(notmuch_world):
    mail, env, asked = notmuch_world
    budget = _sender.ParentBudget(seconds=0.25, clock=lambda: 0.0)
    assert _sender.notmuch_files(mail, {UNKNOWN}, env, budget=budget) == {}
    assert asked == [0.25]                                    # not NOTMUCH_TIMEOUT (2.0)


def test_a_spent_budget_asks_notmuch_nothing(notmuch_world):
    mail, env, asked = notmuch_world
    budget = _sender.ParentBudget(seconds=1.0, clock=stepping(2.0))
    assert _sender.notmuch_files(mail, {UNKNOWN}, env, budget=budget) == {}
    assert asked == [] and budget.spent


def test_without_a_budget_notmuch_keeps_its_own_timeout(notmuch_world):
    mail, env, asked = notmuch_world
    assert _sender.notmuch_files(mail, {UNKNOWN}, env) == {}
    assert asked == [_sender.NOTMUCH_TIMEOUT]


# ── the MCP door (SM-880): its real child is bounded the same way ────────────

#: The real `message send`, started as `-m` would, with the budget's clock
#: replaced first: the one change a test needs to make inside the child.
CHILD = ("import itertools, runpy, sys; from sessionmail import _sender; "
         "t = itertools.count(); _sender.parent_clock = lambda: next(t) * 0.001; "
         "sys.argv = ['message', *sys.argv[1:]]; "
         "runpy.run_module('sessionmail.message', run_name='__main__', alter_sys=True)")


def test_the_mcp_send_with_an_unknown_in_reply_to_sends_and_says_one_hop(tmp_path, monkeypatch):
    from test_mcp_send import call, texts
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    wire = tmp_path / "wire"
    mail = tmp_path / "mail"
    plant(mail / "stores")
    for name in ("eve", "ivan"):
        for leaf in ("new", "cur", "tmp"):
            (mail / "stores" / name / leaf).mkdir(parents=True, exist_ok=True)
    for var in ("SESSIONMAIL_SEND_ORACLE", "CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK",
                "CLAUDE_SESSION_PUB", "FAKE_SENDMAIL_RC", "PYTHONPATH",
                "SESSIONMAIL_PARENT_CHECK", "SESSION_MAIL_PARENT_CHECK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(tmp_path / "argv"))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(mail))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "ivan")
    monkeypatch.setattr(_mcp, "SEND_COMMAND", [sys.executable, "-P", "-c", CHILD, "send"])
    result, _ = call({"to": ["eve"], "subject": "the notice", "body": "words\n",
                      "in_reply_to": UNKNOWN})
    blocks = texts(result)
    assert result.is_error is False, blocks
    assert blocks[0].startswith("<") and blocks[0].endswith(">")    # the Message-ID alone
    assert ONE_HOP in "\n".join(blocks[1:]), blocks
    h = dict(headers_of(wire.read_bytes()))
    assert h["In-Reply-To"] == UNKNOWN and h["References"] == UNKNOWN


def test_the_message_module_is_what_the_door_runs():
    """The wrapper above stands in for SEND_COMMAND; it must run the same module."""
    assert _mcp.SEND_COMMAND[-2:] == ["sessionmail.message", "send"]
    assert message.__name__ == "sessionmail.message"
