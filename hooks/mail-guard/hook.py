#!/usr/bin/env python3
"""mail-guard — PreToolUse hook on Bash: refuse a mail send the shell has damaged.

WHY THIS EXISTS. `session mail send` takes prose. When that prose is typed into
a shell, the SHELL reads it first: a backtick or `$( ` in a double-quoted body
or subject is expanded before the send program is reached, so the program
cannot detect what it lost. It exits 0, prints a Message-ID, and delivers a
message with words missing or replaced. Three measured incidents — a body with
a word eaten, a subject that truncated a body, and a seat that EXECUTED
`git ready approve` because the phrase sat in backticks in a SUBJECT line.
Nothing in the result distinguishes a damaged message from a good one, which is
why nobody checks, which is why it keeps happening.

This hook is the layer BEFORE the shell. It sees the command text as typed —
pre-expansion — which is the only place the executed-subject incident was still
visible, and it DENIES (never warns: a warning is read by a human reviewing a
transcript, not by the agent that already typed the command).

WHAT IT REFUSES, on a command-position invocation of `session mail send` (or of
the resolved script `session-mail-send`, which is how most real callers spell
it) - a verb RETIRED on 2030-12-08, so every deny below sends the caller to
`message send` and its MCP tool, never back to the retired forms:

  1. an inline body — a second positional word, or anything after a literal `--`
  2. a `--subject` whose RAW text carries a backtick or `$(`
  3. a heredoc or pipe feeding the send with no `--body-file`

AND on a command-position `message send` (sessionmail's own CLI, the verb that
survives `session`'s retirement), ONE rule, the subject's:

  4. a `-s`/`--subject` (every spelling click takes: `-s X`, `-sX`,
     `--subject X`, `--subject=X`) or a `--header` whose RAW text carries a
     backtick or `$(` OUTSIDE single quotes — or that IS a bare `$( )`.

Rules 1 and 3 do not apply there, on purpose: `message send`'s positional
words are RECIPIENTS, not a body; it refuses an inline body itself; and
`-f -` reads stdin, so a heredoc or pipe into it is the documented form.

BIAS — DENY ONLY ON A CONFIDENT PARSE. Anything this file cannot classify
(unterminated quotes, a command substitution inside double quotes) is ALLOWED
through. False negatives are acceptable where a determined caller can
obfuscate past any text filter. ONE EXCEPTION FAILS CLOSED: on a send
this guard has already matched, a flag its tables do not know is DENIED, long
or short. Past it the walk cannot say which word is the subject, and the
shell expands a backtick subject before the CLI can refuse the flag, so the
CLI's refusal is no floor there. Both verbs refuse such a flag anyway (click:
NoSuchOption; the retired verb exits 2), so the deny costs no working send.
`-h`/`-hX`, which click takes as help, is not denied. False positives are not:
they block unrelated work and teach agents to resent the guard, which is how
guards get bypassed culturally. So a `grep 'session mail send' transcript.md`,
a `# session mail send ...` comment, and a heredoc that merely CONTAINS the
words are all allowed — each has a test.

Written in python3 (stdlib only) on purpose: a quoting-aware guard written in
the language whose quoting is the hazard is how you get a guard with its own
injection bug.

Protocol: hook JSON on stdin; on deny, the documented PreToolUse decision JSON
on stdout with exit 0. ANY unexpected condition exits 0 silently (allow) — a
guard that can wedge the Bash tool is worse than the hazard it guards.

INSIDE A SUBAGENT (a payload that carries `agent_id`), it also refuses what
would take or mark the SEAT's mail or change its pager, through both
doors with ONE table (`subagent_verb`): the Bash command, and the
message and pager MCP servers' tools, which hooks.json routes here too. A
main thread's MCP call is allowed at once, exit 0 and no output.

Importable: `decide(command_text)` returns a deny reason string or None, so the
test suite drives the same code path the hook runs.
"""

import json
import posixpath
import sys

# ---------------------------------------------------------------------------
# What the RETIRED send's flags looked like, kept by hand. An UNKNOWN flag is
# DENIED (`judge`) - fail closed - and the deny names no table: the
# verb is gone (it exits 2 whatever its args), so nothing should grow here.
# ---------------------------------------------------------------------------
VALUE_FLAGS = {
    "--from",
    "--subject",
    "--project",
    "--task",
    "--supersedes",
    "--in-reply-to",
    "--reply-all",
    "--cc",
    "--bcc",
    "--root",
    # The safe form. Named here BEFORE the CLI grows it so this guard never
    # mistakes a body FILE for an inline body.
    "--body-file",
}
# `--no-parent-check` is a BOOLEAN and must be listed as one: it takes no
# value, so listing it as a value flag would eat the next word and miss an
# inline body after it. (Once, an UNLISTED flag made `judge` fall
# silent; now it is denied, which B16/C17 still pin from both sides.)
BOOL_FLAGS = {"--filedrop", "--quiet", "--no-parent-check", "-h", "--help"}

# Command names that may precede the real command without changing it.
PREFIX_WORDS = {"env", "command", "nohup", "exec", "builtin", "time"}

# The live door, named in every piece of advice this guard prints. The
# session verbs above are RETIRED: `session mail send` now only
# prints where its verbs went. Their matcher stays, but advice that sent an
# agent back to `--body-file` or the MCP `mail_send` tool sent it to a door
# that no longer opens.
MCP_SEND = "mcp__plugin_sessionmail_message__message_send"

SAFE_FORMS = (
    "`session mail send` is retired. Compose the body in a FILE and send it "
    "with `message send TO -s SUBJECT -f FILE` (`-f -` reads the body from "
    "stdin), or through the MCP tool `" + MCP_SEND + "`, which has no shell "
    "in the path at all."
)

