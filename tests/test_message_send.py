"""`message send` - the click door, with the bash oracle behind it.

Hermetic: the oracle is a FAKE named by $SESSIONMAIL_SEND_ORACLE (an
absolute path with a full-path shebang, so it starts whatever $PATH is -
the trap a fake with `#!/usr/bin/env python3` fell into today). It logs
the argv it was handed and prints a Message-ID, or fails on request, so
both roads are watched (Charter 3). Nothing here sends mail.

Pinned (specs/message):
  - a subject over 69 chars, or non-ASCII, or that could execute, is refused
    with exit 2 BEFORE the oracle is called; SESSIONMAIL_SUBJECT_GUARD=off
    lets the executable one through byte for byte;
  - no body and no EOM is usage; an EOM subject implies --no-body;
  - a comma or line break in a recipient, or the HTML-escaped id form, is
    refused before the oracle;
  - the oracle's 2 and 3 are the door's 2 and 3, and its stderr is shown;
  - `-f -` is stdin as BYTES into a private file the oracle reads.
"""

from __future__ import annotations

import os
import socket
import stat
import sys

import pytest
from click.testing import CliRunner

from sessionmail.message import cli

# Usage is 2, not the page's 1: click's UsageError exits 2 and the built `mark`
# verb pinned that (test_message_mark). The page-vs-click gap is filed (ASKS 1410.4).
EX_OK, EX_USAGE, EX_REFUSED, EX_MTA = 0, 2, 2, 3
HOST = socket.gethostname().split(".")[0].lower()


# SM-648: THE HOST IS DERIVED, NEVER PINNED. These tests once spelt "this
# host" as a literal `@hq`, while the code reads `socket.gethostname()`; when
# the fleet moved hq -> nas, nine of them went red on every host but hq.
# Every case that needs THIS host's spelling takes the `host` fixture, which
# runs it twice: on the real host, and on one it can never be (`not-<host>`).
# A literal pinned host fails at least one of the two, so the next rename
# cannot hide behind the machine the suite happens to run on.
@pytest.fixture(params=["this-host", "another-host"])
def host(request, monkeypatch):
    """This host's short name, as the code under test will read it."""
    name = HOST if request.param == "this-host" else f"not-{HOST}"
    monkeypatch.setattr(socket, "gethostname", lambda: name)
    return name


FAKE_ORACLE = """#!{python}
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
i = sys.argv.index("--body-file") + 1 if "--body-file" in sys.argv else None
if i and os.environ.get("FAKE_ECHO_BODY"):
    sys.stdout.write(open(sys.argv[i], "rb").read().decode("utf-8", "replace"))
code = int(os.environ.get("FAKE_FAIL") or 0)
if code:
    sys.stderr.write("session-mail-send: refused by the fake\\n")
    sys.exit(code)
if os.environ.get("FAKE_ENV_LOG"):
    with open(os.environ["FAKE_ENV_LOG"], "w") as f:
        f.write(os.environ.get("SESSION_MAIL_RENDERER", ""))
sys.stderr.write("session-mail-send: sent via fake\\n")
sys.stdout.write("<sent@example.invalid>\\n")
"""


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    fake = tmp_path / "fake-send"
    fake.write_text(FAKE_ORACLE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(fake))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    monkeypatch.delenv("FAKE_ECHO_BODY", raising=False)
    monkeypatch.delenv("SESSIONMAIL_SUBJECT_GUARD", raising=False)
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    body = tmp_path / "body.md"
    body.write_text("dear beta\n")

    class O:
        def calls(self):
            return [l.split("\x1f") for l in log.read_text().splitlines()] if log.exists() else []
    o = O(); o.body = body
    return o


def run(*args, **kw):
    return CliRunner().invoke(cli, ["send", *args], **kw)


def test_happy_path_hands_the_oracle_the_argv_and_prints_its_id(oracle):
    r = run("beta", "-s", "hello", "-f", str(oracle.body))
    assert r.exit_code == EX_OK, r.output
    assert r.output.strip().endswith("<sent@example.invalid>")
    assert oracle.calls() == [["beta", "--from", "alpha", "--subject", "hello",
                               "--body-file", str(oracle.body)]]


