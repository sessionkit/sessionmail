"""`message send --json`: one object on stdout, and the PYTHON sender behind it.

openspec/changes/agents-send-door. RULED Q05: a refusal says what KIND it
is. RULED option A, amending send-in-python decision 8: WITH `--json` the
door calls the package's own `send()` in-process, because the bash oracle has
no kinds; WITHOUT it nothing changes - `tests/test_message_send.py` is that
half, and it is not edited.

Hermetic the way `test_sender.py` is: a fake `sendmail` capturing the wire,
fake `postconf`/`postmap`, a temp mail root. And an oracle that FAILS LOUDLY
if it is ever started, so "no oracle was looked for" is watched, not assumed.
"""

from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail.message import cli
from test_sender import FAKE_POSTCONF, FAKE_POSTMAP, FAKE_SENDMAIL, HOST

TRIPWIRE = """#!{python}
import os
open(os.environ["TRIPWIRE_LOG"], "w").write("the oracle was started")
raise SystemExit(99)
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    wire, tripped = tmp_path / "sendmail.wire", tmp_path / "tripwire.log"
    root = tmp_path / "mail"
    root.mkdir()
    for var in ("SESSIONMAIL_SEND_ORACLE", "SESSIONMAIL_SUBJECT_GUARD", "CLAUDE_CODE_AGENT",
                "CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB", "FAKE_SENDMAIL_RC",
                "SESSIONMAIL_RENDER", "SESSIONMAIL_PARENT_CHECK", "SESSION_MAIL_PARENT_CHECK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(tmp_path / "sendmail.argv"))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setenv("TRIPWIRE_LOG", str(tripped))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    # `session-mail-send` on the PATH is the door's second oracle leg. A
    # tripwire there proves --json never looks for one.
    write_fake(bin_dir / "session-mail-send", TRIPWIRE)
    body = tmp_path / "body.md"
    body.write_text("dear beta\n")

    class World:
        pass

    w = World()
    w.body, w.bin, w.tmp = body, bin_dir, tmp_path
    w.wire = lambda: wire.read_bytes() if wire.exists() else None
    w.tripped = lambda: tripped.exists()
    return w


def run(*args: str, input: bytes | None = None):
    result = CliRunner().invoke(cli, ["send", *args], input=input)
    try:
        said = json.loads(result.stdout)
    except ValueError:
        said = None
    return result, said


def test_a_send_is_one_object_with_sent_yes_the_id_and_the_notices(world):
    r, said = run("beta", "-s", "the point", "-f", str(world.body), "--json")
    assert r.exit_code == 0 and r.stderr == "", "with --json stdout carries everything"
    assert said["sent"] == "yes" and said["id"].endswith(f"@{HOST}.session-mail>")
    assert any("sent via smtp" in n for n in said["notices"])
    assert any("UNSIGNED" in n for n in said["notices"]), "a warning rides in the object"
    assert world.wire().endswith(b"\n\ndear beta\n") and b"From: Alpha <alpha>\n" in world.wire()   # SM-398: the seat's short name
    assert not world.tripped(), "with --json no oracle is looked for"


def test_without_the_flag_the_package_sends_and_no_bash_is_started(world):
    """One sender, in Python. Unset = the
    package's own sender; a `session-mail-send` on the PATH is never started."""
    r, _ = run("beta", "-s", "the point", "-f", str(world.body))
    assert r.exit_code == 0 and not world.tripped(), "unset = the package's sender"
    assert world.wire().endswith(b"\n\ndear beta\n")
    assert r.stdout.strip().splitlines()[-1].endswith(f"@{HOST}.session-mail>"), "the id alone on stdout"


def test_a_stranger_with_no_session_anywhere_sends(world):
    """send-in-python: "A stranger's install sends" - no `session`, no
    `session-mail-send`, nothing named."""
    (world.bin / "session-mail-send").unlink()
    r, _ = run("beta", "-s", "the point", "-f", str(world.body))
    assert r.exit_code == 0 and world.wire() is not None