# ---------------------------------------------------------------------------
# `message send`'s flags (src/sessionmail/message.py). Unlike the session table
# above this one is NOT kept in sync by hand: tests/test_mail_guard_message_send.py
# pins it against the click command's own params (long AND short spellings),
# so a new flag goes red there before this guard can DENY it in the field: an
# unknown flag fails closed, and the deny names these two tables.
# ---------------------------------------------------------------------------
MESSAGE_VALUE_FLAGS = {
    "-s", "--subject",
    "-f", "--body-file",
    "-r", "--in-reply-to",
    "--supersedes",
    "--message-id",
    "-c", "--cc",
    "--bcc",
    "--header",
    "--renderer",
}
MESSAGE_BOOL_FLAGS = {"--no-body", "--raw", "--no-shorten", "--json", "-h", "--help"}
# The values that are judged, and what the deny calls each.
MESSAGE_JUDGED = {"-s": "subject", "--subject": "subject", "--header": "header"}
# An op that OPENS a command substitution. A judged flag with no value left
# before one of these has the substitution AS its value (`-s $(hostname)`).
_SUBST_OPS = ("$(", "`")

# THE SUBJECT'S REMEDY IS NOT THE BODY'S. `message send` refuses a literal backtick or `$(` in a SUBJECT
# itself (_send.check_subject, at parse time, before anything is written),
# whichever door it came through, so single-quoting is not the fix: taking it
# out is. ONE constant, quoted by both judges - the retired verb's caller is
# told the live verb's remedy.
MESSAGE_SUBJECT_REMEDY = (
    "THE FIX IS TO TAKE IT OUT OF THE SUBJECT: retype the subject without the "
    "backtick or `$(`, and put the code span in the BODY - "
    "`message send <to> -s '<subject>' -f FILE`, or the MCP tool `" + MCP_SEND + "`, "
    "whose body travels byte-for-byte. Single-quoting is not a fix: it makes the "
    "character arrive literally, and `message send` refuses that in a subject too. "
    "If you MEANT the shell to run it (a time, a count), run it first - "
    "`T=$(date +%H:%M)` - and put `$T` in the subject."
)
# A HEADER value is not refused for a literal backtick (_send.parse_headers
# refuses only CR, LF and NUL), so there single quotes ARE a way through.
MESSAGE_HEADER_REMEDY = (
    "Put the code span in the BODY instead (`-f FILE`, or the MCP tool `" + MCP_SEND + "`, "
    "whose body travels byte-for-byte) - or, if the header must carry it, "
    "single-quote the value so it arrives as typed."
)

# AN UNKNOWN FLAG FAILS CLOSED. The walkers below cannot tell whether
# a flag they do not know eats the next word, so past one they cannot say which
# word is the subject. Falling silent there was a fail-OPEN: the shell expands
# a backtick subject BEFORE the CLI ever sees the flag, so the CLI's own
# "No such option" refusal comes too late to be the floor (measured). Both verbs refuse
# such a command anyway - click with
# NoSuchOption, the retired verb with exit 2 whatever its args - so this deny
# costs no command that could have sent.
_FLAG_SHOWN_MAX = 60  # a pathological "flag" is cut, not echoed whole


def _shown(flag):
    return flag if len(flag) <= _FLAG_SHOWN_MAX else flag[:_FLAG_SHOWN_MAX] + "..."


def _message_unknown_flag(flag):
    """The one-line deny for a `message send` flag the tables do not know."""
    return (
        "`message send` was given `%s`, a flag this guard does not know, so it "
        "cannot tell which word is the subject and denies rather than guess "
        "If `%s` is a real `message send` option, add it to "
        "MESSAGE_VALUE_FLAGS or MESSAGE_BOOL_FLAGS in hooks/mail-guard/hook.py; "
        "if not, drop it and send with `message send TO -s SUBJECT -f FILE`."
        % (_shown(flag), _shown(flag))
    )


def _session_unknown_flag(flag):
    """The one-line deny for an unknown flag on the RETIRED session verb. It
    names no table: nobody should grow VALUE_FLAGS for a CLI that is gone."""
    return (
        "`session mail send` was given `%s`, a flag this guard does not know, "
        "so it cannot tell which word is the subject or the body and denies "
        "rather than guess. " % _shown(flag)
    ) + SAFE_FORMS


MAX_COMMAND_BYTES = 200_000  # beyond this, allow: not worth a pathological scan


class _Tok:
    """One shell token. `raw` is the text AS TYPED (quotes included) — the whole
    point of this hook is to judge the pre-expansion text; `value` is the same
    token with one layer of quoting removed and NOTHING expanded."""

    __slots__ = ("raw", "value", "kind", "redir_target")

    def __init__(self, raw, value, kind, redir_target=False):
        self.raw = raw
        self.value = value
        self.kind = kind  # "word" | "op" | "redir"
        self.redir_target = redir_target

    def __repr__(self):  # pragma: no cover - debugging aid
        return "_Tok(%r, %r, %r)" % (self.raw, self.value, self.kind)


class _Unparsable(Exception):
    """The text is not something this guard understands well enough to judge."""


# Operators that end a command. `$(` and a backtick OPEN a new command context,
# which is command position too — a send hidden in a substitution still counts.
_OPS = ("&&", "||", ";;", ";", "|&", "|", "&", "\n", "(", ")", "$(", "`")


