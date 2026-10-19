"""mail-guard fails CLOSED on a flag it does not know (SM-777).

Before SM-777 the guard's option walkers returned "no decision" at the first
flag missing from their tables: past it they cannot tell which word is the
subject. That was a fail-OPEN. `message send bob --frobnicate x -s "`cmd`" -f F`
got no decision, the shell ran `cmd`, and only then did click say
`No such option` (repro-001, measured on a live seat).

Now, on a send the guard has ALREADY matched, an unknown flag is denied,
long or short, in one line that names the flag. For `message send` the line
also names the tables to grow and the safe form. For the retired verb it
names no table. Two shapes are deliberately NOT denied, because click does
not refuse them as unknown: the help cluster (`-h`, `-hh`, `-hs X`), and a
known boolean given a value (`--json=1`, one word, stepped over). Commands the
guard does not match never reach the rule.

Every case runs through the pure function `decide` and, where the point is
the hook's real entry point, through `run_hook` (the subprocess the harness
runs).
"""

from __future__ import annotations

import json

import click
import pytest

from sessionmail.message import send
from test_mail_guard import RETIRED_ADVICE, guard, run_hook

BT = '"run `date` now"'  # a LIVE backtick: the shell runs it


def _decision(cmd):
    proc = run_hook(cmd)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow", None
    hso = json.loads(proc.stdout)["hookSpecificOutput"]
    return hso["permissionDecision"], hso["permissionDecisionReason"]


def _is_unknown_flag_deny(reason):
    return reason is not None and "a flag this guard does not know" in reason


# ── message send: an unknown LONG flag is denied ─────────────────────────

UNKNOWN_LONG = [
    # (id, command, the flag the deny must name)
    ("b1-before-subject", "message send bob --frobnicate x -s " + BT + " -f /tmp/b", "--frobnicate"),
    ("b2-before-recipient", "message send --frobnicate x bob -s " + BT + " -f /tmp/b", "--frobnicate"),
    ("b3-no-value", "message send bob --frobnicate -s " + BT + " -f /tmp/b", "--frobnicate"),
    ("c1-equals", "message send bob --frobnicate=x -s " + BT + " -f /tmp/b", "--frobnicate"),
    # a clean subject is denied too: past the flag the walk is out of step,
    # so "no damage found" would be another unconfident parse
    ("clean-subject", "message send bob --frobnicate x -s 'ok' -f /tmp/b", "--frobnicate"),
    ("clean-equals", "message send bob --frobnicate=x -s 'ok' -f /tmp/b", "--frobnicate"),
    # the corpus: real seats typed these, and click refuses both
    ("corpus-reply-to", "message send bob --reply-to '<a@b>' -s 'ok' -f /tmp/b", "--reply-to"),
    ("corpus-dry-run", "message send skillbox -s 'test' -f /dev/null --dry-run", "--dry-run"),
    ("session-habit-from", "message send bob --from me -s 'ok' -f /tmp/b", "--from"),
    ("abbreviation", "message send bob --subj 'ok' -f /tmp/b", "--subj"),
]


@pytest.mark.parametrize("cmd,flag", [(c, f) for _, c, f in UNKNOWN_LONG],
                         ids=[i for i, _, _ in UNKNOWN_LONG])
def test_an_unknown_long_flag_is_denied_naming_the_flag(cmd, flag):
    verdict, reason = _decision(cmd)
    assert verdict == "deny", cmd
    assert reason.startswith("mail-guard: `message send` was given `%s`," % flag), reason
    assert reason == "mail-guard: " + guard.decide(cmd)


def test_the_message_send_deny_is_one_line_naming_tables_file_and_safe_form():
    r = guard.decide("message send bob --frobnicate x -s 'ok' -f /tmp/b")
    assert "\n" not in r
    assert "MESSAGE_VALUE_FLAGS" in r and "MESSAGE_BOOL_FLAGS" in r
    assert "hooks/mail-guard/hook.py" in r
    assert "message send TO -s SUBJECT -f FILE" in r
    for dead in RETIRED_ADVICE:
        assert dead not in r, (dead, r)


def test_a_pathological_flag_is_cut_not_echoed_whole():
    flag = "--" + "x" * 500
    r = guard.decide("message send bob %s -s 'ok' -f /tmp/b" % flag)
    assert _is_unknown_flag_deny(r) and flag not in r and "..." in r


# ── message send: an unknown SHORT flag is denied ────────────────────────

UNKNOWN_SHORT = [
    ("d1", "message send bob -z x -s " + BT + " -f /tmp/b", "-z"),
    ("d2-cluster", "message send bob -zx -s " + BT + " -f /tmp/b", "-z"),
    ("d1-clean", "message send bob -z x -s 'ok' -f /tmp/b", "-z"),
    ("digit", "message send bob -1 -s 'ok' -f /tmp/b", "-1"),
    # click refuses `-hz` (NoSuchOption '-z', no help printed): not help
    ("help-then-unknown", "message send bob -hz -s 'ok' -f /tmp/b", "-z"),
]


@pytest.mark.parametrize("cmd,flag", [(c, f) for _, c, f in UNKNOWN_SHORT],
                         ids=[i for i, _, _ in UNKNOWN_SHORT])
def test_an_unknown_short_flag_is_denied_naming_the_letter(cmd, flag):
    verdict, reason = _decision(cmd)
    assert verdict == "deny", cmd
    assert reason.startswith("mail-guard: `message send` was given `%s`," % flag), reason


