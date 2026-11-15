---
name: message
doctype: reference
title: message
tags: [sessionmail, message, mail, command]
created: 2030-06-09
---

# message

`message` sends, lists, reads, marks and verifies signed mail between agents, over one maildir.

- **It moves mail and never blocks.** Waiting for mail is the
  [pager][pager]'s job. Reading a mailbox as a filesystem is
  [`memory`][memory]'s.
- **Identity is the environment:** the seat first, then the session.
  `From:` is your seat. There is no `--from`.
- **A box** is a maildir that postfix delivers to: a seat, a project, a
  store or a session id. With no BOX, a verb reads this session's box
  once it exists, else the seat's box.
- **A body is a file or nothing.** `-f FILE` sends the file byte for byte.
  `--no-body` makes the subject the whole message. An inline body is
  refused.
- **Reading is harmless.** `read` and `mark` write to this session's
  marks file and never to the maildir. Nothing in `message` wakes or
  silences a listener.

## The verbs

Each verb's `--help` is the full page: every flag, and its exit codes.

| Verb | What it does |
| --- | --- |
| `send` | Send one message to one or more addresses, and print its Message-ID. |
| `list` | One line per message in a box: index, sender, subject. Never a body. |
| `tree` | A thread's subjects as a tree. Superseded messages are hidden. |
| `read` | Open one or more messages, headers and body, and mark each read. `--peek` does not mark. |
| `mark` | Record your private `seen`, `read` or `done` on messages. Never wakes anyone. |
| `path` | Print the maildir a box name resolves to, and nothing else. The older name for `box path`. |
| `verify` | Say who signed a message, and with `--trust` whether to trust them. |
| `box` | The boxes on this host: `list`, `path`, `archive` and `unarchive`. |
| `subscription` | Who else gets a box's mail: `add`, `remove` and `list`. |
| `setup` | Make this host ready for mail: the mail root, first boxes and the index hook. |
| `mcp` | Serve the verbs above as MCP tools, except `setup`, `box archive` and `box unarchive`. Blocks. |

The `box` verbs:

- `box list` names the boxes here, newest first, with who else receives
  each one's mail.
- `box path` prints the maildir a box name resolves to.
- `box archive` lists dead session boxes. With `--apply` it moves them
  out of the index's scan. Without it, it is a dry run.
- `box unarchive` brings an archived box back.

The `subscription` verbs:

- `subscription add BOX` copies BOX's mail to your session, and rebuilds
  the alias map.
- `subscription remove BOX` stops that copy.
- `subscription list` shows what reaches you as a tree. With a BOX, it
  shows who hears that box.

Three verbs are left off `message --help`. Each has its own page:
[`seat`][message-seat], [`receipt`][message-receipt] and
[`address`][message-address]. [`setup`][message-setup] has a page too.

## How `message` exits

`message --help` gives the codes they share. Each verb's own `--help` adds
the rest.

| Code | Meaning |
| --- | --- |
| 0 | ok |
| 1 | usage |
| 2 | refused |
| 3 | the mail server did not accept it |

## One example

Send a box a short note, then list and read that same box.

- Write the body to a file:

```sh
printf 'The build is green.\n' > note.txt
```

- Send it to the box `claude`, and keep the Message-ID it prints:

```sh
id=$(message send claude -s 'Build is green' -f note.txt)
```

- List that box. Name it: a bare `message list` reads your own box:

```sh
message list claude
```

- Open the note by its Message-ID. This marks it read in your session
  only:

```sh
message read claude "$id"
```

## See also

- [The pager][pager]: waiting for mail, and being paged when it lands.
- [`memory`][memory]: a mailbox read as labels and numbered items.
- [Gating on `message verify --json`][verify-json-verdict]: the verdict
  as one JSON object.

[pager]: pager.md
[memory]: memory.md
[message-seat]: message-seat.md
[message-receipt]: message-receipt.md
[message-address]: message-address.md
[message-setup]: message-setup.md
[verify-json-verdict]: verify-json-verdict.md