def tokenize(text):
    """Split shell-ish text into tokens, tracking quotes, comments, redirects
    and heredocs. Raises _Unparsable on anything ambiguous."""
    toks = []
    raw = []
    val = []
    in_word = False
    i = 0
    n = len(text)
    pending_heredocs = []  # [(delimiter, strip_tabs)]
    expect_heredoc_delim = None  # strip_tabs flag while waiting for the word

    def flush():
        """Emit the word under construction. A word emitted while a heredoc
        operator is waiting for its delimiter IS that delimiter — recorded so
        the body can be skipped at the next newline."""
        nonlocal in_word, raw, val, expect_heredoc_delim
        if in_word:
            tok = _Tok("".join(raw), "".join(val), "word")
            toks.append(tok)
            raw, val = [], []
            in_word = False
            if expect_heredoc_delim is not None:
                pending_heredocs.append((tok.value, expect_heredoc_delim))
                expect_heredoc_delim = None

    while i < n:
        c = text[i]

        # --- a newline: end of word, and the moment pending heredoc bodies are
        # consumed. Their CONTENT is data, never commands — skipping it is what
        # keeps `cat <<'EOF' > f ... session mail send ... EOF` from being
        # denied for writing a document.
        if c == "\n":
            flush()
            toks.append(_Tok("\n", "\n", "op"))
            i += 1
            while pending_heredocs:
                delim, strip_tabs = pending_heredocs.pop(0)
                while i < n:
                    eol = text.find("\n", i)
                    line = text[i:] if eol < 0 else text[i:eol]
                    i = n if eol < 0 else eol + 1
                    probe = line.lstrip("\t") if strip_tabs else line
                    if probe.rstrip("\r") == delim:
                        break
            continue

        if c in " \t":
            flush()
            i += 1
            continue

        # --- comment: `#` where a word could start runs to end of line.
        if not in_word and c == "#":
            eol = text.find("\n", i)
            i = n if eol < 0 else eol
            continue

        # --- single quotes: literal, no expansion, no escapes.
        if c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                raise _Unparsable("unterminated single quote")
            raw.append(text[i : end + 1])
            val.append(text[i + 1 : end])
            in_word = True
            i = end + 1
            continue

        # --- double quotes: expansion happens inside, but we do NOT try to
        # parse a nested `$(...)` — quoting inside a substitution inside quotes
        # is exactly the ambiguity this guard refuses to guess at. The text is
        # kept verbatim as one word's raw, and the guard stays silent about any
        # command hidden in there (a deliberate false negative).
        if c == '"':
            j = i + 1
            buf = []
            closed = False
            while j < n:
                d = text[j]
                if d == "\\" and j + 1 < n and text[j + 1] in '$`"\\\n':
                    if text[j + 1] != "\n":
                        buf.append(text[j + 1])
                    j += 2
                    continue
                if d == '"':
                    closed = True
                    break
                buf.append(d)
                j += 1
            if not closed:
                raise _Unparsable("unterminated double quote")
            raw.append(text[i : j + 1])
            val.append("".join(buf))
            in_word = True
            i = j + 1
            continue

        # --- backslash outside quotes escapes the next character.
        if c == "\\":
            if i + 1 >= n:
                raise _Unparsable("trailing backslash")
            if text[i + 1] == "\n":  # line continuation
                i += 2
                continue
            raw.append(text[i : i + 2])
            val.append(text[i + 1])
            in_word = True
            i += 2
            continue

        # --- redirections, heredocs included. Recognised BEFORE the operator
        # table so `>` never looks like a word, and so a redirect TARGET is
        # never miscounted as an inline body (`... --from me > out.txt`).
        if c in "<>" or (c == "&" and text[i : i + 2] == "&>"):
            # A bare file-descriptor prefix (`2` in `2>&1`) is part of the
            # redirection, not an argument — drop it, or it counts as an inline
            # body and denies a perfectly safe send.
            if in_word and raw and "".join(raw).isdigit():
                raw, val, in_word = [], [], False
            flush()
            if text[i : i + 3] in ("<<-",):
                op, adv, strip_tabs, heredoc = "<<-", 3, True, True
            elif text[i : i + 3] == "<<<":
                op, adv, strip_tabs, heredoc = "<<<", 3, False, False
            elif text[i : i + 2] == "<<":
                op, adv, strip_tabs, heredoc = "<<", 2, False, True
            elif text[i : i + 2] in (">>", ">&", "<&", "&>"):
                op, adv, strip_tabs, heredoc = text[i : i + 2], 2, False, False
            else:
                op, adv, strip_tabs, heredoc = c, 1, False, False
            toks.append(_Tok(op, op, "redir"))
            i += adv
            if heredoc:
                expect_heredoc_delim = strip_tabs
            continue

        # --- operators.
        if not in_word or c in "&|;()`" or text[i : i + 2] == "$(":
            matched = None
            for op in _OPS:
                if text.startswith(op, i):
                    matched = op
                    break
            if matched:
                flush()
                toks.append(_Tok(matched, matched, "op"))
                i += len(matched)
                continue

        # --- ordinary character.
        raw.append(c)
        val.append(c)
        in_word = True
        i += 1

    flush()

    # Post-pass: a word directly after a redirect operator is that redirect's
    # TARGET (or a heredoc delimiter), never an argument to the command — so it
    # can never be miscounted as an inline body.
    for k in range(len(toks) - 1):
        if toks[k].kind == "redir" and toks[k + 1].kind == "word":
            toks[k + 1].redir_target = True
    return toks


class _Cmd:
    __slots__ = ("words", "is_pipe_consumer", "has_heredoc", "ended_by")

    def __init__(self):
        self.words = []
        self.is_pipe_consumer = False
        self.has_heredoc = False
        self.ended_by = None  # the op that closed this command, if any