def test_a_named_oracle_still_takes_a_plain_send_for_one_release(world, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    r, _ = run("beta", "-s", "the point", "-f", str(world.body))
    assert world.tripped() and world.wire() is None, "named = the bash, for the diff"
    assert r.exit_code == 3


def _keyed_seat(world, monkeypatch):
    """Enough for `_sender.keyed()`: an agent socket named and a public key FILE. There is
    no ssh-keygen on this world's PATH, so the send goes unsigned and says so; the v2
    signature on every door is test_sender_v2's to prove. This proves the ROUTE."""
    pub = world.tmp / "session-key.pub"
    pub.write_text("ssh-ed25519 AAAA test\n")
    monkeypatch.setenv("CLAUDE_SESSION_AGENT_SOCK", str(world.tmp / "agent.sock"))
    monkeypatch.setenv("CLAUDE_SESSION_PUB", str(pub))


@pytest.mark.parametrize("as_json", [False, True], ids=["cli", "json"])
def test_a_keyed_seat_never_takes_the_bash_and_says_why(world, monkeypatch, as_json):
    """SM-594 W2 (super O1): the bash signs v1 only, so a KEYED seat's send goes by the
    package's sender (v2), never the named oracle - said out loud, once, with the reason."""
    _keyed_seat(world, monkeypatch)
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    r, said = run("beta", "-s", "s", "-f", str(world.body), *(["--json"] if as_json else []))
    assert r.exit_code == 0 and not world.tripped(), r.output
    assert world.wire() is not None, "the mail still goes"
    notices = said["notices"] if as_json else r.stderr.splitlines()
    ours = [n for n in notices if "SESSIONMAIL_SEND_ORACLE is not consulted" in n]
    assert len(ours) == 1, notices
    if not as_json:
        assert "the bash signs v1 only" in ours[0], ours


def test_an_unkeyed_seat_still_takes_the_named_bash(world, monkeypatch):
    """Unkeyed, neither road signs, so W2 leaves the one-release diff alone."""
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    monkeypatch.setenv("CLAUDE_SESSION_AGENT_SOCK", str(world.tmp / "agent.sock"))  # no PUB file
    run("beta", "-s", "s", "-f", str(world.body))
    assert world.tripped(), "unkeyed: the bash, as before"


@pytest.mark.parametrize("args, kind, code", [
    (["beta", "-s", "x" * 70], "subject", 2),
    (["beta", "-s", "a `tick`"], "subject", 2),
    (["beta,gamma", "-s", "s"], "address", 2),
    (["beta", "-s", "s", "-r", "&lt;id@host&gt;"], "thread-id", 2),
    (["beta", "-s", "s", "-r", "not an id"], "thread-id", 2),
    (["beta", "-s", "s", "--header", "From=x"], "header", 2),
], ids=["too-long", "could-execute", "comma", "escaped-id", "malformed-id", "header-owned"])
def test_an_input_refusal_names_the_input_to_fix(world, args, kind, code):
    r, said = run(*args, "-f", str(world.body), "--json")
    assert r.exit_code == code and said["sent"] == "no" and said["kind"] == kind, r.output
    assert said["message"] and world.wire() is None and not world.tripped()


@pytest.mark.parametrize("args, kind, message", [
    (["beta", "-s", "s"], "body", "no body"),
    (["beta", "-s", "s", "--no-body", "-f", "BODY"], "usage", "together"),
], ids=["no-body-and-not-EOM", "both"])
def test_a_usage_refusal_is_an_object_too_in_words_that_fit_both_doors(world, args, kind, message):
    """Code review C5: ONE fault, ONE word - a missing body is `body`
    whichever layer catches it (the sender's `read_body` says `body` too). And
    the object is what an AGENT reads, through a tool whose parameters are
    `body` and `no_body`: it has never seen `-f FILE`."""
    args = [str(world.body) if a == "BODY" else a for a in args]
    r, said = run(*args, "--json")
    assert said["sent"] == "no" and said["kind"] == kind and message in said["message"]
    assert "-f" not in said["message"] and "--" not in said["message"], said["message"]
    assert r.exit_code == 2 and world.wire() is None


def test_the_bodys_own_refusals_reach_a_file_and_a_stdin_alike(world):
    blank = world.tmp / "blank"
    blank.write_text("  \n")
    for args, stdin in ((["-f", str(blank)], None), (["-f", "-"], b" \n")):
        r, said = run("beta", "-s", "s", *args, "--json", input=stdin)
        assert r.exit_code == 2 and said["kind"] == "body" and said["sent"] == "no", r.output


def test_no_route_is_undeliverable_and_nothing_was_sent(world, monkeypatch):
    write_fake(world.bin / "postconf", FAKE_POSTCONF.format(python=sys.executable))
    write_fake(world.bin / "postmap", FAKE_POSTMAP.format(python=sys.executable))
    monkeypatch.setenv("FAKE_MAPPED", f"gamma@{HOST}")
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--json")
    assert r.exit_code == 3 and said["kind"] == "undeliverable" and said["sent"] == "no"
    assert world.wire() is None


def test_an_mta_that_ran_and_failed_is_the_one_sent_unknown(world, monkeypatch):
    monkeypatch.setenv("FAKE_SENDMAIL_RC", "75")
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--json")
    assert r.exit_code == 3 and said["kind"] == "mta" and said["sent"] == "unknown"
    assert any("UNSIGNED" in n for n in said["notices"]), "the warnings before it are kept"


def test_no_seat_is_identity(world, monkeypatch):
    from sessionmail import _seat
    monkeypatch.setattr(_seat, "resolve", lambda *a, **k: (_ for _ in ()).throw(_seat.NoIdentity()))
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--json")
    assert said["kind"] == "identity" and said["sent"] == "no" and world.wire() is None


def test_a_second_to_is_a_cc_and_the_notice_is_in_the_object(world):
    r, said = run("beta", "gamma", "-s", "s", "-f", str(world.body), "--json")
    assert said["sent"] == "yes" and b"Cc: gamma\n" in world.wire()
    assert any("go as Cc" in n for n in said["notices"])


def test_a_named_oracle_is_not_consulted_and_the_object_says_so(world, monkeypatch):
    """Option A as ruled: with --json the door calls the Python sender. A SET
    oracle variable is therefore not used - said out loud, never silently."""
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--json")
    assert said["sent"] == "yes" and not world.tripped()
    assert any("SESSIONMAIL_SEND_ORACLE" in n for n in said["notices"])


def test_eom_and_headers_and_raw_reach_the_sender(world):
    r, said = run("beta", "-s", "speak EOM", "--header", "X-Task=SM-18", "--bcc", "gamma", "--json")
    assert said["sent"] == "yes", r.output
    wire = world.wire()
    assert b"X-Task: SM-18\n" in wire and b"Bcc: gamma\n" in wire and wire.endswith(b"\n\n")


# ── SM-402: `--header` carries ANY header but the transport's own ──────────

def _head(wire: bytes) -> bytes:
    return wire.split(b"\n\n", 1)[0] + b"\n"


@pytest.mark.parametrize("given, on_the_wire", [
    ("Type: probe", b"\nType: probe\n"),                 # the acceptance line
    ("status: done", b"\nStatus: done\n"),               # Title-Case on the wire
    ("x-alert-name=disk", b"\nX-Alert-Name: disk\n"),     # the old K=V form still works
    ("x-trace-id: 7", b"\nX-Trace-ID: 7\n"),              # a conventional spelling keeps it
    ("Type: a=b", b"\nType: a=b\n"),                      # the first separator splits
])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_any_header_rides_title_cased(world, given, on_the_wire, door):
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--header", given,
                  *(["--json"] if door == "json" else []))
    assert r.exit_code == 0, r.output
    assert on_the_wire in _head(world.wire())
    assert not world.tripped(), "a header the bash cannot carry takes the package's sender"
    if door == "cli":
        assert r.stdout.strip().endswith(f"@{HOST}.session-mail>"), "the CLI prints the id"
    else:
        assert said["sent"] == "yes"