# ── message send: what is NOT denied ─────────────────────────────────────

HELP_CLUSTER = [
    # click: EXIT(help), nothing sent (findings/fix-001-clickprobe-short-output.txt)
    "message send -h",
    "message send bob -h",
    "message send bob -hh",
    "message send bob -hs ok",
    "message send bob -hsok",
    "message send -hc carol bob",
]


@pytest.mark.parametrize("cmd", HELP_CLUSTER)
def test_the_help_cluster_is_not_denied(cmd):
    assert _decision(cmd) == ("allow", None)


def test_the_help_cluster_still_judges_the_subject_it_carries():
    """`-hs X` parses X as the subject, and the shell expands X before click
    prints help, so a damaged X is still denied by the SUBJECT rule."""
    for cmd in ("message send bob -hs " + BT, 'message send bob -hs"`date`"'):
        r = guard.decide(cmd)
        assert r is not None and "TAKE IT OUT OF THE SUBJECT" in r, (cmd, r)
        assert not _is_unknown_flag_deny(r)


def test_a_known_boolean_given_a_value_is_stepped_over_not_denied():
    """`--json=1` is one word and cannot eat the next, so the walk stays exact:
    a clean subject is allowed (click refuses it in its own words), and a
    damaged one is denied by the subject rule, not as an unknown flag."""
    assert _decision("message send bob --json=1 -s 'ok' -f /tmp/b") == ("allow", None)
    assert _decision("message send bob --no-body=yes -s 'ok'") == ("allow", None)
    r = guard.decide("message send bob --json=1 -s " + BT + " -f /tmp/b")
    assert r is not None and "TAKE IT OUT OF THE SUBJECT" in r and not _is_unknown_flag_deny(r)


def test_every_option_click_declares_is_never_an_unknown_flag_deny():
    """The deny is reachable only by a flag click does NOT declare: walk every
    spelling `message send` declares, long and short (the same source the
    parity test reads), each in a clean, otherwise-valid send."""
    spellings = []
    for p in send.params:
        if isinstance(p, click.Option):
            spellings += [(o, p.is_flag) for o in p.opts + p.secondary_opts]
    ctx = click.Context(send, **send.context_settings)
    spellings += [(o, True) for o in send.get_help_option(ctx).opts]
    assert {o for o, _ in spellings} == guard.MESSAGE_VALUE_FLAGS | guard.MESSAGE_BOOL_FLAGS
    for opt, is_flag in spellings:
        forms = [opt] if is_flag else [opt + " v", (opt + "=v") if opt.startswith("--") else opt + "v"]
        for form in forms:
            cmd = "message send bob %s -s 'ok' -f /tmp/b" % form
            r = guard.decide(cmd)
            assert not _is_unknown_flag_deny(r), (cmd, r)


def test_after_double_dash_nothing_is_a_flag():
    assert _decision("message send -s 'ok' -f /tmp/b -- --frobnicate -z") == ("allow", None)


@pytest.mark.parametrize("cmd", [
    "message list --frobnicate x",
    "message read --frobnicate 3",
    "grep -rn -- 'message send --frobnicate' docs/",
    "echo message send bob --frobnicate x",
    "# message send bob --frobnicate x -s \"`x`\"",
    "'message' send bob --frobnicate x",
])
def test_a_command_the_guard_does_not_match_never_reaches_the_rule(cmd):
    assert _decision(cmd) == ("allow", None)


# ── the retired session verb: denied, and told the verb is retired ───────

SESSION_UNKNOWN = [
    ("e1", "session mail send bob --frobnicate x --subject " + BT + " --body-file /tmp/b", "--frobnicate"),
    ("e2", "session mail send bob --frobnicate=x --subject " + BT + " --body-file /tmp/b", "--frobnicate"),
    ("e3", "session mail send bob -z x --subject " + BT + " --body-file /tmp/b", "-z"),
    ("e4", "session-mail-send bob --frobnicate x --subject " + BT + " --body-file /tmp/b", "--frobnicate"),
    ("e6", "session mail send bob --frobnicate x " + BT, "--frobnicate"),
    ("clean", "session mail send bob --frobnicate x --subject 'ok' --body-file /tmp/b", "--frobnicate"),
]


@pytest.mark.parametrize("cmd,flag", [(c, f) for _, c, f in SESSION_UNKNOWN],
                         ids=[i for i, _, _ in SESSION_UNKNOWN])
def test_the_retired_verb_denies_an_unknown_flag_and_names_no_table(cmd, flag):
    verdict, reason = _decision(cmd)
    assert verdict == "deny", cmd
    assert reason.startswith("mail-guard: `session mail send` was given `%s`," % flag), reason
    assert reason.endswith(guard.SAFE_FORMS)  # "`session mail send` is retired ..."
    assert "VALUE_FLAGS" not in reason and "BOOL_FLAGS" not in reason
    assert "\n" not in reason
    for dead in RETIRED_ADVICE:
        assert dead not in reason, (dead, reason)


def test_the_retired_verb_steps_over_help_and_a_boolean_given_a_value():
    assert guard.decide("session mail send --help") is None
    assert guard.decide("session mail send -hh") is None
    clean = "session mail send box --from me --quiet=1 --subject 'ok' --body-file /tmp/b"
    assert guard.decide(clean) is None