def split_commands(toks):
    """Group tokens into simple commands, remembering the two facts about a
    command's INPUT that matter here: was it piped into, and is a heredoc
    feeding it - and the op that ENDED it, which is how `-s $(date)` shows:
    the lexer splits at `$(`, so `-s` is left as the command's last word.
    `$((` lexes as `$(` then `(`: that is ARITHMETIC, which runs no command,
    and is recorded as `$((` so it is never taken for a substitution."""
    cmds = []
    cur = _Cmd()
    just_closed = None  # the command the PREVIOUS token closed, if it did
    for t in toks:
        if t.kind == "op":
            if just_closed is not None and just_closed.ended_by == "$(" and t.raw == "(":
                just_closed.ended_by = "$(("
            just_closed = None
            if cur.words or cur.has_heredoc:
                cur.ended_by = t.raw
                cmds.append(cur)
                just_closed = cur
            cur = _Cmd()
            cur.is_pipe_consumer = t.raw in ("|", "|&")
            continue
        just_closed = None
        if t.kind == "redir":
            if t.raw in ("<<", "<<-"):
                cur.has_heredoc = True
            continue
        if t.redir_target:
            continue
        cur.words.append(t)
    if cur.words or cur.has_heredoc:
        cmds.append(cur)
    return cmds


def _is_assignment(value):
    if "=" not in value:
        return False
    name = value.split("=", 1)[0]
    return bool(name) and (name[0].isalpha() or name[0] == "_") and all(
        ch.isalnum() or ch == "_" for ch in name
    )


def _command_head(words):
    """The index of the command NAME: past `VAR=x` assignments and prefix
    words like `env` and `nohup`. Returns None when there is none, or when it
    is quoted or escaped - a quoted first word is overwhelmingly more likely
    to be data being handed to something else than a real invocation."""
    idx = 0
    while idx < len(words):
        w = words[idx]
        if _is_assignment(w.value):
            idx += 1
            continue
        if w.value in PREFIX_WORDS:
            idx += 1
            continue
        break
    if idx >= len(words):
        return None
    head = words[idx]
    if head.raw != head.value:  # quoted or escaped: not a command name we trust
        return None
    return idx


def match_send(words):
    """Is this simple command a `session mail send` / `session-mail-send`
    invocation? Returns its argument tokens, or None.

    Command position only. A quoted command name (`'session' mail send`) is not
    matched: a quoted first word is overwhelmingly more likely to be data being
    handed to something else than a real invocation."""
    idx = _command_head(words)
    if idx is None:
        return None
    head = words[idx]
    base = posixpath.basename(head.value)
    if base == "session-mail-send":
        return words[idx + 1 :]
    if base == "session":
        rest = words[idx + 1 :]
        if len(rest) >= 2 and rest[0].value == "mail" and rest[1].value == "send":
            return rest[2:]
    return None


def match_message_send(words):
    """Is this simple command a `message send` (sessionmail's CLI)? Returns
    its argument tokens, or None. Command position only, as match_send."""
    idx = _command_head(words)
    if idx is None:
        return None
    if posixpath.basename(words[idx].value) != "message":
        return None
    rest = words[idx + 1 :]
    if rest and rest[0].value == "send":
        return rest[1:]
    return None


def _live_text(raw):
    """The part of a word's RAW text the shell still scans for substitutions:
    everything outside single quotes, less backslash-escaped characters.
    `'a `b` c'` and `a\\`b\\`` both arrive literally, so neither can EXECUTE
    anything; a `'` inside double quotes is an apostrophe, not a quote."""
    out = []
    i, n = 0, len(raw)
    in_dq = False
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            if not in_dq or raw[i + 1] in '$`"\\\n':
                i += 2  # escaped: arrives literally, never expands
                continue
        elif c == '"':
            in_dq = not in_dq
            i += 1
            continue
        elif c == "'" and not in_dq:
            end = raw.find("'", i + 1)
            if end < 0:
                return raw  # unbalanced: judge all of it
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _message_damage(raw):
    """_subject_damage, on the live text, with ARITHMETIC set aside: `$((`
    opens `$(( expr ))`, which runs no command. Measured, the one false
    positive in 30 days of transcripts: a subject numbering a
    memory item with `$(( ${NUM:-0} + 10 ))`, which the CLI would have
    accepted. A substitution NESTED in arithmetic keeps its own `$(` and is
    still caught; `$((cmd) )` without the space is missed (a false negative,
    which the bias accepts). The session rule is left exactly as it was."""
    return _subject_damage(_live_text(raw).replace("$((", "(("))


def _message_deny(where, what):
    remedy = MESSAGE_SUBJECT_REMEDY if where == "subject" else MESSAGE_HEADER_REMEDY
    return (
        "the `message send` %s carries %s outside single quotes, which this "
        "shell expands BEFORE the send runs - that is how a subject line got "
        "EXECUTED. %s" % (where, what, remedy)
    )