def test_a_70_char_subject_is_refused_before_the_oracle(oracle):
    r = run("beta", "-s", "x" * 70, "-f", str(oracle.body))
    assert r.exit_code == EX_REFUSED
    assert "70 characters" in r.output and "limit is 69" in r.output
    assert oracle.calls() == []


def test_a_trailing_eom_is_not_counted_in_the_69(oracle):
    """eom-not-counted: the 69 excludes a trailing ' EOM', so 69 plus the marker sends."""
    subject = "x" * 69 + " EOM"
    r = run("beta", "-s", subject, "-f", str(oracle.body))
    assert r.exit_code == EX_OK, r.output
    assert oracle.calls()[0][4] == subject


def test_the_ruperts_subject_from_01_13_sends(oracle):
    """The receipt behind the fix: 66 characters and ' EOM', refused as 70."""
    subject = "849c289 PASS: a gone link falls back with the NOTE; realpath holds EOM"
    r = run("beta", "-s", subject, "-f", str(oracle.body))
    assert r.exit_code == EX_OK, r.output
    assert oracle.calls()[0][4] == subject


def test_70_before_the_eom_is_refused_and_says_the_marker_was_not_counted(oracle):
    r = run("beta", "-s", "x" * 70 + " EOM", "-f", str(oracle.body))
    assert r.exit_code == EX_REFUSED
    assert "70 characters" in r.output and "not counting ' EOM'" in r.output
    assert "limit is 69" in r.output
    assert oracle.calls() == []


def test_a_non_ascii_subject_is_refused_with_the_body_as_remedy(oracle):
    r = run("beta", "-s", "a — b", "-f", str(oracle.body))
    assert r.exit_code == EX_REFUSED and "not ASCII" in r.output and "in the body" in r.output
    assert oracle.calls() == []


def test_an_executable_subject_is_refused_unless_the_seat_switched_the_guard_off(oracle, monkeypatch):
    r = run("beta", "-s", "run `ls`", "-f", str(oracle.body))
    assert r.exit_code == EX_REFUSED and "could execute" in r.output and oracle.calls() == []
    monkeypatch.setenv("SESSIONMAIL_SUBJECT_GUARD", "off")
    r = run("beta", "-s", "run `ls`", "-f", str(oracle.body))
    assert r.exit_code == EX_OK and oracle.calls()[0][4] == "run `ls`"


def test_no_body_is_usage_but_an_eom_subject_implies_no_body(oracle):
    r = run("beta", "-s", "hello")
    assert r.exit_code == EX_USAGE and oracle.calls() == []
    r = run("beta", "-s", "lunch is ready EOM")
    assert r.exit_code == EX_OK and "--no-body" in oracle.calls()[0]


def test_recipients_with_a_comma_or_newline_are_refused_before_the_oracle(oracle):
    for bad in ("beta,gamma", "beta\ngamma"):
        r = run(bad, "-s", "hi", "-f", str(oracle.body))
        assert r.exit_code == EX_REFUSED and "one address per flag" in r.output
    r = run("beta", "--cc", "x\ny", "-s", "hi", "-f", str(oracle.body))
    assert r.exit_code == EX_REFUSED and oracle.calls() == []


def test_the_html_escaped_id_form_is_refused_before_the_oracle(oracle):
    r = run("beta", "-s", "hi", "-f", str(oracle.body), "-r", "&lt;id@example.invalid&gt;")
    assert r.exit_code == EX_REFUSED and "HTML-escaped" in r.output and oracle.calls() == []


def test_threading_cc_bcc_and_headers_reach_the_oracle(oracle):
    r = run("beta", "gamma", "-s", "hi", "-f", str(oracle.body), "-r", "p@example.invalid",
            "--supersedes", "<q@example.invalid>", "--cc", "delta", "--bcc", "eps",
            "--header", "X-Task=SM-18")
    assert r.exit_code == EX_OK, r.output
    call = oracle.calls()[0]
    assert call[0] == "beta" and "--in-reply-to" in call and "--supersedes" in call
    assert call[call.index("--cc") + 1] == "gamma"   # the second To rides as Cc
    assert "delta" in call and call[call.index("--bcc") + 1] == "eps"
    assert call[-2:] == ["--task", "SM-18"]
    assert "go as Cc" in r.output


