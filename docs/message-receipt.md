# What another session recorded: `message receipt`

Every other `message` verb reads your own session's state. `message
receipt` reads ANOTHER session's: has session SID recorded a mark or a
pager verdict on one Message-ID? It is for a program that holds the sid,
like `tm kick --require-receipt`, or an audit. It is read-only and needs no
seat of its own.

```sh
message receipt '<2000000000.11111.11111@host.session-mail>' --session SID [--json]
```

The verb is **hidden**, like [`message seat`][message-seat]: it is not on
`message --help` and is not an MCP tool. A visible verb would push `mcp`
off the first 20 lines of `message --help`, and a tool would put a
cross-session read in front of every agent before it is decided that it
should be. This page and `message receipt --help` are where you find it.

**A row is what SID RECORDED, never proof it read the content.** Each kind
says something different, and the verb reports it as it is, without
flattening it to a yes:

- `read`: `message read` printed it, cut at 40 lines or 400 tokens unless
  `--full`.
- `seen`: the pager put the page, or a ride-along line (maybe cut), in
  context. The reason says which (`pager wake`, `ride-along`).
- `done`: SID said it dealt with it, maybe in a bulk mark.
- a verdict (`wake`, `ride`): the pager judged it and delivered it. A
  verdict row gives the action, the rule, the layer, who judged and when,
  worded as `pager why` words it.

Only SID's transcript shows what it read.

## Where it looks

It reads `<session dir>/mail/` alone (`marks` and `verdicts`, both line
forms of `marks`), and only SID's own rows. Both paths come from the
caller's environment. If yours differs from the target's, you look
somewhere else, and the answer names where it looked.

- A session whose old root (`$SESSIONMAIL_HOME/sessions/<sid>`) is
  anything but absent or the mover's symlink to `mail/` (a real directory,
  a file, a link to somewhere else) has not moved. It exits 2 whatever its
  `mail/` holds, and stderr says why: `not moved; at <path>` for a
  directory or a file, and where the link points for a link. This is
  decided first. The old root is looked at, never read.
- A session that predates per-session marks may hold marks only in its seat's file (`seats/*/marks`), which this verb does not read.
- A retired sessions root (`~/.taskmaster/sessions`) is never consulted.

`--session` is required and takes the full id. It has no default: your own
session is the wrong answer to "did the addressee see it", and a forgotten
flag must fail, not answer that.

## Exits

As `grep`, so `if message receipt ...; then` works:

| exit | meaning |
| --- | --- |
| 0 | a row: SID recorded at least one mark or verdict for ID |
| 1 | no row in SID's `mail/`, including a seat still booting, with no `mail/` yet |
| 2 | usage, could not look (a file or directory it cannot read, the mover's lock held, no `sessiontools`), or not moved; nothing on stdout |
| 3 | no such session: no session dir where the caller's environment puts one |

To a program, 2 means unmeasured, never "no". The text output keeps the
newest 40 rows (by time, not string) and says how many it left out;
`--json` has every row.

## `--json`: schema 1

`--json` prints one object on exits 0, 1 and 3, and nothing on exit 2.
This is schema 1, the contract `tm kick` reads:

```text
{"schema": 1,
 "answer": "yes" | "no" | "no-such-session",
 "message_id": STR, "session": STR,
 "looked_at": {"new": PATH, "old": PATH},
 "marks": [{"stamp": STR, "mark": "seen" | "read" | "done",
            "seat": STR, "reason": STR}, ...],
 "verdicts": [{"stamp": STR, "action": STR, "carry": BOOL,
               "promoted": BOOL, "at_once": BOOL,
               "rule": STR | null, "layer": STR, "line": INT | null,
               "by": STR, "bad": [STR, ...]}, ...],
 "passed_over": {"marks": INT, "verdicts": INT}}
```

`answer` agrees with the exit. `looked_at.new` is the root read;
`looked_at.old` is the old root, looked at and never read. The answer is
about those two and nothing else, and says nothing about a seat's file. A
four-field mark line has `stamp` `""` and `seat` `-`. `carry`, `promoted`
and `bad` read `false`, `false` and `[]` when not recorded. `passed_over`
counts lines that are not records. Within schema 1 keys are only added:
ignore a key you do not know. Anything else raises `schema`. The full
requirements are in `openspec/changes/message-receipt/`, in the git
repository; the release archive leaves `openspec/` out.

[message-seat]: message-seat.md