def judge_message(args, cmd):
    """Return a deny reason for one matched `message send`, or None to allow.

    Only the subject rule, on `-s`/`--subject` and `--header`. The positional
    words are recipients; an inline body is the CLI's to refuse, and `-f -`
    reads stdin by design, so neither the inline-body nor the heredoc/pipe
    rule is here. Parsed as click parses it (measured against the command,
    2030-12-08): options may follow recipients; `-sX` is `-s X`; the word
    after `-s` is its value even when it starts with `-`; `--` ends the
    options; no long option may be abbreviated."""
    last = len(args) - 1
    idx = 0
    while idx <= last:
        t = args[idx]
        v = t.value

        if v == "--":
            return None  # every word after it is a recipient

        if v.startswith("--"):
            name, eq, value = v.partition("=")
            if eq:
                if name in MESSAGE_BOOL_FLAGS:
                    # A boolean given a value (`--json=1`) is ONE word: it
                    # cannot eat the next, so the walk stays exact. Step over
                    # it and judge on; click refuses it in its own words.
                    idx += 1
                    continue
                if name not in MESSAGE_VALUE_FLAGS:
                    return _message_unknown_flag(name)
                where = MESSAGE_JUDGED.get(name)
                if where:
                    what = _message_damage(t.raw)
                    if not what and value == "" and idx == last and cmd.ended_by in _SUBST_OPS:
                        what = "a command substitution as its whole value"
                    if what:
                        return _message_deny(where, what)
                idx += 1
                continue
            if v in MESSAGE_BOOL_FLAGS:
                idx += 1
                continue
            if v not in MESSAGE_VALUE_FLAGS:
                return _message_unknown_flag(v)
            flag, attached = v, None
        elif v.startswith("-") and v != "-":
            # A short cluster, walked letter by letter as click walks it
            # (measured): boolean letters stack (`-h`, `-hh`:
            # eager help, nothing sent); the first VALUE letter takes the rest
            # of the word (`-sX`, `-hsX`) or else the next word as its value;
            # any other letter is click's NoSuchOption, even after `-h`
            # (`-hz`), so it is denied.
            pos, flag = 1, None
            while pos < len(v):
                letter = "-" + v[pos]
                if letter in MESSAGE_BOOL_FLAGS:
                    pos += 1
                    continue
                if letter not in MESSAGE_VALUE_FLAGS:
                    return _message_unknown_flag(letter)
                flag = letter
                break
            if flag is None:
                idx += 1  # booleans only: the help cluster
                continue
            attached = v[pos + 1 :] or None
        else:
            idx += 1  # a recipient
            continue

        # A value flag: its value is attached (`-sX`) or the next word.
        where = MESSAGE_JUDGED.get(flag)
        if attached is not None:
            if where:
                what = _message_damage(t.raw)
                if what:
                    return _message_deny(where, what)
            idx += 1
            continue
        if idx == last:
            if where and cmd.ended_by in _SUBST_OPS:
                return _message_deny(where, "a command substitution as its whole value")
            return None  # truncated: not a confident parse
        if where:
            what = _message_damage(args[idx + 1].raw)
            if what:
                return _message_deny(where, what)
        idx += 2

    return None


def _subject_damage(raw):
    """The two shapes that have actually rewritten a message. Judged on the RAW
    text — pre-expansion — because after the shell has run there is nothing
    left to see. A single-quoted `$(` is not a shell hazard, and is refused
    anyway: the CLI's own parse-time subject guard refuses it too, so allowing
    it here would teach a form the send rejects."""
    if "`" in raw:
        return "a backtick"
    if "$(" in raw:
        return "a $( command substitution"
    return None


def judge(args, cmd):
    """Return a deny reason for one matched invocation, or None to allow."""
    positionals = 0
    reply_all = False
    body_file = False
    idx = 0
    while idx < len(args):
        t = args[idx]
        v = t.value

        if v == "--":
            if idx + 1 < len(args):
                return (
                    "the body is passed inline (after `--`), so the SHELL reads "
                    "it before the send does. " + SAFE_FORMS
                )
            idx += 1
            continue

        if v.startswith("--") and "=" in v:
            name = v.split("=", 1)[0]
            if name in BOOL_FLAGS:
                idx += 1  # a boolean given a value: ONE word, step over it
                continue
            if name not in VALUE_FLAGS:
                return _session_unknown_flag(name)
            if name == "--subject":
                what = _subject_damage(t.raw.split("=", 1)[1])
                if what:
                    return (
                        "the --subject carries %s, which this shell expands "
                        "BEFORE the send runs — that is how a subject line got "
                        "EXECUTED. %s" % (what, MESSAGE_SUBJECT_REMEDY)
                    )
            if name == "--body-file":
                body_file = True
            idx += 1
            continue

        if v in VALUE_FLAGS:
            if idx + 1 >= len(args):
                return None  # truncated: not a confident parse
            valtok = args[idx + 1]
            if v == "--subject":
                what = _subject_damage(valtok.raw)
                if what:
                    return (
                        "the --subject carries %s, which this shell expands "
                        "BEFORE the send runs — that is how a subject line got "
                        "EXECUTED. %s" % (what, MESSAGE_SUBJECT_REMEDY)
                    )
            if v == "--body-file":
                body_file = True
            if v == "--reply-all":
                reply_all = True
            idx += 2
            continue

        if v in BOOL_FLAGS:
            idx += 1
            continue

        if v.startswith("--"):
            return _session_unknown_flag(v)
        if v.startswith("-") and v != "-":
            # A short cluster: boolean letters (`-h`, `-hh`: the help cluster)
            # step over, as `message send` treats them; any other letter is
            # unknown - the session table has no short value flag.
            for ch in v[1:]:
                if "-" + ch not in BOOL_FLAGS:
                    return _session_unknown_flag("-" + ch)
            idx += 1
            continue

        positionals += 1
        idx += 1

    # `--reply-all` derives the recipient from the parent, so it takes NO
    # positional <to>; the first bare word there is already a body.
    allowed_positionals = 0 if reply_all else 1
    if positionals > allowed_positionals:
        return (
            "the body is passed inline (a bare word after the recipient), so "
            "the SHELL reads it before the send does — a backtick or $( in it "
            "is expanded, and the send still exits 0 with a Message-ID for the "
            "REWRITTEN text. " + SAFE_FORMS
        )

    if (cmd.has_heredoc or cmd.is_pipe_consumer) and not body_file:
        return (
            "a heredoc/pipe is feeding this send, and `session mail send` does "
            "not read stdin — the text you piped in is discarded, so the body "
            "you meant to send is not in this command at all (the CLI refuses "
            "it now; before that refusal landed it delivered an EMPTY message "
            "at exit 0). "
            + SAFE_FORMS
        )

    return None


# ── inside a subagent, the seat's mail and pager are not yours ──────────────
#
# A subagent's Bash runs with its seat's environment and the same parent
# process (measured on Claude Code 2.1.300), so `pager` and `message` cannot
# tell it from the seat: what it takes or marks, it takes or marks AS THE SEAT,
# and the seat's pager does not page (`pager status --new` drains the queue; a
# `read` or `seen` mark counts as given, a `done` as terminal). The one place
# the difference shows is this hook's payload: a subagent's PreToolUse carries
# `agent_id`, the main thread's never does (measured on 2.1.289 and
# 2.1.300). So `main` asks `decide_subagent` only then, and a
# main-thread decision is exactly what it was before.
#
# Text-matched, like the rest of this file: it stops an accident, not an
# adversary (a script that runs `message read` goes round it), and it fails
# OPEN like the rest. Read-only verbs stay allowed: `message list`, `message
# read --peek`, `pager status` without --new, `pager why`, and every send.

