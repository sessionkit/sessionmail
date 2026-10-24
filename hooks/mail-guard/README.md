# mail-guard — a PreToolUse hook that refuses a shell-damaged mail send

`session mail send` takes prose. When that prose is typed into a shell, the
**shell reads it first**: a backtick or `$(` in a double-quoted body or subject
is expanded before the send program is ever reached, so the program cannot
detect what it lost. It exits 0, prints a Message-ID, and delivers a message
with words missing or replaced.

Three measured incidents: a body with a word eaten; a subject whose apostrophe
truncated the body; and an agent that **executed `git ready approve`** because
the phrase sat in backticks in a *subject line*. That last one is three faults
at once — silent memory corruption, arbitrary command execution by an agent
that was only writing prose, and a live injection path the day an agent quotes
text it received from someone else.

This hook is the layer **before** the shell. It reads the Bash tool call's
command text as typed — pre-expansion, the only place the executed-subject
incident is still visible — and **denies** it.

## What it refuses

On a **command-position** invocation of `session mail send` or of the resolved
script `session-mail-send`:

1. **an inline body** — a second positional word, or anything after a literal
   `--` (including the `-- "$(cat body.txt)"` workaround, which is itself a
   command substitution);
2. **a `--subject` whose raw text carries a backtick or `$(`**;
3. **a heredoc or pipe feeding the send with no `--body-file`** — the send does
   not read stdin, so that delivers an *empty* message at exit 0.

`session mail send` is **retired** (2030-12-08): it now only prints where its
verbs went. The rules above still match it, but every deny sends the caller to
the live door, never back to the retired `--body-file` or MCP `mail_send`
forms.

For a **body**, the deny reason says the verb is retired and names the two
safe forms: `message send TO -s SUBJECT -f FILE` (`-f -` reads stdin), and
the MCP tool `mcp__plugin_sessionmail_message__message_send` (no shell in the
path at all).

For a **subject** it gives `message send`'s own subject remedy (below): take
it out of the subject. Neither door escapes the subject rule — `message send`
refuses a backtick or `$(` in a subject at parse time, and the MCP tool runs
the same command — and single-quoting is not a fix, because quoting is what
makes the character arrive *literally*, which is exactly what the CLI refuses.
One constant (`MESSAGE_SUBJECT_REMEDY`) serves both judges.

### On `message send` — the subject rule only

`session mail send` retires with the `session` project; `message send` is the
verb that survives, so a guard that knew only the first would guard nothing.
On a **command-position** `message send` it refuses:

4. **a subject or a `--header` whose raw text carries a backtick or `$(`
   outside single quotes** — in every spelling click accepts (`-s X`, `-sX`,
   `--subject X`, `--subject=X`; `--header X`, `--header=X`), or a value that
   *is* a bare substitution (`-s $(hostname)`). Arithmetic `$((…))` runs no
   command and is not refused.

Rules 1 and 3 do **not** apply there, on purpose: its positional words are
recipients, not a body; the CLI refuses an inline body itself; and `-f -`
reads stdin, so a heredoc or pipe into it is the documented form. A
single-quoted backtick is let through because it cannot execute; for a
subject the CLI then refuses it at parse time (`_send.check_subject`), and a
test pins that the two stay in lockstep.

The subject deny says: take the code span out of the subject and put it in
the body — `-f FILE`, or the MCP tool
`mcp__plugin_sessionmail_message__message_send`, whose body travels
byte-for-byte — and, when the shell was *meant* to run it (a time, a count),
run it into a variable first and put `$T` in the subject. A header's value is
not refused for a literal backtick (`_send.parse_headers` refuses only CR, LF
and NUL), so the header deny also offers single quotes.

The flag table for `message send` is pinned by a test against the click
command's own params (long and short spellings), so a new flag goes red in
the test suite rather than being denied in the field (an unknown flag fails
closed: see below).

It **denies**, never warns. A warning is read by a human reviewing a
transcript, not by the agent that already typed the command.

## What it deliberately lets through

Deny only on a **confident parse**. Anything the tokenizer cannot classify —
unterminated quotes, a command substitution nested inside double quotes — is
allowed. So is any command that merely *mentions* the send: a
`grep 'session mail send' …`, a `# session mail send …` comment, a heredoc
whose body documents the form.

False negatives are acceptable where a determined caller can obfuscate past
any text filter. False positives are not: they block unrelated work and teach
agents to resent the guard, which is how guards get bypassed culturally.

### The one thing it fails CLOSED on: a flag it does not know