def test_a_header_send_writes_itself_is_usage(oracle):
    # SM-402: any header rides now (tests/test_message_send_json.py); only
    # the transport's own are refused, naming the flag that sets them.
    r = run("beta", "-s", "hi", "-f", str(oracle.body), "--header", "From: x")
    assert r.exit_code == EX_USAGE and "--from" in r.output and oracle.calls() == []


def test_the_oracles_2_and_3_are_the_doors_2_and_3_with_its_stderr(oracle, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "2")
    r = run("beta", "-s", "hi", "-f", str(oracle.body))
    assert r.exit_code == EX_REFUSED and "refused by the fake" in r.output
    monkeypatch.setenv("FAKE_FAIL", "3")
    assert run("beta", "-s", "hi", "-f", str(oracle.body)).exit_code == EX_MTA


def test_raw_reaches_the_oracle(oracle):
    r = run("beta", "-s", "a log", "-f", str(oracle.body), "--raw")
    assert r.exit_code == EX_OK, r.output
    assert "--raw" in oracle.calls()[-1]                               # 4b, decision 2 switch 4


def test_render_off_for_the_seat_is_raw_in_the_oracles_words(oracle, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_RENDER", "off")
    r = run("beta", "-s", "hello", "-f", str(oracle.body))
    assert r.exit_code == EX_OK, r.output
    assert "--raw" in oracle.calls()[-1]                               # decision 2 switch 3


def test_a_named_renderer_reaches_the_bash_as_its_variable_not_a_flag(oracle, monkeypatch, tmp_path):
    envlog = tmp_path / "env.log"
    monkeypatch.setenv("FAKE_ENV_LOG", str(envlog))
    monkeypatch.delenv("SESSION_MAIL_RENDERER", raising=False)
    monkeypatch.delenv("SESSIONMAIL_RENDERER", raising=False)
    r = run("beta", "-s", "hello", "-f", str(oracle.body), "--renderer", "/opt/bin/my-render")
    assert r.exit_code == EX_OK, r.output
    assert "--renderer" not in oracle.calls()[-1]                      # the bash has no such flag
    assert envlog.read_text() == "/opt/bin/my-render"                  # decision 2 switch 2


def test_the_oracles_4_is_the_doors_4(oracle, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "4")
    r = run("judy", "-s", "report", "-f", str(oracle.body))
    assert r.exit_code == 4                                            # spec: exit 4, never folded to 3
    assert "refused by the fake" in r.output


def test_stdin_body_arrives_as_bytes(oracle, monkeypatch):
    monkeypatch.setenv("FAKE_ECHO_BODY", "1")
    r = run("beta", "-s", "hi", "-f", "-", input="keep `this` and $(that)\n")
    assert r.exit_code == EX_OK
    assert "keep `this` and $(that)" in r.output
    spooled = oracle.calls()[0][6]
    assert not os.path.exists(spooled), "the private stdin file is removed after the send"


def test_no_identity_is_usage_there_is_no_from(oracle, monkeypatch):
    from sessionmail import _seat
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    r = run("beta", "-s", "hi", "-f", str(oracle.body))
    assert r.exit_code == EX_USAGE and "no --from" in r.output and oracle.calls() == []


# ── the From keeps the seat's plus extension ─────────────────────────────
#
# THE DOORBELL COULD REACH A SEAT THAT COULD NOT ANSWER AS ITSELF. Measured
# on a task seat: mail addressed to
# `sessionmail+sm-122.foreman` arrived in its box, it replied with
# `message send -r <parent>`, and the reply went out `From: sessionmail@hq`
# - as the PROJECT, not as the seat. Its environment was already correct
# (`EMAIL`, `TM_AGENT_ADDRESS` and `<session>/address` all carried the
# extension); the door asked the seat for `.name`, which drops the `+` by
# design because a name is a DIRECTORY. `.address` is who you are.
#
# Both directions are pinned, because the failure is silent in both: a
# stripped `+` looks like ordinary project mail, and a `+` grown on a seat
# that never declared one would send as an address postfix does not route.

def test_a_task_seat_answers_as_itself_the_from_keeps_the_plus(oracle, monkeypatch, host):
    monkeypatch.setenv("EMAIL", f"alpha+sm-122.foreman@{host}.example.ts.net")
    r = run("beta", "-s", "report", "-f", str(oracle.body))
    assert r.exit_code == EX_OK, r.output
    assert oracle.calls() == [["beta", "--from", "alpha+sm-122.foreman",
                               "--subject", "report", "--body-file", str(oracle.body)]]


def test_a_seat_that_declares_no_plus_still_sends_as_its_bare_name(oracle, monkeypatch):
    monkeypatch.delenv("EMAIL", raising=False)
    r = run("beta", "-s", "report", "-f", str(oracle.body))
    assert r.exit_code == EX_OK, r.output
    assert oracle.calls() == [["beta", "--from", "alpha", "--subject", "report",
                               "--body-file", str(oracle.body)]]


# ── a reply answers from the address it was mailed at ────────────────────
#
# A SESSION ID IS A DM; A SEAT ADDRESS IS ON THE RECORD. A reply from a seat
# to a quiet direct message is like a reply to a DM from a work address: it
# puts the exchange on the record.
#
# Answering a DM from the seat silently promotes a private exchange to an
# official one, and the sender - who chose the private door - is the last to
# know. That is why every fall-back below is ANNOUNCED: the failure is
# invisible in the message itself.
#
# The address comparison is on LOCAL PARTS (specs/pager, already ratified:
# "grouped by their LOCAL PART, the host discarded, and the +extension KEPT").
# The bare/@<host>/FQDN case below is the one that makes it matter - a verbatim
# comparison misses it and falls back to the seat BY ACCIDENT, which is the
# leak this requirement exists to prevent.

def _parent(root, box, *, mid, original_to):
    d = root / box
    for sub in ("tmp", "new", "cur"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    raw = ("\r\n".join([f"X-Original-To: {original_to}",
                        "From: someone@example.invalid",
                        f"To: {box}@example.invalid",
                        "Date: Tue, 01 Jan 2030 12:00:00 +0000",
                        "Subject: parent",
                        f"Message-ID: <{mid}>"]) + "\r\n\r\nbody\r\n")
    (d / "new" / "1923171201.M1P1.host").write_text(raw)
    return mid


@pytest.fixture
def replying(oracle, tmp_path, monkeypatch, host):
    """A session with BOTH a seat address and a session id, and a box for each."""
    root = tmp_path / "mail"
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha+sm-9.foreman")
    monkeypatch.delenv("EMAIL", raising=False)
    oracle.root = root
    return oracle


def test_a_dm_is_answered_privately_from_the_session_id(replying):
    mid = _parent(replying.root, "sess-1", mid="p1@x", original_to="sess-1")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "sess-1"], replying.calls()


def test_seat_mail_is_answered_on_the_record_from_the_seat(replying, host):
    mid = _parent(replying.root, "sess-1", mid="p2@x",
                  original_to=f"alpha+sm-9.foreman@{host}")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]