#: `pager` verbs that change or shadow the SEAT's pager.
#: `pager hook <event>` is refused as well (see `match_subagent`).
SUBAGENT_PAGER_VERBS = ("stop", "off", "mute", "unmute", "listen")
#: Shells whose `-c` script is judged too, one level down (cheap: it is one
#: word, and the same lexer reads it).
SUBAGENT_SHELLS = ("sh", "bash", "zsh", "dash")
#: How deep `bash -c 'bash -c ...'` is followed before giving up (allow).
SUBAGENT_DEPTH = 2
#: Shell reserved words that open a command position, so the command after
#: them is the one that runs: `{ message read 1; }`, `if message read 1; then`,
#: `for i in 1 2; do message read $i; done`. Skipped on the subagent path only;
#: `for`/`case`/`in` are NOT here (what follows them is a name or data).
SUBAGENT_KEYWORDS = frozenset({"{", "!", "if", "then", "elif", "else", "do", "while", "until"})
_HELP = ("-h", "--help")


def subagent_reason(what, tool=None):
    """The deny's one line, naming what was refused, why, and the read-only way.
    TOOL, the MCP tool's short name, names the tool and its read-only
    forms instead; without it the line is the Bash door's, byte for byte."""
    if tool:
        return ("a subagent shares its seat's session, so the MCP tool `%s` (`%s`) here "
                "would take or mark the SEAT's mail or change its pager, and the seat "
                "would not be paged. Read-only instead: `message_list`, "
                "`message_read` with `peek: true`; leave marking and the pager to the "
                "main thread." % (tool, what))
    return ("a subagent shares its seat's session, so `%s` here would take or mark the "
            "SEAT's mail or change its pager, and the seat would not be paged. "
            "Read-only instead: `message list`, `message read --peek N`; leave marking "
            "and the pager to the main thread." % what)


def _shell_script(words, idx):
    """The `-c` script of a `bash -c '...'` at IDX, or None. `-c` may be
    bundled (`-lc`, `-ec`); the script is the next word."""
    for k in range(idx + 1, len(words) - 1):
        v = words[k].value
        if v == "--" or not v.startswith("-"):
            return None             # past the options: a script file, not -c
        if v.startswith("--"):
            continue                # --login, --noprofile: not the -c we want
        if "c" in v[1:]:
            return words[k + 1].value
    return None


def _past_keywords(words):
    """WORDS without the unquoted reserved words that lead them, so the
    command a compound statement runs is judged as its head."""
    k = 0
    while k < len(words) and words[k].raw == words[k].value \
            and words[k].value in SUBAGENT_KEYWORDS:
        k += 1
    return words[k:]


def match_subagent(words):
    """What one simple command would do to the seat, as the deny names it
    (`message read`, `pager status --new`, ...), or None if it is not ours."""
    idx = _command_head(words)
    if idx is None:
        return None
    base = posixpath.basename(words[idx].value)
    if base not in ("message", "pager"):
        return None
    return subagent_verb(base, [w.value for w in words[idx + 1 :]])


def subagent_verb(base, rest):
    """THE table, for both doors (Bash and MCP): what `BASE REST...`
    (`message` or `pager`, then the words after it) would do to the seat, as
    the deny names it, or None. The MCP door rebuilds REST from the tool call
    (`mcp_call`), so one list decides both."""
    if not rest or any(v in _HELP for v in rest):
        return None                 # bare, or help: prints, touches nothing
    verb, args = rest[0], rest[1:]
    if base == "message":
        if verb == "read" and "--peek" not in args:
            return "message read"
        if verb == "mark":
            return "message mark"
        return None
    if verb == "status" and "--new" in args:
        return "pager status --new"
    if verb in SUBAGENT_PAGER_VERBS:
        return "pager " + verb
    if verb == "hook" and args:
        # Typed into Bash, a hook has no payload
        # on stdin, so no `agent_id`, and `pager hook batch` scoops as a main
        # thread would. The harness runs the real hooks as hook commands,
        # never through the Bash tool, so a main thread is never refused one.
        return "pager hook " + args[0]
    return None


def decide_subagent(command_text, depth=0):
    """The subagent judge, for a payload that carries `agent_id`: deny reason,
    or None to allow. Pure, and it never raises (a guard bug must never
    block a tool call)."""
    if not command_text or len(command_text) > MAX_COMMAND_BYTES:
        return None
    if "message" not in command_text and "pager" not in command_text:
        return None  # cheap reject: neither verb is named anywhere
    try:
        for cmd in split_commands(tokenize(command_text)):
            words = _past_keywords(cmd.words)
            what = match_subagent(words)
            if what:
                return subagent_reason(what)
            idx = _command_head(words)
            if (idx is not None and depth < SUBAGENT_DEPTH
                    and posixpath.basename(words[idx].value) in SUBAGENT_SHELLS):
                script = _shell_script(words, idx)
                if script:
                    reason = decide_subagent(script, depth + 1)
                    if reason:
                        return reason
    except _Unparsable:
        return None
    except Exception:  # noqa: BLE001 - a guard bug must never block a tool call
        return None
    return None