@pytest.mark.parametrize("given, names", [
    ("From: x", "--from"), ("to: x", "TO argument"), ("Subject: x", "--subject"),
    ("Message-ID: <a@b>", "transport"), ("Content-Type: text/html", "transport"),
    ("X-Session-From: forged", "transport"), ("x-session-signature: v=1", "transport"),
    ("MIME-Version: 1.0", "transport"), ("Received: x", "transport"),
    ("References: <a@b>", "--in-reply-to"), ("Supersedes: <a@b>", "--supersedes"),
    ("X-Original-Subject: forged", "--subject"),                      # SM-538 do-002
])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_header_send_writes_is_refused_and_nothing_is_sent(world, given, names, door):
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--header", given,
                  *(["--json"] if door == "json" else []))
    assert r.exit_code == 2 and world.wire() is None and not world.tripped()
    text = said["message"] if door == "json" else r.output
    assert "written by send itself" in text and names in text, text


@pytest.mark.parametrize("given", [
    "Type: a\nX-Session-From: forged", "Type: a\r\nX: y", "Type: a\rb", "Type: a\x00",
    "X-Project: p\nX-Session-From: forged",          # SM-342's door 2, closed here too
    "Bad Name: x", "Ünicode: x", "nothing-to-split", ": empty name",
])
def test_an_injection_or_a_bad_name_is_refused_and_nothing_is_sent(world, given):
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--header", given, "--json")
    assert r.exit_code == 2 and said["kind"] == "header" and world.wire() is None, said


def test_task_and_project_still_ride_as_before(world):
    r, said = run("beta", "-s", "s", "-f", str(world.body), "--json",
                  "--header", "X-Task=SM-402", "--header", "x-project: sessionmail",
                  "--header", "Type: probe")
    assert r.exit_code == 0, r.output
    head = _head(world.wire())
    assert b"\nX-Task: SM-402\n" in head and b"\nX-Project: sessionmail\n" in head
    assert b"\nType: probe\n" in head


def test_task_and_project_alone_take_the_package_unless_the_bash_is_named(world, monkeypatch):
    r, _ = run("beta", "-s", "s", "-f", str(world.body), "--header", "X-Task=SM-402")
    assert r.exit_code == 0 and not world.tripped() and b"X-Task: SM-402" in world.wire()
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    run("beta", "-s", "s", "-f", str(world.body), "--header", "X-Task=SM-402")
    assert world.tripped(), "named, and only --task: the bash carries it as before"


# ── a named oracle passed over says why ─────────────────────────────────────

@pytest.mark.parametrize("args, why", [
    (["--json"], "--json goes by the package's own sender"),
    (["--header", "Type: probe"], "--header Type goes by the package's own sender"),
], ids=["json", "header"])
def test_a_named_oracle_passed_over_names_the_flag_that_did_it(world, monkeypatch, args, why):
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    r, said = run("beta", "-s", "s", "-f", str(world.body), *args)
    assert r.exit_code == 0 and not world.tripped(), r.output
    notices = said["notices"] if said else r.stderr.splitlines()
    ours = [n for n in notices if "SESSIONMAIL_SEND_ORACLE is not consulted" in n]
    assert len(ours) == 1 and why in ours[0], notices
    if "--json" not in args:
        assert "--json" not in ours[0], "a header send never blames --json"


# ── SM-538: the subject shortener at the door (design s2, s6; do-005) ────────
# OFF is the tool's default; only the host's locked file turns it ON
# (`set subject-shorten on`); the flag and the seat variable only turn it OFF.
# Every test runs through BOTH doors: the command line and --json, which is
# what the MCP tool's child runs (`_mcp._send_handler`).

import email
import email.policy

from sessionmail import _send, rules

LONG = "SM-538 the shortener is ready for review and it covers the whole door ok"   # 72
assert len(LONG) == 72


def _host_on(word: str = "on", *more: str) -> None:
    """The host's locked file (sandboxed: conftest points `rules.locked_path`
    into tmp): the switch, and any MORE lines (SM-792's cap)."""
    path = rules.locked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in (f"set subject-shorten {word}", *more)))


def _send_it(world, door, subject, *more, body=True):
    return run("beta", "-s", subject, *(["-f", str(world.body)] if body else []), *more,
               *(["--json"] if door == "json" else []))


def _parsed(world):
    return email.message_from_bytes(world.wire(), policy=email.policy.default)


def _refused_as_today(world, r, said, door, subject, env=None):
    """Exit 2, kind `subject`, nothing written, and the words EXACTLY those
    `check_subject` gives today (design 6.3: OFF means unchanged)."""
    today = _send.check_subject(subject, env)[0]
    assert r.exit_code == 2 and world.wire() is None and not world.tripped(), r.output
    if door == "json":
        assert said["sent"] == "no" and said["kind"] == "subject"
        assert said["message"] == today
    else:
        assert r.stdout == ""
        assert r.stderr.splitlines()[-1] == f"message send: {today}"