def test_the_host_qualified_spelling_still_matches_the_session_id(replying, host):
    # The accident this guards: a verbatim comparison misses bare vs FQDN and
    # falls back to the SEAT - a DM answered from the work address.
    mid = _parent(replying.root, "sess-1", mid="p3@x",
                  original_to=f"sess-1@{host}.example.ts.net")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "sess-1"]


def test_a_fresh_send_is_unaffected_and_uses_the_declared_address(replying):
    r = run("beta", "-s", "fresh", "-f", str(replying.body))
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]


def test_a_parent_delivered_somewhere_else_falls_back_AND_SAYS_SO(replying, host):
    mid = _parent(replying.root, "sess-1", mid="p4@x", original_to=f"stranger@{host}")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]
    assert "does not answer to" in r.output and f"stranger@{host}" in r.output


def test_a_parent_with_no_x_original_to_falls_back_AND_SAYS_SO(replying):
    d = replying.root / "sess-1"
    for sub in ("tmp", "new", "cur"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "new" / "1923171202.M1P1.host").write_text(
        "From: someone@example.invalid\r\nSubject: no xot\r\n"
        "Date: Tue, 01 Jan 2030 12:00:00 +0000\r\nMessage-ID: <p5@x>\r\n\r\nbody\r\n")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", "<p5@x>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]
    assert "no X-Original-To" in r.output


