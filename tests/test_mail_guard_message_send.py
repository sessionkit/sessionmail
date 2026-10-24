"""mail-guard on `message send` - the verb that survives session's retirement.

After the break `session mail send` and `session-mail-send` do not exist, so
a guard that only knew them would guard nothing. `message send` gets ONE of
the three session rules, the subject's: deny when the RAW text of the
subject (`-s X`, `-sX`, `--subject X`, `--subject=X`) or of a `--header`
carries a backtick or `$(` outside single quotes - the shape that EXECUTED
`git ready approve` from a subject line.

Not the other two, on purpose: its positional words are RECIPIENTS, the CLI
refuses an inline body itself, and `-f -` reads stdin, so a heredoc or pipe
into it is the documented form. Those must-allow cases are pinned here as
hard as the denies.
"""

from __future__ import annotations

import json

import click
import pytest

from sessionmail import _send
from sessionmail.message import send
from test_mail_guard import guard, run_hook


# ── deny: every spelling click takes, backtick and $( ───────────────────

DENY = [
    ("s-space-backtick", 'message send bob -s "run `date` now" -f /tmp/b'),
    ("s-space-subst", 'message send bob -s "$(hostname) is up" -f /tmp/b'),
    ("s-attached", 'message send bob -s"run `date`" -f /tmp/b'),
    ("subject-space", 'message send bob --subject "run `date`" -f /tmp/b'),
    ("subject-equals", 'message send bob --subject="$(hostname) up" -f /tmp/b'),
    ("apostrophe-in-dq", 'message send bob -s "it\'s `date`" -f /tmp/b'),
    ("bare-subst-value", "message send bob -s $(hostname) -f /tmp/b"),
    ("bare-backtick-last", "message send bob -f /tmp/b -s `hostname`"),
    ("subject-after-opts", 'message send bob -f /tmp/b --cc carol -s "`x`"'),
    ("subject-first", 'message send -s "`x`" bob -f /tmp/b'),
    ("header-space", 'message send bob -s ok -f /tmp/b --header "X-Note: `date`"'),
    ("header-equals", 'message send bob -s ok -f /tmp/b --header="X-Note: $(date)"'),
    ("after-and", 'cd /tmp && message send bob -s "`x`" -f b'),
    ("inside-subst", 'id=$(message send bob -s "`x`" -f b)'),
    ("env-prefix", 'SESSIONMAIL_FROM_NAME=Alpha message send bob -s "`x`" -f b'),
    ("full-path", '~/.local/bin/message send bob -s "`x`" -f b'),
    ("json", 'message send bob --json -s "`x`" --no-body'),
    # the two genuine denials in 30 days of real transcripts (2030-12-08 scan)
    ("corpus-echo", "message send alpha+memory -r '<a@b>' -s \"$(echo 'x')\" --no-body"),
    ("corpus-date", "message send alpha+memory -s \"RETIRED $(date '+%H:%M'): dup of 400\" -f b"),
    ("subst-inside-arith", 'message send bob -s "$(( $(wc -l < f) + 1 )) items" -f b'),
    # MOVED here from the allow list by SM-777: an unknown flag ahead of the
    # subject used to make judge_message fall silent, and the shell then ran
    # the backtick (`message send` has no --from, so click refuses it - after).
    ("unknown-flag", 'message send bob --from me -s "`x`" -f b'),
]


@pytest.mark.parametrize("cmd", [c for _, c in DENY], ids=[i for i, _ in DENY])
def test_a_damaged_subject_or_header_is_denied(cmd):
    assert guard.decide(cmd) is not None, "allowed, but should have been DENIED:\n" + cmd


def test_the_subject_deny_says_take_it_out_and_names_both_body_doors():
    r = guard.decide('message send bob -s "`date`" -f /tmp/b')
    assert "subject" in r and "TAKE IT OUT OF THE SUBJECT" in r
    assert "-f FILE" in r and guard.MCP_SEND in r and "byte-for-byte" in r
    assert "Single-quoting is not a fix" in r
    # and the deliberate case (`$(date)` in a subject, twice in the corpus)
    # is told the form that works, which the guard allows (variable-from-subst)
    assert "T=$(date +%H:%M)" in r and "`$T`" in r
    # the session names are gone from THIS advice: they will not exist
    assert "session mail send" not in r and "--body-file" not in r and "mail_send`" not in r


@pytest.mark.parametrize("cmd", [
    'message send bob -s"`date`" -f /tmp/b',
    'message send bob --subject "`date`" -f /tmp/b',
    'message send bob --subject="`date`" -f /tmp/b',
])
def test_every_spelling_gets_the_same_advice_byte_for_byte(cmd):
    assert guard.decide(cmd) == guard.decide('message send bob -s "`date`" -f /tmp/b')


def test_the_header_deny_offers_single_quotes_because_the_cli_takes_them():
    """A header is not the subject: `_send.parse_headers` refuses only CR, LF
    and NUL, so a single-quoted backtick arrives and is sent as typed."""
    r = guard.decide('message send bob -s ok -f b --header "X-Note: `date`"')
    assert "header" in r and "single-quote" in r and guard.MCP_SEND in r
    assert _send.parse_headers(("X-Note: `date`",))[1] == [("X-Note", "`date`")]