# ── the same table, through the MCP door ────────────────────────────────────
#
# The plugin's MCP servers (`.mcp.json`: `message mcp`, `pager mcp`) serve the
# verbs as tools, and run with the seat's environment, so a subagent's
# `message_read` takes the mail as the seat just as its Bash `message read`
# does. A subagent is handed those tools, and its MCP PreToolUse carries
# `agent_id` (measured, Claude Code 2.1.301). hooks.json routes the
# message and pager servers' tools here (not memory: none of its verbs is
# denied). `main` asks only when `agent_id` is present; a main thread's MCP
# call is allowed at once, exit 0 and no output, as when the hook never ran.
#
# No second list: `mcp_call` rebuilds the words the server's CLI would read
# (`src/sessionmail/_mcp.py` `_argv`), and `subagent_verb` judges them.

#: A plugin MCP tool's name: `mcp__plugin_sessionmail_<server>__<server>_<verb>`
#: (measured for a `--plugin-dir` load named sessionmail, the fleet's load).
MCP_TOOL_PREFIX = "mcp__plugin_sessionmail_"
#: The servers whose tools are judged (the hooks.json matcher's group).
SUBAGENT_MCP_SERVERS = ("message", "pager")
#: A tool's flag parameter -> the CLI flag `_argv` rebuilds from it. Only the
#: flags the table reads: `message read --peek`, `pager status --new`.
SUBAGENT_MCP_FLAGS = (("peek", "--peek"), ("new_only", "--new"))
#: Tool -> its positional parameter whose words `_argv` passes through to the
#: CLI, so `"args": ["1", "--peek"]` counts as `--peek` does in Bash. Only the
#: table's tools that have one; any other tool ignores the key, as `_argv` does.
SUBAGENT_MCP_WORDS = {"message_read": "args"}
#: The strings the server's validator (pydantic, lax) reads as TRUE for a bool
#: parameter, case-insensitive (measured, pydantic in the fleet venv). A flag
#: is set only on one of these, True, or 1: so `"peek": "yes"` counts as the
#: server counts it, and `"peek": "false"` is no peek.
_MCP_TRUE = frozenset({"1", "on", "t", "true", "y", "yes"})


def _mcp_true(value):
    """Would the server read VALUE, a bool parameter's input, as True?"""
    if isinstance(value, str):
        return value.lower() in _MCP_TRUE
    if isinstance(value, (int, float)):
        return value == 1                   # True, 1, 1.0
    return False


def mcp_call(tool_name, tool_input):
    """(base, rest) the server would run for TOOL_NAME with TOOL_INPUT, in
    `subagent_verb`'s terms (`("message", ["read", "1"])`), or None if it is
    not a message or pager server tool."""
    if not isinstance(tool_name, str) or not tool_name.startswith(MCP_TOOL_PREFIX):
        return None
    server, sep, tool = tool_name[len(MCP_TOOL_PREFIX):].partition("__")
    if not sep or server not in SUBAGENT_MCP_SERVERS or not tool.startswith(server + "_"):
        return None
    verb = tool[len(server) + 1:]
    if not verb:
        return None
    rest = [verb]
    key = SUBAGENT_MCP_WORDS.get(tool)
    words = tool_input.get(key) if key else None
    if isinstance(words, (list, tuple)):
        rest.extend(str(w) for w in words)
    elif words is not None:
        rest.append(str(words))
    for key, flag in SUBAGENT_MCP_FLAGS:
        if _mcp_true(tool_input.get(key)):
            rest.append(flag)
    return server, rest


def decide_subagent_mcp(tool_name, tool_input):
    """The MCP judge, for an MCP payload that carries `agent_id`: deny
    reason, or None to allow. Pure, and it never raises: a non-dict input,
    an unknown tool, anything odd, is allowed (fail OPEN)."""
    try:
        if not isinstance(tool_input, dict):
            return None
        call = mcp_call(tool_name, tool_input)
        if call is None:
            return None
        what = subagent_verb(*call)
        if not what:
            return None
        return subagent_reason(what, tool_name.rsplit("__", 1)[1])
    except Exception:  # noqa: BLE001 - a guard bug must never block a tool call
        return None


def decide(command_text):
    """The whole guard, as one pure function: deny reason, or None to allow."""
    if not command_text or len(command_text) > MAX_COMMAND_BYTES:
        return None
    if "session" not in command_text and "message" not in command_text:
        return None  # cheap reject; nothing else can match
    # EVERY step inside the try, not only the lexer and the judge: before the
    # move, split_commands() and match_send() sat outside it, so `decide()`
    # could raise. The hook process still exited 0 (main's own wrapper), but
    # the importable function - the one the tests and the corpus scan call -
    # did not keep the promise this module makes.
    try:
        toks = tokenize(command_text)
        for cmd in split_commands(toks):
            args = match_send(cmd.words)
            if args is not None:
                reason = judge(args, cmd)
            else:
                args = match_message_send(cmd.words)
                if args is None:
                    continue
                reason = judge_message(args, cmd)
            if reason:
                return reason
    except _Unparsable:
        return None
    except Exception:  # noqa: BLE001 - a guard bug must never block a tool call
        return None
    return None


# ── the deny's row in the session's pager inject log ────────────────────────
#
# openspec/changes/pager-inject-log, "The mail-guard's deny is logged from
# stdlib, and fails open" (its form: `event: inject, via: pretooluse-deny`). The deny reason is text fed to the
# agent, so it is an injection like any other, and gets the same row in the
# same file - written by THIS stdlib copy of sessionmail's `_injectlog`
# protocol, because this file runs on system python3 and cannot import
# sessionmail (mail-guard-hook). The protocol, kept in step by
# tests/test_pager_inject_deny.py, which runs both writers at once:
#
#   <session dir> = $HOME/.session/sessions/<sid> (sessiontools' session_dir)
#   log/mail/ made 0700 (its parents plainly); every file 0600
#   one exclusive flock on log/mail/seq; the counter bumped FIRST,
#     fixed width (20 digits) at offset 0, never truncated
#   pager.jsonl renamed to pager.1.jsonl at 16 MB, INSIDE that lock
#   one O_APPEND write of one compact JSON line, ensure_ascii=False
#
# What this copy does NOT do, on purpose:
#   - `seat` is null. sessionmail resolves a seat by folding the declared
#     address and checking its domain against THIS host (`_seat.resolve`,
#     `declared_address`, `local_domain`); a stdlib copy of that would be a
#     third implementation to drift.
#   - No capture. The `capture` switch is layered through sessiontools'
#     merge (`_layer.read`), which stdlib cannot read faithfully, so the deny
#     row is SIZE-ONLY whatever the switch says.
# No session id: nothing. ANY failure: no row, and the deny has already
# printed (fail OPEN).