def test_an_unfindable_parent_falls_back_AND_SAYS_SO(replying):
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", "<nowhere@x>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]
    assert "not in this session's boxes" in r.output


# ── a mention suffix still reaches us, and is answered as ourselves ──────
#
# MEASURED, not imagined: `catalog+super+boot` (15 messages on this host),
# `show-me+super+boot`, `show-me+show-230.foreman+boot`,
# `kaizen+taskmaster+session`. The boot shelf and its kin are real delivered
# traffic, and a seat named `catalog+super` plainly answers to them.
#
# Two reasons this is not cosmetic. Without it, every reply to boot-shelf mail
# printed "this session does not answer to ..." about an address postfix had
# just delivered to it - a warning firing on ROUTINE traffic, which is how a
# warning stops being read, and the note it would crowd out is the privacy
# one. And for a SESSION ID it IS the privacy rule: `<sid>+<suffix>` is still
# a DM, and without this it would be answered from the seat.

def test_a_mention_of_the_session_id_is_still_a_dm(replying):
    mid = _parent(replying.root, "sess-1", mid="p6@x", original_to="sess-1+role")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "sess-1"], replying.calls()
    assert "does not answer to" not in r.output


def test_a_mention_of_the_seat_is_answered_as_the_seat_not_the_mention(replying, host):
    mid = _parent(replying.root, "sess-1", mid="p7@x",
                  original_to=f"alpha+sm-9.foreman+boot@{host}")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    # answered AS the seat - nobody replies from `...+boot`
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]
    assert "does not answer to" not in r.output


def test_the_match_is_on_a_plus_boundary_not_a_bare_prefix(replying, host):
    # `alpha+sm-9.foreman` must not swallow `alpha+sm-9.foremanX`.
    mid = _parent(replying.root, "sess-1", mid="p8@x",
                  original_to=f"alpha+sm-9.foremanX@{host}")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "alpha+sm-9.foreman"]
    assert "does not answer to" in r.output


# The lookup is ONE file, not a parse of both boxes: every CLI reply spent
# 4.3 s here parsing 11,773 mails to read one header (profiled). It now
# finds the parent the way the sender does (_sender.find_parents: notmuch, then the walk), scoped to each box.

def test_a_reply_parses_no_mail_to_find_its_parent(replying, monkeypatch):
    from sessionmail import _maildir

    def parse(*a, **k):
        raise AssertionError("a reply parsed a whole box to find one parent")
    monkeypatch.setattr(_maildir, "parse", parse)
    mid = _parent(replying.root, "sess-1", mid="p9@x", original_to="sess-1")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "sess-1"]


def test_a_copy_notmuch_finds_in_ANOTHER_box_is_never_the_answer(replying, tmp_path,
                                                                  monkeypatch, host):
    # A Cc'd copy in another seat's box carries THAT seat's X-Original-To. If
    # the index names it first, it must be passed over for this box's copy.
    mid = _parent(replying.root, "sess-1", mid="p10@x", original_to="sess-1")
    _parent(replying.root, "other", mid=mid, original_to=f"stranger@{host}")
    theirs = replying.root / "other" / "new" / "1923171201.M1P1.host"
    ours = replying.root / "sess-1" / "new" / "1923171201.M1P1.host"
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    fake = bindir / "notmuch"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' '{theirs}' '{ours}'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    r = run("beta", "-s", "re", "-f", str(replying.body), "-r", f"<{mid}>")
    assert r.exit_code == EX_OK, r.output
    assert replying.calls()[0][:3] == ["beta", "--from", "sess-1"]
    assert "does not answer to" not in r.output