@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_a_72_char_subject_is_shortened_sent_and_reported(world, door):
    _host_on()
    r, said = _send_it(world, door, LONG)
    assert r.exit_code == 0, r.output
    msg = _parsed(world)
    sent = msg["Subject"]
    assert len(sent) <= 69 and sent.isascii() and sent != LONG
    assert msg["X-Original-Subject"] == LONG, "the exact original, in the header"
    report = f"message send: shortened 72->{len(sent)}: \"{sent}\""
    if door == "cli":
        assert r.stdout.splitlines() == [r.stdout.strip()], "stdout is the Message-ID alone"
        assert r.stdout.strip().endswith(f"@{HOST}.session-mail>")
        ours = [line for line in r.stderr.splitlines() if line.startswith(report)]
    else:
        assert said["sent"] == "yes" and r.stderr == ""
        ours = [n for n in said["notices"] if n.startswith(report)]
    assert len(ours) == 1 and ours[0].endswith("original in X-Original-Subject"), r.output


@pytest.mark.parametrize("door", ["cli", "json"])
def test_no_config_refuses_exactly_as_today(world, door):
    r, said = _send_it(world, door, LONG)
    _refused_as_today(world, r, said, door, LONG)
    assert "not shortened" not in r.output and "shorten" not in r.stderr


@pytest.mark.parametrize("door", ["cli", "json"])
def test_set_subject_shorten_off_refuses_exactly_as_today(world, door):
    _host_on("off")
    r, said = _send_it(world, door, LONG)
    _refused_as_today(world, r, said, door, LONG)


@pytest.mark.parametrize("off", ["off", "0", "no", " OFF "])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_the_seat_variable_turns_it_off_under_a_host_on(world, door, off, monkeypatch):
    _host_on()
    monkeypatch.setenv(_send.SHORTEN_VAR, off)
    r, said = _send_it(world, door, LONG)
    _refused_as_today(world, r, said, door, LONG)


@pytest.mark.parametrize("value", ["on", "1", "yes"])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_the_seat_variable_cannot_turn_it_on_and_says_so(world, door, value, monkeypatch):
    """RULED Q2: a seat can only turn it off. Any other value is ignored with
    a notice, and the host layer (here: no file, so OFF) decides."""
    monkeypatch.setenv(_send.SHORTEN_VAR, value)
    r, said = _send_it(world, door, LONG)
    _refused_as_today(world, r, said, door, LONG)
    notices = said["notices"] if door == "json" else r.stderr.splitlines()
    ours = [n for n in notices if "a seat can only turn the subject shortener off" in n]
    assert len(ours) == 1 and f"{_send.SHORTEN_VAR}={value!r}" in ours[0], notices


@pytest.mark.parametrize("door", ["cli", "json"])
def test_the_seat_variable_set_to_on_under_a_host_on_still_shortens(world, door, monkeypatch):
    """The ignored value changes nothing: the host's ON stands, with the notice."""
    _host_on()
    monkeypatch.setenv(_send.SHORTEN_VAR, "on")
    r, said = _send_it(world, door, LONG)
    assert r.exit_code == 0 and _parsed(world)["X-Original-Subject"] == LONG, r.output


@pytest.mark.parametrize("door", ["cli", "json"])
def test_no_shorten_beats_a_host_on(world, door):
    _host_on()
    r, said = _send_it(world, door, LONG, "--no-shorten")
    _refused_as_today(world, r, said, door, LONG)


@pytest.mark.parametrize("subject, why", [
    ("修复完成", "folding would drop 4 of 4 letters"),
    ("— 🎉 —", "folding would leave nothing alphanumeric"),
])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_gutted_subject_is_refused_in_todays_words_plus_why(world, door, subject, why):
    _host_on()
    r, said = _send_it(world, door, subject)
    today = _send.check_subject(subject)[0]
    assert r.exit_code == 2 and world.wire() is None, r.output
    text = said["message"] if door == "json" else r.stderr.splitlines()[-1]
    assert text == (today if door == "json" else f"message send: {today}") + f" (not shortened: {why})"
    if door == "json":
        assert said["kind"] == "subject"


@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_gutted_eom_subject_never_goes_as_a_bare_eom(world, door):
    _host_on()
    r, said = _send_it(world, door, "修复 EOM", body=False)
    assert r.exit_code == 2 and world.wire() is None and "not shortened" in r.output


@pytest.mark.parametrize("subject", [
    LONG[:40] + "\n" + LONG[41:],              # an LF in a 72-char subject
    LONG[:40] + "\r" + LONG[41:],
    LONG[:40] + "\x1b" + LONG[41:],            # a C0 control
    LONG[:40] + "\x7f" + LONG[41:],            # DEL
    "short\x00",                                # a NUL in a FITTING subject: still first
])
@pytest.mark.parametrize("guard", ["on", "off"])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_a_line_break_or_control_is_refused_before_shortening(world, door, guard,
                                                                     subject, monkeypatch):
    _host_on()
    monkeypatch.setenv("SESSIONMAIL_SUBJECT_GUARD", guard)
    r, said = _send_it(world, door, subject)
    assert r.exit_code == 2 and world.wire() is None, r.output
    text = said["message"] if door == "json" else r.stderr
    assert "line break or a control character" in text and "never shortened" in text
    if door == "json":
        assert said["kind"] == "subject"


