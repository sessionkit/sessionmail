# pager-inject-log

Every string sessionmail puts into an agent's context gets one JSONL row, sized, in the session dir. The text itself is kept only when capture is on.

- **Where:** `~/.session/sessions/<sid>/log/mail/pager.jsonl`, one file per session. A central view is a
  merge of these files ([merge them][sum]). No session id, no file.
- **What gets a row:** every injection, one row each. A row's `via` names the path:
  `monitor`, `listen`, `start`, `prompt`, `batch`, `stop` or `pretooluse-deny`.
- **Its size:** `chars`, `lines` and `tokens_est` are measured on the exact string the agent saw, after every trim.
  `tok: "chars/4"` says the tokens are an estimate, not a tokenizer count.
- **What it never holds:** subjects, senders, bodies or command text. Only Message-IDs and sizes.
- **Today, for this session:** `pager status` prints a `today:` line on stderr. For every seat and day, use the
  [jq one-liner][sum].
- **The text:** off by default. Turn it on with the `capture` setting or `SESSIONMAIL_CAPTURE=on`. Each injection is
  then written to `capture/<seq>.txt`, up to 64 MB per session.
- **Privacy:** `log/mail/` and `capture/` are 0700, and every file in them is 0600. `capture/` holds a
  `.gitignore` of `*`, because `~/.session` can be a git repo. Where it is one, `pager.jsonl` is NOT ignored: it
  holds only Message-IDs and sizes, and that repo may track it.
- **Cost:** a row costs about 0.1-0.2 ms, and about 0.35 ms when captured. An empty tool batch writes no row; it adds
  one count, at about 0.05 ms.
- **The contract:** the proposal, `openspec/changes/pager-inject-log/`, which is not yet ratified. It is in the git
  repository; the release archive leaves `openspec/` out.

## The files

```text
~/.session/sessions/<sid>/log/mail/
  pager.jsonl      the rows (0600)
  pager.1.jsonl    the one older generation: pager.jsonl is renamed here at 16 MB
  seq              the row counter, and the lock every writer takes
  quiet            empty hook runs: "count total_ms max_ms", never reset
  capture/         only when capture is on (0700, .gitignore of *)
    000042.txt     the exact text of the row whose seq is 42 (0600)
    .size .full    the bytes captured so far, and the mark that the 64 MB cap is reached
```

`<sid>` is the Claude Code session id, from `CLAUDE_CODE_SESSION_ID` (or `CLAUDE_SESSION_ID`). The session dir is
sessiontools' `session_dir`: `$HOME/.session/sessions/<sid>`.

## The rows

Every row is one compact JSON object with the **envelope**:
`ts` (local time with its offset), `tool: "mail"`, `event`, `via`, `seq`, `sid`, `seat`, `pid`.

`seq` counts up by one per row, across every writer in the session: the monitor, each hook process and the
mail-guard. Each writer takes it under one `flock`, so no two rows share a number.

An **`inject` row** adds:

| key | what it is |
|---|---|
| `hook_event_name` | from the hook's payload (`PostToolBatch`, `UserPromptSubmit`, `Stop`, `SessionStart`, `PreToolUse`); null for `monitor` and `listen` |
| `tool_use_ids` | the batch's `tool_calls[].tool_use_id`, or the deny's `tool_use_id`; `[]` when there are none |
| `chars` `lines` `tokens_est` `tok` | the size of the exact string emitted (`tokens_est` = max(1, chars // 4)) |
| `items` | the mails behind the string, in order: `id` (Message-ID, or null), `kind` (`mail`/`heard`), `view` (`whole`/`text`/`header`/`count`), `given` (its chars in the string), `in_mail` (the body's chars, or null), `cut` |
| `superseded` `held` `past_budget` | how many mails were replaced, held back for room, or refused past the 8,000-char ride budget; counted, never listed |
| `capped` | the cap that bit, if any: `give_by`, `room`, `batch` or `text` |
| `ms` | for a hook, from hook entry to the emit; for a page, from the oldest mail's arrival. Null on a monitor line that has no arrival (the deaf and off lines) |
| `capture` | `capture/<seq>.txt`, only when that file was written |
| `stderr_chars` | `listen` only: the stderr "paged by" line, sized separately |
| `blocked_tool` | `pretooluse-deny` only: the tool the guard refused (`Bash`) |

The **listener's own rows**, from `pager listen` and the monitor, carry the envelope plus:
- `start`: `box`, `timeout`. Written once armed.
- `exit`: `code`, `pages`, `quiet`, `quiet_ms_total`, `quiet_ms_max`. The last three are the `quiet` counter's
  running totals. A monitor that follows a `/clear` to a new session id writes its `exit` with
  `followed: <new sid>` and `code: null`. A SIGKILL leaves no `exit` row, and that absence is the signal.
- `error`: `what`. A write the listener depends on failed, or `capture full`.

`marks` is unchanged: these rows replace an earlier release's lifecycle lines.

## What counts as injected, and what does not

- **Rows:** everything the hooks put in `additionalContext`, including the settings notices, the deaf line and a
  drift line alone. Also every line the monitor prints, a hand page's stdout lines, the Stop hook's exit-2 line,
  and the mail-guard's deny reason (`mail-guard: ` included).
- **A subagent's deny** is logged under the session id in its payload, which is the parent's, because the reason
  reaches the subagent's context. A subagent's tool batch prints nothing, so it writes nothing.
- **No row:** a hook that prints nothing, including SessionStart's empty JSON. The Stop hook's exit-0 stderr. Also
  `pager status --new`, and the creating run's `systemMessage`, which the person sees and the agent does not.
- **Not yet counted:** a hand listen's arm banner on stderr, its timeout line on stdout, and the "the pager monitor
  (pid N) hears for this session" line. All three reach the agent through the Bash tool.
- **Not ours to count:** Claude Code's own wrapper around each injection.

## Capture

- **The switch:** the `capture` key, a boolean, in `mail.json` at any layer: host `~/.sessionkit/mail/settings/`, then seat,
  then session. The most specific layer wins, and only a boolean decides.
- **The variable:** `SESSIONMAIL_CAPTURE` beats every layer either way: `on`/`1`/`yes`/`true` or
  `off`/`0`/`no`/`false`. Any other value is ignored, silently.
- **When it is read:** for every row, so a change applies from the next injection, even in a running monitor.
- **What is written:** one file per row, holding the exact UTF-8 bytes of the emitted string. Each file is created
  new, so a file is never overwritten.
- **The cap:** the 64 MB counts captured text bytes, kept in `.size`. The capture that would go past it writes
  nothing. Instead, one `{"event":"error","what":"capture full"}` row follows its row, and nothing is captured
  after that.
- **Never captured:** lifecycle rows, and the mail-guard's deny. The deny runs on stdlib python and cannot read
  the layered switch, so its row is size-only.
- **A failed capture costs only the capture:** the row is still written, without the `capture` field. The reverse
  case is rare: the file is written and then its row fails to append (a full disk). That file is named by no row.

## Sum it per seat per day

`pager status` prints this session's `today:` line, for example:

```text
today: 14 injections, ≈3,210 tok (rides ≈2,700: mail ≈2,100, heard ≈600); capture off - …/pager.jsonl
```

For every session and seat, merge the files with `jq`. The program below fills a deny row's null `seat` from
another row of the same session:

<!-- jq-sum: tests/test_pager_inject_status.py runs this exact program -->
```sh
jq -rs '(map(select(.seat)) | map({(.sid): .seat}) | add // {}) as $seat
  | map(select(.event == "inject") | .seat //= ($seat[.sid] // "(no seat)"))
  | group_by([.seat, .ts[0:10]])[]
  | [.[0].seat, .[0].ts[0:10], length, (map(.tokens_est) | add)] | @tsv' \
  ~/.session/sessions/*/log/mail/pager*.jsonl
```

It prints one line per seat per day: seat, date, injections, estimated tokens. For example:

```text
(no seat)	2030-06-09	1	30
sm+t.worker	2030-06-09	3	170
sm+t.worker	2030-12-08	1	7
```

## When a write fails

- **A row is never why an injection fails.** The row is written after the text is printed, and every writer
  swallows its own errors. The mail-guard's writer fails open as well: the deny has already been printed.
- **Locks block without a deadline.** A writer holds the lock for microseconds. A process stopped while holding it
  would block the next hook until its timeout.

[sum]: #sum-it-per-seat-per-day