# ── SM-716 do-003: `--message-id` at the click door (design §6, §10 do-003) ──
# The core's refusals are tests/test_sender.py's (do-002); the --json and MCP
# parity is do-004's. Here: the option is on the page, a chosen id is what
# the door prints and what the wire carries (SC1), the flag takes the
# package's sender even with the bash named and says why (SC7), and the
# `--header` door stays shut on Message-ID (SC9). "Nothing sent" is two
# things: the fake sendmail never ran AND the bash oracle never ran.

import json

from fakes import write_fake
from test_sender import FAKE_SENDMAIL, HOST as OWNHOST

NOT_CONSULTED = "SESSIONMAIL_SEND_ORACLE is not consulted"


@pytest.fixture
def chosen(oracle, tmp_path, monkeypatch):
    """The `oracle` fixture's recorder bash, AND the package's road behind a
    fake sendmail, on an UNKEYED seat that need not sign and does not drop:
    every reason but `--message-id` to pass the bash over is switched off.
    The bash stays named; a test that wants it unset unsets it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    argv, wire = tmp_path / "sendmail.argv", tmp_path / "sendmail.wire"
    root = tmp_path / "mail"
    root.mkdir()
    for var in ("CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB",
                "SESSIONMAIL_REQUIRE_SIGNATURE", "FAKE_SENDMAIL_RC", "SESSIONMAIL_RENDER",
                "SESSIONMAIL_PARENT_CHECK", "SESSION_MAIL_PARENT_CHECK",
                "SESSIONMAIL_SUBJECT_SHORTEN", "SESSION_MAIL_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(argv))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOST", OWNHOST)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    oracle.sendmail_ran = argv.exists
    oracle.wire = lambda: wire.read_bytes() if wire.exists() else None
    return oracle


def _nothing_sent(w) -> bool:
    return not w.sendmail_ran() and w.wire() is None and w.calls() == []


def test_the_page_lists_message_id_and_says_the_host_rule():
    """SC10, and §16 F2: the help keeps three facts - the id is NEW, not the
    reply target; the part after @ is this host's bare short name; and it
    MUST be this host. The epilog says what is refused."""
    r = CliRunner().invoke(cli, ["send", "--help"])
    assert r.exit_code == 0
    page = " ".join(r.output.split())          # click wraps; read it unwrapped
    assert "--message-id ID" in page
    assert "a NEW id for this mail, never the one it replies to" in page
    assert "the part after @ must be this host's bare short name: <x@HOST>" in page
    assert "ending .session-mail" in page and "a malformed or foreign id" in page


def test_the_tools_message_id_is_described_by_the_same_words():
    """§6: the option's help IS the MCP tool's description of `message_id`,
    the one sentence a model reads beside `in_reply_to`."""
    from sessionmail import _mcp

    option = next(p for p in cli.commands["send"].params if p.name == "message_id")
    props = _mcp.server(cli, "message")._tool_manager.get_tool("message_send").parameters["properties"]
    assert props["message_id"]["description"] == option.help
    assert "NEW" in option.help and "must be this host" in option.help


@pytest.mark.parametrize("given", [f"<x.1@{OWNHOST}>", f"x.1@{OWNHOST}"],
                         ids=["brackets", "bare"])
def test_a_chosen_id_is_printed_and_is_the_one_message_id_on_the_wire(chosen, monkeypatch,
                                                                      given):
    """SC1 at the door that names it: `message send T -s S --no-body
    --message-id ...`, with and without the angle brackets."""
    monkeypatch.delenv("SESSIONMAIL_SEND_ORACLE")
    r = run("beta", "-s", "S", "--no-body", "--message-id", given)
    assert r.exit_code == EX_OK, r.output
    assert r.stdout.strip().splitlines()[-1] == f"<x.1@{OWNHOST}>", "the id alone on stdout"
    head = chosen.wire().split(b"\n\n", 1)[0]
    ids = [l for l in head.split(b"\n") if l.lower().startswith(b"message-id:")]
    assert ids == [f"Message-ID: <x.1@{OWNHOST}>".encode()], head
    assert chosen.calls() == []


def test_with_the_bash_named_the_flag_takes_the_package_and_says_why(chosen):
    """SC7: oracle named, seat unkeyed. Without the flag the bash takes the
    send (the control: nothing else diverts it); with it, the package's
    sender does, under the chosen id, and ONE notice names --message-id. The
    recorder bash never sees the flag."""
    r = run("beta", "-s", "S", "--no-body")
    assert r.exit_code == EX_OK and len(chosen.calls()) == 1, r.output
    assert not chosen.sendmail_ran() and NOT_CONSULTED not in r.output, "control: the bash road"

    r = run("beta", "-s", "S", "--no-body", "--message-id", f"x.2@{OWNHOST}")
    assert r.exit_code == EX_OK, r.output
    assert r.stdout.strip().splitlines()[-1] == f"<x.2@{OWNHOST}>"
    assert f"Message-ID: <x.2@{OWNHOST}>".encode() in chosen.wire()
    assert len(chosen.calls()) == 1, "the bash was not started a second time"
    ours = [l for l in r.stderr.splitlines() if NOT_CONSULTED in l]
    assert ours == ["message send: --message-id (the bash has no such flag) goes by the "
                    "package's own sender; $SESSIONMAIL_SEND_ORACLE is not consulted"], r.stderr
    assert not any("--message-id" in a for call in chosen.calls() for a in call)


@pytest.mark.parametrize("given", ["", "no-at-sign", "x@other.example",
                                   f"x@{OWNHOST}.session-mail"],
                         ids=["empty", "malformed", "foreign", "minted-form"])
def test_with_the_bash_named_a_refused_id_never_falls_through_to_it(chosen, given):
    """SC7's other half: a refused id is refused by the package (exit 2,
    kind message-id, nothing sent), never handed to the bash with the flag
    lost. The EMPTY id is the case a truthiness gate would let through."""
    r = run("beta", "-s", "S", "--no-body", "--message-id", given)
    assert r.exit_code == EX_REFUSED, r.output
    assert "--message-id" in r.stderr and _nothing_sent(chosen), r.output
    r = run("beta", "-s", "S", "--no-body", "--message-id", given, "--json")
    said = json.loads(r.stdout)
    assert r.exit_code == EX_REFUSED and said["sent"] == "no" and said["kind"] == "message-id"
    assert _nothing_sent(chosen)


def test_json_is_named_before_message_id_when_both_divert(chosen):
    """The why is ONE reason, the first that applies: --json, as before."""
    r = run("beta", "-s", "S", "--no-body", "--message-id", f"x.3@{OWNHOST}", "--json")
    said = json.loads(r.stdout)
    assert r.exit_code == EX_OK and said["id"] == f"<x.3@{OWNHOST}>", said
    ours = [n for n in said["notices"] if NOT_CONSULTED in n]
    assert len(ours) == 1 and ours[0].startswith("message send: --json goes by"), ours


@pytest.mark.parametrize("named", [True, False], ids=["bash-named", "bash-unset"])
@pytest.mark.parametrize("flag", [[], ["--message-id", f"x.4@{OWNHOST}"]],
                         ids=["header-alone", "with-the-flag"])
def test_a_message_id_header_is_still_refused_and_nothing_is_sent(chosen, monkeypatch,
                                                                  named, flag):
    """SC9 (R6): `--header 'Message-ID: ...'` exits 2, kind `header`, nothing
    sent - and the new flag does not open that door. Review note N5: the
    OWNED words are NOT asserted; they are stale on landing and SM-760's."""
    if not named:
        monkeypatch.delenv("SESSIONMAIL_SEND_ORACLE")
    given = f"Message-ID: <x@{OWNHOST}>"
    r = run("beta", "-s", "S", "--no-body", "--header", given, *flag)
    assert r.exit_code == EX_REFUSED and _nothing_sent(chosen), r.output
    r = run("beta", "-s", "S", "--no-body", "--header", given, *flag, "--json")
    said = json.loads(r.stdout)
    assert r.exit_code == EX_REFUSED and said["sent"] == "no" and said["kind"] == "header", said
    assert _nothing_sent(chosen)