@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_a_tab_is_white_space_and_is_shortened_not_refused(world, door):
    _host_on()
    subject = LONG[:40] + "\t" + LONG[41:]
    r, said = _send_it(world, door, subject)
    assert r.exit_code == 0, r.output
    assert _parsed(world)["X-Original-Subject"] == subject


@pytest.mark.parametrize("guard, words", [
    ("on", "could execute"),                            # check_subject's guard words
    ("off", "line break in the --subject value"),       # the core's refusal, as before
])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_off_a_line_break_is_refused_on_todays_road_in_todays_words(world, door, guard, words,
                                                                    monkeypatch):
    """Design s2 step 2: the new ON-road refusal must never reach the OFF road."""
    monkeypatch.setenv("SESSIONMAIL_SUBJECT_GUARD", guard)
    r, said = _send_it(world, door, "a\nb")
    assert r.exit_code == 2 and world.wire() is None
    text = said["message"] if door == "json" else r.stderr
    assert words in text and "never shortened" not in text, text


@pytest.mark.parametrize("subject", [
    "x" * 70 + " `tick`",          # the hard cut would DROP the backtick...
    "x" * 70 + " $(date)",         # ...and the `$(`: the guard reads what was typed
    "`tick` " + "x" * 70,          # kept by the cut
])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_the_backtick_guard_reads_the_subject_as_typed(world, door, subject):
    """Design s2 step 3: the backtick and `$(` stay refused exactly as today.
    A cut that removed one would send what today refuses, and keep it in
    X-Original-Subject for every later reader with a shell."""
    _host_on()
    r, said = _send_it(world, door, subject)
    assert r.exit_code == 2 and world.wire() is None
    text = said["message"] if door == "json" else r.stderr.splitlines()[-1]
    assert text.endswith(_send.could_execute(subject)) and "could execute" in text


def test_host_on_guard_off_a_backtick_subject_is_shortened_and_sent(world, monkeypatch):
    _host_on()
    monkeypatch.setenv("SESSIONMAIL_SUBJECT_GUARD", "off")
    r, said = run("beta", "-s", "`tick` " + "x" * 70, "-f", str(world.body), "--json")
    assert r.exit_code == 0 and said["sent"] == "yes", said


@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_an_em_dash_and_smart_quotes_fold_instead_of_refusing(world, door):
    _host_on()
    r, said = _send_it(world, door, "SM-538 — “ready”")
    assert r.exit_code == 0, r.output
    msg = _parsed(world)
    assert msg["Subject"] == 'SM-538 - "ready"'
    assert msg["X-Original-Subject"] == "SM-538 — “ready”"
    assert "message send: folded 16->16:" in (" ".join(said["notices"]) if said else r.stderr)


@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_a_fitting_ascii_subject_is_never_touched(world, door):
    _host_on()
    subject = "a fitting subject  with a double space in it"
    r, said = _send_it(world, door, subject)
    assert r.exit_code == 0
    assert f"\nSubject: {subject}\n".encode() in world.wire()
    assert b"X-Original-Subject" not in world.wire()
    assert "shortened" not in r.output and "folded" not in r.output


@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_an_eom_subject_keeps_its_marker_and_needs_no_body(world, door):
    _host_on()
    subject = "x" * 20 + " the shortener keeps the marker at the very end of it all EOM"
    assert _send.counted(subject) > 69          # over even with the marker not counted
    r, said = _send_it(world, door, subject, body=False)
    assert r.exit_code == 0, r.output
    sent = _parsed(world)["Subject"]
    assert sent.endswith(" EOM") and _send.counted(sent) <= 69    # eom-not-counted


@pytest.mark.parametrize("door", ["cli", "json"])
def test_host_on_a_120_char_original_round_trips(world, door):
    # 51 over: past SM-792's default cap of 10, so the host raises its cap
    # here; what this pins is the X-Original-Subject round trip.
    _host_on("on", "set subject-shorten-max 100")
    subject = " ".join(["word"] * 24) + "!"      # 120
    assert len(subject) == 120
    r, said = _send_it(world, door, subject)
    assert r.exit_code == 0 and _parsed(world)["X-Original-Subject"] == subject


def test_host_on_a_named_oracle_is_passed_over_for_a_shortened_subject(world, monkeypatch):
    """Design 4.4: the bash cannot carry X-Original-Subject, so a changed
    subject takes the package's sender, and a notice says which thing did."""
    _host_on()
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    r, _ = run("beta", "-s", LONG, "-f", str(world.body))
    assert r.exit_code == 0 and not world.tripped(), r.output
    ours = [n for n in r.stderr.splitlines() if "SESSIONMAIL_SEND_ORACLE is not consulted" in n]
    assert len(ours) == 1 and "a shortened subject" in ours[0], r.stderr


def test_host_on_a_named_oracle_still_takes_an_unchanged_subject(world, monkeypatch):
    _host_on()
    monkeypatch.setenv("SESSIONMAIL_SEND_ORACLE", str(world.bin / "session-mail-send"))
    r, _ = run("beta", "-s", "the point", "-f", str(world.body))
    assert world.tripped(), "nothing changed, so the bash still takes it for one release"


def test_a_refused_send_prints_no_shortening_report(world):
    """The report comes after the MTA accepted (design s2 step 6)."""
    _host_on()
    r, said = run("beta,gamma", "-s", LONG, "-f", str(world.body), "--json")
    assert r.exit_code == 2 and said["kind"] == "address"
    assert not any("shortened" in n for n in said["notices"])


# ── SM-538 do-006: the counter, after the MTA accepted (design 5.2) ─────────

import os  # noqa: E402

from sessionmail import _metrics  # noqa: E402

