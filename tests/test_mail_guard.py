"""mail-guard - the PreToolUse (Bash) hook that refuses a shell-damaged send.

Ported from session's `skills/session/scripts/test_session_hooks_mail_guard.py`
(groups A-D, at session commit 453ccda) when the hook moved here with the
`session` project's retirement (openspec/changes/mail-leaves-session item 23).
The case names keep their session ids (A1, B7, C13 ...) so a reader can line
the two suites up.

Groups E and F there drove session's `session hooks install` and its
`make check-hooks` gate. Neither moves: here the plugin itself registers the
hook (hooks/hooks.json), so "is this box behind the guard" is "is the plugin
loaded" - pinned in tests/test_mail_guard_wiring.py.

Network-free, mail-free, settings-free: the guard is driven as a pure function
(`decide`) AND end to end as a subprocess reading hook JSON on stdin.

  A. THE TWO LAMP-CHECK COMMANDS ARE DENIED - the two that damaged a message.
  B. The rest of the deny surface, and the reason text (the deny's whole UI).
  C. FALSE-POSITIVE CONTROL: a command that merely MENTIONS the send - a grep,
     a comment, a heredoc writing documentation - must be allowed. These are
     the cases that decide whether the guard is tolerated or bypassed.
  D. The hook process: exit 0, decision JSON, DENY not warn, fail OPEN.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "mail-guard" / "hook.py"


def _load():
    spec = importlib.util.spec_from_file_location("mail_guard", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load()


def run_hook(command, tool_name="Bash", raw_input=None):
    payload = raw_input if raw_input is not None else json.dumps({
        "session_id": "test",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
    })
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True)


# ── A. the lamp check: the two commands from the card, verbatim ──────────
# Written with explicit escapes rather than a triple-quoted block so what
# reaches the guard is exactly the byte sequence a caller would type.

LAMP_1 = "session mail send self --from me --subject 'x' \"a `date` b\""
LAMP_2 = "session mail send self --from me --subject \"it's here\" 'body'"


#: What a deny may no longer tell anyone to use: `session mail send` is
#: retired, and so are its `--body-file` and the MCP `mail_send` tool (review
#: 2030-12-08, H2). The verb itself may still be NAMED - it is what was typed.
RETIRED_ADVICE = ("--body-file", "`mail_send`", "--from <me>")


def names_the_live_forms(reason):
    assert "message send TO -s SUBJECT -f FILE" in reason and "`-f -`" in reason, reason
    assert guard.MCP_SEND == "mcp__plugin_sessionmail_message__message_send"
    assert guard.MCP_SEND in reason, reason
    for dead in RETIRED_ADVICE:
        assert dead not in reason, (dead, reason)


@pytest.mark.parametrize("cmd", [LAMP_1, LAMP_2], ids=["A1-backtick-body", "A2-apostrophe"])
def test_A_lamp_check_is_denied_and_names_both_safe_forms(cmd):
    reason = guard.decide(cmd)
    assert reason is not None, "allowed, but should have been DENIED"
    names_the_live_forms(reason)


# ── B. the rest of the deny surface ──────────────────────────────────────

DENY = [
    ("B1", "session mail send box --from me --subject 'x' -- 'the body'"),
    ("B2", 'session mail send box --from me --subject "x" -- "$(cat body.txt)"'),
    ("B3", "session-mail-send box --from me --subject 'x' 'body'"),
    ("B4", "~/.skillbox/skills/session/scripts/session-mail-send box --from me 'body'"),
    ("B5", "cd /tmp && session mail send box --from me 'body'"),
    ("B6", "out=$(session mail send box --from me 'body')"),
    ("B7", 'session mail send box --from me --subject "run `date` now" --body-file /tmp/b'),
    ("B8", 'session mail send box --from me --subject "$(hostname) is up" --body-file /tmp/b'),
    ("B9", 'session mail send box --from me --subject="run `date`" --body-file /tmp/b'),
    ("B10", "printf '%s' hi | session mail send box --from me --subject 'x'"),
    ("B11", "session mail send box --from me --subject 'x' <<'EOF'\nhello\nEOF\n"),
    ("B12", "session mail send --from me --reply-all '<id@host>' 'body'"),
    ("B13", "session mail send box --from me 'body' --filedrop"),
    # B16: --no-parent-check is a KNOWN boolean (SESH-1066): listed as one, it
    # takes no value, so the inline body before it is still seen. (Before
    # SM-777 an UNLISTED flag made `judge` fall silent; now it is denied, C13.)
    # Paired with C17 below, because either half alone is satisfiable the
    # wrong way.
    ("B16", "session mail send box --from me 'body' --no-parent-check"),
    # C13, MOVED here from the allow list by SM-777: an unknown flag used to
    # make `judge` fall silent "by design", which was a fail-OPEN - the shell
    # expands what follows before any CLI sees the flag. It is DENIED now. The
    # id keeps its session number so the two suites still line up.
    ("C13", "session mail send box --from me --unknown-flag val 'word'"),
]


@pytest.mark.parametrize("cmd", [c for _, c in DENY], ids=[i for i, _ in DENY])
def test_B_deny_surface(cmd):
    assert guard.decide(cmd) is not None, "allowed, but should have been DENIED:\n" + cmd


def test_B14_the_stdin_deny_names_the_empty_message_trap():
    r = guard.decide("printf '%s' hi | session mail send box --from me --subject 'x'")
    assert "EMPTY" in (r or ""), r


def test_B15_the_subject_deny_names_a_remedy_that_works():
    """B15 used to pin a dead end: the subject deny named MCP as the escape,
    and the CLI refuses a backtick in a subject whichever door it came
    through. So it pins the remedy that WORKS - take it out of the subject -
    in the live verb's words: the retired verb's caller gets `message
    send`'s own remedy, one constant for both judges. Both spellings."""
    r = guard.decide('session mail send box --from me --subject "`date`" --body-file /tmp/b')
    assert "subject" in (r or "") and "TAKE IT OUT OF THE SUBJECT" in (r or ""), r
    assert r.endswith(guard.MESSAGE_SUBJECT_REMEDY)    # B15b: one remedy, not two
    assert "-f FILE" in r and guard.MCP_SEND in r      # B15c: the body is where it goes
    for dead in RETIRED_ADVICE:
        assert dead not in r, (dead, r)
    r_eq = guard.decide('session mail send box --from me --subject="`date`" --body-file /tmp/b')
    assert r_eq == r, "B15d the --subject=VALUE spelling gets the SAME advice"