On a send it has already matched (`message send`, or the retired
`session mail send` / `session-mail-send`), a flag that is in none of its
tables is **denied**: long (`--frobnicate x`, `--frobnicate=x`) or short
(`-z x`, `-zx`). Past such a flag the guard cannot tell which word is the
subject. It used to fall silent there, on the theory that the CLI's own refusal
was the floor under this net. That floor is not there for a damaged subject:
the shell expands a backtick *before* the CLI parses anything, so
`message send bob --frobnicate x -s "`cmd`" -f F` ran `cmd` and only then got
click's `No such option` (measured on a live seat).

The deny costs no send that could have worked: click refuses every unknown
flag (`NoSuchOption`), and the retired verb exits 2 whatever its arguments.
Its one line names the flag, and for `message send` the tables to grow
(`MESSAGE_VALUE_FLAGS` / `MESSAGE_BOOL_FLAGS` in `hook.py`) and the safe form;
the retired verb's deny names no table, only the live verb.

Not denied, exactly as click parses them: the help cluster (`-h`, `-hh`,
`-hs X`: click prints help and sends nothing; `-hz` IS denied, click refuses
it), and a known boolean given a value (`--json=1`: one word, stepped over,
the rest still judged; click refuses it in its own words). Commands the guard
does not match (`message list --anything`, a `grep` for the phrase) never
reach this rule.

Measured on 33,302 real agent Bash commands (2030-06-09): 1,645 denied, every
one of them a genuine inline-body or damaged-subject send; **zero** denials of
a command that merely mentioned the phrase. Scanning cost ~16µs per command (0.54s for the whole corpus); the hook process itself, python startup included, runs in ~15ms.

Re-measured with the `message send` rule (2030-12-08), 30 days of transcripts:
168,906 Bash commands, 5,143 of them `message send` invocations. The session
rules' decisions were identical to the pre-move hook's (1,104 denials, zero
moved). `message send` denials: 2, both a real `$( )` in a subject; zero among
the 457 commands that only *mention* `message send` (344 of them carrying a
backtick or `$(` somewhere). One false positive was found and fixed before
this landed — arithmetic `$(( … ))` in a subject. ~18µs per command; the hook
process through its shim, ~27ms.

## Wiring it — the plugin registers it

This hook lived in the `session` repo until that project was retired
(`openspec/changes/mail-leaves-session`, item 23, in the git repository;
the release archive leaves `openspec/` out). There it was wired by hand
into `~/.claude/settings.json` with `session hooks install`, and
`make check-hooks` went red on an unwired box. Here the **plugin** registers
it, like its other hooks:

```
hooks/hooks.json    PreToolUse, matcher Bash -> hooks/mail-guard-hook (timeout 5)
                    PreToolUse, matcher mcp__plugin_sessionmail_(message|pager)__.*
                        -> the same shim (a subagent's MCP door)
hooks/mail-guard-hook   the shim: python3 hooks/mail-guard/hook.py, and exit 0
                        whatever goes wrong
```

So a seat that loaded sessionmail is behind the guard, and one that did not
is not.

**The MCP group** routes the message and pager servers' tools (not
memory's) to the same guard. It acts only on a payload that carries
`agent_id` (a subagent's): there it denies what the Bash door denies a
subagent (`message_read` without `peek`, `message_mark`, `pager_status` with
`new_only`, `pager_stop|off|mute|unmute|listen`), judged by the same table
(`subagent_verb`). A main thread's MCP call is allowed at once, exit 0 and
no output. Cost: one python3 run per such call, about 23 ms on one Mac. Hook configuration is read at **session start**: loading the plugin
does not guard agents that are already running.

**It never blocks by failing.** The only way it speaks is decision JSON on
stdout at exit 0. Measured before the shim: with no python3 on PATH the bare
hook exits 127 (a hook error on every Bash call), and `python3 <missing
file>` exits **2**, which from a PreToolUse hook *blocks* the call. The shim
turns every non-zero exit into 0 and silence; inside, `main()` and `decide()`
already fail open on any exception.

**A box that still wires the old path** (`…/session/hooks/mail-guard/hook.py`
in `~/.claude/settings.json`) loses the guard, with a hook error on every
Bash call, the moment that path stops resolving. Remove that entry once the
plugin is on every launch path; until then a seat with both runs the guard
twice, which is harmless.

## Testing it

`tests/test_mail_guard.py` — the suite ported from session's
`test_session_hooks_mail_guard.py` (groups A-D, the session case ids kept):
the two lamp-check commands, the deny surface, the false-positive control
group, and the hook process contract. `tests/test_mail_guard_wiring.py` —
the registration in `hooks/hooks.json`, and the shim allowing when python3
is missing, `hook.py` is missing, or it crashes. Both run under `make test`.
`tests/test_mail_guard_subagent.py` and `tests/test_mail_guard_subagent_mcp.py`
— the subagent rule through the Bash door and the MCP door.

Written in python3, stdlib only, on purpose: a quoting-aware guard written in
the language whose quoting is the hazard is how you get a guard with its own
injection bug.