LOG_DIR = ("log", "mail")
LOG_FILE = "pager.jsonl"
LOG_ROLLED = "pager.1.jsonl"
LOG_SEQ = "seq"
LOG_SEQ_WIDTH = 20
LOG_ROLL_AT = 16 * 1024 * 1024
#: sessiontools.SESSION_ID_VARS: the id when the payload has none.
SESSION_ID_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
VIA_DENY = "pretooluse-deny"


def _tokens_estimate(text):
    """sessionmail `_tokens.estimate`, the same rule: chars/4, at least 1."""
    return max(1, len(text) // 4) if text else 0


def _session_of(payload, env):
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    for var in SESSION_ID_VARS:
        value = (env.get(var) or "").strip()
        if value:
            return value
    return None


def log_dir(session, env=None):
    """`<session dir>/log/mail`, as sessionmail's `_injectlog.log_dir`
    finds it, or None for no session (`-` included)."""
    import os
    env = os.environ if env is None else env
    if not session or session == "-":
        return None
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".session", "sessions", session, *LOG_DIR)


def deny_row(payload, reason_text, ms, session):
    """The row for a deny whose reason, as printed, is REASON_TEXT. Sizes
    only; never the command, never a subject or a sender."""
    import os
    from datetime import datetime
    tid = payload.get("tool_use_id")
    tool = payload.get("tool_name")
    event = payload.get("hook_event_name")
    return {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "tool": "mail", "event": "inject", "via": VIA_DENY, "seq": None,
        "sid": session, "seat": None, "pid": os.getpid(),
        "hook_event_name": event if isinstance(event, str) and event else "PreToolUse",
        "tool_use_ids": [tid] if isinstance(tid, str) and tid else [],
        "chars": len(reason_text),
        "lines": reason_text.count("\n") + 1 if reason_text else 0,
        "tokens_est": _tokens_estimate(reason_text), "tok": "chars/4",
        "items": [], "superseded": 0, "held": 0, "past_budget": 0, "capped": None,
        "ms": int(ms),
        "blocked_tool": tool if isinstance(tool, str) else None,
    }


def log_append(directory, row, roll_at=None):
    """ROW into DIRECTORY's pager.jsonl under the seq lock; its seq. Raises:
    `log_deny` is the fail-open door."""
    import fcntl
    import os
    limit = LOG_ROLL_AT if roll_at is None else roll_at
    parent = os.path.dirname(directory)
    os.makedirs(parent, exist_ok=True)
    try:
        os.mkdir(directory, 0o700)
        os.chmod(directory, 0o700)
    except FileExistsError:
        pass
    lock = os.open(os.path.join(directory, LOG_SEQ), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = os.pread(lock, LOG_SEQ_WIDTH + 1, 0)
        try:
            seq = int(raw.decode("ascii").strip() or 0) + 1
        except ValueError:
            seq = 1
        os.pwrite(lock, ("%0*d\n" % (LOG_SEQ_WIDTH, seq)).encode("ascii"), 0)
        os.fchmod(lock, 0o600)
        path = os.path.join(directory, LOG_FILE)
        try:
            if os.stat(path).st_size >= limit:
                os.replace(path, os.path.join(directory, LOG_ROLLED))
        except FileNotFoundError:
            pass
        row["seq"] = seq
        data = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, data)
        finally:
            os.close(fd)
        return seq
    finally:
        os.close(lock)


def log_deny(payload, reason_text, started, env=None):
    """Log the deny just printed. Returns its seq, or None: no session, or
    ANY failure. Never raises - the deny is already out (fail OPEN)."""
    try:
        import os
        import time
        env = os.environ if env is None else env
        session = _session_of(payload, env)
        directory = log_dir(session, env)
        if directory is None or not reason_text:
            return None
        ms = max(0, int((time.monotonic() - started) * 1000))
        return log_append(directory, deny_row(payload, reason_text, ms, session))
    except Exception:  # noqa: BLE001 - a row is never why a deny fails
        return None


def main(argv=None):
    import time
    started = time.monotonic()           # the deny row's `ms` is from here
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "Bash":
        # An MCP tool of the message or pager server. A main thread
        # (no `agent_id`) is allowed here, before anything is read: exit 0,
        # no output, the same as when this hook never ran for MCP calls.
        if not payload.get("agent_id"):
            return 0
        reason = decide_subagent_mcp(payload.get("tool_name"), payload.get("tool_input"))
        if not reason:
            return 0
        return _deny(payload, reason, started)
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    # Only a payload that carries `agent_id` (a subagent's) is asked
    # this; a main-thread payload never reaches it, so its decision is the
    # one `decide` alone gives, byte for byte.
    reason = decide_subagent(command) if payload.get("agent_id") else None
    if not reason:
        reason = decide(command)
    if not reason:
        return 0
    return _deny(payload, reason, started)


def _deny(payload, reason, started):
    """Print the deny for REASON, then log it. Both doors use it, so a deny
    reads and logs the same whichever door it came through."""
    text = "mail-guard: " + reason
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": text,
        }
    }
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    # The row AFTER the deny is out (flushed first, so nothing the
    # log does can hold the decision back), sized on the exact reason the
    # agent reads. Fail open: `log_deny` never raises.
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    log_deny(payload, text, started)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - fail OPEN, always
        sys.exit(0)