@pytest.mark.parametrize("cmd", [c for _, c in DENY], ids=[i for i, _ in DENY])
def test_B17_no_deny_sends_anyone_back_to_a_retired_form(cmd):
    reason = guard.decide(cmd)
    for dead in RETIRED_ADVICE:
        assert dead not in reason, (dead, reason)


# ── C. false-positive control: the cases that decide whether it survives ─

ALLOW = [
    ("C1", 'grep -rn "session mail send" docs/'),
    ("C2", "# session mail send box --from me 'body'"),
    ("C3", "ls -la  # session mail send box --from me 'body'"),
    ("C4", "cat <<'EOF' > doc.md\nsession mail send box --from me 'body'\nEOF\n"),
    ("C5", "cat <<-'EOF' > doc.md\n\tsession mail send box --from me 'body'\n\tEOF\n"),
    ("C6", "echo 'session mail send box --from me bod' >> notes.md"),
    ("C7", "session mail send box --from me --subject 'ok' --body-file /tmp/body.txt"),
    ("C8", "session mail send box --from me --subject 'ok' --body-file /tmp/b > out.log 2>&1"),
    ("C9", "session mail send box --from me --subject 'ok' --body-file /tmp/b | tee log"),
    ("C10", "session mail list worker.abc --unread"),
    ("C11", "session mail send --help"),
    ("C12", "tm task standdown --free"),
    ("C14", "session mail send box --from me --subject 'unterminated"),
    ("C15", "echo hi | session mail send box --from me --body-file /dev/stdin"),
    ("C16", "session mail send box --from me --cc other --subject 'ok' --body-file /tmp/b"),
    ("C17", "session mail send box --from me --subject 'ok' --body-file /tmp/b --no-parent-check"),
]


@pytest.mark.parametrize("cmd", [c for _, c in ALLOW], ids=[i for i, _ in ALLOW])
def test_C_false_positive_control(cmd):
    reason = guard.decide(cmd)
    assert reason is None, "DENIED, but should have been allowed:\n%s\nreason: %s" % (cmd, reason)


# ── D. the hook process: its stdin contract and its decision JSON ────────

def test_D1_to_D5_a_deny_is_decision_json_at_exit_0():
    proc = run_hook(LAMP_1)
    assert proc.returncode == 0, proc.stderr                      # D1
    out = json.loads(proc.stdout)                                  # D2
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"                    # D3
    assert hso["permissionDecision"] == "deny", "a warn is a FAIL"  # D4
    names_the_live_forms(hso["permissionDecisionReason"])         # D5


def test_D6_an_allowed_command_produces_no_output():
    proc = run_hook("session mail send box --from me --subject 'ok' --body-file /tmp/b")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_D7_a_non_bash_tool_call_is_never_judged():
    proc = run_hook(LAMP_1, tool_name="Edit")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


@pytest.mark.parametrize("raw", ["not json at all", ""], ids=["D8-garbage", "D9-empty"])
def test_D8_D9_bad_stdin_fails_open(raw):
    proc = run_hook(None, raw_input=raw)
    assert proc.returncode == 0 and proc.stdout.strip() == ""