def test_a_deny_reaches_the_harness_as_decision_json():
    proc = run_hook('message send bob -s "`date`" -f /tmp/b')
    assert proc.returncode == 0
    hso = json.loads(proc.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"].startswith("mail-guard: the `message send` subject")


# ── must allow ───────────────────────────────────────────────────────────

ALLOW = [
    ("single-quoted", "message send bob -s 'plain subject' -f /tmp/b"),
    ("single-quoted-backtick", "message send bob -s 'run `date` now' -f /tmp/b"),
    ("single-quoted-subst", "message send bob --subject='$(x)' -f /tmp/b"),
    ("escaped-backtick", "message send bob -s \\`date\\` -f /tmp/b"),
    ("apostrophe-in-dq", "message send bob -s \"it's here\" -f /tmp/b"),
    ("variable", 'message send bob -s "Re: $SUBJ" -f /tmp/b'),
    ("header-single-quoted", "message send bob -s ok -f b --header 'X-Note: `date`'"),
    ("grep", "grep -rn 'message send' src/"),
    ("grep-dq", 'grep -rn "message send -s \\"`x`\\"" docs/'),
    ("comment", '# message send bob -s "`date`" -f /tmp/b'),
    ("trailing-comment", 'ls  # message send bob -s "`date`" -f /tmp/b'),
    ("heredoc-then-file",
     "cat > /tmp/b <<'EOF'\nrun `date` and $(hostname)\nEOF\nmessage send bob -s 'ok' -f /tmp/b"),
    ("heredoc-documenting",
     "cat <<'EOF' > doc.md\nmessage send bob -s \"`date`\" -f b\nEOF\n"),
    ("stdin-pipe", "printf '%s' 'a `b` c' | message send bob -s 'ok' -f -"),
    ("stdin-heredoc", "message send bob -s 'ok' -f - <<'EOF'\nbody `x` and $(y)\nEOF\n"),
    ("two-recipients", "message send bob carol -s 'ok' -f /tmp/b"),
    ("no-body", "message send bob -s 'done EOM'"),
    ("list", "message list --unread"),
    ("list-subject-regex", "message list --subject 'x'"),
    ("read", "message read 3"),
    ("read-thread", "message read --thread '<id@host>'"),
    ("help", "message send -h"),
    ("after-double-dash", "message send bob -s 'ok' -f b -- -s"),
    ("echo", 'echo "message send bob -s ok -f b"'),
    ("quoted-name", "'message' send bob -s \"`x`\" -f b"),
    # the one FALSE positive the 30-day scan found, before the fix: arithmetic
    # runs no command, and the CLI accepts the number it expands to
    ("arithmetic", 'message send alpha+memory -r "$ID" -s "${NUM:+}$(( ${NUM:-0} + 10 )) A RULE" -f "$F"'),
    ("variable-from-subst", 'T=$(date +%H:%M); message send bob -s "RETIRED $T: dup" -f b'),
    ("arithmetic-unquoted", "message send bob -s $((N+1)) -f b"),
    ("arithmetic-equals", "message send bob --subject=$((N+1)) -f b"),
]


@pytest.mark.parametrize("cmd", [c for _, c in ALLOW], ids=[i for i, _ in ALLOW])
def test_must_allow(cmd):
    reason = guard.decide(cmd)
    assert reason is None, "DENIED, but should have been allowed:\n%s\nreason: %s" % (cmd, reason)


def test_the_session_rules_are_intact_beside_it():
    assert guard.decide("session mail send box --from me 'body'") is not None
    assert guard.decide("printf x | session mail send box --from me --subject 'x'") is not None
    assert guard.decide("session mail send box --from me --subject \"`d`\" --body-file b") is not None


# ── lockstep with the CLI ────────────────────────────────────────────────

@pytest.mark.parametrize("subject", ["run `date` now", "$(x)"])
def test_what_the_guard_lets_through_quoted_the_cli_refuses(subject):
    """The guard allows a single-quoted backtick because it cannot EXECUTE -
    not because it is a working form. `message send` refuses it at parse time,
    before anything is written; if that ever stops, this goes red and the
    allow above needs a second look."""
    assert _send.check_subject(subject, env={}), subject


def test_the_flag_tables_match_the_click_command():
    """An unknown flag makes judge_message DENY (SM-777: it fails closed,
    since past it the walk cannot find the subject), so a flag added to
    `message send` and not here would be denied on every seat that uses it.
    This pins the tables - long AND short spellings, `p.opts` carries both -
    to the click command, so that goes red here first."""
    value, boolean = set(), set()
    for p in send.params:
        if isinstance(p, click.Option):
            (boolean if p.is_flag else value).update(p.opts + p.secondary_opts)
    ctx = click.Context(send, **send.context_settings)  # help_option_names: -h too
    boolean.update(send.get_help_option(ctx).opts)
    assert guard.MESSAGE_VALUE_FLAGS == value
    assert guard.MESSAGE_BOOL_FLAGS == boolean


# ── --message-id (SM-716 do-008): the guard reads past it ────────────────

def _decision(cmd):
    proc = run_hook(cmd)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow"
    return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_message_id_does_not_silence_the_subject_judge():
    """Before do-008 `--message-id` was not in the tables, and an unknown
    long flag made judge_message fall silent (the fail-open SM-777 closed: it
    is denied now), so a `--message-id` ahead of the subject let a backtick
    through. It is a value flag now: the guard steps over its value and
    judges the subject the same as without it."""
    damaged = 'message send bob --message-id x.1@hq -s "run `date` now" -f F'
    plain = 'message send bob -s "run `date` now" -f F'
    assert _decision(damaged) == _decision(plain) == "deny"
    assert guard.decide(damaged) == guard.decide(plain)
    # the equals spelling is stepped over too
    assert _decision('message send bob --message-id=x.1@hq -s "`date`" -f F') == "deny"
    # positive control: the same command without the backtick is allowed
    assert _decision('message send bob --message-id x.1@hq -s "run date now" -f F') == "allow"
