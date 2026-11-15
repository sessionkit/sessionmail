# Which address this shell sends from: `message address`

`message send` takes no `--from`. It works the From out from the
environment: the seat's declared plus address, else its seat word, else the
session id. `message address` prints that answer, so a program that needs
it asks, and keeps no copy of the rule. It takes two options, each with a
section below: `-r ID` and `--json`.

```console
$ message address
alpha@hq.example.net
```

It prints the address and nothing else. It reads only: it sends nothing
and writes nothing.

The verb is **hidden**, like [`message receipt`][message-receipt]: it is
not on `message --help` and is not an MCP tool. A visible verb would push
`mcp` off the first 20 lines of `message --help`. This page and `message
address --help` are where you find it.

## What it prints

The address a fresh `message send` from this shell would carry in `From:`
when it arrives, worked out by the functions `send` itself calls:

- **The local part** is the first of: a plus address (`<base>+<ext>`)
  declared in `$EMAIL`, else in `$TM_AGENT_ADDRESS`; the seat word
  (`$TM_AGENT_ADDRESS`, else `$CLAUDE_CODE_AGENT`); the session id. A bare
  `$EMAIL` declares nothing, and an address variable with no seat and no
  session names nobody.
- **The domain** is the mail origin, which a bare From gains on its way:
  `$SESSIONMAIL_ORIGIN`, else the `postfix.origin` setting, else postfix's
  `myorigin`; with the mail drop, the origin the drop writes. With no
  origin known the local part is printed bare, and stderr says so.

Identity words inherited from a claude this shell does not run under are
dropped first, as `send` drops them. A plain pane on a tmux server that was
started inside a seat carries that seat's words, and it is not that seat:
there the verb exits 2 and names the claude pid the words came from.

The display name `send` puts before the address (`Alpha <alpha@...>`) is
not part of the answer.

## A reply: `-r ID`

A reply answers from the address its parent was mailed at, when that is one
of this session's own, so a reply to a direct message goes out from the
session id and stays off the seat's record. `message address -r ID` prints
the address a reply to ID would carry, by the same function `send` uses.
When that function falls back to the declared address (the parent is not
in this session's boxes, or carries no `X-Original-To`), stderr says why,
as it does on a send.

## `--json`, schema 1

One object on stdout:

```json
{"schema": 1, "address": "alpha@hq.example.net", "kind": "seat",
 "source": "CLAUDE_CODE_AGENT", "session": "", "notes": []}
```

- `address`: what the text form prints.
- `kind`: `plus` (a declared plus address), `seat` (a seat word) or
  `session` (the session id: a session with no seat, or a reply to a
  direct message).
- `source`: the variable that named the address, or `X-Original-To` when
  `-r` chose the address the parent was mailed at.
- `session`: this session's id, or `""` when there is none.
- `notes`: the lines stderr carried, in order.

Within schema 1, keys are only ever added. A refusal prints no object.

## Exits

- `0`: the address is on stdout.
- `2`: usage; or a send from this shell would have no From, and stdout is
  empty. Either nothing names a seat or a session (stderr uses `send`'s
  words, with the inherited-identity clause when it applies), or there is
  no carrier (stderr uses the carrier's words).

[message-receipt]: message-receipt.md