SHORTENED = 'sessionmail_send_total{result="sent",reason="subject_shortened"}'
FOLDED = 'sessionmail_send_total{result="sent",reason="subject_folded"}'
REMOVED = "sessionmail_subject_chars_removed_total"


def _series(world) -> dict[str, int]:
    path = world.tmp / "textfile" / _metrics.FILE
    if not path.exists():
        return {}
    return {name: int(value) for name, value in
            (line.rsplit(" ", 1) for line in path.read_text().splitlines()
             if line and not line.startswith("#"))}


@pytest.fixture
def collector(world, monkeypatch):
    d = world.tmp / "textfile"
    d.mkdir()
    monkeypatch.setenv(_metrics.DIR_VAR, str(d))
    return d


@pytest.mark.parametrize("door", ["cli", "json"])
def test_two_shortened_sends_count_two_and_sum_the_chars_removed(world, door, collector):
    _host_on()
    removed = 0
    for n in (1, 2):
        r, said = _send_it(world, door, LONG)
        assert r.exit_code == 0, r.output
        removed += len(LONG) - len(_parsed(world)["Subject"])
        assert _series(world) == {SHORTENED: n, FOLDED: 0, REMOVED: removed}
    assert removed > 0


@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_folded_only_send_counts_as_folded(world, door, collector):
    _host_on()
    r, said = _send_it(world, door, "SM-538 — “ready”")
    assert r.exit_code == 0, r.output
    assert _series(world) == {SHORTENED: 0, FOLDED: 1, REMOVED: 0}


@pytest.mark.parametrize("door", ["cli", "json"])
def test_an_unchanged_or_refused_send_counts_nothing(world, door, collector):
    _host_on()
    assert _send_it(world, door, "a fitting subject")[0].exit_code == 0
    assert _send_it(world, door, "修复完成")[0].exit_code == 2
    assert not (collector / _metrics.FILE).exists()


def test_with_the_shortener_off_nothing_is_counted(world, collector):
    r, _ = _send_it(world, "json", LONG)
    assert r.exit_code == 2 and not (collector / _metrics.FILE).exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
@pytest.mark.parametrize("door", ["cli", "json"])
def test_an_unwritable_collector_is_a_notice_and_the_send_still_exits_0(world, door, collector):
    _host_on()
    collector.chmod(0o500)
    try:
        r, said = _send_it(world, door, LONG)
    finally:
        collector.chmod(0o700)
    assert r.exit_code == 0 and world.wire() is not None, r.output
    notices = said["notices"] if door == "json" else r.stderr.splitlines()
    assert any(n.startswith("message send: counter not written: ") for n in notices), notices
    if door == "cli":
        assert r.stdout.splitlines() == [r.stdout.strip()], "stdout is still the id alone"


def test_a_missing_collector_writes_nothing_and_says_nothing(world, monkeypatch):
    _host_on()
    monkeypatch.setenv(_metrics.DIR_VAR, str(world.tmp / "absent"))
    r, said = _send_it(world, "json", LONG)
    assert r.exit_code == 0 and not (world.tmp / "absent").exists()
    assert not any("counter" in n for n in said["notices"]), said["notices"]


# ── SM-538 do-006: -c is --cc (design 7) ────────────────────────────────────

@pytest.mark.parametrize("flag", ["-c", "--cc"])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_dash_c_is_cc(world, door, flag):
    r, said = _send_it(world, door, "hi", flag, "gamma", "--no-body", body=False)
    assert r.exit_code == 0, r.output
    assert b"\nCc: gamma\n" in world.wire()


# ── SM-538 do-006: the page names the shortener's switches, above the fold ───

def test_the_send_page_names_the_switches_and_stays_above_40_lines():
    r = CliRunner().invoke(cli, ["send", "--help"])
    assert r.exit_code == 0
    page = r.output
    for needed in ("--no-shorten", "SESSIONMAIL_SUBJECT_SHORTEN=off", "-c, --cc ADDR",
                   "at most its cap, default 10 over"):     # SM-792 N2: the cap is named
        assert needed in page, needed
    assert "fold-override" not in page
    assert len(page.splitlines()) <= 40, len(page.splitlines())
    assert max(len(line) for line in page.splitlines()) <= 80


# ── SM-792: `set subject-shorten-max N`, the host's cap (RULED Q1: OVERAGE) ──
# Default 10. A subject more than N over the limit is declined and refused in
# today's words plus why; only the locked file sets N; a bad line is named.

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
         "judy november oscar papa quebec romeo sierra tango uniform victor")


def _over(k: int) -> str:
    """An ASCII subject exactly K characters over 69, ending in a letter."""
    s = WORDS[:69 + k]
    s = s[:-1] + "z" if s.endswith(" ") else s
    assert len(s) == 69 + k
    return s


def _capped(world, r, said, door, subject, k, cap):
    """Refused as today, plus `(not shortened: <k> over; the cap is <N>: ...)`."""
    today = _send.check_subject(subject)[0]
    why = f" (not shortened: {k} over; the cap is {cap}: rewrite it, or front-load the point)"
    assert r.exit_code == 2 and world.wire() is None and not world.tripped(), r.output
    if door == "json":
        assert said["sent"] == "no" and said["kind"] == "subject"
        assert said["message"] == today + why
    else:
        assert r.stdout == ""
        assert r.stderr.splitlines()[-1] == f"message send: {today}{why}"


def _sent_shortened(world, r, subject):
    assert r.exit_code == 0, r.output
    msg = _parsed(world)
    assert msg["X-Original-Subject"] == subject and _send.counted(msg["Subject"]) <= 69
    return msg["Subject"]


@pytest.mark.parametrize("door", ["cli", "json"])
def test_unset_the_cap_is_10_at_10_over_it_sends(world, door):
    _host_on()
    r, said = _send_it(world, door, _over(10))
    _sent_shortened(world, r, _over(10))


@pytest.mark.parametrize("door", ["cli", "json"])
def test_unset_the_cap_is_10_one_over_it_refuses(world, door):
    _host_on()
    r, said = _send_it(world, door, _over(11))
    _capped(world, r, said, door, _over(11), 11, 10)


@pytest.mark.parametrize("cap", [5, 24])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_the_locked_cap_at_the_cap_sends_one_over_refuses(world, door, cap):
    _host_on("on", f"set subject-shorten-max {cap}")
    r, said = _send_it(world, door, _over(cap))
    _sent_shortened(world, r, _over(cap))
    (world.tmp / "sendmail.wire").unlink()                 # the first send's wire
    r, said = _send_it(world, door, _over(cap + 1))
    _capped(world, r, said, door, _over(cap + 1), cap + 1, cap)


@pytest.mark.parametrize("line", [
    "set subject-shorten-max ten", "set subject-shorten-max -5",
    "set subject-shorten-max 0", "set subject-shorten-max 1.5",
])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_bad_cap_line_is_named_loudly_and_the_default_stands(world, door, line):
    _host_on("on", line)
    r, said = _send_it(world, door, _over(11))
    _capped(world, r, said, door, _over(11), 11, 10)
    notices = said["notices"] if door == "json" else r.stderr.splitlines()
    ours = [n for n in notices if "a bad line of the locked file" in n]
    assert ours == [f"message send: ignoring a bad line of the locked file, "
                    f"{rules.locked_path()}:2: 'set subject-shorten-max' takes one whole "
                    f"number above 0 (got {line.split(maxsplit=2)[2]!r}; the default 10 "
                    f"applies; to never shorten, 'set subject-shorten off'): {line}; "
                    f"the subject shortener's cap is 10"], notices


@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_bad_cap_line_is_named_on_a_send_that_goes_too(world, door):
    _host_on("on", "set subject-shorten-max 0")
    r, said = _send_it(world, door, _over(3))
    _sent_shortened(world, r, _over(3))
    notices = said["notices"] if door == "json" else r.stderr.splitlines()
    assert sum("a bad line of the locked file" in n for n in notices) == 1, notices


@pytest.mark.parametrize("door", ["cli", "json"])
def test_with_the_shortener_off_a_bad_cap_line_says_nothing(world, door):
    _host_on("off", "set subject-shorten-max 0")
    r, said = _send_it(world, door, _over(11))
    _refused_as_today(world, r, said, door, _over(11))
    assert "bad line" not in r.output


@pytest.mark.parametrize("var", ["SESSIONMAIL_SUBJECT_SHORTEN_MAX", "SESSIONMAIL_SHORTEN_MAX"])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_seat_cannot_raise_the_cap_by_env(world, door, var, monkeypatch):
    _host_on()
    monkeypatch.setenv(var, "100")
    monkeypatch.setenv(_send.SHORTEN_VAR, "on")        # ignored, as ever: it cannot raise either
    r, said = _send_it(world, door, _over(11))
    _capped(world, r, said, door, _over(11), 11, 10)


@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_seat_cannot_raise_the_cap_by_a_rules_file(world, door, monkeypatch):
    """Every rules file an agent can write, and the page-rules variable: only
    the locked file (whose path no variable moves) sets the cap."""
    _host_on()
    line = "set subject-shorten-max 100\n"
    from sessionmail import _seat

    home = _seat.home()
    for f in (home / "seats" / "alpha" / "rules", world.tmp / ".sessionmail" / "rules.locked",
              home / "rules.locked"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(line)
    monkeypatch.setenv(rules.PAGE_RULES_VAR, line)
    r, said = _send_it(world, door, _over(11))
    _capped(world, r, said, door, _over(11), 11, 10)


def test_a_seat_cannot_raise_the_cap_by_a_flag(world):
    r = CliRunner().invoke(cli, ["send", "beta", "-s", _over(11), "-f", str(world.body),
                                 "--shorten-max", "100"])
    assert r.exit_code == 2 and "No such option" in r.output and world.wire() is None


@pytest.mark.parametrize("door", ["cli", "json"])
def test_a_folded_subject_that_fits_is_never_capped(world, door):
    _host_on("on", "set subject-shorten-max 1")
    subject = "y" * 66 + " …"                              # 68; the fold makes it 70, then cut
    r, said = _send_it(world, door, subject)
    assert r.exit_code == 0, r.output
    assert _parsed(world)["X-Original-Subject"] == subject


@pytest.mark.parametrize("k, sent", [(10, True), (11, False)])
@pytest.mark.parametrize("door", ["cli", "json"])
def test_the_cap_does_not_count_a_trailing_eom(world, door, k, sent):
    _host_on()
    subject = _over(k) + " EOM"                            # counted: 69 + k, never 73 + k
    r, said = _send_it(world, door, subject, body=False)
    if sent:
        assert _sent_shortened(world, r, subject).endswith(" EOM")
    else:
        _capped(world, r, said, door, subject, k, 10)
        assert "not counting ' EOM'" in r.output


@pytest.mark.parametrize("door", ["cli", "json"])
def test_the_counter_still_counts_the_true_chars_removed(world, door, collector):
    _host_on()
    r, said = _send_it(world, door, _over(10))
    removed = len(_over(10)) - len(_sent_shortened(world, r, _over(10)))
    assert removed > 10, "the overage is capped, not the chars removed (RULED Q1)"
    assert _series(world) == {SHORTENED: 1, FOLDED: 0, REMOVED: removed}
    assert _send_it(world, door, _over(11))[0].exit_code == 2
    assert _series(world) == {SHORTENED: 1, FOLDED: 0, REMOVED: removed}, \
        "a capped refusal counts nothing"


# ── SM-716 do-004: `--message-id` through `--json`, and a structured caller ──
# Design: store projects/sessionmail/tasks/SM-716_feat_send-msgid/design.md @
# 8a4f26595, §5 (the refusals, kind `message-id`), §6 (the doors) and §10
# do-004; README SC6. Nothing new is built for this door: the object's `id`
# is the receipt's, and a refusal's `kind` is the Refused's own.

def _message_ids(wire: bytes) -> list[bytes]:
    return [l for l in _head(wire).split(b"\n") if l.lower().startswith(b"message-id:")]


@pytest.mark.parametrize("given", [f"<x.1@{HOST}>", f"x.1@{HOST}"], ids=["brackets", "bare"])
def test_message_id_json_says_sent_yes_with_the_chosen_id(world, given):
    """SC6 / SC1: `{"sent": "yes", "id": "<x.1@OWNHOST>", ...}`, and the wire
    carries that id and no other."""
    r, said = run("beta", "-s", "S", "--no-body", "--message-id", given, "--json")
    assert r.exit_code == 0 and r.stderr == "", r.output
    assert said["sent"] == "yes" and said["id"] == f"<x.1@{HOST}>", said
    assert _message_ids(world.wire()) == [f"Message-ID: <x.1@{HOST}>".encode()]
    assert not world.tripped()


@pytest.mark.parametrize("given", [
    "", "no-at-sign", "<a@b>@c", f"a b@{HOST}", f"&lt;x@{HOST}&gt;", f"a\nb@{HOST}",
], ids=["empty", "no-at", "two-at", "space", "html-escaped", "line-break"])
def test_message_id_json_a_malformed_id_is_sent_no_kind_message_id(world, given):
    """SC6 / SC3: each of the six is one object, `sent: no`, kind
    `message-id`, exit 2; the message names `--message-id`; nothing sent."""
    r, said = run("beta", "-s", "S", "--no-body", "--message-id", given, "--json")
    assert r.exit_code == 2 and r.stderr == "", r.output
    assert said["sent"] == "no" and said["kind"] == "message-id", said
    assert f"invalid --message-id value {given!r}" in said["message"]
    assert world.wire() is None and not world.tripped()


@pytest.mark.parametrize("given", ["x@other.example", f"x@{HOST}.example.net",
                                   f"x@{HOST}.session-mail"],
                         ids=["other-host", "fqdn", "minted-suffix"])
def test_message_id_json_a_foreign_host_is_sent_no_kind_message_id(world, given):
    """SC6 / SC4 (bare only, RULED OQ5): `sent: no`, kind `message-id`, exit
    2; the message names the right-hand side it saw and the bare host it
    wants; nothing sent."""
    r, said = run("beta", "-s", "S", "--no-body", "--message-id", given, "--json")
    assert r.exit_code == 2 and r.stderr == "", r.output
    assert said["sent"] == "no" and said["kind"] == "message-id", said
    rhs = given.rsplit("@", 1)[1]
    assert repr(rhs) in said["message"] and f"use the bare host {HOST!r}" in said["message"]
    assert world.wire() is None and not world.tripped()


@pytest.mark.parametrize("extra, why", [
    ([], "a Message-ID is ASCII"),
    (["-r", f"x@{HOST}"], "is the same id as this send's --in-reply-to"),
    (["--supersedes", f"x@{HOST}"], "is the same id as this send's --supersedes"),
], ids=["non-ascii", "same-as-reply", "same-as-supersedes"])
def test_message_id_json_the_other_refusals_share_the_kind(world, extra, why):
    """§5's OQ6 and OQ7 rows reach the object with the same kind."""
    given = f"é@{HOST}" if not extra else f"<x@{HOST}>"
    r, said = run("beta", "-s", "S", "--no-body", "--message-id", given, *extra, "--json")
    assert r.exit_code == 2 and said["sent"] == "no" and said["kind"] == "message-id", said
    assert why in said["message"] and world.wire() is None


def test_message_id_json_an_over_long_id_is_sent_no_kind_message_id(world):
    from sessionmail import _sender
    over = "a" * (_sender.MESSAGE_ID_MAX - len(f"<@{HOST}>") + 1) + f"@{HOST}"
    r, said = run("beta", "-s", "S", "--no-body", "--message-id", over, "--json")
    assert r.exit_code == 2 and said["sent"] == "no" and said["kind"] == "message-id", said
    assert f"may not exceed {_sender.MESSAGE_ID_MAX}" in said["message"] and world.wire() is None


@pytest.mark.parametrize("given", ["x@other.example", "no-at-sign"], ids=["foreign", "malformed"])
def test_message_id_a_structured_request_raises_refused_code_2(world, given):
    """SC6, the structured caller: `_sender.send()` given a `Request` with a
    foreign (or malformed) id raises `Refused`, code 2, kind `message-id`,
    `sent` no, and nothing reaches the MTA."""
    import os
    from sessionmail import _sender
    req = _sender.Request(to="beta", from_="alpha", subject="S", message_id=given)
    with pytest.raises(_sender.Refused) as refused:
        _sender.send(req, b"x\n", dict(os.environ))
    assert refused.value.code == 2 and refused.value.kind == "message-id"
    assert refused.value.sent == "no" and "--message-id" in str(refused.value)
    assert world.wire() is None
